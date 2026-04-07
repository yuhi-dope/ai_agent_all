"""
CS パイプライン⑥ — サポート自動対応

トリガー: メール着信 / チャット / フォーム送信
設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.6

Steps:
  Step 1: extractor       問い合わせテキスト → {category, priority, summary} 構造化
  Step 2: saas_reader     顧客コンテキスト収集（contracts/past_tickets/usage）
           + knowledge/qa FAQ・ナレッジベース検索
  Step 3: generator       LLM回答生成 + confidence算出
  Step 4: ConditionEvaluator
           confidence ≥ 0.85  → AI自動回答送信
           confidence 0.5-0.85 → AIドラフト + 人間レビューキュー
           confidence < 0.5   → 即エスカレーション
           category = billing → 経理チームルーティング
           priority = urgent   → 即Slackアラート
  Step 5: message         回答送信（メール / チャット）またはエスカレーション通知
  Step 6: validator       SLA監視（初回応答1h, 解決24h / urgent4h）
  Step 7: saas_writer     support_tickets + ticket_messages に保存
                          解決時 satisfaction_score 収集リンクを添付
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.support_response import SYSTEM_SUPPORT, USER_SUPPORT_TEMPLATE
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# ─── 定数 ──────────────────────────────────────────────────────────────────

# AI自動送信する信頼度の閾値
CONFIDENCE_AUTO_SEND = 0.85
# 人間レビューに回す信頼度の閾値（これ未満はエスカレーション）
CONFIDENCE_HUMAN_REVIEW = 0.50

# SLA定義（分）
SLA_FIRST_RESPONSE_MINUTES = 60   # 初回応答: 1時間
SLA_RESOLUTION_MINUTES = 60 * 24  # 解決: 24時間
SLA_URGENT_RESOLUTION_MINUTES = 60 * 4  # urgent解決: 4時間

# confidence警告ライン
CONFIDENCE_WARNING_THRESHOLD = 0.70

# チケット分類スキーマ
TICKET_CLASSIFICATION_SCHEMA = {
    "category": "問い合わせカテゴリ: account / billing / brain / bpo / integration / bug / feature_request / other",
    "priority": "緊急度: low / medium / high / urgent",
    "summary": "問い合わせ内容の1〜2行の要約（日本語）",
    "subject": "件名（問い合わせから抽出、なければ本文冒頭から生成）",
    "customer_name": "問い合わせ者の名前（なければnull）",
    "sentiment": "顧客の感情: positive / neutral / negative",
}


# ─── データモデル ───────────────────────────────────────────────────────────

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
class SupportAutoResponseResult:
    """
    サポート自動対応パイプライン全体の実行結果

    Attributes:
        success:          パイプライン全体の成否
        steps:            各ステップの実行記録（精度・コスト・時間）
        routing_decision: "auto_send" | "human_review" | "escalate" | "billing_routing"
        ticket_id:        作成 or 更新したチケットID
        response_sent:    実際に顧客へ送信したか
        ai_response:      AI生成回答テキスト
        confidence:       AI回答の信頼度スコア
        sla_due_at:       SLA期限（ISO8601）
        sla_breached:     SLA違反が発生したか
        total_cost_yen:   パイプライン全体のLLMコスト
        total_duration_ms: パイプライン処理時間
        failed_step:      失敗ステップ名（成功時はNone）
    """
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    routing_decision: str = ""
    ticket_id: str | None = None
    response_sent: bool = False
    ai_response: str = ""
    confidence: float = 0.0
    sla_due_at: str = ""
    sla_breached: bool = False
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        status = "OK" if self.success else "FAIL"
        lines = [
            f"[{status}] サポート自動対応パイプライン",
            f"  routing={self.routing_decision}  confidence={self.confidence:.2f}",
            f"  ticket_id={self.ticket_id}  sent={self.response_sent}",
            f"  sla_due={self.sla_due_at}  sla_breached={self.sla_breached}",
            f"  steps={len(self.steps)}/7  cost=¥{self.total_cost_yen:.2f}  {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  failed_step={self.failed_step}")
        for s in self.steps:
            ok = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step{s.step_no} {ok} {s.step_name}: "
                f"conf={s.confidence:.2f} ¥{s.cost_yen:.2f}{warn}"
            )
        return "\n".join(lines)


# ─── 内部ユーティリティ ─────────────────────────────────────────────────────

def _now_jst() -> datetime:
    """現在時刻（JST）を返す。"""
    return datetime.now(tz=timezone(timedelta(hours=9)))


def _calc_sla_due(priority: str, created_at: datetime | None = None) -> str:
    """優先度に応じたSLA期限をISO8601文字列で返す。"""
    base = created_at or _now_jst()
    if priority == "urgent":
        due = base + timedelta(minutes=SLA_URGENT_RESOLUTION_MINUTES)
    else:
        due = base + timedelta(minutes=SLA_RESOLUTION_MINUTES)
    return due.isoformat()


async def _get_confidence_threshold(company_id: str) -> float:
    """scoring_model_versions から学習済みのconfidence閾値を取得する。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = (
            db.table("scoring_model_versions")
            .select("weights")
            .eq("company_id", company_id)
            .eq("model_type", "cs_confidence")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0].get("weights", {}).get("confidence_threshold", CONFIDENCE_AUTO_SEND)
    except Exception as e:
        logger.warning(f"confidence_threshold DB参照失敗（デフォルト値使用）: {e}")
    return CONFIDENCE_AUTO_SEND


async def _determine_routing(
    confidence: float,
    category: str,
    priority: str,
    company_id: str = "",
) -> str:
    """
    ConditionEvaluator ロジック。

    Returns:
        "billing_routing"  — category=billing → 経理チームにルーティング
        "urgent_escalate"  — priority=urgent → 即Slackアラート + エスカレーション
        "auto_send"        — confidence ≥ threshold → AI自動回答送信
        "human_review"     — confidence 0.50-threshold → AIドラフト + 人間レビュー
        "escalate"         — confidence < 0.50 → 即エスカレーション
    """
    threshold = await _get_confidence_threshold(company_id) if company_id else CONFIDENCE_AUTO_SEND
    # billing カテゴリは経理ルーティングが最優先
    if category == "billing":
        return "billing_routing"
    # urgent は即アラート（confidence問わず）
    if priority == "urgent":
        return "urgent_escalate"
    # confidence による分岐
    if confidence >= threshold:
        return "auto_send"
    if confidence >= CONFIDENCE_HUMAN_REVIEW:
        return "human_review"
    return "escalate"


def _format_faq_results(knowledge_items: list[dict]) -> str:
    """knowledge_itemsをプロンプト用テキストに整形する。"""
    if not knowledge_items:
        return "（関連するFAQ・ナレッジが見つかりませんでした）"
    lines = []
    for i, item in enumerate(knowledge_items[:5], 1):  # 上位5件まで
        title = item.get("title", "（タイトルなし）")
        content = item.get("content", item.get("summary", ""))
        relevance = item.get("relevance", item.get("confidence", 0.0))
        lines.append(
            f"[{i}] {title} (関連度: {relevance:.2f})\n{content[:300]}"
        )
    return "\n\n".join(lines)


async def _search_knowledge(company_id: str, query: str) -> list[dict]:
    """
    brain/knowledge/ のベクトル検索でFAQ・ナレッジを検索する。

    Supabase接続がない場合は空リストを返す（パイプライン継続）。
    """
    try:
        from brain.knowledge.search import search_knowledge_items
        results = await search_knowledge_items(
            company_id=company_id,
            query=query,
            limit=5,
        )
        return results or []
    except Exception as e:
        logger.warning(f"knowledge search failed (non-fatal): {e}")
        return []


# ─── メインパイプライン ─────────────────────────────────────────────────────

async def run_support_auto_response_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    dry_run: bool = False,
) -> SupportAutoResponseResult:
    """
    CS パイプライン⑥ サポート自動対応パイプライン実行。

    Args:
        company_id: テナントID（RLS用）
        input_data: {
            "ticket_text":    str  — 問い合わせ本文（必須）
            "ticket_subject": str  — 件名（任意）
            "channel":        str  — "email" | "chat" | "form"（省略時 "email"）
            "customer_id":    str  — 顧客ID（UUIDまたはメールアドレス）
            "ticket_id":      str  — 既存チケットID（再対応時、任意）
            "created_at":     str  — 問い合わせ日時 ISO8601（省略時は現在時刻）
        }
        dry_run: True の場合、送信・DB書き込みをスキップしてテスト実行

    Returns:
        SupportAutoResponseResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "channel": input_data.get("channel", "email"),
        "customer_id": input_data.get("customer_id", ""),
        "ticket_id": input_data.get("ticket_id"),
        "dry_run": dry_run,
    }

    # ─── ヘルパー関数 ─────────────────────────────────────────────────────

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

    def _fail(step_name: str) -> SupportAutoResponseResult:
        return SupportAutoResponseResult(
            success=False,
            steps=steps,
            failed_step=step_name,
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
        )

    def _total_cost() -> float:
        return sum(s.cost_yen for s in steps)

    def _elapsed_ms() -> int:
        return int(time.time() * 1000) - pipeline_start

    # ─── Step 1: extractor — チケット分類・緊急度判定 ──────────────────────
    ticket_text: str = input_data.get("ticket_text", "")
    ticket_subject: str = input_data.get("ticket_subject", "")
    full_text = f"件名: {ticket_subject}\n\n{ticket_text}".strip() if ticket_subject else ticket_text

    if not full_text:
        logger.error("support_pipeline: ticket_text が空です")
        return SupportAutoResponseResult(
            success=False,
            steps=steps,
            failed_step="extractor",
            total_cost_yen=0.0,
            total_duration_ms=_elapsed_ms(),
        )

    try:
        s1_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id,
            agent_name="extractor",
            payload={
                "text": full_text,
                "schema": TICKET_CLASSIFICATION_SCHEMA,
                "domain": "customer_support_ticket",
            },
            context=context,
        ))
    except Exception as e:
        s1_out = MicroAgentOutput(
            agent_name="extractor", success=False,
            result={"error": str(e)}, confidence=0.0, cost_yen=0.0,
            duration_ms=_elapsed_ms(),
        )

    _add_step(1, "extractor", "extractor", s1_out)
    if not s1_out.success:
        return _fail("extractor")

    extracted = s1_out.result.get("extracted", {})
    category: str = extracted.get("category") or "other"
    priority: str = extracted.get("priority") or "medium"
    summary: str = extracted.get("summary") or ticket_text[:100]
    subject: str = extracted.get("subject") or ticket_subject or summary
    sentiment: str = extracted.get("sentiment") or "neutral"
    context.update({
        "category": category,
        "priority": priority,
        "summary": summary,
        "subject": subject,
        "sentiment": sentiment,
    })
    logger.info(
        f"support_pipeline step1: category={category} priority={priority} "
        f"sentiment={sentiment} company={company_id}"
    )

    # ─── Step 2: saas_reader — 顧客コンテキスト収集 + FAQ検索 ─────────────
    s2_start = int(time.time() * 1000)
    customer_context: dict[str, Any] = {}
    knowledge_items: list[dict] = []
    s2_mock = False

    # 2a: 顧客の契約情報・利用状況・過去チケットをSupabaseから取得
    try:
        cust_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "get_customer_context",
                "params": {
                    "table": "customers",
                    "select": "id, company_name, plan_name, industry, created_at",
                    "limit": 1,
                },
            },
            context=context,
        ))
        if cust_out.success and cust_out.result.get("data"):
            customer_context = cust_out.result["data"][0]
        s2_mock = cust_out.result.get("mock", True)
    except Exception as e:
        logger.warning(f"support_pipeline step2 customer fetch failed (non-fatal): {e}")
        s2_mock = True

    # 過去チケット取得
    past_tickets: list[dict] = []
    try:
        past_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "list_past_tickets",
                "params": {
                    "table": "support_tickets",
                    "select": "id, subject, category, status, resolved_at, ai_handled",
                    "limit": 5,
                },
            },
            context=context,
        ))
        if past_out.success:
            past_tickets = past_out.result.get("data", [])
    except Exception as e:
        logger.warning(f"support_pipeline step2 past tickets failed (non-fatal): {e}")

    # 2b: knowledge/qa でFAQ・ナレッジ検索
    knowledge_items = await _search_knowledge(company_id, full_text)

    s2_confidence = 0.5 if s2_mock else 1.0
    s2_result = {
        "customer_context": customer_context,
        "past_tickets": past_tickets,
        "knowledge_items": knowledge_items,
        "knowledge_count": len(knowledge_items),
        "mock": s2_mock,
    }
    s2_out = MicroAgentOutput(
        agent_name="saas_reader", success=True,
        result=s2_result,
        confidence=s2_confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "saas_reader", "saas_reader", s2_out)
    context.update({
        "customer_context": customer_context,
        "past_tickets": past_tickets,
        "knowledge_items": knowledge_items,
    })

    # ─── Step 3: generator — AI回答生成 ─────────────────────────────────────
    s3_start = int(time.time() * 1000)
    ai_response_text: str = ""
    ai_confidence: float = 0.0
    escalation_info: dict[str, Any] = {"needed": False, "reason": None, "department": None, "priority": None}
    response_sources: list[dict] = []
    suggested_followup: str | None = None

    try:
        llm = get_llm_client()

        company_name: str = customer_context.get("company_name", "（不明）")
        plan_name: str = customer_context.get("plan_name", "（不明）")
        start_date_raw = customer_context.get("created_at", "")
        start_date: str = start_date_raw[:10] if start_date_raw else "（不明）"
        industry: str = customer_context.get("industry", "（不明）")

        faq_text = _format_faq_results(knowledge_items)
        user_prompt = USER_SUPPORT_TEMPLATE.format(
            inquiry_text=full_text[:2000],
            company_name=company_name,
            plan_name=plan_name,
            start_date=start_date,
            industry=industry,
            faq_results=faq_text,
        )

        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": SYSTEM_SUPPORT},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.STANDARD,
            max_tokens=1024,
            temperature=0.2,
            company_id=company_id,
            task_type="support_auto_response",
        ))

        raw = response.content.strip()
        # コードフェンスを除去
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        json_match = re.search(r'\{[\s\S]*\}', raw)

        if json_match:
            parsed: dict[str, Any] = json.loads(json_match.group())
            ai_response_text = parsed.get("response_text", "")
            ai_confidence = float(parsed.get("confidence", 0.0))
            response_sources = parsed.get("sources", [])
            escalation_info = parsed.get("escalation", {"needed": False})
            suggested_followup = parsed.get("suggested_followup")
            # sentimentをLLM判定で上書き（分類ステップより精度が高い）
            if parsed.get("sentiment"):
                context["sentiment"] = parsed["sentiment"]
        else:
            # JSONパース失敗: 生テキストを回答として使いconfidenceを下げる
            logger.warning("support_pipeline step3: LLM did not return valid JSON, using raw text")
            ai_response_text = raw[:1000]
            ai_confidence = 0.3

        s3_out = MicroAgentOutput(
            agent_name="generator", success=True,
            result={
                "response_text": ai_response_text,
                "confidence": ai_confidence,
                "sources": response_sources,
                "escalation": escalation_info,
                "suggested_followup": suggested_followup,
            },
            confidence=ai_confidence,
            cost_yen=response.cost_yen,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    except Exception as e:
        logger.error(f"support_pipeline step3 generator error: {e}")
        ai_confidence = 0.0
        escalation_info = {"needed": True, "reason": f"AI回答生成エラー: {str(e)[:100]}", "department": "cs_manager", "priority": "high"}
        s3_out = MicroAgentOutput(
            agent_name="generator", success=False,
            result={"error": str(e), "escalation": escalation_info},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "generator", "generator", s3_out)
    # generator失敗時もエスカレーション経路でパイプラインを継続する
    context.update({
        "ai_response_text": ai_response_text,
        "ai_confidence": ai_confidence,
        "response_sources": response_sources,
        "escalation_info": escalation_info,
        "suggested_followup": suggested_followup,
    })

    # ─── Step 4: ConditionEvaluator — 対応振り分け ────────────────────────
    s4_start = int(time.time() * 1000)

    # LLMがエスカレーション推奨 → confidence強制低下
    if escalation_info.get("needed"):
        effective_confidence = min(ai_confidence, 0.40)
    else:
        effective_confidence = ai_confidence

    routing = await _determine_routing(effective_confidence, category, priority, company_id)
    context["routing_decision"] = routing

    routing_metadata: dict[str, Any] = {
        "routing": routing,
        "effective_confidence": effective_confidence,
        "category": category,
        "priority": priority,
        "escalation_reason": escalation_info.get("reason"),
        "escalation_department": escalation_info.get("department"),
    }

    logger.info(
        f"support_pipeline step4: routing={routing} "
        f"confidence={effective_confidence:.2f} category={category} priority={priority}"
    )

    s4_out = MicroAgentOutput(
        agent_name="condition_evaluator", success=True,
        result=routing_metadata,
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "condition_evaluator", "condition_evaluator", s4_out)

    # ─── Step 5: message — 回答送信 / エスカレーション通知 ──────────────────
    s5_start = int(time.time() * 1000)
    response_sent = False
    message_result: dict[str, Any] = {"routing": routing, "dry_run": dry_run}

    try:
        if routing == "auto_send":
            # AI自動回答送信
            if not dry_run:
                await _send_response(
                    company_id=company_id,
                    channel=context["channel"],
                    customer_id=context["customer_id"],
                    response_text=ai_response_text,
                    ticket_id=context.get("ticket_id"),
                    feedback_link=_make_feedback_link(context.get("ticket_id")),
                )
            response_sent = True
            message_result["sent"] = True
            message_result["response_text"] = ai_response_text
            logger.info(f"support_pipeline step5: AI auto-sent (dry_run={dry_run})")

        elif routing == "human_review":
            # AIドラフトを人間レビューキューに投入
            if not dry_run:
                await _push_to_review_queue(
                    company_id=company_id,
                    ticket_subject=subject,
                    draft_response=ai_response_text,
                    confidence=effective_confidence,
                    category=category,
                    ticket_id=context.get("ticket_id"),
                )
            message_result["queued_for_review"] = True
            message_result["draft_response"] = ai_response_text
            logger.info(f"support_pipeline step5: queued for human review (dry_run={dry_run})")

        elif routing in ("escalate", "urgent_escalate"):
            # エスカレーション通知
            esc_reason = escalation_info.get("reason") or f"confidence低 ({effective_confidence:.2f})"
            esc_dept = escalation_info.get("department") or "cs_manager"
            if not dry_run:
                await _send_escalation_alert(
                    company_id=company_id,
                    ticket_subject=subject,
                    summary=summary,
                    priority=priority,
                    reason=esc_reason,
                    department=esc_dept,
                    routing=routing,
                )
            message_result["escalated"] = True
            message_result["escalation_reason"] = esc_reason
            message_result["escalation_department"] = esc_dept
            logger.info(
                f"support_pipeline step5: escalated to {esc_dept} "
                f"reason={esc_reason} (dry_run={dry_run})"
            )

        elif routing == "billing_routing":
            # 経理チームへルーティング
            if not dry_run:
                await _send_billing_routing(
                    company_id=company_id,
                    ticket_subject=subject,
                    summary=summary,
                    customer_id=context["customer_id"],
                )
            message_result["routed_to_billing"] = True
            logger.info(f"support_pipeline step5: routed to billing team (dry_run={dry_run})")

    except Exception as e:
        logger.error(f"support_pipeline step5 message error: {e}")
        message_result["error"] = str(e)

    s5_out = MicroAgentOutput(
        agent_name="message", success=True,
        result=message_result,
        confidence=1.0, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s5_start,
    )
    _add_step(5, "message", "message", s5_out)
    context["response_sent"] = response_sent

    # ─── Step 6: validator — SLA監視 ─────────────────────────────────────
    created_at_str: str = input_data.get("created_at", "")
    try:
        created_at = datetime.fromisoformat(created_at_str) if created_at_str else _now_jst()
    except ValueError:
        created_at = _now_jst()

    sla_due_at = _calc_sla_due(priority, created_at)
    now_iso = _now_jst().isoformat()

    # SLA違反チェック: 初回応答が1時間以内に送信されなかったか
    first_response_deadline = (
        created_at + timedelta(minutes=SLA_FIRST_RESPONSE_MINUTES)
    ).isoformat()
    sla_breached = (not response_sent) and (now_iso > first_response_deadline)

    sla_document = {
        "routing_decision": routing,
        "priority": priority,
        "response_sent": response_sent,
        "sla_due_at": sla_due_at,
        "first_response_deadline": first_response_deadline,
        "sla_breached": sla_breached,
    }

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": sla_document,
            "required_fields": ["routing_decision", "priority", "sla_due_at"],
            "rules": [
                # SLA違反が発生していないことを確認
                {"field": "sla_breached", "op": "eq", "value": 0},
            ],
        },
        context=context,
    ))
    _add_step(6, "validator", "output_validator", val_out)

    if sla_breached:
        logger.warning(
            f"support_pipeline step6: SLA BREACH detected "
            f"ticket={context.get('ticket_id')} priority={priority}"
        )
    context["sla_due_at"] = sla_due_at
    context["sla_breached"] = sla_breached

    # ─── Step 7: saas_writer — support_tickets + ticket_messages に保存 ────
    s7_start = int(time.time() * 1000)
    ticket_id: str | None = context.get("ticket_id")

    ticket_data: dict[str, Any] = {
        "subject": subject,
        "category": category,
        "priority": priority,
        "ai_handled": routing == "auto_send",
        "ai_confidence": effective_confidence,
        "ai_response": ai_response_text or None,
        "escalated": routing in ("escalate", "urgent_escalate", "billing_routing"),
        "escalation_reason": escalation_info.get("reason"),
        "sla_due_at": sla_due_at,
        "first_response_at": now_iso if response_sent else None,
        "status": _map_routing_to_status(routing),
    }
    if context["customer_id"]:
        ticket_data["customer_id"] = context["customer_id"]

    # 既存チケットは更新、新規は作成
    write_action = "update" if ticket_id else "insert"
    write_params: dict[str, Any] = {
        "table": "support_tickets",
        "data": ticket_data,
        "action": write_action,
    }
    if ticket_id:
        write_params["id"] = ticket_id

    writer_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "supabase",
            "operation": f"{write_action}_support_ticket",
            "params": write_params,
            "approved": True,
            "dry_run": dry_run,
        },
        context=context,
    ))
    _add_step(7, "saas_writer", "saas_writer", writer_out)

    # 新規作成時は saas_writer から返された operation_id を ticket_id として設定
    if not ticket_id and writer_out.success:
        ticket_id = writer_out.result.get("operation_id")

    # ticket_messages にAI回答を保存
    if ai_response_text and not dry_run:
        try:
            msg_data: dict[str, Any] = {
                "sender_type": "ai",
                "content": ai_response_text,
                "attachments": [],
            }
            if ticket_id:
                msg_data["ticket_id"] = ticket_id

            await run_saas_writer(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_writer",
                payload={
                    "service": "supabase",
                    "operation": "insert_ticket_message",
                    "params": {
                        "table": "ticket_messages",
                        "data": msg_data,
                        "action": "insert",
                    },
                    "approved": True,
                    "dry_run": dry_run,
                },
                context=context,
            ))
        except Exception as e:
            logger.warning(f"support_pipeline step7 ticket_messages write failed (non-fatal): {e}")

    # ─── 最終結果 ─────────────────────────────────────────────────────────
    total_duration = _elapsed_ms()
    total_cost = _total_cost()

    logger.info(
        f"support_pipeline complete: routing={routing} confidence={effective_confidence:.2f} "
        f"sla_breached={sla_breached} cost=¥{total_cost:.2f} {total_duration}ms"
    )

    return SupportAutoResponseResult(
        success=True,
        steps=steps,
        routing_decision=routing,
        ticket_id=ticket_id,
        response_sent=response_sent,
        ai_response=ai_response_text,
        confidence=effective_confidence,
        sla_due_at=sla_due_at,
        sla_breached=sla_breached,
        total_cost_yen=total_cost,
        total_duration_ms=total_duration,
    )


# ─── 送信・通知ヘルパー（実装は各コネクタ完成後に差し替え） ────────────────

def _make_feedback_link(ticket_id: str | None) -> str:
    """顧客向けフィードバックリンクを生成する。"""
    if not ticket_id:
        return ""
    return f"https://app.shachotwo.jp/support/feedback?ticket={ticket_id}"


async def _send_response(
    company_id: str,
    channel: str,
    customer_id: str,
    response_text: str,
    ticket_id: str | None,
    feedback_link: str,
) -> None:
    """
    顧客へ回答を送信する。

    現在はログのみ（実装は workers/connector 完成後）。
    channel: "email" → SendGrid / "chat" → LINE WORKS / Slack
    """
    footer = f"\n\n---\nこの回答は役に立ちましたか？ {feedback_link}" if feedback_link else ""
    full_message = response_text + footer
    logger.info(
        f"[send_response] channel={channel} customer={customer_id} "
        f"ticket={ticket_id} chars={len(full_message)}"
    )
    # TODO: workers/connector/email.py または workers/connector/chat.py を呼び出す


async def _notify(channel: str, message: str) -> None:
    """通知送信。SLACK_WEBHOOK_URL 設定時はSlack送信、未設定時はログ出力。"""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(slack_url, json={"text": message, "channel": channel})
    else:
        logger.info(f"[通知][{channel}] {message}")


async def _push_to_review_queue(
    company_id: str,
    ticket_subject: str,
    draft_response: str,
    confidence: float,
    category: str,
    ticket_id: str | None,
) -> None:
    """AIドラフト + 人間レビューキューへの投入。#cs-review チャンネルへ通知。"""
    logger.info(
        f"[push_to_review_queue] ticket={ticket_id} subject={ticket_subject!r} "
        f"confidence={confidence:.2f} category={category}"
    )
    await _notify(
        "#cs-review",
        f"[レビュー依頼] ticket={ticket_id} subject={ticket_subject} "
        f"confidence={confidence:.2f} category={category}",
    )


async def _send_escalation_alert(
    company_id: str,
    ticket_subject: str,
    summary: str,
    priority: str,
    reason: str,
    department: str,
    routing: str,
) -> None:
    """エスカレーションアラート送信。urgent → #cs-urgent / 通常 → #cs-escalated"""
    channel = "#cs-urgent" if routing == "urgent_escalate" else "#cs-escalated"
    logger.info(
        f"[escalation_alert] channel={channel} dept={department} "
        f"priority={priority} reason={reason!r}"
    )
    await _notify(
        channel,
        f"[エスカレーション] subject={ticket_subject} priority={priority} "
        f"dept={department} reason={reason}",
    )


async def _send_billing_routing(
    company_id: str,
    ticket_subject: str,
    summary: str,
    customer_id: str,
) -> None:
    """billing カテゴリを経理チームへルーティング。#billing-support チャンネルへ通知。"""
    logger.info(
        f"[billing_routing] customer={customer_id} subject={ticket_subject!r}"
    )
    await _notify(
        "#billing-support",
        f"[経理ルーティング] customer={customer_id} subject={ticket_subject} "
        f"summary={summary}",
    )


def _map_routing_to_status(routing: str) -> str:
    """routing_decision を support_tickets.status に変換する。"""
    mapping = {
        "auto_send":       "ai_responded",
        "human_review":    "waiting",
        "escalate":        "escalated",
        "urgent_escalate": "escalated",
        "billing_routing": "escalated",
    }
    return mapping.get(routing, "open")
