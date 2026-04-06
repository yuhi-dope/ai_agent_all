"""SaaS connector management endpoints."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from auth.middleware import require_role
from auth.jwt import JWTClaims
from db.supabase import get_service_client
from security.audit import audit_log
from security.encryption import encrypt_field, decrypt_field
from workers.connector.base import ConnectorConfig
from workers.connector.kintone import KintoneConnector
from workers.bpo.sales.background_job_service import fetch_field_mappings_for_app
from workers.bpo.sales.kintone_credentials import resolve_kintone_credentials

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _encrypt_credentials(credentials: dict) -> str:
    """AES-256-GCM で credentials を暗号化して返す。

    MVP: アプリレイヤー暗号化（ENCRYPTION_KEY 環境変数から鍵を取得）。
    Enterprise: GCP KMS に移行予定。
    """
    return encrypt_field(credentials)


def _decrypt_credentials(encoded: str) -> dict:
    """AES-256-GCM で暗号化された credentials を復号して返す。"""
    return decrypt_field(encoded)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConnectorCreate(BaseModel):
    tool_name: str
    tool_type: str          # "saas" | "cli" | "api" | "manual"
    connection_config: dict  # {"api_key": "..."} など — credentials 相当


class ConnectorResponse(BaseModel):
    id: UUID
    tool_name: str
    tool_type: str
    connection_method: str
    health_status: str
    last_health_check: Optional[datetime] = None


class ConnectorListResponse(BaseModel):
    items: list[ConnectorResponse]
    total: int


class HealthCheckResponse(BaseModel):
    id: UUID
    tool_name: str
    health_status: str
    last_health_check: datetime
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_response(row: dict) -> ConnectorResponse:
    return ConnectorResponse(
        id=row["id"],
        tool_name=row["tool_name"],
        tool_type=row["tool_type"],
        connection_method=row["connection_method"],
        health_status=row["health_status"],
        last_health_check=row.get("last_health_check"),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/connectors", response_model=ConnectorListResponse)
async def list_connectors(
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ一覧（admin のみ）。connection_config（credentials）は返さない。"""
    try:
        db = get_service_client()
        result = db.table("tool_connections") \
            .select(
                "id, tool_name, tool_type, connection_method, health_status, last_health_check, status",
                count="exact",
            ) \
            .eq("company_id", str(user.company_id)) \
            .eq("status", "active") \
            .order("created_at", desc=True) \
            .execute()

        items = [_row_to_response(r) for r in (result.data or [])]
        return ConnectorListResponse(items=items, total=result.count or len(items))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"list_connectors failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors", response_model=ConnectorResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    body: ConnectorCreate,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ接続を登録（admin のみ）。credentials は暗号化して保存。"""
    # Validate tool_type
    allowed_tool_types = {"saas", "cli", "api", "manual"}
    if body.tool_type not in allowed_tool_types:
        raise HTTPException(
            status_code=422,
            detail=f"tool_type must be one of: {', '.join(sorted(allowed_tool_types))}",
        )

    # connection_config を暗号化（credentials として扱う）
    encrypted = _encrypt_credentials(body.connection_config)

    try:
        db = get_service_client()
        result = db.table("tool_connections").insert({
            "company_id": str(user.company_id),
            "tool_name": body.tool_name,
            "tool_type": body.tool_type,
            # MVP: connection_method は "api" 固定。Phase 2+ で自動選択
            "connection_method": "api",
            # encrypted credentials を connection_config に格納
            "connection_config": {"_encrypted": encrypted},
            "health_status": "unknown",
            "status": "active",
        }).execute()
    except Exception as e:
        logger.error(f"create_connector failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not result.data:
        raise HTTPException(status_code=500, detail="Insert returned no data")

    new_connector = _row_to_response(result.data[0])
    await audit_log(
        company_id=str(user.company_id),
        user_id=str(user.sub),
        action="connector_created",
        resource_type="tool_connection",
        resource_id=str(new_connector.id),
        details={"tool_name": body.tool_name, "tool_type": body.tool_type},
        ip_address=request.client.host if request.client else None,
    )
    return new_connector


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: UUID,
    request: Request,
    user: JWTClaims = Depends(require_role("admin")),
):
    """コネクタ削除（admin のみ）。論理削除（status='deleted'）。"""
    try:
        db = get_service_client()

        # 存在確認（company_id フィルタで tenant isolation）
        existing = db.table("tool_connections") \
            .select("id, tool_name") \
            .eq("id", str(connector_id)) \
            .eq("company_id", str(user.company_id)) \
            .eq("status", "active") \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Connector not found")

        tool_name = existing.data[0].get("tool_name", "") if existing.data else ""

        # 論理削除
        db.table("tool_connections") \
            .update({"status": "deleted"}) \
            .eq("id", str(connector_id)) \
            .eq("company_id", str(user.company_id)) \
            .execute()

        await audit_log(
            company_id=str(user.company_id),
            user_id=str(user.sub),
            action="connector_deleted",
            resource_type="tool_connection",
            resource_id=str(connector_id),
            details={"tool_name": tool_name},
            ip_address=request.client.host if request.client else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_connector failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/connectors/{connector_id}/test", response_model=HealthCheckResponse)
async def test_connector(
    connector_id: UUID,
    user: JWTClaims = Depends(require_role("admin")),
):
    """接続テスト（ヘルスチェック）。admin のみ。

    TODO: 実際の疎通確認（kintone/freee/Slack/LINE WORKS API ping）を実装する。
          現在は is_active 相当として health_status="healthy" に更新するだけ。
    """
    try:
        db = get_service_client()

        # 存在確認
        existing = db.table("tool_connections") \
            .select("id, tool_name, tool_type, connection_method, health_status") \
            .eq("id", str(connector_id)) \
            .eq("company_id", str(user.company_id)) \
            .eq("status", "active") \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Connector not found")

        now_iso = datetime.now(timezone.utc).isoformat()

        # ヘルスステータス更新
        update_result = db.table("tool_connections") \
            .update({
                "health_status": "healthy",
                "last_health_check": now_iso,
            }) \
            .eq("id", str(connector_id)) \
            .eq("company_id", str(user.company_id)) \
            .execute()

        if not update_result.data:
            raise HTTPException(status_code=500, detail="Health check update failed")

        row = update_result.data[0]
        return HealthCheckResponse(
            id=row["id"],
            tool_name=row["tool_name"],
            health_status=row["health_status"],
            last_health_check=datetime.fromisoformat(row["last_health_check"]),
            message="接続テスト成功",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"test_connector failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# kintone 拡張（b_10 §3-1, 3-2, 3-3）
# ---------------------------------------------------------------------------


class KintoneAppItem(BaseModel):
    appId: str
    name: str
    spaceId: Optional[str] = None


class KintoneAppsResponse(BaseModel):
    apps: list[KintoneAppItem]


class KintoneFieldItem(BaseModel):
    code: str
    label: str
    type: str
    required: bool = False


class KintoneFieldsResponse(BaseModel):
    fields: list[KintoneFieldItem]
    missing_required: list[str]


class KintoneMappingsBody(BaseModel):
    field_mappings: dict[str, str]


class KintoneMappingsSavedResponse(BaseModel):
    saved: bool


def _kintone_connector_for_company(company_id: str) -> KintoneConnector:
    creds = resolve_kintone_credentials(company_id)
    return KintoneConnector(
        ConnectorConfig(tool_name="kintone", credentials=creds)
    )


def _missing_required_for_mfg(
    field_codes_in_app: set[str],
    field_mappings: dict[str, str] | None,
) -> list[str]:
    """leads 取り込みに必須の canonical キーが kintone 上に解決できるか。"""
    m = field_mappings or {}
    missing: list[str] = []
    for canonical in ("name", "corporate_number"):
        kc = m.get(canonical, canonical)
        if kc not in field_codes_in_app:
            missing.append(canonical)
    return missing


def _missing_required_for_construction(
    field_codes_in_app: set[str],
    field_mappings: dict[str, str] | None,
) -> list[str]:
    m = field_mappings or {}
    missing: list[str] = []
    for canonical in ("name", "corporate_number"):
        kc = m.get(canonical, canonical)
        if kc not in field_codes_in_app:
            missing.append(canonical)
    return missing


@router.get("/connectors/kintone/apps", response_model=KintoneAppsResponse)
async def kintone_list_apps(
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneAppsResponse:
    try:
        conn = _kintone_connector_for_company(str(user.company_id))
        apps_raw = await conn.list_apps()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("kintone_list_apps: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"kintone API 障害: {e}",
        ) from e
    apps = [KintoneAppItem(**a) for a in apps_raw]
    return KintoneAppsResponse(apps=apps)


@router.get(
    "/connectors/kintone/apps/{app_id}/fields",
    response_model=KintoneFieldsResponse,
)
async def kintone_list_app_fields(
    app_id: str,
    industry: str = "manufacturing",
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneFieldsResponse:
    """industry=manufacturing|construction で必須チェック対象を切り替え。"""
    try:
        conn = _kintone_connector_for_company(str(user.company_id))
        raw = await conn.list_form_fields(app_id)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("kintone_list_app_fields: %s", e)
        raise HTTPException(status_code=502, detail=f"kintone API 障害: {e}") from e

    fields = [KintoneFieldItem(**f) for f in raw]
    codes = {f.code for f in fields}
    db = get_service_client()
    fm = fetch_field_mappings_for_app(db, str(user.company_id), app_id)

    if industry == "construction":
        missing = _missing_required_for_construction(codes, fm)
    else:
        missing = _missing_required_for_mfg(codes, fm)

    return KintoneFieldsResponse(fields=fields, missing_required=missing)


@router.get(
    "/connectors/kintone/apps/{app_id}/mappings",
    response_model=KintoneMappingsBody,
)
async def kintone_get_mappings(
    app_id: str,
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneMappingsBody:
    db = get_service_client()
    fm = fetch_field_mappings_for_app(db, str(user.company_id), app_id)
    return KintoneMappingsBody(field_mappings=fm or {})


@router.post(
    "/connectors/kintone/apps/{app_id}/mappings",
    response_model=KintoneMappingsSavedResponse,
)
async def kintone_save_mappings(
    app_id: str,
    body: KintoneMappingsBody,
    user: JWTClaims = Depends(require_role("admin")),
) -> KintoneMappingsSavedResponse:
    db = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    db.table("kintone_field_mappings").upsert(
        {
            "company_id": str(user.company_id),
            "app_id": app_id,
            "field_mappings": body.field_mappings,
            "updated_at": now_iso,
        },
        on_conflict="company_id,app_id",
    ).execute()
    return KintoneMappingsSavedResponse(saved=True)
