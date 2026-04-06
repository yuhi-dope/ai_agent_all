"""kintone 製造業リード取り込みのユニットテスト。"""
import pytest

from workers.bpo.sales.kintone_lead_import_runner import flat_from_record
from workers.bpo.sales.kintone_manufacturing_import import (
    _DEFAULT_FIELD_CODES,
    _DEFAULT_FIELD_MAPPINGS,
    build_page_query,
    kintone_record_to_flat,
    map_flat_to_lead_row,
    record_numeric_id,
)


def test_build_page_query() -> None:
    assert build_page_query("", 0) == "order by $id asc"
    assert build_page_query("status in (\"open\")", 0) == '(status in ("open")) order by $id asc'
    assert "$id > 42" in build_page_query("", 42)


def test_record_numeric_id() -> None:
    assert record_numeric_id({"$id": {"value": "99"}}) == 99
    assert record_numeric_id({}) == 0


def test_kintone_record_to_flat() -> None:
    rec = {
        "name": {"value": "テスト株式会社"},
        "corporate_number": {"value": "1234567890123"},
    }
    flat = kintone_record_to_flat(rec)
    assert flat["name"] == "テスト株式会社"
    assert flat["corporate_number"] == "1234567890123"


def test_map_flat_to_lead_row_requires_corp_and_name() -> None:
    assert map_flat_to_lead_row({"name": "A"}, company_id="c1", app_id="1") is None
    assert map_flat_to_lead_row({"corporate_number": "1"}, company_id="c1", app_id="1") is None


def test_flat_from_record_respects_field_mappings() -> None:
    rec = {
        "会社名": {"value": "テスト株式会社"},
        "法人番号": {"value": "9010001000000"},
    }
    m = {"name": "会社名", "corporate_number": "法人番号"}
    flat = flat_from_record(rec, ("name", "corporate_number"), m)
    assert flat["name"] == "テスト株式会社"
    assert flat["corporate_number"] == "9010001000000"


def test_map_flat_to_lead_row_minimal() -> None:
    flat = {
        "name": "株式会社サンプル",
        "corporate_number": "9010001000000",
        "employee_count": 50,
        "annual_revenue": 5_000_000_000,
        "operating_profit": 600_000_000,
        "business_description": "金属切削加工",
        "prefecture": "東京都",
        "address": "東京都千代田区1-1",
    }
    row = map_flat_to_lead_row(flat, company_id="550e8400-e29b-41d4-a716-446655440000", app_id="122")
    assert row is not None
    assert row["company_name"] == "株式会社サンプル"
    assert row["corporate_number"] == "9010001000000"
    assert row["industry"] == "manufacturing"
    assert row["source"] == "kintone"
    assert row["prefecture"] == "東京都"
    assert row["city"] == "千代田区1-1"


def test_map_flat_to_lead_row_with_tsr_fields() -> None:
    """TSRフィールドが正しくマッピングされることを確認。"""
    flat = {
        "name": "株式会社テスト製造",
        "corporate_number": "1234567890123",
        "employee_count": 100,
        "annual_revenue": 10_000_000_000,
        "operating_profit": 1_000_000_000,
        "business_description": "機械加工",
        "prefecture": "愛知県",
        "address": "愛知県名古屋市中区1-1",
        # TSR fields
        "tsr_category_large": "金属製品製造業",
        "tsr_category_medium": "ボルト・ナット・リベット・小ねじ・ワッシャー製造",
        "tsr_category_small": "精密小ねじ製造",
        "tsr_category_detail": "ステンレス精密小ねじ",
        "tsr_business_items": "精密ネジ製造, 受託加工",
        "tsr_suppliers": "鉄鋼メーカーA, 非鉄金属メーカーB",
        "tsr_customers": "自動車部品メーカーC, 電子機器メーカーD",
        "tsr_representative": "山田太郎",
        "representative_phone": "052-123-4567",
        "tsr_revenue_latest": 5_000_000_000,
        "tsr_profit_latest": 500_000_000,
    }
    row = map_flat_to_lead_row(flat, company_id="550e8400-e29b-41d4-a716-446655440001", app_id="123")
    assert row is not None
    # 基本情報確認
    assert row["company_name"] == "株式会社テスト製造"
    assert row["corporate_number"] == "1234567890123"
    # TSRフィールド確認
    assert row["tsr_category_large"] == "金属製品製造業"
    assert row["tsr_category_medium"] == "ボルト・ナット・リベット・小ねじ・ワッシャー製造"
    assert row["tsr_category_small"] == "精密小ねじ製造"
    assert row["tsr_category_detail"] == "ステンレス精密小ねじ"
    assert row["tsr_business_items"] == "精密ネジ製造, 受託加工"
    assert row["tsr_suppliers"] == "鉄鋼メーカーA, 非鉄金属メーカーB"
    assert row["tsr_customers"] == "自動車部品メーカーC, 電子機器メーカーD"
    assert row["tsr_representative"] == "山田太郎"
    assert row["representative_phone"] == "052-123-4567"
    assert row["tsr_revenue_latest"] == 5_000_000_000
    assert row["tsr_profit_latest"] == 500_000_000


def test_map_flat_to_lead_row_with_partial_tsr_fields() -> None:
    """TSRフィールドの一部のみが存在する場合、Noneになること。"""
    flat = {
        "name": "株式会社部分TSR",
        "corporate_number": "9999999999999",
        "employee_count": 30,
        "annual_revenue": 1_000_000_000,
        "operating_profit": 100_000_000,
        "business_description": "加工",
        "prefecture": "北海道",
        "address": "北海道札幌市中央区1-1",
        # TSRフィールドは一部のみ
        "tsr_category_large": "機械・電気・金属関連製造業",
        "tsr_suppliers": "メーカーX",
    }
    row = map_flat_to_lead_row(flat, company_id="550e8400-e29b-41d4-a716-446655440002", app_id="124")
    assert row is not None
    assert row["tsr_category_large"] == "機械・電気・金属関連製造業"
    assert row["tsr_suppliers"] == "メーカーX"
    # 指定されていないTSRフィールドはNone
    assert row["tsr_category_medium"] is None
    assert row["tsr_category_small"] is None
    assert row["tsr_category_detail"] is None
    assert row["tsr_business_items"] is None
    assert row["tsr_customers"] is None
    assert row["tsr_representative"] is None
    assert row["representative_phone"] is None
    assert row["tsr_revenue_latest"] is None
    assert row["tsr_profit_latest"] is None


def test_map_flat_to_lead_row_tsr_numeric_fields() -> None:
    """TSRの数値フィールド（売上・純利益）が整数に変換されることを確認。"""
    flat = {
        "name": "株式会社数値テスト",
        "corporate_number": "5555555555555",
        "business_description": "製造",
        # 数値フィールドが文字列で渡されるケース（カンマ区切り）
        "tsr_revenue_latest": "8,500,000,000",
        "tsr_profit_latest": "850,000,000",
    }
    row = map_flat_to_lead_row(flat, company_id="550e8400-e29b-41d4-a716-446655440003", app_id="125")
    assert row is not None
    assert row["tsr_revenue_latest"] == 8_500_000_000
    assert row["tsr_profit_latest"] == 850_000_000


def test_map_flat_to_lead_row_tsr_numeric_fields_as_float() -> None:
    """TSRの数値フィールドがfloat型で渡される場合、整数に変換されることを確認。"""
    flat = {
        "name": "株式会社数値テストフロート",
        "corporate_number": "4444444444444",
        "business_description": "製造",
        # 数値フィールドがfloat型で渡されるケース
        "tsr_revenue_latest": 9_500_000_000.5,
        "tsr_profit_latest": 950_000_000.0,
    }
    row = map_flat_to_lead_row(flat, company_id="550e8400-e29b-41d4-a716-446655440004", app_id="126")
    assert row is not None
    assert row["tsr_revenue_latest"] == 9_500_000_000
    assert row["tsr_profit_latest"] == 950_000_000


def test_default_field_codes_includes_tsr_fields() -> None:
    """_DEFAULT_FIELD_CODESに11個のTSRフィールドが含まれることを確認。"""
    tsr_field_codes = [
        "tsr_category_large",
        "tsr_category_medium",
        "tsr_category_small",
        "tsr_category_detail",
        "tsr_business_items",
        "tsr_suppliers",
        "tsr_customers",
        "tsr_representative",
        "representative_phone",
        "tsr_revenue_latest",
        "tsr_profit_latest",
    ]
    for code in tsr_field_codes:
        assert code in _DEFAULT_FIELD_CODES, f"{code} が _DEFAULT_FIELD_CODES に含まれていません"


def test_default_field_mappings_includes_all_tsr_fields() -> None:
    """_DEFAULT_FIELD_MAPPINGSにすべてのTSRフィールドのマッピングが含まれることを確認。"""
    tsr_mappings = {
        "tsr_category_large",
        "tsr_category_medium",
        "tsr_category_small",
        "tsr_category_detail",
        "tsr_business_items",
        "tsr_suppliers",
        "tsr_customers",
        "tsr_representative",
        "representative_phone",
        "tsr_revenue_latest",
        "tsr_profit_latest",
    }
    for field_key in tsr_mappings:
        assert field_key in _DEFAULT_FIELD_MAPPINGS, f"{field_key} が _DEFAULT_FIELD_MAPPINGS に含まれていません"
    # 各マッピングの値が文字列（日本語フィールドコード）であることを確認
    for field_key, kintone_field_code in _DEFAULT_FIELD_MAPPINGS.items():
        assert isinstance(kintone_field_code, str), f"{field_key} のマッピング値が文字列ではありません"
        assert len(kintone_field_code) > 0, f"{field_key} のマッピング値が空です"
