"""製造業見積3層エンジン

Layer 0: LLMデフォルト（全24業種をDay 1からカバー）
Layer 1: YAML設定（業種ごとのデータで精度UP）
Layer 2: Pythonプラグイン（計算構造が異なる業種のみ）

解決順序（各データ項目ごと）:
  顧客DB → YAML設定 → LLM推定
"""
from __future__ import annotations

import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from db.supabase import get_service_client
from llm.client import get_llm_client, LLMTask, ModelTier
from llm.prompts.manufacturing import (
    DRAWING_ANALYSIS_PROMPT,
    INDUSTRY_CONTEXTS,
    LAYER0_QUOTING_PROMPT,
)
from workers.bpo.manufacturing.models import (
    AdditionalCostItem,
    CustomerOverrides,
    DrawingAnalysis,
    DrawingFeature,
    HearingInput,
    LayerSource,
    ProcessCostDetail,
    ProcessEstimate,
    QuoteCostBreakdown,
    QuoteResult,
)
from workers.bpo.manufacturing.plugins import get_plugin

logger = logging.getLogger(__name__)

# ─────────────────────────────────────
# YAML設定ローダー
# ─────────────────────────────────────

YAML_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "brain" / "genome" / "data" / "manufacturing_quoting"
)

# JSIC中分類コード → sub_industry マッピング
JSIC_MAP: dict[str, str] = {
    "E-09": "food_chemical",
    "E-10": "food_chemical",
    "E-11": "general",
    "E-12": "general",
    "E-13": "general",
    "E-14": "general",
    "E-15": "general",
    "E-16": "food_chemical",
    "E-17": "general",
    "E-18": "plastics",
    "E-19": "plastics",
    "E-20": "general",
    "E-21": "general",
    "E-22": "metalwork",
    "E-23": "metalwork",
    "E-24": "metalwork",
    "E-25": "metalwork",
    "E-26": "metalwork",
    "E-27": "metalwork",
    "E-28": "electronics",
    "E-29": "electronics",
    "E-30": "general",
    "E-31": "general",
    "E-32": "general",
}


@lru_cache(maxsize=32)
def load_yaml_config(sub_industry: str) -> dict[str, Any] | None:
    """sub_industryに対応するYAML設定を読み込む。なければNone"""
    path = YAML_DIR / f"{sub_industry}.yaml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def clear_yaml_cache() -> None:
    """YAMLキャッシュをクリア（テスト用・管理用）"""
    load_yaml_config.cache_clear()


# ─────────────────────────────────────
# メインエンジン
# ─────────────────────────────────────

class ManufacturingQuotingEngine:
    """3層見積エンジン"""

    async def run(self, hearing: HearingInput) -> QuoteResult:
        """
        メインエントリポイント。

        解決順序: Plugin → YAML → LLM
        """
        company_id = hearing.company_id
        sub_industry = self._resolve_sub_industry(hearing)
        plugin = get_plugin(sub_industry)
        yaml_config = load_yaml_config(sub_industry)
        customer = await self._load_customer_data(company_id)
        layers_used: list[LayerSource] = []

        # === 工程推定 ===
        if plugin and plugin.can_estimate_processes(hearing):
            processes = await plugin.estimate_processes(hearing, yaml_config, customer)
            layers_used.append(LayerSource(
                field="processes", layer="plugin",
                value=plugin.sub_industry_id, confidence=0.7,
            ))
        elif yaml_config and yaml_config.get("process_routing_rules"):
            processes = self._estimate_from_yaml(hearing, yaml_config, customer)
            layers_used.append(LayerSource(
                field="processes", layer="yaml",
                value=sub_industry, confidence=0.7,
            ))
        else:
            processes = await self._estimate_from_llm(hearing, sub_industry)
            layers_used.append(LayerSource(
                field="processes", layer="llm",
                value="gemini-2.5-flash", confidence=0.4,
            ))

        # 共通: バリ取り+検査が含まれていなければ追加
        processes = self._ensure_common_processes(processes)

        # === コスト計算（全レイヤー共通） ===
        costs = self._calculate_costs(
            processes, hearing, yaml_config, customer, layers_used,
        )

        # === プラグイン固有の追加コスト ===
        additional: list[AdditionalCostItem] = []
        if plugin:
            additional = plugin.calculate_additional_costs(hearing, processes)

        # 追加コストを合計に反映
        if additional:
            add_total = sum(
                (a.amount * hearing.quantity if a.per_piece else a.amount)
                for a in additional
            )
            costs.subtotal += add_total
            costs.overhead_cost = int(costs.subtotal * costs.overhead_rate)
            costs.profit = int((costs.subtotal + costs.overhead_cost) * costs.profit_rate)
            costs.total_amount = costs.subtotal + costs.overhead_cost + costs.profit
            costs.unit_price = (
                int(costs.total_amount / hearing.quantity)
                if hearing.quantity > 0 else costs.total_amount
            )

        # === DB保存 ===
        quote_id = ""
        if company_id:
            try:
                quote_id = await self._save_quote(
                    company_id, hearing, processes, costs, additional,
                    sub_industry, layers_used,
                )
            except Exception as e:
                logger.warning(f"見積DB保存失敗: {e}")

        overall_conf = (
            min(l.confidence for l in layers_used) if layers_used else 0.4
        )

        return QuoteResult(
            quote_id=quote_id,
            sub_industry=sub_industry,
            processes=processes,
            costs=costs,
            additional_costs=additional,
            layers_used=layers_used,
            overall_confidence=overall_conf,
        )

    # ─────────────────────────────────
    # sub_industry判定
    # ─────────────────────────────────

    def _resolve_sub_industry(self, hearing: HearingInput) -> str:
        """ヒアリング回答からsub_industryを決定"""
        if hearing.sub_industry:
            return hearing.sub_industry
        if hearing.jsic_code:
            return JSIC_MAP.get(hearing.jsic_code, "general")
        # テキストからの推定はLayer 0に任せる
        return "metalwork"  # デフォルト

    # ─────────────────────────────────
    # Layer 1: YAML設定ベースの工程推定
    # ─────────────────────────────────

    def _estimate_from_yaml(
        self,
        hearing: HearingInput,
        yaml_config: dict[str, Any],
        customer: CustomerOverrides,
    ) -> list[ProcessEstimate]:
        """YAML設定のprocess_routing_rulesから工程推定"""
        rules = yaml_config.get("process_routing_rules", {})
        equipment = yaml_config.get("equipment", {})
        materials = yaml_config.get("materials", {})

        shape = hearing.shape_type or "block"
        shape_rules = rules.get(shape, rules.get("block", {}))
        base_procs = shape_rules.get("base_processes", [])
        cond_procs = shape_rules.get("conditional_processes", [])

        # 材質の加工難易度係数
        mat_data = materials.get(hearing.material, {})
        machinability = mat_data.get("machinability", 1.0)

        processes: list[ProcessEstimate] = []
        order = 1

        for bp in base_procs:
            eq_type = bp["equipment_type"]
            eq_data = equipment.get(eq_type, {})
            eq_name = eq_data.get("name", eq_type)
            base_cycle = eq_data.get("cycle_time_base_min", 10)
            cycle_factor = bp.get("cycle_time_factor", 1.0)
            adjusted_cycle = base_cycle * cycle_factor / machinability

            # 顧客DBのチャージレートでオーバーライド
            charge_rate = customer.charge_rates.get(eq_name)
            setup = bp.get("setup_time_min", eq_data.get("setup_time_min", 30))

            # 顧客の過去実績があればサイクルタイムを上書き
            hist = customer.historical_averages.get(eq_type, {})
            if hist.get("sample_count", 0) >= 3:
                adjusted_cycle = hist.get("avg_cycle_min", adjusted_cycle)
                setup = hist.get("avg_setup_min", setup)

            processes.append(ProcessEstimate(
                sort_order=order,
                process_name=eq_name,
                equipment=eq_name,
                equipment_type=eq_type,
                setup_time_min=setup,
                cycle_time_min=round(adjusted_cycle, 1),
                confidence=0.7,
            ))
            order += 1

        # 条件付き追加工程
        for cp in cond_procs:
            if self._check_condition(cp.get("condition", ""), hearing):
                eq_type = cp["equipment_type"]
                eq_data = equipment.get(eq_type, {})
                eq_name = eq_data.get("name", eq_type)
                base_cycle = eq_data.get("cycle_time_base_min", 10)
                adjusted_cycle = base_cycle * cp.get("cycle_time_factor", 1.0) / machinability
                processes.append(ProcessEstimate(
                    sort_order=order,
                    process_name=eq_name,
                    equipment=eq_name,
                    equipment_type=eq_type,
                    setup_time_min=cp.get("setup_time_min", 30),
                    cycle_time_min=round(adjusted_cycle, 1),
                    confidence=0.6,
                    notes=cp.get("notes", ""),
                ))
                order += 1

        # 外注工程（熱処理・表面処理）
        if hearing.hardness:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="熱処理（外注）",
                equipment="外注",
                equipment_type="heat_treatment",
                setup_time_min=0, cycle_time_min=0,
                is_outsource=True, confidence=0.5,
                notes=f"硬度指定: {hearing.hardness}",
            ))
            order += 1

        if hearing.surface_treatment:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name=f"表面処理（{hearing.surface_treatment}）",
                equipment="外注",
                equipment_type="surface_treatment",
                setup_time_min=0, cycle_time_min=0,
                is_outsource=True, confidence=0.5,
                notes=hearing.surface_treatment,
            ))
            order += 1

        return processes

    def _check_condition(self, condition: str, hearing: HearingInput) -> bool:
        """条件付き工程の判定"""
        if condition == "has_holes":
            return any(
                f.get("feature_type") in ("hole", "thread")
                for f in hearing.features
            )
        if condition == "tight_tolerance":
            tight = hearing.tolerances.get("tight_dimensions", [])
            return bool(tight) or hearing.surface_roughness in (
                "Ra 0.4", "Ra 0.8", "Ra 1.6",
            )
        if condition == "needs_bending":
            return "曲げ" in hearing.specification or "ベンダー" in hearing.specification
        if condition == "needs_welding":
            return "溶接" in hearing.specification
        return False

    # ─────────────────────────────────
    # Layer 0: LLMフォールバック
    # ─────────────────────────────────

    async def _estimate_from_llm(
        self, hearing: HearingInput, sub_industry: str,
    ) -> list[ProcessEstimate]:
        """LLMによる全工程推定（何も設定がない業種のフォールバック）"""
        context = INDUSTRY_CONTEXTS.get(sub_industry, INDUSTRY_CONTEXTS["general"])

        prompt = LAYER0_QUOTING_PROMPT.format(
            sub_industry_name=sub_industry,
            industry_context=context,
            product_name=hearing.product_name,
            specification=hearing.specification,
            material=hearing.material,
            quantity=hearing.quantity,
            delivery_days=hearing.delivery_days or 14,
            quality_standard=hearing.quality_standard,
            finishing=hearing.finishing,
            notes=hearing.notes,
        )

        try:
            client = get_llm_client()
            task = LLMTask(
                messages=[{"role": "user", "content": prompt}],
                tier=ModelTier.FAST,
                max_tokens=2000,
            )
            response = await client.generate(task)
            data = json.loads(response.content)

            processes: list[ProcessEstimate] = []
            for i, proc in enumerate(data.get("processes", []), 1):
                processes.append(ProcessEstimate(
                    sort_order=proc.get("sort_order", i),
                    process_name=proc.get("process_name", "不明"),
                    equipment=proc.get("process_name", ""),
                    equipment_type=proc.get("equipment_type", ""),
                    setup_time_min=float(proc.get("setup_time_min", 30)),
                    cycle_time_min=float(proc.get("cycle_time_min", 10)),
                    is_outsource=proc.get("is_outsource", False),
                    confidence=float(proc.get("confidence", 0.4)),
                    notes=proc.get("notes", ""),
                ))
            return processes

        except Exception as e:
            logger.warning(f"Layer 0 LLM推定失敗: {e}")
            # 最小フォールバック
            return [
                ProcessEstimate(
                    sort_order=1,
                    process_name="加工（詳細不明）",
                    equipment_type="general",
                    setup_time_min=30,
                    cycle_time_min=15,
                    confidence=0.3,
                    notes="LLM推定失敗のためデフォルト値",
                ),
            ]

    # ─────────────────────────────────
    # 共通: コスト計算
    # ─────────────────────────────────

    def _calculate_costs(
        self,
        processes: list[ProcessEstimate],
        hearing: HearingInput,
        yaml_config: dict[str, Any] | None,
        customer: CustomerOverrides,
        layers_used: list[LayerSource],
    ) -> QuoteCostBreakdown:
        """コスト計算（全レイヤー共通の公式）"""
        quantity = hearing.quantity
        overhead_rate = customer.overhead_rate or hearing.overhead_rate
        profit_rate = customer.profit_rate or hearing.profit_rate

        # YAML設定からデフォルト設備マスタを取得
        yaml_equipment = (yaml_config or {}).get("equipment", {})
        yaml_outsource = (yaml_config or {}).get("outsource_costs", {})
        yaml_materials = (yaml_config or {}).get("materials", {})

        # 材料費
        material_cost = self._calc_material_cost(
            hearing, yaml_materials, customer, layers_used,
        )

        # 工程別コスト
        process_details: list[ProcessCostDetail] = []
        total_outsource = 0

        for proc in processes:
            if proc.is_outsource:
                oc = self._calc_outsource_cost(proc, quantity, yaml_outsource)
                total_outsource += oc
                process_details.append(ProcessCostDetail(
                    process_name=proc.process_name,
                    equipment=proc.equipment,
                    setup_time_min=0, cycle_time_min=0, total_time_min=0,
                    charge_rate=0, process_cost=oc, is_outsource=True,
                ))
                continue

            # チャージレート: 顧客DB → YAML → フォールバック5000
            rate = customer.charge_rates.get(proc.process_name)
            source = "customer_db"
            if rate is None:
                eq_data = yaml_equipment.get(proc.equipment_type, {})
                rate = eq_data.get("charge_rate_yen_hour")
                source = "yaml"
            if rate is None:
                rate = 5000
                source = "llm"

            layers_used.append(LayerSource(
                field=f"charge_rate:{proc.process_name}",
                layer=source, value=str(rate),
                confidence=0.9 if source == "customer_db" else 0.7 if source == "yaml" else 0.4,
            ))

            total_time = proc.setup_time_min + (proc.cycle_time_min * quantity)
            process_cost = int(total_time / 60 * rate)

            process_details.append(ProcessCostDetail(
                process_name=proc.process_name,
                equipment=proc.equipment,
                setup_time_min=proc.setup_time_min,
                cycle_time_min=proc.cycle_time_min,
                total_time_min=round(total_time, 1),
                charge_rate=rate,
                process_cost=process_cost,
            ))

        # 集計
        process_total = sum(d.process_cost for d in process_details if not d.is_outsource)
        surface_cost = sum(d.process_cost for d in process_details if "表面処理" in d.process_name)
        inspection_cost = sum(d.process_cost for d in process_details if d.process_name == "検査")

        subtotal = material_cost + process_total + total_outsource
        overhead = int(subtotal * overhead_rate)
        profit = int((subtotal + overhead) * profit_rate)
        total = subtotal + overhead + profit

        return QuoteCostBreakdown(
            material_cost=material_cost,
            process_costs=process_details,
            surface_treatment_cost=surface_cost,
            outsource_cost=total_outsource,
            inspection_cost=inspection_cost,
            subtotal=subtotal,
            overhead_cost=overhead,
            overhead_rate=overhead_rate,
            profit=profit,
            profit_rate=profit_rate,
            total_amount=total,
            unit_price=int(total / quantity) if quantity > 0 else total,
        )

    def _calc_material_cost(
        self,
        hearing: HearingInput,
        yaml_materials: dict,
        customer: CustomerOverrides,
        layers_used: list[LayerSource],
    ) -> int:
        """材料費計算（顧客DB → YAML → フォールバック）"""
        mat_code = hearing.material
        dims = hearing.dimensions
        shape = hearing.shape_type or "block"

        # 材料単価: 顧客DB → YAML → デフォルト
        customer_price = customer.material_prices.get(mat_code)
        yaml_mat = yaml_materials.get(mat_code, {})

        if customer_price:
            kg_price = customer_price
            density = yaml_mat.get("density_kg_m3", 7850)
            source = "customer_db"
        elif yaml_mat:
            kg_price = yaml_mat.get("kg_price", 200)
            density = yaml_mat.get("density_kg_m3", 7850)
            source = "yaml"
        else:
            kg_price = 200
            density = 7850
            source = "llm"

        layers_used.append(LayerSource(
            field="material_price", layer=source,
            value=f"{mat_code}={kg_price}円/kg",
            confidence=0.9 if source == "customer_db" else 0.7 if source == "yaml" else 0.3,
        ))

        # 体積計算
        if shape == "round":
            d = float(dims.get("outer_diameter", dims.get("diameter", 30))) + 5
            l = float(dims.get("length", 50)) + 10
            volume_m3 = math.pi * (d / 2000) ** 2 * (l / 1000)
        elif shape == "plate":
            w = float(dims.get("width", 100)) + 10
            h = float(dims.get("height", dims.get("length", 100))) + 10
            t = float(dims.get("thickness", 10)) + 2
            volume_m3 = (w / 1000) * (h / 1000) * (t / 1000)
        else:
            w = float(dims.get("width", 50)) + 10
            h = float(dims.get("height", 50)) + 10
            l = float(dims.get("length", 50)) + 10
            volume_m3 = (w / 1000) * (h / 1000) * (l / 1000)

        weight_kg = volume_m3 * density
        waste_factor = 1.15
        cost_per_piece = weight_kg * kg_price * waste_factor
        return int(cost_per_piece * hearing.quantity)

    def _calc_outsource_cost(
        self,
        proc: ProcessEstimate,
        quantity: int,
        yaml_outsource: dict,
    ) -> int:
        """外注費計算"""
        if "熱処理" in proc.process_name:
            oc = yaml_outsource.get("heat_treatment", {})
            return max(
                oc.get("base_cost", 5000),
                oc.get("per_piece_cost", 200) * quantity,
            )
        if "表面処理" in proc.process_name:
            st = yaml_outsource.get("surface_treatment", {})
            # 表面処理の種類を特定
            for variant_name, variant_data in st.items():
                if isinstance(variant_data, dict) and variant_name in proc.notes:
                    return max(
                        variant_data.get("base_cost", 3000),
                        variant_data.get("per_piece_cost", 300) * quantity,
                    )
            return max(3000, 300 * quantity)
        return max(3000, 200 * quantity)

    # ─────────────────────────────────
    # ヘルパー
    # ─────────────────────────────────

    def _ensure_common_processes(
        self, processes: list[ProcessEstimate],
    ) -> list[ProcessEstimate]:
        """バリ取り・検査が含まれていなければ追加"""
        has_deburring = any(p.equipment_type == "deburring" for p in processes)
        has_inspection = any(p.equipment_type == "inspection" for p in processes)
        order = max((p.sort_order for p in processes), default=0) + 1

        if not has_deburring:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="バリ取り",
                equipment="手作業",
                equipment_type="deburring",
                setup_time_min=5, cycle_time_min=3,
                confidence=0.8,
            ))
            order += 1
        if not has_inspection:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="検査",
                equipment="検査",
                equipment_type="inspection",
                setup_time_min=10, cycle_time_min=2,
                confidence=0.8,
            ))
        return processes

    async def _load_customer_data(self, company_id: str) -> CustomerOverrides:
        """顧客固有データをDBからロード"""
        if not company_id:
            return CustomerOverrides()

        charge_rates: dict[str, int] = {}
        material_prices: dict[str, int] = {}
        historical: dict[str, dict] = {}

        try:
            client = get_service_client()

            # チャージレート
            cr_result = client.table("mfg_charge_rates").select("*").eq(
                "company_id", company_id,
            ).execute()
            for row in (cr_result.data or []):
                charge_rates[row["equipment_name"]] = int(row["charge_rate"])

            # 材料単価
            mp_result = client.table("mfg_material_prices").select("*").eq(
                "company_id", company_id,
            ).execute()
            for row in (mp_result.data or []):
                material_prices[row["material_code"]] = int(row["unit_price"])

        except Exception as e:
            logger.warning(f"顧客データ取得失敗: {e}")

        return CustomerOverrides(
            charge_rates=charge_rates,
            material_prices=material_prices,
            historical_averages=historical,
        )

    async def _save_quote(
        self,
        company_id: str,
        hearing: HearingInput,
        processes: list[ProcessEstimate],
        costs: QuoteCostBreakdown,
        additional: list[AdditionalCostItem],
        sub_industry: str,
        layers_used: list[LayerSource],
    ) -> str:
        """見積をDBに保存"""
        client = get_service_client()

        # 見積番号の自動採番
        count_result = client.table("mfg_quotes").select(
            "id", count="exact",
        ).eq("company_id", company_id).execute()
        seq = (count_result.count or 0) + 1
        quote_number = f"MQ-{seq:05d}"

        quote_data = {
            "company_id": company_id,
            "quote_number": quote_number,
            "customer_name": hearing.product_name,
            "project_name": hearing.notes or None,
            "quantity": hearing.quantity,
            "material": hearing.material,
            "surface_treatment": hearing.surface_treatment or None,
            "total_amount": costs.total_amount,
            "profit_margin": costs.profit_rate * 100,
            "status": "draft",
            "sub_industry": sub_industry,
            "layers_used": [l.model_dump() for l in layers_used],
            "overall_confidence": min(
                (l.confidence for l in layers_used), default=0.4,
            ),
            "additional_costs": [a.model_dump() for a in additional],
        }
        result = client.table("mfg_quotes").insert(quote_data).execute()
        quote_id = result.data[0]["id"]

        # 工程別明細
        for i, (proc, cost_detail) in enumerate(zip(processes, costs.process_costs)):
            item_data = {
                "quote_id": quote_id,
                "company_id": company_id,
                "sort_order": i + 1,
                "process_name": proc.process_name,
                "equipment": proc.equipment,
                "equipment_type": proc.equipment_type,
                "setup_time_min": proc.setup_time_min,
                "cycle_time_min": proc.cycle_time_min,
                "total_time_min": cost_detail.total_time_min,
                "charge_rate": cost_detail.charge_rate,
                "process_cost": cost_detail.process_cost,
                "material_cost": costs.material_cost if i == 0 else None,
                "outsource_cost": cost_detail.process_cost if proc.is_outsource else None,
                "cost_source": "ai_estimated",
                "confidence": proc.confidence,
                "notes": proc.notes,
                "layer_source": "plugin" if get_plugin(sub_industry) else "yaml" if load_yaml_config(sub_industry) else "llm",
            }
            client.table("mfg_quote_items").insert(item_data).execute()

        return quote_id

    # ─────────────────────────────────
    # 学習
    # ─────────────────────────────────

    async def learn_from_actual(
        self,
        quote_id: str,
        company_id: str,
        actual_times: list[dict],
    ) -> int:
        """実績工数からの学習（既存ロジックを継承）"""
        client = get_service_client()
        learned = 0

        for actual in actual_times:
            item_id = actual.get("item_id")
            if not item_id:
                continue

            update_data: dict[str, Any] = {"user_modified": True}
            if actual.get("actual_setup_time_min") is not None:
                update_data["setup_time_min"] = actual["actual_setup_time_min"]
            if actual.get("actual_cycle_time_min") is not None:
                update_data["cycle_time_min"] = actual["actual_cycle_time_min"]
            update_data["cost_source"] = "past_record"

            client.table("mfg_quote_items").update(update_data).eq(
                "id", item_id,
            ).eq("company_id", company_id).execute()
            learned += 1

        return learned
