# i_02 importパス書き換えリスト

## 目的
ファイル移動に伴い、全ソースコードの import 文を旧パス→新パスに書き換える。影響範囲を事前に網羅する。

## 書き換えルール（sed/replace用パターン）

### Manager層
| 旧import | 新import |
|---|---|
| `from workers.bpo.manager.models import` | `from workers.manager.models import` |
| `from workers.bpo.manager.task_router import` | `from workers.manager.task_router import` |
| `from workers.bpo.manager.notifier import` | `from workers.manager.notifier import` |
| `from workers.bpo.manager.orchestrator import` | `from workers.manager.orchestrator import` |
| `from workers.bpo.manager.schedule_watcher import` | `from workers.manager.schedule_watcher import` |
| `from workers.bpo.manager.event_listener import` | `from workers.manager.event_listener import` |
| `from workers.bpo.manager.condition_evaluator import` | `from workers.manager.condition_evaluator import` |
| `from workers.bpo.manager.proactive_scanner import` | `from workers.manager.proactive_scanner import` |
| `from workers.bpo.engine.approval_workflow import` | `from workers.engine.approval_workflow import` |
| `from workers.bpo.engine.document_gen import` | `from workers.engine.document_gen import` |

### 営業パイプライン
| 旧import | 新import |
|---|---|
| `workers.bpo.sales.marketing.` | `workers.base.sales.marketing.` |
| `workers.bpo.sales.sfa.` | `workers.base.sales.sfa.` |
| `workers.bpo.sales.crm.` | `workers.base.sales.crm.` |
| `workers.bpo.sales.cs.` | `workers.base.sales.cs.` |
| `workers.bpo.sales.learning.` | `workers.base.sales.learning.` |
| `workers.bpo.sales.chain` | `workers.base.sales.chain` |
| `workers.bpo.sales.scheduler` | `workers.base.sales.scheduler` |

### バックオフィスパイプライン
| 旧import | 新import |
|---|---|
| `workers.bpo.common.pipelines.expense_pipeline` | `workers.base.backoffice.accounting.expense_pipeline` |
| `workers.bpo.common.pipelines.invoice_issue_pipeline` | `workers.base.backoffice.accounting.invoice_issue_pipeline` |
| `workers.bpo.common.pipelines.ar_management_pipeline` | `workers.base.backoffice.accounting.ar_management_pipeline` |
| `workers.bpo.common.pipelines.ap_management_pipeline` | `workers.base.backoffice.accounting.ap_management_pipeline` |
| `workers.bpo.common.pipelines.bank_reconciliation_pipeline` | `workers.base.backoffice.accounting.bank_reconciliation_pipeline` |
| `workers.bpo.common.pipelines.journal_entry_pipeline` | `workers.base.backoffice.accounting.journal_entry_pipeline` |
| `workers.bpo.common.pipelines.monthly_close_pipeline` | `workers.base.backoffice.accounting.monthly_close_pipeline` |
| `workers.bpo.common.pipelines.tax_filing_pipeline` | `workers.base.backoffice.accounting.tax_filing_pipeline` |
| `workers.bpo.common.pipelines.attendance_pipeline` | `workers.base.backoffice.labor.attendance_pipeline` |
| `workers.bpo.common.pipelines.payroll_pipeline` | `workers.base.backoffice.labor.payroll_pipeline` |
| `workers.bpo.common.pipelines.social_insurance_pipeline` | `workers.base.backoffice.labor.social_insurance_pipeline` |
| `workers.bpo.common.pipelines.year_end_adjustment_pipeline` | `workers.base.backoffice.labor.year_end_adjustment_pipeline` |
| `workers.bpo.common.pipelines.labor_compliance_pipeline` | `workers.base.backoffice.labor.labor_compliance_pipeline` |
| `workers.bpo.common.pipelines.recruitment_pipeline` | `workers.base.backoffice.hr.recruitment_pipeline` |
| `workers.bpo.common.pipelines.employee_onboarding_pipeline` | `workers.base.backoffice.hr.employee_onboarding_pipeline` |
| `workers.bpo.common.pipelines.employee_offboarding_pipeline` | `workers.base.backoffice.hr.employee_offboarding_pipeline` |
| `workers.bpo.common.pipelines.contract_pipeline` | `workers.base.backoffice.admin.contract_pipeline` |
| `workers.bpo.common.pipelines.admin_reminder_pipeline` | `workers.base.backoffice.admin.admin_reminder_pipeline` |
| `workers.bpo.common.pipelines.vendor_pipeline` | `workers.base.backoffice.procurement.vendor_pipeline` |
| `workers.bpo.common.pipelines.purchase_order_pipeline` | `workers.base.backoffice.procurement.purchase_order_pipeline` |
| `workers.bpo.common.pipelines.compliance_check_pipeline` | `workers.base.backoffice.legal.compliance_check_pipeline` |
| `workers.bpo.common.pipelines.antisocial_screening_pipeline` | `workers.base.backoffice.legal.antisocial_screening_pipeline` |
| `workers.bpo.common.pipelines.account_lifecycle_pipeline` | `workers.base.backoffice.it.account_lifecycle_pipeline` |

### 業界プラグイン
| 旧import | 新import |
|---|---|
| `workers.bpo.construction.` | `workers.industry.construction.` |
| `workers.bpo.manufacturing.` | `workers.industry.manufacturing.` |
| `workers.bpo.clinic.` | `workers.industry.clinic.` |
| `workers.bpo.nursing.` | `workers.industry.nursing.` |
| `workers.bpo.realestate.` | `workers.industry.realestate.` |
| `workers.bpo.logistics.` | `workers.industry.logistics.` |

### 影響を受けるファイル一覧

以下のファイル群で上記のimportが使われている（grep対象）:

1. **全パイプラインファイル** (56本) — 相互参照のimport
2. **ルーターファイル** (routers/bpo/*.py + routers/sales.py等) — パイプライン呼び出し
3. **main.py** — ルーターinclude + scheduler/orchestrator起動
4. **task_router.py** — PIPELINE_REGISTRY の全エントリ
5. **chain.py** — パイプライン連鎖の参照
6. **scheduler.py** — スケジュール実行のimport
7. **orchestrator.py** — 全マネージャーのimport
8. **テストファイル** (tests/workers/bpo/**) — テスト対象のimport

## 書き換え実行手順

1. `grep -r "workers\.bpo\." shachotwo-app/ --include="*.py" -l` で影響ファイルを全列挙
2. 上記テーブルの順に `sed -i` で一括置換（マネージャー→営業→バックオフィス→業界の順）
3. 各置換後に `python -c "import workers.manager.task_router"` 等で import 確認
4. 全置換完了後に `pytest` で全テスト実行
