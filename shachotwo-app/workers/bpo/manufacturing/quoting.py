"""製造業 見積AIパイプライン"""
import json
import logging
import math
from datetime import datetime, timezone

from db.supabase import get_service_client
from llm.client import get_llm_client, LLMTask
from llm.prompts.manufacturing import DRAWING_ANALYSIS_PROMPT, PROCESS_ESTIMATION_PROMPT
from workers.bpo.manufacturing.models import (
    DrawingAnalysis, DrawingFeature, ProcessEstimate,
    ProcessCostDetail, QuoteCostBreakdown,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# 材質データ（密度 kg/m3、加工難易度係数）
# ─────────────────────────────────────
MATERIAL_PROPERTIES = {
    "SS400":  {"density": 7850, "machinability": 1.0, "kg_price": 120, "name": "一般構造用鋼"},
    "S45C":   {"density": 7850, "machinability": 0.9, "kg_price": 150, "name": "機械構造用炭素鋼"},
    "S50C":   {"density": 7850, "machinability": 0.85, "kg_price": 160, "name": "機械構造用炭素鋼"},
    "SCM435": {"density": 7850, "machinability": 0.8, "kg_price": 200, "name": "クロモリ鋼"},
    "SUS304": {"density": 7930, "machinability": 0.6, "kg_price": 400, "name": "ステンレス"},
    "SUS316": {"density": 7980, "machinability": 0.55, "kg_price": 500, "name": "ステンレス"},
    "A5052":  {"density": 2680, "machinability": 1.3, "kg_price": 500, "name": "アルミ合金"},
    "A6061":  {"density": 2700, "machinability": 1.2, "kg_price": 550, "name": "アルミ合金"},
    "A7075":  {"density": 2800, "machinability": 1.0, "kg_price": 800, "name": "超々ジュラルミン"},
    "C3604":  {"density": 8470, "machinability": 1.5, "kg_price": 1200, "name": "快削黄銅"},
    "C1100":  {"density": 8940, "machinability": 1.2, "kg_price": 1500, "name": "純銅"},
    "POM":    {"density": 1410, "machinability": 1.5, "kg_price": 800, "name": "ポリアセタール"},
    "MC901":  {"density": 1160, "machinability": 1.4, "kg_price": 2000, "name": "MCナイロン"},
    "PEEK":   {"density": 1300, "machinability": 0.8, "kg_price": 15000, "name": "PEEK樹脂"},
}

# ─────────────────────────────────────
# デフォルトチャージレート（円/時間）
# ─────────────────────────────────────
DEFAULT_CHARGE_RATES = {
    "材料切断":       {"rate": 3000,  "type": "cutting",          "setup": 15},
    "汎用旋盤":       {"rate": 4000,  "type": "lathe",            "setup": 45},
    "CNC旋盤":        {"rate": 6000,  "type": "cnc_lathe",        "setup": 30},
    "マシニングセンタ": {"rate": 8000,  "type": "machining_center", "setup": 45},
    "5軸MC":          {"rate": 12000, "type": "5axis_mc",         "setup": 60},
    "フライス盤":      {"rate": 4000,  "type": "milling",          "setup": 40},
    "研磨盤":          {"rate": 6000,  "type": "grinder",          "setup": 30},
    "ワイヤーカット":   {"rate": 10000, "type": "wire_cut",         "setup": 30},
    "放電加工":        {"rate": 10000, "type": "edm",              "setup": 30},
    "プレス機":        {"rate": 5000,  "type": "press",            "setup": 60},
    "レーザー加工機":   {"rate": 8000,  "type": "laser",            "setup": 20},
    "溶接":           {"rate": 5000,  "type": "welding",          "setup": 20},
    "バリ取り":        {"rate": 2500,  "type": "deburring",        "setup": 5},
    "検査":           {"rate": 3000,  "type": "inspection",       "setup": 10},
}

# ─────────────────────────────────────
# 形状→工程推定ルール（ルールベース）
# ─────────────────────────────────────
SHAPE_PROCESS_MAP = {
    "round": [
        ("材料切断", "cutting", 15, 3),
        ("CNC旋盤", "cnc_lathe", 30, 8),
    ],
    "block": [
        ("材料切断", "cutting", 15, 3),
        ("マシニングセンタ", "machining_center", 45, 15),
    ],
    "plate": [
        ("レーザー加工機", "laser", 20, 5),
    ],
    "complex": [
        ("材料切断", "cutting", 15, 3),
        ("マシニングセンタ", "machining_center", 60, 20),
        ("ワイヤーカット", "wire_cut", 30, 15),
    ],
}


class QuotingPipeline:
    """製造業 見積AIパイプライン"""

    # ─────────────────────────────────
    # Step 1: 図面/テキスト解析
    # ─────────────────────────────────
    async def analyze_drawing_text(
        self,
        description: str,
        material: str,
        quantity: int,
        surface_treatment: str | None = None,
    ) -> DrawingAnalysis:
        """テキスト入力から形状・寸法を解析（LLM使用）"""
        prompt = DRAWING_ANALYSIS_PROMPT.format(
            description=description,
            material=material,
            quantity=quantity,
            surface_treatment=surface_treatment or "なし",
        )

        try:
            client = get_llm_client()
            task = LLMTask(prompt=prompt, model_tier="fast", max_tokens=2000)
            llm_response = await client.generate(task)
            data = json.loads(llm_response.content)
            features = [
                DrawingFeature(**f) for f in data.get("features", [])
            ]
            return DrawingAnalysis(
                shape_type=data.get("shape_type", "block"),
                dimensions=data.get("dimensions", {}),
                material=material,
                tolerances=data.get("tolerances", {}),
                surface_roughness=data.get("surface_roughness", "Ra 6.3"),
                surface_treatment=surface_treatment or "",
                features=features,
                hardness=data.get("hardness", ""),
                weight_kg=data.get("weight_kg"),
                notes=data.get("notes", ""),
            )
        except Exception as e:
            logger.warning(f"LLM解析失敗、フォールバック: {e}")
            return self._fallback_analysis(description, material, surface_treatment)

    def _fallback_analysis(
        self, description: str, material: str, surface_treatment: str | None
    ) -> DrawingAnalysis:
        """LLM失敗時のフォールバック解析"""
        desc_lower = description.lower()
        shape = "block"
        if any(kw in desc_lower for kw in ["シャフト", "ピン", "丸", "φ", "外径", "軸"]):
            shape = "round"
        elif any(kw in desc_lower for kw in ["板", "プレート", "ブラケット", "カバー", "t="]):
            shape = "plate"
        elif any(kw in desc_lower for kw in ["複雑", "5軸", "異形"]):
            shape = "complex"

        return DrawingAnalysis(
            shape_type=shape,
            dimensions={},
            material=material,
            surface_roughness="Ra 6.3",
            surface_treatment=surface_treatment or "",
        )

    # ─────────────────────────────────
    # Step 2: 工程推定
    # ─────────────────────────────────
    async def estimate_processes(
        self, analysis: DrawingAnalysis
    ) -> list[ProcessEstimate]:
        """形状+材質+公差→工程リストを推定"""
        processes: list[ProcessEstimate] = []
        order = 1

        # ベース工程（形状ベース）
        base = SHAPE_PROCESS_MAP.get(analysis.shape_type, SHAPE_PROCESS_MAP["block"])
        mat_props = MATERIAL_PROPERTIES.get(analysis.material, MATERIAL_PROPERTIES["SS400"])
        machinability = mat_props["machinability"]

        for proc_name, eq_type, setup, cycle in base:
            # 加工難易度で補正（SUS304は1.67倍、C3604は0.67倍）
            adjusted_cycle = cycle / machinability if machinability > 0 else cycle
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name=proc_name,
                equipment=proc_name,
                equipment_type=eq_type,
                setup_time_min=setup,
                cycle_time_min=round(adjusted_cycle, 1),
                confidence=0.7,
            ))
            order += 1

        # 穴あけ・タップ → MC追加（既にMCがなければ）
        has_mc = any(p.equipment_type == "machining_center" for p in processes)
        hole_features = [f for f in analysis.features if f.feature_type in ("hole", "thread")]
        if hole_features and not has_mc:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="マシニングセンタ",
                equipment="マシニングセンタ",
                equipment_type="machining_center",
                setup_time_min=30,
                cycle_time_min=round(5 * len(hole_features) / machinability, 1),
                confidence=0.6,
                notes=f"穴/ネジ {len(hole_features)}箇所",
            ))
            order += 1

        # 厳しい公差 → 研磨追加
        tight = analysis.tolerances.get("tight_dimensions", [])
        if tight or analysis.surface_roughness in ("Ra 0.4", "Ra 0.8", "Ra 1.6"):
            has_grinder = any(p.equipment_type == "grinder" for p in processes)
            if not has_grinder:
                processes.append(ProcessEstimate(
                    sort_order=order,
                    process_name="研磨盤",
                    equipment="研磨盤",
                    equipment_type="grinder",
                    setup_time_min=30,
                    cycle_time_min=round(10 / machinability, 1),
                    confidence=0.6,
                    notes="高精度仕上げ",
                ))
                order += 1

        # 熱処理（HRC指定あり）→ 外注
        if analysis.hardness:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name="熱処理（外注）",
                equipment="外注",
                equipment_type="heat_treatment",
                setup_time_min=0,
                cycle_time_min=0,
                is_outsource=True,
                confidence=0.5,
                notes=f"硬度指定: {analysis.hardness}",
            ))
            order += 1

        # 表面処理（外注）
        if analysis.surface_treatment:
            processes.append(ProcessEstimate(
                sort_order=order,
                process_name=f"表面処理（{analysis.surface_treatment}）",
                equipment="外注",
                equipment_type="surface_treatment",
                setup_time_min=0,
                cycle_time_min=0,
                is_outsource=True,
                confidence=0.5,
                notes=analysis.surface_treatment,
            ))
            order += 1

        # バリ取り
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="バリ取り",
            equipment="手作業",
            equipment_type="deburring",
            setup_time_min=5,
            cycle_time_min=3,
            confidence=0.8,
        ))
        order += 1

        # 検査
        processes.append(ProcessEstimate(
            sort_order=order,
            process_name="検査",
            equipment="検査",
            equipment_type="inspection",
            setup_time_min=10,
            cycle_time_min=2,
            confidence=0.8,
        ))

        return processes

    # ─────────────────────────────────
    # Step 3: コスト計算
    # ─────────────────────────────────
    async def calculate_costs(
        self,
        analysis: DrawingAnalysis,
        processes: list[ProcessEstimate],
        quantity: int,
        company_id: str | None = None,
        overhead_rate: float = 0.15,
        profit_rate: float = 0.15,
    ) -> QuoteCostBreakdown:
        """コスト計算"""
        # 会社のチャージレートを取得
        company_rates = {}
        if company_id:
            try:
                client = get_service_client()
                result = client.table("mfg_charge_rates").select("*").eq(
                    "company_id", company_id
                ).execute()
                for row in (result.data or []):
                    company_rates[row["equipment_name"]] = {
                        "rate": int(row["charge_rate"]),
                        "setup": float(row.get("setup_time_default") or 0),
                    }
            except Exception as e:
                logger.warning(f"チャージレート取得失敗: {e}")

        # 材料費計算
        material_cost = self._calc_material_cost(analysis, quantity)

        # 工程別コスト
        process_cost_details: list[ProcessCostDetail] = []
        total_outsource = 0

        for proc in processes:
            if proc.is_outsource:
                # 外注費は概算
                outsource = self._estimate_outsource_cost(proc, quantity)
                total_outsource += outsource
                process_cost_details.append(ProcessCostDetail(
                    process_name=proc.process_name,
                    equipment=proc.equipment,
                    setup_time_min=0,
                    cycle_time_min=0,
                    total_time_min=0,
                    charge_rate=0,
                    process_cost=outsource,
                    is_outsource=True,
                ))
                continue

            # チャージレート: 会社マスタ → デフォルト
            rate_info = company_rates.get(proc.process_name)
            if rate_info:
                charge_rate = rate_info["rate"]
            elif proc.process_name in DEFAULT_CHARGE_RATES:
                charge_rate = DEFAULT_CHARGE_RATES[proc.process_name]["rate"]
            else:
                charge_rate = 5000  # フォールバック

            total_time = proc.setup_time_min + (proc.cycle_time_min * quantity)
            process_cost = int(total_time / 60 * charge_rate)

            process_cost_details.append(ProcessCostDetail(
                process_name=proc.process_name,
                equipment=proc.equipment,
                setup_time_min=proc.setup_time_min,
                cycle_time_min=proc.cycle_time_min,
                total_time_min=round(total_time, 1),
                charge_rate=charge_rate,
                process_cost=process_cost,
            ))

        # 集計
        process_total = sum(d.process_cost for d in process_cost_details if not d.is_outsource)
        inspection_cost = sum(d.process_cost for d in process_cost_details
                              if d.process_name == "検査")
        surface_cost = sum(d.process_cost for d in process_cost_details
                           if "表面処理" in d.process_name)

        subtotal = material_cost + process_total + total_outsource
        overhead = int(subtotal * overhead_rate)
        profit = int((subtotal + overhead) * profit_rate)
        total = subtotal + overhead + profit

        return QuoteCostBreakdown(
            material_cost=material_cost,
            process_costs=process_cost_details,
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

    def _calc_material_cost(self, analysis: DrawingAnalysis, quantity: int) -> int:
        """材料費計算"""
        mat = MATERIAL_PROPERTIES.get(analysis.material, MATERIAL_PROPERTIES["SS400"])
        dims = analysis.dimensions

        # 体積計算（加工代含む）
        if analysis.shape_type == "round":
            d = float(dims.get("outer_diameter", dims.get("diameter", 30))) + 5  # 加工代5mm
            l = float(dims.get("length", 50)) + 10  # 加工代10mm
            volume_m3 = math.pi * (d / 2000) ** 2 * (l / 1000)
        elif analysis.shape_type == "plate":
            w = float(dims.get("width", 100)) + 10
            h = float(dims.get("height", dims.get("length", 100))) + 10
            t = float(dims.get("thickness", 10)) + 2
            volume_m3 = (w / 1000) * (h / 1000) * (t / 1000)
        else:  # block, complex
            w = float(dims.get("width", 50)) + 10
            h = float(dims.get("height", 50)) + 10
            l = float(dims.get("length", 50)) + 10
            volume_m3 = (w / 1000) * (h / 1000) * (l / 1000)

        weight_kg = volume_m3 * mat["density"]
        waste_factor = 1.15  # 端材ロス15%
        cost_per_piece = weight_kg * mat["kg_price"] * waste_factor
        return int(cost_per_piece * quantity)

    def _estimate_outsource_cost(self, proc: ProcessEstimate, quantity: int) -> int:
        """外注費の概算"""
        if "熱処理" in proc.process_name:
            return max(5000, 200 * quantity)  # 最低5000円、1個200円
        if "表面処理" in proc.process_name:
            if "メッキ" in proc.notes:
                return max(3000, 500 * quantity)
            if "アルマイト" in proc.notes:
                return max(3000, 300 * quantity)
            if "黒染め" in proc.notes:
                return max(2000, 100 * quantity)
            return max(3000, 300 * quantity)  # デフォルト
        return max(3000, 200 * quantity)

    # ─────────────────────────────────
    # Step 4: DB保存
    # ─────────────────────────────────
    async def save_quote(
        self,
        company_id: str,
        customer_name: str,
        project_name: str | None,
        analysis: DrawingAnalysis,
        processes: list[ProcessEstimate],
        costs: QuoteCostBreakdown,
        quantity: int,
        surface_treatment: str | None = None,
        delivery_date=None,
        description: str = "",
    ) -> str:
        """見積をDBに保存してquote_idを返す"""
        client = get_service_client()

        # 見積番号の自動採番
        count_result = client.table("mfg_quotes").select(
            "id", count="exact"
        ).eq("company_id", company_id).execute()
        seq = (count_result.count or 0) + 1
        quote_number = f"MQ-{seq:05d}"

        # quote insert
        quote_data = {
            "company_id": company_id,
            "quote_number": quote_number,
            "customer_name": customer_name,
            "project_name": project_name,
            "quantity": quantity,
            "material": analysis.material,
            "surface_treatment": surface_treatment,
            "delivery_date": str(delivery_date) if delivery_date else None,
            "total_amount": costs.total_amount,
            "profit_margin": costs.profit_rate * 100,
            "status": "draft",
            "shape_type": analysis.shape_type,
            "dimensions": analysis.dimensions,
            "tolerances": analysis.tolerances,
            "surface_roughness": analysis.surface_roughness,
            "features": [f.model_dump() for f in analysis.features],
            "description": description,
        }
        result = client.table("mfg_quotes").insert(quote_data).execute()
        quote_id = result.data[0]["id"]

        # items insert
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
                "ai_estimated_time": cost_detail.total_time_min,
                "notes": proc.notes,
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
        """実績工数からの学習"""
        client = get_service_client()
        learned = 0

        for actual in actual_times:
            item_id = actual.get("item_id")
            actual_setup = actual.get("actual_setup_time_min")
            actual_cycle = actual.get("actual_cycle_time_min")

            if not item_id:
                continue

            update_data = {"user_modified": True}
            if actual_setup is not None:
                update_data["setup_time_min"] = actual_setup
            if actual_cycle is not None:
                update_data["cycle_time_min"] = actual_cycle
            update_data["cost_source"] = "past_record"

            client.table("mfg_quote_items").update(update_data).eq(
                "id", item_id
            ).eq("company_id", company_id).execute()
            learned += 1

        return learned
