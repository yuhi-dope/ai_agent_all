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

---

## UI/UX 設計ルール（フィールド・レイアウト・ビュー）

アプリ作成・フィールド追加時は以下の設計ガイドラインに従うこと。

### フィールドタイプの選び方

**選択肢フィールドの判断基準:**

```
単一選択？
  ├─ YES → 選択肢 5個以下 → RADIO_BUTTON（一覧性が高い）
  │        選択肢 6個以上 → DROP_DOWN（省スペース）
  └─ NO（複数選択）→ 選択肢 5個以下 → CHECK_BOX
                      選択肢 6個以上 → MULTI_SELECT
```

- RADIO_BUTTON は未選択にできない。「未選択」が必要なら DROP_DOWN を使う
- 選択肢が将来増える可能性があるなら DROP_DOWN / MULTI_SELECT が適切
- 自由入力で表記ゆれが起きやすい項目は、選択肢フィールドにする

**テキスト系:**
- 短い文字列（名前・電話番号等）→ SINGLE_LINE_TEXT
- 長い文字列（備考・説明等）→ MULTI_LINE_TEXT
- 書式付きテキスト → RICH_TEXT

**日付・数値:**
- 日付 → DATE（SINGLE_LINE_TEXT にしない）
- 日時 → DATETIME
- 金額・数量 → NUMBER（unit, unitPosition で単位を設定）

### フィールドコードの命名規則

- **スネークケース**を使用: `customer_name`, `order_date`, `total_amount`
- 英数字とアンダースコアのみ
- 意味が明確な名前にする（`field1` のような命名は避ける）

### 選択肢（options）の命名規則

- **ステータス系**: 業務フローの時系列順に並べる（例: 「未着手」→「進行中」→「完了」→「保留」）
- **優先度系**: 重要度順（例: 「高」→「中」→「低」）
- **分類系**: 略称を避け、正式名称を使う
- **Yes/No系**: 「あり/なし」「対象/対象外」（RADIO_BUTTON が適切）

### フィールド配置の原則

フィールド追加時は以下の順序で定義すること（kintone は定義順にフォームに配置される）:

1. **識別情報**（名前、タイトル、コード等）を最初に
2. **分類・ステータス**（カテゴリ、ステータス、優先度等）
3. **詳細情報**（金額、数量、日付等の業務データ）
4. **補足情報**（備考、メモ等）を最後に

### kintone_update_layout（フォームレイアウト更新）

レイアウト API でフィールドの配置・幅・グループ化を制御できる:

```json
{
  "app_id": "123",
  "layout": [
    {
      "type": "ROW",
      "fields": [
        {"type": "SINGLE_LINE_TEXT", "code": "customer_name", "size": {"width": "250"}},
        {"type": "DROP_DOWN", "code": "status", "size": {"width": "150"}}
      ]
    },
    {
      "type": "ROW",
      "fields": [
        {"type": "NUMBER", "code": "amount", "size": {"width": "200"}},
        {"type": "DATE", "code": "due_date", "size": {"width": "150"}}
      ]
    },
    {
      "type": "GROUP",
      "code": "detail_group",
      "layout": [
        {"type": "ROW", "fields": [{"type": "MULTI_LINE_TEXT", "code": "notes", "size": {"width": "500", "innerHeight": "100"}}]}
      ]
    }
  ]
}
```

**レイアウト設計のルール:**
- 関連フィールドは同じ行に横並び配置（例: 姓と名、開始日と終了日）
- 1行あたり 2〜3 フィールドが見やすい
- MULTI_LINE_TEXT / RICH_TEXT は単独行で幅を広くとる
- 補足情報は GROUP で折りたたみ可能にする
- **注意**: レイアウト更新時は全フィールドを指定する必要がある（未指定フィールドはレイアウトから除外される）

### kintone_update_views（ビュー設定）

アプリ作成時は用途に応じたビューも設定すること:

```json
{
  "app_id": "123",
  "views": {
    "全件一覧": {
      "index": "0",
      "type": "LIST",
      "name": "全件一覧",
      "fields": ["record_number", "customer_name", "status", "amount", "due_date"],
      "sort": "updated_time desc"
    },
    "未対応": {
      "index": "1",
      "type": "LIST",
      "name": "未対応",
      "fields": ["record_number", "customer_name", "status", "due_date"],
      "filterCond": "status in (\"未着手\", \"進行中\")",
      "sort": "due_date asc"
    }
  }
}
```

**ビュー設計のルール:**
- デフォルトビューは全件を表示する一覧にする
- よく使うフィルタ条件はビューとして事前定義する
- 一覧に表示するフィールドはレコードを開かずに判断できる情報に絞る（5〜7フィールド程度）
- ソートは業務上最も重要な並び順をデフォルトにする
- **注意**: ビュー更新は全ビューを置換する（リクエストに含まれないビューは削除される）
