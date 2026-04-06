"""建設業 許認可申請パイプライン テスト。"""
import pytest
from unittest.mock import AsyncMock, patch

from workers.bpo.construction.pipelines.permit_pipeline import (
    run_permit_pipeline,
    PermitPipelineResult,
    PERMIT_TYPES,
    PERMIT_VALID_YEARS,
    EXPIRY_WARNING_DAYS,
    SPECIAL_PERMIT_THRESHOLD,
    REQUIREMENTS,
)
from workers.micro.models import MicroAgentOutput


COMPANY_ID = "test-company-001"

# ─── 共通テスト用入力データ ────────────────────────────────────────────────────

BASE_INPUT_RENEWAL = {
    "company_name": "山田建設株式会社",
    "permit_type": "general",
    "application_type": "renewal",
    "license_types": ["土木工事業", "舗装工事業"],
    "expiry_date": "2026-09-30",  # 今日(2026-03-20)から約6ヶ月後
    "manager": {
        "name": "山田太郎",
        "experience_years": 10,
        "role": "経営業務管理責任者",
    },
    "technicians": [
        {
            "name": "鈴木次郎",
            "qualification": "1級土木施工管理技士",
            "license_types": ["土木工事業", "舗装工事業"],
        },
    ],
    "net_assets": 5_000_000,
}

BASE_INPUT_NEW = {
    "company_name": "新規建設株式会社",
    "permit_type": "general",
    "application_type": "new",
    "license_types": ["建築工事業"],
    "manager": {
        "name": "新規太郎",
        "experience_years": 7,
        "role": "経営業務管理責任者",
    },
    "technicians": [
        {
            "name": "技術花子",
            "qualification": "1級建築施工管理技士",
            "license_types": ["建築工事業"],
        },
    ],
    "net_assets": 8_000_000,
}


def _make_generator_mock(content: str = "申請書類ドラフト") -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"content": content, "format": "markdown", "char_count": len(content)},
        confidence=0.9,
        cost_yen=10.0,
        duration_ms=500,
    )


def _make_validator_mock() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="output_validator",
        success=True,
        result={"valid": True, "missing": [], "empty": [], "type_errors": [], "warnings": []},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=5,
    )


# ─── テストケース ─────────────────────────────────────────────────────────────

class TestPermitPipelineHappyPath:
    """正常系テスト"""

    @pytest.mark.asyncio
    async def test_renewal_application_success(self):
        """更新申請で全7ステップ正常完了"""
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result: PermitPipelineResult = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_RENEWAL,
            )

        assert result.success is True
        assert result.failed_step is None
        assert result.total_duration_ms >= 0
        assert result.final_output["company_name"] == "山田建設株式会社"
        assert result.final_output["permit_type"] == "general"

    @pytest.mark.asyncio
    async def test_all_7_steps_executed(self):
        """全7ステップが実行される"""
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_RENEWAL,
            )

        assert len(result.steps) == 7
        step_names = [s.step_name for s in result.steps]
        assert step_names[0] == "document_reader"
        assert step_names[1] == "requirements_check"
        assert step_names[2] == "qualification_check"
        assert step_names[3] == "track_record_check"
        assert step_names[4] == "form_generator"
        assert step_names[5] == "compliance_checker"
        assert step_names[6] == "output_validator"

    @pytest.mark.asyncio
    async def test_new_application_requirements(self):
        """新規申請の要件チェックが通る"""
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_NEW,
            )

        assert result.success is True
        # 新規申請でもrequirements_metがFinalOutputに含まれる
        assert "requirements_met" in result.final_output
        # 経験7年は5年以上なので要件充足
        req_step = result.steps[1]
        assert req_step.step_name == "requirements_check"
        assert req_step.result["requirements_met"] is True


class TestPermitPipelineExpiryWarning:
    """満了日アラートテスト"""

    @pytest.mark.asyncio
    async def test_expiry_warning_180days(self):
        """満了6ヶ月前（180日以内）に警告が出る"""
        # expiry_date を今日から179日後に設定（EXPIRY_WARNING_DAYS=180の閾値内）
        from datetime import date, timedelta
        near_expiry = (date.today() + timedelta(days=179)).isoformat()

        input_data = {**BASE_INPUT_RENEWAL, "expiry_date": near_expiry}
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        # requirements_check で満了日警告が出る
        req_step = result.steps[1]
        warnings = req_step.result.get("warnings", [])
        assert any("満了" in w or "更新" in w for w in warnings), (
            f"満了日警告が出るはず。warnings={warnings}"
        )

    @pytest.mark.asyncio
    async def test_no_expiry_warning_when_far(self):
        """満了日が6ヶ月超先なら警告なし"""
        from datetime import date, timedelta
        far_expiry = (date.today() + timedelta(days=200)).isoformat()

        input_data = {**BASE_INPUT_RENEWAL, "expiry_date": far_expiry}
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        req_step = result.steps[1]
        warnings = req_step.result.get("warnings", [])
        # 200日先なら満了警告は出ないはず
        expiry_warnings = [w for w in warnings if "満了" in w or "更新" in w]
        assert len(expiry_warnings) == 0


class TestPermitPipelineRequirements:
    """要件チェックテスト"""

    @pytest.mark.asyncio
    async def test_manager_experience_insufficient(self):
        """経営責任者経験不足でrequirements_metがFalse"""
        input_data = {
            **BASE_INPUT_NEW,
            "manager": {
                "name": "経験不足太郎",
                "experience_years": 3,  # 5年未満
                "role": "経営業務管理責任者",
            },
        }
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        req_step = result.steps[1]
        assert req_step.step_name == "requirements_check"
        assert req_step.result["requirements_met"] is False
        errors = req_step.result.get("errors", [])
        assert any("経営業務管理責任者" in e for e in errors), (
            f"経験不足エラーが出るはず。errors={errors}"
        )

    @pytest.mark.asyncio
    async def test_technician_qualification_valid(self):
        """有資格技術者がいれば qualification_check で要件充足"""
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_NEW,
            )

        qual_step = result.steps[2]
        assert qual_step.step_name == "qualification_check"
        technicians = qual_step.result.get("technicians", [])
        assert len(technicians) == 1
        assert technicians[0]["qualified"] is True
        assert qual_step.result["all_qualified"] is True


class TestPermitPipelineSpecialPermit:
    """特定建設業テスト"""

    @pytest.mark.asyncio
    async def test_special_permit_asset_check(self):
        """特定建設業で純資産2000万円未満はrequirements_errorになる"""
        input_data = {
            **BASE_INPUT_NEW,
            "permit_type": "special",
            "net_assets": 10_000_000,  # 1000万円（2000万円未満）
        }
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        req_step = result.steps[1]
        assert req_step.step_name == "requirements_check"
        assert req_step.result["requirements_met"] is False
        errors = req_step.result.get("errors", [])
        assert any("特定建設業" in e or "財産" in e for e in errors), (
            f"財産的基礎エラーが出るはず。errors={errors}"
        )

    @pytest.mark.asyncio
    async def test_special_permit_asset_sufficient(self):
        """特定建設業で純資産2000万円以上は財産要件充足"""
        input_data = {
            **BASE_INPUT_NEW,
            "permit_type": "special",
            "net_assets": 25_000_000,  # 2500万円（2000万円以上）
        }
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=_make_generator_mock(),
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        req_step = result.steps[1]
        errors = req_step.result.get("errors", [])
        asset_errors = [e for e in errors if "財産" in e or "特定建設業" in e]
        assert len(asset_errors) == 0


class TestPermitPipelineFormGenerator:
    """書類生成テスト"""

    @pytest.mark.asyncio
    async def test_form_generator_called(self):
        """Step 5 form_generatorが呼ばれ、書類が生成される"""
        mock_content = "## 建設業許可 更新申請書\n山田建設株式会社"
        mock_generator = AsyncMock(return_value=_make_generator_mock(mock_content))

        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            mock_generator,
        ), patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_output_validator",
            new_callable=AsyncMock,
            return_value=_make_validator_mock(),
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_RENEWAL,
            )

        # run_document_generator が1回呼ばれた
        assert mock_generator.call_count == 1
        call_args = mock_generator.call_args[0][0]
        assert call_args.payload["template_name"] == "approval_request"
        assert call_args.payload["format"] == "markdown"

        # Step 5の結果に生成書類が含まれる
        form_step = result.steps[4]
        assert form_step.step_name == "form_generator"
        assert form_step.success is True

        # final_output に生成書類が含まれる
        assert result.final_output.get("generated_form") == mock_content

    @pytest.mark.asyncio
    async def test_form_generator_failure_stops_pipeline(self):
        """書類生成失敗でパイプラインが停止"""
        mock_gen_fail = MicroAgentOutput(
            agent_name="document_generator", success=False,
            result={"error": "LLM接続エラー"}, confidence=0.0,
            cost_yen=0.0, duration_ms=100,
        )
        with patch(
            "workers.bpo.construction.pipelines.permit_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=mock_gen_fail,
        ):
            result = await run_permit_pipeline(
                company_id=COMPANY_ID,
                input_data=BASE_INPUT_RENEWAL,
            )

        assert result.success is False
        assert result.failed_step == "form_generator"
        # Step 5まで実行されている
        assert len(result.steps) == 5


class TestPermitPipelineConstants:
    """定数・設定値テスト"""

    def test_permit_types_defined(self):
        """PERMIT_TYPESが正しく定義されている"""
        assert "general" in PERMIT_TYPES
        assert "special" in PERMIT_TYPES
        assert PERMIT_TYPES["general"] == "一般建設業"
        assert PERMIT_TYPES["special"] == "特定建設業"

    def test_permit_valid_years(self):
        """建設業許可有効期間が5年"""
        assert PERMIT_VALID_YEARS == 5

    def test_expiry_warning_days(self):
        """満了日アラートが180日前"""
        assert EXPIRY_WARNING_DAYS == 180

    def test_special_permit_threshold(self):
        """特定建設業の閾値が4500万円"""
        assert SPECIAL_PERMIT_THRESHOLD == 4_500_000

    def test_requirements_contains_all_items(self):
        """5つの要件チェック項目が定義されている"""
        assert len(REQUIREMENTS) == 5
        assert "keieigyo_kanri_sekininsha" in REQUIREMENTS
        assert "sennin_gijutsusha" in REQUIREMENTS
        assert "zaisan_teki_kiso" in REQUIREMENTS
