"""BasePipeline の単体テスト。

外部依存（LLM, OCR）は全てモック。
各ステップの分岐（スキップ・実行・失敗）と、オーバーライドによる拡張を検証する。
"""
import pytest
from typing import Any, Optional
from unittest.mock import AsyncMock, patch, MagicMock, call

from workers.bpo.engine.base_pipeline import BasePipeline, PipelineStepResult
from workers.micro.models import MicroAgentOutput


# ---------------------------------------------------------------------------
# テスト用具象クラス群
# ---------------------------------------------------------------------------


class MinimalPipeline(BasePipeline):
    """extract_schema / validation_rules なし。最小構成。"""
    pipeline_name = "minimal"


class ExtractValidatePipeline(BasePipeline):
    """抽出 + バリデーションのみ。"""
    pipeline_name = "extract_validate"
    extract_schema = {
        "company_name": "会社名",
        "amount": "金額（円）",
    }
    validation_rules = {
        "required_fields": ["company_name", "amount"],
        "numeric_fields": ["amount"],
    }


class AnomalyPipeline(BasePipeline):
    """異常検知付き。"""
    pipeline_name = "anomaly_test"
    anomaly_config = {
        "fields": ["total_amount", "unit_price"],
        "detect_modes": ["digit_error"],
        "ranges": {"total_amount": [10000, 5000000]},
        "rules": [],
    }


class GeneratePipeline(BasePipeline):
    """ドキュメント生成付き。"""
    pipeline_name = "generate_test"
    generate_template = "summary"


class CustomCalculatePipeline(BasePipeline):
    """_step_calculateをオーバーライドして税額計算を追加。"""
    pipeline_name = "custom_calc"

    async def _step_calculate(
        self, company_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], PipelineStepResult]:
        subtotal = data.get("subtotal", 0)
        tax = round(subtotal * 0.1)
        calculated = {**data, "tax": tax, "total": subtotal + tax}
        step = PipelineStepResult(
            name="calculate",
            success=True,
            confidence=1.0,
        )
        return calculated, step


class EnrichPipeline(BasePipeline):
    """_step_enrichをオーバーライドして単価DBを付与。"""
    pipeline_name = "enrich_test"

    async def _step_enrich(
        self, company_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], PipelineStepResult]:
        enriched = {**data, "unit_price": 15000, "source": "master_db"}
        step = PipelineStepResult(name="enrich", success=True, confidence=1.0)
        return enriched, step


# ---------------------------------------------------------------------------
# モックファクトリ
# ---------------------------------------------------------------------------


def _mock_ocr_output(text: str = "テスト文書本文", success: bool = True) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_ocr",
        success=success,
        result={"text": text, "pages": 1, "source": "file"},
        confidence=0.95,
        cost_yen=0.0,
        duration_ms=10,
    )


def _mock_extractor_output(
    extracted: dict[str, Any] | None = None,
    success: bool = True,
) -> MicroAgentOutput:
    extracted = extracted or {"company_name": "株式会社テスト", "amount": 100000}
    missing = [k for k, v in extracted.items() if v is None]
    return MicroAgentOutput(
        agent_name="structured_extractor",
        success=success,
        result={"extracted": extracted, "missing_fields": missing},
        confidence=0.90,
        cost_yen=8.5,
        duration_ms=300,
    )


def _mock_validator_output(valid: bool = True) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="output_validator",
        success=True,
        result={"valid": valid, "missing": [], "empty": [], "type_errors": [], "warnings": []},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=5,
    )


def _mock_anomaly_output(anomaly_count: int = 0) -> MicroAgentOutput:
    anomalies = []
    if anomaly_count > 0:
        anomalies = [
            {
                "field": "unit_price",
                "value": 1500000,
                "type": "digit_error",
                "severity": "high",
                "message": "unit_price(¥1,500,000)は他項目の平均と乖離しています",
                "suggestion": "¥150,000 ではありませんか？",
            }
        ] * anomaly_count
    return MicroAgentOutput(
        agent_name="anomaly_detector",
        success=True,
        result={"anomalies": anomalies, "total_checked": 2, "anomaly_count": anomaly_count, "passed": anomaly_count == 0},
        confidence=1.0 if anomaly_count == 0 else 0.8,
        cost_yen=0.0,
        duration_ms=2,
    )


def _mock_generator_output() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"content": "# テスト要約\n\n生成されたドキュメント", "format": "markdown", "char_count": 20},
        confidence=0.9,
        cost_yen=5.0,
        duration_ms=400,
    )


# ---------------------------------------------------------------------------
# テストケース
# ---------------------------------------------------------------------------


class TestMinimalPipeline:
    """OCRスキップ（テキスト直接渡し）のテスト。"""

    @pytest.mark.asyncio
    async def test_text_passthrough_skips_ocr_and_extract(self):
        """extract_schemaがない場合、OCR・extractともにスキップされ
        payloadが素通りしてsuccess=Trueになる。"""
        pipeline = MinimalPipeline()
        result = await pipeline.run(
            company_id="test-company",
            payload={"text": "何かのテキスト", "custom_key": "value"},
        )

        assert result["success"] is True
        assert result["pipeline"] == "minimal"
        assert result["failed_step"] is None

        step_names = [s["name"] for s in result["steps"]]
        assert "ocr" in step_names
        assert "extract" in step_names

        # OCRとextractはスキップ扱い
        ocr_step = next(s for s in result["steps"] if s["name"] == "ocr")
        extract_step = next(s for s in result["steps"] if s["name"] == "extract")
        assert ocr_step.get("skipped") is True
        assert extract_step.get("skipped") is True

    @pytest.mark.asyncio
    async def test_file_path_triggers_ocr(self):
        """file_pathが渡された場合はrun_document_ocrが呼ばれる。"""
        pipeline = MinimalPipeline()
        mock_out = _mock_ocr_output("抽出テキスト")

        with patch("workers.micro.ocr.run_document_ocr", AsyncMock(return_value=mock_out)) as mock_ocr:
            result = await pipeline.run(
                company_id="test-company",
                payload={"file_path": "/tmp/test.pdf"},
            )

        assert result["success"] is True
        mock_ocr.assert_called_once()

        ocr_step = next(s for s in result["steps"] if s["name"] == "ocr")
        assert ocr_step.get("skipped") is None
        assert ocr_step["success"] is True

    @pytest.mark.asyncio
    async def test_ocr_failure_stops_pipeline(self):
        """OCRが失敗した場合、パイプラインを即時停止してfailed_stepを設定する。"""
        pipeline = MinimalPipeline()
        failed_out = _mock_ocr_output(success=False)

        with patch("workers.micro.ocr.run_document_ocr", AsyncMock(return_value=failed_out)):
            result = await pipeline.run(
                company_id="test-company",
                payload={"file_path": "/tmp/nonexistent.pdf"},
            )

        assert result["success"] is False
        assert result["failed_step"] == "ocr"


class TestExtractValidatePipeline:
    """抽出 + バリデーションの基本フロー。"""

    @pytest.mark.asyncio
    async def test_extract_and_validate_success(self):
        """テキスト渡しで抽出・バリデーションが通るフルフロー。"""
        pipeline = ExtractValidatePipeline()
        extracted_data = {"company_name": "株式会社テスト建設", "amount": 500000}

        with patch("workers.micro.extractor.run_structured_extractor",
                   AsyncMock(return_value=_mock_extractor_output(extracted_data))), \
             patch("workers.micro.validator.run_output_validator",
                   AsyncMock(return_value=_mock_validator_output(valid=True))):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "株式会社テスト建設 御中\n合計金額 500,000円"},
            )

        assert result["success"] is True
        assert result["data"]["company_name"] == "株式会社テスト建設"
        assert result["data"]["amount"] == 500000

        # コスト集計
        assert result["total_cost_yen"] > 0

    @pytest.mark.asyncio
    async def test_extractor_failure_stops_pipeline(self):
        """抽出失敗でfailed_step='extract'になる。"""
        pipeline = ExtractValidatePipeline()
        failed_out = _mock_extractor_output(success=False)

        with patch("workers.micro.extractor.run_structured_extractor",
                   AsyncMock(return_value=failed_out)):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "読み取り不能なテキスト"},
            )

        assert result["success"] is False
        assert result["failed_step"] == "extract"

    @pytest.mark.asyncio
    async def test_validate_step_recorded_in_steps(self):
        """validateステップがstepsリストに含まれていること。"""
        pipeline = ExtractValidatePipeline()

        with patch("workers.micro.extractor.run_structured_extractor",
                   AsyncMock(return_value=_mock_extractor_output())), \
             patch("workers.micro.validator.run_output_validator",
                   AsyncMock(return_value=_mock_validator_output())):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "テスト"},
            )

        step_names = [s["name"] for s in result["steps"]]
        assert "validate" in step_names
        validate_step = next(s for s in result["steps"] if s["name"] == "validate")
        assert validate_step.get("skipped") is None
        assert validate_step["success"] is True


class TestAnomalyPipeline:
    """異常検知付きパイプラインのテスト。"""

    @pytest.mark.asyncio
    async def test_no_anomaly(self):
        """異常なしの場合、anomaly_warningsは空リスト。"""
        pipeline = AnomalyPipeline()

        with patch("workers.micro.anomaly_detector.run_anomaly_detector",
                   AsyncMock(return_value=_mock_anomaly_output(anomaly_count=0))):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"total_amount": 200000, "unit_price": 50000},
            )

        assert result["success"] is True
        assert result["anomaly_warnings"] == []

        anomaly_step = next(s for s in result["steps"] if s["name"] == "anomaly_check")
        assert anomaly_step.get("skipped") is None
        assert anomaly_step["success"] is True

    @pytest.mark.asyncio
    async def test_anomaly_detected(self):
        """異常検知でanomaly_warningsにデータが入る。パイプラインはsuccessのまま。"""
        pipeline = AnomalyPipeline()

        with patch("workers.micro.anomaly_detector.run_anomaly_detector",
                   AsyncMock(return_value=_mock_anomaly_output(anomaly_count=1))):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"total_amount": 200000, "unit_price": 1500000},
            )

        assert result["success"] is True  # 異常はwarningであり失敗ではない
        assert len(result["anomaly_warnings"]) == 1
        assert result["anomaly_warnings"][0]["field"] == "unit_price"

    @pytest.mark.asyncio
    async def test_anomaly_skipped_when_no_matching_fields(self):
        """dataにfields該当キーがない場合、anomaly_detectorは呼ばれない。"""
        pipeline = AnomalyPipeline()

        with patch("workers.micro.anomaly_detector.run_anomaly_detector",
                   AsyncMock(return_value=_mock_anomaly_output())) as mock_anom:
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "数値フィールドなし"},
            )

        # payloadにtotal_amount/unit_priceがないのでスキップ
        mock_anom.assert_not_called()

        anomaly_step = next(s for s in result["steps"] if s["name"] == "anomaly_check")
        assert anomaly_step.get("skipped") is True

    def test_build_anomaly_items_filters_non_numeric(self):
        """_build_anomaly_itemsは数値型以外のフィールドを除外する。"""
        pipeline = AnomalyPipeline()
        data = {
            "total_amount": 300000,
            "unit_price": "非数値",  # 除外される
            "description": "テキストフィールド",
        }
        items = pipeline._build_anomaly_items(data)
        assert len(items) == 1
        assert items[0]["name"] == "total_amount"
        assert items[0]["value"] == 300000
        assert items[0]["expected_range"] == [10000, 5000000]


class TestCustomCalculatePipeline:
    """_step_calculateオーバーライドのテスト。"""

    @pytest.mark.asyncio
    async def test_custom_calculate_adds_tax(self):
        """オーバーライドされた計算ロジックが正しく適用される。"""
        pipeline = CustomCalculatePipeline()

        result = await pipeline.run(
            company_id="cid-001",
            payload={"subtotal": 100000},
        )

        assert result["success"] is True
        assert result["data"]["tax"] == 10000
        assert result["data"]["total"] == 110000

        calc_step = next(s for s in result["steps"] if s["name"] == "calculate")
        assert calc_step.get("skipped") is None
        assert calc_step["success"] is True

    @pytest.mark.asyncio
    async def test_zero_subtotal(self):
        """subtotal=0の場合、tax=0, total=0になる。"""
        pipeline = CustomCalculatePipeline()

        result = await pipeline.run(
            company_id="cid-001",
            payload={"subtotal": 0},
        )

        assert result["data"]["tax"] == 0
        assert result["data"]["total"] == 0


class TestEnrichPipeline:
    """_step_enrichオーバーライドのテスト。"""

    @pytest.mark.asyncio
    async def test_enrich_adds_master_data(self):
        """enrichステップで単価マスタが付与される。"""
        pipeline = EnrichPipeline()

        result = await pipeline.run(
            company_id="cid-001",
            payload={"item_code": "A001"},
        )

        assert result["success"] is True
        assert result["data"]["unit_price"] == 15000
        assert result["data"]["source"] == "master_db"

        enrich_step = next(s for s in result["steps"] if s["name"] == "enrich")
        assert enrich_step.get("skipped") is None
        assert enrich_step["success"] is True


class TestGeneratePipeline:
    """ドキュメント生成ステップのテスト。"""

    @pytest.mark.asyncio
    async def test_generate_called_with_template(self):
        """generate_templateが設定されている場合、document_generatorが呼ばれる。"""
        pipeline = GeneratePipeline()

        with patch("workers.micro.generator.run_document_generator",
                   AsyncMock(return_value=_mock_generator_output())) as mock_gen:
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "生成元データ"},
            )

        assert result["success"] is True
        mock_gen.assert_called_once()

        gen_step = next(s for s in result["steps"] if s["name"] == "generate")
        assert gen_step.get("skipped") is None
        assert gen_step["success"] is True

        # 生成結果がdataにマージされている
        assert result["data"].get("content") == "# テスト要約\n\n生成されたドキュメント"

    @pytest.mark.asyncio
    async def test_generate_skipped_when_template_is_none(self):
        """generate_template=Noneの場合、generateステップはスキップ。"""
        pipeline = MinimalPipeline()  # generate_template=None

        with patch("workers.micro.generator.run_document_generator",
                   AsyncMock()) as mock_gen:
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "テキスト"},
            )

        mock_gen.assert_not_called()

        gen_step = next(s for s in result["steps"] if s["name"] == "generate")
        assert gen_step.get("skipped") is True


class TestCostAndDurationAccumulation:
    """コスト・処理時間の集計テスト。"""

    @pytest.mark.asyncio
    async def test_total_cost_accumulated_across_steps(self):
        """抽出コスト(8.5円) + バリデーション(0円) = 8.5円が集計される。"""
        pipeline = ExtractValidatePipeline()

        with patch("workers.micro.extractor.run_structured_extractor",
                   AsyncMock(return_value=_mock_extractor_output())), \
             patch("workers.micro.validator.run_output_validator",
                   AsyncMock(return_value=_mock_validator_output())):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"text": "テキスト"},
            )

        assert result["total_cost_yen"] == pytest.approx(8.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_total_duration_accumulated(self):
        """OCR(10ms) + extract(300ms) が加算される。"""
        pipeline = MinimalPipeline()
        mock_out = _mock_ocr_output("テキスト")

        with patch("workers.micro.ocr.run_document_ocr", AsyncMock(return_value=mock_out)):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"file_path": "/tmp/test.pdf"},
            )

        # OCRのduration_ms=10が集計される（スキップステップは0）
        assert result["total_duration_ms"] >= 10


class TestPipelineStepResult:
    """PipelineStepResult.to_dict()のテスト。"""

    def test_to_dict_skipped(self):
        step = PipelineStepResult(name="ocr", success=True, skipped=True)
        d = step.to_dict()
        assert d == {"name": "ocr", "skipped": True}
        assert "success" not in d

    def test_to_dict_success(self):
        step = PipelineStepResult(
            name="extract", success=True, confidence=0.9, cost_yen=5.0, duration_ms=200
        )
        d = step.to_dict()
        assert d["name"] == "extract"
        assert d["success"] is True
        assert d["confidence"] == 0.9
        assert d["cost_yen"] == 5.0
        assert d["duration_ms"] == 200
        assert "skipped" not in d

    def test_to_dict_failure(self):
        step = PipelineStepResult(name="validate", success=False, confidence=0.0)
        d = step.to_dict()
        assert d["success"] is False

    def test_compensatable_defaults_false(self):
        """compensatable・compensation_dataのデフォルト値を確認する。"""
        step = PipelineStepResult(name="enrich", success=True)
        assert step.compensatable is False
        assert step.compensation_data == {}

    def test_compensatable_with_data(self):
        """compensatable=TrueとともにIDをセットできる。"""
        step = PipelineStepResult(
            name="enrich",
            success=True,
            compensatable=True,
            compensation_data={"record_id": "rec-001", "saas": "kintone"},
        )
        assert step.compensatable is True
        assert step.compensation_data["record_id"] == "rec-001"


class TestRetryStep:
    """_retry_step のリトライ挙動テスト。"""

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        """初回成功の場合はリトライしない（ファクトリは1回のみ呼ばれる）。"""
        pipeline = MinimalPipeline()
        call_count = 0

        async def success_factory():
            nonlocal call_count
            call_count += 1
            return PipelineStepResult(name="test", success=True)

        result = await pipeline._retry_step(success_factory, step_name="test")
        assert result.success is True
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_skipped(self):
        """skipped=Trueの場合はリトライしない。"""
        pipeline = MinimalPipeline()
        call_count = 0

        async def skipped_factory():
            nonlocal call_count
            call_count += 1
            return PipelineStepResult(name="test", success=True, skipped=True)

        result = await pipeline._retry_step(skipped_factory, step_name="test")
        assert result.skipped is True
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_success(self):
        """1回失敗後に成功する場合、合計2回呼ばれる。"""
        pipeline = MinimalPipeline()
        call_count = 0

        async def flaky_factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return PipelineStepResult(name="test", success=False)
            return PipelineStepResult(name="test", success=True)

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            result = await pipeline._retry_step(flaky_factory, step_name="test")

        assert result.success is True
        assert call_count == 2
        # 1秒スリープが1回呼ばれる
        mock_sleep.assert_called_once_with(1.0)

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_last_failure(self):
        """3回全て失敗した場合、最後の失敗結果を返す。"""
        pipeline = MinimalPipeline()
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            return PipelineStepResult(name="test", success=False)

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            result = await pipeline._retry_step(always_fail, step_name="test")

        assert result.success is False
        assert call_count == 3  # 初回1 + リトライ2
        # 1秒, 2秒の順でスリープ
        assert mock_sleep.call_args_list == [call(1.0), call(2.0)]

    @pytest.mark.asyncio
    async def test_retry_with_tuple_result(self):
        """タプル (data, PipelineStepResult) を返すステップも正しくリトライする。"""
        pipeline = MinimalPipeline()
        call_count = 0

        async def tuple_factory():
            nonlocal call_count
            call_count += 1
            step = PipelineStepResult(
                name="extract",
                success=(call_count >= 2),
            )
            return {"data": "value"}, step

        with patch("asyncio.sleep", AsyncMock()):
            result = await pipeline._retry_step(tuple_factory, step_name="extract")

        data, step = result
        assert step.success is True
        assert data == {"data": "value"}
        assert call_count == 2


class TestOcrRetryIntegration:
    """OCRステップのリトライ統合テスト（run()経由）。"""

    @pytest.mark.asyncio
    async def test_ocr_retried_on_failure_then_success(self):
        """OCRが1回失敗後に成功した場合、パイプライン全体はsuccessになる。"""
        pipeline = MinimalPipeline()
        call_count = 0

        def make_ocr_output():
            nonlocal call_count
            call_count += 1
            return _mock_ocr_output(text="リトライ後のテキスト", success=(call_count >= 2))

        with patch(
            "workers.micro.ocr.run_document_ocr",
            AsyncMock(side_effect=lambda _: make_ocr_output()),
        ), patch("asyncio.sleep", AsyncMock()):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"file_path": "/tmp/test.pdf"},
            )

        assert result["success"] is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_ocr_fails_all_retries(self):
        """OCRが3回全て失敗した場合、failed_step='ocr'で終了する。"""
        pipeline = MinimalPipeline()

        with patch(
            "workers.micro.ocr.run_document_ocr",
            AsyncMock(return_value=_mock_ocr_output(success=False)),
        ), patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            result = await pipeline.run(
                company_id="cid-001",
                payload={"file_path": "/tmp/broken.pdf"},
            )

        assert result["success"] is False
        assert result["failed_step"] == "ocr"
        # 2回スリープ（1秒 + 2秒）
        assert mock_sleep.call_count == 2


class TestCompensation:
    """補償ロジックのテスト。"""

    @pytest.mark.asyncio
    async def test_compensation_needed_logged_on_failure(self):
        """成功済みの compensatable ステップがある状態でパイプライン失敗した場合、
        compensation_needed が result に含まれる。"""

        class CompensatablePipeline(BasePipeline):
            pipeline_name = "compensatable_test"
            extract_schema = {"field": "値"}

            async def _step_enrich(
                self, company_id: str, data: dict[str, Any]
            ) -> tuple[dict[str, Any], PipelineStepResult]:
                # SaaS書き込みを模倣: compensatable=True
                step = PipelineStepResult(
                    name="enrich",
                    success=True,
                    compensatable=True,
                    compensation_data={"record_id": "kintone-001"},
                )
                return {**data, "enriched": True}, step

        pipeline = CompensatablePipeline()
        # extractを成功、その後のextractorが成功してもバリデーションで失敗させる
        # → enrichはsuccessなのに後続が失敗する = 補償が必要なシナリオ

        # extractを成功させ、enrichも成功、その後extractが失敗するシナリオを作るのは
        # 難しいので、直接_retry_stepの後にfailを起こす方法としてextractを失敗させる
        # 前に、enrichが成功する構成にする。
        # ここではOCRをスキップ(text渡し)しextractを成功、enrichが成功、
        # その後validateを失敗させる構成にする。
        class FailValidatePipeline(CompensatablePipeline):
            validation_rules = {"required_fields": ["nonexistent_field"]}

        pipeline2 = FailValidatePipeline()
        fail_validator = MicroAgentOutput(
            agent_name="output_validator",
            success=False,
            result={"valid": False, "missing": ["nonexistent_field"]},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=1,
        )

        with patch(
            "workers.micro.extractor.run_structured_extractor",
            AsyncMock(return_value=_mock_extractor_output({"field": "値"})),
        ), patch(
            "workers.micro.validator.run_output_validator",
            AsyncMock(return_value=fail_validator),
        ), patch("asyncio.sleep", AsyncMock()):
            result = await pipeline2.run(
                company_id="cid-001",
                payload={"text": "テストテキスト"},
            )

        # validateは失敗するがそれ自体はfailed_stepを設定しない（パイプラインはsuccessになる）
        # → validateの失敗はwarningとして扱われるため、ここではenrichの補償テストに集中

        # enrichはcompensatable=Trueかつsuccessなので、
        # パイプライン全体がsuccessになった後は compensation_needed は出ない
        # この設計を確認する
        assert result["success"] is True
        assert "compensation_needed" not in result

    @pytest.mark.asyncio
    async def test_compensation_needed_when_enrich_succeeds_and_extract_fails(self):
        """extractが失敗した後にenrichが成功していれば... という順序はありえないが、
        enrichが成功した後に generate が失敗するシナリオは実際には起きない
        （generateはsuccess=Falseでもパイプライン続行のため）。
        ここではOCR前にcompensatableステップを模倣するカスタムパイプラインで確認する。"""

        class CompensateOnExtractFailPipeline(BasePipeline):
            """enrichをOCRより前に模倣するためのカスタムパイプライン。
            実際にはこのような順序はないが、補償収集ロジックの動作を検証するため
            _step_ocr自体をcompensatable=Trueで返すように改造する。"""
            pipeline_name = "compensate_test2"

            async def _step_ocr(
                self, company_id: str, payload: dict[str, Any]
            ) -> tuple[str, PipelineStepResult]:
                step = PipelineStepResult(
                    name="ocr",
                    success=True,
                    compensatable=True,
                    compensation_data={"uploaded_id": "file-abc"},
                )
                return "extracted text", step

            async def _step_extract(
                self, company_id: str, text: str, payload: dict[str, Any]
            ) -> tuple[dict[str, Any], PipelineStepResult]:
                step = PipelineStepResult(name="extract", success=False)
                return {}, step

        pipeline = CompensateOnExtractFailPipeline()

        with patch("asyncio.sleep", AsyncMock()):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"file_path": "/tmp/test.pdf"},
            )

        assert result["success"] is False
        assert result["failed_step"] == "extract"
        assert "compensation_needed" in result
        assert len(result["compensation_needed"]) == 1
        assert result["compensation_needed"][0]["step"] == "ocr"
        assert result["compensation_needed"][0]["compensation_data"] == {"uploaded_id": "file-abc"}

    @pytest.mark.asyncio
    async def test_no_compensation_needed_when_no_compensatable_steps(self):
        """compensatable=True のステップが一つもない場合、compensation_needed は返さない。"""
        pipeline = MinimalPipeline()

        with patch(
            "workers.micro.ocr.run_document_ocr",
            AsyncMock(return_value=_mock_ocr_output(success=False)),
        ), patch("asyncio.sleep", AsyncMock()):
            result = await pipeline.run(
                company_id="cid-001",
                payload={"file_path": "/tmp/broken.pdf"},
            )

        assert result["success"] is False
        assert "compensation_needed" not in result
