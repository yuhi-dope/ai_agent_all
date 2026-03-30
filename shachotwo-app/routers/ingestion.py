"""Knowledge ingestion endpoints."""
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from auth.middleware import get_current_user, require_role
from auth.jwt import JWTClaims
from brain.extraction import extract_knowledge
from db.supabase import get_service_client
from security.audit import audit_log
from security.pii_handler import PIIDetector
from security.rate_limiter import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


class TextIngestionRequest(BaseModel):
    content: str
    department: Optional[str] = None
    category: Optional[str] = None


class SessionResponse(BaseModel):
    id: UUID
    input_type: str
    extraction_status: str
    created_at: datetime


class ExtractedItemBrief(BaseModel):
    title: str
    content: str
    category: str
    item_type: str
    department: str
    confidence: float


class TextIngestionResponse(BaseModel):
    session_id: UUID
    items: list[ExtractedItemBrief] = []
    model_used: str = ""
    cost_yen: float = 0.0


class FileIngestionResponse(BaseModel):
    session_id: UUID
    items: list[ExtractedItemBrief] = []
    model_used: str = ""
    cost_yen: float = 0.0
    file_type: str
    file_size: int


class SessionDetailResponse(BaseModel):
    id: UUID
    input_type: str
    extraction_status: str
    extraction_error: Optional[dict] = None
    knowledge_items: list = []
    created_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    has_more: bool = False


@router.post("/ingestion/text", response_model=TextIngestionResponse)
async def ingest_text(
    body: TextIngestionRequest,
    request: Request,
    user: JWTClaims = Depends(get_current_user),
):
    """テキストナレッジ入力 → LLM構造化 → knowledge_items保存"""
    check_rate_limit(user.company_id, "default")
    try:
        # PII検出・マスク（MVPはブロックせずマスク＋警告ログのみ）
        text_content = body.content
        try:
            detector = PIIDetector()
            pii_result = detector.detect_and_report(text_content)
            if pii_result.has_pii:
                logger.warning(
                    f"PII detected in ingestion: company={user.company_id} "
                    f"types={[d.pii_type for d in pii_result.matches]} "
                    f"count={pii_result.total_count}"
                )
                text_content = pii_result.masked_text
        except Exception as pii_err:
            logger.warning(f"PII detection failed, proceeding without masking: {pii_err}")

        result = await extract_knowledge(
            text=text_content,
            company_id=user.company_id,
            user_id=user.sub,
            department=body.department,
            category=body.category,
        )
        response = TextIngestionResponse(
            session_id=result.session_id,
            items=[
                ExtractedItemBrief(
                    title=item.title,
                    content=item.content,
                    category=item.category,
                    item_type=item.item_type,
                    department=item.department,
                    confidence=item.confidence,
                )
                for item in result.items
            ],
            model_used=result.model_used,
            cost_yen=result.cost_yen,
        )
        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="knowledge_session",
            resource_id=str(result.session_id),
            details={"input_type": "text", "items_extracted": len(result.items)},
            ip_address=request.client.host if request.client else None,
        )
        return response
    except Exception as e:
        logger.error(f"Text ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


@router.post("/ingestion/file", response_model=FileIngestionResponse)
async def ingest_file(
    request: Request,
    file: UploadFile = File(...),
    department: Optional[str] = None,
    category: Optional[str] = None,
    user: JWTClaims = Depends(get_current_user),
):
    """ファイルナレッジ入力（txt/PDF/Excel/Word） → テキスト抽出 → LLM構造化 → knowledge_items保存"""
    check_rate_limit(user.company_id, "default")
    from brain.ingestion.file import ingest_file as do_ingest_file

    # Validate file extension
    filename = file.filename or "unknown"
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS.keys()))}",
        )

    # Read file content with size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large: {len(content)} bytes. Maximum: {MAX_FILE_SIZE} bytes (10MB).",
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    # Resolve content type from extension (more reliable than browser-sent type)
    content_type = ALLOWED_EXTENSIONS[ext]

    try:
        result = await do_ingest_file(
            file_content=content,
            filename=filename,
            content_type=content_type,
            company_id=user.company_id,
            user_id=user.sub,
            department=department,
            category=category,
        )

        # Upload original file to Supabase Storage
        storage_path: Optional[str] = None
        try:
            db = get_service_client()
            storage_path = f"{user.company_id}/{result.session_id}/{filename}"
            db.storage.from_("knowledge-files").upload(
                storage_path,
                content,
                {"content-type": content_type},
            )
            # Update knowledge_sessions with file metadata
            db.table("knowledge_sessions").update({
                "file_name": filename,
                "file_size": len(content),
                "file_content_type": content_type,
                "file_storage_path": storage_path,
            }).eq("id", str(result.session_id)).execute()
        except Exception as storage_err:
            logger.warning(f"Failed to upload file to Storage for session {result.session_id}: {storage_err}")
            storage_path = None

        response = FileIngestionResponse(
            session_id=result.session_id,
            items=[
                ExtractedItemBrief(
                    title=item.title,
                    content=item.content,
                    category=item.category,
                    item_type=item.item_type,
                    department=item.department,
                    confidence=item.confidence,
                )
                for item in result.items
            ],
            model_used=result.model_used,
            cost_yen=result.cost_yen,
            file_type=ext,
            file_size=len(content),
        )

        await audit_log(
            company_id=user.company_id,
            user_id=user.sub,
            action="create",
            resource_type="knowledge_session",
            resource_id=str(result.session_id),
            details={
                "input_type": "document",
                "file_name": filename,
                "file_type": ext,
                "file_size": len(content),
                "items_extracted": len(result.items),
                "storage_path": storage_path,
            },
            ip_address=request.client.host if request.client else None,
        )

        return response

    except ValueError as e:
        logger.warning(f"File ingestion validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"File ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _get_extension(filename: str) -> str:
    """Extract lowercase file extension from filename."""
    import os
    _, ext = os.path.splitext(filename)
    return ext.lower()


# ---------------------------------------------------------------------------
# CSV テンプレート定義
# ---------------------------------------------------------------------------

CSV_TEMPLATES: dict[str, dict] = {
    "products": {
        "filename": "製品マスタ_テンプレート.csv",
        "headers": ["品番", "品名", "材質", "単価(円)", "リードタイム(日)", "最小ロット", "備考"],
        "sample_rows": [
            ["MT-001", "フランジ A型", "SUS304", "1500", "5", "100", ""],
            ["MT-002", "シャフト B型", "S45C", "3200", "7", "50", "熱処理あり"],
        ],
    },
    "equipment": {
        "filename": "設備台帳_テンプレート.csv",
        "headers": ["設備名", "メーカー", "型式", "導入年", "保全周期(月)", "設置場所", "備考"],
        "sample_rows": [
            ["CNC旋盤 #1", "オークマ", "LB3000EX", "2018", "6", "第1工場A棟", ""],
        ],
    },
    "suppliers": {
        "filename": "仕入先マスタ_テンプレート.csv",
        "headers": ["仕入先名", "品目", "単価(円)", "リードタイム(日)", "評価(A/B/C)", "連絡先", "備考"],
        "sample_rows": [
            ["○○金属工業", "SUS304丸棒 φ30", "850", "3", "A", "03-xxxx-xxxx", ""],
        ],
    },
    "quality_standards": {
        "filename": "品質基準_テンプレート.csv",
        "headers": ["工程名", "検査項目", "基準値", "許容範囲", "検査方法", "検査頻度", "備考"],
        "sample_rows": [
            ["旋削加工", "外径寸法", "30.00mm", "±0.02mm", "マイクロメータ", "全数", ""],
        ],
    },
    "cost_data": {
        "filename": "原価データ_テンプレート.csv",
        "headers": ["品番", "材料費(円)", "加工費(円)", "外注費(円)", "経費(円)", "合計原価(円)", "備考"],
        "sample_rows": [
            ["MT-001", "500", "800", "0", "200", "1500", ""],
        ],
    },
}

# カテゴリ→knowledge_items カテゴリ名マッピング
_CATEGORY_MAP: dict[str, str] = {
    "products": "product_master",
    "equipment": "equipment_master",
    "suppliers": "supplier_master",
    "quality_standards": "quality_standard",
    "cost_data": "cost_data",
}

# カテゴリ→推定部門マッピング
_CATEGORY_DEPT_MAP: dict[str, str] = {
    "products": "製造",
    "equipment": "製造",
    "suppliers": "購買",
    "quality_standards": "品質管理",
    "cost_data": "原価管理",
}

# CSVプレビュー一時保存（session_id→(rows, headers)）
# 本番はRedis/DBに保存するが、MVPはインメモリで保持
_CSV_PREVIEW_STORE: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# CSV テンプレートダウンロード
# ---------------------------------------------------------------------------

@router.get("/ingestion/csv-template")
async def download_csv_template(
    category: str = Query(..., description="products / equipment / suppliers / quality_standards / cost_data"),
    user: JWTClaims = Depends(get_current_user),
):
    """製造業向けCSVテンプレートを返す（UTF-8 BOM付きCSV）。"""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    tmpl = CSV_TEMPLATES.get(category)
    if not tmpl:
        raise HTTPException(
            status_code=400,
            detail=f"無効なカテゴリです: {category}。有効値: {', '.join(CSV_TEMPLATES.keys())}",
        )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(tmpl["headers"])
    for row in tmpl["sample_rows"]:
        writer.writerow(row)

    # UTF-8 BOM付きでExcelでも文字化けしない
    content = "\ufeff" + buf.getvalue()

    # HTTPヘッダーはlatin-1のみ許容のため、日本語ファイル名はRFC 5987形式でエンコードする
    from urllib.parse import quote
    encoded_filename = quote(tmpl["filename"], safe="")
    ascii_fallback = tmpl["filename"].encode("ascii", errors="replace").decode("ascii")
    content_disposition = (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )

    return StreamingResponse(
        iter([content.encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": content_disposition},
    )


# ---------------------------------------------------------------------------
# CSV プレビュー＆バリデーション
# ---------------------------------------------------------------------------

class CSVValidationError(BaseModel):
    row: int
    column: str
    message: str


class CSVPreviewResponse(BaseModel):
    session_id: str
    columns: list[str]
    rows: list[list[str]]
    total_rows: int
    valid_rows: int
    errors: list[CSVValidationError]


def _validate_csv_rows(
    headers: list[str],
    rows: list[list[str]],
    category: str,
) -> list[CSVValidationError]:
    """CSVの各行をバリデーションしてエラー一覧を返す。"""
    errors: list[CSVValidationError] = []

    # 数値チェック対象カラム（カテゴリ別）
    numeric_columns: dict[str, set[str]] = {
        "products": {"単価(円)", "リードタイム(日)", "最小ロット"},
        "equipment": {"導入年", "保全周期(月)"},
        "suppliers": {"単価(円)", "リードタイム(日)"},
        "quality_standards": set(),
        "cost_data": {"材料費(円)", "加工費(円)", "外注費(円)", "経費(円)", "合計原価(円)"},
    }
    numeric_cols = numeric_columns.get(category, set())

    for row_idx, row in enumerate(rows, start=2):  # ヘッダー行=1なので2始まり
        for col_idx, col_name in enumerate(headers):
            val = row[col_idx] if col_idx < len(row) else ""
            if col_name in numeric_cols and val.strip():
                try:
                    float(val.replace(",", ""))
                except ValueError:
                    errors.append(CSVValidationError(
                        row=row_idx,
                        column=col_name,
                        message=f"数値ではありません: '{val}'",
                    ))
    return errors


@router.post("/ingestion/csv/preview", response_model=CSVPreviewResponse)
async def preview_csv(
    file: UploadFile = File(...),
    category: str = Query("products"),
    user: JWTClaims = Depends(get_current_user),
) -> CSVPreviewResponse:
    """CSVをパースしてプレビュー + バリデーション結果を返す。"""
    import csv
    import io
    import uuid as uuid_mod

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=422, detail="CSVファイルが空です。")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（上限10MB）。")

    # エンコーディング判定（BOM付きUTF-8 / UTF-8 / Shift_JIS）
    text: str
    for encoding in ("utf-8-sig", "utf-8", "shift_jis", "cp932"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=422, detail="CSVのエンコーディングを判定できませんでした。UTF-8またはShift_JISで保存してください。")

    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)

    if not all_rows:
        raise HTTPException(status_code=422, detail="CSVにデータがありません。")

    headers = all_rows[0]
    data_rows = all_rows[1:]

    # 空行を除外
    data_rows = [r for r in data_rows if any(c.strip() for c in r)]

    # バリデーション
    errors = _validate_csv_rows(headers, data_rows, category)
    error_rows = {e.row for e in errors}
    valid_rows = sum(1 for idx in range(len(data_rows)) if (idx + 2) not in error_rows)

    # プレビュー用に先頭10行のみ
    preview_rows = data_rows[:10]

    # セッションIDを発行してインメモリ保存（後のimportで使用）
    session_id = str(uuid_mod.uuid4())
    _CSV_PREVIEW_STORE[session_id] = {
        "headers": headers,
        "rows": data_rows,
        "category": category,
        "company_id": user.company_id,
    }

    return CSVPreviewResponse(
        session_id=session_id,
        columns=headers,
        rows=preview_rows,
        total_rows=len(data_rows),
        valid_rows=valid_rows,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# CSV 取込実行
# ---------------------------------------------------------------------------

class CSVImportRequest(BaseModel):
    session_id: str
    column_mapping: dict[str, str]  # CSVカラム名 → knowledge_items フィールド名
    category: str


class CSVImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: int
    knowledge_items_created: int


def _row_to_knowledge_content(
    headers: list[str],
    row: list[str],
    column_mapping: dict[str, str],
    category: str,
) -> str:
    """CSVの1行をknowledge_itemsのcontent文字列に変換する。"""
    parts: list[str] = []
    for col_idx, col_name in enumerate(headers):
        val = row[col_idx].strip() if col_idx < len(row) else ""
        if not val:
            continue
        # column_mappingで別名が指定されていればそちらを使う
        display_name = column_mapping.get(col_name, col_name)
        parts.append(f"{display_name}: {val}")
    return "\n".join(parts)


@router.post("/ingestion/csv/import", response_model=CSVImportResponse)
async def import_csv(
    req: CSVImportRequest,
    user: JWTClaims = Depends(require_role("admin")),
) -> CSVImportResponse:
    """CSVデータをknowledge_itemsに変換して保存。"""
    # セッションからデータ取得
    preview_data = _CSV_PREVIEW_STORE.get(req.session_id)
    if not preview_data:
        raise HTTPException(
            status_code=404,
            detail="セッションが見つかりません。先に /ingestion/csv/preview を呼び出してください。",
        )

    # 所有者チェック（別会社のセッションを流用させない）
    if preview_data["company_id"] != user.company_id:
        raise HTTPException(status_code=403, detail="このセッションにアクセスする権限がありません。")

    headers: list[str] = preview_data["headers"]
    data_rows: list[list[str]] = preview_data["rows"]
    category = req.category or preview_data.get("category", "products")

    knowledge_category = _CATEGORY_MAP.get(category, category)
    department = _CATEGORY_DEPT_MAP.get(category, "製造")

    # バリデーション（エラー行はスキップ）
    errors_list = _validate_csv_rows(headers, data_rows, category)
    error_row_nums = {e.row for e in errors_list}

    db = get_service_client()
    imported = 0
    skipped = 0
    error_count = len({e.row for e in errors_list})

    rows_to_insert: list[dict] = []
    for row_idx, row in enumerate(data_rows, start=2):
        if row_idx in error_row_nums:
            skipped += 1
            continue

        content = _row_to_knowledge_content(headers, row, req.column_mapping, category)
        if not content.strip():
            skipped += 1
            continue

        # 先頭カラムをタイトルとして使用
        title_val = row[0].strip() if row else ""
        title = f"[{knowledge_category}] {title_val}" if title_val else f"[{knowledge_category}] 行{row_idx}"

        rows_to_insert.append({
            "company_id": user.company_id,
            "department": department,
            "category": knowledge_category,
            "item_type": "fact",
            "title": title,
            "content": content,
            "source_type": "csv_import",
            "source_tag": f"csv_{category}",
            "confidence": 1.0,
        })
        imported += 1

    # 一括insert
    if rows_to_insert:
        try:
            db.table("knowledge_items").insert(rows_to_insert).execute()
        except Exception as e:
            logger.error(f"CSV import insert failed: {e}")
            raise HTTPException(status_code=500, detail=f"知識アイテムの保存に失敗しました: {e}")

        # embeddingはバックグラウンド生成（既存の非同期フローに委ねる）
        # knowledge_itemsにembeddingがない場合はQ&A検索時に自動生成される

    # セッションを消費済みとしてクリア
    _CSV_PREVIEW_STORE.pop(req.session_id, None)

    logger.info(
        f"CSV import: company_id={user.company_id} category={category} "
        f"imported={imported} skipped={skipped} errors={error_count}"
    )

    return CSVImportResponse(
        imported=imported,
        skipped=skipped,
        errors=error_count,
        knowledge_items_created=imported,
    )


@router.get("/ingestion/sessions", response_model=SessionListResponse)
async def list_sessions(
    extraction_status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: JWTClaims = Depends(get_current_user),
):
    """セッション一覧"""
    db = get_service_client()
    q = db.table("knowledge_sessions") \
        .select("id, input_type, extraction_status, created_at", count="exact") \
        .eq("company_id", user.company_id) \
        .order("created_at", desc=True) \
        .range(offset, offset + limit - 1)

    if extraction_status:
        q = q.eq("extraction_status", extraction_status)

    result = q.execute()
    return SessionListResponse(
        items=[SessionResponse(**r) for r in result.data],
        total=result.count or 0,
        has_more=(offset + limit) < (result.count or 0),
    )


@router.get("/ingestion/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: UUID,
    user: JWTClaims = Depends(get_current_user),
):
    """セッション詳細"""
    db = get_service_client()
    result = db.table("knowledge_sessions") \
        .select("*") \
        .eq("id", str(session_id)) \
        .eq("company_id", user.company_id) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Fetch related knowledge items
    items = db.table("knowledge_items") \
        .select("id, title, item_type, department, confidence") \
        .eq("session_id", str(session_id)) \
        .execute()

    return SessionDetailResponse(
        id=result.data["id"],
        input_type=result.data["input_type"],
        extraction_status=result.data["extraction_status"],
        extraction_error=result.data.get("extraction_error"),
        knowledge_items=items.data or [],
        created_at=result.data["created_at"],
    )
