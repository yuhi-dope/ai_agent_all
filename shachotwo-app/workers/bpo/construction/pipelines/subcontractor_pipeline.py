"""
建設業 下請管理パイプライン（マイクロエージェント版）

Steps:
  Step 1: subcontractor_reader   下請業者データ取得（直渡し or DB）
  Step 2: license_checker        建設業許可証・期限確認（許可番号・業種・有効期限）
  Step 3: safety_docs_checker    安全書類の有効期限確認（雇用保険・労災保険）
  Step 4: payment_checker        下請代金支払チェック（建設業法：60日以内支払）
  Step 5: compliance_checker     建設業法コンプライアンス確認
  Step 6: output_validator       バリデーション
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.validator import run_output_validator
from workers.micro.compliance import run_compliance_checker

logger = logging.getLogger(__name__)

PAYMENT_DEADLINE_DAYS = 60          # 建設業法：下請代金は60日以内に支払
LICENSE_EXPIRY_WARNING_DAYS = 90    # 許可証90日前アラート
INSURANCE_EXPIRY_WARNING_DAYS = 30  # 保険30日前アラート
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 建設業許可業種コード
CONSTRUCTION_LICENSE_TYPES = [
    "土木工事業", "建築工事業", "大工工事業", "左官工事業",
    "とび・土工工事業", "石工事業", "屋根工事業", "電気工事業",
    "管工事業", "タイル・れんが・ブロック工事業", "鋼構造物工事業",
    "鉄筋工事業", "舗装工事業", "しゅんせつ工事業", "板金工事業",
    "ガラス工事業", "塗装工事業", "防水工事業", "内装仕上工事業",
    "機械器具設置工事業", "熱絶縁工事業", "電気通信工事業",
    "造園工事業", "さく井工事業", "建具工事業", "水道施設工事業",
    "消防施設工事業", "清掃施設工事業", "解体工事業",
]

REQUIRED_SUBCONTRACTOR_FIELDS = [
    "company_name", "license_number", "license_expiry",
    "license_types", "work_type", "contract_amount",
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
class SubcontractorPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    alerts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 下請管理パイプライン",
            f"  ステップ: {len(self.steps)}/6",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        if self.alerts:
            lines.append(f"  アラート数: {len(self.alerts)}")
            for a in self.alerts:
                lines.append(f"    ⚠️ {a}")
        for s in self.steps:
            status = "✅" if s.success else "❌"
            warn = f" ⚠️{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _parse_date(date_str: str | None) -> date | None:
    """YYYY-MM-DD 形式の文字列をdateに変換。失敗時はNone。"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def run_subcontractor_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    contract_id: str | None = None,
    total_contract_amount: int | None = None,
) -> SubcontractorPipelineResult:
    """
    建設業下請管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"subcontractors": list, "contract_date": str}
                    または {"text": str} / {"file_path": str} でOCR→抽出
        contract_id: 工事契約ID（DBから元請契約情報を取得する場合）
        total_contract_amount: 元請代金（一括下請け禁止チェック用）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    today = date.today()
    alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "contract_id": contract_id,
        "total_contract_amount": total_contract_amount,
        "today": today.isoformat(),
    }

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

    def _fail(step_name: str) -> SubcontractorPipelineResult:
        return SubcontractorPipelineResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
            alerts=alerts,
        )

    # ─── Step 1: subcontractor_reader ───────────────────────────────────
    s1_start = int(time.time() * 1000)
    if "subcontractors" in input_data:
        subcontractors = input_data["subcontractors"]
        contract_date_str = input_data.get("contract_date", today.isoformat())
        context["subcontractors"] = subcontractors
        context["contract_date"] = contract_date_str
        steps.append(StepResult(
            step_no=1, step_name="subcontractor_reader", agent_name="subcontractor_reader",
            success=True, result={"source": "direct_data", "count": len(subcontractors)},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        ))
    elif "text" in input_data or "file_path" in input_data:
        # OCR + 構造化抽出
        try:
            from workers.micro.ocr import run_document_ocr
            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id, agent_name="document_ocr",
                payload={k: v for k, v in input_data.items() if k in ("text", "file_path")},
                context=context,
            ))
        except Exception as e:
            ocr_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": str(e)}, confidence=0.0,
                cost_yen=0.0, duration_ms=0,
            )

        if not ocr_out.success:
            steps.append(StepResult(
                step_no=1, step_name="subcontractor_reader", agent_name="document_ocr",
                success=False, result=ocr_out.result, confidence=0.0,
                cost_yen=ocr_out.cost_yen, duration_ms=ocr_out.duration_ms,
            ))
            return _fail("subcontractor_reader")

        raw_text = ocr_out.result.get("text", "")
        schema = {
            "subcontractors": "list[{company_name: str, license_number: str, license_expiry: str, license_types: list[str], work_type: str, contract_amount: number, payment_due_date: str, insurance_expiry: str}]",
            "contract_date": "str (YYYY-MM-DD)",
        }
        extract_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={"text": raw_text, "schema": schema, "domain": "construction_subcontractor"},
            context=context,
        ))

        if not extract_out.success:
            steps.append(StepResult(
                step_no=1, step_name="subcontractor_reader", agent_name="structured_extractor",
                success=False, result=extract_out.result, confidence=0.0,
                cost_yen=ocr_out.cost_yen + extract_out.cost_yen,
                duration_ms=ocr_out.duration_ms + extract_out.duration_ms,
            ))
            return _fail("subcontractor_reader")

        extracted = extract_out.result.get("extracted", {})
        subcontractors = extracted.get("subcontractors", [])
        contract_date_str = extracted.get("contract_date", today.isoformat())
        context["subcontractors"] = subcontractors
        context["contract_date"] = contract_date_str

        steps.append(StepResult(
            step_no=1, step_name="subcontractor_reader", agent_name="structured_extractor",
            success=True,
            result={"source": "ocr_extracted", "count": len(subcontractors)},
            confidence=extract_out.confidence,
            cost_yen=ocr_out.cost_yen + extract_out.cost_yen,
            duration_ms=ocr_out.duration_ms + extract_out.duration_ms,
        ))
    else:
        steps.append(StepResult(
            step_no=1, step_name="subcontractor_reader", agent_name="subcontractor_reader",
            success=False, result={"error": "入力データが不正です（subcontractors / text / file_path のいずれかが必要）"},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        ))
        return _fail("subcontractor_reader")

    subcontractors = context.get("subcontractors", [])
    contract_date = _parse_date(context.get("contract_date")) or today

    # ─── Step 2: license_checker ────────────────────────────────────────
    s2_start = int(time.time() * 1000)
    license_alerts: list[str] = []
    license_results: list[dict[str, Any]] = []

    for sub in subcontractors:
        name = sub.get("company_name", "不明")
        license_number = sub.get("license_number", "")
        license_expiry_str = sub.get("license_expiry", "")
        license_expiry = _parse_date(license_expiry_str)

        sub_result: dict[str, Any] = {
            "company_name": name,
            "license_number": license_number,
            "license_expiry": license_expiry_str,
            "license_status": "ok",
        }

        if not license_number:
            sub_result["license_status"] = "missing"
            license_alerts.append(f"{name}: 建設業許可番号が未登録")
        elif license_expiry:
            days_until_expiry = (license_expiry - today).days
            if days_until_expiry < 0:
                sub_result["license_status"] = "expired"
                sub_result["days_overdue"] = abs(days_until_expiry)
                license_alerts.append(
                    f"{name}: 建設業許可証が期限切れ（{license_expiry_str}、{abs(days_until_expiry)}日超過）"
                )
            elif days_until_expiry <= LICENSE_EXPIRY_WARNING_DAYS:
                sub_result["license_status"] = "expiring_soon"
                sub_result["days_until_expiry"] = days_until_expiry
                license_alerts.append(
                    f"{name}: 建設業許可証が{days_until_expiry}日後に期限切れ（{license_expiry_str}）"
                )

        license_results.append(sub_result)

    alerts.extend(license_alerts)
    has_expired = any(r.get("license_status") == "expired" for r in license_results)
    s2_confidence = 1.0 if not has_expired else 0.5

    s2_out = MicroAgentOutput(
        agent_name="license_checker", success=True,
        result={
            "license_results": license_results,
            "alerts": license_alerts,
            "has_expired_license": has_expired,
        },
        confidence=s2_confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "license_checker", "license_checker", s2_out)
    context["license_results"] = license_results

    # ─── Step 3: safety_docs_checker ────────────────────────────────────
    s3_start = int(time.time() * 1000)
    safety_alerts: list[str] = []
    safety_results: list[dict[str, Any]] = []

    for sub in subcontractors:
        name = sub.get("company_name", "不明")
        insurance_expiry_str = sub.get("insurance_expiry", "")
        insurance_expiry = _parse_date(insurance_expiry_str)

        sub_safety: dict[str, Any] = {
            "company_name": name,
            "insurance_expiry": insurance_expiry_str,
            "insurance_status": "ok",
        }

        if not insurance_expiry_str:
            sub_safety["insurance_status"] = "missing"
            safety_alerts.append(f"{name}: 雇用保険・労災保険の有効期限が未登録")
        elif insurance_expiry:
            days_until_expiry = (insurance_expiry - today).days
            if days_until_expiry < 0:
                sub_safety["insurance_status"] = "expired"
                sub_safety["days_overdue"] = abs(days_until_expiry)
                safety_alerts.append(
                    f"{name}: 保険証書が期限切れ（{insurance_expiry_str}、{abs(days_until_expiry)}日超過）"
                )
            elif days_until_expiry <= INSURANCE_EXPIRY_WARNING_DAYS:
                sub_safety["insurance_status"] = "expiring_soon"
                sub_safety["days_until_expiry"] = days_until_expiry
                safety_alerts.append(
                    f"{name}: 保険証書が{days_until_expiry}日後に期限切れ（{insurance_expiry_str}）"
                )

        safety_results.append(sub_safety)

    alerts.extend(safety_alerts)
    has_expired_insurance = any(r.get("insurance_status") == "expired" for r in safety_results)
    s3_confidence = 1.0 if not has_expired_insurance else 0.6

    s3_out = MicroAgentOutput(
        agent_name="safety_docs_checker", success=True,
        result={
            "safety_results": safety_results,
            "alerts": safety_alerts,
            "has_expired_insurance": has_expired_insurance,
        },
        confidence=s3_confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "safety_docs_checker", "safety_docs_checker", s3_out)
    context["safety_results"] = safety_results

    # ─── Step 4: payment_checker ─────────────────────────────────────────
    s4_start = int(time.time() * 1000)
    payment_alerts: list[str] = []
    payment_results: list[dict[str, Any]] = []

    for sub in subcontractors:
        name = sub.get("company_name", "不明")
        payment_due_date_str = sub.get("payment_due_date", "")
        payment_due_date = _parse_date(payment_due_date_str)
        contract_amount = sub.get("contract_amount", 0)

        sub_payment: dict[str, Any] = {
            "company_name": name,
            "payment_due_date": payment_due_date_str,
            "contract_amount": contract_amount,
            "payment_status": "ok",
        }

        if payment_due_date:
            days_from_contract = (payment_due_date - contract_date).days
            sub_payment["days_from_contract_to_payment"] = days_from_contract

            if days_from_contract > PAYMENT_DEADLINE_DAYS:
                sub_payment["payment_status"] = "overdue_risk"
                sub_payment["overdue_days"] = days_from_contract - PAYMENT_DEADLINE_DAYS
                payment_alerts.append(
                    f"{name}: 下請代金支払期限が建設業法60日ルールを超過（契約日から{days_from_contract}日後、{days_from_contract - PAYMENT_DEADLINE_DAYS}日超過）"
                )
            elif days_from_contract < 0:
                sub_payment["payment_status"] = "already_paid_or_invalid"

        elif not payment_due_date_str:
            sub_payment["payment_status"] = "missing"
            payment_alerts.append(f"{name}: 下請代金支払期限が未設定")

        payment_results.append(sub_payment)

    alerts.extend(payment_alerts)
    has_payment_violation = any(r.get("payment_status") == "overdue_risk" for r in payment_results)
    s4_confidence = 1.0 if not has_payment_violation else 0.5

    s4_out = MicroAgentOutput(
        agent_name="payment_checker", success=True,
        result={
            "payment_results": payment_results,
            "alerts": payment_alerts,
            "has_payment_violation": has_payment_violation,
            "payment_deadline_days": PAYMENT_DEADLINE_DAYS,
        },
        confidence=s4_confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "payment_checker", "payment_checker", s4_out)
    context["payment_results"] = payment_results

    # ─── Step 5: compliance_checker ──────────────────────────────────────
    s5_start = int(time.time() * 1000)
    compliance_alerts: list[str] = []

    # 許可業種と実施工事の整合性チェック
    for sub in subcontractors:
        name = sub.get("company_name", "不明")
        license_number = sub.get("license_number", "")
        license_types: list[str] = sub.get("license_types", [])
        work_type: str = sub.get("work_type", "")

        # 未許可業者への発注
        if not license_number:
            compliance_alerts.append(
                f"{name}: 建設業許可なし業者への発注（建設業法第3条）— 500万円未満の軽微工事のみ可"
            )
            continue

        # 許可業種と実施工事の不一致チェック
        if work_type and license_types:
            matched = any(work_type in lt or lt in work_type for lt in license_types)
            if not matched:
                compliance_alerts.append(
                    f"{name}: 許可業種（{', '.join(license_types)}）と実施工事（{work_type}）が不一致"
                )

    # 一括下請け禁止チェック（下請代金合計が元請代金の大半を超える場合）
    if total_contract_amount and total_contract_amount > 0:
        total_subcontract = sum(sub.get("contract_amount", 0) for sub in subcontractors)
        ratio = total_subcontract / total_contract_amount
        if ratio >= 0.9:
            compliance_alerts.append(
                f"一括下請け禁止の可能性：下請代金合計が元請代金の{ratio:.0%}（建設業法第22条）"
            )
        context["subcontract_ratio"] = ratio

    alerts.extend(compliance_alerts)

    # 建設業法コンプライアンスチェック（既存マイクロエージェントを活用）
    total_subcontract_amount = sum(sub.get("contract_amount", 0) for sub in subcontractors)
    compliance_data = {
        "subcontract_total": total_subcontract_amount,
        "has_special_construction_license": context.get("has_special_construction_license", False),
    }
    comp_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id, agent_name="compliance_checker",
        payload={"data": compliance_data, "industry": "construction", "rules": ["const_001", "const_002"]},
        context=context,
    ))

    micro_comp_violations = comp_out.result.get("violations", [])
    for v in micro_comp_violations:
        compliance_alerts.append(v["message"])
        if v["message"] not in alerts:
            alerts.append(v["message"])

    s5_out = MicroAgentOutput(
        agent_name="compliance_checker", success=True,
        result={
            "alerts": compliance_alerts,
            "micro_compliance": comp_out.result,
            "passed": len([a for a in compliance_alerts if "違反" in a or "違法" in a]) == 0,
        },
        confidence=1.0, cost_yen=comp_out.cost_yen,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "compliance_checker", "compliance_checker", s5_out)
    context["compliance_alerts"] = compliance_alerts

    # ─── Step 6: output_validator ────────────────────────────────────────
    # 各下請業者ごとに必須フィールドを検証
    validation_errors: list[str] = []
    for i, sub in enumerate(subcontractors):
        for field_name in REQUIRED_SUBCONTRACTOR_FIELDS:
            if not sub.get(field_name):
                validation_errors.append(f"下請業者[{i}] {sub.get('company_name', '不明')}: {field_name} が未設定")

    summary_doc = {
        "subcontractor_count": len(subcontractors),
        "total_subcontract_amount": sum(sub.get("contract_amount", 0) for sub in subcontractors),
        "alert_count": len(alerts),
        "validation_errors": validation_errors,
    }

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "document": summary_doc,
            "required_fields": ["subcontractor_count", "total_subcontract_amount"],
            "numeric_fields": ["subcontractor_count", "total_subcontract_amount"],
            "positive_fields": ["subcontractor_count"],
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"subcontractor_pipeline complete: subcontractors={len(subcontractors)}, "
        f"alerts={len(alerts)}, cost=¥{total_cost_yen:.2f}, {total_duration}ms"
    )

    final_output = {
        "subcontractors": subcontractors,
        "license_results": context.get("license_results", []),
        "safety_results": context.get("safety_results", []),
        "payment_results": context.get("payment_results", []),
        "compliance_alerts": context.get("compliance_alerts", []),
        "all_alerts": alerts,
        "total_subcontract_amount": sum(sub.get("contract_amount", 0) for sub in subcontractors),
        "validation_errors": validation_errors,
    }

    return SubcontractorPipelineResult(
        success=True, steps=steps, final_output=final_output,
        total_cost_yen=total_cost_yen, total_duration_ms=total_duration,
        alerts=alerts,
    )
