"""グローバル例外ハンドラ — 本番環境で内部エラー情報を隠蔽する。

Usage in main.py:
    from security.error_handler import install_error_handlers
    install_error_handlers(app)
"""
import logging
import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")


def install_error_handlers(app: FastAPI) -> None:
    """FastAPIアプリに安全なエラーハンドラをインストールする"""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """HTTPExceptionの処理 — 本番ではdetailをサニタイズ"""
        if ENVIRONMENT in ("production", "staging") and exc.status_code >= 500:
            logger.error(f"HTTP {exc.status_code}: {exc.detail} (path={request.url.path})")
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": "内部エラーが発生しました。管理者にお問い合わせください。"},
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """未処理例外 — 本番では内部情報を一切返さない"""
        logger.error(f"Unhandled exception: {exc} (path={request.url.path})", exc_info=True)
        if ENVIRONMENT in ("production", "staging"):
            return JSONResponse(
                status_code=500,
                content={"detail": "内部エラーが発生しました。管理者にお問い合わせください。"},
            )
        # 開発環境ではデバッグ用にエラー情報を返す
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )
