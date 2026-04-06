"""期限リマインダパイプライン テスト。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from workers.bpo.common.pipelines.admin_reminder_pipeline import (
    PRIORITY_NORMAL,
    PRIORITY_OVERDUE,
    PRIORITY_URGENT,
    PRIORITY_WARNING,
    AdminReminderPipelineResult,
    run_admin_reminder_pipeline,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = str(uuid4())

# リファレンス日: 2026-03-20（タスク指定）
REFERENCE_DATE = "2026-03-20"


def _mock_generator_out() -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name="document_generator", success=True,
        result={"content": "期限リマインダ一覧（テスト）", "format": "text", "char_count": 14},
        confidence=0.9, cost_yen=0.1, duration_ms=200,
    )


# ─────────────────────────────────────────────────────────────────────
# テスト 1: 期限切れ→urgent→warning→normalの順でソートされる
# ─────────────────────────────────────────────────────────────────────
class TestDeadlinesSortedByPriority:
    @pytest.mark.asyncio
    async def test_deadlines_sorted_by_priority(self):
        """期限切れ→urgent→warning→normalの順でリマインダが並ぶ。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [
                {
                    "type": "法人税申告",
                    "deadline_date": "2026-05-31",  # normal（72日後）
                    "description": "第67期",
                    "responsible": "経理部",
                },
                {
                    "type": "建設業許可更新",
                    "deadline_date": "2026-03-15",  # overdue（5日前）
                    "description": "第12345号",
                    "responsible": "総務部",
                },
                {
                    "type": "労働保険申告",
                    "deadline_date": "2026-04-10",  # warning（21日後）
                    "description": "令和7年度",
                    "responsible": "総務部",
                },
                {
                    "type": "社会保険算定",
                    "deadline_date": "2026-03-24",  # urgent（4日後）
                    "description": "定時決定",
                    "responsible": "人事部",
                },
            ],
        }

        with patch(
            "workers.bpo.common.pipelines.admin_reminder_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ):
            result = await run_admin_reminder_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        priorities = [r["priority"] for r in result.reminders]
        assert priorities[0] == PRIORITY_OVERDUE
        assert priorities[1] == PRIORITY_URGENT
        assert priorities[2] == PRIORITY_WARNING
        assert priorities[3] == PRIORITY_NORMAL


# ─────────────────────────────────────────────────────────────────────
# テスト 2: 期限切れを正しく検出
# ─────────────────────────────────────────────────────────────────────
class TestOverdueDeadlineDetected:
    @pytest.mark.asyncio
    async def test_overdue_deadline_detected(self):
        """reference_dateより前の期限はoverdueになる。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [
                {
                    "type": "建設業許可更新",
                    "deadline_date": "2026-03-01",  # 19日前 → overdue
                    "description": "期限切れテスト",
                    "responsible": "総務部",
                },
            ],
        }

        with patch(
            "workers.bpo.common.pipelines.admin_reminder_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ):
            result = await run_admin_reminder_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert len(result.reminders) == 1
        assert result.reminders[0]["priority"] == PRIORITY_OVERDUE
        assert result.final_output["overdue_count"] == 1


# ─────────────────────────────────────────────────────────────────────
# テスト 3: 7日以内はurgent
# ─────────────────────────────────────────────────────────────────────
class TestUrgent7DaysAlert:
    @pytest.mark.asyncio
    async def test_urgent_7days_alert(self):
        """reference_dateから7日以内の期限はurgentになる。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [
                {
                    "type": "社会保険算定",
                    "deadline_date": "2026-03-27",  # 7日後 → urgent
                    "description": "7日後テスト",
                    "responsible": "人事部",
                },
                {
                    "type": "消費税申告",
                    "deadline_date": "2026-03-20",  # 0日後（当日）→ urgent
                    "description": "当日テスト",
                    "responsible": "経理部",
                },
            ],
        }

        with patch(
            "workers.bpo.common.pipelines.admin_reminder_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ):
            result = await run_admin_reminder_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert result.final_output["urgent_count"] == 2
        for reminder in result.reminders:
            assert reminder["priority"] == PRIORITY_URGENT


# ─────────────────────────────────────────────────────────────────────
# テスト 4: 30日超はnormal
# ─────────────────────────────────────────────────────────────────────
class TestNormalBeyond30Days:
    @pytest.mark.asyncio
    async def test_normal_beyond_30days(self):
        """reference_dateから31日以降の期限はnormalになる。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [
                {
                    "type": "法人税申告",
                    "deadline_date": "2026-04-20",  # 31日後 → normal
                    "description": "31日後テスト",
                    "responsible": "経理部",
                },
            ],
        }

        with patch(
            "workers.bpo.common.pipelines.admin_reminder_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ):
            result = await run_admin_reminder_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert result.reminders[0]["priority"] == PRIORITY_NORMAL
        assert result.final_output["normal_count"] == 1


# ─────────────────────────────────────────────────────────────────────
# テスト 5: 全3ステップが実行される
# ─────────────────────────────────────────────────────────────────────
class TestAll3StepsExecuted:
    @pytest.mark.asyncio
    async def test_all_3_steps_executed(self):
        """正常系で全3ステップが steps リストに含まれる。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [
                {
                    "type": "建設業許可更新",
                    "deadline_date": "2026-04-15",
                    "description": "テスト",
                    "responsible": "総務部",
                },
            ],
        }

        with patch(
            "workers.bpo.common.pipelines.admin_reminder_pipeline.run_document_generator",
            new=AsyncMock(return_value=_mock_generator_out()),
        ):
            result = await run_admin_reminder_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

        assert result.success is True
        assert len(result.steps) == 3

        step_names = [s.step_name for s in result.steps]
        assert step_names == ["deadline_scanner", "priority_sorter", "reminder_generator"]

        step_nos = [s.step_no for s in result.steps]
        assert step_nos == [1, 2, 3]

        for step in result.steps:
            assert step.success is True, f"Step {step.step_no} ({step.step_name}) が失敗"


# ─────────────────────────────────────────────────────────────────────
# テスト 6: 空リストでも成功返却（リマインダ0件）
# ─────────────────────────────────────────────────────────────────────
class TestEmptyDeadlinesReturnsSuccess:
    @pytest.mark.asyncio
    async def test_empty_deadlines_returns_success(self):
        """deadlinesが空リストでも success=True でリマインダ0件を返す。"""
        input_data = {
            "reference_date": REFERENCE_DATE,
            "deadlines": [],
        }

        result = await run_admin_reminder_pipeline(
            company_id=COMPANY_ID,
            input_data=input_data,
        )

        assert result.success is True
        assert len(result.reminders) == 0
        assert result.final_output["total_count"] == 0
        assert result.failed_step is None
        # 3ステップとも実行されている
        assert len(result.steps) == 3
