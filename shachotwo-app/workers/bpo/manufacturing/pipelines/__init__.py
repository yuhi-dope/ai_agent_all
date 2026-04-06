"""製造業BPOパイプライン一覧

各パイプラインはlazyにインポートされます。
直接利用時は個別モジュールをインポートしてください。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workers.bpo.manufacturing.pipelines.quoting_pipeline import (
        QuotingPipeline,
        QuotingPipelineResult,
    )
    from workers.bpo.manufacturing.pipelines.production_planning_pipeline import (
        ProductionPlanResult,
    )
    from workers.bpo.manufacturing.pipelines.quality_control_pipeline import (
        QualityControlResult,
    )
    from workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline import (
        InventoryOptimizationResult,
    )
    from workers.bpo.manufacturing.pipelines.sop_management_pipeline import (
        SOPManagementResult,
    )
    from workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline import (
        EquipmentMaintenanceResult,
    )
    from workers.bpo.manufacturing.pipelines.procurement_pipeline import (
        ProcurementResult,
    )
    from workers.bpo.manufacturing.pipelines.iso_document_pipeline import (
        ISODocumentResult,
    )


# パイプラインレジストリ（メタデータのみ。runnerはlazy importで提供）
# key: パイプライン識別子, value: {module, runner_name, result_class_name, description, steps, status}
PIPELINE_REGISTRY: dict[str, dict[str, Any]] = {
    "quoting": {
        "module": "workers.bpo.manufacturing.pipelines.quoting_pipeline",
        "runner_name": "run_quoting_pipeline",
        "result_class_name": "QuotingPipelineResult",
        "description": "見積AI（仕様書→工程推定→原価積み上げ）",
        "steps": 4,
        "status": "implemented",
    },
    "production_planning": {
        "module": "workers.bpo.manufacturing.pipelines.production_planning_pipeline",
        "runner_name": "run_production_planning_pipeline",
        "result_class_name": "ProductionPlanResult",
        "description": "生産計画AI（受注→山積み計算→ガントチャート）",
        "steps": 7,
        "status": "skeleton",
    },
    "quality_control": {
        "module": "workers.bpo.manufacturing.pipelines.quality_control_pipeline",
        "runner_name": "run_quality_control_pipeline",
        "result_class_name": "QualityControlResult",
        "description": "品質管理（検査データ→SPC計算→不良予兆検知）",
        "steps": 7,
        "status": "skeleton",
    },
    "inventory_optimization": {
        "module": "workers.bpo.manufacturing.pipelines.inventory_optimization_pipeline",
        "runner_name": "run_inventory_optimization_pipeline",
        "result_class_name": "InventoryOptimizationResult",
        "description": "在庫最適化（ABC分析→安全在庫→発注点算出）",
        "steps": 7,
        "status": "skeleton",
    },
    "sop_management": {
        "module": "workers.bpo.manufacturing.pipelines.sop_management_pipeline",
        "runner_name": "run_sop_management_pipeline",
        "result_class_name": "SOPManagementResult",
        "description": "SOP管理（手順書作成→安全衛生法チェック→改訂管理）",
        "steps": 7,
        "status": "skeleton",
    },
    "equipment_maintenance": {
        "module": "workers.bpo.manufacturing.pipelines.equipment_maintenance_pipeline",
        "runner_name": "run_equipment_maintenance_pipeline",
        "result_class_name": "EquipmentMaintenanceResult",
        "description": "設備保全（MTBF/MTTR計算→保全カレンダー生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "procurement": {
        "module": "workers.bpo.manufacturing.pipelines.procurement_pipeline",
        "runner_name": "run_procurement_pipeline",
        "result_class_name": "ProcurementResult",
        "description": "仕入管理（BOM展開→MRP計算→発注書生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "iso_document": {
        "module": "workers.bpo.manufacturing.pipelines.iso_document_pipeline",
        "runner_name": "run_iso_document_pipeline",
        "result_class_name": "ISODocumentResult",
        "description": "ISO文書管理（条項別チェック→監査チェックリスト生成）",
        "steps": 8,
        "status": "skeleton",
    },
}


def get_pipeline_runner(pipeline_id: str):
    """パイプラインIDに対応するrunner関数をlazy importで返す"""
    if pipeline_id not in PIPELINE_REGISTRY:
        raise KeyError(f"未知のパイプライン: {pipeline_id}")
    meta = PIPELINE_REGISTRY[pipeline_id]
    import importlib
    module = importlib.import_module(meta["module"])
    return getattr(module, meta["runner_name"])


__all__ = [
    "PIPELINE_REGISTRY",
    "get_pipeline_runner",
]
