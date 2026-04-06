"""Stripe 課金コネクタ（REQ-3003）。

stripe ライブラリはオプショナル。未インストール環境でも import エラーにならない。
STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET は環境変数から取得。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

# stripe はオプショナル依存
try:
    import stripe as _stripe
except ImportError:  # pragma: no cover
    _stripe = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ─────────────────────────────────────
# プラン → Stripe Price ID マッピング
# 実際の Price ID は Stripe Dashboard で作成後に環境変数で上書きする
# ─────────────────────────────────────
_PLAN_PRICE_IDS: dict[str, str] = {
    "common_bpo":           os.environ.get("STRIPE_PRICE_COMMON_BPO", "price_common_bpo_placeholder"),
    "industry_bpo":         os.environ.get("STRIPE_PRICE_INDUSTRY_BPO", "price_industry_bpo_placeholder"),
    "industry_bpo_support": os.environ.get("STRIPE_PRICE_INDUSTRY_BPO_SUPPORT", "price_industry_bpo_support_placeholder"),
}


class StripeBillingConnector:
    """Stripe 課金操作をカプセル化するコネクタ。

    全メソッドは stripe ライブラリが未インストールの場合に RuntimeError を送出する。
    """

    def __init__(self) -> None:
        self._api_key = os.environ.get("STRIPE_SECRET_KEY", "")
        self._webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    def _require_stripe(self) -> Any:
        """stripe モジュールが利用可能か確認する。利用不可なら RuntimeError。"""
        if _stripe is None:
            raise RuntimeError(
                "stripe ライブラリがインストールされていません。"
                "`pip install stripe` を実行してください。"
            )
        _stripe.api_key = self._api_key
        return _stripe

    # ─────────────────────────────────────
    # 顧客管理
    # ─────────────────────────────────────

    async def create_customer(
        self,
        company_id: str,
        email: str,
        name: str,
    ) -> str:
        """Stripe 顧客を作成して stripe_customer_id を返す。

        Args:
            company_id: 内部テナント ID（Stripe メタデータに保存）
            email: 請求先メールアドレス
            name: 会社名

        Returns:
            stripe_customer_id（例: "cus_xxxxx"）
        """
        s = self._require_stripe()
        customer = s.Customer.create(
            email=email,
            name=name,
            metadata={"company_id": company_id},
        )
        logger.info(f"Stripe 顧客作成: company={company_id[:8]} customer_id={customer.id}")
        return customer.id

    # ─────────────────────────────────────
    # サブスクリプション管理
    # ─────────────────────────────────────

    async def create_subscription(
        self,
        customer_id: str,
        plan: str,
        industry: Optional[str] = None,
    ) -> str:
        """Stripe サブスクリプションを作成して subscription_id を返す。

        Args:
            customer_id: Stripe 顧客 ID
            plan: 'common_bpo' | 'industry_bpo' | 'industry_bpo_support'
            industry: 業種コード（industry_bpo 系の場合に設定）

        Returns:
            stripe_subscription_id（例: "sub_xxxxx"）
        """
        s = self._require_stripe()
        price_id = _PLAN_PRICE_IDS.get(plan)
        if not price_id:
            raise ValueError(f"不明なプランです: {plan}")

        metadata: dict[str, str] = {"plan": plan}
        if industry:
            metadata["industry"] = industry

        subscription = s.Subscription.create(
            customer=customer_id,
            items=[{"price": price_id}],
            metadata=metadata,
        )
        logger.info(
            f"Stripe サブスクリプション作成: customer={customer_id} "
            f"plan={plan} sub_id={subscription.id}"
        )
        return subscription.id

    async def cancel_subscription(self, subscription_id: str) -> None:
        """Stripe サブスクリプションを期末解約（cancel_at_period_end=true）に設定する。

        Args:
            subscription_id: Stripe サブスクリプション ID
        """
        s = self._require_stripe()
        s.Subscription.modify(
            subscription_id,
            cancel_at_period_end=True,
        )
        logger.info(f"Stripe サブスクリプション解約設定: sub_id={subscription_id}")

    # ─────────────────────────────────────
    # Checkout / Portal セッション
    # ─────────────────────────────────────

    async def create_checkout_session(
        self,
        customer_id: str,
        plan: str,
        success_url: str,
        cancel_url: str,
        industry: Optional[str] = None,
    ) -> str:
        """Stripe Checkout セッションを作成してリダイレクト URL を返す。

        Args:
            customer_id: Stripe 顧客 ID
            plan: 'common_bpo' | 'industry_bpo' | 'industry_bpo_support'
            success_url: 決済成功後のリダイレクト先
            cancel_url: 決済キャンセル後のリダイレクト先
            industry: 業種コード（オプション）

        Returns:
            Stripe Hosted Checkout の URL
        """
        s = self._require_stripe()
        price_id = _PLAN_PRICE_IDS.get(plan)
        if not price_id:
            raise ValueError(f"不明なプランです: {plan}")

        metadata: dict[str, str] = {"plan": plan}
        if industry:
            metadata["industry"] = industry

        session = s.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            payment_method_types=["card", "customer_balance", "konbini"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            subscription_data={"metadata": metadata},
        )
        logger.info(
            f"Stripe Checkout セッション作成: customer={customer_id} plan={plan}"
        )
        return session.url

    async def create_bank_transfer_checkout_session(
        self,
        customer_id: str,
        amount_yen: int,
        description: str,
        success_url: str,
        cancel_url: str,
    ) -> str:
        """口座振替（bank_transfer）専用の Checkout セッションを作成する。

        サブスクリプションではなく都度払い（payment モード）で発行する。
        建設・製造業の中小企業向け請求書払い対応。

        Args:
            customer_id: Stripe 顧客 ID
            amount_yen: 請求金額（円）
            description: 請求内容の説明
            success_url: 決済成功後のリダイレクト先
            cancel_url: 決済キャンセル後のリダイレクト先

        Returns:
            Stripe Hosted Checkout の URL
        """
        s = self._require_stripe()

        session = s.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            payment_method_types=["customer_balance", "konbini"],
            payment_intent_data={
                "payment_method_options": {
                    "customer_balance": {
                        "funding_type": "bank_transfer",
                        "bank_transfer": {"type": "jp_bank_transfer"},
                    }
                }
            },
            line_items=[{
                "price_data": {
                    "currency": "jpy",
                    "unit_amount": amount_yen,
                    "product_data": {"name": description},
                },
                "quantity": 1,
            }],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        logger.info(
            f"Stripe 口座振替 Checkout セッション作成: customer={customer_id} "
            f"amount={amount_yen}円"
        )
        return session.url

    async def create_billing_portal_session(
        self,
        customer_id: str,
        return_url: str,
    ) -> str:
        """Stripe カスタマーポータルセッションを作成して URL を返す。

        顧客が自分でプランの変更・解約・請求履歴確認を行うためのポータル。

        Args:
            customer_id: Stripe 顧客 ID
            return_url: ポータル退出後のリダイレクト先

        Returns:
            Stripe カスタマーポータルの URL
        """
        s = self._require_stripe()
        session = s.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        logger.info(f"Stripe ポータルセッション作成: customer={customer_id}")
        return session.url

    # ─────────────────────────────────────
    # Webhook 処理
    # ─────────────────────────────────────

    def handle_webhook(
        self,
        payload: bytes,
        sig_header: str,
    ) -> dict[str, Any]:
        """Stripe Webhook の署名を検証してイベントオブジェクトを返す。

        Args:
            payload: リクエストボディ（生バイト列）
            sig_header: "Stripe-Signature" ヘッダー値

        Returns:
            Stripe Event の dict 表現

        Raises:
            ValueError: 署名検証失敗
            RuntimeError: stripe ライブラリ未インストール
        """
        s = self._require_stripe()
        try:
            event = s.Webhook.construct_event(
                payload,
                sig_header,
                self._webhook_secret,
            )
        except s.error.SignatureVerificationError as e:
            logger.warning(f"Stripe Webhook 署名検証失敗: {e}")
            raise ValueError(f"Webhook 署名が無効です: {e}") from e

        return dict(event)

    # ─────────────────────────────────────
    # 請求履歴
    # ─────────────────────────────────────

    async def list_invoices(
        self,
        customer_id: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Stripe から請求履歴を取得する。

        Args:
            customer_id: Stripe 顧客 ID
            limit: 取得件数（最大12件）

        Returns:
            請求書リスト（id, amount_due, status, period_start, period_end, invoice_pdf）
        """
        s = self._require_stripe()
        invoices = s.Invoice.list(customer=customer_id, limit=min(limit, 100))
        return [
            {
                "id": inv.id,
                "amount_due_yen": inv.amount_due,  # Stripe は最小通貨単位（円の場合は1円=1）
                "amount_paid_yen": inv.amount_paid,
                "status": inv.status,
                "period_start": inv.period_start,
                "period_end": inv.period_end,
                "invoice_pdf": inv.invoice_pdf,
                "hosted_invoice_url": inv.hosted_invoice_url,
            }
            for inv in invoices.auto_paging_iter()
            if invoices
        ]
