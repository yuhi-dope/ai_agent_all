"""SmartHR Typed Function Tools."""

from __future__ import annotations

from typing import Any

from server.saas.tools.http import SaaSCreds, api_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

register_saas(SaaSMetadata(
    saas_name="smarthr",
    display_name="SmartHR",
    genre="admin",
    description="従業員情報管理・入退社手続き・年末調整・給与明細配付・組織図管理",
    supported_auth_methods=["oauth2"],
    default_scopes=["read", "write"],
))


def _base(creds: SaaSCreds) -> str:
    return f"{creds.instance_url}/api/v1"


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_list_crews(
    page: int = 0, per_page: int = 0, status: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """従業員一覧を取得する"""
    params: dict[str, Any] = {}
    if page:
        params["page"] = page
    if per_page:
        params["per_page"] = per_page
    if status:
        params["status"] = status
    return await api_request("GET", f"{_base(creds)}/crews", creds=creds, params=params)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_get_crew(crew_id: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """従業員の詳細情報を取得する"""
    return await api_request("GET", f"{_base(creds)}/crews/{crew_id}", creds=creds)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_create_crew(
    last_name: str, first_name: str, email: str, department_id: str = "", *, creds: SaaSCreds,
) -> dict[str, Any]:
    """従業員を新規登録する（入社手続き）"""
    body: dict[str, Any] = {"last_name": last_name, "first_name": first_name, "email": email}
    if department_id:
        body["department_id"] = department_id
    return await api_request("POST", f"{_base(creds)}/crews", creds=creds, json=body)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_update_crew(
    crew_id: str, fields: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """従業員情報を更新する"""
    return await api_request("PATCH", f"{_base(creds)}/crews/{crew_id}", creds=creds, json=fields)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_list_departments(*, creds: SaaSCreds) -> dict[str, Any]:
    """部署一覧を取得する"""
    return await api_request("GET", f"{_base(creds)}/departments", creds=creds)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_list_employment_types(*, creds: SaaSCreds) -> dict[str, Any]:
    """雇用形態一覧を取得する"""
    return await api_request("GET", f"{_base(creds)}/employment_types", creds=creds)


@saas_tool(saas="smarthr", genre="admin")
async def smarthr_get_payroll_statement(
    crew_id: str, year: int, month: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """給与明細を取得する"""
    return await api_request(
        "GET", f"{_base(creds)}/crews/{crew_id}/payroll_statements",
        creds=creds, params={"year": year, "month": month},
    )
