"""ConnectorFactory — tool_name から適切なコネクタインスタンスを返す。"""
from security.encryption import decrypt_field
from workers.connector.base import BaseConnector, ConnectorConfig
from workers.connector.backlog import BacklogConnector
from workers.connector.cloudsign import CloudSignConnector
from workers.connector.email import GmailConnector
from workers.connector.freee import FreeeConnector
from workers.connector.gbizinfo import GBizInfoConnector
from workers.connector.google_calendar import GoogleCalendarConnector
from workers.connector.google_drive import GoogleDriveConnector
from workers.connector.google_sheets import GoogleSheetsConnector
from workers.connector.jobcan import JobcanConnector
from workers.connector.king_of_time import KingOfTimeConnector
from workers.connector.kintone import KintoneConnector
from workers.connector.microsoft365 import Microsoft365Connector
from workers.connector.money_forward import MoneyForwardConnector
from workers.connector.notion import NotionConnector
from workers.connector.playwright_form import PlaywrightFormConnector
from workers.connector.slack import SlackConnector
from workers.connector.smarthr import SmartHRConnector
from workers.connector.yayoi import YayoiConnector

CONNECTORS: dict[str, type[BaseConnector]] = {
    "kintone": KintoneConnector,
    "freee": FreeeConnector,
    "slack": SlackConnector,
    "cloudsign": CloudSignConnector,
    "gbizinfo": GBizInfoConnector,
    "playwright_form": PlaywrightFormConnector,
    "google_sheets": GoogleSheetsConnector,
    "google_drive": GoogleDriveConnector,
    "google_calendar": GoogleCalendarConnector,
    "email": GmailConnector,
    "smarthr": SmartHRConnector,
    "king_of_time": KingOfTimeConnector,
    "money_forward": MoneyForwardConnector,
    "yayoi": YayoiConnector,
    "jobcan": JobcanConnector,
    "backlog": BacklogConnector,
    "notion": NotionConnector,
    "microsoft365": Microsoft365Connector,
}


def get_connector(tool_name: str, encrypted_credentials: str) -> BaseConnector:
    """暗号化された認証情報を復号して、対応するコネクタを返す。

    Args:
        tool_name:             "kintone" | "freee" | "slack"
        encrypted_credentials: encrypt_field() で暗号化した認証情報文字列

    Returns:
        BaseConnector のサブクラスインスタンス

    Raises:
        ValueError: tool_name が未登録の場合
    """
    cls = CONNECTORS.get(tool_name)
    if cls is None:
        raise ValueError(
            f"Unknown connector: {tool_name}. Available: {list(CONNECTORS)}"
        )
    credentials: dict = decrypt_field(encrypted_credentials)
    return cls(ConnectorConfig(tool_name=tool_name, credentials=credentials))
