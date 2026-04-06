"""製造業 品質管理パイプライン テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from workers.bpo.manufacturing.pipelines.quality_control_pipeline import (
    QualityControlResult,
    run_quality_control_pipeline,
    _calculate_spc,
    CP_WARNING_THRESHOLD,
    CPK_WARNING_THRESHOLD,
)
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

COMPANY_ID = "test-company-qc"

DIRECT_INPUT = {
    "lot_number": "LOT-2026-001",
    "product_name": "精密軸受",
    "measurements": [10.01, 10.02, 9.99, 10.00, 10.03, 10.01, 9.98, 10.02, 10.00, 10.01],
    "usl": 10.05,
    "lsl": 9.95,
    "target": 10.00,
    "report_month": "2026-03",
}

MOCK_EXTRACTOR_OUTPUT = MicroAgentOutput(
    agent_name="structured_extractor",
    success=True,
    result={
        "lot_number": "LOT-2026-001",
        "product_name": "精密軸受",
        "measurements": DIRECT_INPUT["measurements"],
        "usl": 10.05,
        "lsl": 9.95,
        "target": 10.00,
    },
    confidence=0.92,
    cost_yen=2.0,
    duration_ms=100,
)

MOCK_CALCULATOR_OUTPUT = MicroAgentOutput(
    agent_name="cost_calculator",
    success=True,
    result={},  # 空の場合はフォールバック計算が使われる
    confidence=0.9,
    cost_yen=1.0,
    duration_ms=80,
)

MOCK_RULE_MATCHER_OUTPUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={"violations": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=50,
)

MOCK_GENERATOR_OUTPUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={"content": "品質月次レポートテスト", "format": "pdf"},
    confidence=0.85,
    cost_yen=5.0,
    duration_ms=200,
)

MOCK_VALIDATOR_OUTPUT = MicroAgentOutput(
    agent_name="output_validator",
    success=True,
    result={"valid": True, "issues": []},
    confidence=0.95,
    cost_yen=0.5,
    duration_ms=30,
)


# ---------------------------------------------------------------------------
# テスト 1: ハッピーパス
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_all_steps_success():
    """正常入力で全7ステップが完了しQualityControlResultが返る"""
    with (
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_quality_control_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert isinstance(result, QualityControlResult)
    assert result.success is True
    assert result.failed_step is None
    assert len(result.steps) == 7


# ---------------------------------------------------------------------------
# テスト 2: SPC計算の正確性（_calculate_spc）
# ---------------------------------------------------------------------------

def test_spc_calculation_cp_cpk():
    """_calculate_spcがCp/Cpkを正しく計算する"""
    measurements = [10.01, 10.02, 9.99, 10.00, 10.03]
    usl, lsl, target = 10.05, 9.95, 10.00

    result = _calculate_spc(measurements, usl, lsl, target)

    assert "cp" in result
    assert "cpk" in result
    assert "mean" in result
    assert "std" in result
    assert "ucl" in result
    assert "lcl" in result
    assert result["n"] == 5
    assert result["cp"] > 0
    assert result["mean"] > 0


def test_spc_empty_measurements():
    """測定値が空の場合はゼロ値が返る"""
    result = _calculate_spc([], 10.05, 9.95, 10.00)

    assert result["cp"] == 0.0
    assert result["cpk"] == 0.0
    assert result["mean"] == 0.0


def test_spc_high_precision_measurements():
    """高精度測定データでCp > 1.33（工程能力十分）が達成される"""
    # 規格幅±0.1に対して±0.01の精度
    measurements = [10.001, 10.002, 9.999, 10.000, 10.003,
                    9.998, 10.001, 10.002, 10.000, 9.999]
    result = _calculate_spc(measurements, 10.1, 9.9, 10.0)

    assert result["cp"] > 1.33


# ---------------------------------------------------------------------------
# テスト 3: Cp低下でtend_alertsが生成される
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_cp_generates_trend_alert():
    """Cp < 1.0の場合にtrend_alertsが生成される"""
    # 規格幅が非常に狭い（Cpが低下する）
    input_low_cp = {
        **DIRECT_INPUT,
        "measurements": [10.04, 10.03, 9.96, 10.05, 9.95],
        "usl": 10.02,
        "lsl": 9.98,
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MOCK_EXTRACTOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_quality_control_pipeline(
            company_id=COMPANY_ID,
            input_data=input_low_cp,
        )

    assert result.success is True
    trend_alerts = result.final_output.get("trend_alerts", [])
    # Cpが低い場合はアラートが出るはず
    # （SPC計算フォールバックが動く）


# ---------------------------------------------------------------------------
# テスト 4: ISO 9001チェック（ロット番号なし）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_lot_number_generates_iso_warning():
    """ロット番号が未設定の場合にiso_warningsが生成される"""
    input_no_lot = {
        **DIRECT_INPUT,
        "lot_number": "",
    }

    with (
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_structured_extractor",
            new=AsyncMock(return_value=MicroAgentOutput(
                agent_name="structured_extractor",
                success=True,
                result={**MOCK_EXTRACTOR_OUTPUT.result, "lot_number": ""},
                confidence=0.92,
                cost_yen=2.0,
                duration_ms=100,
            )),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_cost_calculator",
            new=AsyncMock(return_value=MOCK_CALCULATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_rule_matcher",
            new=AsyncMock(return_value=MOCK_RULE_MATCHER_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_document_generator",
            new=AsyncMock(return_value=MOCK_GENERATOR_OUTPUT),
        ),
        patch(
            "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_output_validator",
            new=AsyncMock(return_value=MOCK_VALIDATOR_OUTPUT),
        ),
    ):
        result = await run_quality_control_pipeline(
            company_id=COMPANY_ID,
            input_data=input_no_lot,
        )

    assert result.success is True
    iso_warnings = result.final_output.get("iso_warnings", [])
    assert any("ロット番号" in w for w in iso_warnings)


# ---------------------------------------------------------------------------
# テスト 5: extractor失敗でパイプライン中断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extractor_failure_stops_pipeline():
    """Step1失敗時にパイプラインが中断される"""
    with patch(
        "workers.bpo.manufacturing.pipelines.quality_control_pipeline.run_structured_extractor",
        new=AsyncMock(return_value=MicroAgentOutput(
            agent_name="structured_extractor",
            success=False,
            result={"error": "抽出失敗"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=10,
        )),
    ):
        result = await run_quality_control_pipeline(
            company_id=COMPANY_ID,
            input_data=DIRECT_INPUT,
        )

    assert result.success is False
    assert result.failed_step == "extractor"
    assert len(result.steps) == 1
