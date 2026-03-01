# kintone 固有ルール

## 操作の順序（必須）

1. **既存アプリを操作する場合**: 必ず最初に `kintone_get_app_fields` でフィールド定義を取得し、実際のフィールドコードを確認してから操作する
2. **新規アプリにフィールドを追加する場合**: `kintone_add_fields` → `kintone_deploy_app` の順で実行する（デプロイしないと反映されない）
3. **レコード操作**: フィールドコードが不明な場合は先に `kintone_get_app_fields` で確認する

## kintone_add_fields のフィールド定義フォーマット

`fields` パラメータはフィールドコードをキー、定義をバリューとするオブジェクト。各フィールド定義には必ず以下を含めること:

```json
{
  "フィールドコード": {
    "type": "フィールドタイプ",
    "code": "フィールドコード",
    "label": "表示名"
  }
}
```

### 必須プロパティ
- `type`: フィールドタイプ（SINGLE_LINE_TEXT, NUMBER, RADIO_BUTTON, DROP_DOWN, DATE, RICH_TEXT, CHECK_BOX, MULTI_LINE_TEXT 等）
- `code`: フィールドコード（英数字とアンダースコア）
- `label`: 表示ラベル

### フィールドタイプ別の追加必須プロパティ

**RADIO_BUTTON / DROP_DOWN / CHECK_BOX / MULTI_SELECT**:
```json
{
  "status": {
    "type": "DROP_DOWN",
    "code": "status",
    "label": "ステータス",
    "options": {
      "見込み": {"label": "見込み", "index": 0},
      "提案中": {"label": "提案中", "index": 1},
      "受注": {"label": "受注", "index": 2}
    }
  }
}
```
- `options` の各項目に `label`（表示名）と `index`（表示順序、0始まり）が必須

**NUMBER**:
```json
{
  "amount": {
    "type": "NUMBER",
    "code": "amount",
    "label": "金額",
    "unit": "円",
    "unitPosition": "AFTER"
  }
}
```

## kintone_get_records のクエリ構文

- クエリで使用するフィールドコードは、実際にアプリに存在するものだけを使うこと
- 存在しないフィールドコードを指定すると GAIA_IQ11 エラーになる
- クエリ例: `"status in (\"見込み\", \"提案中\") order by updated_time desc limit 10"`

## kintone_add_record / kintone_update_record のレコード形式

レコードのフィールド値はフィールドタイプごとの形式で指定する:

```json
{
  "record": {
    "case_name": {"value": "案件A"},
    "amount": {"value": "100000"},
    "status": {"value": "見込み"},
    "date_field": {"value": "2025-01-15"}
  }
}
```

- すべてのフィールド値は `{"value": "..."}` 形式で指定する
- NUMBER 型でも値は文字列で指定する
- `record_id` は 1 以上の正の整数でなければならない

## kintone_add_records の一括追加

- 最大 100 件まで一括追加可能
- `records` は配列で、各要素は上記のレコード形式と同じ

## よくあるエラーと対処

- `CB_VA01`: 入力値が不正。フィールド定義の必須プロパティ（code, label, options等）が不足していないか確認
- `GAIA_IQ11`: 指定したフィールドが存在しない。`kintone_get_app_fields` でフィールドコードを確認すること
- `CB_IL02`: リクエスト形式が不正
