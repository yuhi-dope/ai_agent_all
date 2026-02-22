"""Integration tests for SandboxMCPClient (requires Docker daemon).

These tests are marked with ``@pytest.mark.integration`` so they can be
skipped in CI without Docker::

    pytest tests/test_sandbox_client.py -m integration -v
"""
from __future__ import annotations

import asyncio
import shutil

import pytest

from develop_agent.sandbox.client import SandboxMCPClient


def docker_available() -> bool:
    """Check if Docker CLI is available."""
    return shutil.which("docker") is not None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available(), reason="Docker not available"),
]


class TestSandboxMCPClient:
    """All tests create/destroy a real Docker container."""

    def test_write_and_read_file(self):
        async def _test():
            async with SandboxMCPClient() as client:
                w = await client.write_file("test.py", "print('hello')")
                assert w["success"] is True
                r = await client.read_file("test.py")
                assert r["success"] is True
                assert r["content"] == "print('hello')"

        asyncio.run(_test())

    def test_run_command(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file("hello.py", "print('world')")
                r = await client.run_command(["python", "hello.py"])
                assert r["success"] is True
                assert "world" in r["stdout"]

        asyncio.run(_test())

    def test_ruff_check(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file(
                    "clean.py", "def hello():\n    return 1\n"
                )
                r = await client.run_command(["ruff", "check", "clean.py"])
                assert r["success"] is True

        asyncio.run(_test())

    def test_list_files(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file("a.py", "# a")
                await client.write_file("sub/b.py", "# b")
                listing = await client.list_files(".")
                assert listing["success"] is True
                files = listing["files"]
                py_files = [f for f in files if f.endswith(".py")]
                assert "a.py" in py_files
                assert "sub/b.py" in py_files

        asyncio.run(_test())

    def test_extract_all_files(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file("a.py", "# a")
                await client.write_file("sub/b.py", "# b")
                files = await client.extract_all_files()
                assert "a.py" in files
                assert "sub/b.py" in files
                assert files["a.py"] == "# a"

        asyncio.run(_test())

    def test_audit_log(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file("x.py", "pass")
                log = await client.get_audit_log()
                assert len(log) >= 1
                assert log[0]["tool"] == "file_write"

        asyncio.run(_test())

    def test_host_audit_log(self):
        async def _test():
            async with SandboxMCPClient() as client:
                await client.write_file("x.py", "pass")
                await client.read_file("x.py")
                host_log = client.host_audit_log
                assert len(host_log) == 2
                assert host_log[0]["tool"] == "file_write"
                assert host_log[1]["tool"] == "file_read"

        asyncio.run(_test())

    def test_write_generated_code(self):
        async def _test():
            code = {
                "main.py": "print('hello')",
                "lib/utils.py": "def add(a, b): return a + b",
            }
            async with SandboxMCPClient() as client:
                written = await client.write_generated_code(code)
                assert "main.py" in written
                assert "lib/utils.py" in written
                files = await client.extract_all_files()
                assert files["main.py"] == "print('hello')"

        asyncio.run(_test())

    def test_container_destroyed_on_exit(self):
        async def _test():
            client = SandboxMCPClient()
            await client.start()
            name = client.container_name
            assert name is not None
            await client.stop()
            # Container should not exist after stop
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            assert proc.returncode != 0  # Container not found

        asyncio.run(_test())

    def test_container_destroyed_on_error(self):
        async def _test():
            container_name = None
            try:
                async with SandboxMCPClient() as client:
                    container_name = client.container_name
                    raise RuntimeError("Simulated failure")
            except RuntimeError:
                pass
            # Container should still be cleaned up
            if container_name:
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "inspect",
                    container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                assert proc.returncode != 0

        asyncio.run(_test())
