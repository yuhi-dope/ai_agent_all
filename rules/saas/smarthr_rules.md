# SmartHR 固有ルール

## 操作の順序（必須）

1. **従業員を登録する前に**: `smarthr_list_departments` で部署一覧を取得し、正しい department_id を確認する
2. **従業員情報を更新する前に**: `smarthr_get_crew` で現在の情報を確認する
3. **雇用形態を指定する場合**: `smarthr_list_employment_types` で雇用形態一覧を確認する

## smarthr_create_crew（従業員登録）

### パラメータ形式

```json
{
  "last_name": "山田",
  "first_name": "太郎",
  "email": "yamada@example.com",
  "department_id": "dept-xxx-yyy"
}
```

### 必須フィールド
- `last_name`: 姓
- `first_name`: 名
- `email`: メールアドレス（重複不可）

### 注意事項
- 既に同じメールアドレスの従業員が存在するとエラー
- `department_id` は `smarthr_list_departments` で取得した値を使用する
- プレースホルダの ID（`dept-xxx` 等）は絶対に使わない

## smarthr_update_crew（従業員更新）

### パラメータ形式

```json
{
  "crew_id": "crew-xxx-yyy",
  "fields": {
    "last_name": "新しい姓",
    "department_id": "dept-zzz"
  }
}
```

### 注意事項
- `crew_id` は `smarthr_list_crews` または `smarthr_get_crew` で取得した実際の ID を使用
- 更新対象のフィールドのみを `fields` に含める
- 存在しない crew_id を指定すると 404 エラー

## smarthr_list_crews（従業員一覧）

### パラメータ
- `page`: ページ番号（1始まり）
- `per_page`: 1ページあたりの件数（デフォルト: 10、最大: 100）
- `status`: フィルタ（`employed` = 在職中、`resigned` = 退職済み等）

### 注意事項
- 大量の従業員がいる場合はページネーションが必要
- `per_page` の最大値は 100

## smarthr_get_payroll_statement（給与明細）

### パラメータ
- `crew_id`: 対象従業員の ID
- `year`: 西暦年（整数、例: 2025）
- `month`: 月（整数、1-12）

### 注意事項
- 給与明細が存在しない月を指定すると空の結果が返る
- 権限のない従業員の給与明細は取得不可

## よくあるエラーと対処

- `404 Not Found`: crew_id や department_id が存在しない → 一覧取得で確認
- `422 Unprocessable Entity`: バリデーションエラー（メールアドレス重複、必須フィールド不足等）
- `401 Unauthorized`: トークン期限切れ → 再認証
