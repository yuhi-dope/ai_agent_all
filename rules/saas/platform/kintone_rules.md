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

## kintone_add_record のレコード形式

レコードのフィールド値はフィールドタイプごとの形式で指定する:

```json
{
  "app_id": 123,
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
- **DROP_DOWN/RADIO_BUTTON/CHECK_BOX のフィールド値は、そのフィールドの options に定義された値と完全一致させること**

## kintone_update_record のレコード更新

**`record_id` は必須。省略すると CB_VA01 エラーになる。**

```json
{
  "app_id": 123,
  "record_id": 1,
  "record": {
    "status": {"value": "受注"}
  }
}
```

- `record_id` は `kintone_get_records` で取得したレコードの `$id` 値（正の整数）を使用する
- **`record_id` をプレースホルダや推測値にしない**。必ず事前に `kintone_get_records` でレコードを取得し、実際の ID を使用する
- 更新対象のフィールドのみ `record` に含める（全フィールドを含める必要はない）

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

アプリ作成・編集・UI改善時は以下の設計ガイドラインに**必ず従うこと**。
「デザインを改善して」「UIを良くして」等の指示があった場合、コンテキストの現在のレイアウト・フィールド・ビューを分析し、以下のルールに違反している箇所を特定して修正する計画を立てること。

---

### 1. フィールドタイプの選び方

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

### 2. フィールドコードの命名規則

- **スネークケース**を使用: `customer_name`, `order_date`, `total_amount`
- 英数字とアンダースコアのみ
- 意味が明確な名前にする（`field1` のような命名は避ける）

### 3. 選択肢（options）の命名規則

- **ステータス系**: 業務フローの時系列順に並べる（例: 「未着手」→「進行中」→「完了」→「保留」）
- **優先度系**: 重要度順（例: 「高」→「中」→「低」）
- **分類系**: 略称を避け、正式名称を使う
- **Yes/No系**: 「あり/なし」「対象/対象外」（RADIO_BUTTON が適切）

---

### 4. フォームレイアウト設計（最重要）

#### 4.1 カラム幅の基準

kintone のレコード詳細画面はコメント欄（368px）が右側に表示されるため、フォーム有効幅は約 **720〜960px** が最適。

**推奨カラム基準: 120px 単位**

| カラム数 | 幅 | 用途 |
|---------|-----|------|
| 1カラム (120px) | 120px | RADIO_BUTTON, CHECK_BOX, 短い数値 |
| 2カラム (240px) | 240px | SINGLE_LINE_TEXT, DROP_DOWN, DATE, NUMBER |
| 3カラム (360px) | 360px | SINGLE_LINE_TEXT（長め）, LINK |
| 4カラム (480px) | 480px | MULTI_LINE_TEXT, RICH_TEXT（短め） |
| 6カラム (720px) | 720px | MULTI_LINE_TEXT, RICH_TEXT（全幅） |

- 同じ行のフィールドは合計 **720px 以内** に収める
- 関連アプリ間でカラム基準を統一する

#### 4.2 フィールド配置の原則（業務フロー順）

フォーム上のフィールドは**業務の入力順・参照順**に従って配置する:

```
[セクション1: 識別・基本情報]
  → 名前、タイトル、コード、管理番号 等
  → LABEL で「■ 基本情報」のようなセクション見出しを付ける

[セクション2: 分類・ステータス]
  → カテゴリ、ステータス、優先度、担当者 等
  → 関連するフィールドは同じ行に横並び

[セクション3: 業務データ]
  → 金額、数量、日付、期間 等
  → 金額+税率、開始日+終了日 のようなペアは同じ行

[セクション4: 詳細・備考]
  → MULTI_LINE_TEXT / RICH_TEXT は単独行・全幅
  → GROUP で折りたたみ可能にする

[セクション5: システム情報（フッター）]
  → レコード番号、作成日時、更新日時 等
  → 罫線（HR / LABEL）で区切る
```

#### 4.3 横並びの組み合わせパターン

```
推奨パターン（1行あたり）:
  ✓ SINGLE_LINE_TEXT(240px) + DROP_DOWN(240px) + DATE(240px)   = 720px
  ✓ SINGLE_LINE_TEXT(360px) + NUMBER(240px)                    = 600px
  ✓ SINGLE_LINE_TEXT(240px) + SINGLE_LINE_TEXT(240px)          = 480px
  ✓ DATE(240px) + DATE(240px)                                  = 480px（開始〜終了）

非推奨:
  ✗ MULTI_LINE_TEXT を他フィールドと横並び → 単独行にすべき
  ✗ RADIO_BUTTON/CHECK_BOX を横並び → 選択肢が読みにくくなる
  ✗ 4つ以上のフィールドを1行に詰め込む
```

#### 4.4 LABEL を使ったセクション分け

各セクションの先頭に LABEL フィールドを配置し、小見出しを付ける:

```json
{"type": "ROW", "fields": [{"type": "LABEL", "label": "<b>■ 基本情報</b>", "size": {"width": "720"}}]}
```

- 見出しの先頭に統一記号を付ける（■, ●, ▶ 等）
- HTML の `<b>` タグで太字にする
- 幅はフォーム全幅に揃える

#### 4.5 GROUP（フィールドグループ）の活用

**頻繁に参照しない補足情報**は GROUP で折りたたみ可能にする:
- 備考・メモ・履歴
- 管理者向け情報（社内コード、内部メモ等）
- 添付ファイル群

GROUP にはわかりやすいラベルを付け、内部に LABEL で説明を加えると可読性が上がる。

#### 4.6 UI改善タスクでのチェックポイント

「UIを改善して」等の指示があった場合、コンテキストのレイアウトを以下の観点で分析すること:

1. **全フィールドが1行1フィールドで縦に並んでいないか** → 関連フィールドを横並びにする
2. **セクション分けがないか** → LABEL で見出しを追加する
3. **フィールド幅がバラバラか** → 120px 基準に統一する
4. **MULTI_LINE_TEXT が狭い幅で配置されていないか** → 全幅（720px）にする
5. **補足情報が主要情報と混在していないか** → GROUP で折りたたむ
6. **システム情報（レコード番号等）が上部にないか** → フッターに移動する
7. **業務フローと配置順が合っているか** → 入力順に並べ替える

---

### 5. kintone_update_layout（フォームレイアウト更新 API）

レイアウト API でフィールドの配置・幅・グループ化を制御できる:

```json
{
  "app_id": "123",
  "layout": [
    {
      "type": "ROW",
      "fields": [
        {"type": "LABEL", "label": "<b>■ 基本情報</b>", "size": {"width": "720"}}
      ]
    },
    {
      "type": "ROW",
      "fields": [
        {"type": "SINGLE_LINE_TEXT", "code": "customer_name", "size": {"width": "360"}},
        {"type": "DROP_DOWN", "code": "status", "size": {"width": "240"}}
      ]
    },
    {
      "type": "ROW",
      "fields": [
        {"type": "NUMBER", "code": "amount", "size": {"width": "240"}},
        {"type": "DATE", "code": "due_date", "size": {"width": "240"}}
      ]
    },
    {
      "type": "GROUP",
      "code": "detail_group",
      "layout": [
        {
          "type": "ROW",
          "fields": [
            {"type": "MULTI_LINE_TEXT", "code": "notes", "size": {"width": "720", "innerHeight": "120"}}
          ]
        }
      ]
    }
  ]
}
```

**API の注意事項:**
- レイアウト更新時は**全フィールドを指定する**必要がある（未指定フィールドはレイアウトから除外される）
- コンテキストの現在のレイアウトに含まれる全フィールドを漏れなく含めること
- SUBTABLE 内のフィールドはレイアウトに直接含めない（SUBTABLE ごと配置する）
- レイアウト更新後は `kintone_deploy_app` でデプロイが必要

---

### 6. ビュー設計

#### 6.1 基本ルール

- **デフォルトビュー（index: "0"）**: 全件を表示する一覧。主要フィールドを 5〜7 列表示
- **業務ビュー**: よく使うフィルタ条件をビューとして事前定義する
- 一覧に表示するフィールドは**レコードを開かずに判断できる情報**に絞る
- ソートは業務上最も重要な並び順をデフォルトにする

#### 6.2 推奨ビュー構成

```
どのアプリでも以下を基本セットとする:
  1. 全件一覧（デフォルト）: 主要フィールド + 更新日時降順
  2. ステータス別ビュー: 未完了/対応中 等でフィルタ
  3. 担当者別ビュー: 自分が担当のレコード（filterCond に担当者フィールド）
```

#### 6.3 kintone_update_views API

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

**API の注意事項:**
- ビュー更新は**全ビューを置換する**（リクエストに含まれないビューは削除される）
- コンテキストの現在のビュー設定に含まれるビューを漏れなく含めること
- ビュー更新後は `kintone_deploy_app` でデプロイが必要
