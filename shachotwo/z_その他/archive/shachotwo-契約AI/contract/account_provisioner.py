"""Supabaseアカウント自動作成"""

from __future__ import annotations

from supabase import create_client

from config import settings


def _get_client():
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def provision_account(contract: dict) -> dict:
    """企業 & 管理者ユーザーを作成"""
    client = _get_client()

    # 企業作成
    company_data = {
        "name": contract.get("company_name", ""),
        "industry": contract.get("industry", ""),
        "plan": contract.get("plan", "starter"),
        "stripe_customer_id": contract.get("stripe_customer_id", ""),
    }
    company_result = client.table("companies").insert(company_data).execute()
    company = company_result.data[0]

    # 管理者ユーザー作成
    user_data = {
        "company_id": company["id"],
        "email": contract.get("email", ""),
        "display_name": contract.get("contact_name", ""),
        "role": "admin",
    }
    user_result = client.table("users").insert(user_data).execute()
    user = user_result.data[0]

    # TODO: 業種テンプレート適用（genome_templatesからコピー）
    # TODO: マジックリンク生成 → ログイン情報メール送信

    return {
        "company_id": company["id"],
        "user_id": user["id"],
        "email": contract.get("email", ""),
    }


async def generate_magic_link(email: str) -> str:
    """Supabase Authでマジックリンク生成"""
    client = _get_client()
    # Supabase Auth のマジックリンク
    result = client.auth.admin.generate_link({
        "type": "magiclink",
        "email": email,
        "options": {"redirect_to": settings.app_base_url},
    })
    return result.properties.action_link if result else ""
