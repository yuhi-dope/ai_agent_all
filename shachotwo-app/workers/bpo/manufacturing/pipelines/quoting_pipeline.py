"""製造業 見積AIパイプライン

4ステップ:
  Step 1: spec_reader         仕様書・図面データ取得（直渡し or テキスト抽出）
  Step 2: process_estimator   工程・工数推定（材料費・加工費・外注費の分解）
  Step 3: price_calculator    見積金額計算（原価積み上げ + 利益率）
  Step 4: output_validator    バリデーション
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from llm.client import LLMClient, LLMTask, ModelTier

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 加工種別デフォルト時間単価（円/時間）
MACHINING_HOURLY_RATES: dict[str, int] = {
    "旋盤加工": 8_000,
    "フライス加工": 9_000,
    "研削加工": 10_000,
    "溶接": 7_000,
    "板金加工": 6_000,
    "プレス加工": 5_000,
    "樹脂成型": 8_000,
    "default": 7_500,
}

# 材料区分
MATERIAL_TYPES: list[str] = ["SS400", "SUS304", "SUS316", "A5052", "C1100", "樹脂", "その他"]

# 利益率区分
PROFIT_RATES: dict[str, float] = {
    "standard": 0.25,    # 標準: 25%
    "rush": 0.35,        # 急ぎ案件: 35%（段取り費加算）
    "large_lot": 0.15,   # 大量発注: 15%（コスト優先）
    "prototype": 0.40,   # 試作: 40%
}

# デフォルト受注区分
DEFAULT_ORDER_TYPE = "standard"

# テキスト抽出時のデフォルト信頼度（抽出失敗時）
EXTRACTION_FALLBACK_CONFIDENCE = 0.5


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class QuotingPipelineResult:
    """見積パイプラインの最終結果"""
    product_name: str
    material: str
    quantity: int
    order_type: str
    quote_items: list[dict] = field(default_factory=list)
    total_material_cost: int = 0
    total_processing_cost: int = 0
    total_amount: int = 0
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    steps_executed: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------

class QuotingPipeline:
    """
    製造業 見積AIパイプライン

    Step 1: spec_reader         仕様書・図面データ取得（直渡し or テキスト抽出）
    Step 2: process_estimator   工程・工数推定（材料費・加工費・外注費の分解）
    Step 3: price_calculator    見積金額計算（原価積み上げ + 利益率）
    Step 4: output_validator    バリデーション
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    # ------------------------------------------------------------------
    # Step 1: spec_reader
    # ------------------------------------------------------------------

    async def spec_reader(self, input_data: dict) -> dict:
        """
        仕様書・図面データを取得して構造化する。

        input_data には以下のいずれかを含む:
        - 直渡し形式: product_name / material / quantity / processes / ... が揃っている
        - テキスト形式: "text" キーに仕様テキスト
        - ファイル形式: "file_path" キーにファイルパス（テキストとして読み込み）

        Returns:
            直渡しの場合: input_data をそのまま返す
            テキスト/ファイルの場合: LLM 抽出済み構造化データを返す
        """
        # テキスト入力の場合
        if "text" in input_data or "file_path" in input_data:
            raw_text = input_data.get("text", "")
            if not raw_text and "file_path" in input_data:
                try:
                    with open(input_data["file_path"], encoding="utf-8") as f:
                        raw_text = f.read()
                except OSError as exc:
                    logger.warning(f"spec_reader: cannot read file {input_data['file_path']}: {exc}")
                    raw_text = ""

            extracted = await self._run_structured_extractor(raw_text)
            return extracted

        # 直渡し形式
        return input_data

    # ------------------------------------------------------------------
    # Step 2: process_estimator
    # ------------------------------------------------------------------

    async def process_estimator(self, spec: dict) -> dict:
        """
        工程ごとに材料費・加工費を分解してリストアップする。

        spec は Step 1 の出力（構造化済みデータ）。
        processes がない場合は警告を出してデフォルトを使う。
        """
        processes = spec.get("processes", [])
        material_weight_kg = float(spec.get("material_weight_kg", 0))
        material_unit_price = float(spec.get("material_unit_price", 0))
        quantity = int(spec.get("quantity", 1))

        # 工程別明細を構築
        quote_items: list[dict] = []

        for proc in processes:
            process_name: str = proc.get("process_name", "不明")
            estimated_hours: float = float(proc.get("estimated_hours", 0))
            setup_hours: float = float(proc.get("setup_hours", 0))
            hourly_rate: int = MACHINING_HOURLY_RATES.get(
                process_name, MACHINING_HOURLY_RATES["default"]
            )
            total_hours = estimated_hours + setup_hours
            processing_cost_per_unit = hourly_rate * total_hours
            processing_cost_total = int(processing_cost_per_unit * quantity)

            quote_items.append({
                "process_name": process_name,
                "hourly_rate": hourly_rate,
                "estimated_hours": estimated_hours,
                "setup_hours": setup_hours,
                "total_hours_per_unit": total_hours,
                "processing_cost_per_unit": int(processing_cost_per_unit),
                "processing_cost_total": processing_cost_total,
            })

        # 材料費（全体）
        total_material_cost = int(material_weight_kg * material_unit_price * quantity)

        return {
            **spec,
            "quote_items": quote_items,
            "total_material_cost": total_material_cost,
        }

    # ------------------------------------------------------------------
    # Step 3: price_calculator
    # ------------------------------------------------------------------

    async def price_calculator(self, processed: dict) -> dict:
        """
        原価積み上げ + 利益率で最終見積金額を算出する。

        total_amount = cost_subtotal / (1 - profit_rate)
        """
        order_type: str = processed.get("order_type", DEFAULT_ORDER_TYPE)
        profit_rate_key = order_type if order_type in PROFIT_RATES else DEFAULT_ORDER_TYPE
        profit_rate = Decimal(str(PROFIT_RATES[profit_rate_key]))

        total_material_cost: int = processed.get("total_material_cost", 0)

        # 加工費合計
        quote_items: list[dict] = processed.get("quote_items", [])
        total_processing_cost: int = sum(
            item.get("processing_cost_total", 0) for item in quote_items
        )

        cost_subtotal = Decimal(str(total_material_cost + total_processing_cost))
        if profit_rate >= Decimal("1"):
            # 安全チェック
            total_amount = int(cost_subtotal)
        else:
            total_amount = int(cost_subtotal / (Decimal("1") - profit_rate))

        return {
            **processed,
            "total_processing_cost": total_processing_cost,
            "cost_subtotal": int(cost_subtotal),
            "profit_rate": float(profit_rate),
            "total_amount": total_amount,
        }

    # ------------------------------------------------------------------
    # Step 4: output_validator
    # ------------------------------------------------------------------

    async def output_validator(self, calculated: dict) -> QuotingPipelineResult:
        """
        計算結果をバリデーションして QuotingPipelineResult を返す。
        """
        warnings: list[str] = list(calculated.get("warnings", []))
        confidence: float = float(calculated.get("confidence", 1.0))

        # 金額が 0 の場合は警告
        if calculated.get("total_amount", 0) == 0:
            warnings.append("total_amount が 0 です。入力データを確認してください。")

        # 工程がない場合は警告
        if not calculated.get("quote_items"):
            warnings.append("工程データがありません。見積精度が低下します。")

        # 利益率が設定外の場合
        order_type = calculated.get("order_type", DEFAULT_ORDER_TYPE)
        if order_type not in PROFIT_RATES:
            warnings.append(
                f"order_type '{order_type}' が未定義です。'{DEFAULT_ORDER_TYPE}' を適用しました。"
            )

        steps_executed = calculated.get("steps_executed", [
            "spec_reader",
            "process_estimator",
            "price_calculator",
            "output_validator",
        ])

        return QuotingPipelineResult(
            product_name=calculated.get("product_name", ""),
            material=calculated.get("material", ""),
            quantity=int(calculated.get("quantity", 1)),
            order_type=order_type,
            quote_items=calculated.get("quote_items", []),
            total_material_cost=calculated.get("total_material_cost", 0),
            total_processing_cost=calculated.get("total_processing_cost", 0),
            total_amount=calculated.get("total_amount", 0),
            confidence=confidence,
            warnings=warnings,
            steps_executed=steps_executed,
        )

    # ------------------------------------------------------------------
    # メインエントリポイント
    # ------------------------------------------------------------------

    async def run(self, input_data: dict) -> QuotingPipelineResult:
        """
        4 ステップを順に実行して見積結果を返す。

        input_data の形式:
        {
            "product_name": "ステンレス角フランジ",
            "material": "SUS304",
            "quantity": 10,
            "processes": [
                {"process_name": "旋盤加工", "estimated_hours": 2.5, "setup_hours": 0.5},
                {"process_name": "研削加工", "estimated_hours": 1.0, "setup_hours": 0.25},
            ],
            "material_weight_kg": 2.5,
            "material_unit_price": 1_200,
            "order_type": "standard",
            "delivery_days": 14,
        }
        または "text" / "file_path" キーで仕様書テキストを渡す。
        """
        steps_executed: list[str] = []

        # Step 1
        spec = await self.spec_reader(input_data)
        steps_executed.append("spec_reader")

        # Step 2
        processed = await self.process_estimator(spec)
        steps_executed.append("process_estimator")

        # Step 3
        calculated = await self.price_calculator(processed)
        steps_executed.append("price_calculator")

        # steps_executed をペイロードに含めて Step 4 に渡す
        calculated["steps_executed"] = steps_executed + ["output_validator"]

        # Step 4
        result = await self.output_validator(calculated)

        return result

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    async def _run_structured_extractor(self, raw_text: str) -> dict:
        """
        仕様書テキストから製造見積に必要なスペック情報を LLM で抽出する。

        抽出失敗時は confidence を EXTRACTION_FALLBACK_CONFIDENCE に設定して継続。
        """
        if not raw_text.strip():
            return {
                "product_name": "不明",
                "material": "その他",
                "quantity": 1,
                "processes": [],
                "material_weight_kg": 0.0,
                "material_unit_price": 0,
                "order_type": DEFAULT_ORDER_TYPE,
                "confidence": EXTRACTION_FALLBACK_CONFIDENCE,
                "warnings": ["入力テキストが空のため抽出できませんでした。"],
            }

        system_prompt = (
            "あなたは製造業の見積AIアシスタントです。"
            "以下の仕様書テキストから製造見積に必要な情報を抽出し、JSON形式で返してください。\n\n"
            "出力形式（必ずこのJSONのみ）:\n"
            "{\n"
            '  "product_name": "製品名",\n'
            '  "material": "材質（SS400/SUS304/SUS316/A5052/C1100/樹脂/その他）",\n'
            '  "quantity": 数量（整数）,\n'
            '  "processes": [\n'
            '    {"process_name": "工程名", "estimated_hours": 加工時間（小数）, "setup_hours": 段取り時間（小数）}\n'
            "  ],\n"
            '  "material_weight_kg": 材料重量kg（小数）,\n'
            '  "material_unit_price": 材料単価円/kg（整数）,\n'
            '  "order_type": "standard/rush/large_lot/prototype",\n'
            '  "delivery_days": 納期日数（整数、不明なら14）\n'
            "}"
        )

        task = LLMTask(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"仕様書:\n{raw_text}"},
            ],
            tier=ModelTier.STANDARD,
            max_tokens=1024,
            temperature=0.1,
            task_type="manufacturing_spec_extraction",
        )

        try:
            response = await self.llm.generate(task)
            content = response.content.strip()

            # JSON 抽出
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                extracted = json.loads(json_match.group())
            else:
                extracted = json.loads(content)

            # 必須キーのデフォルト補完
            extracted.setdefault("product_name", "不明")
            extracted.setdefault("material", "その他")
            extracted.setdefault("quantity", 1)
            extracted.setdefault("processes", [])
            extracted.setdefault("material_weight_kg", 0.0)
            extracted.setdefault("material_unit_price", 0)
            extracted.setdefault("order_type", DEFAULT_ORDER_TYPE)
            extracted.setdefault("confidence", 0.8)

            return extracted

        except Exception as exc:
            logger.warning(f"_run_structured_extractor: 抽出失敗 ({exc}). フォールバックを使用。")
            return {
                "product_name": "不明",
                "material": "その他",
                "quantity": 1,
                "processes": [],
                "material_weight_kg": 0.0,
                "material_unit_price": 0,
                "order_type": DEFAULT_ORDER_TYPE,
                "confidence": EXTRACTION_FALLBACK_CONFIDENCE,
                "warnings": [f"仕様書テキストからの抽出に失敗しました: {exc}"],
            }


async def run_quoting_pipeline(company_id: str = "", input_data: dict | None = None, **kwargs) -> QuotingPipelineResult:
    """製造業見積パイプラインを実行する便利関数"""
    pipeline = QuotingPipeline()
    return await pipeline.run(input_data or {})


async def run_quoting_engine(company_id: str = "", input_data: dict | None = None, **kwargs):
    """3層エンジンを使った製造業見積パイプライン（新方式）"""
    from workers.bpo.manufacturing.engine import ManufacturingQuotingEngine
    from workers.bpo.manufacturing.models import HearingInput

    data = input_data or {}
    hearing = HearingInput(
        product_name=data.get("product_name", ""),
        specification=data.get("text", data.get("description", "")),
        material=data.get("material", "SS400"),
        quantity=int(data.get("quantity", 1)),
        delivery_days=data.get("delivery_days"),
        surface_treatment=data.get("surface_treatment", ""),
        order_type=data.get("order_type", "standard"),
        notes=data.get("notes", ""),
        company_id=company_id,
        sub_industry=data.get("sub_industry", ""),
        jsic_code=data.get("jsic_code", ""),
        overhead_rate=float(data.get("overhead_rate", 0.15)),
        profit_rate=float(data.get("profit_rate", 0.15)),
    )

    engine = ManufacturingQuotingEngine()
    return await engine.run(hearing)
