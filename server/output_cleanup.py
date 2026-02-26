"""
output/ ディレクトリの古いサブディレクトリを定期削除するスケジューラ。
ENABLE_OUTPUT_CLEANUP_SCHEDULER=1 で有効化。
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = _PROJECT_ROOT / "output"


def _is_enabled() -> bool:
    val = os.environ.get("ENABLE_OUTPUT_CLEANUP_SCHEDULER", "").strip()
    return val in ("1", "true", "yes")


def _ttl_seconds() -> float:
    try:
        days = int(os.environ.get("OUTPUT_CLEANUP_TTL_DAYS", "30"))
    except (TypeError, ValueError):
        days = 30
    return days * 86400


def _interval_seconds() -> float:
    try:
        hours = int(os.environ.get("OUTPUT_CLEANUP_INTERVAL_HOURS", "24"))
    except (TypeError, ValueError):
        hours = 24
    return hours * 3600


def cleanup_once() -> int:
    """
    output/ 配下のサブディレクトリのうち、最終更新が TTL を超えたものを削除する。
    削除した件数を返す。
    """
    if not OUTPUT_DIR.exists():
        return 0
    ttl = _ttl_seconds()
    now = time.time()
    deleted = 0
    for entry in OUTPUT_DIR.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age = now - mtime
        if age > ttl:
            try:
                shutil.rmtree(entry)
                logger.info(
                    "Deleted old output directory: %s (age=%.0f hours)",
                    entry.name,
                    age / 3600,
                )
                deleted += 1
            except Exception as e:
                logger.warning("Failed to delete %s: %s", entry.name, e)
    return deleted


async def _scheduler_loop():
    interval = _interval_seconds()
    logger.info(
        "Output cleanup scheduler started: interval=%dh, ttl=%dd",
        int(interval / 3600),
        int(_ttl_seconds() / 86400),
    )
    while True:
        try:
            deleted = cleanup_once()
            if deleted:
                logger.info("Output cleanup: deleted %d directories", deleted)
        except Exception as e:
            logger.warning("Output cleanup error: %s", e)
        await asyncio.sleep(interval)


async def start_scheduler():
    """ENABLE_OUTPUT_CLEANUP_SCHEDULER=1 のときのみスケジューラを起動する。"""
    if not _is_enabled():
        logger.info("Output cleanup scheduler disabled")
        return
    asyncio.create_task(_scheduler_loop())
