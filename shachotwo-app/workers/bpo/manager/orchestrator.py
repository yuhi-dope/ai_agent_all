"""BPO Manager — Orchestrator。全マネージャーコンポーネントを定期実行する。"""
import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# バックグラウンドタスク参照
_orchestrator_task: asyncio.Task | None = None


def _is_global_kill_switch_on() -> bool:
    """環境変数 BPO_KILL_SWITCH=true でグローバル停止を判定する。"""
    return os.environ.get("BPO_KILL_SWITCH", "").lower() in ("true", "1", "yes")


async def _is_tenant_kill_switch_on(company_id: str) -> bool:
    """companies.bpo_kill_switch カラムを参照してテナント別停止フラグを取得する。
    カラムが存在しない場合や取得失敗時は False（停止しない）として扱う。
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = (
            db.table("companies")
            .select("bpo_kill_switch")
            .eq("id", company_id)
            .maybe_single()
            .execute()
        )
        if result.data is None:
            return False
        return bool(result.data.get("bpo_kill_switch", False))
    except Exception as e:
        logger.warning(f"orchestrator: bpo_kill_switch取得失敗 ({company_id[:8]}): {e}")
        return False


async def _get_active_company_ids() -> list[str]:
    """アクティブかつkill switchがOFFのテナントのcompany_idリストを取得する。

    bpo_kill_switch カラムが存在しない場合は全アクティブテナントを返す。
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        # bpo_kill_switch=True のテナントは取得段階で除外する（カラム不在時は全件取得）
        try:
            result = (
                db.table("companies")
                .select("id")
                .eq("is_active", True)
                .neq("bpo_kill_switch", True)
                .execute()
            )
        except Exception:
            # カラムが存在しない場合はフォールバック: is_active のみでフィルタ
            result = db.table("companies").select("id").eq("is_active", True).execute()
        return [r["id"] for r in (result.data or [])]
    except Exception as e:
        logger.error(f"orchestrator: テナント取得失敗: {e}")
        return []


async def _run_schedule_cycle():
    """全テナントのスケジュールトリガーを評価し、該当タスクを実行する。"""
    from workers.bpo.manager.schedule_watcher import scan_schedule_triggers
    from workers.bpo.manager.task_router import route_and_execute

    company_ids = await _get_active_company_ids()
    for cid in company_ids:
        try:
            tasks = await scan_schedule_triggers(cid)
            for task in tasks:
                asyncio.create_task(route_and_execute(task))
                logger.info(f"orchestrator schedule: dispatched {task.pipeline} for {cid[:8]}")
        except Exception as e:
            logger.error(f"orchestrator schedule error for {cid[:8]}: {e}")


async def _run_condition_cycle():
    """全テナントの条件連鎖トリガーを評価し、該当タスクを実行する。"""
    from workers.bpo.manager.condition_evaluator import evaluate_knowledge_triggers
    from workers.bpo.manager.task_router import route_and_execute

    company_ids = await _get_active_company_ids()
    for cid in company_ids:
        try:
            tasks = await evaluate_knowledge_triggers(cid)
            for task in tasks:
                asyncio.create_task(route_and_execute(task))
                logger.info(f"orchestrator condition: dispatched {task.pipeline} for {cid[:8]}")
        except Exception as e:
            logger.error(f"orchestrator condition error for {cid[:8]}: {e}")


async def _run_proactive_cycle():
    """全テナントの先読みスキャンを実行する。"""
    from workers.bpo.manager.proactive_scanner import scan_proactive_tasks
    from workers.bpo.manager.task_router import route_and_execute

    company_ids = await _get_active_company_ids()
    for cid in company_ids:
        try:
            tasks = await scan_proactive_tasks(cid)
            for task in tasks:
                asyncio.create_task(route_and_execute(task))
                logger.info(f"orchestrator proactive: dispatched {task.pipeline} for {cid[:8]}")
        except Exception as e:
            logger.error(f"orchestrator proactive error for {cid[:8]}: {e}")


async def _orchestrator_loop():
    """メインオーケストレータループ。1分間隔でスケジュール、5分間隔で条件、30分間隔で先読みを実行。

    各サイクル冒頭でkill switchをチェックする:
    - グローバルkill switch (BPO_KILL_SWITCH=true) → 全テナントをスキップ
    - テナント別kill switch (companies.bpo_kill_switch=True) → そのテナントのみスキップ
      ※ _get_active_company_ids() がkill switch=Trueのテナントを除外するため、
      テナントループ内での個別チェックは追加のDBアクセスを避けるため省略。
    """
    from workers.bpo.manager.notifier import notify_pipeline_event

    logger.info("orchestrator: バックグラウンドループ開始")
    tick = 0
    _global_kill_notified = False  # グローバルkill switch通知を1回だけ送るフラグ

    while True:
        try:
            # ── グローバルkill switchチェック ──────────────────────────────
            if _is_global_kill_switch_on():
                if not _global_kill_notified:
                    logger.warning("orchestrator: グローバルkill switch ON — 全テナントのBPO処理を停止")
                    asyncio.ensure_future(
                        notify_pipeline_event(
                            company_id="global",
                            pipeline="*",
                            event_type="kill_switch",
                            details={"reason": "BPO_KILL_SWITCH 環境変数によるグローバル停止"},
                        )
                    )
                    _global_kill_notified = True
                await asyncio.sleep(60)
                continue

            # グローバルkill switchが解除されたらフラグをリセット
            if _global_kill_notified:
                logger.info("orchestrator: グローバルkill switch 解除 — BPO処理を再開")
                _global_kill_notified = False

            # ── 通常サイクル ───────────────────────────────────────────────
            # 毎分: スケジュールトリガー評価（cron式と現在時刻を照合）
            await _run_schedule_cycle()

            # 5分ごと: 条件連鎖トリガー評価
            if tick % 5 == 0:
                await _run_condition_cycle()

            # 30分ごと: 先読みスキャン
            if tick % 30 == 0:
                await _run_proactive_cycle()

            tick += 1
        except Exception as e:
            logger.error(f"orchestrator loop error: {e}")

        await asyncio.sleep(60)  # 1分間隔


async def start_orchestrator():
    """オーケストレータをバックグラウンドタスクとして起動する。"""
    global _orchestrator_task
    if _orchestrator_task and not _orchestrator_task.done():
        logger.warning("orchestrator: already running")
        return
    _orchestrator_task = asyncio.create_task(_orchestrator_loop())
    logger.info("orchestrator: started")


async def stop_orchestrator():
    """オーケストレータを停止する。"""
    global _orchestrator_task
    if _orchestrator_task and not _orchestrator_task.done():
        _orchestrator_task.cancel()
        try:
            await _orchestrator_task
        except asyncio.CancelledError:
            pass
    _orchestrator_task = None
    logger.info("orchestrator: stopped")
