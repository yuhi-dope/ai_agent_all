"""GoogleDriveConnector — Google Drive API v3 コネクタ。

営業用フォルダ自動構造:
    シャチョツー営業/{会社名}/{提案書|契約書|議事録}/

認証: サービスアカウント + Domain-Wide Delegation (既存Gmail/Sheetsと同パターン)
"""
import base64
import io
import logging
import os
from typing import Any

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

# 営業フォルダ構造のサブフォルダ名
_SALES_SUBFOLDERS = ["提案書", "契約書", "議事録"]

MIME_FOLDER = "application/vnd.google-apps.folder"


class GoogleDriveConnector(BaseConnector):
    """Google Drive API v3 コネクタ。BaseConnector準拠。

    credentials:
        credentials_path (str): サービスアカウントJSONファイルのパス
        delegated_email (str, optional): ドメイン委任対象アドレス
        root_folder_id (str, optional): 営業フォルダのルートID

    read_records の resource:
        "files"   — フォルダ内ファイル一覧
        "file"    — 単一ファイルメタデータ取得
        "content" — ファイルコンテンツ取得 (export/download)

    write_record の resource:
        "upload"          — ファイルアップロード
        "create_folder"   — フォルダ作成
        "share"           — 共有設定
        "update_metadata" — メタデータ更新
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._service = None

    def _get_service(self):
        """Drive API v3 サービスオブジェクトを取得（遅延初期化）。"""
        if self._service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            creds_path = self.config.credentials.get(
                "credentials_path",
                os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
            )
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

            delegated = self.config.credentials.get(
                "delegated_email",
                os.environ.get("GOOGLE_DRIVE_DELEGATED_EMAIL", ""),
            )
            if delegated:
                creds = creds.with_subject(delegated)

            self._service = build("drive", "v3", credentials=creds)
        return self._service

    @property
    def root_folder_id(self) -> str:
        """営業フォルダのルートフォルダID。"""
        return self.config.credentials.get(
            "root_folder_id",
            os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", ""),
        )

    # ------------------------------------------------------------------
    # BaseConnector 実装
    # ------------------------------------------------------------------

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """Drive からファイル情報を読み取る。

        resource="files":
            filters: folder_id, mime_type, query, page_size
        resource="file":
            filters: file_id
        resource="content":
            filters: file_id, export_mime_type
        """
        service = self._get_service()

        if resource == "files":
            return self._list_files(service, filters)
        elif resource == "file":
            return [self._get_file_metadata(service, filters)]
        elif resource == "content":
            return [self._get_file_content(service, filters)]
        else:
            logger.warning("GoogleDriveConnector.read_records: resource '%s' 未サポート", resource)
            return []

    async def write_record(self, resource: str, data: dict) -> dict:
        """Drive にファイルを書き込む。

        resource="upload":
            data: name, folder_id, content_b64, mime_type
        resource="create_folder":
            data: name, parent_folder_id
        resource="share":
            data: file_id, email, role
        resource="update_metadata":
            data: file_id, name, description
        """
        service = self._get_service()

        if resource == "upload":
            return self._upload_file(service, data)
        elif resource == "create_folder":
            return self._create_folder(service, data)
        elif resource == "share":
            return self._share_file(service, data)
        elif resource == "update_metadata":
            return self._update_metadata(service, data)
        else:
            raise ValueError(f"GoogleDriveConnector: 未知のresource '{resource}'")

    async def health_check(self) -> bool:
        """Drive API 疎通確認。about.get でストレージ情報を取得。"""
        try:
            service = self._get_service()
            service.about().get(fields="storageQuota").execute()
            return True
        except Exception as e:
            logger.error("GoogleDriveConnector.health_check failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # 営業フォルダ構造の自動生成
    # ------------------------------------------------------------------

    async def ensure_folder_hierarchy(self, company_name: str) -> dict[str, str]:
        """営業用フォルダ構造を自動生成し、各サブフォルダのIDを返す。

        構造:
            {root_folder}/
                {company_name}/
                    提案書/
                    契約書/
                    議事録/

        Returns:
            {"company": folder_id, "提案書": folder_id, "契約書": folder_id, "議事録": folder_id}
        """
        service = self._get_service()
        parent_id = self.root_folder_id

        # 会社フォルダを検索 or 作成
        company_folder_id = self._find_or_create_folder(
            service, company_name, parent_id
        )

        result = {"company": company_folder_id}
        for subfolder_name in _SALES_SUBFOLDERS:
            sub_id = self._find_or_create_folder(
                service, subfolder_name, company_folder_id
            )
            result[subfolder_name] = sub_id

        return result

    async def upload_document(
        self,
        company_name: str,
        subfolder: str,
        filename: str,
        content_b64: str,
        mime_type: str,
    ) -> dict:
        """営業フォルダにドキュメントをアップロードする便利メソッド。

        Args:
            company_name: 会社名（フォルダ自動作成）
            subfolder: "提案書" | "契約書" | "議事録"
            filename: ファイル名
            content_b64: base64エンコードされたファイルコンテンツ
            mime_type: MIMEタイプ

        Returns:
            {"id": file_id, "name": filename, "webViewLink": url}
        """
        folders = await self.ensure_folder_hierarchy(company_name)
        folder_id = folders.get(subfolder, folders["company"])
        return await self.write_record("upload", {
            "name": filename,
            "folder_id": folder_id,
            "content_b64": content_b64,
            "mime_type": mime_type,
        })

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _list_files(self, service: Any, filters: dict) -> list[dict]:
        folder_id = filters.get("folder_id", "")
        mime_type = filters.get("mime_type", "")
        query = filters.get("query", "")
        page_size = filters.get("page_size", 100)

        q_parts: list[str] = ["trashed = false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if mime_type:
            q_parts.append(f"mimeType = '{mime_type}'")
        if query:
            q_parts.append(query)

        result = service.files().list(
            q=" and ".join(q_parts),
            fields="files(id, name, mimeType, webViewLink, modifiedTime, size)",
            pageSize=page_size,
            orderBy="modifiedTime desc",
        ).execute()
        return result.get("files", [])

    def _get_file_metadata(self, service: Any, filters: dict) -> dict:
        file_id = filters.get("file_id", "")
        return service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, webViewLink, modifiedTime, size, parents",
        ).execute()

    def _get_file_content(self, service: Any, filters: dict) -> dict:
        file_id = filters.get("file_id", "")
        export_mime = filters.get("export_mime_type", "")

        if export_mime:
            content = service.files().export(
                fileId=file_id, mimeType=export_mime
            ).execute()
        else:
            content = service.files().get_media(fileId=file_id).execute()

        content_b64 = base64.b64encode(
            content if isinstance(content, bytes) else content.encode("utf-8")
        ).decode("utf-8")
        return {"file_id": file_id, "content_b64": content_b64}

    def _upload_file(self, service: Any, data: dict) -> dict:
        from googleapiclient.http import MediaIoBaseUpload

        name = data["name"]
        folder_id = data.get("folder_id", "")
        content_b64 = data["content_b64"]
        mime_type = data.get("mime_type", "application/octet-stream")

        file_metadata: dict[str, Any] = {"name": name}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        content_bytes = base64.b64decode(content_b64)
        media = MediaIoBaseUpload(
            io.BytesIO(content_bytes),
            mimetype=mime_type,
            resumable=True,
        )

        result = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink",
        ).execute()

        logger.info(
            "GoogleDriveConnector: uploaded '%s' → %s",
            name,
            result.get("id"),
        )
        return result

    def _create_folder(self, service: Any, data: dict) -> dict:
        name = data["name"]
        parent_id = data.get("parent_folder_id", "")

        metadata: dict[str, Any] = {
            "name": name,
            "mimeType": MIME_FOLDER,
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        result = service.files().create(
            body=metadata,
            fields="id, name, webViewLink",
        ).execute()

        logger.info("GoogleDriveConnector: created folder '%s' → %s", name, result.get("id"))
        return result

    def _share_file(self, service: Any, data: dict) -> dict:
        file_id = data["file_id"]
        email = data["email"]
        role = data.get("role", "reader")

        permission = {
            "type": "user",
            "role": role,
            "emailAddress": email,
        }
        result = service.permissions().create(
            fileId=file_id,
            body=permission,
            sendNotificationEmail=False,
        ).execute()
        return {"permission_id": result.get("id"), "file_id": file_id}

    def _update_metadata(self, service: Any, data: dict) -> dict:
        file_id = data["file_id"]
        updates: dict[str, str] = {}
        if "name" in data:
            updates["name"] = data["name"]
        if "description" in data:
            updates["description"] = data["description"]

        result = service.files().update(
            fileId=file_id,
            body=updates,
            fields="id, name, webViewLink",
        ).execute()
        return result

    def _find_or_create_folder(
        self, service: Any, name: str, parent_id: str
    ) -> str:
        """指定名のフォルダを検索し、なければ作成してIDを返す。"""
        q_parts = [
            f"name = '{name}'",
            f"mimeType = '{MIME_FOLDER}'",
            "trashed = false",
        ]
        if parent_id:
            q_parts.append(f"'{parent_id}' in parents")

        result = service.files().list(
            q=" and ".join(q_parts),
            fields="files(id)",
            pageSize=1,
        ).execute()

        files = result.get("files", [])
        if files:
            return files[0]["id"]

        return self._create_folder(service, {
            "name": name,
            "parent_folder_id": parent_id,
        })["id"]
