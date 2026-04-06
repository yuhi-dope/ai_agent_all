"""
CS⑦ アップセル支援パイプライン — コンサルへのブリーフィング テスト

対象: workers/bpo/sales/cs/upsell_briefing_pipeline.py

テスト方針:
- 外部依存（Supabase / Gemini LLM / Slack / Google Calendar）は全てモック
- _evaluate_upsell_opportunities の4パターン判定を単体テスト
- パイプライン全ステップの成功フローを統合テスト
- 機会未検知時のスキップ完了フローを確認
- Step 4（Slack）・Step 5（カレンダー）の非クリティカル失敗継続を確認
- record_upsell_outcome のフィードバック記録テスト
- /upsell/outcome エンドポイントのテスト
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.sales.cs.upsell_briefing_pipeline import (
    UPSELL_BPO_UTILIZATION_THRESHOLD,
    UPSELL_QA_WEEKLY_THRESHOLD,
    UPSELL_HEALTH_SCORE_THRESHOLD,
    UPSELL_CONTRACT_MONTHS_THRESHOLD,
    UPSELL_CUSTOM_REQUESTS_THRESHOLD,
    PRICING,
    ALL_BPO_MODULES,
    UpsellBriefingPipelineResult,
    UpsellOpportunity,
    _evaluate_upsell_opportunities,
    _build_slack_message,
    run_upsell_briefing_pipeline,
    record_upsell_outcome,
)
from workers.micro.models import MicroAgentOutput

# ─── テスト定数 ────────────────────────────────────────────────────────────────

COMPANY_ID = "test-shachotwo-company"
CUSTOMER_ID = "test-customer-construction-001"
CUSTOMER_NAME = "テスト建設株式会社"


# ─── _evaluate_upsell_opportunities 単体テスト ────────────────────────────────

class TestEvaluateUpsellOpportunities:

    def test_pattern1_add_module_high_utilization(self):
        """パターン1: BPOコア利用率80%以上 + 未使用モジュールあり → 追加モジュール提案"""
        usage = {
            "bpo_utilization_rate": 0.85,
            "active_modules": ["estimation", "safety_docs", "billing"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 5.0,
            "health_score": 70.0,
            "contract_months": 3,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)

        assert len(opportunities) >= 1
        opp = next(o for o in opportunities if o.trigger_type == "add_module")
        assert opp.trigger_type == "add_module"
        assert opp.urgency == "medium"  # 85%: high閾値(90%)未満
        assert opp.estimated_mrr_increase > 0
        assert len(opp.recommended_modules) <= 3

    def test_pattern1_urgency_high_above_90pct(self):
        """パターン1: 利用率90%以上のとき urgency=high"""
        usage = {
            "bpo_utilization_rate": 0.91,
            "active_modules": ["estimation", "safety_docs", "billing", "cost_report"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 3.0,
            "health_score": 60.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        opp = next((o for o in opportunities if o.trigger_type == "add_module"), None)
        assert opp is not None
        assert opp.urgency == "high"

    def test_pattern1_no_trigger_below_threshold(self):
        """パターン1: 利用率79%ではトリガーしない"""
        usage = {
            "bpo_utilization_rate": 0.79,
            "active_modules": ["estimation"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 5.0,
            "health_score": 60.0,
            "contract_months": 3,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        assert not any(o.trigger_type == "add_module" for o in opportunities)

    def test_pattern2_bpo_upgrade(self):
        """パターン2: ブレインのみ + Q&A週10回以上 → BPOコアアップグレード提案"""
        usage = {
            "bpo_utilization_rate": 0.0,
            "active_modules": [],
            "has_bpo_core": False,
            "has_brain_only": True,
            "qa_weekly_avg": 12.0,
            "health_score": 70.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)

        assert len(opportunities) == 1
        opp = opportunities[0]
        assert opp.trigger_type == "bpo_upgrade"
        assert opp.estimated_mrr_increase == PRICING["bpo_core"]
        assert opp.urgency == "medium"

    def test_pattern2_high_urgency_above_20(self):
        """パターン2: Q&A週20回以上のとき urgency=high"""
        usage = {
            "bpo_utilization_rate": 0.0,
            "active_modules": [],
            "has_bpo_core": False,
            "has_brain_only": True,
            "qa_weekly_avg": 21.0,
            "health_score": 70.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        opp = next((o for o in opportunities if o.trigger_type == "bpo_upgrade"), None)
        assert opp is not None
        assert opp.urgency == "high"

    def test_pattern2_no_trigger_qa_below_threshold(self):
        """パターン2: Q&A週9回ではトリガーしない"""
        usage = {
            "bpo_utilization_rate": 0.0,
            "active_modules": [],
            "has_bpo_core": False,
            "has_brain_only": True,
            "qa_weekly_avg": 9.0,
            "health_score": 70.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        assert not any(o.trigger_type == "bpo_upgrade" for o in opportunities)

    def test_pattern3_backoffice_bpo(self):
        """パターン3: health≥80 + 6ヶ月経過 → バックオフィスBPO提案"""
        usage = {
            "bpo_utilization_rate": 0.5,
            "active_modules": ["estimation"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 5.0,
            "health_score": 82.0,
            "contract_months": 7,
            "custom_request_count": 1,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)

        opp = next((o for o in opportunities if o.trigger_type == "backoffice"), None)
        assert opp is not None
        assert opp.estimated_mrr_increase == PRICING["backoffice_bpo"]
        assert opp.urgency == "medium"

    def test_pattern3_no_trigger_has_backoffice(self):
        """パターン3: すでにバックオフィスBPO導入済みならトリガーしない"""
        usage = {
            "bpo_utilization_rate": 0.5,
            "active_modules": ["estimation"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 5.0,
            "health_score": 85.0,
            "contract_months": 8,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": True,  # 導入済み
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        assert not any(o.trigger_type == "backoffice" for o in opportunities)

    def test_pattern4_custom_dev(self):
        """パターン4: 全BPO利用中 + カスタム要望3件以上 → 自社開発BPO提案"""
        usage = {
            "bpo_utilization_rate": 1.0,
            "active_modules": sorted(ALL_BPO_MODULES),
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 8.0,
            "health_score": 90.0,
            "contract_months": 12,
            "custom_request_count": 4,
            "has_all_bpo": True,
            "has_backoffice_bpo": True,
        }
        opportunities = _evaluate_upsell_opportunities(usage)

        opp = next((o for o in opportunities if o.trigger_type == "custom_dev"), None)
        assert opp is not None
        assert opp.estimated_mrr_increase == 0  # 別途見積
        assert opp.urgency == "high"

    def test_no_opportunity_low_usage(self):
        """利用率低・Q&A少・健全度低・期間短 → 機会なし"""
        usage = {
            "bpo_utilization_rate": 0.30,
            "active_modules": ["estimation"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 2.0,
            "health_score": 50.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        assert len(opportunities) == 0

    def test_multiple_opportunities(self):
        """複数パターンが同時にトリガーされる場合、全て返す"""
        usage = {
            "bpo_utilization_rate": 0.85,
            "active_modules": ["estimation", "safety_docs", "billing"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 5.0,
            "health_score": 88.0,
            "contract_months": 8,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        opportunities = _evaluate_upsell_opportunities(usage)
        # パターン1（add_module）+ パターン3（backoffice）の2件
        assert len(opportunities) == 2
        trigger_types = {o.trigger_type for o in opportunities}
        assert "add_module" in trigger_types
        assert "backoffice" in trigger_types


# ─── _build_slack_message 単体テスト ─────────────────────────────────────────

class TestBuildSlackMessage:

    def test_message_contains_customer_name(self):
        """Slackメッセージに顧客名が含まれる"""
        opps = [
            UpsellOpportunity(
                trigger_type="add_module",
                title="追加モジュール提案タイミング",
                reason="BPOコア利用率85%到達",
                urgency="medium",
                estimated_mrr_increase=100_000,
                recommended_modules=["safety_docs"],
            )
        ]
        msg = _build_slack_message("テスト建設株式会社", opps, "/upsell/test-id")
        assert "テスト建設株式会社" in msg
        assert "100,000" in msg
        assert "/upsell/test-id" in msg

    def test_message_custom_dev_shows_separate_estimate(self):
        """自社開発BPO（mrr=0）は「別途見積」と表示"""
        opps = [
            UpsellOpportunity(
                trigger_type="custom_dev",
                title="自社開発BPO提案",
                reason="全BPO利用中、カスタム要望4件",
                urgency="high",
                estimated_mrr_increase=0,
                recommended_modules=["custom_bpo"],
            )
        ]
        msg = _build_slack_message("テスト製造株式会社", opps, "/upsell/mfg-id")
        assert "別途見積" in msg

    def test_message_multiple_opportunities(self):
        """機会が複数ある場合「他N件」が表示される"""
        opps = [
            UpsellOpportunity(
                trigger_type="add_module",
                title="追加モジュール提案",
                reason="利用率85%",
                urgency="medium",
                estimated_mrr_increase=100_000,
            ),
            UpsellOpportunity(
                trigger_type="backoffice",
                title="バックオフィスBPO提案",
                reason="6ヶ月経過",
                urgency="medium",
                estimated_mrr_increase=200_000,
            ),
        ]
        msg = _build_slack_message("テスト歯科", opps, "/upsell/dental-id")
        assert "他1件" in msg


# ─── パイプライン統合テスト ────────────────────────────────────────────────────

# saas_readerのモックレスポンス
_MOCK_SAAS_OUT_SUCCESS = MicroAgentOutput(
    agent_name="saas_reader",
    success=True,
    result={"data": [], "count": 0, "service": "supabase", "mock": True},
    confidence=0.5,
    cost_yen=0.0,
    duration_ms=5,
)

_MOCK_RULE_OUT = MicroAgentOutput(
    agent_name="rule_matcher",
    success=True,
    result={
        "matched_rules": [],
        "applied_values": {},
        "unmatched_fields": [],
    },
    confidence=0.5,
    cost_yen=0.0,
    duration_ms=3,
)

_MOCK_GEN_OUT = MicroAgentOutput(
    agent_name="document_generator",
    success=True,
    result={
        "content": "# テスト建設株式会社 アップセルブリーフィング\n\n## 推奨アクション\n追加モジュール導入を提案",
        "format": "markdown",
        "char_count": 60,
    },
    confidence=0.9,
    cost_yen=5.0,
    duration_ms=800,
)

_MOCK_SLOTS_OUT = MicroAgentOutput(
    agent_name="calendar_booker",
    success=True,
    result={
        "slots": [
            {"id": "slot1", "start": "2026-03-25T10:00:00", "end": "2026-03-25T10:30:00",
             "date_label": "3/25（水）", "time_label": "10:00〜10:30"},
            {"id": "slot2", "start": "2026-03-25T13:00:00", "end": "2026-03-25T13:30:00",
             "date_label": "3/25（水）", "time_label": "13:00〜13:30"},
            {"id": "slot3", "start": "2026-03-26T10:00:00", "end": "2026-03-26T10:30:00",
             "date_label": "3/26（木）", "time_label": "10:00〜10:30"},
        ]
    },
    confidence=1.0,
    cost_yen=0.0,
    duration_ms=200,
)

_MOCK_BLOCK_OUT = MicroAgentOutput(
    agent_name="calendar_booker",
    success=True,
    result={
        "meeting": {
            "calendar_event_id": "evt-abc123",
            "meet_url": "https://meet.google.com/test-abc",
            "title": "[提案準備] テスト建設株式会社",
        }
    },
    confidence=1.0,
    cost_yen=0.0,
    duration_ms=150,
)


def _make_input_data_with_opportunity() -> dict:
    """拡張タイミングが検知されるinput_dataを生成する"""
    return {
        "customer_name": CUSTOMER_NAME,
        "bpo_utilization_rate": 0.85,  # パターン1トリガー条件
        "has_bpo_core": True,
        "has_brain_only": False,
        "has_all_bpo": False,
        "has_backoffice_bpo": False,
        "health_score": 70.0,
        "contract_months": 3,
        "current_mrr": 280_000,
        "briefing_base_url": "https://app.shachotwo.com",
    }


def _make_input_data_no_opportunity() -> dict:
    """拡張タイミングが検知されないinput_dataを生成する"""
    return {
        "customer_name": CUSTOMER_NAME,
        "bpo_utilization_rate": 0.30,
        "has_bpo_core": True,
        "has_brain_only": False,
        "has_all_bpo": False,
        "has_backoffice_bpo": False,
        "health_score": 50.0,
        "contract_months": 2,
        "current_mrr": 280_000,
    }


@pytest.mark.asyncio
async def test_pipeline_full_success():
    """全ステップ正常完了フロー: 5ステップを経て UpsellBriefingPipelineResult が返る"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)) as mock_saas,
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert isinstance(result, UpsellBriefingPipelineResult)
    assert result.success is True
    assert result.skipped_no_opportunity is False
    assert len(result.steps) == 5
    assert len(result.opportunities) >= 1
    assert result.final_output["customer_name"] == CUSTOMER_NAME
    assert "briefing_content" in result.final_output
    assert result.final_output["total_estimated_mrr_increase"] > 0


@pytest.mark.asyncio
async def test_pipeline_skips_when_no_opportunity():
    """拡張タイミング未到達: Step2完了後にスキップ完了する"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_no_opportunity(),
        )

    assert result.success is True
    assert result.skipped_no_opportunity is True
    assert len(result.opportunities) == 0
    # Step1+Step2 のみ実行（Step3〜5はスキップ）
    assert len(result.steps) == 2


@pytest.mark.asyncio
async def test_pipeline_force_run_no_opportunity():
    """force_run=True: 機会未検知でも全ステップ実行する"""
    input_data = {**_make_input_data_no_opportunity(), "force_run": True}
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=input_data,
        )

    assert result.success is True
    assert result.skipped_no_opportunity is False
    assert len(result.steps) == 5


@pytest.mark.asyncio
async def test_pipeline_step1_failure_returns_fail():
    """Step1（saas_reader）失敗時は即座に failed_step=saas_reader で返す"""
    fail_out = MicroAgentOutput(
        agent_name="saas_reader",
        success=False,
        result={"error": "Supabase connection failed"},
        confidence=0.0, cost_yen=0.0, duration_ms=10,
    )
    with patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
               new=AsyncMock(return_value=fail_out)):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert result.success is False
    assert result.failed_step == "saas_reader"
    assert len(result.steps) == 1


@pytest.mark.asyncio
async def test_pipeline_step3_generator_failure_returns_fail():
    """Step3（generator）失敗時は failed_step=generator で返す"""
    gen_fail_out = MicroAgentOutput(
        agent_name="document_generator",
        success=False,
        result={"error": "LLM timeout"},
        confidence=0.0, cost_yen=0.0, duration_ms=30_000,
    )
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=gen_fail_out)),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert result.success is False
    assert result.failed_step == "generator"
    assert len(result.steps) == 3


@pytest.mark.asyncio
async def test_pipeline_slack_failure_is_non_critical():
    """Step4（Slack）が例外を投げてもパイプライン全体はsuccess=Trueで完了する"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        # get_connector は使われない（credentials未設定のためdry-run経路になる）
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert result.success is True
    slack_step = next(s for s in result.steps if s.step_name == "message")
    # dry-run経路なので sent=False だが success=True
    assert slack_step.success is True
    assert result.final_output["slack_message"] != ""


@pytest.mark.asyncio
async def test_pipeline_calendar_failure_is_non_critical():
    """Step5（calendar_booker）が例外を投げてもパイプライン全体はsuccess=Trueで完了する"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=Exception("Google API unavailable"))),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert result.success is True
    assert len(result.steps) == 5
    cal_step = next(s for s in result.steps if s.step_name == "calendar_booker")
    assert cal_step.success is True  # ノンクリティカル扱い


@pytest.mark.asyncio
async def test_pipeline_summary_output():
    """summary() メソッドが主要情報を含む文字列を返す"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    summary = result.summary()
    assert "アップセル支援パイプライン" in summary
    assert "ステップ" in summary
    assert "機会件数" in summary


@pytest.mark.asyncio
async def test_pipeline_skip_summary():
    """機会未検知時のsummary()がスキップ完了の文言を含む"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_no_opportunity(),
        )

    summary = result.summary()
    assert "スキップ" in summary


@pytest.mark.asyncio
async def test_pipeline_cost_accumulation():
    """各ステップのコストが total_cost_yen に正しく集計される"""
    gen_out_with_cost = MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"content": "ブリーフィング", "format": "markdown", "char_count": 10},
        confidence=0.9,
        cost_yen=12.5,
        duration_ms=600,
    )
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=gen_out_with_cost)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    # Step3のコスト12.5円が集計されている
    assert result.total_cost_yen == pytest.approx(12.5)
    assert result.total_duration_ms >= 0


@pytest.mark.asyncio
async def test_pipeline_briefing_url_contains_customer_id():
    """ブリーフィングURLに customer_company_id が含まれる"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data={
                **_make_input_data_with_opportunity(),
                "briefing_base_url": "https://app.shachotwo.com",
            },
        )

    assert CUSTOMER_ID in result.final_output["briefing_url"]
    assert result.final_output["briefing_url"].startswith("https://app.shachotwo.com")


# ─── upsell_opportunity_id テスト ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_final_output_contains_upsell_opportunity_id():
    """パイプライン成功時に final_output に upsell_opportunity_id が含まれる"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert "upsell_opportunity_id" in result.final_output
    opp_id = result.final_output["upsell_opportunity_id"]
    assert isinstance(opp_id, str)
    assert len(opp_id) > 0


# ─── DB重みによる閾値調整テスト ────────────────────────────────────────────────

class TestEvaluateUpsellOpportunitiesWithWeights:

    def test_lower_threshold_triggers_pattern1_earlier(self):
        """閾値を下げると75%でもパターン1がトリガーされる"""
        usage = {
            "bpo_utilization_rate": 0.75,  # デフォルト閾値(80%)未満
            "active_modules": ["estimation", "safety_docs"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 3.0,
            "health_score": 60.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        # デフォルト閾値ではトリガーしない
        default_opps = _evaluate_upsell_opportunities(usage)
        assert not any(o.trigger_type == "add_module" for o in default_opps)

        # 閾値を下げるとトリガーする（accepted が5件蓄積した場合: 0.80 - 5*0.01 = 0.75）
        adjusted_opps = _evaluate_upsell_opportunities(usage, bpo_utilization_threshold=0.75)
        assert any(o.trigger_type == "add_module" for o in adjusted_opps)

    def test_higher_threshold_suppresses_pattern1(self):
        """閾値を上げると85%でもパターン1がトリガーされなくなる"""
        usage = {
            "bpo_utilization_rate": 0.83,  # デフォルト閾値(80%)超
            "active_modules": ["estimation"],
            "has_bpo_core": True,
            "has_brain_only": False,
            "qa_weekly_avg": 3.0,
            "health_score": 60.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        # デフォルト閾値ではトリガーする
        default_opps = _evaluate_upsell_opportunities(usage)
        assert any(o.trigger_type == "add_module" for o in default_opps)

        # 閾値を上げるとトリガーしない（rejected が3件蓄積した場合: 0.80 + 3*0.01 = 0.83）
        adjusted_opps = _evaluate_upsell_opportunities(usage, bpo_utilization_threshold=0.85)
        assert not any(o.trigger_type == "add_module" for o in adjusted_opps)

    def test_lower_qa_threshold_triggers_pattern2_earlier(self):
        """Q&A閾値を下げると7回でもパターン2がトリガーされる"""
        usage = {
            "bpo_utilization_rate": 0.0,
            "active_modules": [],
            "has_bpo_core": False,
            "has_brain_only": True,
            "qa_weekly_avg": 7.0,  # デフォルト閾値(10回)未満
            "health_score": 60.0,
            "contract_months": 2,
            "custom_request_count": 0,
            "has_all_bpo": False,
            "has_backoffice_bpo": False,
        }
        default_opps = _evaluate_upsell_opportunities(usage)
        assert not any(o.trigger_type == "bpo_upgrade" for o in default_opps)

        adjusted_opps = _evaluate_upsell_opportunities(usage, qa_weekly_threshold=7.0)
        assert any(o.trigger_type == "bpo_upgrade" for o in adjusted_opps)


# ─── record_upsell_outcome テスト ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_upsell_outcome_accepted_increases_weight():
    """accepted 時に同パターンの重みが +5 増加する"""
    mock_db = MagicMock()
    # 既存モデルあり（version=1, weight=0）
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "model-001", "weights": {"additional_module": 0}, "version": 1}
    ]
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch(
        "workers.bpo.sales.cs.upsell_briefing_pipeline.get_service_client",
        return_value=mock_db,
    ):
        result = await record_upsell_outcome(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            opportunity_type="additional_module",
            outcome="accepted",
        )

    assert result["success"] is True
    assert result["new_weights"]["additional_module"] == 5  # 0 + 5


@pytest.mark.asyncio
async def test_record_upsell_outcome_rejected_decreases_weight():
    """rejected 時に同パターンの重みが -3 減少する"""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "model-002", "weights": {"upgrade_to_bpo": 5}, "version": 2}
    ]
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch(
        "workers.bpo.sales.cs.upsell_briefing_pipeline.get_service_client",
        return_value=mock_db,
    ):
        result = await record_upsell_outcome(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            opportunity_type="upgrade_to_bpo",
            outcome="rejected",
            reason="費用対効果が不明確",
        )

    assert result["success"] is True
    assert result["new_weights"]["upgrade_to_bpo"] == 2  # 5 - 3


@pytest.mark.asyncio
async def test_record_upsell_outcome_deferred_no_weight_change():
    """deferred 時に重みは変化しない"""
    initial_weights = {"backoffice_bpo": 3}
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"id": "model-003", "weights": initial_weights.copy(), "version": 1}
    ]
    mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch(
        "workers.bpo.sales.cs.upsell_briefing_pipeline.get_service_client",
        return_value=mock_db,
    ):
        result = await record_upsell_outcome(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            opportunity_type="backoffice_bpo",
            outcome="deferred",
        )

    assert result["success"] is True
    assert result["new_weights"]["backoffice_bpo"] == 3  # 変化なし


@pytest.mark.asyncio
async def test_record_upsell_outcome_no_existing_model_creates_first_version():
    """既存モデルがない場合は version=1 の新規モデルを作成する"""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

    with patch(
        "workers.bpo.sales.cs.upsell_briefing_pipeline.get_service_client",
        return_value=mock_db,
    ):
        result = await record_upsell_outcome(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            opportunity_type="custom_bpo",
            outcome="accepted",
        )

    assert result["success"] is True
    assert result["new_weights"]["custom_bpo"] == 5  # 初期値0 + 5


@pytest.mark.asyncio
async def test_record_upsell_outcome_db_error_returns_failure():
    """DB接続エラー時は success=False を返す（例外を外に出さない）"""
    with patch(
        "db.supabase.get_service_client",
        side_effect=Exception("DB connection failed"),
    ):
        result = await record_upsell_outcome(
            company_id=COMPANY_ID,
            customer_id=CUSTOMER_ID,
            opportunity_type="additional_module",
            outcome="accepted",
        )

    assert result["success"] is False
    assert "error" in result


# ─── パイプラインのDB重み参照テスト ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_step2_loads_upsell_weights_from_db():
    """Step2でscoring_model_versionsからupsell重みを読み込む"""
    mock_db = MagicMock()
    mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
        {"weights": {"additional_module": 10}}
    ]

    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
        patch("db.supabase.get_service_client",
              return_value=mock_db),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    # DB重み参照に失敗してもパイプライン全体は成功する（デフォルト閾値にフォールバック）
    assert result.success is True


@pytest.mark.asyncio
async def test_pipeline_step2_weight_fetch_failure_falls_back_to_defaults():
    """DB重み取得失敗時はデフォルト閾値でパイプラインが継続する"""
    with (
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_saas_reader",
              new=AsyncMock(return_value=_MOCK_SAAS_OUT_SUCCESS)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_rule_matcher",
              new=AsyncMock(return_value=_MOCK_RULE_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_document_generator",
              new=AsyncMock(return_value=_MOCK_GEN_OUT)),
        patch("workers.bpo.sales.cs.upsell_briefing_pipeline.run_calendar_booker",
              new=AsyncMock(side_effect=[_MOCK_SLOTS_OUT, _MOCK_BLOCK_OUT])),
        patch("db.supabase.get_service_client",
              side_effect=Exception("DB unavailable")),
    ):
        result = await run_upsell_briefing_pipeline(
            company_id=COMPANY_ID,
            customer_company_id=CUSTOMER_ID,
            input_data=_make_input_data_with_opportunity(),
        )

    assert result.success is True
    assert len(result.steps) == 5
