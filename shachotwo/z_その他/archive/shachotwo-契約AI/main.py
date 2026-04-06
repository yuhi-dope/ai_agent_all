"""契約AI — エントリポイント"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import stripe
from config import settings

app = FastAPI(title="shachotwo-契約AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

stripe.api_key = settings.stripe_secret_key


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/contract/{deal_id}/start")
async def start_contract(deal_id: str):
    """契約フロー開始"""
    from contract.trigger import start_contract_flow
    await start_contract_flow(deal_id)
    return {"status": "started", "deal_id": deal_id}


@app.get("/contract/{deal_id}/status")
async def contract_status(deal_id: str):
    """契約ステータス確認"""
    # TODO: apo_contracts から取得
    return {"deal_id": deal_id, "status": "pending"}


@app.post("/webhook/cloudsign")
async def cloudsign_webhook(payload: dict):
    """CloudSign署名完了Webhook"""
    from contract.cloudsign import handle_webhook
    await handle_webhook(payload)
    return {"status": "ok"}


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Stripe決済Webhook"""
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(body, sig, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return {"error": "invalid signature"}, 400

    from contract.stripe_billing import handle_checkout_completed, handle_invoice_paid, handle_subscription_cancelled
    handlers = {
        "checkout.session.completed": handle_checkout_completed,
        "invoice.paid": handle_invoice_paid,
        "customer.subscription.deleted": handle_subscription_cancelled,
    }
    handler = handlers.get(event.type)
    if handler:
        await handler(event.data.object)

    return {"status": "ok"}
