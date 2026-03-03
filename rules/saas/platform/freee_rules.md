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

---

## UI/UX 設計ルール（会計データ品質）

### 仕訳の品質ルール

- **摘要（description）**: 取引内容がわかる具体的な記述にする（「支払い」ではなく「3月分オフィス家賃 支払い」）
- **勘定科目**: `freee_list_account_items` で確認した正式名称を使用する。推測しない
- **税区分**: 取引内容に応じた正しい税区分を選択する
  - 1: 課税売上（10%）
  - 2: 課税仕入（10%）
  - 9: 非課税
  - 0: 対象外
- **金額**: 税込額を整数で入力する（1円未満の端数は四捨五入）
- **日付**: 取引の発生日（請求日ではなく納品日やサービス提供日）を入力

### 請求書の品質ルール

- **品目名**: 具体的に記述する（「コンサルティング費用 2025年3月分」等、期間を含める）
- **数量と単価**: 分離して入力する（合計額のみを 1 x 合計額 としない）
- **取引先**: `freee_list_partners` で既存の取引先を確認してから使用。新規作成は極力避ける

### 勘定科目の選択指針

| 取引内容 | 勘定科目例 |
|---------|-----------|
| オフィス家賃 | 地代家賃 |
| 従業員給与 | 給料手当 |
| 外注費 | 外注費 or 業務委託費 |
| 交通費 | 旅費交通費 |
| 接待飲食 | 接待交際費 |
| ソフトウェア利用料 | 通信費 or 支払手数料 |
| 備品購入 | 消耗品費（10万円未満）or 工具器具備品 |

**注意**: 上記は一般的な例。実際の勘定科目は `freee_list_account_items` で確認すること
