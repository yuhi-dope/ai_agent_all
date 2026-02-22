# syntax=docker/dockerfile:1
# =============================================================================
# Develop Agent System - Dockerfile
# ターゲット環境: Google Cloud Run (Python 3.11)
# =============================================================================

FROM python:3.11-slim AS base

# セキュリティ: 非 root ユーザーを作成
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /bin/bash appuser

WORKDIR /app

# 依存をレイヤーキャッシュするため requirements を先にコピー
COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# アプリコードをコピー（.dockerignore で不要なファイルは除外済み）
COPY . .

# ファイル所有権を非 root ユーザーに変更
RUN chown -R appuser:appgroup /app

USER appuser

# Cloud Run は $PORT 環境変数でポートを指定する（デフォルト: 8080）
ENV PORT=8080

EXPOSE 8080

# ヘルスチェック: /health エンドポイントを使用
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

# Cloud Run: 1インスタンス1プロセス（workers=1）推奨
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
