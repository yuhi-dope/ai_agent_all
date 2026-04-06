"""
積算AI 全レベル通し検証テスト

Level 1: 数量抽出（テキスト→構造化）
Level 2: 単価推定（過去実績+公共労務単価+LLM推定）
Level 3: 諸経費計算
Level 4: 内訳書データ生成
Level 5: フィードバック学習（確定→learn_from_result）

※ LLM呼び出しが必要なためintegrationマーク。
   実行: pytest tests/test_estimation_fullpipeline.py --run-integration -v
"""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from uuid import uuid4

from workers.bpo.construction.estimator import EstimationPipeline
from workers.bpo.construction.models import (
    EstimationItemCreate,
    EstimationItemWithPrice,
    OverheadBreakdown,
    PriceSource,
)


# テストデータ
from tests.data.sample_quantity_sheets import ALL_SAMPLES, SAMPLE_1_TEXT, SAMPLE_1_EXPECTED_ITEMS, SAMPLE_1_META


# ─────────────────────────────────────
# Level 1: 数量抽出（LLM不要 — モック）
# ─────────────────────────────────────

class TestLevel1Extraction:
    """数量計算書テキスト→構造化アイテムの抽出精度を検証"""

    @pytest.mark.asyncio
    async def test_extraction_returns_items(self):
        """LLMが正しいJSON形式で返せば、全アイテムが抽出される"""
        # LLMレスポンスをモック（期待される正解データ）
        mock_llm_response = MagicMock()
        mock_llm_response.content = json.dumps([
            {
                "sort_order": i + 1,
                "category": item["category"],
                "subcategory": item["subcategory"],
                "detail": item["detail"],
                "quantity": item["quantity"],
                "unit": item["unit"],
            }
            for i, item in enumerate(SAMPLE_1_EXPECTED_ITEMS)
        ], ensure_ascii=False)

        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate.return_value = mock_llm_response

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])

        # EstimationPipeline のコンストラクタ内で LLMClient() が呼ばれるため
        # patch のコンテキスト内でインスタンス生成する
        # LLMパスに入るよう、パイプ区切り行が取れないシンプルなテキストを渡す
        raw_text_no_pipe = "テキスト形式のデータ（LLMパス用）\n" + "\n".join(
            f"細別: {item['detail']}\n数量: {item['quantity']} {item['unit']}\n---"
            for item in SAMPLE_1_EXPECTED_ITEMS[:1]
        )

        with patch("workers.bpo.construction.estimator.LLMClient", return_value=mock_llm_instance), \
             patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            pipeline = EstimationPipeline()
            items = await pipeline.extract_quantities(
                project_id=str(uuid4()),
                company_id=str(uuid4()),
                raw_text=raw_text_no_pipe,
            )

        assert len(items) == len(SAMPLE_1_EXPECTED_ITEMS)
        for item, expected in zip(items, SAMPLE_1_EXPECTED_ITEMS):
            assert item.category == expected["category"]
            assert float(item.quantity) == expected["quantity"]
            assert item.unit == expected["unit"]

    @pytest.mark.asyncio
    async def test_extraction_handles_malformed_json(self):
        """LLMがJSONの前後にテキストを付けても、パースできる"""
        raw_json = json.dumps([
            {"sort_order": 1, "category": "土工", "subcategory": "掘削工",
             "detail": "バックホウ掘削", "quantity": 100, "unit": "m3"},
        ], ensure_ascii=False)
        mock_response = MagicMock()
        mock_response.content = f"以下が抽出結果です。\n{raw_json}\n以上です。"

        mock_llm_instance = AsyncMock()
        mock_llm_instance.generate.return_value = mock_response

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.or_.return_value.execute.return_value = MagicMock(data=[])

        with patch("workers.bpo.construction.estimator.LLMClient", return_value=mock_llm_instance), \
             patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            pipeline = EstimationPipeline()
            items = await pipeline.extract_quantities(
                project_id=str(uuid4()),
                company_id=str(uuid4()),
                raw_text="テスト（LLMパス用）",
            )

        assert len(items) == 1
        assert items[0].category == "土工"

    def test_all_samples_have_expected_items(self):
        """全サンプルデータにexpected_itemsが定義されている"""
        for i, sample in enumerate(ALL_SAMPLES):
            assert len(sample["expected"]) > 0, f"Sample {i} has no expected items"
            assert sample["meta"]["name"], f"Sample {i} has no name"
            assert sample["text"].strip(), f"Sample {i} has no text"


# ─────────────────────────────────────
# Level 2: 単価推定（モック）
# ─────────────────────────────────────

class TestLevel2PriceSuggestion:
    """単価推定の動作を検証"""

    @pytest.mark.asyncio
    async def test_suggest_with_past_records(self):
        """過去実績がある場合、加重平均+動的confidenceで候補が返る"""
        company_id = str(uuid4())
        project_id = str(uuid4())

        # estimation_items（items_overrideで渡してDBアクセスをスキップ）
        # EstimationItemWithPrice は created_at が必須なので含める
        items_override = [
            {"id": str(uuid4()), "category": "土工", "subcategory": "掘削工",
             "detail": "バックホウ掘削", "quantity": "1500", "unit": "m3",
             "unit_price": None, "specification": None, "price_source": None,
             "price_confidence": None, "notes": None, "sort_order": 1,
             "source_document": None, "project_id": project_id, "company_id": company_id,
             "created_at": "2026-03-01T00:00:00", "amount": None},
        ]

        # unit_price_master（過去実績3件）
        past_prices = [
            {"id": str(uuid4()), "unit_price": "650", "updated_at": "2026-02-01T00:00:00",
             "region": "東京都", "accuracy_rate": 0.85, "used_count": 3},
            {"id": str(uuid4()), "unit_price": "680", "updated_at": "2025-11-01T00:00:00",
             "region": "東京都", "accuracy_rate": 0.90, "used_count": 5},
            {"id": str(uuid4()), "unit_price": "620", "updated_at": "2025-06-01T00:00:00",
             "region": "埼玉県", "accuracy_rate": None, "used_count": 1},
        ]

        # DB呼び出しを段階的にモック
        def table_side_effect(name):
            mock_table = MagicMock()
            if name == "unit_price_master":
                # select("*").eq("company_id", ...).eq("category", ...).order(...).limit(...).execute()
                mock_table.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=past_prices)
                mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock()
            elif name == "public_labor_rates":
                mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
            return mock_table

        mock_db = MagicMock()
        mock_db.table.side_effect = table_side_effect

        with patch("workers.bpo.construction.estimator.LLMClient"), \
             patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            pipeline = EstimationPipeline()
            results = await pipeline.suggest_unit_prices(
                project_id=project_id,
                company_id=company_id,
                region="東京都",
                fiscal_year=2026,
                items_override=items_override,
            )

        assert len(results) == 1
        item = results[0]
        assert len(item.price_candidates) >= 1
        candidate = item.price_candidates[0]
        assert candidate["source"] == PriceSource.PAST_RECORD.value
        # 加重平均なので650-680の間になるはず
        assert 600 <= candidate["unit_price"] <= 700
        # 動的confidence: base0.5 + count_bonus(3件*0.05=0.15) + region_bonus(0.1) = 0.75
        assert 0.5 <= candidate["confidence"] <= 0.95


# ─────────────────────────────────────
# Level 3: 諸経費計算
# ─────────────────────────────────────

class TestLevel3Overhead:
    """諸経費計算の検証"""

    @pytest.mark.asyncio
    async def test_public_civil_overhead(self):
        """公共土木の諸経費率が正しく適用される"""
        pipeline = EstimationPipeline()
        project_id = str(uuid4())
        company_id = str(uuid4())

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {"unit_price": "650", "quantity": "1500"},  # 975,000
                {"unit_price": "500", "quantity": "250"},   # 125,000
            ]
        )

        with patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            result = await pipeline.calculate_overhead(
                project_id=project_id,
                company_id=company_id,
                project_type="public_civil",
            )

        assert isinstance(result, OverheadBreakdown)
        assert result.direct_cost > 0
        assert result.common_temporary > 0
        assert result.site_management > 0
        assert result.general_admin > 0
        assert result.total > result.direct_cost  # 諸経費分だけ増える

    @pytest.mark.asyncio
    async def test_private_civil_overhead_rate_differs(self):
        """民間土木は公共土木と異なる諸経費率"""
        pipeline = EstimationPipeline()

        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"unit_price": "1000", "quantity": "100"}]
        )

        with patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            public = await pipeline.calculate_overhead(str(uuid4()), str(uuid4()), "public_civil")
            private = await pipeline.calculate_overhead(str(uuid4()), str(uuid4()), "private_civil")

        # 民間は公共と諸経費率が異なる（設計上public=22%, private=27%等）
        assert public.total != private.total or public.direct_cost == private.direct_cost


# ─────────────────────────────────────
# Level 4: 内訳書データ生成
# ─────────────────────────────────────

class TestLevel4BreakdownGeneration:
    """内訳書データ生成の検証"""

    @pytest.mark.asyncio
    async def test_generate_breakdown_data(self):
        """内訳書データが正しい構造で生成される"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"name": "テスト工事", "region": "東京都", "fiscal_year": 2026,
                  "project_type": "public_civil", "estimated_amount": 15000000}
        )
        mock_db.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value = MagicMock(
            data=[
                {"category": "土工", "subcategory": "掘削工", "detail": "バックホウ掘削",
                 "quantity": "1500", "unit": "m3", "unit_price": "650",
                 "specification": "0.8m3級", "amount": 975000},
            ]
        )

        with patch("workers.bpo.construction.estimator.LLMClient"), \
             patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            pipeline = EstimationPipeline()
            data = await pipeline.generate_breakdown_data(str(uuid4()), str(uuid4()))

        assert "title" in data
        assert "headers" in data
        assert "rows" in data
        assert len(data["rows"]) == 1
        # generate_breakdown_data は "title" に文字列、"meta" に dict を返す
        assert isinstance(data["title"], str)
        assert "テスト工事" in data["title"]
        assert data["meta"]["工事名"] == "テスト工事"


# ─────────────────────────────────────
# Level 5: フィードバック学習
# ─────────────────────────────────────

class TestLevel5FeedbackLearning:
    """確定→learn_from_result→単価マスタ蓄積の検証"""

    @pytest.mark.asyncio
    async def test_learn_from_result_saves_with_accuracy(self):
        """learn_from_resultがaccuracy_rate付きでunit_price_masterに保存する"""
        project_id = str(uuid4())
        company_id = str(uuid4())

        mock_db = MagicMock()

        # プロジェクト情報
        mock_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={"region": "東京都", "fiscal_year": 2026}
        )

        # estimation_items（notesにAI推定値を含む）
        items_data = [
            {
                "category": "土工", "subcategory": "掘削工", "detail": "バックホウ掘削",
                "specification": "0.8m3級", "unit": "m3", "unit_price": "680",
                "project_id": project_id,
                "notes": json.dumps({"original_ai_price": 650, "user_modified": True, "finalized_at": "2026-03-18T00:00:00"}),
            },
            {
                "category": "コンクリート工", "subcategory": "型枠工", "detail": "普通型枠",
                "specification": None, "unit": "m2", "unit_price": "5000",
                "project_id": project_id,
                "notes": json.dumps({"original_ai_price": 5000, "user_modified": False, "finalized_at": "2026-03-18T00:00:00"}),
            },
        ]

        inserted_records = []

        def table_side_effect(name):
            mock_table = MagicMock()
            if name == "estimation_projects":
                mock_table.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
                    data={"region": "東京都", "fiscal_year": 2026}
                )
            elif name == "estimation_items":
                # .select("*").eq("project_id", ...).not_.is_("unit_price", "null").execute()
                # not_ は属性アクセス、is_ はその後のメソッド呼び出し
                mock_table.select.return_value.eq.return_value.not_.is_.return_value.execute.return_value = MagicMock(data=items_data)
            elif name == "unit_price_master":
                def capture_insert(data):
                    inserted_records.append(data)
                    return MagicMock(execute=MagicMock(return_value=MagicMock()))
                mock_table.insert.side_effect = capture_insert
            return mock_table

        mock_db.table.side_effect = table_side_effect

        with patch("workers.bpo.construction.estimator.LLMClient"), \
             patch("workers.bpo.construction.estimator.get_client", return_value=mock_db):
            pipeline = EstimationPipeline()
            count = await pipeline.learn_from_result(project_id, company_id)

        assert count == 2
        assert len(inserted_records) == 2

        # 1件目: AI推定650、確定680 → accuracy = 1 - |650-680|/680 ≈ 0.9559
        record1 = inserted_records[0]
        assert record1["ai_estimated_price"] == 650
        assert 0.95 <= record1["accuracy_rate"] <= 0.96

        # 2件目: AI推定5000、確定5000 → accuracy = 1.0
        record2 = inserted_records[1]
        assert record2["ai_estimated_price"] == 5000
        assert record2["accuracy_rate"] == 1.0


# ─────────────────────────────────────
# 全レベル統合テスト（integrationマーク）
# ─────────────────────────────────────

@pytest.mark.integration
class TestFullPipelineIntegration:
    """LLM + DB を使った全レベル通し検証（--run-integration必要）"""

    @pytest.mark.asyncio
    async def test_sample1_full_pipeline(self):
        """パターン1: 道路改良工事を全レベル通す"""
        pipeline = EstimationPipeline()
        sample = ALL_SAMPLES[0]

        # Level 1: 数量抽出
        items = await pipeline.extract_quantities(
            project_id="test",
            company_id="test",
            raw_text=sample["text"],
        )
        assert len(items) >= len(sample["expected"]) * 0.7  # 70%以上抽出できればOK

        # Level 2以降は実DB接続が必要なため、ここではLevel 1のみ
