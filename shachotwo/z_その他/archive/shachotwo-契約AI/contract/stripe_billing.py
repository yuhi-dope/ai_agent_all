"""Stripe決済連携 — Checkout / Subscription / Invoice"""

from __future__ import annotations

import stripe

from config import settings

stripe.api_key = settings.stripe_secret_key


def create_customer(company_name: str, email: str, metadata: dict | None = None) -> str:
    """Stripe Customer作成"""
    customer = stripe.Customer.create(
        name=company_name,
        email=email,
        metadata=metadata or {},
    )
    return customer.id


def create_checkout_session(
    customer_id: str,
    price_id: str,
    trial_days: int = 30,
    success_url: str = "",
    cancel_url: str = "",
    metadata: dict | None = None,
) -> str:
    """Stripe Checkout Session生成（クレカ払い）"""
    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data={"trial_period_days": trial_days, "metadata": metadata or {}},
        success_url=success_url or f"{settings.app_base_url}/contract/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=cancel_url or f"{settings.app_base_url}/contract/cancel",
        metadata=metadata or {},
    )
    return session.url


def create_invoice(customer_id: str, price_id: str, due_days: int = 30) -> str:
    """請求書払い用のStripe Invoice作成"""
    invoice_item = stripe.InvoiceItem.create(
        customer=customer_id,
        price=price_id,
    )
    invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=due_days,
    )
    stripe.Invoice.finalize_invoice(invoice.id)
    stripe.Invoice.send_invoice(invoice.id)
    return invoice.id


def create_subscription(customer_id: str, price_id: str, trial_days: int = 30) -> str:
    """サブスクリプション作成"""
    sub = stripe.Subscription.create(
        customer=customer_id,
        items=[{"price": price_id}],
        trial_period_days=trial_days,
        payment_behavior="default_incomplete",
    )
    return sub.id


async def setup_payment_after_signing(document_id: str) -> None:
    """署名完了後に決済セットアップ"""
    # TODO: document_id から contract を取得
    # TODO: payment_method に応じて Checkout or Invoice を生成
    # TODO: メールで決済リンク送信
    pass


async def handle_checkout_completed(event_data: dict) -> None:
    """checkout.session.completed Webhook"""
    customer_id = event_data.get("customer", "")
    subscription_id = event_data.get("subscription", "")
    # TODO: apo_contracts を更新（stripe_subscription_id, status='active'）
    # TODO: アカウント自動作成を起動
    from contract.account_provisioner import provision_account
    await provision_account({"stripe_customer_id": customer_id, "stripe_subscription_id": subscription_id})


async def handle_invoice_paid(event_data: dict) -> None:
    """invoice.paid Webhook"""
    customer_id = event_data.get("customer", "")
    # TODO: 請求書払いの入金確認 → アカウント作成
    from contract.account_provisioner import provision_account
    await provision_account({"stripe_customer_id": customer_id})


async def handle_subscription_cancelled(event_data: dict) -> None:
    """customer.subscription.deleted Webhook"""
    subscription_id = event_data.get("id", "")
    # TODO: apo_contracts の status を 'cancelled' に更新
    pass
