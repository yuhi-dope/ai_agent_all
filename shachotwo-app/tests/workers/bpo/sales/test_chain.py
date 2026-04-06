"""パイプライン自動チェーンのユニットテスト"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.chain import (
    _log_bpo_completion,
    _log_chain_event,
    _run_customer_onboarding,
    _run_proposal_generation,
    _run_quotation_contract,
    _run_upsell_briefing,
    _run_win_loss_feedback,
    trigger_next_pipeline,
)


# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------


def _make_result(**kwargs: Any) -> Any:
    """テスト用の軽量結果オブジェクトを生成する。"""
    return type("Result", (), {"final_output": {}, **kwargs})()


# ---------------------------------------------------------------------------
# trigger_next_pipeline のチェーンルール
# ---------------------------------------------------------------------------


class TestTriggerNextPipeline:
    """trigger_next_pipeline のチェーンルール検証。"""

    @pytest.mark.asyncio
    async def test_lead_qualified_triggers_proposal(self) -> None:
        """リード QUALIFIED → 提案書生成パイプラインが起動される。"""
        result = _make_result(
            routing="QUALIFIED",
            final_output={"routing": "QUALIFIED", "lead": {"id": "lead-001", "company_name": "テスト建設"}},
        )

        created_tasks: list = []
        with (
            patch("workers.bpo.sales.chain._run_proposal_generation", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain.asyncio.create_task", side_effect=lambda coro: created_tasks.append(coro)) as mock_ct,
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("lead_qualification_pipeline", result, "company-1")

        assert "proposal_generation_pipeline" in out["triggered"]

    @pytest.mark.asyncio
    async def test_lead_review_does_not_trigger_proposal(self) -> None:
        """リード REVIEW → 提案書生成は起動されない。"""
        result = _make_result(
            routing="REVIEW",
            final_output={"routing": "REVIEW", "lead": {}},
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("lead_qualification_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_lead_nurturing_does_not_trigger_proposal(self) -> None:
        """リード NURTURING → 提案書生成は起動されない。"""
        result = _make_result(
            routing="NURTURING",
            final_output={"routing": "NURTURING", "lead": {}},
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("lead_qualification_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_contract_signed_triggers_onboarding(self) -> None:
        """契約署名完了 → 顧客ライフサイクル(onboarding) が起動される。"""
        result = _make_result(
            final_output={"contract": {"status": "signed", "customer_id": "cust-999"}},
        )

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._run_customer_onboarding", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("quotation_contract_pipeline", result, "company-1")

        assert "customer_lifecycle_pipeline(onboarding)" in out["triggered"]

    @pytest.mark.asyncio
    async def test_contract_signed_no_customer_id_skips(self) -> None:
        """契約署名完了でも customer_id がなければ起動しない。"""
        result = _make_result(
            final_output={"contract": {"status": "signed"}},
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("quotation_contract_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_contract_not_signed_skips_onboarding(self) -> None:
        """契約未署名 → onboarding は起動されない。"""
        result = _make_result(
            final_output={"contract": {"status": "pending", "customer_id": "cust-1"}},
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("quotation_contract_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_health_high_with_unused_modules_triggers_upsell(self) -> None:
        """ヘルス >= 80 + 未使用モジュール → upsell_briefing が起動される。"""
        result = _make_result(
            final_output={
                "health": {"score": 85, "unused_modules": ["backoffice"]},
                "customer_id": "cust-42",
            },
        )

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._run_upsell_briefing", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("customer_lifecycle_pipeline", result, "company-1")

        assert "upsell_briefing_pipeline" in out["triggered"]

    @pytest.mark.asyncio
    async def test_health_high_no_unused_modules_skips_upsell(self) -> None:
        """ヘルス >= 80 でも未使用モジュールなし → upsell は起動しない。"""
        result = _make_result(
            final_output={
                "health": {"score": 90, "unused_modules": []},
                "customer_id": "cust-42",
            },
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("customer_lifecycle_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_health_low_skips_upsell(self) -> None:
        """ヘルス < 80 → upsell は起動しない。"""
        result = _make_result(
            final_output={
                "health": {"score": 30, "unused_modules": ["backoffice"]},
                "customer_id": "cust-42",
            },
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("customer_lifecycle_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_opportunity_won_triggers_win_loss(self) -> None:
        """商談 won → win_loss_feedback が起動される。"""
        result = _make_result()

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._run_win_loss_feedback", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline(
                "opportunity_stage_changed",
                result,
                "company-1",
                context={"stage": "won", "opportunity_id": "opp-001"},
            )

        assert "win_loss_feedback_pipeline(outcome=won)" in out["triggered"]

    @pytest.mark.asyncio
    async def test_opportunity_lost_triggers_win_loss(self) -> None:
        """商談 lost → win_loss_feedback が起動される。"""
        result = _make_result()

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._run_win_loss_feedback", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline(
                "opportunity_stage_changed",
                result,
                "company-1",
                context={"stage": "lost", "opportunity_id": "opp-002"},
            )

        assert "win_loss_feedback_pipeline(outcome=lost)" in out["triggered"]

    @pytest.mark.asyncio
    async def test_proposal_sent_triggers_quotation_contract(self) -> None:
        """提案書 sent + opportunity_id あり → quotation_contract_pipeline が起動される。"""
        result = _make_result(
            final_output={
                "email_sent": True,
                "opportunity_id": "opp-100",
                "proposal": {"status": "sent"},
            },
        )

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._run_quotation_contract", new_callable=AsyncMock),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("proposal_generation_pipeline", result, "company-1")

        assert "quotation_contract_pipeline" in out["triggered"]

    @pytest.mark.asyncio
    async def test_proposal_draft_does_not_trigger_quotation_contract(self) -> None:
        """提案書 draft → quotation_contract_pipeline は起動されない。"""
        result = _make_result(
            final_output={
                "email_sent": False,
                "opportunity_id": "opp-100",
                "proposal": {"status": "draft"},
            },
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("proposal_generation_pipeline", result, "company-1")

        assert "quotation_contract_pipeline" not in out["triggered"]

    @pytest.mark.asyncio
    async def test_proposal_sent_no_opportunity_id_skips(self) -> None:
        """提案書 sent でも opportunity_id なし → quotation_contract は起動しない。"""
        result = _make_result(
            final_output={
                "email_sent": True,
                "opportunity_id": "",
                "proposal": {"status": "sent"},
            },
        )

        with patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock):
            out = await trigger_next_pipeline("proposal_generation_pipeline", result, "company-1")

        assert "quotation_contract_pipeline" not in out["triggered"]

    @pytest.mark.asyncio
    async def test_bpo_construction_estimation_logs_completion(self) -> None:
        """construction/estimation 完了 → bpo_completion_logged がtriggeredに含まれる。"""
        result = _make_result(final_output={"success": True})

        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("construction/estimation", result, "company-1")

        assert "bpo_completion_logged:construction/estimation" in out["triggered"]

    @pytest.mark.asyncio
    async def test_bpo_manufacturing_quoting_logs_completion(self) -> None:
        """manufacturing/quoting 完了 → bpo_completion_logged がtriggeredに含まれる。"""
        result = _make_result(final_output={"success": True})

        with (
            patch("workers.bpo.sales.chain.asyncio.create_task"),
            patch("workers.bpo.sales.chain._log_chain_event", new_callable=AsyncMock),
        ):
            out = await trigger_next_pipeline("manufacturing/quoting", result, "company-1")

        assert "bpo_completion_logged:manufacturing/quoting" in out["triggered"]

    @pytest.mark.asyncio
    async def test_unknown_pipeline_returns_empty(self) -> None:
        """未登録パイプライン名 → triggered は空。"""
        result = _make_result()

        out = await trigger_next_pipeline("unknown_pipeline", result, "company-1")

        assert out["triggered"] == []

    @pytest.mark.asyncio
    async def test_returns_dict_with_triggered_key(self) -> None:
        """戻り値は必ず {"triggered": [...]} 形式。"""
        result = _make_result()

        out = await trigger_next_pipeline("unknown_pipeline", result, "company-1")

        assert isinstance(out, dict)
        assert "triggered" in out


# ---------------------------------------------------------------------------
# ログ記録
# ---------------------------------------------------------------------------


class TestLogChainEvent:
    """_log_chain_event のテスト。"""

    @pytest.mark.asyncio
    async def test_log_inserts_to_execution_logs(self) -> None:
        """execution_logs に insert が呼ばれる。"""
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("workers.bpo.sales.chain.get_service_client", return_value=mock_db):
            await _log_chain_event("company-x", "lead_qualification_pipeline", ["proposal_generation_pipeline"])

        mock_db.table.assert_called_with("execution_logs")
        insert_call = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_call["company_id"] == "company-x"
        assert insert_call["agent_name"] == "pipeline_chain"
        assert "lead_qualification_pipeline" in insert_call["input_hash"]

    @pytest.mark.asyncio
    async def test_log_failure_does_not_raise(self) -> None:
        """DB エラーが発生しても例外を上げない。"""
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.side_effect = Exception("DB error")

        with patch("workers.bpo.sales.chain.get_service_client", return_value=mock_db):
            # 例外が上がらないことを確認
            await _log_chain_event("company-x", "some_pipeline", ["next_pipeline"])


# ---------------------------------------------------------------------------
# ラッパー関数のエラーハンドリング
# ---------------------------------------------------------------------------


class TestWrapperErrorHandling:
    """各ラッパー関数がエラーを上位に伝播させないことを確認。"""

    @pytest.mark.asyncio
    async def test_run_proposal_generation_catches_error(self) -> None:
        """_run_proposal_generation はエラーをキャッチして終了する。"""
        with patch(
            "workers.bpo.sales.sfa.proposal_generation_pipeline.run_proposal_generation_pipeline",
            side_effect=Exception("LLM error"),
        ):
            # 例外が上がらないことを確認
            await _run_proposal_generation("company-1", {"id": "lead-1", "company_name": "ABC"})

    @pytest.mark.asyncio
    async def test_run_customer_onboarding_catches_error(self) -> None:
        """_run_customer_onboarding はエラーをキャッチして終了する。"""
        with patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_customer_lifecycle_pipeline",
            side_effect=Exception("DB error"),
        ):
            await _run_customer_onboarding("company-1", "cust-1")

    @pytest.mark.asyncio
    async def test_run_upsell_briefing_catches_error(self) -> None:
        """_run_upsell_briefing はエラーをキャッチして終了する。"""
        with patch(
            "workers.bpo.sales.cs.upsell_briefing_pipeline.run_upsell_briefing_pipeline",
            side_effect=Exception("Slack error"),
        ):
            await _run_upsell_briefing("company-1", "cust-1")

    @pytest.mark.asyncio
    async def test_run_win_loss_feedback_catches_error(self) -> None:
        """_run_win_loss_feedback はエラーをキャッチして終了する。"""
        with patch(
            "workers.bpo.sales.learning.win_loss_feedback_pipeline.run_win_loss_feedback_pipeline",
            side_effect=Exception("LLM error"),
        ):
            await _run_win_loss_feedback("company-1", "opp-1", "won")

    @pytest.mark.asyncio
    async def test_run_quotation_contract_catches_error(self) -> None:
        """_run_quotation_contract はエラーをキャッチして終了する。"""
        with patch(
            "workers.bpo.sales.sfa.quotation_contract_pipeline.run_quotation_contract_pipeline",
            side_effect=Exception("LLM error"),
        ):
            await _run_quotation_contract("company-1", "opp-1")

    @pytest.mark.asyncio
    async def test_log_bpo_completion_inserts_to_execution_logs(self) -> None:
        """_log_bpo_completion は execution_logs に insert する。"""
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        result = _make_result(success=True)

        with patch("db.supabase.get_service_client", return_value=mock_db):
            await _log_bpo_completion("company-x", "construction/estimation", result)

        mock_db.table.assert_called_with("execution_logs")
        insert_call = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_call["company_id"] == "company-x"
        assert insert_call["pipeline"] == "construction/estimation"
        assert insert_call["payload"]["event"] == "bpo_pipeline_completed"

    @pytest.mark.asyncio
    async def test_log_bpo_completion_handles_db_error(self) -> None:
        """_log_bpo_completion はDB エラーが発生しても例外を上げない。"""
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.side_effect = Exception("DB error")

        with patch("db.supabase.get_service_client", return_value=mock_db):
            await _log_bpo_completion("company-x", "manufacturing/quoting", None)
