"""
アップセル支援パイプライン⑦ — コンサルへのブリーフィング

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.7

概要:
  人間のCSコンサルタントが主役。AIはデータ収集・分析・タイミング検知・資料準備を行い、
  コンサルが「何を、いつ、どう提案すべきか」を判断できる状態を作る。

トリガー:
  - 日次ヘルススコア計算後
  - 利用マイルストーン到達（BPOコア利用率80%突破、Q&A週10回到達、等）

Steps:
  Step 1: saas_reader      Supabase経由で顧客利用データ収集
                           （機能利用率・Q&A回数・BPO実行数・契約情報）
  Step 2: rule_matcher     拡張タイミング判定（4パターン）
                           - BPOコア利用率≥80% + 未使用モジュール → 追加モジュール提案
                           - ブレインのみ + Q&A週10回以上      → BPOコアアップグレード提案
                           - health≥80 + 契約6ヶ月経過         → バックオフィスBPO提案
                           - 全BPO利用中 + カスタム要望3件以上  → 自社開発BPO提案
  Step 3: generator        コンサル用ブリーフィング生成
                           （顧客プロファイル・推奨アクション・見積シミュレーション）
  Step 4: message          Slack #sales-upsell 通知
  Step 5: calendar_booker  コンサルカレンダーに「提案準備」ブロック追加
                           + 顧客との商談候補日3枠提示
"""
import json
import os
import time
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.generator import run_document_generator
from workers.micro.calendar_booker import run_calendar_booker

try:
    from db.supabase import get_service_client
except ImportError:
    get_service_client = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ─── 定数 ──────────────────────────────────────────────────────────────────

CONFIDENCE_WARNING_THRESHOLD = 0.60

# 拡張タイミング判定しきい値
UPSELL_BPO_UTILIZATION_THRESHOLD = 0.80   # BPOコア利用率80%以上
UPSELL_QA_WEEKLY_THRESHOLD = 10           # Q&A週10回以上
UPSELL_HEALTH_SCORE_THRESHOLD = 80        # ヘルススコア80以上
UPSELL_CONTRACT_MONTHS_THRESHOLD = 6      # 契約6ヶ月経過
UPSELL_CUSTOM_REQUESTS_THRESHOLD = 3      # カスタム要望3件以上

# 料金定義（見積シミュレーション用）
PRICING = {
    "brain_only": 30_000,
    "bpo_core": 250_000,
    "additional_module": 100_000,
    "backoffice_bpo": 200_000,
    "custom_bpo": 0,  # 別途見積
}

# 利用可能なモジュール一覧（未使用モジュール判定に使用）
ALL_BPO_MODULES = {
    "estimation",        # 見積
    "safety_docs",       # 安全書類
    "billing",           # 請求
    "cost_report",       # 原価報告
    "permit",            # 許認可
    "photo_organize",    # 写真整理
    "subcontractor",     # 下請管理
    "construction_plan", # 工程管理
    "quoting",           # 製造見積
    "production_plan",   # 生産計画
    "receipt_check",     # レセプトチェック
}

SLACK_SALES_UPSELL_CHANNEL = "sales-upsell"

# ─── データクラス ───────────────────────────────────────────────────────────

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
class UpsellOpportunity:
    """検知されたアップセル機会"""
    trigger_type: str      # "add_module" | "bpo_upgrade" | "backoffice" | "custom_dev"
    title: str             # 提案タイトル（例: "追加モジュール提案タイミング"）
    reason: str            # 根拠（例: "BPOコア利用率85%、安全書類AIが未使用"）
    urgency: str           # "high" | "medium" | "low"
    estimated_mrr_increase: int   # 月次追加売上見込み（円）
    recommended_modules: list[str] = field(default_factory=list)


@dataclass
class UpsellBriefingPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    opportunities: list[UpsellOpportunity] = field(default_factory=list)
    # アップセル機会が1件もない場合はスキップ完了
    skipped_no_opportunity: bool = False

    def summary(self) -> str:
        if self.skipped_no_opportunity:
            return "-- アップセル支援パイプライン: 拡張タイミング未到達（スキップ）"
        lines = [
            f"{'OK' if self.success else 'NG'} アップセル支援パイプライン",
            f"  ステップ: {len(self.steps)}/5",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
            f"  機会件数: {len(self.opportunities)}件",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        for opp in self.opportunities:
            lines.append(
                f"  [{opp.urgency.upper()}] {opp.title} — +¥{opp.estimated_mrr_increase:,}/月"
            )
        return "\n".join(lines)


# ─── 内部ヘルパー ───────────────────────────────────────────────────────────

def _make_step(
    step_no: int,
    step_name: str,
    agent_name: str,
    out: MicroAgentOutput,
) -> StepResult:
    warn = None
    if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
        warn = f"confidence低 ({out.confidence:.2f})"
    return StepResult(
        step_no=step_no, step_name=step_name, agent_name=agent_name,
        success=out.success, result=out.result, confidence=out.confidence,
        cost_yen=out.cost_yen, duration_ms=out.duration_ms, warning=warn,
    )


def _fail(
    steps: list[StepResult],
    pipeline_start: int,
    step_name: str,
    opportunities: list[UpsellOpportunity],
) -> UpsellBriefingPipelineResult:
    return UpsellBriefingPipelineResult(
        success=False,
        steps=steps,
        final_output={},
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
        failed_step=step_name,
        opportunities=opportunities,
    )


def _evaluate_upsell_opportunities(
    usage: dict[str, Any],
    bpo_utilization_threshold: float = UPSELL_BPO_UTILIZATION_THRESHOLD,
    qa_weekly_threshold: float = UPSELL_QA_WEEKLY_THRESHOLD,
    health_score_threshold: float = UPSELL_HEALTH_SCORE_THRESHOLD,
    custom_requests_threshold: int = UPSELL_CUSTOM_REQUESTS_THRESHOLD,
) -> list[UpsellOpportunity]:
    """
    設計書 Section 4.7 の4パターンに従いアップセル機会を判定する。

    Args:
        usage: saas_readerで収集した顧客利用データ集約値
        bpo_utilization_threshold: パターン1の閾値（DB重みで動的調整）
        qa_weekly_threshold: パターン2の閾値（DB重みで動的調整）
        health_score_threshold: パターン3の閾値（DB重みで動的調整）
        custom_requests_threshold: パターン4の閾値（DB重みで動的調整）

    Returns:
        検知されたUpsellOpportunityリスト（空=拡張タイミング未到達）
    """
    opportunities: list[UpsellOpportunity] = []

    bpo_utilization: float = usage.get("bpo_utilization_rate", 0.0)
    active_modules: set[str] = set(usage.get("active_modules", []))
    has_bpo_core: bool = usage.get("has_bpo_core", False)
    has_brain_only: bool = usage.get("has_brain_only", False)
    qa_weekly_avg: float = usage.get("qa_weekly_avg", 0.0)
    health_score: float = usage.get("health_score", 0.0)
    contract_months: int = usage.get("contract_months", 0)
    custom_request_count: int = usage.get("custom_request_count", 0)
    has_all_bpo: bool = usage.get("has_all_bpo", False)

    # パターン1: BPOコア利用率≥閾値 + 未使用モジュール → 追加モジュール提案
    if has_bpo_core and bpo_utilization >= bpo_utilization_threshold:
        unused = sorted(ALL_BPO_MODULES - active_modules)
        if unused:
            # 最大3モジュールを推奨
            recommended = unused[:3]
            mrr_increase = len(recommended) * PRICING["additional_module"]
            opportunities.append(UpsellOpportunity(
                trigger_type="add_module",
                title="追加モジュール提案タイミング",
                reason=(
                    f"BPOコア利用率{bpo_utilization * 100:.0f}%到達。"
                    f"未使用モジュール{len(unused)}件あり（推奨: {', '.join(recommended)}）"
                ),
                urgency="high" if bpo_utilization >= 0.90 else "medium",
                estimated_mrr_increase=mrr_increase,
                recommended_modules=recommended,
            ))

    # パターン2: ブレインのみ契約 + Q&A週閾値回以上 → BPOコアアップグレード提案
    if has_brain_only and not has_bpo_core and qa_weekly_avg >= qa_weekly_threshold:
        mrr_increase = PRICING["bpo_core"]
        opportunities.append(UpsellOpportunity(
            trigger_type="bpo_upgrade",
            title="BPOコアアップグレード提案",
            reason=(
                f"ブレインのみ契約でQ&A週平均{qa_weekly_avg:.0f}回。"
                "業務自動化（BPOコア）の導入タイミング。"
            ),
            urgency="high" if qa_weekly_avg >= 20 else "medium",
            estimated_mrr_increase=mrr_increase,
            recommended_modules=["bpo_core"],
        ))

    # パターン3: health_score≥閾値 + 契約6ヶ月経過 → バックオフィスBPO提案
    if (
        health_score >= health_score_threshold
        and contract_months >= UPSELL_CONTRACT_MONTHS_THRESHOLD
        and not usage.get("has_backoffice_bpo", False)
    ):
        mrr_increase = PRICING["backoffice_bpo"]
        opportunities.append(UpsellOpportunity(
            trigger_type="backoffice",
            title="バックオフィスBPO提案",
            reason=(
                f"ヘルススコア{health_score:.0f}、契約{contract_months}ヶ月経過。"
                "経理・給与・勤怠の自動化（バックオフィスBPO）の導入タイミング。"
            ),
            urgency="medium",
            estimated_mrr_increase=mrr_increase,
            recommended_modules=["backoffice_bpo"],
        ))

    # パターン4: 全BPO利用中 + カスタム要望閾値件以上 → 自社開発BPO提案
    if has_all_bpo and custom_request_count >= custom_requests_threshold:
        opportunities.append(UpsellOpportunity(
            trigger_type="custom_dev",
            title="自社開発BPO提案（コンサル必須）",
            reason=(
                f"全BPOモジュール利用中。カスタム要望{custom_request_count}件蓄積。"
                "業種特化の自社開発BPO導入を提案する段階。"
            ),
            urgency="high",
            estimated_mrr_increase=0,  # 別途見積
            recommended_modules=["custom_bpo"],
        ))

    return opportunities


def _build_slack_message(
    customer_name: str,
    opportunities: list[UpsellOpportunity],
    briefing_url: str,
) -> str:
    """Slack #sales-upsell 向けメッセージを構築する。"""
    if not opportunities:
        return f"[{customer_name}] アップセル機会なし（force_run）\nブリーフィング: {briefing_url}"
    opp = opportunities[0]  # 最優先機会
    urgency_label = {"high": "緊急", "medium": "要対応", "low": "参考"}.get(opp.urgency, "")
    mrr_text = (
        f"+¥{opp.estimated_mrr_increase:,}/月"
        if opp.estimated_mrr_increase > 0
        else "別途見積"
    )
    lines = [
        f"[{urgency_label}] [{customer_name}] {opp.title}",
        opp.reason,
        f"想定MRR増: {mrr_text}",
    ]
    if len(opportunities) > 1:
        lines.append(f"（他{len(opportunities) - 1}件の機会あり）")
    lines.append(f"ブリーフィング: {briefing_url}")
    return "\n".join(lines)


# ─── パイプライン本体 ────────────────────────────────────────────────────────

async def run_upsell_briefing_pipeline(
    company_id: str,
    customer_company_id: str,
    input_data: dict[str, Any],
) -> UpsellBriefingPipelineResult:
    """
    アップセル支援パイプライン（コンサルへのブリーフィング）。

    Args:
        company_id:          シャチョツー自社テナントID（sales側）
        customer_company_id: 分析対象の顧客テナントID
        input_data:          追加コンテキスト
            customer_name    (str):  顧客会社名
            consultant_email (str, optional): コンサル担当者メール（カレンダー登録用）
            briefing_base_url (str, optional): ブリーフィングURLのベース（デフォルト空文字）
            force_run        (bool, optional): 機会未検知でも強制実行（テスト用）

    Returns:
        UpsellBriefingPipelineResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    opportunities: list[UpsellOpportunity] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "customer_company_id": customer_company_id,
        "customer_name": input_data.get("customer_name", "顧客企業"),
        "today": date.today().isoformat(),
    }
    customer_name: str = context["customer_name"]
    briefing_base_url: str = input_data.get("briefing_base_url", "")
    force_run: bool = input_data.get("force_run", False)

    # ─── Step 1: saas_reader — 顧客利用データ収集 ──────────────────────────
    # Supabase から顧客の利用統計・契約情報を収集する。
    # 実運用では execution_logs / qa_sessions / tool_connections / companies を参照。
    s1_start = int(time.time() * 1000)
    try:
        # 1-a: 実行ログ（BPO実行数・モジュール別利用率）
        exec_log_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "get_execution_logs",
                "params": {
                    "table": "execution_logs",
                    "select": "task_type, status, created_at",
                    "limit": 500,
                },
            },
            context={"target_company_id": customer_company_id},
        ))

        # 1-b: Q&Aセッション数（過去30日）
        qa_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "get_qa_sessions",
                "params": {
                    "table": "knowledge_sessions",
                    "select": "id, session_type, created_at",
                    "limit": 200,
                },
            },
            context={"target_company_id": customer_company_id},
        ))

        # 1-c: カスタム要望（proactive_proposals テーブル）
        proposals_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "get_custom_requests",
                "params": {
                    "table": "proactive_proposals",
                    "select": "id, proposal_type, status, created_at",
                    "limit": 100,
                },
            },
            context={"target_company_id": customer_company_id},
        ))

        # いずれかのsaas_readerが失敗した場合は例外を発生させてStep1失敗扱いにする
        for sub_out in (exec_log_out, qa_out, proposals_out):
            if not sub_out.success:
                raise RuntimeError(sub_out.result.get("error", "saas_reader failed"))

        # 全サブ読み取りの中で最低の confidence を採用
        min_confidence = min(
            exec_log_out.confidence,
            qa_out.confidence,
            proposals_out.confidence,
        )

        # 利用データを集約（モックデータ or 実データを正規化）
        exec_logs: list[dict] = exec_log_out.result.get("data", [])
        qa_sessions: list[dict] = qa_out.result.get("data", [])
        custom_requests: list[dict] = proposals_out.result.get("data", [])

        # 実行されたBPOタスク種別一覧
        active_modules: set[str] = {
            log.get("task_type", "").replace("_pipeline", "")
            for log in exec_logs
            if log.get("status") == "completed"
        }

        # BPOコア利用率: input_dataで明示された場合はそれを優先、なければ実行ログから計算
        if "bpo_utilization_rate" in input_data:
            bpo_utilization_rate = float(input_data["bpo_utilization_rate"])
        else:
            bpo_utilization_rate = (
                len(active_modules & ALL_BPO_MODULES) / len(ALL_BPO_MODULES)
                if ALL_BPO_MODULES else 0.0
            )

        # Q&A週平均: input_dataで明示された場合はそれを優先、なければセッション数から計算
        if "qa_weekly_avg" in input_data:
            qa_weekly_avg = float(input_data["qa_weekly_avg"])
        else:
            qa_weekly_avg = len(qa_sessions) / 4.0

        # 契約経過月数はinput_dataから取得（実運用ではcompaniesテーブル参照）
        contract_months: int = input_data.get("contract_months", 0)

        # ヘルススコアはinput_dataから取得（実運用ではhealth_score計算済み値を使用）
        health_score: float = float(input_data.get("health_score", 0.0))

        # カスタム要望件数
        custom_request_count = len([
            r for r in custom_requests
            if r.get("proposal_type") == "custom_request"
        ])

        # 契約内容フラグはinput_dataから（実運用ではsubscriptionsテーブル参照）
        has_bpo_core: bool = input_data.get("has_bpo_core", len(active_modules) > 0)
        has_brain_only: bool = input_data.get("has_brain_only", not has_bpo_core)
        has_all_bpo: bool = input_data.get("has_all_bpo", ALL_BPO_MODULES <= active_modules)
        has_backoffice_bpo: bool = input_data.get("has_backoffice_bpo", False)

        usage_summary: dict[str, Any] = {
            "bpo_utilization_rate": bpo_utilization_rate,
            "active_modules": sorted(active_modules),
            "qa_weekly_avg": qa_weekly_avg,
            "health_score": health_score,
            "contract_months": contract_months,
            "custom_request_count": custom_request_count,
            "has_bpo_core": has_bpo_core,
            "has_brain_only": has_brain_only,
            "has_all_bpo": has_all_bpo,
            "has_backoffice_bpo": has_backoffice_bpo,
            "exec_log_count": len(exec_logs),
            "qa_session_count": len(qa_sessions),
        }

        s1_out = MicroAgentOutput(
            agent_name="saas_reader",
            success=True,
            result=usage_summary,
            confidence=min_confidence,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    except Exception as e:
        logger.error(f"upsell_briefing Step1 error: {e}")
        s1_out = MicroAgentOutput(
            agent_name="saas_reader",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    steps.append(_make_step(1, "saas_reader", "saas_reader", s1_out))
    if not s1_out.success:
        return _fail(steps, pipeline_start, "saas_reader", opportunities)

    context["usage"] = s1_out.result
    usage = s1_out.result

    # ─── Step 2: rule_matcher — 拡張タイミング判定 ─────────────────────────
    # knowledge_itemsからアップセルルールを参照しつつ、
    # 内部ロジック(_evaluate_upsell_opportunities)で4パターンを確定判定する。
    s2_start = int(time.time() * 1000)

    # アップセル検知の閾値をDB学習済み重みで調整
    upsell_weights: dict[str, float] = {}
    try:
        _db = get_service_client()
        _model_result = (
            _db.table("scoring_model_versions")
            .select("weights")
            .eq("company_id", company_id)
            .eq("model_type", "upsell_scoring")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if _model_result.data:
            upsell_weights = _model_result.data[0].get("weights", {})
            logger.info(f"upsell_scoring weights loaded: {upsell_weights}")
    except Exception as _e:
        logger.warning(f"upsell weights fetch failed (using defaults): {_e}")

    # 重みによる閾値調整（正の重み→閾値下げ＝検知しやすい、負の重み→閾値上げ＝検知しにくい）
    # 各パターンの閾値を上書きして usage_with_thresholds に格納し、判定に反映する
    _additional_module_threshold = max(
        0.5, UPSELL_BPO_UTILIZATION_THRESHOLD - upsell_weights.get("additional_module", 0) * 0.01
    )
    _upgrade_to_bpo_threshold = max(
        5.0, UPSELL_QA_WEEKLY_THRESHOLD - upsell_weights.get("upgrade_to_bpo", 0) * 0.1
    )
    _backoffice_bpo_health_threshold = max(
        60.0, UPSELL_HEALTH_SCORE_THRESHOLD - upsell_weights.get("backoffice_bpo", 0) * 0.5
    )
    _custom_bpo_requests_threshold = max(
        1, int(UPSELL_CUSTOM_REQUESTS_THRESHOLD - upsell_weights.get("custom_bpo", 0) * 0.05)
    )

    try:
        rule_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "extracted_data": usage,
                "domain": "upsell_timing",
                "category": "expansion",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning(f"rule_matcher failed, continuing with internal logic: {e}")
        rule_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={"matched_rules": [], "applied_values": usage, "unmatched_fields": []},
            confidence=0.5,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    # knowledge_itemsのルール照合結果をusageにマージ（上書き優先）
    applied = rule_out.result.get("applied_values", {})
    merged_usage = {**usage, **{k: v for k, v in applied.items() if k in usage}}

    # 4パターン判定（設計書Section 4.7 の主ロジック）。学習済み閾値を適用して判定。
    opportunities = _evaluate_upsell_opportunities(
        merged_usage,
        bpo_utilization_threshold=_additional_module_threshold,
        qa_weekly_threshold=_upgrade_to_bpo_threshold,
        health_score_threshold=_backoffice_bpo_health_threshold,
        custom_requests_threshold=_custom_bpo_requests_threshold,
    )
    rule_out_with_opportunities = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={
            **rule_out.result,
            "opportunities": [
                {
                    "trigger_type": o.trigger_type,
                    "title": o.title,
                    "reason": o.reason,
                    "urgency": o.urgency,
                    "estimated_mrr_increase": o.estimated_mrr_increase,
                    "recommended_modules": o.recommended_modules,
                }
                for o in opportunities
            ],
            "opportunity_count": len(opportunities),
        },
        confidence=rule_out.confidence,
        cost_yen=rule_out.cost_yen,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    steps.append(_make_step(2, "rule_matcher", "rule_matcher", rule_out_with_opportunities))

    # 機会なし、かつforce_runでなければここでスキップ完了
    if not opportunities and not force_run:
        logger.info(
            f"upsell_briefing: no opportunity detected for {customer_name} "
            f"({customer_company_id}), skipping"
        )
        return UpsellBriefingPipelineResult(
            success=True,
            steps=steps,
            final_output={"customer_company_id": customer_company_id, "opportunities": []},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            opportunities=[],
            skipped_no_opportunity=True,
        )

    context["opportunities"] = opportunities
    context["opportunity_count"] = len(opportunities)

    # ─── Step 3: generator — コンサル用ブリーフィング生成 ──────────────────
    # 顧客プロファイル・現在の利用状況・推奨アクション・見積シミュレーションを含む
    # Markdown形式のブリーフィングドキュメントを生成する。
    primary_opp = opportunities[0] if opportunities else None
    briefing_data: dict[str, Any] = {
        "customer_name": customer_name,
        "customer_company_id": customer_company_id,
        "as_of_date": context["today"],
        "usage_summary": {
            "bpo_utilization_rate_pct": f"{usage.get('bpo_utilization_rate', 0) * 100:.0f}%",
            "active_modules": usage.get("active_modules", []),
            "qa_weekly_avg": usage.get("qa_weekly_avg", 0),
            "health_score": usage.get("health_score", 0),
            "contract_months": usage.get("contract_months", 0),
            "custom_request_count": usage.get("custom_request_count", 0),
        },
        "opportunities": [
            {
                "trigger_type": o.trigger_type,
                "title": o.title,
                "reason": o.reason,
                "urgency": o.urgency,
                "estimated_mrr_increase": o.estimated_mrr_increase,
                "recommended_modules": o.recommended_modules,
            }
            for o in opportunities
        ],
        "estimate_simulation": {
            "current_mrr": input_data.get("current_mrr", 0),
            "potential_mrr_increase": sum(o.estimated_mrr_increase for o in opportunities),
            "potential_total_mrr": (
                input_data.get("current_mrr", 0)
                + sum(o.estimated_mrr_increase for o in opportunities)
            ),
        },
        "consultant_notes": input_data.get("consultant_notes", ""),
    }

    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template_name": "upsell_briefing",
            "data": briefing_data,
            "format": "markdown",
        },
        context=context,
    ))
    steps.append(_make_step(3, "generator", "document_generator", gen_out))
    if not gen_out.success:
        return _fail(steps, pipeline_start, "generator", opportunities)

    briefing_content: str = gen_out.result.get("content", "")
    context["briefing_content"] = briefing_content

    # ─── Step 4: message — Slack #sales-upsell 通知 ────────────────────────
    # Slackへの実送信は workers/connector/slack を使用する。
    # connectorが未設定の場合はメッセージ本文をresultに格納してsuccess=Trueで返す。
    briefing_url = (
        f"{briefing_base_url}/upsell/{customer_company_id}"
        if briefing_base_url
        else f"/upsell/{customer_company_id}"
    )
    slack_message = _build_slack_message(customer_name, opportunities, briefing_url)

    s4_start = int(time.time() * 1000)
    slack_sent = False
    try:
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(slack_url, json={
                    "text": slack_message,
                    "channel": f"#{SLACK_SALES_UPSELL_CHANNEL}",
                })
            slack_sent = True
        else:
            logger.info(f"[通知][#{SLACK_SALES_UPSELL_CHANNEL}] {slack_message}")
            slack_sent = False

        s4_out = MicroAgentOutput(
            agent_name="message",
            success=True,
            result={
                "channel": SLACK_SALES_UPSELL_CHANNEL,
                "message": slack_message,
                "sent": slack_sent,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        logger.warning(f"upsell_briefing 通知失敗 (non-critical): {e}")
        s4_out = MicroAgentOutput(
            agent_name="message",
            success=True,  # 通知失敗はノンクリティカル
            result={
                "channel": SLACK_SALES_UPSELL_CHANNEL,
                "message": slack_message,
                "sent": False,
                "error": str(e),
            },
            confidence=0.8,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    steps.append(_make_step(4, "message", "message", s4_out))

    # ─── Step 5: calendar_booker — コンサルカレンダーにブロック追加 ─────────
    # 1. 「提案準備」ブロック（翌営業日AM）をコンサルカレンダーに作成
    # 2. 顧客との商談候補日を3枠取得してresultに格納
    # Google Credentials未設定の場合はモックスロットを返す。
    s5_start = int(time.time() * 1000)
    consultant_email: str = input_data.get("consultant_email", "")

    try:
        # 5-a: 商談候補枠を3枠取得
        slots_out = await run_calendar_booker(MicroAgentInput(
            company_id=company_id,
            agent_name="calendar_booker",
            payload={"action": "get_slots", "days_ahead": 7},
            context=context,
        ))
        candidate_slots: list[dict] = slots_out.result.get("slots", [])[:3]

        # 5-b: 「提案準備」ブロックを最初の空き枠に作成
        prep_block_created = False
        prep_event: dict[str, Any] = {}
        if candidate_slots:
            prep_slot = candidate_slots[0]
            prep_title = f"[提案準備] {customer_name} アップセルブリーフィング確認"
            try:
                block_out = await run_calendar_booker(MicroAgentInput(
                    company_id=company_id,
                    agent_name="calendar_booker",
                    payload={
                        "action": "create_meeting",
                        "slot": prep_slot,
                        "company_name": customer_name,
                        "contact_name": "CSコンサルタント",
                        "attendee_email": consultant_email,
                    },
                    context=context,
                ))
                if block_out.success:
                    prep_event = block_out.result.get("meeting", {})
                    prep_block_created = True
                    prep_event["title"] = prep_title
            except Exception as e:
                logger.warning(f"calendar create_meeting failed: {e}")

        s5_out = MicroAgentOutput(
            agent_name="calendar_booker",
            success=True,
            result={
                "prep_block_created": prep_block_created,
                "prep_event": prep_event,
                "candidate_slots": candidate_slots,
                "slot_count": len(candidate_slots),
            },
            confidence=1.0 if prep_block_created else 0.7,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    except Exception as e:
        logger.warning(f"upsell_briefing calendar_booker failed (non-critical): {e}")
        s5_out = MicroAgentOutput(
            agent_name="calendar_booker",
            success=True,  # カレンダー失敗はノンクリティカル
            result={
                "prep_block_created": False,
                "prep_event": {},
                "candidate_slots": [],
                "slot_count": 0,
                "error": str(e),
            },
            confidence=0.5,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    steps.append(_make_step(5, "calendar_booker", "calendar_booker", s5_out))

    # ─── 完了 ────────────────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration_ms = int(time.time() * 1000) - pipeline_start

    # フィードバック時に参照する一意ID（後続の record_upsell_outcome で使用）
    upsell_opportunity_id: str = str(uuid4())

    final_output: dict[str, Any] = {
        "upsell_opportunity_id": upsell_opportunity_id,
        "customer_company_id": customer_company_id,
        "customer_name": customer_name,
        "opportunities": [
            {
                "trigger_type": o.trigger_type,
                "title": o.title,
                "reason": o.reason,
                "urgency": o.urgency,
                "estimated_mrr_increase": o.estimated_mrr_increase,
                "recommended_modules": o.recommended_modules,
            }
            for o in opportunities
        ],
        "briefing_content": briefing_content,
        "briefing_url": briefing_url,
        "slack_message": slack_message,
        "candidate_slots": s5_out.result.get("candidate_slots", []),
        "prep_block_created": s5_out.result.get("prep_block_created", False),
        "total_estimated_mrr_increase": sum(o.estimated_mrr_increase for o in opportunities),
        "as_of_date": context["today"],
    }

    logger.info(
        f"upsell_briefing_pipeline complete: customer={customer_name}, "
        f"opportunities={len(opportunities)}, "
        f"mrr_increase=+{final_output['total_estimated_mrr_increase']:,}円, "
        f"{total_duration_ms}ms"
    )

    return UpsellBriefingPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration_ms,
        opportunities=opportunities,
    )


# ─── フィードバック記録 ──────────────────────────────────────────────────────

async def record_upsell_outcome(
    company_id: str,
    customer_id: str,
    opportunity_type: str,  # "additional_module" | "upgrade_to_bpo" | "backoffice_bpo" | "custom_bpo"
    outcome: str,            # "accepted" | "rejected" | "deferred"
    recommended_modules: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    """アップセル提案の結果を記録し、検知ルールの重みを調整する。

    accepted → 同パターンの閾値を緩和（検知しやすく）
    rejected → 同パターンの閾値を厳格化（検知しにくく）
    deferred → 変更なし（再提案タイミングを記録のみ）
    """
    try:
        db = get_service_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. execution_logsに結果を記録
        db.table("execution_logs").insert({
            "company_id": company_id,
            "pipeline": "upsell_briefing_pipeline",
            "step": "outcome_feedback",
            "status": "completed",
            "payload": {
                "customer_id": customer_id,
                "opportunity_type": opportunity_type,
                "outcome": outcome,
                "recommended_modules": recommended_modules,
                "reason": reason,
            },
            "executed_at": now_iso,
        }).execute()

        # 2. scoring_model_versionsのupsell_scoring重みを更新
        model_result = (
            db.table("scoring_model_versions")
            .select("id, weights, version")
            .eq("company_id", company_id)
            .eq("model_type", "upsell_scoring")
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        weights: dict[str, float] = {}
        old_version = 0
        if model_result.data:
            weights = model_result.data[0].get("weights", {})
            old_version = model_result.data[0].get("version", 0)
            # 旧バージョンを非アクティブ化
            db.table("scoring_model_versions").update({"is_active": False}).eq(
                "id", model_result.data[0]["id"]
            ).execute()

        # 重み調整
        pattern_key = opportunity_type
        current_weight: float = float(weights.get(pattern_key, 0))

        if outcome == "accepted":
            weights[pattern_key] = current_weight + 5  # 成功 → +5（検知しやすく）
        elif outcome == "rejected":
            weights[pattern_key] = current_weight - 3  # 失敗 → -3（検知しにくく）
        # deferred は変更なし

        # 新バージョンを記録
        db.table("scoring_model_versions").insert({
            "company_id": company_id,
            "model_type": "upsell_scoring",
            "version": old_version + 1,
            "weights": weights,
            "is_active": True,
            "created_at": now_iso,
        }).execute()

        logger.info(
            f"upsell outcome recorded: {opportunity_type}={outcome} customer={customer_id}"
        )
        return {"success": True, "new_weights": weights}

    except Exception as e:
        logger.error(f"upsell outcome recording failed: {e}")
        return {"success": False, "error": str(e)}
