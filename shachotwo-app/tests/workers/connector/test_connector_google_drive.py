"""GoogleDriveConnector のユニットテスト。"""
import base64
import io
import pytest
from unittest.mock import MagicMock, patch, ANY

from workers.connector.base import ConnectorConfig
from workers.connector.google_drive import GoogleDriveConnector


def _make_connector() -> GoogleDriveConnector:
    return GoogleDriveConnector(ConnectorConfig(
        tool_name="google_drive",
        credentials={"credentials_path": "/tmp/fake.json", "root_folder_id": "root123"},
    ))


def _mock_service() -> MagicMock:
    """Drive API v3 サービスのモック。"""
    svc = MagicMock()
    # files().list()
    svc.files().list().execute.return_value = {
        "files": [{"id": "f1", "name": "test.pdf", "mimeType": "application/pdf"}]
    }
    # files().get()
    svc.files().get().execute.return_value = {
        "id": "f1", "name": "test.pdf", "webViewLink": "https://drive/f1"
    }
    # files().create()
    svc.files().create().execute.return_value = {
        "id": "new1", "name": "uploaded.pdf", "webViewLink": "https://drive/new1"
    }
    # permissions().create()
    svc.permissions().create().execute.return_value = {"id": "perm1"}
    # files().update()
    svc.files().update().execute.return_value = {
        "id": "f1", "name": "renamed.pdf", "webViewLink": "https://drive/f1"
    }
    # about().get()
    svc.about().get().execute.return_value = {"storageQuota": {"limit": "100"}}
    return svc


class TestGoogleDriveConnector:
    def setup_method(self) -> None:
        self.connector = _make_connector()
        self.service = _mock_service()

    @pytest.mark.asyncio
    async def test_read_files(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.read_records("files", {"folder_id": "root123"})
        assert len(result) == 1
        assert result[0]["id"] == "f1"

    @pytest.mark.asyncio
    async def test_read_file_metadata(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.read_records("file", {"file_id": "f1"})
        assert len(result) == 1
        assert result[0]["name"] == "test.pdf"

    @pytest.mark.asyncio
    async def test_write_upload(self):
        content = base64.b64encode(b"hello pdf").decode()
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("upload", {
                "name": "test.pdf",
                "folder_id": "root123",
                "content_b64": content,
                "mime_type": "application/pdf",
            })
        assert result["id"] == "new1"

    @pytest.mark.asyncio
    async def test_write_create_folder(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("create_folder", {
                "name": "新規フォルダ",
                "parent_folder_id": "root123",
            })
        assert "id" in result

    @pytest.mark.asyncio
    async def test_write_share(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("share", {
                "file_id": "f1",
                "email": "user@example.com",
                "role": "reader",
            })
        assert result["permission_id"] == "perm1"

    @pytest.mark.asyncio
    async def test_write_update_metadata(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.write_record("update_metadata", {
                "file_id": "f1",
                "name": "renamed.pdf",
            })
        assert result["name"] == "renamed.pdf"

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            assert await self.connector.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        self.service.about().get().execute.side_effect = Exception("auth error")
        with patch.object(self.connector, "_get_service", return_value=self.service):
            assert await self.connector.health_check() is False

    @pytest.mark.asyncio
    async def test_ensure_folder_hierarchy(self):
        # _find_or_create_folder: 最初は検索で見つかる、サブフォルダは作成
        call_count = 0
        def mock_list(**kwargs):
            nonlocal call_count
            call_count += 1
            mock = MagicMock()
            if call_count == 1:
                # 会社フォルダが見つかる
                mock.execute.return_value = {"files": [{"id": "company_folder_id"}]}
            else:
                # サブフォルダは見つからない → 作成される
                mock.execute.return_value = {"files": []}
            return mock

        self.service.files().list = mock_list
        with patch.object(self.connector, "_get_service", return_value=self.service):
            result = await self.connector.ensure_folder_hierarchy("テスト株式会社")
        assert "company" in result

    @pytest.mark.asyncio
    async def test_write_unknown_resource_raises(self):
        with patch.object(self.connector, "_get_service", return_value=self.service):
            with pytest.raises(ValueError, match="未知のresource"):
                await self.connector.write_record("unknown", {})


class TestGoogleDriveFactoryRegistration:
    def test_registered_in_factory(self):
        from workers.connector.factory import CONNECTORS
        assert "google_drive" in CONNECTORS
        assert CONNECTORS["google_drive"] is GoogleDriveConnector
