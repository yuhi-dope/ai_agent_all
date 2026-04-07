# Google Workspace 双方向同期 — インフラセットアップ手順

> **対象**: 自社営業活動用のGWS連携。クライアント企業向けではない。
> **前提**: GCPプロジェクト + Cloud Run が deploy/cloud-run-config.md の通り設定済み。

---

## 1. サービスアカウント設定

### 1-1. サービスアカウント作成（既存のものを使う場合はスキップ）

```bash
export PROJECT_ID=your-project-id
export SA_NAME=shachotwo-gws

gcloud iam service-accounts create $SA_NAME \
  --display-name="ShachoTwo GWS Integration"
```

### 1-2. サービスアカウントキー（JSON）を取得

```bash
gcloud iam service-accounts keys create credentials.json \
  --iam-account=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com
```

> **重要**: `credentials.json` はgitにコミットしない。Cloud Run ではSecret Managerに格納する。

### 1-3. 必要な GCP API を有効化

```bash
gcloud services enable \
  gmail.googleapis.com \
  calendar-json.googleapis.com \
  drive.googleapis.com \
  pubsub.googleapis.com \
  --project=$PROJECT_ID
```

---

## 2. Google Workspace Domain-Wide Delegation（DWD）

サービスアカウントが自社Workspaceユーザーの代理操作をするために必要。

### 2-1. サービスアカウントの Client ID を確認

```bash
gcloud iam service-accounts describe $SA_NAME@$PROJECT_ID.iam.gserviceaccount.com \
  --format="value(uniqueId)"
```

### 2-2. Google Workspace 管理コンソールで設定

1. https://admin.google.com → セキュリティ → API の制御 → ドメイン全体の委任
2. 「新しく追加」をクリック
3. **クライアントID**: 上記で確認した uniqueId
4. **OAuth スコープ**（カンマ区切りで入力）:

```
https://www.googleapis.com/auth/gmail.send,https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets
```

5. 「承認」をクリック

> **注意**: スコープ反映に最大24時間かかる場合がある。

---

## 3. Gmail Watch 用 Pub/Sub トピック設定

Gmail Watch API は Pub/Sub 経由で Push 通知を送る。

### 3-1. トピックとサブスクリプション作成

```bash
export TOPIC_NAME=gmail-push
export SUBSCRIPTION_NAME=gmail-push-sub
export CLOUD_RUN_URL=https://your-cloud-run-url.run.app

# トピック作成
gcloud pubsub topics create $TOPIC_NAME --project=$PROJECT_ID

# Push サブスクリプション作成（Cloud Run の Webhook URL に配信）
gcloud pubsub subscriptions create $SUBSCRIPTION_NAME \
  --topic=$TOPIC_NAME \
  --push-endpoint="$CLOUD_RUN_URL/api/v1/webhooks/gmail-push" \
  --ack-deadline=30 \
  --project=$PROJECT_ID
```

### 3-2. Gmail API にトピックへの Publish 権限を付与

```bash
# Gmail API のサービスアカウントに Publisher 権限を付与
gcloud pubsub topics add-iam-policy-binding $TOPIC_NAME \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project=$PROJECT_ID
```

> `gmail-api-push@system.gserviceaccount.com` は Google 公式のサービスアカウント。変更不要。

---

## 4. Google Drive ルートフォルダ設定

### 4-1. 営業用ルートフォルダを Google Drive で作成

1. Google Drive で「シャチョツー営業」フォルダを作成
2. URL からフォルダID を取得: `https://drive.google.com/drive/folders/{FOLDER_ID}`
3. サービスアカウントのメールアドレスをフォルダに「編集者」として共有

```
共有先: shachotwo-gws@your-project-id.iam.gserviceaccount.com
権限: 編集者
```

---

## 5. 環境変数一覧

Cloud Run（または .env）に設定する環境変数:

| 変数名 | 説明 | 例 |
|---|---|---|
| `GOOGLE_CREDENTIALS_PATH` | サービスアカウントJSONのパス | `/secrets/credentials.json` |
| `GMAIL_DELEGATED_EMAIL` | DWD対象メールアドレス（自社の営業用アカウント） | `sales@yourcompany.com` |
| `SENDER_EMAIL` | メール送信元アドレス | `sales@yourcompany.com` |
| `GOOGLE_DRIVE_DELEGATED_EMAIL` | Drive DWD対象（通常はGMAIL_DELEGATED_EMAILと同じ） | `sales@yourcompany.com` |
| `GOOGLE_DRIVE_ROOT_FOLDER_ID` | 営業用ルートフォルダID | `1a2b3c4d5e6f...` |
| `GOOGLE_CALENDAR_DELEGATED_EMAIL` | Calendar DWD対象 | `sales@yourcompany.com` |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | 営業リストのスプレッドシートID | `abc123def456...` |
| `GMAIL_PUBSUB_TOPIC` | Pub/Sub トピック名 | `projects/your-project/topics/gmail-push` |
| `GWS_CALENDAR_WEBHOOK_URL` | Calendar Watch の通知先URL | `https://your-cloud-run.run.app/api/v1/webhooks/calendar-push` |
| `ENABLE_BPO_ORCHESTRATOR` | オーケストレータ有効化（Watch自動更新含む） | `1` |

---

## 6. 動作確認チェックリスト

設定完了後、以下の順序で確認:

- [ ] **Health Check**: `curl https://your-url/health` → `{"status": "ok"}`
- [ ] **Gmail 送信テスト**: Gmail コネクタの health_check API → `true`
- [ ] **Drive テスト**: ルートフォルダにテストファイルをアップロード → Drive に表示される
- [ ] **Calendar テスト**: テストイベント作成 → Google Calendar に表示される
- [ ] **Gmail Watch 登録**: オーケストレータ起動後、watch_channels テーブルに gmail レコードが作成される
- [ ] **Calendar Watch 登録**: 同上、calendar レコードが作成される
- [ ] **メール受信テスト**: テストメール送信 → Pub/Sub → webhooks/gmail-push → email_classifier が分類
- [ ] **Calendar Push テスト**: イベント作成/終了 → webhooks/calendar-push → event_listener が発火

---

## 7. トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| `403 Forbidden` | DWD 未設定 or スコープ不足 | §2 を再確認。スコープ反映に最大24時間 |
| `404 Not Found (topic)` | Pub/Sub トピック未作成 | §3-1 を実行 |
| `Permission denied on topic` | Publisher 権限未付与 | §3-2 を実行 |
| Gmail Watch が切れる | 有効期限7日。オーケストレータ未起動 | `ENABLE_BPO_ORCHESTRATOR=1` を確認 |
| Drive にファイルが見えない | サービスアカウントにフォルダ共有されていない | §4-1 の共有設定を確認 |
| Calendar イベントが見えない | DWD 対象アカウントが違う | `GOOGLE_CALENDAR_DELEGATED_EMAIL` を確認 |

---

## 8. Watch 有効期限について

| サービス | 最大有効期限 | 自動更新タイミング |
|---|---|---|
| Gmail Watch | 7日 | 毎日 07:00 に期限チェック、6日目に更新 |
| Calendar Watch | 30日 | 毎日 07:00 に期限チェック、25日目に更新 |

オーケストレータ（`ENABLE_BPO_ORCHESTRATOR=1`）が起動していれば自動で更新される。
手動更新が必要な場合は watch_channels テーブルの `is_active` を `false` にすると次回ループで再登録される。
