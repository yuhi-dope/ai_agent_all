"""卸売業BPOパイプライン一覧

各パイプラインはlazyにインポートされます。
直接利用時は個別モジュールをインポートしてください。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workers.bpo.wholesale.pipelines.order_processing_pipeline import (
        OrderProcessingResult,
    )
    from workers.bpo.wholesale.pipelines.inventory_management_pipeline import (
        InventoryManagementResult,
    )
    from workers.bpo.wholesale.pipelines.accounts_receivable_pipeline import (
        AccountsReceivableResult,
    )
    from workers.bpo.wholesale.pipelines.accounts_payable_pipeline import (
        AccountsPayableResult,
    )
    from workers.bpo.wholesale.pipelines.shipping_pipeline import (
        ShippingResult,
    )
    from workers.bpo.wholesale.pipelines.sales_intelligence_pipeline import (
        SalesIntelligenceResult,
    )


# パイプラインレジストリ（メタデータのみ。runnerはlazy importで提供）
PIPELINE_REGISTRY: dict[str, dict[str, Any]] = {
    "order_processing": {
        "module": "workers.bpo.wholesale.pipelines.order_processing_pipeline",
        "runner_name": "run_order_processing_pipeline",
        "result_class_name": "OrderProcessingResult",
        "description": "受発注AI（FAX/メールOCR→品目構造化→商品マスタマッチング→在庫引当→確認書生成）",
        "steps": 7,
        "status": "skeleton",
        "phase": "A",
        "tier": "killer",
    },
    "inventory_management": {
        "module": "workers.bpo.wholesale.pipelines.inventory_management_pipeline",
        "runner_name": "run_inventory_management_pipeline",
        "result_class_name": "InventoryManagementResult",
        "description": "在庫・倉庫管理（ABC分析+需要予測+安全在庫+発注点+FIFO）",
        "steps": 7,
        "status": "skeleton",
        "phase": "A",
        "tier": "tier1",
    },
    "accounts_receivable": {
        "module": "workers.bpo.wholesale.pipelines.accounts_receivable_pipeline",
        "runner_name": "run_accounts_receivable_pipeline",
        "result_class_name": "AccountsReceivableResult",
        "description": "請求・売掛管理（締日別請求+入金消込+与信管理+インボイス制度対応）",
        "steps": 6,
        "status": "skeleton",
        "phase": "B",
        "tier": "tier1",
    },
    "accounts_payable": {
        "module": "workers.bpo.wholesale.pipelines.accounts_payable_pipeline",
        "runner_name": "run_accounts_payable_pipeline",
        "result_class_name": "AccountsPayableResult",
        "description": "仕入・買掛管理（発注書+検収+三者照合+リベート計算+支払予定）",
        "steps": 7,
        "status": "skeleton",
        "phase": "B",
        "tier": "tier1",
    },
    "shipping": {
        "module": "workers.bpo.wholesale.pipelines.shipping_pipeline",
        "runner_name": "run_shipping_pipeline",
        "result_class_name": "ShippingResult",
        "description": "物流・配送管理（出荷指示+送り状+ピッキングリスト+配送追跡）",
        "steps": 6,
        "status": "skeleton",
        "phase": "C",
        "tier": "tier2",
    },
    "sales_intelligence": {
        "module": "workers.bpo.wholesale.pipelines.sales_intelligence_pipeline",
        "runner_name": "run_sales_intelligence_pipeline",
        "result_class_name": "SalesIntelligenceResult",
        "description": "営業支援（RFM分析+クロスABC+アソシエーション+季節需要予測）",
        "steps": 6,
        "status": "skeleton",
        "phase": "C",
        "tier": "tier2",
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
