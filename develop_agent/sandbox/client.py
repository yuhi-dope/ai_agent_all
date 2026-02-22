"""
Host-side MCP client for the Docker sandbox.
Manages container lifecycle and communicates with the MCP server
inside via STDIO transport (docker exec).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Any, Callable, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from develop_agent.config import (
    SANDBOX_CPU_LIMIT,
    SANDBOX_IMAGE,
    SANDBOX_MEMORY_LIMIT,
    SANDBOX_NETWORK,
    SANDBOX_PIDS_LIMIT,
)

logger = logging.getLogger(__name__)


class SandboxMCPClient:
    """
    Manages a Docker sandbox container and communicates with the
    MCP server inside it via STDIO transport (docker exec).

    Usage::

        async with SandboxMCPClient() as client:
            await client.write_file("main.py", "print('hello')")
            result = await client.run_command(["python", "main.py"])
            files = await client.extract_all_files()
    """

    def __init__(
        self,
        image: str = SANDBOX_IMAGE,
        memory_limit: str = SANDBOX_MEMORY_LIMIT,
        cpu_limit: str = SANDBOX_CPU_LIMIT,
        pids_limit: int = SANDBOX_PIDS_LIMIT,
        network_mode: str = SANDBOX_NETWORK,
    ):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.network_mode = network_mode
        self.container_name: Optional[str] = None
        self.session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._host_audit_log: list[dict[str, Any]] = []

    async def __aenter__(self) -> "SandboxMCPClient":
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Create and start the Docker container, then connect MCP session."""
        self.container_name = f"sandbox-{uuid.uuid4().hex[:12]}"

        # Create container (detached, with sleep to keep alive)
        create_cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.container_name,
            "--network",
            self.network_mode,
            "--memory",
            self.memory_limit,
            "--cpus",
            self.cpu_limit,
            "--pids-limit",
            str(self.pids_limit),
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=100m",
            "--tmpfs",
            "/workspace:rw,exec,size=500m,uid=1001,gid=1001",
            "--entrypoint",
            "sleep",
            self.image,
            "infinity",  # Keep container alive; we use docker exec for MCP
        ]

        proc = await asyncio.create_subprocess_exec(
            *create_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create sandbox container: {stderr.decode()}"
            )
        logger.info("Sandbox container started: %s", self.container_name)

        # Connect MCP session via docker exec
        server_params = StdioServerParameters(
            command="docker",
            args=[
                "exec",
                "-i",
                self.container_name,
                "python",
                "/opt/sandbox/mcp_server.py",
            ],
            env=None,
        )

        self._exit_stack = AsyncExitStack()
        transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        stdio_read, stdio_write = transport
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(stdio_read, stdio_write)
        )
        await self.session.initialize()

        # Verify connection
        tools_response = await self.session.list_tools()
        tool_names = [t.name for t in tools_response.tools]
        logger.info("MCP connected. Available tools: %s", tool_names)

    async def stop(self) -> None:
        """Disconnect MCP session and destroy the container."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass  # Best effort cleanup
            self._exit_stack = None
            self.session = None

        if self.container_name:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                self.container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.info(
                "Sandbox container destroyed: %s", self.container_name
            )
            self.container_name = None

    # ── Core MCP call ──────────────────────────────────────────

    async def _call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Call an MCP tool and return parsed JSON result."""
        if not self.session:
            raise RuntimeError("MCP session not connected")
        result = await self.session.call_tool(name, arguments)

        # MCP returns content as list of TextContent objects
        text_parts = []
        for content_item in result.content:
            if hasattr(content_item, "text"):
                text_parts.append(content_item.text)
        raw_text = "\n".join(text_parts)

        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            parsed = {"success": False, "raw": raw_text}

        # Host-side audit
        self._host_audit_log.append(
            {
                "tool": name,
                "arguments": arguments,
                "result_success": parsed.get("success"),
            }
        )
        return parsed

    # ── High-level convenience methods ─────────────────────────

    async def write_file(
        self, path: str, content: str
    ) -> dict[str, Any]:
        """Write a file into the sandbox workspace."""
        return await self._call_tool(
            "file_write", {"path": path, "content": content}
        )

    async def read_file(self, path: str) -> dict[str, Any]:
        """Read a file from the sandbox workspace."""
        return await self._call_tool("file_read", {"path": path})

    async def list_files(self, path: str = ".") -> dict[str, Any]:
        """List files in the sandbox workspace."""
        return await self._call_tool("list_files", {"path": path})

    async def run_command(
        self,
        command: list[str],
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Execute a command inside the sandbox."""
        return await self._call_tool(
            "run_command",
            {"command": command, "timeout_seconds": timeout_seconds},
        )

    async def get_audit_log(self) -> list[dict[str, Any]]:
        """Retrieve the audit log from inside the container."""
        result = await self._call_tool("get_audit_log", {})
        return result.get("records", [])

    async def write_generated_code(
        self,
        generated_code: dict[str, str],
        normalize_fn: Optional[Callable[[str], str]] = None,
    ) -> list[str]:
        """
        Write all generated code files into the sandbox.
        Returns list of paths that were written.
        """
        written = []
        for rel_path, content in generated_code.items():
            if normalize_fn:
                rel_path = normalize_fn(rel_path)
            if not rel_path:
                continue
            result = await self.write_file(rel_path, content)
            if result.get("success"):
                written.append(rel_path)
        return written

    async def extract_all_files(self) -> dict[str, str]:
        """
        Read all files from the sandbox workspace and return as
        dict[relative_path, content].
        """
        listing = await self.list_files(".")
        if not listing.get("success"):
            return {}
        files: dict[str, str] = {}
        for file_path in listing.get("files", []):
            # Skip audit log and hidden files
            if file_path.startswith(".sandbox_") or file_path.startswith("."):
                continue
            read_result = await self.read_file(file_path)
            if read_result.get("success"):
                files[file_path] = read_result["content"]
        return files

    @property
    def host_audit_log(self) -> list[dict[str, Any]]:
        """Return host-side audit log entries."""
        return list(self._host_audit_log)
