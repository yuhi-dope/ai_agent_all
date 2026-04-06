"""image_classifier マイクロエージェント テスト。"""
import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.models import MicroAgentError, MicroAgentInput, MicroAgentOutput
from workers.micro.image_classifier import (
    run_image_classifier,
    _load_image_base64,
    _parse_llm_response,
    _make_fallback_result,
)

COMPANY_ID = "test-company-001"
CATEGORIES = ["基礎工事", "鉄骨工事", "外壁工事", "内装工事", "設備工事", "完成写真", "その他"]

# テスト用の最小画像 (1x1 pixel PNG, base64)
_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _make_input(payload: dict, agent_name: str = "image_classifier") -> MicroAgentInput:
    return MicroAgentInput(
        company_id=COMPANY_ID,
        agent_name=agent_name,
        payload=payload,
    )


def _make_llm_response(content: str, cost_yen: float = 0.05) -> MagicMock:
    mock = MagicMock()
    mock.content = content
    mock.cost_yen = cost_yen
    return mock


# ─── _load_image_base64 ───────────────────────────────────────────────────────

class TestLoadImageBase64:
    def test_returns_image_base64_directly(self):
        payload = {"image_base64": _TINY_PNG_BASE64}
        result = _load_image_base64(payload)
        assert result == _TINY_PNG_BASE64

    def test_reads_file_from_path(self, tmp_path):
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0test image bytes")
        payload = {"image_path": str(img_file)}
        result = _load_image_base64(payload)
        expected = base64.b64encode(b"\xff\xd8\xff\xe0test image bytes").decode("utf-8")
        assert result == expected

    def test_raises_on_nonexistent_path(self):
        payload = {"image_path": "/nonexistent/path/image.jpg"}
        with pytest.raises(MicroAgentError) as exc_info:
            _load_image_base64(payload)
        assert "image_classifier" in str(exc_info.value)
        assert "存在しません" in str(exc_info.value)

    def test_raises_when_no_image_provided(self):
        with pytest.raises(MicroAgentError) as exc_info:
            _load_image_base64({})
        assert "image_path または image_base64" in str(exc_info.value)

    def test_prefers_base64_over_path(self, tmp_path):
        """image_base64 が優先される。"""
        img_file = tmp_path / "other.jpg"
        img_file.write_bytes(b"other image")
        payload = {
            "image_base64": _TINY_PNG_BASE64,
            "image_path": str(img_file),
        }
        result = _load_image_base64(payload)
        assert result == _TINY_PNG_BASE64


# ─── _parse_llm_response ─────────────────────────────────────────────────────

class TestParseLlmResponse:
    def test_parses_valid_json(self):
        raw = json.dumps({
            "primary_category": "鉄骨工事",
            "confidence_score": 0.85,
            "all_scores": {"鉄骨工事": 0.85, "基礎工事": 0.10, "その他": 0.05},
            "description": "鉄骨の組み立て作業",
            "labels": ["鉄骨工事"],
        }, ensure_ascii=False)
        result = _parse_llm_response(raw, CATEGORIES, multi_label=False)
        assert result["primary_category"] == "鉄骨工事"
        assert result["confidence_score"] == 0.85
        assert result["labels"] == ["鉄骨工事"]
        assert result["description"] == "鉄骨の組み立て作業"

    def test_multi_label_includes_high_score_categories(self):
        raw = json.dumps({
            "primary_category": "鉄骨工事",
            "confidence_score": 0.60,
            "all_scores": {"鉄骨工事": 0.60, "設備工事": 0.25, "その他": 0.15},
            "description": "鉄骨と設備が写っている",
            "labels": ["鉄骨工事", "設備工事"],
        }, ensure_ascii=False)
        result = _parse_llm_response(raw, CATEGORIES, multi_label=True)
        assert "鉄骨工事" in result["labels"]
        assert "設備工事" in result["labels"]
        assert "その他" not in result["labels"]

    def test_single_label_mode(self):
        raw = json.dumps({
            "primary_category": "外壁工事",
            "confidence_score": 0.70,
            "all_scores": {"外壁工事": 0.70, "鉄骨工事": 0.30},
            "description": "外壁施工中",
            "labels": ["外壁工事"],
        }, ensure_ascii=False)
        result = _parse_llm_response(raw, CATEGORIES, multi_label=False)
        assert result["labels"] == ["外壁工事"]

    def test_raises_on_non_json_response(self):
        with pytest.raises(MicroAgentError) as exc_info:
            _parse_llm_response("これはJSONではありません", CATEGORIES, multi_label=False)
        assert "parse" in str(exc_info.value)

    def test_clamps_confidence_score(self):
        raw = json.dumps({
            "primary_category": "基礎工事",
            "confidence_score": 1.5,  # 範囲外
            "all_scores": {"基礎工事": 1.5},
            "description": "基礎工事",
            "labels": ["基礎工事"],
        }, ensure_ascii=False)
        result = _parse_llm_response(raw, CATEGORIES, multi_label=False)
        assert result["confidence_score"] == 1.0

    def test_generates_all_scores_when_missing(self):
        raw = json.dumps({
            "primary_category": "内装工事",
            "confidence_score": 0.80,
            "description": "内装工事",
            "labels": ["内装工事"],
        }, ensure_ascii=False)
        result = _parse_llm_response(raw, CATEGORIES, multi_label=False)
        assert "内装工事" in result["all_scores"]
        assert result["all_scores"]["内装工事"] == 0.80


# ─── _make_fallback_result ───────────────────────────────────────────────────

class TestMakeFallbackResult:
    def test_returns_low_confidence(self):
        result = _make_fallback_result(CATEGORIES, multi_label=False)
        assert result["confidence_score"] == 0.3
        assert result["fallback"] is True
        assert result["primary_category"] == CATEGORIES[0]

    def test_all_scores_sum_approximately_one(self):
        result = _make_fallback_result(CATEGORIES, multi_label=False)
        total = sum(result["all_scores"].values())
        assert abs(total - 1.0) < 0.01

    def test_single_category(self):
        result = _make_fallback_result(["その他"], multi_label=False)
        assert result["primary_category"] == "その他"
        assert result["all_scores"]["その他"] == 0.3


# ─── run_image_classifier ────────────────────────────────────────────────────

class TestRunImageClassifier:
    @pytest.mark.asyncio
    async def test_basic_classification(self):
        """正常系: base64画像をLLMで分類できる。"""
        llm_json = json.dumps({
            "primary_category": "鉄骨工事",
            "confidence_score": 0.85,
            "all_scores": {"鉄骨工事": 0.85, "基礎工事": 0.10, "その他": 0.05},
            "description": "鉄骨の組み立て作業中。クレーンが写っている",
            "labels": ["鉄骨工事"],
        }, ensure_ascii=False)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(return_value=_make_llm_response(llm_json, cost_yen=0.05))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
                "context": "建設業の工事写真を分類してください",
                "multi_label": False,
            }))

        assert out.success is True
        assert out.agent_name == "image_classifier"
        assert out.result["primary_category"] == "鉄骨工事"
        assert out.result["confidence_score"] == 0.85
        assert out.confidence == 0.85
        assert out.cost_yen == 0.05
        assert out.result["labels"] == ["鉄骨工事"]
        assert "クレーン" in out.result["description"]

    @pytest.mark.asyncio
    async def test_multi_label_classification(self):
        """multi_label=True の場合、複数ラベルを返す。"""
        llm_json = json.dumps({
            "primary_category": "鉄骨工事",
            "confidence_score": 0.55,
            "all_scores": {
                "鉄骨工事": 0.55,
                "設備工事": 0.30,
                "その他": 0.15,
            },
            "description": "鉄骨と設備工事が進行中",
            "labels": ["鉄骨工事", "設備工事"],
        }, ensure_ascii=False)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(return_value=_make_llm_response(llm_json, cost_yen=0.06))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
                "context": "工事写真の分類",
                "multi_label": True,
            }))

        assert out.success is True
        assert "鉄骨工事" in out.result["labels"]
        assert "設備工事" in out.result["labels"]
        assert "その他" not in out.result["labels"]

    @pytest.mark.asyncio
    async def test_reads_image_from_file_path(self, tmp_path):
        """image_path からファイルを読み込んで分類できる。"""
        img_file = tmp_path / "test_construction.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0fake jpeg data")

        llm_json = json.dumps({
            "primary_category": "完成写真",
            "confidence_score": 0.90,
            "all_scores": {"完成写真": 0.90, "その他": 0.10},
            "description": "建物の完成写真",
            "labels": ["完成写真"],
        }, ensure_ascii=False)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(return_value=_make_llm_response(llm_json))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_path": str(img_file),
                "categories": CATEGORIES,
                "context": "建設業の工事写真を分類してください",
            }))

        assert out.success is True
        assert out.result["primary_category"] == "完成写真"

    @pytest.mark.asyncio
    async def test_image_not_found_raises_error(self):
        """存在しない image_path は MicroAgentError を送出する。"""
        with pytest.raises(MicroAgentError) as exc_info:
            await run_image_classifier(_make_input({
                "image_path": "/nonexistent/path/image.jpg",
                "categories": CATEGORIES,
            }))
        assert "image_classifier" in str(exc_info.value)
        assert "存在しません" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_image_raises_error(self):
        """image_path も image_base64 もない場合は MicroAgentError を送出する。"""
        with pytest.raises(MicroAgentError) as exc_info:
            await run_image_classifier(_make_input({
                "categories": CATEGORIES,
            }))
        assert "image_classifier" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_empty_categories_raises_error(self):
        """categories が空の場合は MicroAgentError を送出する。"""
        with pytest.raises(MicroAgentError) as exc_info:
            await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": [],
            }))
        assert "categories" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_llm_api_failure_returns_fallback(self):
        """LLM API が例外を投げた場合はフォールバック結果を返す（success=True）。"""
        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(side_effect=RuntimeError("API接続エラー"))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
                "context": "工事写真の分類",
            }))

        assert out.success is True
        assert out.confidence == 0.3
        assert out.cost_yen == 0.0
        assert out.result.get("fallback") is True
        assert out.result["primary_category"] == CATEGORIES[0]

    @pytest.mark.asyncio
    async def test_llm_returns_non_json_uses_fallback(self):
        """LLMがJSONでないレスポンスを返した場合はフォールバック結果を返す。"""
        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(
                return_value=_make_llm_response("これはJSON形式ではありません", cost_yen=0.01)
            )
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
            }))

        assert out.success is True
        assert out.confidence == 0.3
        assert out.result.get("fallback") is True

    @pytest.mark.asyncio
    async def test_cost_yen_from_llm_response(self):
        """cost_yen は LLM レスポンスの値をそのまま使う。"""
        llm_json = json.dumps({
            "primary_category": "基礎工事",
            "confidence_score": 0.75,
            "all_scores": {"基礎工事": 0.75, "その他": 0.25},
            "description": "基礎工事の写真",
            "labels": ["基礎工事"],
        }, ensure_ascii=False)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(return_value=_make_llm_response(llm_json, cost_yen=0.123))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
            }))

        assert out.cost_yen == 0.123

    @pytest.mark.asyncio
    async def test_llm_task_uses_fast_tier_and_vision(self):
        """LLMTask に tier=FAST と requires_vision=True が設定されることを確認する。"""
        from llm.client import LLMTask, ModelTier

        llm_json = json.dumps({
            "primary_category": "設備工事",
            "confidence_score": 0.65,
            "all_scores": {"設備工事": 0.65, "その他": 0.35},
            "description": "設備工事",
            "labels": ["設備工事"],
        }, ensure_ascii=False)

        captured_task: list[LLMTask] = []

        async def capture_generate(task: LLMTask):
            captured_task.append(task)
            return _make_llm_response(llm_json)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(side_effect=capture_generate)
            mock_get_llm.return_value = mock_llm

            await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
            }))

        assert len(captured_task) == 1
        task = captured_task[0]
        assert task.tier == ModelTier.FAST
        assert task.requires_vision is True
        assert task.task_type == "image_classifier"
        assert task.company_id == COMPANY_ID

    @pytest.mark.asyncio
    async def test_output_has_all_required_fields(self):
        """出力の result に仕様の全フィールドが含まれる。"""
        llm_json = json.dumps({
            "primary_category": "内装工事",
            "confidence_score": 0.70,
            "all_scores": {"内装工事": 0.70, "完成写真": 0.20, "その他": 0.10},
            "description": "内装工事の施工中",
            "labels": ["内装工事"],
        }, ensure_ascii=False)

        with patch("workers.micro.image_classifier.get_llm_client") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.generate = AsyncMock(return_value=_make_llm_response(llm_json))
            mock_get_llm.return_value = mock_llm

            out = await run_image_classifier(_make_input({
                "image_base64": _TINY_PNG_BASE64,
                "categories": CATEGORIES,
            }))

        assert "primary_category" in out.result
        assert "confidence_score" in out.result
        assert "all_scores" in out.result
        assert "description" in out.result
        assert "labels" in out.result
        assert isinstance(out.result["labels"], list)
        assert isinstance(out.result["all_scores"], dict)
        assert out.duration_ms >= 0
