"""
共通BPO コンプライアンスチェックパイプライン（バックオフィスBPO）

レジストリキー: backoffice/compliance_check
トリガー: スケジュール（毎月1日）/ イベント（法改正通知）
承認: 不要（レポート生成のみ。アラート有りの場合は担当者通知）
設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション6.1

Steps:
  Step 1: saas_reader     全社データ取得（従業員数、売上、業種、許認可）
  Step 2: rule_matcher    業界許認可の有効期限チェック（建設業許可5年更新等）
  Step 3: rule_matcher    APPI（個人情報保護法）対応状況チェック
  Step 4: rule_matcher    ハラスメント防止措置の実施状況チェック
  Step 5: rule_matcher    人数閾値義務チェック（50名→ストレスチェック等）
  Step 6: generator       コンプライアンスダッシュボードレポート生成
  Step 7: message         期限接近項目のSlackアラート
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.saas_writer import run_saas_writer
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
)

logger = logging.getLogger(__name__)

# 期限接近アラートの日数閾値
ALERT_DAYS_THRESHOLD = 30

# 人数閾値別の法定義務
HEADCOUNT_OBLIGATIONS = [
    {
        "threshold": 10,
        "obligation": "就業規則作成・届出義務（10名以上）",
        "law": "労働基準法89条",
    },
    {
        "threshold": 50,
        "obligation": "ストレスチェック実施義務（50名以上）",
        "law": "労働安全衛生法66条の10",
    },
    {
        "threshold": 50,
        "obligation": "産業医選任義務（50名以上）",
        "law": "労働安全衛生法13条",
    },
    {
        "threshold": 50,
        "obligation": "衛生管理者選任義務（50名以上）",
        "law": "労働安全衛生法12条",
    },
    {
        "threshold": 100,
        "obligation": "障害者雇用率達成義務（100名以上、2.5%）",
        "law": "障害者雇用促進法43条",
    },
    {
        "threshold": 101,
        "obligation": "育児・介護支援プラン策定義務（101名以上）",
        "law": "次世代育成支援対策推進法12条",
    },
    {
        "threshold": 301,
        "obligation": "女性活躍推進法行動計画策定・届出義務（301名以上）",
        "law": "女性活躍推進法8条",
    },
]

# APPI（個人情報保護法）チェック項目
APPI_CHECKLIST = [
    "プライバシーポリシー公表",
    "個人情報取扱規程整備",
    "安全管理措置（組織・人的・物理・技術）",
    "委託先管理体制",
    "本人からの開示・訂正・削除請求手続き",
    "漏えい時の報告・通知体制",
]

# ハラスメント防止措置チェック項目
HARASSMENT_CHECKLIST = [
    "ハラスメント防止規程整備",
    "相談窓口設置",
    "従業員への周知・啓発",
    "管理職向け研修実施（直近1年以内）",
    "事案発生時の対応手順整備",
]


@dataclass
class ComplianceCheckPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    compliance_alerts: list[str] = field(default_factory=list)
    # チェック結果サマリー
    license_issues: list[dict[str, Any]] = field(default_factory=list)
    appi_issues: list[dict[str, Any]] = field(default_factory=list)
    harassment_issues: list[dict[str, Any]] = field(default_factory=list)
    headcount_obligations: list[dict[str, Any]] = field(default_factory=list)
    report_generated: bool = False
    alert_sent: bool = False
    total_risk_count: int = 0


async def run_compliance_check_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> ComplianceCheckPipelineResult:
    """
    コンプライアンスチェックパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "company_profile": dict,  # 直接渡し形式
                {
                    "industry": str,           # 業種（建設業/製造業等）
                    "employee_count": int,     # 従業員数
                    "annual_revenue": int,     # 年間売上（円）
                    "licenses": [              # 保有許認可リスト
                        {
                            "name": str,       # 許認可名
                            "expiry_date": str, # 有効期限 YYYY-MM-DD
                            "authority": str,  # 管轄行政庁
                        }
                    ],
                    "appi_status": dict,       # APPI対応状況 {item: bool}
                    "harassment_status": dict, # ハラスメント対応状況 {item: bool}
                },
            "encrypted_credentials": str,     # kintone/SaaS認証情報（SaaS取得する場合）
            "slack_webhook_url": str,          # Slackアラート先（省略可）
            "report_format": str,             # "markdown" | "text" (省略時: "markdown")
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    compliance_alerts: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "compliance_check",
        "check_date": date.today().isoformat(),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, ComplianceCheckPipelineResult)

    # ─── Step 1: saas_reader ── 全社データ取得 ───────────────────────────────
    if "company_profile" in input_data:
        company_profile = input_data["company_profile"]
        s1_out = MicroAgentOutput(
            agent_name="saas_reader", success=True,
            result={"source": "direct", "company_profile": company_profile},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    else:
        try:
            s1_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id, agent_name="saas_reader",
                payload={
                    "service": "kintone",
                    "operation": "get_company_profile",
                    "params": {
                        "include_licenses": True,
                        "include_compliance_status": True,
                    },
                    "encrypted_credentials": input_data.get("encrypted_credentials"),
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="saas_reader", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
        company_profile = s1_out.result.get("company_profile", {})
    record_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return emit_fail("saas_reader")
    context["company_profile"] = company_profile

    # ─── Step 2: rule_matcher ── 業界許認可の有効期限チェック ─────────────────
    licenses: list[dict] = company_profile.get("licenses", [])
    license_issues: list[dict] = []
    today = date.today()
    alert_threshold = today + timedelta(days=ALERT_DAYS_THRESHOLD)

    for lic in licenses:
        expiry_str = lic.get("expiry_date", "")
        if not expiry_str:
            continue
        try:
            expiry_date = date.fromisoformat(expiry_str)
            days_remaining = (expiry_date - today).days
            if days_remaining < 0:
                license_issues.append({
                    "name": lic.get("name", ""),
                    "expiry_date": expiry_str,
                    "days_remaining": days_remaining,
                    "severity": "CRITICAL",
                    "detail": f"許認可期限切れ（{abs(days_remaining)}日超過）",
                })
                compliance_alerts.append(
                    f"[緊急] {lic.get('name')} 期限切れ（{expiry_str}）"
                )
            elif expiry_date <= alert_threshold:
                license_issues.append({
                    "name": lic.get("name", ""),
                    "expiry_date": expiry_str,
                    "days_remaining": days_remaining,
                    "severity": "WARNING",
                    "detail": f"許認可期限接近（残{days_remaining}日）",
                })
                compliance_alerts.append(
                    f"[警告] {lic.get('name')} 期限接近（{expiry_str}、残{days_remaining}日）"
                )
        except ValueError:
            license_issues.append({
                "name": lic.get("name", ""),
                "expiry_date": expiry_str,
                "severity": "WARNING",
                "detail": "有効期限の日付形式が不正です",
            })

    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "industry": company_profile.get("industry", ""),
                    "licenses": licenses,
                },
                "domain": "compliance_license",
                "category": "license_expiry",
            },
            context=context,
        ))
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "matched_rules": [],
                "applied_values": {"license_issues": license_issues},
                "unmatched_fields": [],
            },
            confidence=0.9, cost_yen=0.0, duration_ms=0,
        )
    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return emit_fail("rule_matcher_license")
    # rule_matcherの結果で補完される追加issue
    extra_issues = s2_out.result.get("applied_values", {}).get("license_issues", [])
    if isinstance(extra_issues, list):
        license_issues.extend([i for i in extra_issues if i not in license_issues])
    context["license_issues"] = license_issues

    # ─── Step 3: rule_matcher ── APPI対応状況チェック ────────────────────────
    appi_status: dict = company_profile.get("appi_status", {})
    appi_issues: list[dict] = []
    for item in APPI_CHECKLIST:
        if not appi_status.get(item, False):
            appi_issues.append({
                "item": item,
                "status": "未対応",
                "severity": "WARNING",
                "law": "個人情報保護法",
            })
            compliance_alerts.append(f"[APPI未対応] {item}")

    try:
        s3_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {"appi_status": appi_status},
                "domain": "compliance_appi",
                "category": "appi_checklist",
            },
            context=context,
        ))
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "matched_rules": APPI_CHECKLIST,
                "applied_values": {"appi_issues": appi_issues},
                "unmatched_fields": [],
            },
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(3, "rule_matcher", "rule_matcher", s3_out)
    context["appi_issues"] = appi_issues

    # ─── Step 4: rule_matcher ── ハラスメント防止措置チェック ────────────────
    harassment_status: dict = company_profile.get("harassment_status", {})
    harassment_issues: list[dict] = []
    for item in HARASSMENT_CHECKLIST:
        if not harassment_status.get(item, False):
            harassment_issues.append({
                "item": item,
                "status": "未対応",
                "severity": "WARNING",
                "law": "労働施策総合推進法（ハラスメント防止法）",
            })
            compliance_alerts.append(f"[ハラスメント未対応] {item}")

    try:
        s4_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {"harassment_status": harassment_status},
                "domain": "compliance_harassment",
                "category": "harassment_prevention",
            },
            context=context,
        ))
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "matched_rules": HARASSMENT_CHECKLIST,
                "applied_values": {"harassment_issues": harassment_issues},
                "unmatched_fields": [],
            },
            confidence=0.95, cost_yen=0.0, duration_ms=0,
        )
    record_step(4, "rule_matcher", "rule_matcher", s4_out)
    context["harassment_issues"] = harassment_issues

    # ─── Step 5: rule_matcher ── 人数閾値義務チェック ────────────────────────
    employee_count: int = company_profile.get("employee_count", 0)
    headcount_obligations: list[dict] = []

    for ob in HEADCOUNT_OBLIGATIONS:
        threshold = ob["threshold"]
        applies = employee_count >= threshold
        status_val = company_profile.get("headcount_status", {}).get(ob["obligation"], False)
        headcount_obligations.append({
            "threshold": threshold,
            "obligation": ob["obligation"],
            "law": ob["law"],
            "applies": applies,
            "compliant": status_val if applies else True,
            "status": "対象" if applies else "対象外",
        })
        if applies and not status_val:
            compliance_alerts.append(
                f"[人数義務未対応] {ob['obligation']}（{employee_count}名、閾値{threshold}名）"
            )

    try:
        s5_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id, agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "employee_count": employee_count,
                    "industry": company_profile.get("industry", ""),
                },
                "domain": "compliance_headcount",
                "category": "headcount_obligations",
            },
            context=context,
        ))
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="rule_matcher", success=True,
            result={
                "matched_rules": [],
                "applied_values": {"headcount_obligations": headcount_obligations},
                "unmatched_fields": [],
            },
            confidence=0.98, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "rule_matcher", "rule_matcher", s5_out)
    context["headcount_obligations"] = headcount_obligations

    # ─── Step 6: generator ── コンプライアンスダッシュボードレポート生成 ────────
    total_risk_count = (
        len(license_issues)
        + len(appi_issues)
        + len(harassment_issues)
        + sum(1 for o in headcount_obligations if o["applies"] and not o["compliant"])
    )
    report_data = {
        "company_id": company_id,
        "check_date": context["check_date"],
        "employee_count": employee_count,
        "industry": company_profile.get("industry", ""),
        "total_risk_count": total_risk_count,
        "license_issues": license_issues,
        "appi_issues": appi_issues,
        "harassment_issues": harassment_issues,
        "headcount_obligations": [o for o in headcount_obligations if o["applies"]],
        "compliance_alerts": compliance_alerts,
    }
    try:
        s6_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "compliance_dashboard",
                "data": report_data,
                "format": input_data.get("report_format", "markdown"),
            },
            context=context,
        ))
    except Exception as e:
        # フォールバック: 簡易テキストレポート
        report_lines = [
            f"## コンプライアンスチェックレポート ({context['check_date']})",
            f"従業員数: {employee_count}名 / 業種: {company_profile.get('industry', '未設定')}",
            f"リスク総数: {total_risk_count}件",
            "",
            "### 許認可チェック",
        ] + [f"- [{i['severity']}] {i['detail']} ({i['name']})" for i in license_issues] + [
            "",
            "### APPI対応状況",
        ] + [f"- [未対応] {i['item']}" for i in appi_issues] + [
            "",
            "### ハラスメント防止",
        ] + [f"- [未対応] {i['item']}" for i in harassment_issues]
        s6_out = MicroAgentOutput(
            agent_name="document_generator", success=True,
            result={"document": "\n".join(report_lines), "mock": True},
            confidence=0.85, cost_yen=0.0, duration_ms=0,
        )
    record_step(6, "generator", "document_generator", s6_out)
    report_generated = s6_out.success
    context["report"] = s6_out.result.get("document", "")

    # ─── Step 7: message ── 期限接近項目のSlackアラート ──────────────────────
    alert_sent = False
    if compliance_alerts and input_data.get("slack_webhook_url"):
        urgent_alerts = [a for a in compliance_alerts if "[緊急]" in a or "[CRITICAL]" in a]
        alert_body = (
            f"*コンプライアンスチェック完了* ({context['check_date']})\n"
            f"リスク総数: {total_risk_count}件\n"
            + "\n".join(f"• {a}" for a in compliance_alerts[:10])
            + (f"\n...他{len(compliance_alerts) - 10}件" if len(compliance_alerts) > 10 else "")
        )
        try:
            s7_out = await run_saas_writer(MicroAgentInput(
                company_id=company_id, agent_name="saas_writer",
                payload={
                    "service": "slack",
                    "operation": "post_message",
                    "params": {
                        "webhook_url": input_data.get("slack_webhook_url"),
                        "text": alert_body,
                        "urgent": len(urgent_alerts) > 0,
                    },
                    "approved": True,
                },
                context=context,
            ))
            alert_sent = s7_out.success
        except Exception as e:
            s7_out = MicroAgentOutput(
                agent_name="saas_writer", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0, duration_ms=0,
            )
    else:
        s7_out = MicroAgentOutput(
            agent_name="saas_writer", success=True,
            result={"sent": False, "reason": "Slack webhook未設定またはアラートなし"},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "message", "saas_writer", s7_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        "compliance_check_pipeline complete: risks=%d, license=%d, appi=%d, harassment=%d, %dms",
        total_risk_count, len(license_issues), len(appi_issues),
        len(harassment_issues), total_duration,
    )

    return ComplianceCheckPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "check_date": context["check_date"],
            "employee_count": employee_count,
            "total_risk_count": total_risk_count,
            "license_issues": license_issues,
            "appi_issues": appi_issues,
            "harassment_issues": harassment_issues,
            "headcount_obligations": headcount_obligations,
            "compliance_alerts": compliance_alerts,
            "report": context.get("report", ""),
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        compliance_alerts=compliance_alerts,
        license_issues=license_issues,
        appi_issues=appi_issues,
        harassment_issues=harassment_issues,
        headcount_obligations=headcount_obligations,
        report_generated=report_generated,
        alert_sent=alert_sent,
        total_risk_count=total_risk_count,
    )
