"""営業BPO定期実行スケジューラ。

FastAPI起動時にバックグラウンドタスクとして稼働し、
各パイプラインを定期的に実行する。

スケジュール:
  - 毎日 08:00 JST: outreach_pipeline（アウトリーチ日次バッチ）
  - 毎日 09:00 JST: customer_lifecycle_pipeline(health_check)（全顧客ヘルス計算）
  - 毎日 10:00 JST: upsell_briefing_pipeline（拡張タイミング検知）
  - 毎月 1日 09:00 JST: revenue_request_pipeline（月次売上集計）
  - 毎月 1日 10:00 JST: cs_feedback_pipeline（CS品質月次学習）
  - 毎日 18:00 JST: win_loss_feedback_pipeline(outreach_pdca)（アウトリーチPDCA）
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# スケジュール定義
SCHEDULES = [
    {"name": "outreach_daily", "hour": 8, "minute": 0, "monthly": False},
    {"name": "health_check_daily", "hour": 9, "minute": 0, "monthly": False},
    {"name": "upsell_check_daily", "hour": 10, "minute": 0, "monthly": False},
    {"name": "outreach_pdca_daily", "hour": 18, "minute": 0, "monthly": False},
    {"name": "revenue_monthly", "hour": 9, "minute": 0, "monthly": True, "day": 1},
    {"name": "cs_feedback_monthly", "hour": 10, "minute": 0, "monthly": True, "day": 1},
]

_running = False
_tasks: list[asyncio.Task] = []


async def _get_system_company_id() -> str:
    """シャチョツー自社のcompany_idを取得する。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = db.table("companies").select("id").eq("slug", "shachotwo").limit(1).execute()
        if result.data:
            return result.data[0]["id"]
    except Exception:
        pass
    import os
    return os.environ.get("SYSTEM_COMPANY_ID", "system")


async def _run_scheduled_task(name: str) -> None:
    """スケジュール名に応じたパイプラインを実行する。"""
    company_id = await _get_system_company_id()
    logger.info(f"[scheduler] Starting: {name}")
    try:
        if name == "outreach_daily":
            from workers.bpo.sales.marketing.outreach_pipeline import run_outreach_pipeline
            await run_outreach_pipeline(company_id=company_id, input_data={"mode": "daily_batch"})

        elif name == "health_check_daily":
            from workers.bpo.sales.crm.customer_lifecycle_pipeline import run_customer_lifecycle_pipeline
            from db.supabase import get_service_client
            db = get_service_client()
            customers = db.table("customers").select("id").eq("status", "active").execute()
            for cust in (customers.data or []):
                try:
                    await run_customer_lifecycle_pipeline(
                        company_id=company_id,
                        input_data={"mode": "health_check", "customer_id": cust["id"]},
                    )
                except Exception as e:
                    logger.warning(f"health_check failed for {cust['id']}: {e}")
            # ROI実績計測（アクティブ顧客のみ）
            from workers.bpo.sales.learning.roi_feedback import calculate_roi_actuals
            for cust in (customers.data or []):
                try:
                    await calculate_roi_actuals(company_id, cust["id"])
                except Exception as e:
                    logger.warning(f"roi_actual failed for {cust['id']}: {e}")

        elif name == "upsell_check_daily":
            from workers.bpo.sales.cs.upsell_briefing_pipeline import run_upsell_briefing_pipeline
            await run_upsell_briefing_pipeline(company_id=company_id, input_data={"mode": "daily_scan"})

        elif name == "outreach_pdca_daily":
            from workers.bpo.sales.learning.win_loss_feedback_pipeline import run_win_loss_feedback_pipeline
            await run_win_loss_feedback_pipeline(company_id=company_id, input_data={"outcome": "outreach_pdca"})

        elif name == "revenue_monthly":
            from workers.bpo.sales.crm.revenue_request_pipeline import run_revenue_request_pipeline
            await run_revenue_request_pipeline(company_id=company_id, input_data={"mode": "revenue"})

        elif name == "cs_feedback_monthly":
            from workers.bpo.sales.learning.cs_feedback_pipeline import run_cs_feedback_pipeline
            await run_cs_feedback_pipeline(company_id=company_id, input_data={"mode": "monthly"})

        logger.info(f"[scheduler] Completed: {name}")
    except Exception as e:
        logger.error(f"[scheduler] Failed: {name}: {e}")


async def _scheduler_loop(schedule: dict) -> None:
    """1つのスケジュールエントリのループ。"""
    name = schedule["name"]
    while _running:
        now = datetime.now(JST)
        target_hour = schedule["hour"]
        target_minute = schedule["minute"]
        is_monthly = schedule.get("monthly", False)
        target_day = schedule.get("day", 1)

        # 次の実行時刻を計算
        target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if is_monthly:
            # 月次: 対象日の対象時刻
            if now.day > target_day or (now.day == target_day and now >= target):
                # 来月
                if now.month == 12:
                    target = target.replace(year=now.year + 1, month=1, day=target_day)
                else:
                    target = target.replace(month=now.month + 1, day=target_day)
            else:
                target = target.replace(day=target_day)
        else:
            # 日次: 今日の対象時刻が過ぎていたら明日
            if now >= target:
                target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.info(f"[scheduler] {name}: next run in {wait_seconds:.0f}s at {target.isoformat()}")
        await asyncio.sleep(wait_seconds)

        if _running:
            await _run_scheduled_task(name)


async def start_scheduler() -> None:
    """全スケジュールのバックグラウンドタスクを起動する。"""
    global _running
    _running = True
    for schedule in SCHEDULES:
        task = asyncio.create_task(_scheduler_loop(schedule))
        _tasks.append(task)
    logger.info(f"[scheduler] Started {len(SCHEDULES)} scheduled tasks")


async def stop_scheduler() -> None:
    """スケジューラを停止する。"""
    global _running
    _running = False
    for task in _tasks:
        task.cancel()
    _tasks.clear()
    logger.info("[scheduler] Stopped")
