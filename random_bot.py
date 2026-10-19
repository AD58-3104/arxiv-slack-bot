"""Slack のスラッシュコマンドを受け付ける。常駐させる。

/arxiv-random          : 現在の取得元で、検索式にマッチする全期間の論文から 1 本をランダムに返す
/paper-source [名前]   : 取得元 (arxiv / openalex) を表示・切り替える
/paper-rerun           : 新着投稿を手動で再実行する（定期実行が失敗したとき用）
/translator-usage      : 今月の Azure Translator の使用文字数と残りを返す

/arxiv-random・/paper-source・/paper-rerun は、コマンドを打ったチャンネルの channel_id を持つフィードが対象。
該当がなければ feeds.json の先頭のフィードが対象になる。
"""
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import bot

app = App(token=os.environ["SLACK_BOT_TOKEN"])


@app.command("/arxiv-random")
def arxiv_random(ack, respond, command):
    ack()  # Slack は 3 秒以内の応答を要求するので先に返す
    try:
        paper = bot.random_paper(bot.feed_for_channel(command["channel_id"]))
        respond(response_type="in_channel", unfurl_links=False,
                text=bot.format_message(paper, bot.translate(paper["abstract"])))
    except Exception as e:
        respond(f"論文の取得に失敗しました: {e}")


@app.command("/paper-source")
def paper_source(ack, respond, command):
    ack()
    feed = bot.feed_for_channel(command["channel_id"])
    name = command["text"].strip().lower()
    if name not in bot.SOURCES:
        respond(f"フィード「{feed['name']}」の現在の取得元: {bot.get_source(feed)}\n"
                "切り替えは `/paper-source arxiv` または `/paper-source openalex`")
        return
    try:
        bot.source_file(feed).write_text(name)
        # 切り替え直後に過去の論文がまとめて流れないよう、今の新着は既読にしておく
        seen = bot.load_seen(feed)
        new = [p["id"] for p in bot.fetch_new(feed) if p["id"] not in seen]
        bot.save_seen(feed, seen + new)
        respond(response_type="in_channel",
                text=f"フィード「{feed['name']}」の取得元を {name} に切り替えました（現時点の新着 {len(new)} 件は既読扱い）")
    except Exception as e:
        respond(f"切り替えに失敗しました: {e}")


@app.command("/paper-rerun")
def paper_rerun(ack, respond, command):
    ack()
    feed = bot.feed_for_channel(command["channel_id"])
    respond(response_type="in_channel", text=f"フィード「{feed['name']}」の新着投稿を再実行します…")
    try:
        # 新着ゼロのお知らせは定期実行で流れているはずなので、ここでは結果だけを返す
        n = bot.run_feed(feed, notify_empty=False)
        respond(response_type="in_channel",
                text=f"フィード「{feed['name']}」の再実行が完了しました（新たに投稿: {n} 件）")
    except Exception as e:
        respond(response_type="in_channel",
                text=f":warning: フィード「{feed['name']}」の再実行に失敗しました\n```{bot.slack_escape(bot.describe_error(e))}```")


@app.command("/translator-usage")
def translator_usage(ack, respond):
    ack()
    month, used = bot.monthly_usage()
    respond(
        f"{month}（UTC）の翻訳文字数: {used:,} / {bot.MONTHLY_CHAR_LIMIT:,} 文字\n"
        f"残り: {max(bot.MONTHLY_CHAR_LIMIT - used, 0):,} 文字（このボットが翻訳した分のみで計算）"
    )


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
