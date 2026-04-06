"""
CS対応品質フィードバック学習パイプライン（パイプライン⑨）

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.9

トリガー: チケットクローズ時 / 月次バッチ

Steps:
  Step 1: saas_reader        クローズ済みチケット収集
                             （AI回答/人間回答比率、CSAT分布、エスカレーション理由）
  Step 2: extractor          パターン分析
                             ・CSAT>=4 のAI回答 → 「良い回答」good
                             ・CSAT<=2 のAI回答 → 「改善必要」needs_improvement
                             ・人間修正パターン抽出
  Step 3: knowledge_updater  knowledge/qa 自動更新
                             ・新FAQパターン追加（knowledge_items）
                             ・既存FAQ改善版で更新
                             ・cs_feedback テーブルに quality_label を記録
  Step 4: threshold_adjuster confidence閾値の自動調整
                             ・CSAT平均 < 4.0 → 0.85→0.90（慎重モード）
                             ・CSAT平均 >= 4.5 → 0.85→0.80（自動対応率UP）
                             ・scoring_model_versions に新バージョン記録
  Step 5: report_generator   月次レポート生成 + Slack投稿
                             ・AI対応率推移 / CSAT推移 / よくある質問TOP10
                             ・「今月のAI改善提案」
"""
import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator

logger = logging.getLogger(__name__)

# confidence 警告ライン
CONFIDENCE_WARNING_THRESHOLD = 0.70

# CSAT閾値設計書定義
CSAT_GOOD_THRESHOLD = 4       # CSAT >= 4 → good
CSAT_BAD_THRESHOLD = 2        # CSAT <= 2 → needs_improvement

# confidence閾値自動調整ロジック（設計書 Step 4）
CONFIDENCE_LOWER_BOUND = 0.80   # CSAT平均 >= 4.5 → 引き下げ（自動対応率UP）
CONFIDENCE_DEFAULT = 0.85       # ベースライン
CONFIDENCE_UPPER_BOUND = 0.90   # CSAT平均 < 4.0 → 引き上げ（慎重モード）

CSAT_AVG_RAISE_THRESHOLD = 4.0   # この未満 → 閾値引き上げ
CSAT_AVG_LOWER_THRESHOLD = 4.5   # この以上 → 閾値引き下げ


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
class CsFeedbackPipelineResult:
    """CS品質フィードバックパイプライン全体の実行結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'[OK]' if self.success else '[NG]'} CS品質フィードバックパイプライン",
            f"  ステップ: {len(self.steps)}/5",
            f"  コスト: {self.total_cost_yen:.2f}円",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "[OK]" if s.success else "[NG]"
            warn = f" [!]{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}, {s.cost_yen:.2f}円{warn}"
            )
        return "\n".join(lines)


def _make_step_result(
    step_no: int,
    step_name: str,
    agent_name: str,
    out: MicroAgentOutput,
) -> StepResult:
    """MicroAgentOutput から StepResult を生成する。"""
    warn: str | None = None
    if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
        warn = f"confidence低 ({out.confidence:.2f} < {CONFIDENCE_WARNING_THRESHOLD})"
    return StepResult(
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


# ── 内部ヘルパー ──────────────────────────────────────────────────────────────

def _classify_quality(csat_score: int | None, was_ai_response: bool) -> str | None:
    """
    CSATスコアとAI回答フラグからquality_labelを返す。
    人間回答のみのチケットはNone（分類不要）。
    """
    if not was_ai_response:
        return None
    if csat_score is None:
        return "no_rating"
    if csat_score >= CSAT_GOOD_THRESHOLD:
        return "good"
    if csat_score <= CSAT_BAD_THRESHOLD:
        return "needs_improvement"
    return "neutral"


def _compute_csat_stats(tickets: list[dict]) -> dict[str, Any]:
    """
    チケット一覧からCSAT統計を計算する。

    Returns:
        {
            total_count, ai_response_count, human_response_count,
            ai_ratio, csat_scores, csat_avg, csat_distribution,
            good_count, needs_improvement_count
        }
    """
    total = len(tickets)
    ai_count = 0
    human_count = 0
    csat_scores: list[float] = []
    good_count = 0
    needs_improvement_count = 0

    csat_dist: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "no_rating": 0}

    for t in tickets:
        is_ai = bool(t.get("ai_response"))
        csat = t.get("csat_score")

        if is_ai:
            ai_count += 1
        else:
            human_count += 1

        if csat is not None:
            try:
                score = int(csat)
                csat_scores.append(score)
                key = str(score) if 1 <= score <= 5 else "no_rating"
                csat_dist[key] = csat_dist.get(key, 0) + 1
            except (ValueError, TypeError):
                csat_dist["no_rating"] += 1
        else:
            csat_dist["no_rating"] += 1

        label = _classify_quality(csat, is_ai)
        if label == "good":
            good_count += 1
        elif label == "needs_improvement":
            needs_improvement_count += 1

    csat_avg = round(sum(csat_scores) / len(csat_scores), 2) if csat_scores else None
    ai_ratio = round(ai_count / total, 3) if total > 0 else 0.0

    return {
        "total_count": total,
        "ai_response_count": ai_count,
        "human_response_count": human_count,
        "ai_ratio": ai_ratio,
        "csat_avg": csat_avg,
        "csat_distribution": csat_dist,
        "good_count": good_count,
        "needs_improvement_count": needs_improvement_count,
    }


async def _update_knowledge_item(
    company_id: str,
    question: str,
    answer: str,
    quality_label: str,
    source_ticket_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    """
    knowledge_items テーブルに新しいFAQエントリを追加する。

    - good ラベル: そのまま高confidence（0.90）で追加
    - needs_improvement ラベル: low_confidence（0.50）でフラグ付き追加
    """
    confidence_score = 0.90 if quality_label == "good" else 0.50
    data = {
        "company_id": company_id,
        "type": "faq",
        "title": question[:100],
        "content": answer,
        "source": "cs_feedback_auto",
        "confidence": confidence_score,
        "metadata": {
            "quality_label": quality_label,
            "source_ticket_id": source_ticket_id,
            "auto_generated": True,
        },
    }
    writer_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "supabase",
            "operation": "upsert_knowledge_item",
            "params": {
                "table": "knowledge_items",
                "data": data,
            },
            "approved": True,
            "dry_run": dry_run,
        },
        context={},
    ))
    return writer_out.result


async def _record_cs_feedback(
    company_id: str,
    ticket: dict,
    quality_label: str,
    dry_run: bool,
) -> dict[str, Any]:
    """cs_feedback テーブルに quality_label を記録する。"""
    ticket_id = ticket.get("id", "unknown")
    data = {
        "company_id": company_id,
        "ticket_id": ticket_id,
        "ai_response": ticket.get("ai_response"),
        "human_correction": ticket.get("human_correction"),
        "csat_score": ticket.get("csat_score"),
        "was_escalated": ticket.get("was_escalated", False),
        "quality_label": quality_label,
        "improvement_applied": False,
    }
    writer_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "supabase",
            "operation": "insert_cs_feedback",
            "params": {
                "table": "cs_feedback",
                "data": data,
            },
            "approved": True,
            "dry_run": dry_run,
        },
        context={},
    ))
    return writer_out.result


async def _record_scoring_model_version(
    company_id: str,
    new_confidence_threshold: float,
    csat_avg: float | None,
    previous_threshold: float,
    dry_run: bool,
) -> dict[str, Any]:
    """scoring_model_versions テーブルに新バージョンを記録し、active=True にする。"""
    # まず現行アクティブバージョンを非アクティブ化（dry_run時はスキップ）
    if not dry_run:
        try:
            from db.supabase import get_service_client
            db = get_service_client()
            db.table("scoring_model_versions").update({"active": False}).eq(
                "company_id", company_id
            ).eq("model_type", "cs_confidence").eq("active", True).execute()
        except Exception as e:
            logger.warning(f"scoring_model_versions deactivate failed (non-fatal): {e}")

    data = {
        "company_id": company_id,
        "model_type": "cs_confidence",
        "version": int(time.time()),  # タイムスタンプをバージョンとして使用
        "weights": {
            "confidence_threshold": new_confidence_threshold,
        },
        "performance_metrics": {
            "csat_avg": csat_avg,
            "previous_threshold": previous_threshold,
            "adjustment_reason": (
                "csat_avg_below_4.0_raised" if new_confidence_threshold > previous_threshold
                else "csat_avg_above_4.5_lowered"
            ),
        },
        "active": True,
    }
    writer_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "supabase",
            "operation": "insert_scoring_model_version",
            "params": {
                "table": "scoring_model_versions",
                "data": data,
            },
            "approved": True,
            "dry_run": dry_run,
        },
        context={},
    ))
    return writer_out.result


async def _notify(channel: str, message: str) -> dict[str, Any]:
    """通知送信。SLACK_WEBHOOK_URL 設定時はSlack送信、未設定時はログ出力。"""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    if slack_url:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(slack_url, json={"text": message, "channel": channel})
        return {"success": True, "channel": channel, "method": "slack"}
    else:
        logger.info(f"[通知][{channel}] {message}")
        return {"success": True, "channel": channel, "method": "log"}


# ── メインパイプライン ─────────────────────────────────────────────────────────

async def run_cs_feedback_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> CsFeedbackPipelineResult:
    """
    CS対応品質フィードバック学習パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "period_days"         (int,  optional): 集計期間（日数）。デフォルト 30
            "min_tickets"         (int,  optional): 最低チケット数。デフォルト 10
            "encrypted_credentials" (str, optional): Intercom/Zendesk API認証情報
            "slack_channel"       (str,  optional): Slack投稿先チャンネル。デフォルト "#cs-report"
            "slack_credentials"   (str,  optional): Slack認証情報
            "dry_run"             (bool, optional): True → DBに書き込まない
            "current_confidence_threshold" (float, optional): 現在の閾値。デフォルト 0.85
        }

    Returns:
        CsFeedbackPipelineResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "period_days": input_data.get("period_days", 30),
        "min_tickets": input_data.get("min_tickets", 10),
        "slack_channel": input_data.get("slack_channel", "#cs-report"),
        "dry_run": input_data.get("dry_run", False),
        "current_confidence_threshold": input_data.get(
            "current_confidence_threshold", CONFIDENCE_DEFAULT
        ),
        "encrypted_credentials": input_data.get("encrypted_credentials"),
        "slack_credentials": input_data.get("slack_credentials"),
    }
    dry_run: bool = context["dry_run"]

    def _add_step(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        sr = _make_step_result(step_no, step_name, agent_name, out)
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> CsFeedbackPipelineResult:
        return CsFeedbackPipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ── Step 1: saas_reader — クローズ済みチケット収集 ───────────────────────
    logger.info(f"[cs_feedback] Step 1: saas_reader start (company={company_id})")
    s1_start = int(time.time() * 1000)
    try:
        s1_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "list_closed_tickets_with_feedback",
                "params": {
                    "table": "cs_feedback",
                    "select": (
                        "id,ticket_id,ai_response,human_correction,"
                        "csat_score,was_escalated,quality_label,created_at"
                    ),
                    "limit": 500,
                },
                **(
                    {"encrypted_credentials": context["encrypted_credentials"]}
                    if context["encrypted_credentials"]
                    else {}
                ),
            },
            context=context,
        ))
    except Exception as e:
        logger.error(f"[cs_feedback] Step 1 error: {e}")
        s1_out = MicroAgentOutput(
            agent_name="saas_reader",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    step1 = _add_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("saas_reader")

    tickets: list[dict] = s1_out.result.get("data", [])
    ticket_count = len(tickets)
    logger.info(f"[cs_feedback] Step 1 done: {ticket_count} tickets")

    if ticket_count < context["min_tickets"]:
        logger.info(
            f"[cs_feedback] チケット数不足 ({ticket_count} < {context['min_tickets']}), "
            "パイプラインを早期終了します"
        )
        return CsFeedbackPipelineResult(
            success=True,
            steps=steps,
            final_output={
                "skipped": True,
                "reason": f"チケット数不足 ({ticket_count}件 < 最低{context['min_tickets']}件)",
                "ticket_count": ticket_count,
            },
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
        )

    # CSAT統計を先行計算（Step 2以降で参照）
    csat_stats = _compute_csat_stats(tickets)
    context["tickets"] = tickets
    context["csat_stats"] = csat_stats

    # ── Step 2: extractor — パターン分析 ─────────────────────────────────────
    logger.info(f"[cs_feedback] Step 2: extractor start")
    s2_start = int(time.time() * 1000)

    # AI回答があるチケットのみ抽出して分析
    ai_tickets = [t for t in tickets if t.get("ai_response")]
    good_responses: list[dict] = []
    needs_improvement_responses: list[dict] = []
    human_correction_patterns: list[dict] = []

    for t in ai_tickets:
        csat = t.get("csat_score")
        label = _classify_quality(csat, was_ai_response=True)
        if label == "good":
            good_responses.append({
                "ticket_id": t.get("ticket_id"),
                "ai_response": t.get("ai_response"),
                "csat_score": csat,
            })
        elif label == "needs_improvement":
            needs_improvement_responses.append({
                "ticket_id": t.get("ticket_id"),
                "ai_response": t.get("ai_response"),
                "human_correction": t.get("human_correction"),
                "csat_score": csat,
            })
        # 人間が修正したパターン（human_correctionが存在する）
        if t.get("human_correction"):
            human_correction_patterns.append({
                "ticket_id": t.get("ticket_id"),
                "original_ai_response": t.get("ai_response"),
                "corrected_response": t.get("human_correction"),
                "csat_score": csat,
            })

    # LLMで改善パターンの要約・FAQパターン抽出
    extraction_text = json.dumps(
        {
            "good_responses_sample": good_responses[:5],
            "needs_improvement_sample": needs_improvement_responses[:5],
            "human_corrections_sample": human_correction_patterns[:5],
            "stats": csat_stats,
        },
        ensure_ascii=False,
    )

    try:
        s2_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id,
            agent_name="structured_extractor",
            payload={
                "text": extraction_text,
                "schema": {
                    "top_faq_patterns": (
                        "よくある質問パターンのリスト（最大10件）。"
                        "各要素は {question: str, answer: str, frequency: str} 形式"
                    ),
                    "common_failure_reasons": (
                        "AI回答が低評価になった主な理由のリスト（最大5件）。"
                        "各要素は {reason: str, example: str} 形式"
                    ),
                    "improvement_suggestions": (
                        "AI回答品質を向上させるための提案リスト（最大5件）。"
                        "各要素は str 形式"
                    ),
                    "escalation_patterns": (
                        "エスカレーションが発生しやすいトピックのリスト（最大5件）"
                    ),
                },
                "domain": "cs_quality_analysis",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning(f"[cs_feedback] Step 2 LLM error (non-fatal): {e}")
        s2_out = MicroAgentOutput(
            agent_name="structured_extractor",
            success=True,
            result={
                "extracted": {
                    "top_faq_patterns": [],
                    "common_failure_reasons": [],
                    "improvement_suggestions": [str(e)],
                    "escalation_patterns": [],
                },
                "missing_fields": [],
            },
            confidence=0.3,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    step2 = _add_step(2, "extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return _fail("extractor")

    extracted_patterns = s2_out.result.get("extracted", {})
    context["good_responses"] = good_responses
    context["needs_improvement_responses"] = needs_improvement_responses
    context["human_correction_patterns"] = human_correction_patterns
    context["extracted_patterns"] = extracted_patterns
    logger.info(
        f"[cs_feedback] Step 2 done: good={len(good_responses)}, "
        f"needs_improvement={len(needs_improvement_responses)}, "
        f"corrections={len(human_correction_patterns)}"
    )

    # ── Step 3: knowledge_updater — knowledge/qa 自動更新 ───────────────────
    logger.info(f"[cs_feedback] Step 3: knowledge_updater start")
    s3_start = int(time.time() * 1000)

    knowledge_added = 0
    knowledge_errors: list[str] = []
    cs_feedback_recorded = 0

    # (A) 高評価AI回答 → knowledge_items に good として追加
    for resp in good_responses[:10]:  # 上限10件
        ticket_id = str(resp.get("ticket_id", ""))
        ai_response = resp.get("ai_response", "")
        if not ai_response or len(ai_response) < 20:
            continue
        try:
            await _update_knowledge_item(
                company_id=company_id,
                question=f"[CS自動抽出] ticket:{ticket_id}",
                answer=ai_response,
                quality_label="good",
                source_ticket_id=ticket_id,
                dry_run=dry_run,
            )
            knowledge_added += 1
        except Exception as e:
            knowledge_errors.append(f"good knowledge add error: {e}")
            logger.warning(f"[cs_feedback] knowledge good add failed: {e}")

    # (B) 抽出FAQパターン → knowledge_items に追加
    for faq in extracted_patterns.get("top_faq_patterns", [])[:10]:
        question = faq.get("question", "")
        answer = faq.get("answer", "")
        if not question or not answer:
            continue
        try:
            await _update_knowledge_item(
                company_id=company_id,
                question=question,
                answer=answer,
                quality_label="good",
                source_ticket_id="auto_extracted",
                dry_run=dry_run,
            )
            knowledge_added += 1
        except Exception as e:
            knowledge_errors.append(f"faq add error: {e}")
            logger.warning(f"[cs_feedback] faq knowledge add failed: {e}")

    # (C) cs_feedback テーブルに quality_label を記録（未記録分のみ）
    for t in ai_tickets[:50]:  # バッチ上限50件
        csat = t.get("csat_score")
        label = _classify_quality(csat, was_ai_response=True)
        if label is None:
            continue
        # quality_label が未設定のものだけ記録
        if t.get("quality_label"):
            continue
        try:
            await _record_cs_feedback(
                company_id=company_id,
                ticket=t,
                quality_label=label,
                dry_run=dry_run,
            )
            cs_feedback_recorded += 1
        except Exception as e:
            knowledge_errors.append(f"cs_feedback record error: {e}")
            logger.warning(f"[cs_feedback] cs_feedback record failed: {e}")

    s3_out = MicroAgentOutput(
        agent_name="knowledge_updater",
        success=True,
        result={
            "knowledge_items_added": knowledge_added,
            "cs_feedback_recorded": cs_feedback_recorded,
            "errors": knowledge_errors,
            "dry_run": dry_run,
        },
        confidence=1.0 if not knowledge_errors else 0.8,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )

    step3 = _add_step(3, "knowledge_updater", "knowledge_updater", s3_out)
    context["knowledge_update_result"] = s3_out.result
    logger.info(
        f"[cs_feedback] Step 3 done: added={knowledge_added}, "
        f"feedback_recorded={cs_feedback_recorded}"
    )

    # ── Step 4: threshold_adjuster — confidence閾値の自動調整 ───────────────
    logger.info(f"[cs_feedback] Step 4: threshold_adjuster start")
    s4_start = int(time.time() * 1000)

    csat_avg: float | None = csat_stats.get("csat_avg")
    current_threshold: float = context["current_confidence_threshold"]
    new_threshold: float = current_threshold
    adjustment_action: str = "no_change"

    if csat_avg is not None:
        if csat_avg < CSAT_AVG_RAISE_THRESHOLD:
            # CSAT平均 < 4.0 → 閾値を引き上げ（AIは慎重に、人間対応を増やす）
            new_threshold = CONFIDENCE_UPPER_BOUND
            adjustment_action = (
                f"raised: CSAT平均{csat_avg:.2f} < {CSAT_AVG_RAISE_THRESHOLD:.1f} "
                f"→ 閾値 {current_threshold:.2f} → {new_threshold:.2f}"
            )
            logger.info(f"[cs_feedback] 閾値引き上げ: {current_threshold} → {new_threshold}")
        elif csat_avg >= CSAT_AVG_LOWER_THRESHOLD:
            # CSAT平均 >= 4.5 → 閾値を引き下げ（AI自動対応率UP）
            new_threshold = CONFIDENCE_LOWER_BOUND
            adjustment_action = (
                f"lowered: CSAT平均{csat_avg:.2f} >= {CSAT_AVG_LOWER_THRESHOLD:.1f} "
                f"→ 閾値 {current_threshold:.2f} → {new_threshold:.2f}"
            )
            logger.info(f"[cs_feedback] 閾値引き下げ: {current_threshold} → {new_threshold}")
        else:
            adjustment_action = (
                f"no_change: CSAT平均{csat_avg:.2f} "
                f"({CSAT_AVG_RAISE_THRESHOLD:.1f}〜{CSAT_AVG_LOWER_THRESHOLD:.1f}の範囲)"
            )

    # 閾値変更があった場合のみ scoring_model_versions に記録
    threshold_version_id: str | None = None
    if new_threshold != current_threshold:
        try:
            version_result = await _record_scoring_model_version(
                company_id=company_id,
                new_confidence_threshold=new_threshold,
                csat_avg=csat_avg,
                previous_threshold=current_threshold,
                dry_run=dry_run,
            )
            threshold_version_id = version_result.get("operation_id")
        except Exception as e:
            logger.warning(f"[cs_feedback] scoring_model_versions record failed: {e}")

    s4_out = MicroAgentOutput(
        agent_name="threshold_adjuster",
        success=True,
        result={
            "previous_threshold": current_threshold,
            "new_threshold": new_threshold,
            "adjustment_action": adjustment_action,
            "csat_avg": csat_avg,
            "version_id": threshold_version_id,
            "dry_run": dry_run,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )

    step4 = _add_step(4, "threshold_adjuster", "threshold_adjuster", s4_out)
    context["threshold_result"] = s4_out.result
    logger.info(f"[cs_feedback] Step 4 done: {adjustment_action}")

    # ── Step 5: report_generator — 月次レポート生成 + Slack投稿 ─────────────
    logger.info(f"[cs_feedback] Step 5: report_generator start")
    s5_start = int(time.time() * 1000)

    # レポートデータ組み立て
    top_faq_list = extracted_patterns.get("top_faq_patterns", [])
    improvement_suggestions = extracted_patterns.get("improvement_suggestions", [])
    failure_reasons = extracted_patterns.get("common_failure_reasons", [])

    report_data = {
        "period_days": context["period_days"],
        "total_tickets": csat_stats["total_count"],
        "ai_response_count": csat_stats["ai_response_count"],
        "human_response_count": csat_stats["human_response_count"],
        "ai_ratio_percent": round(csat_stats["ai_ratio"] * 100, 1),
        "csat_avg": csat_avg,
        "csat_distribution": csat_stats["csat_distribution"],
        "good_ai_responses": csat_stats["good_count"],
        "needs_improvement_ai_responses": csat_stats["needs_improvement_count"],
        "knowledge_items_added": knowledge_added,
        "confidence_threshold_before": current_threshold,
        "confidence_threshold_after": new_threshold,
        "threshold_adjustment": adjustment_action,
        "top_faq_patterns": top_faq_list[:10],
        "common_failure_reasons": failure_reasons[:5],
        "improvement_suggestions": improvement_suggestions[:5],
    }

    try:
        report_out = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template_name": "monthly_report",
                "data": report_data,
                "format": "markdown",
            },
            context=context,
        ))
        report_content: str = report_out.result.get("content", "")
        report_cost = report_out.cost_yen
    except Exception as e:
        logger.warning(f"[cs_feedback] report generation failed, using fallback: {e}")
        # フォールバックレポート
        dist = csat_stats["csat_distribution"]
        top_faqs_str = "\n".join(
            f"  {i+1}. {faq.get('question', '')}"
            for i, faq in enumerate(top_faq_list[:10])
        ) or "  (データなし)"
        suggestions_str = "\n".join(
            f"  - {s}" for s in improvement_suggestions[:5]
        ) or "  (データなし)"
        report_content = (
            f"# CS品質月次レポート（直近{context['period_days']}日間）\n\n"
            f"## AI対応概況\n"
            f"- 総チケット数: {csat_stats['total_count']}件\n"
            f"- AI対応率: {round(csat_stats['ai_ratio'] * 100, 1)}%"
            f"（AI:{csat_stats['ai_response_count']}件 / 人間:{csat_stats['human_response_count']}件）\n\n"
            f"## CSAT推移\n"
            f"- CSAT平均: {csat_avg if csat_avg else 'N/A'}\n"
            f"- 分布: 5点={dist.get('5',0)}件 / 4点={dist.get('4',0)}件 / "
            f"3点={dist.get('3',0)}件 / 2点={dist.get('2',0)}件 / 1点={dist.get('1',0)}件\n"
            f"- 高評価(CSAT>=4): {csat_stats['good_count']}件\n"
            f"- 要改善(CSAT<=2): {csat_stats['needs_improvement_count']}件\n\n"
            f"## よくある質問 TOP{min(10, len(top_faq_list))}\n{top_faqs_str}\n\n"
            f"## 今月のAI改善提案\n{suggestions_str}\n\n"
            f"## confidence閾値調整\n"
            f"- 変更前: {current_threshold:.2f} → 変更後: {new_threshold:.2f}\n"
            f"- 判定: {adjustment_action}\n\n"
            f"## ナレッジ更新\n"
            f"- 新規FAQエントリ追加: {knowledge_added}件\n"
        )
        report_cost = 0.0
        report_out = MicroAgentOutput(
            agent_name="document_generator",
            success=True,
            result={"content": report_content, "format": "markdown", "char_count": len(report_content)},
            confidence=0.7,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    # 通知（Slack未設定時はログ出力）
    slack_post_result: dict[str, Any] = {}
    slack_success = False
    try:
        slack_post_result = await _notify(
            channel=context["slack_channel"],
            message=report_content,
        )
        slack_success = slack_post_result.get("success", False) or dry_run
    except Exception as e:
        logger.warning(f"[cs_feedback] 通知失敗 (non-fatal): {e}")
        slack_post_result = {"error": str(e)}

    s5_out = MicroAgentOutput(
        agent_name="report_generator",
        success=True,
        result={
            "report_content": report_content,
            "char_count": len(report_content),
            "slack_channel": context["slack_channel"],
            "slack_posted": slack_success,
            "slack_result": slack_post_result,
            "dry_run": dry_run,
        },
        confidence=0.9,
        cost_yen=report_cost,
        duration_ms=int(time.time() * 1000) - s5_start,
    )

    step5 = _add_step(5, "report_generator", "report_generator", s5_out)
    context["report"] = s5_out.result
    logger.info(
        f"[cs_feedback] Step 5 done: "
        f"report={len(report_content)}文字, slack_posted={slack_success}"
    )

    # ── 最終結果 ─────────────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    final_output = {
        "period_days": context["period_days"],
        "ticket_count": ticket_count,
        "csat_stats": csat_stats,
        "good_responses_count": len(good_responses),
        "needs_improvement_count": len(needs_improvement_responses),
        "human_correction_count": len(human_correction_patterns),
        "knowledge_items_added": knowledge_added,
        "confidence_threshold": {
            "previous": current_threshold,
            "new": new_threshold,
            "action": adjustment_action,
        },
        "report_char_count": len(report_content),
        "slack_posted": slack_success,
        "dry_run": dry_run,
    }

    logger.info(
        f"[cs_feedback] pipeline complete: "
        f"tickets={ticket_count}, csat_avg={csat_avg}, "
        f"threshold={current_threshold}→{new_threshold}, "
        f"cost={total_cost_yen:.2f}円, {total_duration}ms"
    )

    return CsFeedbackPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
    )
