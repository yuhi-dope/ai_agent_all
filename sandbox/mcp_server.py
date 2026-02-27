"""
MCP Server for sandbox code execution environment.
Runs inside Docker container, exposes file and command tools via STDIO.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

WORKSPACE = Path(os.environ.get("SANDBOX_WORKSPACE", "/workspace"))
AUDIT_LOG_PATH = Path(
    os.environ.get("SANDBOX_AUDIT_LOG", "/workspace/.sandbox_audit.jsonl")
)
MAX_OUTPUT_BYTES = 50_000  # Truncate command output at 50KB

mcp = FastMCP(name="SandboxMCP")

# ── Blocked commands ───────────────────────────────────────────
BLOCKED_COMMANDS = frozenset(
    {"rm", "chmod", "chown", "kill", "pkill", "dd", "mkfs", "mount", "umount"}
)


# ── Audit logging ──────────────────────────────────────────────


def _audit(
    tool_name: str, arguments: dict[str, Any], result: dict[str, Any]
) -> None:
    """Append one audit record as a JSON line."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "arguments": arguments,
        "result_summary": {
            "success": result.get("success"),
            "error": result.get("error"),
        },
    }
    try:
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Best-effort logging


def _resolve_safe_path(rel_path: str) -> Path | None:
    """Resolve a relative path within WORKSPACE. Returns None if path escapes."""
    target = (WORKSPACE / rel_path).resolve()
    if not str(target).startswith(str(WORKSPACE.resolve())):
        return None
    return target


# ── Tools ──────────────────────────────────────────────────────


@mcp.tool()
def file_write(path: str, content: str) -> dict[str, Any]:
    """
    Write content to a file at the given relative path inside /workspace.
    Creates parent directories automatically. Returns success status.
    """
    args = {"path": path, "content_length": len(content)}
    target = _resolve_safe_path(path)
    if target is None:
        result: dict[str, Any] = {
            "success": False,
            "error": "Path traversal denied",
        }
        _audit("file_write", args, result)
        return result
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result = {
            "success": True,
            "path": str(target.relative_to(WORKSPACE)),
        }
    except Exception as e:
        result = {"success": False, "error": str(e)}
    _audit("file_write", args, result)
    return result


@mcp.tool()
def file_read(path: str) -> dict[str, Any]:
    """Read content from a file at the given relative path inside /workspace."""
    args = {"path": path}
    target = _resolve_safe_path(path)
    if target is None:
        result: dict[str, Any] = {
            "success": False,
            "error": "Path traversal denied",
        }
        _audit("file_read", args, result)
        return result
    try:
        if not target.is_file():
            result = {"success": False, "error": "File not found"}
            _audit("file_read", args, result)
            return result
        content = target.read_text(encoding="utf-8", errors="replace")
        result = {"success": True, "content": content}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    _audit("file_read", args, result)
    return result


@mcp.tool()
def list_files(path: str = ".") -> dict[str, Any]:
    """List files recursively under the given relative directory inside /workspace."""
    args = {"path": path}
    target = _resolve_safe_path(path)
    if target is None:
        result: dict[str, Any] = {
            "success": False,
            "error": "Path traversal denied",
        }
        _audit("list_files", args, result)
        return result
    try:
        files = []
        for p in sorted(target.rglob("*")):
            if p.is_file():
                files.append(str(p.relative_to(WORKSPACE)))
        result = {"success": True, "files": files}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    _audit("list_files", args, result)
    return result


@mcp.tool()
def run_command(
    command: list[str], timeout_seconds: int = 120
) -> dict[str, Any]:
    """
    Execute a command inside the sandbox workspace.
    Command is a list of strings (e.g. ["ruff", "check", "."]).
    Returns exit code, stdout, stderr. Output is truncated at 50KB.
    """
    args = {"command": command, "timeout_seconds": timeout_seconds}

    # Block dangerous commands
    if command and command[0] in BLOCKED_COMMANDS:
        result: dict[str, Any] = {
            "success": False,
            "error": f"Command '{command[0]}' is blocked",
        }
        _audit("run_command", args, result)
        return result

    try:
        t0 = time.monotonic()
        proc = subprocess.run(
            command,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=min(timeout_seconds, 600),  # Hard cap at 10 min
        )
        elapsed = round(time.monotonic() - t0, 2)
        stdout = proc.stdout[:MAX_OUTPUT_BYTES] if proc.stdout else ""
        stderr = proc.stderr[:MAX_OUTPUT_BYTES] if proc.stderr else ""
        result = {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": elapsed,
        }
    except subprocess.TimeoutExpired:
        result = {
            "success": False,
            "error": f"Timeout after {timeout_seconds}s",
        }
    except FileNotFoundError:
        result = {
            "success": False,
            "error": f"Command not found: {command[0] if command else '(empty)'}",
        }
    except Exception as e:
        result = {"success": False, "error": str(e)}
    _audit("run_command", args, result)
    return result


@mcp.tool()
def get_audit_log() -> dict[str, Any]:
    """Return the full audit log as a list of records."""
    try:
        if not AUDIT_LOG_PATH.exists():
            return {"success": True, "records": []}
        lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        records = [json.loads(line) for line in lines if line.strip()]
        return {"success": True, "records": records}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import sys

    transport = "stdio"
    for arg in sys.argv[1:]:
        if arg.startswith("--transport="):
            transport = arg.split("=", 1)[1]
        elif arg == "--http":
            transport = "streamable-http"

    if transport == "streamable-http":
        port = int(os.environ.get("MCP_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")
