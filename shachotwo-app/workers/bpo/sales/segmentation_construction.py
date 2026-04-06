"""建設業リード向けの簡易セグメント（売上区分・工種・優先度）。

b_10 §3-7 — 製造業用 classify_company とは独立。
"""
from __future__ import annotations

from typing import Optional

from workers.bpo.sales.segmentation import classify_profit_segment, classify_revenue_segment

_CONSTRUCTION_WORK_TYPES: dict[str, tuple[str, ...]] = {
    "土木": ("土木", "舗装", "造成", "基礎", "河川", "トンネル", "橋梁"),
    "建築": ("建築", "造作", "内装", "リフォーム", "設計施工", "木造", "鉄骨"),
    "設備": ("設備", "電気", "空調", "衛生", "管工事", "プラント"),
}


def detect_construction_sub_industry(main_work_type: str) -> str:
    t = (main_work_type or "").strip()
    for label, kws in _CONSTRUCTION_WORK_TYPES.items():
        if any(k in t for k in kws):
            return label
    return "その他建設"


def classify_construction_lead(
    annual_revenue: Optional[int],
    operating_profit: Optional[int],
    employee_count: Optional[int],
    main_work_type: str,
) -> dict[str, str]:
    rev = classify_revenue_segment(annual_revenue)
    prof = classify_profit_segment(operating_profit)
    sub = detect_construction_sub_industry(main_work_type)

    if annual_revenue is None and employee_count is None:
        tier = "C"
    elif annual_revenue and annual_revenue >= 10_000_000_000:
        tier = "S"
    elif annual_revenue and annual_revenue >= 1_000_000_000:
        tier = "A"
    elif employee_count and employee_count >= 100:
        tier = "A"
    else:
        tier = "B"

    return {
        "revenue_segment": rev,
        "profit_segment": prof,
        "sub_industry": sub,
        "priority_tier": tier,
    }
