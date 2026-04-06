"""承認ワークフローエンジン + Trust Scorer"""
import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from db.supabase import get_service_client as get_client
from llm.client import LLMClient, LLMTask, ModelTier

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# Trust Scorer（信頼度スコア）
# ─────────────────────────────────────

@dataclass
class TrustScore:
    approval_rate: float
    rejection_rate: float
    modification_rate: float
    consecutive_successes: int
    weighted_score: float
    level: int  # 0-4

    @property
    def level_name(self) -> str:
        return {
            0: "通知のみ",
            1: "下書き作成（承認必須）",
            2: "自動実行（事後レビュー）",
            3: "完全自律（異常時のみ通知）",
        }.get(self.level, "不明")


class TrustScorer:
    """BPOエージェントごとの信頼度スコアを管理"""

    @staticmethod
    async def calculate(company_id: str, target_type: str) -> TrustScore:
        """直近のapproval履歴からTrustScoreを計算"""
        client = get_client()

        # 直近100件の承認履歴を取得
        result = client.table("bpo_approvals").select(
            "status, modification_diff"
        ).eq("company_id", company_id).eq(
            "target_type", target_type
        ).not_.is_("decided_at", "null").order(
            "decided_at", desc=True
        ).limit(100).execute()

        rows = result.data or []
        total = len(rows)

        if total == 0:
            return TrustScore(
                approval_rate=0, rejection_rate=0, modification_rate=0,
                consecutive_successes=0, weighted_score=0, level=0,
            )

        approved = sum(1 for r in rows if r["status"] == "approved" and not r.get("modification_diff"))
        modified = sum(1 for r in rows if r["status"] == "approved" and r.get("modification_diff"))
        rejected = sum(1 for r in rows if r["status"] == "rejected")

        approval_rate = approved / total
        rejection_rate = rejected / total
        modification_rate = modified / total

        # 連続成功カウント（直近から遡って承認が続いた回数）
        consecutive = 0
        for r in rows:
            if r["status"] == "approved" and not r.get("modification_diff"):
                consecutive += 1
            else:
                break

        # 重み付きスコア（却下は重い）
        weighted = (approved * 1.0 - rejected * 3.0 - modified * 0.5) / total

        # レベル判定
        level = 0
        if total >= 5:
            level = 1
        if total >= 20 and approval_rate >= 0.8:
            level = 2
        if total >= 50 and approval_rate >= 0.9 and consecutive >= 30:
            level = 3

        return TrustScore(
            approval_rate=round(approval_rate, 4),
            rejection_rate=round(rejection_rate, 4),
            modification_rate=round(modification_rate, 4),
            consecutive_successes=consecutive,
            weighted_score=round(weighted, 4),
            level=level,
        )


# ─────────────────────────────────────
# 承認ワークフロー
# ─────────────────────────────────────

async def create_approval(
    company_id: str,
    target_type: str,
    target_id: str,
    requested_by: str,
) -> dict:
    """承認リクエストを作成"""
    client = get_client()
    result = client.table("bpo_approvals").insert({
        "company_id": company_id,
        "target_type": target_type,
        "target_id": target_id,
        "requested_by": requested_by,
        "status": "pending",
    }).execute()
    return result.data[0] if result.data else {}


async def approve(
    approval_id: str,
    approver_id: str,
    comment: str | None = None,
    modification_diff: dict | None = None,
) -> dict:
    """承認（修正ありの場合はdiffを記録）"""
    client = get_client()
    update_data = {
        "approver_id": approver_id,
        "status": "approved",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    if comment:
        update_data["comment"] = comment
    if modification_diff:
        update_data["modification_diff"] = modification_diff

    result = client.table("bpo_approvals").update(
        update_data
    ).eq("id", approval_id).execute()
    return result.data[0] if result.data else {}


async def reject(
    approval_id: str,
    approver_id: str,
    comment: str | None = None,
    rejection_reason: str | None = None,
) -> dict:
    """却下（理由を記録）"""
    client = get_client()
    update_data = {
        "approver_id": approver_id,
        "status": "rejected",
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    if comment:
        update_data["comment"] = comment
    if rejection_reason:
        update_data["rejection_reason"] = rejection_reason

    result = client.table("bpo_approvals").update(
        update_data
    ).eq("id", approval_id).execute()
    return result.data[0] if result.data else {}


async def approve_with_learning(
    approval_id: str,
    approver_id: str,
    company_id: str,
    comment: str | None = None,
    modification_diff: dict | None = None,
    rejection_reason: str | None = None,
    is_rejection: bool = False,
    pipeline: str | None = None,
    step_name: str | None = None,
    source_execution_id: str | None = None,
) -> dict:
    """承認/却下 + 学習ルール抽出 + learned_rules テーブルへの永続化

    修正diffや却下理由からLLMでルールを抽出し、learned_rules テーブルに INSERT する。
    pipeline が未指定の場合は bpo_approvals.target_type を使用する。
    """
    if is_rejection:
        result = await reject(approval_id, approver_id, comment, rejection_reason)
    else:
        result = await approve(approval_id, approver_id, comment, modification_diff)

    # pipeline が未渡しの場合は bpo_approvals レコードから取得
    resolved_pipeline = pipeline
    if not resolved_pipeline:
        resolved_pipeline = result.get("target_type", "unknown")

    # 修正または却下の場合、学習ルール抽出を試みる
    learned_rule = None
    if modification_diff or rejection_reason:
        try:
            llm = LLMClient()
            context = ""
            rule_type = "correction"

            if modification_diff:
                context = (
                    f"承認者が以下の修正を行いました:\n"
                    f"修正前: {modification_diff.get('before')}\n"
                    f"修正後: {modification_diff.get('after')}"
                )
                rule_type = "correction"
            elif rejection_reason:
                context = f"承認者が以下の理由で却下しました:\n{rejection_reason}"
                rule_type = "preference"

            if context:
                llm_response = await llm.generate(LLMTask(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "あなたは業務ルール抽出エンジンです。"
                                "承認者の修正・却下パターンから、今後のAI処理に適用すべきルールを"
                                "1文で簡潔に抽出してください。\n\n"
                                + context
                            ),
                        }
                    ],
                    tier=ModelTier.FAST,
                    task_type="rule_extraction",
                    company_id=company_id,
                ))
                learned_rule = llm_response.content.strip()

                # ① bpo_approvals に learned_rule を記録（後方互換）
                client = get_client()
                client.table("bpo_approvals").update({
                    "learned_rule": learned_rule,
                }).eq("id", approval_id).execute()

                # ② learned_rules テーブルに永続化
                insert_data: dict = {
                    "company_id": company_id,
                    "pipeline": resolved_pipeline,
                    "rule_type": rule_type,
                    "rule_text": learned_rule,
                    "confidence": 0.7,
                }
                if step_name:
                    insert_data["step_name"] = step_name
                if source_execution_id:
                    insert_data["source_execution_id"] = source_execution_id

                client.table("learned_rules").insert(insert_data).execute()
                logger.info(
                    "learned_rule persisted: company=%s pipeline=%s type=%s",
                    company_id, resolved_pipeline, rule_type,
                )

        except Exception as e:
            logger.warning(f"Failed to extract learned rule: {e}")

    return {**result, "learned_rule": learned_rule}


async def _get_learned_rules(
    company_id: str,
    pipeline: str,
    limit: int = 20,
) -> list[dict]:
    """指定パイプラインの有効な学習ルールを返す。

    パイプライン実行時に knowledge_items と同様にコンテキストへ注入するために使用する。
    返り値は dict のリスト（rule_text / rule_type / confidence / applied_count）。
    """
    client = get_client()
    result = client.table("learned_rules").select(
        "id, rule_text, rule_type, confidence, applied_count, step_name"
    ).eq("company_id", company_id).eq("pipeline", pipeline).eq(
        "is_active", True
    ).order("confidence", desc=True).order(
        "applied_count", desc=True
    ).limit(limit).execute()

    rows = result.data or []

    # 取得したルールの applied_count をインクリメント（SQL 式で一括更新）
    ids = [r["id"] for r in rows]
    if ids:
        try:
            client.rpc(
                "increment_learned_rules_applied_count",
                {"rule_ids": ids},
            ).execute()
        except Exception as e:
            logger.warning("Failed to increment applied_count: %s", e)

    return [
        {
            "rule_text": r["rule_text"],
            "rule_type": r["rule_type"],
            "confidence": float(r["confidence"]),
            "applied_count": r["applied_count"],
            "step_name": r.get("step_name"),
        }
        for r in rows
    ]


async def get_pending_approvals(
    company_id: str,
    target_type: str | None = None,
) -> list[dict]:
    """未承認の申請一覧を取得"""
    client = get_client()
    query = client.table("bpo_approvals").select("*").eq(
        "company_id", company_id
    ).eq("status", "pending")
    if target_type:
        query = query.eq("target_type", target_type)
    result = query.order("requested_at", desc=True).execute()
    return result.data or []


async def get_trust_score(company_id: str, target_type: str) -> TrustScore:
    """指定タイプのTrustScoreを取得"""
    return await TrustScorer.calculate(company_id, target_type)
