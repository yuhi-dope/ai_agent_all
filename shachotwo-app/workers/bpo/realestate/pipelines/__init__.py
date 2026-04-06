"""不動産業 BPO パイプライン"""
from workers.bpo.realestate.pipelines.rent_collection_pipeline import (
    RentCollectionPipeline,
    run_rent_collection_pipeline,
)
from workers.bpo.realestate.pipelines.property_appraisal_pipeline import (
    run_property_appraisal_pipeline,
    PropertyAppraisalResult,
)
from workers.bpo.realestate.pipelines.contract_generation_pipeline import (
    run_contract_generation_pipeline,
    ContractGenerationResult,
)
from workers.bpo.realestate.pipelines.remittance_pipeline import (
    run_remittance_pipeline,
    RemittanceResult,
)
from workers.bpo.realestate.pipelines.property_listing_pipeline import (
    run_property_listing_pipeline,
    PropertyListingResult,
)
from workers.bpo.realestate.pipelines.property_crm_pipeline import (
    run_property_crm_pipeline,
    PropertyCrmResult,
)
from workers.bpo.realestate.pipelines.repair_management_pipeline import (
    run_repair_management_pipeline,
    RepairManagementResult,
)
from workers.bpo.realestate.pipelines.license_management_pipeline import (
    run_license_management_pipeline,
    LicenseManagementResult,
)

# パイプラインレジストリ（task_router から参照）
PIPELINE_REGISTRY: dict[str, object] = {
    "rent_collection":    run_rent_collection_pipeline,
    "property_appraisal": run_property_appraisal_pipeline,
    "contract_generation": run_contract_generation_pipeline,
    "remittance":         run_remittance_pipeline,
    "property_listing":   run_property_listing_pipeline,
    "property_crm":       run_property_crm_pipeline,
    "repair_management":  run_repair_management_pipeline,
    "license_management": run_license_management_pipeline,
}

__all__ = [
    # 家賃回収（既存）
    "RentCollectionPipeline",
    "run_rent_collection_pipeline",
    # 物件査定AI
    "run_property_appraisal_pipeline",
    "PropertyAppraisalResult",
    # 契約書AI自動生成
    "run_contract_generation_pipeline",
    "ContractGenerationResult",
    # 送金・入金管理
    "run_remittance_pipeline",
    "RemittanceResult",
    # 物件資料・広告作成AI
    "run_property_listing_pipeline",
    "PropertyListingResult",
    # 内見・顧客管理CRM
    "run_property_crm_pipeline",
    "PropertyCrmResult",
    # 修繕・設備管理
    "run_repair_management_pipeline",
    "RepairManagementResult",
    # 免許・届出管理
    "run_license_management_pipeline",
    "LicenseManagementResult",
    # レジストリ
    "PIPELINE_REGISTRY",
]
