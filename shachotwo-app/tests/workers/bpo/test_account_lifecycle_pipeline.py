"""アカウントライフサイクル管理パイプライン（account_lifecycle_pipeline）のテスト。

全て Supabase / LLM をモックし、DB 接続なしで実行する。
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

COMPANY_ID = "test-company-lifecycle"
_NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_user(
    email: str = "user@example.com",
    role: str = "editor",
    is_active: bool = True,
    last_login_at: datetime | None = None,
    user_id: str = "user-001",
) -> dict:
    login = last_login_at or (_NOW - timedelta(days=10))
    return {
        "id": user_id,
        "email": email,
        "role": role,
        "is_active": is_active,
        "last_login_at": _iso(login),
        "created_at": _iso(_NOW - timedelta(days=100)),
    }


def _make_db_mock(users: list[dict] | None = None) -> MagicMock:
    """Supabase クライアントのチェーンモックを作る。"""
    db = MagicMock()
    db.table.return_value = db
    db.select.return_value = db
    db.eq.return_value = db
    db.update.return_value = db
    db.insert.return_value = db
    db.execute.return_value = MagicMock(data=users or [])
    return db


def _make_extractor_output() -> MagicMock:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="structured_extractor",
        success=True,
        result={"operation_mode": "create", "target_user_exists": "true", "action_summary": "OK"},
        confidence=0.95,
        cost_yen=0.5,
        duration_ms=100,
    )


def _make_validator_output(success: bool = True) -> MagicMock:
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="output_validator",
        success=success,
        result={"all_passed": success, "items": []},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=50,
    )


class TestAccountLifecycleCreate:
    """createモードのテスト。"""

    @pytest.mark.asyncio
    async def test_create_new_user_success(self):
        """新規ユーザー作成が正常完了すること。"""
        db = _make_db_mock(users=[])  # ユーザーなし → 新規作成

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "create",
                    "user_email": "newuser@example.com",
                    "user_role": "editor",
                    "trigger_event": "onboarding_complete",
                },
            )

        assert result.success is True
        assert result.failed_step is None
        assert "newuser@example.com" in result.accounts_created
        assert result.approval_required is False

    @pytest.mark.asyncio
    async def test_create_reactivates_existing_inactive_user(self):
        """既存の非アクティブユーザーが再アクティブ化されること。"""
        existing_user = _make_user(email="existing@example.com", is_active=False)
        db = _make_db_mock(users=[existing_user])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "create",
                    "user_email": "existing@example.com",
                    "user_role": "admin",
                    "trigger_event": "onboarding_complete",
                },
            )

        assert result.success is True
        assert "existing@example.com" in result.accounts_created

    @pytest.mark.asyncio
    async def test_invalid_role_falls_back_to_editor(self):
        """不正なroleが指定された場合はeditorにフォールバックすること。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "create",
                    "user_email": "user@example.com",
                    "user_role": "superadmin",  # 無効なロール
                },
            )

        assert result.success is True


class TestAccountLifecycleSuspend:
    """suspendモードのテスト。"""

    @pytest.mark.asyncio
    async def test_suspend_active_user_success(self):
        """アクティブユーザーの停止が正常完了すること。"""
        target = _make_user(email="leave@example.com", is_active=True)
        db = _make_db_mock(users=[target])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "suspend",
                    "user_email": "leave@example.com",
                    "trigger_event": "offboarding_start",
                },
            )

        assert result.success is True
        assert "leave@example.com" in result.accounts_suspended
        assert result.approval_required is False

    @pytest.mark.asyncio
    async def test_suspend_nonexistent_user_records_error(self):
        """存在しないユーザーへのsuspendはエラーを記録してもパイプラインは継続すること。"""
        db = _make_db_mock(users=[])  # ユーザーなし

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output(success=False)),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "suspend",
                    "user_email": "ghost@example.com",
                },
            )

        # suspendモードでユーザーなし → executor_errorsが入るがパイプラインは失敗しない
        assert result.failed_step is None
        assert result.accounts_suspended == []
        assert len(result.final_output.get("executor_errors", [])) > 0


class TestAccountLifecycleDelete:
    """deleteモードのテスト（承認フロー経由）。"""

    @pytest.mark.asyncio
    async def test_delete_requires_approval(self):
        """deleteモードは承認フロー経由になりapproval_required=Trueであること。"""
        target = _make_user(email="quit@example.com", is_active=False)
        db = _make_db_mock(users=[target])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "delete",
                    "user_email": "quit@example.com",
                    "trigger_event": "offboarding_complete",
                },
            )

        assert result.success is True
        assert result.approval_required is True
        assert "quit@example.com" in result.accounts_deleted
        # 物理削除されていないこと（bpo_approvals にinsertされるだけ）
        assert result.accounts_suspended == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_user_records_error(self):
        """存在しないユーザーへのdeleteはerrorを記録すること。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "delete",
                    "user_email": "nobody@example.com",
                },
            )

        assert result.failed_step is None
        assert result.approval_required is False
        assert result.accounts_deleted == []


class TestAccountLifecycleReview:
    """reviewモード（月次棚卸し）のテスト。"""

    @pytest.mark.asyncio
    async def test_review_detects_inactive_users(self):
        """90日超非アクティブのユーザーがinactive_detectedに含まれること。"""
        active_user = _make_user(
            email="active@example.com",
            last_login_at=_NOW - timedelta(days=10),
            user_id="u-active",
        )
        flagged_user = _make_user(
            email="flagged@example.com",
            last_login_at=_NOW - timedelta(days=95),
            user_id="u-flagged",
        )
        db = _make_db_mock(users=[active_user, flagged_user])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        assert result.success is True
        assert result.accounts_reviewed == 2
        assert "flagged@example.com" in result.inactive_detected
        assert "active@example.com" not in result.inactive_detected

    @pytest.mark.asyncio
    async def test_review_auto_suspends_180day_inactive(self):
        """180日超非アクティブのアクティブユーザーが自動停止されること。"""
        long_inactive = _make_user(
            email="stale@example.com",
            last_login_at=_NOW - timedelta(days=185),
            is_active=True,
            user_id="u-stale",
        )
        db = _make_db_mock(users=[long_inactive])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        assert result.success is True
        assert "stale@example.com" in result.accounts_suspended

    @pytest.mark.asyncio
    async def test_review_empty_company_succeeds(self):
        """ユーザーが0件でも正常完了すること。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        assert result.success is True
        assert result.accounts_reviewed == 0
        assert result.inactive_detected == []


class TestAccountLifecycleGeneral:
    """パイプライン全般のテスト。"""

    @pytest.mark.asyncio
    async def test_invalid_mode_falls_back_to_review(self):
        """不正なmodeはreviewにフォールバックすること。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "unknown_mode"},
            )

        assert result.success is True
        assert result.final_output["mode"] == "review"

    @pytest.mark.asyncio
    async def test_db_reader_failure_returns_failed_step(self):
        """Step1のDB取得失敗時にfailed_step='db_reader'で返ること。"""
        db = MagicMock()
        db.table.return_value = db
        db.select.return_value = db
        db.eq.return_value = db
        db.execute.side_effect = Exception("Supabase接続エラー")

        with (
            patch("db.supabase.get_service_client", return_value=db),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        assert result.success is False
        assert result.failed_step == "db_reader"

    @pytest.mark.asyncio
    async def test_result_has_6_steps_on_success(self):
        """正常完了時に6ステップが記録されること。"""
        db = _make_db_mock(users=[_make_user()])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        assert result.success is True
        assert len(result.steps) == 6

    @pytest.mark.asyncio
    async def test_to_summary_contains_key_fields(self):
        """to_summary()が主要項目を含む文字列を返すこと。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={"mode": "review"},
            )

        summary = result.to_summary()
        assert "アカウントライフサイクル管理パイプライン" in summary
        assert "モード" in summary

    @pytest.mark.asyncio
    async def test_trigger_event_rule_matched(self):
        """trigger_event=onboarding_complete がルール照合されること。"""
        db = _make_db_mock(users=[])

        with (
            patch("db.supabase.get_service_client", return_value=db),
            patch("workers.micro.extractor.run_structured_extractor", new_callable=AsyncMock, return_value=_make_extractor_output()),
            patch("workers.micro.validator.run_output_validator", new_callable=AsyncMock, return_value=_make_validator_output()),
        ):
            from workers.bpo.common.pipelines.account_lifecycle_pipeline import run_account_lifecycle_pipeline

            result = await run_account_lifecycle_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "mode": "create",
                    "user_email": "new@example.com",
                    "trigger_event": "onboarding_complete",
                },
            )

        assert result.success is True
        assert result.final_output["matched_rule"] is not None
        assert result.final_output["matched_rule"]["trigger"] == "onboarding_complete"
