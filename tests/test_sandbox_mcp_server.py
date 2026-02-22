"""Tests for sandbox/mcp_server.py tool functions (unit-level, no Docker).

SANDBOX_WORKSPACE env var is overridden to a temp directory so that
the mcp_server module operates on safe, ephemeral paths.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add sandbox/ to path so we can import mcp_server directly
_sandbox_dir = str(Path(__file__).resolve().parent.parent / "sandbox")
if _sandbox_dir not in sys.path:
    sys.path.insert(0, _sandbox_dir)


@pytest.fixture(autouse=True)
def _sandbox_workspace(tmp_path, monkeypatch):
    """Point SANDBOX_WORKSPACE and SANDBOX_AUDIT_LOG to a temp dir."""
    monkeypatch.setenv("SANDBOX_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "SANDBOX_AUDIT_LOG", str(tmp_path / ".sandbox_audit.jsonl")
    )
    import mcp_server  # noqa: F811

    # Reload so module-level Path() picks up new env
    importlib.reload(mcp_server)
    yield mcp_server


def test_file_write_and_read(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.file_write("hello.py", "print('hi')")
    assert result["success"] is True

    result = srv.file_read("hello.py")
    assert result["success"] is True
    assert result["content"] == "print('hi')"


def test_file_write_creates_subdirs(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.file_write("sub/dir/app.py", "x = 1")
    assert result["success"] is True
    assert result["path"] == "sub/dir/app.py"


def test_file_write_path_traversal(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.file_write("../../etc/passwd", "evil")
    assert result["success"] is False
    assert "traversal" in result["error"].lower()


def test_file_read_not_found(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.file_read("nonexistent.py")
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_file_read_path_traversal(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.file_read("../../../etc/hosts")
    assert result["success"] is False
    assert "traversal" in result["error"].lower()


def test_list_files(_sandbox_workspace, tmp_path):
    srv = _sandbox_workspace
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")

    result = srv.list_files(".")
    assert result["success"] is True
    # audit log file might be present too
    py_files = [f for f in result["files"] if f.endswith(".py")]
    assert "a.py" in py_files
    assert "sub/b.py" in py_files


def test_list_files_path_traversal(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.list_files("../../")
    assert result["success"] is False
    assert "traversal" in result["error"].lower()


def test_run_command_echo(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.run_command(["echo", "hello"])
    assert result["success"] is True
    assert "hello" in result["stdout"]
    assert result["exit_code"] == 0
    assert "elapsed_seconds" in result


def test_run_command_blocked_rm(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.run_command(["rm", "-rf", "/"])
    assert result["success"] is False
    assert "blocked" in result["error"].lower()


def test_run_command_blocked_chmod(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.run_command(["chmod", "777", "foo"])
    assert result["success"] is False
    assert "blocked" in result["error"].lower()


def test_run_command_not_found(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.run_command(["nonexistent_cmd_xyz"])
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_run_command_nonzero_exit(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.run_command(["python", "-c", "import sys; sys.exit(1)"])
    assert result["success"] is False
    assert result["exit_code"] == 1


def test_audit_log_records_operations(_sandbox_workspace):
    srv = _sandbox_workspace
    srv.file_write("test.py", "pass")
    srv.file_read("test.py")
    srv.run_command(["echo", "hi"])

    result = srv.get_audit_log()
    assert result["success"] is True
    records = result["records"]
    assert len(records) >= 3

    tools_used = [r["tool"] for r in records]
    assert "file_write" in tools_used
    assert "file_read" in tools_used
    assert "run_command" in tools_used

    # Each record has timestamp
    for r in records:
        assert "timestamp" in r
        assert "result_summary" in r


def test_audit_log_empty_initially(_sandbox_workspace):
    srv = _sandbox_workspace
    result = srv.get_audit_log()
    assert result["success"] is True
    assert result["records"] == []
