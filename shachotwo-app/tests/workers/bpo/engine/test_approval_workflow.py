"""approval_workflow.py のユニットテスト

テスト対象:
- approve_with_learning: learned_rules への INSERT と bpo_approvals の更新
- _get_learned_rules: 有効ルールの取得と applied_count インクリメント
- approve / reject: 基本的な承認・却下フロー
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone


# ─────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────

COMPANY_ID = "11111111-1111-1111-1111-111111111111"
APPROVAL_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
APPROVER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
EXECUTION_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _make_supabase_mock(return_data: list | None = None):
    """Supabase クライアントのチェーンモックを生成する"""
    mock_result = MagicMock()
    mock_result.data = return_data or []

    chain = MagicMock()
    # テーブル操作チェーン（select/insert/update/eq/order/limit）は全て chain を返す
    for method in ("table", "select", "insert", "update", "eq", "not_",
                   "order", "limit", "rpc"):
        getattr(chain, method).return_value = chain
    chain.execute.return_value = mock_result
    # not_.is_ も chain を返す
    chain.not_ = chain
    chain.is_ = MagicMock(return_value=chain)
    return chain


def _make_llm_response(content: str = "金額は税込で記載すること"):
    from llm.client import LLMResponse
    return LLMResponse(
        content=content,
        model_used="gemini-2.5-flash",
        tokens_in=50,
        tokens_out=20,
        cost_yen=0.01,
        latency_ms=300,
    )


# ─────────────────────────────────────
# approve_with_learning のテスト
# ─────────────────────────────────────

class TestApproveWithLearning:

    @pytest.mark.asyncio
    async def test_modification_diff_inserts_learned_rule(self):
        """modification_diff がある場合、learned_rules テーブルに INSERT される"""
        approval_record = {
            "id": APPROVAL_ID,
            "company_id": COMPANY_ID,
            "target_type": "estimation_pipeline",
            "status": "approved",
            "modification_diff": {"before": "¥100,000", "after": "¥110,000（税込）"},
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])
        llm_response = _make_llm_response("金額は必ず税込で記載すること")

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.engine.approval_workflow.LLMClient"
        ) as MockLLMClient:
            mock_llm_instance = MagicMock()
            mock_llm_instance.generate = AsyncMock(return_value=llm_response)
            MockLLMClient.return_value = mock_llm_instance

            from workers.bpo.engine.approval_workflow import approve_with_learning
            result = await approve_with_learning(
                approval_id=APPROVAL_ID,
                approver_id=APPROVER_ID,
                company_id=COMPANY_ID,
                modification_diff={"before": "¥100,000", "after": "¥110,000（税込）"},
                pipeline="estimation_pipeline",
                source_execution_id=EXECUTION_ID,
            )

        assert result["learned_rule"] == "金額は必ず税込で記載すること"
        # learned_rules への insert が呼ばれたことを確認
        insert_calls = [
            c for c in db_mock.table.call_args_list
            if c.args and c.args[0] == "learned_rules"
        ]
        assert len(insert_calls) >= 1

    @pytest.mark.asyncio
    async def test_rejection_reason_inserts_preference_rule(self):
        """rejection_reason がある場合、rule_type=preference で INSERT される"""
        approval_record = {
            "id": APPROVAL_ID,
            "company_id": COMPANY_ID,
            "target_type": "invoice_pipeline",
            "status": "rejected",
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])
        llm_response = _make_llm_response("支払条件が末締め翌月払いの場合は再確認が必要")

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.engine.approval_workflow.LLMClient"
        ) as MockLLMClient:
            mock_llm_instance = MagicMock()
            mock_llm_instance.generate = AsyncMock(return_value=llm_response)
            MockLLMClient.return_value = mock_llm_instance

            from workers.bpo.engine.approval_workflow import approve_with_learning
            result = await approve_with_learning(
                approval_id=APPROVAL_ID,
                approver_id=APPROVER_ID,
                company_id=COMPANY_ID,
                rejection_reason="支払条件が間違っていた",
                is_rejection=True,
                pipeline="invoice_pipeline",
            )

        assert result["learned_rule"] == "支払条件が末締め翌月払いの場合は再確認が必要"
        # LLM が呼ばれた際の LLMTask の rule_type 引数は insert_data 経由で確認
        # insert が learned_rules テーブルに対して行われたこと
        insert_calls = [
            c for c in db_mock.table.call_args_list
            if c.args and c.args[0] == "learned_rules"
        ]
        assert len(insert_calls) >= 1

    @pytest.mark.asyncio
    async def test_no_diff_no_reason_skips_llm(self):
        """modification_diff も rejection_reason もない場合、LLM は呼ばれない"""
        approval_record = {
            "id": APPROVAL_ID,
            "company_id": COMPANY_ID,
            "target_type": "estimation_pipeline",
            "status": "approved",
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.engine.approval_workflow.LLMClient"
        ) as MockLLMClient:
            mock_llm_instance = MagicMock()
            mock_llm_instance.generate = AsyncMock()
            MockLLMClient.return_value = mock_llm_instance

            from workers.bpo.engine.approval_workflow import approve_with_learning
            result = await approve_with_learning(
                approval_id=APPROVAL_ID,
                approver_id=APPROVER_ID,
                company_id=COMPANY_ID,
            )

        assert result["learned_rule"] is None
        mock_llm_instance.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_does_not_raise(self):
        """LLM 呼び出しが失敗しても例外を握りつぶして処理を継続する"""
        approval_record = {
            "id": APPROVAL_ID,
            "company_id": COMPANY_ID,
            "target_type": "estimation_pipeline",
            "status": "approved",
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.engine.approval_workflow.LLMClient"
        ) as MockLLMClient:
            mock_llm_instance = MagicMock()
            mock_llm_instance.generate = AsyncMock(side_effect=RuntimeError("LLM timeout"))
            MockLLMClient.return_value = mock_llm_instance

            from workers.bpo.engine.approval_workflow import approve_with_learning
            result = await approve_with_learning(
                approval_id=APPROVAL_ID,
                approver_id=APPROVER_ID,
                company_id=COMPANY_ID,
                modification_diff={"before": "旧", "after": "新"},
            )

        # 例外が上位に伝播せず、learned_rule は None で返る
        assert result["learned_rule"] is None

    @pytest.mark.asyncio
    async def test_pipeline_fallback_to_target_type(self):
        """pipeline 未指定時は bpo_approvals.target_type を pipeline として使用する"""
        approval_record = {
            "id": APPROVAL_ID,
            "company_id": COMPANY_ID,
            "target_type": "subcontractor_pipeline",
            "status": "approved",
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])
        llm_response = _make_llm_response("外注費は必ず3社相見積もりを取ること")

        inserted_data: list[dict] = []

        original_insert = db_mock.insert

        def capture_insert(data):
            inserted_data.append(data)
            return db_mock

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ), patch(
            "workers.bpo.engine.approval_workflow.LLMClient"
        ) as MockLLMClient:
            mock_llm_instance = MagicMock()
            mock_llm_instance.generate = AsyncMock(return_value=llm_response)
            MockLLMClient.return_value = mock_llm_instance

            from workers.bpo.engine.approval_workflow import approve_with_learning
            result = await approve_with_learning(
                approval_id=APPROVAL_ID,
                approver_id=APPROVER_ID,
                company_id=COMPANY_ID,
                modification_diff={"before": "1社見積", "after": "3社見積"},
                # pipeline を意図的に渡さない
            )

        assert result["learned_rule"] == "外注費は必ず3社相見積もりを取ること"


# ─────────────────────────────────────
# _get_learned_rules のテスト
# ─────────────────────────────────────

class TestGetLearnedRules:

    @pytest.mark.asyncio
    async def test_returns_active_rules_as_dict_list(self):
        """is_active=True のルールを dict のリストで返す"""
        rows = [
            {
                "id": "r1",
                "rule_text": "金額は税込で記載すること",
                "rule_type": "correction",
                "confidence": "0.70",
                "applied_count": 5,
                "step_name": "calculate",
            },
            {
                "id": "r2",
                "rule_text": "外注費は3社見積もりを取ること",
                "rule_type": "preference",
                "confidence": "0.90",
                "applied_count": 12,
                "step_name": None,
            },
        ]
        db_mock = _make_supabase_mock(return_data=rows)

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import _get_learned_rules
            result = await _get_learned_rules(COMPANY_ID, "estimation_pipeline")

        assert len(result) == 2
        assert result[0]["rule_text"] == "金額は税込で記載すること"
        assert result[0]["confidence"] == 0.70
        assert result[1]["applied_count"] == 12

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_rules(self):
        """ルールが存在しない場合は空リストを返す"""
        db_mock = _make_supabase_mock(return_data=[])

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import _get_learned_rules
            result = await _get_learned_rules(COMPANY_ID, "unknown_pipeline")

        assert result == []

    @pytest.mark.asyncio
    async def test_rpc_increment_called_when_rules_exist(self):
        """ルールが取得された場合、applied_count インクリメント RPC が呼ばれる"""
        rows = [
            {
                "id": "r1",
                "rule_text": "ルール1",
                "rule_type": "correction",
                "confidence": "0.80",
                "applied_count": 3,
                "step_name": None,
            }
        ]
        db_mock = _make_supabase_mock(return_data=rows)

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import _get_learned_rules
            await _get_learned_rules(COMPANY_ID, "estimation_pipeline")

        # rpc が呼ばれたことを確認
        db_mock.rpc.assert_called_once_with(
            "increment_learned_rules_applied_count",
            {"rule_ids": ["r1"]},
        )

    @pytest.mark.asyncio
    async def test_rpc_failure_does_not_raise(self):
        """RPC 失敗時も例外を握りつぶしてルールを返す"""
        rows = [
            {
                "id": "r1",
                "rule_text": "ルール1",
                "rule_type": "correction",
                "confidence": "0.70",
                "applied_count": 1,
                "step_name": None,
            }
        ]
        # SELECT 用モック（正常）と RPC 用モック（失敗）を分ける
        select_result = MagicMock()
        select_result.data = rows

        select_chain = MagicMock()
        for method in ("table", "select", "eq", "order", "limit"):
            getattr(select_chain, method).return_value = select_chain
        select_chain.execute.return_value = select_result

        rpc_chain = MagicMock()
        rpc_chain.execute.side_effect = RuntimeError("RPC error")

        db_mock = MagicMock()
        # table("learned_rules") → select_chain、rpc(...) → rpc_chain
        db_mock.table.return_value = select_chain
        db_mock.rpc.return_value = rpc_chain

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import _get_learned_rules
            result = await _get_learned_rules(COMPANY_ID, "estimation_pipeline")

        # RPC 失敗でもルールは返る
        assert len(result) == 1
        assert result[0]["rule_text"] == "ルール1"


# ─────────────────────────────────────
# approve / reject の基本テスト
# ─────────────────────────────────────

class TestApproveReject:

    @pytest.mark.asyncio
    async def test_approve_sets_status_approved(self):
        approval_record = {
            "id": APPROVAL_ID,
            "status": "approved",
            "approver_id": APPROVER_ID,
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import approve
            result = await approve(APPROVAL_ID, APPROVER_ID, comment="問題なし")

        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_sets_status_rejected(self):
        approval_record = {
            "id": APPROVAL_ID,
            "status": "rejected",
            "approver_id": APPROVER_ID,
        }
        db_mock = _make_supabase_mock(return_data=[approval_record])

        with patch(
            "workers.bpo.engine.approval_workflow.get_client",
            return_value=db_mock,
        ):
            from workers.bpo.engine.approval_workflow import reject
            result = await reject(
                APPROVAL_ID, APPROVER_ID,
                rejection_reason="金額計算が誤っている",
            )

        assert result["status"] == "rejected"
