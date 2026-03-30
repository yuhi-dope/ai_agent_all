"""
共通BPO 退社手続きパイプライン（バックオフィスBPO）

レジストリキー: backoffice/employee_offboarding
トリガー: イベント（退職届受理）/ 手動
承認: 最終給与は承認必要

Steps:
  Step 1: extractor      退職届・退職理由データ取得・構造化
  Step 2: calculator     最終給与・退職金計算（勤続年数×係数 + 日割り + 未消化有給買取）
  Step 3: rule_matcher   社保喪失届・離職票の要否判定（雇用形態・加入状況チェック）
  Step 4: generator      社保喪失届・離職票・源泉徴収票生成
  Step 5: rule_matcher   貸与品回収チェックリスト生成（PC・スマホ・社員証・鍵等）
  Step 6: generator      引継ぎ項目リスト自動生成（担当業務一覧から）
  Step 7: message        退職手続き完了通知

連鎖トリガー: → backoffice/social_insurance（資格喪失届を自動発火）
             → backoffice/account_lifecycle（アカウント完全削除スケジュール）

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション3.3
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.saas_writer import run_saas_writer
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# 退職手続き期限（日数）
DEADLINE_SOCIAL_INSURANCE_LOSS = 5      # 資格喪失届: 退職日翌日から5日以内
DEADLINE_WITHHOLDING_SLIP = 31          # 源泉徴収票: 退職後1ヶ月以内
DEADLINE_EMPLOYMENT_CERTIFICATE = 14   # 退職証明書: 請求後2週間を目安

# 離職票の離職理由コード（主要）
RESIGNATION_REASON_CODES: dict[str, str] = {
    "voluntary": "4D（自己都合退職）",
    "dismissal": "2A（会社都合解雇）",
    "contract_expiry": "2C（契約期間満了）",
    "mutual": "3D（合意退職）",
    "retirement": "6A（定年退職）",
}

# SaaSアカウント無効化対象サービス
DEFAULT_DISABLE_SERVICES = [
    "google_workspace",
    "slack",
    "freee",
    "kintone",
]

# 貸与品マスタ（全社標準 — 会社側でカスタマイズ可能）
DEFAULT_COMPANY_ASSETS = [
    {"item": "会社貸与PC", "category": "IT機器", "requires_data_wipe": True},
    {"item": "スマートフォン", "category": "IT機器", "requires_data_wipe": True},
    {"item": "社員証・IDカード", "category": "セキュリティ", "requires_data_wipe": False},
    {"item": "入館カード・鍵", "category": "セキュリティ", "requires_data_wipe": False},
    {"item": "名刺（未使用分）", "category": "消耗品", "requires_data_wipe": False},
    {"item": "会社クレジットカード", "category": "財務", "requires_data_wipe": False},
    {"item": "制服・作業服", "category": "被服", "requires_data_wipe": False},
]

# 未消化有給の買取単価計算係数（通常給 ÷ 月間所定労働日数 20日）
SCHEDULED_WORK_DAYS_PER_MONTH = 20

# 退職金テーブル（勤続年数ごとの係数 — 会社規程により上書き可能）
RETIREMENT_PAY_COEFFICIENT: dict[int, float] = {
    1: 1.0,
    2: 2.1,
    3: 3.3,
    4: 4.6,
    5: 6.0,
    10: 13.5,
    15: 22.0,
    20: 31.5,
    30: 50.0,
}


def _calc_retirement_pay(monthly_salary: int, years_of_service: int) -> int:
    """
    勤続年数から退職金を計算する。

    係数テーブルの補間（最近傍）を使用。
    months_salary × coefficient で算出。
    """
    if years_of_service <= 0:
        return 0
    keys = sorted(RETIREMENT_PAY_COEFFICIENT.keys())
    coeff = RETIREMENT_PAY_COEFFICIENT[keys[0]]
    for k in keys:
        if years_of_service >= k:
            coeff = RETIREMENT_PAY_COEFFICIENT[k]
        else:
            break
    return int(monthly_salary * coeff)


@dataclass
class OffboardingPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    employee_name: str = ""
    final_salary: Decimal = field(default_factory=lambda: Decimal("0"))
    unused_pto_payout: Decimal = field(default_factory=lambda: Decimal("0"))
    retirement_pay_calculated: int = 0
    documents: list[dict[str, Any]] = field(default_factory=list)
    assets_to_recover: list[dict[str, Any]] = field(default_factory=list)
    handover_items: list[str] = field(default_factory=list)
    accounts_to_disable: list[dict[str, Any]] = field(default_factory=list)
    social_insurance_triggered: bool = False
    account_lifecycle_triggered: bool = False
    chain_triggers: list[dict[str, Any]] = field(default_factory=list)
    filing_deadlines: list[dict[str, Any]] = field(default_factory=list)
    approval_required: bool = True   # 最終給与は承認必要

    def to_offboarding_summary(self) -> str:
        extra = [
            f"  従業員: {self.employee_name}",
            f"  最終給与: Y{self.final_salary:,}（承認待ち）",
            f"  未消化有給買取: Y{self.unused_pto_payout:,}",
            f"  退職金: Y{self.retirement_pay_calculated:,}",
            f"  書類: {len(self.documents)}件",
            f"  回収貸与品: {len(self.assets_to_recover)}件",
            f"  引継ぎ項目: {len(self.handover_items)}件",
            f"  社保連鎖: {'発火' if self.social_insurance_triggered else '未発火'}",
        ]
        return pipeline_summary(
            label="退社手続きパイプライン",
            total_steps=7,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_employee_offboarding_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> OffboardingPipelineResult:
    """
    退社手続きパイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            "employee_name"            (str):  従業員氏名
            "employee_id"              (str):  従業員ID（SmartHR）
            "employee_email"           (str):  従業員メールアドレス
            "retirement_date"          (str):  退職日（YYYY-MM-DD）
            "last_work_date"           (str):  最終出勤日（省略時=退職日）
            "resignation_reason"       (str):  離職理由キー（voluntary/dismissal/contract_expiry/mutual/retirement）
            "resignation_document_text" (str): 退職届のOCRテキスト（省略可）
            "employment_type"          (str):  雇用形態（"正社員" | "契約社員" | "パート"）
            "monthly_salary"           (int):  月給（円、最終給与計算用）
            "years_of_service"         (int):  勤続年数（退職金計算用）
            "unused_pto_days"          (int):  未消化有給日数
            "retirement_pay_override"  (int):  退職金上書き値（省略時は勤続年数テーブルで自動計算）
            "worked_days_in_month"     (int):  当月勤務日数（日割り計算用）
            "scheduled_days_in_month"  (int):  当月所定労働日数（デフォルト20）
            "social_insurance_enrolled" (bool): 社会保険加入済みかどうか
            "employment_insurance_enrolled" (bool): 雇用保険加入済みかどうか
            "assigned_tasks"           (list): 担当業務リスト（引継ぎ生成用）
            "company_assets"           (list): 貸与品マスタ（省略時はデフォルト）
            "saas_services"            (list): 無効化するSaaSサービス（省略時はデフォルト）
            "send_address"             (str):  書類郵送先住所
            "dry_run"                  (bool): True=SmartHR/SaaS操作を実行しない
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, OffboardingPipelineResult)

    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "employee_offboarding",
        "dry_run": input_data.get("dry_run", False),
    }

    employee_name: str = input_data.get("employee_name", "")
    employee_id: str = input_data.get("employee_id", "")
    employee_email: str = input_data.get("employee_email", "")
    resignation_reason: str = input_data.get("resignation_reason", "voluntary")
    employment_type: str = input_data.get("employment_type", "正社員")
    monthly_salary: int = input_data.get("monthly_salary", 0)
    years_of_service: int = input_data.get("years_of_service", 0)
    unused_pto_days: int = input_data.get("unused_pto_days", 0)
    retirement_pay_override: int | None = input_data.get("retirement_pay_override")
    worked_days: int = input_data.get("worked_days_in_month", 0)
    scheduled_days: int = input_data.get("scheduled_days_in_month", SCHEDULED_WORK_DAYS_PER_MONTH)
    social_insurance_enrolled: bool = input_data.get("social_insurance_enrolled", True)
    employment_insurance_enrolled: bool = input_data.get("employment_insurance_enrolled", True)
    assigned_tasks: list[str] = input_data.get("assigned_tasks", [])
    company_assets: list[dict] = input_data.get("company_assets", DEFAULT_COMPANY_ASSETS)
    saas_services: list[str] = input_data.get("saas_services", DEFAULT_DISABLE_SERVICES)
    send_address: str = input_data.get("send_address", "")
    dry_run: bool = context["dry_run"]

    # 退職日・最終出勤日をパース
    today = date.today()
    try:
        retirement_date = date.fromisoformat(input_data["retirement_date"]) if input_data.get("retirement_date") else today
    except ValueError:
        retirement_date = today
    try:
        last_work_date = date.fromisoformat(input_data["last_work_date"]) if input_data.get("last_work_date") else retirement_date
    except ValueError:
        last_work_date = retirement_date

    context.update({
        "employee_name": employee_name,
        "retirement_date": retirement_date.isoformat(),
        "last_work_date": last_work_date.isoformat(),
    })

    # ─── Step 1: extractor ── 退職届・退職理由データ取得・構造化 ──────────────
    resignation_doc_text: str = input_data.get("resignation_document_text", "")
    if not resignation_doc_text:
        # テキストがない場合は入力データを構造化テキストとして構築
        resignation_doc_text = (
            f"氏名: {employee_name}\n"
            f"退職日: {retirement_date.isoformat()}\n"
            f"退職理由: {resignation_reason}\n"
            f"部署: {input_data.get('department', '')}\n"
            f"役職: {input_data.get('job_title', '')}\n"
            f"雇用形態: {employment_type}\n"
            f"勤続年数: {years_of_service}年\n"
        )

    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id, agent_name="structured_extractor",
        payload={
            "text": resignation_doc_text,
            "schema": {
                "employee_name": "退職者氏名",
                "retirement_date": "退職日（YYYY-MM-DD）",
                "resignation_reason": "退職理由（voluntary/dismissal/contract_expiry/mutual/retirement）",
                "department": "部署名",
                "job_title": "役職・職種",
                "employment_type": "雇用形態",
                "years_of_service": "勤続年数（整数）",
                "assigned_tasks_summary": "担当業務の概要",
            },
            "hint": "退職届・退職願から退職情報を構造化抽出",
        },
        context=context,
    ))
    record_step(1, "resignation_extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return emit_fail("resignation_extractor")

    # 抽出結果で入力を補完
    extracted: dict[str, Any] = s1_out.result
    employee_name = employee_name or extracted.get("employee_name", "")
    context["employee_name"] = employee_name

    # ─── Step 2: calculator ── 最終給与・退職金計算 ───────────────────────────
    try:
        scheduled_days_dec = Decimal(str(max(scheduled_days, 1)))
        daily_rate = Decimal(str(monthly_salary)) / scheduled_days_dec
        prorated_salary = daily_rate * Decimal(str(worked_days))
        pto_payout = daily_rate * Decimal(str(unused_pto_days))

        # 退職金: 上書き値があれば使用、なければ勤続年数テーブルで計算
        if retirement_pay_override is not None:
            retirement_pay = retirement_pay_override
        else:
            retirement_pay = _calc_retirement_pay(monthly_salary, years_of_service)

        final_salary_total = prorated_salary + pto_payout + Decimal(str(retirement_pay))

        s2_result: dict[str, Any] = {
            "monthly_salary": monthly_salary,
            "worked_days": worked_days,
            "scheduled_days": scheduled_days,
            "daily_rate": int(daily_rate),
            "prorated_salary": int(prorated_salary),
            "unused_pto_days": unused_pto_days,
            "pto_payout": int(pto_payout),
            "years_of_service": years_of_service,
            "retirement_pay": retirement_pay,
            "final_salary_total": int(final_salary_total),
            "approval_required": True,
        }
        s2_out = MicroAgentOutput(
            agent_name="calculator",
            success=True,
            result=s2_result,
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=0,
        )
    except Exception as exc:
        logger.error(f"offboarding: 最終給与計算失敗 ({employee_name}): {exc}")
        s2_out = MicroAgentOutput(
            agent_name="calculator",
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=0,
        )

    record_step(2, "final_salary_calculator", "calculator", s2_out)
    if not s2_out.success:
        return emit_fail("final_salary_calculator")

    final_salary = Decimal(str(s2_out.result["final_salary_total"]))
    pto_payout_amount = Decimal(str(s2_out.result["pto_payout"]))
    retirement_pay_amount: int = s2_out.result["retirement_pay"]
    context["final_salary"] = int(final_salary)

    # ─── Step 3: rule_matcher ── 社保喪失届・離職票の要否判定 ────────────────
    need_si_loss = social_insurance_enrolled and employment_type in ("正社員", "契約社員")
    # 雇用保険: 自己都合でも会社都合でも離職票は原則発行義務あり
    need_employment_cert = employment_insurance_enrolled
    # 自己都合以外は離職票が特に重要（失業給付に影響）
    is_company_reason = resignation_reason in ("dismissal", "contract_expiry")

    si_rules: list[dict[str, Any]] = [
        {
            "rule_id": "OFF-001",
            "rule_name": "社会保険資格喪失届要否",
            "condition": f"enrolled={social_insurance_enrolled}, type={employment_type}",
            "matched": need_si_loss,
            "result": "要届出（退職日翌日から5日以内）" if need_si_loss else "不要",
        },
        {
            "rule_id": "OFF-002",
            "rule_name": "離職票発行要否",
            "condition": f"enrolled={employment_insurance_enrolled}",
            "matched": need_employment_cert,
            "result": "要発行" if need_employment_cert else "不要（雇用保険未加入）",
        },
        {
            "rule_id": "OFF-003",
            "rule_name": "会社都合退職（給付優遇）",
            "condition": f"reason={resignation_reason}",
            "matched": is_company_reason,
            "result": "会社都合コード付与（給付制限なし）" if is_company_reason else "自己都合コード",
        },
    ]

    s3_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "need_social_insurance_loss": need_si_loss,
            "need_employment_certificate": need_employment_cert,
            "is_company_reason": is_company_reason,
            "rules_evaluated": si_rules,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(3, "insurance_requirement_matcher", "rule_matcher", s3_out)
    context["need_si_loss"] = need_si_loss
    context["need_employment_cert"] = need_employment_cert

    # ─── Step 4: generator ── 社保喪失届・離職票・源泉徴収票生成 ─────────────
    reason_code = RESIGNATION_REASON_CODES.get(resignation_reason, RESIGNATION_REASON_CODES["voluntary"])
    documents_to_generate: list[str] = ["源泉徴収票", "退職証明書"]
    if need_si_loss:
        documents_to_generate.append("健康保険・厚生年金保険被保険者資格喪失届")
    if need_employment_cert:
        documents_to_generate.append("離職票")

    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "退職関連書類セット",
            "variables": {
                "employee_name": employee_name,
                "employee_id": employee_id,
                "retirement_date": retirement_date.isoformat(),
                "job_title": input_data.get("job_title", ""),
                "department": input_data.get("department", ""),
                "final_salary": int(final_salary),
                "prorated_salary": s2_out.result.get("prorated_salary", 0),
                "pto_payout": int(pto_payout_amount),
                "retirement_pay": retirement_pay_amount,
                "resignation_reason_code": reason_code,
                "is_company_reason": is_company_reason,
                "monthly_salary": monthly_salary,
                "documents": documents_to_generate,
            },
        },
        context=context,
    ))
    record_step(4, "retirement_docs_generator", "document_generator", s4_out)
    if not s4_out.success:
        return emit_fail("retirement_docs_generator")

    generated_docs: list[dict[str, Any]] = [
        {"type": doc, "pdf_path": s4_out.result.get(f"{doc}_path", "")}
        for doc in documents_to_generate
    ]

    # ─── Step 5: rule_matcher ── 貸与品回収チェックリスト生成 ────────────────
    # 会社から貸与された品目を照合して回収リストを生成
    assets_to_recover: list[dict[str, Any]] = []
    for asset in company_assets:
        assets_to_recover.append({
            "item": asset["item"],
            "category": asset.get("category", "その他"),
            "requires_data_wipe": asset.get("requires_data_wipe", False),
            "recovery_deadline": last_work_date.isoformat(),
            "status": "pending",
            "notes": "データ消去確認必要" if asset.get("requires_data_wipe") else "",
        })

    s5_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "assets_to_recover": assets_to_recover,
            "total_items": len(assets_to_recover),
            "data_wipe_required": sum(1 for a in assets_to_recover if a["requires_data_wipe"]),
            "recovery_deadline": last_work_date.isoformat(),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(5, "asset_recovery_matcher", "rule_matcher", s5_out)
    context["assets_to_recover"] = assets_to_recover

    # ─── Step 6: generator ── 引継ぎ項目リスト自動生成 ───────────────────────
    task_list = assigned_tasks or extracted.get("assigned_tasks_summary", "")
    if not task_list:
        task_list = input_data.get("job_title", "担当業務")

    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "業務引継ぎリスト",
            "variables": {
                "employee_name": employee_name,
                "retirement_date": retirement_date.isoformat(),
                "department": input_data.get("department", ""),
                "job_title": input_data.get("job_title", ""),
                "assigned_tasks": task_list,
                "prompt": (
                    f"{employee_name}（{input_data.get('job_title', '')}）の退職に伴う"
                    "業務引継ぎリストを作成してください。"
                    f"担当業務: {task_list}\n"
                    "各業務について: 業務概要・引継ぎ先・期限・注意事項を含めること。"
                ),
            },
        },
        context=context,
    ))
    record_step(6, "handover_list_generator", "document_generator", s6_out)
    if not s6_out.success:
        logger.warning(f"offboarding: 引継ぎリスト生成失敗 ({employee_name}) — フォールバック使用")

    handover_raw = s6_out.result.get("content", "")
    handover_items: list[str] = (
        handover_raw if isinstance(handover_raw, list)
        else [ln.strip() for ln in str(handover_raw).split("\n") if ln.strip()]
    )
    if not handover_items and assigned_tasks:
        # フォールバック: 担当業務を引継ぎ項目の雛形として使用
        handover_items = [f"【要引継ぎ】{task}" for task in assigned_tasks]
    context["handover_items"] = handover_items

    # ─── Step 7: message ── 退職手続き完了通知 ──────────────────────────────
    filing_deadlines = [
        {
            "filing": "社会保険資格喪失届",
            "required": need_si_loss,
            "deadline": (retirement_date + timedelta(days=DEADLINE_SOCIAL_INSURANCE_LOSS)).isoformat(),
            "days_remaining": (retirement_date + timedelta(days=DEADLINE_SOCIAL_INSURANCE_LOSS) - today).days,
            "penalty": "遅延による保険料二重払いリスク",
        },
        {
            "filing": "源泉徴収票交付",
            "required": True,
            "deadline": (retirement_date + timedelta(days=DEADLINE_WITHHOLDING_SLIP)).isoformat(),
            "days_remaining": (retirement_date + timedelta(days=DEADLINE_WITHHOLDING_SLIP) - today).days,
            "penalty": "所得税法違反（¥1万以下の罰金）",
        },
        {
            "filing": "退職証明書（請求時）",
            "required": True,
            "deadline": (retirement_date + timedelta(days=DEADLINE_EMPLOYMENT_CERTIFICATE)).isoformat(),
            "days_remaining": (retirement_date + timedelta(days=DEADLINE_EMPLOYMENT_CERTIFICATE) - today).days,
            "penalty": "労働基準法違反（¥30万以下の罰金）",
        },
    ]

    # SaaSアカウント無効化スケジュール設定
    disable_date = (last_work_date + timedelta(days=1)).isoformat()
    accounts_to_disable: list[dict[str, Any]] = []
    total_disable_cost = 0.0
    total_disable_ms = 0

    for service in saas_services:
        dis_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": service,
                "operation": "schedule_account_disable",
                "params": {
                    "employee_id": employee_id,
                    "email": employee_email,
                    "disable_date": disable_date,
                    "reason": "退職",
                },
                "approved": True,
                "dry_run": dry_run,
            },
            context=context,
        ))
        total_disable_cost += dis_out.cost_yen
        total_disable_ms += dis_out.duration_ms
        accounts_to_disable.append({
            "service": service,
            "disable_date": disable_date,
            "scheduled": dis_out.success,
        })
        if not dis_out.success:
            logger.warning(f"offboarding: {service}無効化スケジュール失敗 ({employee_name})")

    try:
        from workers.micro.message import run_message_drafter
        msg = await run_message_drafter(
            document_type="退職手続き完了通知",
            context={
                "employee_name": employee_name,
                "retirement_date": retirement_date.strftime("%Y年%m月%d日"),
                "documents": [d["type"] for d in generated_docs],
                "send_address": send_address,
                "final_salary": int(final_salary),
                "pto_payout": int(pto_payout_amount),
                "retirement_pay": retirement_pay_amount,
                "assets_to_recover": [a["item"] for a in assets_to_recover],
                "handover_items_count": len(handover_items),
                "filing_deadlines": [
                    f for f in filing_deadlines if f.get("required", True)
                ],
            },
            company_id=company_id,
        )
        s7_result: dict[str, Any] = {
            "subject": msg.subject,
            "body": msg.body,
            "to": employee_email,
            "sent": not dry_run,
        }
        s7_confidence = 0.95
    except Exception as exc:
        logger.warning(f"message draft failed for offboarding: {exc}")
        s7_result = {
            "subject": f"【退職手続き完了のご連絡】{employee_name} 様",
            "body": (
                f"{employee_name} 様\n\n"
                f"退職日（{retirement_date.strftime('%Y年%m月%d日')}）に伴う手続きが完了しました。\n"
                "関連書類を送付いたします。ご確認のほどよろしくお願いいたします。\n\n担当者"
            ),
            "to": employee_email,
            "sent": False,
        }
        s7_confidence = 0.60

    s7_out = MicroAgentOutput(
        agent_name="message",
        success=True,
        result={**s7_result, "accounts_scheduled": len(accounts_to_disable)},
        confidence=s7_confidence,
        cost_yen=total_disable_cost,
        duration_ms=total_disable_ms,
    )
    record_step(7, "offboarding_completion_notifier", "message", s7_out)

    # ─── 連鎖トリガー準備 ────────────────────────────────────────────────────
    chain_triggers: list[dict[str, Any]] = []
    if need_si_loss or need_employment_cert:
        chain_triggers.append({
            "pipeline": "backoffice/social_insurance",
            "trigger_event": "employee_left",
            "input_data": {
                "filing_type": "loss",
                "employee_id": employee_id,
                "employee_name": employee_name,
                "retirement_date": retirement_date.isoformat(),
                "need_si_loss": need_si_loss,
                "need_employment_cert": need_employment_cert,
                "is_company_reason": is_company_reason,
            },
            "fire": True,
        })
    chain_triggers.append({
        "pipeline": "backoffice/account_lifecycle",
        "trigger_event": "employee_offboarded",
        "input_data": {
            "employee_id": employee_id,
            "employee_email": employee_email,
            "disable_date": disable_date,
            "services": saas_services,
        },
        "fire": True,
    })

    final_output: dict[str, Any] = {
        "employee_name": employee_name,
        "employee_id": employee_id,
        "retirement_date": retirement_date.isoformat(),
        "last_work_date": last_work_date.isoformat(),
        "extracted_data": extracted,
        "final_salary": int(final_salary),
        "pto_payout": int(pto_payout_amount),
        "retirement_pay": retirement_pay_amount,
        "approval_required": True,
        "documents": generated_docs,
        "assets_to_recover": assets_to_recover,
        "handover_items": handover_items,
        "accounts_to_disable": accounts_to_disable,
        "filing_deadlines": filing_deadlines,
        "farewell_email": s7_result,
        "chain_triggers": chain_triggers,
    }

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"employee_offboarding_pipeline complete: company={company_id}, "
        f"employee={employee_name}, final_salary=Y{int(final_salary):,}, "
        f"assets={len(assets_to_recover)}, handover={len(handover_items)}, {total_duration}ms"
    )

    return OffboardingPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        employee_name=employee_name,
        final_salary=final_salary,
        unused_pto_payout=pto_payout_amount,
        retirement_pay_calculated=retirement_pay_amount,
        documents=generated_docs,
        assets_to_recover=assets_to_recover,
        handover_items=handover_items,
        accounts_to_disable=accounts_to_disable,
        social_insurance_triggered=any(t["fire"] and "social_insurance" in t["pipeline"] for t in chain_triggers),
        account_lifecycle_triggered=True,
        chain_triggers=chain_triggers,
        filing_deadlines=filing_deadlines,
        approval_required=True,
    )
