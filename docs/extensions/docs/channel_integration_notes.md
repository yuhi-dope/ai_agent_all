# 4チャネル統合 — 技術実装ノート

営業資料（`docs/sales_deck.md`）で提案する4つの入力チャネルの実装状況と、追加実装に必要な作業の概要。

---

## 現状の実装状況

| チャネル | ステータス | 備考 |
|---------|----------|------|
| **Notion** | 実装済み | Webhook受信 (`POST /webhook/notion`) + DB一括処理 (`POST /run-from-database`) |
| **Slack** | 環境変数のみ定義済み | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_OAUTH_REDIRECT_URI` が `.env.example` に存在 |
| **Google Drive** | 環境変数のみ定義済み | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` が `.env.example` に存在 |
| **Chatwork** | 未着手 | 環境変数の定義も未実施 |

---

## チャネル別 実装設計

### 1. Slack 連携

**方式:** Slack Events API + Bot Token

**必要な実装:**

```
server/
├── slack_handler.py       # Slack Events API のハンドラー
└── main.py                # POST /webhook/slack エンドポイント追加

.env.example に追加:
  SLACK_BOT_TOKEN=xoxb-...
  SLACK_SIGNING_SECRET=...
  SLACK_CHANNEL_ID=...     # AI社員が監視するチャンネル
```

**フロー:**
```
1. ユーザーが Slack チャンネルにメッセージを投稿
2. Slack Events API が POST /webhook/slack に通知
3. Signing Secret で署名検証（Notion Webhook と同様のパターン）
4. メッセージテキストを user_requirement として抽出
5. invoke() でエージェント実行（BackgroundTasks）
6. 完了後、同チャンネルのスレッドに結果を投稿（chat.postMessage）
```

**Slack 固有の考慮事項:**
- Events API の URL Verification（`challenge` パラメータの返却）
- 3秒ルール: Slack は 3秒以内にレスポンスを返さないとリトライする → 即座に 200 を返し、BackgroundTasks で処理
- Bot 自身のメッセージを無視する（無限ループ防止）
- スレッド返信で進捗を段階的に報告可能（spec完了時、impl完了時）

**必要な Slack App 権限 (OAuth Scopes):**
- `channels:history` — チャンネルのメッセージ読取
- `chat:write` — メッセージ投稿
- `files:read` — 添付ファイル読取（仕様書の添付対応時）

---

### 2. Google Drive 連携

**方式:** Google Drive Push Notifications (Webhook) or Polling

**必要な実装:**

```
server/
├── gdrive_handler.py      # Google Drive Webhook / Polling ハンドラー
├── gdrive_client.py       # Google Docs API クライアント
└── main.py                # POST /webhook/gdrive エンドポイント追加

.env.example に追加:
  GOOGLE_CLIENT_ID=...
  GOOGLE_CLIENT_SECRET=...
  GDRIVE_WATCH_FOLDER_ID=...   # 監視対象フォルダ
  GDRIVE_WEBHOOK_SECRET=...    # 署名検証用
```

**フロー:**
```
1. ユーザーが Google Docs に要件ドキュメントを作成/更新
2. Drive Push Notification が POST /webhook/gdrive に通知
   （または定期ポーリングで新規/更新ドキュメントを検出）
3. Google Docs API でドキュメント内容を取得（Markdown変換）
4. ドキュメント本文を user_requirement として抽出
5. invoke() でエージェント実行
6. 完了後、ドキュメントにコメントまたは末尾に結果リンクを追記
```

**Google Drive 固有の考慮事項:**
- Push Notification は有効期限がある（最大 24h） → 定期的な再登録が必要
- ポーリング方式の方がシンプル（5分間隔で Changes API を呼ぶ）
- Google Docs → Markdown 変換が必要（google-docs-to-markdown ライブラリ等）
- フォルダ単位の監視（特定フォルダに入れたドキュメントのみ処理）
- ドキュメントの「ステータス」管理: ファイル名に prefix（[実装希望]）or カスタムプロパティ

**必要な Google API スコープ:**
- `https://www.googleapis.com/auth/drive.readonly` — ファイル読取
- `https://www.googleapis.com/auth/documents.readonly` — Docs内容読取
- `https://www.googleapis.com/auth/drive.file` — 結果書き戻し用（任意）

---

### 3. Chatwork 連携

**方式:** Chatwork Webhook + API

**必要な実装:**

```
server/
├── chatwork_handler.py    # Chatwork Webhook ハンドラー
└── main.py                # POST /webhook/chatwork エンドポイント追加

.env.example に追加:
  CHATWORK_API_TOKEN=...
  CHATWORK_WEBHOOK_TOKEN=...   # Webhook 署名検証用
  CHATWORK_ROOM_ID=...         # AI社員が監視するルーム
```

**フロー:**
```
1. ユーザーが Chatwork の専用ルームにメッセージを投稿
2. Chatwork Webhook が POST /webhook/chatwork に通知
3. Webhook Token で署名検証
4. メッセージ本文を user_requirement として抽出
5. invoke() でエージェント実行（BackgroundTasks）
6. 完了後、同ルームにタスク or メッセージで結果を投稿
```

**Chatwork 固有の考慮事項:**
- Webhook は「ルーム内のメッセージ作成」イベントを受信
- Chatwork API はレートリミット（5分あたり300リクエスト）あり
- タスク機能を活用: メッセージ受信 → タスク作成（進行中） → 完了時にタスク完了
- メンション対応: `[To:BOT_ACCOUNT_ID]` が含まれるメッセージのみ処理（ノイズ排除）
- ファイルアップロード: Chatwork API で成果物ファイルをアップロード可能

**必要な権限:**
- Chatwork API Token（管理者発行）
- Webhook 設定（ルーム単位）

---

## 共通アーキテクチャ: チャネルアダプタパターン

4チャネルを統一的に扱うため、以下のアダプタパターンを推奨:

```python
# server/channel_adapter.py（設計案）

class ChannelMessage:
    """チャネル共通のメッセージ構造"""
    source: str              # "notion" | "slack" | "gdrive" | "chatwork"
    requirement: str         # 要件テキスト
    sender_id: str           # 送信者識別子
    reply_to: dict           # 返信先情報（チャネル固有）

class ChannelAdapter(ABC):
    """チャネルアダプタの基底クラス"""

    async def parse_webhook(self, request) -> ChannelMessage:
        """Webhookペイロードをパース"""
        ...

    async def send_progress(self, reply_to, message: str):
        """進捗を返信"""
        ...

    async def send_result(self, reply_to, run_result):
        """完成結果を返信"""
        ...
```

**メリット:**
- エージェントエンジン（`develop_agent/`）は入力チャネルを意識しない
- 新チャネル追加時はアダプタを1つ追加するだけ
- テスト時にモックアダプタで置き換え可能

---

## 実装優先度（提案）

| 優先度 | チャネル | 理由 |
|--------|---------|------|
| 1 | Notion | 実装済み。ベースライン |
| 2 | Slack | OAuth環境変数が定義済み。Events API は Notion Webhook とほぼ同じパターン |
| 3 | Chatwork | 中小企業での利用率が高い。API がシンプルで実装容易 |
| 4 | Google Drive | Push Notification の有効期限管理がやや複雑。Docs→Markdown変換も必要 |

**見積もり:**
- Slack: 2-3日（Webhook + Bot投稿 + OAuth）
- Chatwork: 2日（Webhook + メッセージ投稿）
- Google Drive: 3-5日（ポーリング + Docs変換 + 書き戻し）
