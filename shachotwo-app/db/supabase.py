"""Supabase client wrapper with RLS context management."""
import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from supabase import create_client, Client

logger = logging.getLogger(__name__)


def _get_url() -> str:
    url = os.environ.get("SUPABASE_URL", "")
    if not url:
        logger.error("SUPABASE_URL is not set!")
    return url


def _get_service_key() -> str:
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _get_anon_key() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "")


def get_service_client() -> Client:
    """Service client — bypasses RLS. Use for migrations/admin only."""
    return create_client(_get_url(), _get_service_key())


def get_client() -> Client:
    """Anon client — respects RLS."""
    return create_client(_get_url(), _get_anon_key())


# Lazy singleton
_service_client: Client | None = None


@property
def service_client() -> Client:
    global _service_client
    if _service_client is None:
        _service_client = get_service_client()
    return _service_client


@asynccontextmanager
async def with_company_context(company_id: str) -> AsyncGenerator[Client, None]:
    """Context manager that sets RLS company_id for the session.

    Usage:
        async with with_company_context(company_id) as client:
            result = client.table("knowledge_items").select("*").execute()
    """
    client = get_client()
    # Set company_id for RLS via PostgREST header
    # Supabase translates this to: SET LOCAL app.company_id = '<value>'
    client.postgrest.auth(
        token=None,
        headers={"x-company-id": company_id},
    )
    try:
        yield client
    finally:
        pass  # Connection cleanup handled by supabase-py
