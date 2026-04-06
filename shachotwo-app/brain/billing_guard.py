"""プラン別フィーチャーフラグ（REQ-3003）。

各テナントの現在プランを取得し、機能・制限を制御する。
パイプライン実行・Q&A・コネクタ数の上限チェックはここを通す。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# プラン定義
# ─────────────────────────────────────

@dataclass
class PlanLimits:
    """プラン別の制限値。"""
    plan: str
    max_pipelines: int          # 月間パイプライン実行上限（-1 = 無制限）
    max_qa_per_month: int       # 月間Q&A上限（-1 = 無制限）
    max_connectors: int         # 接続可能コネクタ数（-1 = 無制限）
    has_industry_pipelines: bool    # 業種特化パイプライン利用可否
    has_human_support: bool         # 人間サポート（パートナー伴走）利用可否
    features: list[str] = field(default_factory=list)


# プラン別制限マスタ（CLAUDE.md 料金体系に準拠）
_PLAN_LIMITS: dict[str, PlanLimits] = {
    # 共通BPO: ¥150,000/月
    # バックオフィス全般 + ブレイン込み。業種特化パイプラインは利用不可
    "common_bpo": PlanLimits(
        plan="common_bpo",
        max_pipelines=300,          # 月間基本枠
        max_qa_per_month=500,       # 月間基本枠
        max_connectors=3,
        has_industry_pipelines=False,
        has_human_support=False,
        features=[
            "dashboard",
            "qa",
            "knowledge_base",
            "bpo_common",
            "digital_twin_basic",
        ],
    ),

    # 業種特化BPO: ¥300,000/月
    # 共通BPO + 業種固有パイプライン全部まるっと
    "industry_bpo": PlanLimits(
        plan="industry_bpo",
        max_pipelines=2000,         # 大幅拡張
        max_qa_per_month=2000,
        max_connectors=10,
        has_industry_pipelines=True,
        has_human_support=False,
        features=[
            "dashboard",
            "qa",
            "knowledge_base",
            "bpo_common",
            "bpo_industry",
            "digital_twin_full",
            "sfa_crm",
            "marketing",
            "connectors_tier1",
        ],
    ),

    # 業種特化BPO + 人間サポート: ¥450,000/月
    # industry_bpo + パートナーによるコンサル型伴走
    "industry_bpo_support": PlanLimits(
        plan="industry_bpo_support",
        max_pipelines=-1,           # 無制限
        max_qa_per_month=-1,        # 無制限
        max_connectors=-1,          # 無制限
        has_industry_pipelines=True,
        has_human_support=True,
        features=[
            "dashboard",
            "qa",
            "knowledge_base",
            "bpo_common",
            "bpo_industry",
            "digital_twin_full",
            "sfa_crm",
            "marketing",
            "connectors_tier1",
            "human_support",
            "priority_support",
            "custom_genome",
        ],
    ),
}

# フォールバック: プラン未設定 or 解約済みテナント向けの最小制限
_FREE_LIMITS = PlanLimits(
    plan="none",
    max_pipelines=0,
    max_qa_per_month=0,
    max_connectors=0,
    has_industry_pipelines=False,
    has_human_support=False,
    features=[],
)


# ─────────────────────────────────────
# BillingGuard
# ─────────────────────────────────────

class BillingGuard:
    """テナントのプラン情報に基づいて機能アクセスを制御する。

    Usage:
        guard = BillingGuard()
        limits = await guard.get_plan_limits(company_id)
        if not await guard.check_feature(company_id, "bpo_industry"):
            raise HTTPException(403, "このプランでは利用できません")
    """

    async def get_plan_limits(self, company_id: str) -> PlanLimits:
        """テナントの現在プランに対応する PlanLimits を返す。

        subscriptions テーブルから最新の active/trialing レコードを取得する。
        プランが見つからない場合や解約済みの場合は _FREE_LIMITS を返す。

        Args:
            company_id: テナント ID

        Returns:
            PlanLimits（プラン別の制限値）
        """
        plan = await self._fetch_active_plan(company_id)
        return _PLAN_LIMITS.get(plan, _FREE_LIMITS)

    async def check_feature(self, company_id: str, feature: str) -> bool:
        """指定した機能がテナントのプランで利用可能か確認する。

        Args:
            company_id: テナント ID
            feature: 機能名（例: 'bpo_industry', 'human_support'）

        Returns:
            True = 利用可能, False = 利用不可
        """
        limits = await self.get_plan_limits(company_id)
        return feature in limits.features

    async def check_pipeline_quota(
        self,
        company_id: str,
        current_month_count: int,
    ) -> bool:
        """今月のパイプライン実行数が上限内か確認する。

        Args:
            company_id: テナント ID
            current_month_count: 今月の実行済み回数

        Returns:
            True = 上限内（実行可能）, False = 上限超過
        """
        limits = await self.get_plan_limits(company_id)
        if limits.max_pipelines == -1:
            return True  # 無制限
        return current_month_count < limits.max_pipelines

    async def check_qa_quota(
        self,
        company_id: str,
        current_month_count: int,
    ) -> bool:
        """今月のQ&A回数が上限内か確認する。

        Args:
            company_id: テナント ID
            current_month_count: 今月の実行済み回数

        Returns:
            True = 上限内（実行可能）, False = 上限超過
        """
        limits = await self.get_plan_limits(company_id)
        if limits.max_qa_per_month == -1:
            return True  # 無制限
        return current_month_count < limits.max_qa_per_month

    # ─────────────────────────────────────
    # 内部ヘルパー
    # ─────────────────────────────────────

    async def _fetch_active_plan(self, company_id: str) -> str:
        """subscriptions テーブルから active/trialing のプランを取得する。

        Returns:
            プラン名文字列。取得失敗や未加入の場合は 'none'。
        """
        try:
            db = get_service_client()
            result = (
                db.table("subscriptions")
                .select("plan, status")
                .eq("company_id", company_id)
                .in_("status", ["active", "trialing"])
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if rows:
                return rows[0].get("plan", "none")
        except Exception as e:
            logger.warning(
                f"BillingGuard: プラン取得失敗（フォールバック=none）: "
                f"company={company_id[:8]} error={e}"
            )
        return "none"


# ─────────────────────────────────────
# モジュールレベルのシングルトン
# ─────────────────────────────────────

# FastAPI の Depends や直接呼び出しで使うシングルトンインスタンス
billing_guard = BillingGuard()


async def get_plan_limits(company_id: str) -> PlanLimits:
    """get_plan_limits のモジュールレベルショートカット。"""
    return await billing_guard.get_plan_limits(company_id)


async def check_feature(company_id: str, feature: str) -> bool:
    """check_feature のモジュールレベルショートカット。"""
    return await billing_guard.check_feature(company_id, feature)
