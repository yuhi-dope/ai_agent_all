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

---

## UI/UX 設計ルール

### Gmail — メールの読みやすさ

- **件名**: 具体的かつ簡潔に（「【依頼】3/15 までに見積書のご確認をお願いします」のように種別・期限・要旨を含める）
- **本文構成**: 宛名 → 要旨（1〜2文）→ 詳細 → アクション依頼 → 署名
- **箇条書き活用**: 複数の依頼事項は箇条書きで整理
- **改行**: 段落間に空行を入れて読みやすくする

### Google Calendar — 予定の品質

- **タイトル**: 会議の目的がわかる名称にする（「打ち合わせ」ではなく「Q2 営業戦略レビュー」）
- **時間**: 30分 or 60分 単位で設定。5分前開始等の中途半端な時間は避ける
- **説明欄**: アジェンダ・事前資料リンク・参加者の役割を記載
- **参加者**: 必要最小限にする

### Google Sheets — スプレッドシートの設計

- **ヘッダー行**: 1行目は必ずヘッダー（列名）にする。太字・背景色で視覚的に区別
- **データ型の統一**: 同じ列のデータ型は統一する（数値列に文字列を混ぜない）
- **日付フォーマット**: `YYYY-MM-DD` or `YYYY/MM/DD` で統一
- **金額フォーマット**: 数値として入力（「¥」「円」は含めない。表示形式で対応）
- **シート名**: 内容がわかる名前にする（「Sheet1」のままにしない）

```
良い例:
| 日付       | 取引先     | 売上     | 担当者   |
|------------|-----------|----------|---------|
| 2025-03-01 | A社       | 500000   | 田中    |

悪い例:
| data1      | data2     | data3    | data4   |
|------------|-----------|----------|---------|
| 3/1        | A社       | ¥500,000 | 田中    |
```
