"""Salesforce Typed Function Tools."""

from __future__ import annotations

from typing import Any

from server.saas.tools.http import SaaSCreds, api_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

register_saas(SaaSMetadata(
    saas_name="salesforce",
    display_name="Salesforce",
    genre="sfa",
    description="商談管理・取引先管理・リード管理・レポート取得",
    supported_auth_methods=["oauth2"],
    default_scopes=["api", "refresh_token", "offline_access"],
))

_API_VER = "v59.0"


def _base(creds: SaaSCreds) -> str:
    return f"{creds.instance_url}/services/data/{_API_VER}"


@saas_tool(saas="salesforce", genre="sfa")
async def sf_query(query: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """SOQL クエリを実行して Salesforce データを取得"""
    return await api_request("GET", f"{_base(creds)}/query", creds=creds, params={"q": query})


@saas_tool(saas="salesforce", genre="sfa")
async def sf_create_record(
    object_type: str, fields: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Salesforce オブジェクトのレコードを作成"""
    return await api_request("POST", f"{_base(creds)}/sobjects/{object_type}", creds=creds, json=fields)


@saas_tool(saas="salesforce", genre="sfa")
async def sf_update_record(
    object_type: str, record_id: str, fields: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """Salesforce レコードを更新"""
    return await api_request("PATCH", f"{_base(creds)}/sobjects/{object_type}/{record_id}", creds=creds, json=fields)


@saas_tool(saas="salesforce", genre="sfa")
async def sf_get_opportunity_pipeline(*, creds: SaaSCreds) -> dict[str, Any]:
    """商談パイプライン（ステージ別集計）を取得"""
    soql = "SELECT Id,Name,StageName,Amount,CloseDate FROM Opportunity WHERE IsClosed=false"
    return await api_request("GET", f"{_base(creds)}/query", creds=creds, params={"q": soql})


@saas_tool(saas="salesforce", genre="sfa")
async def sf_describe_object(object_type: str, *, creds: SaaSCreds) -> dict[str, Any]:
    """Salesforce オブジェクトのメタデータを取得"""
    return await api_request("GET", f"{_base(creds)}/sobjects/{object_type}/describe", creds=creds)
