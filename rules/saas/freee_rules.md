# freee 会計 固有ルール

## 操作の順序（必須）

1. **取引を作成する前に**: `freee_list_account_items` で勘定科目一覧を取得し、正しい勘定科目IDを確認する
2. **請求書を作成する前に**: `freee_list_partners` で取引先一覧を取得し、正しい partner_id を確認する
3. **消込を実行する前に**: 対象の取引IDと入出金IDが正しいことを確認する

## company_id について

- すべてのツールで `company_id` は必須パラメータ
- company_id はコンテキストから取得すること。不明な場合は操作を計画しない

## freee_create_journal（仕訳作成）

### details 配列の形式

```json
{
  "company_id": 12345,
  "details": [
    {
      "account_item_id": 101,
      "tax_code": 1,
      "amount": 10000,
      "description": "摘要テキスト"
    }
  ]
}
```

### 必須フィールド
- `account_item_id`: 勘定科目ID（`freee_list_account_items` で事前に確認）
- `tax_code`: 税区分コード（1=課税売上, 2=課税仕入等）
- `amount`: 金額（整数、税込）

### 注意事項
- 金額は整数で指定（小数不可）
- 日付は `YYYY-MM-DD` 形式
- 仕訳は貸借が一致する必要がある

## freee_create_invoice（請求書作成）

### items 配列の形式

```json
{
  "company_id": 12345,
  "partner_id": 678,
  "items": [
    {
      "name": "コンサルティング費用",
      "quantity": 1,
      "unit_price": 100000,
      "tax_code": 1
    }
  ]
}
```

### 注意事項
- `partner_id` は `freee_list_partners` で事前に取得すること
- `unit_price` は整数
- 存在しない partner_id を指定すると 404 エラー

## freee_list_journals（仕訳一覧）

- `start_date`, `end_date` は `YYYY-MM-DD` 形式
- 日付を指定しない場合は当月のデータが返る
- 大量データの場合はページネーションに注意（1回で最大100件）

## freee_get_trial_balance（試算表）

- `fiscal_year` は西暦の整数（例: 2025）
- 会計期間が設定されていないとエラーになる

## よくあるエラーと対処

- `invalid_account_item_id`: 勘定科目IDが存在しない → `freee_list_account_items` で確認
- `invalid_partner_id`: 取引先IDが存在しない → `freee_list_partners` で確認
- `validation_error`: 必須パラメータ不足、金額が整数でない等
- `unauthorized`: トークン期限切れ → 再認証
