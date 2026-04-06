# Kintone製造業リスト取得 — 要件定義書

> **対象リポジトリ**: `shachotwo-app/`
> **作成日**: 2026-04-01
> **ステータス**: 策定中

---

## 1. 概要・目的

kintone に蓄積された製造業ターゲット企業リストを、シャチョツーの `leads` テーブルへ取り込み、SFA・マーケティング自動化に活用する。

### ゴール
- kintone アプリのレコードを **定義済みフィールドコードでマッピング** して leads に upsert
- 10件プローブ後に全件取り込む **安全な2フェーズ設計**
- 取り込んだリードに **セグメント分類**（売上/利益/従業員/サブ業種）を自動付与
- 運用担当者が **ノーコードで設定・実行** できる UI を提供

---

## 2. 実装済み範囲（確認済み）

| コンポーネント | ファイル | 状態 |
|---|---|---|
| Kintone APIアダプター | `workers/connector/kintone.py` | ✅ 完了 |
| importロジック（全件+プローブ） | `workers/bpo/sales/kintone_manufacturing_import.py` | ✅ 完了 |
| セグメント分類 | `workers/bpo/sales/segmentation.py` | ✅ 完了 |
| APIエンドポイント | `POST /marketing/manufacturing/import-kintone` | ✅ 完了 |
| コネクタ登録UI（subdomain/api_token） | `frontend/settings/connectors/page.tsx` | ✅ 完了 |
| import実行UI（app_id入力） | `frontend/marketing/targets/page.tsx` | ✅ 完了 |
| ユニットテスト | `tests/.../test_kintone_manufacturing_import.py` | ✅ 完了 |

---

## 3. 未実装一覧（残り）

### 優先度定義
- **P0**: PMF検証に必須（なければ動かない）
- **P1**: 運用品質に必須（なければ不便・壊れる）
- **P2**: 利便性向上（あると良い）
- **P3**: Phase 2+（今は切る）

---

### 3-1. kintone アプリ一覧取得 API ｜ P1

**問題**: 現状、運用担当者は kintone 管理画面からアプリ ID を手動確認して入力する必要がある。誤入力が多い。

**要件**:

```
GET /connectors/kintone/apps
```

- `tool_connections` から認証情報を取得（`resolve_kintone_credentials` 流用）
- kintone API: `GET https://{subdomain}.cybozu.com/k/v1/apps.json` を呼ぶ
- レスポンス:
  ```json
  {
    "apps": [
      { "appId": "122", "name": "製造業ターゲットリスト", "spaceId": null },
      { "appId": "45",  "name": "顧客管理", "spaceId": "3" }
    ]
  }
  ```
- エラー: kintone 未接続なら 400、API 障害なら 502

**フロントエンド対応**:
- `marketing/targets/page.tsx` のアプリ ID 入力欄を **ドロップダウン選択** に変更
- 「アプリを読み込む」ボタンで API 呼び出し → 一覧表示

**実装箇所**:
- `workers/connector/kintone.py` に `list_apps()` メソッド追加
- `routers/marketing.py` に GET エンドポイント追加（または `routers/connector.py` に追加）

---

### 3-2. kintone フィールド一覧取得 API ｜ P1

**問題**: kintone のフィールドコードはアプリごとに異なる（例: `company_name` / `企業名` / `会社名`）。現状のデフォルト設定（`_DEFAULT_FIELD_CODES`）と一致しない場合、全件スキップされてもエラーが出ない。

**要件**:

```
GET /connectors/kintone/apps/{app_id}/fields
```

- kintone API: `GET https://{subdomain}.cybozu.com/k/v1/app/form/fields.json?app={app_id}`
- レスポンス:
  ```json
  {
    "fields": [
      { "code": "name", "label": "会社名", "type": "SINGLE_LINE_TEXT" },
      { "code": "corporate_number", "label": "法人番号", "type": "SINGLE_LINE_TEXT" },
      { "code": "kaisha_mei", "label": "企業名カナ", "type": "SINGLE_LINE_TEXT" }
    ]
  }
  ```

**フロントエンド対応**:
- アプリ選択後に「フィールドを確認」ボタン → フィールド一覧をモーダルまたはサイドパネルで表示
- 必須フィールド（`name`, `corporate_number`）がアプリに存在するかを自動チェック。欠如している場合は警告表示

**実装箇所**:
- `workers/connector/kintone.py` に `list_fields(app_id)` メソッド追加
- `routers/marketing.py` または `routers/connector.py` に GET エンドポイント追加

---

### 3-3. カスタムフィールドマッピング設定 ｜ P1

**問題**: kintone アプリのフィールドコードが `_DEFAULT_FIELD_CODES` と異なる場合、正常に取り込めない。現状は「アプリのフィールドコードをデフォルトと一致させてください」という運用回避になっているが、顧客のアプリを改変させるのは現実的でない。

**要件**:

```
# kintone アプリごとのフィールドマッピング設定（DB保存）
テーブル: kintone_field_mappings
  - id: UUID
  - company_id: UUID
  - app_id: TEXT           -- kintone アプリ ID
  - field_mappings: JSONB  -- {"name": "会社名", "corporate_number": "法人番号", ...}
  - created_at / updated_at
```

- **API**:
  - `POST /connectors/kintone/apps/{app_id}/mappings` — マッピング保存
  - `GET  /connectors/kintone/apps/{app_id}/mappings` — マッピング取得
- **import ロジック変更**:
  - `import_manufacturing_leads_from_kintone()` にオプション引数 `field_mappings: dict | None = None` を追加
  - `field_mappings` 指定時は `kintone_record_to_flat()` でコードを変換してから flat へマップ
- **フロントエンド**:
  - フィールド一覧表示後に「leads フィールド ↔ kintone フィールドコード」のマッピング設定 UI

**DB マイグレーション追加**: `db/migrations/` に `kintone_field_mappings` テーブル追加

---

### 3-4. ジョブステータス確認 API + UI ｜ P1

**問題**: 現状は fire-and-forget のバックグラウンドタスクで実行され、`job_id` だけ返る。取り込み状況・エラーを確認する手段がない。

**要件**:

```
# ジョブ状態管理テーブル
テーブル: background_jobs
  - id: UUID (job_id)
  - company_id: UUID
  - job_type: TEXT     -- "kintone_mfg_import" | "gbizinfo_load" | ...
  - status: TEXT       -- "queued" | "running" | "completed" | "failed"
  - payload: JSONB     -- リクエストパラメータ
  - result: JSONB      -- 完了結果（total_received, total_upsert_ok, total_skipped）
  - error_message: TEXT
  - started_at / completed_at / created_at
```

- `POST /marketing/manufacturing/import-kintone` — job レコード INSERT 後にバックグラウンドタスク起動
- バックグラウンドタスク内で status を `running` → `completed` / `failed` に更新
- **GET エンドポイント追加**:
  ```
  GET /jobs/{job_id}
  GET /jobs?job_type=kintone_mfg_import&limit=20
  ```
- **フロントエンド**:
  - import 実行後に「ジョブ状態」をポーリング表示（3秒間隔、最大5分）
  - 完了時: `total_received`, `upsert_ok`, `skipped` の数字を表示
  - 失敗時: エラーメッセージを表示 + 「再実行」ボタン

---

### 3-5. ドライラン結果プレビュー UI ｜ P2

**問題**: `dry_run=true` でも結果がログにしか出力されず、UI では確認できない。

**要件**:
- 3-4 のジョブ状態と連動
- dry_run 完了時に `result.preview_rows`（先頭20件のマッピング結果）を返す
- UI でテーブル表示（会社名・法人番号・セグメント・priority_tier）
- 確認後に「本実行する」ボタン

---

### 3-6. 定期自動 import (Cron) ｜ P2

**問題**: 現状は手動実行のみ。kintone データが随時更新される場合、毎回手動実行が必要。

**要件**:
- `kintone_sync_schedules` テーブルで設定管理
  ```
  - app_id, cron_expression, enabled, last_run_at, next_run_at
  ```
- スケジュール例: 毎日 AM 2:00 に全件差分 upsert
- 差分取得クエリ: `kintone` 側の `更新日時 > last_run_at` でフィルタ
- Cloud Run Jobs または FastAPI の `apscheduler` で実行
- UI: 自動実行の ON/OFF・時刻設定

---

### 3-7. 建設業 kintone import 対応 ｜ P1

**問題**: 現状は製造業専用。建設業も同様に kintone からリードを取り込む需要がある。

**既存コード**: `kintone_manufacturing_import.py` は製造業固定（`industry: "manufacturing"` ハードコード）。

**要件**:
- `kintone_manufacturing_import.py` を **業界ジェネリック化** するか、`kintone_construction_import.py` を追加
- 業界判定フィールド: `industry: "construction"` | `"manufacturing"` | ...
- 建設業用フィールドコード: `contractor_license_number`（建設業許可番号）, `main_work_type`（主要工種）, `permit_expiry_date` を追加
- セグメント分類: 建設業用の `segmentation_construction.py` を作成（売上区分・許可区分で分類）
- APIエンドポイント追加: `POST /marketing/construction/import-kintone`

**建設業固有フィールドコード（デフォルト）**:

| leads フィールド | kintone フィールドコード | 説明 |
|---|---|---|
| `company_name` | `name` | 会社名 |
| `corporate_number` | `corporate_number` | 法人番号 |
| `industry` | 固定値 `"construction"` | 業界 |
| `sub_industry` | `main_work_type` | 主要工種（土木/建築/設備） |
| `prefecture` | `prefecture` | 都道府県 |
| `address` | `address` | 住所 |
| `phone` | `phone` | 電話番号 |
| `employee_count` | `employee_count` | 従業員数 |
| `annual_revenue` | `annual_revenue` | 年商 |
| `website_url` | `website_url` | HP |
| `representative` | `representative` | 代表者名 |
| `contractor_license_number` | `contractor_license` | 建設業許可番号 |
| `permit_expiry_date` | `permit_expiry` | 許可有効期限 |

---

### 3-8. import 失敗通知 ｜ P2

**問題**: バックグラウンドタスクの失敗はログにしか出力されず、運用担当者が気づかない。

**要件**:
- 失敗時に Slack 通知（`workers/connector/slack.py` を流用）
- 通知内容: job_id, app_id, エラーメッセージ, 発生時刻
- 通知チャンネル: `tool_connections` の Slack 設定から取得（なければスキップ）

---

### 3-9. 取り込みレコード数上限 / レート制御 ｜ P2

**問題**: kintone APIの制限（1リクエスト500件、1日10,000リクエスト等）を超えた場合のハンドリングが不十分。

**要件**:
- `kintone_manufacturing_import.py` にレート制限検出を追加（HTTP 429 の retry-after 対応）
- 取り込み上限設定: `max_records: int = 50000`（超えた場合は警告で停止）
- バッチ間の sleep を設定値化: `batch_delay_sec: float = 0.15`

---

## 4. データフロー（全体）

```
[kintone アプリ]
    │
    │ ① GET /k/v1/apps.json（アプリ一覧）
    │ ② GET /k/v1/app/form/fields.json（フィールド確認）
    ▼
[KintoneConnector]  ←→  カスタムマッピング設定（kintone_field_mappings）
    │
    │ ③ GET /k/v1/records.json（10件プローブ）
    │ ④ GET /k/v1/records.json（全件 $id ページング）
    ▼
[kintone_manufacturing_import.py / kintone_construction_import.py]
    │  kintone_record_to_flat()  →  map_flat_to_lead_row()
    │  classify_company()（segmentation）
    ▼
[leads テーブル] upsert on_conflict=(company_id, corporate_number)
    │
    ▼
[background_jobs テーブル]  →  UI ジョブ状態確認  →  Slack 通知
```

---

## 5. 実装優先順位・ロードマップ

### Phase 1（MVP〜PMF検証 / 今すぐ）

| # | 機能 | 優先度 | 実装目安 |
|---|---|---|---|
| 3-4 | ジョブステータス確認 API + UI | P1 | `background_jobs` テーブル + GET エンドポイント + フロントポーリング |
| 3-7 | 建設業 import 対応 | P1 | `kintone_construction_import.py` 追加 + エンドポイント追加 |
| 3-1 | アプリ一覧取得 API | P1 | `list_apps()` + GET エンドポイント + UI ドロップダウン化 |
| 3-2 | フィールド一覧取得 API | P1 | `list_fields()` + GET エンドポイント + 必須フィールド警告UI |

### Phase 1.5（パイロット運用開始後）

| # | 機能 | 優先度 |
|---|---|---|
| 3-3 | カスタムフィールドマッピング | P1 |
| 3-5 | ドライランプレビュー UI | P2 |
| 3-8 | import 失敗通知 | P2 |

### Phase 2（スケール）

| # | 機能 | 優先度 |
|---|---|---|
| 3-6 | 定期自動 import (Cron) | P2 |
| 3-9 | レート制御・上限設定 | P2 |

---

## 6. API 仕様（追加分）

### 6-1. アプリ一覧取得

```
GET /connectors/kintone/apps
Authorization: Bearer {jwt}
Role: admin

Response 200:
{
  "apps": [
    { "appId": "122", "name": "製造業ターゲット", "spaceId": null, "creator": "..." },
    ...
  ]
}

Error 400: kintone 未接続
Error 502: kintone API 障害
```

### 6-2. フィールド一覧取得

```
GET /connectors/kintone/apps/{app_id}/fields
Authorization: Bearer {jwt}
Role: admin

Response 200:
{
  "fields": [
    { "code": "name", "label": "会社名", "type": "SINGLE_LINE_TEXT", "required": true },
    { "code": "corporate_number", "label": "法人番号", "type": "SINGLE_LINE_TEXT" },
    ...
  ],
  "missing_required": []   // leads の必須フィールドで app に存在しないコード
}
```

### 6-3. マッピング設定

```
POST /connectors/kintone/apps/{app_id}/mappings
Authorization: Bearer {jwt}
Role: admin

Request:
{
  "field_mappings": {
    "name": "会社名",
    "corporate_number": "法人番号",
    "phone": "電話番号",
    "prefecture": "都道府県",
    "address": "住所",
    "employee_count": "従業員数"
  }
}

Response 200: { "saved": true }
```

### 6-4. ジョブ状態確認

```
GET /jobs/{job_id}
Authorization: Bearer {jwt}

Response 200:
{
  "job_id": "...",
  "job_type": "kintone_mfg_import",
  "status": "completed",     // queued | running | completed | failed
  "result": {
    "total_received": 2500,
    "total_upsert_ok": 2487,
    "total_skipped": 13,
    "probe_ok": true,
    "dry_run": false
  },
  "error_message": null,
  "started_at": "2026-04-01T02:00:00Z",
  "completed_at": "2026-04-01T02:04:32Z"
}

GET /jobs?job_type=kintone_mfg_import&limit=20
```

### 6-5. 建設業 import

```
POST /marketing/construction/import-kintone
Authorization: Bearer {jwt}
Role: admin

Request:
{
  "app_id": "45",
  "query": "permit_expiry > \"2026-04-01\"",
  "dry_run": false,
  "probe_size": 10
}

Response 200: KintoneMfgImportResponse (同形式)
```

---

## 7. DB マイグレーション（追加分）

```sql
-- background_jobs（汎用ジョブ管理）
CREATE TABLE background_jobs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  job_type        TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','running','completed','failed')),
  payload         JSONB,
  result          JSONB,
  error_message   TEXT,
  started_at      TIMESTAMPTZ,
  completed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON background_jobs (company_id, job_type, created_at DESC);
ALTER TABLE background_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON background_jobs
  USING (company_id = (SELECT company_id FROM jwt_claims()));

-- kintone_field_mappings（フィールドコードマッピング）
CREATE TABLE kintone_field_mappings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  app_id          TEXT NOT NULL,
  field_mappings  JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (company_id, app_id)
);
ALTER TABLE kintone_field_mappings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "company_isolation" ON kintone_field_mappings
  USING (company_id = (SELECT company_id FROM jwt_claims()));
```

---

## 8. フロントエンド変更箇所（追加分）

| ファイル | 変更内容 |
|---|---|
| `marketing/targets/page.tsx` | アプリID入力 → ドロップダウン化 / ジョブ状態ポーリング追加 / dry_run プレビュー表示 |
| `settings/connectors/page.tsx` | kintone 接続後にフィールド確認リンクを表示 |
| `marketing/targets/page.tsx`（建設業タブ） | 建設業用 import セクションを追加 |
| 新規: `components/KintoneFieldMapper.tsx` | フィールドマッピング設定コンポーネント |
| 新規: `components/JobStatusBadge.tsx` | ジョブ状態バッジ（queued/running/completed/failed） |

---

## 9. 技術的考慮事項

### kintone API 制限
| 制限 | 値 | 対策 |
|---|---|---|
| 1リクエスト最大取得件数 | 500件 | `KINTONE_MAX_LIMIT = 500`（実装済み） |
| offset 上限 | 10,000件 | `$id > last_id` のカーソル方式（実装済み） |
| 1日のAPIリクエスト上限 | 無制限（ただし同時接続数制限あり） | batch間 0.15秒 sleep（実装済み） |
| API トークンのアクセス権 | アプリ単位で設定 | 複数アプリは複数トークン or ユーザー認証方式 |

### フィールドコードの多様性
kintone のフィールドコードはアプリ設計者が自由に定義するため以下のパターンが混在する：
- 英語スネークケース: `company_name`, `corporate_number`（デフォルト想定）
- 日本語: `会社名`, `法人番号`
- 独自コード: `得意先名`, `法人番号_TSR`

→ カスタムマッピング（3-3）が必須。アプリ一覧・フィールド確認（3-1, 3-2）で運用担当者が確認できる状態にする。

### セキュリティ
- kintone API トークンは `tool_connections.connection_config._encrypted`（AES-256-GCM）で保管済み
- `background_jobs` テーブルの `payload` にトークンを含めない（`company_id` + `app_id` のみ格納）
- RLS: 全新規テーブルに `company_id` ベースの RLS 必須

---

*設計書の正 (Source of Truth): `shachotwo/b_詳細設計/b_10_kintone製造業リスト取得_要件定義.md`*
