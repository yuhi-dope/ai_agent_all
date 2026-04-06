"""不動産業 契約書AI自動生成パイプライン

Steps:
  Step 1: terms_reader          取引条件の取得・正規化（必須項目欠損チェック）
  Step 2: template_selector     contract_typeに基づくテンプレート選択・基本条項設定
  Step 3: clause_generator      35条/37条の必須記載事項を取引条件から自動埋め
  Step 4: special_conditions    特約条項のLLM生成（条件マッピング→テンプレ+LLM補完）
  Step 5: risk_checker          全条項の法令違反チェック（宅建業法40条/消費者契約法/借地借家法）
  Step 6: document_generator    Word/PDF出力+電子署名連携用メタデータ付与
  Step 7: output_validator      バリデーション（必須記載事項の完全性チェック）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.compliance import run_compliance_checker
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 宅建業法35条（重要事項説明）の必須記載事項
ARTICLE_35_REQUIRED_ITEMS = [
    "登記権利", "法令制限", "私道負担", "設備インフラ",
    "建物状況調査", "解除条件", "損害賠償", "供託所情報",
]

# 宅建業法37条（契約書面）の必須記載事項
ARTICLE_37_REQUIRED_ITEMS_SALE = [
    "当事者", "物件表示", "代金額", "引渡時期",
    "移転登記申請時期", "契約解除", "損害賠償",
]
ARTICLE_37_REQUIRED_ITEMS_LEASE = [
    "当事者", "物件表示", "賃料額", "引渡時期",
    "敷金礼金", "契約解除", "損害賠償",
]

# 特約自動生成マッピング（条件 → 必要な特約）
SPECIAL_CONDITION_MAPPING: dict[str, list[str]] = {
    "築30年超":          ["契約不適合責任免責特約（築古）"],
    "売主宅建業者":      ["契約不適合責任2年特約（宅建業法40条）"],
    "ローン利用":        ["ローン特約（白紙解除条項）"],
    "引渡前リフォーム":  ["リフォーム条件特約"],
    "借地権付き":        ["借地権承諾特約"],
    "定期借家":          ["定期借家特約（借地借家法38条）"],
    "ペット可":          ["ペット飼育特約（種類・数・原状回復）"],
    "事業用":            ["使用目的制限特約"],
    "テナント退去予定":  ["引渡条件特約（空渡し/居抜き）"],
}

# 法令違反チェックパターン
PROHIBITED_PATTERNS: list[dict[str, str]] = [
    {
        "pattern": "契約不適合責任.*完全免除.*売主.*宅建業者",
        "severity": "error",
        "message": "宅建業法40条違反: 売主が宅建業者の場合、契約不適合責任を完全免除できません（最低2年）",
        "law": "宅建業法40条",
    },
    {
        "pattern": "違約金.*（消費者.*[5-9][0-9]%|消費者.*100%）",
        "severity": "error",
        "message": "消費者契約法9条・10条: 一方的に不利な違約金条項は無効の可能性があります",
        "law": "消費者契約法9条・10条",
    },
    {
        "pattern": "更新拒絶.*無条件",
        "severity": "error",
        "message": "借地借家法28条違反: 更新拒絶には正当事由が必要です",
        "law": "借地借家法28条",
    },
    {
        "pattern": "敷金.*全額.*返還しない",
        "severity": "warning",
        "message": "民法622条の2・判例抵触の可能性: 敷金の全額不返還特約は無効となる場合があります",
        "law": "民法622条の2",
    },
    {
        "pattern": "通常損耗.*借主負担",
        "severity": "warning",
        "message": "原状回復ガイドライン（国交省）・最判H17.12.16: 通常損耗の借主負担特約は無効の可能性があります",
        "law": "民法621条・最判H17.12.16",
    },
]


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
class ContractGenerationResult:
    """契約書AI自動生成パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 契約書AI自動生成パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        risks = self.final_output.get("risk_items", [])
        errors = [r for r in risks if r.get("severity") == "error"]
        warnings = [r for r in risks if r.get("severity") == "warning"]
        lines.append(f"  リスク: error={len(errors)}, warning={len(warnings)}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_contract_generation_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> ContractGenerationResult:
    """
    契約書AI自動生成パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "contract_type": str,          # sale / purchase / lease / sublease / management
            "property_address": str,       # 物件所在地
            "parties": dict,               # {seller/landlord: {name, address}, buyer/tenant: {name, address}}
            "terms": dict,                 # {price, payment_schedule, delivery_date, ...}
            "conditions": list[str],       # 特約生成条件（"築30年超", "ローン利用" 等）
            "is_seller_takken_gyosha": bool,  # 売主が宅建業者か
        }

    Returns:
        ContractGenerationResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {"company_id": company_id}

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms, warning=warn,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> ContractGenerationResult:
        return ContractGenerationResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: terms_reader ────────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": str(input_data),
            "schema": {
                "contract_type": "str",
                "property_address": "str",
                "parties": "dict",
                "terms": "dict",
                "conditions": "list",
            },
            "purpose": "取引条件の正規化・必須項目欠損チェック",
        },
        context=context,
    ))
    _add_step(1, "terms_reader", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("terms_reader")
    normalized_terms = {**input_data, **s1_out.result}
    contract_type = normalized_terms.get("contract_type", "lease")
    context["normalized_terms"] = normalized_terms
    context["contract_type"] = contract_type

    # ─── Step 2: template_selector ───────────────────────────────────────
    s2_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "rule_type": "contract_template_selection",
            "contract_type": contract_type,
            "required_articles_35": ARTICLE_35_REQUIRED_ITEMS,
            "required_articles_37": (
                ARTICLE_37_REQUIRED_ITEMS_SALE
                if contract_type in ("sale", "purchase")
                else ARTICLE_37_REQUIRED_ITEMS_LEASE
            ),
        },
        context=context,
    ))
    _add_step(2, "template_selector", "rule_matcher", s2_out)
    if not s2_out.success:
        return _fail("template_selector")
    selected_template = s2_out.result.get("template", {})
    context["selected_template"] = selected_template

    # ─── Step 3: clause_generator ────────────────────────────────────────
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "契約書_条項生成",
            "variables": {
                "contract_type": contract_type,
                "parties": normalized_terms.get("parties", {}),
                "terms": normalized_terms.get("terms", {}),
                "property_address": normalized_terms.get("property_address", ""),
                "required_items_35": ARTICLE_35_REQUIRED_ITEMS,
                "required_items_37": (
                    ARTICLE_37_REQUIRED_ITEMS_SALE
                    if contract_type in ("sale", "purchase")
                    else ARTICLE_37_REQUIRED_ITEMS_LEASE
                ),
            },
            "purpose": "35条/37条必須記載事項の自動埋め",
        },
        context=context,
    ))
    _add_step(3, "clause_generator", "document_generator", s3_out)
    if not s3_out.success:
        return _fail("clause_generator")
    filled_clauses = s3_out.result.get("clauses", [])
    context["filled_clauses"] = filled_clauses

    # ─── Step 4: special_conditions ──────────────────────────────────────
    conditions: list[str] = normalized_terms.get("conditions", [])
    if normalized_terms.get("is_seller_takken_gyosha"):
        conditions.append("売主宅建業者")
    required_specials: list[str] = []
    for cond in conditions:
        required_specials.extend(SPECIAL_CONDITION_MAPPING.get(cond, []))

    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "特約条項_LLM生成",
            "variables": {
                "contract_type": contract_type,
                "conditions": conditions,
                "required_specials": required_specials,
                "parties": normalized_terms.get("parties", {}),
                "terms": normalized_terms.get("terms", {}),
            },
            "purpose": "特約条項のLLM生成（借地借家法・民法・宅建業法準拠）",
        },
        context=context,
    ))
    _add_step(4, "special_conditions", "document_generator", s4_out)
    if not s4_out.success:
        logger.warning("[contract_generation] 特約条項生成失敗 — 空の特約で続行")
    special_clauses = s4_out.result.get("special_clauses", [])
    context["special_clauses"] = special_clauses
    context["all_clauses"] = filled_clauses + special_clauses

    # ─── Step 5: risk_checker ────────────────────────────────────────────
    s5_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id,
        agent_name="compliance_checker",
        payload={
            "check_type": "contract_law_compliance",
            "clauses": context["all_clauses"],
            "contract_type": contract_type,
            "prohibited_patterns": PROHIBITED_PATTERNS,
            "is_seller_takken_gyosha": normalized_terms.get("is_seller_takken_gyosha", False),
        },
        context=context,
    ))
    _add_step(5, "risk_checker", "compliance_checker", s5_out)
    risk_items = s5_out.result.get("violations", [])
    has_errors = any(r.get("severity") == "error" for r in risk_items)
    context["risk_items"] = risk_items
    if has_errors:
        logger.warning(
            f"[contract_generation] 法令違反が検出されました: "
            f"{[r['message'] for r in risk_items if r.get('severity') == 'error']}"
        )

    # ─── Step 6: document_generator ──────────────────────────────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "契約書_PDF出力",
            "variables": {
                "contract_type": contract_type,
                "parties": normalized_terms.get("parties", {}),
                "terms": normalized_terms.get("terms", {}),
                "property_address": normalized_terms.get("property_address", ""),
                "clauses_35": [c for c in filled_clauses if c.get("article") == "35"],
                "clauses_37": [c for c in filled_clauses if c.get("article") == "37"],
                "special_clauses": special_clauses,
                "risk_items": risk_items,
            },
            "output_format": "pdf",
            "metadata": {
                "electronic_signature_ready": True,
                "contract_type": contract_type,
            },
        },
        context=context,
    ))
    _add_step(6, "document_generator", "document_generator", s6_out)
    context["generated_document"] = s6_out.result

    # ─── Step 7: output_validator ─────────────────────────────────────────
    s7_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "clauses_35": [c for c in filled_clauses if c.get("article") == "35"],
                "clauses_37": [c for c in filled_clauses if c.get("article") == "37"],
                "special_clauses": special_clauses,
            },
            "required_fields": (
                ARTICLE_37_REQUIRED_ITEMS_SALE
                if contract_type in ("sale", "purchase")
                else ARTICLE_37_REQUIRED_ITEMS_LEASE
            ),
            "check_type": "contract_completeness",
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", s7_out)

    final_output = {
        "contract_type": contract_type,
        "normalized_terms": normalized_terms,
        "filled_clauses": filled_clauses,
        "special_clauses": special_clauses,
        "risk_items": risk_items,
        "has_errors": has_errors,
        "generated_document": s6_out.result,
        "validation_result": s7_out.result,
    }

    return ContractGenerationResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )
