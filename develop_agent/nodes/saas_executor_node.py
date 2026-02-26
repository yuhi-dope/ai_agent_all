"""SaaS 操作実行ノード.

LangGraph パイプラインで SaaS 操作を実行する。
state.saas_operations に格納された操作リストを順次実行し、
結果を state.saas_results に格納する。

使用例（グラフ内）:
    graph.add_node("saas_executor", saas_executor_node)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from develop_agent.state import AgentState

logger = logging.getLogger(__name__)


def saas_executor_node(state: AgentState) -> dict[str, Any]:
    """SaaS 操作を実行するノード.

    state に必要なキー:
        - company_id: 企業ID
        - saas_connection_id: SaaS接続ID
        - saas_operations: [{"tool_name": "...", "arguments": {...}}, ...]

    Returns:
        {"saas_results": [...], "status": "saas_completed" | "saas_error"}
    """
    company_id = state.get("company_id", "")
    connection_id = state.get("saas_connection_id", "")
    operations = state.get("saas_operations") or []
    run_id = state.get("run_id", "")

    if not company_id or not connection_id:
        return {
            "saas_results": [],
            "status": "saas_error",
            "error_logs": list(state.get("error_logs") or [])
            + ["SaaS実行エラー: company_id または connection_id が未設定"],
        }

    if not operations:
        return {"saas_results": [], "status": "saas_completed"}

    # 同期ノードから async executor を呼ぶためのブリッジ
    results = asyncio.get_event_loop().run_until_complete(
        _execute_operations(company_id, connection_id, operations, run_id)
    )

    # 全操作が成功したかチェック
    all_success = all(r.get("result", {}).get("success", False) for r in results)
    status = "saas_completed" if all_success else "saas_partial"

    return {"saas_results": results, "status": status}


async def _execute_operations(
    company_id: str,
    connection_id: str,
    operations: list[dict],
    run_id: str,
) -> list[dict]:
    """SaaS 操作を順次実行する."""
    from server.saas_executor import SaaSExecutor
    from server import persist

    executor = SaaSExecutor(company_id=company_id, connection_id=connection_id)
    results = []

    try:
        await executor.initialize()

        for op in operations:
            tool_name = op.get("tool_name", "")
            arguments = op.get("arguments", {})

            try:
                result = await executor.execute(tool_name, arguments)
                results.append({"tool_name": tool_name, "result": result})
            except Exception as e:
                logger.exception("SaaS操作失敗: %s", tool_name)
                results.append({
                    "tool_name": tool_name,
                    "result": {"success": False, "error": str(e)},
                })

        # 監査ログを永続化
        audit_log = executor.get_audit_log()
        if audit_log:
            persist.persist_audit_logs(
                run_id=run_id or f"saas_{connection_id}",
                audit_records=audit_log,
                source="saas",
            )

    except Exception as e:
        logger.exception("SaaS Executor 初期化失敗")
        results.append({
            "tool_name": "_initialize",
            "result": {"success": False, "error": str(e)},
        })
    finally:
        await executor.close()

    return results
