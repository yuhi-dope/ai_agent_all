# kintone 固有ルール

## 最重要ルール（必ず守ること）

1. **既存フィールドを重複追加しない**: コンテキストに「フィールド定義」が含まれている場合、そこに記載されたフィールドは既に存在する。`kintone_add_fields` で再追加すると `CB_VA01: フィールドコードが重複` エラーになる。
2. **選択肢の値は正確に一致させる**: DROP_DOWN/RADIO_BUTTON/CHECK_BOX/MULTI_SELECT フィールドにレコード値を設定する際、コンテキストの `options` に記載された正確な値のみ使用すること。推測した値（例: 「高」「中」「低」）は使わない。
3. **クエリ演算子はフィールドタイプに合わせる**: DROP_DOWN/CHECK_BOX 等には `=` ではなく `in` を使うこと（後述）。

## 操作の順序（必須）

1. **既存アプリにレコードを追加/更新する場合**: コンテキストのフィールド定義を参照し、既存フィールドに合わせた操作のみ計画する。フィールド追加は不要。
2. **新規アプリにフィールドを追加する場合**: `kintone_add_fields` → `kintone_deploy_app` の順で実行する（デプロイしないと反映されない）
3. **レコード操作**: コンテキストのフィールド定義を参照し、正しいフィールドコード・フィールドタイプ・選択肢を使用する

## kintone_add_fields のフィールド定義フォーマット

**注意: コンテキストに既存フィールド定義がある場合、そのフィールドは追加しないこと。**

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

- クエリで使用するフィールドコードは、コンテキストのフィールド定義に存在するものだけを使うこと
- 存在しないフィールドコードを指定すると GAIA_IQ11 エラーになる

### フィールドタイプ別の使用可能な演算子

| フィールドタイプ | 使用可能な演算子 | 使用不可の演算子 |
|---|---|---|
| SINGLE_LINE_TEXT, MULTI_LINE_TEXT, RICH_TEXT | `=`, `!=`, `like`, `not like` | |
| NUMBER | `=`, `!=`, `>`, `<`, `>=`, `<=` | |
| DROP_DOWN, RADIO_BUTTON | `in`, `not in` | `=`, `!=`（GAIA_IQ03 エラー） |
| CHECK_BOX, MULTI_SELECT | `in`, `not in` | `=`, `!=`（GAIA_IQ03 エラー） |
| DATE, DATETIME | `=`, `!=`, `>`, `<`, `>=`, `<=` | |
| STATUS（プロセス管理） | `=`, `!=` | |

**重要**: DROP_DOWN, RADIO_BUTTON, CHECK_BOX, MULTI_SELECT 型のフィールドには `=` 演算子を使用できない。必ず `in` / `not in` を使うこと。

### クエリ例

```
# DROP_DOWN / RADIO_BUTTON フィールド → in を使う
"status in (\"見込み\", \"提案中\") order by updated_time desc limit 10"

# テキストフィールド → = や like を使える
"customer_name like \"田中\" limit 10"

# 数値フィールド
"amount >= 100000 limit 10"

# 複合条件
"status in (\"見込み\") and amount >= 50000 order by created_time desc limit 20"
```

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
- **DROP_DOWN/RADIO_BUTTON/CHECK_BOX のフィールド値は、そのフィールドの options に定義された値と完全一致させること**

## kintone_add_records の一括追加

- 最大 100 件まで一括追加可能
- `records` は配列で、各要素は上記のレコード形式と同じ
- **新規フィールド追加と同じ計画内でレコードを追加する場合、add_fields の options に定義した値と同じ値をレコードに使用すること**

## よくあるエラーと対処

- `CB_VA01`: 入力値が不正。以下を確認:
  - フィールド定義の必須プロパティ（code, label, options等）が不足していないか
  - **フィールドコードが既存フィールドと重複していないか**（コンテキストのフィールド定義を確認）
  - **レコード値が選択肢に存在するか**（コンテキストの options を確認）
- `GAIA_IQ11`: 指定したフィールドが存在しない。コンテキストのフィールド定義でフィールドコードを確認すること
- `GAIA_IQ03`: 演算子がフィールドタイプに対応していない。DROP_DOWN/CHECK_BOX 等は `=` ではなく `in` を使うこと
- `CB_IL02`: リクエスト形式が不正
