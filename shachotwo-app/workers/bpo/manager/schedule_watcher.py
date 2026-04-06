"""BPO Manager — ScheduleWatcher。Cronベースのトリガーを評価する。"""
import logging
from datetime import datetime, timezone
from typing import Any

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 組み込みスケジュールトリガー定義
# --------------------------------------------------------------------------
# knowledge_items に未登録の企業でも動作するデフォルトトリガー。
# DBに同じ pipeline + cron_expr を持つアイテムが存在する場合は DB 側が優先される
# （scan_schedule_triggers の DB ファースト実装によって重複実行はない）。
#
# フィールド:
#   cron_expr        : "分 時 日 月 曜日" (weekday: 0=月曜)
#   pipeline         : PIPELINE_REGISTRY のキー
#   execution_level  : ExecutionLevel 値（int）
#   estimated_impact : 0〜1
#   input_data       : パイプラインへ渡す追加パラメータ
#   description      : 人間向け説明（ログ用）
# --------------------------------------------------------------------------
BUILTIN_SCHEDULE_TRIGGERS: list[dict[str, Any]] = [
    # ── セールス・CS パイプライン ──────────────────────────────────────────
    {
        # 毎日 08:00 — 企業リサーチ & アウトリーチ 400 件/日
        "cron_expr": "0 8 * * *",
        "pipeline": "sales/outreach",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {"daily_limit": 400},
        "description": "企業リサーチ&アウトリーチ日次実行（400件/日）",
    },
    {
        # 毎日 09:00 — ヘルススコア日次計算
        "cron_expr": "0 9 * * *",
        "pipeline": "sales/customer_lifecycle",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {"mode": "health_check"},
        "description": "顧客ヘルススコア日次計算",
    },
    {
        # 毎日 10:00 — SLA 違反チェック
        "cron_expr": "0 10 * * *",
        "pipeline": "sales/support_auto_response",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.7,
        "input_data": {"mode": "sla_check"},
        "description": "SLA違反チェック日次スキャン",
    },
    {
        # 毎月1日 09:00 — MRR/チャーンレポート生成
        "cron_expr": "0 9 1 * *",
        "pipeline": "sales/revenue_report",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.6,
        "input_data": {"mode": "revenue"},
        "description": "MRR/チャーンレポート月次生成（毎月1日）",
    },
    {
        # 毎月15日 09:00 — 要望ランキング更新
        "cron_expr": "0 9 15 * *",
        "pipeline": "sales/revenue_report",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.4,
        "input_data": {"mode": "request"},
        "description": "要望ランキング更新（毎月15日）",
    },
    {
        # 毎週月曜 09:00 — アウトリーチ PDCA
        # cron_expr weekday: 0 = 月曜（Python weekday() に合わせた独自定義）
        "cron_expr": "0 9 * * 0",
        "pipeline": "sales/win_loss_feedback",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.5,
        "input_data": {"mode": "outreach_pdca"},
        "description": "アウトリーチPDCA週次レビュー（毎週月曜）",
    },
    {
        # 毎月末 09:00 — CS 品質月次レビュー
        # 月末は「日=28〜31」では確実ではないため "L" 相当として
        # _matches_cron_last_day() で別途処理する（後述）。
        # ここでは sentinel cron として "0 9 28-31 * *" を使い、
        # 実際の最終日判定は input_data の "last_day_only": True フラグで行う。
        "cron_expr": "0 9 28-31 * *",
        "pipeline": "sales/cs_feedback",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {"last_day_only": True},
        "description": "CS品質月次レビュー（毎月末）",
    },
    # ── バックオフィス スケジュール ─────────────────────────────────
    {
        "cron_expr": "0 9 * * *",
        "pipeline": "backoffice/ar_management",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {},
        "description": "売掛管理・入金消込日次スキャン",
    },
    {
        "cron_expr": "0 18 * * *",
        "pipeline": "backoffice/bank_reconciliation",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {},
        "description": "銀行照合日次",
    },
    {
        "cron_expr": "0 9 25 * *",
        "pipeline": "common/payroll",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.8,
        "input_data": {},
        "description": "給与計算月次（25日）",
    },
    {
        "cron_expr": "0 9 25 * *",
        "pipeline": "backoffice/ap_management",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.8,
        "input_data": {},
        "description": "買掛支払処理（月末5日前）",
    },
    {
        "cron_expr": "0 9 28-31 * *",
        "pipeline": "backoffice/invoice_issue",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.7,
        "input_data": {"last_day_only": True},
        "description": "請求書発行月末",
    },
    {
        "cron_expr": "0 9 5 * *",
        "pipeline": "backoffice/monthly_close",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.8,
        "input_data": {},
        "description": "月次決算（5営業日目）",
    },
    {
        "cron_expr": "0 9 1 * *",
        "pipeline": "backoffice/labor_compliance",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.6,
        "input_data": {},
        "description": "労務コンプライアンス月次チェック",
    },
    {
        "cron_expr": "0 9 1 * *",
        "pipeline": "backoffice/compliance_check",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.6,
        "input_data": {},
        "description": "コンプライアンスチェック月次",
    },
    # ── Google Workspace 同期 ──────────────────────────────────────
    {
        # 毎日 07:00 — Gmail/Calendar Watch有効期限チェック＆更新
        "cron_expr": "0 7 * * *",
        "pipeline": "internal/gws_watch_renewal",
        "execution_level": ExecutionLevel.AUTONOMOUS,
        "estimated_impact": 0.3,
        "input_data": {},
        "description": "Gmail/Calendar Watch有効期限の自動更新",
    },
    {
        # 毎日 20:00 — DB→GWS逆同期（日次バッチ）
        "cron_expr": "0 20 * * *",
        "pipeline": "internal/gws_reverse_sync",
        "execution_level": ExecutionLevel.AUTONOMOUS,
        "estimated_impact": 0.4,
        "input_data": {},
        "description": "パイプライン結果のGoogle Workspace逆同期（日次）",
    },
    {
        # 毎時15分 — 保留中・失敗GWS同期レコードのリトライ（指数バックオフ付き）
        "cron_expr": "0 * * * *",
        "pipeline": "internal/gws_pending_sync",
        "execution_level": ExecutionLevel.AUTONOMOUS,
        "estimated_impact": 0.3,
        "input_data": {},
        "description": "GWS逆同期失敗レコードの毎時リトライ（pending/failed→synced）",
    },
    {
        # 毎日 21:00 — パイプライン精度監視（日次）
        "cron_expr": "0 21 * * *",
        "pipeline": "internal/accuracy_check",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.3,
        "input_data": {},
        "description": "パイプライン精度監視（日次）",
    },
    {
        # 毎週日曜 03:00 — プロンプト改善サイクル（confidence >= 0.8 の提案のみ自動適用）
        # cron_expr weekday: 6 = 日曜（Python weekday() に合わせた独自定義）
        "cron_expr": "0 3 * * 6",
        "pipeline": "internal/improvement_cycle",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.5,
        "input_data": {},
        "description": "プロンプト改善サイクル（週次）",
    },
]


def _is_last_day_of_month(now: datetime) -> bool:
    """現在日時が月末かどうかを判定する。"""
    import calendar
    last_day = calendar.monthrange(now.year, now.month)[1]
    return now.day == last_day


def _matches_cron(cron_expr: str, now: datetime) -> bool:
    """
    簡易Cron評価（croniterがない場合のフォールバック）。
    書式: "分 時 日 月 曜日"（* はワイルドカード）
    例: "0 9 25 * *" → 毎月25日9時0分
    """
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        minute, hour, day, month, weekday = parts

        def match_field(field: str, value: int) -> bool:
            if field == "*":
                return True
            if "," in field:
                return value in [int(x) for x in field.split(",")]
            if "-" in field:
                start, end = field.split("-")
                return int(start) <= value <= int(end)
            return int(field) == value

        return (
            match_field(minute, now.minute)
            and match_field(hour, now.hour)
            and match_field(day, now.day)
            and match_field(month, now.month)
            and match_field(weekday, now.weekday())
        )
    except Exception:
        return False


async def scan_schedule_triggers(company_id: str) -> list[BPOTask]:
    """
    knowledge_items の metadata に cron_expr があるアイテムを評価し、
    現在時刻と一致する BPOTask リストを返す。

    評価順序:
    1. DB の knowledge_items（カスタムスケジュール、企業固有設定）
    2. BUILTIN_SCHEDULE_TRIGGERS（組み込みデフォルト）
       - DB に同一 pipeline + cron_expr が存在する場合は組み込みをスキップ（重複防止）

    knowledge_item の metadata 例:
    {"trigger_type": "schedule", "cron_expr": "0 9 25 * *", "pipeline": "common/payroll"}
    """
    now = datetime.now(timezone.utc)
    tasks: list[BPOTask] = []

    # ── Step 1: DB ファーストでカスタムスケジュールを評価 ──────────────────
    # DB に登録済みの pipeline キーを追跡して組み込みとの重複を防ぐ
    db_registered_pipelines: set[str] = set()

    try:
        from db.supabase import get_service_client
        db = get_service_client()

        result = db.table("knowledge_items").select(
            "id, title, metadata, confidence"
        ).eq("company_id", company_id).eq("is_active", True).execute()

        items = result.data or []

        for item in items:
            meta = item.get("metadata") or {}
            if meta.get("trigger_type") != "schedule":
                continue

            cron_expr = meta.get("cron_expr", "")
            pipeline = meta.get("pipeline", "")
            if not cron_expr or not pipeline:
                continue

            db_registered_pipelines.add(pipeline)

            if _matches_cron(cron_expr, now):
                tasks.append(BPOTask(
                    company_id=company_id,
                    pipeline=pipeline,
                    trigger_type=TriggerType.SCHEDULE,
                    execution_level=ExecutionLevel(meta.get("execution_level", 2)),
                    input_data=meta.get("input_data", {}),
                    estimated_impact=float(item.get("confidence", 0.8)),
                    knowledge_item_ids=[item["id"]],
                ))

        logger.debug(f"schedule_watcher DB: {len(tasks)} tasks for {company_id}")

    except Exception as e:
        logger.error(f"schedule_watcher DB error: {e}")
        # DB エラーでも組み込みトリガーは評価を続ける

    # ── Step 2: 組み込みスケジュールトリガーの評価 ─────────────────────────
    builtin_tasks: list[BPOTask] = []
    for trigger in BUILTIN_SCHEDULE_TRIGGERS:
        pipeline = trigger["pipeline"]

        # DB に同一パイプラインが登録済みなら組み込みをスキップ
        if pipeline in db_registered_pipelines:
            continue

        cron_expr = trigger["cron_expr"]
        input_data = dict(trigger.get("input_data", {}))

        # 月末限定フラグの処理: last_day_only=True の場合は月末のみ発火
        if input_data.get("last_day_only") and not _is_last_day_of_month(now):
            continue

        if _matches_cron(cron_expr, now):
            builtin_tasks.append(BPOTask(
                company_id=company_id,
                pipeline=pipeline,
                trigger_type=TriggerType.SCHEDULE,
                execution_level=ExecutionLevel(trigger["execution_level"]),
                input_data=input_data,
                estimated_impact=float(trigger.get("estimated_impact", 0.5)),
                knowledge_item_ids=[],  # 組み込みトリガーはknowledge_item不要
                context={"builtin": True, "description": trigger.get("description", "")},
            ))

    if builtin_tasks:
        logger.info(
            f"schedule_watcher builtin: {len(builtin_tasks)} triggers fired "
            f"({[t.pipeline for t in builtin_tasks]}) for {company_id}"
        )

    tasks.extend(builtin_tasks)
    logger.info(f"schedule_watcher: total {len(tasks)} tasks for {company_id}")
    return tasks
