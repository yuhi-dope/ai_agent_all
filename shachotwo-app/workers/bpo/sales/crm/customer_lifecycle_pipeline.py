"""
CRM パイプライン④ — 顧客ライフサイクル管理

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.4

トリガー:
  - モード "onboarding" : 契約締結時（CloudSign Webhook等から呼び出し）
  - モード "health_check": 日次スケジュール（ScheduleWatcher / cron 毎朝9:00）

Steps（オンボーディング）:
  Step 1: account_setup      customers テーブルをステータス onboarding に更新 + ゲノム適用
  Step 2: welcome_email      ウェルカムメール送信（welcome_email.html テンプレート）
  Step 3: onboard_sequence   Day 1/3/7/14/30 フォローシーケンスをスケジュール登録

Steps（ヘルススコア計算・日次）:
  Step 4: usage_data         saas_reader → ログイン頻度 / BPO実行数 / Q&A回数 収集
  Step 5: health_calculator  5次元スコア計算（利用度30% / エンゲージメント25% /
                             サポート15% / NPS 15% / 拡張可能性15%）
  Step 6: alert_matcher      rule_matcher → スコア閾値判定
                               < 40: 解約リスクアラート → CS担当に Slack 緊急通知
                               40-60: 注意フラグ
                               > 80 + 未使用モジュール: 拡張提案
  Step 7: action_message     message → 必要なアクションを実行
                               解約リスク: CS担当アラート + 対策サジェスト
                               拡張提案: 追加モジュール提案メール生成
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator

logger = logging.getLogger(__name__)

# ─── 定数 ────────────────────────────────────────────────────────────────────

CONFIDENCE_WARNING_THRESHOLD = 0.70

# ヘルススコア次元の重み（合計 = 1.0）
HEALTH_WEIGHTS: dict[str, float] = {
    "usage":       0.30,   # 利用度（WAU / DAU）
    "engagement":  0.25,   # エンゲージメント（機能幅）
    "support":     0.15,   # サポート（チケット頻度 → 少ないほど高い）
    "nps":         0.15,   # NPS
    "expansion":   0.15,   # 拡張可能性（未使用モジュール比率）
}

# アラート閾値
RISK_THRESHOLD = 40
CAUTION_THRESHOLD = 60
EXPANSION_THRESHOLD = 80

# オンボードフォローシーケンス — プラン別（日数: {message, template, plan対象}）
# template が指定されている場合は HTML メールテンプレートを使用する
ONBOARD_SEQUENCE_SELF: dict[int, dict] = {
    1:  {"message": "初期設定の開始をご確認ください",
         "template": "welcome_email.html", "subject_suffix": "はじめかたのご案内"},
    2:  {"message": "御社のデータの安全性についてご説明します",
         "template": "onboard_security.html", "subject_suffix": "データの安全性について"},
    3:  {"message": "最初のナレッジを入れてみましょう",
         "template": "onboard_day3.html", "subject_suffix": "ナレッジ登録のご案内"},
    7:  {"message": "Q&Aを試してみましょう",
         "template": "onboard_day7.html", "subject_suffix": "Q&A体験のご案内"},
    14: {"message": "BPO機能を試しましょう",
         "template": "onboard_day14.html", "subject_suffix": "BPO機能のご案内"},
    30: {"message": "ご利用1ヶ月目となりました。NPS調査にご協力ください",
         "template": "onboard_day30.html", "subject_suffix": "1ヶ月のまとめ"},
}

# コンサル/フルサポートプラン追加メール（セルフの全内容 + 以下）
ONBOARD_SEQUENCE_CONSUL_EXTRA: dict[int, dict] = {
    2:  {"message": "初回ヒアリング（Meet 60分）のリマインダーです",
         "template": None, "subject_suffix": "初回ヒアリングのご案内"},
    5:  {"message": "初回ヒアリングの議事録を共有いたします",
         "template": None, "subject_suffix": "ヒアリング議事録の共有"},
    10: {"message": "ナレッジ投入サポート（Meet 30分）のリマインダーです",
         "template": None, "subject_suffix": "ナレッジ投入サポートのご案内"},
    17: {"message": "BPO実行トレーニング（Meet 30分）のリマインダーです",
         "template": None, "subject_suffix": "BPOトレーニングのご案内"},
    24: {"message": "自走確認ミーティング（Meet 30分）のリマインダーです",
         "template": None, "subject_suffix": "自走確認ミーティングのご案内"},
    45: {"message": "定着フォロー1回目（Meet 30分）のリマインダーです",
         "template": None, "subject_suffix": "定着フォロー1回目のご案内"},
    55: {"message": "定着フォロー2回目（Meet 30分）のリマインダーです",
         "template": None, "subject_suffix": "定着フォロー2回目のご案内"},
}

# 離脱リスク検知の閾値（日数: チェック条件）
CHURN_DETECTION_RULES: list[dict] = [
    {
        "day": 7,
        "condition": "knowledge_count_zero",
        "label": "Day 7 ナレッジ0件",
        "action": "slack_alert",
        "message": "離脱リスク: Day 7でナレッジ0件です。早期フォローが必要です。",
    },
    {
        "day": 14,
        "condition": "qa_count_zero",
        "label": "Day 14 Q&A 0回",
        "action": "phone_followup",
        "message": "離脱リスク: Day 14でQ&A未使用です。電話フォローアップを推奨します。",
    },
    {
        "day": 21,
        "condition": "bpo_count_zero",
        "label": "Day 21 BPO実行0回",
        "action": "upgrade_email",
        "message": "離脱リスク: Day 21でBPO未実行です。コンサルプランへのアップグレード提案を送信します。",
        "template": "churn_risk_alert.html",
    },
]

# 後方互換のためのレガシー定数（既存コードが参照している場合）
ONBOARD_SEQUENCE: dict[int, str] = {
    day: info["message"] for day, info in ONBOARD_SEQUENCE_SELF.items()
}

# ゲノムデータベースパス
_GENOME_BASE = Path(__file__).parents[4] / "brain" / "genome" / "data"


# ─── 結果モデル ──────────────────────────────────────────────────────────────

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
class CustomerLifecyclePipelineResult:
    """customer_lifecycle_pipeline の実行結果。"""
    success: bool
    mode: str                                       # "onboarding" | "health_check"
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    # ヘルスチェック専用
    health_score: int | None = None
    health_label: str | None = None                 # "risk" | "caution" | "healthy" | "expansion"
    action_required: bool = False

    def summary(self) -> str:
        mode_label = "オンボーディング" if self.mode == "onboarding" else "ヘルスチェック"
        ok = "OK" if self.success else "NG"
        lines = [
            f"[{ok}] 顧客ライフサイクルパイプライン ({mode_label})",
            f"  ステップ数: {len(self.steps)}",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.health_score is not None:
            lines.append(f"  ヘルススコア: {self.health_score} [{self.health_label}]")
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} [{status}] {s.step_name}: confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


# ─── パイプライン本体 ────────────────────────────────────────────────────────

async def run_customer_lifecycle_pipeline(
    company_id: str,
    customer_id: str,
    mode: str = "health_check",
    input_data: dict[str, Any] | None = None,
) -> CustomerLifecyclePipelineResult:
    """
    顧客ライフサイクル管理パイプライン。

    Args:
        company_id:  シャチョツー運営側のテナントID（自社 company_id）
        customer_id: customers テーブルの UUID
        mode:        "onboarding" または "health_check"
        input_data:  追加データ（例: {"contract_data": {...}}）

    Returns:
        CustomerLifecyclePipelineResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "customer_id": customer_id,
        "mode": mode,
        "today": date.today().isoformat(),
        **(input_data or {}),
    }

    # ─── ユーティリティ ──────────────────────────────────────────────────────

    def _add_step(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
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

    def _fail(step_name: str) -> CustomerLifecyclePipelineResult:
        return CustomerLifecyclePipelineResult(
            success=False,
            mode=mode,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    def _done(final: dict[str, Any], **kwargs: Any) -> CustomerLifecyclePipelineResult:
        return CustomerLifecyclePipelineResult(
            success=True,
            mode=mode,
            steps=steps,
            final_output=final,
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            **kwargs,
        )

    # ─── Step 0（共通）: 顧客情報取得 ────────────────────────────────────────
    # steps リストには追加しない（前提条件取得のみ）
    customer_row: dict[str, Any] = {}
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        res = db.table("customers").select(
            "id, customer_company_name, industry, plan, active_modules, "
            "mrr, health_score, nps_score, status, onboarded_at, cs_owner, "
            "created_at"
        ).eq("id", customer_id).eq("company_id", company_id).single().execute()
        if res.data:
            customer_row = res.data
        else:
            logger.warning(f"customers row not found: customer_id={customer_id}")
    except Exception as e:
        logger.warning(f"customers fetch failed: {e}")

    context["customer"] = customer_row

    # ═══════════════════════════════════════════════════════════════════════════
    # モード分岐
    # ═══════════════════════════════════════════════════════════════════════════

    if mode == "onboarding":
        return await _run_onboarding(
            company_id, customer_id, context, steps,
            pipeline_start, _add_step, _fail, _done,
        )
    else:
        return await _run_health_check(
            company_id, customer_id, context, steps,
            pipeline_start, _add_step, _fail, _done,
        )


# ─── オンボーディングフロー ──────────────────────────────────────────────────

async def _run_onboarding(
    company_id: str,
    customer_id: str,
    context: dict[str, Any],
    steps: list[StepResult],
    pipeline_start: int,
    _add_step: Any,
    _fail: Any,
    _done: Any,
) -> CustomerLifecyclePipelineResult:
    customer = context.get("customer", {})
    industry = customer.get("industry", "")

    # ─── Step 1: account_setup ──────────────────────────────────────────────
    # customers.status を "onboarding" に更新し、ゲノムを適用する
    s1_start = int(time.time() * 1000)
    genome_applied: dict[str, Any] = {}
    setup_warnings: list[str] = []

    try:
        # ゲノムファイル読み込み（{industry}.json が存在する場合）
        genome_path = _GENOME_BASE / f"{industry}.json"
        if genome_path.exists():
            try:
                with genome_path.open(encoding="utf-8") as f:
                    genome_data = json.load(f)
                genome_applied = genome_data
            except Exception as ge:
                setup_warnings.append(f"genome load failed: {ge}")
        else:
            setup_warnings.append(f"genome not found for industry={industry!r}, using defaults")

        # customers テーブル更新
        from db.supabase import get_service_client
        db = get_service_client()
        update_payload: dict[str, Any] = {
            "status": "onboarding",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if genome_applied:
            update_payload["genome_customizations"] = genome_applied
        db.table("customers").update(update_payload).eq("id", customer_id).execute()

        s1_out = MicroAgentOutput(
            agent_name="account_setup",
            success=True,
            result={
                "status_set": "onboarding",
                "genome_applied": bool(genome_applied),
                "genome_industry": industry,
                "warnings": setup_warnings,
            },
            confidence=1.0 if not setup_warnings else 0.85,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    except Exception as e:
        s1_out = MicroAgentOutput(
            agent_name="account_setup",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    _add_step(1, "account_setup", "account_setup", s1_out)
    if not s1_out.success:
        return _fail("account_setup")
    context.update(s1_out.result)

    # ─── Step 2: welcome_email ───────────────────────────────────────────────
    # ウェルカムメールを message マイクロエージェント相当の処理で送信
    s2_start = int(time.time() * 1000)
    try:
        template_path = (
            Path(__file__).parents[2] / "templates" / "welcome_email.html"
        )
        template_html = template_path.read_text(encoding="utf-8") if template_path.exists() else ""

        company_name = customer.get("customer_company_name", "")
        login_url = context.get("login_url", "https://app.shachotwo.com/login")

        # プラン情報を取得
        plan = customer.get("plan", "self")
        industry_labels = {
            "construction": "建設業", "manufacturing": "製造業",
            "dental": "歯科", "care": "介護・福祉", "logistics": "物流・運送",
            "wholesale": "卸売業", "realestate": "不動産",
        }
        industry_label = industry_labels.get(industry, industry or "御社の業種")

        # Jinja2 が使えない場合は素朴な置換
        try:
            from jinja2 import Template  # type: ignore
            rendered = Template(template_html).render(
                company_name=company_name,
                contact_name=context.get("contact_name", "ご担当者"),
                login_url=login_url,
                plan=plan,
                industry_label=industry_label,
                slots=context.get("meeting_slots", []),
                cs_owner_name=context.get("cs_owner_name", "担当者"),
                slack_channel=context.get("slack_channel", ""),
                sender_name=context.get("sender_name", "杉本 祐陽"),
                unsubscribe_url=context.get("unsubscribe_url", "#"),
            )
        except ImportError:
            rendered = (
                template_html
                .replace("{{ company_name }}", company_name)
                .replace("{{ contact_name }}", context.get("contact_name", "ご担当者"))
                .replace("{{ login_url }}", login_url)
            )

        # saas_writer 経由でメール送信ログを記録（実際の送信は外部メールサービスに委譲）
        email_payload: dict[str, Any] = {
            "service": "supabase",
            "operation": "insert",
            "params": {
                "table": "execution_logs",
                "record": {
                    "company_id": company_id,
                    "pipeline": "customer_lifecycle",
                    "step": "welcome_email",
                    "customer_id": customer_id,
                    "payload": {
                        "email_type": "welcome",
                        "to": context.get("contact_email", ""),
                        "subject": f"【シャチョツーへようこそ】{company_name} 様 はじめかたのご案内",
                        "body_html": rendered[:2000],  # DB保存用に切り詰め
                    },
                    "status": "queued",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            "approved": True,
            "dry_run": context.get("dry_run", False),
        }
        email_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload=email_payload,
            context=context,
        ))

        s2_out = MicroAgentOutput(
            agent_name="welcome_email",
            success=email_out.success,
            result={
                "email_queued": email_out.success,
                "template_used": "welcome_email.html",
                "subject": email_payload["params"]["record"]["payload"]["subject"],
            },
            confidence=0.95,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        s2_out = MicroAgentOutput(
            agent_name="welcome_email",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "welcome_email", "saas_writer", s2_out)
    if not s2_out.success:
        return _fail("welcome_email")

    # ─── Step 3: onboard_sequence（プラン別） ──────────────────────────────────
    # プランに応じたフォローシーケンスをスケジュールキューに登録する
    s3_start = int(time.time() * 1000)
    sequence_jobs: list[dict[str, Any]] = []
    today_str = context["today"]
    plan = customer.get("plan", "self")  # self / consul / full_support

    try:
        from datetime import timedelta
        from db.supabase import get_service_client
        db = get_service_client()

        base_date = date.fromisoformat(today_str)

        # セルフプランのシーケンス（全プラン共通）
        combined_sequence: dict[int, dict] = dict(ONBOARD_SEQUENCE_SELF)

        # コンサル/フルサポートの場合は追加メールをマージ
        if plan in ("consul", "full_support"):
            combined_sequence.update(ONBOARD_SEQUENCE_CONSUL_EXTRA)

        # 離脱検知ジョブも登録
        for rule in CHURN_DETECTION_RULES:
            day = rule["day"]
            if day not in combined_sequence:
                combined_sequence[day] = {
                    "message": rule["message"],
                    "template": rule.get("template"),
                    "subject_suffix": rule["label"],
                    "is_churn_check": True,
                    "churn_rule": rule,
                }

        for day_offset in sorted(combined_sequence.keys()):
            seq_info = combined_sequence[day_offset]
            scheduled_date = base_date + timedelta(days=day_offset)
            job: dict[str, Any] = {
                "company_id": company_id,
                "customer_id": customer_id,
                "pipeline": "customer_lifecycle",
                "trigger_type": "onboard_sequence",
                "scheduled_at": scheduled_date.isoformat(),
                "payload": {
                    "day": day_offset,
                    "message": seq_info["message"],
                    "template": seq_info.get("template"),
                    "subject_suffix": seq_info.get("subject_suffix", ""),
                    "email_to": context.get("contact_email", ""),
                    "company_name": customer.get("customer_company_name", ""),
                    "plan": plan,
                    "is_churn_check": seq_info.get("is_churn_check", False),
                    "churn_rule": seq_info.get("churn_rule"),
                },
                "status": "scheduled",
            }
            sequence_jobs.append(job)

        # execution_logs にバルク登録（スケジュールキューとして活用）
        if not context.get("dry_run", False) and sequence_jobs:
            db.table("execution_logs").insert(sequence_jobs).execute()

        s3_out = MicroAgentOutput(
            agent_name="onboard_sequence",
            success=True,
            result={
                "jobs_scheduled": len(sequence_jobs),
                "plan": plan,
                "schedule": [
                    {"day": j["payload"]["day"], "date": j["scheduled_at"],
                     "is_churn_check": j["payload"].get("is_churn_check", False)}
                    for j in sequence_jobs
                ],
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="onboard_sequence",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "onboard_sequence", "schedule_writer", s3_out)
    if not s3_out.success:
        return _fail("onboard_sequence")

    final_output = {
        "customer_id": customer_id,
        "status": "onboarding",
        "plan": plan,
        "genome_applied": context.get("genome_applied", False),
        "genome_industry": industry,
        "welcome_email_queued": s2_out.result.get("email_queued", False),
        "sequence_jobs_scheduled": s3_out.result.get("jobs_scheduled", 0),
        "schedule": s3_out.result.get("schedule", []),
        "churn_detection_enabled": True,
    }
    logger.info(
        f"customer_lifecycle onboarding complete: customer_id={customer_id}, "
        f"plan={plan}, industry={industry}, jobs={final_output['sequence_jobs_scheduled']}"
    )
    return _done(final_output)


# ─── ヘルスチェックフロー ────────────────────────────────────────────────────

async def _run_health_check(
    company_id: str,
    customer_id: str,
    context: dict[str, Any],
    steps: list[StepResult],
    pipeline_start: int,
    _add_step: Any,
    _fail: Any,
    _done: Any,
) -> CustomerLifecyclePipelineResult:
    customer = context.get("customer", {})

    # ─── Step 4: usage_data ─────────────────────────────────────────────────
    # saas_reader 経由で利用統計を収集する
    usage_out = await run_saas_reader(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_reader",
        payload={
            "service": "supabase",
            "operation": "get_usage_stats",
            "params": {
                "table": "execution_logs",
                "select": "id, pipeline, executed_at, status",
                "customer_id": customer_id,
                "limit": 200,
            },
        },
        context=context,
    ))
    _add_step(4, "usage_data", "saas_reader", usage_out)
    if not usage_out.success:
        return _fail("usage_data")

    raw_logs: list[dict] = usage_out.result.get("data", [])
    context["raw_usage_logs"] = raw_logs

    # 利用統計を集計
    usage_metrics = _compute_usage_metrics(raw_logs, customer)
    context["usage_metrics"] = usage_metrics

    # ─── Step 5: health_calculator ──────────────────────────────────────────
    # 5次元スコアを算出する（LLM不使用・純粋な数値計算）
    s5_start = int(time.time() * 1000)
    try:
        dimensions = _compute_health_dimensions(usage_metrics, customer)
        total_score = int(
            sum(dimensions[dim] * weight for dim, weight in HEALTH_WEIGHTS.items())
        )
        total_score = max(0, min(100, total_score))

        risk_factors: list[str] = _identify_risk_factors(dimensions, customer)

        s5_out = MicroAgentOutput(
            agent_name="health_calculator",
            success=True,
            result={
                "score": total_score,
                "dimensions": dimensions,
                "risk_factors": risk_factors,
                "weights": HEALTH_WEIGHTS,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )
    except Exception as e:
        s5_out = MicroAgentOutput(
            agent_name="health_calculator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    _add_step(5, "health_calculator", "health_calculator", s5_out)
    if not s5_out.success:
        return _fail("health_calculator")

    health_score: int = s5_out.result["score"]
    dimensions: dict[str, float] = s5_out.result["dimensions"]
    risk_factors: list[str] = s5_out.result["risk_factors"]
    context["health_score"] = health_score
    context["dimensions"] = dimensions

    # ─── Step 6: alert_matcher ──────────────────────────────────────────────
    # rule_matcher でアラート区分を判定する
    alert_data: dict[str, Any] = {
        "health_score": health_score,
        "risk_factors": risk_factors,
        "active_modules": customer.get("active_modules") or [],
        "plan": customer.get("plan", ""),
        "nps_score": customer.get("nps_score"),
    }
    alert_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "extracted_data": alert_data,
            "domain": "crm_health_alert",
            "category": "health_score",
        },
        context=context,
    ))
    _add_step(6, "alert_matcher", "rule_matcher", alert_out)
    # rule_matcher は rules なしでも success=True を返すため失敗でも続行

    # スコア閾値によるラベル決定（rule_matcher 結果を補完）
    health_label, action_required = _classify_health(health_score, customer)
    context["health_label"] = health_label
    context["action_required"] = action_required

    # ─── Step 7: action_message ─────────────────────────────────────────────
    # アクションが必要な場合のみメッセージ生成・ログ記録を行う
    s7_start = int(time.time() * 1000)
    action_result: dict[str, Any] = {"action_required": action_required, "actions_taken": []}

    if action_required:
        try:
            actions = await _execute_health_actions(
                company_id, customer_id, customer, health_label,
                health_score, dimensions, risk_factors, context,
            )
            action_result["actions_taken"] = actions
        except Exception as e:
            logger.warning(f"action_message failed (non-fatal): {e}")
            action_result["error"] = str(e)

    s7_out = MicroAgentOutput(
        agent_name="action_message",
        success=True,
        result=action_result,
        confidence=0.9,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s7_start,
    )
    _add_step(7, "action_message", "action_message", s7_out)

    # ─── customer_health テーブルに記録 ─────────────────────────────────────
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        db.table("customer_health").insert({
            "company_id": company_id,
            "customer_id": customer_id,
            "score": health_score,
            "dimensions": dimensions,
            "risk_factors": risk_factors,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        # customers.health_score も更新
        db.table("customers").update({
            "health_score": health_score,
            "status": _status_from_label(health_label),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", customer_id).execute()
    except Exception as e:
        logger.warning(f"customer_health persist failed: {e}")

    final_output = {
        "customer_id": customer_id,
        "health_score": health_score,
        "health_label": health_label,
        "dimensions": dimensions,
        "risk_factors": risk_factors,
        "action_required": action_required,
        "actions_taken": action_result.get("actions_taken", []),
    }

    logger.info(
        f"customer_lifecycle health_check complete: customer_id={customer_id}, "
        f"score={health_score}, label={health_label}"
    )
    return _done(
        final_output,
        health_score=health_score,
        health_label=health_label,
        action_required=action_required,
    )


# ─── ヘルパー関数 ────────────────────────────────────────────────────────────

def _compute_usage_metrics(
    logs: list[dict],
    customer: dict[str, Any],
) -> dict[str, Any]:
    """execution_logs から利用統計を集計する。"""
    from datetime import timedelta

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    bpo_executions_7d = 0
    bpo_executions_30d = 0
    unique_pipelines: set[str] = set()

    for log in logs:
        executed_at_raw = log.get("executed_at", "")
        try:
            executed_date = date.fromisoformat(str(executed_at_raw)[:10])
        except (ValueError, TypeError):
            continue

        pipeline = log.get("pipeline", "")
        if executed_date >= week_ago:
            bpo_executions_7d += 1
            unique_pipelines.add(pipeline)
        if executed_date >= month_ago:
            bpo_executions_30d += 1

    active_modules: list[str] = customer.get("active_modules") or []
    available_modules_count = max(1, len(active_modules))

    return {
        "bpo_executions_7d": bpo_executions_7d,
        "bpo_executions_30d": bpo_executions_30d,
        "unique_pipelines_7d": len(unique_pipelines),
        "active_modules_count": available_modules_count,
        "log_total": len(logs),
    }


def _compute_health_dimensions(
    metrics: dict[str, Any],
    customer: dict[str, Any],
) -> dict[str, float]:
    """5次元スコア（各 0〜100）を算出する。"""

    # 利用度: WAU相当（7日間のBPO実行数）を基準に正規化
    # 週5回以上を 100 点とする
    usage_raw = min(metrics["bpo_executions_7d"], 20) / 20 * 100
    usage_score = round(usage_raw, 1)

    # エンゲージメント: ユニークパイプライン数 / アクティブモジュール数
    active_modules = metrics["active_modules_count"]
    unique = metrics["unique_pipelines_7d"]
    engagement_score = round(min(unique / max(active_modules, 1), 1.0) * 100, 1)

    # サポート: チケット頻度が低いほど高い（ここでは execution_logs 中のエラー数を代替指標）
    # エラー率が 0% → 100点、エラー率 50%以上 → 0点
    total_logs = max(metrics["log_total"], 1)
    # customer_health テーブルに support_ticket_count がないため0で初期化
    support_score = 80.0  # デフォルト（チケットデータ未取得の場合は中間値）

    # NPS スコア（-100〜100 → 0〜100 に正規化）
    nps_raw: int | None = customer.get("nps_score")
    if nps_raw is not None:
        nps_score = round((nps_raw + 100) / 2, 1)
    else:
        nps_score = 50.0  # 未回答は中間値

    # 拡張可能性: 未使用モジュール比率（高いほど拡張余地あり → スコアとしては中程度）
    # ここでは「追加モジュールが多い = スコア高」ではなく
    # 「既存モジュールを十分に使いこなしている」を良い状態と定義する
    # 週の利用数が active_modules の半分以上利用している場合を良しとする
    if active_modules > 0 and unique >= active_modules / 2:
        expansion_score = 80.0
    elif active_modules == 0:
        expansion_score = 30.0
    else:
        expansion_score = 50.0

    return {
        "usage":       usage_score,
        "engagement":  engagement_score,
        "support":     support_score,
        "nps":         nps_score,
        "expansion":   expansion_score,
    }


def _identify_risk_factors(
    dimensions: dict[str, float],
    customer: dict[str, Any],
) -> list[str]:
    """スコアが低い次元からリスク要因を生成する。"""
    factors: list[str] = []
    if dimensions["usage"] < 30:
        factors.append("低ログイン頻度・BPO実行数が少ない")
    if dimensions["engagement"] < 30:
        factors.append("利用機能の幅が狭い")
    if dimensions["support"] < 40:
        factors.append("サポートチケット頻度が高い")
    if dimensions["nps"] < 30 or customer.get("nps_score") is None:
        factors.append("NPS未回答またはスコアが低い")
    if dimensions["expansion"] < 40:
        factors.append("モジュール活用率が低い")
    return factors


def _classify_health(
    score: int,
    customer: dict[str, Any],
) -> tuple[str, bool]:
    """スコアからラベルとアクション要否を返す。"""
    active_modules: list[str] = customer.get("active_modules") or []
    # 全モジュールのうち未契約モジュールが存在するかどうかの簡易チェック
    all_modules = {"brain", "bpo_core", "backoffice", "analytics"}
    unused_modules = all_modules - set(active_modules)

    if score < RISK_THRESHOLD:
        return "risk", True
    elif score < CAUTION_THRESHOLD:
        return "caution", False
    elif score > EXPANSION_THRESHOLD and unused_modules:
        return "expansion", True
    else:
        return "healthy", False


def _status_from_label(label: str) -> str:
    """health_label を customers.status 値にマップする。"""
    return {
        "risk":      "at_risk",
        "caution":   "active",
        "healthy":   "active",
        "expansion": "active",
    }.get(label, "active")


async def _execute_health_actions(
    company_id: str,
    customer_id: str,
    customer: dict[str, Any],
    health_label: str,
    health_score: int,
    dimensions: dict[str, float],
    risk_factors: list[str],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """ヘルスラベルに応じた自動アクションを実行する。"""
    actions: list[dict[str, Any]] = []
    company_name = customer.get("customer_company_name", "顧客企業")
    cs_owner_id = customer.get("cs_owner")
    dry_run: bool = context.get("dry_run", False)

    if health_label == "risk":
        # CS担当への緊急通知（Slack未設定時はログ出力）+ 対策サジェストをDB記録
        slack_message = (
            f"[解約リスクアラート] {company_name}\n"
            f"ヘルススコア: {health_score}/100\n"
            f"リスク要因: {', '.join(risk_factors) or 'なし'}\n"
            f"--- 推奨アクション ---\n"
            f"1. 48時間以内に担当CSから直接連絡する\n"
            f"2. 活用支援MTGをセットし、利用課題を深掘りする\n"
            f"3. 必要に応じてプランのダウングレードを提案する"
        )
        notify_payload: dict[str, Any] = {
            "service": "supabase",
            "operation": "insert",
            "params": {
                "table": "execution_logs",
                "record": {
                    "company_id": company_id,
                    "pipeline": "customer_lifecycle",
                    "step": "risk_alert",
                    "customer_id": customer_id,
                    "payload": {
                        "channel": "cs-alerts",
                        "message": slack_message,
                        "health_score": health_score,
                        "risk_factors": risk_factors,
                        "cs_owner_id": str(cs_owner_id) if cs_owner_id else None,
                        "label": "risk",
                    },
                    "status": "queued",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            "approved": True,
            "dry_run": dry_run,
        }
        notify_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload=notify_payload,
            context=context,
        ))
        # 通知送信（Slack未設定時はログ出力）
        slack_url = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(slack_url, json={"text": slack_message, "channel": "#cs-alerts"})
            except Exception as e:
                logger.warning(f"[customer_lifecycle] 通知失敗 (non-fatal): {e}")
        else:
            logger.info(f"[通知][#cs-alerts] {slack_message}")
        actions.append({
            "type": "risk_alert",
            "success": notify_out.success,
            "health_score": health_score,
        })

    elif health_label == "expansion":
        # 未使用モジュールへの拡張提案メールをキューに積む
        active_modules: list[str] = customer.get("active_modules") or []
        all_modules = {"brain", "bpo_core", "backoffice", "analytics"}
        unused = sorted(all_modules - set(active_modules))

        expansion_message = (
            f"{company_name} 様\n\n"
            f"ご利用が大変順調で、ヘルススコアが {health_score}/100 を達成されました。\n\n"
            f"さらなる業務自動化に向け、以下の追加モジュールをご提案します:\n"
            + "\n".join(f"  - {m}" for m in unused)
            + "\n\n詳しくはご担当のCSまでお問い合わせください。"
        )
        expand_payload: dict[str, Any] = {
            "service": "supabase",
            "operation": "insert",
            "params": {
                "table": "execution_logs",
                "record": {
                    "company_id": company_id,
                    "pipeline": "customer_lifecycle",
                    "step": "expansion_proposal",
                    "customer_id": customer_id,
                    "payload": {
                        "email_type": "expansion_proposal",
                        "to": context.get("contact_email", ""),
                        "subject": f"【ご提案】{company_name} 様の業務自動化をさらに拡張しませんか",
                        "body_text": expansion_message,
                        "unused_modules": unused,
                        "health_score": health_score,
                    },
                    "status": "queued",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            "approved": True,
            "dry_run": dry_run,
        }
        expand_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload=expand_payload,
            context=context,
        ))
        actions.append({
            "type": "expansion_proposal_email",
            "success": expand_out.success,
            "unused_modules": unused,
        })

    return actions


# ─── 離脱検知実行 ───────────────────────────────────────────────────────────

async def run_churn_detection(
    company_id: str,
    customer_id: str,
    churn_rule: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    離脱検知ルールを評価し、条件に該当する場合にアクションを実行する。

    churn_rule の例:
      {"day": 7, "condition": "knowledge_count_zero", "action": "slack_alert", ...}

    Returns:
        {"triggered": bool, "action_taken": str | None, "detail": str}
    """
    dry_run: bool = context.get("dry_run", False)

    try:
        from db.supabase import get_service_client
        db = get_service_client()

        customer = db.table("customers").select(
            "customer_company_name, plan, cs_owner"
        ).eq("id", customer_id).eq("company_id", company_id).single().execute()
        customer_data = customer.data or {}
        company_name = customer_data.get("customer_company_name", "顧客企業")

        condition = churn_rule.get("condition", "")
        triggered = False

        if condition == "knowledge_count_zero":
            kn = db.table("knowledge_items").select(
                "id", count="exact"
            ).eq("company_id", company_id).execute()
            triggered = (kn.count or 0) == 0

        elif condition == "qa_count_zero":
            qa = db.table("qa_sessions").select(
                "id", count="exact"
            ).eq("company_id", company_id).execute()
            triggered = (qa.count or 0) == 0

        elif condition == "bpo_count_zero":
            bpo = db.table("execution_logs").select(
                "id", count="exact"
            ).eq("company_id", company_id).neq(
                "trigger_type", "onboard_sequence"
            ).execute()
            triggered = (bpo.count or 0) == 0

        if not triggered:
            return {"triggered": False, "action_taken": None, "detail": "条件非該当"}

        action = churn_rule.get("action", "")
        action_taken = action
        detail = churn_rule.get("message", "")

        if action == "slack_alert":
            slack_message = (
                f"[離脱リスクアラート] {company_name}\n"
                f"{churn_rule.get('label', '')}\n"
                f"{detail}"
            )
            slack_url = os.environ.get("SLACK_WEBHOOK_URL")
            if slack_url and not dry_run:
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(
                            slack_url,
                            json={"text": slack_message, "channel": "#cs-alerts"},
                        )
                except Exception as e:
                    logger.warning(f"[churn_detection] Slack通知失敗: {e}")
            else:
                logger.info(f"[離脱検知][#cs-alerts] {slack_message}")

        elif action == "phone_followup":
            # execution_logs にフォローアップタスクを記録
            if not dry_run:
                db.table("execution_logs").insert({
                    "company_id": company_id,
                    "customer_id": customer_id,
                    "pipeline": "customer_lifecycle",
                    "step": "churn_phone_followup",
                    "payload": {
                        "action": "phone_followup",
                        "label": churn_rule.get("label", ""),
                        "message": detail,
                        "cs_owner": customer_data.get("cs_owner"),
                    },
                    "status": "queued",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()

        elif action == "upgrade_email":
            # 離脱リスクメール（コンサルアップグレード提案）を送信キューに登録
            template_name = churn_rule.get("template", "churn_risk_alert.html")
            template_path = Path(__file__).parents[2] / "templates" / template_name
            template_html = ""
            if template_path.exists():
                template_html = template_path.read_text(encoding="utf-8")

            try:
                from jinja2 import Template  # type: ignore
                rendered = Template(template_html).render(
                    company_name=company_name,
                    contact_name=context.get("contact_name", "ご担当者"),
                    dashboard_url=context.get("dashboard_url", "https://app.shachotwo.com"),
                    support_url=context.get("support_url", "https://app.shachotwo.com/support"),
                    unsubscribe_url=context.get("unsubscribe_url", "#"),
                )
            except ImportError:
                rendered = template_html

            if not dry_run:
                db.table("execution_logs").insert({
                    "company_id": company_id,
                    "customer_id": customer_id,
                    "pipeline": "customer_lifecycle",
                    "step": "churn_upgrade_email",
                    "payload": {
                        "email_type": "churn_risk_alert",
                        "to": context.get("contact_email", ""),
                        "subject": f"【シャチョツー】{company_name} 様、お困りではありませんか？",
                        "body_html": rendered[:2000],
                        "template": template_name,
                    },
                    "status": "queued",
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }).execute()

        return {"triggered": True, "action_taken": action_taken, "detail": detail}

    except Exception as e:
        logger.warning(f"[churn_detection] error: {e}")
        return {"triggered": False, "action_taken": None, "detail": f"error: {e}"}
