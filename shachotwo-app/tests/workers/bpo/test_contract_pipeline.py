"""契約書AIパイプライン テスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from workers.bpo.common.pipelines.contract_pipeline import (
    ContractPipelineResult,
    StepResult,
    _check_contract_risks,
    _contract_years,
    run_contract_pipeline,
)
from dataclasses import dataclass, field


@dataclass
class OcrResult:
    text: str = ""
    confidence: float = 0.9
    cost_yen: float = 0.0
    duration_ms: int = 0
    source: str = "file"


@dataclass
class ExtractorResult:
    data: dict = field(default_factory=dict)
    confidence: float = 0.9
    cost_yen: float = 0.0
    duration_ms: int = 0


@dataclass
class DiffResult:
    changes: list = field(default_factory=list)
    change_summary: str = ""
    has_significant_changes: bool = False
    confidence: float = 0.9
    cost_yen: float = 0.0
    duration_ms: int = 0


@dataclass
class ValidatorResult:
    is_valid: bool = True
    missing_fields: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    confidence: float = 1.0
    duration_ms: int = 0


# ------------------------------------------------------------------ #
# テスト用フィクスチャ
# ------------------------------------------------------------------ #
COMPANY_ID = str(uuid4())

VALID_CONTRACT: dict = {
    "contract_title": "業務委託契約書",
    "party_a": "株式会社テスト甲",
    "party_b": "株式会社テスト乙",
    "contract_amount": 1000000,
    "start_date": "2026-04-01",
    "end_date": "2027-03-31",
    "auto_renewal": False,
    "cancellation_notice_days": 30,
    "penalty_clause": "",
    "governing_law": "日本法",
}


def _mock_ocr(text: str = "契約書テキスト") -> OcrResult:
    return OcrResult(
        text=text,
        confidence=0.95,
        cost_yen=0.33,
        duration_ms=50,
        source="file",
    )


def _mock_extractor(data: dict) -> ExtractorResult:
    return ExtractorResult(
        data=data,
        confidence=0.85,
        cost_yen=0.05,
        duration_ms=300,
    )


def _mock_diff(changes: list | None = None) -> DiffResult:
    return DiffResult(
        changes=changes or [],
        change_summary="変更なし" if not changes else f"{len(changes)}件の変更",
        has_significant_changes=bool(changes),
        confidence=0.9,
        cost_yen=0.03,
        duration_ms=200,
    )


def _mock_validator(is_valid: bool = True, missing: list | None = None) -> ValidatorResult:
    missing = missing or []
    return ValidatorResult(
        is_valid=is_valid,
        missing_fields=missing,
        warnings=[f"必須フィールド '{f}' が未設定または不明です" for f in missing],
        confidence=1.0 if is_valid else 0.7,
        duration_ms=10,
    )


# ------------------------------------------------------------------ #
# テスト 1: contract dict 直渡しで正常完了
# ------------------------------------------------------------------ #
class TestContractDictDirectInput:
    @pytest.mark.asyncio
    async def test_success_with_contract_dict(self):
        """contract dict を直接渡した場合、OCR・抽出スキップで全5ステップが成功する。"""
        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": VALID_CONTRACT},
            )

        assert isinstance(result, ContractPipelineResult)
        assert result.success is True
        assert len(result.steps) == 5
        # Step1, Step2 はスキップ（コスト0）
        assert result.steps[0].step_name == "document_ocr"
        assert result.steps[0].result.get("skipped") is True
        assert result.steps[1].step_name == "contract_extractor"
        # contract dict 直渡し時は warning が設定され、抽出コスト=0
        assert result.steps[1].cost_yen == 0.0
        assert result.steps[1].warning is not None
        # final_output に契約情報が含まれる
        assert result.final_output["contract_title"] == "業務委託契約書"
        assert result.failed_step is None


# ------------------------------------------------------------------ #
# テスト 2: auto_renewal でリスクアラートが出る
# ------------------------------------------------------------------ #
class TestAutoRenewalRiskAlert:
    @pytest.mark.asyncio
    async def test_auto_renewal_alert_generated(self):
        """auto_renewal=True かつ cancellation_notice_days>=90 の場合にリスクアラートが出る。"""
        contract_with_renewal = {
            **VALID_CONTRACT,
            "auto_renewal": True,
            "cancellation_notice_days": 90,
        }

        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": contract_with_renewal},
            )

        assert result.success is True
        assert any("自動更新" in alert for alert in result.risk_alerts), \
            f"自動更新アラートが期待されるが: {result.risk_alerts}"
        assert "90" in "\n".join(result.risk_alerts)

    def test_auto_renewal_below_threshold_no_alert(self):
        """auto_renewal=True でも cancellation_notice_days<90 の場合はアラートなし。"""
        contract = {**VALID_CONTRACT, "auto_renewal": True, "cancellation_notice_days": 60}
        alerts, _ = _check_contract_risks(contract)
        assert not any("自動更新" in a for a in alerts)

    def test_auto_renewal_false_no_alert(self):
        """auto_renewal=False の場合はアラートなし。"""
        contract = {**VALID_CONTRACT, "auto_renewal": False, "cancellation_notice_days": 120}
        alerts, _ = _check_contract_risks(contract)
        assert not any("自動更新" in a for a in alerts)


# ------------------------------------------------------------------ #
# テスト 3: penalty_clause で review_required=True になる
# ------------------------------------------------------------------ #
class TestPenaltyClauseReviewRequired:
    @pytest.mark.asyncio
    async def test_penalty_clause_sets_review_required(self):
        """penalty_clause が非空の場合、review_required=True かつアラートが出る。"""
        contract_with_penalty = {
            **VALID_CONTRACT,
            "penalty_clause": "契約解除時は委託料の50%を違約金として支払う。",
        }

        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": contract_with_penalty},
            )

        assert result.success is True
        assert result.review_required is True
        assert any("違約金" in alert for alert in result.risk_alerts), \
            f"違約金アラートが期待されるが: {result.risk_alerts}"

    def test_empty_penalty_clause_no_review(self):
        """penalty_clause が空文字の場合 review_required は False のまま。"""
        contract = {**VALID_CONTRACT, "penalty_clause": ""}
        alerts, review_required = _check_contract_risks(contract)
        assert not any("違約金" in a for a in alerts)
        assert review_required is False

    def test_foreign_law_sets_review_required(self):
        """governing_law に外国関連キーワードがある場合も review_required=True。"""
        contract = {**VALID_CONTRACT, "governing_law": "米国ニューヨーク州法"}
        alerts, review_required = _check_contract_risks(contract)
        assert any("外国法" in a for a in alerts)
        assert review_required is True


# ------------------------------------------------------------------ #
# テスト 4: previous_contract 渡しで diff_detector が呼ばれる
# ------------------------------------------------------------------ #
class TestDiffDetectorCalledWithPreviousContract:
    @pytest.mark.asyncio
    async def test_diff_detector_called_when_previous_contract_given(self):
        """previous_contract を渡した場合、run_diff_detector が呼ばれる。"""
        previous = {
            **VALID_CONTRACT,
            "contract_amount": 800000,
            "end_date": "2026-12-31",
        }

        mock_diff = AsyncMock(
            return_value=_mock_diff(changes=[
                {"field": "contract_amount", "before": 800000, "after": 1000000, "significance": "high"},
                {"field": "end_date", "before": "2026-12-31", "after": "2027-03-31", "significance": "medium"},
            ])
        )

        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_diff_detector",
            new=mock_diff,
        ), patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": VALID_CONTRACT},
                previous_contract=previous,
            )

        assert result.success is True
        mock_diff.assert_awaited_once()
        # diff_detector の呼び出し引数を確認
        call_kwargs = mock_diff.call_args.kwargs
        assert call_kwargs["before"] == previous
        assert call_kwargs["after"] == VALID_CONTRACT

        # Step4 に変更情報が記録されている
        step4 = next(s for s in result.steps if s.step_name == "diff_detector")
        assert step4.result["has_significant_changes"] is True
        assert len(step4.result["changes"]) == 2

    @pytest.mark.asyncio
    async def test_diff_detector_skipped_when_no_previous_contract(self):
        """previous_contract が None の場合、diff_detector はスキップされる。"""
        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_diff_detector",
        ) as mock_diff, patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": VALID_CONTRACT},
                previous_contract=None,
            )

        mock_diff.assert_not_called()
        step4 = next(s for s in result.steps if s.step_name == "diff_detector")
        assert step4.result.get("skipped") is True


# ------------------------------------------------------------------ #
# テスト 5: 全5ステップが実行される
# ------------------------------------------------------------------ #
class TestAllFiveStepsExecuted:
    @pytest.mark.asyncio
    async def test_all_five_steps_present_in_result(self):
        """正常系で全5ステップが steps リストに含まれる。"""
        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": VALID_CONTRACT},
            )

        assert len(result.steps) == 5

        step_names = [s.step_name for s in result.steps]
        assert step_names == [
            "document_ocr",
            "contract_extractor",
            "risk_checker",
            "diff_detector",
            "output_validator",
        ]

        step_nos = [s.step_no for s in result.steps]
        assert step_nos == [1, 2, 3, 4, 5]

        # 全ステップ成功
        for step in result.steps:
            assert step.success is True, f"Step {step.step_no} ({step.step_name}) が失敗"

    @pytest.mark.asyncio
    async def test_step_cost_and_duration_accumulated(self):
        """total_cost_yen と total_duration_ms が各ステップの合計に対応している。"""
        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": VALID_CONTRACT},
            )

        expected_cost = sum(s.cost_yen for s in result.steps)
        assert abs(result.total_cost_yen - expected_cost) < 1e-6
        assert result.total_duration_ms >= 0


# ------------------------------------------------------------------ #
# テスト 6: テキスト入力での正常系（OCR + 抽出 が呼ばれる）
# ------------------------------------------------------------------ #
class TestTextInputPipeline:
    @pytest.mark.asyncio
    async def test_text_input_calls_ocr_and_extractor(self):
        """text 入力の場合、run_document_ocr と run_structured_extractor が呼ばれる。"""
        mock_ocr = AsyncMock(return_value=_mock_ocr("これは業務委託契約書です。"))
        mock_extractor = AsyncMock(return_value=_mock_extractor(VALID_CONTRACT))

        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_document_ocr",
            new=mock_ocr,
        ), patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_structured_extractor",
            new=mock_extractor,
        ), patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator()),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": "これは業務委託契約書です。"},
            )

        assert result.success is True
        mock_ocr.assert_awaited_once()
        mock_extractor.assert_awaited_once()

        step1 = result.steps[0]
        assert step1.step_name == "document_ocr"
        assert step1.result.get("skipped") is not True


# ------------------------------------------------------------------ #
# テスト 7: 必須フィールド不足でバリデーション警告
# ------------------------------------------------------------------ #
class TestValidationWarnings:
    @pytest.mark.asyncio
    async def test_missing_required_field_recorded_in_step5(self):
        """必須フィールドが不足している場合、Step5 に警告が記録される。"""
        incomplete_contract = {
            **VALID_CONTRACT,
            "party_a": "",  # 空文字
        }

        with patch(
            "workers.bpo.common.pipelines.contract_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator(is_valid=False, missing=["party_a"])),
        ):
            result = await run_contract_pipeline(
                company_id=COMPANY_ID,
                input_data={"contract": incomplete_contract},
            )

        assert result.success is True  # バリデーション警告はパイプライン失敗にはならない
        step5 = next(s for s in result.steps if s.step_name == "output_validator")
        assert step5.result["is_valid"] is False
        assert "party_a" in step5.result["missing_fields"]


# ------------------------------------------------------------------ #
# ユニットテスト: _contract_years
# ------------------------------------------------------------------ #
class TestContractYears:
    def test_one_year_contract(self):
        assert _contract_years("2026-04-01", "2027-03-31") == 1

    def test_five_year_contract(self):
        assert _contract_years("2020-01-01", "2025-01-01") == 5

    def test_six_year_contract_triggers_alert(self):
        contract = {**VALID_CONTRACT, "start_date": "2020-01-01", "end_date": "2026-01-01"}
        alerts, _ = _check_contract_risks(contract)
        assert any("長期契約" in a for a in alerts), f"長期契約アラートが期待されるが: {alerts}"

    def test_invalid_date_returns_none(self):
        assert _contract_years("not-a-date", "2027-01-01") is None

    def test_unknown_date_skips_check(self):
        contract = {**VALID_CONTRACT, "start_date": "不明", "end_date": "不明"}
        alerts, _ = _check_contract_risks(contract)
        assert not any("長期契約" in a for a in alerts)
