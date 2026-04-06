"""介護報酬請求パイプライン テスト"""
import pytest

from workers.bpo.nursing.pipelines.care_billing_pipeline import (
    CareBillingPipeline,
    CareBillingPipelineResult,
    REGION_UNIT_PRICE,
    SHOGUU_KAIZEN_RATES,
)


# ---------------------------------------------------------------------------
# 共通フィクスチャ
# ---------------------------------------------------------------------------

def _base_input(
    records: list[dict] | None = None,
    shoguu_kaizen_level: str = "I",
    region: str = "3級地",
    facility_type: str = "訪問介護",
    period_year: int = 2026,
    period_month: int = 3,
) -> dict:
    default_records = [
        {
            "user_id": "U001",
            "user_name": "鈴木一郎",
            "service_type": "訪問介護",
            "care_level": 3,
            "service_date": "2026-03-05",
            "service_hours": 1.5,
            "service_code": "111131",
            "units": 245,
        }
    ]
    return {
        "service_records": records if records is not None else default_records,
        "facility_info": {
            "shoguu_kaizen_level": shoguu_kaizen_level,
            "region": region,
            "facility_type": facility_type,
        },
        "period_year": period_year,
        "period_month": period_month,
    }


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------

class TestCareBillingPipeline:

    @pytest.mark.asyncio
    async def test_direct_records_success(self):
        """TC-1: 直渡しで全5ステップ正常完了"""
        pipeline = CareBillingPipeline()
        result = await pipeline.run(_base_input())

        assert isinstance(result, CareBillingPipelineResult)
        assert result.success is True
        assert result.error is None
        assert len(result.steps_completed) == 5
        assert len(result.records) == 1

    @pytest.mark.asyncio
    async def test_all_5_steps_executed(self):
        """TC-4: 全5ステップが実行されていることを確認"""
        pipeline = CareBillingPipeline()
        result = await pipeline.run(_base_input())

        expected_steps = [
            "record_reader",
            "service_code_mapper",
            "addition_checker",
            "compliance_checker",
            "output_validator",
        ]
        assert result.steps_completed == expected_steps

    @pytest.mark.asyncio
    async def test_total_amount_calculation(self):
        """TC-2: 単位数×地域単価の計算正確性

        245単位 × 11.05（3級地）= 2,707円
        加算（加算I=13.7%）: 245 × 0.137 × 11.05 = 370.8 → 370円
        合計 = 2,707 + 370 = 3,077円
        """
        pipeline = CareBillingPipeline()
        result = await pipeline.run(
            _base_input(
                records=[
                    {
                        "user_id": "U001",
                        "user_name": "田中花子",
                        "service_type": "訪問介護",
                        "care_level": 2,
                        "service_date": "2026-03-10",
                        "service_hours": 1.0,
                        "service_code": "111131",
                        "units": 245,
                    }
                ],
                region="3級地",
                shoguu_kaizen_level="none",
            )
        )

        assert result.success is True
        unit_price = REGION_UNIT_PRICE["3級地"]  # 11.05
        expected_base_amount = int(245 * unit_price)
        assert result.total_amount == expected_base_amount

    @pytest.mark.asyncio
    async def test_shoguu_kaizen_addition(self):
        """TC-3: 処遇改善加算の計算

        加算I(13.7%) = base_units × 0.137 × unit_price
        """
        pipeline = CareBillingPipeline()
        units = 500
        region = "その他"
        level = "I"
        result = await pipeline.run(
            _base_input(
                records=[
                    {
                        "user_id": "U002",
                        "user_name": "山田太郎",
                        "service_type": "訪問介護",
                        "care_level": 3,
                        "service_date": "2026-03-15",
                        "service_hours": 2.0,
                        "service_code": "111131",
                        "units": units,
                    }
                ],
                shoguu_kaizen_level=level,
                region=region,
            )
        )

        assert result.success is True
        unit_price = REGION_UNIT_PRICE[region]
        rate = SHOGUU_KAIZEN_RATES[level]
        expected_addition = int(units * rate * unit_price)
        # total_amount = base + addition
        expected_base = int(units * unit_price)
        assert result.total_amount == expected_base + expected_addition

    @pytest.mark.asyncio
    async def test_late_record_triggers_risk(self):
        """TC-5: 翌月記録で返戻リスクアラートが発生する

        period_month=3 なのに service_date が 2026-04-xx → リスク
        """
        pipeline = CareBillingPipeline()
        result = await pipeline.run(
            _base_input(
                records=[
                    {
                        "user_id": "U003",
                        "user_name": "佐藤次郎",
                        "service_type": "訪問介護",
                        "care_level": 2,
                        "service_date": "2026-04-01",  # 翌月
                        "service_hours": 1.0,
                        "service_code": "111131",
                        "units": 245,
                    }
                ],
                period_year=2026,
                period_month=3,
            )
        )

        assert result.success is True
        assert len(result.rejection_risks) > 0
        assert any("不一致" in r or "返戻" in r for r in result.rejection_risks)

    @pytest.mark.asyncio
    async def test_multiple_users_processed(self):
        """TC-6: 複数利用者の処理"""
        records = [
            {
                "user_id": f"U{i:03d}",
                "user_name": f"利用者{i}",
                "service_type": "訪問介護",
                "care_level": (i % 5) + 1,
                "service_date": f"2026-03-{i + 1:02d}",
                "service_hours": 1.0,
                "service_code": "111131",
                "units": 245,
            }
            for i in range(1, 6)
        ]
        pipeline = CareBillingPipeline()
        result = await pipeline.run(_base_input(records=records))

        assert result.success is True
        assert len(result.records) == 5
        # 合計単位数は5名分
        assert result.total_units >= 245 * 5

    @pytest.mark.asyncio
    async def test_region_unit_price_applied(self):
        """TC-7: 地域単価が正しく適用される

        同一単位数でも地域が異なれば請求額が変わる。
        1級地(11.40) vs その他(10.00)
        """
        pipeline = CareBillingPipeline()
        units = 300

        result_1 = await pipeline.run(
            _base_input(
                records=[
                    {
                        "user_id": "U010",
                        "user_name": "東京太郎",
                        "service_type": "訪問介護",
                        "care_level": 3,
                        "service_date": "2026-03-10",
                        "service_hours": 2.0,
                        "service_code": "111131",
                        "units": units,
                    }
                ],
                region="1級地",
                shoguu_kaizen_level="none",
            )
        )
        result_2 = await pipeline.run(
            _base_input(
                records=[
                    {
                        "user_id": "U010",
                        "user_name": "地方太郎",
                        "service_type": "訪問介護",
                        "care_level": 3,
                        "service_date": "2026-03-10",
                        "service_hours": 2.0,
                        "service_code": "111131",
                        "units": units,
                    }
                ],
                region="その他",
                shoguu_kaizen_level="none",
            )
        )

        assert result_1.success is True
        assert result_2.success is True
        assert result_1.total_amount > result_2.total_amount

        expected_1 = int(units * REGION_UNIT_PRICE["1級地"])
        expected_2 = int(units * REGION_UNIT_PRICE["その他"])
        assert result_1.total_amount == expected_1
        assert result_2.total_amount == expected_2

    @pytest.mark.asyncio
    async def test_empty_records_fails(self):
        """TC-8: 空records で失敗（success=False）"""
        pipeline = CareBillingPipeline()
        result = await pipeline.run(_base_input(records=[]))

        assert result.success is False
        assert result.error is not None
        assert "空" in result.error or "service_records" in result.error
