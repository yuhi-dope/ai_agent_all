"""workers/bpo/sales — 営業BPO パイプライン（ポジション別5部門 × 全12本）。

ポジション別ディレクトリ:
    marketing/  — マーケティングAI社員（アウトリーチ）
    sfa/        — SFA AI社員（リード→提案→見積→契約→同意）
    crm/        — CRM AI社員（ライフサイクル・売上/要望管理）
    cs/         — CS AI社員（サポート自動対応・アップセル・解約）
    learning/   — 学習AI社員（受注/失注フィードバック・CS品質学習）

後方互換:
    pipelines/  — 旧パスからの re-export レイヤー（実体ファイルはここに残存）

テンプレート:
    templates/quotation_template.html  — 見積書
    templates/contract_template.html   — 契約書
    templates/proposal_template.html   — 提案書
    templates/welcome_email.html       — ウェルカムメール
    templates/nurture_3day.html        — ナーチャリング（3日後）
    templates/nurture_7day.html        — ナーチャリング（7日後）
    templates/post_meeting.html        — 商談後フォロー
    templates/lost_survey.html         — 失注理由アンケート

PIPELINE_REGISTRY:
    marketing/outreach_pipeline              — マーケ⓪ 企業リサーチ＆アウトリーチ（400件/日自動）
    sfa/lead_qualification_pipeline          — SFA①  リードクオリフィケーション
    sfa/proposal_generation_pipeline         — SFA②  提案書AI生成・送付
    sfa/quotation_contract_pipeline          — SFA③  見積書・契約書自動送付
    sfa/consent_flow                         — SFA③b 電子同意フロー（CloudSign簡易代替）
    crm/customer_lifecycle_pipeline          — CRM④  顧客ライフサイクル管理
    crm/revenue_request_pipeline             — CRM⑤  売上・要望管理（MRR/ARR算出 + 要望管理ボード）
    cs/support_auto_response_pipeline        — CS⑥   サポート自動対応
    cs/upsell_briefing_pipeline              — CS⑦   アップセル支援
    cs/cancellation_pipeline                 — CRM⑩  解約フロー
    learning/win_loss_feedback_pipeline       — 学習⑧  受注/失注フィードバック学習
    learning/cs_feedback_pipeline             — 学習⑨  CS対応品質フィードバック学習
"""
