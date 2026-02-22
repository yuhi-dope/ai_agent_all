"""
Sandboxed guardrails: lint, build, test, E2E via MCP client.
Replaces subprocess-based functions from guardrails.py.

Pure-Python functions (run_secret_scan, count_generated_code_lines) are
re-exported from the original guardrails module.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from develop_agent.config import (
    E2E_TEST_TIMEOUT_SECONDS,
    UNIT_TEST_TIMEOUT_SECONDS,
)

# Re-export pure-Python functions that don't need sandbox
from develop_agent.utils.guardrails import (  # noqa: F401
    ScanResult,
    count_generated_code_lines,
    run_secret_scan,
)

if TYPE_CHECKING:
    from develop_agent.sandbox.client import SandboxMCPClient


async def run_lint_build_check_sandbox(
    client: "SandboxMCPClient",
    generated_code: dict[str, str],
) -> ScanResult:
    """Run lint/build inside sandbox via MCP."""
    findings: list[str] = []

    has_py = any(p.endswith(".py") for p in generated_code)
    # Check for package.json inside sandbox
    pkg_result = await client.read_file("package.json")
    has_js = pkg_result.get("success", False)

    if has_py:
        result = await client.run_command(
            ["ruff", "check", "."], timeout_seconds=120
        )
        if not result.get("success"):
            error = result.get("error", "")
            stderr = result.get("stderr", "")
            stdout = result.get("stdout", "")
            if error and "not found" in error.lower():
                pass  # ruff not installed, skip
            elif error:
                findings.append(f"ruff: {error}")
            elif stderr:
                findings.append(f"ruff: {stderr[:2000]}")
            elif stdout:
                findings.append(f"ruff stdout: {stdout[:2000]}")

    if has_js:
        result = await client.run_command(
            ["npm", "run", "build"], timeout_seconds=180
        )
        if not result.get("success"):
            error = result.get("error", "")
            stderr = result.get("stderr", "")
            stdout = result.get("stdout", "")
            if error and "not found" in error.lower():
                pass  # npm not installed, skip
            elif error:
                findings.append(f"npm run build: {error}")
            else:
                findings.append(
                    f"npm run build: {stderr[:2000] or stdout[:2000]}"
                )

    if not has_py and not has_js:
        return ScanResult(passed=True, findings=[])

    return ScanResult(passed=len(findings) == 0, findings=findings)


async def run_unit_test_sandbox(
    client: "SandboxMCPClient",
    generated_code: dict[str, str],
) -> ScanResult:
    """Run unit tests inside sandbox via MCP."""
    findings: list[str] = []

    has_py = any(p.endswith(".py") for p in generated_code)
    pkg_result = await client.read_file("package.json")
    has_js = pkg_result.get("success", False)

    if has_py:
        result = await client.run_command(
            ["pytest", "-q", "--tb=short"],
            timeout_seconds=UNIT_TEST_TIMEOUT_SECONDS,
        )
        if not result.get("success"):
            error = result.get("error", "")
            if error and "not found" in error.lower():
                pass  # pytest not available, skip
            else:
                stderr = result.get("stderr", "")
                stdout = result.get("stdout", "")
                findings.append(
                    f"pytest: {stderr[:2000] or stdout[:2000] or error}"
                )

    if has_js and pkg_result.get("success"):
        try:
            pkg = json.loads(pkg_result.get("content", "{}"))
            scripts = pkg.get("scripts") or {}
            if "test" in scripts or "test:unit" in scripts:
                cmd = (
                    ["npm", "run", "test"]
                    if "test" in scripts
                    else ["npm", "run", "test:unit"]
                )
                result = await client.run_command(
                    cmd, timeout_seconds=UNIT_TEST_TIMEOUT_SECONDS
                )
                if not result.get("success"):
                    stderr = result.get("stderr", "")
                    stdout = result.get("stdout", "")
                    findings.append(
                        f"npm test: {stderr[:2000] or stdout[:2000]}"
                    )
        except (json.JSONDecodeError, KeyError):
            pass

    if not has_py and not has_js:
        return ScanResult(passed=True, findings=[])

    return ScanResult(passed=len(findings) == 0, findings=findings)


async def run_e2e_test_sandbox(
    client: "SandboxMCPClient",
    generated_code: dict[str, str],
) -> ScanResult:
    """Run E2E (Playwright) tests inside sandbox via MCP."""
    findings: list[str] = []

    # Check for playwright config
    has_config = False
    for config_name in (
        "playwright.config.ts",
        "playwright.config.js",
        "playwright.config.mjs",
    ):
        r = await client.read_file(config_name)
        if r.get("success"):
            has_config = True
            break

    has_script = False
    pkg_result = await client.read_file("package.json")
    if pkg_result.get("success"):
        try:
            pkg = json.loads(pkg_result.get("content", "{}"))
            scripts = " ".join((pkg.get("scripts") or {}).values())
            if "playwright" in scripts.lower():
                has_script = True
        except (json.JSONDecodeError, KeyError):
            pass

    if not has_config and not has_script:
        return ScanResult(passed=True, findings=[])

    result = await client.run_command(
        ["npx", "playwright", "test"],
        timeout_seconds=E2E_TEST_TIMEOUT_SECONDS,
    )
    if not result.get("success"):
        error = result.get("error", "")
        if error and "not found" in error.lower():
            return ScanResult(passed=True, findings=[])
        stderr = result.get("stderr", "")
        stdout = result.get("stdout", "")
        findings.append(
            f"playwright: {stderr[:2000] or stdout[:2000] or error}"
        )

    return ScanResult(passed=len(findings) == 0, findings=findings)
