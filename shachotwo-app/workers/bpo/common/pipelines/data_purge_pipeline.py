"""
データ自動削除パイプライン（パージジョブ）

レジストリキー: internal/data_purge
トリガー: スケジュール（毎月1日 03:00 UTC）
承認: 不要（自動実行）
対象テーブル:
  - knowledge_items: expires_at < NOW()
  - invitations: expires_at < NOW() AND status = 'pending'
  - execution_logs: created_at < NOW() - INTERVAL '2 years'（長期保存対象外）
  - proactive_proposals: expires_at < NOW() AND status != 'approved'
保持ポリシー:
  - audit_logs: 削除しない（5年保持。法令対応）
  - bpo_approvals: 削除しない（内部統制証跡）
  - employees: 退社後3年保持（労働基準法109条）
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from workers.bpo.common.pipelines.pipeline_utils import StepResult

logger = logging.getLogger(__name__)

# パージ対象テーブルの定義
PURGEABLE_TABLES = [
    "knowledge_items",
    "invitations",
    "execution_logs",
    "proactive_proposals",
]


@dataclass
class DataPurgePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    purged_counts: dict[str, int] = field(default_factory=dict)  # テーブル名 → 削除件数
    total_purged: int = 0
    dry_run: bool = False  # Trueの場合は削除せずカウントのみ


def _make_step(
    step_no: int,
    step_name: str,
    success: bool,
    result: dict[str, Any],
    duration_ms: int,
    warning: str | None = None,
) -> StepResult:
    """DataPurge専用のStepResult生成ヘルパー（LLM不使用のためcost_yen=0）。"""
    return StepResult(
        step_no=step_no,
        step_name=step_name,
        agent_name="data_purge",
        success=success,
        result=result,
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=duration_ms,
        warning=warning,
    )


async def _count_targets(
    db: Any,
    company_id: str,
    now_iso: str,
    cutoff_2yr: str,
    tables: list[str],
) -> dict[str, int]:
    """各テーブルの削除対象件数をカウントして返す。"""
    counts: dict[str, int] = {}

    if "knowledge_items" in tables:
        try:
            res = db.table("knowledge_items").select(
                "id", count="exact"
            ).eq("company_id", company_id).lt(
                "expires_at", now_iso
            ).not_.is_("expires_at", "null").execute()
            counts["knowledge_items"] = res.count or 0
        except Exception as e:
            logger.warning(f"knowledge_items カウント失敗: {e}")
            counts["knowledge_items"] = -1

    if "invitations" in tables:
        try:
            res = db.table("invitations").select(
                "id", count="exact"
            ).eq("company_id", company_id).lt(
                "expires_at", now_iso
            ).eq("status", "pending").execute()
            counts["invitations"] = res.count or 0
        except Exception as e:
            logger.warning(f"invitations カウント失敗: {e}")
            counts["invitations"] = -1

    if "execution_logs" in tables:
        try:
            res = db.table("execution_logs").select(
                "id", count="exact"
            ).eq("company_id", company_id).lt(
                "created_at", cutoff_2yr
            ).execute()
            counts["execution_logs"] = res.count or 0
        except Exception as e:
            logger.warning(f"execution_logs カウント失敗: {e}")
            counts["execution_logs"] = -1

    if "proactive_proposals" in tables:
        try:
            res = db.table("proactive_proposals").select(
                "id", count="exact"
            ).eq("company_id", company_id).lt(
                "expires_at", now_iso
            ).not_.is_("expires_at", "null").neq("status", "approved").execute()
            counts["proactive_proposals"] = res.count or 0
        except Exception as e:
            logger.warning(f"proactive_proposals カウント失敗: {e}")
            counts["proactive_proposals"] = -1

    return counts


async def run_data_purge_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> DataPurgePipelineResult:
    """
    Args:
        company_id: テナントID
        input_data: {
            "dry_run": bool,  # True=削除せずカウントのみ（デフォルト: False）
            "tables": list[str],  # 対象テーブルを絞る場合（デフォルト: 全テーブル）
        }
    """
    pipeline_start_ms = int(time.time() * 1000)
    steps: list[StepResult] = []

    dry_run: bool = input_data.get("dry_run", False)
    tables_filter: list[str] = input_data.get("tables", PURGEABLE_TABLES)
    # 対象テーブルをPURGEABLE_TABLESに含まれるものに限定
    tables = [t for t in tables_filter if t in PURGEABLE_TABLES]
    if not tables:
        tables = list(PURGEABLE_TABLES)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff_2yr = (now - timedelta(days=730)).isoformat()

    result = DataPurgePipelineResult(
        success=True,
        dry_run=dry_run,
    )

    # ── DBクライアント取得 ─────────────────────────────────────────────────────
    try:
        from db.supabase import get_service_client
        db = get_service_client()
    except Exception as e:
        logger.error(f"data_purge: DBクライアント取得失敗: {e}")
        result.success = False
        result.failed_step = "db_init"
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start_ms
        result.final_output = {"error": f"DBクライアント取得失敗: {e}"}
        return result

    # ── Step 1: 削除対象のカウント（dry_run 含む） ────────────────────────────
    step_start = int(time.time() * 1000)
    try:
        counts = await _count_targets(db, company_id, now_iso, cutoff_2yr, tables)
        step_duration = int(time.time() * 1000) - step_start
        steps.append(_make_step(
            step_no=1,
            step_name="count_targets",
            success=True,
            result={"counts": counts, "dry_run": dry_run},
            duration_ms=step_duration,
        ))
        logger.info(
            f"data_purge Step 1 count: company={company_id} counts={counts} dry_run={dry_run}"
        )
    except Exception as e:
        step_duration = int(time.time() * 1000) - step_start
        logger.error(f"data_purge Step 1 失敗: {e}")
        steps.append(_make_step(
            step_no=1,
            step_name="count_targets",
            success=False,
            result={"error": str(e)},
            duration_ms=step_duration,
            warning=f"カウント失敗: {e}",
        ))
        result.success = False
        result.failed_step = "count_targets"
        result.steps = steps
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start_ms
        result.final_output = {"error": str(e)}
        return result

    # dry_run=True の場合は削除せずカウント結果のみ返す
    if dry_run:
        result.purged_counts = {k: v for k, v in counts.items() if v >= 0}
        result.total_purged = 0
        result.steps = steps
        result.total_duration_ms = int(time.time() * 1000) - pipeline_start_ms
        result.final_output = {
            "dry_run": True,
            "would_purge": result.purged_counts,
            "message": "dry_run=True のため削除は実行しませんでした。",
        }
        logger.info(f"data_purge dry_run完了: company={company_id} would_purge={result.purged_counts}")
        return result

    # ── Step 2: knowledge_items の期限切れ削除 ────────────────────────────────
    if "knowledge_items" in tables:
        step_start = int(time.time() * 1000)
        try:
            db.table("knowledge_items").delete().eq(
                "company_id", company_id
            ).lt("expires_at", now_iso).not_.is_("expires_at", "null").execute()
            deleted = counts.get("knowledge_items", 0)
            result.purged_counts["knowledge_items"] = max(deleted, 0)
            step_duration = int(time.time() * 1000) - step_start
            steps.append(_make_step(
                step_no=2,
                step_name="purge_knowledge_items",
                success=True,
                result={"deleted": result.purged_counts["knowledge_items"]},
                duration_ms=step_duration,
            ))
            logger.info(
                f"data_purge Step 2 knowledge_items: company={company_id} deleted={result.purged_counts['knowledge_items']}"
            )
        except Exception as e:
            step_duration = int(time.time() * 1000) - step_start
            logger.error(f"data_purge Step 2 knowledge_items 失敗: {e}")
            result.purged_counts["knowledge_items"] = 0
            steps.append(_make_step(
                step_no=2,
                step_name="purge_knowledge_items",
                success=False,
                result={"error": str(e)},
                duration_ms=step_duration,
                warning=f"削除失敗（他テーブルは継続）: {e}",
            ))
            # 1テーブルの失敗で全体を止めない

    # ── Step 3: invitations の期限切れ削除（pending のみ） ───────────────────
    if "invitations" in tables:
        step_start = int(time.time() * 1000)
        try:
            db.table("invitations").delete().eq(
                "company_id", company_id
            ).lt("expires_at", now_iso).eq("status", "pending").execute()
            deleted = counts.get("invitations", 0)
            result.purged_counts["invitations"] = max(deleted, 0)
            step_duration = int(time.time() * 1000) - step_start
            steps.append(_make_step(
                step_no=3,
                step_name="purge_invitations",
                success=True,
                result={"deleted": result.purged_counts["invitations"]},
                duration_ms=step_duration,
            ))
            logger.info(
                f"data_purge Step 3 invitations: company={company_id} deleted={result.purged_counts['invitations']}"
            )
        except Exception as e:
            step_duration = int(time.time() * 1000) - step_start
            logger.error(f"data_purge Step 3 invitations 失敗: {e}")
            result.purged_counts["invitations"] = 0
            steps.append(_make_step(
                step_no=3,
                step_name="purge_invitations",
                success=False,
                result={"error": str(e)},
                duration_ms=step_duration,
                warning=f"削除失敗（他テーブルは継続）: {e}",
            ))
            # 1テーブルの失敗で全体を止めない

    # ── Step 4: execution_logs の2年超古いレコード削除 ────────────────────────
    if "execution_logs" in tables:
        step_start = int(time.time() * 1000)
        try:
            db.table("execution_logs").delete().eq(
                "company_id", company_id
            ).lt("created_at", cutoff_2yr).execute()
            deleted = counts.get("execution_logs", 0)
            result.purged_counts["execution_logs"] = max(deleted, 0)
            step_duration = int(time.time() * 1000) - step_start
            steps.append(_make_step(
                step_no=4,
                step_name="purge_execution_logs",
                success=True,
                result={"deleted": result.purged_counts["execution_logs"], "cutoff": cutoff_2yr},
                duration_ms=step_duration,
            ))
            logger.info(
                f"data_purge Step 4 execution_logs: company={company_id} "
                f"deleted={result.purged_counts['execution_logs']} cutoff={cutoff_2yr}"
            )
        except Exception as e:
            step_duration = int(time.time() * 1000) - step_start
            logger.error(f"data_purge Step 4 execution_logs 失敗: {e}")
            result.purged_counts["execution_logs"] = 0
            steps.append(_make_step(
                step_no=4,
                step_name="purge_execution_logs",
                success=False,
                result={"error": str(e)},
                duration_ms=step_duration,
                warning=f"削除失敗（他テーブルは継続）: {e}",
            ))
            # 1テーブルの失敗で全体を止めない

    # ── Step 5: proactive_proposals の期限切れ（承認済み以外） ───────────────
    if "proactive_proposals" in tables:
        step_start = int(time.time() * 1000)
        try:
            db.table("proactive_proposals").delete().eq(
                "company_id", company_id
            ).lt("expires_at", now_iso).not_.is_("expires_at", "null").neq("status", "approved").execute()
            deleted = counts.get("proactive_proposals", 0)
            result.purged_counts["proactive_proposals"] = max(deleted, 0)
            step_duration = int(time.time() * 1000) - step_start
            steps.append(_make_step(
                step_no=5,
                step_name="purge_proactive_proposals",
                success=True,
                result={"deleted": result.purged_counts["proactive_proposals"]},
                duration_ms=step_duration,
            ))
            logger.info(
                f"data_purge Step 5 proactive_proposals: company={company_id} "
                f"deleted={result.purged_counts['proactive_proposals']}"
            )
        except Exception as e:
            step_duration = int(time.time() * 1000) - step_start
            logger.error(f"data_purge Step 5 proactive_proposals 失敗: {e}")
            result.purged_counts["proactive_proposals"] = 0
            steps.append(_make_step(
                step_no=5,
                step_name="purge_proactive_proposals",
                success=False,
                result={"error": str(e)},
                duration_ms=step_duration,
                warning=f"削除失敗（他テーブルは継続）: {e}",
            ))
            # 1テーブルの失敗で全体を止めない

    # ── Step 6: 削除結果のサマリーをexecution_logsに記録 ─────────────────────
    result.total_purged = sum(v for v in result.purged_counts.values() if v > 0)
    step_start = int(time.time() * 1000)
    try:
        db.table("execution_logs").insert({
            "company_id": company_id,
            "pipeline_name": "data_purge",
            "status": "completed",
            "result_summary": {
                "purged_counts": result.purged_counts,
                "total_purged": result.total_purged,
                "dry_run": result.dry_run,
            },
        }).execute()
        step_duration = int(time.time() * 1000) - step_start
        steps.append(_make_step(
            step_no=6,
            step_name="record_summary",
            success=True,
            result={
                "purged_counts": result.purged_counts,
                "total_purged": result.total_purged,
            },
            duration_ms=step_duration,
        ))
        logger.info(
            f"data_purge 完了: company={company_id} total_purged={result.total_purged} "
            f"counts={result.purged_counts}"
        )
    except Exception as e:
        step_duration = int(time.time() * 1000) - step_start
        logger.error(f"data_purge Step 6 サマリー記録失敗: {e}")
        steps.append(_make_step(
            step_no=6,
            step_name="record_summary",
            success=False,
            result={"error": str(e)},
            duration_ms=step_duration,
            warning=f"サマリー記録失敗: {e}",
        ))
        # サマリー記録失敗は全体の成否に影響させない

    # 失敗ステップがあれば success=False に設定
    failed_steps = [s.step_name for s in steps if not s.success]
    if failed_steps:
        result.success = False
        result.failed_step = failed_steps[0]

    result.steps = steps
    result.total_duration_ms = int(time.time() * 1000) - pipeline_start_ms
    result.final_output = {
        "purged_counts": result.purged_counts,
        "total_purged": result.total_purged,
        "dry_run": result.dry_run,
        "executed_at": now_iso,
        # 保持ポリシーの明示（削除対象外テーブル）
        "preserved_tables": [
            "audit_logs（5年保持・法令対応）",
            "bpo_approvals（内部統制証跡）",
            "employees（退社後3年保持・労働基準法109条）",
        ],
    }
    return result
