"""士業BPO FastAPIルーター

社労士・税理士・行政書士・弁護士 の各パイプラインエンドポイント + 共通期限管理
"""
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.middleware import get_current_user
from security.rate_limiter import check_rate_limit
from workers.bpo.professional.pipelines import (
    run_procedure_generation_pipeline,
    run_bookkeeping_check_pipeline,
    run_permit_generation_pipeline,
    run_contract_review_pipeline,
    run_deadline_mgmt_pipeline,
)
from db.supabase import get_service_client as get_client

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────────────────
# リクエストモデル
# ─────────────────────────────────────

class ProfessionalPipelineRequest(BaseModel):
    """士業BPOパイプライン 汎用リクエスト"""
    input_data: dict[str, Any]
    options: dict[str, Any] = {}


# ─────────────────────────────────────
# ヘルパー: 実行ログ保存
# ─────────────────────────────────────

def _save_pipeline_log(
    company_id: str,
    pipeline_type: str,
    profession_type: str,
    input_data: dict[str, Any],
    result: Any,
    execution_time_ms: int,
) -> None:
    """professional_pipeline_logs テーブルに実行結果を記録する（失敗しても無視）"""
    try:
        client = get_client()
        is_valid = getattr(result, "is_valid", True)
        compliance_warnings = getattr(result, "compliance_warnings", [])
        warnings_count = len(compliance_warnings) if isinstance(compliance_warnings, list) else 0

        output_summary: dict[str, Any] = {}
        for attr in (
            "procedure_type", "permit_type", "contract_type",
            "risk_score", "error_count", "warning_count",
        ):
            val = getattr(result, attr, None)
            if val is not None:
                output_summary[attr] = val

        client.table("professional_pipeline_logs").insert({
            "company_id": company_id,
            "pipeline_type": pipeline_type,
            "profession_type": profession_type,
            "input_summary": {k: str(v)[:200] for k, v in input_data.items()},
            "output_summary": output_summary,
            "is_valid": is_valid,
            "warnings_count": warnings_count,
            "execution_time_ms": execution_time_ms,
        }).execute()
    except Exception:
        logger.warning("professional_pipeline_log の保存に失敗しました（無視して続行）")


# ─────────────────────────────────────
# 社労士: 手続き書類自動生成
# ─────────────────────────────────────

@router.post("/procedure-generation")
async def run_procedure_generation(
    body: ProfessionalPipelineRequest,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """社労士: 各種届出書類（資格取得届・喪失届・算定基礎届等）のドラフトを自動生成"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    t0 = time.monotonic()
    try:
        result = await run_procedure_generation_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _save_pipeline_log(
            company_id=str(user.company_id),
            pipeline_type="procedure_generation",
            profession_type="labor_consultant",
            input_data=body.input_data,
            result=result,
            execution_time_ms=elapsed_ms,
        )
        return {
            "success": result.is_valid,
            "procedure_type": result.procedure_type,
            "procedure_name": result.procedure_name,
            "is_eligible": result.is_eligible,
            "missing_fields": result.missing_fields,
            "generated_form": result.generated_form,
            "form_fields": result.form_fields,
            "compliance_warnings": result.compliance_warnings,
            "deadline_date": result.deadline_date.isoformat() if result.deadline_date else None,
            "is_compliant": result.is_compliant,
            "validation_errors": result.validation_errors,
            "is_valid": result.is_valid,
            "execution_time_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("procedure_generation_pipeline 失敗")
        raise HTTPException(status_code=500, detail="手続き書類生成中にエラーが発生しました")


# ─────────────────────────────────────
# 税理士: 記帳自動チェック
# ─────────────────────────────────────

@router.post("/bookkeeping-check")
async def run_bookkeeping_check(
    body: ProfessionalPipelineRequest,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """税理士: 仕訳データの科目妥当性・消費税区分・金額チェックを自動実行"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    t0 = time.monotonic()
    try:
        result = await run_bookkeeping_check_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _save_pipeline_log(
            company_id=str(user.company_id),
            pipeline_type="bookkeeping_check",
            profession_type="tax_accountant",
            input_data=body.input_data,
            result=result,
            execution_time_ms=elapsed_ms,
        )
        return {
            "success": result.is_valid,
            "company_name": result.company_name,
            "period_year": result.period_year,
            "period_month": result.period_month,
            "journal_count": result.journal_count,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "info_count": result.info_count,
            "account_issues": result.account_issues,
            "tax_category_issues": result.tax_category_issues,
            "amount_issues": result.amount_issues,
            "check_items": [
                {
                    "severity": item.severity,
                    "category": item.category,
                    "message": item.message,
                    "journal_index": item.journal_index,
                    "account": item.account,
                    "amount": item.amount,
                }
                for item in result.check_items
            ],
            "validation_errors": result.validation_errors,
            "is_valid": result.is_valid,
            "execution_time_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("bookkeeping_check_pipeline 失敗")
        raise HTTPException(status_code=500, detail="記帳チェック中にエラーが発生しました")


# ─────────────────────────────────────
# 行政書士: 許可申請書自動生成
# ─────────────────────────────────────

@router.post("/permit-generation")
async def run_permit_generation(
    body: ProfessionalPipelineRequest,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """行政書士: 建設業許可・産廃収集運搬許可等の申請書ドラフトを自動生成"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    t0 = time.monotonic()
    try:
        result = await run_permit_generation_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _save_pipeline_log(
            company_id=str(user.company_id),
            pipeline_type="permit_generation",
            profession_type="administrative_scribe",
            input_data=body.input_data,
            result=result,
            execution_time_ms=elapsed_ms,
        )
        return {
            "success": result.is_valid,
            "permit_type": result.permit_type,
            "permit_name": result.permit_name,
            "category": result.category,
            "company_name": result.company_name,
            "all_requirements_met": result.all_requirements_met,
            "unmet_requirements": result.unmet_requirements,
            "requirement_checks": [
                {
                    "requirement": item.requirement,
                    "met": item.met,
                    "actual_value": item.actual_value,
                    "required_value": item.required_value,
                    "note": item.note,
                }
                for item in result.requirement_checks
            ],
            "generated_documents": result.generated_documents,
            "required_attachments": result.required_attachments,
            "compliance_warnings": result.compliance_warnings,
            "estimated_processing_days": result.estimated_processing_days,
            "validation_errors": result.validation_errors,
            "is_valid": result.is_valid,
            "execution_time_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("permit_generation_pipeline 失敗")
        raise HTTPException(status_code=500, detail="許可申請書生成中にエラーが発生しました")


# ─────────────────────────────────────
# 弁護士: 契約書レビュー
# ─────────────────────────────────────

@router.post("/contract-review")
async def run_contract_review(
    body: ProfessionalPipelineRequest,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """弁護士: 契約書のリスク条項を自動検出し、修正案を提示"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    t0 = time.monotonic()
    try:
        result = await run_contract_review_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        _save_pipeline_log(
            company_id=str(user.company_id),
            pipeline_type="contract_review",
            profession_type="lawyer",
            input_data=body.input_data,
            result=result,
            execution_time_ms=elapsed_ms,
        )
        return {
            "success": result.is_valid,
            "contract_type": result.contract_type,
            "contract_type_name": result.contract_type_name,
            "company_name": result.company_name,
            "counterparty": result.counterparty,
            "clause_count": result.clause_count,
            "key_clauses_found": result.key_clauses_found,
            "key_clauses_missing": result.key_clauses_missing,
            "risk_score": result.risk_score,
            "high_risks": result.high_risks,
            "medium_risks": result.medium_risks,
            "risks": [
                {
                    "severity": item.severity,
                    "clause_title": item.clause_title,
                    "issue": item.issue,
                    "recommendation": item.recommendation,
                }
                for item in result.risks
            ],
            "suggestions": result.suggestions,
            "validation_errors": result.validation_errors,
            "is_valid": result.is_valid,
            "execution_time_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("contract_review_pipeline 失敗")
        raise HTTPException(status_code=500, detail="契約書レビュー中にエラーが発生しました")


# ─────────────────────────────────────
# 共通: 期限管理
# ─────────────────────────────────────

@router.post("/deadline-management")
async def run_deadline_management(
    body: ProfessionalPipelineRequest,
    user=Depends(get_current_user),
) -> dict[str, Any]:
    """共通: 社労士・税理士・行政書士の各種法定期限を一括管理・アラート生成"""
    check_rate_limit(str(user.company_id), "bpo_pipeline")
    t0 = time.monotonic()
    try:
        result = await run_deadline_mgmt_pipeline(
            company_id=str(user.company_id),
            input_data=body.input_data,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        try:
            client = get_client()
            client.table("professional_pipeline_logs").insert({
                "company_id": str(user.company_id),
                "pipeline_type": "deadline_management",
                "profession_type": "common",
                "input_summary": {k: str(v)[:200] for k, v in body.input_data.items()},
                "output_summary": {
                    "critical_cases": len(result.critical_cases),
                    "overdue_cases": len(result.overdue_cases),
                    "upcoming_deadlines": len(result.upcoming_deadlines),
                },
                "is_valid": result.is_valid,
                "warnings_count": 0,
                "execution_time_ms": elapsed_ms,
            }).execute()
        except Exception:
            logger.warning("deadline_management ログ保存失敗（無視して続行）")

        return {
            "success": result.is_valid,
            "office_name": result.office_name,
            "reference_date": result.reference_date.isoformat() if result.reference_date else None,
            "active_cases": result.active_cases,
            "critical_cases": result.critical_cases,
            "upcoming_deadlines": result.upcoming_deadlines,
            "overdue_cases": result.overdue_cases,
            "task_list": result.task_list,
            "validation_errors": result.validation_errors,
            "is_valid": result.is_valid,
            "execution_time_ms": elapsed_ms,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("deadline_mgmt_pipeline 失敗")
        raise HTTPException(status_code=500, detail="期限管理処理中にエラーが発生しました")
