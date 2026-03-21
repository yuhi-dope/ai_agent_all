"""
建設業 施工計画書AI生成パイプライン（マイクロエージェント版）

Steps:
  Step 1: input_normalizer        入力データ正規化・工事種別判定
  Step 2: template_selector       工事種別に応じたテンプレート選択
  Step 3: hazard_analyzer         工種固有の危険要因分析（LLM）
  Step 4: section_generator       各章の本文生成（LLM） — 施工方針/工法/品質/環境/工程
  Step 5: safety_plan_builder     安全管理計画書生成（危険要因+対策をマージ）
  Step 6: compliance_checker      建設業法・労安法チェック
  Step 7: output_validator        必須記載事項の確認
"""
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 施工計画書の必須フィールド（建設業法施行規則 第14条の2準拠）
REQUIRED_PLAN_FIELDS = [
    "project_name",
    "project_type",
    "construction_policy",
    "construction_methods",
    "safety_management_plan",
    "quality_management_plan",
    "schedule_overview",
]

CONFIDENCE_WARNING_THRESHOLD = 0.70

# プロジェクト種別 → テンプレートカテゴリのマッピング
_PROJECT_TYPE_MAP: dict[str, str] = {
    "土木": "civil",
    "建築": "building",
    "道路": "civil",
    "橋梁": "civil",
    "河川": "civil",
    "上下水道": "civil",
    "造成": "civil",
    "RC造": "building",
    "S造": "building",
    "木造": "building",
    "解体": "building",
    "内装": "building",
    "設備": "building",
}

_OWNER_TYPE_MAP: dict[str, str] = {
    "公共": "public",
    "官公庁": "public",
    "国土交通省": "public",
    "都道府県": "public",
    "市区町村": "public",
    "民間": "private",
    "民": "private",
}

# テンプレート定義（工事種別 × 発注者種別）
_TEMPLATES: dict[str, dict[str, Any]] = {
    "civil_public": {
        "name": "公共土木工事 施工計画書",
        "required_sections": [
            "工事概要",
            "施工方針",
            "施工体制",
            "主要工種の施工方法",
            "品質管理計画",
            "安全管理計画",
            "環境保全対策",
            "工程計画",
            "緊急時対応",
        ],
        "compliance_standards": [
            "公共工事標準仕様書",
            "土木工事施工管理基準",
            "建設工事公衆災害防止対策要綱",
        ],
    },
    "civil_private": {
        "name": "民間土木工事 施工計画書",
        "required_sections": [
            "工事概要",
            "施工方針",
            "施工体制",
            "主要工種の施工方法",
            "品質管理計画",
            "安全管理計画",
            "環境保全対策",
            "工程計画",
        ],
        "compliance_standards": [
            "建設業法",
            "労働安全衛生法",
        ],
    },
    "building_public": {
        "name": "公共建築工事 施工計画書",
        "required_sections": [
            "工事概要",
            "施工方針",
            "施工体制",
            "主要工種の施工方法",
            "品質管理計画",
            "安全管理計画",
            "環境保全対策",
            "工程計画",
            "完成検査対応",
        ],
        "compliance_standards": [
            "公共建築工事標準仕様書",
            "公共建築改修工事標準仕様書",
            "建設工事公衆災害防止対策要綱（建築工事編）",
        ],
    },
    "building_private": {
        "name": "民間建築工事 施工計画書",
        "required_sections": [
            "工事概要",
            "施工方針",
            "施工体制",
            "主要工種の施工方法",
            "品質管理計画",
            "安全管理計画",
            "環境保全対策",
            "工程計画",
        ],
        "compliance_standards": [
            "建設業法",
            "建築基準法",
            "労働安全衛生法",
        ],
    },
}


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
class ConstructionPlanPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'FAIL'} 施工計画書生成パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "FAIL"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_construction_plan_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    site_id: str | None = None,
) -> ConstructionPlanPipelineResult:
    """
    建設業 施工計画書AI生成パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "project_name": str,          # 工事名
            "project_type": str,          # 工種（土木/建築/道路/橋梁/RC造 等）
            "owner_type": str,            # 発注者種別（公共/民間）
            "scale": str,                 # 規模（例: "延床面積3,000m2" "掘削深さ5m"）
            "conditions": str,            # 施工条件の自由記述
            "work_types": list[str],      # 工種リスト（例: ["土工事","型枠工事"]）
            "site_name": str | None,      # 現場名
            "start_date": str | None,     # 工期開始（YYYY-MM-DD）
            "end_date": str | None,       # 工期終了（YYYY-MM-DD）
            "superintendent": str | None, # 現場代理人名
            "safety_manager": str | None, # 安全管理者名
        }
        site_id: 工事現場ID（DBから追加情報を取得する場合）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "site_id": site_id,
    }

    def _add_step(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no,
            step_name=step_name,
            agent_name=agent_name,
            success=out.success,
            result=out.result,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
            warning=warn,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> ConstructionPlanPipelineResult:
        return ConstructionPlanPipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: input_normalizer ────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    project_name: str = input_data.get("project_name", "").strip()
    raw_project_type: str = input_data.get("project_type", "").strip()
    raw_owner_type: str = input_data.get("owner_type", "民間").strip()
    scale: str = input_data.get("scale", "").strip()
    conditions: str = input_data.get("conditions", "").strip()
    work_types: list[str] = input_data.get("work_types", [])
    site_name: str = input_data.get("site_name", project_name)
    start_date: str = input_data.get("start_date", "")
    end_date: str = input_data.get("end_date", "")
    superintendent: str = input_data.get("superintendent", "")
    safety_manager: str = input_data.get("safety_manager", "")

    if not project_name:
        s1_out = MicroAgentOutput(
            agent_name="input_normalizer",
            success=False,
            result={"error": "project_name は必須です"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
        _add_step(1, "input_normalizer", "input_normalizer", s1_out)
        return _fail("input_normalizer")

    # 工事種別の正規化（キーワードマッチ）
    normalized_type = "建築"  # デフォルト
    for keyword, mapped in _PROJECT_TYPE_MAP.items():
        if keyword in raw_project_type:
            normalized_type = keyword
            break

    category = _PROJECT_TYPE_MAP.get(normalized_type, "building")
    owner_category = "private"
    for keyword, mapped in _OWNER_TYPE_MAP.items():
        if keyword in raw_owner_type:
            owner_category = mapped
            break

    template_key = f"{category}_{owner_category}"
    if template_key not in _TEMPLATES:
        template_key = "building_private"

    context.update({
        "project_name": project_name,
        "project_type": normalized_type,
        "raw_project_type": raw_project_type,
        "owner_type": raw_owner_type,
        "category": category,
        "owner_category": owner_category,
        "template_key": template_key,
        "scale": scale,
        "conditions": conditions,
        "work_types": work_types,
        "site_name": site_name,
        "start_date": start_date,
        "end_date": end_date,
        "superintendent": superintendent,
        "safety_manager": safety_manager,
    })

    s1_out = MicroAgentOutput(
        agent_name="input_normalizer",
        success=True,
        result={
            "project_name": project_name,
            "normalized_type": normalized_type,
            "category": category,
            "owner_category": owner_category,
            "template_key": template_key,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "input_normalizer", "input_normalizer", s1_out)

    # ─── Step 2: template_selector ───────────────────────────────────────
    s2_start = int(time.time() * 1000)
    template = _TEMPLATES[template_key]
    context["template"] = template

    s2_out = MicroAgentOutput(
        agent_name="template_selector",
        success=True,
        result={
            "template_key": template_key,
            "template_name": template["name"],
            "sections": template["required_sections"],
            "compliance_standards": template["compliance_standards"],
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "template_selector", "template_selector", s2_out)

    # ─── Step 3: hazard_analyzer ─────────────────────────────────────────
    # 工種固有の危険要因をLLMで分析する
    work_types_str = "、".join(work_types) if work_types else raw_project_type
    hazard_prompt = f"""建設工事の危険要因を分析し、JSON形式で出力してください。

工事名: {project_name}
工事種別: {raw_project_type}
規模: {scale}
施工条件: {conditions}
主要工種: {work_types_str}

以下のJSON形式で出力してください（説明文不要）:
{{
  "hazards": [
    {{
      "category": "墜落・転落" | "倒壊・崩壊" | "挟まれ・巻き込まれ" | "感電" | "熱中症" | "粉塵" | "騒音・振動" | "交通" | "その他",
      "description": "具体的な危険内容",
      "risk_level": "高" | "中" | "低",
      "countermeasures": ["対策1", "対策2"]
    }}
  ],
  "key_risk_summary": "この工事の主要リスクの一文要約"
}}"""

    try:
        from llm.client import get_llm_client, LLMTask, ModelTier
        llm = get_llm_client()
        s3_start = int(time.time() * 1000)
        hazard_response = await llm.generate(LLMTask(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは建設安全管理の専門家です。"
                        "工事種別に応じた具体的な危険要因と対策を分析します。"
                        "必ずJSONのみを出力してください。"
                    ),
                },
                {"role": "user", "content": hazard_prompt},
            ],
            tier=ModelTier.FAST,
            max_tokens=1500,
            temperature=0.2,
            company_id=company_id,
            task_type="hazard_analyzer",
        ))
        raw_json = hazard_response.content.strip()
        # JSON前後の```を除去
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        hazard_data = json.loads(raw_json)
        hazards = hazard_data.get("hazards", [])
        key_risk_summary = hazard_data.get("key_risk_summary", "")
        s3_out = MicroAgentOutput(
            agent_name="hazard_analyzer",
            success=True,
            result={"hazards": hazards, "key_risk_summary": key_risk_summary},
            confidence=0.88,
            cost_yen=hazard_response.cost_yen,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        logger.warning(f"hazard_analyzer LLMエラー（フォールバック使用）: {e}")
        s3_start = int(time.time() * 1000)
        # フォールバック: 基本的な危険要因リスト
        hazards = [
            {
                "category": "墜落・転落",
                "description": "高所作業における墜落・転落",
                "risk_level": "高",
                "countermeasures": ["安全帯の着用徹底", "手すり・足場の適切な設置"],
            },
            {
                "category": "倒壊・崩壊",
                "description": "掘削・解体時の倒壊・崩壊",
                "risk_level": "高",
                "countermeasures": ["適切な土留め工の設置", "定期的な点検実施"],
            },
            {
                "category": "交通",
                "description": "工事車両と歩行者・一般車両の接触",
                "risk_level": "中",
                "countermeasures": ["交通誘導員の配置", "工事看板・バリケードの設置"],
            },
        ]
        key_risk_summary = f"{raw_project_type}工事における一般的な危険要因への対策を実施する。"
        s3_out = MicroAgentOutput(
            agent_name="hazard_analyzer",
            success=True,
            result={"hazards": hazards, "key_risk_summary": key_risk_summary, "fallback": True},
            confidence=0.60,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "hazard_analyzer", "hazard_analyzer", s3_out)
    context["hazards"] = hazards
    context["key_risk_summary"] = key_risk_summary

    # ─── Step 4: section_generator ───────────────────────────────────────
    # 各章の本文をLLMで一括生成する
    sections_prompt = f"""建設工事の施工計画書の各章を生成してください。

【工事情報】
工事名: {project_name}
工事種別: {raw_project_type}
発注者区分: {raw_owner_type}
規模: {scale}
施工条件: {conditions}
主要工種: {work_types_str}
工期: {start_date} 〜 {end_date}
テンプレート: {template["name"]}
適用基準: {", ".join(template["compliance_standards"])}

以下のJSON形式で各章を出力してください（説明文不要）:
{{
  "construction_policy": "施工方針（3〜5文）",
  "construction_methods": {{
    "overview": "施工方法の全体概要（2〜3文）",
    "work_type_details": [
      {{"work_type": "工種名", "method": "施工方法の説明（2〜4文）"}}
    ]
  }},
  "quality_management_plan": {{
    "policy": "品質管理方針（2〜3文）",
    "checkpoints": ["管理項目1", "管理項目2", "管理項目3"]
  }},
  "environmental_measures": {{
    "policy": "環境保全方針（1〜2文）",
    "measures": ["対策1", "対策2", "対策3"]
  }},
  "schedule_overview": "工程計画の概要（2〜4文。工期・主要マイルストーンに言及）",
  "construction_system": {{
    "superintendent": "{superintendent or '（現場代理人名）'}",
    "safety_manager": "{safety_manager or '（安全管理者名）'}",
    "description": "施工体制の説明（1〜2文）"
  }}
}}"""

    try:
        s4_start = int(time.time() * 1000)
        from llm.client import get_llm_client, LLMTask, ModelTier
        llm = get_llm_client()
        sections_response = await llm.generate(LLMTask(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは建設工事の施工計画書作成の専門家です。"
                        "工事内容に即した具体的で実用的な内容を生成してください。"
                        "公共工事の場合は標準仕様書に準拠した表現を用いてください。"
                        "必ずJSONのみを出力してください。"
                    ),
                },
                {"role": "user", "content": sections_prompt},
            ],
            tier=ModelTier.STANDARD,
            max_tokens=3000,
            temperature=0.3,
            company_id=company_id,
            task_type="construction_plan_generator",
        ))
        raw_json = sections_response.content.strip()
        if raw_json.startswith("```"):
            raw_json = raw_json.split("```")[1]
            if raw_json.startswith("json"):
                raw_json = raw_json[4:]
        sections_data = json.loads(raw_json)
        s4_confidence = 0.88
        s4_cost = sections_response.cost_yen
        s4_out = MicroAgentOutput(
            agent_name="section_generator",
            success=True,
            result=sections_data,
            confidence=s4_confidence,
            cost_yen=s4_cost,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        logger.warning(f"section_generator LLMエラー（フォールバック使用）: {e}")
        s4_start = int(time.time() * 1000)
        sections_data = {
            "construction_policy": (
                f"本工事は{raw_project_type}として施工する。"
                "安全・品質・工程・環境の4管理を徹底し、"
                f"発注者の要求品質を満足させることを施工方針とする。"
            ),
            "construction_methods": {
                "overview": f"{raw_project_type}の施工にあたり、仕様書および設計図書に従い施工する。",
                "work_type_details": [
                    {"work_type": wt, "method": f"{wt}については、設計図書に基づき適切に施工する。"}
                    for wt in (work_types or [raw_project_type])
                ],
            },
            "quality_management_plan": {
                "policy": "施工管理基準に基づき、各工種の品質を確保する。",
                "checkpoints": ["材料検査", "施工確認", "完成検査"],
            },
            "environmental_measures": {
                "policy": "工事施工にあたり、周辺環境への影響を最小限に抑える。",
                "measures": ["騒音・振動の低減", "粉塵飛散防止", "廃棄物の適正処理"],
            },
            "schedule_overview": (
                f"工期は{start_date}から{end_date}とする。"
                "準備工→主要工事→後片付けの順に施工し、工程の遅延防止に努める。"
            ),
            "construction_system": {
                "superintendent": superintendent or "（現場代理人名）",
                "safety_manager": safety_manager or "（安全管理者名）",
                "description": "現場代理人の指揮のもと、安全管理者と連携して施工を進める。",
            },
        }
        s4_out = MicroAgentOutput(
            agent_name="section_generator",
            success=True,
            result=sections_data,
            confidence=0.55,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "section_generator", "section_generator", s4_out)
    context["sections"] = sections_data

    # ─── Step 5: safety_plan_builder ─────────────────────────────────────
    # 危険要因分析（Step3）と章本文（Step4）を統合して安全管理計画を構築する
    s5_start = int(time.time() * 1000)
    high_risks = [h for h in hazards if h.get("risk_level") == "高"]
    mid_risks = [h for h in hazards if h.get("risk_level") == "中"]

    safety_management_plan = {
        "policy": f"本工事は{key_risk_summary} 労働安全衛生法および関係法令を遵守し、無災害施工を目指す。",
        "high_risk_items": [
            {
                "hazard": h["description"],
                "category": h["category"],
                "countermeasures": h.get("countermeasures", []),
            }
            for h in high_risks
        ],
        "mid_risk_items": [
            {
                "hazard": h["description"],
                "category": h["category"],
                "countermeasures": h.get("countermeasures", []),
            }
            for h in mid_risks
        ],
        "daily_activities": [
            "朝礼・KY活動の実施（毎日作業開始前）",
            "作業前の危険予知活動（KYK）の実施",
            "安全管理者による巡回点検（午前・午後各1回）",
            "ヒヤリハット・事故報告の即時共有",
        ],
        "emergency_contacts": "緊急時は直ちに現場代理人および安全管理者に報告し、必要に応じて119/110に通報する。",
    }

    s5_out = MicroAgentOutput(
        agent_name="safety_plan_builder",
        success=True,
        result={"safety_management_plan": safety_management_plan},
        confidence=0.92,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "safety_plan_builder", "safety_plan_builder", s5_out)
    context["safety_management_plan"] = safety_management_plan

    # ─── Step 6: compliance_checker ──────────────────────────────────────
    s6_start = int(time.time() * 1000)
    compliance_warnings: list[str] = []
    compliance_ok_items: list[str] = []

    # 現場代理人の配置確認（建設業法第19条の2）
    if not superintendent:
        compliance_warnings.append("現場代理人が未設定です（建設業法第19条の2）")
    else:
        compliance_ok_items.append(f"現場代理人設定済み: {superintendent}")

    # 安全管理者の配置確認（労安法第11条 — 50人以上の現場で必置）
    if not safety_manager:
        compliance_warnings.append(
            "安全管理者が未設定です（労働安全衛生法第11条。"
            "常時50人以上の作業場は専任安全管理者が必置）"
        )
    else:
        compliance_ok_items.append(f"安全管理者設定済み: {safety_manager}")

    # 公共工事の場合: 施工計画書の提出義務確認
    if owner_category == "public":
        compliance_ok_items.append("公共工事: 施工計画書の発注者提出が必要です（着工前）")

    # 高リスク項目がある場合: 特別教育の実施確認
    high_risk_categories = {h["category"] for h in high_risks}
    if "墜落・転落" in high_risk_categories:
        compliance_ok_items.append("高所作業: 特別教育（フルハーネス型安全帯）の受講確認が必要")
    if "感電" in high_risk_categories:
        compliance_ok_items.append("電気工事: 低圧電気取扱業務特別教育の受講確認が必要")

    # 工期チェック
    if not start_date or not end_date:
        compliance_warnings.append("工期（開始日・終了日）が未設定です")

    s6_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={
            "warnings": compliance_warnings,
            "ok_items": compliance_ok_items,
            "passed": len(compliance_warnings) == 0,
            "standards_applied": template["compliance_standards"],
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s6_start,
    )
    _add_step(6, "compliance_checker", "compliance_checker", s6_out)
    context["compliance_warnings"] = compliance_warnings

    # ─── Step 7: output_validator ────────────────────────────────────────
    final_doc: dict[str, Any] = {
        # 基本情報
        "project_name": project_name,
        "project_type": raw_project_type,
        "owner_type": raw_owner_type,
        "scale": scale,
        "conditions": conditions,
        "work_types": work_types,
        "site_name": site_name,
        "start_date": start_date,
        "end_date": end_date,
        "superintendent": superintendent,
        "safety_manager": safety_manager,
        # テンプレート情報
        "template_name": template["name"],
        "sections_required": template["required_sections"],
        "compliance_standards": template["compliance_standards"],
        # 生成コンテンツ
        "construction_policy": sections_data.get("construction_policy", ""),
        "construction_methods": sections_data.get("construction_methods", {}),
        "safety_management_plan": safety_management_plan,
        "quality_management_plan": sections_data.get("quality_management_plan", {}),
        "environmental_measures": sections_data.get("environmental_measures", {}),
        "schedule_overview": sections_data.get("schedule_overview", ""),
        "construction_system": sections_data.get("construction_system", {}),
        # コンプライアンス
        "compliance_warnings": compliance_warnings,
        "compliance_ok_items": compliance_ok_items,
    }

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": final_doc,
            "required_fields": REQUIRED_PLAN_FIELDS,
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"construction_plan_pipeline complete: "
        f"project={project_name}, type={raw_project_type}, "
        f"template={template_key}, {total_duration}ms, "
        f"cost=¥{total_cost_yen:.2f}"
    )

    return ConstructionPlanPipelineResult(
        success=True,
        steps=steps,
        final_output=final_doc,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
    )
