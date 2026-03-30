"""What-ifシミュレーションモジュール — ルールベース + LLMベースの二段構成。

- simulate_whatif(snapshot, changes): パラメータ変更の数値影響をルールベースで試算
- run_whatif(company_id, scenario, supabase): 自然言語シナリオをLLMで5次元分析
- simulate_manufacturing_whatif(snapshot, scenario, knowledge_items): 製造業特化シナリオ計算
"""
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from brain.twin.models import TwinSnapshot
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

# 数値フィールドに対するインパクトメッセージ生成ルール
# (dimension, field) -> callable(before_val, after_val, delta_val) -> str
_IMPACT_RULES: dict[tuple[str, str], Any] = {
    ("cost", "monthly_fixed_cost"): lambda b, a, d: (
        f"月次固定費が {abs(d):,}円{'削減' if d < 0 else '増加'}されます"
        + (f"（{abs(d / b) * 100:.1f}%{'削減' if d < 0 else '増加'}）" if b != 0 else "")
    ),
    ("cost", "monthly_variable_cost"): lambda b, a, d: (
        f"月次変動費が {abs(d):,}円{'削減' if d < 0 else '増加'}されます"
    ),
    ("people", "headcount"): lambda b, a, d: (
        f"人員が {abs(d)}名{'減少' if d < 0 else '増員'}されます（{b}名 → {a}名）"
    ),
    ("process", "automation_rate"): lambda b, a, d: (
        f"自動化率が {b:.0%} から {a:.0%} に{'向上' if d > 0 else '低下'}されます"
    ),
}

_MAX_KNOWLEDGE_ITEMS = 40

# 製造業デフォルトパラメータ
_DEFAULT_AVG_ANNUAL_SALARY = 4_000_000          # 平均年収 400万円
_DEFAULT_SOCIAL_INSURANCE_RATE = 0.15           # 社保負担率 15%
_DEFAULT_LEARNING_COEFFICIENT = 0.85            # 習熟係数（新人）
_DEFAULT_AVG_UNIT_PRICE = 50_000                # 平均単価（円）
_DEFAULT_PROFIT_MARGIN = 0.15                   # 利益率 15%
_DEFAULT_PRODUCTION_PER_PERSON_PER_MONTH = 100  # 人あたり月産能力（個）
_DEFAULT_PRODUCTION_PER_MACHINE_PER_MONTH = 200 # 設備1台あたり月産能力（個）
_DEFAULT_EQUIPMENT_MAINTENANCE_COST_RATE = 0.05 # 設備維持費率（投資額に対する年率）
_DEFAULT_MATERIAL_COST_RATIO = 0.4              # 材料費比率（売上に対して）
_DEFAULT_INSPECTION_TIME_HOURS = 0.5            # 1個あたり検査時間（時間）
_DEFAULT_HOURLY_WAGE = 2_000                    # 時給（円）


# ---------------------------------------------------------------------------
# 製造業特化 — データクラス
# ---------------------------------------------------------------------------

@dataclass
class WhatIfScenario:
    """製造業特化のWhat-Ifシナリオ定義。"""

    scenario_type: str          # "headcount" / "equipment" / "demand" / "cost" / "quality"
    parameter: str              # 変更するパラメータ名（例: "manufacturing_headcount"）
    current_value: float        # 現在の値
    new_value: float            # 変更後の値
    metadata: dict[str, Any] = field(default_factory=dict)  # 追加コンテキスト


@dataclass
class Impact:
    """シナリオが各次元に与える影響。"""

    dimension: str              # "people" / "process" / "cost" / "tool" / "risk"
    metric: str                 # "production_capacity" / "labor_cost" 等
    current_value: float        # 現在値
    projected_value: float      # 変化後の予測値
    change_pct: float           # 変化率（%）
    explanation: str            # 影響の説明


@dataclass
class ManufacturingWhatIfResult:
    """製造業What-Ifシミュレーションの実行結果。"""

    scenario: WhatIfScenario
    impacts: list[Impact]
    summary: str                # LLMが生成するサマリー
    confidence: float           # 計算信頼度 0.0-1.0
    assumptions: list[str]      # 前提条件
    risks: list[str]            # リスク
    recommendation: str         # 推奨アクション
    model_used: str = ""
    cost_yen: float = 0.0


# ---------------------------------------------------------------------------
# Pydanticモデル（LLMベース用）
# ---------------------------------------------------------------------------

class DimensionImpact(BaseModel):
    """5次元の各次元への影響。"""
    dimension: str  # people / process / cost / tool / risk
    impact_level: str  # high / medium / low / none
    score_delta: float = 0.0   # -1.0〜+1.0（正=改善、負=悪化）
    description: str = ""
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)


class WhatIfResult(BaseModel):
    """What-Ifシミュレーションの実行結果。"""
    scenario: str
    summary: str
    dimension_impacts: list[DimensionImpact]
    overall_score_delta: float = 0.0  # 全体への影響スコア
    feasibility: str = "medium"       # high / medium / low
    recommended_next_steps: list[str] = Field(default_factory=list)
    model_used: str = ""
    cost_yen: float = 0.0


# ---------------------------------------------------------------------------
# ルールベース実装（Phase 1 MVP）
# ---------------------------------------------------------------------------

async def simulate_whatif(
    snapshot: TwinSnapshot,
    changes: dict,
) -> dict:
    """パラメータ変更のWhat-ifシミュレーションを実行する。

    Args:
        snapshot: 現在のTwinSnapshot
        changes: 変更内容。以下のキーを持つ dict:
            - dimension (str): "people" / "process" / "cost" / "tool" / "risk"
            - field (str): 変更するフィールド名
            - value: 変更後の値

    Returns:
        以下のキーを持つ dict:
        - before (dict): 変更前の次元状態
        - after (dict): 変更後の次元状態
        - delta (dict): 変更差分（数値フィールドのみ）
        - impact_summary (str): 自然言語のインパクト説明

    Raises:
        ValueError: dimension が無効 / field が存在しない場合
    """
    dimension: str = changes.get("dimension", "")
    field: str = changes.get("field", "")
    new_value = changes.get("value")

    # バリデーション
    valid_dimensions = {"people", "process", "cost", "tool", "risk"}
    if dimension not in valid_dimensions:
        raise ValueError(
            f"Invalid dimension '{dimension}'. Must be one of: {valid_dimensions}"
        )

    dim_state = getattr(snapshot, dimension)
    if not hasattr(dim_state, field):
        raise ValueError(
            f"Field '{field}' does not exist in {dimension} dimension."
        )

    # before — 変更前の状態
    before_state = dim_state.model_dump()
    before_val = before_state.get(field)

    # after — フィールドを変更したコピーを生成
    after_data = dict(before_state)
    after_data[field] = new_value
    after_state = after_data

    # delta — 数値の場合のみ差分を計算
    delta: dict = {}
    if isinstance(before_val, (int, float)) and isinstance(new_value, (int, float)):
        delta[field] = new_value - before_val

    # impact_summary
    impact_summary = _build_impact_summary(
        dimension=dimension,
        field=field,
        before_val=before_val,
        after_val=new_value,
        delta=delta.get(field),
    )

    logger.debug(
        "simulate_whatif: company_id=%s dimension=%s field=%s %s→%s",
        snapshot.company_id,
        dimension,
        field,
        before_val,
        new_value,
    )

    return {
        "before": before_state,
        "after": after_state,
        "delta": delta,
        "impact_summary": impact_summary,
    }


def _build_impact_summary(
    dimension: str,
    field: str,
    before_val: Any,
    after_val: Any,
    delta: Any,
) -> str:
    """インパクトサマリーを生成する。

    登録済みルールがあればそれを使い、なければ汎用メッセージを返す。
    """
    rule_key = (dimension, field)
    rule_fn = _IMPACT_RULES.get(rule_key)

    if rule_fn and delta is not None:
        try:
            return rule_fn(before_val, after_val, delta)
        except (ZeroDivisionError, TypeError, ValueError) as exc:
            logger.debug("Impact rule failed: %s", exc)

    # 汎用フォールバック
    if delta is not None:
        direction = "増加" if delta > 0 else "削減"
        return f"{dimension}.{field} が {before_val} から {after_val} に{direction}されます"

    return f"{dimension}.{field} が {before_val!r} から {after_val!r} に変更されます"


# ---------------------------------------------------------------------------
# LLMベース実装（自然言語シナリオ × 5次元影響分析）
# ---------------------------------------------------------------------------

_SYSTEM_WHATIF = """あなたは企業経営のシミュレーション専門家です。
与えられたシナリオが会社の5次元（ヒト/プロセス/コスト/ツール/リスク）に与える影響を分析してください。

## 5次元の定義
- people: 人材・組織（人数、スキル、採用・離職リスク）
- process: 業務プロセス（自動化率、ボトルネック、文書化状況）
- cost: コスト構造（固定費・変動費・投資対効果）
- tool: ITツール・システム（SaaS連携、自動化ツール）
- risk: リスク（コンプライアンス、事業継続、競合）

## 出力形式（JSON）
{
  "summary": "シナリオ全体の要約（100文字以内）",
  "dimension_impacts": [
    {
      "dimension": "people|process|cost|tool|risk",
      "impact_level": "high|medium|low|none",
      "score_delta": -1.0〜+1.0,
      "description": "影響の詳細",
      "risks": ["リスク1"],
      "opportunities": ["機会1"]
    }
  ],
  "overall_score_delta": -1.0〜+1.0,
  "feasibility": "high|medium|low",
  "recommended_next_steps": ["次のステップ1", "次のステップ2"]
}

重要:
- score_delta: +1.0=大幅改善、0=影響なし、-1.0=大幅悪化
- feasibility: 現状のリソースで実現可能かの評価
- 5つの次元全てについて分析すること
- 日本語で出力
"""


def _extract_json_object(content: str) -> str:
    """LLMレスポンスからJSONオブジェクトを抽出する。"""
    text = content.strip()
    # コードブロックを除去
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()
    # JSONオブジェクトを直接探す
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
    return text


async def run_whatif(
    company_id: str,
    scenario: str,
    supabase: Any,
) -> WhatIfResult:
    """What-Ifシナリオを実行し、5次元影響を分析する。

    「もし新拠点を開設したら？」「主力顧客が離脱したら？」などの
    自然言語シナリオをLLMが5次元で定量・定性分析する。

    Args:
        company_id: テナントID（RLS必須）
        scenario: シミュレーションしたいシナリオ（自然言語）
        supabase: Supabaseクライアント

    Returns:
        WhatIfResult — 5次元への影響分析結果
    """
    # 1. 現在の会社状態を取得
    snap_result = supabase.table("company_state_snapshots") \
        .select("*") \
        .eq("company_id", company_id) \
        .order("snapshot_at", desc=True) \
        .limit(1) \
        .execute()
    snapshots = snap_result.data or []

    # 2. 関連ナレッジを取得
    knowledge_result = supabase.table("knowledge_items") \
        .select("id, title, content, department, item_type") \
        .eq("company_id", company_id) \
        .eq("is_active", True) \
        .order("created_at", desc=True) \
        .limit(_MAX_KNOWLEDGE_ITEMS) \
        .execute()
    knowledge_items = knowledge_result.data or []

    # 3. コンテキスト構築
    context = _build_whatif_context(scenario, snapshots, knowledge_items)

    # 4. LLM呼び出し
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": _SYSTEM_WHATIF},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.STANDARD,
        task_type="whatif_simulation",
        company_id=company_id,
        max_tokens=2048,
        temperature=0.3,
    ))

    # 5. レスポンスをパース
    result = _parse_whatif_result(scenario, response.content)
    result.model_used = response.model_used
    result.cost_yen = response.cost_yen

    logger.info(
        "run_whatif: company_id=%s, scenario='%s...', overall_delta=%.2f",
        company_id,
        scenario[:30],
        result.overall_score_delta,
    )

    return result


def _build_whatif_context(
    scenario: str,
    snapshots: list[dict],
    knowledge_items: list[dict],
) -> str:
    """What-If分析用のコンテキスト文字列を構築する。"""
    parts: list[str] = [f"## シミュレーションシナリオ\n{scenario}\n"]

    if snapshots:
        parts.append("\n## 現在の会社状態\n")
        state = snapshots[0]
        for dim in ["people_state", "process_state", "cost_state", "tool_state", "risk_state"]:
            val = state.get(dim)
            if val:
                label = dim.replace("_state", "")
                parts.append(f"- {label}: {json.dumps(val, ensure_ascii=False)[:300]}\n")

    if knowledge_items:
        parts.append("\n## 関連ナレッジ（上位）\n")
        for i, item in enumerate(knowledge_items[:20], 1):
            parts.append(
                f"[{i}] {item.get('title', '')} ({item.get('item_type', '?')}): "
                f"{item.get('content', '')[:150]}\n"
            )

    parts.append("\n上記のシナリオが会社の5次元に与える影響をJSON形式で分析してください。")
    return "".join(parts)


def _parse_whatif_result(scenario: str, content: str) -> WhatIfResult:
    """LLMレスポンスをWhatIfResultにパースする。"""
    try:
        text = _extract_json_object(content)
        data = json.loads(text)

        impacts = []
        for raw_dim in data.get("dimension_impacts", []):
            try:
                impacts.append(DimensionImpact(
                    dimension=str(raw_dim.get("dimension", "operations")),
                    impact_level=str(raw_dim.get("impact_level", "medium")),
                    score_delta=float(raw_dim.get("score_delta", 0.0)),
                    description=str(raw_dim.get("description", "")),
                    risks=raw_dim.get("risks", []),
                    opportunities=raw_dim.get("opportunities", []),
                ))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse dimension impact: %s", e)
                continue

        return WhatIfResult(
            scenario=scenario,
            summary=str(data.get("summary", "シミュレーション完了")),
            dimension_impacts=impacts,
            overall_score_delta=float(data.get("overall_score_delta", 0.0)),
            feasibility=str(data.get("feasibility", "medium")),
            recommended_next_steps=data.get("recommended_next_steps", []),
        )

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning("WhatIf parse failed: %s — returning fallback", e)
        return WhatIfResult(
            scenario=scenario,
            summary=content[:200],
            dimension_impacts=[],
            overall_score_delta=0.0,
            feasibility="medium",
        )


# ---------------------------------------------------------------------------
# 製造業特化 — シナリオ計算エンジン
# ---------------------------------------------------------------------------

def _extract_manufacturing_params(
    snapshot: TwinSnapshot,
    knowledge_items: list[dict],
) -> dict[str, float]:
    """スナップショット + ナレッジから製造業パラメータを抽出する。

    ナレッジから数値を読み取れた場合はそちらを優先し、
    読み取れない場合はデフォルト値を使う。
    """
    params: dict[str, float] = {
        "headcount": float(snapshot.people.headcount) if snapshot.people.headcount else 10.0,
        "monthly_fixed_cost": float(snapshot.cost.monthly_fixed_cost),
        "monthly_variable_cost": float(snapshot.cost.monthly_variable_cost),
        "avg_annual_salary": _DEFAULT_AVG_ANNUAL_SALARY,
        "social_insurance_rate": _DEFAULT_SOCIAL_INSURANCE_RATE,
        "learning_coefficient": _DEFAULT_LEARNING_COEFFICIENT,
        "avg_unit_price": _DEFAULT_AVG_UNIT_PRICE,
        "profit_margin": _DEFAULT_PROFIT_MARGIN,
        "production_per_person": _DEFAULT_PRODUCTION_PER_PERSON_PER_MONTH,
        "production_per_machine": _DEFAULT_PRODUCTION_PER_MACHINE_PER_MONTH,
        "equipment_maintenance_rate": _DEFAULT_EQUIPMENT_MAINTENANCE_COST_RATE,
        "material_cost_ratio": _DEFAULT_MATERIAL_COST_RATIO,
        "inspection_time_hours": _DEFAULT_INSPECTION_TIME_HOURS,
        "hourly_wage": _DEFAULT_HOURLY_WAGE,
        "defect_rate": 0.02,            # 不良率 2%（デフォルト）
        "monthly_production": 0.0,      # 月産量（後で計算）
        "equipment_count": 1.0,         # 設備台数
        "operating_rate": 0.8,          # 稼働率 80%
    }

    # 月産量の推定: headcount × 人あたり生産量
    params["monthly_production"] = (
        params["headcount"] * params["production_per_person"]
    )

    # ナレッジから数値パラメータを上書き
    _override_params_from_knowledge(params, knowledge_items)

    return params


def _override_params_from_knowledge(
    params: dict[str, float],
    knowledge_items: list[dict],
) -> None:
    """ナレッジアイテムから数値パラメータを抽出して params を上書きする。"""
    keyword_map: dict[str, list[str]] = {
        "defect_rate": ["不良率", "不良品率", "歩留"],
        "avg_unit_price": ["平均単価", "単価", "販売価格"],
        "profit_margin": ["利益率", "粗利率", "営業利益率"],
        "hourly_wage": ["時給", "時間単価"],
        "operating_rate": ["稼働率"],
        "material_cost_ratio": ["材料費比率", "材料費率", "原材料比率"],
    }

    number_pattern = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(%|円|割)?")

    for item in knowledge_items:
        content = item.get("content", "") or ""
        title = item.get("title", "") or ""
        text = f"{title} {content}"

        for param_key, keywords in keyword_map.items():
            for kw in keywords:
                if kw in text:
                    m = number_pattern.search(text[text.find(kw):text.find(kw) + 50])
                    if m:
                        raw_val = float(m.group(1))
                        unit = m.group(2) or ""
                        # パーセント表記の場合は 0-1 に変換
                        if unit == "%" and raw_val > 1:
                            raw_val /= 100.0
                        params[param_key] = raw_val
                        break


def _calculate_headcount_impacts(
    scenario: WhatIfScenario,
    p: dict[str, float],
) -> list[Impact]:
    """人員変更シナリオの影響計算。"""
    current_hc = p["headcount"]
    new_hc = scenario.new_value
    delta_hc = new_hc - current_hc
    added_hc = max(0.0, delta_hc)

    # 生産能力（新人の習熟係数を考慮）
    effective_new_hc = current_hc + added_hc * p["learning_coefficient"]
    current_prod = p["monthly_production"]
    new_prod = current_prod * (effective_new_hc / current_hc) if current_hc > 0 else current_prod
    prod_change_pct = (new_prod - current_prod) / current_prod * 100 if current_prod > 0 else 0.0

    # 人件費（年次→月次換算、社保込み）
    annual_cost_per_person = p["avg_annual_salary"] * (1 + p["social_insurance_rate"])
    monthly_cost_per_person = annual_cost_per_person / 12
    current_labor_cost = current_hc * monthly_cost_per_person
    new_labor_cost = new_hc * monthly_cost_per_person
    labor_cost_delta = new_labor_cost - current_labor_cost
    labor_change_pct = (labor_cost_delta / current_labor_cost * 100) if current_labor_cost > 0 else 0.0

    # 利益影響
    prod_delta = new_prod - current_prod
    incremental_revenue = prod_delta * p["avg_unit_price"]
    incremental_profit = incremental_revenue * p["profit_margin"] - labor_cost_delta
    current_monthly_profit = current_prod * p["avg_unit_price"] * p["profit_margin"] - current_labor_cost
    new_monthly_profit = current_monthly_profit + incremental_profit
    profit_change_pct = (
        (new_monthly_profit - current_monthly_profit) / abs(current_monthly_profit) * 100
        if current_monthly_profit != 0 else 0.0
    )

    return [
        Impact(
            dimension="people",
            metric="headcount",
            current_value=current_hc,
            projected_value=new_hc,
            change_pct=round((delta_hc / current_hc * 100) if current_hc > 0 else 0.0, 1),
            explanation=f"製造部門の人員を {current_hc:.0f}名 から {new_hc:.0f}名 に変更します（習熟係数 {p['learning_coefficient']:.1%}）",
        ),
        Impact(
            dimension="process",
            metric="production_capacity",
            current_value=round(current_prod, 1),
            projected_value=round(new_prod, 1),
            change_pct=round(prod_change_pct, 1),
            explanation=f"月産能力が {current_prod:.0f}個 → {new_prod:.0f}個 に変化します（新人習熟係数を考慮）",
        ),
        Impact(
            dimension="cost",
            metric="monthly_labor_cost",
            current_value=round(current_labor_cost),
            projected_value=round(new_labor_cost),
            change_pct=round(labor_change_pct, 1),
            explanation=f"月次人件費が {current_labor_cost:,.0f}円 → {new_labor_cost:,.0f}円（社保込み {(1 + p['social_insurance_rate']):.0%}）",
        ),
        Impact(
            dimension="cost",
            metric="monthly_profit_impact",
            current_value=round(current_monthly_profit),
            projected_value=round(new_monthly_profit),
            change_pct=round(profit_change_pct, 1),
            explanation=(
                f"増産による増収 {incremental_revenue:,.0f}円 から人件費増 {labor_cost_delta:,.0f}円 を差し引いた"
                f" 月次利益影響は {incremental_profit:+,.0f}円"
            ),
        ),
    ]


def _calculate_equipment_impacts(
    scenario: WhatIfScenario,
    p: dict[str, float],
) -> list[Impact]:
    """設備投資シナリオの影響計算。"""
    current_machines = p["equipment_count"]
    new_machines = scenario.new_value
    added_machines = max(0.0, new_machines - current_machines)
    investment_amount = scenario.metadata.get("investment_amount", 5_000_000.0 * added_machines)

    # 生産能力増
    current_prod = p["monthly_production"]
    new_prod = current_prod + added_machines * p["production_per_machine"]
    prod_change_pct = (new_prod - current_prod) / current_prod * 100 if current_prod > 0 else 0.0

    # 年間増産利益
    annual_prod_increase = (new_prod - current_prod) * 12
    annual_incremental_revenue = annual_prod_increase * p["avg_unit_price"]
    annual_incremental_profit = annual_incremental_revenue * p["profit_margin"]

    # 年間維持費
    annual_maintenance = investment_amount * p["equipment_maintenance_rate"]

    # 投資回収期間
    net_annual_gain = annual_incremental_profit - annual_maintenance
    payback_years = (
        investment_amount / net_annual_gain
        if net_annual_gain > 0 else float("inf")
    )

    # ROI
    roi = (net_annual_gain / investment_amount * 100) if investment_amount > 0 else 0.0

    # 稼働率（台数が増えると1台あたり稼働率は下がる可能性があるが、ここでは変動なしと仮定）
    current_utilization = p["operating_rate"]

    return [
        Impact(
            dimension="tool",
            metric="equipment_count",
            current_value=current_machines,
            projected_value=new_machines,
            change_pct=round((added_machines / current_machines * 100) if current_machines > 0 else 0.0, 1),
            explanation=f"設備台数を {current_machines:.0f}台 → {new_machines:.0f}台 に増設します（投資額 {investment_amount:,.0f}円）",
        ),
        Impact(
            dimension="process",
            metric="production_capacity",
            current_value=round(current_prod, 1),
            projected_value=round(new_prod, 1),
            change_pct=round(prod_change_pct, 1),
            explanation=f"月産能力が {current_prod:.0f}個 → {new_prod:.0f}個（+{new_prod - current_prod:.0f}個）",
        ),
        Impact(
            dimension="cost",
            metric="investment_payback_years",
            current_value=0.0,
            projected_value=round(payback_years, 1) if math.isfinite(payback_years) else 999.0,
            change_pct=0.0,
            explanation=(
                f"投資回収期間 {payback_years:.1f}年"
                f"（年間増産利益 {annual_incremental_profit:,.0f}円 - 維持費 {annual_maintenance:,.0f}円 = 純利益 {net_annual_gain:,.0f}円/年）"
                if math.isfinite(payback_years)
                else "投資回収見込みなし（増産利益が維持費を下回っています）"
            ),
        ),
        Impact(
            dimension="cost",
            metric="equipment_roi_pct",
            current_value=0.0,
            projected_value=round(roi, 1),
            change_pct=0.0,
            explanation=f"設備投資 ROI = {roi:.1f}%/年（年間純増益 {net_annual_gain:,.0f}円 ÷ 投資額 {investment_amount:,.0f}円）",
        ),
        Impact(
            dimension="process",
            metric="operating_rate",
            current_value=round(current_utilization, 3),
            projected_value=round(current_utilization, 3),
            change_pct=0.0,
            explanation="設備追加直後は稼働率が低下する可能性がありますが、受注増による回復を想定しています",
        ),
    ]


def _calculate_demand_impacts(
    scenario: WhatIfScenario,
    p: dict[str, float],
) -> list[Impact]:
    """受注変動シナリオの影響計算。"""
    demand_change_rate = (scenario.new_value - scenario.current_value) / scenario.current_value \
        if scenario.current_value != 0 else 0.0
    current_prod = p["monthly_production"]
    required_prod = current_prod * (1 + demand_change_rate)
    prod_increase = required_prod - current_prod

    # 内製可能増産量（稼働率の余力）
    inhouse_capacity_increase = current_prod * (1 - p["operating_rate"])
    outsource_required = max(0.0, prod_increase - inhouse_capacity_increase)

    # 必要追加人員（内製対応分）
    inhouse_increase = min(prod_increase, inhouse_capacity_increase)
    required_additional_people = math.ceil(
        (prod_increase - inhouse_capacity_increase)
        / (p["production_per_person"] * p["operating_rate"])
    ) if prod_increase > inhouse_capacity_increase else 0

    # 必要追加設備
    required_additional_machines = math.ceil(
        max(0.0, prod_increase - inhouse_capacity_increase) / p["production_per_machine"]
    ) if prod_increase > inhouse_capacity_increase else 0

    # 外注費（外注単価は平均単価の80%と仮定）
    outsource_unit_cost = p["avg_unit_price"] * 0.8
    monthly_outsource_cost = outsource_required * outsource_unit_cost

    # 利益への影響
    revenue_increase = prod_increase * p["avg_unit_price"]
    cost_increase = monthly_outsource_cost + (
        required_additional_people
        * p["avg_annual_salary"] * (1 + p["social_insurance_rate"]) / 12
    )
    profit_increase = revenue_increase * p["profit_margin"] - cost_increase

    return [
        Impact(
            dimension="process",
            metric="required_production",
            current_value=round(current_prod, 1),
            projected_value=round(required_prod, 1),
            change_pct=round(demand_change_rate * 100, 1),
            explanation=f"受注 {demand_change_rate:+.0%} 変動により、月産量が {current_prod:.0f}個 → {required_prod:.0f}個 必要になります",
        ),
        Impact(
            dimension="people",
            metric="required_additional_headcount",
            current_value=0.0,
            projected_value=float(required_additional_people),
            change_pct=round(required_additional_people / p["headcount"] * 100, 1) if p["headcount"] > 0 else 0.0,
            explanation=(
                f"内製余力 {inhouse_capacity_increase:.0f}個 で吸収後、追加{required_additional_people}名が必要です"
                if required_additional_people > 0
                else f"現在の稼働率余力（{1 - p['operating_rate']:.0%}）で内製対応可能です"
            ),
        ),
        Impact(
            dimension="tool",
            metric="required_additional_equipment",
            current_value=0.0,
            projected_value=float(required_additional_machines),
            change_pct=0.0,
            explanation=(
                f"内製対応超過分に対して追加設備が {required_additional_machines}台 必要です"
                if required_additional_machines > 0
                else "設備の追加投資は不要です"
            ),
        ),
        Impact(
            dimension="cost",
            metric="monthly_outsource_cost",
            current_value=0.0,
            projected_value=round(monthly_outsource_cost),
            change_pct=0.0,
            explanation=(
                f"外注対応量 {outsource_required:.0f}個 × 外注単価 {outsource_unit_cost:,.0f}円 = 月次外注費 {monthly_outsource_cost:,.0f}円"
                if outsource_required > 0
                else "外注は不要です（内製対応可能）"
            ),
        ),
        Impact(
            dimension="cost",
            metric="monthly_profit_impact",
            current_value=0.0,
            projected_value=round(profit_increase),
            change_pct=0.0,
            explanation=f"増収 {revenue_increase:,.0f}円 から追加コスト {cost_increase:,.0f}円 を差し引いた月次利益影響は {profit_increase:+,.0f}円",
        ),
    ]


def _calculate_cost_change_impacts(
    scenario: WhatIfScenario,
    p: dict[str, float],
) -> list[Impact]:
    """原価変動シナリオの影響計算（材料費変動）。"""
    material_change_rate = (scenario.new_value - scenario.current_value) / scenario.current_value \
        if scenario.current_value != 0 else 0.0

    # 現在の原価率
    current_cost_rate = 1 - p["profit_margin"]
    # 材料費が全原価に占める比率を material_cost_ratio とする
    material_portion_of_cogs = p["material_cost_ratio"]

    # 新しい原価率: 材料費のみ変動
    new_cost_rate = current_cost_rate + current_cost_rate * material_portion_of_cogs * material_change_rate
    new_profit_margin = 1 - new_cost_rate
    profit_margin_delta = new_profit_margin - p["profit_margin"]

    # 月次売上（推定）
    monthly_revenue = p["monthly_production"] * p["avg_unit_price"]

    # 利益減少額
    profit_loss = monthly_revenue * abs(profit_margin_delta) * (-1 if profit_margin_delta < 0 else 1)

    # 利益率を維持するために必要な値上げ額（単価あたり）
    required_price_increase_per_unit = (
        p["avg_unit_price"] * abs(profit_margin_delta)
        if profit_margin_delta < 0 else 0.0
    )

    return [
        Impact(
            dimension="cost",
            metric="material_cost_change_rate",
            current_value=round(p["material_cost_ratio"] * current_cost_rate, 4),
            projected_value=round(p["material_cost_ratio"] * new_cost_rate, 4),
            change_pct=round(material_change_rate * 100, 1),
            explanation=f"材料費が {material_change_rate:+.1%} 変動します（材料費は売上の約 {p['material_cost_ratio']:.0%}）",
        ),
        Impact(
            dimension="cost",
            metric="gross_profit_margin",
            current_value=round(p["profit_margin"] * 100, 2),
            projected_value=round(new_profit_margin * 100, 2),
            change_pct=round(profit_margin_delta * 100, 2),
            explanation=f"原価率が {current_cost_rate:.1%} → {new_cost_rate:.1%} に変化し、粗利率が {p['profit_margin']:.1%} → {new_profit_margin:.1%} になります",
        ),
        Impact(
            dimension="cost",
            metric="monthly_profit_impact",
            current_value=round(monthly_revenue * p["profit_margin"]),
            projected_value=round(monthly_revenue * new_profit_margin),
            change_pct=round(profit_margin_delta / p["profit_margin"] * 100, 1) if p["profit_margin"] != 0 else 0.0,
            explanation=f"月次利益への影響は {profit_loss:+,.0f}円（月次売上 {monthly_revenue:,.0f}円 ベース）",
        ),
        Impact(
            dimension="process",
            metric="required_price_increase_per_unit",
            current_value=p["avg_unit_price"],
            projected_value=round(p["avg_unit_price"] + required_price_increase_per_unit),
            change_pct=round(required_price_increase_per_unit / p["avg_unit_price"] * 100, 1) if p["avg_unit_price"] > 0 else 0.0,
            explanation=(
                f"利益率を維持するために必要な値上げ額は {required_price_increase_per_unit:,.0f}円/個"
                f"（現在単価 {p['avg_unit_price']:,.0f}円 → {p['avg_unit_price'] + required_price_increase_per_unit:,.0f}円）"
                if required_price_increase_per_unit > 0
                else "原価低減のため、値上げは不要です"
            ),
        ),
    ]


def _calculate_quality_impacts(
    scenario: WhatIfScenario,
    p: dict[str, float],
) -> list[Impact]:
    """品質改善シナリオの影響計算。"""
    current_defect_rate = p["defect_rate"]
    new_defect_rate = scenario.new_value
    defect_improvement = current_defect_rate - new_defect_rate  # 改善量（正なら改善）

    monthly_prod = p["monthly_production"]

    # 廃棄コスト削減: 不良率改善 × 月産量 × 平均原価
    avg_unit_cost = p["avg_unit_price"] * (1 - p["profit_margin"])
    waste_cost_reduction = defect_improvement * monthly_prod * avg_unit_cost

    # 再検査工数削減
    inspection_time_reduction_hours = defect_improvement * monthly_prod * p["inspection_time_hours"]
    inspection_cost_reduction = inspection_time_reduction_hours * p["hourly_wage"]

    # 総コスト削減
    total_cost_reduction = waste_cost_reduction + inspection_cost_reduction

    # 実効生産量の向上（不良品が減ることで良品率が上がる）
    current_good_rate = 1 - current_defect_rate
    new_good_rate = 1 - new_defect_rate
    effective_prod_increase = monthly_prod * (new_good_rate - current_good_rate)

    return [
        Impact(
            dimension="process",
            metric="defect_rate",
            current_value=round(current_defect_rate * 100, 2),
            projected_value=round(new_defect_rate * 100, 2),
            change_pct=round(-defect_improvement / current_defect_rate * 100, 1) if current_defect_rate > 0 else 0.0,
            explanation=f"不良率が {current_defect_rate:.1%} → {new_defect_rate:.1%} に改善されます（{defect_improvement:.2%} 削減）",
        ),
        Impact(
            dimension="cost",
            metric="monthly_waste_cost_reduction",
            current_value=0.0,
            projected_value=round(waste_cost_reduction),
            change_pct=0.0,
            explanation=f"廃棄コスト削減: {defect_improvement:.2%} × {monthly_prod:.0f}個/月 × 単価 {avg_unit_cost:,.0f}円 = {waste_cost_reduction:,.0f}円/月",
        ),
        Impact(
            dimension="cost",
            metric="monthly_inspection_cost_reduction",
            current_value=0.0,
            projected_value=round(inspection_cost_reduction),
            change_pct=0.0,
            explanation=f"再検査工数削減: {inspection_time_reduction_hours:.1f}時間/月 × 時給 {p['hourly_wage']:,.0f}円 = {inspection_cost_reduction:,.0f}円/月",
        ),
        Impact(
            dimension="cost",
            metric="total_monthly_cost_reduction",
            current_value=0.0,
            projected_value=round(total_cost_reduction),
            change_pct=0.0,
            explanation=f"月次コスト削減合計: {total_cost_reduction:,.0f}円（廃棄 + 再検査工数）",
        ),
        Impact(
            dimension="process",
            metric="effective_good_units_increase",
            current_value=round(monthly_prod * current_good_rate, 1),
            projected_value=round(monthly_prod * new_good_rate, 1),
            change_pct=round(effective_prod_increase / (monthly_prod * current_good_rate) * 100, 1) if current_good_rate > 0 else 0.0,
            explanation=f"月次良品数が {monthly_prod * current_good_rate:.0f}個 → {monthly_prod * new_good_rate:.0f}個（+{effective_prod_increase:.0f}個）に向上",
        ),
        Impact(
            dimension="risk",
            metric="customer_satisfaction_impact",
            current_value=0.0,
            projected_value=1.0,
            change_pct=0.0,
            explanation="不良率低下により顧客クレーム・返品リスクが軽減され、顧客満足度の向上が期待されます（定性評価）",
        ),
    ]


_SCENARIO_CALCULATORS = {
    "headcount": _calculate_headcount_impacts,
    "equipment": _calculate_equipment_impacts,
    "demand": _calculate_demand_impacts,
    "cost": _calculate_cost_change_impacts,
    "quality": _calculate_quality_impacts,
}

_SYSTEM_MANUFACTURING_WHATIF = """あなたは製造業の経営シミュレーション専門家です。
以下の数値計算結果を受け取り、経営者向けのサマリーと推奨アクションを日本語で生成してください。

## 出力形式（JSON）
{
  "summary": "シナリオ全体の要約（150文字以内）",
  "recommendation": "推奨アクション（200文字以内）",
  "risks": ["リスク1", "リスク2"],
  "assumptions": ["前提条件1", "前提条件2"]
}

重要:
- 数値は丸めて読みやすく表現すること
- 推奨アクションは具体的なネクストアクションを含めること
- 前提条件はデフォルト値を使用している場合に必ず明記すること
- 日本語で出力
"""


def _build_manufacturing_context(
    scenario: WhatIfScenario,
    impacts: list[Impact],
) -> str:
    """LLM向けコンテキスト文字列を構築する。"""
    scenario_labels = {
        "headcount": "人員変更",
        "equipment": "設備投資",
        "demand": "受注変動",
        "cost": "原価変動",
        "quality": "品質改善",
    }
    label = scenario_labels.get(scenario.scenario_type, scenario.scenario_type)

    parts = [
        f"## シナリオ: {label}\n",
        f"- パラメータ: {scenario.parameter}\n",
        f"- 変更: {scenario.current_value} → {scenario.new_value}\n\n",
        "## 計算結果\n",
    ]

    for imp in impacts:
        change_str = f"{imp.change_pct:+.1f}%" if imp.change_pct != 0 else "変動なし/新規"
        parts.append(
            f"- [{imp.dimension}] {imp.metric}: {imp.current_value} → {imp.projected_value}（{change_str}）\n"
            f"  {imp.explanation}\n"
        )

    parts.append("\n上記の計算結果を踏まえ、経営者向けのサマリーと推奨アクションをJSONで生成してください。")
    return "".join(parts)


async def simulate_manufacturing_whatif(
    snapshot: TwinSnapshot,
    scenario: WhatIfScenario,
    knowledge_items: list[dict] | None = None,
    company_id: str | None = None,
) -> ManufacturingWhatIfResult:
    """製造業特化のWhat-Ifシミュレーションを実行する。

    ルールベースの数値計算 + LLMによるサマリー生成の二段構成。

    Args:
        snapshot: 現在のTwinSnapshot
        scenario: シミュレーションシナリオ定義
        knowledge_items: ナレッジアイテムリスト（パラメータ上書きに使用）
        company_id: LLM呼び出し用テナントID（省略時はsnapshotから取得）

    Returns:
        ManufacturingWhatIfResult — 製造業特化の影響分析結果

    Raises:
        ValueError: 不正なシナリオタイプの場合
    """
    if scenario.scenario_type not in _SCENARIO_CALCULATORS:
        raise ValueError(
            f"Unknown scenario_type '{scenario.scenario_type}'. "
            f"Must be one of: {list(_SCENARIO_CALCULATORS.keys())}"
        )

    cid = company_id or snapshot.company_id
    ki = knowledge_items or []

    # 1. ナレッジからパラメータを抽出
    params = _extract_manufacturing_params(snapshot, ki)

    # 2. ルールベース計算
    calculator = _SCENARIO_CALCULATORS[scenario.scenario_type]
    impacts = calculator(scenario, params)

    # 3. デフォルト前提条件
    base_assumptions = [
        f"平均年収 {params['avg_annual_salary']:,.0f}円（ナレッジに数値がない場合のデフォルト）",
        f"社保負担率 {params['social_insurance_rate']:.0%}",
        f"利益率 {params['profit_margin']:.0%}",
        f"月産量 {params['monthly_production']:.0f}個（人あたり {params['production_per_person']:.0f}個/月 × {params['headcount']:.0f}名）",
    ]

    if scenario.scenario_type == "headcount":
        base_assumptions.append(f"新人習熟係数 {params['learning_coefficient']:.0%}")
    elif scenario.scenario_type == "equipment":
        base_assumptions.append(f"設備維持費率 {params['equipment_maintenance_rate']:.0%}/年")
    elif scenario.scenario_type == "demand":
        base_assumptions.append(f"現稼働率 {params['operating_rate']:.0%}（余力 {1 - params['operating_rate']:.0%}）")
    elif scenario.scenario_type == "cost":
        base_assumptions.append(f"材料費比率 {params['material_cost_ratio']:.0%}（売上に対して）")
    elif scenario.scenario_type == "quality":
        base_assumptions.append(
            f"現在の不良率 {params['defect_rate']:.1%}、"
            f"検査時間 {params['inspection_time_hours']:.1f}時間/個、"
            f"時給 {params['hourly_wage']:,.0f}円"
        )

    # 4. LLMによるサマリー生成
    summary = "シミュレーション完了"
    recommendation = "計算結果を確認してください"
    risks: list[str] = []
    model_used = ""
    cost_yen = 0.0

    try:
        context = _build_manufacturing_context(scenario, impacts)
        llm = get_llm_client()
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_MANUFACTURING_WHATIF},
                {"role": "user", "content": context},
            ],
            tier=ModelTier.FAST,
            task_type="manufacturing_whatif",
            company_id=cid,
            max_tokens=1024,
            temperature=0.2,
        ))
        model_used = response.model_used
        cost_yen = response.cost_yen

        parsed = _parse_manufacturing_llm_response(response.content)
        summary = parsed.get("summary", summary)
        recommendation = parsed.get("recommendation", recommendation)
        risks = parsed.get("risks", risks)
        additional_assumptions = parsed.get("assumptions", [])
        base_assumptions.extend(additional_assumptions)

    except Exception as exc:
        logger.warning("Manufacturing WhatIf LLM call failed: %s", exc)

    logger.info(
        "simulate_manufacturing_whatif: company_id=%s, scenario_type=%s, impacts=%d",
        cid,
        scenario.scenario_type,
        len(impacts),
    )

    return ManufacturingWhatIfResult(
        scenario=scenario,
        impacts=impacts,
        summary=summary,
        confidence=0.7,  # ルールベース + デフォルトパラメータの信頼度
        assumptions=base_assumptions,
        risks=risks,
        recommendation=recommendation,
        model_used=model_used,
        cost_yen=cost_yen,
    )


def _parse_manufacturing_llm_response(content: str) -> dict[str, Any]:
    """製造業What-If LLMレスポンスをパースする。"""
    try:
        text = _extract_json_object(content)
        data = json.loads(text)
        return {
            "summary": str(data.get("summary", "")),
            "recommendation": str(data.get("recommendation", "")),
            "risks": [str(r) for r in data.get("risks", [])],
            "assumptions": [str(a) for a in data.get("assumptions", [])],
        }
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning("Manufacturing WhatIf parse failed: %s", e)
        return {}
