"""document_ocr マイクロエージェント。PDF/画像/テキストをテキストに変換する。"""
import os
import time
import logging
from pathlib import Path

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)

# Google Document AI のオプショナルインポート
# インストールされていない環境でも動作するよう try/except でラップし、
# モジュールレベルの `documentai` 名前は必ず存在させる（テスト時の patch 対象）
try:
    from google.cloud import documentai  # type: ignore[assignment]
    _DOCUMENTAI_AVAILABLE = True
except ImportError:
    documentai = None  # type: ignore[assignment]
    _DOCUMENTAI_AVAILABLE = False
    logger.info("google-cloud-documentai not installed; Document AI unavailable")

# Document AI 対応MIMEタイプ
_DOCUMENTAI_MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

# 1ページあたりのDocument AIコスト (円)
_DOCUMENTAI_COST_PER_PAGE_YEN = 2.0


def _get_documentai_config() -> tuple[str, str, str] | None:
    """環境変数からDocument AI設定を取得する。未設定はNoneを返す。"""
    project_id = os.environ.get("DOCUMENT_AI_PROJECT_ID", "")
    location = os.environ.get("DOCUMENT_AI_LOCATION", "us")
    processor_id = os.environ.get("DOCUMENT_AI_PROCESSOR_ID", "")
    if not project_id or not processor_id:
        return None
    return project_id, location, processor_id


async def _run_document_ai(file_path: Path, mime_type: str) -> tuple[str, int]:
    """
    Google Document AI でファイルをOCR処理する。

    Returns:
        (extracted_text, page_count)

    Raises:
        Exception: Document AI API エラー時
    """
    config = _get_documentai_config()
    if config is None:
        raise RuntimeError("Document AI 環境変数 (DOCUMENT_AI_PROJECT_ID, DOCUMENT_AI_PROCESSOR_ID) が未設定")

    project_id, location, processor_id = config

    # Document AI クライアント初期化
    client_options = {"api_endpoint": f"{location}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(client_options=client_options)

    # プロセッサ名
    processor_name = client.processor_path(project_id, location, processor_id)

    # ファイル読み込み
    raw_document = documentai.RawDocument(
        content=file_path.read_bytes(),
        mime_type=mime_type,
    )

    # API 呼び出し
    request = documentai.ProcessRequest(
        name=processor_name,
        raw_document=raw_document,
    )
    result = client.process_document(request=request)
    document = result.document

    # ページ数取得
    page_count = len(document.pages) if document.pages else 1

    return document.text, page_count


async def run_document_ocr(input: MicroAgentInput) -> MicroAgentOutput:
    """
    PDF/画像ファイルをテキストに変換する。

    payload:
        file_path (str, optional): ファイルパス（PDF/PNG/JPG/TIFF）
        text (str, optional): テキストが直接渡された場合はそのまま通す

    result:
        text (str): 抽出テキスト
        pages (int): ページ数（ファイルの場合）
        source (str): "text" | "file" | "document_ai" | "pypdf" | "mock"
    """
    start_ms = int(time.time() * 1000)
    agent_name = "document_ocr"

    try:
        payload = input.payload
        text = payload.get("text")
        file_path = payload.get("file_path")

        # テキスト直渡し
        if text:
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name,
                success=True,
                result={"text": text, "pages": 1, "source": "text"},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        if not file_path:
            raise MicroAgentError(agent_name, "input_validation", "file_path または text が必要です")

        path = Path(file_path)
        if not path.exists():
            raise MicroAgentError(agent_name, "file_check", f"ファイルが見つかりません: {file_path}")

        suffix = path.suffix.lower()

        # テキストファイルはそのまま読む（Document AI 不要）
        if suffix in (".txt", ".md"):
            content = path.read_text(encoding="utf-8")
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name,
                success=True,
                result={"text": content, "pages": 1, "source": "file"},
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        # Document AI 対応MIMEタイプの場合、Document AI を優先試行
        mime_type = _DOCUMENTAI_MIME_TYPES.get(suffix)
        if mime_type and _DOCUMENTAI_AVAILABLE and _get_documentai_config() is not None:
            try:
                extracted, pages = await _run_document_ai(path, mime_type)
                cost_yen = pages * _DOCUMENTAI_COST_PER_PAGE_YEN
                duration_ms = int(time.time() * 1000) - start_ms
                logger.info(
                    f"document_ocr: Document AI 成功 file={path.name} pages={pages} "
                    f"cost_yen={cost_yen}"
                )
                return MicroAgentOutput(
                    agent_name=agent_name,
                    success=True,
                    result={"text": extracted, "pages": pages, "source": "document_ai"},
                    confidence=0.9,
                    cost_yen=cost_yen,
                    duration_ms=duration_ms,
                )
            except Exception as e:
                logger.warning(
                    f"document_ocr: Document AI 失敗 ({e}), pypdf にフォールバック"
                )

        # pypdf フォールバック（PDF のみ）
        if suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                pages = len(reader.pages)
                extracted = "\n".join(
                    page.extract_text() or "" for page in reader.pages
                )
                duration_ms = int(time.time() * 1000) - start_ms
                logger.info(
                    f"document_ocr: pypdf フォールバック file={path.name} pages={pages}"
                )
                return MicroAgentOutput(
                    agent_name=agent_name,
                    success=True,
                    result={"text": extracted, "pages": pages, "source": "pypdf"},
                    confidence=0.6,
                    cost_yen=0.0,
                    duration_ms=duration_ms,
                )
            except ImportError:
                logger.warning("pypdf not installed, falling back to mock")

        # 最終フォールバック: mock
        logger.warning(f"document_ocr: mock フォールバック file={file_path}")
        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "text": f"[OCR mock] {path.name} の内容（実際のOCRには Google Document AI が必要です）",
                "pages": 1,
                "source": "mock",
            },
            confidence=0.3,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"document_ocr error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
