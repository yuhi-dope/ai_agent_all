"""freee Typed Function Tools."""

from __future__ import annotations

from typing import Any

from server.saas.tools.http import SaaSCreds, api_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

register_saas(SaaSMetadata(
    saas_name="freee",
    display_name="freee会計",
    genre="accounting",
    description="仕訳作成・取引先管理・勘定科目管理・月次決算・請求書発行",
    supported_auth_methods=["oauth2"],
    default_scopes=["read", "write"],
))

_BASE = "https://api.freee.co.jp/api/1"


@saas_tool(saas="freee", genre="accounting")
async def freee_create_journal(
    company_id: int, details: list, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """仕訳を作成する"""
    return await api_request("POST", f"{_BASE}/deals", creds=creds, json={
        "company_id": company_id, "type": "expense", "details": details,
    })


@saas_tool(saas="freee", genre="accounting")
async def freee_list_journals(
    company_id: int, start_date: str = "", end_date: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """仕訳一覧を取得する"""
    params: dict[str, Any] = {"company_id": company_id}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return await api_request("GET", f"{_BASE}/deals", creds=creds, params=params)


@saas_tool(saas="freee", genre="accounting")
async def freee_list_partners(company_id: int, *, creds: SaaSCreds) -> dict[str, Any]:
    """取引先一覧を取得する"""
    return await api_request("GET", f"{_BASE}/partners", creds=creds, params={"company_id": company_id})


@saas_tool(saas="freee", genre="accounting")
async def freee_create_invoice(
    company_id: int, partner_id: int, items: list | None = None, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """請求書を作成する"""
    return await api_request("POST", f"{_BASE}/invoices", creds=creds, json={
        "company_id": company_id, "partner_id": partner_id,
        "invoice_lines": items or [],
    })


@saas_tool(saas="freee", genre="accounting")
async def freee_get_trial_balance(
    company_id: int, fiscal_year: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """試算表を取得する"""
    return await api_request("GET", f"{_BASE}/reports/trial_bs", creds=creds, params={
        "company_id": company_id, "fiscal_year": fiscal_year,
    })


@saas_tool(saas="freee", genre="accounting")
async def freee_list_account_items(company_id: int, *, creds: SaaSCreds) -> dict[str, Any]:
    """勘定科目一覧を取得する"""
    return await api_request("GET", f"{_BASE}/account_items", creds=creds, params={"company_id": company_id})


@saas_tool(saas="freee", genre="accounting")
async def freee_reconcile(
    company_id: int, bank_transaction_id: int, deal_id: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """入出金消込を実行する"""
    return await api_request(
        "PUT", f"{_BASE}/wallet_txns/{bank_transaction_id}/match",
        creds=creds, json={"deal_id": deal_id},
    )
