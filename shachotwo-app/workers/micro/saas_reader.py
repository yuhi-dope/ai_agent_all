"""saas_reader マイクロエージェント。READ ONLYのSaaS APIからデータを取得する。"""
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)

# workers/connector が対応していないサービス向けのモックデータ
_MOCK_DATA: dict[str, dict[str, Any]] = {
    "smarthr": {
        "get_employees": {"data": [{"id": "e1", "display_name": "テスト花子", "department": "営業部"}], "count": 1},
    },
    "supabase": {},
}

# workers/connector が対応しているサービス一覧
_CONNECTOR_SERVICES = {"kintone", "freee", "slack"}


async def _read_supabase(company_id: str, operation: str, params: dict) -> dict[str, Any]:
    """Supabase経由のREAD操作。"""
    from db.supabase import get_service_client
    db = get_service_client()
    table = params.get("table", "")
    select = params.get("select", "*")
    limit = params.get("limit", 50)

    if not table:
        return {"data": [], "count": 0}

    result = (
        db.table(table)
        .select(select)
        .eq("company_id", company_id)
        .limit(limit)
        .execute()
    )
    data = result.data or []
    return {"data": data, "count": len(data)}


async def _read_via_connector(
    tool_name: str,
    encrypted_credentials: str,
    resource: str,
    filters: dict,
) -> tuple[list[dict], bool]:
    """workers/connector を使って SaaS からレコードを取得する。

    Returns:
        (records, mock_flag): レコードリストと、モックかどうかのフラグ
    """
    from workers.connector import get_connector
    connector = get_connector(tool_name, encrypted_credentials)
    records = await connector.read_records(resource, filters)
    return records, False


async def run_saas_reader(input: MicroAgentInput) -> MicroAgentOutput:
    """READ ONLYのSaaS APIからデータを取得する。

    payload:
        service              (str): "freee" | "kintone" | "slack" | "smarthr" | "supabase"
        operation            (str): 操作名（例: "list_expenses", "get_employees"）
        params               (dict): 操作パラメータ
        resource             (str, optional): コネクタ向けリソース識別子（app_id 等）
        encrypted_credentials (str, optional): encrypt_field() 済み認証情報
            ※ encrypted_credentials が指定されている場合は workers/connector を使用。
              指定がない場合はモックデータを返す。

    result:
        data    (list|dict): 取得データ
        count   (int):       データ件数
        service (str):       使用サービス
        mock    (bool):      モックデータかどうか
    """
    start_ms = int(time.time() * 1000)
    agent_name = "saas_reader"

    try:
        service: str = input.payload.get("service", "")
        operation: str = input.payload.get("operation", "")
        params: dict = input.payload.get("params", {})
        resource: str = input.payload.get("resource", operation)
        encrypted_credentials: str | None = input.payload.get("encrypted_credentials")

        if not service:
            return MicroAgentOutput(
                agent_name=agent_name, success=False,
                result={"error": "service が必要です"},
                confidence=0.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        mock = False
        records: list[dict] = []

        if service == "supabase":
            try:
                result_data = await _read_supabase(input.company_id, operation, params)
                records = result_data.get("data", [])
            except Exception as e:
                logger.warning(f"supabase read failed, using mock: {e}")
                records = []
                mock = True

        elif service in _CONNECTOR_SERVICES and encrypted_credentials:
            # workers/connector を使って実際のAPIを呼ぶ
            try:
                records, mock = await _read_via_connector(
                    service, encrypted_credentials, resource, params
                )
            except Exception as e:
                logger.warning(f"connector read failed ({service}), falling back to mock: {e}")
                records = []
                mock = True

        else:
            # 未対応サービスまたは認証情報なし → モックデータ
            service_mocks = _MOCK_DATA.get(service, {})
            mock_entry = service_mocks.get(operation, {"data": [], "count": 0})
            records = mock_entry.get("data", [])
            mock = True
            logger.info(f"saas_reader mock: service={service}, operation={operation}")

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"data": records, "count": len(records), "service": service, "mock": mock},
            confidence=0.5 if mock else 1.0,
            cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"saas_reader error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )
