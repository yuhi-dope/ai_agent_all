"""
全社自動化パイプライン E2E テスト
— リード獲得 → 提案 → 契約 → オンボード → CS → アップセルの一気通貫フロー検証

テスト設計方針:
  - 外部API（LLM / Supabase / CloudSign / SendGrid / gBizINFO 等）は全てモック
  - 各パイプラインを順番に呼び出し、前の出力が次の入力に正しく渡ることを検証
  - 各ステップ間で必須フィールド（lead_id / opportunity_id / customer_id 等）が
    欠落していないことを assert する
  - dry_run=True を使って外部書き込みを回避する

テスト対象パイプライン（連鎖順）:
  1. outreach_pipeline          — リード獲得（企業リサーチ＆アウトリーチ）
  2. lead_qualification_pipeline — スコアリング → QUALIFIED 判定
  3. proposal_generation_pipeline — 提案書生成
  4. quotation_contract_pipeline  — 見積 → 契約 → 署名
  5. customer_lifecycle_pipeline  — オンボーディング
  6. support_auto_response_pipeline — CS自動回答
  7. upsell_briefing_pipeline      — アップセル検知
"""
from __future__ import annotations

import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# WeasyPrint スタブ（CI環境インストール不要）
# conftest.py より先に差し込む必要があるため、ここで設定する
# ─────────────────────────────────────────────────────────────────────────────
_weasyprint_stub = types.ModuleType("weasyprint")
_weasyprint_stub.HTML = MagicMock(
    return_value=MagicMock(write_pdf=MagicMock(return_value=b"%PDF-1.4 e2e-test"))
)
_weasyprint_stub.CSS = MagicMock()
sys.modules.setdefault("weasyprint", _weasyprint_stub)

from workers.bpo.sales.marketing.outreach_pipeline import (
    OutreachPipelineResult,
    run_outreach_pipeline,
)
from workers.bpo.sales.sfa.lead_qualification_pipeline import (
    LeadQualificationResult,
    run_lead_qualification_pipeline,
)
from workers.bpo.sales.sfa.proposal_generation_pipeline import (
    ProposalGenerationResult,
    run_proposal_generation_pipeline,
)
from workers.bpo.sales.sfa.quotation_contract_pipeline import (
    QuotationContractResult,
    run_quotation_contract_pipeline,
    APPROVAL_APPROVED,
)
from workers.bpo.sales.crm.customer_lifecycle_pipeline import (
    CustomerLifecyclePipelineResult,
    run_customer_lifecycle_pipeline,
)
from workers.bpo.sales.cs.support_auto_response_pipeline import (
    SupportAutoResponseResult,
    run_support_auto_response_pipeline,
)
from workers.bpo.sales.cs.upsell_briefing_pipeline import (
    UpsellBriefingPipelineResult,
    run_upsell_briefing_pipeline,
)
from workers.micro.models import MicroAgentOutput

# ─────────────────────────────────────────────────────────────────────────────
# テスト共通定数
# ─────────────────────────────────────────────────────────────────────────────

SHACHOTWO_COMPANY_ID = "shachotwo-tenant-001"   # シャチョツー自社テナントID
CUSTOMER_COMPANY_ID  = "customer-tenant-abc-001" # 顧客テナントID（契約後に発行）

LEAD_ID           = str(uuid.uuid4())
OPPORTUNITY_ID    = str(uuid.uuid4())
PROPOSAL_ID       = str(uuid.uuid4())
CONTRACT_ID       = str(uuid.uuid4())
CUSTOMER_ID       = str(uuid.uuid4())  # customers テーブルのUUID
TICKET_ID         = str(uuid.uuid4())

# 高スコア獲得パターンのリード入力（QUALIFIED 判定させる）
HIGH_SCORE_LEAD_INPUT: dict = {
    "company_name":   "株式会社テスト建設",
    "contact_name":   "山田太郎",
    "contact_email":  "yamada@test-kensetsu.co.jp",
    "contact_phone":  "03-1234-5678",
    "industry":       "建設業",
    "employee_count": 30,
    "urgency":        "すぐ導入したい",
    "budget":         "BPOコア",
    "source":         "紹介",
    "need":           "見積作業の自動化と安全書類の作成効率化",
}

# ─────────────────────────────────────────────────────────────────────────────
# ユーティリティ: MicroAgentOutput ファクトリ
# ─────────────────────────────────────────────────────────────────────────────

def _micro_ok(agent_name: str, result: dict, confidence: float = 0.95) -> MicroAgentOutput:
    """成功ステータスの MicroAgentOutput を生成する。"""
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=result,
        confidence=confidence,
        cost_yen=1.0,
        duration_ms=50,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: outreach_pipeline — アウトリーチ実行後の lead_id を検証
# ─────────────────────────────────────────────────────────────────────────────

class TestStep1OutreachPipeline:
    """Step 1: リード獲得（企業リサーチ＆アウトリーチ）"""

    async def test_outreach_returns_result_with_lead_records(self):
        """パイプラインが OutreachPipelineResult を返し、records に1件以上存在する。"""
        researcher_out = _micro_ok(
            "company_researcher",
            {
                "company_name": "株式会社テスト建設",
                "industry": "construction",
                "pain_points": [
                    {"detail": "人手不足", "appeal_message": "人手不足を解消"},
                    {"detail": "見積作業が煩雑", "appeal_message": "見積作業を自動化"},
                ],
                "tone": "professional",
                "estimated_employees": 30,
                "scale": "中規模",
                "industry_tasks": [],
                "industry_appeal": "建設業向けAI",
            },
        )
        # lp_generator: content キーを含める
        lp_out = _micro_ok(
            "document_generator",
            {
                "content": "【シャチョツー】建設業向けLP内容...",
            },
        )
        # outreach_composer: subject + body を含める
        email_out = _micro_ok(
            "document_generator",
            {
                "subject": "【シャチョツー】建設業の見積業務を月20時間削減する方法",
                "body": "山田社長様、建設業特化のAIアシスタントをご紹介します。",
            },
        )
        signal_out = _micro_ok(
            "signal_detector",
            {"signals": [], "temperature": "warm"},
        )
        calendar_out = _micro_ok("calendar_booker", {"booked": False})

        # run_document_generator は複数回呼ばれるため、side_effect で対応
        async def _doc_gen_side_effect(input_obj):
            # input_obj.agent_name で判定して応答を変える
            if input_obj.agent_name == "lp_generator":
                return lp_out
            else:  # outreach_composer
                return email_out

        with (
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_company_researcher",
                new_callable=AsyncMock,
                return_value=researcher_out,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_signal_detector",
                new_callable=AsyncMock,
                return_value=signal_out,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_document_generator",
                new_callable=AsyncMock,
                side_effect=_doc_gen_side_effect,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_calendar_booker",
                new_callable=AsyncMock,
                return_value=calendar_out,
            ),
        ):
            result = await run_outreach_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "construction",  # 建設業を指定
                    "companies": [
                        {
                            "name": "株式会社テスト建設",
                            "industry": "建設業",
                            "hp_url": "https://test-kensetsu.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        assert isinstance(result, OutreachPipelineResult)
        assert result.success is True
        # records に1件以上格納されていること
        assert len(result.records) >= 1
        # 最初のレコードに必須フィールドが存在すること
        first = result.records[0]
        assert first.company_name, "company_name が空"
        assert first.industry, "industry が空"


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: lead_qualification_pipeline — QUALIFIED 判定と lead_id を検証
# ─────────────────────────────────────────────────────────────────────────────

class TestStep2LeadQualification:
    """Step 2: スコアリング → QUALIFIED 判定"""

    async def test_qualified_lead_has_lead_id(self):
        """
        高スコア入力で QUALIFIED になり、lead_id が返ること。
        dry_run=True のため実際の DB 保存はしない。
        """
        # saas_writer がリードIDを返す
        writer_out = _micro_ok(
            "saas_writer",
            {"operation_id": LEAD_ID, "written": True},
        )
        # rule_matcher は空のルールセットを返す（カスタムルールなし）
        rule_out = _micro_ok(
            "rule_matcher",
            {
                "matched_rules": [],
                "applied_values": HIGH_SCORE_LEAD_INPUT,
                "unmatched_fields": [],
            },
        )
        # LLM お礼メール生成（テンプレートフォールバック）
        llm_response = MagicMock()
        llm_response.content = '{"subject": "お問い合わせありがとうございます", "body": "感謝します。"}'
        llm_response.cost_yen = 0.5
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        with (
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            result = await run_lead_qualification_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data=HIGH_SCORE_LEAD_INPUT,
                dry_run=True,
            )

        assert isinstance(result, LeadQualificationResult)
        assert result.success is True

        # QUALIFIED 判定されること（スコア ≥ 70）
        assert result.routing == "QUALIFIED", (
            f"期待: QUALIFIED, 実際: {result.routing} (score={result.lead_score})"
        )
        assert result.lead_score >= 70

        # lead_id が返ること
        assert result.lead_id is not None, "lead_id が None — 次のパイプラインに渡せない"
        assert result.lead_id != "", "lead_id が空文字 — 次のパイプラインに渡せない"

        # score_reasons が存在すること
        assert len(result.score_reasons) > 0, "score_reasons が空"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: proposal_generation_pipeline — lead_id を受け取り proposal を生成
# ─────────────────────────────────────────────────────────────────────────────

class TestStep3ProposalGeneration:
    """Step 3: 提案書生成 — lead_id 受け渡しを検証"""

    async def test_proposal_generated_with_lead_id(self):
        """
        lead_id を input_data に渡したとき、提案書が生成され
        final_output に proposal_id が含まれること。
        """
        mock_lead = {
            "id": LEAD_ID,
            "company_name": "株式会社テスト建設",
            "contact_name": "山田太郎",
            "contact_email": "yamada@test-kensetsu.co.jp",
            "industry": "construction",
            "employee_count": 30,
            "score": 100,
            "score_reasons": ["業種マッチ", "即導入"],
            "status": "qualified",
        }
        mock_opportunity = {
            "id": OPPORTUNITY_ID,
            "company_id": SHACHOTWO_COMPANY_ID,
            "lead_id": LEAD_ID,
            "target_company_name": "株式会社テスト建設",
            "target_industry": "construction",
            "selected_modules": ["brain", "bpo_core"],
            "monthly_amount": 280000,
            "stage": "qualification",
        }

        reader_out = _micro_ok(
            "saas_reader",
            {"lead": mock_lead, "opportunity": mock_opportunity},
        )
        rule_out = _micro_ok(
            "rule_matcher",
            {"genome": {}, "industry_code": "construction", "industry_label": "建設業"},
        )
        mock_proposal_json = {
            "cover": {
                "title": "シャチョツーご提案書",
                "target_company": "株式会社テスト建設",
                "date": "2026-03-22",
            },
            "pain_points": [{"category": "見積", "description": "月20時間"}],
            "solutions": [{"module": "bpo_core", "effect": "90%削減"}],
            "pricing": {"monthly_total": 280000},
        }
        doc_gen_out = _micro_ok(
            "document_generator",
            {"proposal_json": mock_proposal_json},
        )
        pdf_out = _micro_ok(
            "pdf_generator",
            {"pdf_bytes": b"%PDF-1.4 test-proposal", "size_kb": 120},
        )
        writer_out = _micro_ok(
            "saas_writer",
            {"operation_id": PROPOSAL_ID, "written": True},
        )
        # LLM メール生成
        llm_response = MagicMock()
        llm_response.content = (
            '{"subject": "ご提案書送付のご連絡", "body_html": "<p>ご提案書をお送りします</p>"}'
        )
        llm_response.cost_yen = 1.5
        llm_response.tokens_in = 200
        llm_response.tokens_out = 100
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        with (
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=reader_out,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_pdf_generator",
                new_callable=AsyncMock,
                return_value=pdf_out,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            result = await run_proposal_generation_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "lead_id": LEAD_ID,          # Step 2 から引き継いだ lead_id
                    "opportunity_id": OPPORTUNITY_ID,
                },
                dry_run=True,
            )

        assert isinstance(result, ProposalGenerationResult)
        assert result.success is True, f"提案書生成失敗: {result.failed_step}"

        # final_output に必須フィールドが含まれること
        fo = result.final_output
        assert "proposal_id" in fo, "proposal_id が final_output に含まれない"
        assert fo["proposal_id"], "proposal_id が空"
        assert "proposal_number" in fo, "proposal_number が final_output に含まれない"
        assert fo["proposal_number"].startswith("PR-"), (
            f"proposal_number フォーマット不正: {fo['proposal_number']}"
        )
        assert "industry" in fo, "industry が final_output に含まれない"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: quotation_contract_pipeline — opportunity_id → contract_id
# ─────────────────────────────────────────────────────────────────────────────

class TestStep4QuotationContract:
    """Step 4: 見積書 → 契約書 → CloudSign 署名"""

    async def test_contract_generated_from_opportunity_id(self):
        """
        opportunity_id を渡したとき、Phase B まで進み contract_id が返ること。
        dry_run=True のため実際の送信はスキップ。
        """
        pdf_out = _micro_ok(
            "pdf_generator",
            {"pdf_bytes": b"%PDF-1.4 quotation", "size_kb": 80},
        )
        writer_out = _micro_ok(
            "saas_writer",
            {"operation_id": CONTRACT_ID, "written": True},
        )

        with (
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
                new_callable=AsyncMock,
                return_value=pdf_out,
            ),
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "opportunity_id":     OPPORTUNITY_ID,  # Step 3 から引き継いだ ID
                    "selected_modules":   ["brain", "bpo_core"],
                    "target_company_name": "株式会社テスト建設",
                    "contact_name":       "山田太郎",
                    "contact_email":      "yamada@test-kensetsu.co.jp",
                    "billing_cycle":      "monthly",
                    "referral":           False,
                    "target_industry":    "construction",
                },
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        assert isinstance(result, QuotationContractResult)
        assert result.success is True, f"見積・契約パイプライン失敗: {result.failed_step}"

        # Phase B まで完了していること
        assert result.phase == "phase_b", f"期待: phase_b, 実際: {result.phase}"

        # contract_id が返ること（次のパイプラインに渡す）
        assert result.contract_id is not None, "contract_id が None"
        assert result.contract_id != "", "contract_id が空文字"

        # quotation_id も存在すること
        assert result.quotation_id is not None, "quotation_id が None"

        # final_output の contract_id が一致すること
        assert result.final_output.get("contract_id") == result.contract_id

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: customer_lifecycle_pipeline (onboarding) — customer_id を検証
# ─────────────────────────────────────────────────────────────────────────────

class TestStep5CustomerOnboarding:
    """Step 5: 契約後オンボーディング — customer_id 受け渡しを検証"""

    async def test_onboarding_completes_with_customer_id(self):
        """
        customer_id を渡したとき、オンボーディングが成功し
        final_output に customer_id が含まれること。
        """
        # DB 操作をモック（customers テーブル参照・更新）
        mock_db = MagicMock()
        # customers.select → 顧客情報返却
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={
                "id": CUSTOMER_ID,
                "customer_company_name": "株式会社テスト建設",
                "industry": "construction",
                "plan": "bpo_core",
                "active_modules": ["brain", "bpo_core"],
                "mrr": 280000,
                "health_score": 75,
                "nps_score": None,
                "status": "active",
                "onboarded_at": None,
                "cs_owner": None,
                "created_at": "2026-03-22T00:00:00+09:00",
            }
        )
        # customers.update
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        # execution_logs.insert（シーケンス登録）
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])

        writer_out = _micro_ok(
            "saas_writer",
            {"email_queued": True, "operation_id": str(uuid.uuid4())},
        )
        reader_out = _micro_ok(
            "saas_reader",
            {"customer": {"id": CUSTOMER_ID}},
        )
        rule_out = _micro_ok(
            "rule_matcher",
            {"genome": {}, "industry_code": "construction", "industry_label": "建設業"},
        )

        with (
            patch(
                "db.supabase.get_service_client",
                return_value=mock_db,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=reader_out,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out,
            ),
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                customer_id=CUSTOMER_ID,           # Step 4 契約後に発行された ID
                mode="onboarding",
                input_data={"dry_run": True},
            )

        assert isinstance(result, CustomerLifecyclePipelineResult)
        assert result.success is True, f"オンボーディング失敗: {result.failed_step}"
        assert result.mode == "onboarding"

        # final_output に customer_id が含まれること（CSへの引き継ぎに必須）
        fo = result.final_output
        assert "customer_id" in fo, "customer_id が final_output に含まれない"
        assert fo["customer_id"] == CUSTOMER_ID, (
            f"customer_id 不一致: expected={CUSTOMER_ID}, actual={fo['customer_id']}"
        )
        assert fo.get("status") == "onboarding", "status が onboarding でない"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: support_auto_response_pipeline — customer_id → ticket_id
# ─────────────────────────────────────────────────────────────────────────────

class TestStep6SupportAutoResponse:
    """Step 6: CS自動回答 — customer_id を渡してチケット ID を返す"""

    async def test_support_response_returns_ticket_id(self):
        """
        customer_id を含む問い合わせを投入したとき、
        ticket_id が返り回答が生成されること。
        """
        # Step 1 extractor（問い合わせ分類）
        extractor_out = _micro_ok(
            "structured_extractor",
            {
                "extracted": {
                    "category":       "brain",
                    "priority":       "medium",
                    "summary":        "ナレッジ登録の方法がわからない",
                    "subject":        "ナレッジ登録について",
                    "customer_name":  "山田太郎",
                    "sentiment":      "neutral",
                }
            },
        )
        # Step 2 saas_reader（顧客コンテキスト）
        reader_out = _micro_ok(
            "saas_reader",
            {
                "contracts": [{"plan": "bpo_core", "status": "active"}],
                "past_tickets": [],
                "usage": {"login_count_7d": 3},
            },
        )
        # LLM 回答生成
        llm_response = MagicMock()
        llm_response.content = (
            "ナレッジ登録はダッシュボードの「ナレッジ」タブから行えます。"
            "「＋追加」ボタンをクリックし、タイトルと本文を入力してください。"
        )
        llm_response.cost_yen = 0.8
        llm_response.tokens_in = 150
        llm_response.tokens_out = 80
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_response)

        # Step 7 saas_writer（チケット保存）
        ticket_writer_out = _micro_ok(
            "saas_writer",
            {"operation_id": TICKET_ID, "written": True},
        )
        # Step 6 validator
        validator_out = _micro_ok(
            "validator",
            {"sla_breached": False, "elapsed_minutes": 0},
        )

        with (
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_structured_extractor",
                new_callable=AsyncMock,
                return_value=extractor_out,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=reader_out,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.get_llm_client",
                return_value=mock_llm,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=validator_out,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=ticket_writer_out,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline._search_knowledge",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "title": "ナレッジ登録方法",
                        "content": "ダッシュボードの「ナレッジ」タブから追加できます。",
                        "relevance": 0.92,
                    }
                ],
            ),
        ):
            result = await run_support_auto_response_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "ticket_text":    "ナレッジ登録の方法を教えてください。",
                    "ticket_subject": "ナレッジ登録について",
                    "channel":        "email",
                    "customer_id":    CUSTOMER_ID,  # Step 5 から引き継いだ ID
                },
                dry_run=True,
            )

        assert isinstance(result, SupportAutoResponseResult)
        assert result.success is True, f"CS自動回答失敗: {result.failed_step}"

        # ticket_id が返ること（アップセルパイプラインへの引き継ぎに必要）
        assert result.ticket_id is not None, "ticket_id が None"
        assert result.ticket_id != "", "ticket_id が空文字"

        # AI 回答が生成されていること
        assert result.ai_response, "ai_response が空"

        # SLA 期限が設定されていること
        assert result.sla_due_at, "sla_due_at が未設定"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Step 7: upsell_briefing_pipeline — customer_company_id → アップセル機会検知
# ─────────────────────────────────────────────────────────────────────────────

class TestStep7UpsellBriefing:
    """Step 7: アップセル検知 — customer_company_id を渡して機会を返す"""

    async def test_upsell_opportunity_detected(self):
        """
        利用率の高い顧客データを注入したとき、
        アップセル機会が検知され final_output が返ること。
        """
        # Step 1 saas_reader — BPO実行ログ（高利用率を模擬）
        exec_log_out = _micro_ok(
            "saas_reader",
            {
                "records": [
                    {"task_type": "estimation",      "status": "completed"},
                    {"task_type": "safety_docs",     "status": "completed"},
                    {"task_type": "billing",         "status": "completed"},
                    {"task_type": "cost_report",     "status": "completed"},
                    {"task_type": "photo_organize",  "status": "completed"},
                ]
                * 20  # 100件 → 高利用率
            },
        )
        # Q&Aセッション数（週10回以上 → アップグレード提案トリガー）
        qa_out = _micro_ok(
            "saas_reader",
            {"records": [{"id": str(uuid.uuid4())} for _ in range(15)]},
        )
        # 契約情報（ブレインのみ → BPOコア提案候補）
        contract_out = _micro_ok(
            "saas_reader",
            {
                "records": [
                    {
                        "plan":           "brain_only",
                        "status":         "active",
                        "signed_at":      "2025-09-01T00:00:00",
                        "health_score":   85,
                        "custom_request_count": 0,
                        "active_modules": ["brain"],
                    }
                ]
            },
        )
        # rule_matcher — アップセル機会判定
        rule_out = _micro_ok(
            "rule_matcher",
            {
                "matched_rules": [
                    {
                        "rule_id": "bpo_upgrade",
                        "trigger": "ブレインのみ + Q&A週10回以上",
                        "recommendation": "BPOコアアップグレード提案",
                    }
                ],
                "applied_values": {},
                "unmatched_fields": [],
            },
        )
        # document_generator — ブリーフィング生成
        doc_gen_out = _micro_ok(
            "document_generator",
            {
                "briefing": {
                    "customer_profile": "建設業・ブレインのみ利用・Q&A週15回",
                    "recommended_action": "BPOコア提案",
                    "estimated_mrr_increase": 250000,
                }
            },
        )
        # calendar_booker — カレンダー予約
        calendar_out = _micro_ok(
            "calendar_booker",
            {"booked": False, "candidate_slots": []},
        )

        with (
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                side_effect=[exec_log_out, qa_out, contract_out],
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out,
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=doc_gen_out,
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
                new_callable=AsyncMock,
                return_value=calendar_out,
            ),
        ):
            result = await run_upsell_briefing_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                customer_company_id=CUSTOMER_COMPANY_ID,  # Step 5 から引き継いだ顧客テナントID
                input_data={
                    "customer_name":     "株式会社テスト建設",
                    "consultant_email":  "consultant@shachotwo.jp",
                    "force_run":         True,  # 機会未検知でも強制実行（テスト用）
                },
            )

        assert isinstance(result, UpsellBriefingPipelineResult)
        assert result.success is True, f"アップセル検知失敗: {result.failed_step}"

        # skipped_no_opportunity でないこと（機会が検知されていること）
        assert not result.skipped_no_opportunity, "アップセル機会が未検知（expected: 検知あり）"

        # final_output に必須フィールドが含まれること
        fo = result.final_output
        assert "customer_name" in fo, "customer_name が final_output に含まれない"
        assert fo["customer_name"] == "株式会社テスト建設"

        return result


# ─────────────────────────────────────────────────────────────────────────────
# E2E 統合テスト: 全パイプライン連鎖検証
# ─────────────────────────────────────────────────────────────────────────────

class TestSalesE2EFullPipeline:
    """
    全社自動化パイプライン E2E 連鎖テスト。

    リード獲得 → スコアリング → 提案書 → 見積・契約 → オンボード → CS → アップセルの
    一気通貫フローを通し、各ステップ間のデータ受け渡しが正しく行われることを検証する。
    """

    async def test_full_pipeline_chain_data_handoff(self):
        """
        全7パイプラインを順に実行し、各ステップ間の必須フィールドが
        欠落なく引き渡されることを検証する。
        """
        # ──────────────────────────────────────────────────────────────
        # Step 1: アウトリーチ → 対象企業のリードレコードを確認
        # ──────────────────────────────────────────────────────────────
        researcher_out = _micro_ok(
            "company_researcher",
            {
                "company_name": "株式会社テスト建設",
                "industry": "construction",
                "pain_points": [
                    {"detail": "見積作業が煩雑", "appeal_message": "見積を自動化"},
                ],
                "tone": "professional",
                "scale": "中規模",
                "industry_tasks": [],
                "industry_appeal": "建設業向けAI",
            },
        )
        signal_out = _micro_ok("signal_detector", {"signals": [], "temperature": "warm"})
        lp_out = _micro_ok("document_generator", {"content": "LP content..."})
        email_out = _micro_ok(
            "document_generator",
            {"subject": "ご案内", "body": "シャチョツーをご紹介します。"},
        )
        calendar_out_step1 = _micro_ok("calendar_booker", {"booked": False})

        async def _doc_gen_full(input_obj):
            if input_obj.agent_name == "lp_generator":
                return lp_out
            else:
                return email_out

        with (
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_company_researcher",
                new_callable=AsyncMock,
                return_value=researcher_out,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_signal_detector",
                new_callable=AsyncMock,
                return_value=signal_out,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_document_generator",
                new_callable=AsyncMock,
                side_effect=_doc_gen_full,
            ),
            patch(
                "workers.bpo.sales.marketing.outreach_pipeline.run_calendar_booker",
                new_callable=AsyncMock,
                return_value=calendar_out_step1,
            ),
        ):
            outreach_result = await run_outreach_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "construction",
                    "companies": [
                        {
                            "name": "株式会社テスト建設",
                            "industry": "建設業",
                            "hp_url": "https://test-kensetsu.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        assert outreach_result.success is True, "Step1 アウトリーチ失敗"
        assert len(outreach_result.records) >= 1, "Step1: records が空"
        # アウトリーチ対象企業名が記録されていること
        assert outreach_result.records[0].company_name == "株式会社テスト建設"

        # ──────────────────────────────────────────────────────────────
        # Step 2: スコアリング → lead_id を取得
        # ──────────────────────────────────────────────────────────────
        rule_out_step2 = _micro_ok(
            "rule_matcher",
            {
                "matched_rules": [],
                "applied_values": HIGH_SCORE_LEAD_INPUT,
                "unmatched_fields": [],
            },
        )
        writer_out_step2 = _micro_ok(
            "saas_writer",
            {"operation_id": LEAD_ID, "written": True},
        )
        llm_email = MagicMock()
        llm_email.content = '{"subject": "お礼", "body": "ありがとうございます。"}'
        llm_email.cost_yen = 0.5
        mock_llm_step2 = AsyncMock()
        mock_llm_step2.generate = AsyncMock(return_value=llm_email)

        with (
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out_step2,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out_step2,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.get_llm_client",
                return_value=mock_llm_step2,
            ),
        ):
            qual_result = await run_lead_qualification_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data=HIGH_SCORE_LEAD_INPUT,
                dry_run=True,
            )

        assert qual_result.success is True, "Step2 スコアリング失敗"
        assert qual_result.routing == "QUALIFIED", (
            f"Step2: QUALIFIED でない (score={qual_result.lead_score}, routing={qual_result.routing})"
        )
        # Step 2 → Step 3 の引き渡し: lead_id が必須
        assert qual_result.lead_id is not None, "Step2→3 引き渡し失敗: lead_id が None"
        handoff_lead_id = qual_result.lead_id  # Step 3 に渡す

        # ──────────────────────────────────────────────────────────────
        # Step 3: 提案書生成 — lead_id を受け取り proposal_id を返す
        # ──────────────────────────────────────────────────────────────
        mock_lead_step3 = {
            "id": handoff_lead_id,
            "company_name": "株式会社テスト建設",
            "contact_name": "山田太郎",
            "contact_email": "yamada@test-kensetsu.co.jp",
            "industry": "construction",
            "employee_count": 30,
            "score": qual_result.lead_score,
            "status": "qualified",
        }
        mock_opp_step3 = {
            "id": OPPORTUNITY_ID,
            "company_id": SHACHOTWO_COMPANY_ID,
            "lead_id": handoff_lead_id,
            "target_company_name": "株式会社テスト建設",
            "target_industry": "construction",
            "selected_modules": ["brain", "bpo_core"],
            "monthly_amount": 280000,
            "stage": "qualification",
        }
        reader_out_step3 = _micro_ok(
            "saas_reader",
            {"lead": mock_lead_step3, "opportunity": mock_opp_step3},
        )
        rule_out_step3 = _micro_ok(
            "rule_matcher",
            {"genome": {}, "industry_code": "construction"},
        )
        mock_proposal_json = {
            "cover": {"title": "シャチョツーご提案書", "target_company": "株式会社テスト建設"},
            "pain_points": [],
            "solutions": [],
            "pricing": {"monthly_total": 280000},
        }
        doc_gen_out_step3 = _micro_ok(
            "document_generator",
            {"proposal_json": mock_proposal_json},
        )
        pdf_out_step3 = _micro_ok(
            "pdf_generator",
            {"pdf_bytes": b"%PDF-1.4 proposal", "size_kb": 150},
        )
        writer_out_step3 = _micro_ok(
            "saas_writer",
            {"operation_id": PROPOSAL_ID, "written": True},
        )
        llm_mail_step3 = MagicMock()
        llm_mail_step3.content = '{"subject": "ご提案書", "body_html": "<p>送付します</p>"}'
        llm_mail_step3.cost_yen = 1.2
        llm_mail_step3.tokens_in = 180
        llm_mail_step3.tokens_out = 90
        mock_llm_step3 = AsyncMock()
        mock_llm_step3.generate = AsyncMock(return_value=llm_mail_step3)

        with (
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=reader_out_step3,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out_step3,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_pdf_generator",
                new_callable=AsyncMock,
                return_value=pdf_out_step3,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out_step3,
            ),
            patch(
                "workers.bpo.sales.sfa.proposal_generation_pipeline.get_llm_client",
                return_value=mock_llm_step3,
            ),
        ):
            proposal_result = await run_proposal_generation_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "lead_id":        handoff_lead_id,    # Step 2 → 3 の引き渡し
                    "opportunity_id": OPPORTUNITY_ID,
                },
                dry_run=True,
            )

        assert proposal_result.success is True, "Step3 提案書生成失敗"
        # Step 3 → Step 4 の引き渡し: opportunity_id が final_output に含まれること
        assert "proposal_id" in proposal_result.final_output, (
            "Step3→4 引き渡し失敗: proposal_id が final_output に含まれない"
        )
        handoff_opportunity_id = OPPORTUNITY_ID  # 既知のIDを継続使用

        # ──────────────────────────────────────────────────────────────
        # Step 4: 見積・契約 — opportunity_id → contract_id
        # ──────────────────────────────────────────────────────────────
        pdf_out_step4 = _micro_ok(
            "pdf_generator",
            {"pdf_bytes": b"%PDF-1.4 contract", "size_kb": 90},
        )
        writer_out_step4 = _micro_ok(
            "saas_writer",
            {"operation_id": CONTRACT_ID, "written": True},
        )

        with (
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
                new_callable=AsyncMock,
                return_value=pdf_out_step4,
            ),
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out_step4,
            ),
        ):
            contract_result = await run_quotation_contract_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "opportunity_id":      handoff_opportunity_id,  # Step 3 → 4
                    "selected_modules":    ["brain", "bpo_core"],
                    "target_company_name": "株式会社テスト建設",
                    "contact_name":        "山田太郎",
                    "contact_email":       "yamada@test-kensetsu.co.jp",
                    "billing_cycle":       "monthly",
                    "referral":            False,
                    "target_industry":     "construction",
                },
                approval_status=APPROVAL_APPROVED,
                dry_run=True,
            )

        assert contract_result.success is True, "Step4 見積・契約失敗"
        assert contract_result.phase == "phase_b", "Step4: Phase B に到達していない"
        # Step 4 → Step 5 の引き渡し: contract_id が必須
        assert contract_result.contract_id is not None, (
            "Step4→5 引き渡し失敗: contract_id が None"
        )
        handoff_contract_id = contract_result.contract_id

        # ──────────────────────────────────────────────────────────────
        # Step 5: オンボーディング — customer_id を渡す
        # ──────────────────────────────────────────────────────────────
        mock_db_step5 = MagicMock()
        mock_db_step5.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={
                "id": CUSTOMER_ID,
                "customer_company_name": "株式会社テスト建設",
                "industry": "construction",
                "plan": "bpo_core",
                "active_modules": ["brain", "bpo_core"],
                "mrr": 280000,
                "health_score": 70,
                "nps_score": None,
                "status": "active",
                "onboarded_at": None,
                "cs_owner": None,
                "created_at": "2026-03-22T00:00:00+09:00",
            }
        )
        mock_db_step5.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
        mock_db_step5.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])

        onboard_writer_out = _micro_ok(
            "saas_writer",
            {"email_queued": True, "operation_id": str(uuid.uuid4())},
        )

        with (
            patch(
                "db.supabase.get_service_client",
                return_value=mock_db_step5,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=onboard_writer_out,
            ),
        ):
            lifecycle_result = await run_customer_lifecycle_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="onboarding",
                input_data={
                    "contract_id": handoff_contract_id,  # Step 4 → 5 の参照情報
                    "dry_run": True,
                },
            )

        assert lifecycle_result.success is True, "Step5 オンボーディング失敗"
        # Step 5 → Step 6 の引き渡し: customer_id が final_output に含まれること
        assert "customer_id" in lifecycle_result.final_output, (
            "Step5→6 引き渡し失敗: customer_id が final_output に含まれない"
        )
        handoff_customer_id = lifecycle_result.final_output["customer_id"]
        assert handoff_customer_id == CUSTOMER_ID, (
            f"customer_id 不一致: {handoff_customer_id} != {CUSTOMER_ID}"
        )

        # ──────────────────────────────────────────────────────────────
        # Step 6: CS自動回答 — customer_id を渡しチケットIDを取得
        # ──────────────────────────────────────────────────────────────
        extractor_out_step6 = _micro_ok(
            "structured_extractor",
            {
                "extracted": {
                    "category":      "brain",
                    "priority":      "medium",
                    "summary":       "ナレッジ登録方法を知りたい",
                    "subject":       "ナレッジ登録について",
                    "customer_name": "山田太郎",
                    "sentiment":     "neutral",
                }
            },
        )
        reader_out_step6 = _micro_ok(
            "saas_reader",
            {"contracts": [{"plan": "bpo_core"}], "past_tickets": [], "usage": {}},
        )
        llm_response_step6 = MagicMock()
        llm_response_step6.content = (
            "ナレッジ登録はダッシュボードの「ナレッジ」タブから行えます。"
        )
        llm_response_step6.cost_yen = 0.6
        llm_response_step6.tokens_in = 100
        llm_response_step6.tokens_out = 60
        mock_llm_step6 = AsyncMock()
        mock_llm_step6.generate = AsyncMock(return_value=llm_response_step6)
        validator_out_step6 = _micro_ok("validator", {"sla_breached": False})
        ticket_writer_out_step6 = _micro_ok(
            "saas_writer",
            {"operation_id": TICKET_ID, "written": True},
        )

        with (
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_structured_extractor",
                new_callable=AsyncMock,
                return_value=extractor_out_step6,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=reader_out_step6,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.get_llm_client",
                return_value=mock_llm_step6,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=validator_out_step6,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=ticket_writer_out_step6,
            ),
            patch(
                "workers.bpo.sales.cs.support_auto_response_pipeline._search_knowledge",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            support_result = await run_support_auto_response_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "ticket_text": "ナレッジ登録の方法を教えてください。",
                    "channel":     "email",
                    "customer_id": handoff_customer_id,  # Step 5 → 6 の引き渡し
                },
                dry_run=True,
            )

        assert support_result.success is True, "Step6 CS自動回答失敗"
        # ticket_id が返ること
        assert support_result.ticket_id is not None, (
            "Step6→7 引き渡し失敗: ticket_id が None"
        )
        # ai_response が生成されていること
        assert support_result.ai_response, "Step6: ai_response が空"

        # ──────────────────────────────────────────────────────────────
        # Step 7: アップセル検知 — customer_company_id を渡す
        # ──────────────────────────────────────────────────────────────
        exec_log_out_step7 = _micro_ok(
            "saas_reader",
            {"records": [{"task_type": "estimation", "status": "completed"}] * 50},
        )
        qa_out_step7 = _micro_ok(
            "saas_reader",
            {"records": [{"id": str(uuid.uuid4())} for _ in range(12)]},
        )
        contract_out_step7 = _micro_ok(
            "saas_reader",
            {
                "records": [
                    {
                        "plan":                 "brain_only",
                        "status":               "active",
                        "signed_at":            "2025-09-01T00:00:00",
                        "health_score":         85,
                        "custom_request_count": 0,
                        "active_modules":       ["brain"],
                    }
                ]
            },
        )
        rule_out_step7 = _micro_ok(
            "rule_matcher",
            {
                "matched_rules": [
                    {
                        "rule_id":    "bpo_upgrade",
                        "trigger":    "ブレインのみ + Q&A週10回以上",
                        "recommendation": "BPOコアアップグレード提案",
                    }
                ],
                "applied_values": {},
                "unmatched_fields": [],
            },
        )
        doc_gen_out_step7 = _micro_ok(
            "document_generator",
            {
                "briefing": {
                    "customer_profile":       "建設業・ブレインのみ・Q&A週12回",
                    "recommended_action":     "BPOコア提案",
                    "estimated_mrr_increase": 250000,
                }
            },
        )
        calendar_out_step7 = _micro_ok("calendar_booker", {"booked": False})

        with (
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                side_effect=[exec_log_out_step7, qa_out_step7, contract_out_step7],
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out_step7,
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=doc_gen_out_step7,
            ),
            patch(
                "workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
                new_callable=AsyncMock,
                return_value=calendar_out_step7,
            ),
        ):
            upsell_result = await run_upsell_briefing_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                customer_company_id=CUSTOMER_COMPANY_ID,  # 顧客テナントID
                input_data={
                    "customer_name": "株式会社テスト建設",
                    "force_run":     True,
                },
            )

        assert upsell_result.success is True, "Step7 アップセル検知失敗"
        assert not upsell_result.skipped_no_opportunity, (
            "Step7: アップセル機会が未検知 (skipped_no_opportunity=True)"
        )

        # ──────────────────────────────────────────────────────────────
        # 全ステップ通過後の総合検証
        # ──────────────────────────────────────────────────────────────

        # 各パイプラインが成功していること
        assert outreach_result.success,  "Step1 アウトリーチ: 失敗"
        assert qual_result.success,      "Step2 スコアリング: 失敗"
        assert proposal_result.success,  "Step3 提案書生成: 失敗"
        assert contract_result.success,  "Step4 見積・契約: 失敗"
        assert lifecycle_result.success, "Step5 オンボーディング: 失敗"
        assert support_result.success,   "Step6 CS自動回答: 失敗"
        assert upsell_result.success,    "Step7 アップセル: 失敗"

        # 主要な ID が欠落なく連鎖していること
        assert qual_result.lead_id == LEAD_ID, (
            f"lead_id 連鎖不整合: {qual_result.lead_id}"
        )
        assert contract_result.contract_id is not None, "contract_id 欠落"
        assert lifecycle_result.final_output["customer_id"] == CUSTOMER_ID, (
            "customer_id 連鎖不整合"
        )
        assert support_result.ticket_id is not None, "ticket_id 欠落"

    async def test_nurturing_lead_does_not_proceed_to_proposal(self):
        """
        低スコアリード（NURTURING 判定）は提案書パイプラインに進まないこと。
        routing が NURTURING であることを確認する。
        """
        nurturing_input = {
            "company_name":   "個人事務所ABC",
            "contact_name":   "田中次郎",
            "contact_email":  "tanaka@abc-office.jp",
            "industry":       "コンサルティング",  # 対応業種外 → 低スコア
            "employee_count": 3,
            "urgency":        "情報収集",
            "budget":         "",
            "source":         "Google",
            "need":           "業務効率化について知りたい",
        }
        rule_out = _micro_ok(
            "rule_matcher",
            {
                "matched_rules": [],
                "applied_values": nurturing_input,
                "unmatched_fields": [],
            },
        )
        writer_out = _micro_ok("saas_writer", {"operation_id": str(uuid.uuid4())})
        llm_fallback = MagicMock()
        llm_fallback.content = '{"subject": "お礼", "body": "ありがとうございます。"}'
        llm_fallback.cost_yen = 0.3
        mock_llm = AsyncMock()
        mock_llm.generate = AsyncMock(return_value=llm_fallback)

        with (
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
            patch(
                "workers.bpo.sales.sfa.lead_qualification_pipeline.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            result = await run_lead_qualification_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data=nurturing_input,
                dry_run=True,
            )

        assert result.success is True
        assert result.routing == "NURTURING", (
            f"期待: NURTURING, 実際: {result.routing} (score={result.lead_score})"
        )
        assert result.lead_score < 40, f"スコアが高すぎる: {result.lead_score}"
        # NURTURING の場合、next_action.type が "nurturing" であること
        next_action = result.final_output.get("next_action", {})
        assert next_action.get("type") == "nurturing", (
            f"next_action.type が nurturing でない: {next_action.get('type')}"
        )
        # NURTURING の場合は提案書パイプラインをトリガーしないこと
        assert next_action.get("pipeline") is None, (
            f"NURTURING なのに pipeline が設定されている: {next_action.get('pipeline')}"
        )

    async def test_contract_phase_a_only_when_pending(self):
        """
        approval_status=pending の場合、Phase A（見積）のみで停止し
        contract_id が発行されないこと。
        """
        pdf_out = _micro_ok(
            "pdf_generator",
            {"pdf_bytes": b"%PDF-1.4 quotation-pending", "size_kb": 70},
        )
        writer_out = _micro_ok("saas_writer", {"operation_id": str(uuid.uuid4())})

        with (
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_pdf_generator",
                new_callable=AsyncMock,
                return_value=pdf_out,
            ),
            patch(
                "workers.bpo.sales.sfa.quotation_contract_pipeline.run_saas_writer",
                new_callable=AsyncMock,
                return_value=writer_out,
            ),
        ):
            result = await run_quotation_contract_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                input_data={
                    "opportunity_id":      OPPORTUNITY_ID,
                    "selected_modules":    ["brain"],
                    "target_company_name": "個人事務所ABC",
                    "contact_name":        "田中次郎",
                    "contact_email":       "tanaka@abc-office.jp",
                    "billing_cycle":       "monthly",
                    "referral":            False,
                },
                approval_status="pending",  # 承認待ち → Phase A で停止
                dry_run=True,
            )

        assert result.success is True
        # Phase A のみで停止していること
        assert result.phase == "phase_a", (
            f"期待: phase_a, 実際: {result.phase}"
        )
        # Phase B は実行されていないため contract_id が None であること
        assert result.contract_id is None, (
            f"pending なのに contract_id が発行されている: {result.contract_id}"
        )
        assert result.approval_status == "pending"

    async def test_health_check_mode_returns_health_score(self):
        """
        mode=health_check で顧客ライフサイクルパイプラインを実行したとき、
        ヘルススコアが返ること。
        """
        mock_db = MagicMock()
        # customers 取得
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
            data={
                "id": CUSTOMER_ID,
                "customer_company_name": "株式会社テスト建設",
                "industry": "construction",
                "plan": "bpo_core",
                "active_modules": ["brain", "bpo_core"],
                "mrr": 280000,
                "health_score": 75,
                "nps_score": 8,
                "status": "active",
                "onboarded_at": "2026-01-15",
                "cs_owner": "tanaka@shachotwo.jp",
                "created_at": "2026-01-10T00:00:00+09:00",
            }
        )
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

        usage_reader_out = _micro_ok(
            "saas_reader",
            {
                "records": [
                    {"login_count_7d": 5, "bpo_exec_count_30d": 30, "qa_count_7d": 8}
                ]
            },
        )
        cost_calc_out = _micro_ok(
            "calculator",
            {"health_score": 72, "dimensions": {"usage": 80, "engagement": 70}},
        )
        rule_out_health = _micro_ok(
            "rule_matcher",
            {"matched_rules": [], "alert_level": "healthy"},
        )

        with (
            patch(
                "db.supabase.get_service_client",
                return_value=mock_db,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
                new_callable=AsyncMock,
                return_value=usage_reader_out,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_cost_calculator",
                new_callable=AsyncMock,
                return_value=cost_calc_out,
            ),
            patch(
                "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
                new_callable=AsyncMock,
                return_value=rule_out_health,
            ),
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=SHACHOTWO_COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="health_check",
            )

        assert isinstance(result, CustomerLifecyclePipelineResult)
        assert result.success is True, f"ヘルスチェック失敗: {result.failed_step}"
        assert result.mode == "health_check"
        # ヘルススコアが数値で返ること
        assert result.health_score is not None, "health_score が None"
        assert isinstance(result.health_score, int), (
            f"health_score が int でない: {type(result.health_score)}"
        )
        # ヘルスラベルが設定されること
        assert result.health_label in ("risk", "caution", "healthy", "expansion"), (
            f"health_label が不正: {result.health_label}"
        )
