"""BPO Manager — ConditionEvaluator 動的DB参照テスト。"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.manager.condition_evaluator import (
    BUILTIN_CONDITION_CHAINS,
    BUILTIN_SALES_CONDITION_CHAINS,
    _EVALUATOR_REGISTRY,
    _OPERATOR_MAP,
    _evaluate_dynamic_condition,
    _evaluate_builtin_condition_chains,
    _evaluate_builtin_sales_chains,
    _eval_health_score_low,
    _eval_sla_breach,
    _eval_upsell_high,
    _eval_lost_reengagement,
    evaluate_knowledge_triggers,
)
from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel

COMPANY_ID = "test-company-001"


def _make_db_mock(table_data: dict[str, list]) -> MagicMock:
    """テーブル名 → レコードリスト のマッピングからDBモックを生成する。"""
    db = MagicMock()

    def _table_side_effect(table_name: str):
        mock_table = MagicMock()
        rows = table_data.get(table_name, [])

        # チェーンメソッドはすべて自身を返す
        for method in ("select", "eq", "lte", "gte", "lt", "gt", "not_", "in_", "limit"):
            getattr(mock_table, method).return_value = mock_table

        # not_.in_ の特殊チェーン対応
        mock_table.not_ = MagicMock()
        mock_table.not_.in_ = MagicMock(return_value=mock_table)

        mock_table.execute.return_value = MagicMock(data=rows)
        return mock_table

    db.table.side_effect = _table_side_effect
    return db


# ─── _OPERATOR_MAP ────────────────────────────────────────────────────────────

class TestOperatorMap:
    def test_lte(self):
        assert _OPERATOR_MAP["<="](30, 30) is True
        assert _OPERATOR_MAP["<="](31, 30) is False

    def test_gte(self):
        assert _OPERATOR_MAP[">="](80, 80) is True
        assert _OPERATOR_MAP[">="](79, 80) is False

    def test_lt(self):
        assert _OPERATOR_MAP["<"](29, 30) is True
        assert _OPERATOR_MAP["<"](30, 30) is False

    def test_gt(self):
        assert _OPERATOR_MAP[">"](31, 30) is True
        assert _OPERATOR_MAP[">"](30, 30) is False

    def test_eq(self):
        assert _OPERATOR_MAP["=="](50, 50) is True
        assert _OPERATOR_MAP["=="](51, 50) is False

    def test_ne(self):
        assert _OPERATOR_MAP["!="](51, 50) is True
        assert _OPERATOR_MAP["!="](50, 50) is False


# ─── _evaluate_dynamic_condition ─────────────────────────────────────────────

class TestEvaluateDynamicCondition:
    @pytest.mark.asyncio
    async def test_health_score_lte_30_matches(self):
        """health_score <= 30 の顧客が存在する場合にマッチすること"""
        db = _make_db_mock({"customers": [
            {"id": "c1", "health_score": 25},
            {"id": "c2", "health_score": 30},
            {"id": "c3", "health_score": 31},
        ]})
        condition = {"field": "health_score", "operator": "<=", "threshold": 30, "table": "customers"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert len(result) == 2
        ids = [r["id"] for r in result]
        assert "c1" in ids
        assert "c2" in ids
        assert "c3" not in ids

    @pytest.mark.asyncio
    async def test_no_match_returns_empty_list(self):
        """閾値超えのレコードのみ存在する場合は空リストを返すこと"""
        db = _make_db_mock({"customers": [
            {"id": "c1", "health_score": 50},
            {"id": "c2", "health_score": 80},
        ]})
        condition = {"field": "health_score", "operator": "<=", "threshold": 30, "table": "customers"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_field_in_record_is_skipped(self):
        """フィールドが存在しないレコードはスキップされること"""
        db = _make_db_mock({"customers": [
            {"id": "c1"},  # health_score なし
            {"id": "c2", "health_score": 20},
        ]})
        condition = {"field": "health_score", "operator": "<=", "threshold": 30, "table": "customers"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert len(result) == 1
        assert result[0]["id"] == "c2"

    @pytest.mark.asyncio
    async def test_unsupported_table_returns_empty(self):
        """非対応テーブルは空リストを返すこと"""
        db = _make_db_mock({})
        condition = {"field": "score", "operator": ">=", "threshold": 10, "table": "unknown_table"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert result == []

    @pytest.mark.asyncio
    async def test_unsupported_operator_returns_empty(self):
        """非対応 operator は空リストを返すこと"""
        db = _make_db_mock({"customers": [{"id": "c1", "health_score": 20}]})
        condition = {"field": "health_score", "operator": "LIKE", "threshold": 30, "table": "customers"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert result == []

    @pytest.mark.asyncio
    async def test_missing_condition_fields_returns_empty(self):
        """condition に必須フィールドが欠けている場合は空リストを返すこと"""
        db = _make_db_mock({"customers": [{"id": "c1", "health_score": 20}]})
        # threshold が欠けている
        condition = {"field": "health_score", "operator": "<="}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert result == []

    @pytest.mark.asyncio
    async def test_db_exception_returns_empty(self):
        """DB例外発生時は空リストを返すこと"""
        db = MagicMock()
        db.table.side_effect = RuntimeError("DB接続エラー")
        condition = {"field": "health_score", "operator": "<=", "threshold": 30, "table": "customers"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert result == []

    @pytest.mark.asyncio
    async def test_support_tickets_table(self):
        """support_tickets テーブルも評価できること"""
        db = _make_db_mock({"support_tickets": [
            {"id": "t1", "priority": 5},
            {"id": "t2", "priority": 3},
        ]})
        condition = {"field": "priority", "operator": ">=", "threshold": 5, "table": "support_tickets"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_opportunities_table(self):
        """opportunities テーブルも評価できること"""
        db = _make_db_mock({"opportunities": [
            {"id": "op1", "amount": 1000000},
            {"id": "op2", "amount": 500000},
        ]})
        condition = {"field": "amount", "operator": ">", "threshold": 800000, "table": "opportunities"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert len(result) == 1
        assert result[0]["id"] == "op1"

    @pytest.mark.asyncio
    async def test_execution_logs_table(self):
        """execution_logs テーブルも評価できること"""
        db = _make_db_mock({"execution_logs": [
            {"id": "log1", "duration_ms": 5000},
        ]})
        condition = {"field": "duration_ms", "operator": "!=", "threshold": 0, "table": "execution_logs"}
        result = await _evaluate_dynamic_condition(COMPANY_ID, db, condition)
        assert len(result) == 1


# ─── 個別 evaluator 関数 ─────────────────────────────────────────────────────

class TestEvalHealthScoreLow:
    @pytest.mark.asyncio
    async def test_returns_customers_with_low_health(self):
        db = _make_db_mock({"customers": [
            {"id": "c1", "name": "低スコア顧客", "health_score": 20},
        ]})
        result = await _eval_health_score_low(COMPANY_ID, db)
        assert len(result) == 1
        assert result[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_low_score(self):
        db = _make_db_mock({"customers": []})
        result = await _eval_health_score_low(COMPANY_ID, db)
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.table.side_effect = RuntimeError("接続失敗")
        result = await _eval_health_score_low(COMPANY_ID, db)
        assert result == []


class TestEvalSlaBreach:
    @pytest.mark.asyncio
    async def test_returns_breached_tickets(self):
        db = _make_db_mock({"support_tickets": [
            {"id": "t1", "title": "期限超過チケット", "sla_due_at": "2024-01-01T00:00:00Z", "status": "open"},
        ]})
        result = await _eval_sla_breach(COMPANY_ID, db)
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_breach(self):
        db = _make_db_mock({"support_tickets": []})
        result = await _eval_sla_breach(COMPANY_ID, db)
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.table.side_effect = RuntimeError("接続失敗")
        result = await _eval_sla_breach(COMPANY_ID, db)
        assert result == []


class TestEvalUpsellHigh:
    @pytest.mark.asyncio
    async def test_returns_high_score_long_contract_customers(self):
        six_months_ago = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        db = _make_db_mock({"customers": [
            {"id": "c1", "name": "優良顧客", "health_score": 90, "contract_started_at": six_months_ago},
        ]})
        result = await _eval_upsell_high(COMPANY_ID, db)
        assert len(result) == 1
        assert result[0]["id"] == "c1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_match(self):
        db = _make_db_mock({"customers": []})
        result = await _eval_upsell_high(COMPANY_ID, db)
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.table.side_effect = RuntimeError("接続失敗")
        result = await _eval_upsell_high(COMPANY_ID, db)
        assert result == []


class TestEvalLostReengagement:
    @pytest.mark.asyncio
    async def test_returns_recently_lost_opportunities(self):
        recent_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db = _make_db_mock({"opportunities": [
            {"id": "op1", "title": "失注案件", "stage": "lost", "updated_at": recent_date},
        ]})
        result = await _eval_lost_reengagement(COMPANY_ID, db)
        assert len(result) == 1
        assert result[0]["id"] == "op1"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_lost(self):
        db = _make_db_mock({"opportunities": []})
        result = await _eval_lost_reengagement(COMPANY_ID, db)
        assert result == []

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        db = MagicMock()
        db.table.side_effect = RuntimeError("接続失敗")
        result = await _eval_lost_reengagement(COMPANY_ID, db)
        assert result == []


# ─── _evaluate_builtin_condition_chains ──────────────────────────────────────

class TestEvaluateBuiltinConditionChains:
    @pytest.mark.asyncio
    async def test_health_score_low_fires_cancellation_pipeline(self):
        """health_score_low がマッチすれば sales/cancellation タスクが生成されること"""
        db = _make_db_mock({"customers": [
            {"id": "c1", "name": "低スコア顧客A", "health_score": 20},
            {"id": "c2", "name": "低スコア顧客B", "health_score": 10},
        ]})
        tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)

        cancellation_tasks = [t for t in tasks if t.pipeline == "sales/cancellation"]
        assert len(cancellation_tasks) >= 1
        task = cancellation_tasks[0]
        assert task.company_id == COMPANY_ID
        assert task.trigger_type == TriggerType.CONDITION
        assert task.context.get("builtin_dynamic") is True
        assert task.context.get("matched_count") == 2
        assert set(task.context.get("matched_record_ids", [])) == {"c1", "c2"}

    @pytest.mark.asyncio
    async def test_sla_breach_fires_support_pipeline(self):
        """sla_breach がマッチすれば sales/support_auto_response タスクが生成されること"""
        db = _make_db_mock({
            "customers": [],
            "support_tickets": [
                {"id": "t1", "title": "超過チケット", "sla_due_at": "2024-01-01Z", "status": "open"},
            ],
            "opportunities": [],
        })
        tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)

        support_tasks = [t for t in tasks if t.pipeline == "sales/support_auto_response"]
        assert len(support_tasks) >= 1
        assert support_tasks[0].input_data.get("mode") == "escalation"

    @pytest.mark.asyncio
    async def test_upsell_high_fires_upsell_pipeline(self):
        """upsell_high がマッチすれば sales/upsell_briefing タスクが生成されること"""
        six_months_ago = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        db = _make_db_mock({
            "customers": [
                {"id": "c1", "name": "優良顧客", "health_score": 90, "contract_started_at": six_months_ago},
            ],
            "support_tickets": [],
            "opportunities": [],
        })
        tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)

        upsell_tasks = [t for t in tasks if t.pipeline == "sales/upsell_briefing"]
        assert len(upsell_tasks) >= 1

    @pytest.mark.asyncio
    async def test_lost_reengagement_fires_win_loss_pipeline(self):
        """lost_reengagement がマッチすれば sales/win_loss_feedback タスクが生成されること"""
        recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        db = _make_db_mock({
            "customers": [],
            "support_tickets": [],
            "opportunities": [
                {"id": "op1", "title": "失注案件", "stage": "lost", "updated_at": recent_date},
            ],
        })
        tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)

        win_loss_tasks = [t for t in tasks if t.pipeline == "sales/win_loss_feedback"]
        assert len(win_loss_tasks) >= 1
        assert win_loss_tasks[0].input_data.get("mode") == "reengagement_pdca"

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """どの条件もマッチしない場合は空リストを返すこと"""
        db = _make_db_mock({
            "customers": [],
            "support_tickets": [],
            "opportunities": [],
        })
        tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)
        assert tasks == []

    @pytest.mark.asyncio
    async def test_evaluator_exception_skips_chain(self):
        """1つの evaluator が例外を投げても他のチェーンの評価は続くこと"""
        # _eval_health_score_low だけ例外を投げ、残りは空を返す
        db = _make_db_mock({
            "customers": [],
            "support_tickets": [],
            "opportunities": [],
        })

        original_fn = _EVALUATOR_REGISTRY["health_score_low"]
        try:
            async def _failing(company_id, db):
                raise RuntimeError("意図的な評価エラー")

            _EVALUATOR_REGISTRY["health_score_low"] = _failing
            # 例外があっても他のチェーンが処理されること（クラッシュしないこと）
            tasks = await _evaluate_builtin_condition_chains(COMPANY_ID, db)
            assert isinstance(tasks, list)
        finally:
            _EVALUATOR_REGISTRY["health_score_low"] = original_fn

    def test_all_chains_have_evaluator_key(self):
        """BUILTIN_CONDITION_CHAINS の全エントリに evaluator_key が存在すること"""
        for chain in BUILTIN_CONDITION_CHAINS:
            assert "evaluator_key" in chain, f"chain '{chain.get('name')}' に evaluator_key がない"

    def test_all_evaluator_keys_registered(self):
        """BUILTIN_CONDITION_CHAINS の全 evaluator_key が _EVALUATOR_REGISTRY に登録されていること"""
        for chain in BUILTIN_CONDITION_CHAINS:
            key = chain["evaluator_key"]
            assert key in _EVALUATOR_REGISTRY, f"evaluator_key '{key}' が _EVALUATOR_REGISTRY に未登録"

    def test_all_target_pipelines_in_registry(self):
        """BUILTIN_CONDITION_CHAINS の全 target_pipeline が PIPELINE_REGISTRY に登録されていること"""
        from workers.bpo.manager.task_router import PIPELINE_REGISTRY
        for chain in BUILTIN_CONDITION_CHAINS:
            pipeline = chain["target_pipeline"]
            assert pipeline in PIPELINE_REGISTRY, (
                f"BUILTIN_CONDITION_CHAINS '{pipeline}' が PIPELINE_REGISTRY に未登録"
            )


# ─── evaluate_knowledge_triggers（統合テスト） ───────────────────────────────

class TestEvaluateKnowledgeTriggers:
    @pytest.mark.asyncio
    async def test_dynamic_condition_in_source_meta_triggers_task(self):
        """source_meta.condition フィールドが動的評価されてタスクが生成されること"""
        mock_db = MagicMock()

        # knowledge_relations: 1件の triggers 関係
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"source_id": "src-1", "target_id": "tgt-1", "metadata": {}}
        ]

        # knowledge_items (sources)
        sources_data = [
            {
                "id": "src-1",
                "title": "ヘルス低下監視",
                "is_active": True,
                "confidence": 0.9,
                "metadata": {
                    "condition": {
                        "field": "health_score",
                        "operator": "<=",
                        "threshold": 30,
                        "table": "customers",
                    }
                },
            }
        ]
        # knowledge_items (targets)
        targets_data = [
            {
                "id": "tgt-1",
                "title": "解約フロー",
                "confidence": 0.8,
                "metadata": {
                    "pipeline": "sales/cancellation",
                    "execution_level": 1,
                    "input_data": {"mode": "risk_alert"},
                },
            }
        ]

        # customers テーブル（動的評価用）
        customers_data = [{"id": "c1", "health_score": 20}]

        # DB モックの呼び出し順に応じて異なるデータを返す
        call_count = {"n": 0}
        table_mock_map = {
            "knowledge_relations": [{"source_id": "src-1", "target_id": "tgt-1", "metadata": {}}],
            "knowledge_items_sources": sources_data,
            "knowledge_items_targets": targets_data,
            "customers": customers_data,
        }

        # 簡単のため: get_service_client をパッチしてシンプルなモックを返す
        # ここでは evaluate_knowledge_triggers の中の各 DB 呼び出しをシンプルにモック
        with patch("db.supabase.get_service_client") as mock_get_db:
            db = MagicMock()
            mock_get_db.return_value = db

            # 呼び出し順を追跡する
            call_sequence = []

            def table_factory(table_name):
                call_sequence.append(table_name)
                tbl = MagicMock()

                # チェーンメソッド
                for m in ("select", "eq", "in_", "not_", "lte", "gte", "lt", "limit"):
                    getattr(tbl, m).return_value = tbl
                tbl.not_.in_ = MagicMock(return_value=tbl)

                if table_name == "knowledge_relations":
                    tbl.execute.return_value = MagicMock(data=[
                        {"source_id": "src-1", "target_id": "tgt-1", "metadata": {}}
                    ])
                elif table_name == "knowledge_items":
                    # 2回目の呼び出し判定は困難なため、sources + targets を合算して返す
                    tbl.execute.return_value = MagicMock(data=sources_data + targets_data)
                elif table_name == "customers":
                    tbl.execute.return_value = MagicMock(data=customers_data)
                else:
                    tbl.execute.return_value = MagicMock(data=[])
                return tbl

            db.table.side_effect = table_factory

            tasks = await evaluate_knowledge_triggers(COMPANY_ID)

        # sales/cancellation タスクが生成されていること（knowledge_relations 経由 or builtin）
        pipelines = [t.pipeline for t in tasks]
        assert "sales/cancellation" in pipelines

    @pytest.mark.asyncio
    async def test_static_fallback_when_no_condition_field(self):
        """source_meta に condition フィールドがない場合は condition_met フラグにフォールバックすること"""
        with patch("db.supabase.get_service_client") as mock_get_db:
            db = MagicMock()
            mock_get_db.return_value = db

            def table_factory(table_name):
                tbl = MagicMock()
                for m in ("select", "eq", "in_", "not_", "lte", "gte", "lt", "limit"):
                    getattr(tbl, m).return_value = tbl
                tbl.not_.in_ = MagicMock(return_value=tbl)

                if table_name == "knowledge_relations":
                    tbl.execute.return_value = MagicMock(data=[
                        {"source_id": "src-2", "target_id": "tgt-2", "metadata": {}}
                    ])
                elif table_name == "knowledge_items":
                    tbl.execute.return_value = MagicMock(data=[
                        {
                            "id": "src-2",
                            "title": "静的フラグソース",
                            "is_active": True,
                            "confidence": 0.8,
                            "metadata": {"condition_met": True},  # 静的フラグ
                        },
                        {
                            "id": "tgt-2",
                            "title": "ターゲット",
                            "confidence": 0.8,
                            "metadata": {
                                "pipeline": "sales/win_loss_feedback",
                                "execution_level": 1,
                                "input_data": {},
                            },
                        },
                    ])
                else:
                    tbl.execute.return_value = MagicMock(data=[])
                return tbl

            db.table.side_effect = table_factory

            tasks = await evaluate_knowledge_triggers(COMPANY_ID)

        win_loss_tasks = [t for t in tasks if t.pipeline == "sales/win_loss_feedback"]
        # static フラグ condition_met=True によってタスクが生成されること
        assert len(win_loss_tasks) >= 1

    @pytest.mark.asyncio
    async def test_static_fallback_false_skips_task(self):
        """condition フィールドなし + condition_met=False の場合はタスクが生成されないこと"""
        with patch("db.supabase.get_service_client") as mock_get_db:
            db = MagicMock()
            mock_get_db.return_value = db

            def table_factory(table_name):
                tbl = MagicMock()
                for m in ("select", "eq", "in_", "not_", "lte", "gte", "lt", "limit"):
                    getattr(tbl, m).return_value = tbl
                tbl.not_.in_ = MagicMock(return_value=tbl)

                if table_name == "knowledge_relations":
                    tbl.execute.return_value = MagicMock(data=[
                        {"source_id": "src-3", "target_id": "tgt-3", "metadata": {}}
                    ])
                elif table_name == "knowledge_items":
                    tbl.execute.return_value = MagicMock(data=[
                        {
                            "id": "src-3",
                            "title": "未発火ソース",
                            "is_active": True,
                            "confidence": 0.8,
                            "metadata": {"condition_met": False},  # 条件未成立
                        },
                        {
                            "id": "tgt-3",
                            "title": "ターゲット",
                            "confidence": 0.8,
                            "metadata": {
                                "pipeline": "sales/proposal_generation",
                                "execution_level": 2,
                                "input_data": {},
                            },
                        },
                    ])
                else:
                    tbl.execute.return_value = MagicMock(data=[])
                return tbl

            db.table.side_effect = table_factory

            tasks = await evaluate_knowledge_triggers(COMPANY_ID)

        # sales/proposal_generation は生成されないこと
        proposal_tasks = [t for t in tasks if t.pipeline == "sales/proposal_generation"]
        assert len(proposal_tasks) == 0

    @pytest.mark.asyncio
    async def test_db_exception_returns_empty(self):
        """DB接続失敗時は空リストを返すこと（クラッシュしないこと）"""
        with patch("db.supabase.get_service_client", side_effect=RuntimeError("DB接続不可")):
            tasks = await evaluate_knowledge_triggers(COMPANY_ID)
        assert tasks == []

    @pytest.mark.asyncio
    async def test_inactive_source_is_skipped(self):
        """is_active=False のソースはスキップされること"""
        with patch("db.supabase.get_service_client") as mock_get_db:
            db = MagicMock()
            mock_get_db.return_value = db

            def table_factory(table_name):
                tbl = MagicMock()
                for m in ("select", "eq", "in_", "not_", "lte", "gte", "lt", "limit"):
                    getattr(tbl, m).return_value = tbl
                tbl.not_.in_ = MagicMock(return_value=tbl)

                if table_name == "knowledge_relations":
                    tbl.execute.return_value = MagicMock(data=[
                        {"source_id": "src-4", "target_id": "tgt-4", "metadata": {}}
                    ])
                elif table_name == "knowledge_items":
                    tbl.execute.return_value = MagicMock(data=[
                        {
                            "id": "src-4",
                            "title": "非アクティブソース",
                            "is_active": False,  # 非アクティブ
                            "confidence": 0.8,
                            "metadata": {"condition_met": True},
                        },
                        {
                            "id": "tgt-4",
                            "title": "ターゲット",
                            "confidence": 0.8,
                            "metadata": {
                                "pipeline": "sales/outreach",
                                "execution_level": 1,
                                "input_data": {},
                            },
                        },
                    ])
                else:
                    tbl.execute.return_value = MagicMock(data=[])
                return tbl

            db.table.side_effect = table_factory
            tasks = await evaluate_knowledge_triggers(COMPANY_ID)

        outreach_from_relation = [
            t for t in tasks
            if t.pipeline == "sales/outreach" and t.knowledge_item_ids
        ]
        assert len(outreach_from_relation) == 0


# ─── BUILTIN_CONDITION_CHAINS 構造チェック ───────────────────────────────────

class TestBuiltinConditionChainsStructure:
    REQUIRED_FIELDS = {"name", "target_pipeline", "execution_level", "estimated_impact", "evaluator_key"}

    def test_all_chains_have_required_fields(self):
        for chain in BUILTIN_CONDITION_CHAINS:
            missing = self.REQUIRED_FIELDS - set(chain.keys())
            assert not missing, f"chain '{chain.get('name')}' に不足フィールド: {missing}"

    def test_estimated_impact_in_range(self):
        for chain in BUILTIN_CONDITION_CHAINS:
            impact = chain["estimated_impact"]
            assert 0.0 <= impact <= 1.0, f"chain '{chain['name']}' の estimated_impact が範囲外: {impact}"

    def test_four_builtin_conditions_defined(self):
        """4つの組み込み条件が定義されていること"""
        keys = {c["evaluator_key"] for c in BUILTIN_CONDITION_CHAINS}
        assert "health_score_low" in keys
        assert "sla_breach" in keys
        assert "upsell_high" in keys
        assert "lost_reengagement" in keys
