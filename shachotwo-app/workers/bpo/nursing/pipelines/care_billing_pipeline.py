"""介護・福祉業 介護報酬請求AIパイプライン

5ステップ:
    Step 1: record_reader       実績記録データ取得（直渡し or テキスト抽出）
    Step 2: service_code_mapper サービスコード→報酬単位数のマッピング
    Step 3: addition_checker    加算チェック（処遇改善加算・特定処遇改善加算・ベースアップ加算）
    Step 4: compliance_checker  介護保険法コンプライアンス（算定要件・記録義務）
    Step 5: output_validator    バリデーション
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 介護サービス区分
SERVICE_TYPES: dict[str, str] = {
    "訪問介護": "11",
    "訪問入浴介護": "12",
    "訪問看護": "13",
    "通所介護": "15",
    "通所リハビリテーション": "16",
    "短期入所生活介護": "21",
    "認知症対応型通所介護": "72",
    "グループホーム": "32",
    "特養": "51",
    "老健": "52",
}

# 処遇改善加算率（訪問介護の例）
SHOGUU_KAIZEN_RATES: dict[str, float] = {
    "I":    0.137,  # 加算I
    "II":   0.100,
    "III":  0.055,
    "IV":   0.033,
    "V1":   0.022,
    "none": 0.0,
}

# 地域区分（単位数×地域単価）
REGION_UNIT_PRICE: dict[str, float] = {
    "1級地": 11.40,   # 東京23区
    "2級地": 11.12,
    "3級地": 11.05,
    "4級地": 10.84,
    "5級地": 10.70,
    "6級地": 10.42,
    "7級地": 10.21,
    "その他": 10.00,
}

# 要介護度別の区分支給限度基準額（単位/月）
CARE_LEVEL_LIMIT_UNITS: dict[int, int] = {
    1: 16_765,
    2: 19_705,
    3: 27_048,
    4: 30_938,
    5: 36_217,
}

# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------


@dataclass
class ServiceRecordResult:
    """処理済みサービス実績の1件"""
    user_id: str
    user_name: str
    service_type: str
    care_level: int
    service_date: str
    units: int
    addition_units: int
    total_units_per_record: int
    amount: int  # 円


@dataclass
class CareBillingPipelineResult:
    """介護報酬請求パイプラインの最終結果"""
    success: bool
    period_year: int
    period_month: int
    facility_type: str
    region: str
    shoguu_kaizen_level: str
    records: list[ServiceRecordResult] = field(default_factory=list)
    total_units: int = 0
    addition_units: int = 0
    total_amount: int = 0          # 請求総額（円）
    rejection_risks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps_completed: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------


class CareBillingPipeline:
    """
    介護報酬請求AIパイプライン

    input_data 形式:
    {
        "service_records": [
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
        ],
        "facility_info": {
            "shoguu_kaizen_level": "I",
            "region": "3級地",
            "facility_type": "訪問介護",
        },
        "period_year": 2026,
        "period_month": 3,
    }
    """

    async def run(self, input_data: dict) -> CareBillingPipelineResult:
        """パイプライン実行（全5ステップ）"""
        period_year: int = input_data.get("period_year", 0)
        period_month: int = input_data.get("period_month", 0)
        facility_info: dict = input_data.get("facility_info", {})
        facility_type: str = facility_info.get("facility_type", "")
        region: str = facility_info.get("region", "その他")
        shoguu_kaizen_level: str = facility_info.get("shoguu_kaizen_level", "none")

        result = CareBillingPipelineResult(
            success=False,
            period_year=period_year,
            period_month=period_month,
            facility_type=facility_type,
            region=region,
            shoguu_kaizen_level=shoguu_kaizen_level,
        )

        try:
            # Step 1: record_reader
            raw_records = await self._step1_record_reader(input_data)
            result.steps_completed.append("record_reader")

            # Step 2: service_code_mapper
            mapped_records = await self._step2_service_code_mapper(raw_records)
            result.steps_completed.append("service_code_mapper")

            # Step 3: addition_checker
            base_units, addition_units, addition_amount = await self._step3_addition_checker(
                mapped_records, shoguu_kaizen_level, region
            )
            result.steps_completed.append("addition_checker")

            # Step 4: compliance_checker
            risks = await self._step4_compliance_checker(
                mapped_records, period_year, period_month
            )
            result.rejection_risks.extend(risks)
            result.steps_completed.append("compliance_checker")

            # Step 5: output_validator
            validated_records, warnings = await self._step5_output_validator(
                mapped_records, region
            )
            result.warnings.extend(warnings)
            result.steps_completed.append("output_validator")

            # 集計
            unit_price = REGION_UNIT_PRICE.get(region, 10.00)
            total_base_amount = int(base_units * unit_price)
            result.records = validated_records
            result.total_units = base_units + addition_units
            result.addition_units = addition_units
            result.total_amount = total_base_amount + addition_amount
            result.success = True

        except ValueError as e:
            result.error = str(e)
            logger.error(f"CareBillingPipeline failed: {e}")

        return result

    # -----------------------------------------------------------------------
    # Step 1: record_reader
    # -----------------------------------------------------------------------

    async def _step1_record_reader(self, input_data: dict) -> list[dict]:
        """実績記録データ取得（直渡し or テキスト抽出）"""
        service_records = input_data.get("service_records", [])
        if not service_records:
            raise ValueError("service_records が空です。処理対象のサービス実績が必要です。")
        return list(service_records)

    # -----------------------------------------------------------------------
    # Step 2: service_code_mapper
    # -----------------------------------------------------------------------

    async def _step2_service_code_mapper(
        self, raw_records: list[dict]
    ) -> list[dict]:
        """サービスコード→報酬単位数のマッピング

        直渡しデータには units が含まれているため、
        存在しない場合はサービス区分・時間から推計する。
        """
        mapped = []
        for record in raw_records:
            rec = dict(record)
            # units が既に設定されている場合はそのまま使用
            if "units" not in rec or rec["units"] is None:
                # サービス時間から単位数を推計（簡易ロジック）
                service_hours: float = float(rec.get("service_hours", 1.0))
                rec["units"] = self._estimate_units_from_hours(
                    rec.get("service_type", ""), service_hours
                )
            mapped.append(rec)
        return mapped

    def _estimate_units_from_hours(self, service_type: str, hours: float) -> int:
        """サービス時間から単位数を推計（フォールバック）"""
        # 訪問介護の簡易マスタ（実装では正式サービスコードマスタを参照）
        if service_type == "訪問介護":
            if hours < 0.5:
                return 167
            elif hours < 1.0:
                return 245
            elif hours < 1.5:
                return 388
            elif hours < 2.0:
                return 452
            else:
                return 579
        elif service_type == "通所介護":
            if hours < 3.0:
                return 386
            elif hours < 5.0:
                return 561
            elif hours < 7.0:
                return 738
            else:
                return 867
        else:
            # デフォルト: 時間×200単位（概算）
            return int(hours * 200)

    # -----------------------------------------------------------------------
    # Step 3: addition_checker
    # -----------------------------------------------------------------------

    async def _step3_addition_checker(
        self,
        records: list[dict],
        shoguu_kaizen_level: str,
        region: str,
    ) -> tuple[int, int, int]:
        """加算チェック（処遇改善加算・特定処遇改善加算・ベースアップ加算）

        Returns:
            (base_units, addition_units, addition_amount_yen)
        """
        base_units = sum(int(r.get("units", 0)) for r in records)

        rate = SHOGUU_KAIZEN_RATES.get(shoguu_kaizen_level, 0.0)
        unit_price = REGION_UNIT_PRICE.get(region, 10.00)

        # 処遇改善加算 = 総単位数 × 加算率 × 地域単価
        addition_amount = int(base_units * rate * unit_price)
        addition_units = int(base_units * rate)

        logger.info(
            f"加算チェック: base_units={base_units}, rate={rate}, "
            f"addition_units={addition_units}, addition_amount={addition_amount}"
        )
        return base_units, addition_units, addition_amount

    # -----------------------------------------------------------------------
    # Step 4: compliance_checker
    # -----------------------------------------------------------------------

    async def _step4_compliance_checker(
        self,
        records: list[dict],
        period_year: int,
        period_month: int,
    ) -> list[str]:
        """介護保険法コンプライアンス（算定要件・記録義務）

        Returns:
            返戻リスクのリスト
        """
        risks: list[str] = []

        for record in records:
            user_name = record.get("user_name", record.get("user_id", "不明"))
            service_date_str: str = str(record.get("service_date", ""))

            # 記録日と提供日の一致確認（記録が翌月以降 → 返戻リスク）
            if service_date_str:
                try:
                    svc_date = date.fromisoformat(service_date_str)
                    # サービス提供月と請求月が一致しているか確認
                    if svc_date.year != period_year or svc_date.month != period_month:
                        risks.append(
                            f"[{user_name}] サービス提供日 {service_date_str} が"
                            f"請求対象期間（{period_year}/{period_month:02d}）と不一致。"
                            "返戻リスクあり。"
                        )
                except ValueError:
                    risks.append(
                        f"[{user_name}] service_date の形式が不正: {service_date_str}"
                    )

            # 要介護度と利用限度額の超過確認
            care_level: int = int(record.get("care_level", 0))
            units: int = int(record.get("units", 0))
            if care_level in CARE_LEVEL_LIMIT_UNITS:
                limit = CARE_LEVEL_LIMIT_UNITS[care_level]
                if units > limit:
                    risks.append(
                        f"[{user_name}] 要介護{care_level} の区分支給限度基準額 "
                        f"{limit:,}単位 を超過（{units:,}単位）。超過分は全額自己負担。"
                    )

            # サービスコードと介護度の適合確認
            service_code: str = str(record.get("service_code", ""))
            service_type: str = record.get("service_type", "")
            if service_type and service_type in SERVICE_TYPES:
                expected_prefix = SERVICE_TYPES[service_type]
                if service_code and not service_code.startswith(expected_prefix):
                    risks.append(
                        f"[{user_name}] サービスコード {service_code} が"
                        f"サービス区分「{service_type}」（{expected_prefix}系）と不一致。"
                    )

        return risks

    # -----------------------------------------------------------------------
    # Step 5: output_validator
    # -----------------------------------------------------------------------

    async def _step5_output_validator(
        self,
        records: list[dict],
        region: str,
    ) -> tuple[list[ServiceRecordResult], list[str]]:
        """バリデーションと ServiceRecordResult リストへの変換"""
        validated: list[ServiceRecordResult] = []
        warnings: list[str] = []

        unit_price = REGION_UNIT_PRICE.get(region, 10.00)

        for record in records:
            user_id: str = str(record.get("user_id", ""))
            user_name: str = record.get("user_name", user_id)
            units: int = int(record.get("units", 0))

            if units <= 0:
                warnings.append(
                    f"[{user_name}] 単位数が0以下です。スキップします。"
                )
                continue

            amount = int(units * unit_price)
            validated.append(
                ServiceRecordResult(
                    user_id=user_id,
                    user_name=user_name,
                    service_type=record.get("service_type", ""),
                    care_level=int(record.get("care_level", 0)),
                    service_date=str(record.get("service_date", "")),
                    units=units,
                    addition_units=0,  # 個別レコードの加算は集計レベルで計上
                    total_units_per_record=units,
                    amount=amount,
                )
            )

        return validated, warnings


async def run_care_billing_pipeline(company_id: str = "", input_data: dict | None = None, **kwargs) -> CareBillingPipelineResult:
    """介護報酬請求パイプラインを実行する便利関数"""
    pipeline = CareBillingPipeline()
    return await pipeline.run(input_data or {})
