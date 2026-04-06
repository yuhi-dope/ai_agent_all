"""DB→GWS逆同期エンジン。パイプライン結果をGoogle Workspaceに反映する。

同期先:
  - Google Sheets: 営業リストステータス更新、CRMサマリー
  - Google Drive: 提案書PDF、契約書、議事録を自動保管
  - Google Calendar: 商談イベントに資料リンク追加
  - Gmail下書き: フォローアップメール下書き生成

冪等性: gws_sync_state テーブルで sync_id（execution_log_id + sync_type）を管理。

クレデンシャル解決順序（ヘルパー関数 `_resolve_credentials` 参照）:
  1. tool_connections テーブル（company_id + tool_name で検索）
  2. 環境変数 GOOGLE_CREDENTIALS_PATH
  3. 上記いずれもなければ RuntimeError を送出（サイレント失敗しない）

リトライ方针:
  - pending / (failed かつ retry_count < MAX_RETRY_COUNT) のレコードを再実行
  - 成功: status="synced", synced_at=now()
  - 失敗(retry_count < MAX_RETRY_COUNT): retry_count += 1, status="failed", next_retry_at 更新
  - 失敗(retry_count >= MAX_RETRY_COUNT): status="permanently_failed"
  - 指数バックオフ: retry_count=1 → 5分, retry_count=2 → 15分
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# -- リトライ設定 -----------------------------------------------
MAX_RETRY_COUNT = 3

# 指数バックオフの待ち時間（分）: retry_count 後に次回実行するまでの待ち時間
_RETRY_BACKOFF_MINUTES: dict[int, int] = {
    1: 5,
    2: 15,
}

# sync_type -> ハンドラ関数名のマッピング
# gws_sync_state.sync_type に格納する値はこのキーに合わせる
_SYNC_TYPE_HANDLERS: dict[str, str] = {
    "proposal_to_drive": "_sync_proposal_to_drive",
    "contract_to_drive": "_sync_contract_to_drive",
    "outreach_to_sheets": "_sync_outreach_to_sheets",
    "lead_to_sheets": "_sync_lead_to_sheets",
    "support_draft": "_sync_support_draft",
    "followup_draft": "_sync_followup_draft",
}


# ---------------------------------------------------------------------------
# クレデンシャル解決
# ---------------------------------------------------------------------------


async def _resolve_credentials(company_id: str, tool_name: str) -> dict:
    """カンパニーID + tool_name に対応するクレデンシャル dict を返す。

    解決順序:
      1. tool_connections テーブルの connection_config
      2. 環境変数 GOOGLE_CREDENTIALS_PATH
      3. どちらもなければ RuntimeError

    Returns:
        ConnectorConfig.credentials に渡せる dict
        （少なくとも "credentials_path" または "service_account_info" を含む）
    """
    # 1. DB から取得
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = db.table("tool_connections").select("connection_config").eq(
            "company_id", company_id
        ).eq("tool_name", tool_name).eq("status", "active").limit(1).execute()

        if result.data:
            config = result.data[0].get("connection_config") or {}
            if config:
                logger.debug(
                    "_resolve_credentials: DB hit tool=%s company=%s",
                    tool_name, company_id[:8],
                )
                return config
    except Exception as e:
        logger.warning(
            "_resolve_credentials: DB lookup failed tool=%s: %s", tool_name, e
        )

    # 2. 環境変数
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "")
    if creds_path:
        logger.debug(
            "_resolve_credentials: env fallback tool=%s path=%s", tool_name, creds_path
        )
        return {"credentials_path": creds_path}

    # 3. 解決不能
    raise RuntimeError(
        f"クレデンシャルが見つかりません: tool={tool_name}, company_id={company_id[:8]}. "
        "tool_connections テーブルへの登録 または GOOGLE_CREDENTIALS_PATH 環境変数の設定が必要です。"
    )


# ---------------------------------------------------------------------------
# メインディスパッチャ
# ---------------------------------------------------------------------------


async def sync_pipeline_result(
    company_id: str,
    pipeline_name: str,
    result: Any,
) -> dict[str, list[str]]:
    """パイプライン実行結果を Google Workspace に反映する。

    chain.py から非同期で呼び出される。
    同期失敗時は gws_sync_state に pending レコードを INSERT し、
    run_pending_syncs() によるリトライ対象とする。

    Args:
        company_id: テナントID
        pipeline_name: 完了したパイプライン名
        result: パイプラインの戻り値

    Returns:
        {"synced": [成功した同期タイプのリスト]}
    """
    synced: list[str] = []
    output = _extract_output(result)

    try:
        # 提案書生成 -> Drive に PDF 保存 + Calendar に資料リンク
        if pipeline_name == "proposal_generation_pipeline":
            ok = await _sync_proposal_to_drive(company_id, output)
            if ok:
                synced.append("drive:proposal")
            else:
                await _enqueue_pending_sync(
                    company_id, "proposal_to_drive", pipeline_name, output
                )
            if await _sync_proposal_link_to_calendar(company_id, output):
                synced.append("calendar:proposal_link")

        # 見積・契約 -> Drive に契約書保存
        elif pipeline_name == "quotation_contract_pipeline":
            ok = await _sync_contract_to_drive(company_id, output)
            if ok:
                synced.append("drive:contract")
            else:
                await _enqueue_pending_sync(
                    company_id, "contract_to_drive", pipeline_name, output
                )

        # アウトリーチ -> Sheets の送信ステータス更新
        elif pipeline_name == "outreach_pipeline":
            ok = await _sync_outreach_to_sheets(company_id, output)
            if ok:
                synced.append("sheets:outreach_status")
            else:
                await _enqueue_pending_sync(
                    company_id, "outreach_to_sheets", pipeline_name, output
                )

        # リード判定 -> Sheets のリードステータス更新
        elif pipeline_name == "lead_qualification_pipeline":
            ok = await _sync_lead_to_sheets(company_id, output)
            if ok:
                synced.append("sheets:lead_status")
            else:
                await _enqueue_pending_sync(
                    company_id, "lead_to_sheets", pipeline_name, output
                )

        # サポート自動応答 -> Gmail 下書き生成
        elif pipeline_name == "support_auto_response_pipeline":
            ok = await _sync_support_draft(company_id, output)
            if ok:
                synced.append("gmail:support_draft")
            else:
                await _enqueue_pending_sync(
                    company_id, "support_draft", pipeline_name, output
                )

        # 顧客ライフサイクル(followup) -> Gmail フォローアップ下書き
        elif pipeline_name == "customer_lifecycle_pipeline":
            mode = output.get("mode", "")
            if mode == "followup":
                ok = await _sync_followup_draft(company_id, output)
                if ok:
                    synced.append("gmail:followup_draft")
                else:
                    await _enqueue_pending_sync(
                        company_id, "followup_draft", pipeline_name, output
                    )

    except Exception as e:
        logger.error("sync_engine: dispatch error pipeline=%s: %s", pipeline_name, e)

    if synced:
        logger.info("sync_engine: %s -> %s", pipeline_name, synced)

    return {"synced": synced}


# ---------------------------------------------------------------------------
# リトライバッチ実行
# ---------------------------------------------------------------------------


async def run_pending_syncs(company_id: str) -> int:
    """gws_sync_state テーブルの未同期・失敗レコードを一括リトライする。

    schedule_watcher から毎時（"0 * * * *"）に呼び出される。

    対象レコード:
      - status="pending"
      - status="failed" かつ retry_count < MAX_RETRY_COUNT かつ
        (next_retry_at IS NULL または next_retry_at <= now())

    成功時: status="synced", synced_at=now()
    失敗時:
      - retry_count + 1 < MAX_RETRY_COUNT -> status="failed", next_retry_at=now()+バックオフ
      - retry_count + 1 >= MAX_RETRY_COUNT -> status="permanently_failed"

    Returns:
        成功した件数
    """
    from db.supabase import get_service_client
    db = get_service_client()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # pending レコードを取得
    pending_res = db.table("gws_sync_state").select("*").eq(
        "company_id", company_id
    ).eq("status", "pending").limit(50).execute()

    # failed かつリトライ可能レコードを取得
    failed_res = db.table("gws_sync_state").select("*").eq(
        "company_id", company_id
    ).eq("status", "failed").lt("retry_count", MAX_RETRY_COUNT).limit(50).execute()

    # next_retry_at フィルタ: Python 側で判定（Supabase の OR/IS NULL 構文を避ける）
    pending_records: list[dict] = list(pending_res.data or [])
    failed_records: list[dict] = [
        r for r in (failed_res.data or [])
        if _is_retry_due(r, now)
    ]

    all_records = pending_records + failed_records
    if not all_records:
        logger.info("run_pending_syncs: no records to process for company=%s", company_id[:8])
        return 0

    logger.info(
        "run_pending_syncs: %d pending + %d failed = %d total for company=%s",
        len(pending_records), len(failed_records), len(all_records), company_id[:8],
    )

    success_count = 0
    import sys
    current_module = sys.modules[__name__]

    for record in all_records:
        record_id = record.get("id")
        sync_type = record.get("sync_type", "")
        retry_count: int = int(record.get("retry_count") or 0)
        payload: dict = record.get("payload") or {}

        # ハンドラを解決
        handler_name = _SYNC_TYPE_HANDLERS.get(sync_type)
        if not handler_name:
            logger.warning(
                "run_pending_syncs: unknown sync_type=%s record_id=%s", sync_type, record_id
            )
            db.table("gws_sync_state").update({
                "status": "permanently_failed",
                "error_message": f"未知の sync_type: {sync_type}",
                "updated_at": now_iso,
            }).eq("id", record_id).eq("company_id", company_id).execute()
            continue

        handler = getattr(current_module, handler_name, None)
        if handler is None:
            logger.error(
                "run_pending_syncs: handler not found handler=%s", handler_name
            )
            continue

        # ハンドラを実行
        try:
            ok: bool = await handler(company_id, payload)
        except Exception as e:
            ok = False
            logger.error(
                "run_pending_syncs: handler error handler=%s record_id=%s: %s",
                handler_name, record_id, e,
            )

        if ok:
            # 成功: synced に更新
            db.table("gws_sync_state").update({
                "status": "synced",
                "synced_at": now_iso,
                "updated_at": now_iso,
                "error_message": None,
            }).eq("id", record_id).eq("company_id", company_id).execute()
            success_count += 1
            logger.info(
                "run_pending_syncs: synced record_id=%s sync_type=%s retry_count=%d",
                record_id, sync_type, retry_count,
            )
        else:
            # 失敗: retry_count を更新
            new_retry_count = retry_count + 1
            if new_retry_count >= MAX_RETRY_COUNT:
                db.table("gws_sync_state").update({
                    "status": "permanently_failed",
                    "retry_count": new_retry_count,
                    "updated_at": now_iso,
                }).eq("id", record_id).eq("company_id", company_id).execute()
                logger.warning(
                    "run_pending_syncs: permanently_failed record_id=%s sync_type=%s",
                    record_id, sync_type,
                )
            else:
                backoff_minutes = _RETRY_BACKOFF_MINUTES.get(new_retry_count, 15)
                next_retry_at = (now + timedelta(minutes=backoff_minutes)).isoformat()
                db.table("gws_sync_state").update({
                    "status": "failed",
                    "retry_count": new_retry_count,
                    "next_retry_at": next_retry_at,
                    "updated_at": now_iso,
                }).eq("id", record_id).eq("company_id", company_id).execute()
                logger.info(
                    "run_pending_syncs: retry_count=%d->%d next_retry_at=%s record_id=%s",
                    retry_count, new_retry_count, next_retry_at, record_id,
                )

    logger.info(
        "run_pending_syncs: success=%d / total=%d for company=%s",
        success_count, len(all_records), company_id[:8],
    )
    return success_count


def _is_retry_due(record: dict, now: datetime) -> bool:
    """次回リトライ時刻が現在時刻以前かどうかを判定する。"""
    next_retry_at_str: str | None = record.get("next_retry_at")
    if not next_retry_at_str:
        return True
    try:
        next_retry_at = datetime.fromisoformat(next_retry_at_str.replace("Z", "+00:00"))
        return now >= next_retry_at
    except Exception:
        return True


async def _enqueue_pending_sync(
    company_id: str,
    sync_type: str,
    source_pipeline: str,
    payload: dict,
) -> None:
    """同期失敗時に gws_sync_state へ pending レコードを INSERT する。

    run_pending_syncs() が次回実行時にリトライする。
    payload には再実行に必要な output データを格納する。
    """
    sync_id = _make_sync_id(company_id, sync_type, source_pipeline)
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        db.table("gws_sync_state").insert({
            "company_id": company_id,
            "sync_id": sync_id,
            "sync_type": sync_type,
            "source_pipeline": source_pipeline,
            "status": "pending",
            "retry_count": 0,
            "payload": payload,
            "created_at": now_iso,
            "updated_at": now_iso,
        }).execute()
        logger.info(
            "_enqueue_pending_sync: enqueued sync_type=%s company=%s",
            sync_type, company_id[:8],
        )
    except Exception as e:
        logger.warning("_enqueue_pending_sync failed: %s", e)


# ---------------------------------------------------------------------------
# 個別同期ハンドラ
# ---------------------------------------------------------------------------


async def _sync_proposal_to_drive(company_id: str, output: dict) -> bool:
    """提案書PDFをDriveに保存する。"""
    pdf_b64 = output.get("pdf_b64", "") or output.get("proposal", {}).get("pdf_b64", "")
    company_name = output.get("company_name", "") or output.get("lead", {}).get("company_name", "未設定")
    proposal_id = output.get("proposal_id", "")

    if not pdf_b64:
        return False

    sync_id = _make_sync_id(company_id, "drive:proposal", proposal_id)
    if await _is_already_synced(company_id, sync_id):
        return False

    try:
        connector = await _get_drive_connector(company_id)
        result = await connector.upload_document(
            company_name=company_name,
            subfolder="提案書",
            filename=f"提案書_{company_name}_{_today_str()}.pdf",
            content_b64=pdf_b64,
            mime_type="application/pdf",
        )
        await _record_sync(company_id, sync_id, "drive", "proposal_generation", result.get("id", ""))
        logger.info("sync_engine: proposal uploaded to Drive file_id=%s", result.get("id"))
        return True
    except Exception as e:
        await _record_sync_failure(company_id, sync_id, "drive", "proposal_generation", str(e))
        logger.error("sync_engine: proposal Drive upload failed: %s", e)
        return False


async def _sync_proposal_link_to_calendar(company_id: str, output: dict) -> bool:
    """Calendar の商談イベントに提案書リンクを追加する。"""
    event_id = output.get("calendar_event_id", "")
    drive_link = output.get("drive_link", "") or output.get("webViewLink", "")

    if not event_id or not drive_link:
        return False

    try:
        connector = await _get_calendar_connector(company_id)
        event = await connector.read_records("event", {"event_id": event_id})
        if event:
            current_desc = event[0].get("description", "") or ""
            new_desc = f"{current_desc}\n\n提案書: {drive_link}"
            await connector.write_record("update_event", {
                "event_id": event_id,
                "description": new_desc,
            })
            return True
    except Exception as e:
        logger.warning("sync_engine: calendar update failed: %s", e)
    return False


async def _sync_contract_to_drive(company_id: str, output: dict) -> bool:
    """契約書PDFをDriveに保存する。"""
    pdf_b64 = output.get("contract_pdf_b64", "") or output.get("contract", {}).get("pdf_b64", "")
    company_name = output.get("company_name", "未設定")
    contract_id = output.get("contract_id", "")

    if not pdf_b64:
        return False

    sync_id = _make_sync_id(company_id, "drive:contract", contract_id)
    if await _is_already_synced(company_id, sync_id):
        return False

    try:
        connector = await _get_drive_connector(company_id)
        result = await connector.upload_document(
            company_name=company_name,
            subfolder="契約書",
            filename=f"契約書_{company_name}_{_today_str()}.pdf",
            content_b64=pdf_b64,
            mime_type="application/pdf",
        )
        await _record_sync(company_id, sync_id, "drive", "quotation_contract", result.get("id", ""))
        return True
    except Exception as e:
        await _record_sync_failure(company_id, sync_id, "drive", "quotation_contract", str(e))
        return False


async def _sync_outreach_to_sheets(company_id: str, output: dict) -> bool:
    """アウトリーチ結果をSheetsに反映する。"""
    sent_count = output.get("sent_count", 0)
    if sent_count <= 0:
        return False

    try:
        connector = await _get_sheets_connector(company_id)
        today = _today_str()
        await connector.write_record("", {
            "range": "実績!A:C",
            "values": [[today, str(sent_count), "outreach_pipeline"]],
        })
        return True
    except Exception as e:
        logger.warning("sync_engine: sheets outreach sync failed: %s", e)
        return False


async def _sync_lead_to_sheets(company_id: str, output: dict) -> bool:
    """リードスコア結果をSheetsに反映する。"""
    lead = output.get("lead", {})
    score = output.get("score", 0)
    routing = output.get("routing", "")
    row_number = lead.get("row_number")

    if not row_number:
        return False

    try:
        connector = await _get_sheets_connector(company_id)
        await connector.write_record("", {
            "range": f"営業リスト!M{row_number}:O{row_number}",
            "values": [[str(score), routing, _today_str()]],
        })
        return True
    except Exception as e:
        logger.warning("sync_engine: sheets lead sync failed: %s", e)
        return False


async def _sync_support_draft(company_id: str, output: dict) -> bool:
    """サポート自動応答の下書きをGmailに作成する。"""
    draft_subject = output.get("reply_subject", "")
    draft_body = output.get("reply_body", "")
    to_email = output.get("customer_email", "")

    if not draft_body or not to_email:
        return False

    try:
        connector = await _get_gmail_connector(company_id)
        await connector.write_record("send", {
            "to": to_email,
            "subject": draft_subject or "Re: サポートお問い合わせ",
            "body_html": draft_body,
        })
        return True
    except Exception as e:
        logger.warning("sync_engine: gmail support draft failed: %s", e)
        return False


async def _sync_followup_draft(company_id: str, output: dict) -> bool:
    """商談フォローアップメールの下書きをGmailに作成する。"""
    draft_subject = output.get("followup_subject", "")
    draft_body = output.get("followup_body", "")
    to_email = output.get("contact_email", "")

    if not draft_body or not to_email:
        return False

    try:
        connector = await _get_gmail_connector(company_id)
        await connector.write_record("send", {
            "to": to_email,
            "subject": draft_subject or "本日はありがとうございました",
            "body_html": draft_body,
        })
        return True
    except Exception as e:
        logger.warning("sync_engine: gmail followup draft failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _extract_output(result: Any) -> dict:
    """パイプラインの result オブジェクトから dict を抽出する。"""
    if result is None:
        return {}
    if isinstance(result, dict):
        return result
    if hasattr(result, "final_output"):
        return result.final_output or {}
    if hasattr(result, "result"):
        return result.result if isinstance(result.result, dict) else {}
    return {}


def _make_sync_id(company_id: str, sync_type: str, source_id: str) -> str:
    """冪等性用のsync_idを生成する。"""
    raw = f"{company_id}:{sync_type}:{source_id}:{_today_str()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _is_already_synced(company_id: str, sync_id: str) -> bool:
    """同一sync_idが既に同期済かチェックする。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = db.table("gws_sync_state").select("id").eq(
            "sync_id", sync_id
        ).eq("company_id", company_id).eq("status", "synced").execute()
        return bool(result.data)
    except Exception:
        return False


async def _record_sync(
    company_id: str,
    sync_id: str,
    sync_type: str,
    source_pipeline: str,
    target_resource: str = "",
) -> None:
    """同期成功を記録する。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        db.table("gws_sync_state").upsert({
            "company_id": company_id,
            "sync_id": sync_id,
            "sync_type": sync_type,
            "source_pipeline": source_pipeline,
            "target_resource": target_resource,
            "status": "synced",
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning("_record_sync failed: %s", e)


async def _record_sync_failure(
    company_id: str,
    sync_id: str,
    sync_type: str,
    source_pipeline: str,
    error: str,
) -> None:
    """同期失敗を記録する（日次バッチでリトライ対象になる）。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        db.table("gws_sync_state").upsert({
            "company_id": company_id,
            "sync_id": sync_id,
            "sync_type": sync_type,
            "source_pipeline": source_pipeline,
            "status": "pending",
            "retry_count": 0,
            "error_message": error[:500],
        }).execute()
    except Exception as e:
        logger.warning("_record_sync_failure failed: %s", e)


async def _get_drive_connector(company_id: str):
    """GoogleDriveConnector のインスタンスを返す（クレデンシャル付き）。"""
    from workers.connector.google_drive import GoogleDriveConnector
    from workers.connector.base import ConnectorConfig
    creds = await _resolve_credentials(company_id, "google_drive")
    return GoogleDriveConnector(ConnectorConfig(tool_name="google_drive", credentials=creds))


async def _get_calendar_connector(company_id: str):
    """GoogleCalendarConnector のインスタンスを返す（クレデンシャル付き）。"""
    from workers.connector.google_calendar import GoogleCalendarConnector
    from workers.connector.base import ConnectorConfig
    creds = await _resolve_credentials(company_id, "google_calendar")
    return GoogleCalendarConnector(ConnectorConfig(tool_name="google_calendar", credentials=creds))


async def _get_sheets_connector(company_id: str):
    """GoogleSheetsConnector のインスタンスを返す（クレデンシャル付き）。"""
    from workers.connector.google_sheets import GoogleSheetsConnector
    from workers.connector.base import ConnectorConfig
    creds = await _resolve_credentials(company_id, "google_sheets")
    return GoogleSheetsConnector(ConnectorConfig(tool_name="google_sheets", credentials=creds))


async def _get_gmail_connector(company_id: str):
    """GmailConnector のインスタンスを返す（クレデンシャル付き）。"""
    from workers.connector.email import GmailConnector
    from workers.connector.base import ConnectorConfig
    creds = await _resolve_credentials(company_id, "email")
    return GmailConnector(ConnectorConfig(tool_name="email", credentials=creds))
