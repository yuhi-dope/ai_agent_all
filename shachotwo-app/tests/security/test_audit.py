"""Tests for security.audit — Audit logging."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from security.audit import AuditLogger, audit_log


class TestAuditLogger:
    """Test AuditLogger.log method."""

    @pytest.mark.asyncio
    async def test_log_basic(self):
        """Audit log inserts correct data to audit_logs table."""
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "test-id"}])

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("security.audit.get_service_client", return_value=mock_client):
            logger = AuditLogger()
            await logger.log(
                company_id="comp-123",
                user_id="user-456",
                action="create",
                resource_type="knowledge_item",
                resource_id="item-789",
                details={"title": "Test Rule"},
                ip_address="192.168.1.1",
            )

        mock_client.table.assert_called_once_with("audit_logs")
        inserted = mock_table.insert.call_args[0][0]
        assert inserted["company_id"] == "comp-123"
        assert inserted["actor_user_id"] == "user-456"
        assert inserted["action"] == "create"
        assert inserted["resource_type"] == "knowledge_item"
        assert inserted["resource_id"] == "item-789"
        assert inserted["metadata"] == {"title": "Test Rule"}
        assert inserted["ip_address"] == "192.168.1.1"

    @pytest.mark.asyncio
    async def test_log_minimal(self):
        """Audit log works with minimal required fields."""
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{}])

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("security.audit.get_service_client", return_value=mock_client):
            logger = AuditLogger()
            await logger.log(
                company_id="comp-123",
                action="read",
                resource_type="knowledge_item",
            )

        inserted = mock_table.insert.call_args[0][0]
        assert inserted["company_id"] == "comp-123"
        assert inserted["action"] == "read"
        assert "actor_user_id" not in inserted
        assert "resource_id" not in inserted
        assert "metadata" not in inserted
        assert "ip_address" not in inserted

    @pytest.mark.asyncio
    async def test_log_swallows_exceptions(self):
        """Audit log should not propagate exceptions — fail silently."""
        with patch("security.audit.get_service_client", side_effect=Exception("DB connection failed")):
            logger = AuditLogger()
            # Should NOT raise
            await logger.log(
                company_id="comp-123",
                action="create",
                resource_type="knowledge_item",
            )


class TestAuditLogConvenience:
    """Test the module-level audit_log function."""

    @pytest.mark.asyncio
    async def test_convenience_function(self):
        """audit_log() convenience function delegates to AuditLogger."""
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{}])

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("security.audit.get_service_client", return_value=mock_client):
            await audit_log(
                company_id="comp-123",
                user_id="user-456",
                action="delete",
                resource_type="knowledge_item",
                resource_id="item-789",
            )

        mock_client.table.assert_called_once_with("audit_logs")
        inserted = mock_table.insert.call_args[0][0]
        assert inserted["action"] == "delete"

    @pytest.mark.asyncio
    async def test_details_as_dict(self):
        """details field properly passes through as dict."""
        mock_table = MagicMock()
        mock_table.insert.return_value.execute.return_value = MagicMock(data=[{}])

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        details = {
            "old_values": {"title": "Old Title"},
            "new_values": {"title": "New Title"},
            "version": 2,
        }

        with patch("security.audit.get_service_client", return_value=mock_client):
            await audit_log(
                company_id="comp-123",
                action="update",
                resource_type="knowledge_item",
                details=details,
            )

        inserted = mock_table.insert.call_args[0][0]
        assert inserted["metadata"] == details
        assert inserted["metadata"]["old_values"]["title"] == "Old Title"
