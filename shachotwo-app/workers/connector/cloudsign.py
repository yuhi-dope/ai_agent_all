"""CloudSignConnector — CloudSign API v2 電子契約コネクタ。"""
import logging
from typing import Any

import httpx

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cloudsign.jp/v2"


class CloudSignConnector(BaseConnector):
    """CloudSign REST API v2 コネクタ。

    credentials:
        api_token (str): CloudSign API トークン

    主要機能:
        - 電子契約ドキュメントの作成・送信
        - 署名ステータス確認
        - 署名済みPDFダウンロード
        - Webhook受信処理
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    @property
    def _token(self) -> str:
        return self.config.credentials.get("api_token", "")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # ── BaseConnector 抽象メソッド ──────────────────────

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """ドキュメント一覧を取得する。

        Args:
            resource: "documents" (固定)
            filters:  status, page, per_page 等

        Returns:
            ドキュメントの list
        """
        params: dict[str, Any] = {}
        if "status" in filters:
            params["status"] = filters["status"]
        if "page" in filters:
            params["page"] = filters["page"]
        if "per_page" in filters:
            params["per_page"] = filters["per_page"]

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{BASE_URL}/{resource}",
                params=params,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json().get("documents", [])

    async def write_record(self, resource: str, data: dict) -> dict:
        """ドキュメントを作成する。

        Args:
            resource: "documents" (固定)
            data:     title 等のドキュメント情報

        Returns:
            CloudSign API レスポンス dict
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BASE_URL}/{resource}",
                json=data,
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def health_check(self) -> bool:
        """CloudSign API への疎通確認。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{BASE_URL}/documents",
                    params={"per_page": 1},
                    headers=self._headers,
                )
                return resp.status_code < 500
        except Exception:
            return False

    # ── CloudSign 固有メソッド ──────────────────────────

    async def create_document(self, title: str, pdf_content: bytes) -> str:
        """CloudSign にドキュメントを作成し PDF を添付する。

        Args:
            title:       ドキュメントタイトル
            pdf_content: PDF バイナリ

        Returns:
            作成されたドキュメント ID
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # ドキュメント作成
            resp = await client.post(
                f"{BASE_URL}/documents",
                headers=self._headers,
                json={"title": title},
            )
            resp.raise_for_status()
            doc_id: str = resp.json()["id"]

            # PDF 添付
            await client.post(
                f"{BASE_URL}/documents/{doc_id}/files",
                headers={"Authorization": f"Bearer {self._token}"},
                files={"file": ("contract.pdf", pdf_content, "application/pdf")},
            )

        logger.info(f"CloudSign document created: {doc_id}")
        return doc_id

    async def send_for_signature(
        self,
        document_id: str,
        recipient_email: str,
        recipient_name: str,
        organization: str,
    ) -> None:
        """署名依頼を送信する。

        Args:
            document_id:     ドキュメント ID
            recipient_email: 署名者メールアドレス
            recipient_name:  署名者名
            organization:    署名者組織名
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 参加者追加
            await client.post(
                f"{BASE_URL}/documents/{document_id}/participants",
                headers=self._headers,
                json={
                    "email": recipient_email,
                    "name": recipient_name,
                    "organization": organization,
                },
            )
            # 送信
            resp = await client.post(
                f"{BASE_URL}/documents/{document_id}/send",
                headers=self._headers,
            )
            resp.raise_for_status()

        logger.info(f"CloudSign signature request sent: {document_id}")

    async def check_status(self, document_id: str) -> str:
        """ドキュメントの署名ステータスを確認する。

        Returns:
            "draft" | "waiting" | "completed" | "rejected" | "unknown"
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{BASE_URL}/documents/{document_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json().get("status", "unknown")

    async def download_signed(self, document_id: str) -> bytes:
        """署名済み PDF をダウンロードする。

        Returns:
            PDF バイナリ
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{BASE_URL}/documents/{document_id}/pdf",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.content

    async def handle_webhook(self, payload: dict) -> dict[str, Any]:
        """CloudSign Webhook を受信して処理する。

        Args:
            payload: Webhook ペイロード

        Returns:
            処理結果 dict (event, document_id, status)
        """
        event = payload.get("event", "")
        document_id = payload.get("document_id", "")

        result: dict[str, Any] = {
            "event": event,
            "document_id": document_id,
            "status": "processed",
        }

        if event == "document.completed":
            result["action"] = "signing_completed"
            logger.info(f"CloudSign signing completed: {document_id}")
        elif event == "document.rejected":
            result["action"] = "signing_rejected"
            logger.warning(f"CloudSign signing rejected: {document_id}")
        else:
            result["action"] = "unknown_event"
            logger.debug(f"CloudSign webhook event: {event}")

        return result
