"""workers/bpo/sales/pipelines — 後方互換レイヤー。

実体は各ポジションディレクトリに存在:
    marketing/  — マーケティングAI社員
    sfa/        — SFA（営業支援）AI社員
    crm/        — CRM（顧客関係管理）AI社員
    cs/         — CS（カスタマーサクセス）AI社員
    learning/   — 学習AI社員

このファイルは旧パス ``workers.bpo.sales.pipelines.xxx`` からの import を
維持するための re-export です。新規コードでは各ポジションディレクトリから
直接 import してください。
"""
# ── marketing ─────────────────────────────────────────────────────────────
from workers.bpo.sales.marketing.outreach_pipeline import (
    run_outreach_pipeline,
    OutreachPipelineResult,
    OutreachRecord,
)

# ── sfa ───────────────────────────────────────────────────────────────────
from workers.bpo.sales.sfa.lead_qualification_pipeline import (
    LeadQualificationResult,
    run_lead_qualification_pipeline,
)
from workers.bpo.sales.sfa.proposal_generation_pipeline import (
    run_proposal_generation_pipeline,
    ProposalGenerationResult,
    StepResult as ProposalStepResult,
)
from workers.bpo.sales.sfa.quotation_contract_pipeline import (
    run_quotation_contract_pipeline,
    QuotationContractResult,
    StepRecord as QuotationStepRecord,
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_REVISION_REQUESTED,
)
from workers.bpo.sales.sfa.consent_flow import (
    run_consent_flow_pipeline,
    process_consent_agreement,
    ConsentFlowResult,
    StepResult as ConsentFlowStepResult,
)

# ── crm ───────────────────────────────────────────────────────────────────
from workers.bpo.sales.crm.customer_lifecycle_pipeline import (
    run_customer_lifecycle_pipeline,
    CustomerLifecyclePipelineResult,
)
from workers.bpo.sales.crm.revenue_request_pipeline import (
    run_revenue_request_pipeline,
    RevenueRequestPipelineResult,
    StepResult as RevenueRequestStepResult,
)

# ── cs ────────────────────────────────────────────────────────────────────
from workers.bpo.sales.cs.support_auto_response_pipeline import (
    run_support_auto_response_pipeline,
    SupportAutoResponseResult,
)
from workers.bpo.sales.cs.upsell_briefing_pipeline import (
    run_upsell_briefing_pipeline,
    UpsellBriefingPipelineResult,
    UpsellOpportunity,
)
from workers.bpo.sales.cs.cancellation_pipeline import (
    run_cancellation_pipeline,
    CancellationPipelineResult,
    StepResult as CancellationStepResult,
)

# ── learning ──────────────────────────────────────────────────────────────
from workers.bpo.sales.learning.win_loss_feedback_pipeline import (
    run_win_loss_feedback_pipeline,
    WinLossFeedbackResult,
    StepResult as WinLossStepResult,
)
from workers.bpo.sales.learning.cs_feedback_pipeline import (
    run_cs_feedback_pipeline,
    CsFeedbackPipelineResult,
    StepResult as CsFeedbackStepResult,
)


__all__ = [
    # marketing
    "run_outreach_pipeline",
    "OutreachPipelineResult",
    "OutreachRecord",
    # sfa
    "run_lead_qualification_pipeline",
    "LeadQualificationResult",
    "run_proposal_generation_pipeline",
    "ProposalGenerationResult",
    "ProposalStepResult",
    "run_quotation_contract_pipeline",
    "QuotationContractResult",
    "QuotationStepRecord",
    "APPROVAL_PENDING",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "APPROVAL_REVISION_REQUESTED",
    "run_consent_flow_pipeline",
    "process_consent_agreement",
    "ConsentFlowResult",
    "ConsentFlowStepResult",
    # crm
    "run_customer_lifecycle_pipeline",
    "CustomerLifecyclePipelineResult",
    "run_revenue_request_pipeline",
    "RevenueRequestPipelineResult",
    "RevenueRequestStepResult",
    # cs
    "run_support_auto_response_pipeline",
    "SupportAutoResponseResult",
    "run_upsell_briefing_pipeline",
    "UpsellBriefingPipelineResult",
    "UpsellOpportunity",
    "run_cancellation_pipeline",
    "CancellationPipelineResult",
    "CancellationStepResult",
    # learning
    "run_win_loss_feedback_pipeline",
    "WinLossFeedbackResult",
    "WinLossStepResult",
    "run_cs_feedback_pipeline",
    "CsFeedbackPipelineResult",
    "CsFeedbackStepResult",
]

# PIPELINE_REGISTRY — BPO Manager の TaskRouter が参照するメタデータ
# NOTE: module パスは旧パス（pipelines/）を維持。実体ファイルは pipelines/ に残存。
PIPELINE_REGISTRY: dict[str, dict] = {
    "lead_qualification_pipeline": {
        "module": "workers.bpo.sales.sfa.lead_qualification_pipeline",
        "function": "run_lead_qualification_pipeline",
        "industry": "sales",
        "position": "sfa",
        "trigger": "form_submit / webhook / email_inbound",
        "steps": 6,
        "description": (
            "フォームデータ構造化抽出 + スコアリングルール照合 + "
            "スコア計算（業種/従業員数/緊急度/予算/流入元） + "
            "振り分け（QUALIFIED≥70 / REVIEW 40-69 / NURTURING<40）+ "
            "leads DB保存 + 初回お礼メール生成"
        ),
    },
    "outreach_pipeline": {
        "module": "workers.bpo.sales.marketing.outreach_pipeline",
        "function": "run_outreach_pipeline",
        "industry": "sales",
        "position": "marketing",
        "trigger": "schedule_daily_0800 / manual",
        "steps": 8,
        "description": (
            "企業リサーチ（gBizINFO）+ ペイン推定 + LP生成 + "
            "メール/フォーム送信 + シグナル判定 + 商談予約 + leads保存"
        ),
    },
    "proposal_generation_pipeline": {
        "module": "workers.bpo.sales.sfa.proposal_generation_pipeline",
        "function": "run_proposal_generation_pipeline",
        "industry": "sales",
        "position": "sfa",
        "trigger": "lead_score_gte_70 / manual",
        "steps": 8,
        "description": (
            "リード情報取得 + 業種テンプレート選択 + LLM提案書JSON生成 + "
            "PDF生成（WeasyPrint）+ Storage保存 + メール生成 + "
            "SendGrid送信 + proposals/opportunities DB更新"
        ),
    },
    "quotation_contract_pipeline": {
        "module": "workers.bpo.sales.sfa.quotation_contract_pipeline",
        "function": "run_quotation_contract_pipeline",
        "industry": "sales",
        "position": "sfa",
        "trigger": "proposal_accepted / manual",
        "steps": 8,
        "description": (
            "Phase A: モジュール選択→見積金額計算（Decimal）+ "
            "見積書PDF生成（quotation_template.html）+ メール送付 + 承認確認。"
            "Phase B: 契約書PDF生成（contract_template.html）+ "
            "CloudSign電子署名送信 + contracts/customers DB更新 + Slack受注通知 + "
            "freee請求書自動発行"
        ),
    },
    "customer_lifecycle_pipeline": {
        "module": "workers.bpo.sales.crm.customer_lifecycle_pipeline",
        "function": "run_customer_lifecycle_pipeline",
        "industry": "sales",
        "position": "crm",
        "trigger": "contract_signed / schedule_daily_0900",
        "steps": 7,
        "description": (
            "[onboarding] customers.status更新 + ゲノム適用（{industry}.json）+ "
            "ウェルカムメール送信（welcome_email.html）+ "
            "Day1/3/7/14/30 フォローシーケンス登録 / "
            "[health_check] 利用データ収集 + 5次元ヘルススコア計算"
            "（利用度30%/エンゲージメント25%/サポート15%/NPS15%/拡張15%）+ "
            "アラート判定（<40:解約リスクSlack通知 / >80+未使用:拡張提案メール）+ "
            "customer_health テーブル保存"
        ),
    },
    "support_auto_response_pipeline": {
        "module": "workers.bpo.sales.cs.support_auto_response_pipeline",
        "function": "run_support_auto_response_pipeline",
        "industry": "sales",
        "position": "cs",
        "trigger": "inbound_email / inbound_chat / inbound_form",
        "steps": 7,
        "description": (
            "チケット分類・緊急度判定 + 顧客コンテキスト収集 + FAQ検索 + "
            "AI回答生成 + ConditionEvaluator振り分け（auto_send/human_review/escalate/billing_routing） + "
            "回答送信 + SLA監視 + support_tickets/ticket_messages保存"
        ),
    },
    "upsell_briefing_pipeline": {
        "module": "workers.bpo.sales.cs.upsell_briefing_pipeline",
        "function": "run_upsell_briefing_pipeline",
        "industry": "sales",
        "position": "cs",
        "trigger": "schedule_daily_after_health_score / milestone_reached",
        "steps": 5,
        "description": (
            "顧客利用データ収集（Supabase）+ 拡張タイミング判定4パターン + "
            "コンサル用ブリーフィング生成（LLM）+ Slack #sales-upsell通知 + "
            "コンサルカレンダー「提案準備」ブロック追加 + 商談候補日3枠提示"
        ),
    },
    "win_loss_feedback_pipeline": {
        "module": "workers.bpo.sales.learning.win_loss_feedback_pipeline",
        "function": "run_win_loss_feedback_pipeline",
        "industry": "sales",
        "position": "learning",
        "trigger": "opportunity_stage_changed_to_won_or_lost / schedule_daily_outreach_pdca",
        "steps": 7,
        "description": (
            "[受注] 受注パターン抽出 + スコアリング重み更新（業種×規模ボーナス調整） + "
            "成功テンプレート保存（win_loss_patterns）/ "
            "[失注] ヒアリングメール自動送信 + 失注パターン分析・月次アラート / "
            "[PDCA] 業種別反応率集計（outreach_performance）+ A/Bテスト文案自動生成"
        ),
    },
    "cs_feedback_pipeline": {
        "module": "workers.bpo.sales.learning.cs_feedback_pipeline",
        "function": "run_cs_feedback_pipeline",
        "industry": "sales",
        "position": "learning",
        "trigger": "ticket_closed / schedule_monthly",
        "steps": 5,
        "description": (
            "クローズ済みチケット収集（AI回答/人間回答比率・CSAT分布） + "
            "パターン分析（CSAT>=4→good / CSAT<=2→needs_improvement / 人間修正パターン抽出） + "
            "knowledge/qa自動更新（新FAQ追加・既存FAQ改善・cs_feedback記録） + "
            "confidence閾値自動調整（CSAT<4.0→0.90 / CSAT>=4.5→0.80、scoring_model_versionsに記録） + "
            "月次レポート生成 + Slack投稿（AI対応率/CSAT推移/FAQ TOP10/改善提案）"
        ),
    },
    "consent_flow": {
        "module": "workers.bpo.sales.sfa.consent_flow",
        "function": "run_consent_flow_pipeline",
        "industry": "sales",
        "position": "sfa",
        "trigger": "quotation_approved / contract_ready",
        "steps": 7,
        "description": (
            "契約書/見積書PDF生成 + 同意トークン（UUID）生成 + "
            "同意依頼メール送信（Gmail API） + 同意ボタン押下→consent_records記録 + "
            "同意済みスタンプ付きPDF生成 + Supabase Storage保存 + "
            "contracts テーブルのステータスを signed に更新"
        ),
    },
    "revenue_request_pipeline": {
        "module": "workers.bpo.sales.crm.revenue_request_pipeline",
        "function": "run_revenue_request_pipeline",
        "industry": "sales",
        "position": "crm",
        "trigger": "schedule_monthly_1st / inbound_request",
        "steps": 7,
        "description": (
            "[売上管理] freee APIで請求・入金データ取得→MRR/ARR/NRR/チャーン率算出→月次レポート + Slack投稿 / "
            "[要望管理] サポートチケット・Slack・メールから要望を構造化抽出→優先スコア算出→"
            "要望ボード管理（ステータス遷移）→ロードマップ連携"
        ),
    },
    "cancellation_pipeline": {
        "module": "workers.bpo.sales.cs.cancellation_pipeline",
        "function": "run_cancellation_pipeline",
        "industry": "sales",
        "position": "cs",
        "trigger": "cancellation_request_api / manual",
        "steps": 6,
        "description": (
            "解約申請受付（contracts→termination_requested + LLM理由分類） + "
            "データエクスポート（knowledge_items/execution_logs → ZIP → Supabase Storage + 署名付きURL）+ "
            "最終請求書発行（freee + 日割り計算）+ "
            "テナント無効化（customers→churned / contracts→terminated / churned_at記録）+ "
            "解約理由収集・学習（アンケートメール生成 + win_loss_patterns outcome=churned保存 + "
            "customer_health最終スコア記録）+ "
            "解約完了通知（顧客メール+エクスポートURL / Slack or logger社内通知）"
        ),
    },
}
