# Slack 固有ルール

## 操作の順序（必須）

1. **メッセージ送信の前に**: `slack_list_channels` でチャンネル一覧を取得し、正しいチャンネル ID を確認する
2. **リアクション追加の前に**: `slack_get_channel_history` で対象メッセージの timestamp を確認する

## チャンネル ID について

- チャンネル名（`#general` 等）ではなく、チャンネル ID（`C01XXXXXXXX` 形式）を使用する
- `slack_list_channels` で取得した `id` フィールドを使用すること
- プレースホルダや推測した ID は使わない

## slack_send_message（メッセージ送信）

### パラメータ形式

```json
{
  "channel": "C01XXXXXXXX",
  "text": "メッセージ本文"
}
```

### 注意事項
- `channel` はチャンネル ID（`C` で始まる文字列）
- `text` は必須（`blocks` のみでは通知に本文が表示されない）
- メンション: `<@U01XXXXXXXX>` 形式でユーザー ID を指定
- リンク: `<https://example.com|表示テキスト>` 形式
- Bot がチャンネルに参加していないと `not_in_channel` エラー

### blocks パラメータ（任意）

リッチなメッセージを送る場合に使用:

```json
{
  "channel": "C01XXXXXXXX",
  "text": "フォールバックテキスト",
  "blocks": [
    {
      "type": "section",
      "text": {"type": "mrkdwn", "text": "*太字* と `コード`"}
    }
  ]
}
```

## slack_list_channels（チャンネル一覧）

### パラメータ
- `types`: チャンネルタイプ（デフォルト: `public_channel`）
  - `public_channel`: パブリックチャンネル
  - `private_channel`: プライベートチャンネル（Bot が参加しているもののみ）

## slack_get_channel_history（メッセージ履歴）

### パラメータ
- `channel`: チャンネル ID（必須）
- `limit`: 取得件数（デフォルト: 20）

### 注意事項
- 最新のメッセージから降順で返る
- `timestamp`（`ts`）はメッセージの一意識別子として使用される

## slack_add_reaction（リアクション追加）

### パラメータ形式

```json
{
  "channel": "C01XXXXXXXX",
  "timestamp": "1234567890.123456",
  "name": "thumbsup"
}
```

### 注意事項
- `timestamp` は `slack_get_channel_history` で取得した `ts` 値
- `name` はコロンなしの絵文字名（`:thumbsup:` ではなく `thumbsup`）

## slack_upload_file（ファイルアップロード）

### パラメータ形式

```json
{
  "channels": "C01XXXXXXXX",
  "content": "ファイルの内容",
  "filename": "report.txt"
}
```

### 注意事項
- `channels` はチャンネル ID
- `content` はテキストコンテンツ（バイナリファイルは非対応）

## よくあるエラーと対処

- `not_in_channel`: Bot がチャンネルに参加していない → チャンネルに Bot を招待する必要がある
- `channel_not_found`: チャンネル ID が不正 → `slack_list_channels` で確認
- `invalid_auth`: トークン期限切れ → 再認証
- `ratelimited`: レート制限 → しばらく待ってから再試行（Tier 1: 1回/秒、Tier 2: 20回/分）

---

## UI/UX 設計ルール（メッセージの読みやすさ）

### メッセージ構成の原則

- **結論を先に**: 最初の1行で要旨を伝え、詳細は後に続ける
- **長文は避ける**: 1メッセージは 5〜10行以内。長くなる場合は blocks で構造化するか、ファイルとしてアップロード
- **アクションを明確に**: 依頼事項がある場合は「対応が必要な項目」を箇条書きで明示

### mrkdwn フォーマットの使い分け

```
*太字*: 重要な情報、見出し
_斜体_: 補足説明
`コード`: 技術的な値、ID、コマンド
> 引用: 他メッセージの引用、補足情報
```

### blocks を使うべき場面

- **通知・レポート**: ヘッダー + セクション + 区切り線で構造化
- **表形式のデータ**: セクションブロックで整理（Slack にテーブルはないため、等幅テキストか箇条書きで代替）
- **アクション付きメッセージ**: ボタンやリンク付きのインタラクティブメッセージ

### blocks 構成例（レポート通知）

```json
{
  "blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": "日次レポート"}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "*売上*: 1,234,567円\n*件数*: 15件"}},
    {"type": "divider"},
    {"type": "section", "text": {"type": "mrkdwn", "text": "詳細は <https://example.com|こちら> を参照"}}
  ]
}
```

### チャンネル選択の考慮

- 業務連絡は適切なチャンネルに送る（全体チャンネルへの不要な通知を避ける）
- メンションは本当に必要な場合のみ使用（`@channel` や `@here` は慎重に）
