"""建設業 見積パイプライン テスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.construction.pipelines.estimation_pipeline import (
    run_estimation_pipeline,
    EstimationPipelineResult,
    CONFIDENCE_WARNING_THRESHOLD,
)


COMPANY_ID = "test-company-001"

# テスト用モック工事仕様テキスト
MOCK_SPEC_TEXT = """
令和7年度 テスト道路改良工事
発注者: テスト市
工種別内訳書

掘削工
  路体盛土（河砂利）
  m3
  100
  1500
  単-001

舗装工
  上層路盤工（粒度調整砕石 t=150）
  m2
  500
  2800

コンクリート工
  型枠工
  m2
  80
  4500
"""


class TestEstimationPipelineHappyPath:
    """正常系テスト"""

    @pytest.mark.asyncio
    async def test_text_input_completes_8_steps(self):
        """テキスト入力で9ステップが全て実行される（Step9: anomaly_detector を含む）"""
        mock_items = [
            MagicMock(model_dump=lambda mode=None: {
                "category": "土工", "detail": "掘削工",
                "quantity": 100, "unit": "m3", "unit_price": 1500,
                "price_candidates": [{"source": "past_record", "unit_price": 1500, "confidence": 0.85}],
            }),
            MagicMock(model_dump=lambda mode=None: {
                "category": "舗装工", "detail": "路盤工",
                "quantity": 500, "unit": "m2", "unit_price": 2800,
                "price_candidates": [{"source": "past_record", "unit_price": 2800, "confidence": 0.80}],
            }),
        ]
        mock_priced = [MagicMock(model_dump=lambda mode=None: {
            **item.model_dump(), "price_candidates": item.model_dump()["price_candidates"]
        }) for item in mock_items]

        mock_overhead = MagicMock(
            direct_cost=290000, common_temporary=14500,
            site_management=60900, general_admin=44086, total=409486,
        )
        mock_overhead.model_dump = lambda mode=None: {
            "direct_cost": 290000, "common_temporary": 14500,
            "site_management": 60900, "general_admin": 44086, "total": 409486,
        }

        mock_breakdown = {
            "title": "工事費内訳書", "items": [i.model_dump() for i in mock_items],
            "direct_cost": 290000, "total_cost": 409486,
            "overhead": mock_overhead.model_dump(),
            "headers": ["工種", "種別", "細別", "規格", "数量", "単位", "単価", "金額"],
            "rows": [],
        }

        mock_ep = AsyncMock()
        mock_ep.extract_quantities = AsyncMock(return_value=mock_items)
        mock_ep.suggest_unit_prices = AsyncMock(return_value=mock_priced)
        mock_ep.calculate_overhead = AsyncMock(return_value=mock_overhead)
        mock_ep.generate_breakdown_data = AsyncMock(return_value=mock_breakdown)

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result: EstimationPipelineResult = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": MOCK_SPEC_TEXT},
                project_id="proj-001",
            )

        assert result.success is True
        assert len(result.steps) == 9  # Step 9: anomaly_detector が追加された
        assert result.failed_step is None
        assert result.total_duration_ms >= 0
        # Step名が正しい順序か確認
        step_names = [s.step_name for s in result.steps]
        assert step_names[0] == "document_ocr"
        assert step_names[7] == "output_validator"
        assert step_names[8] == "anomaly_detector"

    @pytest.mark.asyncio
    async def test_direct_items_input_skips_ocr(self):
        """items直渡しはOCRがスキップされ Step1 confidence=1.0"""
        mock_ep = AsyncMock()
        mock_ep.suggest_unit_prices = AsyncMock(return_value=[])
        mock_ep.generate_breakdown_data = AsyncMock(return_value={
            "title": "test", "items": [], "direct_cost": 0, "total_cost": 0, "overhead": {}, "headers": [], "rows": [],
        })

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"items": []},
            )

        step1 = result.steps[0]
        assert step1.step_name == "document_ocr"
        assert step1.result["source"] == "direct_items"
        assert step1.confidence == 1.0

    @pytest.mark.asyncio
    async def test_pipeline_result_summary(self):
        """summaryメソッドが適切な文字列を返す"""
        mock_ep = AsyncMock()
        mock_ep.extract_quantities = AsyncMock(return_value=[])
        mock_ep.suggest_unit_prices = AsyncMock(return_value=[])
        mock_ep.generate_breakdown_data = AsyncMock(return_value={
            "title": "test", "items": [], "direct_cost": 0, "total_cost": 0,
            "overhead": {}, "headers": [], "rows": [],
        })

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": "テスト"},
            )

        summary = result.summary()
        assert "見積パイプライン" in summary
        assert "ステップ" in summary


class TestEstimationPipelineErrors:
    """エラーハンドリング テスト"""

    @pytest.mark.asyncio
    async def test_ocr_failure_stops_pipeline(self):
        """OCR失敗でパイプラインが停止（Step1でfailed_step設定）"""
        result = await run_estimation_pipeline(
            company_id=COMPANY_ID,
            input_data={"file_path": "/nonexistent/file.pdf"},
        )
        # document_ocrはMicroAgentErrorを発生させるか失敗を返す
        # 失敗した場合はfailed_stepが設定される
        assert result.failed_step is not None or not result.success

    @pytest.mark.asyncio
    async def test_quantity_extraction_failure_stops_pipeline(self):
        """数量抽出失敗でパイプラインが停止"""
        mock_ep = AsyncMock()
        mock_ep.extract_quantities = AsyncMock(side_effect=Exception("DB接続エラー"))

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": MOCK_SPEC_TEXT},
            )

        assert result.success is False
        assert result.failed_step == "quantity_extractor"
        assert len(result.steps) == 2  # Step1(OCR) + Step2(extractor)まで実行


class TestEstimationPipelineCostTracking:
    """コスト・精度計測テスト"""

    @pytest.mark.asyncio
    async def test_total_cost_is_sum_of_steps(self):
        """total_cost_yen は各ステップの合計"""
        mock_ep = AsyncMock()
        mock_ep.extract_quantities = AsyncMock(return_value=[])
        mock_ep.suggest_unit_prices = AsyncMock(return_value=[])
        mock_ep.generate_breakdown_data = AsyncMock(return_value={
            "title": "test", "items": [], "direct_cost": 0, "total_cost": 0,
            "overhead": {}, "headers": [], "rows": [],
        })

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": "テスト"},
            )

        step_total = sum(s.cost_yen for s in result.steps)
        assert abs(result.total_cost_yen - step_total) < 0.001

    @pytest.mark.asyncio
    async def test_low_confidence_step_has_warning(self):
        """confidence < 閾値のステップは warning が設定される"""
        mock_ep = AsyncMock()
        mock_ep.extract_quantities = AsyncMock(return_value=[])  # 空→confidence低
        mock_ep.suggest_unit_prices = AsyncMock(return_value=[])
        mock_ep.generate_breakdown_data = AsyncMock(return_value={
            "title": "test", "items": [], "direct_cost": 0, "total_cost": 0,
            "overhead": {}, "headers": [], "rows": [],
        })

        with patch(
            "workers.bpo.construction.pipelines.estimation_pipeline.EstimationPipeline",
            return_value=mock_ep,
        ):
            result = await run_estimation_pipeline(
                company_id=COMPANY_ID,
                input_data={"text": "テスト"},
            )

        # quantity_extractor step (step 2) は items=0なので confidence < 閾値
        step2 = result.steps[1]
        if step2.confidence < CONFIDENCE_WARNING_THRESHOLD:
            assert step2.warning is not None
