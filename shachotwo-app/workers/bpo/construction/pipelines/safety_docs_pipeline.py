"""
建設業 安全書類パイプライン（マイクロエージェント版）

Steps:
  Step 1: document_ocr            書類テキスト抽出
  Step 2: roster_extractor        作業員名簿データ抽出
  Step 3: qualification_checker   資格有効期限チェック
  Step 4: safety_plan_generator   安全衛生計画書生成（LLM）
  Step 5: compliance_checker      建設業法コンプライアンスチェック
  Step 6: output_validator        必須記載事項チェック
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

REQUIRED_ROSTER_FIELDS = ["site_name", "workers", "as_of_date"]
CONFIDENCE_WARNING_THRESHOLD = 0.70
# 資格有効期限アラート日数
QUALIFICATION_ALERT_DAYS = 90


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
class SafetyDocsPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 安全書類パイプライン",
            f"  ステップ: {len(self.steps)}/6",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "✅" if s.success else "❌"
            warn = f" ⚠️{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_safety_docs_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    site_id: str | None = None,
    doc_type: str = "worker_roster",
) -> SafetyDocsPipelineResult:
    """
    建設業安全書類パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"text": str} または {"workers": list} または {"site_id": str}
        site_id: 工事現場ID（ある場合はDBから作業員情報を取得）
        doc_type: "worker_roster" | "qualification_list" | "safety_plan"
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    today = date.today()
    context: dict[str, Any] = {
        "company_id": company_id,
        "site_id": site_id or input_data.get("site_id"),
        "doc_type": doc_type,
        "as_of_date": today.isoformat(),
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

    def _fail(step_name: str) -> SafetyDocsPipelineResult:
        return SafetyDocsPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: document_ocr ───────────────────────────────────────────
    if "workers" in input_data or context.get("site_id"):
        # 直渡し or サイトIDからDB取得
        context["workers"] = input_data.get("workers", [])
        context["site_name"] = input_data.get("site_name", "")
        steps.append(StepResult(
            step_no=1, step_name="document_ocr", agent_name="document_ocr",
            success=True, result={"source": "direct_or_db"}, confidence=1.0,
            cost_yen=0.0, duration_ms=0,
        ))
    else:
        try:
            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id, agent_name="document_ocr",
                payload={k: v for k, v in input_data.items() if k in ("text", "file_path")},
                context=context,
            ))
        except Exception as e:
            ocr_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        _add_step(1, "document_ocr", "document_ocr", ocr_out)
        if not ocr_out.success:
            return _fail("document_ocr")
        context["raw_text"] = ocr_out.result.get("text", "")

    # ─── Step 2: roster_extractor ────────────────────────────────────────
    s2_start = int(time.time() * 1000)
    if context.get("workers"):
        s2_out = MicroAgentOutput(
            agent_name="roster_extractor", success=True,
            result={"workers": context["workers"], "site_name": context.get("site_name", "")},
            confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
        )
    elif context.get("site_id"):
        # DBから作業員情報を取得
        try:
            from db.supabase import get_service_client
            db = get_service_client()
            site_row = db.table("construction_sites").select("name").eq(
                "id", context["site_id"]
            ).single().execute()
            assignments = db.table("site_worker_assignments").select(
                "worker_id, role, entry_date, construction_workers(id, last_name, first_name, experience_years)"
            ).eq("site_id", context["site_id"]).is_("exit_date", "null").execute()
            workers = []
            for asgn in (assignments.data or []):
                w = asgn.get("construction_workers", {})
                workers.append({
                    "worker_id": asgn.get("worker_id"),
                    "name": f"{w.get('last_name', '')} {w.get('first_name', '')}",
                    "role": asgn.get("role", ""),
                    "entry_date": asgn.get("entry_date", ""),
                    "experience_years": w.get("experience_years", 0),
                })
            site_name = site_row.data.get("name", "") if site_row.data else ""
            s2_out = MicroAgentOutput(
                agent_name="roster_extractor", success=True,
                result={"workers": workers, "site_name": site_name},
                confidence=1.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s2_start,
            )
        except Exception as e:
            s2_out = MicroAgentOutput(
                agent_name="roster_extractor", success=False,
                result={"error": str(e)}, confidence=0.0,
                cost_yen=0.0, duration_ms=int(time.time() * 1000) - s2_start,
            )
    else:
        schema = {
            "site_name": "string",
            "workers": "list[{name: str, role: str, experience_years: int, qualifications: list[str]}]",
        }
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": context.get("raw_text", ""), "schema": schema},
            context=context,
        ))

    _add_step(2, "roster_extractor", "roster_extractor", s2_out)
    if not s2_out.success:
        return _fail("roster_extractor")
    context["workers"] = s2_out.result.get("workers", [])
    context["site_name"] = s2_out.result.get("site_name", "")

    # ─── Step 3: qualification_checker ───────────────────────────────────
    s3_start = int(time.time() * 1000)
    expiring: list[dict] = []
    expired: list[dict] = []
    cutoff = today + timedelta(days=QUALIFICATION_ALERT_DAYS)

    if context.get("site_id"):
        try:
            from db.supabase import get_service_client
            db = get_service_client()
            quals = db.table("worker_qualifications").select(
                "worker_id, qualification_name, expiry_date"
            ).eq("company_id", company_id).not_.is_("expiry_date", "null").execute()
            for q in (quals.data or []):
                if not q.get("expiry_date"):
                    continue
                exp = date.fromisoformat(q["expiry_date"])
                days_left = (exp - today).days
                if days_left < 0:
                    expired.append({**q, "days_left": days_left})
                elif exp <= cutoff:
                    expiring.append({**q, "days_left": days_left})
        except Exception:
            pass

    warnings = []
    if expired:
        warnings.append(f"資格期限切れ: {len(expired)}件")
    if expiring:
        warnings.append(f"資格期限90日以内: {len(expiring)}件")

    s3_out = MicroAgentOutput(
        agent_name="qualification_checker", success=True,
        result={"expiring": expiring, "expired": expired, "warnings": warnings},
        confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "qualification_checker", "qualification_checker", s3_out)
    context["qualification_warnings"] = warnings

    # ─── Step 4: safety_plan_generator ───────────────────────────────────
    s4_start = int(time.time() * 1000)
    if doc_type == "safety_plan":
        work_details = input_data.get("work_details", "一般建設工事")
        gen_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "工事安全衛生計画書",
                "variables": {
                    "site_name": context["site_name"],
                    "work_details": work_details,
                    "worker_count": len(context["workers"]),
                    "as_of_date": context["as_of_date"],
                },
            },
            context=context,
        ))
    else:
        # worker_roster / qualification_list はドキュメント生成スキップ
        gen_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={"skipped": True, "doc_type": doc_type},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    _add_step(4, "safety_plan_generator", "document_generator", gen_out)
    context["generated_doc"] = gen_out.result

    # ─── Step 5: compliance_checker ──────────────────────────────────────
    s5_start = int(time.time() * 1000)
    compliance_warnings: list[str] = []
    # 作業員ゼロチェック
    if not context["workers"]:
        compliance_warnings.append("作業員名簿に作業員が登録されていません")
    # 安全管理者チェック（20人以上の現場は安全管理者が必要）
    if len(context["workers"]) >= 20:
        has_safety_manager = any(
            "安全管理者" in w.get("role", "") or "安全衛生管理者" in w.get("role", "")
            for w in context["workers"]
        )
        if not has_safety_manager:
            compliance_warnings.append("安全管理者が未配置（作業員20名以上の現場は必置）")
    compliance_warnings.extend(context["qualification_warnings"])

    s5_out = MicroAgentOutput(
        agent_name="compliance_checker", success=True,
        result={"warnings": compliance_warnings, "passed": len(compliance_warnings) == 0},
        confidence=1.0, cost_yen=0.0, duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)

    # ─── Step 6: output_validator ────────────────────────────────────────
    doc = {
        "site_name": context["site_name"],
        "workers": context["workers"],
        "as_of_date": context["as_of_date"],
        "doc_type": doc_type,
        "compliance_warnings": compliance_warnings,
        "expiring_qualifications": s3_out.result.get("expiring", []),
        "expired_qualifications": s3_out.result.get("expired", []),
    }
    if gen_out.result.get("content"):
        doc["safety_plan_content"] = gen_out.result["content"]

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": doc,
            "required_fields": REQUIRED_ROSTER_FIELDS,
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"safety_docs_pipeline complete: doc_type={doc_type}, "
        f"workers={len(context['workers'])}, {total_duration}ms"
    )

    return SafetyDocsPipelineResult(
        success=True, steps=steps, final_output=doc,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
    )
