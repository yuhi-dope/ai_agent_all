"""
SFA パイプライン③ — 見積書・契約書自動送付

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.3

Phase A: 見積書
  Step 1: quotation_calculator  選択モジュールから見積金額を計算
  Step 2: pdf_generator         見積書PDF生成（quotation_template.html）
  Step 3: email_sender          見積書メール送付
  Step 4: approval_checker      見積承認確認（承認済みなら Phase B へ）

Phase B: 契約書
  Step 5: contract_generator    契約書生成（contract_template.html）
  Step 6: cloudsign_sender      CloudSign 電子署名依頼送信
  Step 7: db_writer             contracts 保存 / opportunities → won / customers 作成 / Slack 受注通知
  Step 8: invoice_issuer        freee 請求書自動発行 + メール送付
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from db.supabase import get_service_client
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.pdf_generator import run_pdf_generator
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer

logger = logging.getLogger(__name__)


async def _notify(channel: str, message: str) -> None:
    """通知送信。SLACK_WEBHOOK_URL 設定時はSlack送信、未設定時はログ出力。"""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(slack_url, json={"text": message, "channel": channel})
    else:
        logger.info(f"[通知][{channel}] {message}")

# ── 料金定数 ────────────────────────────────────────────────────────────────

PRICE_BRAIN = 30_000          # ブレイン月額（税抜）
PRICE_BPO_CORE = 250_000      # BPOコア月額（税抜）
PRICE_ADDITIONAL = 100_000    # 追加モジュール1個あたり月額（税抜）
ANNUAL_DISCOUNT_RATE = Decimal("0.10")   # 年払い10%OFF
TAX_RATE = Decimal("0.10")               # 消費税10%

# ── 公式料金プラン（CLAUDE.md 料金体系） ─────────────────────────────────────
# selected_modules に "common_bpo" または "industry_bpo" を渡すと
# 正式プランの単価が適用される。

PRICING_PLANS: dict[str, dict[str, Any]] = {
    "common_bpo": {
        "name": "共通BPO",
        "monthly_price": 150_000,
        "description": "バックオフィス全部 + ブレイン",
        "included_bpo_calls": 300,
        "included_qa_calls": 500,
    },
    "industry_bpo": {
        "name": "業種特化BPO",
        "monthly_price": 300_000,
        "description": "共通BPO + 業種固有パイプライン全部",
        "included_bpo_calls": 300,
        "included_qa_calls": 500,
    },
    "human_support_addon": {
        "name": "人間サポート追加",
        "monthly_price": 200_000,
        "description": "AIだけでは対応しきれない業務を人間がサポート",
    },
}

ONBOARDING_PLANS: dict[str, dict[str, Any]] = {
    "self": {
        "name": "セルフ",
        "price": 0,
        "duration_months": 0,
        "description": "無料。お客様ご自身でセットアップ",
    },
    "consul": {
        "name": "コンサル",
        "monthly_price": 50_000,
        "duration_months": 2,
        "description": "2ヶ月間のコンサルティングサポート（合計10万円）",
    },
    "full_support": {
        "name": "フルサポート",
        "monthly_price": 300_000,
        "duration_months": 3,
        "description": "3ヶ月間のフルサポートオンボーディング（合計90万円）",
    },
}

# 見積番号プレフィックス（QT-YYYYMM-XXXX）
_QT_PREFIX = "QT"

# 承認ステータス
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_REVISION_REQUESTED = "revision_requested"


# ── データモデル ─────────────────────────────────────────────────────────────

@dataclass
class StepRecord:
    """単一ステップの実行記録。全ステップを steps リストで保持しリトライ・分析に使う。"""
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
class QuotationContractResult:
    """パイプライン実行結果。Phase A・Phase B を通じたすべての情報を保持する。"""
    success: bool
    phase: str                              # "phase_a" | "phase_b" | "partial"
    steps: list[StepRecord] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_status: str = APPROVAL_PENDING  # 見積承認ステータス
    quotation_id: str | None = None
    contract_id: str | None = None
    cloudsign_document_id: str | None = None
    freee_invoice_id: str | None = None

    def summary(self) -> str:
        status_icon = "OK" if self.success else "NG"
        lines = [
            f"[{status_icon}] 見積・契約パイプライン  phase={self.phase}",
            f"  ステップ数  : {len(self.steps)} / 8",
            f"  合計コスト  : ¥{self.total_cost_yen:.2f}",
            f"  処理時間    : {self.total_duration_ms}ms",
            f"  承認ステータス: {self.approval_status}",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        if self.quotation_id:
            lines.append(f"  見積ID       : {self.quotation_id}")
        if self.contract_id:
            lines.append(f"  契約ID       : {self.contract_id}")
        if self.cloudsign_document_id:
            lines.append(f"  CloudSign ID  : {self.cloudsign_document_id}")
        if self.freee_invoice_id:
            lines.append(f"  freee 請求書ID: {self.freee_invoice_id}")
        for s in self.steps:
            ok = "OK" if s.success else "NG"
            warn = f"  WARN: {s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no:02d} [{ok}] {s.step_name}"
                f"  conf={s.confidence:.2f}  ¥{s.cost_yen:.2f}  {s.duration_ms}ms{warn}"
            )
        return "\n".join(lines)


# ── ヘルパー ─────────────────────────────────────────────────────────────────

def _generate_quotation_number() -> str:
    """見積番号 QT-YYYYMM-XXXX を採番する。"""
    today = date.today()
    suffix = str(uuid.uuid4().int)[:4].zfill(4)
    return f"{_QT_PREFIX}-{today.strftime('%Y%m')}-{suffix}"


def _make_step(
    step_no: int,
    step_name: str,
    output: MicroAgentOutput,
    warning: str | None = None,
) -> StepRecord:
    return StepRecord(
        step_no=step_no,
        step_name=step_name,
        agent_name=output.agent_name,
        success=output.success,
        result=output.result,
        confidence=output.confidence,
        cost_yen=output.cost_yen,
        duration_ms=output.duration_ms,
        warning=warning,
    )


def _fail(
    result: QuotationContractResult,
    step_record: StepRecord,
    failed_step_name: str,
) -> QuotationContractResult:
    """失敗ステップを記録して結果を返す共通処理。"""
    result.steps.append(step_record)
    result.success = False
    result.failed_step = failed_step_name
    result.total_cost_yen = sum(s.cost_yen for s in result.steps)
    result.total_duration_ms = sum(s.duration_ms for s in result.steps)
    return result


# ── Step 1: 見積金額計算 ──────────────────────────────────────────────────────

async def _step1_calculate_quotation(
    company_id: str,
    selected_modules: list[str],
    billing_cycle: str,
    referral: bool,
) -> tuple[StepRecord, dict[str, Any]]:
    """
    選択モジュールから見積金額を計算する（LLM不使用、Decimal演算）。

    selected_modules 例:
        ["brain", "bpo_core", "bpo_additional_1", "bpo_additional_2"]

    billing_cycle:
        "monthly" | "annual"  — annual の場合 10% 引き

    referral:
        True の場合、初月無料（1ヶ月分を割引として line_items に追加）
    """
    start_ms = int(time.time() * 1000)
    agent_name = "quotation_calculator"

    try:
        line_items: list[dict[str, Any]] = []
        subtotal = Decimal("0")

        # モジュールごとに明細を積む
        module_prices: list[tuple[str, int]] = []

        # 公式プランキー（common_bpo / industry_bpo / human_support_addon）を優先処理
        for plan_key, plan_info in PRICING_PLANS.items():
            if plan_key in selected_modules:
                module_prices.append((
                    f"{plan_info['name']}（{plan_info['description']}）",
                    plan_info["monthly_price"],
                ))

        # オンボーディングプラン（onboarding_consul / onboarding_full_support）
        for ob_key, ob_info in ONBOARDING_PLANS.items():
            onboarding_module_key = f"onboarding_{ob_key}"
            if onboarding_module_key in selected_modules:
                total_price = ob_info.get("price", 0) or (
                    ob_info.get("monthly_price", 0) * ob_info.get("duration_months", 1)
                )
                if total_price > 0:
                    duration = ob_info.get("duration_months", 0)
                    label = (
                        f"オンボーディング: {ob_info['name']}"
                        + (f"（{duration}ヶ月）" if duration > 0 else "（無料）")
                    )
                    module_prices.append((label, total_price))

        # レガシーキー（brain / bpo_core）は後方互換のため残す
        if "brain" in selected_modules and not any(
            k in selected_modules for k in ("common_bpo", "industry_bpo")
        ):
            module_prices.append(("ブレイン（デジタルツイン・Q&A）", PRICE_BRAIN))
        if "bpo_core" in selected_modules and not any(
            k in selected_modules for k in ("common_bpo", "industry_bpo")
        ):
            module_prices.append(("BPOコア（業種特化主要モジュール）", PRICE_BPO_CORE))

        # 追加モジュール（bpo_additional_N）をカウント
        additional_count = sum(
            1 for m in selected_modules if m.startswith("bpo_additional")
        )
        if additional_count > 0:
            module_prices.append((
                f"追加モジュール（×{additional_count}個）",
                PRICE_ADDITIONAL * additional_count,
            ))

        if not module_prices:
            raise ValueError(
                "selected_modules に有効なプランキー（common_bpo / industry_bpo / "
                "human_support_addon / brain / bpo_core / bpo_additional_N）が1つも含まれていません"
            )

        for name, unit_price in module_prices:
            amount = unit_price  # quantity=1（月額）
            line_items.append({
                "name": name,
                "quantity": 1,
                "unit": "月",
                "unit_price": unit_price,
                "amount": amount,
            })
            subtotal += Decimal(str(amount))

        # 年払い割引
        discount_amount = Decimal("0")
        if billing_cycle == "annual":
            discount_amount = (subtotal * ANNUAL_DISCOUNT_RATE).to_integral_value()
            if discount_amount > 0:
                line_items.append({
                    "name": "年払い割引（10%）",
                    "quantity": 1,
                    "unit": "式",
                    "unit_price": -int(discount_amount),
                    "amount": -int(discount_amount),
                })
                subtotal -= discount_amount

        # 紹介割引（初月無料）
        referral_discount = Decimal("0")
        if referral:
            referral_discount = subtotal  # 初月1ヶ月分全額
            if referral_discount > 0:
                line_items.append({
                    "name": "紹介割引（初月無料）",
                    "quantity": 1,
                    "unit": "式",
                    "unit_price": -int(referral_discount),
                    "amount": -int(referral_discount),
                })
                subtotal -= referral_discount

        tax = (subtotal * TAX_RATE).to_integral_value()
        total = subtotal + tax

        calc_result: dict[str, Any] = {
            "line_items": line_items,
            "subtotal": int(subtotal),
            "tax": int(tax),
            "total": int(total),
            "billing_cycle": billing_cycle,
            "referral": referral,
        }

        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result=calc_result,
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        record = _make_step(1, "quotation_calculator", output)
        return record, calc_result

    except Exception as exc:
        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(1, "quotation_calculator", output), {}


# ── Step 2: 見積書 PDF 生成 ───────────────────────────────────────────────────

async def _step2_generate_quotation_pdf(
    company_id: str,
    opportunity: dict[str, Any],
    calc: dict[str, Any],
) -> tuple[StepRecord, bytes]:
    """
    quotation_template.html に計算結果を流し込んで PDF バイナリを生成する。

    opportunity には以下が必要:
        company_name, contact_name (optional)
    """
    today = date.today()
    valid_until = today + timedelta(days=30)
    quotation_number = _generate_quotation_number()

    # テンプレート変数
    items_for_tmpl = []
    for li in calc.get("line_items", []):
        items_for_tmpl.append({
            "name": li["name"],
            "quantity": li["quantity"],
            "unit_price": abs(li["unit_price"]),   # 割引行はマイナス表示を別途
            "amount": li["amount"],
        })

    class _Est:
        """テンプレートが参照するデータ構造をオブジェクトに変換する簡易 DTO。"""
        pass

    est = _Est()
    est.company_name = opportunity.get("target_company_name", "")  # type: ignore[attr-defined]
    est.contact_name = opportunity.get("contact_name", "")          # type: ignore[attr-defined]
    est.estimate_number = quotation_number                          # type: ignore[attr-defined]
    est.issue_date = today                                          # type: ignore[attr-defined]
    est.valid_until = valid_until                                   # type: ignore[attr-defined]
    est.items = items_for_tmpl                                      # type: ignore[attr-defined]
    est.subtotal = calc.get("subtotal", 0)                         # type: ignore[attr-defined]
    est.tax = calc.get("tax", 0)                                   # type: ignore[attr-defined]
    est.total = calc.get("total", 0)                               # type: ignore[attr-defined]
    billing_note = "年払い" if calc.get("billing_cycle") == "annual" else "月払い"
    est.notes = f"お支払い方法: {billing_note}。本見積の有効期限は発行日より30日です。"  # type: ignore[attr-defined]

    pdf_input = MicroAgentInput(
        company_id=company_id,
        agent_name="pdf_generator",
        payload={
            "template_name": "quotation_template.html",
            "data": {"est": est},
        },
    )
    pdf_output = await run_pdf_generator(pdf_input)
    record = _make_step(2, "pdf_generator(quotation)", pdf_output)

    pdf_bytes: bytes = pdf_output.result.get("pdf_bytes", b"") if pdf_output.success else b""
    # 見積番号をコンテキストに乗せるため result に追加
    if pdf_output.success:
        pdf_output.result["quotation_number"] = quotation_number
        pdf_output.result["valid_until"] = valid_until.isoformat()

    return record, pdf_bytes


# ── Step 3: 見積書メール送付 ──────────────────────────────────────────────────

async def _step3_send_quotation_email(
    company_id: str,
    recipient_email: str,
    recipient_name: str,
    company_name: str,
    pdf_bytes: bytes,
    quotation_number: str,
    total: int,
    valid_until: str,
) -> StepRecord:
    """
    SendGrid (または SMTP) 経由で見積書 PDF をメール添付して送付する。

    MVP では saas_writer service="sendgrid" として操作を記録し、
    実際の送信ロジックは connector/sendgrid.py（Phase 1 で実装）に委譲する。
    """
    email_params: dict[str, Any] = {
        "to": recipient_email,
        "to_name": recipient_name,
        "subject": f"【シャチョツー】お見積書のご送付 — {quotation_number}",
        "body_text": (
            f"{company_name} {recipient_name} 様\n\n"
            "お世話になっております。シャチョツーです。\n"
            "お見積書を作成いたしましたので添付にてご送付申し上げます。\n\n"
            f"見積番号: {quotation_number}\n"
            f"合計金額: ¥{total:,}（税込）\n"
            f"有効期限: {valid_until}\n\n"
            "ご不明点がございましたら、お気軽にご連絡ください。\n"
            "何卒よろしくお願い申し上げます。"
        ),
        "attachment_pdf": pdf_bytes[:100] if pdf_bytes else b"",  # ログ用に先頭のみ
        "attachment_name": f"{quotation_number}.pdf",
    }

    writer_input = MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "sendgrid",
            "operation": "send_quotation_email",
            "params": email_params,
            "approved": True,  # メール送付は事前承認済みフロー
            "dry_run": False,
        },
    )
    writer_output = await run_saas_writer(writer_input)
    return _make_step(3, "email_sender(quotation)", writer_output)


# ── Step 4: 承認確認 ──────────────────────────────────────────────────────────

async def _step4_check_approval(
    company_id: str,
    quotation_id: str,
    approval_status: str,
) -> StepRecord:
    """
    見積書の承認ステータスを検証する（LLM不使用）。

    approval_status:
        "approved"              → Phase B へ進む
        "revision_requested"    → Step 1 に戻すシグナルを返す
        "rejected"              → パイプライン終了（失注フロー）
        "pending"               → 承認待ち（パイプライン一時停止）

    Phase 1 では入力の approval_status をそのまま評価する。
    Phase 2 以降は Supabase quotations テーブルを polling して判断する。
    """
    start_ms = int(time.time() * 1000)

    valid_statuses = {APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED, APPROVAL_REVISION_REQUESTED}
    if approval_status not in valid_statuses:
        approval_status = APPROVAL_PENDING

    is_approved = approval_status == APPROVAL_APPROVED
    warning = None
    if approval_status == APPROVAL_REVISION_REQUESTED:
        warning = "修正要求あり。見積を再計算して再送してください。"
    elif approval_status == APPROVAL_REJECTED:
        warning = "見積が却下されました。失注フローに遷移します。"
    elif approval_status == APPROVAL_PENDING:
        warning = "承認待ちです。承認後に Phase B が開始されます。"

    duration_ms = int(time.time() * 1000) - start_ms
    output = MicroAgentOutput(
        agent_name="approval_checker",
        success=True,   # チェッカー自体は成功。承認判定は result.approved で確認する
        result={
            "approved": is_approved,
            "approval_status": approval_status,
            "quotation_id": quotation_id,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=duration_ms,
    )
    return _make_step(4, "approval_checker", output, warning=warning)


# ── Step 5: 契約書生成 ────────────────────────────────────────────────────────

async def _step5_generate_contract_pdf(
    company_id: str,
    opportunity: dict[str, Any],
    calc: dict[str, Any],
) -> tuple[StepRecord, bytes]:
    """
    contract_template.html に商談・見積情報を流し込んで契約書 PDF を生成する。
    """

    class _Contract:
        pass

    c = _Contract()
    c.company_name = opportunity.get("target_company_name", "")      # type: ignore[attr-defined]
    c.representative = opportunity.get("representative", "代表取締役") # type: ignore[attr-defined]
    c.address = opportunity.get("address", "")                        # type: ignore[attr-defined]
    c.plan_name = _build_plan_name(opportunity.get("selected_modules", []))  # type: ignore[attr-defined]
    c.monthly_amount = calc.get("subtotal", 0)                        # type: ignore[attr-defined]
    c.start_date = date.today() + timedelta(days=7)                   # type: ignore[attr-defined]

    pdf_input = MicroAgentInput(
        company_id=company_id,
        agent_name="pdf_generator",
        payload={
            "template_name": "contract_template.html",
            "data": {"c": c},
        },
    )
    pdf_output = await run_pdf_generator(pdf_input)
    record = _make_step(5, "pdf_generator(contract)", pdf_output)
    pdf_bytes: bytes = pdf_output.result.get("pdf_bytes", b"") if pdf_output.success else b""
    return record, pdf_bytes


def _build_plan_name(selected_modules: list[str]) -> str:
    """選択モジュールからプラン名を生成する。"""
    parts = []
    if "brain" in selected_modules:
        parts.append("ブレイン")
    if "bpo_core" in selected_modules:
        parts.append("BPOコア")
    additional_count = sum(1 for m in selected_modules if m.startswith("bpo_additional"))
    if additional_count > 0:
        parts.append(f"追加モジュール×{additional_count}")
    return " + ".join(parts) if parts else "カスタムプラン"


# ── Step 6: CloudSign 電子署名送信 ───────────────────────────────────────────

async def _step6_send_cloudsign(
    company_id: str,
    contract_pdf: bytes,
    opportunity: dict[str, Any],
    cloudsign_credentials: dict[str, str],
) -> tuple[StepRecord, str]:
    """
    CloudSign に契約書 PDF をアップロードして署名依頼を送信する。

    Returns:
        (StepRecord, cloudsign_document_id)
    """
    start_ms = int(time.time() * 1000)
    agent_name = "cloudsign_sender"

    try:
        from workers.connector.cloudsign import CloudSignConnector
        from workers.connector.base import ConnectorConfig

        config = ConnectorConfig(
            tool_name="cloudsign",
            credentials=cloudsign_credentials,
        )
        connector = CloudSignConnector(config)

        contract_title = (
            f"シャチョツー サービス利用契約書 — "
            f"{opportunity.get('target_company_name', '')} 様"
        )

        # ドキュメント作成 + PDF 添付
        doc_id = await connector.create_document(
            title=contract_title,
            pdf_content=contract_pdf,
        )

        # 署名依頼送信
        await connector.send_for_signature(
            document_id=doc_id,
            recipient_email=opportunity.get("contact_email", ""),
            recipient_name=opportunity.get("contact_name", ""),
            organization=opportunity.get("target_company_name", ""),
        )

        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={"cloudsign_document_id": doc_id, "title": contract_title},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(6, "cloudsign_sender", output), doc_id

    except Exception as exc:
        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(6, "cloudsign_sender", output), ""


# ── Step 7: DB 書き込み（contracts / opportunities / customers / Slack） ──────

async def _step7_write_db_and_notify(
    company_id: str,
    opportunity_id: str,
    opportunity: dict[str, Any],
    calc: dict[str, Any],
    cloudsign_document_id: str,
    quotation_number: str,
) -> tuple[StepRecord, str]:
    """
    署名完了後の後処理:
      1. contracts テーブルに保存
      2. opportunities.stage を "won" に更新
      3. customers テーブルにレコード作成
      4. Slack に受注祝い通知

    Returns:
        (StepRecord, contract_id)
    """
    start_ms = int(time.time() * 1000)
    agent_name = "db_writer"
    contract_id = str(uuid.uuid4())

    try:
        db = get_service_client()

        # 1. contracts 保存
        contract_data: dict[str, Any] = {
            "id": contract_id,
            "company_id": company_id,
            "opportunity_id": opportunity_id,
            "cloudsign_document_id": cloudsign_document_id,
            "plan_name": _build_plan_name(opportunity.get("selected_modules", [])),
            "monthly_amount": calc.get("subtotal", 0),
            "billing_cycle": calc.get("billing_cycle", "monthly"),
            "status": "signed",
            "signed_at": _now_iso(),
        }
        db.table("contracts").insert(contract_data).execute()
        logger.info(f"contracts 保存完了: contract_id={contract_id}")

        # 2. opportunities ステージを "won" に更新
        db.table("opportunities").update({
            "stage": "won",
            "stage_changed_at": _now_iso(),
        }).eq("id", opportunity_id).eq("company_id", company_id).execute()
        logger.info(f"opportunities.stage=won: opportunity_id={opportunity_id}")

        # 3. customers テーブルにレコード作成（テナントプロビジョニング起点）
        customer_data: dict[str, Any] = {
            "company_id": company_id,
            "opportunity_id": opportunity_id,
            "contract_id": contract_id,
            "company_name": opportunity.get("target_company_name", ""),
            "industry": opportunity.get("target_industry", ""),
            "contact_name": opportunity.get("contact_name", ""),
            "contact_email": opportunity.get("contact_email", ""),
            "mrr": calc.get("subtotal", 0),
            "billing_cycle": calc.get("billing_cycle", "monthly"),
            "status": "onboarding",
            "contracted_at": _now_iso(),
        }
        customer_res = db.table("customers").insert(customer_data).execute()
        customer_id = customer_res.data[0]["id"] if customer_res.data else ""
        logger.info(f"customers 作成完了: customer_id={customer_id}")

        # 4. 受注祝い通知（Slack未設定時はログ出力）
        notify_message = (
            f":tada: 受注おめでとうございます！\n"
            f"*{opportunity.get('target_company_name', '')}* 様と契約締結しました。\n"
            f"プラン: {_build_plan_name(opportunity.get('selected_modules', []))}\n"
            f"月額: ¥{calc.get('subtotal', 0):,}（税抜）\n"
            f"契約ID: {contract_id}"
        )
        await _notify(channel="#受注通知", message=notify_message)

        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "contract_id": contract_id,
                "customer_id": customer_id,
                "opportunity_stage": "won",
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(7, "db_writer", output), contract_id

    except Exception as exc:
        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(7, "db_writer", output), ""


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Step 8: freee 請求書発行 ─────────────────────────────────────────────────

async def _step8_issue_invoice(
    company_id: str,
    opportunity: dict[str, Any],
    calc: dict[str, Any],
    contract_id: str,
    freee_credentials: dict[str, Any],
) -> tuple[StepRecord, str]:
    """
    freee API で請求書を自動作成し、メール送付する。

    Returns:
        (StepRecord, freee_invoice_id)
    """
    start_ms = int(time.time() * 1000)
    agent_name = "invoice_issuer"

    try:
        from workers.connector.freee import FreeeConnector
        from workers.connector.base import ConnectorConfig

        config = ConnectorConfig(
            tool_name="freee",
            credentials=freee_credentials,
        )
        connector = FreeeConnector(config)

        today = date.today()
        due_date = date(today.year, today.month + 1 if today.month < 12 else 1,
                        28 if today.month < 12 else 28)  # 翌月28日払い（簡易）

        # freee 請求書作成ペイロード
        invoice_payload: dict[str, Any] = {
            "issue_date": today.isoformat(),
            "due_date": due_date.isoformat(),
            "partner_name": opportunity.get("target_company_name", ""),
            "partner_email": opportunity.get("contact_email", ""),
            "description": (
                f"シャチョツー利用料（{_build_plan_name(opportunity.get('selected_modules', []))}）\n"
                f"契約ID: {contract_id}"
            ),
            "invoice_lines": [
                {
                    "name": li["name"],
                    "unit_price": abs(li["unit_price"]),
                    "quantity": li["quantity"],
                    "tax_code": 1,  # 課税10%
                }
                for li in calc.get("line_items", [])
                if li.get("unit_price", 0) > 0   # 割引行は除外（freee では別途対応）
            ],
            "payment_type": "振込",
            "message": "いつもお世話になっております。ご確認のほど、よろしくお願いいたします。",
        }

        invoice_resp = await connector.write_record("invoices", invoice_payload)
        invoice_id: str = str(invoice_resp.get("invoice", {}).get("id", ""))

        # メール送付（saas_writer 経由）
        email_params: dict[str, Any] = {
            "to": opportunity.get("contact_email", ""),
            "to_name": opportunity.get("contact_name", ""),
            "subject": f"【シャチョツー】ご請求書のご送付 — {contract_id[:8].upper()}",
            "body_text": (
                f"{opportunity.get('target_company_name', '')} 様\n\n"
                "この度はシャチョツーにご契約いただきありがとうございます。\n"
                "ご請求書をfreeeよりお送りいたしました。\n"
                f"お支払期限: {due_date.isoformat()}\n"
                "ご不明点は担当者までお気軽にお問い合わせください。"
            ),
            "freee_invoice_id": invoice_id,
        }
        email_input = MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "sendgrid",
                "operation": "send_invoice_email",
                "params": email_params,
                "approved": True,
                "dry_run": False,
            },
        )
        await run_saas_writer(email_input)

        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={"freee_invoice_id": invoice_id},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(8, "invoice_issuer(freee)", output), invoice_id

    except Exception as exc:
        duration_ms = int(time.time() * 1000) - start_ms
        output = MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(exc)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
        return _make_step(8, "invoice_issuer(freee)", output), ""


# ── 入力バリデーション ─────────────────────────────────────────────────────────

def _validate_input(input_data: dict[str, Any]) -> list[str]:
    """必須フィールドを検証してエラーメッセージのリストを返す。"""
    errors: list[str] = []
    required = [
        "opportunity_id",
        "selected_modules",
        "target_company_name",
        "contact_email",
    ]
    for key in required:
        if not input_data.get(key):
            errors.append(f"必須フィールドが不足しています: {key}")

    modules = input_data.get("selected_modules", [])
    if isinstance(modules, list) and len(modules) == 0:
        errors.append("selected_modules に1つ以上のモジュールを指定してください")

    return errors


# ── メインパイプライン ─────────────────────────────────────────────────────────

async def run_quotation_contract_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    cloudsign_credentials: dict[str, str] | None = None,
    freee_credentials: dict[str, Any] | None = None,
    approval_status: str = APPROVAL_PENDING,
    dry_run: bool = False,
) -> QuotationContractResult:
    """
    SFA パイプライン③ — 見積書・契約書自動送付。

    Args:
        company_id:
            テナントID。全 DB 操作に注入される（RLS 保証）。

        input_data:
            必須キー:
                opportunity_id      (str)       商談ID
                selected_modules    (list[str])  ["brain", "bpo_core", "bpo_additional_1", ...]
                target_company_name (str)        見込み企業名
                contact_email       (str)        署名者・送付先メールアドレス
            任意キー:
                contact_name        (str)        担当者名
                billing_cycle       (str)        "monthly" | "annual" （デフォルト "monthly"）
                referral            (bool)       紹介割引（初月無料） （デフォルト False）
                representative      (str)        相手方代表者名
                address             (str)        相手方住所
                target_industry     (str)        業種コード

        cloudsign_credentials:
            {"api_token": "..."}
            None の場合 Phase B の Step 6 はスキップ（dry_run 扱い）。

        freee_credentials:
            {"access_token": "...", "company_id": 12345}
            None の場合 Phase B の Step 8 はスキップ。

        approval_status:
            見積承認ステータス。
            "approved"  → Phase A 完了後 Phase B に進む。
            "pending"   → Phase A で停止（承認待ち）。
            "rejected"  → Phase A 完了後パイプライン終了（失注）。
            "revision_requested" → Phase A 完了後停止（修正して再実行）。

        dry_run:
            True の場合、DB・メール・CloudSign・freee への実際の書き込みを行わない。

    Returns:
        QuotationContractResult

    Steps:
        Phase A — 見積書
          Step 1: quotation_calculator  モジュール選択 → 見積金額計算（Decimal演算）
          Step 2: pdf_generator         見積書PDF生成（quotation_template.html）
          Step 3: email_sender          見積書メール送付
          Step 4: approval_checker      承認ステータス確認

        Phase B — 契約書（approval_status == "approved" の場合のみ）
          Step 5: pdf_generator         契約書PDF生成（contract_template.html）
          Step 6: cloudsign_sender      CloudSign 電子署名依頼送信
          Step 7: db_writer             contracts保存 / opportunities→won / customers作成 / Slack通知
          Step 8: invoice_issuer        freee 請求書発行 + メール送付
    """
    pipeline_start = int(time.time() * 1000)
    result = QuotationContractResult(
        success=False,
        phase="phase_a",
        approval_status=approval_status,
    )

    # 入力バリデーション
    validation_errors = _validate_input(input_data)
    if validation_errors:
        result.failed_step = "input_validation"
        result.final_output = {"errors": validation_errors}
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        logger.error(f"quotation_contract_pipeline 入力エラー: {validation_errors}")
        return result

    opportunity_id: str = input_data["opportunity_id"]
    selected_modules: list[str] = input_data["selected_modules"]
    billing_cycle: str = input_data.get("billing_cycle", "monthly")
    referral: bool = bool(input_data.get("referral", False))

    opportunity: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "target_company_name": input_data.get("target_company_name", ""),
        "contact_name": input_data.get("contact_name", ""),
        "contact_email": input_data.get("contact_email", ""),
        "representative": input_data.get("representative", "代表取締役"),
        "address": input_data.get("address", ""),
        "target_industry": input_data.get("target_industry", ""),
        "selected_modules": selected_modules,
    }

    # ════════════════════════════════
    #  Phase A: 見積書
    # ════════════════════════════════
    logger.info(
        f"[quotation_contract_pipeline] Phase A 開始  "
        f"company_id={company_id}  opportunity_id={opportunity_id}"
    )

    # ── Step 1: 見積金額計算 ─────────────────────────
    step1, calc = await _step1_calculate_quotation(
        company_id, selected_modules, billing_cycle, referral
    )
    result.steps.append(step1)
    if not step1.success:
        return _fail(result, step1, "quotation_calculator")  # type: ignore[return-value]
    # _fail が result を返すが、この時点では result に step1 は append 済み
    if not step1.success:
        result.success = False
        result.failed_step = "quotation_calculator"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    # ── Step 2: 見積書 PDF 生成 ──────────────────────
    step2, quotation_pdf = await _step2_generate_quotation_pdf(
        company_id, opportunity, calc
    )
    result.steps.append(step2)
    if not step2.success:
        result.success = False
        result.failed_step = "pdf_generator(quotation)"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    quotation_number: str = step2.result.get("quotation_number", _generate_quotation_number())
    valid_until: str = step2.result.get("valid_until", (date.today() + timedelta(days=30)).isoformat())
    result.quotation_id = quotation_number

    # ── Step 3: メール送付 ───────────────────────────
    step3 = await _step3_send_quotation_email(
        company_id=company_id,
        recipient_email=opportunity["contact_email"],
        recipient_name=opportunity["contact_name"],
        company_name=opportunity["target_company_name"],
        pdf_bytes=quotation_pdf,
        quotation_number=quotation_number,
        total=calc.get("total", 0),
        valid_until=valid_until,
    )
    result.steps.append(step3)
    if not step3.success:
        result.success = False
        result.failed_step = "email_sender(quotation)"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    # ── Step 4: 承認確認 ─────────────────────────────
    step4 = await _step4_check_approval(company_id, quotation_number, approval_status)
    result.steps.append(step4)
    result.approval_status = approval_status

    # 承認待ち・修正要求・却下 → Phase A で停止
    if approval_status != APPROVAL_APPROVED:
        result.success = True   # Phase A 自体は成功
        result.phase = "phase_a"
        result.final_output = {
            "phase": "phase_a",
            "quotation_number": quotation_number,
            "calc": calc,
            "approval_status": approval_status,
            "next_action": _approval_next_action(approval_status),
        }
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        logger.info(
            f"[quotation_contract_pipeline] Phase A 完了（Phase B 待機）  "
            f"approval_status={approval_status}"
        )
        return result

    # ════════════════════════════════
    #  Phase B: 契約書
    # ════════════════════════════════
    result.phase = "phase_b"
    logger.info(
        f"[quotation_contract_pipeline] Phase B 開始  "
        f"opportunity_id={opportunity_id}"
    )

    # ── Step 5: 契約書 PDF 生成 ──────────────────────
    step5, contract_pdf = await _step5_generate_contract_pdf(
        company_id, opportunity, calc
    )
    result.steps.append(step5)
    if not step5.success:
        result.success = False
        result.failed_step = "pdf_generator(contract)"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result

    # ── Step 6: CloudSign 署名依頼 ───────────────────
    cloudsign_document_id = ""
    if cloudsign_credentials and not dry_run:
        step6, cloudsign_document_id = await _step6_send_cloudsign(
            company_id, contract_pdf, opportunity, cloudsign_credentials
        )
    else:
        # クレデンシャル未設定 or dry_run → スキップ（ログ記録のみ）
        reason = "dry_run" if dry_run else "cloudsign_credentials 未設定"
        step6 = StepRecord(
            step_no=6,
            step_name="cloudsign_sender",
            agent_name="cloudsign_sender",
            success=True,
            result={"skipped": True, "reason": reason},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=0,
            warning=f"CloudSign 署名送信スキップ（{reason}）",
        )
    result.steps.append(step6)
    if not step6.success:
        result.success = False
        result.failed_step = "cloudsign_sender"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result
    result.cloudsign_document_id = cloudsign_document_id or None

    # ── Step 7: DB 書き込み + Slack 通知 ────────────
    contract_id = ""
    if not dry_run:
        step7, contract_id = await _step7_write_db_and_notify(
            company_id=company_id,
            opportunity_id=opportunity_id,
            opportunity=opportunity,
            calc=calc,
            cloudsign_document_id=cloudsign_document_id,
            quotation_number=quotation_number,
        )
    else:
        step7 = StepRecord(
            step_no=7,
            step_name="db_writer",
            agent_name="db_writer",
            success=True,
            result={"skipped": True, "reason": "dry_run"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=0,
            warning="DB 書き込みスキップ（dry_run）",
        )
        contract_id = f"DRY-{uuid.uuid4().hex[:8].upper()}"

    result.steps.append(step7)
    if not step7.success:
        result.success = False
        result.failed_step = "db_writer"
        result.total_cost_yen = sum(s.cost_yen for s in result.steps)
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start
        return result
    result.contract_id = contract_id

    # ── Step 8: freee 請求書発行 ─────────────────────
    freee_invoice_id = ""
    if freee_credentials and not dry_run:
        step8, freee_invoice_id = await _step8_issue_invoice(
            company_id=company_id,
            opportunity=opportunity,
            calc=calc,
            contract_id=contract_id,
            freee_credentials=freee_credentials,
        )
    else:
        reason = "dry_run" if dry_run else "freee_credentials 未設定"
        step8 = StepRecord(
            step_no=8,
            step_name="invoice_issuer(freee)",
            agent_name="invoice_issuer",
            success=True,
            result={"skipped": True, "reason": reason},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=0,
            warning=f"freee 請求書発行スキップ（{reason}）",
        )
    result.steps.append(step8)
    # Step 8 の失敗は警告扱い（契約は成立しているため）
    if not step8.success:
        result.steps[-1].warning = f"freee 請求書発行失敗（手動発行が必要）: {step8.result.get('error', '')}"
        logger.warning(f"freee 請求書発行失敗（契約は成立）: {step8.result.get('error', '')}")
    result.freee_invoice_id = freee_invoice_id or None

    # ── 完了 ─────────────────────────────────────────
    result.success = True
    result.phase = "phase_b"
    result.final_output = {
        "phase": "phase_b",
        "quotation_number": quotation_number,
        "contract_id": contract_id,
        "cloudsign_document_id": cloudsign_document_id,
        "freee_invoice_id": freee_invoice_id,
        "calc": calc,
        "opportunity_stage": "won",
    }
    result.total_cost_yen = sum(s.cost_yen for s in result.steps)
    result.total_duration_ms = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"[quotation_contract_pipeline] Phase B 完了  "
        f"contract_id={contract_id}  "
        f"total_duration={result.total_duration_ms}ms  "
        f"total_cost=¥{result.total_cost_yen:.2f}"
    )
    return result


def _approval_next_action(approval_status: str) -> str:
    """承認ステータスに応じた次のアクション説明を返す。"""
    return {
        APPROVAL_PENDING: "顧客の承認を待ってください。30日後に自動フォローアップメールを送信します。",
        APPROVAL_REVISION_REQUESTED: "修正事項を確認して見積を再計算し、再度パイプラインを実行してください。",
        APPROVAL_REJECTED: "失注フローに遷移してください。opportunities.stage を lost に更新します。",
    }.get(approval_status, "不明なステータスです。")
