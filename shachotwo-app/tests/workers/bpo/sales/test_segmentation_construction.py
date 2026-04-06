"""建設リード向けセグメント（b_10 §3-7）のユニットテスト。"""
from workers.bpo.sales.segmentation_construction import (
    classify_construction_lead,
    detect_construction_sub_industry,
)


def test_detect_construction_sub_industry_civil() -> None:
    assert detect_construction_sub_industry("土木一式工事") == "土木"


def test_detect_construction_sub_industry_architecture() -> None:
    assert detect_construction_sub_industry("木造建築") == "建築"


def test_detect_construction_sub_industry_equipment() -> None:
    assert detect_construction_sub_industry("電気設備工事") == "設備"


def test_detect_construction_sub_industry_other() -> None:
    assert detect_construction_sub_industry("不明") == "その他建設"


def test_classify_construction_lead_tier_and_segments() -> None:
    out = classify_construction_lead(
        annual_revenue=2_000_000_000,
        operating_profit=100_000_000,
        employee_count=50,
        main_work_type="舗装工事",
    )
    assert out["sub_industry"] == "土木"
    assert out["priority_tier"] == "A"
    assert "revenue_segment" in out
    assert "profit_segment" in out
