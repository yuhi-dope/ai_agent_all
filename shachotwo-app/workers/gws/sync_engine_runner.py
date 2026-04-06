"""GWS pending sync ランナー。task_router から呼び出されるパイプラインラッパー。"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_pending_syncs_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """gws_sync_state の pending/failed レコードをリトライする。

    PIPELINE_REGISTRY 経由で task_router から呼び出される。
    run_pending_syncs() を会社単位で実行し、結果を PipelineResult 互換の dict で返す。

    Args:
        company_id: テナントID
        input_data: 追加パラメータ（現在未使用）

    Returns:
        PipelineResult 互換の dict
    """
    from workers.gws.sync_engine import run_pending_syncs

    try:
        processed = await run_pending_syncs(company_id)
        return {
            "success": True,
            "final_output": {
                "processed": processed,
                "message": f"GWS pending sync: {processed} 件を同期しました",
            },
        }
    except Exception as e:
        logger.error("run_pending_syncs_pipeline: error company=%s: %s", company_id[:8], e)
        return {
            "success": False,
            "final_output": {"error": str(e)},
        }
