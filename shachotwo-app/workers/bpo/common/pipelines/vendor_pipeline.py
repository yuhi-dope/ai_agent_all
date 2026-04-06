"""
共通BPO 取引先管理パイプライン（マイクロエージェント版）

Steps:
  Step 1: vendor_reader       取引先データ取得（直渡し）
  Step 2: score_calculator    取引先スコア計算（支払遅延・取引量・継続年数）
  Step 3: risk_assessor       リスク評価（集中リスク・信用リスク）
  Step 4: output_validator    バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONCENTRATION_RISK_THRESHOLD = 0.30  # 1社への依存が30%超でリスク
PAYMENT_DELAY_RISK_DAYS = 30         # 支払遅延30日超でリスク
MIN_SCORE = 0
MAX_SCORE = 100

# スコア計算重み
SCORE_WEIGHTS = {
    "payment_reliability": 0.40,   # 支払い信頼性（遅延なし=100）
    "transaction_volume": 0.30,    # 取引量（多いほど高評価）
    "relationship_years": 0.20,    # 取引年数（長いほど高評価）
    "incident_history": 0.10,      # トラブル履歴（なし=100）
}

# スコア計算用基準値
_MAX_TRANSACTION_AMOUNT = 50_000_000  # 取引量スコア満点の基準（5000万円）
_MAX_RELATIONSHIP_YEARS = 20          # 取引年数スコア満点の基準（20年）
_MAX_DELAY_DAYS_FOR_SCORE = 60        # 遅延スコアがゼロになる日数基準


@dataclass
class StepResult:
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


@dataclass
class VendorPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    risk_vendors: list[str] = field(default_factory=list)  # リスクフラグ取引先一覧


def _calc_vendor_score(vendor: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """
    取引先スコアを計算する（0〜100）。
    Returns:
        (total_score, sub_scores)
    """
    avg_delay = float(vendor.get("avg_payment_delay_days", 0))
    transaction_amount = float(vendor.get("annual_transaction_amount", 0))
    relationship_years = float(vendor.get("relationship_years", 0))
    incident_count = int(vendor.get("incident_count", 0))

    # 支払い信頼性スコア（遅延ゼロ=100、MAX_DELAY_DAYS_FOR_SCOREで0に線形減少）
    payment_score = max(
        0.0,
        100.0 * (1.0 - avg_delay / _MAX_DELAY_DAYS_FOR_SCORE)
    )

    # 取引量スコア（MAX_TRANSACTION_AMOUNTで満点、線形）
    volume_score = min(100.0, 100.0 * transaction_amount / _MAX_TRANSACTION_AMOUNT)

    # 取引年数スコア（MAX_RELATIONSHIP_YEARSで満点、線形）
    years_score = min(100.0, 100.0 * relationship_years / _MAX_RELATIONSHIP_YEARS)

    # トラブル履歴スコア（0件=100、1件ごとに20点減点）
    incident_score = max(0.0, 100.0 - incident_count * 20.0)

    sub_scores = {
        "payment_reliability": round(payment_score, 1),
        "transaction_volume": round(volume_score, 1),
        "relationship_years": round(years_score, 1),
        "incident_history": round(incident_score, 1),
    }

    total = (
        payment_score * SCORE_WEIGHTS["payment_reliability"]
        + volume_score * SCORE_WEIGHTS["transaction_volume"]
        + years_score * SCORE_WEIGHTS["relationship_years"]
        + incident_score * SCORE_WEIGHTS["incident_history"]
    )
    total = max(float(MIN_SCORE), min(float(MAX_SCORE), total))
    return round(total, 1), sub_scores


def _assess_vendor_risks(
    vendor: dict[str, Any],
    score: float,
) -> list[str]:
    """
    取引先のリスクを評価し、リスクメッセージのリストを返す。
    """
    risks: list[str] = []
    name = vendor.get("vendor_name", vendor.get("vendor_id", "不明"))
    ratio = float(vendor.get("transaction_ratio", 0.0))
    avg_delay = float(vendor.get("avg_payment_delay_days", 0))

    if ratio > CONCENTRATION_RISK_THRESHOLD:
        risks.append(f"{name} への取引集中リスク（{ratio:.0%}）")

    if avg_delay > PAYMENT_DELAY_RISK_DAYS:
        risks.append(f"{name} の支払遅延リスク")

    if score < 60:
        risks.append(f"{name} の総合スコア低リスク（スコア: {score:.1f}）")

    return risks


async def run_vendor_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> VendorPipelineResult:
    """
    取引先管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "vendors": [
                {
                    "vendor_name": str,
                    "vendor_id": str,
                    "annual_transaction_amount": float,
                    "relationship_years": float,
                    "avg_payment_delay_days": float,
                    "incident_count": int,
                    "transaction_ratio": float,  # 0.0〜1.0
                },
                ...
            ]
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "vendor_management",
    }

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> VendorPipelineResult:
        return VendorPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: vendor_reader ────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    vendors: list[dict] = input_data.get("vendors", [])
    s1_out = MicroAgentOutput(
        agent_name="vendor_reader", success=True,
        result={
            "vendors": vendors,
            "count": len(vendors),
            "source": "direct",
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "vendor_reader", "vendor_reader", s1_out)
    if not s1_out.success:
        return _fail("vendor_reader")
    context["vendors"] = vendors

    # ─── Step 2: score_calculator ─────────────────────────────────────
    s2_start = int(time.time() * 1000)
    scored_vendors: list[dict] = []

    for vendor in vendors:
        score, sub_scores = _calc_vendor_score(vendor)
        scored_vendors.append({
            **vendor,
            "score": score,
            "sub_scores": sub_scores,
        })

    s2_out = MicroAgentOutput(
        agent_name="score_calculator", success=True,
        result={
            "scored_vendors": scored_vendors,
            "avg_score": (
                round(sum(v["score"] for v in scored_vendors) / len(scored_vendors), 1)
                if scored_vendors else 0.0
            ),
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "score_calculator", "score_calculator", s2_out)
    if not s2_out.success:
        return _fail("score_calculator")
    context["scored_vendors"] = scored_vendors

    # ─── Step 3: risk_assessor ────────────────────────────────────────
    s3_start = int(time.time() * 1000)
    all_risks: list[str] = []
    risk_vendor_names: list[str] = []
    risk_details: list[dict] = []

    for vendor in scored_vendors:
        vendor_risks = _assess_vendor_risks(vendor, vendor["score"])
        vendor_name = vendor.get("vendor_name", vendor.get("vendor_id", "不明"))

        risk_details.append({
            "vendor_name": vendor_name,
            "vendor_id": vendor.get("vendor_id", ""),
            "score": vendor["score"],
            "risks": vendor_risks,
            "has_risk": len(vendor_risks) > 0,
        })

        if vendor_risks:
            all_risks.extend(vendor_risks)
            if vendor_name not in risk_vendor_names:
                risk_vendor_names.append(vendor_name)

    s3_out = MicroAgentOutput(
        agent_name="risk_assessor", success=True,
        result={
            "risk_details": risk_details,
            "all_risks": all_risks,
            "risk_vendor_names": risk_vendor_names,
            "risk_count": len(all_risks),
        },
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "risk_assessor", "risk_assessor", s3_out)
    if not s3_out.success:
        return _fail("risk_assessor")
    context["risk_details"] = risk_details
    context["risk_vendor_names"] = risk_vendor_names

    # ─── Step 4: output_validator ─────────────────────────────────────
    # scored_vendors全件のスコアが0〜100範囲内か検証
    score_range_errors: list[str] = []
    for sv in scored_vendors:
        s = sv.get("score", -1)
        if not (MIN_SCORE <= s <= MAX_SCORE):
            score_range_errors.append(
                f"{sv.get('vendor_name', sv.get('vendor_id', '不明'))}: "
                f"スコア範囲外（{s}）"
            )

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": {
                "vendor_count": len(scored_vendors),
                "risk_count": len(all_risks),
                "score_range_errors": score_range_errors,
            },
            "required_fields": ["vendor_count", "risk_count"],
            "numeric_fields": ["vendor_count", "risk_count"],
            "positive_fields": [],
            "rules": [
                {"field": "vendor_count", "op": "gte", "value": 0},
                {"field": "risk_count", "op": "gte", "value": 0},
            ],
        },
        context=context,
    ))
    _add_step(4, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"vendor_pipeline complete: {len(vendors)} vendors, "
        f"risk_vendors={len(risk_vendor_names)}, {total_duration}ms"
    )

    return VendorPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "vendor_count": len(vendors),
            "scored_vendors": scored_vendors,
            "risk_details": risk_details,
            "all_risks": all_risks,
            "risk_vendor_names": risk_vendor_names,
            "risk_count": len(all_risks),
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        risk_vendors=risk_vendor_names,
    )
