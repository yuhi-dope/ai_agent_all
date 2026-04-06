"""SOC2準備: セキュリティヘッダーミドルウェア。

全レスポンスに以下のHTTPセキュリティヘッダーを付与する:
- X-Content-Type-Options: スニッフィング防止
- X-Frame-Options: クリックジャッキング防止
- X-XSS-Protection: 古いブラウザ向けXSS防止（モダンブラウザはCSPが主体）
- Strict-Transport-Security: HTTPS強制（HSTS）
- Content-Security-Policy: XSS・インジェクション攻撃の緩和

使用例:
    app.add_middleware(SecurityHeadersMiddleware)
"""
import os
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# 環境判定（開発環境ではHSTS等の一部ヘッダーを緩める）
_ENV = os.environ.get("ENVIRONMENT", "development")
_IS_PRODUCTION = _ENV in ("production", "staging")

# Content-Security-Policy（本番用）
# 'nonce-{nonce}' が必要な場合はエンドポイント側で動的生成すること
_CSP_PRODUCTION = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "  # Next.jsのインラインスクリプト対応
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://*.supabase.co https://*.supabase.in; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self';"
)

# Content-Security-Policy（開発用: 緩めの設定）
_CSP_DEVELOPMENT = "default-src 'self' 'unsafe-inline' 'unsafe-eval' *;"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """全レスポンスにセキュリティヘッダーを付与するミドルウェア。

    SOC2 CC6.1（論理アクセス制御）・CC6.6（悪意ある行為の防止）の要件に対応。
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # スニッフィング防止（MIME型の推測を禁止）
        response.headers["X-Content-Type-Options"] = "nosniff"

        # クリックジャッキング防止（iframeへの埋め込みを禁止）
        response.headers["X-Frame-Options"] = "DENY"

        # XSS防止（レガシーブラウザ向け）
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # HSTS（本番・ステージング環境のみ）
        # 開発環境でHTTPSを使用しない場合に誤って適用されるのを防ぐ
        if _IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # CSP（環境に応じて切り替え）
        response.headers["Content-Security-Policy"] = (
            _CSP_PRODUCTION if _IS_PRODUCTION else _CSP_DEVELOPMENT
        )

        # 参照元情報の送信制限
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # ブラウザの機能ポリシー（カメラ・マイク・位置情報等へのアクセスを制限）
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        return response
