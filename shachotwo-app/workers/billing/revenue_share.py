"""パートナー収益分配エンジン。

月次バッチで実行し、app_installations × price_yen を集計して
revenue_share_records に記録し、Stripe Connect で送金する。

実行方法:
    engine = RevenueShareEngine()
    result = await engine.run_monthly_batch("2026-04")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# Stripe はオプション依存。未インストール / キー未設定でも動作する。
try:
    import stripe  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    stripe = None  # type: ignore[assignment]


# ─────────────────────────────────────
# データモデル
# ─────────────────────────────────────

@dataclass
class RevenueShareSummary:
    """パートナー1件分の収益分配サマリー。"""
    partner_id: str
    period_month: str           # YYYY-MM 形式
    total_gross_yen: int        # 総売上（プラットフォーム + パートナー取り分）
    total_partner_yen: int      # パートナー取り分
    total_platform_yen: int     # プラットフォーム取り分
    app_count: int              # 対象アプリ数
    installation_count: int     # アクティブインストール数
    stripe_payout_id: Optional[str]  # Stripe transfer ID（送金済みの場合）
    status: str                 # pending | paid | failed


@dataclass
class _AppInstallationRow:
    """app_installations JOIN partner_apps の集計行。"""
    partner_id: str
    app_id: str
    price_yen: int
    revenue_share_rate: float   # 0.0〜1.0（partners テーブルから結合）
    installation_id: str
    company_id: str


# ─────────────────────────────────────
# エンジン本体
# ─────────────────────────────────────

class RevenueShareEngine:
    """パートナー収益分配の計算・送金を担うエンジン。"""

    async def calculate_month(self, period_month: str) -> list[RevenueShareSummary]:
        """指定月の全パートナー収益を計算してDBに保存し、サマリーを返す。

        処理フロー:
        1. app_installations JOIN partner_apps でアクティブなインストールを取得
        2. partner_apps.price_yen × is_active の数で gross 計算
        3. partners.revenue_share_rate でパートナー取り分計算
        4. revenue_share_records に upsert
           (partner_id + app_id + company_id + period_month ON CONFLICT DO UPDATE)
        5. サマリーを集計して返す

        Args:
            period_month: 計算対象月（YYYY-MM 形式）

        Returns:
            パートナーごとの RevenueShareSummary リスト
        """
        db = get_service_client()

        # 1. アクティブなインストールを取得（app_installations JOIN partner_apps JOIN partners）
        rows_resp = (
            db.table("app_installations")
            .select(
                "id, company_id, app_id, "
                "partner_apps(id, price_yen, partner_id, "
                "partners(id, revenue_share_rate, stripe_account_id))"
            )
            .eq("is_active", True)
            .execute()
        )

        raw_rows: list[dict] = rows_resp.data or []
        logger.info(
            "calculate_month: period=%s, active_installations=%d",
            period_month,
            len(raw_rows),
        )

        # 2. パートナー別に集計
        # partner_id -> {app_id -> [installation_rows]}
        partner_map: dict[str, dict[str, list[dict]]] = {}
        for row in raw_rows:
            app = row.get("partner_apps") or {}
            if not app:
                continue
            partner = app.get("partners") or {}
            if not partner:
                continue
            pid = partner.get("id") or app.get("partner_id")
            if not pid:
                continue
            aid = app.get("id") or row.get("app_id")
            partner_map.setdefault(pid, {}).setdefault(aid, []).append(row)

        summaries: list[RevenueShareSummary] = []

        for partner_id, apps in partner_map.items():
            total_gross = 0
            total_partner = 0
            total_platform = 0
            app_count = len(apps)
            installation_count = 0

            # パートナーの revenue_share_rate を最初の行から取得
            first_app = next(iter(apps.values()))
            first_row = first_app[0]
            app_info = first_row.get("partner_apps") or {}
            partner_info = app_info.get("partners") or {}
            revenue_share_rate = float(partner_info.get("revenue_share_rate", 0.5))

            upsert_records: list[dict] = []

            for app_id, installs in apps.items():
                app_data = installs[0].get("partner_apps") or {}
                price_yen = int(app_data.get("price_yen", 0))

                for inst in installs:
                    installation_count += 1
                    gross = price_yen
                    partner_yen = int(gross * revenue_share_rate)
                    platform_yen = gross - partner_yen

                    total_gross += gross
                    total_partner += partner_yen
                    total_platform += platform_yen

                    upsert_records.append({
                        "partner_id": partner_id,
                        "app_id": app_id,
                        "company_id": inst.get("company_id"),
                        "installation_id": inst.get("id"),
                        "period_month": period_month,
                        "gross_yen": gross,
                        "partner_yen": partner_yen,
                        "platform_yen": platform_yen,
                        "revenue_share_rate": revenue_share_rate,
                        "status": "pending",
                    })

            # 3. revenue_share_records に upsert
            if upsert_records:
                db.table("revenue_share_records").upsert(
                    upsert_records,
                    on_conflict="partner_id,app_id,company_id,period_month",
                ).execute()
                logger.info(
                    "upserted revenue_share_records: partner=%s, records=%d",
                    partner_id,
                    len(upsert_records),
                )

            summaries.append(
                RevenueShareSummary(
                    partner_id=partner_id,
                    period_month=period_month,
                    total_gross_yen=total_gross,
                    total_partner_yen=total_partner,
                    total_platform_yen=total_platform,
                    app_count=app_count,
                    installation_count=installation_count,
                    stripe_payout_id=None,
                    status="pending",
                )
            )

        logger.info(
            "calculate_month done: period=%s, partners=%d",
            period_month,
            len(summaries),
        )
        return summaries

    async def payout_partner(self, partner_id: str, period_month: str) -> str:
        """Stripe Connect でパートナーに送金する。

        処理フロー:
        - partners.stripe_account_id が必要
        - stripe.transfers.create でパートナーアカウントに送金
        - revenue_share_records を status=paid / stripe_payout_id に更新
        - stripe が使えない / stripe_account_id 未設定の場合は status=pending のままログ出力

        Args:
            partner_id: 送金対象のパートナーID
            period_month: 対象月（YYYY-MM 形式）

        Returns:
            Stripe transfer ID、またはスキップ理由を示す文字列
        """
        db = get_service_client()

        # パートナー情報を取得
        partner_resp = (
            db.table("partners")
            .select("id, stripe_account_id")
            .eq("id", partner_id)
            .maybe_single()
            .execute()
        )
        partner = partner_resp.data
        if not partner:
            logger.error("payout_partner: partner not found: %s", partner_id)
            return "error:partner_not_found"

        stripe_account_id: Optional[str] = partner.get("stripe_account_id")

        # 対象月の pending レコードを集計
        records_resp = (
            db.table("revenue_share_records")
            .select("id, partner_yen, status")
            .eq("partner_id", partner_id)
            .eq("period_month", period_month)
            .eq("status", "pending")
            .execute()
        )
        records = records_resp.data or []
        if not records:
            logger.info(
                "payout_partner: no pending records: partner=%s period=%s",
                partner_id,
                period_month,
            )
            return "skipped:no_pending_records"

        total_partner_yen = sum(int(r.get("partner_yen", 0)) for r in records)
        record_ids = [r["id"] for r in records]

        # Stripe Connect 送金
        if stripe is None or not stripe_account_id:
            reason = "stripe_unavailable" if stripe is None else "no_stripe_account_id"
            logger.warning(
                "payout_partner: skipped (%s): partner=%s period=%s total_yen=%d",
                reason,
                partner_id,
                period_month,
                total_partner_yen,
            )
            return f"pending:{reason}"

        import os
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

        try:
            transfer = stripe.transfers.create(
                amount=total_partner_yen,  # 円（JPY は最小単位が1円）
                currency="jpy",
                destination=stripe_account_id,
                metadata={
                    "partner_id": partner_id,
                    "period_month": period_month,
                },
            )
            transfer_id: str = transfer["id"]
            logger.info(
                "payout_partner: stripe transfer created: transfer_id=%s partner=%s yen=%d",
                transfer_id,
                partner_id,
                total_partner_yen,
            )
        except Exception as exc:
            logger.error(
                "payout_partner: stripe transfer failed: partner=%s error=%s",
                partner_id,
                exc,
            )
            # 送金失敗時は status=failed に更新
            db.table("revenue_share_records").update({"status": "failed"}).in_(
                "id", record_ids
            ).execute()
            return f"failed:{exc}"

        # 送金成功 → status=paid, stripe_payout_id を更新
        db.table("revenue_share_records").update(
            {"status": "paid", "stripe_payout_id": transfer_id}
        ).in_("id", record_ids).execute()

        return transfer_id

    async def run_monthly_batch(self, period_month: str) -> dict:
        """月次バッチ: 全パートナーの計算 + 送金を実行する。

        エラーが発生しても他パートナーの処理を継続し、errors リストに追記する。

        Args:
            period_month: 処理対象月（YYYY-MM 形式）

        Returns:
            {
                "period_month": str,
                "processed_partners": int,
                "total_payout_yen": int,
                "errors": list[str],
            }
        """
        logger.info("run_monthly_batch: start period=%s", period_month)

        errors: list[str] = []
        total_payout_yen = 0

        # Step 1: 全パートナーの収益を計算
        try:
            summaries = await self.calculate_month(period_month)
        except Exception as exc:
            msg = f"calculate_month failed: {exc}"
            logger.error("run_monthly_batch: %s", msg)
            return {
                "period_month": period_month,
                "processed_partners": 0,
                "total_payout_yen": 0,
                "errors": [msg],
            }

        processed_partners = 0

        # Step 2: 各パートナーに送金
        for summary in summaries:
            try:
                result = await self.payout_partner(summary.partner_id, period_month)
                if result.startswith("failed:") or result.startswith("error:"):
                    errors.append(
                        f"partner={summary.partner_id} payout={result}"
                    )
                else:
                    total_payout_yen += summary.total_partner_yen
                    processed_partners += 1
                    logger.info(
                        "run_monthly_batch: partner=%s payout_result=%s yen=%d",
                        summary.partner_id,
                        result,
                        summary.total_partner_yen,
                    )
            except Exception as exc:
                msg = f"partner={summary.partner_id} exception={exc}"
                logger.error("run_monthly_batch: unexpected error: %s", msg)
                errors.append(msg)

        result_summary = {
            "period_month": period_month,
            "processed_partners": processed_partners,
            "total_payout_yen": total_payout_yen,
            "errors": errors,
        }
        logger.info(
            "run_monthly_batch: done period=%s processed=%d total_yen=%d errors=%d",
            period_month,
            processed_partners,
            total_payout_yen,
            len(errors),
        )
        return result_summary
