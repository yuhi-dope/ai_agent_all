"""P1パイプライン（請求・安全書類・経費・給与）のテスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


COMPANY_ID = "test-company-p1"


# ─── billing_pipeline ────────────────────────────────────────────────────────

class TestBillingPipeline:
    @pytest.mark.asyncio
    async def test_direct_progress_items(self):
        from workers.bpo.construction.pipelines.billing_pipeline import run_billing_pipeline
        result = await run_billing_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "progress_items": [
                    {"item_name": "土工", "contract_amount": 1_000_000, "progress_rate": 0.5},
                    {"item_name": "舗装工", "contract_amount": 500_000, "progress_rate": 0.8},
                ]
            },
            period_year=2025,
            period_month=3,
        )
        assert result.success is True
        assert len(result.steps) == 6  # Step 6: anomaly_detector が追加された
        assert result.final_output["total"] > 0

    @pytest.mark.asyncio
    async def test_total_includes_tax(self):
        from workers.bpo.construction.pipelines.billing_pipeline import run_billing_pipeline
        result = await run_billing_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "progress_items": [
                    {"item_name": "仮設工", "contract_amount": 100_000, "progress_rate": 1.0},
                ]
            },
        )
        assert result.success is True
        inv = result.final_output
        # 税込 = 税抜 + 消費税
        assert inv["total"] == inv["subtotal"] + inv["tax_amount"]

    @pytest.mark.asyncio
    async def test_empty_items_returns_zero(self):
        from workers.bpo.construction.pipelines.billing_pipeline import run_billing_pipeline
        result = await run_billing_pipeline(
            company_id=COMPANY_ID,
            input_data={"progress_items": []},
        )
        assert result.success is True
        # total=0 はバリデーション警告が出るが成功扱い

    @pytest.mark.asyncio
    async def test_text_input_calls_ocr_and_extractor(self):
        from workers.bpo.construction.pipelines.billing_pipeline import run_billing_pipeline

        mock_ocr = AsyncMock(return_value=MagicMock(
            success=True, result={"text": "出来高50%"}, confidence=0.9, cost_yen=0.0, duration_ms=10,
        ))
        mock_extract = AsyncMock(return_value=MagicMock(
            success=True,
            result={"progress_items": [{"item_name": "A", "contract_amount": 200_000, "progress_rate": 0.5}],
                    "client_name": "テスト建設", "contract_amount": 200_000},
            confidence=0.85, cost_yen=5.0, duration_ms=100,
        ))
        with patch("workers.bpo.construction.pipelines.billing_pipeline.run_document_ocr", mock_ocr), \
             patch("workers.bpo.construction.pipelines.billing_pipeline.run_structured_extractor", mock_extract):
            result = await run_billing_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": "出来高50%"},
            )
        assert result.success is True
        assert result.final_output["client_name"] == "テスト建設"


# ─── safety_docs_pipeline ─────────────────────────────────────────────────────

class TestSafetyDocsPipeline:
    @pytest.mark.asyncio
    async def test_direct_workers(self):
        from workers.bpo.construction.pipelines.safety_docs_pipeline import run_safety_docs_pipeline
        result = await run_safety_docs_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "workers": [
                    {"name": "山田太郎", "role": "職長", "experience_years": 10},
                    {"name": "鈴木次郎", "role": "作業員", "experience_years": 3},
                ],
                "site_name": "テスト現場",
            },
            doc_type="worker_roster",
        )
        assert result.success is True
        assert len(result.steps) == 6
        assert result.final_output["site_name"] == "テスト現場"

    @pytest.mark.asyncio
    async def test_20plus_workers_needs_safety_manager(self):
        from workers.bpo.construction.pipelines.safety_docs_pipeline import run_safety_docs_pipeline
        workers = [{"name": f"作業員{i}", "role": "作業員", "experience_years": 1} for i in range(25)]
        result = await run_safety_docs_pipeline(
            company_id=COMPANY_ID,
            input_data={"workers": workers, "site_name": "大規模現場"},
        )
        assert result.success is True
        compliance_step = next(s for s in result.steps if s.step_name == "compliance_checker")
        assert any("安全管理者" in w for w in compliance_step.result.get("warnings", []))

    @pytest.mark.asyncio
    async def test_safety_plan_calls_generator(self):
        from workers.bpo.construction.pipelines.safety_docs_pipeline import run_safety_docs_pipeline

        mock_gen = AsyncMock(return_value=MagicMock(
            success=True,
            result={"content": "安全衛生計画書の内容"},
            confidence=0.9, cost_yen=10.0, duration_ms=200,
        ))
        with patch("workers.bpo.construction.pipelines.safety_docs_pipeline.run_document_generator", mock_gen):
            result = await run_safety_docs_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "workers": [{"name": "山田", "role": "職長", "experience_years": 5}],
                    "site_name": "安全計画テスト現場",
                    "work_details": "コンクリート打設工事",
                },
                doc_type="safety_plan",
            )
        assert result.success is True
        assert mock_gen.called


# ─── expense_pipeline ────────────────────────────────────────────────────────

class TestExpensePipeline:
    @pytest.mark.asyncio
    async def test_direct_expense_within_limit(self):
        from workers.bpo.common.pipelines.expense_pipeline import run_expense_pipeline
        result = await run_expense_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "expense": {
                    "expense_date": "2025-03-15",
                    "amount": 3_000,
                    "category": "交通費",
                    "purpose": "顧客訪問",
                    "vendor": "JR",
                }
            },
        )
        assert result.success is True
        assert result.final_output["approved_amount"] == 3_000
        assert result.final_output["over_limit"] is False

    @pytest.mark.asyncio
    async def test_over_limit_sets_approval_required(self):
        from workers.bpo.common.pipelines.expense_pipeline import run_expense_pipeline

        # rule_matcherをモックして上限1万円を返す
        mock_rule = AsyncMock(return_value=MagicMock(
            success=True,
            result={"matched_rules": [{"limit_amount": 10_000}]},
            confidence=0.9, cost_yen=0.0, duration_ms=10,
        ))
        with patch("workers.bpo.common.pipelines.expense_pipeline.run_rule_matcher", mock_rule):
            result = await run_expense_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "expense": {
                        "expense_date": "2025-03-15",
                        "amount": 30_000,
                        "category": "消耗品費",
                        "purpose": "事務用品購入",
                        "vendor": "文具店",
                    }
                },
            )
        assert result.success is True
        assert result.approval_required is True
        assert result.final_output["over_limit"] is True
        assert result.final_output["approved_amount"] == 10_000

    @pytest.mark.asyncio
    async def test_entertainment_over_5000_requires_approval(self):
        from workers.bpo.common.pipelines.expense_pipeline import run_expense_pipeline
        result = await run_expense_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "expense": {
                    "expense_date": "2025-03-10",
                    "amount": 8_000,
                    "category": "交際費",
                    "purpose": "顧客接待",
                    "vendor": "レストラン",
                }
            },
        )
        assert result.success is True
        assert result.approval_required is True

    @pytest.mark.asyncio
    async def test_all_5_steps_executed(self):
        from workers.bpo.common.pipelines.expense_pipeline import run_expense_pipeline
        result = await run_expense_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "expense": {
                    "expense_date": "2025-03-01",
                    "amount": 1_000,
                    "category": "交通費",
                    "purpose": "テスト",
                    "vendor": "バス",
                }
            },
        )
        assert len(result.steps) == 7  # Step 7: anomaly_detector が追加された


# ─── payroll_pipeline ────────────────────────────────────────────────────────

class TestPayrollPipeline:
    def _make_attendance(self, employee_id: str = "emp001", overtime_hours: float = 10.0) -> dict:
        return {
            "employee_id": employee_id,
            "employee_name": f"社員{employee_id}",
            "monthly_salary": 300_000,
            "work_days": 20,
            "work_hours": 160.0,
            "overtime_hours": overtime_hours,
            "late_night_hours": 2.0,
            "holiday_work_hours": 0.0,
            "absent_days": 0,
            "paid_leave_days": 1,
        }

    @pytest.mark.asyncio
    async def test_basic_payroll_calculation(self):
        from workers.bpo.common.pipelines.payroll_pipeline import run_payroll_pipeline
        result = await run_payroll_pipeline(
            company_id=COMPANY_ID,
            input_data={"attendance": self._make_attendance()},
            period_year=2025,
            period_month=3,
        )
        assert result.success is True
        assert len(result.steps) == 7
        payslip = result.final_output["payslips"][0]
        assert payslip["gross_salary"] > 300_000  # 残業代込みで基本給超える
        assert payslip["net_salary"] > 0
        assert payslip["net_salary"] < payslip["gross_salary"]  # 控除後は必ず減る

    @pytest.mark.asyncio
    async def test_overtime_above_45h_triggers_alert(self):
        from workers.bpo.common.pipelines.payroll_pipeline import run_payroll_pipeline
        result = await run_payroll_pipeline(
            company_id=COMPANY_ID,
            input_data={"attendance": self._make_attendance(overtime_hours=50.0)},
        )
        assert result.success is True
        assert any("36協定" in a for a in result.compliance_alerts)

    @pytest.mark.asyncio
    async def test_net_salary_less_than_gross(self):
        from workers.bpo.common.pipelines.payroll_pipeline import run_payroll_pipeline
        result = await run_payroll_pipeline(
            company_id=COMPANY_ID,
            input_data={"attendance": self._make_attendance(overtime_hours=0.0)},
        )
        assert result.success is True
        payslip = result.final_output["payslips"][0]
        assert payslip["net_salary"] < payslip["gross_salary"]

    @pytest.mark.asyncio
    async def test_batch_multiple_employees(self):
        from workers.bpo.common.pipelines.payroll_pipeline import run_payroll_pipeline
        result = await run_payroll_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    self._make_attendance("emp001"),
                    self._make_attendance("emp002"),
                    self._make_attendance("emp003"),
                ]
            },
        )
        assert result.success is True
        assert len(result.final_output["payslips"]) == 3
        assert result.final_output["employee_count"] == 3
