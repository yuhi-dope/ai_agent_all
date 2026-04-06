"""CloudSign API連携 — 電子契約の作成・送信・署名検知"""

from __future__ import annotations

import base64

import httpx

from config import settings

BASE_URL = "https://api.cloudsign.jp/v1"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.cloudsign_token}",
        "Content-Type": "application/json",
    }


async def create_document(title: str, pdf_content: bytes) -> str:
    """CloudSignにドキュメントを作成"""
    async with httpx.AsyncClient(timeout=30) as client:
        # ドキュメント作成
        resp = await client.post(
            f"{BASE_URL}/documents",
            headers=_headers(),
            json={"title": title},
        )
        resp.raise_for_status()
        doc_id = resp.json()["id"]

        # PDF添付
        await client.post(
            f"{BASE_URL}/documents/{doc_id}/files",
            headers={"Authorization": f"Bearer {settings.cloudsign_token}"},
            files={"file": ("contract.pdf", pdf_content, "application/pdf")},
        )

    return doc_id


async def send_for_signature(document_id: str, recipient_email: str, recipient_name: str, organization: str) -> None:
    """署名依頼を送信"""
    async with httpx.AsyncClient(timeout=30) as client:
        # 参加者追加
        await client.post(
            f"{BASE_URL}/documents/{document_id}/participants",
            headers=_headers(),
            json={
                "email": recipient_email,
                "name": recipient_name,
                "organization": organization,
            },
        )
        # 送信
        await client.post(
            f"{BASE_URL}/documents/{document_id}/send",
            headers=_headers(),
        )


async def get_document_status(document_id: str) -> str:
    """ドキュメントステータス確認"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BASE_URL}/documents/{document_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json().get("status", "unknown")


async def download_signed_pdf(document_id: str) -> bytes:
    """署名済みPDFダウンロード"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{BASE_URL}/documents/{document_id}/pdf", headers=_headers())
        resp.raise_for_status()
        return resp.content


async def handle_webhook(payload: dict) -> None:
    """CloudSign Webhook受信処理"""
    event = payload.get("event", "")
    document_id = payload.get("document_id", "")

    if event == "document.completed":
        # 署名完了 → 決済セットアップへ
        from contract.stripe_billing import setup_payment_after_signing
        await setup_payment_after_signing(document_id)

        # TODO: apo_contracts の status を 'signed' に更新
        # TODO: apo_contract_events にイベント記録
        # TODO: 署名済みPDFを双方にメール送信
