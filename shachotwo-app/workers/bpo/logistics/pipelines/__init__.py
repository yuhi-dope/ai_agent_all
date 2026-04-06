"""物流・運送業 BPOパイプライン一覧

各パイプラインはlazyにインポートされます。
直接利用時は個別モジュールをインポートしてください。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workers.bpo.logistics.pipelines.dispatch_pipeline import (
        DispatchResult,
    )
    from workers.bpo.logistics.pipelines.operation_management_pipeline import (
        OperationManagementResult,
    )
    from workers.bpo.logistics.pipelines.vehicle_management_pipeline import (
        VehicleManagementResult,
    )
    from workers.bpo.logistics.pipelines.charter_management_pipeline import (
        CharterManagementResult,
    )
    from workers.bpo.logistics.pipelines.freight_billing_pipeline import (
        FreightBillingResult,
    )
    from workers.bpo.logistics.pipelines.warehouse_management_pipeline import (
        WarehouseManagementResult,
    )
    from workers.bpo.logistics.pipelines.safety_management_pipeline import (
        SafetyManagementResult,
    )
    from workers.bpo.logistics.pipelines.permit_management_pipeline import (
        PermitManagementResult,
    )


# パイプラインレジストリ（メタデータのみ。runnerはlazy importで提供）
# key: パイプライン識別子, value: {module, runner_name, result_class_name, description, steps, status}
PIPELINE_REGISTRY: dict[str, dict[str, Any]] = {
    "dispatch": {
        "module": "workers.bpo.logistics.pipelines.dispatch_pipeline",
        "runner_name": "run_dispatch_pipeline",
        "result_class_name": "DispatchResult",
        "description": "配車計画AI（配送依頼→ドライバー割当→ルート最適化→2024年問題チェック）",
        "steps": 5,
        "status": "implemented",
    },
    "operation_management": {
        "module": "workers.bpo.logistics.pipelines.operation_management_pipeline",
        "runner_name": "run_operation_management_pipeline",
        "result_class_name": "OperationManagementResult",
        "description": "運行管理（運行指示書+点呼記録+日報+アルコールチェック+法定保存）",
        "steps": 7,
        "status": "skeleton",
    },
    "vehicle_management": {
        "module": "workers.bpo.logistics.pipelines.vehicle_management_pipeline",
        "runner_name": "run_vehicle_management_pipeline",
        "result_class_name": "VehicleManagementResult",
        "description": "車両管理（車検/点検期日アラート+車両別コスト+稼働率分析）",
        "steps": 7,
        "status": "skeleton",
    },
    "charter_management": {
        "module": "workers.bpo.logistics.pipelines.charter_management_pipeline",
        "runner_name": "run_charter_management_pipeline",
        "result_class_name": "CharterManagementResult",
        "description": "傭車管理（傭車先マスタ照合+依頼書生成+下請法チェック+コスト計算）",
        "steps": 7,
        "status": "skeleton",
    },
    "freight_billing": {
        "module": "workers.bpo.logistics.pipelines.freight_billing_pipeline",
        "runner_name": "run_freight_billing_pipeline",
        "result_class_name": "FreightBillingResult",
        "description": "請求・運賃計算（距離制/重量制/個建て+燃料サーチャージ+請求書生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "warehouse_management": {
        "module": "workers.bpo.logistics.pipelines.warehouse_management_pipeline",
        "runner_name": "run_warehouse_management_pipeline",
        "result_class_name": "WarehouseManagementResult",
        "description": "倉庫管理（入出庫+ABC分析+棚卸差異+保管料計算）",
        "steps": 7,
        "status": "skeleton",
    },
    "safety_management": {
        "module": "workers.bpo.logistics.pipelines.safety_management_pipeline",
        "runner_name": "run_safety_management_pipeline",
        "result_class_name": "SafetyManagementResult",
        "description": "安全管理（事故記録+Gマーク基準チェック+安全教育計画+報告書生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "permit_management": {
        "module": "workers.bpo.logistics.pipelines.permit_management_pipeline",
        "runner_name": "run_permit_management_pipeline",
        "result_class_name": "PermitManagementResult",
        "description": "届出・許認可管理（事業報告書+実績報告書+変更届出+期限アラート）",
        "steps": 7,
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
