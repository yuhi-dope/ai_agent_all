"""同意管理モジュール — 個人情報保護法・GDPR対応。

ユーザーの各種データ処理への同意を記録・管理する。
consent_records テーブルへの CRUD と「忘れられる権利」対応を提供する。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from db.supabase import get_service_client
from security.audit import audit_log

logger = logging.getLogger(__name__)

CONSENT_TYPES = [
    "knowledge_collection",   # ナレッジ収集への同意
    "data_processing",        # データ処理への同意
    "behavior_inference",     # 行動推論への同意
    "benchmark_sharing",      # 匿名ベンチマーク共有への同意
    "right_to_deletion",      # データ削除権の確認
    "data_portability",       # データポータビリティの確認
    "partner_data_sharing",   # パートナーとのデータ共有への同意
]

CONSENT_VERSION_LATEST = "1.0"


class ConsentRecord(BaseModel):
    """同意レコードのPydanticモデル。"""

    id: str
    company_id: str
    user_id: str
    consent_type: str
    granted_at: datetime
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    consent_version: str = CONSENT_VERSION_LATEST
    is_active: bool  # revoked_at is None かつ expires_at が未来


def _is_active(row: dict[str, Any]) -> bool:
    """DBレコードから is_active を計算する。"""
    if row.get("revoked_at") is not None:
        return False
    expires_at = row.get("expires_at")
    if expires_at is not None:
        # 文字列の場合は datetime に変換
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return False
    return True


def _row_to_model(row: dict[str, Any]) -> ConsentRecord:
    """DB行をConsentRecordモデルに変換する。"""

    def _parse_dt(val: Any) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=timezone.utc)
            return val
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))

    return ConsentRecord(
        id=str(row["id"]),
        company_id=str(row["company_id"]),
        user_id=str(row["user_id"]),
        consent_type=row["consent_type"],
        granted_at=_parse_dt(row["granted_at"]),
        revoked_at=_parse_dt(row.get("revoked_at")),
        expires_at=_parse_dt(row.get("expires_at")),
        consent_version=row.get("consent_version", CONSENT_VERSION_LATEST),
        is_active=_is_active(row),
    )


async def grant_consent(
    company_id: str,
    user_id: str,
    consent_type: str,
    version: str = CONSENT_VERSION_LATEST,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> ConsentRecord:
    """同意を付与して DB に保存する。

    同一 (company_id, user_id, consent_type) の有効な同意が既に存在する場合も
    新レコードを追加する（バージョン・日時を記録するため）。

    Args:
        company_id: テナントID
        user_id: 同意したユーザーのID
        consent_type: CONSENT_TYPES のいずれか
        version: 同意ポリシーのバージョン
        ip_address: 同意時のIPアドレス（任意）
        user_agent: 同意時のUserAgent（任意）
        expires_at: 同意の有効期限（任意）

    Returns:
        作成した ConsentRecord

    Raises:
        ValueError: consent_type が不正な場合
    """
    if consent_type not in CONSENT_TYPES:
        raise ValueError(
            f"無効な consent_type: {consent_type!r}. "
            f"有効な値: {CONSENT_TYPES}"
        )

    record_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    row: dict[str, Any] = {
        "id": record_id,
        "company_id": company_id,
        "user_id": user_id,
        "consent_type": consent_type,
        "granted_at": now.isoformat(),
        "consent_version": version,
    }
    if ip_address is not None:
        row["ip_address"] = ip_address
    if user_agent is not None:
        row["user_agent"] = user_agent
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        row["expires_at"] = expires_at.isoformat()

    db = get_service_client()
    result = (
        db.table("consent_records")
        .insert(row)
        .execute()
    )

    if not result.data:
        raise RuntimeError("consent_records への INSERT が失敗しました")

    await audit_log(
        company_id=company_id,
        user_id=user_id,
        action="create",
        resource_type="consent_record",
        resource_id=record_id,
        details={"consent_type": consent_type, "version": version},
        ip_address=ip_address,
    )

    return _row_to_model(result.data[0])


async def revoke_consent(
    company_id: str,
    user_id: str,
    consent_type: str,
) -> bool:
    """同意を取り消す（revoked_at を設定する）。

    対象の (company_id, user_id, consent_type) で revoked_at が NULL の
    全レコードに revoked_at = now() を設定する。

    Args:
        company_id: テナントID
        user_id: ユーザーID
        consent_type: 取り消す同意の種別

    Returns:
        取り消し対象レコードが 1 件以上あれば True、なければ False
    """
    if consent_type not in CONSENT_TYPES:
        raise ValueError(
            f"無効な consent_type: {consent_type!r}. "
            f"有効な値: {CONSENT_TYPES}"
        )

    now = datetime.now(timezone.utc)
    db = get_service_client()

    result = (
        db.table("consent_records")
        .update({"revoked_at": now.isoformat()})
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .eq("consent_type", consent_type)
        .is_("revoked_at", "null")
        .execute()
    )

    revoked_count = len(result.data) if result.data else 0

    if revoked_count > 0:
        await audit_log(
            company_id=company_id,
            user_id=user_id,
            action="update",
            resource_type="consent_record",
            details={"consent_type": consent_type, "revoked_count": revoked_count},
        )

    return revoked_count > 0


async def check_consent(
    company_id: str,
    user_id: str,
    consent_type: str,
) -> bool:
    """有効な同意があるか確認する。

    revoked_at IS NULL かつ (expires_at IS NULL または expires_at > now())
    のレコードが存在する場合に True を返す。

    Args:
        company_id: テナントID
        user_id: ユーザーID
        consent_type: 確認する同意の種別

    Returns:
        有効な同意があれば True
    """
    db = get_service_client()
    now = datetime.now(timezone.utc)

    # revoked_at IS NULL のレコードを取得
    result = (
        db.table("consent_records")
        .select("id, expires_at, revoked_at")
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .eq("consent_type", consent_type)
        .is_("revoked_at", "null")
        .execute()
    )

    if not result.data:
        return False

    for row in result.data:
        expires_at = row.get("expires_at")
        if expires_at is None:
            # 有効期限なし = 有効
            return True
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > now:
            return True

    return False


async def get_user_consents(
    company_id: str,
    user_id: str,
) -> list[ConsentRecord]:
    """ユーザーの全同意記録を取得する（有効・失効問わず）。

    Args:
        company_id: テナントID
        user_id: ユーザーID

    Returns:
        ConsentRecord のリスト（granted_at 降順）
    """
    db = get_service_client()

    result = (
        db.table("consent_records")
        .select("*")
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .order("granted_at", desc=True)
        .execute()
    )

    if not result.data:
        return []

    return [_row_to_model(row) for row in result.data]


async def delete_all_user_data(
    company_id: str,
    user_id: str,
) -> dict[str, int]:
    """忘れられる権利（Right of Erasure）対応。

    以下を処理する:
    1. knowledge_sessions で triggered_by = user_id のレコードを削除
    2. consent_records の未失効レコードに revoked_at を設定（物理削除はしない）

    Args:
        company_id: テナントID
        user_id: 削除対象ユーザーのID

    Returns:
        {"deleted_sessions": N, "revoked_consents": M}
    """
    db = get_service_client()
    now = datetime.now(timezone.utc)

    # 1. knowledge_sessions の削除
    sessions_result = (
        db.table("knowledge_sessions")
        .delete()
        .eq("company_id", company_id)
        .eq("triggered_by", user_id)
        .execute()
    )
    deleted_sessions = len(sessions_result.data) if sessions_result.data else 0

    # 2. consent_records の revoked_at 設定（物理削除せず論理削除）
    consents_result = (
        db.table("consent_records")
        .update({"revoked_at": now.isoformat()})
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .is_("revoked_at", "null")
        .execute()
    )
    revoked_consents = len(consents_result.data) if consents_result.data else 0

    await audit_log(
        company_id=company_id,
        user_id=user_id,
        action="delete",
        resource_type="user_data",
        details={
            "reason": "right_of_erasure",
            "deleted_sessions": deleted_sessions,
            "revoked_consents": revoked_consents,
        },
    )

    logger.info(
        "right_of_erasure: company=%s user=%s deleted_sessions=%d revoked_consents=%d",
        company_id, user_id, deleted_sessions, revoked_consents,
    )

    return {
        "deleted_sessions": deleted_sessions,
        "revoked_consents": revoked_consents,
    }
