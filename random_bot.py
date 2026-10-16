"""Slack の /arxiv-random で、検索式にマッチする全期間の論文から 1 本をランダムに返す。常駐させる。"""
import os
import random

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import bot

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def pick_random_paper():
    total, _ = bot.fetch_papers(max_results=1)  # max_results=0 は arXiv が 500 を返す
    # ponytail: arXiv API は start 30000 件目以降を返さない。該当件数がそれを超えたら日付範囲で分割して引く
    _, papers = bot.fetch_papers(start=random.randrange(min(total, 30000)), max_results=1)
    return papers[0]


@app.command("/arxiv-random")
def arxiv_random(ack, respond):
    ack()  # Slack は 3 秒以内の応答を要求するので先に返す
    try:
        paper = pick_random_paper()
        respond(response_type="in_channel", unfurl_links=False,
                text=bot.format_message(paper, bot.translate([paper["abstract"]])[0]))
    except Exception as e:
        respond(f"論文の取得に失敗しました: {e}")


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
