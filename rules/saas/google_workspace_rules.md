# Google Workspace 固有ルール

## サービス別ツール

Google Workspace は複数サービスで構成される。ツール名のプレフィックスでサービスを判別:
- `gmail_*`: Gmail
- `gcal_*`: Google Calendar
- `gdrive_*`: Google Drive
- `gsheets_*`: Google Sheets

## Gmail

### gmail_send（メール送信）

```json
{
  "to": "recipient@example.com",
  "subject": "件名",
  "body": "本文テキスト"
}
```

#### 注意事項
- `to` は有効なメールアドレスであること
- `body` はプレーンテキスト（HTML不可）
- 送信元は OAuth 認証したユーザーのアドレスになる
- 大量送信はレート制限に注意（1日あたりの送信上限あり）

### gmail_search（メール検索）

```json
{
  "query": "from:sender@example.com subject:報告 after:2025/01/01",
  "max_results": 10
}
```

#### クエリ構文
- `from:`: 送信者
- `to:`: 宛先
- `subject:`: 件名
- `after:YYYY/MM/DD`: 日付以降
- `before:YYYY/MM/DD`: 日付以前
- `has:attachment`: 添付ファイルあり
- `is:unread`: 未読
- 複数条件はスペース区切りで AND

### gmail_read（メール読み取り）

- `message_id` は `gmail_search` で取得した ID を使用する
- プレースホルダの ID は使わない

## Google Calendar

### gcal_create_event（予定作成）

```json
{
  "summary": "会議タイトル",
  "start": "2025-03-01T10:00:00+09:00",
  "end": "2025-03-01T11:00:00+09:00",
  "attendees": ["user1@example.com", "user2@example.com"]
}
```

#### 注意事項
- `start` と `end` は ISO 8601 形式（タイムゾーン付き）: `YYYY-MM-DDTHH:MM:SS+09:00`
- `end` は `start` より後の時刻であること
- `attendees` は任意（メールアドレスの配列）
- 終日イベントの場合は日付のみ: `2025-03-01`

### gcal_list_events（予定一覧）

- `time_min`, `time_max` は ISO 8601 形式
- 指定しない場合は現在時刻以降のイベントが返る

## Google Drive

### gdrive_list_files（ファイル一覧）

```json
{
  "query": "name contains '報告書'",
  "folder_id": "folder-id-xxx"
}
```

#### クエリ構文
- `name contains '検索語'`: ファイル名で検索
- `mimeType = 'application/vnd.google-apps.spreadsheet'`: スプレッドシートのみ
- `mimeType = 'application/vnd.google-apps.document'`: ドキュメントのみ
- `modifiedTime > '2025-01-01T00:00:00'`: 更新日時でフィルタ

### gdrive_read_file（ファイル読み取り）

- `file_id` は `gdrive_list_files` で取得した ID を使用する
- テキスト形式のファイルのみ読み取り可能

## Google Sheets

### gsheets_read（シート読み取り）

```json
{
  "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
  "range": "Sheet1!A1:D10"
}
```

### gsheets_write（シート書き込み）

```json
{
  "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
  "range": "Sheet1!A1",
  "values": [
    ["名前", "部署", "メール"],
    ["山田太郎", "開発部", "yamada@example.com"]
  ]
}
```

#### 注意事項
- `range` は `シート名!セル範囲` 形式（例: `Sheet1!A1:C10`）
- `values` は二次元配列（行の配列）
- 既存データを上書きする（追記ではない）
- `spreadsheet_id` は URL の `/d/` と `/edit` の間の文字列

## 共通の注意事項

- すべてのツールで ID はプレースホルダではなく実際の値を使用すること
- ファイルやメッセージの ID は事前に検索・一覧取得で確認する
- レート制限: Google API は1秒あたりのリクエスト数に制限がある
