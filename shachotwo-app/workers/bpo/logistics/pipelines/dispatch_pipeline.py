"""物流・運送業 配車計画AIパイプライン

Step 1: order_reader        配送依頼データ取得（直渡し or テキスト抽出）
Step 2: driver_matcher      ドライバー・車両のマッチング（積載量・免許種別）
Step 3: route_optimizer     ルート最適化（配送順序・総距離最小化）
Step 4: compliance_checker  労働法コンプライアンス（2024年問題）
Step 5: output_validator    バリデーション
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 2024年問題: 改正労働基準法（物流特則）
# ---------------------------------------------------------------------------
ANNUAL_OVERTIME_LIMIT = 960    # 年間残業時間上限（時間）
MONTHLY_OVERTIME_LIMIT = 80    # 月間残業上限（時間）
REST_TIME_REQUIRED = 11        # 継続休息時間（時間）
DRIVING_LIMIT_DAILY = 9        # 1日の最大運転時間（時間）
DRIVING_LIMIT_4DAYS = 9 * 4   # 4日間の最大合計

# ---------------------------------------------------------------------------
# 車両種別と積載量（kg）
# ---------------------------------------------------------------------------
VEHICLE_TYPES: dict[str, int] = {
    "軽貨物": 350,
    "小型トラック": 2_000,
    "中型トラック": 5_000,
    "大型トラック": 10_000,
    "トレーラー": 20_000,
}

# ---------------------------------------------------------------------------
# 免許種別と対応車両
# 「上位免許は下位車両を運転可能」ルール
# ---------------------------------------------------------------------------
LICENSE_TYPES: list[str] = ["普通", "中型", "大型", "大型特殊", "けん引"]

# 車両種別ごとに必要な最低免許レベル（インデックス）
_VEHICLE_LICENSE_REQUIRED: dict[str, int] = {
    "軽貨物": 0,        # 普通以上
    "小型トラック": 0,  # 普通以上
    "中型トラック": 1,  # 中型以上
    "大型トラック": 2,  # 大型以上
    "トレーラー": 4,    # けん引以上
}


def _license_level(license_type: str) -> int:
    """免許種別のレベルを返す（高いほど上位）"""
    try:
        return LICENSE_TYPES.index(license_type)
    except ValueError:
        return -1


def _can_drive(driver_license: str, vehicle_type: str) -> bool:
    """ドライバーの免許で対象車両を運転できるか確認する"""
    required = _VEHICLE_LICENSE_REQUIRED.get(vehicle_type, 0)
    actual = _license_level(driver_license)
    return actual >= required


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------
@dataclass
class DispatchPipelineResult:
    """配車計画パイプラインの実行結果"""
    dispatch_plan: list[dict[str, Any]] = field(default_factory=list)
    compliance_alerts: list[str] = field(default_factory=list)
    total_distance_km: float = 0.0
    steps_executed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unmatched_orders: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------
class DispatchPipeline:
    """
    物流・運送業 配車計画AIパイプライン

    Step 1: order_reader        配送依頼データ取得
    Step 2: driver_matcher      ドライバー・車両マッチング
    Step 3: route_optimizer     ルート最適化
    Step 4: compliance_checker  2024年問題コンプライアンスチェック
    Step 5: output_validator    バリデーション
    """

    async def run(self, input_data: dict[str, Any]) -> DispatchPipelineResult:
        """
        パイプラインを実行する。

        Args:
            input_data: 配送依頼・ドライバー情報を含む辞書
                {
                    "orders": [...],    # 配送依頼リスト
                    "drivers": [...],   # ドライバーリスト
                    "dispatch_date": "YYYY-MM-DD",
                }

        Returns:
            DispatchPipelineResult
        """
        result = DispatchPipelineResult()

        # Step 1: order_reader
        orders, drivers, dispatch_date = self._step1_order_reader(input_data, result)
        result.steps_executed.append("order_reader")

        if result.errors:
            return result

        # Step 2: driver_matcher
        driver_order_map, unmatched = self._step2_driver_matcher(orders, drivers, result)
        result.steps_executed.append("driver_matcher")
        result.unmatched_orders = unmatched

        # Step 3: route_optimizer
        dispatch_plan, total_distance = self._step3_route_optimizer(
            driver_order_map, drivers, orders
        )
        result.dispatch_plan = dispatch_plan
        result.total_distance_km = total_distance
        result.steps_executed.append("route_optimizer")

        # Step 4: compliance_checker
        alerts = self._step4_compliance_checker(drivers, dispatch_plan, dispatch_date)
        result.compliance_alerts = alerts
        result.steps_executed.append("compliance_checker")

        # Step 5: output_validator
        self._step5_output_validator(result)
        result.steps_executed.append("output_validator")

        return result

    # ------------------------------------------------------------------
    # Step 1: order_reader
    # ------------------------------------------------------------------
    def _step1_order_reader(
        self,
        input_data: dict[str, Any],
        result: DispatchPipelineResult,
    ) -> tuple[list[dict], list[dict], str]:
        """配送依頼データを取得・検証する"""
        orders: list[dict] = input_data.get("orders", [])
        drivers: list[dict] = input_data.get("drivers", [])
        dispatch_date: str = input_data.get("dispatch_date", "")

        if not orders:
            result.errors.append("配送依頼が0件です。ordersを指定してください。")
        if not drivers:
            result.errors.append("ドライバーが0件です。driversを指定してください。")
        if not dispatch_date:
            result.errors.append("配車日(dispatch_date)が指定されていません。")

        logger.info(
            "Step1 order_reader: orders=%d drivers=%d date=%s",
            len(orders), len(drivers), dispatch_date,
        )
        return orders, drivers, dispatch_date

    # ------------------------------------------------------------------
    # Step 2: driver_matcher
    # ------------------------------------------------------------------
    def _step2_driver_matcher(
        self,
        orders: list[dict],
        drivers: list[dict],
        result: DispatchPipelineResult,
    ) -> tuple[dict[str, list[dict]], list[str]]:
        """
        各配送依頼に対して適合するドライバーを決定する。

        マッチング条件:
          1. 積載量チェック: order.weight_kg <= VEHICLE_TYPES[driver.vehicle_type]
          2. 免許種別チェック: 車両に必要な免許を所持しているか
          3. 残業時間チェック: monthly_overtime_hours > MONTHLY_OVERTIME_LIMIT → アラート
        """
        # urgentを先に処理するため並び替え
        sorted_orders = sorted(
            orders,
            key=lambda o: (0 if o.get("priority") == "urgent" else 1),
        )

        # ドライバーIDとドライバー情報のマッピング
        driver_map: dict[str, dict] = {d["driver_id"]: d for d in drivers}

        # ドライバーごとの割り当て配送リスト
        driver_order_map: dict[str, list[dict]] = {d["driver_id"]: [] for d in drivers}

        # ドライバーごとの現在の積載量トラッカー
        driver_load_tracker: dict[str, float] = {d["driver_id"]: 0.0 for d in drivers}

        unmatched_orders: list[str] = []

        for order in sorted_orders:
            order_id = order.get("order_id", "UNKNOWN")
            weight_kg = float(order.get("weight_kg", 0))
            matched = False

            # 残業超過フラグを持つドライバーは後回し（後で別途アラートを出す）
            available_drivers = [
                d for d in drivers
                if _can_drive(d["license_type"], d["vehicle_type"])
                and VEHICLE_TYPES.get(d["vehicle_type"], 0) >= weight_kg
            ]

            if not available_drivers:
                unmatched_orders.append(order_id)
                logger.warning(
                    "Step2: 注文 %s (%.0fkg) に適合するドライバーがいません",
                    order_id, weight_kg,
                )
                continue

            # 積載量の余裕が最も多いドライバーから割り当て（貪欲法）
            best_driver = None
            best_remaining = -1.0
            for driver in available_drivers:
                capacity = float(VEHICLE_TYPES.get(driver["vehicle_type"], 0))
                current_load = driver_load_tracker[driver["driver_id"]]
                remaining = capacity - current_load - weight_kg
                if remaining >= 0 and remaining > best_remaining:
                    best_remaining = remaining
                    best_driver = driver

            if best_driver is None:
                unmatched_orders.append(order_id)
                logger.warning(
                    "Step2: 注文 %s (%.0fkg) — 積載量不足のため割り当て不可",
                    order_id, weight_kg,
                )
                continue

            driver_id = best_driver["driver_id"]
            driver_order_map[driver_id].append(order)
            driver_load_tracker[driver_id] += weight_kg
            matched = True

            logger.debug(
                "Step2: 注文 %s → ドライバー %s (%s)",
                order_id, driver_id, best_driver["vehicle_type"],
            )

        logger.info(
            "Step2 driver_matcher: 割り当て完了 / 未割り当て %d 件",
            len(unmatched_orders),
        )
        return driver_order_map, unmatched_orders

    # ------------------------------------------------------------------
    # Step 3: route_optimizer
    # ------------------------------------------------------------------
    def _step3_route_optimizer(
        self,
        driver_order_map: dict[str, list[dict]],
        drivers: list[dict],
        all_orders: list[dict],
    ) -> tuple[list[dict], float]:
        """
        各ドライバーの配送ルートを最適化する。

        簡易実装:
          - urgentを先頭に、normalをtime_window.startの昇順で並べる
          - 推定距離: 1件あたり平均15kmとして計算
        """
        DISTANCE_PER_STOP_KM = 15.0  # 1配送先あたりの推定距離（往復考慮）

        driver_map: dict[str, dict] = {d["driver_id"]: d for d in drivers}
        dispatch_plan: list[dict] = []
        total_distance = 0.0

        for driver_id, orders in driver_order_map.items():
            if not orders:
                continue

            driver_info = driver_map.get(driver_id, {})

            # urgentを先頭、その後time_window.startでソート
            sorted_stops = sorted(
                orders,
                key=lambda o: (
                    0 if o.get("priority") == "urgent" else 1,
                    o.get("time_window", {}).get("start", "99:99"),
                ),
            )

            estimated_km = float(len(sorted_stops)) * DISTANCE_PER_STOP_KM
            total_distance += estimated_km

            plan_entry = {
                "driver_id": driver_id,
                "driver_name": driver_info.get("name", ""),
                "vehicle_type": driver_info.get("vehicle_type", ""),
                "stops": [
                    {
                        "order_id": o.get("order_id"),
                        "destination": o.get("destination"),
                        "weight_kg": o.get("weight_kg"),
                        "time_window": o.get("time_window"),
                        "priority": o.get("priority", "normal"),
                    }
                    for o in sorted_stops
                ],
                "estimated_distance_km": estimated_km,
                "estimated_driving_hours": round(estimated_km / 40.0, 1),  # 平均時速40km
            }
            dispatch_plan.append(plan_entry)

        logger.info(
            "Step3 route_optimizer: %d ルート生成 合計推定 %.1f km",
            len(dispatch_plan), total_distance,
        )
        return dispatch_plan, total_distance

    # ------------------------------------------------------------------
    # Step 4: compliance_checker
    # ------------------------------------------------------------------
    def _step4_compliance_checker(
        self,
        drivers: list[dict],
        dispatch_plan: list[dict],
        dispatch_date: str,
    ) -> list[str]:
        """
        2024年問題（改正労働基準法 物流特則）コンプライアンスチェック。

        チェック項目:
          1. 月間残業80時間超 → アラート
          2. 継続休息11時間未確保 → アラート
          3. 1日の運転予定9時間超 → アラート
        """
        alerts: list[str] = []

        # plan をdriver_idで引けるよう変換
        plan_map: dict[str, dict] = {p["driver_id"]: p for p in dispatch_plan}

        for driver in drivers:
            driver_id = driver["driver_id"]
            name = driver.get("name", driver_id)
            monthly_ot = float(driver.get("monthly_overtime_hours", 0))
            last_rest_end_str: str | None = driver.get("last_rest_end")

            # --- チェック1: 月間残業上限 ---
            if monthly_ot > MONTHLY_OVERTIME_LIMIT:
                alerts.append(
                    f"{name} の残業時間（{monthly_ot:.1f}時間）が"
                    f"2024年問題上限（{MONTHLY_OVERTIME_LIMIT}時間/月）を超過しています"
                )
            elif monthly_ot >= MONTHLY_OVERTIME_LIMIT * 0.9:
                # 90%超: 近づいている警告
                alerts.append(
                    f"{name} の残業時間が2024年問題上限に近づいています"
                    f"（{monthly_ot:.1f}/{MONTHLY_OVERTIME_LIMIT}時間）"
                )

            # --- チェック2: 継続休息11時間 ---
            if last_rest_end_str:
                try:
                    last_rest_end = datetime.fromisoformat(last_rest_end_str)
                    dispatch_dt = datetime.fromisoformat(f"{dispatch_date}T00:00:00")
                    rest_hours = (dispatch_dt - last_rest_end).total_seconds() / 3600
                    if rest_hours < REST_TIME_REQUIRED:
                        alerts.append(
                            f"{name} の休息時間が不足しています"
                            f"（{rest_hours:.1f}時間 < 必要{REST_TIME_REQUIRED}時間）"
                        )
                except ValueError:
                    logger.warning("ドライバー %s の last_rest_end 解析失敗: %s", name, last_rest_end_str)

            # --- チェック3: 1日の運転時間 ---
            plan = plan_map.get(driver_id)
            if plan:
                estimated_driving = plan.get("estimated_driving_hours", 0)
                if estimated_driving > DRIVING_LIMIT_DAILY:
                    alerts.append(
                        f"{name} の運転時間が上限超過"
                        f"（推定 {estimated_driving:.1f}時間 > 上限 {DRIVING_LIMIT_DAILY}時間）"
                    )

        logger.info("Step4 compliance_checker: %d 件のアラート", len(alerts))
        return alerts

    # ------------------------------------------------------------------
    # Step 5: output_validator
    # ------------------------------------------------------------------
    def _step5_output_validator(self, result: DispatchPipelineResult) -> None:
        """生成された配車計画の整合性を検証する"""
        if not result.dispatch_plan and not result.unmatched_orders:
            result.errors.append("配車計画が生成されませんでした。")
            return

        for plan in result.dispatch_plan:
            if not plan.get("driver_id"):
                result.errors.append("配車計画にdriver_idが含まれていません。")
            if not isinstance(plan.get("stops"), list):
                result.errors.append(
                    f"ドライバー {plan.get('driver_id')} の stops が不正です。"
                )

        logger.info(
            "Step5 output_validator: dispatch_plan=%d unmatched=%d errors=%d",
            len(result.dispatch_plan),
            len(result.unmatched_orders),
            len(result.errors),
        )


async def run_dispatch_pipeline(company_id: str = "", input_data: dict | None = None, **kwargs) -> DispatchPipelineResult:
    """物流配車計画パイプラインを実行する便利関数"""
    pipeline = DispatchPipeline()
    return await pipeline.run(input_data or {})
