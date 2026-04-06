"""BPO Manager — EventListener。Webhook/SaaS変更検知からBPOTaskを生成する。"""
import logging
from typing import Any

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 組み込みイベントトリガー定義
# --------------------------------------------------------------------------
# knowledge_items に未登録の企業でも動作するデフォルトトリガー。
# DB に同じ event_type を持つアイテムが存在する場合は DB 側が優先される。
#
# フィールド:
#   event_type       : イベント識別子（完全一致 or 末尾 * でプレフィックス一致）
#   pipeline         : PIPELINE_REGISTRY のキー
#   execution_level  : ExecutionLevel 値（int）
#   estimated_impact : 0〜1
#   input_data       : パイプラインへ渡す追加パラメータ
#   condition        : None or dict（追加条件評価に使う。評価はハンドラ側で行う）
#   description      : 人間向け説明（ログ用）
# --------------------------------------------------------------------------
BUILTIN_EVENT_TRIGGERS: list[dict[str, Any]] = [
    # ── セールスパイプライン ─────────────────────────────────────────────
    {
        # 新規リード登録 → リード資格審査
        "event_type": "lead_created",
        "pipeline": "sales/lead_qualification",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {},
        "condition": None,
        "description": "新規リード登録時のリード資格審査",
    },
    {
        # リードスコア 70 以上 → 提案書自動生成
        # payload に lead_score フィールドが含まれることを前提とする
        "event_type": "lead_score_gte_70",
        "pipeline": "sales/proposal_generation",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {},
        "condition": {"field": "lead_score", "operator": "gte", "value": 70},
        "description": "リードスコア70以上時の提案書自動生成",
    },
    {
        # 提案承認 → 見積・契約書作成
        "event_type": "proposal_accepted",
        "pipeline": "sales/quotation_contract",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.8,
        "input_data": {},
        "condition": None,
        "description": "提案承認時の見積・契約書作成",
    },
    {
        # 契約署名完了 → オンボーディング開始
        "event_type": "contract_signed",
        "pipeline": "sales/customer_lifecycle",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {"mode": "onboarding"},
        "condition": None,
        "description": "契約署名完了時のオンボーディング開始",
    },
    {
        # チケット作成 → サポート自動応答
        "event_type": "ticket_created",
        "pipeline": "sales/support_auto_response",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.5,
        "input_data": {},
        "condition": None,
        "description": "チケット作成時のサポート自動応答",
    },
    {
        # 受注 → 受注/失注フィードバック（受注側）
        "event_type": "opportunity_won",
        "pipeline": "sales/win_loss_feedback",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.6,
        "input_data": {"outcome": "won"},
        "condition": None,
        "description": "受注時の勝因フィードバック収集",
    },
    {
        # 失注 → 受注/失注フィードバック（失注側）
        "event_type": "opportunity_lost",
        "pipeline": "sales/win_loss_feedback",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.6,
        "input_data": {"outcome": "lost"},
        "condition": None,
        "description": "失注時の敗因フィードバック収集",
    },
    {
        # ヘルススコア高 + 未使用モジュールあり → アップセル提案
        # payload に health_score と unused_modules が含まれることを前提とする
        "event_type": "health_score_high",
        "pipeline": "sales/upsell_briefing",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {},
        "condition": {"field": "health_score", "operator": "gte", "value": 80},
        "description": "ヘルススコア80以上+未使用モジュールあり時のアップセル提案",
    },
    {
        # 解約申請 → 解約フロー開始
        "event_type": "cancellation_requested",
        "pipeline": "sales/cancellation",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.9,
        "input_data": {},
        "condition": None,
        "description": "解約申請時の解約フロー（承認必須）",
    },
    # ── バックオフィス イベント ──────────────────────────────────────
    {
        "event_type": "employee_joined",
        "pipeline": "backoffice/employee_onboarding",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {},
        "condition": None,
        "description": "入社時の入社手続きパイプライン発火",
    },
    {
        "event_type": "employee_left",
        "pipeline": "backoffice/employee_offboarding",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {},
        "condition": None,
        "description": "退職時の退社手続きパイプライン発火",
    },
    {
        "event_type": "salary_changed",
        "pipeline": "backoffice/social_insurance",
        "execution_level": ExecutionLevel.APPROVAL_GATED,
        "estimated_impact": 0.7,
        "input_data": {"filing_type": "monthly_change"},
        "condition": None,
        "description": "給与変更時の社保月額変更届",
    },
    {
        "event_type": "vendor_registered",
        "pipeline": "backoffice/antisocial_screening",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {},
        "condition": None,
        "description": "新規取引先登録時の反社チェック",
    },
    {
        "event_type": "purchase_requested",
        "pipeline": "backoffice/purchase_order",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {},
        "condition": None,
        "description": "購買依頼時の発注パイプライン発火",
    },
    {
        "event_type": "goods_received",
        "pipeline": "backoffice/purchase_order",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.5,
        "input_data": {"mode": "inspection"},
        "condition": None,
        "description": "納品時の検収パイプライン発火",
    },
    # ── Gmail Watch イベント ────────────────────────────────────────
    {
        "event_type": "email_reply_interested",
        "pipeline": "sales/lead_qualification",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {"source": "email_reply"},
        "condition": None,
        "description": "メール返信（興味あり）→リード資格審査",
    },
    {
        "event_type": "email_inquiry_new",
        "pipeline": "sales/lead_qualification",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.6,
        "input_data": {"source": "inbound_inquiry"},
        "condition": None,
        "description": "新規問い合わせメール→リード資格審査",
    },
    {
        "event_type": "email_support_request",
        "pipeline": "sales/support_auto_response",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.5,
        "input_data": {"source": "email"},
        "condition": None,
        "description": "サポート問い合わせメール→自動応答",
    },
    # ── Calendar Watch イベント ─────────────────────────────────────
    {
        "event_type": "meeting_ended",
        "pipeline": "sales/customer_lifecycle",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.7,
        "input_data": {"mode": "followup"},
        "condition": None,
        "description": "商談終了検知→フォローアップメール下書き+CRM更新+次アクション",
    },
    {
        "event_type": "meeting_created",
        "pipeline": "sales/proposal_generation",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {"mode": "research"},
        "condition": None,
        "description": "商談イベント作成→企業リサーチ開始",
    },
    {
        "event_type": "email_meeting_request",
        "pipeline": "sales/lead_qualification",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.8,
        "input_data": {"source": "meeting_request"},
        "condition": None,
        "description": "面談リクエストメール→リード資格審査（高優先度）",
    },
]


def _evaluate_condition(condition: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    """
    イベントトリガーの追加条件を評価する。

    condition 例:
      {"field": "lead_score", "operator": "gte", "value": 70}
      {"field": "health_score", "operator": "gte", "value": 80}

    condition が None の場合は常に True を返す。
    """
    if condition is None:
        return True

    field = condition.get("field", "")
    operator = condition.get("operator", "eq")
    expected = condition.get("value")

    actual = payload.get(field)
    if actual is None:
        return False

    try:
        if operator == "eq":
            return actual == expected
        if operator == "gte":
            return float(actual) >= float(expected)
        if operator == "gt":
            return float(actual) > float(expected)
        if operator == "lte":
            return float(actual) <= float(expected)
        if operator == "lt":
            return float(actual) < float(expected)
        if operator == "in":
            return actual in (expected or [])
    except (TypeError, ValueError):
        return False

    return False


def _event_type_matches(trigger_event: str, event_type: str) -> bool:
    """
    イベントタイプの一致判定。
    完全一致 or プレフィックス一致（例: "freee.expense.*"）を評価する。
    """
    if trigger_event == event_type:
        return True
    if trigger_event.endswith("*") and event_type.startswith(trigger_event[:-1]):
        return True
    return False


async def handle_webhook(
    company_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> BPOTask | None:
    """
    受信した Webhook イベントを BPOTask に変換する。

    評価順序:
    1. DB の knowledge_items（企業固有イベントマッピング）
    2. BUILTIN_EVENT_TRIGGERS（組み込みデフォルト）
       - DB に同一 event_type が登録済みの場合は組み込みをスキップ

    event_type 例:
      "freee.expense.created", "smarthr.employee.updated",
      "lead_created", "contract_signed", "cancellation_requested"
    """
    # ── Step 1: DB ファーストでカスタムイベントマッピングを評価 ──────────
    db_registered_event_types: set[str] = set()

    try:
        from db.supabase import get_service_client
        db = get_service_client()

        result = db.table("knowledge_items").select(
            "id, title, metadata, confidence"
        ).eq("company_id", company_id).eq("is_active", True).execute()

        items = result.data or []

        for item in items:
            meta = item.get("metadata") or {}
            if meta.get("trigger_type") != "event":
                continue

            trigger_event = meta.get("event_type", "")
            if not trigger_event:
                continue

            db_registered_event_types.add(trigger_event)

            if _event_type_matches(trigger_event, event_type):
                pipeline = meta.get("pipeline", "")
                if not pipeline:
                    continue

                input_data = {**meta.get("input_data", {}), **payload}

                task = BPOTask(
                    company_id=company_id,
                    pipeline=pipeline,
                    trigger_type=TriggerType.EVENT,
                    execution_level=ExecutionLevel(meta.get("execution_level", 2)),
                    input_data=input_data,
                    estimated_impact=float(item.get("confidence", 0.8)),
                    knowledge_item_ids=[item["id"]],
                )
                logger.info(f"event_listener DB: matched {event_type} → {pipeline}")
                return task

    except Exception as e:
        logger.error(f"event_listener DB error: {e}")
        # DB エラーでも組み込みトリガーの評価を続ける

    # ── Step 2: 組み込みイベントトリガーの評価 ────────────────────────────
    for trigger in BUILTIN_EVENT_TRIGGERS:
        trigger_event = trigger["event_type"]

        # DB に同一 event_type が登録済みなら組み込みをスキップ
        if any(_event_type_matches(registered, event_type) for registered in db_registered_event_types):
            logger.debug(f"event_listener builtin: skipped {event_type} (DB override)")
            return None

        if not _event_type_matches(trigger_event, event_type):
            continue

        # 追加条件の評価（例: lead_score >= 70, health_score >= 80）
        condition = trigger.get("condition")
        if not _evaluate_condition(condition, payload):
            logger.debug(
                f"event_listener builtin: condition not met for {event_type} "
                f"(condition={condition}, payload_keys={list(payload.keys())})"
            )
            continue

        input_data = {**trigger.get("input_data", {}), **payload}

        task = BPOTask(
            company_id=company_id,
            pipeline=trigger["pipeline"],
            trigger_type=TriggerType.EVENT,
            execution_level=ExecutionLevel(trigger["execution_level"]),
            input_data=input_data,
            estimated_impact=float(trigger.get("estimated_impact", 0.5)),
            knowledge_item_ids=[],
            context={
                "builtin": True,
                "description": trigger.get("description", ""),
            },
        )
        logger.info(
            f"event_listener builtin: matched {event_type} → {trigger['pipeline']} "
            f"({trigger.get('description', '')})"
        )
        return task

    logger.debug(f"event_listener: no match for {event_type}")
    return None


async def poll_saas_changes(
    company_id: str,
    service: str,
) -> list[BPOTask]:
    """
    SaaSの変更をポーリングしてBPOTaskを生成する。
    execution_logsの前回ポーリング時刻以降の差分を検出し、
    該当するイベントをhandle_webhookに変換する。

    対応サービス: freee, kintone, slack
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        # 前回のポーリング時刻を取得
        last_run_result = db.table("execution_logs").select(
            "created_at"
        ).eq("company_id", company_id).eq(
            "action_type", f"poll_{service}"
        ).order("created_at", desc=True).limit(1).execute()

        since = None
        if last_run_result.data:
            since = last_run_result.data[0]["created_at"]

        # テナントのコネクタ認証情報を取得
        cred_result = db.table("saas_connections").select(
            "encrypted_credentials"
        ).eq("company_id", company_id).eq("tool_name", service).eq(
            "is_active", True
        ).limit(1).execute()

        if not cred_result.data:
            logger.debug(f"poll_saas_changes: {service} not connected for {company_id[:8]}")
            return []

        encrypted_creds = cred_result.data[0]["encrypted_credentials"]

        # コネクタ経由でデータ取得
        from workers.connector.factory import get_connector
        connector = get_connector(service, encrypted_creds)

        tasks: list[BPOTask] = []
        filters: dict = {}
        if since:
            filters["since"] = since

        # サービス別のポーリングロジック
        if service == "freee":
            records = await connector.read_records("invoices", filters)
            for rec in records:
                status = rec.get("payment_status", "")
                if status == "overdue":
                    task = await handle_webhook(company_id, "freee.invoice.overdue", rec)
                    if task:
                        tasks.append(task)
                elif status == "paid":
                    task = await handle_webhook(company_id, "freee.invoice.paid", rec)
                    if task:
                        tasks.append(task)

        elif service == "kintone":
            records = await connector.read_records("records", filters)
            for rec in records:
                event_type = f"kintone.record.{rec.get('status', 'updated')}"
                task = await handle_webhook(company_id, event_type, rec)
                if task:
                    tasks.append(task)

        elif service == "slack":
            records = await connector.read_records("messages", filters)
            for rec in records:
                # サポートチケットになりうるメッセージを検出
                text = rec.get("text", "")
                if any(kw in text for kw in ["問題", "エラー", "不具合", "help", "質問"]):
                    task = await handle_webhook(company_id, "ticket_created", {
                        "source": "slack",
                        "text": text,
                        "channel": rec.get("channel", ""),
                        "user": rec.get("user", ""),
                        "ts": rec.get("ts", ""),
                    })
                    if task:
                        tasks.append(task)

        # ポーリング記録を保存
        db.table("execution_logs").insert({
            "company_id": company_id,
            "action_type": f"poll_{service}",
            "operations": {"service": service, "records_found": len(tasks)},
        }).execute()

        logger.info(f"poll_saas_changes: {service} for {company_id[:8]} → {len(tasks)} tasks")
        return tasks

    except Exception as e:
        logger.error(f"poll_saas_changes error ({service}): {e}")
        return []
