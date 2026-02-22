"""Tests for guardrails_sandbox.py with mocked MCP client."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from develop_agent.utils.guardrails_sandbox import (
    run_e2e_test_sandbox,
    run_lint_build_check_sandbox,
    run_unit_test_sandbox,
)


def _make_mock_client(**read_files: str) -> AsyncMock:
    """Create a mock SandboxMCPClient with pre-configured file reads."""
    client = AsyncMock()

    async def mock_read_file(path: str):
        if path in read_files:
            return {"success": True, "content": read_files[path]}
        return {"success": False, "error": "not found"}

    client.read_file = mock_read_file
    return client


# ── Lint/Build ─────────────────────────────────────────────────


class TestLintBuildSandbox:
    def test_python_lint_pass(self):
        client = _make_mock_client()
        client.run_command = AsyncMock(
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"main.py": "x = 1"})
        )
        assert result.passed is True

    def test_python_lint_fail(self):
        client = _make_mock_client()
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "exit_code": 1,
                "stdout": "E501 line too long",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"main.py": "x = 1"})
        )
        assert result.passed is False
        assert any("ruff" in f.lower() for f in result.findings)

    def test_skip_when_ruff_not_found(self):
        client = _make_mock_client()
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "error": "Command not found: ruff",
            }
        )
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"main.py": "x = 1"})
        )
        # ruff not found should be gracefully skipped
        assert result.passed is True

    def test_no_py_no_js(self):
        client = _make_mock_client()
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"readme.md": "# Hello"})
        )
        assert result.passed is True

    def test_js_build_pass(self):
        pkg = json.dumps({"name": "test", "scripts": {"build": "tsc"}})
        client = _make_mock_client(**{"package.json": pkg})
        client.run_command = AsyncMock(
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"index.js": "console.log(1)"})
        )
        assert result.passed is True

    def test_js_build_fail(self):
        pkg = json.dumps({"name": "test", "scripts": {"build": "tsc"}})
        client = _make_mock_client(**{"package.json": pkg})
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "TS2322: Type error",
            }
        )
        result = asyncio.run(
            run_lint_build_check_sandbox(client, {"index.ts": "const x: number = 'a'"})
        )
        assert result.passed is False
        assert any("npm run build" in f for f in result.findings)


# ── Unit Test ──────────────────────────────────────────────────


class TestUnitTestSandbox:
    def test_pytest_pass(self):
        client = _make_mock_client()
        client.run_command = AsyncMock(
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "1 passed",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_unit_test_sandbox(client, {"test_main.py": "def test_x(): pass"})
        )
        assert result.passed is True

    def test_pytest_fail(self):
        client = _make_mock_client()
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "exit_code": 1,
                "stdout": "1 failed",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_unit_test_sandbox(client, {"test_main.py": "def test_x(): assert False"})
        )
        assert result.passed is False
        assert any("pytest" in f for f in result.findings)

    def test_js_test_skip_no_script(self):
        pkg = json.dumps({"name": "test", "scripts": {}})
        client = _make_mock_client(**{"package.json": pkg})
        client.run_command = AsyncMock()
        result = asyncio.run(
            run_unit_test_sandbox(client, {"index.js": "module.exports = {}"})
        )
        assert result.passed is True
        client.run_command.assert_not_called()

    def test_js_test_runs_when_script_present(self):
        pkg = json.dumps({"name": "test", "scripts": {"test": "jest"}})
        client = _make_mock_client(**{"package.json": pkg})
        client.run_command = AsyncMock(
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "Tests passed",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_unit_test_sandbox(client, {"index.js": "module.exports = {}"})
        )
        assert result.passed is True


# ── E2E Test ───────────────────────────────────────────────────


class TestE2ETestSandbox:
    def test_skip_no_playwright(self):
        client = _make_mock_client()
        result = asyncio.run(
            run_e2e_test_sandbox(client, {"main.py": "pass"})
        )
        assert result.passed is True

    def test_runs_when_config_exists(self):
        client = _make_mock_client(
            **{"playwright.config.ts": "export default {}"}
        )
        client.run_command = AsyncMock(
            return_value={
                "success": True,
                "exit_code": 0,
                "stdout": "1 passed",
                "stderr": "",
            }
        )
        result = asyncio.run(
            run_e2e_test_sandbox(client, {"e2e/test.spec.ts": "test('x', () => {})"})
        )
        assert result.passed is True

    def test_fails_when_playwright_fails(self):
        client = _make_mock_client(
            **{"playwright.config.js": "module.exports = {}"}
        )
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "1 failed",
            }
        )
        result = asyncio.run(
            run_e2e_test_sandbox(client, {"e2e/test.spec.ts": "test('x', () => {})"})
        )
        assert result.passed is False
        assert any("playwright" in f for f in result.findings)

    def test_skip_when_playwright_not_found(self):
        client = _make_mock_client(
            **{"playwright.config.ts": "export default {}"}
        )
        client.run_command = AsyncMock(
            return_value={
                "success": False,
                "error": "Command not found: npx",
            }
        )
        result = asyncio.run(
            run_e2e_test_sandbox(client, {"main.py": "pass"})
        )
        assert result.passed is True
