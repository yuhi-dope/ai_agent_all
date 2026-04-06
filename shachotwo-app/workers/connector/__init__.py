"""workers/connector — SaaS APIアダプター層。

saas_reader / saas_writer マイクロエージェントから呼ばれる薄いアダプター。
各コネクタは BaseConnector を継承し、read_records / write_record / health_check を実装する。

対応コネクタ:
    kintone         — kintone REST API v1
    freee           — freee API v1
    slack           — Slack Web API
    cloudsign       — CloudSign API v2（電子契約）
    gbizinfo        — gBizINFO 法人情報API
    playwright_form — Playwright フォーム自動送信
    google_sheets   — Google Sheets API
    email           — Gmail API（GmailConnector）

使い方:
    from workers.connector import get_connector
    connector = get_connector("kintone", encrypted_credentials)
    records = await connector.read_records("123", {"query": "..."})
"""
from workers.connector.base import BaseConnector, ConnectorConfig
from workers.connector.factory import get_connector

__all__ = ["BaseConnector", "ConnectorConfig", "get_connector"]
