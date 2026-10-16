"""arXiv の新着論文を Slack に流す。cron 等で定期実行する想定。"""
import json
import os
import pathlib
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
MAX_RESULTS = int(os.environ.get("ARXIV_MAX_RESULTS", "50"))
AZURE_KEY = os.environ["AZURE_TRANSLATOR_KEY"]
AZURE_REGION = os.environ["AZURE_TRANSLATOR_REGION"]
SEEN_FILE = pathlib.Path(__file__).with_name("seen.json")
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch_papers(start=0, max_results=MAX_RESULTS):
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
        })
    return int(root.findtext("{http://a9.com/-/spec/opensearch/1.1/}totalResults")), papers


def translate(texts):
    out = []
    for i in range(0, len(texts), 10):  # 1 リクエスト 50,000 文字制限があるので分割
        req = urllib.request.Request(
            "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&to=ja",
            data=json.dumps([{"Text": t} for t in texts[i:i + 10]]).encode(),
            headers={
                "Ocp-Apim-Subscription-Key": AZURE_KEY,
                "Ocp-Apim-Subscription-Region": AZURE_REGION,
                "Content-Type": "application/json",
                "X-ClientTraceId": str(uuid.uuid4()),
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            out += [d["translations"][0]["text"] for d in json.load(r)]
    return out


def slack_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(paper, abstract_ja):
    return f"*<{paper['url']}|{slack_escape(paper['title'])}>*\n{slack_escape(abstract_ja)}"


def post_slack(paper, abstract_ja):
    req = urllib.request.Request(
        os.environ["SLACK_WEBHOOK_URL"],
        data=json.dumps({"text": format_message(paper, abstract_ja), "unfurl_links": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30).close()


def main():
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else []
    new = [p for p in fetch_papers()[1][::-1] if p["id"] not in seen]  # 古い順に投稿
    if not new:
        return
    for paper, ja in zip(new, translate([p["abstract"] for p in new])):
        post_slack(paper, ja)
        seen.append(paper["id"])
        SEEN_FILE.write_text(json.dumps(seen[-2000:]))  # 投稿ごとに保存し、途中失敗で二重投稿しない


if __name__ == "__main__":
    main()
