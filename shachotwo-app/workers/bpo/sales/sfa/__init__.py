"""workers/bpo/sales/sfa — SFA（営業支援）AI社員。

パイプライン:
    lead_qualification_pipeline   — SFA①  リードクオリフィケーション
    proposal_generation_pipeline  — SFA②  提案書AI生成・送付
    quotation_contract_pipeline   — SFA③  見積書・契約書自動送付
    consent_flow                  — SFA③b 電子同意フロー（CloudSign簡易代替）
"""
from workers.bpo.sales.sfa.lead_qualification_pipeline import (
    run_lead_qualification_pipeline,
    LeadQualificationResult,
)
from workers.bpo.sales.sfa.proposal_generation_pipeline import (
    run_proposal_generation_pipeline,
    ProposalGenerationResult,
    StepResult as ProposalStepResult,
)
from workers.bpo.sales.sfa.quotation_contract_pipeline import (
    run_quotation_contract_pipeline,
    QuotationContractResult,
    StepRecord as QuotationStepRecord,
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
    APPROVAL_REVISION_REQUESTED,
)
from workers.bpo.sales.sfa.consent_flow import (
    run_consent_flow_pipeline,
    process_consent_agreement,
    ConsentFlowResult,
    StepResult as ConsentFlowStepResult,
)

__all__ = [
    "run_lead_qualification_pipeline",
    "LeadQualificationResult",
    "run_proposal_generation_pipeline",
    "ProposalGenerationResult",
    "ProposalStepResult",
    "run_quotation_contract_pipeline",
    "QuotationContractResult",
    "QuotationStepRecord",
    "APPROVAL_PENDING",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "APPROVAL_REVISION_REQUESTED",
    "run_consent_flow_pipeline",
    "process_consent_agreement",
    "ConsentFlowResult",
    "ConsentFlowStepResult",
]
