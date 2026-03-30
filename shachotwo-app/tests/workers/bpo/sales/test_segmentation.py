"""製造業ターゲット企業セグメント分類ロジックのユニットテスト"""
import pytest
from workers.bpo.sales.segmentation import (
    classify_company,
    classify_revenue_segment,
    classify_profit_segment,
    classify_employee_segment,
    detect_sub_industry,
    CompanySegment,
    MANUFACTURING_SUB_INDUSTRIES,
)


# ---------------------------------------------------------------------------
# classify_revenue_segment
# ---------------------------------------------------------------------------

class TestClassifyRevenueSegment:
    def test_micro(self):
        assert classify_revenue_segment(50_000_000) == "micro"

    def test_small(self):
        assert classify_revenue_segment(500_000_000) == "small"

    def test_mid(self):
        assert classify_revenue_segment(5_000_000_000) == "mid"

    def test_large(self):
        assert classify_revenue_segment(20_000_000_000) == "large"

    def test_enterprise(self):
        assert classify_revenue_segment(100_000_000_000) == "enterprise"

    def test_none_returns_unknown(self):
        assert classify_revenue_segment(None) == "unknown"

    def test_boundary_small_to_mid(self):
        # 10億円ちょうどは mid
        assert classify_revenue_segment(1_000_000_000) == "mid"

    def test_boundary_large_start(self):
        # 100億円ちょうどは large
        assert classify_revenue_segment(10_000_000_000) == "large"


# ---------------------------------------------------------------------------
# classify_profit_segment
# ---------------------------------------------------------------------------

class TestClassifyProfitSegment:
    def test_below_target(self):
        assert classify_profit_segment(100_000_000) == "below_target"

    def test_target_core(self):
        assert classify_profit_segment(1_000_000_000) == "target_core"  # 10億

    def test_target_upper(self):
        assert classify_profit_segment(20_000_000_000) == "target_upper"  # 200億

    def test_out_of_range(self):
        assert classify_profit_segment(60_000_000_000) == "out_of_range"  # 600億

    def test_none_returns_unknown(self):
        assert classify_profit_segment(None) == "unknown"

    def test_boundary_core_start(self):
        # 5億円ちょうどは target_core
        assert classify_profit_segment(500_000_000) == "target_core"


# ---------------------------------------------------------------------------
# classify_employee_segment
# ---------------------------------------------------------------------------

class TestClassifyEmployeeSegment:
    def test_startup(self):
        assert classify_employee_segment(5) == "startup"

    def test_small(self):
        assert classify_employee_segment(30) == "small"

    def test_mid(self):
        assert classify_employee_segment(100) == "mid"

    def test_large(self):
        assert classify_employee_segment(500) == "large"

    def test_enterprise(self):
        assert classify_employee_segment(2000) == "enterprise"

    def test_none_returns_unknown(self):
        assert classify_employee_segment(None) == "unknown"


# ---------------------------------------------------------------------------
# detect_sub_industry
# ---------------------------------------------------------------------------

class TestDetectSubIndustry:
    def test_metalwork_keyword(self):
        assert detect_sub_industry("金属加工・プレス部品の製造") == "金属加工"

    def test_resin_keyword(self):
        assert detect_sub_industry("射出成形による樹脂部品") == "樹脂加工"

    def test_machine_keyword(self):
        assert detect_sub_industry("産業機械の設計・製造") == "機械製造"

    def test_electronic_keyword(self):
        assert detect_sub_industry("プリント基板の実装・検査") == "電子部品"

    def test_food_keyword(self):
        assert detect_sub_industry("冷凍食品の製造・販売") == "食品製造"

    def test_chemical_keyword(self):
        assert detect_sub_industry("塗料・接着剤の製造") == "化学製品"

    def test_auto_keyword(self):
        assert detect_sub_industry("自動車部品の加工・組立") == "自動車部品"

    def test_fallback_unknown(self):
        assert detect_sub_industry("特殊材料の開発") == "その他製造"

    def test_empty_string(self):
        assert detect_sub_industry("") == "その他製造"

    def test_all_categories_have_keywords_or_are_fallback(self):
        for category, keywords in MANUFACTURING_SUB_INDUSTRIES.items():
            if category == "その他製造":
                assert keywords == [], "その他製造のキーワードは空リストであること"
            else:
                assert len(keywords) > 0, f"{category} にキーワードが定義されていること"


# ---------------------------------------------------------------------------
# classify_company — 優先度 S
# ---------------------------------------------------------------------------

class TestClassifyCompanyTierS:
    def test_s_tier_ideal_target(self):
        seg = classify_company(
            annual_revenue=5_000_000_000,   # 50億
            operating_profit=2_000_000_000, # 20億（target_core）
            employee_count=150,             # 150名（mid, 50〜300の範囲）
            industry_text="金属加工・切削部品",
        )
        assert seg.priority_tier == "S"
        assert seg.is_target is True
        assert seg.sub_industry == "金属加工"

    def test_s_tier_min_boundary(self):
        # employee_count=50 は EMPLOYEE_SEGMENTS で small (11-50) に分類されるため
        # Sティア条件 emp_seg in ("mid", "large") を満たさない → A ティア
        seg = classify_company(
            operating_profit=500_000_001,   # 5億超（target_core）
            employee_count=50,              # small セグメント（11〜50）
            industry_text="プレス加工",
        )
        assert seg.priority_tier == "A"  # S条件の emp_seg チェックで外れる

    def test_s_tier_51_employees(self):
        # 51名は mid セグメント (51〜200) → S 条件を満たす
        seg = classify_company(
            operating_profit=500_000_001,
            employee_count=51,
            industry_text="プレス加工",
        )
        assert seg.priority_tier == "S"

    def test_s_tier_max_boundary(self):
        seg = classify_company(
            operating_profit=9_999_999_999, # 100億未満（target_core）
            employee_count=300,             # 最大300名
            industry_text="溶接構造物",
        )
        assert seg.priority_tier == "S"


# ---------------------------------------------------------------------------
# classify_company — 優先度 A
# ---------------------------------------------------------------------------

class TestClassifyCompanyTierA:
    def test_a_tier_upper_profit(self):
        seg = classify_company(
            operating_profit=15_000_000_000,  # 150億（target_upper）
            employee_count=300,
            industry_text="機械製造",
        )
        assert seg.priority_tier == "A"
        assert seg.is_target is True

    def test_a_tier_core_profit_small_employee(self):
        seg = classify_company(
            operating_profit=1_000_000_000,  # 10億（target_core）
            employee_count=20,               # small（50未満なのでSではない）
            industry_text="電子部品",
        )
        assert seg.priority_tier == "A"

    def test_a_tier_upper_limit_employee(self):
        # employee_count=1000 は large セグメント (201〜1000) に含まれる → A 条件を満たす
        seg = classify_company(
            operating_profit=5_000_000_000,
            employee_count=1000,
            industry_text="食品製造",
        )
        assert seg.priority_tier == "A"

    def test_a_tier_1001_employees_becomes_c(self):
        # 1001名は enterprise セグメント → A 条件 emp_seg in ("small","mid","large") を外れ C
        seg = classify_company(
            operating_profit=5_000_000_000,
            employee_count=1001,
            industry_text="食品製造",
        )
        assert seg.priority_tier == "C"


# ---------------------------------------------------------------------------
# classify_company — 優先度 B
# ---------------------------------------------------------------------------

class TestClassifyCompanyTierB:
    def test_b_tier_no_profit_mid_revenue(self):
        seg = classify_company(
            annual_revenue=5_000_000_000,  # 50億（mid）
            operating_profit=None,
            employee_count=80,
            industry_text="化学製品",
        )
        assert seg.priority_tier == "B"
        assert seg.is_target is True

    def test_b_tier_large_revenue(self):
        seg = classify_company(
            annual_revenue=20_000_000_000,  # 200億（large）
            operating_profit=None,
            industry_text="自動車部品",
        )
        assert seg.priority_tier == "B"

    def test_b_tier_capital_stock_fallback(self):
        seg = classify_company(
            annual_revenue=None,
            operating_profit=None,
            capital_stock=50_000_000,  # 5000万（>= 1000万）
            employee_count=60,
            industry_text="樹脂成形",
        )
        assert seg.priority_tier == "B"


# ---------------------------------------------------------------------------
# classify_company — 優先度 C
# ---------------------------------------------------------------------------

class TestClassifyCompanyTierC:
    def test_c_tier_out_of_range_profit(self):
        seg = classify_company(
            operating_profit=60_000_000_000,  # 600億（out_of_range）
            employee_count=500,
            industry_text="機械製造",
        )
        assert seg.priority_tier == "C"
        assert seg.is_target is False

    def test_c_tier_below_target_profit(self):
        seg = classify_company(
            operating_profit=100_000_000,  # 1億（below_target）
            employee_count=20,
            industry_text="金属加工",
        )
        assert seg.priority_tier == "C"

    def test_c_tier_micro_revenue_no_profit(self):
        seg = classify_company(
            annual_revenue=50_000_000,  # 5000万（micro）
            operating_profit=None,
            industry_text="その他製造",
        )
        assert seg.priority_tier == "C"

    def test_c_tier_all_none(self):
        seg = classify_company()
        assert seg.priority_tier == "C"
        assert seg.is_target is False

    def test_c_tier_enterprise_revenue_no_profit(self):
        seg = classify_company(
            annual_revenue=100_000_000_000,  # 1000億（enterprise）
            operating_profit=None,
            industry_text="機械製造",
        )
        assert seg.priority_tier == "C"


# ---------------------------------------------------------------------------
# 返却値の構造
# ---------------------------------------------------------------------------

class TestCompanySegmentStructure:
    def test_reasons_not_empty_for_target(self):
        seg = classify_company(
            operating_profit=1_000_000_000,
            employee_count=100,
            industry_text="金属加工",
        )
        assert len(seg.reasons) > 0

    def test_reasons_not_empty_for_non_target(self):
        seg = classify_company(
            operating_profit=100_000_000,
            industry_text="金属加工",
        )
        assert len(seg.reasons) > 0

    def test_return_type(self):
        seg = classify_company()
        assert isinstance(seg, CompanySegment)
        assert hasattr(seg, "revenue_segment")
        assert hasattr(seg, "profit_segment")
        assert hasattr(seg, "employee_segment")
        assert hasattr(seg, "sub_industry")
        assert hasattr(seg, "priority_tier")
        assert hasattr(seg, "is_target")
        assert hasattr(seg, "reasons")
