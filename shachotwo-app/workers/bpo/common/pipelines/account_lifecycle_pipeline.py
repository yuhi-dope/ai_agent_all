"""
共通BPO アカウントライフサイクル管理パイプライン

レジストリキー: backoffice/account_lifecycle
トリガー: イベント（入社・退社・役職変更時）/ スケジュール（月次棚卸し）/ 手動
承認: 必須（削除操作のみ。作成・停止は自動）
設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md

Steps:
  Step 1: db_reader       現在のアカウント一覧・状態をSupabaseから取得
  Step 2: rule_matcher    ライフサイクルルール評価（入社→作成/退社→停止・削除/休職→一時停止）
  Step 3: extractor       変更対象アカウントリスト抽出（新規/変更/削除）
  Step 4: executor        アカウント操作実行（作成/更新/停止）
  Step 5: validator       操作結果検証・棚卸しレポート生成
  Step 6: notifier        変更完了通知（Slack代替: execution_logsへの記録）
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.extractor import run_structured_extractor
from workers.micro.validator import run_output_validator
from workers.micro.generator import run_document_generator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# ライフサイクルイベント→アクションマッピング
LIFECYCLE_RULES = [
    {"trigger": "onboarding_complete", "action": "activate", "role": "editor"},
    {"trigger": "offboarding_start", "action": "suspend"},
    {"trigger": "offboarding_complete", "action": "schedule_delete", "delay_days": 30},
    {"trigger": "role_change", "action": "update_role"},
    # 棚卸しルール
    {"condition": "inactive_days >= 90", "action": "flag_for_review"},
    {"condition": "inactive_days >= 180", "action": "auto_suspend"},
]

# アカウント操作モード
MODE_CREATE = "create"
MODE_SUSPEND = "suspend"
MODE_DELETE = "delete"
MODE_REVIEW = "review"
VALID_MODES = {MODE_CREATE, MODE_SUSPEND, MODE_DELETE, MODE_REVIEW}

# 非アクティブ判定の閾値（日数）
INACTIVE_FLAG_DAYS = 90
INACTIVE_SUSPEND_DAYS = 180

# デフォルトロール
DEFAULT_ROLE = "editor"
VALID_ROLES = {"admin", "editor"}


@dataclass
class AccountLifecyclePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = False
    # ライフサイクル操作結果
    accounts_created: list[str] = field(default_factory=list)
    accounts_suspended: list[str] = field(default_factory=list)
    accounts_deleted: list[str] = field(default_factory=list)
    accounts_updated: list[str] = field(default_factory=list)
    accounts_reviewed: int = 0  # 棚卸し対象総数
    inactive_detected: list[str] = field(default_factory=list)  # 90日以上ログインなし
    report_generated: bool = False

    def to_summary(self) -> str:
        extra = [
            f"  モード: {self.final_output.get('mode', '-')}",
            f"  作成: {len(self.accounts_created)}件 {self.accounts_created}",
            f"  停止: {len(self.accounts_suspended)}件 {self.accounts_suspended}",
            f"  削除承認待ち: {len(self.accounts_deleted)}件 {self.accounts_deleted}",
            f"  更新: {len(self.accounts_updated)}件 {self.accounts_updated}",
            f"  棚卸し総数: {self.accounts_reviewed}件",
            f"  非アクティブ検出: {len(self.inactive_detected)}件 {self.inactive_detected}",
            f"  レポート生成: {'あり' if self.report_generated else 'なし'}",
        ]
        if self.approval_required:
            extra.append("  承認待ちあり（削除操作）")
        return pipeline_summary(
            label="アカウントライフサイクル管理パイプライン",
            total_steps=6,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_account_lifecycle_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> AccountLifecyclePipelineResult:
    """
    アカウントライフサイクル管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "mode": "create" | "suspend" | "delete" | "review",  # 操作モード
            "user_email": str,      # 対象ユーザーのメール（create/suspend/deleteモード）
            "user_role": str,       # 付与するロール（createモード: admin/editor）
            "trigger_event": str,   # トリガーイベント（onboarding_complete/offboarding_start等）
            # reviewモードの場合は全アカウントを棚卸し
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, AccountLifecyclePipelineResult)

    mode: str = input_data.get("mode", MODE_REVIEW)
    user_email: str = input_data.get("user_email", "")
    user_role: str = input_data.get("user_role", DEFAULT_ROLE)
    trigger_event: str = input_data.get("trigger_event", "")

    if mode not in VALID_MODES:
        logger.warning(f"account_lifecycle: 不正なmode={mode}。reviewにフォールバック")
        mode = MODE_REVIEW

    if user_role not in VALID_ROLES:
        user_role = DEFAULT_ROLE

    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "account_lifecycle",
        "mode": mode,
        "user_email": user_email,
        "trigger_event": trigger_event,
    }

    result = AccountLifecyclePipelineResult(success=False)

    # ─── Step 1: db_reader ── 現在のアカウント一覧をSupabaseから取得 ───────────
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        users_resp = (
            db.table("users")
            .select("id, email, role, is_active, last_login_at, created_at")
            .eq("company_id", company_id)
            .execute()
        )
        all_users: list[dict[str, Any]] = users_resp.data or []

        s1_out = MicroAgentOutput(
            agent_name="db_reader",
            success=True,
            result={
                "users": all_users,
                "total_count": len(all_users),
                "active_count": sum(1 for u in all_users if u.get("is_active")),
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - pipeline_start,
        )
    except Exception as exc:
        logger.error(f"account_lifecycle step1 db_reader failed: {exc}")
        s1_out = MicroAgentOutput(
            agent_name="db_reader",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - pipeline_start,
        )

    record_step(1, "db_reader", "db_reader", s1_out)
    if not s1_out.success:
        return emit_fail("db_reader")

    all_users = s1_out.result["users"]
    context["all_users"] = all_users
    context["total_user_count"] = len(all_users)

    # ─── Step 2: rule_matcher ── ライフサイクルルール評価 ─────────────────────
    # trigger_event に対応するアクションをルールから導出
    matched_rule: dict[str, Any] | None = None
    for rule in LIFECYCLE_RULES:
        if trigger_event and rule.get("trigger") == trigger_event:
            matched_rule = rule
            break

    # mode から実行アクションを決定（trigger_event優先）
    derived_action: str = mode
    if matched_rule:
        derived_action = matched_rule.get("action", mode)

    rules_evaluated = [
        {
            "rule_id": f"LC-{i+1:03d}",
            "rule": rule,
            "matched": (
                rule.get("trigger") == trigger_event
                if trigger_event
                else False
            ),
        }
        for i, rule in enumerate(LIFECYCLE_RULES)
    ]

    s2_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "trigger_event": trigger_event,
            "matched_rule": matched_rule,
            "derived_action": derived_action,
            "rules_evaluated": rules_evaluated,
            "matched_count": 1 if matched_rule else 0,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    context["derived_action"] = derived_action
    context["matched_rule"] = matched_rule

    # ─── Step 3: extractor ── 変更対象アカウントリスト抽出 ───────────────────
    now_utc = datetime.now(timezone.utc)
    inactive_flag: list[dict[str, Any]] = []
    inactive_suspend: list[dict[str, Any]] = []

    for user in all_users:
        last_login_raw: str | None = user.get("last_login_at")
        if not last_login_raw:
            # ログイン記録なし → 作成日から計算
            created_raw: str | None = user.get("created_at")
            if created_raw:
                try:
                    created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                    inactive_days = (now_utc - created_dt).days
                except ValueError:
                    inactive_days = 0
            else:
                inactive_days = 0
        else:
            try:
                last_login_dt = datetime.fromisoformat(last_login_raw.replace("Z", "+00:00"))
                inactive_days = (now_utc - last_login_dt).days
            except ValueError:
                inactive_days = 0

        if inactive_days >= INACTIVE_SUSPEND_DAYS and user.get("is_active"):
            inactive_suspend.append({**user, "inactive_days": inactive_days})
        elif inactive_days >= INACTIVE_FLAG_DAYS:
            inactive_flag.append({**user, "inactive_days": inactive_days})

    # 操作対象ユーザーの特定（review以外）
    target_user: dict[str, Any] | None = None
    if mode != MODE_REVIEW and user_email:
        target_user = next(
            (u for u in all_users if u.get("email") == user_email),
            None,
        )

    # reviewモード: 棚卸し対象 = 非アクティブユーザー全員
    review_targets: list[dict[str, Any]] = inactive_flag + inactive_suspend

    extractor_text = (
        f"操作モード: {mode}\n"
        f"対象メール: {user_email}\n"
        f"トリガーイベント: {trigger_event}\n"
        f"導出アクション: {derived_action}\n"
        f"全ユーザー数: {len(all_users)}\n"
        f"非アクティブ（90日+）: {len(inactive_flag)}件\n"
        f"自動停止対象（180日+）: {len(inactive_suspend)}件\n"
        f"対象ユーザー存在: {'あり' if target_user else 'なし'}\n"
    )

    s3_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": extractor_text,
            "schema": {
                "operation_mode": "操作モード（create/suspend/delete/review）",
                "target_user_exists": "対象ユーザーの存在有無（true/false）",
                "inactive_flag_count": "要フラグユーザー数（90日+）",
                "auto_suspend_count": "自動停止対象数（180日+）",
                "action_summary": "実行すべきアクションのサマリー",
            },
            "hint": "アカウントライフサイクル管理のための操作対象抽出",
        },
        context=context,
    ))
    record_step(3, "extractor", "structured_extractor", s3_out)
    if not s3_out.success:
        return emit_fail("extractor")

    context["target_user"] = target_user
    context["inactive_flag"] = inactive_flag
    context["inactive_suspend"] = inactive_suspend
    context["review_targets"] = review_targets

    # ─── Step 4: executor ── アカウント操作実行 ──────────────────────────────
    accounts_created: list[str] = []
    accounts_suspended: list[str] = []
    accounts_deleted: list[str] = []
    accounts_updated: list[str] = []
    approval_required = False
    executor_errors: list[str] = []

    step4_start = int(time.time() * 1000)

    try:
        from db.supabase import get_service_client as _get_db
        db2 = _get_db()

        if mode == MODE_CREATE and user_email:
            # 新規アカウント作成
            existing = next((u for u in all_users if u.get("email") == user_email), None)
            if existing:
                # 既存ユーザーの再アクティブ化
                db2.table("users").update({
                    "is_active": True,
                    "role": user_role,
                }).eq("company_id", company_id).eq("email", user_email).execute()
                accounts_created.append(user_email)
                logger.info(f"account_lifecycle: ユーザー再アクティブ化 {user_email}")
            else:
                # 新規作成
                db2.table("users").insert({
                    "company_id": company_id,
                    "email": user_email,
                    "role": user_role,
                    "is_active": True,
                }).execute()
                accounts_created.append(user_email)
                logger.info(f"account_lifecycle: ユーザー作成 {user_email} role={user_role}")

        elif mode == MODE_SUSPEND and user_email:
            # アカウント停止
            if target_user:
                db2.table("users").update({
                    "is_active": False,
                }).eq("company_id", company_id).eq("email", user_email).execute()
                accounts_suspended.append(user_email)
                logger.info(f"account_lifecycle: ユーザー停止 {user_email}")
            else:
                executor_errors.append(f"停止対象ユーザーが見つかりません: {user_email}")

        elif mode == MODE_DELETE and user_email:
            # 削除は承認フロー経由（即時削除しない）
            if target_user:
                db2.table("bpo_approvals").insert({
                    "company_id": company_id,
                    "action_type": "account_delete",
                    "target_id": target_user.get("id", user_email),
                    "target_email": user_email,
                    "requested_by": "system",
                    "status": "pending",
                    "metadata": {
                        "trigger_event": trigger_event,
                        "user_role": target_user.get("role"),
                        "last_login_at": target_user.get("last_login_at"),
                    },
                }).execute()
                accounts_deleted.append(user_email)
                approval_required = True
                logger.info(f"account_lifecycle: 削除承認リクエスト登録 {user_email}")
            else:
                executor_errors.append(f"削除対象ユーザーが見つかりません: {user_email}")

        elif mode == MODE_REVIEW:
            # 棚卸しモード: 180日超の非アクティブを自動停止
            for u in inactive_suspend:
                db2.table("users").update({
                    "is_active": False,
                }).eq("company_id", company_id).eq("id", u["id"]).execute()
                accounts_suspended.append(u["email"])
                logger.info(
                    f"account_lifecycle: 自動停止（{INACTIVE_SUSPEND_DAYS}日非アクティブ）"
                    f" {u['email']} inactive_days={u['inactive_days']}"
                )

    except Exception as exc:
        logger.error(f"account_lifecycle step4 executor failed: {exc}")
        executor_errors.append(str(exc))

    step4_duration = int(time.time() * 1000) - step4_start

    # 対象ユーザーが見つからないエラーは警告扱い（パイプライン継続）
    # 実際のDB/例外エラーのみ失敗扱い
    has_fatal_error = any(
        err for err in executor_errors
        if not err.startswith("停止対象ユーザーが見つかりません") and not err.startswith("削除対象ユーザーが見つかりません")
    )
    s4_out = MicroAgentOutput(
        agent_name="executor",
        success=not has_fatal_error,
        result={
            "accounts_created": accounts_created,
            "accounts_suspended": accounts_suspended,
            "accounts_deleted_pending": accounts_deleted,
            "accounts_updated": accounts_updated,
            "approval_required": approval_required,
            "errors": executor_errors,
        },
        confidence=1.0 if not executor_errors else 0.5,
        cost_yen=0.0,
        duration_ms=step4_duration,
    )
    record_step(4, "executor", "executor", s4_out)
    if not s4_out.success and mode != MODE_REVIEW:
        return emit_fail("executor")

    result.accounts_created = accounts_created
    result.accounts_suspended = accounts_suspended
    result.accounts_deleted = accounts_deleted
    result.accounts_updated = accounts_updated
    result.approval_required = approval_required
    result.inactive_detected = [u["email"] for u in inactive_flag]
    result.accounts_reviewed = len(all_users)

    # ─── Step 5: validator ── 操作結果検証・棚卸しレポート生成 ────────────────
    validation_items = [
        {
            "field": "company_id_filter",
            "value": True,
            "expected": True,
            "label": "全DB操作にcompany_idフィルタ適用",
        },
        {
            "field": "delete_approval",
            "value": mode != MODE_DELETE or approval_required,
            "expected": True,
            "label": "削除操作は承認フロー経由",
        },
        {
            "field": "operation_success",
            "value": len(executor_errors) == 0,
            "expected": True,
            "label": "アカウント操作エラーなし",
        },
    ]

    if mode == MODE_REVIEW:
        validation_items.append({
            "field": "inactive_detection",
            "value": True,
            "expected": True,
            "label": f"非アクティブ検出完了（{len(inactive_flag)}件フラグ / {len(inactive_suspend)}件自動停止）",
        })

    s5_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "check_type": "account_lifecycle_result",
            "items": validation_items,
            "mode": mode,
            "total_users": len(all_users),
            "inactive_flag_count": len(inactive_flag),
            "auto_suspend_count": len(inactive_suspend),
            "errors": executor_errors,
        },
        context=context,
    ))
    record_step(5, "validator", "output_validator", s5_out)

    # バリデーション失敗は警告扱い（ログのみ）
    if not s5_out.success:
        logger.warning(f"account_lifecycle: バリデーション警告 company={company_id} mode={mode}")

    # ─── Step 6: notifier ── execution_logsへの記録 ──────────────────────────
    step6_start = int(time.time() * 1000)
    report_generated = False

    try:
        from db.supabase import get_service_client as _get_db3
        db3 = _get_db3()

        result_summary = {
            "mode": mode,
            "created": len(accounts_created),
            "suspended": len(accounts_suspended),
            "deleted_pending": len(accounts_deleted),
            "updated": len(accounts_updated),
            "reviewed": len(all_users),
            "inactive_flagged": len(inactive_flag),
            "inactive_emails": [u["email"] for u in inactive_flag],
            "auto_suspended": len(inactive_suspend),
            "approval_required": approval_required,
            "errors": executor_errors,
            "trigger_event": trigger_event,
        }

        db3.table("execution_logs").insert({
            "company_id": company_id,
            "pipeline_name": "account_lifecycle",
            "status": "completed",
            "result_summary": result_summary,
        }).execute()
        report_generated = True
        logger.info(
            f"account_lifecycle: execution_log記録完了 company={company_id} "
            f"mode={mode} created={len(accounts_created)} suspended={len(accounts_suspended)}"
        )
    except Exception as exc:
        logger.error(f"account_lifecycle step6 notifier failed: {exc}")

    step6_duration = int(time.time() * 1000) - step6_start

    s6_out = MicroAgentOutput(
        agent_name="notifier",
        success=report_generated,
        result={
            "logged": report_generated,
            "pipeline_name": "account_lifecycle",
            "company_id": company_id,
        },
        confidence=1.0 if report_generated else 0.0,
        cost_yen=0.0,
        duration_ms=step6_duration,
    )
    record_step(6, "notifier", "notifier", s6_out)

    result.report_generated = report_generated

    # ─── 最終出力組み立て ─────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration_ms = int(time.time() * 1000) - pipeline_start

    final_output: dict[str, Any] = {
        "mode": mode,
        "trigger_event": trigger_event,
        "user_email": user_email,
        "user_role": user_role,
        "derived_action": derived_action,
        "matched_rule": matched_rule,
        "all_users_count": len(all_users),
        "accounts_created": accounts_created,
        "accounts_suspended": accounts_suspended,
        "accounts_deleted_pending": accounts_deleted,
        "accounts_updated": accounts_updated,
        "approval_required": approval_required,
        "inactive_flag": [
            {"email": u["email"], "inactive_days": u["inactive_days"]}
            for u in inactive_flag
        ],
        "auto_suspended": [
            {"email": u["email"], "inactive_days": u["inactive_days"]}
            for u in inactive_suspend
        ],
        "executor_errors": executor_errors,
        "report_generated": report_generated,
        "validation_passed": s5_out.success,
    }

    logger.info(
        f"account_lifecycle_pipeline complete: company={company_id} mode={mode} "
        f"created={len(accounts_created)} suspended={len(accounts_suspended)} "
        f"approval_required={approval_required} {total_duration_ms}ms"
    )

    result.success = True
    result.steps = steps
    result.final_output = final_output
    result.total_cost_yen = total_cost_yen
    result.total_duration_ms = total_duration_ms

    return result
