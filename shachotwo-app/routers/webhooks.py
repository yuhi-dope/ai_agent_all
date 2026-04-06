"""外部 SaaS Webhook 受信エンドポイント — CloudSign / freee / Intercom"""
import logging
import hmac
import hashlib
import os
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel

from db.supabase import get_service_client
from db import crud_sales

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models — CloudSign Webhook
# ---------------------------------------------------------------------------


class CloudSignWebhookPayload(BaseModel):
    """CloudSign 署名完了 Webhook ペイロード"""
    event_type: str                         # document.signed / document.rejected / document.expired
    document_id: str
    document_title: Optional[str] = None
    signed_at: Optional[str] = None
    signer_email: Optional[str] = None
    # カスタムメタデータ（署名依頼作成時に設定）
    contract_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Request / Response models — freee Webhook
# ---------------------------------------------------------------------------


class FreeeWebhookPayload(BaseModel):
    """freee Webhook ペイロード（請求書入金通知等）"""
    event_type: str                         # invoice.paid / invoice.overdue / deal.created
    company_id: int                         # freee 社内ID（シャチョツーの company_id ではない）
    payload: dict                           # イベント詳細（freee のドキュメント参照）


# ---------------------------------------------------------------------------
# Request / Response models — Intercom Webhook
# ---------------------------------------------------------------------------


class IntercomWebhookPayload(BaseModel):
    """Intercom チャットメッセージ Webhook ペイロード（オプション機能）"""
    type: str                               # notification_event / ping
    topic: Optional[str] = None            # conversation.user.created / conversation.user.replied 等
    data: Optional[dict] = None
    delivery_attempts: Optional[int] = None


class WebhookResponse(BaseModel):
    received: bool = True
    message: str = "ok"


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _verify_hmac_sha256(secret: str, body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 シグネチャを検証する。"""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/webhooks/cloudsign", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def cloudsign_webhook(
    request: Request,
    x_cloudsign_signature: Optional[str] = Header(None),
):
    """CloudSign 電子署名完了 Webhook を受信する。

    処理フロー（署名完了時）:
    1. contracts.status = "signed" / signed_at を更新
    2. contracts テーブルから opportunity_id を取得
    3. opportunities.stage = "won" に更新
    4. customers テーブルにレコードを自動作成
    5. consent_flow パイプラインで後続処理をトリガー
    6. Slack に受注祝い通知
    """
    try:
        raw_body = await request.body()
        body = await request.json()
        payload = CloudSignWebhookPayload(**body)

        # CloudSign Webhook シグネチャ検証
        secret = os.environ.get("CLOUDSIGN_WEBHOOK_SECRET", "")
        if secret and x_cloudsign_signature:
            if not _verify_hmac_sha256(secret, raw_body, x_cloudsign_signature):
                raise HTTPException(status_code=401, detail="Invalid signature")

        contract_id = payload.contract_id
        if not contract_id:
            logger.warning("CloudSign webhook: contract_id が未設定")
            return WebhookResponse(received=True, message="contract_id missing, skipped")

        db = get_service_client()
        now = datetime.now(timezone.utc).isoformat()

        if payload.event_type == "document.signed":
            # 1. contracts テーブルを更新
            contract_result = (
                db.table("contracts")
                .update({
                    "status": "active",
                    "cloudsign_status": "signed",
                    "signed_at": payload.signed_at or now,
                    "updated_at": now,
                })
                .eq("id", contract_id)
                .execute()
            )
            contract = contract_result.data[0] if contract_result.data else None

            if contract:
                company_id = contract.get("company_id", "")
                opp_id = contract.get("opportunity_id")

                # 2. opportunities.stage = "won" に更新
                if opp_id:
                    await crud_sales.update_opportunity(
                        company_id=company_id,
                        opp_id=opp_id,
                        data={"stage": "won", "probability": 100},
                    )

                    # 3. customers テーブルにレコードを自動作成
                    opp = await crud_sales.get_opportunity(company_id, opp_id)
                    customer_id_val: str | None = None
                    if opp:
                        try:
                            new_customer = await crud_sales.create_customer(
                                company_id=company_id,
                                data={
                                    "customer_name": opp.get("target_company_name", ""),
                                    "industry": opp.get("target_industry"),
                                    "opportunity_id": opp_id,
                                    "lead_id": opp.get("lead_id"),
                                    "contract_id": contract_id,
                                    "selected_modules": opp.get("selected_modules", []),
                                    "mrr": opp.get("monthly_amount", 0),
                                    "contract_signed_at": payload.signed_at or now,
                                },
                            )
                            if isinstance(new_customer, dict):
                                customer_id_val = new_customer.get("id")
                        except Exception as cust_err:
                            logger.warning(f"顧客自動作成に失敗（非致命的）: {cust_err}")

                # 4. consent_flow で後続処理をトリガー
                try:
                    from workers.bpo.sales.sfa.consent_flow import process_consent_agreement
                    # consent_token が contract に含まれている場合に呼び出す
                    consent_token = contract.get("consent_token")
                    if consent_token:
                        await process_consent_agreement(
                            company_id=company_id,
                            contract_id=contract_id,
                            consent_token=consent_token,
                            user_id="system",
                            ip_address="webhook",
                            user_agent="CloudSign-Webhook",
                            contract_data={
                                "contract_title": contract.get("contract_number", ""),
                                "signed_at": payload.signed_at or now,
                            },
                        )
                except (ImportError, Exception) as flow_err:
                    logger.warning(f"consent_flow 後続処理に失敗（非致命的）: {flow_err}")

                # 5. customer_lifecycle_pipeline で onboarding 自動起動
                try:
                    from workers.bpo.sales.chain import trigger_next_pipeline
                    import asyncio
                    asyncio.create_task(trigger_next_pipeline(
                        "quotation_contract_pipeline",
                        type("Result", (), {"final_output": {"contract": {"status": "signed", "customer_id": customer_id_val}}})(),
                        company_id,
                    ))
                except Exception as chain_err:
                    logger.warning(f"onboarding chain trigger failed: {chain_err}")

            logger.info(f"Contract signed: contract_id={contract_id}")

        elif payload.event_type == "document.rejected":
            db.table("contracts").update({
                "status": "rejected",
                "cloudsign_status": "rejected",
                "updated_at": now,
            }).eq("id", contract_id).execute()
            logger.warning(f"Contract rejected: contract_id={contract_id}")

        elif payload.event_type == "document.expired":
            db.table("contracts").update({
                "status": "expired",
                "cloudsign_status": "expired",
                "updated_at": now,
            }).eq("id", contract_id).execute()
            logger.warning(f"Contract expired: contract_id={contract_id}")

        return WebhookResponse(received=True, message="CloudSign webhook processed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"cloudsign webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/webhooks/freee", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def freee_webhook(
    request: Request,
    x_freee_signature: Optional[str] = Header(None),
):
    """freee 請求書入金通知 Webhook を受信する。

    処理するイベント:
    - invoice.paid: 入金確認 → revenue_records に記録 → customers.mrr を確定
    - invoice.overdue: 未入金期限超過 → CS 担当に警告
    """
    try:
        raw_body = await request.body()
        body = await request.json()
        payload = FreeeWebhookPayload(**body)

        # freee Webhook シグネチャ検証
        secret = os.environ.get("FREEE_WEBHOOK_SECRET", "")
        if secret and x_freee_signature:
            if not _verify_hmac_sha256(secret, raw_body, x_freee_signature):
                raise HTTPException(status_code=401, detail="Invalid signature")

        if payload.event_type == "invoice.paid":
            invoice_data = payload.payload
            freee_invoice_id = invoice_data.get("id")
            amount = invoice_data.get("total_amount", 0)
            customer_ref = invoice_data.get("partner_name", "")
            our_company_id = invoice_data.get("shachotwo_company_id", "")

            # freee の company_id からシャチョツーの company_id を特定
            # mapping テーブルがあれば使う。なければ payload 内の shachotwo_company_id を使用
            if not our_company_id:
                db = get_service_client()
                mapping = (
                    db.table("tool_connections")
                    .select("company_id")
                    .eq("service_name", "freee")
                    .eq("external_id", str(payload.company_id))
                    .maybe_single()
                    .execute()
                )
                if mapping.data:
                    our_company_id = mapping.data["company_id"]

            if our_company_id:
                # revenue_records に入金記録を INSERT
                await crud_sales.create_revenue(
                    company_id=our_company_id,
                    data={
                        "freee_invoice_id": str(freee_invoice_id),
                        "amount": amount,
                        "payment_status": "paid",
                        "paid_at": datetime.now(timezone.utc).isoformat(),
                        "customer_name": customer_ref,
                        "period": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "source": "freee_webhook",
                    },
                )
                logger.info(f"freee invoice paid: invoice_id={freee_invoice_id}, amount={amount}")
            else:
                logger.warning(
                    f"freee webhook: company_id マッピングが見つかりません "
                    f"(freee_company_id={payload.company_id})"
                )

        elif payload.event_type == "invoice.overdue":
            invoice_data = payload.payload
            overdue_id = invoice_data.get("id")
            our_company_id = invoice_data.get("shachotwo_company_id", "")

            if our_company_id:
                # customer_health にスコア減算を記録
                customer_id = invoice_data.get("shachotwo_customer_id")
                if customer_id:
                    try:
                        await crud_sales.create_health_record(
                            company_id=our_company_id,
                            data={
                                "customer_id": customer_id,
                                "event_type": "invoice_overdue",
                                "health_delta": -10,
                                "note": f"freee 請求書 #{overdue_id} が未入金期限超過",
                            },
                        )
                        # customers.health_score を減算
                        await crud_sales.update_customer(
                            company_id=our_company_id,
                            customer_id=customer_id,
                            data={"health_score_delta": -10},
                        )
                    except Exception as health_err:
                        logger.warning(f"health_score 更新に失敗（非致命的）: {health_err}")

            logger.warning(f"freee invoice overdue: {overdue_id}")

        return WebhookResponse(received=True, message="freee webhook processed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"freee webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/webhooks/intercom", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def intercom_webhook(
    request: Request,
    x_hub_signature: Optional[str] = Header(None),
):
    """Intercom チャットメッセージ Webhook を受信する（オプション機能）。

    処理するトピック:
    - conversation.user.created: 新規チャット問い合わせ → サポートチケットを自動作成
    - conversation.user.replied: 既存チャット返信 → チケットにメッセージ追加
    - conversation.admin.closed: エージェントがクローズ → チケットを resolved に更新
    """
    try:
        body = await request.json()

        # Intercom はまず ping を送ってくるので応答する
        if body.get("type") == "notification_event_data":
            return WebhookResponse(received=True, message="ping")

        payload = IntercomWebhookPayload(**body)

        # Intercom HMAC-SHA1 シグネチャ検証
        secret = os.environ.get("INTERCOM_WEBHOOK_SECRET", "")
        if secret and x_hub_signature:
            raw_body = await request.body()
            expected = hmac.new(secret.encode(), raw_body, hashlib.sha1).hexdigest()
            if not hmac.compare_digest(f"sha1={expected}", x_hub_signature):
                raise HTTPException(status_code=401, detail="Invalid signature")

        topic = payload.topic or ""
        data = payload.data or {}
        item = data.get("item", {})
        conversation_id = item.get("id", "")

        # Intercom データから company_id を特定（カスタムアトリビュート or user メタデータ）
        user_data = item.get("user", {}) or item.get("contacts", {})
        our_company_id = ""
        if isinstance(user_data, dict):
            our_company_id = user_data.get("custom_attributes", {}).get("shachotwo_company_id", "")
        elif isinstance(user_data, list) and user_data:
            our_company_id = user_data[0].get("custom_attributes", {}).get("shachotwo_company_id", "")

        if topic == "conversation.user.created" and our_company_id:
            # 新規問い合わせをサポートチケットとして作成
            message_body = ""
            parts = item.get("conversation_parts", {}).get("conversation_parts", [])
            if parts:
                message_body = parts[0].get("body", "")
            elif item.get("source", {}).get("body"):
                message_body = item["source"]["body"]

            try:
                ticket = await crud_sales.create_ticket(
                    company_id=our_company_id,
                    data={
                        "subject": f"Intercom: {item.get('source', {}).get('subject', 'チャット問い合わせ')}",
                        "channel": "intercom",
                        "external_id": conversation_id,
                        "description": message_body,
                    },
                )
                logger.info(f"Intercom → ticket created: {ticket.get('id')} for conversation {conversation_id}")
            except Exception as ticket_err:
                logger.warning(f"チケット作成に失敗: {ticket_err}")

        elif topic == "conversation.user.replied" and our_company_id:
            # 既存チケットにメッセージを追加
            db = get_service_client()
            ticket_result = (
                db.table("support_tickets")
                .select("id")
                .eq("company_id", our_company_id)
                .eq("external_id", conversation_id)
                .maybe_single()
                .execute()
            )
            if ticket_result.data:
                ticket_id = ticket_result.data["id"]
                parts = item.get("conversation_parts", {}).get("conversation_parts", [])
                msg_body = parts[-1].get("body", "") if parts else ""
                try:
                    await crud_sales.create_message(
                        company_id=our_company_id,
                        data={
                            "ticket_id": ticket_id,
                            "sender": "customer",
                            "body": msg_body,
                            "channel": "intercom",
                        },
                    )
                except Exception as msg_err:
                    logger.warning(f"メッセージ追加に失敗: {msg_err}")

            logger.info(f"Intercom user replied: {conversation_id}")

        elif topic == "conversation.admin.closed" and our_company_id:
            # チケットを resolved に更新
            db = get_service_client()
            ticket_result = (
                db.table("support_tickets")
                .select("id")
                .eq("company_id", our_company_id)
                .eq("external_id", conversation_id)
                .maybe_single()
                .execute()
            )
            if ticket_result.data:
                ticket_id = ticket_result.data["id"]
                try:
                    await crud_sales.update_ticket(
                        company_id=our_company_id,
                        ticket_id=ticket_id,
                        data={"status": "resolved", "resolved_at": datetime.now(timezone.utc).isoformat()},
                    )
                except Exception as close_err:
                    logger.warning(f"チケットクローズに失敗: {close_err}")

            logger.info(f"Intercom conversation closed: {conversation_id}")

        return WebhookResponse(received=True, message="Intercom webhook processed")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"intercom webhook failed: {e}")
        # Intercom はレスポンスが 2xx 以外の場合リトライするため必ず 200 を返す
        return WebhookResponse(received=False, message="Processing error, will retry")


# ---------------------------------------------------------------------------
# Request / Response models — LP Event Webhook
# ---------------------------------------------------------------------------


class LPEventPayload(BaseModel):
    """LP訪問イベントペイロード"""
    event_type: str  # "lp_view" | "cta_click" | "doc_download" | "schedule_confirmed"
    lead_id: Optional[str] = None
    campaign_id: Optional[str] = None
    duration_sec: int = 0
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# LP Event Endpoint
# ---------------------------------------------------------------------------


@router.post("/webhooks/lp-event", response_model=WebhookResponse, status_code=status.HTTP_200_OK)
async def lp_event_webhook(payload: LPEventPayload):
    """LP訪問・CTA クリック等のイベントを受信する。

    LP HTMLに埋め込まれたトラッキングスクリプトから送信される。
    signal_detector経由でhot/warm/cold判定し、lead_activitiesに記録する。
    """
    try:
        db = get_service_client()
        now = datetime.now(timezone.utc).isoformat()

        # lead_activities に記録
        activity_data: dict[str, Any] = {
            "activity_type": payload.event_type,
            "activity_data": {
                "duration_sec": payload.duration_sec,
                "page_url": payload.page_url,
                "referrer": payload.referrer,
                **(payload.metadata or {}),
            },
            "channel": "lp",
            "created_at": now,
        }

        if payload.lead_id:
            activity_data["lead_id"] = payload.lead_id
            db.table("lead_activities").insert(activity_data).execute()

        # シグナル検知（hot/warm/cold）
        temperature = "cold"
        if payload.event_type == "cta_click":
            temperature = "hot"
        elif payload.event_type == "schedule_confirmed":
            temperature = "confirmed"
        elif payload.event_type == "doc_download":
            temperature = "warm"
        elif payload.event_type == "lp_view" and payload.duration_sec >= 30:
            temperature = "warm"

        # hotリードはlead_qualification自動起動
        if temperature == "hot" and payload.lead_id:
            try:
                from workers.bpo.sales.chain import trigger_next_pipeline
                import asyncio
                asyncio.create_task(trigger_next_pipeline(
                    "lp_hot_signal",
                    None,
                    "system",
                    {"lead_id": payload.lead_id, "signal": temperature},
                ))
            except Exception:
                pass

        logger.info(f"LP event: {payload.event_type} lead={payload.lead_id} temp={temperature}")
        return WebhookResponse(received=True, message=f"temperature={temperature}")

    except Exception as e:
        logger.error(f"lp_event webhook failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
