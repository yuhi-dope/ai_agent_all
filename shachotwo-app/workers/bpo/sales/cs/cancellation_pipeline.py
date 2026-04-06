"""解約フローパイプライン

顧客が解約申請を行った時点から、データエクスポート・最終請求・テナント無効化・
解約理由収集・完了通知までを一気通貫で処理する。

トリガー: 解約申請 API（POST /api/v1/crm/customers/{id}/cancel）

Steps:
  Step 1: cancellation_intake
      contracts テーブルのステータスを termination_requested に更新。
      解約理由の LLM 分類（price / feature_lack / competitor / not_needed / other）。

  Step 2: data_export
      knowledge_items を JSON/CSV でエクスポート。
      execution_logs をエクスポート。
      Supabase Storage に ZIP 保存 → ダウンロード URL 生成。

  Step 3: final_invoice
      freee Connector で未払い分の最終請求書を発行。
      解約日までの日割り計算。

  Step 4: tenant_deactivation
      customers.status を churned に更新。
      contracts.status を terminated に更新。
      churned_at タイムスタンプ記録。

  Step 5: churn_learning
      解約理由アンケートメール送信（message micro-agent）。
      win_loss_patterns テーブルに outcome="churned" で保存。
      customer_health の最終スコアも記録（解約予測モデル学習データ）。

  Step 6: completion_notify
      顧客にデータエクスポート URL 付きの完了メール送信。
      社内ログ出力（Slack 未設定時は logger.info）。
"""
from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from llm.client import LLMTask, ModelTier, get_llm_client
from workers.micro.message import run_message_drafter
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 解約理由のラベル（LLM 分類ラベルと一致させる）
CancellationReason = Literal[
    "price",         # 価格
    "feature_lack",  # 機能不足
    "competitor",    # 他社移行
    "not_needed",    # 不要
    "other",         # その他
]

# データエクスポートの Supabase Storage バケット名
EXPORT_BUCKET = "bpo-exports"

# 解約完了メール用テンプレートキー
COMPLETION_EMAIL_TEMPLATE = "cancellation_complete"

# 解約アンケートメール用テンプレートキー
SURVEY_EMAIL_TEMPLATE = "churn_survey"

# 解約理由アンケートの送信猶予（解約完了後 N 日後）
SURVEY_DELAY_DAYS = 3


# ---------------------------------------------------------------------------
# 結果モデル
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """1ステップの実行結果"""
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
class CancellationPipelineResult:
    """解約フローパイプライン全体の実行結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        status_mark = "OK" if self.success else "NG"
        lines = [
            f"[{status_mark}] 解約フローパイプライン",
            f"  ステップ完了: {len(self.steps)}/6",
            f"  LLMコスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            mark = "OK" if s.success else "NG"
            warn = f"  [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} [{mark}] {s.step_name}: "
                f"confidence={s.confidence:.2f}, ¥{s.cost_yen:.2f}{warn}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ms_now(base_ms: int) -> int:
    return int(time.time() * 1000) - base_ms


def _classify_reason_llm_prompt(reason_text: str) -> str:
    """解約理由テキストを LLM 分類するためのプロンプトを組み立てる。"""
    return (
        "次の解約理由テキストを、以下のカテゴリのひとつに分類してください。\n"
        "カテゴリ: price, feature_lack, competitor, not_needed, other\n"
        "JSON 形式 {\"reason\": \"<category>\", \"summary\": \"<50字以内の日本語要約>\"} "
        "のみを返してください。\n\n"
        f"解約理由テキスト:\n{reason_text}"
    )


async def _classify_cancellation_reason(
    reason_text: str,
    company_id: str,
) -> tuple[str, str, float]:
    """
    解約理由テキストを LLM で分類する。

    Returns:
        (reason_category, reason_summary, cost_yen)
    """
    if not reason_text.strip():
        return "other", "解約理由の記載なし", 0.0

    llm = get_llm_client()
    task = LLMTask(
        messages=[
            {
                "role": "system",
                "content": (
                    "あなたは SaaS 解約理由を分析するアナリストです。"
                    "ユーザーの指示に従い JSON のみを返してください。"
                ),
            },
            {"role": "user", "content": _classify_reason_llm_prompt(reason_text)},
        ],
        tier=ModelTier.FAST,
        max_tokens=256,
        temperature=0.1,
        company_id=company_id,
        task_type="cancellation_reason_classify",
    )
    try:
        resp = await llm.generate(task)
        import re
        content = resp.content.strip()
        content = re.sub(r"^```(?:json)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        parsed = json.loads(content)
        reason = parsed.get("reason", "other")
        summary = parsed.get("summary", reason_text[:50])
        cost = resp.cost_yen if hasattr(resp, "cost_yen") else 0.0
        return reason, summary, cost
    except Exception as e:
        logger.warning(f"cancellation_reason classify fallback: {e}")
        return "other", reason_text[:50], 0.0


def _build_knowledge_csv(knowledge_items: list[dict]) -> bytes:
    """knowledge_items リストを CSV バイト列に変換する。"""
    import csv

    buf = io.StringIO()
    if not knowledge_items:
        return b""
    headers = list(knowledge_items[0].keys()) if knowledge_items else []
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in knowledge_items:
        writer.writerow({k: str(v) for k, v in row.items()})
    return buf.getvalue().encode("utf-8-sig")


def _build_zip(
    knowledge_items: list[dict],
    execution_logs: list[dict],
    customer_id: str,
) -> bytes:
    """エクスポートデータを ZIP に圧縮して返す。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{customer_id}_knowledge_items.json",
            json.dumps(knowledge_items, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            f"{customer_id}_knowledge_items.csv",
            _build_knowledge_csv(knowledge_items).decode("utf-8-sig"),
        )
        zf.writestr(
            f"{customer_id}_execution_logs.json",
            json.dumps(execution_logs, ensure_ascii=False, indent=2),
        )
    buf.seek(0)
    return buf.read()


def _daily_amount(monthly_amount: int, cancellation_date: date) -> int:
    """月額料金を解約日までの日割り金額に換算する。"""
    days_in_month = 30  # 標準化: 1ヶ月 = 30日
    used_days = cancellation_date.day
    return int(Decimal(str(monthly_amount)) * Decimal(str(used_days)) / Decimal(str(days_in_month)))


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------

async def run_cancellation_pipeline(
    company_id: str,
    customer_id: str,
    input_data: dict[str, Any],
) -> CancellationPipelineResult:
    """
    解約フローパイプライン実行。

    Args:
        company_id: テナント ID（RLS に用いるテナント識別子）
        customer_id: 解約対象の顧客 ID
        input_data: {
            "reason_text": str,          # 解約理由（自由記述）
            "contract_id": str,          # 対象契約 ID
            "monthly_amount": int,       # 月額料金（円）
            "cancellation_date": str,    # 解約日 YYYY-MM-DD（省略時: 当日）
            "contact_email": str,        # 顧客連絡先メール
            "customer_name": str,        # 顧客名
            "plan_name": str,            # プラン名（最終請求書用）
            "slack_webhook_url": str | None,  # 社内通知（省略可）
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "customer_id": customer_id,
        "contract_id": input_data.get("contract_id", ""),
        "monthly_amount": input_data.get("monthly_amount", 0),
        "contact_email": input_data.get("contact_email", ""),
        "customer_name": input_data.get("customer_name", ""),
        "plan_name": input_data.get("plan_name", ""),
        "slack_webhook_url": input_data.get("slack_webhook_url"),
        "cancellation_date": input_data.get("cancellation_date")
            or date.today().isoformat(),
        "reason_text": input_data.get("reason_text", ""),
    }

    # ── ヘルパー ──────────────────────────────────────────────────────────────

    def _add_step(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f} < {CONFIDENCE_WARNING_THRESHOLD})"
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

    def _fail(step_name: str) -> CancellationPipelineResult:
        return CancellationPipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=_ms_now(pipeline_start),
            failed_step=step_name,
        )

    def _micro_out(
        agent_name: str,
        success: bool,
        result: dict,
        confidence: float,
        cost_yen: float,
        start_ms: int,
    ) -> MicroAgentOutput:
        return MicroAgentOutput(
            agent_name=agent_name,
            success=success,
            result=result,
            confidence=confidence,
            cost_yen=cost_yen,
            duration_ms=_ms_now(start_ms),
        )

    # ── Step 1: cancellation_intake ──────────────────────────────────────────
    # contracts テーブルのステータスを termination_requested に更新し、
    # 解約理由を LLM で分類する。
    s1_start = int(time.time() * 1000)
    try:
        reason_text = context["reason_text"]
        reason_category, reason_summary, classify_cost = (
            await _classify_cancellation_reason(reason_text, company_id)
        )
        context["reason_category"] = reason_category
        context["reason_summary"] = reason_summary

        # contracts テーブルの更新を saas_writer 経由で記録する
        # （実環境では Supabase クライアントを直接呼ぶが、
        #   マイクロエージェントパターンに合わせて saas_writer を経由する）
        writer_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase",
                "table": "contracts",
                "operation": "update",
                "filter": {"id": context["contract_id"]},
                "data": {
                    "status": "termination_requested",
                    "cancellation_reason": reason_category,
                    "cancellation_reason_text": reason_text[:500],
                    "termination_requested_at": _now_iso(),
                },
            },
            context=context,
        ))

        s1_out = _micro_out(
            agent_name="cancellation_intake",
            success=True,
            result={
                "reason_category": reason_category,
                "reason_summary": reason_summary,
                "contract_status": "termination_requested",
                "contract_id": context["contract_id"],
                "writer_success": writer_out.success,
            },
            confidence=0.95,
            cost_yen=classify_cost,
            start_ms=s1_start,
        )
    except Exception as e:
        logger.error(f"cancellation_intake error: {e}")
        s1_out = _micro_out(
            agent_name="cancellation_intake",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            start_ms=s1_start,
        )

    _add_step(1, "cancellation_intake", "cancellation_intake", s1_out)
    if not s1_out.success:
        return _fail("cancellation_intake")

    # ── Step 2: data_export ──────────────────────────────────────────────────
    # knowledge_items / execution_logs を取得し ZIP にまとめて Storage へ保存。
    s2_start = int(time.time() * 1000)
    try:
        # knowledge_items の読み取り
        ki_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "table": "knowledge_items",
                "filter": {"company_id": company_id, "is_active": True},
                "columns": [
                    "id", "category", "subcategory", "title", "content",
                    "source", "confidence", "created_at", "updated_at",
                ],
                "limit": 10000,
            },
            context=context,
        ))
        knowledge_items: list[dict] = ki_out.result.get("rows", [])

        # execution_logs の読み取り
        el_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "table": "execution_logs",
                "filter": {"company_id": company_id},
                "columns": [
                    "id", "pipeline_name", "status", "input_summary",
                    "output_summary", "cost_yen", "duration_ms", "created_at",
                ],
                "limit": 50000,
            },
            context=context,
        ))
        execution_logs: list[dict] = el_out.result.get("rows", [])

        # ZIP 生成
        zip_bytes = _build_zip(knowledge_items, execution_logs, customer_id)
        zip_key = f"exports/{company_id}/{customer_id}/data_export_{date.today().isoformat()}.zip"

        # Storage への保存を saas_writer 経由で行う
        storage_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase_storage",
                "bucket": EXPORT_BUCKET,
                "key": zip_key,
                "content_type": "application/zip",
                "data_base64": __import__("base64").b64encode(zip_bytes).decode(),
            },
            context=context,
        ))

        # ダウンロード URL（Storage が返した署名付き URL。失敗時は空文字）
        download_url: str = storage_out.result.get("signed_url", "")
        if not download_url:
            # フォールバック: パスベースのパブリック URL を組み立てる
            supabase_url = os.getenv("SUPABASE_URL", "")
            download_url = (
                f"{supabase_url}/storage/v1/object/{EXPORT_BUCKET}/{zip_key}"
                if supabase_url else zip_key
            )

        context["export_zip_key"] = zip_key
        context["export_download_url"] = download_url
        context["knowledge_items_count"] = len(knowledge_items)
        context["execution_logs_count"] = len(execution_logs)

        s2_out = _micro_out(
            agent_name="data_export",
            success=True,
            result={
                "zip_key": zip_key,
                "download_url": download_url,
                "knowledge_items_count": len(knowledge_items),
                "execution_logs_count": len(execution_logs),
                "zip_size_bytes": len(zip_bytes),
            },
            confidence=0.95 if storage_out.success else 0.70,
            cost_yen=0.0,
            start_ms=s2_start,
        )
    except Exception as e:
        logger.error(f"data_export error: {e}")
        s2_out = _micro_out(
            agent_name="data_export",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            start_ms=s2_start,
        )

    _add_step(2, "data_export", "data_export", s2_out)
    if not s2_out.success:
        return _fail("data_export")

    # ── Step 3: final_invoice ────────────────────────────────────────────────
    # freee Connector で未払い分の最終請求書を発行する。
    # 解約日までの日割り計算を行う。
    s3_start = int(time.time() * 1000)
    try:
        cancel_date = date.fromisoformat(context["cancellation_date"])
        monthly_amount = int(context["monthly_amount"])
        prorated_amount = _daily_amount(monthly_amount, cancel_date)

        # freee へ最終請求書を発行（saas_writer 経由）
        freee_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "freee",
                "operation": "create_invoice",
                "data": {
                    "customer_id": customer_id,
                    "customer_name": context["customer_name"],
                    "invoice_date": _now_iso()[:10],
                    "due_date": cancel_date.isoformat(),
                    "items": [
                        {
                            "description": (
                                f"最終月利用料（{cancel_date.year}年{cancel_date.month}月"
                                f"1日〜{cancel_date.day}日 日割り）"
                            ),
                            "quantity": 1,
                            "unit_price": prorated_amount,
                            "tax_rate": 10,
                        }
                    ],
                    "notes": (
                        f"解約に伴う最終請求書。"
                        f"プラン: {context['plan_name']}。"
                        f"解約日: {cancel_date.isoformat()}。"
                    ),
                },
            },
            context=context,
        ))

        invoice_id: str = freee_out.result.get("invoice_id", "")
        context["final_invoice_id"] = invoice_id
        context["prorated_amount"] = prorated_amount

        s3_out = _micro_out(
            agent_name="final_invoice",
            success=True,
            result={
                "invoice_id": invoice_id,
                "prorated_amount": prorated_amount,
                "monthly_amount": monthly_amount,
                "cancellation_date": cancel_date.isoformat(),
                "used_days": cancel_date.day,
                "freee_success": freee_out.success,
            },
            confidence=0.92 if freee_out.success else 0.70,
            cost_yen=0.0,
            start_ms=s3_start,
        )
    except Exception as e:
        logger.error(f"final_invoice error: {e}")
        s3_out = _micro_out(
            agent_name="final_invoice",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            start_ms=s3_start,
        )

    _add_step(3, "final_invoice", "final_invoice", s3_out)
    if not s3_out.success:
        return _fail("final_invoice")

    # ── Step 4: tenant_deactivation ──────────────────────────────────────────
    # customers.status を churned に更新し、contracts.status を terminated にする。
    s4_start = int(time.time() * 1000)
    try:
        churned_at = _now_iso()

        # customers テーブル更新
        customers_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase",
                "table": "customers",
                "operation": "update",
                "filter": {"id": customer_id},
                "data": {
                    "status": "churned",
                    "churned_at": churned_at,
                },
            },
            context=context,
        ))

        # contracts テーブル更新
        contracts_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase",
                "table": "contracts",
                "operation": "update",
                "filter": {"id": context["contract_id"]},
                "data": {
                    "status": "terminated",
                    "terminated_at": churned_at,
                },
            },
            context=context,
        ))

        context["churned_at"] = churned_at

        s4_out = _micro_out(
            agent_name="tenant_deactivation",
            success=True,
            result={
                "customer_status": "churned",
                "contract_status": "terminated",
                "churned_at": churned_at,
                "customers_write_success": customers_out.success,
                "contracts_write_success": contracts_out.success,
            },
            confidence=1.0 if (customers_out.success and contracts_out.success) else 0.70,
            cost_yen=0.0,
            start_ms=s4_start,
        )
    except Exception as e:
        logger.error(f"tenant_deactivation error: {e}")
        s4_out = _micro_out(
            agent_name="tenant_deactivation",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            start_ms=s4_start,
        )

    _add_step(4, "tenant_deactivation", "tenant_deactivation", s4_out)
    if not s4_out.success:
        return _fail("tenant_deactivation")

    # ── Step 5: churn_learning ───────────────────────────────────────────────
    # 解約理由アンケートメールを送信し、学習データを win_loss_patterns に保存する。
    s5_start = int(time.time() * 1000)
    churn_cost = 0.0
    try:
        # アンケートメールのドラフトを message micro-agent で生成
        survey_draft = await run_message_drafter(
            document_type="解約アンケート依頼",
            context={
                "customer_name": context["customer_name"],
                "plan_name": context["plan_name"],
                "cancellation_date": context["cancellation_date"],
                "reason_category": context["reason_category"],
                "reason_summary": context["reason_summary"],
                "survey_delay_days": SURVEY_DELAY_DAYS,
                # message.py の既存フォールバックが不動産用なので
                # 渡さない key は無視される
                "property_name": "",
                "tenant_name": context["customer_name"],
                "room_number": "",
                "monthly_rent": 0,
                "payment_due_date": "",
                "overdue_days": 0,
                "late_fee": 0,
                "total_overdue": 0,
                "reference_date": context["cancellation_date"],
            },
            company_id=company_id,
            model_tier=ModelTier.FAST,
        )

        # win_loss_patterns テーブルに churned レコードを保存
        pattern_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase",
                "table": "win_loss_patterns",
                "operation": "insert",
                "data": {
                    "company_id": company_id,
                    "customer_id": customer_id,
                    "outcome": "churned",
                    "reason_category": context["reason_category"],
                    "reason_summary": context["reason_summary"],
                    "reason_text": context["reason_text"][:500],
                    "plan_name": context["plan_name"],
                    "churned_at": context["churned_at"],
                    "created_at": _now_iso(),
                },
            },
            context=context,
        ))

        # customer_health の最終スコアを読み取って保存
        # （スコアが存在しない場合は skip）
        health_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "table": "customer_health",
                "filter": {"customer_id": customer_id},
                "columns": ["score", "usage", "engagement", "support", "nps", "expansion"],
                "order_by": "created_at desc",
                "limit": 1,
            },
            context=context,
        ))
        final_health_score: dict | None = None
        if health_out.success and health_out.result.get("rows"):
            final_health_score = health_out.result["rows"][0]

        if final_health_score:
            await run_saas_writer(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_writer",
                payload={
                    "service": "supabase",
                    "table": "win_loss_patterns",
                    "operation": "update",
                    "filter": {
                        "customer_id": customer_id,
                        "outcome": "churned",
                    },
                    "data": {"final_health_score": final_health_score},
                },
                context=context,
            ))

        context["survey_email_subject"] = survey_draft.subject
        context["survey_email_body"] = survey_draft.body

        s5_out = _micro_out(
            agent_name="churn_learning",
            success=True,
            result={
                "pattern_saved": pattern_out.success,
                "final_health_score": final_health_score,
                "survey_email_subject": survey_draft.subject,
                "survey_template_fallback": survey_draft.is_template_fallback,
                "model_used": survey_draft.model_used,
            },
            confidence=0.90,
            cost_yen=churn_cost,
            start_ms=s5_start,
        )
    except Exception as e:
        logger.warning(f"churn_learning non-fatal error: {e}")
        # 学習データ保存失敗は致命的ではない（解約フロー自体は続行）
        s5_out = _micro_out(
            agent_name="churn_learning",
            success=True,  # 非致命的: パイプラインは続行
            result={"error": str(e), "skipped": True},
            confidence=0.50,
            cost_yen=churn_cost,
            start_ms=s5_start,
        )

    _add_step(5, "churn_learning", "churn_learning", s5_out)

    # ── Step 6: completion_notify ────────────────────────────────────────────
    # 顧客にデータエクスポート URL 付きの完了メールを送信する。
    # 社内ログを出力する（Slack 設定がある場合は Slack にも通知）。
    s6_start = int(time.time() * 1000)
    notify_cost = 0.0
    try:
        # 完了メールのドラフトを LLM で生成
        llm = get_llm_client()
        completion_prompt = (
            f"解約が完了した顧客への完了通知メールを作成してください。\n\n"
            f"顧客名: {context['customer_name']}\n"
            f"プラン: {context['plan_name']}\n"
            f"解約日: {context['cancellation_date']}\n"
            f"データダウンロードURL: {context['export_download_url']}\n"
            f"ダウンロード可能ファイル: "
            f"ナレッジ {context['knowledge_items_count']}件 / "
            f"実行ログ {context['execution_logs_count']}件\n\n"
            f"JSON形式 {{\"subject\": \"件名\", \"body\": \"本文\"}} のみを返してください。"
        )
        comp_task = LLMTask(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたは SaaS のカスタマーサクセス担当です。"
                        "解約完了メールを丁寧かつ簡潔に作成してください。"
                        "JSON のみを返してください。"
                    ),
                },
                {"role": "user", "content": completion_prompt},
            ],
            tier=ModelTier.FAST,
            max_tokens=512,
            temperature=0.3,
            company_id=company_id,
            task_type="cancellation_completion_email",
        )
        try:
            comp_resp = await llm.generate(comp_task)
            import re
            content = comp_resp.content.strip()
            content = re.sub(r"^```(?:json)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
            comp_parsed = json.loads(content)
            completion_subject = comp_parsed.get("subject", "解約手続き完了のご案内")
            completion_body = comp_parsed.get("body", "")
            notify_cost = comp_resp.cost_yen if hasattr(comp_resp, "cost_yen") else 0.0
        except Exception as llm_err:
            logger.warning(f"completion_notify LLM fallback: {llm_err}")
            completion_subject = "解約手続き完了のご案内"
            completion_body = (
                f"{context['customer_name']} 様\n\n"
                f"このたびはご利用いただきありがとうございました。\n"
                f"解約手続きが完了しました（解約日: {context['cancellation_date']}）。\n\n"
                f"お客様のデータは以下の URL よりダウンロードいただけます。\n"
                f"URL: {context['export_download_url']}\n\n"
                f"またのご縁をお待ちしております。\n"
                f"シャチョツー カスタマーサクセス"
            )

        # メール送信を saas_writer（SendGrid 経由）で実行
        email_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "sendgrid",
                "operation": "send_email",
                "data": {
                    "to": context["contact_email"],
                    "subject": completion_subject,
                    "body": completion_body,
                    "template_key": COMPLETION_EMAIL_TEMPLATE,
                },
            },
            context=context,
        ))

        # 社内通知（Slack または logger）
        slack_webhook = context.get("slack_webhook_url")
        slack_notified = False
        if slack_webhook:
            try:
                slack_out = await run_saas_writer(MicroAgentInput(
                    company_id=company_id,
                    agent_name="saas_writer",
                    payload={
                        "service": "slack_webhook",
                        "webhook_url": slack_webhook,
                        "data": {
                            "text": (
                                f"[解約完了] {context['customer_name']} "
                                f"/ プラン: {context['plan_name']} "
                                f"/ 解約日: {context['cancellation_date']} "
                                f"/ 理由: {context['reason_category']} "
                                f"({context['reason_summary']})"
                            ),
                        },
                    },
                    context=context,
                ))
                slack_notified = slack_out.success
            except Exception as slack_err:
                logger.warning(f"slack notification failed (non-fatal): {slack_err}")

        # Slack 未設定または失敗時は logger で記録
        if not slack_notified:
            logger.info(
                "cancellation_pipeline complete: "
                "customer_id=%s customer_name=%s plan=%s "
                "cancellation_date=%s reason=%s(%s) "
                "export_url=%s invoice_id=%s prorated_amount=%d",
                customer_id,
                context["customer_name"],
                context["plan_name"],
                context["cancellation_date"],
                context["reason_category"],
                context["reason_summary"],
                context.get("export_download_url", ""),
                context.get("final_invoice_id", ""),
                context.get("prorated_amount", 0),
            )

        s6_out = _micro_out(
            agent_name="completion_notify",
            success=True,
            result={
                "completion_email_sent": email_out.success,
                "completion_email_subject": completion_subject,
                "slack_notified": slack_notified,
                "download_url": context["export_download_url"],
            },
            confidence=0.95 if email_out.success else 0.70,
            cost_yen=notify_cost,
            start_ms=s6_start,
        )
    except Exception as e:
        logger.error(f"completion_notify error: {e}")
        # 通知失敗は解約フロー自体を取り消さない（non-fatal 扱い）
        s6_out = _micro_out(
            agent_name="completion_notify",
            success=True,
            result={"error": str(e), "skipped": True},
            confidence=0.50,
            cost_yen=notify_cost,
            start_ms=s6_start,
        )

    _add_step(6, "completion_notify", "completion_notify", s6_out)

    # ── 最終結果 ──────────────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = _ms_now(pipeline_start)

    final_output: dict[str, Any] = {
        "customer_id": customer_id,
        "customer_name": context["customer_name"],
        "contract_id": context["contract_id"],
        "reason_category": context.get("reason_category", "other"),
        "reason_summary": context.get("reason_summary", ""),
        "churned_at": context.get("churned_at", ""),
        "cancellation_date": context["cancellation_date"],
        "export_download_url": context.get("export_download_url", ""),
        "knowledge_items_count": context.get("knowledge_items_count", 0),
        "execution_logs_count": context.get("execution_logs_count", 0),
        "final_invoice_id": context.get("final_invoice_id", ""),
        "prorated_amount": context.get("prorated_amount", 0),
    }

    logger.info(
        "cancellation_pipeline complete: "
        "customer_id=%s duration=%dms cost=¥%.2f",
        customer_id,
        total_duration,
        total_cost_yen,
    )

    return CancellationPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
    )
