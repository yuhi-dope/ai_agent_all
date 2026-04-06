"""llm_summarizer マイクロエージェント テスト。"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.llm_summarizer import run_llm_summarizer

COMPANY_ID = "test-company-001"


def _make_input(payload: dict) -> MicroAgentInput:
    return MicroAgentInput(
        company_id=COMPANY_ID,
        agent_name="llm_summarizer",
        payload=payload,
    )


def _make_mock_llm(content: str, cost_yen: float = 0.05) -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.cost_yen = cost_yen
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(return_value=mock_response)
    return mock_llm


# ─── 短文テスト（LLM不使用） ────────────────────────────────────────────────

class TestShortText:
    @pytest.mark.asyncio
    async def test_short_text_returns_as_is(self):
        """500文字以下のテキストはLLMを呼ばずそのまま返す。"""
        short_text = "この契約書は3ページから構成されます。"
        with patch("workers.micro.llm_summarizer.get_llm_client") as mock_get:
            out = await run_llm_summarizer(_make_input({
                "text": short_text,
                "style": "bullet",
            }))

        assert out.success is True
        assert out.result["summary"] == short_text
        assert out.result["original_length"] == len(short_text)
        assert out.result["summary_length"] == len(short_text)
        assert out.result["compression_ratio"] == 1.0
        assert out.cost_yen == 0.0
        assert out.confidence == 1.0
        # LLMが呼ばれていないことを確認
        mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_exactly_500_chars_no_llm(self):
        """ちょうど500文字もLLM不使用。"""
        text_500 = "あ" * 500
        with patch("workers.micro.llm_summarizer.get_llm_client") as mock_get:
            out = await run_llm_summarizer(_make_input({"text": text_500}))

        assert out.success is True
        assert out.cost_yen == 0.0
        mock_get.assert_not_called()


# ─── bullet style要約テスト ──────────────────────────────────────────────────

class TestBulletStyle:
    @pytest.mark.asyncio
    async def test_bullet_summary_happy_path(self):
        """bullet styleで正常に要約が返る。"""
        long_text = "建設業法第19条に基づき、" * 60  # 600文字超
        bullet_response = "・工期は2024年4月から2025年3月まで\n・請負金額は1億円\n・主任技術者を配置すること"

        mock_llm = _make_mock_llm(bullet_response, cost_yen=0.03)
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            out = await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "bullet",
                "max_length": 300,
            }))

        assert out.success is True
        assert out.agent_name == "llm_summarizer"
        assert out.result["summary"] == bullet_response
        assert out.result["original_length"] == len(long_text)
        assert out.result["style"] == "bullet"
        assert out.result["compression_ratio"] < 1.0
        assert out.confidence == 0.8
        assert out.cost_yen == 0.03
        mock_llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_bullet_prompt_contains_correct_instruction(self):
        """bullet styleのプロンプトに箇条書き指示が含まれる。"""
        long_text = "契約内容の詳細。" * 100

        mock_llm = _make_mock_llm("・要点1\n・要点2")
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "bullet",
            }))

        call_args = mock_llm.generate.call_args[0][0]
        user_msg = next(m["content"] for m in call_args.messages if m["role"] == "user")
        assert "箇条書き" in user_msg
        assert "最大10項目" in user_msg

    @pytest.mark.asyncio
    async def test_paragraph_style_prompt(self):
        """paragraph styleのプロンプトにmax_length指示が含まれる。"""
        long_text = "議事録の内容。" * 100

        mock_llm = _make_mock_llm("本日の議事録要約。")
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "paragraph",
                "max_length": 400,
            }))

        call_args = mock_llm.generate.call_args[0][0]
        user_msg = next(m["content"] for m in call_args.messages if m["role"] == "user")
        assert "400文字以内" in user_msg


# ─── structured style要約テスト ─────────────────────────────────────────────

class TestStructuredStyle:
    @pytest.mark.asyncio
    async def test_structured_parse_success(self):
        """structured styleでJSONが正しくパースされる。"""
        long_text = "安全書類の詳細内容。" * 100
        structured_json = json.dumps({
            "title": "安全書類レビュー結果",
            "summary": "本書類は建設業法に基づく安全管理計画を記述しています。",
            "key_points": ["主任技術者配置済み", "安全教育実施済み"],
            "risks": ["高所作業リスクあり"],
            "action_items": ["安全帯着用を徹底する"],
        }, ensure_ascii=False)

        mock_llm = _make_mock_llm(structured_json)
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            out = await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "structured",
            }))

        assert out.success is True
        assert out.result["style"] == "structured"
        assert "安全書類レビュー結果" in out.result["summary"]
        assert "本書類は建設業法" in out.result["summary"]
        assert out.result["key_points"] == ["主任技術者配置済み", "安全教育実施済み"]
        assert out.result["risks"] == ["高所作業リスクあり"]
        assert out.result["action_items"] == ["安全帯着用を徹底する"]

    @pytest.mark.asyncio
    async def test_structured_parse_with_code_fence(self):
        """コードフェンス付きJSONも正しくパースされる。"""
        long_text = "契約書の詳細内容。" * 100
        fenced_json = (
            "```json\n"
            + json.dumps({
                "title": "契約書レビュー",
                "summary": "標準的な請負契約書です。",
                "key_points": ["工期明記"],
                "risks": [],
                "action_items": [],
            }, ensure_ascii=False)
            + "\n```"
        )

        mock_llm = _make_mock_llm(fenced_json)
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            out = await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "structured",
            }))

        assert out.success is True
        assert out.result["style"] == "structured"
        assert out.result["key_points"] == ["工期明記"]

    @pytest.mark.asyncio
    async def test_structured_parse_failure_falls_back_to_bullet(self):
        """structured styleでJSONパース失敗時はbulletにフォールバックする。"""
        long_text = "帳票の内容。" * 100
        invalid_json = "これはJSONではありません。要点をまとめると・工期・金額・技術者。"

        mock_llm = _make_mock_llm(invalid_json)
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            out = await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "structured",
            }))

        assert out.success is True
        # フォールバック後はbullet
        assert out.result["style"] == "bullet"
        assert out.result["summary"] == invalid_json
        # key_points等は空リスト
        assert out.result["key_points"] == []
        assert out.result["risks"] == []
        assert out.result["action_items"] == []


# ─── focus指定テスト ─────────────────────────────────────────────────────────

class TestFocusOption:
    @pytest.mark.asyncio
    async def test_focus_added_to_prompt(self):
        """focus指定時、プロンプトに「特に〜に注目して」が含まれる。"""
        long_text = "労働契約書の全条項。" * 100

        mock_llm = _make_mock_llm("・残業上限36時間\n・割増賃金25%")
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "bullet",
                "focus": "36協定違反リスク",
            }))

        call_args = mock_llm.generate.call_args[0][0]
        user_msg = next(m["content"] for m in call_args.messages if m["role"] == "user")
        assert "36協定違反リスク" in user_msg
        assert "注目" in user_msg

    @pytest.mark.asyncio
    async def test_no_focus_no_focus_clause(self):
        """focus未指定時、プロンプトに「注目」という語句が含まれない。"""
        long_text = "一般的な契約書。" * 100

        mock_llm = _make_mock_llm("要約テキスト")
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "bullet",
            }))

        call_args = mock_llm.generate.call_args[0][0]
        user_msg = next(m["content"] for m in call_args.messages if m["role"] == "user")
        assert "注目" not in user_msg


# ─── 空テキストエラーテスト ──────────────────────────────────────────────────

class TestEmptyText:
    @pytest.mark.asyncio
    async def test_empty_string_returns_failure(self):
        """空文字列はsuccess=Falseを返す。"""
        out = await run_llm_summarizer(_make_input({"text": ""}))

        assert out.success is False
        assert "空" in out.result["error"]
        assert out.cost_yen == 0.0
        assert out.confidence == 0.0

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_failure(self):
        """空白のみのテキストもsuccess=Falseを返す。"""
        out = await run_llm_summarizer(_make_input({"text": "   \n\t  "}))

        assert out.success is False
        assert out.cost_yen == 0.0

    @pytest.mark.asyncio
    async def test_missing_text_key_returns_failure(self):
        """textキー自体が存在しない場合もsuccess=Falseを返す。"""
        out = await run_llm_summarizer(_make_input({"style": "bullet"}))

        assert out.success is False


# ─── LLMタスク設定テスト ─────────────────────────────────────────────────────

class TestLLMTaskConfig:
    @pytest.mark.asyncio
    async def test_uses_fast_tier(self):
        """LLM呼び出しにFASTティアが使用される。"""
        from llm.client import ModelTier

        long_text = "詳細な議事録内容。" * 100
        mock_llm = _make_mock_llm("要約")
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            await run_llm_summarizer(_make_input({"text": long_text}))

        call_args = mock_llm.generate.call_args[0][0]
        assert call_args.tier == ModelTier.FAST
        assert call_args.company_id == COMPANY_ID
        assert call_args.task_type == "llm_summarizer"

    @pytest.mark.asyncio
    async def test_compression_ratio_calculated_correctly(self):
        """compression_ratioが正しく計算される。"""
        long_text = "あ" * 1000
        summary_text = "い" * 100  # 圧縮率 0.1

        mock_llm = _make_mock_llm(summary_text)
        with patch("workers.micro.llm_summarizer.get_llm_client", return_value=mock_llm):
            out = await run_llm_summarizer(_make_input({
                "text": long_text,
                "style": "paragraph",
            }))

        assert out.result["original_length"] == 1000
        assert out.result["summary_length"] == 100
        assert out.result["compression_ratio"] == pytest.approx(0.1, abs=0.001)
