# Salesforce 固有ルール

## 操作の順序（必須）

1. **レコード操作の前に**: `sf_describe_object` でオブジェクトのフィールド定義を確認する
2. **SOQL クエリの前に**: 使用するフィールド名が正しいことを `sf_describe_object` で確認する
3. **レコード更新の前に**: `sf_query` で対象レコードの現在の値を取得して確認する

## sf_query（SOQL クエリ）

### SOQL 構文ルール

```sql
SELECT Id, Name, Amount FROM Opportunity WHERE StageName = 'Closed Won' LIMIT 10
```

- フィールド名は大文字小文字を正確に指定する（例: `StageName` であって `stagename` ではない）
- 文字列値はシングルクォートで囲む: `WHERE Name = 'Test'`
- 日付リテラル: `WHERE CreatedDate = TODAY` or `WHERE CreatedDate > 2025-01-01T00:00:00Z`
- LIKE 演算子: `WHERE Name LIKE '%test%'`
- IN 句: `WHERE Status IN ('New', 'Open')`
- NULL チェック: `WHERE Email != null`
- LIMIT は必ず指定する（大量データの取得を防ぐ）

### 日付関数
- `TODAY`, `YESTERDAY`, `TOMORROW`
- `LAST_N_DAYS:30`, `NEXT_N_DAYS:7`
- `THIS_MONTH`, `LAST_MONTH`, `THIS_YEAR`

### 注意事項
- 存在しないフィールドを指定すると `INVALID_FIELD` エラー
- 権限のないオブジェクトにクエリすると `INSUFFICIENT_ACCESS` エラー
- クエリ結果が2000件を超える場合は `queryMore` が必要（現在未対応なので LIMIT を付ける）

## sf_create_record（レコード作成）

### パラメータ形式

```json
{
  "object_type": "Account",
  "fields": {
    "Name": "新規取引先",
    "Industry": "Technology",
    "Phone": "03-1234-5678"
  }
}
```

### 注意事項
- `object_type` は正確な API 名を使用（例: `Account`, `Opportunity`, `Contact`, `Lead`）
- 必須フィールドが欠けると `REQUIRED_FIELD_MISSING` エラー
- `Id` フィールドは自動生成されるため指定しない
- 参照フィールド（Lookup/Master-Detail）は ID で指定: `"AccountId": "001XXXXXXXXXXXXXXX"`

## sf_update_record（レコード更新）

### パラメータ形式

```json
{
  "object_type": "Opportunity",
  "record_id": "006XXXXXXXXXXXXXXX",
  "fields": {
    "StageName": "Closed Won",
    "Amount": 1000000
  }
}
```

### 注意事項
- `record_id` は 15 文字または 18 文字の Salesforce ID
- 不正な ID 形式は `MALFORMED_ID` エラー
- 読み取り専用フィールド（`CreatedDate` 等）は更新不可
- `Id` フィールドは fields に含めない

## sf_describe_object（メタデータ取得）

- 操作対象のオブジェクトのフィールド名・型・必須有無を確認するために使用
- 必ずレコード操作の前に実行して、正しいフィールド名を確認する

## 主要オブジェクト名

| 日本語 | API名 |
|--------|-------|
| 取引先 | Account |
| 取引先責任者 | Contact |
| 商談 | Opportunity |
| リード | Lead |
| ケース | Case |
| 活動 | Task / Event |

## よくあるエラーと対処

- `INVALID_FIELD`: フィールド名が間違っている → `sf_describe_object` で確認
- `MALFORMED_ID`: Salesforce ID の形式が不正（15 or 18文字の英数字）
- `REQUIRED_FIELD_MISSING`: 必須フィールドが未指定 → `sf_describe_object` で required を確認
- `INSUFFICIENT_ACCESS`: 権限不足
- `ENTITY_IS_DELETED`: 削除済みレコードへの操作
