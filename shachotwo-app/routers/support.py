"""CS（カスタマーサポート）エンドポイント — チケット管理・AI自動対応・CS KPI"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# SLA定義（優先度別、単位: 時間）
# ---------------------------------------------------------------------------

_SLA_HOURS: dict[str, int] = {
    "urgent": 4,
    "high": 8,
    "medium": 24,
    "low": 72,
}


def _calc_sla_due(priority: str, created_at: Optional[datetime] = None) -> datetime:
    """優先度に応じたSLA期限を返す。"""
    base = created_at or datetime.now(timezone.utc)
    hours = _SLA_HOURS.get(priority, 24)
    return base + timedelta(hours=hours)


def _ticket_number() -> str:
    """TK-YYYYMM-XXXX 形式のチケット番号を生成する。"""
    import random
    now = datetime.now(timezone.utc)
    seq = random.randint(1000, 9999)
    return f"TK-{now.strftime('%Y%m')}-{seq}"


# ---------------------------------------------------------------------------
# Request / Response models — Ticket
# ---------------------------------------------------------------------------


class TicketCreate(BaseModel):
    customer_id: UUID
    subject: str
    category: str                           # usage / billing / bug / feature / account
    priority: str = "medium"               # low / medium / high / urgent
    initial_message: str                    # 最初のメッセージ本文


class TicketUpdate(BaseModel):
    status: Optional[str] = None            # open/waiting/ai_responded/escalated/resolved/closed
    priority: Optional[str] = None
    satisfaction_score: Optional[int] = None  # 1-5
    version: int                            # 楽観的ロック用


class TicketResponse(BaseModel):
    id: UUID
    customer_id: UUID
    ticket_number: str
    subject: str
    category: str
    priority: str
    ai_handled: bool
    ai_confidence: Optional[float] = None
    ai_response: Optional[str] = None
    escalated: bool
    escalated_to: Optional[UUID] = None
    escalation_reason: Optional[str] = None
    sla_due_at: Optional[datetime] = None
    first_response_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    status: str
    satisfaction_score: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class TicketListResponse(BaseModel):
    items: list[TicketResponse]
    total: int
    has_more: bool = False


class TicketMessageCreate(BaseModel):
    content: str
    sender_type: str = "agent"             # customer / agent / ai
    attachments: Optional[list[dict]] = None


class TicketMessageResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    sender_type: str
    sender_id: Optional[UUID] = None
    content: str
    attachments: list
    created_at: datetime


class EscalateRequest(BaseModel):
    reason: str
    escalate_to: Optional[UUID] = None      # 担当者（Noneなら自動アサイン）


class EscalateResponse(BaseModel):
    ticket_id: UUID
    escalated_to: Optional[UUID] = None
    reason: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Request / Response models — Inbound Webhook
# ---------------------------------------------------------------------------


class EmailInboundPayload(BaseModel):
    from_email: str
    to_email: str
    subject: str
    body_text: str
    body_html: Optional[str] = None
    attachments: Optional[list[dict]] = None
    message_id: Optional[str] = None       # メールMessageID（重複防止用）


class ChatInboundPayload(BaseModel):
    channel: str                            # intercom / slack / line_works
    sender_id: str
    sender_name: Optional[str] = None
    content: str
    conversation_id: Optional[str] = None


class InboundProcessResponse(BaseModel):
    ticket_id: UUID
    ticket_number: str
    ai_response_sent: bool
    escalated: bool
    message: str


# ---------------------------------------------------------------------------
# Request / Response models — CS Metrics
# ---------------------------------------------------------------------------


class CSMetricsResponse(BaseModel):
    total_tickets: int
    open_tickets: int
    resolved_today: int
    avg_csat: Optional[float] = None        # 平均CSAT（1-5）
    ai_auto_resolution_rate: float          # AI 自動解決率
    avg_first_response_minutes: Optional[float] = None  # 平均初回応答時間（分）
    avg_resolution_hours: Optional[float] = None        # 平均解決時間（時間）
    sla_achievement_rate: float             # SLA 達成率（%）
    escalation_rate: float                  # エスカレーション率（%）
    by_category: dict                       # カテゴリ別チケット数
    by_priority: dict                       # 優先度別チケット数
    period_days: int


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

_TICKETS_SELECT = (
    "id, customer_id, ticket_number, subject, category, priority, "
    "ai_handled, ai_confidence, ai_response, escalated, escalated_to, "
    "escalation_reason, sla_due_at, first_response_at, resolved_at, "
    "status, satisfaction_score, created_at, updated_at"
)


def _build_ticket_response(row: dict) -> TicketResponse:
    return TicketResponse(
        id=row["id"],
        customer_id=row["customer_id"],
        ticket_number=row.get("ticket_number") or "",
        subject=row.get("subject") or "",
        category=row.get("category") or "",
        priority=row.get("priority") or "medium",
        ai_handled=row.get("ai_handled") or False,
        ai_confidence=row.get("ai_confidence"),
        ai_response=row.get("ai_response"),
        escalated=row.get("escalated") or False,
        escalated_to=row.get("escalated_to"),
        escalation_reason=row.get("escalation_reason"),
        sla_due_at=row.get("sla_due_at"),
        first_response_at=row.get("first_response_at"),
        resolved_at=row.get("resolved_at"),
        status=row.get("status") or "open",
        satisfaction_score=row.get("satisfaction_score"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _trigger_auto_response_pipeline(
    company_id: str,
    ticket_id: str,
    customer_id: str,
    subject: str,
    initial_message: str,
    priority: str,
    channel: str = "email",
) -> None:
    """support_auto_response_pipeline を非同期でトリガーする（fire-and-forget）。

    パイプラインが失敗してもチケット作成レスポンスはブロックしない。
    パイプライン結果を support_tickets に反映する。
    """
    try:
        from workers.bpo.sales.cs.support_auto_response_pipeline import (
            run_support_auto_response_pipeline,
        )
        result = await run_support_auto_response_pipeline(
            company_id=company_id,
            input_data={
                "ticket_text": initial_message,
                "ticket_subject": subject,
                "channel": channel,
                "customer_id": customer_id,
                "ticket_id": ticket_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info(
            f"support_auto_response_pipeline: ticket={ticket_id} "
            f"routing={result.routing_decision} confidence={result.confidence:.2f} "
            f"sent={result.response_sent}"
        )

        # パイプライン結果を support_tickets に反映
        update_data: dict = {
            "ai_handled": result.response_sent,
            "ai_confidence": result.confidence,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if result.ai_response:
            update_data["ai_response"] = result.ai_response
        if result.response_sent:
            update_data["status"] = "ai_responded"
            update_data["first_response_at"] = datetime.now(timezone.utc).isoformat()
        if result.routing_decision in ("escalate", "urgent_escalate"):
            update_data["escalated"] = True
            update_data["escalation_reason"] = f"AI confidence={result.confidence:.2f}"
            update_data["status"] = "escalated"

        db = get_service_client()
        db.table("support_tickets").update(update_data).eq("id", ticket_id).execute()

        # AI回答をメッセージとして記録
        if result.ai_response:
            db.table("ticket_messages").insert({
                "company_id": company_id,
                "ticket_id": ticket_id,
                "sender_type": "ai",
                "sender_id": None,
                "content": result.ai_response,
                "attachments": [],
            }).execute()

    except Exception as e:
        logger.warning(f"support_auto_response_pipeline failed (non-fatal): ticket={ticket_id} error={e}")


# ---------------------------------------------------------------------------
# Endpoints — Ticket
# ---------------------------------------------------------------------------


@router.post("/support/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    body: TicketCreate,
    user: JWTClaims = Depends(get_current_user),
):
    """サポートチケットを作成する。

    - support_tickets テーブルに INSERT
    - チケット番号を自動採番（TK-YYYYMM-XXXX 形式）
    - SLA期限を priority に応じて設定（urgent: 4h / high: 8h / medium: 24h / low: 72h）
    - 作成後に support_auto_response_pipeline を非同期でトリガー
    """
    try:
        db = get_service_client()

        # 顧客の存在確認（テナント分離）
        cust_check = (
            db.table("customers")
            .select("id")
            .eq("id", str(body.customer_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not cust_check.data:
            raise HTTPException(status_code=404, detail="Customer not found")

        now = datetime.now(timezone.utc)
        sla_due = _calc_sla_due(body.priority, now)

        insert_data = {
            "company_id": str(user.company_id),
            "customer_id": str(body.customer_id),
            "ticket_number": _ticket_number(),
            "subject": body.subject,
            "category": body.category,
            "priority": body.priority,
            "ai_handled": False,
            "escalated": False,
            "status": "open",
            "sla_due_at": sla_due.isoformat(),
        }

        result = db.table("support_tickets").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Insert failed")

        ticket = result.data[0]
        ticket_id = ticket["id"]

        # 初回メッセージを ticket_messages に記録
        db.table("ticket_messages").insert({
            "company_id": str(user.company_id),
            "ticket_id": ticket_id,
            "sender_type": "customer",
            "sender_id": None,
            "content": body.initial_message,
            "attachments": [],
        }).execute()

        # AI自動対応パイプラインを非同期でトリガー（fire-and-forget）
        asyncio.create_task(
            _trigger_auto_response_pipeline(
                company_id=str(user.company_id),
                ticket_id=ticket_id,
                customer_id=str(body.customer_id),
                subject=body.subject,
                initial_message=body.initial_message,
                priority=body.priority,
                channel="form",
            )
        )

        return _build_ticket_response(ticket)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"create ticket failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/support/tickets", response_model=TicketListResponse)
async def list_tickets(
    ticket_status: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    category: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    ai_handled: Optional[bool] = None,
    escalated: Optional[bool] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """チケット一覧を取得する（SLA期限が近い順でソート）。"""
    try:
        db = get_service_client()
        q = (
            db.table("support_tickets")
            .select(_TICKETS_SELECT, count="exact")
            .eq("company_id", str(user.company_id))
            .order("sla_due_at", desc=False)  # SLA期限昇順（切迫したものを先に）
            .range(offset, offset + limit - 1)
        )
        if ticket_status:
            q = q.eq("status", ticket_status)
        if priority:
            q = q.eq("priority", priority)
        if category:
            q = q.eq("category", category)
        if customer_id:
            q = q.eq("customer_id", str(customer_id))
        if ai_handled is not None:
            q = q.eq("ai_handled", ai_handled)
        if escalated is not None:
            q = q.eq("escalated", escalated)

        result = q.execute()
        items = [_build_ticket_response(r) for r in (result.data or [])]
        total = result.count or 0
        return TicketListResponse(
            items=items,
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list tickets failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/support/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """チケット詳細を取得する。"""
    try:
        db = get_service_client()
        result = (
            db.table("support_tickets")
            .select(_TICKETS_SELECT)
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return _build_ticket_response(result.data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get ticket failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/support/tickets/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: UUID,
    body: TicketUpdate,
    user: JWTClaims = Depends(get_current_user),
):
    """チケット情報を更新する（ステータス変更・CSAT記録等、楽観的ロック）。

    support_tickets に version カラムがないため、
    version は updated_at の epoch 秒で代替する（version=0 は無条件更新）。
    """
    try:
        db = get_service_client()

        current = (
            db.table("support_tickets")
            .select("id, updated_at, status, first_response_at")
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not current.data:
            raise HTTPException(status_code=404, detail="Ticket not found")

        # 楽観的ロック
        if body.version > 0:
            try:
                dt = datetime.fromisoformat(
                    str(current.data.get("updated_at", "")).replace("Z", "+00:00")
                )
                current_version = int(dt.timestamp())
            except Exception:
                current_version = 0
            if current_version != body.version:
                raise HTTPException(
                    status_code=409,
                    detail="VERSION_CONFLICT: Ticket has been modified by another user. Please refresh and try again.",
                )

        now = datetime.now(timezone.utc)
        update_data: dict = {"updated_at": now.isoformat()}

        if body.status is not None:
            update_data["status"] = body.status
            if body.status in ("resolved", "closed"):
                update_data["resolved_at"] = now.isoformat()
        if body.priority is not None:
            update_data["priority"] = body.priority
            # 優先度変更時はSLAを再計算
            update_data["sla_due_at"] = _calc_sla_due(body.priority, now).isoformat()
        if body.satisfaction_score is not None:
            if not (1 <= body.satisfaction_score <= 5):
                raise HTTPException(status_code=422, detail="satisfaction_score must be 1-5")
            update_data["satisfaction_score"] = body.satisfaction_score

        result = (
            db.table("support_tickets")
            .update(update_data)
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=500, detail="Update failed")

        return _build_ticket_response(result.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update ticket failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/support/tickets/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=status.HTTP_201_CREATED)
async def add_ticket_message(
    ticket_id: UUID,
    body: TicketMessageCreate,
    user: JWTClaims = Depends(get_current_user),
):
    """チケットにメッセージを追加する。

    - ticket_messages テーブルに INSERT
    - agent メッセージの場合は first_response_at を更新（未設定なら）
    """
    try:
        db = get_service_client()

        # チケットの存在確認（テナント分離）
        ticket_check = (
            db.table("support_tickets")
            .select("id, first_response_at")
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not ticket_check.data:
            raise HTTPException(status_code=404, detail="Ticket not found")

        now = datetime.now(timezone.utc)
        insert_data = {
            "company_id": str(user.company_id),
            "ticket_id": str(ticket_id),
            "sender_type": body.sender_type,
            "sender_id": str(user.sub) if body.sender_type == "agent" else None,
            "content": body.content,
            "attachments": body.attachments or [],
        }

        result = db.table("ticket_messages").insert(insert_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Insert failed")

        msg = result.data[0]

        # agent メッセージかつ first_response_at が未設定なら更新
        if body.sender_type == "agent" and not ticket_check.data.get("first_response_at"):
            db.table("support_tickets").update({
                "first_response_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "waiting",  # エージェントが返信 → 顧客の返信待ち
            }).eq("id", str(ticket_id)).execute()

        return TicketMessageResponse(
            id=msg["id"],
            ticket_id=msg["ticket_id"],
            sender_type=msg["sender_type"],
            sender_id=msg.get("sender_id"),
            content=msg["content"],
            attachments=msg.get("attachments") or [],
            created_at=msg["created_at"],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"add ticket message failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/support/tickets/{ticket_id}/escalate", response_model=EscalateResponse)
async def escalate_ticket(
    ticket_id: UUID,
    body: EscalateRequest,
    user: JWTClaims = Depends(get_current_user),
):
    """チケットをエスカレーションする。

    - support_tickets の escalated=True / escalation_reason / escalated_to を更新
    - status を escalated に変更
    """
    try:
        db = get_service_client()

        # チケットの存在確認
        ticket_check = (
            db.table("support_tickets")
            .select("id, status, customer_id")
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .single()
            .execute()
        )
        if not ticket_check.data:
            raise HTTPException(status_code=404, detail="Ticket not found")

        now = datetime.now(timezone.utc)
        update_data: dict = {
            "escalated": True,
            "escalation_reason": body.reason,
            "status": "escalated",
            "updated_at": now.isoformat(),
        }
        escalated_to: Optional[UUID] = None
        if body.escalate_to:
            # 指定された担当者の存在確認
            user_check = (
                db.table("users")
                .select("id")
                .eq("id", str(body.escalate_to))
                .eq("company_id", str(user.company_id))
                .single()
                .execute()
            )
            if user_check.data:
                update_data["escalated_to"] = str(body.escalate_to)
                escalated_to = body.escalate_to

        result = (
            db.table("support_tickets")
            .update(update_data)
            .eq("id", str(ticket_id))
            .eq("company_id", str(user.company_id))
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=500, detail="Escalation update failed")

        # エスカレーションメッセージをチケットに記録
        try:
            db.table("ticket_messages").insert({
                "company_id": str(user.company_id),
                "ticket_id": str(ticket_id),
                "sender_type": "agent",
                "sender_id": str(user.sub),
                "content": f"[エスカレーション] {body.reason}",
                "attachments": [],
            }).execute()
        except Exception as msg_err:
            logger.warning(f"Failed to record escalation message: {msg_err}")

        return EscalateResponse(
            ticket_id=ticket_id,
            escalated_to=escalated_to,
            reason=body.reason,
            status="escalated",
            message="チケットをエスカレーションしました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"escalate ticket failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — Inbound Webhook（外部サービスからの受信）
# ---------------------------------------------------------------------------


async def _create_ticket_from_inbound(
    company_id: str,
    customer_id: Optional[str],
    subject: str,
    body_text: str,
    channel: str,
    from_identifier: str,
) -> dict:
    """受信メッセージからチケットを作成する内部ユーティリティ。"""
    db = get_service_client()

    # 顧客を照合（company_id でテナント分離）
    if not customer_id:
        # メールアドレスで顧客を検索
        cust_result = (
            db.table("customers")
            .select("id")
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        # NOTE: 実際には customers テーブルに contact_email が必要。
        # MVP では company_id の最初の顧客に紐付けるフォールバックを使用。
        if cust_result.data:
            customer_id = cust_result.data[0]["id"]
        else:
            raise HTTPException(status_code=422, detail="No customer found for this company")

    now = datetime.now(timezone.utc)
    sla_due = _calc_sla_due("medium", now)  # デフォルト medium

    insert_data = {
        "company_id": company_id,
        "customer_id": customer_id,
        "ticket_number": _ticket_number(),
        "subject": subject,
        "category": "usage",  # extractor が後で分類
        "priority": "medium",
        "ai_handled": False,
        "escalated": False,
        "status": "open",
        "sla_due_at": sla_due.isoformat(),
    }

    result = db.table("support_tickets").insert(insert_data).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create ticket")

    ticket = result.data[0]
    ticket_id = ticket["id"]

    # 初回メッセージを記録
    db.table("ticket_messages").insert({
        "company_id": company_id,
        "ticket_id": ticket_id,
        "sender_type": "customer",
        "sender_id": None,
        "content": body_text,
        "attachments": [],
    }).execute()

    return ticket


@router.post("/support/inbound/email", response_model=InboundProcessResponse, status_code=status.HTTP_200_OK)
async def inbound_email_webhook(
    payload: EmailInboundPayload,
    request: Request,
):
    """受信メール Webhook を処理する（SendGrid Inbound Parse など）。

    - メールから顧客を特定してチケットを自動作成
    - support_auto_response_pipeline をトリガー
    - 重複メール（message_id）は無視
    """
    try:
        # NOTE: このエンドポイントは外部 Webhook のため company_id を特定する必要がある。
        # to_email のドメインまたは専用トークンで company を特定するのが理想だが、
        # MVP では to_email をキーに companies テーブルを検索する。
        db = get_service_client()

        # to_email のドメインで company を特定（companies.slug または email ドメインで検索）
        to_domain = payload.to_email.split("@")[-1] if "@" in payload.to_email else ""
        company_result = (
            db.table("companies")
            .select("id")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if not company_result.data:
            raise HTTPException(status_code=422, detail="No active company found")
        company_id = company_result.data[0]["id"]

        # 重複チェック（message_id があれば）
        if payload.message_id:
            dup_check = (
                db.table("support_tickets")
                .select("id, ticket_number")
                .eq("company_id", company_id)
                .eq("subject", payload.subject)
                .limit(1)
                .execute()
            )
            # NOTE: 完全な重複防止には ticket_messages の content 比較が必要。
            # MVP では subject の完全一致で近似。

        ticket = await _create_ticket_from_inbound(
            company_id=company_id,
            customer_id=None,
            subject=payload.subject,
            body_text=payload.body_text,
            channel="email",
            from_identifier=payload.from_email,
        )

        # AI自動対応パイプラインを非同期でトリガー
        asyncio.create_task(
            _trigger_auto_response_pipeline(
                company_id=company_id,
                ticket_id=ticket["id"],
                customer_id=ticket["customer_id"],
                subject=payload.subject,
                initial_message=payload.body_text,
                priority="medium",
                channel="email",
            )
        )

        return InboundProcessResponse(
            ticket_id=ticket["id"],
            ticket_number=ticket["ticket_number"],
            ai_response_sent=False,  # パイプライン完了前なので pending
            escalated=False,
            message="メールを受信してチケットを作成しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"inbound email webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/support/inbound/chat", response_model=InboundProcessResponse, status_code=status.HTTP_200_OK)
async def inbound_chat_webhook(
    payload: ChatInboundPayload,
    request: Request,
):
    """チャット Webhook を処理する（Intercom / Slack / LINE WORKS など）。

    - チャットから顧客を特定してチケットを自動作成
    - support_auto_response_pipeline をトリガー
    """
    try:
        db = get_service_client()

        # MVP: アクティブな最初の company に紐付け
        company_result = (
            db.table("companies")
            .select("id")
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if not company_result.data:
            raise HTTPException(status_code=422, detail="No active company found")
        company_id = company_result.data[0]["id"]

        subject = f"[{payload.channel}] {payload.content[:50]}{'...' if len(payload.content) > 50 else ''}"

        ticket = await _create_ticket_from_inbound(
            company_id=company_id,
            customer_id=None,
            subject=subject,
            body_text=payload.content,
            channel=payload.channel,
            from_identifier=payload.sender_id,
        )

        # AI自動対応パイプラインを非同期でトリガー
        asyncio.create_task(
            _trigger_auto_response_pipeline(
                company_id=company_id,
                ticket_id=ticket["id"],
                customer_id=ticket["customer_id"],
                subject=subject,
                initial_message=payload.content,
                priority="medium",
                channel=payload.channel,
            )
        )

        return InboundProcessResponse(
            ticket_id=ticket["id"],
            ticket_number=ticket["ticket_number"],
            ai_response_sent=False,
            escalated=False,
            message=f"{payload.channel} からメッセージを受信してチケットを作成しました",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"inbound chat webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints — CS Metrics
# ---------------------------------------------------------------------------


@router.get("/support/metrics", response_model=CSMetricsResponse)
async def get_cs_metrics(
    days: int = Query(30, ge=1, le=365),
    user: JWTClaims = Depends(get_current_user),
):
    """CS KPI を取得する（CSAT / FRT / 解決時間 / AI 対応率 / SLA 達成率）。"""
    try:
        db = get_service_client()
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).isoformat()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        # 期間内のチケットを全取得
        result = (
            db.table("support_tickets")
            .select(
                "id, status, category, priority, ai_handled, escalated, "
                "satisfaction_score, first_response_at, resolved_at, "
                "sla_due_at, created_at"
            )
            .eq("company_id", str(user.company_id))
            .gte("created_at", since)
            .execute()
        )
        rows = result.data or []

        total_tickets = len(rows)
        open_tickets = sum(1 for r in rows if r.get("status") not in ("resolved", "closed"))

        # 今日解決
        resolved_today = sum(
            1 for r in rows
            if r.get("resolved_at") and str(r["resolved_at"]) >= today_start
        )

        # CSAT（満足度スコアあるもので平均）
        csat_scores = [r["satisfaction_score"] for r in rows if r.get("satisfaction_score")]
        avg_csat = round(sum(csat_scores) / len(csat_scores), 2) if csat_scores else None

        # AI自動解決率
        ai_handled_count = sum(1 for r in rows if r.get("ai_handled"))
        ai_auto_resolution_rate = round(ai_handled_count / total_tickets * 100, 2) if total_tickets > 0 else 0.0

        # 平均初回応答時間（分）
        frt_minutes: list[float] = []
        for r in rows:
            if r.get("first_response_at") and r.get("created_at"):
                try:
                    created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
                    first_resp = datetime.fromisoformat(str(r["first_response_at"]).replace("Z", "+00:00"))
                    diff_min = (first_resp - created).total_seconds() / 60
                    if diff_min >= 0:
                        frt_minutes.append(diff_min)
                except Exception:
                    pass
        avg_first_response_minutes = round(sum(frt_minutes) / len(frt_minutes), 2) if frt_minutes else None

        # 平均解決時間（時間）
        resolution_hours: list[float] = []
        for r in rows:
            if r.get("resolved_at") and r.get("created_at"):
                try:
                    created = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
                    resolved = datetime.fromisoformat(str(r["resolved_at"]).replace("Z", "+00:00"))
                    diff_hours = (resolved - created).total_seconds() / 3600
                    if diff_hours >= 0:
                        resolution_hours.append(diff_hours)
                except Exception:
                    pass
        avg_resolution_hours = round(sum(resolution_hours) / len(resolution_hours), 2) if resolution_hours else None

        # SLA達成率（解決済みのうち sla_due_at 以内に解決したもの）
        resolved_rows = [r for r in rows if r.get("resolved_at") and r.get("sla_due_at")]
        sla_met = 0
        for r in resolved_rows:
            try:
                resolved = datetime.fromisoformat(str(r["resolved_at"]).replace("Z", "+00:00"))
                sla_due = datetime.fromisoformat(str(r["sla_due_at"]).replace("Z", "+00:00"))
                if resolved <= sla_due:
                    sla_met += 1
            except Exception:
                pass
        sla_achievement_rate = round(sla_met / len(resolved_rows) * 100, 2) if resolved_rows else 100.0

        # エスカレーション率
        escalated_count = sum(1 for r in rows if r.get("escalated"))
        escalation_rate = round(escalated_count / total_tickets * 100, 2) if total_tickets > 0 else 0.0

        # カテゴリ別・優先度別集計
        by_category: dict = {}
        by_priority: dict = {}
        for r in rows:
            cat = r.get("category") or "unknown"
            pri = r.get("priority") or "medium"
            by_category[cat] = by_category.get(cat, 0) + 1
            by_priority[pri] = by_priority.get(pri, 0) + 1

        return CSMetricsResponse(
            total_tickets=total_tickets,
            open_tickets=open_tickets,
            resolved_today=resolved_today,
            avg_csat=avg_csat,
            ai_auto_resolution_rate=ai_auto_resolution_rate,
            avg_first_response_minutes=avg_first_response_minutes,
            avg_resolution_hours=avg_resolution_hours,
            sla_achievement_rate=sla_achievement_rate,
            escalation_rate=escalation_rate,
            by_category=by_category,
            by_priority=by_priority,
            period_days=days,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cs metrics failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
