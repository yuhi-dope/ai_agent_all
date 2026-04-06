"""取引先管理パイプライン テスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from workers.bpo.common.pipelines.vendor_pipeline import (
    CONCENTRATION_RISK_THRESHOLD,
    MAX_SCORE,
    MIN_SCORE,
    PAYMENT_DELAY_RISK_DAYS,
    VendorPipelineResult,
    run_vendor_pipeline,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = str(uuid4())

# ─── テスト用ベンダーデータ ──────────────────────────────────────────

HEALTHY_VENDOR = {
    "vendor_name": "山田物産",
    "vendor_id": "V001",
    "annual_transaction_amount": 5_000_000,
    "relationship_years": 8,
    "avg_payment_delay_days": 0,
    "incident_count": 0,
    "transaction_ratio": 0.10,  # 10% → 集中リスクなし
}

HIGH_CONCENTRATION_VENDOR = {
    "vendor_name": "集中リスク商事",
    "vendor_id": "V002",
    "annual_transaction_amount": 20_000_000,
    "relationship_years": 5,
    "avg_payment_delay_days": 0,
    "incident_count": 0,
    "transaction_ratio": 0.40,  # 40% → 集中リスクあり
}

PAYMENT_DELAY_VENDOR = {
    "vendor_name": "遅延物産",
    "vendor_id": "V003",
    "annual_transaction_amount": 3_000_000,
    "relationship_years": 3,
    "avg_payment_delay_days": 45,  # 45日遅延 → リスクあり
    "incident_count": 1,
    "transaction_ratio": 0.10,
}


def _mock_validator_out() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="output_validator", success=True,
        result={"valid": True, "missing": [], "empty": [], "type_errors": [], "warnings": []},
        confidence=1.0, cost_yen=0.0, duration_ms=5,
    )


# ─────────────────────────────────────────────────────────────────────
# テスト 1: 問題なし取引先でスコアが高い
# ─────────────────────────────────────────────────────────────────────
class TestHealthyVendorHighScore:
    @pytest.mark.asyncio
    async def test_healthy_vendor_high_score(self):
        """支払遅延なし・トラブルなし・集中リスクなしの取引先はスコアが高い。"""
        input_data = {"vendors": [HEALTHY_VENDOR]}

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert len(result.final_output["scored_vendors"]) == 1

        scored = result.final_output["scored_vendors"][0]
        assert scored["score"] >= 60, f"スコアが低すぎる: {scored['score']}"
        # リスクフラグなし
        assert len(result.risk_vendors) == 0


# ─────────────────────────────────────────────────────────────────────
# テスト 2: 30%超集中でアラート
# ─────────────────────────────────────────────────────────────────────
class TestConcentrationRiskAlert:
    @pytest.mark.asyncio
    async def test_concentration_risk_alert(self):
        """transaction_ratio > CONCENTRATION_RISK_THRESHOLD (30%) でリスクアラート。"""
        input_data = {"vendors": [HIGH_CONCENTRATION_VENDOR]}

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        # 取引集中リスクが検出されている
        assert "集中リスク商事" in result.risk_vendors
        all_risks = result.final_output["all_risks"]
        assert any("取引集中リスク" in r for r in all_risks), f"集中リスクが期待されるが: {all_risks}"
        # リスクメッセージにパーセント表示が含まれる
        assert any("40%" in r for r in all_risks)


# ─────────────────────────────────────────────────────────────────────
# テスト 3: 支払遅延アラート
# ─────────────────────────────────────────────────────────────────────
class TestPaymentDelayRisk:
    @pytest.mark.asyncio
    async def test_payment_delay_risk(self):
        """avg_payment_delay_days > PAYMENT_DELAY_RISK_DAYS (30) で支払遅延リスク。"""
        input_data = {"vendors": [PAYMENT_DELAY_VENDOR]}

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert "遅延物産" in result.risk_vendors
        all_risks = result.final_output["all_risks"]
        assert any("支払遅延リスク" in r for r in all_risks), f"支払遅延リスクが期待されるが: {all_risks}"


# ─────────────────────────────────────────────────────────────────────
# テスト 4: 全4ステップが実行される
# ─────────────────────────────────────────────────────────────────────
class TestAll4StepsExecuted:
    @pytest.mark.asyncio
    async def test_all_4_steps_executed(self):
        """正常系で全4ステップが steps リストに含まれる。"""
        input_data = {"vendors": [HEALTHY_VENDOR]}

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert len(result.steps) == 4

        step_names = [s.step_name for s in result.steps]
        assert step_names == [
            "vendor_reader",
            "score_calculator",
            "risk_assessor",
            "output_validator",
        ]

        step_nos = [s.step_no for s in result.steps]
        assert step_nos == [1, 2, 3, 4]

        for step in result.steps:
            assert step.success is True, f"Step {step.step_no} ({step.step_name}) が失敗"


# ─────────────────────────────────────────────────────────────────────
# テスト 5: 複数取引先の処理
# ─────────────────────────────────────────────────────────────────────
class TestMultipleVendorsProcessed:
    @pytest.mark.asyncio
    async def test_multiple_vendors_processed(self):
        """複数の取引先が全件スコア計算・リスク評価される。"""
        input_data = {
            "vendors": [
                HEALTHY_VENDOR,
                HIGH_CONCENTRATION_VENDOR,
                PAYMENT_DELAY_VENDOR,
            ]
        }

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        # 全3件処理済み
        assert result.final_output["vendor_count"] == 3
        assert len(result.final_output["scored_vendors"]) == 3

        # healthy vendorはリスクなし
        healthy_detail = next(
            d for d in result.final_output["risk_details"]
            if d["vendor_name"] == "山田物産"
        )
        assert healthy_detail["has_risk"] is False

        # リスクある取引先が2件
        risk_details_with_risk = [
            d for d in result.final_output["risk_details"] if d["has_risk"]
        ]
        assert len(risk_details_with_risk) == 2


# ─────────────────────────────────────────────────────────────────────
# テスト 6: スコアが0-100の範囲内
# ─────────────────────────────────────────────────────────────────────
class TestScoreRangeValid:
    @pytest.mark.asyncio
    async def test_score_range_valid(self):
        """全取引先のスコアが MIN_SCORE 以上 MAX_SCORE 以下である。"""
        extreme_vendors = [
            {
                "vendor_name": "最悪取引先",
                "vendor_id": "V_BAD",
                "annual_transaction_amount": 0,
                "relationship_years": 0,
                "avg_payment_delay_days": 999,
                "incident_count": 100,
                "transaction_ratio": 0.99,
            },
            {
                "vendor_name": "最高取引先",
                "vendor_id": "V_BEST",
                "annual_transaction_amount": 100_000_000,
                "relationship_years": 30,
                "avg_payment_delay_days": 0,
                "incident_count": 0,
                "transaction_ratio": 0.01,
            },
            HEALTHY_VENDOR,
            HIGH_CONCENTRATION_VENDOR,
            PAYMENT_DELAY_VENDOR,
        ]

        input_data = {"vendors": extreme_vendors}

        with patch(
            "workers.bpo.common.pipelines.vendor_pipeline.run_output_validator",
            new=AsyncMock(return_value=_mock_validator_out()),
        ):
            result = await run_vendor_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        for sv in result.final_output["scored_vendors"]:
            score = sv["score"]
            assert MIN_SCORE <= score <= MAX_SCORE, (
                f"{sv['vendor_name']}: スコア範囲外 {score}"
            )
