"""SOC2準備: 監査ログミドルウェア + ヘルパー関数。

使用方法:
    # エンドポイント内での直接呼び出し
    await log_audit(
        db=get_service_client(),
        company_id=user.company_id,
        actor_user_id=user.sub,
        actor_role=user.role,
        action="update",
        resource_type="knowledge_item",
        resource_id=str(item_id),
        old_values={"title": "旧タイトル"},
        new_values={"title": "新タイトル"},
        request=request,
    )

    # FastAPIアプリへのミドルウェア追加
    app.add_middleware(AuditLogMiddleware)
"""
import logging
from typing import Any, Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from db.supabase import get_service_client

logger = logging.getLogger(__name__)


def _extract_ip(request: Request) -> Optional[str]:
    """リクエストからIPアドレスを取得する。
    X-Forwarded-For（リバースプロキシ経由）を優先する。
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # カンマ区切りの場合は最初のIP（実クライアント）を使用
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return None


async def log_audit(
    *,
    company_id: str,
    actor_user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    old_values: Optional[dict[str, Any]] = None,
    new_values: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
    metadata: Optional[dict[str, Any]] = None,
    db=None,
) -> None:
    """監査ログをaudit_logsテーブルに書き込む。

    fire-and-forget安全設計: 例外が発生しても呼び出し元の処理を中断しない。

    Args:
        company_id:     テナントID（RLS対象）
        actor_user_id:  操作者のユーザーID
        actor_role:     操作者のロール（admin/editor）
        action:         操作種別（create/read/update/delete/login/logout/export/approve/reject）
        resource_type:  リソース種別（pipeline/knowledge_item/execution_log/billing/user/connector）
        resource_id:    対象リソースのID
        old_values:     変更前の値（update/delete時）
        new_values:     変更後の値（create/update時）
        request:        FastAPIリクエストオブジェクト（ip_address/user_agentの自動抽出に使用）
        metadata:       追加メタデータ（任意）
        db:             Supabaseクライアント（省略時はservice clientを使用）
    """
    try:
        _db = db or get_service_client()

        entry: dict[str, Any] = {
            "company_id": company_id,
            "action": action,
            "resource_type": resource_type,
        }
        if actor_user_id is not None:
            entry["actor_user_id"] = actor_user_id
        if actor_role is not None:
            entry["actor_role"] = actor_role
        if resource_id is not None:
            entry["resource_id"] = resource_id
        if old_values is not None:
            entry["old_values"] = old_values
        if new_values is not None:
            entry["new_values"] = new_values
        if metadata is not None:
            entry["metadata"] = metadata

        # リクエストオブジェクトからip_address/user_agentを自動抽出
        if request is not None:
            ip = _extract_ip(request)
            if ip:
                entry["ip_address"] = ip
            ua = request.headers.get("User-Agent")
            if ua:
                entry["user_agent"] = ua

        _db.table("audit_logs").insert(entry).execute()
        logger.debug(
            "audit: action=%s resource_type=%s resource_id=%s actor=%s company=%s",
            action, resource_type, resource_id, actor_user_id, company_id,
        )
    except Exception:
        # 監査ログの書き込み失敗はメイン処理を止めない（fire-and-forget）
        logger.exception(
            "監査ログ書き込み失敗: action=%s resource_type=%s resource_id=%s",
            action, resource_type, resource_id,
        )


class AuditLogMiddleware(BaseHTTPMiddleware):
    """FastAPIミドルウェア: リクエストのip_address/user_agentをコンテキストに付与する。

    エンドポイント側でlog_audit()を呼ぶ際にrequest引数を渡せば
    このミドルウェアがなくても同等の情報が記録される。
    本ミドルウェアはリクエストオブジェクトを渡せない箇所（サービス層等）向けの
    コンテキスト補完用として追加する。

    使用例:
        app.add_middleware(AuditLogMiddleware)
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # リクエストコンテキストをstateに保存（エンドポイント内で参照可能）
        request.state.audit_ip = _extract_ip(request)
        request.state.audit_user_agent = request.headers.get("User-Agent", "")

        response = await call_next(request)
        return response
