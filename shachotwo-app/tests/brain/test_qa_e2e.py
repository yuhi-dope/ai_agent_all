"""
Knowledge Q&A E2Eテスト

answer_question() の動作をモックで検証する。
- hybrid_search と get_llm_client はモックする（DB・LLMアクセスなし）
- answer_question() 本体のロジック（コンテキスト構築・レスポンス解析）は実際に動かす

シナリオ:
1. 検索結果あり → LLMが回答生成 → QAResultが返る
2. 検索結果なし → 「ナレッジが登録されていません」メッセージ
3. LLM失敗時 → エラーが伝播する
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from brain.knowledge.qa import answer_question, QAResult
from brain.knowledge.search import SearchResult


# ─── テストデータ ───

COMPANY_ID = str(uuid4())

SAMPLE_SEARCH_RESULTS = [
    SearchResult(
        item_id=uuid4(),
        title="有給休暇の申請手順",
        content="有給休暇は2週間前までに上長に申請し、人事システムに登録してください。",
        department="総務部",
        category="rule",
        item_type="text",
        confidence=0.9,
        similarity=0.85,
    ),
    SearchResult(
        item_id=uuid4(),
        title="残業申請ルール",
        content="残業が発生する場合は事前に上長の承認が必要です。月45時間が上限となります。",
        department="総務部",
        category="rule",
        item_type="text",
        confidence=0.8,
        similarity=0.72,
    ),
]

SAMPLE_LLM_JSON_RESPONSE = json.dumps({
    "answer": "有給休暇は2週間前までに上長に申請し、人事システムに登録してください。",
    "confidence": 0.9,
    "missing_info": None,
}, ensure_ascii=False)


# ─── シナリオ1: 検索結果あり → LLMが回答生成 ───

class TestAnswerQuestionWithResults:
    """検索結果がある場合のQ&A動作を検証"""

    async def test_returns_qa_result_with_answer(self):
        """検索結果ありのとき、LLM回答を含む QAResult が返る"""
        mock_llm_response = MagicMock()
        mock_llm_response.content = SAMPLE_LLM_JSON_RESPONSE
        mock_llm_response.model_used = "gemini-2.5-flash"
        mock_llm_response.cost_yen = 0.005

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = mock_llm_response

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question(
                question="有給休暇の申請方法を教えてください",
                company_id=COMPANY_ID,
            )

        assert isinstance(result, QAResult)
        assert result.answer == "有給休暇は2週間前までに上長に申請し、人事システムに登録してください。"
        assert result.model_used == "gemini-2.5-flash"
        assert result.cost_yen == 0.005
        assert len(result.sources) == 2

    async def test_sources_contain_search_result_titles(self):
        """sourcesに検索結果のタイトルが含まれる"""
        mock_llm_response = MagicMock()
        mock_llm_response.content = SAMPLE_LLM_JSON_RESPONSE
        mock_llm_response.model_used = "gemini-2.5-flash"
        mock_llm_response.cost_yen = 0.003

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = mock_llm_response

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question(
                question="残業申請について教えてください",
                company_id=COMPANY_ID,
                department="総務部",
            )

        source_titles = [s.title for s in result.sources]
        assert "有給休暇の申請手順" in source_titles
        assert "残業申請ルール" in source_titles

    async def test_confidence_is_calculated_from_search_results(self):
        """confidenceはベクトル検索スコアから計算される（LLM自己評価も加味）"""
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps({
            "answer": "テスト回答",
            "confidence": 0.95,
            "missing_info": None,
        }, ensure_ascii=False)
        mock_llm_response.model_used = "gemini-2.5-flash"
        mock_llm_response.cost_yen = 0.002

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = mock_llm_response

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question(
                question="テスト質問",
                company_id=COMPANY_ID,
            )

        # confidenceは0〜1の範囲内
        assert 0.0 <= result.confidence <= 1.0

    async def test_plain_text_llm_response_fallback(self):
        """LLMがJSONでなくプレーンテキストを返した場合もフォールバックで処理できる"""
        mock_llm_response = MagicMock()
        mock_llm_response.content = "有給休暇は2週間前までに申請してください。"
        mock_llm_response.model_used = "gemini-2.5-flash"
        mock_llm_response.cost_yen = 0.001

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = mock_llm_response

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question(
                question="有給の申請方法は？",
                company_id=COMPANY_ID,
            )

        assert isinstance(result, QAResult)
        # プレーンテキストフォールバック: LLMの応答がそのまま answer になる
        assert "有給休暇は2週間前" in result.answer


# ─── シナリオ2: 検索結果なし ───

class TestAnswerQuestionWithoutResults:
    """ナレッジが未登録の場合の動作を検証"""

    async def test_returns_no_knowledge_message(self):
        """検索結果なしのとき、ナレッジ未登録メッセージが返る"""
        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=[])), \
             patch("brain.knowledge.qa.get_llm_client") as mock_get_llm:
            result = await answer_question(
                question="存在しないトピックについて教えてください",
                company_id=COMPANY_ID,
            )
            # LLMは呼ばれない
            mock_get_llm.assert_not_called()

        assert isinstance(result, QAResult)
        assert "ナレッジが登録されていません" in result.answer
        assert result.confidence == 0.0
        assert result.sources == []
        assert result.model_used == "none"
        assert result.cost_yen == 0.0

    async def test_missing_info_is_set_when_no_results(self):
        """検索結果なしのとき、missing_info が設定される"""
        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=[])), \
             patch("brain.knowledge.qa.get_llm_client"):
            result = await answer_question(
                question="未登録の質問",
                company_id=COMPANY_ID,
            )

        assert result.missing_info is not None
        assert len(result.missing_info) > 0


# ─── シナリオ3: LLM失敗時 ───

class TestAnswerQuestionLLMFailure:
    """LLMが例外を投げた場合のエラー伝播を検証"""

    async def test_llm_error_propagates(self):
        """LLM呼び出しが失敗した場合、例外が伝播する"""
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = RuntimeError("LLM API呼び出しに失敗しました")

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            with pytest.raises(RuntimeError, match="LLM API呼び出しに失敗しました"):
                await answer_question(
                    question="テスト質問",
                    company_id=COMPANY_ID,
                )

    async def test_llm_generate_is_called_with_correct_task_type(self):
        """LLM呼び出しに task_type='qa' が設定されている"""
        from llm.client import LLMTask

        mock_llm_response = MagicMock()
        mock_llm_response.content = SAMPLE_LLM_JSON_RESPONSE
        mock_llm_response.model_used = "gemini-2.5-flash"
        mock_llm_response.cost_yen = 0.004

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = mock_llm_response

        with patch("brain.knowledge.qa.enhanced_search", new=AsyncMock(return_value=SAMPLE_SEARCH_RESULTS)), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            await answer_question(
                question="テスト質問",
                company_id=COMPANY_ID,
            )

        mock_llm.generate.assert_called_once()
        call_args = mock_llm.generate.call_args[0][0]
        assert isinstance(call_args, LLMTask)
        assert call_args.task_type == "qa"
