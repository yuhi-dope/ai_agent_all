"""
CRM パイプライン⑤ — 売上・要望管理

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.5

パイプラインは2モードで動作する:

  【売上管理モード】 mode="revenue"  トリガー: 毎月1日（ScheduleWatcher）
    Step 1: freee_data_fetcher   freee APIで請求・入金データ取得 → revenue_records 記録
    Step 2: mrr_calculator       MRR/ARR/NRR/チャーン率算出
    Step 3: report_generator     月次レポート生成 + Slack投稿

  【要望管理モード】 mode="request"  トリガー: 随時（チケット・Slack・メール着信）
    Step 4: request_extractor    サポートチケット・Slack・メールから要望を構造化抽出
    Step 5: priority_scorer      要望頻度 × 顧客MRR × 解約リスク → 優先スコア算出
    Step 6: response_drafter     顧客への回答メッセージ自動生成

  mode="both" を指定すると両モードを順に実行する。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from workers.micro.anomaly_detector import run_anomaly_detector
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.calculator import run_cost_calculator
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 優先スコア計算の重み
PRIORITY_WEIGHT_FREQUENCY = 0.35   # 要望頻度（vote_count）
PRIORITY_WEIGHT_MRR = 0.40         # 顧客MRR
PRIORITY_WEIGHT_CHURN_RISK = 0.25  # 解約リスク（health_score の逆数）

# 優先スコアのしきい値
PRIORITY_HIGH_THRESHOLD = 70
PRIORITY_MEDIUM_THRESHOLD = 40

# confidence 警告しきい値
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 要望カテゴリ
REQUEST_CATEGORIES = ["feature", "improvement", "integration", "bug"]

# 月次レポートSlack通知テンプレート名
MONTHLY_REPORT_TEMPLATE = "monthly_revenue_report"


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

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
class RevenueMetrics:
    """売上指標"""
    mrr: int                    # 今月のMRR（円）
    arr: int                    # ARR = MRR × 12
    new_mrr: int                # 新規MRR
    expansion_mrr: int          # 拡張MRR（アップセル）
    contraction_mrr: int        # 縮小MRR（ダウングレード）
    churned_mrr: int            # チャーンMRR
    nrr: float                  # Net Revenue Retention（%）
    churn_rate: float           # 月次チャーン率（%）
    active_customer_count: int
    period_year: int
    period_month: int


@dataclass
class RequestPriorityResult:
    """要望優先度判定結果"""
    request_id: str
    title: str
    category: str
    priority_score: float       # 0〜100
    priority_level: str         # high / medium / low
    vote_count: int
    customer_mrr: int
    churn_risk_score: float     # 0〜1（高いほどリスク大）
    ai_categories: list[str]
    similar_request_ids: list[str]


@dataclass
class RevenueRequestPipelineResult:
    """パイプライン最終結果"""
    success: bool
    mode: str                               # "revenue" | "request" | "both"
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    # 売上管理モードの出力
    revenue_metrics: RevenueMetrics | None = None
    report_content: str | None = None
    slack_posted: bool = False

    # 要望管理モードの出力
    request_priorities: list[RequestPriorityResult] = field(default_factory=list)
    response_drafts: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        mode_label = {
            "revenue": "売上管理",
            "request": "要望管理",
            "both": "売上・要望管理（両方）",
        }.get(self.mode, self.mode)
        status = "OK" if self.success else "FAILED"
        lines = [
            f"[{status}] CRM パイプライン⑤ — {mode_label}",
            f"  ステップ実行数: {len(self.steps)}",
            f"  総コスト: {self.total_cost_yen:.2f}円",
            f"  総処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        if self.revenue_metrics:
            m = self.revenue_metrics
            lines += [
                f"  MRR: {m.mrr:,}円 / ARR: {m.arr:,}円",
                f"  NRR: {m.nrr:.1f}% / チャーン率: {m.churn_rate:.2f}%",
                f"  Slackレポート投稿: {'済' if self.slack_posted else '未'}",
            ]
        if self.request_priorities:
            high = sum(1 for r in self.request_priorities if r.priority_level == "high")
            lines.append(
                f"  要望処理件数: {len(self.request_priorities)}件 (high優先度: {high}件)"
            )
        for s in self.steps:
            status_mark = "OK" if s.success else "NG"
            warn = f" [warn: {s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} [{status_mark}] {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------

async def run_revenue_request_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    mode: Literal["revenue", "request", "both"] = "both",
    period_year: int | None = None,
    period_month: int | None = None,
    freee_credentials: str | None = None,
    dry_run: bool = False,
) -> RevenueRequestPipelineResult:
    """
    CRM パイプライン⑤ — 売上・要望管理を実行する。

    Args:
        company_id:          テナントID
        input_data:          入力データ（モードにより異なる、詳細は各ステップ参照）
        mode:                実行モード。"revenue" / "request" / "both"
        period_year:         売上管理の対象年（省略時: 当月）
        period_month:        売上管理の対象月（省略時: 当月）
        freee_credentials:   freee API認証情報（encrypt_field() 済み）
        dry_run:             True の場合はDB書き込み・Slack投稿をスキップ

    input_data キー:
        【売上管理モード共通】
            customers (list[dict]): アクティブ顧客リスト。各要素に mrr, health_score,
                                    status ("active"/"churned"/"new"/"expansion" 等) を含む
                                    ※ 省略時は Supabase から自動取得

        【要望管理モード共通】
            requests (list[dict]): 要望テキストリスト。各要素に以下を含む
                - source_type: "ticket" | "slack" | "email"
                - text:        要望の生テキスト
                - customer_id: 顧客ID（UUID）
                - customer_mrr: 顧客の現在MRR（円）
                - health_score: 顧客のヘルススコア（0〜100）
                - request_id:  既存要望ID（類似グルーピング用。新規の場合は省略）
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "dry_run": dry_run,
    }

    today = date.today()
    target_year = period_year or today.year
    target_month = period_month or today.month
    context["period_year"] = target_year
    context["period_month"] = target_month

    revenue_metrics: RevenueMetrics | None = None
    report_content: str | None = None
    slack_posted = False
    request_priorities: list[RequestPriorityResult] = []
    response_drafts: list[dict[str, Any]] = []

    # ヘルパー: StepResult を生成してリストに追加
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

    # ヘルパー: 失敗結果を生成
    def _fail(step_name: str) -> RevenueRequestPipelineResult:
        return RevenueRequestPipelineResult(
            success=False, mode=mode, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # =========================================================================
    # 売上管理モード
    # =========================================================================

    if mode in ("revenue", "both"):

        # ─── Step 1: freee_data_fetcher ──────────────────────────────────
        s1_out = await _step1_freee_data_fetcher(
            company_id=company_id,
            input_data=input_data,
            context=context,
            freee_credentials=freee_credentials,
            target_year=target_year,
            target_month=target_month,
            dry_run=dry_run,
        )
        _add_step(1, "freee_data_fetcher", s1_out.agent_name, s1_out)
        if not s1_out.success:
            return _fail("freee_data_fetcher")
        context.update(s1_out.result)

        # ─── Step 2: mrr_calculator ──────────────────────────────────────
        s2_out = await _step2_mrr_calculator(
            company_id=company_id,
            context=context,
            target_year=target_year,
            target_month=target_month,
        )
        _add_step(2, "mrr_calculator", s2_out.agent_name, s2_out)
        if not s2_out.success:
            return _fail("mrr_calculator")
        revenue_metrics = _build_revenue_metrics(s2_out.result, target_year, target_month)
        context["revenue_metrics"] = s2_out.result

        # ─── Step 3: report_generator ────────────────────────────────────
        s3_out = await _step3_report_generator(
            company_id=company_id,
            context=context,
            revenue_metrics=revenue_metrics,
            dry_run=dry_run,
        )
        _add_step(3, "report_generator", s3_out.agent_name, s3_out)
        # レポート生成失敗はパイプライン全体を止めない（警告扱い）
        if s3_out.success:
            report_content = s3_out.result.get("content", "")
            slack_posted = s3_out.result.get("slack_posted", False)

    # =========================================================================
    # 要望管理モード
    # =========================================================================

    if mode in ("request", "both"):
        requests_input: list[dict[str, Any]] = input_data.get("requests", [])

        if not requests_input:
            logger.info("revenue_request_pipeline: requests が空のため要望管理ステップをスキップ")
        else:
            # ─── Step 4: request_extractor ───────────────────────────────
            step_no_base = 1 if mode == "request" else 3

            s4_out = await _step4_request_extractor(
                company_id=company_id,
                requests_input=requests_input,
                context=context,
            )
            _add_step(step_no_base + 1, "request_extractor", s4_out.agent_name, s4_out)
            if not s4_out.success:
                return _fail("request_extractor")
            context["structured_requests"] = s4_out.result.get("structured_requests", [])

            # ─── Step 5: priority_scorer ─────────────────────────────────
            s5_out = await _step5_priority_scorer(
                company_id=company_id,
                context=context,
            )
            _add_step(step_no_base + 2, "priority_scorer", s5_out.agent_name, s5_out)
            if not s5_out.success:
                return _fail("priority_scorer")
            request_priorities = _build_request_priorities(s5_out.result.get("prioritized", []))
            context["request_priorities"] = s5_out.result.get("prioritized", [])

            # ─── Step 6: response_drafter ─────────────────────────────────
            s6_out = await _step6_response_drafter(
                company_id=company_id,
                context=context,
                request_priorities=request_priorities,
                dry_run=dry_run,
            )
            _add_step(step_no_base + 3, "response_drafter", s6_out.agent_name, s6_out)
            # 回答生成失敗はパイプライン全体を止めない
            if s6_out.success:
                response_drafts = s6_out.result.get("response_drafts", [])

    # =========================================================================
    # anomaly_detector: MRR・顧客数等の前月比異常値検知
    # =========================================================================

    _mrr_anomaly_items: list[dict[str, Any]] = []
    _mrr_historical: dict[str, list] = {}
    if revenue_metrics:
        _mrr_anomaly_items = [
            {"name": "MRR", "value": revenue_metrics.mrr},
            {"name": "ARR", "value": revenue_metrics.arr},
            {"name": "新規MRR", "value": revenue_metrics.new_mrr},
            {"name": "チャーンMRR", "value": revenue_metrics.churned_mrr},
            {"name": "顧客数", "value": revenue_metrics.active_customer_count},
        ]
        # input_data に historical_mrr が含まれている場合はZ-scoreチェックに使用
        _hist_raw: dict = input_data.get("historical_mrr", {})
        if _hist_raw:
            _mrr_historical = _hist_raw

    _mrr_anomaly_warnings: list[dict] = []
    if _mrr_anomaly_items:
        try:
            _mrr_out = await run_anomaly_detector(MicroAgentInput(
                company_id=company_id,
                agent_name="anomaly_detector",
                payload={
                    "items": _mrr_anomaly_items,
                    "historical_values": _mrr_historical,
                    "detect_modes": ["digit_error", "zscore"],
                },
                context=context,
            ))
            if _mrr_out.success and _mrr_out.result.get("anomaly_count", 0) > 0:
                _mrr_anomaly_warnings = _mrr_out.result["anomalies"]
        except Exception as _ae:
            logger.warning(f"anomaly_detector (mrr) 非致命的エラー（スキップ）: {_ae}")

    # =========================================================================
    # 結果組み立て
    # =========================================================================

    final_output: dict[str, Any] = {}
    if revenue_metrics:
        final_output["revenue"] = {
            "mrr": revenue_metrics.mrr,
            "arr": revenue_metrics.arr,
            "nrr": revenue_metrics.nrr,
            "churn_rate": revenue_metrics.churn_rate,
            "new_mrr": revenue_metrics.new_mrr,
            "expansion_mrr": revenue_metrics.expansion_mrr,
            "contraction_mrr": revenue_metrics.contraction_mrr,
            "churned_mrr": revenue_metrics.churned_mrr,
            "active_customer_count": revenue_metrics.active_customer_count,
            "period": f"{target_year}-{target_month:02d}",
        }
    if request_priorities:
        final_output["requests"] = [
            {
                "request_id": r.request_id,
                "title": r.title,
                "priority_score": r.priority_score,
                "priority_level": r.priority_level,
                "category": r.category,
                "vote_count": r.vote_count,
            }
            for r in request_priorities
        ]

    if _mrr_anomaly_warnings:
        final_output["anomaly_warnings"] = _mrr_anomaly_warnings

    return RevenueRequestPipelineResult(
        success=True,
        mode=mode,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
        revenue_metrics=revenue_metrics,
        report_content=report_content,
        slack_posted=slack_posted,
        request_priorities=request_priorities,
        response_drafts=response_drafts,
    )


# ---------------------------------------------------------------------------
# Step 実装
# ---------------------------------------------------------------------------

async def _step1_freee_data_fetcher(
    company_id: str,
    input_data: dict[str, Any],
    context: dict[str, Any],
    freee_credentials: str | None,
    target_year: int,
    target_month: int,
    dry_run: bool,
) -> MicroAgentOutput:
    """
    Step 1: freee APIから請求・入金データを取得し、revenue_records に記録する。

    顧客リストが input_data["customers"] に直渡しされた場合はfreeeをスキップする。
    freee_credentials がない場合は Supabase から顧客データを取得する（モック扱い）。
    """
    start_ms = int(time.time() * 1000)
    agent_name = "freee_data_fetcher"

    # 直渡し形式: customers が既に渡されている場合はfreeeをスキップ
    if "customers" in input_data:
        customers: list[dict] = input_data["customers"]
        logger.info(f"freee_data_fetcher: 直渡し顧客データ {len(customers)}件を使用")
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"customers": customers, "freee_invoices": [], "source": "direct"},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    # freee経由でデータ取得
    if freee_credentials:
        freee_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "freee",
                "operation": "list_invoices",
                "resource": "invoices",
                "params": {
                    "year": target_year,
                    "month": target_month,
                    "status": "issued",
                },
                "encrypted_credentials": freee_credentials,
            },
            context=context,
        ))
        if not freee_out.success:
            logger.warning(f"freee_data_fetcher: freee取得失敗 ({freee_out.result.get('error')}). Supabaseにフォールバック")
    else:
        freee_out = None
        logger.info("freee_data_fetcher: freee_credentials なし。Supabaseから顧客データを取得")

    # Supabase から顧客テーブルを取得（フォールバック兼ねた補完）
    db_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "service": "supabase",
            "operation": "list_customers",
            "params": {
                "table": "customers",
                "select": "id, name, status, mrr, health_score, plan_type",
                "limit": 500,
            },
        },
        context=context,
    ))

    customers = db_out.result.get("data", []) if db_out.success else []
    freee_invoices = (freee_out.result.get("data", []) if freee_out and freee_out.success else [])

    # revenue_records に入金データを記録（dry_run でない場合）
    if freee_invoices and not dry_run:
        for inv in freee_invoices:
            await run_saas_writer(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_writer",
                payload={
                    "service": "supabase",
                    "operation": "record_revenue",
                    "approved": True,
                    "dry_run": False,
                    "params": {
                        "table": "revenue_records",
                        "action": "insert",
                        "data": {
                            "customer_id": inv.get("partner_id", ""),
                            "record_type": "mrr",
                            "amount": inv.get("amount", 0),
                            "effective_date": f"{target_year}-{target_month:02d}-01",
                            "freee_invoice_id": inv.get("id"),
                            "payment_status": inv.get("payment_status", "pending"),
                        },
                    },
                },
                context=context,
            ))

    confidence = 1.0 if not db_out.result.get("mock") else 0.6
    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={
            "customers": customers,
            "freee_invoices": freee_invoices,
            "source": "freee+supabase" if freee_invoices else "supabase",
        },
        confidence=confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


async def _step2_mrr_calculator(
    company_id: str,
    context: dict[str, Any],
    target_year: int,
    target_month: int,
) -> MicroAgentOutput:
    """
    Step 2: 顧客データから MRR/ARR/NRR/チャーン率を算出する。

    算出方式:
        MRR           = Σ(status="active" な顧客の mrr)
        new_mrr       = Σ(status="new" な顧客の mrr)
        expansion_mrr = Σ(status="expansion" な顧客の mrr差分)
        churned_mrr   = Σ(status="churned" な顧客の mrr)
        contraction_mrr = Σ(status="contraction" な顧客の mrr差分)
        ARR           = MRR × 12
        NRR           = (MRR + expansion_mrr - contraction_mrr - churned_mrr) / prior_mrr × 100
        churn_rate    = churned_mrr / (MRR + churned_mrr) × 100
    """
    start_ms = int(time.time() * 1000)
    agent_name = "mrr_calculator"

    customers: list[dict] = context.get("customers", [])
    if not customers:
        logger.warning("mrr_calculator: 顧客データが空です")
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={
                "mrr": 0, "arr": 0, "new_mrr": 0,
                "expansion_mrr": 0, "contraction_mrr": 0,
                "churned_mrr": 0, "nrr": 100.0, "churn_rate": 0.0,
                "active_customer_count": 0, "warning": "顧客データが空です",
            },
            confidence=0.5, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    # 顧客ステータス別にMRRを集計
    mrr_items = [
        {"category": c.get("status", "active"), "quantity": 1, "unit_price": c.get("mrr", 0)}
        for c in customers
    ]

    calc_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={"items": mrr_items},
        context=context,
    ))

    # ステータス別集計（Decimal使用で丸め誤差防止）
    def _sum_mrr(statuses: list[str]) -> int:
        return int(sum(
            Decimal(str(c.get("mrr", 0)))
            for c in customers
            if c.get("status", "active") in statuses
        ))

    active_mrr = _sum_mrr(["active", "expansion", "contraction"])
    new_mrr = _sum_mrr(["new"])
    expansion_mrr = _sum_mrr(["expansion"])
    contraction_mrr = _sum_mrr(["contraction"])
    churned_mrr = _sum_mrr(["churned"])

    total_mrr = active_mrr + new_mrr
    arr = total_mrr * 12
    active_count = sum(1 for c in customers if c.get("status", "active") not in ("churned",))

    # NRR: (MRR + 拡張 - 縮小 - チャーン) / 先月MRR × 100
    # 先月MRR = 今月MRR + churned - new - expansion_net（簡易近似）
    prior_mrr = total_mrr + churned_mrr - new_mrr
    if prior_mrr > 0:
        nrr = round(
            (total_mrr + expansion_mrr - contraction_mrr - churned_mrr) / prior_mrr * 100, 2
        )
    else:
        nrr = 100.0

    # チャーン率: チャーンMRR / (今月MRR + チャーンMRR)
    mrr_start = total_mrr + churned_mrr
    churn_rate = round(churned_mrr / mrr_start * 100, 3) if mrr_start > 0 else 0.0

    confidence = calc_out.confidence if calc_out.success else 0.7

    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={
            "mrr": total_mrr,
            "arr": arr,
            "new_mrr": new_mrr,
            "expansion_mrr": expansion_mrr,
            "contraction_mrr": contraction_mrr,
            "churned_mrr": churned_mrr,
            "nrr": nrr,
            "churn_rate": churn_rate,
            "active_customer_count": active_count,
        },
        confidence=confidence, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


async def _step3_report_generator(
    company_id: str,
    context: dict[str, Any],
    revenue_metrics: RevenueMetrics,
    dry_run: bool,
) -> MicroAgentOutput:
    """
    Step 3: 月次レポートを生成し、Slackに投稿する。

    生成物:
        - ダッシュボード用JSONデータ
        - Slack月次サマリーメッセージ
    """
    start_ms = int(time.time() * 1000)
    agent_name = "report_generator"

    m = revenue_metrics
    report_data = {
        "period": f"{m.period_year}年{m.period_month}月",
        "mrr": m.mrr,
        "arr": m.arr,
        "nrr": m.nrr,
        "churn_rate": m.churn_rate,
        "new_mrr": m.new_mrr,
        "expansion_mrr": m.expansion_mrr,
        "contraction_mrr": m.contraction_mrr,
        "churned_mrr": m.churned_mrr,
        "active_customer_count": m.active_customer_count,
        "generated_at": datetime.now().isoformat(),
    }

    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template_name": "monthly_report",
            "data": report_data,
            "format": "markdown",
        },
        context=context,
    ))

    if not gen_out.success:
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": gen_out.result.get("error", "レポート生成失敗")},
            confidence=0.0, cost_yen=gen_out.cost_yen,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    report_content = gen_out.result.get("content", "")

    # 通知（Slack未設定時はログ出力）
    slack_posted = False
    if not dry_run:
        slack_msg = _build_slack_summary(m, report_content)
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(slack_url, json={"text": slack_msg, "channel": "#revenue-report"})
                slack_posted = True
            except Exception as e:
                logger.warning(f"[revenue_request] Slack通知失敗 (non-fatal): {e}")
        else:
            logger.info(f"[通知][#revenue-report] {slack_msg}")
            slack_posted = True

    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={
            "content": report_content,
            "report_json": report_data,
            "slack_posted": slack_posted,
        },
        confidence=gen_out.confidence, cost_yen=gen_out.cost_yen,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


async def _step4_request_extractor(
    company_id: str,
    requests_input: list[dict[str, Any]],
    context: dict[str, Any],
) -> MicroAgentOutput:
    """
    Step 4: サポートチケット・Slack・メールから要望を構造化抽出する。

    入力テキストを LLM で構造化し、カテゴリ分類と類似要望グルーピングを行う。
    複数ソースの要望を一括処理する。
    """
    start_ms = int(time.time() * 1000)
    agent_name = "request_extractor"

    structured_requests: list[dict[str, Any]] = []
    total_cost = 0.0

    for req in requests_input:
        text = req.get("text", "")
        source_type = req.get("source_type", "unknown")
        customer_id = req.get("customer_id", "")
        customer_mrr = req.get("customer_mrr", 0)
        health_score = req.get("health_score", 50)
        request_id = req.get("request_id", "")

        if not text.strip():
            continue

        schema = {
            "title": "要望のタイトル（30字以内の端的な表現）",
            "description": "要望の詳細説明",
            "category": f"カテゴリ（{'/'.join(REQUEST_CATEGORIES)}）",
            "ai_categories": "AIによる詳細タグリスト（例: ['UI改善', 'CSV出力', 'レポート機能']）",
            "urgency": "緊急度（high/medium/low）",
        }

        ext_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id,
            agent_name="structured_extractor",
            payload={
                "text": text,
                "schema": schema,
                "domain": "crm_feature_request",
            },
            context=context,
        ))

        total_cost += ext_out.cost_yen

        extracted = ext_out.result.get("extracted", {}) if ext_out.success else {}
        structured_requests.append({
            "request_id": request_id,
            "source_type": source_type,
            "customer_id": customer_id,
            "customer_mrr": customer_mrr,
            "health_score": health_score,
            "raw_text": text,
            "title": extracted.get("title", text[:30]),
            "description": extracted.get("description", text),
            "category": extracted.get("category", "feature"),
            "ai_categories": extracted.get("ai_categories") or [],
            "urgency": extracted.get("urgency", "medium"),
            "extraction_confidence": ext_out.confidence,
        })

    confidence = (
        sum(r["extraction_confidence"] for r in structured_requests) / len(structured_requests)
        if structured_requests else 0.0
    )

    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={"structured_requests": structured_requests, "count": len(structured_requests)},
        confidence=round(confidence, 3), cost_yen=total_cost,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


async def _step5_priority_scorer(
    company_id: str,
    context: dict[str, Any],
) -> MicroAgentOutput:
    """
    Step 5: 優先度スコアを算出する。

    優先スコア = 要望頻度スコア × 0.35 + MRRスコア × 0.40 + 解約リスクスコア × 0.25

    各スコアの正規化方法:
        - 要望頻度スコア: vote_count を最大値で割って 0〜100 に正規化
        - MRRスコア: customer_mrr を最大MRRで割って 0〜100 に正規化
        - 解約リスクスコア: (100 - health_score) で 0〜100 に正規化（health低=リスク高）

    知識アイテムDB照合で優先度上書きルールも適用する。
    """
    start_ms = int(time.time() * 1000)
    agent_name = "priority_scorer"

    structured_requests: list[dict[str, Any]] = context.get("structured_requests", [])
    if not structured_requests:
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"prioritized": [], "count": 0},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )

    # 類似グルーピング: 同カテゴリ + タイトル類似性でvote_countを集計
    grouped = _group_similar_requests(structured_requests)

    # 正規化用最大値
    max_votes = max((g["vote_count"] for g in grouped), default=1)
    max_mrr = max((g["customer_mrr"] for g in grouped), default=1)

    prioritized: list[dict[str, Any]] = []
    for req in grouped:
        vote_score = (req["vote_count"] / max_votes) * 100 if max_votes > 0 else 0
        mrr_score = (req["customer_mrr"] / max_mrr) * 100 if max_mrr > 0 else 0
        churn_risk_score = 100 - req.get("health_score", 50)  # health低=リスク高

        priority_score = round(
            vote_score * PRIORITY_WEIGHT_FREQUENCY
            + mrr_score * PRIORITY_WEIGHT_MRR
            + churn_risk_score * PRIORITY_WEIGHT_CHURN_RISK,
            2,
        )

        if priority_score >= PRIORITY_HIGH_THRESHOLD:
            priority_level = "high"
        elif priority_score >= PRIORITY_MEDIUM_THRESHOLD:
            priority_level = "medium"
        else:
            priority_level = "low"

        prioritized.append({
            **req,
            "priority_score": priority_score,
            "priority_level": priority_level,
            "vote_score": round(vote_score, 2),
            "mrr_score": round(mrr_score, 2),
            "churn_risk_score": round(churn_risk_score, 2),
        })

    # 優先スコア降順でソート
    prioritized.sort(key=lambda x: x["priority_score"], reverse=True)

    # 知識アイテムDBの優先度上書きルールを照合
    rule_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "extracted_data": {req["title"]: req["priority_level"] for req in prioritized},
            "domain": "crm_request_priority",
            "category": "priority_override",
        },
        context=context,
    ))
    # ルール照合で上書きがあれば反映
    if rule_out.success:
        applied = rule_out.result.get("applied_values", {})
        for req in prioritized:
            if req["title"] in applied and applied[req["title"]] in ("high", "medium", "low", "critical"):
                req["priority_level"] = applied[req["title"]]

    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={"prioritized": prioritized, "count": len(prioritized)},
        confidence=0.9, cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


async def _step6_response_drafter(
    company_id: str,
    context: dict[str, Any],
    request_priorities: list[RequestPriorityResult],
    dry_run: bool,
) -> MicroAgentOutput:
    """
    Step 6: 顧客への回答メッセージを自動生成する。

    生成内容:
        - 「ご要望を承りました。検討状況をお知らせします」系の受付確認メッセージ
        - priority="high" の要望にはより丁寧な個別回答を生成
        - ステータス変更時の自動通知文（planned/done/declined）
    """
    start_ms = int(time.time() * 1000)
    agent_name = "response_drafter"

    response_drafts: list[dict[str, Any]] = []
    total_cost = 0.0

    for req in request_priorities:
        priority_label = {"high": "高優先度", "medium": "中優先度", "low": "低優先度"}.get(
            req.priority_level, req.priority_level
        )

        draft_data = {
            "request_title": req.title,
            "request_category": req.category,
            "priority_level": priority_label,
            "priority_score": req.priority_score,
            "vote_count": req.vote_count,
            "customer_mrr_yen": req.customer_mrr,
            "status": "reviewing",
            "response_type": "acknowledgment",  # acknowledgment / status_update / declined
        }

        gen_out = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template_name": "approval_request",
                "data": draft_data,
                "format": "text",
            },
            context=context,
        ))

        total_cost += gen_out.cost_yen

        response_drafts.append({
            "request_id": req.request_id,
            "title": req.title,
            "priority_level": req.priority_level,
            "response_text": gen_out.result.get("content", _default_response(req)) if gen_out.success else _default_response(req),
            "draft_confidence": gen_out.confidence if gen_out.success else 0.5,
        })

    return MicroAgentOutput(
        agent_name=agent_name, success=True,
        result={"response_drafts": response_drafts, "count": len(response_drafts)},
        confidence=0.85, cost_yen=total_cost,
        duration_ms=int(time.time() * 1000) - start_ms,
    )


# ---------------------------------------------------------------------------
# ヘルパー関数
# ---------------------------------------------------------------------------

def _build_revenue_metrics(result: dict[str, Any], year: int, month: int) -> RevenueMetrics:
    return RevenueMetrics(
        mrr=result.get("mrr", 0),
        arr=result.get("arr", 0),
        new_mrr=result.get("new_mrr", 0),
        expansion_mrr=result.get("expansion_mrr", 0),
        contraction_mrr=result.get("contraction_mrr", 0),
        churned_mrr=result.get("churned_mrr", 0),
        nrr=result.get("nrr", 100.0),
        churn_rate=result.get("churn_rate", 0.0),
        active_customer_count=result.get("active_customer_count", 0),
        period_year=year,
        period_month=month,
    )


def _build_request_priorities(prioritized: list[dict[str, Any]]) -> list[RequestPriorityResult]:
    results = []
    for p in prioritized:
        results.append(RequestPriorityResult(
            request_id=p.get("request_id", ""),
            title=p.get("title", ""),
            category=p.get("category", "feature"),
            priority_score=p.get("priority_score", 0.0),
            priority_level=p.get("priority_level", "low"),
            vote_count=p.get("vote_count", 1),
            customer_mrr=p.get("customer_mrr", 0),
            churn_risk_score=p.get("churn_risk_score", 50.0),
            ai_categories=p.get("ai_categories") or [],
            similar_request_ids=p.get("similar_request_ids") or [],
        ))
    return results


def _group_similar_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    同一カテゴリかつタイトルが類似（先頭20文字が一致）するリクエストをグルーピングし
    vote_count を集計する。既存request_idがあれば類似IDリストに追加する。
    """
    groups: dict[str, dict[str, Any]] = {}

    for req in requests:
        # グルーピングキー: category + タイトル先頭20文字
        key = f"{req.get('category', 'feature')}::{req.get('title', '')[:20]}"

        if key in groups:
            groups[key]["vote_count"] = groups[key].get("vote_count", 1) + 1
            # MRR は最大値（影響度の高い顧客を代表）
            groups[key]["customer_mrr"] = max(
                groups[key].get("customer_mrr", 0), req.get("customer_mrr", 0)
            )
            # ヘルススコアは最小値（最もリスクの高い顧客を代表）
            groups[key]["health_score"] = min(
                groups[key].get("health_score", 100), req.get("health_score", 50)
            )
            # 類似要望ID追加
            if req.get("request_id"):
                similar = groups[key].setdefault("similar_request_ids", [])
                if req["request_id"] not in similar:
                    similar.append(req["request_id"])
        else:
            groups[key] = {
                **req,
                "vote_count": req.get("vote_count", 1),
                "similar_request_ids": [],
            }

    return list(groups.values())


def _build_slack_summary(m: RevenueMetrics, report_content: str) -> str:
    """Slack投稿用のサマリーテキストを組み立てる。"""
    nrr_emoji = "up" if m.nrr >= 110 else ("right" if m.nrr >= 100 else "down")
    churn_emoji = "green_circle" if m.churn_rate <= 2 else ("yellow_circle" if m.churn_rate <= 5 else "red_circle")

    return (
        f"[{m.period_year}年{m.period_month}月] 月次売上レポート\n"
        f"\n"
        f"MRR: {m.mrr:,}円  /  ARR: {m.arr:,}円\n"
        f"NRR: {m.nrr:.1f}%  [{nrr_emoji}]\n"
        f"月次チャーン率: {m.churn_rate:.2f}%  [{churn_emoji}]\n"
        f"\n"
        f"内訳:\n"
        f"  新規MRR: +{m.new_mrr:,}円\n"
        f"  拡張MRR: +{m.expansion_mrr:,}円\n"
        f"  縮小MRR: -{m.contraction_mrr:,}円\n"
        f"  チャーンMRR: -{m.churned_mrr:,}円\n"
        f"  アクティブ顧客数: {m.active_customer_count}社\n"
    )


def _default_response(req: RequestPriorityResult) -> str:
    """LLM生成失敗時のフォールバック回答テンプレート。"""
    priority_msg = {
        "high": "優先度が高い要望として検討チームに共有しました。",
        "medium": "要望としてバックログに追加しました。",
        "low": "ご要望を承りました。今後の改善検討に活用します。",
    }.get(req.priority_level, "ご要望を承りました。")

    return (
        f"平素よりご利用いただきありがとうございます。\n\n"
        f"「{req.title}」についてのご要望を承りました。\n"
        f"{priority_msg}\n\n"
        f"検討状況に変更がございましたら、改めてご連絡いたします。\n"
        f"引き続きどうぞよろしくお願いいたします。"
    )
