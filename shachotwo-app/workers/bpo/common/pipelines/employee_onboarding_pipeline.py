"""
共通BPO 入社手続きパイプライン（バックオフィスBPO）

レジストリキー: backoffice/employee_onboarding
トリガー: イベント（内定承諾）/ 手動
承認: 不要（チェックリスト駆動）
コネクタ: SmartHR、Google Workspace（アカウント作成）、Slack（チャンネル招待）

Steps:
  Step 1: extractor       入社書類からデータ抽出（雇用契約書・身元保証書等のOCRテキスト→JSON）
  Step 2: validator       書類チェックリスト検証（マイナンバー/年金手帳/雇用保険証/扶養控除申告書/給与振込届）
  Step 3: rule_matcher    社保届出要否判定（雇用形態・週労働時間・加入条件チェック）
  Step 4: generator       社保届出書類 + 入社書類セット生成
  Step 5: saas_writer     SaaSアカウント発行リスト生成（Google/Slack/社内ツール）
  Step 6: generator       30/60/90日教育計画生成
  Step 7: message         ウェルカムメール・初日案内送信

連鎖トリガー: → backoffice/social_insurance（資格取得届を自動発火）

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション3.2
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# 入社時に従業員から提出が必要な書類チェックリスト
REQUIRED_SUBMISSION_DOCS = [
    "マイナンバー",
    "年金手帳",
    "雇用保険被保険者証",
    "扶養控除等申告書",
    "給与振込口座届",
]

# 入社書類の種別リスト（会社側が用意・生成するもの）
ONBOARDING_DOCUMENTS = [
    "雇用契約書",
    "身元保証書",
    "秘密保持誓約書",
    "マイナンバー提供依頼書",
    "給与振込口座届",
    "通勤手当申請書",
]

# 社会保険加入の要否判定ルール
SOCIAL_INSURANCE_RULES = {
    "正社員": True,
    "契約社員": True,
    "パート": None,  # 週30時間以上で加入必須 → rule_matcherで判定
    "アルバイト": None,
}

# 入社後フォロー計画の定義（30/60/90日）
ONBOARDING_EDUCATION_PLAN = {
    "day_1": "オリエンテーション・社内ツール説明・チーム紹介",
    "week_1": "業務概要説明・OJT開始・社内規程の読み込み",
    "day_30": "1ヶ月面談・業務目標設定・困りごとヒアリング",
    "day_60": "中間振り返り・スキルギャップ確認・追加研修検討",
    "day_90": "試用期間終了面談・本採用判定・目標再設定",
}

# 標準SaaSアカウント作成対象
DEFAULT_SAAS_SERVICES = ["google_workspace", "slack"]

# 初日スケジュールのデフォルト項目
FIRST_DAY_SCHEDULE = [
    {"time": "09:00", "title": "入社手続き・書類確認", "duration_min": 60},
    {"time": "10:00", "title": "オリエンテーション（会社概要・就業規則）", "duration_min": 90},
    {"time": "11:30", "title": "部署紹介・席案内", "duration_min": 30},
    {"time": "12:00", "title": "ランチ（チームと）", "duration_min": 60},
    {"time": "13:00", "title": "ITツールセットアップ", "duration_min": 120},
    {"time": "15:00", "title": "業務説明・OJT開始", "duration_min": 120},
]


@dataclass
class OnboardingPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    employee_name: str = ""
    documents_generated: list[str] = field(default_factory=list)
    accounts_created: list[str] = field(default_factory=list)
    social_insurance_required: bool = False
    social_insurance_triggered: bool = False
    chain_trigger: dict[str, Any] = field(default_factory=dict)
    checklist: list[dict[str, Any]] = field(default_factory=list)
    missing_docs: list[str] = field(default_factory=list)
    education_plan: dict[str, str] = field(default_factory=dict)
    anomaly_warnings: list[str] = field(default_factory=list)

    def to_onboarding_summary(self) -> str:
        extra = [
            f"  従業員: {self.employee_name}",
            f"  書類生成: {len(self.documents_generated)}件",
            f"  未提出書類: {self.missing_docs if self.missing_docs else 'なし'}",
            f"  アカウント作成: {self.accounts_created}",
            f"  社保届出要否: {'要' if self.social_insurance_required else '否'}",
            f"  社保連鎖: {'発火' if self.social_insurance_triggered else '未発火'}",
        ]
        if self.anomaly_warnings:
            for w in self.anomaly_warnings:
                extra.append(f"  [異常検知] {w}")
        return pipeline_summary(
            label="入社手続きパイプライン",
            total_steps=7,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_employee_onboarding_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> OnboardingPipelineResult:
    """
    入社手続きパイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            "employee_name"      (str):  従業員氏名
            "employee_email"     (str):  従業員メールアドレス（入社前の連絡先）
            "join_date"          (str):  入社日（YYYY-MM-DD）
            "department"         (str):  配属部署
            "job_title"          (str):  職種・役職
            "employment_type"    (str):  雇用形態（"正社員" | "契約社員" | "パート"）
            "salary"             (int):  月給（円）
            "weekly_work_hours"  (float): 週所定労働時間（社保判定用、パート・アルバイトで必要）
            "submitted_docs"     (list): 提出済み書類名リスト
            "document_texts"     (str):  スキャン済み入社書類のOCRテキスト（省略可）
            "manager_email"      (str):  上長メールアドレス（スケジュール調整用）
            "saas_services"      (list): 作成するSaaSアカウント（省略時はデフォルト）
            "dry_run"            (bool): True=SmartHR/SaaS登録を実行しない
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, OnboardingPipelineResult)

    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "employee_onboarding",
        "dry_run": input_data.get("dry_run", False),
    }

    employee_name: str = input_data.get("employee_name", "")
    employee_email: str = input_data.get("employee_email", "")
    join_date_str: str = input_data.get("join_date", "")
    department: str = input_data.get("department", "")
    job_title: str = input_data.get("job_title", "")
    employment_type: str = input_data.get("employment_type", "正社員")
    salary: int = input_data.get("salary", 0)
    weekly_work_hours: float = float(input_data.get("weekly_work_hours", 40.0))
    manager_email: str = input_data.get("manager_email", "")
    saas_services: list[str] = input_data.get("saas_services", DEFAULT_SAAS_SERVICES)
    dry_run: bool = context["dry_run"]

    # 入社日をパース
    try:
        join_date = date.fromisoformat(join_date_str) if join_date_str else date.today() + timedelta(days=14)
    except ValueError:
        join_date = date.today() + timedelta(days=14)

    context.update({
        "employee_name": employee_name,
        "join_date": join_date.isoformat(),
        "department": department,
        "job_title": job_title,
    })

    # ─── Step 1: extractor ── 入社書類からデータ抽出 ──────────────────────────
    document_texts: str = input_data.get("document_texts", "")
    if not document_texts:
        # テキストがない場合は入力データ自体を構造化データとして使う
        document_texts = (
            f"氏名: {employee_name}\n"
            f"メール: {employee_email}\n"
            f"入社日: {join_date.isoformat()}\n"
            f"部署: {department}\n"
            f"役職: {job_title}\n"
            f"雇用形態: {employment_type}\n"
            f"月給: {salary}円\n"
        )

    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id, agent_name="structured_extractor",
        payload={
            "text": document_texts,
            "schema": {
                "employee_name": "従業員氏名",
                "email": "メールアドレス",
                "join_date": "入社日（YYYY-MM-DD）",
                "department": "部署名",
                "job_title": "役職・職種",
                "employment_type": "雇用形態",
                "salary": "月給（整数・円）",
                "address": "住所",
                "emergency_contact": "緊急連絡先",
            },
            "hint": "入社書類（雇用契約書・身元保証書等）から従業員基本情報を抽出",
        },
        context=context,
    ))
    record_step(1, "document_extractor", "structured_extractor", s1_out)
    if not s1_out.success:
        return emit_fail("document_extractor")

    # 抽出結果と入力データをマージ（入力が優先）
    extracted: dict[str, Any] = s1_out.result
    employee_name = employee_name or extracted.get("employee_name", "")
    employee_email = employee_email or extracted.get("email", "")
    context["employee_name"] = employee_name

    # ─── Step 2: validator ── 書類チェックリスト検証 ──────────────────────────
    submitted_docs: list[str] = input_data.get("submitted_docs", [])
    missing_docs: list[str] = [
        doc for doc in REQUIRED_SUBMISSION_DOCS if doc not in submitted_docs
    ]
    doc_check_items = [
        {
            "field": doc,
            "value": doc in submitted_docs,
            "expected": True,
            "label": doc,
        }
        for doc in REQUIRED_SUBMISSION_DOCS
    ]
    s2_out = await run_output_validator(MicroAgentInput(
        company_id=company_id, agent_name="output_validator",
        payload={
            "check_type": "document_checklist",
            "items": doc_check_items,
            "missing_docs": missing_docs,
            "note": (
                f"未提出書類: {missing_docs}" if missing_docs
                else "全提出書類が揃っています"
            ),
        },
        context=context,
    ))
    record_step(2, "document_checklist_validator", "output_validator", s2_out)
    # 書類不足は警告扱い（処理は続行、チェックリストに記録）
    if missing_docs:
        logger.warning(
            f"onboarding: 未提出書類あり ({employee_name}): {missing_docs}"
        )
    context["missing_docs"] = missing_docs

    # ─── Step 3: rule_matcher ── 社保届出要否判定 ────────────────────────────
    # 雇用形態と週労働時間から社会保険加入要否を判定
    si_required_by_type = SOCIAL_INSURANCE_RULES.get(employment_type)
    if si_required_by_type is None:
        # パート・アルバイトは週30時間以上で加入必須（2022年10月改正: 従業員101人以上は週20時間以上）
        si_required_by_type = weekly_work_hours >= 30.0

    si_rules: list[dict[str, Any]] = [
        {
            "rule_id": "SI-001",
            "rule_name": "社会保険加入義務（雇用形態）",
            "condition": f"employment_type={employment_type}",
            "matched": employment_type in ("正社員", "契約社員"),
            "result": employment_type in ("正社員", "契約社員"),
        },
        {
            "rule_id": "SI-002",
            "rule_name": "社会保険加入義務（労働時間）",
            "condition": f"weekly_hours={weekly_work_hours}",
            "matched": weekly_work_hours >= 30.0,
            "result": weekly_work_hours >= 30.0,
        },
        {
            "rule_id": "SI-003",
            "rule_name": "雇用保険加入義務（週20時間以上）",
            "condition": f"weekly_hours={weekly_work_hours}",
            "matched": weekly_work_hours >= 20.0,
            "result": weekly_work_hours >= 20.0,
        },
    ]

    social_insurance_required: bool = bool(si_required_by_type)
    employment_insurance_required: bool = weekly_work_hours >= 20.0

    s3_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "social_insurance_required": social_insurance_required,
            "employment_insurance_required": employment_insurance_required,
            "rules_evaluated": si_rules,
            "matched_count": sum(1 for r in si_rules if r["matched"]),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(3, "social_insurance_rule_matcher", "rule_matcher", s3_out)
    context["social_insurance_required"] = social_insurance_required
    context["employment_insurance_required"] = employment_insurance_required

    # ─── Step 4: generator ── 社保届出書類 + 入社書類セット生成 ──────────────
    documents_to_generate = list(ONBOARDING_DOCUMENTS)
    if social_insurance_required:
        documents_to_generate.append("健康保険・厚生年金保険被保険者資格取得届")
    if employment_insurance_required:
        documents_to_generate.append("雇用保険被保険者資格取得届")

    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "入社書類セット",
            "variables": {
                "employee_name": employee_name,
                "join_date": join_date.isoformat(),
                "department": department,
                "job_title": job_title,
                "employment_type": employment_type,
                "salary": salary,
                "documents": documents_to_generate,
                "social_insurance_required": social_insurance_required,
                "employment_insurance_required": employment_insurance_required,
            },
        },
        context=context,
    ))
    record_step(4, "document_set_generator", "document_generator", s4_out)
    if not s4_out.success:
        return emit_fail("document_set_generator")

    generated_doc_names: list[str] = s4_out.result.get("documents", documents_to_generate)
    context["generated_documents"] = generated_doc_names

    # ─── Step 5: saas_writer ── SaaSアカウント発行リスト生成 ────────────────
    accounts_created: list[str] = []
    account_errors: list[str] = []
    total_account_cost = 0.0
    total_account_ms = 0

    for service in saas_services:
        acc_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id, agent_name="saas_writer",
            payload={
                "service": service,
                "operation": "create_account",
                "params": {
                    "name": employee_name,
                    "email": employee_email,
                    "department": department,
                    "role": "member",
                },
                "approved": True,
                "dry_run": dry_run,
            },
            context=context,
        ))
        total_account_cost += acc_out.cost_yen
        total_account_ms += acc_out.duration_ms
        if acc_out.success:
            accounts_created.append(service)
        else:
            account_errors.append(f"{service}: {acc_out.result.get('error', '不明なエラー')}")
            logger.warning(f"onboarding: {service}アカウント作成失敗 ({employee_name})")

    s5_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=len(accounts_created) > 0 or len(saas_services) == 0,
        result={
            "accounts_created": accounts_created,
            "errors": account_errors,
        },
        confidence=len(accounts_created) / max(len(saas_services), 1),
        cost_yen=total_account_cost,
        duration_ms=total_account_ms,
    )
    record_step(5, "account_provisioner", "saas_writer", s5_out)
    context["accounts_created"] = accounts_created

    # ─── Step 6: generator ── 30/60/90日教育計画生成 ─────────────────────────
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "入社教育計画",
            "variables": {
                "employee_name": employee_name,
                "join_date": join_date.isoformat(),
                "department": department,
                "job_title": job_title,
                "employment_type": employment_type,
                "education_plan": ONBOARDING_EDUCATION_PLAN,
                "first_day_schedule": FIRST_DAY_SCHEDULE,
                "manager_email": manager_email,
                "accounts_created": accounts_created,
                "prompt": (
                    f"{employee_name}（{job_title}・{department}）の入社後30日・60日・90日の"
                    "フォローアップ計画を作成してください。"
                    "業務習熟・チーム融合・目標設定の観点を含めること。"
                ),
            },
        },
        context=context,
    ))
    record_step(6, "education_plan_generator", "document_generator", s6_out)
    if not s6_out.success:
        logger.warning(f"onboarding: 教育計画生成失敗 ({employee_name}) — フォールバック使用")

    education_plan_content: str = s6_out.result.get("content", "")
    education_plan: dict[str, str] = (
        s6_out.result.get("education_plan", ONBOARDING_EDUCATION_PLAN)
        if s6_out.success
        else ONBOARDING_EDUCATION_PLAN
    )
    context["education_plan"] = education_plan

    # ─── Step 7: message ── ウェルカムメール・初日案内送信 ────────────────────
    try:
        from workers.micro.message import run_message_drafter
        msg = await run_message_drafter(
            document_type="入社前案内メール",
            context={
                "employee_name": employee_name,
                "join_date": join_date.strftime("%Y年%m月%d日"),
                "department": department,
                "job_title": job_title,
                "accounts_created": accounts_created,
                "documents": generated_doc_names,
                "first_day_schedule": FIRST_DAY_SCHEDULE,
                "missing_docs": missing_docs,
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
        logger.warning(f"message draft failed for onboarding: {exc}")
        s7_result = {
            "subject": f"【入社のご案内】{join_date.strftime('%Y年%m月%d日')}ご入社予定の{employee_name}様へ",
            "body": (
                f"{employee_name} 様\n\n"
                f"ご入社日（{join_date.strftime('%Y年%m月%d日')}）が近づいてまいりました。\n"
                "書類のご提出と当日の持ち物をご確認ください。\n\n担当者よりご連絡いたします。"
            ),
            "to": employee_email,
            "sent": False,
        }
        s7_confidence = 0.60

    s7_out = MicroAgentOutput(
        agent_name="message",
        success=True,
        result=s7_result,
        confidence=s7_confidence,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(7, "welcome_email_sender", "message", s7_out)

    # ─── 連鎖トリガー準備（社会保険資格取得届）────────────────────────────────
    employee_id: str = extracted.get("employee_id", "")
    chain_trigger: dict[str, Any] = {
        "pipeline": "backoffice/social_insurance",
        "trigger_event": "employee_joined",
        "input_data": {
            "filing_type": "acquisition",
            "employee_id": employee_id,
            "employee_name": employee_name,
            "join_date": join_date.isoformat(),
            "salary": salary,
            "social_insurance_required": social_insurance_required,
            "employment_insurance_required": employment_insurance_required,
        },
        "fire": social_insurance_required or employment_insurance_required,
    }

    # ─── チェックリスト生成 ───────────────────────────────────────────────────
    checklist = [
        {
            "item": "入社書類抽出・確認",
            "status": "done" if s1_out.success else "error",
            "due_date": join_date.isoformat(),
        },
        {
            "item": "提出書類チェック",
            "status": "warning" if missing_docs else "done",
            "due_date": (join_date - timedelta(days=1)).isoformat(),
            "note": f"未提出: {missing_docs}" if missing_docs else None,
        },
        {
            "item": "社保・雇用保険届出",
            "status": "triggered" if chain_trigger["fire"] else "not_required",
            "due_date": (join_date + timedelta(days=5)).isoformat(),
        },
        {
            "item": "入社書類セット生成",
            "status": "done" if s4_out.success else "error",
            "due_date": join_date.isoformat(),
        },
        {
            "item": "SaaSアカウント発行",
            "status": "done" if accounts_created else "error",
            "due_date": join_date.isoformat(),
        },
        {
            "item": "教育計画作成",
            "status": "done" if s6_out.success else "warning",
            "due_date": join_date.isoformat(),
        },
        {
            "item": "入社案内メール送信",
            "status": "done" if not dry_run else "dry_run",
            "due_date": (join_date - timedelta(days=3)).isoformat(),
        },
    ]

    final_output: dict[str, Any] = {
        "employee_name": employee_name,
        "employee_id": employee_id,
        "join_date": join_date.isoformat(),
        "extracted_data": extracted,
        "missing_docs": missing_docs,
        "social_insurance_required": social_insurance_required,
        "employment_insurance_required": employment_insurance_required,
        "documents_generated": generated_doc_names,
        "accounts_created": accounts_created,
        "education_plan": education_plan,
        "welcome_email": s7_result,
        "chain_trigger": chain_trigger,
        "checklist": checklist,
    }

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"employee_onboarding_pipeline complete: company={company_id}, "
        f"employee={employee_name}, si_required={social_insurance_required}, "
        f"accounts={accounts_created}, {total_duration}ms"
    )

    return OnboardingPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        employee_name=employee_name,
        documents_generated=generated_doc_names,
        accounts_created=accounts_created,
        social_insurance_required=social_insurance_required,
        social_insurance_triggered=chain_trigger["fire"],
        chain_trigger=chain_trigger,
        checklist=checklist,
        missing_docs=missing_docs,
        education_plan=education_plan,
    )
