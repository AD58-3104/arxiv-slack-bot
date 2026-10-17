"""Slack のスラッシュコマンドを受け付ける。常駐させる。

/arxiv-random          : 現在の取得元で、検索式にマッチする全期間の論文から 1 本をランダムに返す
/paper-source [名前]   : 取得元 (arxiv / openalex) を表示・切り替える
"""
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import bot

app = App(token=os.environ["SLACK_BOT_TOKEN"])


@app.command("/arxiv-random")
def arxiv_random(ack, respond):
    ack()  # Slack は 3 秒以内の応答を要求するので先に返す
    try:
        paper = bot.random_paper()
        respond(response_type="in_channel", unfurl_links=False,
                text=bot.format_message(paper, bot.translate(paper["abstract"])))
    except Exception as e:
        respond(f"論文の取得に失敗しました: {e}")


@app.command("/paper-source")
def paper_source(ack, respond, command):
    ack()
    name = command["text"].strip().lower()
    if name not in bot.SOURCES:
        respond(f"現在の取得元: {bot.get_source()}\n切り替えは `/paper-source arxiv` または `/paper-source openalex`")
        return
    try:
        bot.SOURCE_FILE.write_text(name)
        # 切り替え直後に過去の論文がまとめて流れないよう、今の新着は既読にしておく
        seen = bot.load_seen()
        new = [p["id"] for p in bot.fetch_new() if p["id"] not in seen]
        bot.save_seen(seen + new)
        respond(response_type="in_channel",
                text=f"論文の取得元を {name} に切り替えました（現時点の新着 {len(new)} 件は既読扱い）")
    except Exception as e:
        respond(f"切り替えに失敗しました: {e}")


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
