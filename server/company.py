"""
Company (テナント) CRUD。
Supabase の companies / user_companies テーブルを操作する。
"""

import hashlib
import os
import secrets
import string


def _generate_slug() -> str:
    """ランダムな会社IDを生成する。英数字10桁。"""
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(10))


def _get_client():
    """Supabase クライアントを返す（シングルトン）。"""
    from server._supabase import get_client

    return get_client()


def _hash_password(password: str) -> str:
    """パスワードをハッシュ化して 'salt_hex:key_hex' 形式で返す。"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored_hash: str) -> bool:
    """保存済みハッシュとパスワードを照合する。"""
    try:
        salt_hex, key_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
    except (ValueError, AttributeError):
        return False
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(key, expected_key)


def create_company(
    name: str,
    employee_count: str = "",
    annual_revenue: str = "",
    industry: str = "",
    founded_date: str = "",
    corporate_number: str = "",
    password: str = "",
) -> dict | None:
    """会社を作成して返す。slug はサーバー側で自動生成（英数字10桁）。"""
    client = _get_client()
    if not client:
        return None
    # slug 衝突時は最大5回リトライ
    for _ in range(5):
        slug = _generate_slug()
        try:
            row = {"name": name, "slug": slug}
            if employee_count:
                row["employee_count"] = employee_count
            if annual_revenue:
                row["annual_revenue"] = annual_revenue
            if industry:
                row["industry"] = industry
            if founded_date:
                row["founded_date"] = founded_date
            if corporate_number:
                row["corporate_number"] = corporate_number
            if password:
                row["password_hash"] = _hash_password(password)
            r = client.table("companies").insert(row).execute()
            rows = r.data or []
            return rows[0] if rows else None
        except Exception:
            continue
    return None


def get_company_by_slug(slug: str) -> dict | None:
    """slug で会社を検索。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = (
            client.table("companies")
            .select("*")
            .eq("slug", slug)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def login_company(slug: str, password: str) -> dict | None:
    """slug + パスワードで会社を認証する。成功時は会社 dict、失敗時は None。"""
    company = get_company_by_slug(slug)
    if not company:
        return None
    stored_hash = company.get("password_hash") or ""
    if not stored_hash:
        return None
    if not _verify_password(password, stored_hash):
        return None
    return company


def get_company_by_id(company_id: str) -> dict | None:
    """ID で会社を取得。"""
    client = _get_client()
    if not client:
        return None
    try:
        r = (
            client.table("companies")
            .select("*")
            .eq("id", company_id)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_user_companies(user_id: str) -> list:
    """ユーザーの所属会社一覧を返す。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("user_companies")
            .select("company_id, role")
            .eq("user_id", user_id)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def add_user_to_company(
    user_id: str, company_id: str, role: str = "member"
) -> bool:
    """ユーザーを会社に紐付ける。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("user_companies").insert(
            {"user_id": user_id, "company_id": company_id, "role": role}
        ).execute()
        return True
    except Exception:
        return False


def search_companies(query: str, limit: int = 10) -> list:
    """会社名または slug の部分一致検索（オートコンプリート用）。"""
    client = _get_client()
    if not client:
        return []
    q = query.strip().lower()
    if not q:
        return []
    try:
        r = (
            client.table("companies")
            .select("id, name, slug")
            .or_(f"name.ilike.%{q}%,slug.ilike.%{q}%")
            .limit(limit)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


def get_all_companies() -> list:
    """全会社一覧（admin 用）。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("companies")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return list(r.data) if r.data else []
    except Exception:
        return []


# -- オンボーディング --

ONBOARDING_STEPS = [
    "company_profile",
    "github_repo",
    "github_initial_commit",
    "vercel_project",
    "vercel_env",
    "supabase_project",
    "supabase_tables",
    "secret_manager_token",
    "env_github_repository",
]


def update_company_profile(
    company_id: str,
    employee_count: str = "",
    annual_revenue: str = "",
    industry: str = "",
    founded_date: str = "",
) -> dict | None:
    """企業プロフィールを更新する。3項目すべて入力済みなら onboarding の company_profile を自動で true にする。"""
    client = _get_client()
    if not client:
        return None
    try:
        updates: dict = {}
        if employee_count:
            updates["employee_count"] = employee_count
        if annual_revenue:
            updates["annual_revenue"] = annual_revenue
        if industry:
            updates["industry"] = industry
        if founded_date:
            updates["founded_date"] = founded_date
        if not updates:
            return None

        # 3項目すべて埋まっていたら onboarding の company_profile を true にする
        if employee_count and annual_revenue and industry and founded_date:
            current_ob = get_onboarding(company_id)
            current_ob["company_profile"] = True
            import json

            updates["onboarding"] = json.dumps(current_ob)

        r = (
            client.table("companies")
            .update(updates)
            .eq("id", company_id)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_onboarding(company_id: str) -> dict:
    """会社のオンボーディングステータスを返す。未設定時はデフォルト。"""
    company = get_company_by_id(company_id)
    if not company:
        return {step: False for step in ONBOARDING_STEPS}
    ob = company.get("onboarding") or {}
    if isinstance(ob, str):
        import json
        try:
            ob = json.loads(ob)
        except Exception:
            ob = {}
    return {step: ob.get(step, False) for step in ONBOARDING_STEPS}


def update_onboarding(company_id: str, updates: dict) -> dict | None:
    """オンボーディングステータスを部分更新する。"""
    client = _get_client()
    if not client:
        return None
    current = get_onboarding(company_id)
    for key, val in updates.items():
        if key in ONBOARDING_STEPS:
            current[key] = bool(val)
    try:
        import json
        client.table("companies").update(
            {"onboarding": json.dumps(current)}
        ).eq("id", company_id).execute()
        return current
    except Exception:
        return None


# -- インフラ設定 --

INFRA_FIELDS = [
    "github_repository",
    "github_token_secret_name",
    "client_supabase_url",
    "vercel_project_url",
]

# 暗号化して保存するインフラトークンカラム
_INFRA_TOKEN_FIELDS = [
    "supabase_mgmt_token_enc",
    "vercel_token_enc",
]

# トークンカラム → 有効期限カラムのマッピング
_INFRA_TOKEN_EXPIRES = {
    "supabase_mgmt_token_enc": "supabase_mgmt_token_expires_at",
    "vercel_token_enc": "vercel_token_expires_at",
}

# インフラ項目 → 自動完了するオンボーディングステップのマッピング
_INFRA_TO_OB_STEP = {
    "github_repository": "github_repo",
    "github_token_secret_name": "secret_manager_token",
    "client_supabase_url": "supabase_project",
    "vercel_project_url": "vercel_project",
}


def get_company_infra(company_id: str) -> dict:
    """会社のインフラ設定値を返す。トークンの有無と有効期限も含む（値は返さない）。"""
    company = get_company_by_id(company_id)
    if not company:
        result = {f: None for f in INFRA_FIELDS}
        for tf in _INFRA_TOKEN_FIELDS:
            result[tf + "_exists"] = False
            result[_INFRA_TOKEN_EXPIRES[tf]] = None
        return result
    result = {f: company.get(f) for f in INFRA_FIELDS}
    for tf in _INFRA_TOKEN_FIELDS:
        result[tf + "_exists"] = bool(company.get(tf))
        exp_field = _INFRA_TOKEN_EXPIRES[tf]
        result[exp_field] = company.get(exp_field)
    return result


def get_infra_token(company_id: str, field: str) -> str | None:
    """暗号化されたインフラトークンを復号して返す。"""
    if field not in _INFRA_TOKEN_FIELDS:
        return None
    company = get_company_by_id(company_id)
    if not company:
        return None
    enc_value = company.get(field)
    if not enc_value:
        return None
    from server.crypto import decrypt
    return decrypt(enc_value)


def save_infra_token(
    company_id: str, field: str, plaintext: str, expires_at: str = "",
) -> bool:
    """インフラトークンを暗号化して companies テーブルに保存する。"""
    if field not in _INFRA_TOKEN_FIELDS:
        return False
    client = _get_client()
    if not client:
        return False
    from server.crypto import encrypt
    enc_value = encrypt(plaintext)
    updates: dict = {field: enc_value}
    if expires_at:
        updates[_INFRA_TOKEN_EXPIRES[field]] = expires_at
    try:
        client.table("companies").update(updates).eq("id", company_id).execute()
        return True
    except Exception:
        return False


def update_infra_token_expires(company_id: str, field: str, expires_at: str) -> bool:
    """トークンの有効期限だけを更新する（トークン再入力不要）。"""
    if field not in _INFRA_TOKEN_FIELDS:
        return False
    client = _get_client()
    if not client:
        return False
    exp_col = _INFRA_TOKEN_EXPIRES[field]
    val = expires_at if expires_at else None
    try:
        client.table("companies").update(
            {exp_col: val}
        ).eq("id", company_id).execute()
        return True
    except Exception:
        return False


def update_company_infra(company_id: str, updates: dict) -> dict | None:
    """インフラ設定を更新する。値が入力されたら対応するオンボーディングステップを自動完了にする。"""
    client = _get_client()
    if not client:
        return None
    try:
        import json

        db_updates: dict = {}
        for key, val in updates.items():
            if key in INFRA_FIELDS and val is not None:
                db_updates[key] = str(val).strip()

        if not db_updates:
            return None

        # 値が入力された項目に対応するオンボーディングステップを自動完了
        current_ob = get_onboarding(company_id)
        ob_changed = False
        for infra_key, ob_step in _INFRA_TO_OB_STEP.items():
            if infra_key in db_updates and db_updates[infra_key]:
                if not current_ob.get(ob_step):
                    current_ob[ob_step] = True
                    ob_changed = True
        if ob_changed:
            db_updates["onboarding"] = json.dumps(current_ob)

        r = (
            client.table("companies")
            .update(db_updates)
            .eq("id", company_id)
            .execute()
        )
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def disconnect_infra(company_id: str, service: str) -> bool:
    """インフラ接続を解除する。関連するインフラ設定・トークン・オンボーディングをクリア。"""
    client = _get_client()
    if not client:
        return False

    import json

    # サービスごとにクリアするカラムとオンボーディングステップを定義
    _SERVICE_MAP = {
        "github": {
            "infra_fields": ["github_repository", "github_token_secret_name"],
            "token_fields": [],
            "ob_steps": ["github_repo", "github_initial_commit", "env_github_repository"],
        },
        "supabase": {
            "infra_fields": ["client_supabase_url"],
            "token_fields": ["supabase_mgmt_token_enc", "supabase_mgmt_token_expires_at"],
            "ob_steps": ["supabase_project", "supabase_tables"],
        },
        "vercel": {
            "infra_fields": ["vercel_project_url"],
            "token_fields": ["vercel_token_enc", "vercel_token_expires_at"],
            "ob_steps": ["vercel_project", "vercel_env"],
        },
    }

    mapping = _SERVICE_MAP.get(service)
    if not mapping:
        return False

    try:
        # インフラ設定 + トークンカラムを NULL にする
        db_updates: dict = {}
        for field in mapping["infra_fields"]:
            db_updates[field] = None
        for field in mapping["token_fields"]:
            db_updates[field] = None

        # オンボーディングステップを False に戻す
        current_ob = get_onboarding(company_id)
        for step in mapping["ob_steps"]:
            current_ob[step] = False
        db_updates["onboarding"] = json.dumps(current_ob)

        client.table("companies").update(db_updates).eq("id", company_id).execute()
        return True
    except Exception:
        return False


# -- 招待トークン --

def generate_invite_token(
    company_id: str,
    created_by: str = "",
    role: str = "member",
    expires_days: int = 7,
) -> dict | None:
    """ワンタイム招待トークンを生成して返す。"""
    client = _get_client()
    if not client:
        return None
    token = secrets.token_urlsafe(32)
    from datetime import datetime, timedelta, timezone

    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()
    try:
        row = {
            "company_id": company_id,
            "token": token,
            "created_by": created_by or None,
            "role": role,
            "expires_at": expires_at,
        }
        r = client.table("invite_tokens").insert(row).execute()
        rows = r.data or []
        if rows:
            return {"token": token, "expires_at": expires_at}
        return None
    except Exception:
        return None


def consume_invite_token(token: str, user_id: str = "") -> dict | None:
    """招待トークンを消費してユーザーを会社に追加する。成功時は会社 dict を返す。"""
    client = _get_client()
    if not client:
        return None
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        r = (
            client.table("invite_tokens")
            .select("*")
            .eq("token", token)
            .is_("consumed_at", "null")
            .gt("expires_at", now)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if not rows:
            return None
        invite = rows[0]
        # トークンを消費済みにする
        client.table("invite_tokens").update(
            {"consumed_at": now, "consumed_by": user_id or None}
        ).eq("id", invite["id"]).execute()
        # ユーザーを会社に追加
        if user_id and user_id != "anonymous":
            add_user_to_company(user_id, invite["company_id"], invite.get("role", "member"))
        return get_company_by_id(invite["company_id"])
    except Exception:
        return None


def get_company_members(company_id: str) -> list:
    """会社のメンバー一覧を返す（メールアドレス付き）。"""
    client = _get_client()
    if not client:
        return []
    try:
        r = (
            client.table("user_companies")
            .select("user_id, role, created_at")
            .eq("company_id", company_id)
            .order("created_at")
            .execute()
        )
        members = list(r.data) if r.data else []
        for m in members:
            try:
                user_resp = client.auth.admin.get_user_by_id(m["user_id"])
                m["email"] = user_resp.user.email or ""
            except Exception:
                m["email"] = ""
        return members
    except Exception:
        return []


def remove_company_member(company_id: str, user_id: str) -> bool:
    """会社からメンバーを削除する。"""
    client = _get_client()
    if not client:
        return False
    try:
        client.table("user_companies").delete().eq(
            "company_id", company_id
        ).eq("user_id", user_id).execute()
        return True
    except Exception:
        return False
