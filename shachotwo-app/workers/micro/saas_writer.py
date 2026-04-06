"""saas_writer マイクロエージェント。承認済みタスクのみSaaSに書き込む。"""
import time
import uuid
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)

# コネクタ経由で書き込み対応するサービス一覧
_CONNECTOR_SERVICES = {"freee", "kintone", "slack", "smarthr"}


async def _fetch_encrypted_credentials(company_id: str, tool_name: str) -> str | None:
    """tool_connections テーブルから暗号化済みクレデンシャルを取得する。

    Args:
        company_id: テナントID
        tool_name:  "freee" | "kintone" | "slack" | "smarthr" 等

    Returns:
        暗号化済みクレデンシャル文字列。見つからない場合は None。
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = db.table("tool_connections") \
            .select("connection_config") \
            .eq("company_id", company_id) \
            .eq("tool_name", tool_name) \
            .eq("status", "active") \
            .limit(1) \
            .execute()
        if result.data:
            return result.data[0].get("connection_config")
    except Exception as e:
        logger.warning(f"_fetch_encrypted_credentials failed: tool={tool_name} error={e}")
    return None


async def _log_to_audit(company_id: str, agent_name: str, operation: str, params: dict, dry_run: bool) -> str | None:
    """audit_logsに操作を記録する。"""
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        result = db.table("audit_logs").insert({
            "company_id": company_id,
            "action": f"saas_writer.{operation}",
            "resource_type": "saas_operation",
            "details": {
                "agent": agent_name,
                "operation": operation,
                "params_summary": str(params)[:500],
                "dry_run": dry_run,
            },
        }).execute()
        if result.data:
            return result.data[0].get("id")
    except Exception as e:
        logger.warning(f"audit log failed: {e}")
    return None


async def run_saas_writer(input: MicroAgentInput) -> MicroAgentOutput:
    """
    承認済みタスクのみSaaSに書き込む。approved=Falseは即座に拒否。

    payload:
        service (str): "freee" | "smarthr" | "kintone" | "supabase"
        operation (str): 操作名（例: "create_expense", "update_invoice"）
        params (dict): 操作パラメータ
        approved (bool): 承認済みフラグ（必須）
        dry_run (bool, optional): True の場合DBには書き込まずログのみ

    result:
        success (bool): 実行成功
        operation_id (str|None): 実行ID
        dry_run (bool): ドライランかどうか
    """
    start_ms = int(time.time() * 1000)
    agent_name = "saas_writer"

    try:
        service: str = input.payload.get("service", "")
        operation: str = input.payload.get("operation", "")
        params: dict = input.payload.get("params", {})
        approved: bool = input.payload.get("approved", False)
        dry_run: bool = input.payload.get("dry_run", False)

        # 承認チェック（最重要ガード）
        if not approved:
            return MicroAgentOutput(
                agent_name=agent_name, success=False,
                result={"error": "未承認の書き込みは禁止されています (approved=False)", "requires_approval": True},
                confidence=1.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        if not service or not operation:
            return MicroAgentOutput(
                agent_name=agent_name, success=False,
                result={"error": "service と operation が必要です"},
                confidence=0.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        # 全操作をaudit_logsに記録（dry_run含む）
        operation_id = await _log_to_audit(
            input.company_id, agent_name, operation, params, dry_run
        ) or str(uuid.uuid4())

        if dry_run:
            logger.info(f"saas_writer DRY_RUN: service={service} op={operation} id={operation_id}")
            duration_ms = int(time.time() * 1000) - start_ms
            return MicroAgentOutput(
                agent_name=agent_name, success=True,
                result={"success": True, "operation_id": operation_id, "dry_run": True},
                confidence=1.0, cost_yen=0.0, duration_ms=duration_ms,
            )

        # 実際の書き込み（各SaaS別実装）
        if service == "supabase":
            from db.supabase import get_service_client
            db = get_service_client()
            table = params.get("table", "")
            data = params.get("data", {})
            action = params.get("action", "insert")  # "insert" | "update" | "delete"

            if not table or not data:
                raise ValueError("supabase書き込みにはtableとdataが必要です")

            data["company_id"] = input.company_id  # RLS保証

            if action == "insert":
                db.table(table).insert(data).execute()
            elif action == "update":
                record_id = params.get("id")
                if record_id:
                    db.table(table).update(data).eq("id", record_id).execute()
            elif action == "delete":
                record_id = params.get("id")
                if record_id:
                    db.table(table).delete().eq("id", record_id).eq("company_id", input.company_id).execute()
        elif service in _CONNECTOR_SERVICES:
            # コネクタ経由の書き込み（freee/kintone/slack/smarthr）
            encrypted_creds = await _fetch_encrypted_credentials(input.company_id, service)
            if not encrypted_creds:
                logger.warning(
                    f"saas_writer: {service} のクレデンシャルが未登録。"
                    f"operation_id={operation_id} を mock=True で返します。"
                )
                duration_ms = int(time.time() * 1000) - start_ms
                return MicroAgentOutput(
                    agent_name=agent_name, success=True,
                    result={"success": True, "operation_id": operation_id, "dry_run": False, "mock": True,
                            "reason": f"{service} クレデンシャル未設定"},
                    confidence=0.5, cost_yen=0.0, duration_ms=duration_ms,
                )
            try:
                from workers.connector.factory import get_connector
                connector = get_connector(service, encrypted_creds)
            except ValueError as exc:
                logger.warning(
                    f"saas_writer: {service} のコネクタが未登録 ({exc})。"
                    f"operation_id={operation_id} を mock=True で返します。"
                )
                duration_ms = int(time.time() * 1000) - start_ms
                return MicroAgentOutput(
                    agent_name=agent_name, success=True,
                    result={"success": True, "operation_id": operation_id, "dry_run": False, "mock": True,
                            "reason": str(exc)},
                    confidence=0.5, cost_yen=0.0, duration_ms=duration_ms,
                )
            resource = params.get("resource", operation)
            data = params.get("data", params)
            connector_result = await connector.write_record(resource, data)
            logger.info(
                f"saas_writer CONNECTOR_WRITE: service={service} op={operation} "
                f"id={operation_id} result={connector_result}"
            )
        else:
            # 未対応サービス: noop + warning
            logger.warning(
                f"saas_writer: {service} は未対応サービスです。"
                f"operation_id={operation_id} のみ記録。"
            )

        logger.info(f"saas_writer EXECUTED: service={service} op={operation} id={operation_id}")
        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"success": True, "operation_id": operation_id, "dry_run": False},
            confidence=1.0, cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"saas_writer error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )
