"""テナント分離テスト

全BPOパイプラインで、テナントAのデータがテナントBから参照・干渉できないことを検証。
"""
import asyncio
import types
import pytest
from unittest.mock import patch

from workers.bpo.manager.models import BPOTask, ExecutionLevel, TriggerType, PipelineResult
from workers.bpo.manager.task_router import route_and_execute

TENANT_A = "company_tenant_a_test"
TENANT_B = "company_tenant_b_test"


def _make_mock_pipeline(captured: dict | None = None):
    """route_and_executeが呼ぶパイプライン関数のモック生成"""
    async def mock_pipeline(*args, **kwargs):
        cid = kwargs.get("company_id", "")
        if captured is not None:
            captured["company_id"] = cid
            captured["input_data"] = kwargs.get("input_data")
        return type("R", (), {
            "success": True, "steps": [],
            "final_output": {"tenant": cid},
            "total_cost_yen": 0.0, "total_duration_ms": 0, "failed_step": None,
        })()
    return mock_pipeline


class TestTenantIsolation:

    @pytest.mark.asyncio
    async def test_pipeline_receives_correct_company_id(self):
        """パイプラインに渡されるcompany_idが正しい"""
        captured = {}
        mock_fn = _make_mock_pipeline(captured)

        with patch("workers.bpo.manager.task_router.importlib.import_module") as mock_import:
            mock_import.return_value = types.SimpleNamespace(run_dispatch_pipeline=mock_fn)

            task = BPOTask(
                company_id=TENANT_A,
                pipeline="logistics/dispatch",
                trigger_type=TriggerType.EVENT,
                execution_level=ExecutionLevel.DRAFT_CREATE,
                input_data={"test": True},
            )
            await route_and_execute(task)

            assert captured["company_id"] == TENANT_A

    @pytest.mark.asyncio
    async def test_different_tenants_get_different_results(self):
        """異なるテナントの結果が混ざらない"""
        mock_fn = _make_mock_pipeline()

        with patch("workers.bpo.manager.task_router.importlib.import_module") as mock_import:
            mock_import.return_value = types.SimpleNamespace(run_care_billing_pipeline=mock_fn)

            results = {}
            for tid in [TENANT_A, TENANT_B]:
                task = BPOTask(
                    company_id=tid,
                    pipeline="nursing/care_billing",
                    trigger_type=TriggerType.EVENT,
                    input_data={},
                )
                results[tid] = await route_and_execute(task)

            assert results[TENANT_A].final_output.get("tenant") == TENANT_A
            assert results[TENANT_B].final_output.get("tenant") == TENANT_B

    @pytest.mark.asyncio
    async def test_concurrent_tenants_do_not_interfere(self):
        """並行実行時にデータが干渉しない"""
        mock_fn = _make_mock_pipeline()

        with patch("workers.bpo.manager.task_router.importlib.import_module") as mock_import:
            mock_import.return_value = types.SimpleNamespace(run_expense_pipeline=mock_fn)

            tasks = []
            for tid in [TENANT_A, TENANT_B, TENANT_A, TENANT_B]:
                t = BPOTask(
                    company_id=tid,
                    pipeline="common/expense",
                    trigger_type=TriggerType.EVENT,
                    input_data={},
                )
                tasks.append(route_and_execute(t))

            results = await asyncio.gather(*tasks)
            assert all(r.success for r in results)
            assert results[0].final_output["tenant"] == TENANT_A
            assert results[1].final_output["tenant"] == TENANT_B


class TestTenantIsolationCostTracker:

    def test_cost_tracking_per_tenant(self):
        """テナント別コストが独立している"""
        from llm.cost_tracker import CostTracker
        tracker = CostTracker()
        tracker.record_cost(TENANT_A, 49_000)
        tracker.record_cost(TENANT_B, 100)

        assert tracker.get_status(TENANT_A)["total_cost_yen"] == 49_000
        assert tracker.get_status(TENANT_B)["total_cost_yen"] == 100

    def test_budget_exceeded_does_not_affect_other_tenant(self):
        """テナントAの予算超過がテナントBに影響しない"""
        from llm.cost_tracker import CostTracker
        from fastapi import HTTPException
        tracker = CostTracker()
        tracker.record_cost(TENANT_A, 50_001)

        with pytest.raises(HTTPException):
            tracker.check_budget(TENANT_A)
        tracker.check_budget(TENANT_B)  # 例外なし=OK
