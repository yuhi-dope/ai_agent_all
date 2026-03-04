"""kintone Typed Function Tools.

旧 server/saas/mcp/kintone.py (371行) を関数ベースに置き換え。
"""

from __future__ import annotations

from typing import Any

from server.saas.tools.http import SaaSCreds, kintone_request
from server.saas.tools.registry import SaaSMetadata, register_saas, saas_tool

# --- SaaS メタ情報 ---

register_saas(SaaSMetadata(
    saas_name="kintone",
    display_name="kintone",
    genre="admin",
    description="アプリのレコード操作・フィールド管理・ビュー取得・プロセス管理",
    supported_auth_methods=["api_key", "oauth2"],
    default_scopes=[
        "k:app_record:read", "k:app_record:write",
        "k:app_settings:read", "k:app_settings:write",
        "k:file:read", "k:file:write",
    ],
))


# --- ツール関数 ---

@saas_tool(saas="kintone", genre="admin")
async def kintone_get_records(
    app_id: int,
    query: str = "",
    fields: list | None = None,
    *,
    creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのレコード一覧を取得する"""
    params: dict[str, Any] = {"app": app_id}
    if query:
        params["query"] = query
    if fields:
        params["fields"] = fields
    return await kintone_request(creds, "GET", "/k/v1/records.json", params=params)


@saas_tool(saas="kintone", genre="admin")
async def kintone_add_record(
    app_id: int, record: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリにレコードを1件追加する"""
    return await kintone_request(
        creds, "POST", "/k/v1/record.json",
        json={"app": app_id, "record": record},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_update_record(
    app_id: int, record_id: int, record: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのレコードを更新する"""
    return await kintone_request(
        creds, "PUT", "/k/v1/record.json",
        json={"app": app_id, "id": record_id, "record": record},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_get_app_fields(
    app_id: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのフィールド定義を取得する"""
    return await kintone_request(
        creds, "GET", "/k/v1/app/form/fields.json", params={"app": app_id},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_get_apps(
    space_id: int | None = None, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone スペース内のアプリ一覧を取得する（ページネーション対応）"""
    params: dict[str, Any] = {}
    if space_id:
        params["spaceIds"] = [space_id]
    all_apps: list = []
    offset = 0
    limit = 100
    while True:
        params["limit"] = str(limit)
        params["offset"] = str(offset)
        result = await kintone_request(
            creds, "GET", "/k/v1/apps.json", params=params,
        )
        apps = result.get("apps", [])
        all_apps.extend(apps)
        if len(apps) < limit:
            break
        offset += limit
    return {"apps": all_apps}


@saas_tool(saas="kintone", genre="admin")
async def kintone_update_status(
    app_id: int, record_id: int, action: str, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone プロセス管理のステータスを更新する"""
    return await kintone_request(
        creds, "PUT", "/k/v1/record/status.json",
        json={"app": app_id, "id": record_id, "action": action},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_add_fields(
    app_id: int, fields: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリにフィールドを追加する（プレビュー環境）。追加後は kintone_deploy_app でデプロイが必要"""
    return await kintone_request(
        creds, "POST", "/k/v1/preview/app/form/fields.json",
        json={"app": app_id, "properties": fields},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_deploy_app(
    app_id: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリの設定変更を本番環境にデプロイする"""
    return await kintone_request(
        creds, "POST", "/k/v1/preview/app/deploy.json",
        json={"apps": [{"app": app_id}]},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_add_records(
    app_id: int, records: list, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリに複数レコードを一括追加する（最大100件）"""
    return await kintone_request(
        creds, "POST", "/k/v1/records.json",
        json={"app": app_id, "records": records},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_get_layout(
    app_id: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのフォームレイアウトを取得する"""
    return await kintone_request(
        creds, "GET", "/k/v1/app/form/layout.json", params={"app": app_id},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_update_layout(
    app_id: int, layout: list, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのフォームレイアウトを更新する（プレビュー環境）。全フィールドを指定すること"""
    return await kintone_request(
        creds, "PUT", "/k/v1/preview/app/form/layout.json",
        json={"app": app_id, "layout": layout},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_get_views(
    app_id: int, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのビュー設定を取得する"""
    return await kintone_request(
        creds, "GET", "/k/v1/app/views.json", params={"app": app_id},
    )


@saas_tool(saas="kintone", genre="admin")
async def kintone_update_views(
    app_id: int, views: dict, *, creds: SaaSCreds,
) -> dict[str, Any]:
    """kintone アプリのビュー設定を更新する（プレビュー環境）。全ビューを指定すること"""
    return await kintone_request(
        creds, "PUT", "/k/v1/preview/app/views.json",
        json={"app": app_id, "views": views},
    )
