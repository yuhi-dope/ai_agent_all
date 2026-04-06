"""不動産業 BPOパイプライン テスト（7本スケルトン）

テスト対象:
  - property_appraisal_pipeline    物件査定AI
  - contract_generation_pipeline   契約書AI自動生成
  - remittance_pipeline            送金・入金管理
  - property_listing_pipeline      物件資料・広告作成AI
  - property_crm_pipeline          内見・顧客管理CRM
  - repair_management_pipeline     修繕・設備管理
  - license_management_pipeline    免許・届出管理
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.message import MessageDraftResult


# ---------------------------------------------------------------------------
# 共通モックヘルパー
# ---------------------------------------------------------------------------

def _ok_output(agent_name: str = "mock", result: dict | None = None) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=result or {},
        confidence=0.90,
        cost_yen=1.0,
        duration_ms=50,
    )


def _ng_output(agent_name: str = "mock") -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=False,
        result={"error": "テスト用エラー"},
        confidence=0.0,
        cost_yen=0.0,
        duration_ms=10,
    )


def _mock_draft(document_type: str = "テスト文書") -> MessageDraftResult:
    return MessageDraftResult(
        subject=f"【{document_type}】件名",
        body="本文テスト",
        document_type=document_type,
        model_used="gemini-2.5-flash",
        is_template_fallback=False,
    )


# ===========================================================================
# 1. property_appraisal_pipeline
# ===========================================================================

class TestPropertyAppraisalPipeline:
    """物件査定AIパイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_cost_calculator")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_saas_reader")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_structured_extractor")
    async def test_success_self_use_residential(
        self,
        mock_extractor,
        mock_saas_reader,
        mock_calc,
        mock_rule_matcher,
        mock_gen,
        mock_validator,
    ):
        """自用住宅の査定が正常完了することを確認する。"""
        from workers.bpo.realestate.pipelines.property_appraisal_pipeline import (
            run_property_appraisal_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "property_type": "自用住宅", "address": "東京都渋谷区1-1-1",
            "prefecture": "東京都", "municipality": "渋谷区",
            "building_year": 2010, "structure": "RC",
        })
        mock_saas_reader.return_value = _ok_output("saas_reader", {
            "transactions": [
                {"transaction_price": 45_000_000, "area": 65.0, "transaction_date": "2025-06-01"},
            ],
            "land_price_per_sqm": 800_000,
        })
        # cost_calculator は3回呼ばれる（比較法/原価法/収益法スキップ後の原価法）
        mock_calc.side_effect = [
            _ok_output("cost_calculator", {"appraised_price": 42_000_000, "used_count": 3}),
            _ok_output("cost_calculator", {"appraised_price": 38_000_000}),
        ]
        mock_rule_matcher.return_value = _ok_output("rule_matcher", {
            "appraised_price": 40_000_000,
            "price_range_low": 36_000_000,
            "price_range_high": 44_000_000,
            "confidence": 0.82,
        })
        mock_gen.return_value = _ok_output("document_generator", {"pdf_url": "https://example.com/report.pdf"})
        mock_validator.return_value = _ok_output("output_validator")

        result = await run_property_appraisal_pipeline(
            company_id="test-company",
            input_data={
                "property_type": "自用住宅",
                "transaction_type": "sale",
                "address": "東京都渋谷区1-1-1",
                "prefecture": "東京都",
                "municipality": "渋谷区",
                "land_area": 120.0,
                "building_area": 95.0,
                "building_year": 2010,
                "structure": "RC",
                "floor_plan": "3LDK",
                "nearest_station": "渋谷駅",
                "station_distance_min": 8,
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert result.final_output["appraised_price"] == 40_000_000
        assert result.final_output["confidence"] == 0.82

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_saas_reader")
    @patch("workers.bpo.realestate.pipelines.property_appraisal_pipeline.run_structured_extractor")
    async def test_fail_at_data_collector(self, mock_extractor, mock_saas_reader):
        """Step2でデータ収集失敗した場合にfailを返すことを確認する。"""
        from workers.bpo.realestate.pipelines.property_appraisal_pipeline import (
            run_property_appraisal_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "property_type": "自用住宅", "building_year": 2010, "structure": "RC",
        })
        mock_saas_reader.return_value = _ng_output("saas_reader")

        result = await run_property_appraisal_pipeline(
            company_id="test-company",
            input_data={
                "property_type": "自用住宅",
                "transaction_type": "sale",
                "address": "東京都港区1-1-1",
                "prefecture": "東京都",
                "municipality": "港区",
            },
        )

        assert result.success is False
        assert result.failed_step == "data_collector"

    def test_weight_table_sum_to_one(self):
        """加重比率の合計が1.0になることを確認する。"""
        from workers.bpo.realestate.pipelines.property_appraisal_pipeline import WEIGHT_TABLE
        for property_type, weights in WEIGHT_TABLE.items():
            total = sum(weights)
            assert abs(total - 1.0) < 1e-6, f"{property_type}: 合計={total}"


# ===========================================================================
# 2. contract_generation_pipeline
# ===========================================================================

class TestContractGenerationPipeline:
    """契約書AI自動生成パイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_compliance_checker")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_structured_extractor")
    async def test_success_lease_contract(
        self, mock_extractor, mock_rule_matcher, mock_compliance,
        mock_gen, mock_validator
    ):
        """賃貸借契約書の生成が正常完了することを確認する。"""
        from workers.bpo.realestate.pipelines.contract_generation_pipeline import (
            run_contract_generation_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "contract_type": "lease",
            "property_address": "東京都新宿区1-2-3",
            "parties": {
                "landlord": {"name": "山田太郎", "address": "東京都"},
                "tenant": {"name": "鈴木花子", "address": "神奈川県"},
            },
            "terms": {"monthly_rent": 80000, "deposit": 160000},
            "conditions": [],
        })
        mock_rule_matcher.return_value = _ok_output("rule_matcher", {"template": {"type": "lease_residential"}})
        # document_generator は3回呼ばれる（条項埋め/特約/PDF出力）
        mock_gen.side_effect = [
            _ok_output("document_generator", {"clauses": [{"article": "37", "text": "賃料条項"}]}),
            _ok_output("document_generator", {"special_clauses": []}),
            _ok_output("document_generator", {"pdf_url": "https://example.com/contract.pdf"}),
        ]
        mock_compliance.return_value = _ok_output("compliance_checker", {"violations": []})
        mock_validator.return_value = _ok_output("output_validator")

        result = await run_contract_generation_pipeline(
            company_id="test-company",
            input_data={
                "contract_type": "lease",
                "property_address": "東京都新宿区1-2-3",
                "parties": {
                    "landlord": {"name": "山田太郎", "address": "東京都"},
                    "tenant": {"name": "鈴木花子", "address": "神奈川県"},
                },
                "terms": {"monthly_rent": 80000, "deposit": 160000},
                "conditions": [],
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert result.final_output["has_errors"] is False

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_compliance_checker")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.contract_generation_pipeline.run_structured_extractor")
    async def test_error_on_law_violation(
        self, mock_extractor, mock_rule_matcher, mock_compliance, mock_gen
    ):
        """法令違反（error）が検出された場合にhas_errors=Trueを返すことを確認する。"""
        from workers.bpo.realestate.pipelines.contract_generation_pipeline import (
            run_contract_generation_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "contract_type": "sale",
            "property_address": "東京都港区1-1-1",
            "parties": {}, "terms": {}, "conditions": [],
        })
        mock_rule_matcher.return_value = _ok_output("rule_matcher", {"template": {}})
        mock_gen.side_effect = [
            _ok_output("document_generator", {"clauses": []}),
            _ok_output("document_generator", {"special_clauses": []}),
            _ok_output("document_generator", {"pdf_url": "..."}),
        ]
        mock_compliance.return_value = _ok_output("compliance_checker", {
            "violations": [
                {"severity": "error", "message": "宅建業法40条違反", "law": "宅建業法40条"}
            ]
        })

        result = await run_contract_generation_pipeline(
            company_id="test-company",
            input_data={
                "contract_type": "sale",
                "property_address": "東京都港区1-1-1",
                "parties": {}, "terms": {},
                "is_seller_takken_gyosha": True, "conditions": [],
            },
        )

        assert result.success is True  # パイプライン自体は完了
        assert result.final_output["has_errors"] is True


# ===========================================================================
# 3. remittance_pipeline
# ===========================================================================

class TestRemittancePipeline:
    """送金・入金管理パイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_cost_calculator")
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_saas_reader")
    async def test_success_monthly_remittance(
        self, mock_saas, mock_calc, mock_gen, mock_validator
    ):
        """月次送金計算が正常完了することを確認する。"""
        from workers.bpo.realestate.pipelines.remittance_pipeline import run_remittance_pipeline

        mock_saas.return_value = _ok_output("saas_reader", {
            "payment_records": [{"amount": 80000, "status": "matched"}],
        })
        # document_generator: invoice + statement の2回
        mock_gen.side_effect = [
            _ok_output("document_generator", {"invoice_number": "INV-2026-03-001"}),
            _ok_output("document_generator", {"pdf_url": "https://example.com/statement.pdf"}),
        ]
        mock_calc.return_value = _ok_output("cost_calculator", {"remittance_amount": 72_000})
        mock_validator.return_value = _ok_output("output_validator")

        result = await run_remittance_pipeline(
            company_id="test-company",
            input_data={
                "period_year": 2026,
                "period_month": 3,
                "property_id": "prop-001",
                "transaction_type": "lease",
                "transaction_price": 80_000,
                "agency_fee": 44_000,  # 上限以内（80000 × 0.55 = 44000）
                "management_fee_rate": 0.05,
                "rent_collected": 80_000,
                "repair_cost": 0,
                "renewal_fee_share": 0,
                "arrears_collected": 0,
                "invoice_registration_number": "T1234567890123",
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert result.final_output["remittance_amount"] == 72_000

    def test_agency_fee_limit_calculation(self):
        """仲介手数料上限計算が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.remittance_pipeline import _calc_agency_fee_limit

        # 賃貸: 月額10万円 × 1.1ヶ月 = 11万円
        assert _calc_agency_fee_limit(100_000, "lease") == 110_000

        # 売買400万超: 5000万円 × 3.3% + 66000 = 1,716,000円
        expected = int(50_000_000 * 0.033 + 66_000)
        assert _calc_agency_fee_limit(50_000_000, "sale") == expected

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.remittance_pipeline.run_saas_reader")
    async def test_fail_on_agency_fee_exceeded(self, mock_saas, mock_gen):
        """仲介手数料が上限を超えた場合にfailを返すことを確認する。"""
        from workers.bpo.realestate.pipelines.remittance_pipeline import run_remittance_pipeline

        mock_saas.return_value = _ok_output("saas_reader", {})
        mock_gen.return_value = _ok_output("document_generator", {})

        result = await run_remittance_pipeline(
            company_id="test-company",
            input_data={
                "period_year": 2026,
                "period_month": 3,
                "property_id": "prop-001",
                "transaction_type": "lease",
                "transaction_price": 80_000,
                "agency_fee": 200_000,  # 上限（88000円）の大幅超過
                "management_fee_rate": 0.05,
                "rent_collected": 80_000,
                "repair_cost": 0,
                "renewal_fee_share": 0,
                "arrears_collected": 0,
                "invoice_registration_number": "T1234567890123",
            },
        )

        assert result.success is False
        assert result.failed_step == "fee_validator"

    def test_deposit_refund_calculation(self):
        """敷金精算計算（経過年数考慮）が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.remittance_pipeline import _calc_deposit_refund

        # 壁紙（耐用年数6年）、2年入居後退去
        result = _calc_deposit_refund(
            deposit_amount=160_000,
            tenure_months=24,  # 2年
            restoration_items=[{"category": "壁紙", "cost": 60_000}],
        )
        # 残存価値 = 1 - 2/6 = 2/3
        expected_tenant_share = int(60_000 * (2/3))
        expected_refund = 160_000 - expected_tenant_share
        assert result["total_tenant_share"] == expected_tenant_share
        assert result["refund_amount"] == expected_refund


# ===========================================================================
# 4. property_listing_pipeline
# ===========================================================================

class TestPropertyListingPipeline:
    """物件資料・広告作成AIパイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.property_listing_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.property_listing_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.property_listing_pipeline.run_compliance_checker")
    @patch("workers.bpo.realestate.pipelines.property_listing_pipeline.run_document_ocr")
    @patch("workers.bpo.realestate.pipelines.property_listing_pipeline.run_structured_extractor")
    async def test_success_both_maisoku_and_portal(
        self, mock_extractor, mock_ocr, mock_compliance, mock_gen, mock_validator
    ):
        """マイソク+ポータル両方の生成が正常完了することを確認する。"""
        from workers.bpo.realestate.pipelines.property_listing_pipeline import (
            run_property_listing_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "address": "東京都渋谷区1-1-1",
            "nearest_station": "渋谷駅",
            "station_distance_meters": 640,
            "building_year_month": "2015-03",
            "transaction_type": "仲介",
            "license_number": "東京都知事(3)第12345号",
        })
        mock_ocr.return_value = _ok_output("document_ocr", {
            "classified_photos": [{"url": "https://example.com/1.jpg", "category": "外観"}],
            "main_photo": "https://example.com/1.jpg",
        })
        mock_compliance.return_value = _ok_output("compliance_checker", {"violations": []})
        # document_generator: copy + maisoku + portal = 3回
        mock_gen.side_effect = [
            _ok_output("document_generator", {
                "catch_copy": "渋谷駅8分・RC造・2LDK",
                "ad_text": "閑静な住宅街に立地する人気物件です。収納が豊富で...",
            }),
            _ok_output("document_generator", {"pdf_url": "https://example.com/maisoku.pdf"}),
            _ok_output("document_generator", {"SUUMO": {"title": "渋谷区 2LDK"}}),
        ]
        mock_validator.return_value = _ok_output("output_validator")

        result = await run_property_listing_pipeline(
            company_id="test-company",
            input_data={
                "property_data": {
                    "address": "東京都渋谷区1-1-1",
                    "transport": "渋谷駅徒歩8分",
                    "building_area": 65.0,
                    "structure": "RC",
                    "floor_plan": "2LDK",
                    "rent": 220_000,
                    "nearest_station": "渋谷駅",
                    "station_distance_meters": 640,
                    "transaction_type": "仲介",
                    "license_number": "東京都知事(3)第12345号",
                },
                "photo_files": ["https://example.com/1.jpg"],
                "target_persona": "ファミリー",
                "ad_type": "both",
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert len(result.final_output["compliance_violations"]) == 0

    def test_walk_time_calculation(self):
        """徒歩所要時間の計算が公正競争規約通りであることを確認する。"""
        from workers.bpo.realestate.pipelines.property_listing_pipeline import _calc_walk_time

        assert _calc_walk_time(80) == 1    # ちょうど80m = 1分
        assert _calc_walk_time(81) == 2    # 81m = 端数切り上げ = 2分
        assert _calc_walk_time(0) == 0     # 0m = 0分（建物内）
        assert _calc_walk_time(800) == 10  # 800m = 10分


# ===========================================================================
# 5. property_crm_pipeline
# ===========================================================================

class TestPropertyCrmPipeline:
    """内見・顧客管理CRMパイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.property_crm_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.property_crm_pipeline.run_saas_writer")
    @patch("workers.bpo.realestate.pipelines.property_crm_pipeline.run_message_drafter")
    @patch("workers.bpo.realestate.pipelines.property_crm_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.property_crm_pipeline.run_structured_extractor")
    async def test_success_new_inquiry(
        self, mock_extractor, mock_rule_matcher, mock_message, mock_saas_writer, mock_validator
    ):
        """新規反響の処理が正常完了することを確認する。"""
        from workers.bpo.realestate.pipelines.property_crm_pipeline import (
            run_property_crm_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "customer_name": "佐藤次郎",
            "phone": "090-1234-5678",
            "email": "sato@example.com",
            "transaction_type": "rent",
            "preferred_areas": ["渋谷区"],
            "budget_max": 200_000,
            "floor_plans": ["2LDK"],
            "max_station_distance": 10,
        })
        mock_rule_matcher.side_effect = [
            _ok_output("rule_matcher", {
                "is_new": True,
                "customer_id": "customer-001",
                "temperature_score": 30,
            }),
            _ok_output("rule_matcher", {
                "matched": [
                    {"property_id": "prop-001", "match_score": 0.85},
                    {"property_id": "prop-002", "match_score": 0.72},
                ]
            }),
        ]
        mock_message.return_value = _mock_draft("物件提案メール")
        mock_saas_writer.return_value = _ok_output("saas_writer", {"saved": True})
        mock_validator.return_value = _ok_output("output_validator")

        result = await run_property_crm_pipeline(
            company_id="test-company",
            input_data={
                "inquiry_source": "suumo",
                "inquiry_text": "渋谷区で2LDK、予算20万円以内の物件を探しています。",
                "action_type": "new_inquiry",
                "property_db": [
                    {"property_id": "prop-001", "address": "渋谷区", "rent": 185_000, "floor_plan": "2LDK"},
                    {"property_id": "prop-002", "address": "渋谷区", "rent": 195_000, "floor_plan": "2LDK"},
                ],
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert result.final_output["is_new_customer"] is True
        assert len(result.final_output["matched_properties"]) == 2

    def test_temperature_label_thresholds(self):
        """温度感スコアのラベル判定が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.property_crm_pipeline import _calc_temperature_label

        assert _calc_temperature_label(100) == "hot"
        assert _calc_temperature_label(80) == "hot"
        assert _calc_temperature_label(79) == "warm"
        assert _calc_temperature_label(50) == "warm"
        assert _calc_temperature_label(49) == "cool"
        assert _calc_temperature_label(20) == "cool"
        assert _calc_temperature_label(19) == "cold"
        assert _calc_temperature_label(0) == "cold"


# ===========================================================================
# 6. repair_management_pipeline
# ===========================================================================

class TestRepairManagementPipeline:
    """修繕・設備管理パイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.repair_management_pipeline.run_saas_writer")
    @patch("workers.bpo.realestate.pipelines.repair_management_pipeline.run_message_drafter")
    @patch("workers.bpo.realestate.pipelines.repair_management_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.repair_management_pipeline.run_structured_extractor")
    async def test_urgent_water_leak(
        self, mock_extractor, mock_rule_matcher, mock_message, mock_saas
    ):
        """緊急（漏水）案件がLevel1に判定されることを確認する。"""
        from workers.bpo.realestate.pipelines.repair_management_pipeline import (
            run_repair_management_pipeline,
        )
        mock_extractor.return_value = _ok_output("structured_extractor", {
            "category": "plumbing",
            "location": "キッチン",
            "situation": "台所の蛇口から水漏れが止まらない",
            "symptom": "水が漏れている",
        })
        mock_rule_matcher.side_effect = [
            _ok_output("rule_matcher", {
                "vendor": {"vendor_id": "v-001", "name": "山田設備", "estimated_cost": 30_000},
            }),
        ]
        mock_message.side_effect = [_mock_draft("修繕見積依頼"), _mock_draft("緊急修繕事後報告")]
        mock_saas.return_value = _ok_output("saas_writer", {"saved": True})

        result = await run_repair_management_pipeline(
            company_id="test-company",
            input_data={
                "property_id": "prop-001",
                "room_number": "301",
                "description": "漏水が発生しています。台所から水が出て止まりません。",
                "request_timestamp": "2026-03-28T14:00:00",
                "vendor_db": [{"vendor_id": "v-001", "name": "山田設備", "specialty": "plumbing"}],
            },
        )

        assert result.success is True
        assert result.final_output["urgency_level"] == 1
        assert result.final_output["urgency_deadline"] == "2時間以内"

    def test_urgency_detection_keywords(self):
        """緊急度キーワード検出が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.repair_management_pipeline import (
            _detect_urgency_from_keywords,
        )

        level, conf = _detect_urgency_from_keywords("ガス漏れが発生しています")
        assert level == 1
        assert conf > 0.80

        level, conf = _detect_urgency_from_keywords("給湯器が故障しました")
        assert level == 2

        level, conf = _detect_urgency_from_keywords("壁紙が剥がれてきました")
        assert level == 3

    def test_context_correction_summer_ac(self):
        """真夏のエアコン故障が緊急度2に補正されることを確認する。"""
        from workers.bpo.realestate.pipelines.repair_management_pipeline import (
            _apply_context_correction,
        )
        corrected = _apply_context_correction(
            base_level=3,
            request_hour=14,
            season_month=8,  # 8月（真夏）
            category="hvac",
        )
        assert corrected == 2


# ===========================================================================
# 7. license_management_pipeline
# ===========================================================================

class TestLicenseManagementPipeline:
    """免許・届出管理パイプラインのテスト"""

    @pytest.mark.asyncio
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_message_drafter")
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_output_validator")
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_document_generator")
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_diff_detector")
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_rule_matcher")
    @patch("workers.bpo.realestate.pipelines.license_management_pipeline.run_saas_reader")
    async def test_success_renewal_alert(
        self, mock_saas, mock_rule_matcher, mock_diff, mock_gen, mock_validator, mock_message
    ):
        """90日前アラートが正常に生成されることを確認する。"""
        from workers.bpo.realestate.pipelines.license_management_pipeline import (
            run_license_management_pipeline,
        )
        from datetime import timedelta

        expiry_date = (date.today() + timedelta(days=75)).isoformat()
        mock_saas.return_value = _ok_output("saas_reader", {
            "license_data": {
                "license_number": "東京都知事(3)第12345号",
                "expiry_date": expiry_date,
                "renewal_count": 3,
            },
            "employee_data": [
                {
                    "name": "田中宅建士",
                    "is_takkenshi": True,
                    "is_senin": True,
                    "takkenshi_expiry": (date.today() + timedelta(days=365)).isoformat(),
                    "status": "active",
                    "hire_date": "2020-04-01",
                }
            ],
        })
        mock_rule_matcher.return_value = _ok_output("rule_matcher")
        mock_diff.return_value = _ok_output("diff_detector", {"missing_items": []})
        mock_gen.side_effect = [
            _ok_output("document_generator", {"report_url": "https://example.com/report.pdf"}),
            _ok_output("document_generator", {"roster_url": "https://example.com/roster.pdf"}),
        ]
        mock_validator.return_value = _ok_output("output_validator")
        mock_message.return_value = _mock_draft("免許更新アラート通知")

        result = await run_license_management_pipeline(
            company_id="test-company",
            input_data={
                "license_data": {
                    "license_number": "東京都知事(3)第12345号",
                    "expiry_date": expiry_date,
                },
                "employee_data": [],
                "checklist_status": {},
                "fiscal_year": 2025,
            },
        )

        assert result.success is True
        assert len(result.steps) == 7
        assert result.final_output["license_expired"] is False
        assert len(result.final_output["alerts"]) >= 1

    def test_days_remaining_calculation(self):
        """残日数計算が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.license_management_pipeline import (
            _calc_days_remaining,
        )
        from datetime import timedelta

        future_date = (date.today() + timedelta(days=60)).isoformat()
        assert _calc_days_remaining(future_date) == 60

        past_date = (date.today() - timedelta(days=1)).isoformat()
        assert _calc_days_remaining(past_date) < 0

    def test_alert_level_determination(self):
        """アラートレベルの判定が正しいことを確認する。"""
        from workers.bpo.realestate.pipelines.license_management_pipeline import (
            _determine_alert_level,
        )

        assert "expired" in _determine_alert_level(-1)
        assert "30d" in _determine_alert_level(25)
        assert "60d" in _determine_alert_level(45)
        assert "90d" in _determine_alert_level(75)
        assert "180d" in _determine_alert_level(150)
        assert _determine_alert_level(200) == []

    def test_renewal_checklist_completeness(self):
        """更新チェックリストの必要書類が網羅されていることを確認する。"""
        from workers.bpo.realestate.pipelines.license_management_pipeline import RENEWAL_CHECKLIST

        required_items = {doc["item"] for doc in RENEWAL_CHECKLIST}
        # 宅建業法で必要とされる主要書類が含まれていることを確認
        assert "誓約書" in required_items
        assert "宅地建物取引士証の写し（専任宅建士全員）" in required_items
        assert "登記されていないことの証明書（役員全員）" in required_items


# ===========================================================================
# PIPELINE_REGISTRY テスト
# ===========================================================================

class TestPipelineRegistry:
    """パイプラインレジストリのテスト"""

    def test_all_pipelines_registered(self):
        """全8本のパイプラインがレジストリに登録されていることを確認する。"""
        from workers.bpo.realestate.pipelines import PIPELINE_REGISTRY

        expected_keys = {
            "rent_collection",
            "property_appraisal",
            "contract_generation",
            "remittance",
            "property_listing",
            "property_crm",
            "repair_management",
            "license_management",
        }
        assert set(PIPELINE_REGISTRY.keys()) == expected_keys

    def test_all_entries_are_callable(self):
        """レジストリの全エントリが呼び出し可能であることを確認する。"""
        from workers.bpo.realestate.pipelines import PIPELINE_REGISTRY

        for name, fn in PIPELINE_REGISTRY.items():
            assert callable(fn), f"{name} は呼び出し可能である必要があります"
