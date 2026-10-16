# arxiv-slack-bot

arXiv の論文を Slack に流すボットです。投稿の形式は、タイトル（英語のまま）と、Azure Translator で日本語訳したアブストラクトです。

| ファイル | 役割 | 動かし方 |
|---|---|---|
| `bot.py` | 検索式にマッチする新着論文を Incoming Webhook で投稿する | 定期実行（systemd timer） |
| `random_bot.py` | `/arxiv-random` で、全期間の該当論文から 1 本をランダムに返す | 常駐（systemd service） |

検索式は環境変数 `ARXIV_QUERY` で指定します。指定しない場合は `bot.py` に書いた次の式を使います（ロボティクス分野で、ヒューマノイド・二足・脚・四足のいずれかを含み、かつ強化学習に関するもの）。

```
cat:cs.RO AND (all:humanoid OR all:biped OR all:bipedal OR all:legged OR all:quadruped) AND (all:"reinforcement learning" OR abs:RL)
```

書き方は [arXiv API のマニュアル](https://info.arxiv.org/help/api/user-manual.html#query_details) を参照してください。注意点が 2 つあります。

- 語形はまとめて検索されません。`biped` と `bipedal` は別の語として扱われます。
- `all:` はタイトルやアブストラクトなど全体を、`abs:` はアブストラクトだけを対象にします。

---

## 1. Slack 側の設定

1 つの Slack アプリで、新着投稿用の Webhook とスラッシュコマンドの両方をまかないます。

1. **アプリを作る**
   https://api.slack.com/apps →「Create New App」→「From scratch」を選びます。アプリ名（例: `arXiv bot`）を付け、導入先のワークスペースを選びます。

2. **Socket Mode を有効にする**（`random_bot.py` 用。これがあれば公開 URL は要りません）
   - 左メニュー「Socket Mode」→「Enable Socket Mode」をオンにします。
   - App-Level Token の作成を求められるので、名前は任意で、スコープに `connections:write` を付けて作成します。
   - 発行された `xapp-...` を控えます。これが **`SLACK_APP_TOKEN`** です。

3. **スラッシュコマンドを追加する**
   - 左メニュー「Slash Commands」→「Create New Command」を開きます。
   - Command は `/arxiv-random`、Short Description は任意（例: 論文をランダムに 1 本紹介）にして保存します。
   - Socket Mode が有効なら Request URL の欄は出ないので、入力は要りません。

4. **Incoming Webhook を有効にする**（`bot.py` 用）
   - 左メニュー「Incoming Webhooks」→「Activate Incoming Webhooks」をオンにします。

5. **ワークスペースにインストールする**
   - 左メニュー「OAuth & Permissions」→「Bot Token Scopes」に `commands` があることを確認します（スラッシュコマンドを作ると自動で入ります）。
   - 「Install to Workspace」を押し、許可画面で新着論文を流すチャンネルを選びます。
   - 発行された Bot User OAuth Token（`xoxb-...`）を控えます。これが **`SLACK_BOT_TOKEN`** です。
   - 「Incoming Webhooks」画面に戻り、追加された Webhook URL を控えます。これが **`SLACK_WEBHOOK_URL`** です。

スコープや設定を後から変えた場合は、「Reinstall to Workspace」が必要です。`/arxiv-random` はチャンネルにボットを招待しなくても使えます。結果は、コマンドを打ったチャンネルに全員に見える形で投稿されます。

## 2. Azure Translator の準備

1. Azure Portal で「Translator」リソースを作成します（無料の F0 プランで月 200 万文字まで使えます）。
2. 「キーとエンドポイント」画面で、次の 2 つを控えます。
   - **KEY 1** → `AZURE_TRANSLATOR_KEY`
   - **場所/リージョン**（例: `japaneast`）→ `AZURE_TRANSLATOR_REGION`

## 3. サーバーへの導入

前提は、Python 3.10 以上と systemd が入った Linux サーバーです。以下は `/opt/arxiv-slack-bot` に置き、専用ユーザー `arxivbot` で動かす例です。

```bash
# 専用ユーザーとコードの配置
sudo useradd --system --create-home --shell /usr/sbin/nologin arxivbot
sudo mkdir -p /opt/arxiv-slack-bot
sudo cp bot.py random_bot.py /opt/arxiv-slack-bot/   # git clone や scp でもよい
cd /opt/arxiv-slack-bot

# 依存ライブラリ（slack_bolt のみ。bot.py は標準ライブラリだけで動く）
sudo python3 -m venv .venv
sudo .venv/bin/pip install slack_bolt

# 投稿済み ID を記録する seen.json をこのディレクトリに書き込むため、所有者を変更
sudo chown -R arxivbot: /opt/arxiv-slack-bot
```

### 環境変数ファイル

`/etc/arxiv-slack-bot.env` を作ります。秘密情報を含むので、権限は 600 にします。

```bash
sudo tee /etc/arxiv-slack-bot.env > /dev/null <<'EOF'
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
AZURE_TRANSLATOR_KEY=...
AZURE_TRANSLATOR_REGION=japaneast
# 検索式を変えるときだけ指定する。中に " があるので全体を ' で囲む
# ARXIV_QUERY='cat:cs.RO AND all:humanoid AND (all:"reinforcement learning" OR abs:RL)'
# 1 回に取得する新着の件数（既定は 50）
# ARXIV_MAX_RESULTS=50
EOF
sudo chmod 600 /etc/arxiv-slack-bot.env
```

### 常駐: `/arxiv-random`

`/etc/systemd/system/arxiv-random.service` を次の内容で作ります。

```ini
[Unit]
Description=arXiv random paper Slack bot
After=network-online.target
Wants=network-online.target

[Service]
User=arxivbot
WorkingDirectory=/opt/arxiv-slack-bot
EnvironmentFile=/etc/arxiv-slack-bot.env
ExecStart=/opt/arxiv-slack-bot/.venv/bin/python random_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 定期実行: 新着投稿

`/etc/systemd/system/arxiv-new.service` を作ります。

```ini
[Unit]
Description=Post new arXiv papers to Slack
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=arxivbot
WorkingDirectory=/opt/arxiv-slack-bot
EnvironmentFile=/etc/arxiv-slack-bot.env
ExecStart=/usr/bin/python3 bot.py
```

`/etc/systemd/system/arxiv-new.timer` を作ります。arXiv の新着は日本時間の朝 9〜10 時ごろに公開されるので、毎日 10 時に実行する設定にしています。

```ini
[Unit]
Description=Run arxiv-new daily

[Timer]
OnCalendar=*-*-* 10:00:00 Asia/Tokyo
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` にしておくと、サーバーが止まっていて実行時刻を逃した場合に、起動後に実行されます。

### 起動

```bash
sudo systemctl daemon-reload

# 初回は過去の新着が最大 50 件まとめて流れる。避けたいなら件数を絞って 1 度だけ手動実行する
#（env ファイルで ARXIV_MAX_RESULTS を指定していると、そちらが優先される）
sudo systemd-run --wait --uid=arxivbot \
  -p EnvironmentFile=/etc/arxiv-slack-bot.env -E ARXIV_MAX_RESULTS=5 \
  /usr/bin/python3 /opt/arxiv-slack-bot/bot.py

sudo systemctl enable --now arxiv-random.service arxiv-new.timer
```

## 4. 動作確認・運用

```bash
systemctl status arxiv-random.service       # 常駐しているか
systemctl list-timers arxiv-new.timer       # 次回の実行予定
sudo systemctl start arxiv-new.service      # 新着投稿をいま実行する
journalctl -u arxiv-random -u arxiv-new -f  # ログ
```

- Slack の任意のチャンネルで `/arxiv-random` と打ち、論文が返ってくれば常駐側は動いています。
- コードや環境変数を変えたら、`sudo systemctl restart arxiv-random` で再起動します。新着投稿側は次の実行から反映されます。
- `seen.json` を消すと、取得した範囲の新着が再び流れます。
