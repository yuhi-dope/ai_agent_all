"""不動産業 免許・届出管理パイプライン

Steps:
  Step 1: license_reader        免許情報の取得・残存期間計算（宅建業免許/宅建士証）
  Step 2: alert_generator       更新アラート生成（6ヶ月/90日/60日/30日前のタイミング）
  Step 3: checklist_manager     必要書類チェックリスト管理（未取得書類の取得方法ガイド付与）
  Step 4: report_generator      業務状況報告書の自動生成（取引実績・宅建士情報・資産状況）
  Step 5: employee_roster       法定従業者名簿の自動生成・更新（退職者10年保持）
  Step 6: output_validator      バリデーション（期限超過・未提出書類の警告）
  Step 7: notification_sender   担当者・経営陣へのアラート通知（Slack/メール）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.diff import run_diff_detector
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator
from workers.micro.message import run_message_drafter

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 宅建業免許の有効期間（年）
LICENSE_VALID_YEARS = 5

# 更新申請期間（有効期限満了前）
RENEWAL_WINDOW_DAYS_START = 90   # 90日前から申請開始
RENEWAL_WINDOW_DAYS_END   = 30   # 30日前が申請期限

# アラートタイミング（日数前）
ALERT_THRESHOLDS = {
    "180d": 180,  # 6ヶ月前: 準備開始
    "90d":   90,  # 90日前: 申請受付開始
    "60d":   60,  # 60日前: 締切30日前警告
    "30d":   30,  # 30日前: 最終警告
}

# 更新に必要な書類チェックリスト
RENEWAL_CHECKLIST: list[dict[str, str]] = [
    {"item": "免許申請書", "source": "申請書式（都道府県窓口またはHP）"},
    {"item": "身分証明書（役員全員）", "source": "本籍地の市区町村"},
    {"item": "登記されていないことの証明書（役員全員）", "source": "法務局"},
    {"item": "納税証明書（法人税 or 所得税）", "source": "税務署"},
    {"item": "貸借対照表・損益計算書", "source": "社内（税理士作成）"},
    {"item": "宅地建物取引士証の写し（専任宅建士全員）", "source": "自社保管"},
    {"item": "専任の宅地建物取引士設置証明書", "source": "社内作成"},
    {"item": "事務所の写真（外観・内部・表示板）", "source": "自社撮影"},
    {"item": "誓約書", "source": "申請書式"},
    {"item": "相談役・顧問・株主一覧", "source": "社内作成"},
    {"item": "略歴書（役員全員）", "source": "社内作成"},
]

# 業務状況報告書の必要項目
BUSINESS_REPORT_REQUIRED_FIELDS = [
    "office_info",         # 事務所情報
    "takkenshi_list",      # 宅建士一覧
    "transaction_results", # 取引実績
    "employee_count",      # 従業者数
]

# 従業者名簿の法定保存期間（年）
EMPLOYEE_ROSTER_RETENTION_YEARS = 10


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
class LicenseManagementResult:
    """免許・届出管理パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 免許・届出管理パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        alerts = self.final_output.get("alerts", [])
        lines.append(f"  アラート数: {len(alerts)}件")
        expired = self.final_output.get("license_expired", False)
        if expired:
            lines.append("  警告: 免許が失効しています（無免許営業リスク）")
        pending_docs = self.final_output.get("pending_documents", [])
        lines.append(f"  未取得書類: {len(pending_docs)}件")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _calc_days_remaining(expiry_date_str: str) -> int:
    """免許有効期限までの残日数を計算する。"""
    try:
        expiry = date.fromisoformat(expiry_date_str)
        return (expiry - date.today()).days
    except (ValueError, TypeError):
        return -1


def _determine_alert_level(days_remaining: int) -> list[str]:
    """残日数からアクティブなアラートレベルを決定する。"""
    active_alerts: list[str] = []
    if days_remaining < 0:
        active_alerts.append("expired")
    elif days_remaining <= ALERT_THRESHOLDS["30d"]:
        active_alerts.append("30d")
    elif days_remaining <= ALERT_THRESHOLDS["60d"]:
        active_alerts.append("60d")
    elif days_remaining <= ALERT_THRESHOLDS["90d"]:
        active_alerts.append("90d")
    elif days_remaining <= ALERT_THRESHOLDS["180d"]:
        active_alerts.append("180d")
    return active_alerts


async def run_license_management_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> LicenseManagementResult:
    """
    免許・届出管理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "license_data": dict,          # 免許情報（license_number, expiry_date, renewal_count等）
            "employee_data": list[dict],   # 従業者データ（宅建士情報含む）
            "checklist_status": dict,      # 書類取得状況 {item: bool}
            "transaction_summary": dict,   # 取引実績サマリー
            "fiscal_year": int,            # 業務状況報告の対象年度
            "report_due_date": str,        # 業務状況報告の提出期限（ISO8601）
        }

    Returns:
        LicenseManagementResult
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

    def _fail(step_name: str) -> LicenseManagementResult:
        return LicenseManagementResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: license_reader ──────────────────────────────────────────
    s1_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "data_sources": ["license_management", "realestate_employees"],
            "company_id": company_id,
            "include_takkenshi_expiry": True,
        },
        context=context,
    ))
    _add_step(1, "license_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("license_reader")

    # 入力データとDB取得データをマージ
    license_data = {
        **(s1_out.result.get("license_data") or {}),
        **(input_data.get("license_data") or {}),
    }
    expiry_date = license_data.get("expiry_date", "")
    days_remaining = _calc_days_remaining(expiry_date)
    is_expired = days_remaining < 0
    license_data["days_remaining"] = days_remaining
    license_data["is_expired"] = is_expired

    # 宅建士証の有効期限もチェック
    employee_data: list[dict] = (
        s1_out.result.get("employee_data") or
        input_data.get("employee_data") or []
    )
    takkenshi_expiry_warnings: list[str] = []
    for emp in employee_data:
        if emp.get("is_takkenshi") and emp.get("takkenshi_expiry"):
            tk_days = _calc_days_remaining(emp["takkenshi_expiry"])
            if tk_days <= 180:
                takkenshi_expiry_warnings.append(
                    f"宅建士証期限: {emp['name']} — 残{tk_days}日"
                )

    context["license_data"] = license_data
    context["employee_data"] = employee_data
    context["takkenshi_expiry_warnings"] = takkenshi_expiry_warnings

    if is_expired:
        logger.error(
            f"[license_management] 宅建業免許が失効: "
            f"company_id={company_id}, expiry={expiry_date}"
        )

    # ─── Step 2: alert_generator ─────────────────────────────────────────
    active_alert_levels = _determine_alert_level(days_remaining)
    alert_messages: list[dict[str, str]] = []
    for level in active_alert_levels:
        if level == "expired":
            alert_messages.append({
                "level": "critical",
                "message": "宅建業免許が失効しています。即時対応が必要です。（宅建業法12条: 3年以下の懲役または300万円以下の罰金）",
                "action": "都道府県知事または国土交通大臣に即刻問い合わせてください",
            })
        elif level == "30d":
            alert_messages.append({
                "level": "error",
                "message": f"本日が免許更新申請の最終期限です（残{days_remaining}日）",
                "action": "申請書類を揃えて今すぐ提出してください",
            })
        elif level == "60d":
            alert_messages.append({
                "level": "warning",
                "message": f"免許更新申請期限まで残{days_remaining}日です",
                "action": "未取得書類を確認し、早急に準備を進めてください",
            })
        elif level == "90d":
            alert_messages.append({
                "level": "info",
                "message": f"免許更新申請の受付が開始されました（残{days_remaining}日）",
                "action": "書類チェックリストを確認し、申請準備を開始してください",
            })
        elif level == "180d":
            alert_messages.append({
                "level": "info",
                "message": f"6ヶ月後に免許更新が必要です（残{days_remaining}日）",
                "action": "更新チェックリストを確認し、準備を開始してください",
            })
    # 宅建士証の警告も追加
    for tw in takkenshi_expiry_warnings:
        alert_messages.append({"level": "warning", "message": tw, "action": "宅建士証の更新手続きを進めてください"})

    s2_start = int(time.time() * 1000)
    s2_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            "alerts": alert_messages,
            "active_levels": active_alert_levels,
            "days_remaining": days_remaining,
            "renewal_window_open": (
                RENEWAL_WINDOW_DAYS_END <= days_remaining <= RENEWAL_WINDOW_DAYS_START
            ),
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "alert_generator", "rule_matcher", s2_out)
    context["alerts"] = alert_messages

    # ─── Step 3: checklist_manager ───────────────────────────────────────
    checklist_status: dict[str, bool] = input_data.get("checklist_status", {})
    pending_docs: list[dict] = []
    completed_docs: list[dict] = []
    for doc in RENEWAL_CHECKLIST:
        item_name = doc["item"]
        is_done = checklist_status.get(item_name, False)
        if is_done:
            completed_docs.append({"item": item_name, "status": "取得済み"})
        else:
            pending_docs.append({
                "item": item_name,
                "source": doc["source"],
                "status": "未取得",
            })

    s3_out = await run_diff_detector(MicroAgentInput(
        company_id=company_id,
        agent_name="diff_detector",
        payload={
            "diff_type": "checklist_status",
            "checklist": RENEWAL_CHECKLIST,
            "current_status": checklist_status,
            "pending_items": pending_docs,
            "completed_items": completed_docs,
        },
        context=context,
    ))
    _add_step(3, "checklist_manager", "diff_detector", s3_out)
    context["pending_docs"] = pending_docs
    context["completed_docs"] = completed_docs

    # ─── Step 4: report_generator ─────────────────────────────────────────
    fiscal_year = input_data.get("fiscal_year", date.today().year - 1)
    transaction_summary = input_data.get("transaction_summary", {})
    s4_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "業務状況報告書",
            "variables": {
                "fiscal_year": fiscal_year,
                "company_id": company_id,
                "license_data": license_data,
                "office_info": license_data.get("office_info", {}),
                "takkenshi_list": [e for e in employee_data if e.get("is_takkenshi")],
                "transaction_results": transaction_summary,
                "employee_count": len([e for e in employee_data if e.get("status") == "active"]),
                "due_date": input_data.get("report_due_date", ""),
            },
            "required_fields": BUSINESS_REPORT_REQUIRED_FIELDS,
            "output_format": "pdf",
        },
        context=context,
    ))
    _add_step(4, "report_generator", "document_generator", s4_out)
    if not s4_out.success:
        logger.warning("[license_management] 業務状況報告書生成失敗 — 続行")
    context["business_report"] = s4_out.result

    # ─── Step 5: employee_roster ──────────────────────────────────────────
    # 退職者も10年間保持（宅建業法48条3項）
    today = date.today()
    retention_cutoff = today - timedelta(days=EMPLOYEE_ROSTER_RETENTION_YEARS * 365)
    active_employees = [e for e in employee_data if e.get("status") == "active"]
    retained_employees = [
        e for e in employee_data
        if e.get("status") == "terminated" and
        e.get("termination_date") and
        date.fromisoformat(str(e["termination_date"])) >= retention_cutoff
    ]

    s5_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "従業者名簿",
            "variables": {
                "active_employees": active_employees,
                "retained_employees": retained_employees,
                "as_of_date": today.isoformat(),
                "retention_cutoff": retention_cutoff.isoformat(),
                "required_fields": [
                    "name", "birth_date", "employee_cert_number",
                    "primary_duty", "is_takkenshi", "hire_date", "termination_date",
                ],
            },
            "output_format": "pdf",
        },
        context=context,
    ))
    _add_step(5, "employee_roster", "document_generator", s5_out)
    context["employee_roster"] = s5_out.result

    # ─── Step 6: output_validator ─────────────────────────────────────────
    s6_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "license_data": license_data,
                "alerts": alert_messages,
                "pending_docs": pending_docs,
                "business_report": s4_out.result,
                "employee_roster": s5_out.result,
            },
            "required_fields": ["license_data", "alerts"],
            "check_type": "license_compliance",
            "is_expired": is_expired,
            "days_remaining": days_remaining,
        },
        context=context,
    ))
    _add_step(6, "output_validator", "output_validator", s6_out)

    # ─── Step 7: notification_sender ──────────────────────────────────────
    critical_alerts = [a for a in alert_messages if a.get("level") in ("critical", "error")]
    if critical_alerts or (days_remaining <= ALERT_THRESHOLDS["90d"]):
        notification = await run_message_drafter(
            document_type="免許更新アラート通知",
            context={
                "alerts": alert_messages,
                "days_remaining": days_remaining,
                "pending_docs_count": len(pending_docs),
                "license_number": license_data.get("license_number", ""),
                "expiry_date": expiry_date,
                "is_expired": is_expired,
            },
            company_id=company_id,
        )
        s7_start = int(time.time() * 1000)
        s7_out = MicroAgentOutput(
            agent_name="message_drafter",
            success=True,
            result={
                "subject": notification.subject,
                "body": notification.body,
                "sent": False,  # TODO: Slack/メール送信実装
                "channels": ["email", "slack"],
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )
    else:
        s7_start = int(time.time() * 1000)
        s7_out = MicroAgentOutput(
            agent_name="message_drafter",
            success=True,
            result={"skipped": True, "reason": "アラートなし（通知不要）"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )
    _add_step(7, "notification_sender", "message_drafter", s7_out)

    final_output = {
        "license_data": license_data,
        "days_remaining": days_remaining,
        "license_expired": is_expired,
        "alerts": alert_messages,
        "pending_documents": pending_docs,
        "completed_documents": completed_docs,
        "business_report": s4_out.result,
        "employee_roster": s5_out.result,
        "validation": s6_out.result,
        "notification": s7_out.result,
        "takkenshi_expiry_warnings": takkenshi_expiry_warnings,
    }

    return LicenseManagementResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )
