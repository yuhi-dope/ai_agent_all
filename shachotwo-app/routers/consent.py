"""同意管理エンドポイント — 個人情報保護法・GDPR 対応 + 電子同意フロー。

Endpoints:
    POST   /consent/grant                — 同意付与
    POST   /consent/revoke               — 同意取り消し
    GET    /consent/status               — 自分の同意状況一覧
    DELETE /consent/all-my-data          — 全データ削除（忘れられる権利）
    GET    /consent/{token}              — 電子同意画面表示（認証不要、トークン認証）
    POST   /consent/{token}/agree        — 電子同意実行（IPアドレス・UA記録）
    GET    /consent/{token}/document     — 同意対象ドキュメントPDFプレビュー
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth.jwt import JWTClaims
from auth.middleware import get_current_user
from db.supabase import get_service_client
from security.consent import (
    CONSENT_TYPES,
    ConsentRecord,
    check_consent,
    delete_all_user_data,
    get_user_consents,
    grant_consent,
    revoke_consent,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# --------------------------------------------------------------------------- #
# Request / Response models
# --------------------------------------------------------------------------- #


class GrantConsentRequest(BaseModel):
    consent_type: str = Field(description=f"有効な値: {CONSENT_TYPES}")
    version: str = Field(default="1.0", description="同意ポリシーのバージョン")
    expires_at: Optional[datetime] = Field(default=None, description="同意の有効期限（省略可）")


class RevokeConsentRequest(BaseModel):
    consent_type: str = Field(description=f"有効な値: {CONSENT_TYPES}")


class ConsentStatusResponse(BaseModel):
    consents: list[ConsentRecord]
    active_types: list[str]


class GrantConsentResponse(BaseModel):
    record: ConsentRecord
    message: str


class RevokeConsentResponse(BaseModel):
    revoked: bool
    consent_type: str
    message: str


class DeleteAllDataResponse(BaseModel):
    deleted_sessions: int
    revoked_consents: int
    message: str


# --------------------------------------------------------------------------- #
# 電子同意フロー Request / Response models
# --------------------------------------------------------------------------- #


class ConsentTokenInfoResponse(BaseModel):
    """同意画面表示用レスポンス。"""
    token: str
    contract_id: str
    contract_title: str
    contract_data: dict[str, Any]
    company_name: str
    to_email: str
    status: str
    created_at: str
    is_expired: bool


class ConsentAgreeRequest(BaseModel):
    """同意実行リクエスト。"""
    full_name: str = Field(description="同意者氏名")
    agreed: bool = Field(description="同意チェック（Trueのみ受付）")


class ConsentAgreeResponse(BaseModel):
    """同意実行レスポンス。"""
    success: bool
    consent_record_id: str
    contract_id: str
    signed_at: str
    message: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.post(
    "/consent/grant",
    response_model=GrantConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="同意付与",
)
async def api_grant_consent(
    body: GrantConsentRequest,
    request: Request,
    user: JWTClaims = Depends(get_current_user),
) -> GrantConsentResponse:
    """ユーザーが特定のデータ処理に同意することを記録する。"""
    if body.consent_type not in CONSENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_CONSENT_TYPE",
                    "message": f"無効な consent_type: {body.consent_type!r}",
                    "valid_types": CONSENT_TYPES,
                }
            },
        )

    ip_address: Optional[str] = None
    if request.client:
        ip_address = request.client.host
    user_agent: Optional[str] = request.headers.get("user-agent")

    record = await grant_consent(
        company_id=user.company_id,
        user_id=user.sub,
        consent_type=body.consent_type,
        version=body.version,
        ip_address=ip_address,
        user_agent=user_agent,
        expires_at=body.expires_at,
    )

    return GrantConsentResponse(
        record=record,
        message=f"{body.consent_type} への同意を記録しました",
    )


@router.post(
    "/consent/revoke",
    response_model=RevokeConsentResponse,
    summary="同意取り消し",
)
async def api_revoke_consent(
    body: RevokeConsentRequest,
    user: JWTClaims = Depends(get_current_user),
) -> RevokeConsentResponse:
    """ユーザーが特定のデータ処理への同意を取り消す。"""
    if body.consent_type not in CONSENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_CONSENT_TYPE",
                    "message": f"無効な consent_type: {body.consent_type!r}",
                    "valid_types": CONSENT_TYPES,
                }
            },
        )

    revoked = await revoke_consent(
        company_id=user.company_id,
        user_id=user.sub,
        consent_type=body.consent_type,
    )

    if not revoked:
        return RevokeConsentResponse(
            revoked=False,
            consent_type=body.consent_type,
            message=f"{body.consent_type} の有効な同意が見つかりませんでした",
        )

    return RevokeConsentResponse(
        revoked=True,
        consent_type=body.consent_type,
        message=f"{body.consent_type} への同意を取り消しました",
    )


@router.get(
    "/consent/status",
    response_model=ConsentStatusResponse,
    summary="同意状況一覧",
)
async def api_consent_status(
    user: JWTClaims = Depends(get_current_user),
) -> ConsentStatusResponse:
    """ログインユーザー自身の全同意記録と現在有効な同意種別一覧を返す。"""
    consents = await get_user_consents(
        company_id=user.company_id,
        user_id=user.sub,
    )
    active_types = [c.consent_type for c in consents if c.is_active]
    # 重複排除しつつ順序を保つ
    seen: set[str] = set()
    unique_active_types: list[str] = []
    for ct in active_types:
        if ct not in seen:
            seen.add(ct)
            unique_active_types.append(ct)

    return ConsentStatusResponse(
        consents=consents,
        active_types=unique_active_types,
    )


@router.delete(
    "/consent/all-my-data",
    response_model=DeleteAllDataResponse,
    summary="全データ削除（忘れられる権利）",
)
async def api_delete_all_my_data(
    user: JWTClaims = Depends(get_current_user),
) -> DeleteAllDataResponse:
    """個人情報保護法・GDPR の「忘れられる権利」に基づき、ユーザーの全データを削除する。

    削除されるデータ:
    - ユーザーが起動した knowledge_sessions
    - ユーザーの同意記録（論理削除）
    """
    result = await delete_all_user_data(
        company_id=user.company_id,
        user_id=user.sub,
    )

    return DeleteAllDataResponse(
        deleted_sessions=result["deleted_sessions"],
        revoked_consents=result["revoked_consents"],
        message=(
            f"データ削除が完了しました。"
            f"セッション {result['deleted_sessions']} 件を削除、"
            f"同意 {result['revoked_consents']} 件を取り消しました。"
        ),
    )


# --------------------------------------------------------------------------- #
# 電子同意フロー Endpoints（認証不要 — トークンで認証）
# --------------------------------------------------------------------------- #


async def _get_consent_token_record(token: str) -> dict[str, Any]:
    """consent_tokens テーブルからトークンレコードを取得する。

    見つからない場合は HTTPException 404 を送出する。
    """
    db = get_service_client()
    result = db.table("consent_tokens").select("*").eq("token", token).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="同意リンクが見つかりません。URLをご確認ください。",
        )
    return result.data[0]


def _check_token_expired(record: dict[str, Any]) -> bool:
    """トークンが期限切れかどうかを判定する。"""
    expires_at = record.get("expires_at")
    if expires_at is None:
        return False
    if isinstance(expires_at, str):
        expires_at_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    else:
        expires_at_dt = expires_at
    if expires_at_dt.tzinfo is None:
        expires_at_dt = expires_at_dt.replace(tzinfo=timezone.utc)
    return expires_at_dt <= datetime.now(timezone.utc)


@router.get(
    "/consent/{token}",
    response_model=ConsentTokenInfoResponse,
    summary="電子同意画面表示",
)
async def api_get_consent_info(token: str) -> ConsentTokenInfoResponse:
    """同意トークンから同意画面表示用の情報を返す。

    認証不要 — トークンの一意性で認証する。
    外部の顧客がこのURLを受け取り、同意内容を確認する画面で使用する。
    """
    record = await _get_consent_token_record(token)
    is_expired = _check_token_expired(record)

    # contract_data から表示情報を取得
    contract_data = record.get("contract_data", {})
    if isinstance(contract_data, str):
        import json
        try:
            contract_data = json.loads(contract_data)
        except (json.JSONDecodeError, TypeError):
            contract_data = {}

    # 会社名を取得
    company_name = ""
    try:
        db = get_service_client()
        company_result = db.table("companies").select("name").eq(
            "id", record["company_id"]
        ).execute()
        if company_result.data:
            company_name = company_result.data[0].get("name", "")
    except Exception:
        logger.warning("会社名の取得に失敗")

    return ConsentTokenInfoResponse(
        token=token,
        contract_id=record.get("contract_id", ""),
        contract_title=contract_data.get("contract_title", "契約書"),
        contract_data=contract_data,
        company_name=company_name,
        to_email=record.get("to_email", ""),
        status=record.get("status", "pending"),
        created_at=record.get("created_at", ""),
        is_expired=is_expired,
    )


@router.post(
    "/consent/{token}/agree",
    response_model=ConsentAgreeResponse,
    summary="電子同意実行",
)
async def api_agree_consent(
    token: str,
    body: ConsentAgreeRequest,
    request: Request,
) -> ConsentAgreeResponse:
    """同意ボタン押下時に呼ばれる。IPアドレス・User-Agent・タイムスタンプを記録する。

    認証不要 — トークンの一意性で認証する。
    """
    # バリデーション: agreed が True でなければ拒否
    if not body.agreed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="同意チェックをオンにしてください。",
        )

    # トークン検証
    record = await _get_consent_token_record(token)

    # 期限切れチェック
    if _check_token_expired(record):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="この同意リンクは有効期限が切れています。再発行をご依頼ください。",
        )

    # 既に同意済みかチェック
    if record.get("status") == "agreed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="この契約は既に同意済みです。",
        )

    # IPアドレス・User-Agent を取得
    ip_address: str = ""
    if request.client:
        ip_address = request.client.host
    user_agent: str = request.headers.get("user-agent", "")

    # contract_data を取得
    contract_data = record.get("contract_data", {})
    if isinstance(contract_data, str):
        import json
        try:
            contract_data = json.loads(contract_data)
        except (json.JSONDecodeError, TypeError):
            contract_data = {}

    # consent_flow の Step 4-7 を実行
    from workers.bpo.sales.sfa.consent_flow import process_consent_agreement

    # user_id はトークンレコードに紐づくメールアドレスから特定
    # 外部顧客の場合は to_email をユーザー識別子として使用
    user_id = record.get("agreed_by_user_id", record.get("to_email", "external"))

    flow_result = await process_consent_agreement(
        company_id=record["company_id"],
        contract_id=record.get("contract_id", ""),
        consent_token=token,
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        contract_data=contract_data,
    )

    if not flow_result.success:
        logger.error(
            f"電子同意フロー失敗: token={token}, "
            f"failed_step={flow_result.failed_step}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="同意処理に失敗しました。しばらく経ってから再度お試しください。",
        )

    return ConsentAgreeResponse(
        success=True,
        consent_record_id=flow_result.final_output.get("consent_record_id", ""),
        contract_id=flow_result.final_output.get("contract_id", ""),
        signed_at=flow_result.final_output.get("signed_at", ""),
        message="同意が完了しました。同意済みの書類はメールでもお送りします。",
    )


@router.get(
    "/consent/{token}/document",
    summary="同意対象ドキュメントPDFプレビュー",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "契約書PDF",
        }
    },
)
async def api_get_consent_document(token: str) -> Response:
    """同意対象の契約書PDFを返す。

    認証不要 — トークンの一意性で認証する。
    ブラウザでのPDFプレビュー表示、またはダウンロードに使用する。
    """
    record = await _get_consent_token_record(token)

    # contract_data を取得してPDFを生成
    contract_data = record.get("contract_data", {})
    if isinstance(contract_data, str):
        import json
        try:
            contract_data = json.loads(contract_data)
        except (json.JSONDecodeError, TypeError):
            contract_data = {}

    company_id = record["company_id"]

    # PDFを生成
    from workers.bpo.sales.sfa.consent_flow import _generate_contract_pdf

    try:
        pdf_result = await _generate_contract_pdf(company_id, contract_data)
        pdf_bytes = pdf_result["pdf_bytes"]
        filename = pdf_result.get("filename", "document.pdf")
    except Exception as exc:
        logger.error(f"同意対象ドキュメントPDF生成失敗: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="書類の取得に失敗しました。しばらく経ってから再度お試しください。",
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )
