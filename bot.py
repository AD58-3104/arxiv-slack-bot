"""arXiv または OpenAlex の新着論文を Slack に流す。cron 等で定期実行する想定。"""
import json
import os
import pathlib
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

# arXiv API の検索式。書き方: https://info.arxiv.org/help/api/user-manual.html#query_details
QUERY = os.environ.get(
    "ARXIV_QUERY",
    'cat:cs.RO AND (all:humanoid OR all:biped OR all:bipedal OR all:legged OR all:quadruped)'
    ' AND (all:"reinforcement learning" OR abs:RL)',
)
# OpenAlex のタイトル・アブストラクト検索式。AND/OR/"..." が使える。カンマはフィルタの区切りになるので使えない
OPENALEX_QUERY = os.environ.get(
    "OPENALEX_QUERY",
    '(humanoid OR biped OR bipedal OR legged OR quadruped) AND "reinforcement learning"',
)
MAX_RESULTS = int(os.environ.get("ARXIV_MAX_RESULTS", "50"))
AZURE_KEY = os.environ["AZURE_TRANSLATOR_KEY"]
AZURE_REGION = os.environ["AZURE_TRANSLATOR_REGION"]
SEEN_FILE = pathlib.Path(__file__).with_name("seen.json")
SOURCE_FILE = pathlib.Path(__file__).with_name("source.txt")  # /paper-source で書き換える
SOURCES = ("arxiv", "openalex")
USAGE_FILE = pathlib.Path(__file__).with_name("usage.json")  # 月ごとの翻訳文字数
MONTHLY_CHAR_LIMIT = int(os.environ.get("AZURE_MONTHLY_CHAR_LIMIT", "2000000"))  # F0 の無料枠
ATOM = "{http://www.w3.org/2005/Atom}"
USER_AGENT = "arxiv-slack-bot/0.1 (+https://github.com/AD58-3104/arxiv-slack-bot)"


def get_source():
    return SOURCE_FILE.read_text().strip() if SOURCE_FILE.exists() else "arxiv"


def fetch_arxiv(start=0, max_results=MAX_RESULTS):
    """新しい順に start 件目から取得し、(全期間の該当件数, 論文リスト) を返す。"""
    params = urllib.parse.urlencode({
        "search_query": QUERY,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": start,
        "max_results": max_results,
    })
    with urllib.request.urlopen(f"https://export.arxiv.org/api/query?{params}", timeout=60) as r:
        root = ET.fromstring(r.read())
    papers = []
    for e in root.iter(f"{ATOM}entry"):
        url = e.findtext(f"{ATOM}id").strip()
        papers.append({
            "id": url.rsplit("/", 1)[-1].split("v")[0],  # バージョン違いを同一扱い
            "url": url,
            "title": " ".join(e.findtext(f"{ATOM}title").split()),
            "abstract": " ".join(e.findtext(f"{ATOM}summary").split()),
            "published": e.findtext(f"{ATOM}published")[:10],  # 初版 (v1) の投稿日
        })
    return int(root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults")), papers


def fetch_openalex(**params):
    """OpenAlex の /works を検索する。params は sort, per-page, sample などをそのまま渡す。"""
    params["filter"] = (f"title_and_abstract.search:{OPENALEX_QUERY},"
                        "type:article|preprint|conference-paper")  # 本・データセット等を除く
    url = f"https://api.openalex.org/works?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        works = json.load(r)["results"]
    papers = []
    for w in works:
        doi = w.get("doi") or ""
        # API は単語→出現位置の転置インデックスで返すので、位置順に並べて本文に戻す
        inv = w.get("abstract_inverted_index") or {}
        abstract = " ".join(word for _, word in sorted((i, word) for word, idx in inv.items() for i in idx))
        papers.append({
            # arXiv 論文は arXiv ID にそろえ、取得元を切り替えても二重投稿しない
            "id": doi.split("arxiv.")[-1] if "10.48550/arxiv." in doi.lower() else w["id"].rsplit("/", 1)[-1],
            "url": doi or w["id"],
            "title": " ".join((w.get("title") or "(無題)").split()),
            "abstract": abstract[:3000],  # 本文ごと入っている登録があるので、翻訳文字数を抑える
            "published": w.get("publication_date") or "不明",
        })
    return papers


def fetch_new():
    """現在の取得元から新しい順に MAX_RESULTS 件を返す。"""
    if get_source() == "openalex":
        return fetch_openalex(sort="publication_date:desc", **{"per-page": MAX_RESULTS})
    return fetch_arxiv()[1]


def random_paper():
    """現在の取得元で、検索式にマッチする全期間の論文から 1 本をランダムに返す。"""
    if get_source() == "openalex":
        return fetch_openalex(sample=1, seed=random.randrange(10**9))[0]
    total, _ = fetch_arxiv(max_results=1)  # max_results=0 は arXiv が 500 を返す
    # ponytail: arXiv API は start 30000 件目以降を返さない。該当件数がそれを超えたら日付範囲で分割して引く
    return fetch_arxiv(start=random.randrange(min(total, 30000)), max_results=1)[1][0]


def monthly_usage():
    """(今月, 今月このボットが翻訳した文字数) を返す。Azure の無料枠は UTC の暦月ごとにリセットされる。"""
    month = time.strftime("%Y-%m", time.gmtime())
    usage = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else {}
    return month, usage.get(month, 0)


def add_usage(chars):
    # ponytail: このボット経由の分だけを数える。同じキーを他で使うなら Azure Monitor のメトリクスを見る
    month, used = monthly_usage()
    usage = json.loads(USAGE_FILE.read_text()) if USAGE_FILE.exists() else {}
    usage[month] = used + chars
    USAGE_FILE.write_text(json.dumps(usage))


def translate(text):
    if not text:
        return "（アブストラクトなし）"
    req = urllib.request.Request(
        "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ja",
        data=json.dumps([{"Text": text}]).encode(),
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_KEY,
            "Ocp-Apim-Subscription-Region": AZURE_REGION,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                result = json.load(r)[0]["translations"][0]["text"]
            add_usage(len(text))
            return result
        except urllib.error.HTTPError as e:
            # 無料プラン (F0) は 1 分あたりの文字数に上限があり、超えると 429 が返る
            if e.code != 429 or attempt == 4:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 60))


def slack_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def alphaxiv_url(paper_id):
    """alphaXiv に要約ブログがあればその URL を、なければ None を返す。"""
    if not re.fullmatch(r"\d{4}\.\d{4,5}", paper_id):  # arXiv 以外 (OpenAlex の W...) は対象外
        return None
    url = f"https://www.alphaxiv.org/overview/{paper_id}"
    # 公式 API がないので HTML に要約本文があるかで判定する。Python 既定の UA は 403 になる
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return url if b"markdown-content blog-post" in r.read() else None
    except Exception:
        return None  # alphaXiv 側の不調で投稿自体を止めない


def format_message(paper, abstract_ja):
    blog = alphaxiv_url(paper["id"])
    return (
        f"*<{paper['url']}|{slack_escape(paper['title'])}>*\n"
        f"投稿日: {paper['published']}\n{slack_escape(abstract_ja)}"
        + (f"\n<{blog}|alphaXiv の解説>" if blog else "")
    )


def post_slack(text):
    req = urllib.request.Request(
        os.environ["SLACK_WEBHOOK_URL"],
        data=json.dumps({"text": text, "unfurl_links": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30).close()


def load_seen():
    return json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else []


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen[-2000:]))


def main():
    seen = load_seen()
    new = [p for p in fetch_new()[::-1] if p["id"] not in seen]  # 古い順に投稿
    if not new:
        post_slack(f"本日の新着論文はありません（取得元: {get_source()}）")
    for paper in new:
        post_slack(format_message(paper, translate(paper["abstract"])))
        seen.append(paper["id"])
        save_seen(seen)  # 投稿ごとに保存し、途中失敗で二重投稿しない


if __name__ == "__main__":
    main()
