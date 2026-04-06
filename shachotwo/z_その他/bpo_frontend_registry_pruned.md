# BPO フロントカタログ縮小メモ（MVP）

## 方針

業務自動化一覧（`/bpo`）のカードは **建設4 + 製造8 + 共通4 = 16 本**に絞った。  
フロントの単一ソースは `shachotwo-app/frontend/src/lib/bpo-pipeline-catalog.ts`（実装時に追加）。

## 一覧から外した pipeline key（復活用）

以下は旧 `bpo/page.tsx` の `PIPELINE_REGISTRY` にあったが、MVP 表示から削除した。

| key | 備考 |
|-----|------|
| `dental/receipt_check` | 凍結業種系 |
| `restaurant/fl_cost` | 同上 |
| `beauty/booking_recall` | 同上 |
| `logistics/dispatch` | コア6のうち優先外で縮小時に削除 |
| `ecommerce/product_listing` | 同上 |
| `nursing/care_billing` | 同上 |
| `staffing/dispatch_contract` | 同上 |
| `clinic/medical_receipt` | 同上 |
| `pharmacy/dispensing_billing` | 同上 |
| `hotel/revenue_mgmt` | 同上 |
| `realestate/rent_collection` | 同上 |
| `auto_repair/repair_quoting` | 同上 |
| `professional/deadline_mgmt` | 同上 |
| `wholesale/order_processing` | 卸売6本すべて |
| `wholesale/inventory_management` | 同上 |
| `wholesale/accounts_receivable` | 同上 |
| `wholesale/accounts_payable` | 同上 |
| `wholesale/shipping` | 同上 |
| `wholesale/sales_intelligence` | 同上 |

復活するときは `bpo-pipeline-catalog.ts` の `MVP_PIPELINES` に戻し、`RUN_PAGE_PIPELINE_META` にサンプルJSONが必要なら追加。バックエンド実行は `workers/bpo/manager/task_router.py` の `PIPELINE_REGISTRY` を正とする。

## 製造8本と API `pipeline_id`

| フロント key | `POST /api/v1/bpo/manufacturing/pipelines/{id}` の id |
|--------------|--------------------------------------------------------|
| `manufacturing/quoting` | （専用 CRUD `/quotes`、一覧は `/bpo/manufacturing`） |
| `manufacturing/production_planning` | `production_planning` |
| `manufacturing/quality_control` | `quality_control` |
| … | 以下同様に `manufacturing/` を除いた id |

## 関連ファイル（実装チェックリスト）

- `frontend/src/lib/bpo-pipeline-catalog.ts`（新規）
- `frontend/src/app/(authenticated)/bpo/page.tsx`（カタログ import・`COMING_SOON` 空・`INDUSTRY_FIRST_STEP` は建設/製造のみ）
- `frontend/src/app/(authenticated)/bpo/run/page.tsx`（`RUN_PAGE_PIPELINE_META` のみ使用）
- `frontend/src/app/(authenticated)/bpo/manufacturing/pipelines/[pipelineId]/run/page.tsx`（新規）
- `frontend/src/app/(authenticated)/bpo/manufacturing/page.tsx`（その他製造BPOセクション）
- `frontend/src/lib/bpo-industry-nav.ts`（ラベル「製造BPO」等）
