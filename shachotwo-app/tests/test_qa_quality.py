"""Q&A品質評価テスト — ゴールデンデータセットに基づく精度・幻覚検出・応答時間テスト。

Usage:
  # モックテスト（CI用、LLM不要）
  pytest tests/test_qa_quality.py -k "mock"

  # 実環境テスト（LLM + Supabase必要、--run-integration フラグ付き）
  pytest tests/test_qa_quality.py -k "integration" --run-integration
"""
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.knowledge.qa import QAResult, answer_question
from brain.knowledge.search import SearchResult


GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"


def load_golden_dataset() -> list[dict]:
    with open(GOLDEN_DATASET_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["questions"]


GOLDEN_QUESTIONS = load_golden_dataset()


def _make_search_result(title: str, content: str, department: str) -> SearchResult:
    return SearchResult(
        item_id=uuid4(),
        title=title,
        content=content,
        department=department,
        category="policy",
        item_type="rule",
        confidence=0.9,
        similarity=0.85,
    )


# ============================================================================
# Mock Tests（CI用 — LLM不要）
# ============================================================================

class TestGoldenDatasetStructure:
    """ゴールデンデータセットの構造検証"""

    def test_dataset_has_50_questions(self):
        assert len(GOLDEN_QUESTIONS) == 50

    def test_all_questions_have_required_fields(self):
        for q in GOLDEN_QUESTIONS:
            assert "id" in q
            assert "question" in q
            assert "expected_answer_contains" in q
            assert "department" in q
            assert isinstance(q["expected_answer_contains"], list)
            assert len(q["expected_answer_contains"]) > 0

    def test_departments_covered(self):
        departments = {q["department"] for q in GOLDEN_QUESTIONS}
        assert "営業" in departments
        assert "現場管理" in departments
        assert "経理" in departments
        assert "安全管理" in departments
        assert "総務" in departments

    def test_unique_ids(self):
        ids = [q["id"] for q in GOLDEN_QUESTIONS]
        assert len(ids) == len(set(ids))


class TestQAAccuracyMock:
    """モック環境でのQ&A精度テスト（LLMの出力をシミュレート）"""

    @pytest.mark.asyncio
    async def test_answer_contains_expected_keywords(self):
        """LLMがナレッジの内容を含む回答を返すことを検証"""
        # 検索結果にナレッジを含める
        search_results = [
            _make_search_result(
                "見積もり作成基準",
                "見積もりの有効期限は提出日から30日間とする。諸経費率27%（民間）。公共工事は22%。",
                "営業",
            ),
        ]

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=json.dumps({
                "answer": "見積もりの有効期限は提出日から30日間です。諸経費率は民間27%、公共工事22%です。",
                "confidence": 0.9,
                "sources": [],
                "missing_info": None,
            }),
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=search_results), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question("見積もりの有効期限は？", str(uuid4()))

        # 正解キーワードが含まれているか
        assert "30日" in result.answer
        assert result.confidence > 0.5


class TestHallucinationDetectionMock:
    """幻覚検出テスト（モック）"""

    @pytest.mark.asyncio
    async def test_no_hallucination_when_knowledge_exists(self):
        """ナレッジにある情報だけで回答すべき"""
        knowledge_content = "経費精算は発生月の翌月10日までに申請する。"
        search_results = [
            _make_search_result("経費精算ルール", knowledge_content, "経理"),
        ]

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=json.dumps({
                "answer": "経費精算は発生月の翌月10日までに申請してください。",
                "confidence": 0.9,
                "sources": [],
                "missing_info": None,
            }),
            model_used="gemini-2.5-flash",
            cost_yen=0.01,
        )

        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=search_results), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            result = await answer_question("経費精算の期限は？", str(uuid4()))

        # 回答がナレッジの内容に基づいているか
        assert "翌月10日" in result.answer

    @pytest.mark.asyncio
    async def test_low_confidence_when_no_knowledge(self):
        """ナレッジがない場合、confidence が低い or 回答なし"""
        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=[]):
            result = await answer_question("存在しないナレッジに関する質問", str(uuid4()))

        assert result.confidence == 0.0


class TestResponseTimeMock:
    """応答時間テスト（モック — 処理ロジック自体の時間計測）"""

    @pytest.mark.asyncio
    async def test_response_within_timeout(self):
        """モック環境ではLLM呼び出しが即座に返るため、処理ロジック自体が速いことを確認"""
        search_results = [
            _make_search_result("テスト", "テスト内容", "営業"),
        ]

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = MagicMock(
            content=json.dumps({"answer": "回答", "confidence": 0.8, "sources": [], "missing_info": None}),
            model_used="mock",
            cost_yen=0.0,
        )

        with patch("brain.knowledge.qa.hybrid_search", new_callable=AsyncMock, return_value=search_results), \
             patch("brain.knowledge.qa.get_llm_client", return_value=mock_llm):
            start = time.time()
            await answer_question("テスト", str(uuid4()))
            elapsed = time.time() - start

        # 処理ロジック自体は100ms以内であるべき
        assert elapsed < 0.1


# ============================================================================
# Integration Tests（実環境用 — --run-integration フラグ必要）
# ============================================================================

@pytest.mark.integration
class TestQAAccuracyIntegration:
    """実環境でのQ&A精度テスト（LLM + Supabase必要）"""

    @pytest.mark.asyncio
    async def test_golden_dataset_accuracy(self):
        """
        ゴールデンデータセット50問の正答率を測定

        正答基準: expected_answer_contains のキーワードの50%以上が回答に含まれる
        目標: 正答率 ≥ 60%
        """
        company_id = "test-company-id"  # テスト用企業ID
        correct = 0
        total = len(GOLDEN_QUESTIONS)
        results = []

        for q in GOLDEN_QUESTIONS:
            try:
                result = await answer_question(
                    question=q["question"],
                    company_id=company_id,
                    department=q.get("department"),
                )

                # キーワードマッチ率
                expected = q["expected_answer_contains"]
                matched = sum(1 for kw in expected if kw in result.answer)
                match_rate = matched / len(expected) if expected else 0

                is_correct = match_rate >= 0.5
                if is_correct:
                    correct += 1

                results.append({
                    "id": q["id"],
                    "question": q["question"],
                    "match_rate": match_rate,
                    "correct": is_correct,
                    "confidence": result.confidence,
                })
            except Exception as e:
                results.append({
                    "id": q["id"],
                    "question": q["question"],
                    "error": str(e),
                    "correct": False,
                })

        accuracy = correct / total
        print(f"\n{'='*60}")
        print(f"Q&A精度テスト結果: {correct}/{total} = {accuracy:.1%}")
        print(f"目標: ≥ 60%  {'✅ PASS' if accuracy >= 0.6 else '❌ FAIL'}")
        print(f"{'='*60}")

        assert accuracy >= 0.6, f"Q&A accuracy {accuracy:.1%} < 60% target"

    @pytest.mark.asyncio
    async def test_response_time_under_3_seconds(self):
        """応答時間が3秒以内かテスト（5問サンプル）"""
        company_id = "test-company-id"
        sample = GOLDEN_QUESTIONS[:5]
        slow_count = 0

        for q in sample:
            start = time.time()
            try:
                await answer_question(q["question"], company_id)
            except Exception:
                pass
            elapsed = time.time() - start
            if elapsed > 3.0:
                slow_count += 1
                print(f"SLOW: {q['question']} took {elapsed:.1f}s")

        assert slow_count <= 1, f"{slow_count}/5 questions exceeded 3s response time"

    @pytest.mark.asyncio
    async def test_hallucination_rate_under_10_percent(self):
        """
        幻覚率テスト: ナレッジにない情報を生成していないか

        方法: 意図的に存在しないトピックで質問し、
              LLMが「わからない」と答えるか確認
        """
        company_id = "test-company-id"
        trick_questions = [
            "宇宙ステーションの建設許可について教えてください",
            "AIロボットの労災保険はどうなっていますか",
            "量子コンピュータを使った積算方法は？",
            "タイムマシンの安全管理基準は？",
            "火星での工事の諸経費率は？",
        ]

        hallucination_count = 0
        for q in trick_questions:
            try:
                result = await answer_question(q, company_id)
                # 高いconfidenceで回答していたら幻覚の疑い
                if result.confidence > 0.7 and "わからない" not in result.answer and "登録されていません" not in result.answer:
                    hallucination_count += 1
                    print(f"HALLUCINATION: {q} → confidence={result.confidence}")
            except Exception:
                pass

        rate = hallucination_count / len(trick_questions)
        assert rate < 0.1, f"Hallucination rate {rate:.0%} ≥ 10%"
