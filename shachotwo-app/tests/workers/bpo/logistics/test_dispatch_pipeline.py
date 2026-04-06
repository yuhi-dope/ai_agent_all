"""物流・運送業 配車計画AIパイプライン テスト"""
import pytest

from workers.bpo.logistics.pipelines.dispatch_pipeline import (
    DispatchPipeline,
    DispatchPipelineResult,
    MONTHLY_OVERTIME_LIMIT,
    REST_TIME_REQUIRED,
    DRIVING_LIMIT_DAILY,
    VEHICLE_TYPES,
)


# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------
def _make_input(
    orders: list[dict] | None = None,
    drivers: list[dict] | None = None,
    dispatch_date: str = "2026-03-20",
) -> dict:
    """標準的な input_data を組み立てるヘルパー"""
    if orders is None:
        orders = [
            {
                "order_id": "ORD001",
                "destination": "東京都港区xxx",
                "weight_kg": 500,
                "volume_m3": 1.5,
                "time_window": {"start": "09:00", "end": "12:00"},
                "priority": "normal",
            }
        ]
    if drivers is None:
        drivers = [
            {
                "driver_id": "D001",
                "name": "田中太郎",
                "vehicle_type": "中型トラック",
                "license_type": "中型",
                "monthly_overtime_hours": 40.0,
                "last_rest_end": "2026-03-19T20:00",
            }
        ]
    return {"orders": orders, "drivers": drivers, "dispatch_date": dispatch_date}


# ---------------------------------------------------------------------------
# テスト1: 直渡し入力で全5ステップ正常完了
# ---------------------------------------------------------------------------
class TestDirectOrdersSuccess:
    """test_direct_orders_success: 直渡しで全5ステップ正常完了"""

    @pytest.mark.asyncio
    async def test_direct_orders_success(self):
        pipeline = DispatchPipeline()
        input_data = _make_input()

        result = await pipeline.run(input_data)

        assert isinstance(result, DispatchPipelineResult)
        assert len(result.errors) == 0, f"予期しないエラー: {result.errors}"
        assert len(result.dispatch_plan) > 0
        assert result.total_distance_km > 0


# ---------------------------------------------------------------------------
# テスト2: 積載量オーバーでマッチングNG
# ---------------------------------------------------------------------------
class TestVehicleCapacityCheck:
    """test_vehicle_capacity_check: 積載量オーバーのオーダーは未割り当てになる"""

    @pytest.mark.asyncio
    async def test_vehicle_capacity_check(self):
        # 軽貨物（350kg）に 1,000kg の荷物を割り当てようとする
        orders = [
            {
                "order_id": "OVER001",
                "destination": "大阪市中央区xxx",
                "weight_kg": 1_000,
                "volume_m3": 3.0,
                "time_window": {"start": "10:00", "end": "14:00"},
                "priority": "normal",
            }
        ]
        drivers = [
            {
                "driver_id": "D010",
                "name": "軽貨物ドライバー",
                "vehicle_type": "軽貨物",
                "license_type": "普通",
                "monthly_overtime_hours": 20.0,
                "last_rest_end": "2026-03-19T20:00",
            }
        ]
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input(orders=orders, drivers=drivers))

        # 積載量超過のためマッチング不可 → unmatched_orders に含まれる
        assert "OVER001" in result.unmatched_orders
        # dispatch_plan にはそのオーダーが含まれない
        all_order_ids = [
            stop["order_id"]
            for plan in result.dispatch_plan
            for stop in plan["stops"]
        ]
        assert "OVER001" not in all_order_ids


# ---------------------------------------------------------------------------
# テスト3: 月80時間超で2024年問題アラート
# ---------------------------------------------------------------------------
class TestMonthlyOvertimeAlert:
    """test_monthly_overtime_alert: 月80時間超の場合アラートが出る"""

    @pytest.mark.asyncio
    async def test_monthly_overtime_alert(self):
        drivers = [
            {
                "driver_id": "D020",
                "name": "残業超過ドライバー",
                "vehicle_type": "中型トラック",
                "license_type": "中型",
                "monthly_overtime_hours": MONTHLY_OVERTIME_LIMIT + 5,  # 85時間
                "last_rest_end": "2026-03-19T20:00",
            }
        ]
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input(drivers=drivers))

        overtime_alerts = [a for a in result.compliance_alerts if "残業時間" in a and "超過" in a]
        assert len(overtime_alerts) > 0, f"残業超過アラートがない: {result.compliance_alerts}"
        assert "残業超過ドライバー" in overtime_alerts[0]


# ---------------------------------------------------------------------------
# テスト4: 継続休息11時間未確保アラート
# ---------------------------------------------------------------------------
class TestRestTimeAlert:
    """test_rest_time_alert: 前回休息終了から11時間未満の場合アラートが出る"""

    @pytest.mark.asyncio
    async def test_rest_time_alert(self):
        # dispatch_date = 2026-03-20, last_rest_end = 前日22:00 → 休息2時間しかない
        drivers = [
            {
                "driver_id": "D030",
                "name": "休息不足ドライバー",
                "vehicle_type": "中型トラック",
                "license_type": "中型",
                "monthly_overtime_hours": 30.0,
                "last_rest_end": "2026-03-19T22:00",  # 0:00まで2時間しか休んでいない
            }
        ]
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input(drivers=drivers))

        rest_alerts = [a for a in result.compliance_alerts if "休息時間が不足" in a]
        assert len(rest_alerts) > 0, f"休息不足アラートがない: {result.compliance_alerts}"
        assert "休息不足ドライバー" in rest_alerts[0]


# ---------------------------------------------------------------------------
# テスト5: 配車計画が生成される
# ---------------------------------------------------------------------------
class TestDispatchPlanGenerated:
    """test_dispatch_plan_generated: 配車計画が正しい構造で生成される"""

    @pytest.mark.asyncio
    async def test_dispatch_plan_generated(self):
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input())

        assert len(result.dispatch_plan) > 0

        plan = result.dispatch_plan[0]
        assert "driver_id" in plan
        assert "driver_name" in plan
        assert "vehicle_type" in plan
        assert "stops" in plan
        assert isinstance(plan["stops"], list)
        assert "estimated_distance_km" in plan
        assert plan["estimated_distance_km"] > 0

        stop = plan["stops"][0]
        assert "order_id" in stop
        assert "destination" in stop
        assert "priority" in stop


# ---------------------------------------------------------------------------
# テスト6: 全5ステップが確認される
# ---------------------------------------------------------------------------
class TestAll5StepsExecuted:
    """test_all_5_steps_executed: 全5ステップが実行される"""

    @pytest.mark.asyncio
    async def test_all_5_steps_executed(self):
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input())

        expected_steps = [
            "order_reader",
            "driver_matcher",
            "route_optimizer",
            "compliance_checker",
            "output_validator",
        ]
        for step in expected_steps:
            assert step in result.steps_executed, f"ステップ '{step}' が実行されていません"

        assert result.steps_executed == expected_steps


# ---------------------------------------------------------------------------
# テスト7: 複数ドライバーへの割り当て
# ---------------------------------------------------------------------------
class TestMultipleDriversAssigned:
    """test_multiple_drivers_assigned: 複数ドライバーに配送が割り当てられる"""

    @pytest.mark.asyncio
    async def test_multiple_drivers_assigned(self):
        # 2台のトラックに収まるよう荷物を設計
        orders = [
            {
                "order_id": "ORD101",
                "destination": "東京都新宿区xxx",
                "weight_kg": 4_000,  # 中型トラックの上限近く
                "volume_m3": 10.0,
                "time_window": {"start": "09:00", "end": "12:00"},
                "priority": "normal",
            },
            {
                "order_id": "ORD102",
                "destination": "東京都渋谷区xxx",
                "weight_kg": 4_000,  # もう1台分
                "volume_m3": 10.0,
                "time_window": {"start": "13:00", "end": "17:00"},
                "priority": "normal",
            },
        ]
        drivers = [
            {
                "driver_id": "D101",
                "name": "山田一郎",
                "vehicle_type": "中型トラック",
                "license_type": "中型",
                "monthly_overtime_hours": 30.0,
                "last_rest_end": "2026-03-19T20:00",
            },
            {
                "driver_id": "D102",
                "name": "鈴木次郎",
                "vehicle_type": "中型トラック",
                "license_type": "中型",
                "monthly_overtime_hours": 30.0,
                "last_rest_end": "2026-03-19T20:00",
            },
        ]
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input(orders=orders, drivers=drivers))

        # 2台のドライバー両方にstopsが割り当てられていることを確認
        active_drivers = [p for p in result.dispatch_plan if len(p["stops"]) > 0]
        assert len(active_drivers) >= 2, (
            f"複数ドライバーへの割り当てがされていません: "
            f"{[(p['driver_id'], len(p['stops'])) for p in result.dispatch_plan]}"
        )


# ---------------------------------------------------------------------------
# テスト8: 緊急配送の優先処理
# ---------------------------------------------------------------------------
class TestUrgentOrderPrioritized:
    """test_urgent_order_prioritized: urgentオーダーがnormalより先に処理される"""

    @pytest.mark.asyncio
    async def test_urgent_order_prioritized(self):
        orders = [
            {
                "order_id": "NORMAL001",
                "destination": "東京都品川区xxx",
                "weight_kg": 200,
                "volume_m3": 0.5,
                "time_window": {"start": "09:00", "end": "12:00"},
                "priority": "normal",
            },
            {
                "order_id": "URGENT001",
                "destination": "東京都千代田区xxx",
                "weight_kg": 100,
                "volume_m3": 0.3,
                "time_window": {"start": "10:00", "end": "11:00"},
                "priority": "urgent",
            },
            {
                "order_id": "NORMAL002",
                "destination": "東京都江東区xxx",
                "weight_kg": 300,
                "volume_m3": 0.8,
                "time_window": {"start": "08:00", "end": "10:00"},
                "priority": "normal",
            },
        ]
        drivers = [
            {
                "driver_id": "D200",
                "name": "佐藤三郎",
                "vehicle_type": "小型トラック",
                "license_type": "普通",
                "monthly_overtime_hours": 20.0,
                "last_rest_end": "2026-03-19T20:00",
            }
        ]
        pipeline = DispatchPipeline()
        result = await pipeline.run(_make_input(orders=orders, drivers=drivers))

        assert len(result.dispatch_plan) > 0
        plan = result.dispatch_plan[0]
        stops = plan["stops"]

        # 最初のstopがurgentであることを確認
        assert stops[0]["order_id"] == "URGENT001", (
            f"urgentオーダーが先頭にない: {[s['order_id'] for s in stops]}"
        )
