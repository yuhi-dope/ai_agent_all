"""
建設業 許認可申請パイプライン（マイクロエージェント版）

Steps:
  Step 1: document_reader     申請書類・現況データ取得
  Step 2: requirements_check  申請要件確認（経営業務管理責任者・専任技術者・財産的基礎）
  Step 3: qualification_check 技術者資格確認（国家資格・実務経験年数）
  Step 4: track_record_check  工事実績確認（許可業種に対応する実績）
  Step 5: form_generator      申請書類生成（run_document_generator使用）
  Step 6: compliance_checker  建設業法・行政手続コンプライアンス確認
  Step 7: output_validator    バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# ─── 定数 ────────────────────────────────────────────────────────────────────

PERMIT_TYPES = {
    "general": "一般建設業",
    "special": "特定建設業",  # 下請発注4500万円以上
}
PERMIT_VALID_YEARS = 5  # 建設業許可は5年有効
EXPIRY_WARNING_DAYS = 180  # 6ヶ月前アラート
SPECIAL_PERMIT_THRESHOLD = 4_500_000  # 特定建設業の閾値（4500万円）

# 要件チェック項目
REQUIREMENTS = {
    "keieigyo_kanri_sekininsha": "経営業務管理責任者（5年以上の経験）",
    "sennin_gijutsusha": "専任技術者（資格or実務経験10年）",
    "zaisan_teki_kiso": "財産的基礎（純資産500万円以上or500万円以上の資金調達能力）",
    "jyushochu_no_eigyo_basho": "営業所の確保",
    "futekiga_nai": "欠格要件非該当",
}

# 財産的基礎の閾値
GENERAL_NET_ASSET_THRESHOLD = 5_000_000    # 一般建設業: 純資産500万円
SPECIAL_NET_ASSET_THRESHOLD = 20_000_000   # 特定建設業: 純資産2000万円

# 要件チェック項目の必須フィールド
REQUIRED_PERMIT_FIELDS = [
    "company_name", "permit_type", "application_type",
    "license_types", "requirements_met",
]

CONFIDENCE_WARNING_THRESHOLD = 0.70


# ─── データクラス ─────────────────────────────────────────────────────────────

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
class PermitPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_pending: bool = False

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 許認可申請パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" WARNING:{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


# ─── パイプライン本体 ─────────────────────────────────────────────────────────

async def run_permit_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    application_id: str | None = None,
) -> PermitPipelineResult:
    """
    建設業許認可申請パイプライン実行。

    Args:
        company_id: テナントID
        input_data: 申請情報（会社名・許可種別・申請種別・技術者情報等）
        application_id: 申請管理ID（ある場合はDBから既存データを取得）

    input_data の形式:
        {
            "company_name": "山田建設株式会社",
            "permit_type": "general",          # general or special
            "application_type": "renewal",     # new / renewal / change
            "license_types": ["土木工事業", "舗装工事業"],
            "expiry_date": "2026-09-30",       # 現許可の満了日（renewal時）
            "manager": {
                "name": "山田太郎",
                "experience_years": 10,
                "role": "経営業務管理責任者",
            },
            "technicians": [
                {"name": "鈴木次郎", "qualification": "1級土木施工管理技士", "license_types": ["土木工事業"]},
            ],
            "net_assets": 5_000_000,  # 純資産
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "application_id": application_id,
        "today": today.isoformat(),
    }

    def _add_step(step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput) -> StepResult:
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

    def _fail(step_name: str) -> PermitPipelineResult:
        return PermitPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: document_reader ──────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    try:
        company_name = input_data.get("company_name", "")
        permit_type = input_data.get("permit_type", "general")
        application_type = input_data.get("application_type", "new")
        license_types = input_data.get("license_types", [])
        expiry_date_str = input_data.get("expiry_date")
        manager = input_data.get("manager", {})
        technicians = input_data.get("technicians", [])
        net_assets = input_data.get("net_assets", 0)

        context.update({
            "company_name": company_name,
            "permit_type": permit_type,
            "permit_type_label": PERMIT_TYPES.get(permit_type, permit_type),
            "application_type": application_type,
            "license_types": license_types,
            "expiry_date": expiry_date_str,
            "manager": manager,
            "technicians": technicians,
            "net_assets": net_assets,
        })

        s1_out = MicroAgentOutput(
            agent_name="document_reader", success=True,
            result={
                "company_name": company_name,
                "permit_type": permit_type,
                "application_type": application_type,
                "license_types": license_types,
                "technician_count": len(technicians),
            },
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    except Exception as e:
        s1_out = MicroAgentOutput(
            agent_name="document_reader", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s1_start,
        )

    _add_step(1, "document_reader", "document_reader", s1_out)
    if not s1_out.success:
        return _fail("document_reader")

    # ─── Step 2: requirements_check ──────────────────────────────────────
    s2_start = int(time.time() * 1000)
    requirements_warnings: list[str] = []
    requirements_errors: list[str] = []

    try:
        # 経営業務管理責任者: 5年以上の経験が必要
        mgr = context["manager"]
        exp_years = mgr.get("experience_years", 0)
        if exp_years < 5:
            requirements_errors.append(
                f"経営業務管理責任者（{mgr.get('name', '未設定')}）の経験年数が不足しています: "
                f"{exp_years}年（必要: 5年以上）"
            )

        # 専任技術者の存在確認
        if not context["technicians"]:
            requirements_errors.append("専任技術者が登録されていません")

        # 財産的基礎チェック
        net_assets_val = context["net_assets"]
        if context["permit_type"] == "special":
            # 特定建設業: 純資産2000万円以上
            if net_assets_val < SPECIAL_NET_ASSET_THRESHOLD:
                requirements_errors.append(
                    f"特定建設業の財産的基礎不足: 純資産{net_assets_val:,}円"
                    f"（必要: {SPECIAL_NET_ASSET_THRESHOLD:,}円以上）"
                )
        else:
            # 一般建設業: 純資産500万円以上
            if net_assets_val < GENERAL_NET_ASSET_THRESHOLD:
                requirements_warnings.append(
                    f"財産的基礎要確認: 純資産{net_assets_val:,}円"
                    f"（推奨: {GENERAL_NET_ASSET_THRESHOLD:,}円以上）"
                )

        # 更新申請の場合: 満了日チェック
        if context["application_type"] == "renewal" and context["expiry_date"]:
            try:
                expiry = date.fromisoformat(context["expiry_date"])
                days_until_expiry = (expiry - today).days
                if days_until_expiry <= EXPIRY_WARNING_DAYS:
                    requirements_warnings.append(
                        f"許可満了日まで{days_until_expiry}日: "
                        f"満了日{expiry.isoformat()}（早急に更新申請を）"
                    )
            except ValueError:
                requirements_warnings.append(f"満了日の形式が不正: {context['expiry_date']}")

        requirements_met = len(requirements_errors) == 0
        s2_out = MicroAgentOutput(
            agent_name="requirements_check", success=True,
            result={
                "requirements_met": requirements_met,
                "errors": requirements_errors,
                "warnings": requirements_warnings,
                "checked_items": list(REQUIREMENTS.keys()),
            },
            confidence=1.0 if requirements_met else 0.6,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="requirements_check", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "requirements_check", "requirements_check", s2_out)
    if not s2_out.success:
        return _fail("requirements_check")
    context["requirements_errors"] = requirements_errors
    context["requirements_warnings"] = requirements_warnings
    context["requirements_met"] = s2_out.result.get("requirements_met", False)

    # ─── Step 3: qualification_check ─────────────────────────────────────
    s3_start = int(time.time() * 1000)
    qualification_results: list[dict[str, Any]] = []
    qualification_warnings: list[str] = []

    try:
        techs = context["technicians"]
        license_types_needed = set(context["license_types"])

        covered_license_types: set[str] = set()
        for tech in techs:
            q = tech.get("qualification", "")
            tech_licenses = tech.get("license_types", [])
            exp = tech.get("experience_years", 0)

            # 国家資格あり または 実務経験10年以上で要件充足
            has_qualification = bool(q)
            has_experience = exp >= 10

            qualified = has_qualification or has_experience
            result_entry = {
                "name": tech.get("name", ""),
                "qualification": q,
                "experience_years": exp,
                "qualified": qualified,
                "covered_license_types": tech_licenses,
            }
            qualification_results.append(result_entry)

            if qualified:
                covered_license_types.update(tech_licenses)
            else:
                qualification_warnings.append(
                    f"技術者 {tech.get('name', '未設定')}: "
                    f"資格なし・実務経験{exp}年（必要: 資格取得 or 10年以上）"
                )

        # 許可業種をカバーできているか確認
        uncovered = license_types_needed - covered_license_types
        if uncovered:
            qualification_warnings.append(
                f"以下の許可業種の専任技術者が未設定: {', '.join(uncovered)}"
            )

        s3_out = MicroAgentOutput(
            agent_name="qualification_check", success=True,
            result={
                "technicians": qualification_results,
                "covered_license_types": list(covered_license_types),
                "uncovered_license_types": list(uncovered) if uncovered else [],
                "warnings": qualification_warnings,
                "all_qualified": len(qualification_warnings) == 0,
            },
            confidence=1.0 if not qualification_warnings else 0.7,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="qualification_check", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "qualification_check", "qualification_check", s3_out)
    if not s3_out.success:
        return _fail("qualification_check")
    context["qualification_results"] = qualification_results
    context["qualification_warnings"] = qualification_warnings

    # ─── Step 4: track_record_check ──────────────────────────────────────
    s4_start = int(time.time() * 1000)
    track_record_warnings: list[str] = []

    try:
        track_records = input_data.get("track_records", [])
        license_types_needed = set(context["license_types"])

        # 工事実績のある業種を集計
        covered_by_records: set[str] = set()
        for record in track_records:
            lt = record.get("license_type", "")
            if lt:
                covered_by_records.add(lt)

        # 実績のない業種に警告
        no_record_types = license_types_needed - covered_by_records
        if no_record_types and context["application_type"] == "new":
            track_record_warnings.append(
                f"以下の許可業種の工事実績が未登録: {', '.join(no_record_types)}"
                f"（新規申請時は実績があると審査がスムーズです）"
            )

        s4_out = MicroAgentOutput(
            agent_name="track_record_check", success=True,
            result={
                "track_records": track_records,
                "covered_license_types": list(covered_by_records),
                "warnings": track_record_warnings,
            },
            confidence=1.0 if not track_record_warnings else 0.8,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="track_record_check", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "track_record_check", "track_record_check", s4_out)
    if not s4_out.success:
        return _fail("track_record_check")
    context["track_record_warnings"] = track_record_warnings

    # ─── Step 5: form_generator ───────────────────────────────────────────
    form_data = {
        "company_name": context["company_name"],
        "permit_type": context["permit_type_label"],
        "application_type": context["application_type"],
        "license_types": context["license_types"],
        "manager": context["manager"],
        "technicians": context["technicians"],
        "net_assets": context["net_assets"],
        "requirements_met": context["requirements_met"],
        "requirements_errors": context["requirements_errors"],
        "requirements_warnings": context["requirements_warnings"],
    }
    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template_name": "approval_request",
            "data": form_data,
            "format": "markdown",
        },
        context=context,
    ))
    _add_step(5, "form_generator", "document_generator", gen_out)
    if not gen_out.success:
        return _fail("form_generator")
    context["generated_form"] = gen_out.result.get("content", "")

    # ─── Step 6: compliance_checker ──────────────────────────────────────
    s6_start = int(time.time() * 1000)
    compliance_warnings: list[str] = []

    try:
        # 建設業法チェック
        if context["permit_type"] == "special" and context["net_assets"] < SPECIAL_NET_ASSET_THRESHOLD:
            compliance_warnings.append(
                f"建設業法第15条: 特定建設業許可の財産的基礎要件未充足"
                f"（純資産{context['net_assets']:,}円 < {SPECIAL_NET_ASSET_THRESHOLD:,}円）"
            )

        # 経営業務管理責任者の要件（建設業法第7条）
        mgr = context["manager"]
        if mgr.get("experience_years", 0) < 5:
            compliance_warnings.append(
                f"建設業法第7条: 経営業務管理責任者の経験年数不足"
                f"（{mgr.get('experience_years', 0)}年 < 5年）"
            )

        # 更新期限チェック（行政手続）
        if context["application_type"] == "renewal" and context["expiry_date"]:
            try:
                expiry = date.fromisoformat(context["expiry_date"])
                days_until_expiry = (expiry - today).days
                if days_until_expiry < 30:
                    compliance_warnings.append(
                        f"行政手続: 更新申請が許可満了日直前です（残{days_until_expiry}日）。"
                        f"速やかに都道府県知事または国土交通大臣に申請してください。"
                    )
            except ValueError:
                pass

        # 専任技術者の不足チェック
        if context["qualification_warnings"]:
            compliance_warnings.append(
                f"建設業法第7条: 専任技術者要件に問題があります"
            )

        s6_out = MicroAgentOutput(
            agent_name="compliance_checker", success=True,
            result={
                "compliance_passed": len(compliance_warnings) == 0,
                "warnings": compliance_warnings,
                "checked_laws": ["建設業法第7条", "建設業法第15条", "行政手続法"],
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s6_start,
        )
    except Exception as e:
        s6_out = MicroAgentOutput(
            agent_name="compliance_checker", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s6_start,
        )

    _add_step(6, "compliance_checker", "compliance_checker", s6_out)
    context["compliance_warnings"] = compliance_warnings

    # ─── Step 7: output_validator ─────────────────────────────────────────
    final_doc = {
        "company_name": context["company_name"],
        "permit_type": context["permit_type"],
        "application_type": context["application_type"],
        "license_types": context["license_types"],
        "requirements_met": context["requirements_met"],
        "generated_form": context["generated_form"],
    }
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": final_doc,
            "required_fields": REQUIRED_PERMIT_FIELDS,
            "numeric_fields": [],
            "positive_fields": [],
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    all_warnings = (
        requirements_warnings
        + requirements_errors
        + qualification_warnings
        + track_record_warnings
        + compliance_warnings
    )

    logger.info(
        f"permit_pipeline complete: company={context['company_name']}, "
        f"type={context['permit_type_label']}, "
        f"cost=¥{total_cost_yen:.2f}, {total_duration}ms"
    )

    final_output = {
        **final_doc,
        "all_warnings": all_warnings,
        "requirements_errors": requirements_errors,
        "compliance_warnings": compliance_warnings,
    }
    return PermitPipelineResult(
        success=True, steps=steps, final_output=final_output,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
    )
