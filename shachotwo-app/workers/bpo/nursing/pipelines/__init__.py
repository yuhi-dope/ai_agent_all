"""介護・福祉業BPOパイプライン一覧

各パイプラインはlazyにインポートされます。
直接利用時は個別モジュールをインポートしてください。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workers.bpo.nursing.pipelines.care_billing_pipeline import (
        CareBillingPipelineResult,
    )
    from workers.bpo.nursing.pipelines.care_plan_pipeline import (
        CarePlanResult,
    )
    from workers.bpo.nursing.pipelines.shift_management_pipeline import (
        ShiftManagementResult,
    )
    from workers.bpo.nursing.pipelines.care_record_pipeline import (
        CareRecordResult,
    )
    from workers.bpo.nursing.pipelines.medication_management_pipeline import (
        MedicationManagementResult,
    )
    from workers.bpo.nursing.pipelines.audit_preparation_pipeline import (
        AuditPreparationResult,
    )
    from workers.bpo.nursing.pipelines.billing_collection_pipeline import (
        BillingCollectionResult,
    )


# パイプラインレジストリ（メタデータのみ。runnerはlazy importで提供）
# key: パイプライン識別子, value: {module, runner_name, result_class_name, description, steps, status}
PIPELINE_REGISTRY: dict[str, dict[str, Any]] = {
    "care_billing": {
        "module": "workers.bpo.nursing.pipelines.care_billing_pipeline",
        "runner_name": "run_care_billing_pipeline",
        "result_class_name": "CareBillingPipelineResult",
        "description": "介護報酬請求AI（実績→サービスコード→加算チェック→国保連請求）",
        "steps": 5,
        "status": "implemented",
    },
    "care_plan": {
        "module": "workers.bpo.nursing.pipelines.care_plan_pipeline",
        "runner_name": "run_care_plan_pipeline",
        "result_class_name": "CarePlanResult",
        "description": "ケアプラン作成支援（アセスメント→ニーズ抽出→第1〜7表生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "shift_management": {
        "module": "workers.bpo.nursing.pipelines.shift_management_pipeline",
        "runner_name": "run_shift_management_pipeline",
        "result_class_name": "ShiftManagementResult",
        "description": "シフト・勤怠管理（72時間ルール/配置基準チェック→シフト表生成）",
        "steps": 7,
        "status": "skeleton",
    },
    "care_record": {
        "module": "workers.bpo.nursing.pipelines.care_record_pipeline",
        "runner_name": "run_care_record_pipeline",
        "result_class_name": "CareRecordResult",
        "description": "記録・日誌AI（バイタル→SOAP記録→状態変化検知）",
        "steps": 7,
        "status": "skeleton",
    },
    "medication_management": {
        "module": "workers.bpo.nursing.pipelines.medication_management_pipeline",
        "runner_name": "run_medication_management_pipeline",
        "result_class_name": "MedicationManagementResult",
        "description": "服薬管理（処方→スケジュール→相互作用チェック→介助適法性確認）",
        "steps": 7,
        "status": "skeleton",
    },
    "audit_preparation": {
        "module": "workers.bpo.nursing.pipelines.audit_preparation_pipeline",
        "runner_name": "run_audit_preparation_pipeline",
        "result_class_name": "AuditPreparationResult",
        "description": "監査・実地指導準備（チェックリスト→書類欠損検出→指摘事項予測）",
        "steps": 7,
        "status": "skeleton",
    },
    "billing_collection": {
        "module": "workers.bpo.nursing.pipelines.billing_collection_pipeline",
        "runner_name": "run_billing_collection_pipeline",
        "result_class_name": "BillingCollectionResult",
        "description": "請求・入金管理（自己負担金→公費判定→請求書生成→未収金督促）",
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
