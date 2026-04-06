"""Audit Logging — CRUD operations recorded to audit_logs table.

The audit_logs table is INSERT-only (no UPDATE/DELETE) per security design.
Logs are retained for 5 years per compliance requirements.

Usage:
    from security.audit import audit_log

    # In an endpoint:
    await audit_log(
        company_id=user.company_id,
        user_id=user.sub,
        action="create",
        resource_type="knowledge_item",
        resource_id=str(item_id),
        details={"title": "New rule"},
    )
"""
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


class AuditLogEntry(BaseModel):
    """Pydantic model for an audit log entry."""
    company_id: str
    user_id: Optional[str] = None
    action: str = Field(description="create / read / update / delete")
    resource_type: str = Field(description="knowledge_item / proposal / session / etc.")
    resource_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    ip_address: Optional[str] = None


class AuditLogger:
    """Audit logger that writes to the audit_logs table.

    Uses the service client (bypasses RLS) since audit logs must always
    be writable regardless of the request's RLS context.
    """

    async def log(
        self,
        company_id: str,
        user_id: Optional[str] = None,
        action: str = "",
        resource_type: str = "",
        resource_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        """Write an audit log entry to the database.

        This method is fire-and-forget safe — it catches and logs
        any exceptions rather than propagating them to avoid breaking
        the main request flow.
        """
        try:
            db = get_service_client()
            entry: dict[str, Any] = {
                "company_id": company_id,
                "action": action,
                "resource_type": resource_type,
            }
            if user_id is not None:
                entry["actor_user_id"] = user_id
            if resource_id is not None:
                entry["resource_id"] = resource_id
            if details is not None:
                entry["metadata"] = details
            if ip_address is not None:
                entry["ip_address"] = ip_address

            db.table("audit_logs").insert(entry).execute()
            logger.debug(
                "audit_log: %s %s %s by user=%s company=%s",
                action, resource_type, resource_id, user_id, company_id,
            )
        except Exception:
            logger.exception(
                "Failed to write audit log: action=%s resource_type=%s resource_id=%s",
                action, resource_type, resource_id,
            )


# Module-level singleton
_logger = AuditLogger()


async def audit_log(
    company_id: str,
    user_id: Optional[str] = None,
    action: str = "",
    resource_type: str = "",
    resource_id: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    """Convenience function — delegates to the singleton AuditLogger.

    Usage:
        await audit_log(
            company_id="...",
            user_id="...",
            action="create",
            resource_type="knowledge_item",
            resource_id="...",
            details={"title": "..."},
        )
    """
    await _logger.log(
        company_id=company_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
    )
