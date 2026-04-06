"""建設業 下請管理パイプライン テスト。"""
import pytest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from workers.bpo.construction.pipelines.subcontractor_pipeline import (
    run_subcontractor_pipeline,
    SubcontractorPipelineResult,
    PAYMENT_DEADLINE_DAYS,
    LICENSE_EXPIRY_WARNING_DAYS,
    INSURANCE_EXPIRY_WARNING_DAYS,
)


COMPANY_ID = "test-company-001"
TODAY = date.today()


def _future(days: int) -> str:
    return (TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


def _past(days: int) -> str:
    return (TODAY - timedelta(days=days)).strftime("%Y-%m-%d")


def _make_subcontractor(**kwargs) -> dict:
    """デフォルト値付きの正常な下請業者データを返す。"""
    base = {
        "company_name": "山田建設",
        "license_number": "国土交通大臣許可（特-03）第12345号",
        "license_expiry": _future(365),
        "license_types": ["土木工事業", "舗装工事業"],
        "work_type": "舗装工事",
        "contract_amount": 5_000_000,
        "payment_due_date": (TODAY + timedelta(days=30)).strftime("%Y-%m-%d"),
        "insurance_expiry": _future(180),
    }
    base.update(kwargs)
    return base


class TestSubcontractorPipelineHappyPath:
    """正常系テスト"""

    @pytest.mark.asyncio
    async def test_direct_subcontractors_success(self):
        """直渡しで全6ステップが正常完了する。"""
        subcontractors = [_make_subcontractor()]
        result: SubcontractorPipelineResult = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        assert result.failed_step is None
        assert len(result.steps) == 6
        assert result.total_duration_ms >= 0
        assert result.total_cost_yen >= 0.0

    @pytest.mark.asyncio
    async def test_all_6_steps_executed(self):
        """全6ステップが正しい名前で実行される。"""
        subcontractors = [_make_subcontractor()]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert len(result.steps) == 6
        step_names = [s.step_name for s in result.steps]
        assert step_names[0] == "subcontractor_reader"
        assert step_names[1] == "license_checker"
        assert step_names[2] == "safety_docs_checker"
        assert step_names[3] == "payment_checker"
        assert step_names[4] == "compliance_checker"
        assert step_names[5] == "output_validator"

        for step in result.steps:
            assert step.success is True


class TestLicenseChecker:
    """許可証チェック テスト"""

    @pytest.mark.asyncio
    async def test_expired_license_triggers_alert(self):
        """許可証期限切れ（past date）でアラートが発生する。"""
        subcontractors = [
            _make_subcontractor(license_expiry=_past(10))
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        # license_checker ステップのresultに期限切れが記録されている
        license_step = result.steps[1]
        assert license_step.step_name == "license_checker"
        has_expired = license_step.result.get("has_expired_license", False)
        assert has_expired is True
        # アラートリストに期限切れメッセージが含まれる
        assert any("期限切れ" in a for a in result.alerts)

    @pytest.mark.asyncio
    async def test_license_expiry_warning_90days(self):
        """許可証期限が90日以内でwarningアラートが発生する。"""
        expiry_within_warning = _future(LICENSE_EXPIRY_WARNING_DAYS - 10)  # 80日後（警告範囲内）
        subcontractors = [
            _make_subcontractor(license_expiry=expiry_within_warning)
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        license_step = result.steps[1]
        license_results = license_step.result.get("license_results", [])
        assert len(license_results) > 0
        assert license_results[0]["license_status"] == "expiring_soon"
        assert any("日後に期限切れ" in a for a in result.alerts)

    @pytest.mark.asyncio
    async def test_license_type_mismatch_alert(self):
        """許可業種と実施工事種別が不一致のときアラートが発生する。"""
        subcontractors = [
            _make_subcontractor(
                license_types=["電気工事業"],
                work_type="舗装工事",  # 電気工事業には含まれない
            )
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        compliance_step = result.steps[4]
        assert compliance_step.step_name == "compliance_checker"
        compliance_alerts = compliance_step.result.get("alerts", [])
        assert any("不一致" in a for a in compliance_alerts)


class TestSafetyDocsChecker:
    """安全書類チェック テスト"""

    @pytest.mark.asyncio
    async def test_insurance_expiry_warning(self):
        """保険証書期限が30日以内でwarningアラートが発生する。"""
        expiry_within_warning = _future(INSURANCE_EXPIRY_WARNING_DAYS - 5)  # 25日後
        subcontractors = [
            _make_subcontractor(insurance_expiry=expiry_within_warning)
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        safety_step = result.steps[2]
        assert safety_step.step_name == "safety_docs_checker"
        safety_results = safety_step.result.get("safety_results", [])
        assert len(safety_results) > 0
        assert safety_results[0]["insurance_status"] == "expiring_soon"
        assert any("保険証書" in a for a in result.alerts)


class TestPaymentChecker:
    """支払チェック テスト"""

    @pytest.mark.asyncio
    async def test_payment_overdue_triggers_alert(self):
        """支払期限が契約日から60日を超えるとアラートが発生する（建設業法）。"""
        # 契約日から70日後に支払 → 60日ルール違反
        contract_date = TODAY
        payment_due = contract_date + timedelta(days=70)  # 60日超過
        subcontractors = [
            _make_subcontractor(payment_due_date=payment_due.strftime("%Y-%m-%d"))
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": contract_date.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        payment_step = result.steps[3]
        assert payment_step.step_name == "payment_checker"
        has_violation = payment_step.result.get("has_payment_violation", False)
        assert has_violation is True
        assert any("60日" in a or "超過" in a for a in result.alerts)

    @pytest.mark.asyncio
    async def test_payment_within_60days_no_alert(self):
        """支払期限が契約日から60日以内の場合はアラートなし。"""
        contract_date = TODAY
        payment_due = contract_date + timedelta(days=45)  # 60日以内
        subcontractors = [
            _make_subcontractor(payment_due_date=payment_due.strftime("%Y-%m-%d"))
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": contract_date.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        payment_step = result.steps[3]
        has_violation = payment_step.result.get("has_payment_violation", False)
        assert has_violation is False


class TestMultipleSubcontractors:
    """複数下請業者テスト"""

    @pytest.mark.asyncio
    async def test_multiple_subcontractors(self):
        """複数下請業者をまとめて処理できる。"""
        subcontractors = [
            _make_subcontractor(company_name="山田建設"),
            _make_subcontractor(
                company_name="田中工務店",
                license_types=["大工工事業"],
                work_type="大工工事",
                contract_amount=3_000_000,
            ),
            _make_subcontractor(
                company_name="鈴木電気",
                license_types=["電気工事業"],
                work_type="電気工事",
                contract_amount=2_000_000,
            ),
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        assert len(result.steps) == 6
        # 全3業者分の結果が含まれる
        license_step = result.steps[1]
        assert len(license_step.result.get("license_results", [])) == 3
        safety_step = result.steps[2]
        assert len(safety_step.result.get("safety_results", [])) == 3
        payment_step = result.steps[3]
        assert len(payment_step.result.get("payment_results", [])) == 3
        # total_subcontract_amount が正しい
        expected_total = 5_000_000 + 3_000_000 + 2_000_000
        assert result.final_output["total_subcontract_amount"] == expected_total

    @pytest.mark.asyncio
    async def test_multiple_subcontractors_mixed_alerts(self):
        """複数業者のうち一部に問題がある場合、アラートが正しく収集される。"""
        subcontractors = [
            _make_subcontractor(company_name="正常業者"),
            _make_subcontractor(
                company_name="期限切れ業者",
                license_expiry=_past(5),  # 5日前に期限切れ
            ),
        ]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        assert result.success is True
        # 期限切れ業者のアラートが含まれる
        assert any("期限切れ" in a for a in result.alerts)
        # 正常業者のアラートは含まれない
        normal_alerts = [a for a in result.alerts if "正常業者" in a]
        # 正常業者はlicense_expiryが1年先なのでアラートなし
        assert len(normal_alerts) == 0


class TestResultStructure:
    """結果構造テスト"""

    @pytest.mark.asyncio
    async def test_step1_direct_data_source(self):
        """直渡しデータのStep1はsource=direct_data。"""
        subcontractors = [_make_subcontractor()]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        step1 = result.steps[0]
        assert step1.step_name == "subcontractor_reader"
        assert step1.result["source"] == "direct_data"
        assert step1.confidence == 1.0

    @pytest.mark.asyncio
    async def test_total_cost_is_sum_of_steps(self):
        """total_cost_yen は各ステップのコスト合計に等しい。"""
        subcontractors = [_make_subcontractor()]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        step_total = sum(s.cost_yen for s in result.steps)
        assert abs(result.total_cost_yen - step_total) < 0.001

    @pytest.mark.asyncio
    async def test_summary_method_returns_string(self):
        """summary()メソッドが適切な文字列を返す。"""
        subcontractors = [_make_subcontractor()]
        result = await run_subcontractor_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "subcontractors": subcontractors,
                "contract_date": TODAY.strftime("%Y-%m-%d"),
            },
        )

        summary = result.summary()
        assert isinstance(summary, str)
        assert "下請管理パイプライン" in summary
        assert "ステップ" in summary
