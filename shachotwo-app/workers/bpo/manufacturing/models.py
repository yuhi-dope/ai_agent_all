"""製造業BPO Pydanticモデル"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


# ─────────────────────────────────────
# 図面解析
# ─────────────────────────────────────

class DrawingFeature(BaseModel):
    """加工要素（穴、溝、ネジ等）"""
    feature_type: str  # hole, slot, thread, chamfer, fillet, pocket, groove
    description: str
    dimensions: dict = {}


class DrawingAnalysis(BaseModel):
    """図面解析結果"""
    shape_type: str  # round(丸物), block(角物), plate(板物), complex(複雑形状)
    dimensions: dict = {}  # outer_diameter, length, width, height, thickness etc.
    material: str = ""
    tolerances: dict = {}  # general_tolerance, tight_dimensions
    surface_roughness: str = ""  # Ra 6.3, Ra 1.6, Ra 0.4 etc.
    surface_treatment: str = ""
    features: list[DrawingFeature] = []
    hardness: str = ""  # HRC 50-55 etc.
    weight_kg: Optional[float] = None
    notes: str = ""


# ─────────────────────────────────────
# 工程推定
# ─────────────────────────────────────

class ProcessEstimate(BaseModel):
    """工程推定結果"""
    sort_order: int
    process_name: str  # 材料切断, CNC旋盤, マシニングセンタ, 研磨, 表面処理, 検査 etc.
    equipment: str = ""  # 具体的な設備名
    equipment_type: str = ""  # lathe, machining_center, grinder, press, laser, welding etc.
    setup_time_min: float = 30.0  # 段取り時間（分）
    cycle_time_min: float = 10.0  # サイクルタイム（分/個）
    is_outsource: bool = False  # 外注工程か
    confidence: float = 0.7
    notes: str = ""


# ─────────────────────────────────────
# コスト計算
# ─────────────────────────────────────

class ProcessCostDetail(BaseModel):
    """工程別コスト詳細"""
    process_name: str
    equipment: str = ""
    setup_time_min: float
    cycle_time_min: float
    total_time_min: float
    charge_rate: int
    process_cost: int
    is_outsource: bool = False


class QuoteCostBreakdown(BaseModel):
    """見積コスト内訳"""
    material_cost: int = 0
    process_costs: list[ProcessCostDetail] = []
    surface_treatment_cost: int = 0
    outsource_cost: int = 0
    inspection_cost: int = 0
    subtotal: int = 0
    overhead_cost: int = 0
    overhead_rate: float = 0.15
    profit: int = 0
    profit_rate: float = 0.15
    total_amount: int = 0
    unit_price: int = 0  # total / quantity


# ─────────────────────────────────────
# API リクエスト/レスポンス
# ─────────────────────────────────────

class MfgQuoteCreate(BaseModel):
    customer_name: str
    project_name: Optional[str] = None
    material: str = "SS400"
    quantity: int = 1
    surface_treatment: Optional[str] = None
    delivery_date: Optional[date] = None
    description: str = ""  # 部品のテキスト説明
    overhead_rate: float = 0.15
    profit_rate: float = 0.15


class MfgQuoteItemResponse(BaseModel):
    id: str
    sort_order: int
    process_name: str
    equipment: Optional[str] = None
    equipment_type: Optional[str] = None
    setup_time_min: Optional[float] = None
    cycle_time_min: Optional[float] = None
    total_time_min: Optional[float] = None
    charge_rate: Optional[int] = None
    process_cost: Optional[int] = None
    material_cost: Optional[int] = None
    outsource_cost: Optional[int] = None
    cost_source: str = "ai_estimated"
    confidence: Optional[float] = None
    user_modified: bool = False
    notes: Optional[str] = None


class MfgQuoteResponse(BaseModel):
    id: str
    quote_number: str
    customer_name: str
    project_name: Optional[str] = None
    quantity: int
    material: Optional[str] = None
    surface_treatment: Optional[str] = None
    shape_type: Optional[str] = None
    total_amount: Optional[int] = None
    profit_margin: Optional[float] = None
    status: str = "draft"
    items: list[MfgQuoteItemResponse] = []
    cost_breakdown: Optional[QuoteCostBreakdown] = None
    created_at: Optional[datetime] = None


class MfgQuoteItemUpdate(BaseModel):
    setup_time_min: Optional[float] = None
    cycle_time_min: Optional[float] = None
    charge_rate: Optional[int] = None
    notes: Optional[str] = None


class MfgFinalizeItem(BaseModel):
    item_id: str
    confirmed_setup_time: Optional[float] = None
    confirmed_cycle_time: Optional[float] = None
    confirmed_charge_rate: Optional[int] = None


class MfgFinalizeRequest(BaseModel):
    items: list[MfgFinalizeItem]


class MfgFinalizeResponse(BaseModel):
    finalized_count: int
    learned_count: int
    accuracy_summary: dict


class ChargeRateCreate(BaseModel):
    equipment_name: str
    equipment_type: str
    charge_rate: int
    setup_time_default: Optional[float] = None
    notes: Optional[str] = None


class ChargeRateResponse(BaseModel):
    id: str
    equipment_name: str
    equipment_type: str
    charge_rate: int
    setup_time_default: Optional[float] = None
    notes: Optional[str] = None
