"""共通マイクロエージェント テスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.validator import run_output_validator


COMPANY_ID = "test-company-001"


# ─── document_ocr ───────────────────────────────────────────────────────────

class TestDocumentOcr:
    @pytest.mark.asyncio
    async def test_text_passthrough(self):
        """テキスト直渡しは confidence=1.0 でそのまま返る"""
        out = await run_document_ocr(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="document_ocr",
            payload={"text": "工事名: テスト道路工事\n数量: 100m3"},
        ))
        assert out.success is True
        assert out.confidence == 1.0
        assert "テスト道路工事" in out.result["text"]
        assert out.result["source"] == "text"
        assert out.cost_yen == 0.0

    @pytest.mark.asyncio
    async def test_no_input_returns_error(self):
        """file_path も text もない場合は MicroAgentError"""
        with pytest.raises(MicroAgentError) as exc_info:
            await run_document_ocr(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="document_ocr",
                payload={},
            ))
        assert "document_ocr" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_error(self):
        """存在しないファイルは MicroAgentError"""
        with pytest.raises(MicroAgentError):
            await run_document_ocr(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="document_ocr",
                payload={"file_path": "/nonexistent/path/test.pdf"},
            ))

    @pytest.mark.asyncio
    async def test_txt_file(self, tmp_path):
        """テキストファイルは confidence=1.0 で読み込める"""
        f = tmp_path / "test.txt"
        f.write_text("テスト内容\n工種: 掘削工\n数量: 50m3", encoding="utf-8")
        out = await run_document_ocr(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="document_ocr",
            payload={"file_path": str(f)},
        ))
        assert out.success is True
        assert "掘削工" in out.result["text"]
        assert out.confidence == 1.0

    @pytest.mark.asyncio
    async def test_document_ai_pdf(self, tmp_path):
        """Document AI が利用可能・環境変数あり → confidence=0.9, source=document_ai"""
        f = tmp_path / "invoice.pdf"
        f.write_bytes(b"%PDF-1.4 dummy content")

        # Document AI レスポンスのモック
        mock_page = MagicMock()
        mock_doc = MagicMock()
        mock_doc.text = "請求書\n工事名: テスト橋梁工事\n金額: 1,500,000円"
        mock_doc.pages = [mock_page, mock_page]  # 2ページ

        mock_result = MagicMock()
        mock_result.document = mock_doc

        mock_client = MagicMock()
        mock_client.processor_path.return_value = "projects/p/locations/us/processors/proc"
        mock_client.process_document.return_value = mock_result

        mock_documentai = MagicMock()
        mock_documentai.DocumentProcessorServiceClient.return_value = mock_client
        mock_documentai.RawDocument.return_value = MagicMock()
        mock_documentai.ProcessRequest.return_value = MagicMock()

        with patch.dict("os.environ", {
            "DOCUMENT_AI_PROJECT_ID": "test-project",
            "DOCUMENT_AI_PROCESSOR_ID": "test-processor",
            "DOCUMENT_AI_LOCATION": "us",
        }):
            with patch("workers.micro.ocr._DOCUMENTAI_AVAILABLE", True):
                with patch("workers.micro.ocr.documentai", mock_documentai):
                    out = await run_document_ocr(MicroAgentInput(
                        company_id=COMPANY_ID,
                        agent_name="document_ocr",
                        payload={"file_path": str(f)},
                    ))

        assert out.success is True
        assert out.result["source"] == "document_ai"
        assert out.confidence == 0.9
        assert out.cost_yen == 2 * 2.0  # 2ページ × ¥2.0
        assert "テスト橋梁工事" in out.result["text"]
        assert out.result["pages"] == 2

    @pytest.mark.asyncio
    async def test_document_ai_image_png(self, tmp_path):
        """PNG画像もDocument AIで処理できる"""
        f = tmp_path / "receipt.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n dummy png content")

        mock_page = MagicMock()
        mock_doc = MagicMock()
        mock_doc.text = "領収書\n品名: セメント\n数量: 200袋"
        mock_doc.pages = [mock_page]

        mock_result = MagicMock()
        mock_result.document = mock_doc

        mock_client = MagicMock()
        mock_client.processor_path.return_value = "projects/p/locations/us/processors/proc"
        mock_client.process_document.return_value = mock_result

        mock_documentai = MagicMock()
        mock_documentai.DocumentProcessorServiceClient.return_value = mock_client
        mock_documentai.RawDocument.return_value = MagicMock()
        mock_documentai.ProcessRequest.return_value = MagicMock()

        with patch.dict("os.environ", {
            "DOCUMENT_AI_PROJECT_ID": "test-project",
            "DOCUMENT_AI_PROCESSOR_ID": "test-processor",
        }):
            with patch("workers.micro.ocr._DOCUMENTAI_AVAILABLE", True):
                with patch("workers.micro.ocr.documentai", mock_documentai):
                    out = await run_document_ocr(MicroAgentInput(
                        company_id=COMPANY_ID,
                        agent_name="document_ocr",
                        payload={"file_path": str(f)},
                    ))

        assert out.success is True
        assert out.result["source"] == "document_ai"
        assert out.cost_yen == 1 * 2.0  # 1ページ × ¥2.0

    @pytest.mark.asyncio
    async def test_document_ai_failure_falls_back_to_pypdf(self, tmp_path):
        """Document AI がエラー → pypdf フォールバック → confidence=0.6, source=pypdf"""
        f = tmp_path / "contract.pdf"
        f.write_bytes(b"%PDF-1.4 dummy")

        mock_documentai = MagicMock()
        mock_documentai.DocumentProcessorServiceClient.side_effect = Exception("API Error")

        mock_reader = MagicMock()
        mock_page_obj = MagicMock()
        mock_page_obj.extract_text.return_value = "契約書\n工事名: テスト工事"
        mock_reader.pages = [mock_page_obj]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict("os.environ", {
            "DOCUMENT_AI_PROJECT_ID": "test-project",
            "DOCUMENT_AI_PROCESSOR_ID": "test-processor",
        }):
            with patch("workers.micro.ocr._DOCUMENTAI_AVAILABLE", True):
                with patch("workers.micro.ocr.documentai", mock_documentai):
                    with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
                        out = await run_document_ocr(MicroAgentInput(
                            company_id=COMPANY_ID,
                            agent_name="document_ocr",
                            payload={"file_path": str(f)},
                        ))

        assert out.success is True
        assert out.result["source"] == "pypdf"
        assert out.confidence == 0.6
        assert out.cost_yen == 0.0

    @pytest.mark.asyncio
    async def test_no_env_vars_falls_back_to_pypdf(self, tmp_path):
        """環境変数未設定 → Document AI スキップ → pypdf フォールバック"""
        f = tmp_path / "spec.pdf"
        f.write_bytes(b"%PDF-1.4 dummy")

        mock_reader = MagicMock()
        mock_page_obj = MagicMock()
        mock_page_obj.extract_text.return_value = "仕様書\n工種: 鉄筋工"
        mock_reader.pages = [mock_page_obj]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        # 環境変数を明示的に除去
        with patch.dict("os.environ", {}, clear=False):
            import os
            saved_project = os.environ.pop("DOCUMENT_AI_PROJECT_ID", None)
            saved_processor = os.environ.pop("DOCUMENT_AI_PROCESSOR_ID", None)
            try:
                with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
                    out = await run_document_ocr(MicroAgentInput(
                        company_id=COMPANY_ID,
                        agent_name="document_ocr",
                        payload={"file_path": str(f)},
                    ))
            finally:
                if saved_project:
                    os.environ["DOCUMENT_AI_PROJECT_ID"] = saved_project
                if saved_processor:
                    os.environ["DOCUMENT_AI_PROCESSOR_ID"] = saved_processor

        assert out.success is True
        assert out.result["source"] == "pypdf"
        assert out.confidence == 0.6

    @pytest.mark.asyncio
    async def test_image_no_documentai_falls_back_to_mock(self, tmp_path):
        """Document AI が使えない状態で画像ファイル → mock フォールバック"""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff dummy jpeg")

        with patch("workers.micro.ocr._DOCUMENTAI_AVAILABLE", False):
            out = await run_document_ocr(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="document_ocr",
                payload={"file_path": str(f)},
            ))

        assert out.success is True
        assert out.result["source"] == "mock"
        assert out.confidence == 0.3
        assert out.cost_yen == 0.0


# ─── structured_extractor ───────────────────────────────────────────────────

class TestStructuredExtractor:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        """正常系: LLMが正しいJSONを返す"""
        mock_response = MagicMock()
        mock_response.content = '{"工事名": "テスト道路改良工事", "数量": 100, "単位": "m3"}'
        mock_response.cost_yen = 0.05

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("workers.micro.extractor.get_llm_client", return_value=mock_llm):
            out = await run_structured_extractor(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="structured_extractor",
                payload={
                    "text": "工事名: テスト道路改良工事 数量100m3",
                    "schema": {"工事名": "工事の名称", "数量": "工事数量", "単位": "数量の単位"},
                },
            ))

        assert out.success is True
        assert out.result["extracted"]["工事名"] == "テスト道路改良工事"
        assert out.result["extracted"]["数量"] == 100
        assert out.confidence > 0.8
        assert out.cost_yen == 0.05

    @pytest.mark.asyncio
    async def test_empty_text_raises_error(self):
        """テキストが空の場合は MicroAgentError"""
        with pytest.raises(MicroAgentError):
            await run_structured_extractor(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="structured_extractor",
                payload={"text": "", "schema": {"field": "desc"}},
            ))

    @pytest.mark.asyncio
    async def test_missing_fields_lower_confidence(self):
        """抽出できなかったフィールドがある場合 confidence が下がる"""
        mock_response = MagicMock()
        mock_response.content = '{"工事名": "テスト", "数量": null, "単位": null}'
        mock_response.cost_yen = 0.03

        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("workers.micro.extractor.get_llm_client", return_value=mock_llm):
            out = await run_structured_extractor(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="structured_extractor",
                payload={
                    "text": "工事名のみ記載あり",
                    "schema": {"工事名": "名称", "数量": "数量", "単位": "単位"},
                },
            ))

        assert out.success is True
        assert len(out.result["missing_fields"]) == 2
        assert out.confidence < 0.5


# ─── rule_matcher ────────────────────────────────────────────────────────────

class TestRuleMatcher:
    @pytest.mark.asyncio
    async def test_no_rules_returns_original_data(self):
        """DBにルールがない場合はextracted_dataをそのまま返す"""
        mock_db = MagicMock()
        mock_response = MagicMock()
        mock_response.data = []
        mock_db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.eq.return_value.order.return_value \
            .limit.return_value.execute.return_value = mock_response

        with patch("workers.micro.rule_matcher.get_service_client", return_value=mock_db):
            out = await run_rule_matcher(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="rule_matcher",
                payload={
                    "extracted_data": {"掘削": 100, "unit": "m3"},
                    "domain": "construction_estimation",
                },
            ))

        assert out.success is True
        assert out.result["applied_values"]["掘削"] == 100
        assert out.confidence == 0.5  # ルールなし → 0.5

    @pytest.mark.asyncio
    async def test_missing_domain_raises_error(self):
        """domain が空の場合は MicroAgentError"""
        with pytest.raises(MicroAgentError):
            await run_rule_matcher(MicroAgentInput(
                company_id=COMPANY_ID,
                agent_name="rule_matcher",
                payload={"extracted_data": {"field": "value"}, "domain": ""},
            ))


# ─── output_validator ────────────────────────────────────────────────────────

class TestOutputValidator:
    @pytest.mark.asyncio
    async def test_all_required_fields_present(self):
        """全必須フィールドが存在すれば valid=True, confidence=1.0"""
        out = await run_output_validator(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="output_validator",
            payload={
                "document": {"title": "見積書", "total": 1000000, "items": [{"name": "掘削工"}]},
                "required_fields": ["title", "total", "items"],
            },
        ))
        assert out.success is True
        assert out.result["valid"] is True
        assert out.result["missing"] == []
        assert out.confidence == 1.0

    @pytest.mark.asyncio
    async def test_missing_required_field(self):
        """必須フィールドが欠損すれば valid=False, confidence が下がる"""
        out = await run_output_validator(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="output_validator",
            payload={
                "document": {"title": "見積書"},
                "required_fields": ["title", "total", "items"],
            },
        ))
        assert out.success is True
        assert out.result["valid"] is False
        assert "total" in out.result["missing"]
        assert "items" in out.result["missing"]
        assert out.confidence < 1.0

    @pytest.mark.asyncio
    async def test_numeric_type_error(self):
        """数値フィールドに文字列が入っていれば type_errors に記録"""
        out = await run_output_validator(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="output_validator",
            payload={
                "document": {"total": "百万円", "title": "test"},
                "required_fields": ["title", "total"],
                "numeric_fields": ["total"],
            },
        ))
        assert out.result["valid"] is False
        assert len(out.result["type_errors"]) == 1

    @pytest.mark.asyncio
    async def test_positive_field_warning(self):
        """正の数チェックでゼロなら warnings に記録（エラーではない）"""
        out = await run_output_validator(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="output_validator",
            payload={
                "document": {"total": 0, "title": "test"},
                "required_fields": ["title", "total"],
                "positive_fields": ["total"],
            },
        ))
        assert out.result["valid"] is True  # warningはエラーではない
        assert any("ゼロ" in w for w in out.result["warnings"])

    @pytest.mark.asyncio
    async def test_cost_yen_is_zero(self):
        """output_validatorはLLM不使用なのでコストはゼロ"""
        out = await run_output_validator(MicroAgentInput(
            company_id=COMPANY_ID,
            agent_name="output_validator",
            payload={"document": {"a": 1}, "required_fields": ["a"]},
        ))
        assert out.cost_yen == 0.0
