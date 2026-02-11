"""
ガードレール: シークレットスキャン、高エントロピー検出、Lint/Build 実行。
実行はサンドボックス（Docker/DevContainer）内で行う前提。
"""
from __future__ import annotations

import re
import subprocess
import math
from pathlib import Path
from typing import NamedTuple


# --- Secret Scan パターン（develop_agent に合わせる） ---
SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI-style key (sk-...)"),
    (r"sbp_[a-zA-Z0-9]+", "Stripe-style key (sbp_...)"),
    (r"API_KEY\s*=\s*[\"'][^\"']+[\"']", "API_KEY assignment"),
    (r"(?:password|secret|token)\s*=\s*[\"'][^\"']+[\"']", "password/secret/token assignment"),
    (r"SUPABASE_KEY\s*=\s*[\"'][^\"']+[\"']", "SUPABASE_KEY assignment"),
    (r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer token"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "Private key block"),
]

# 高エントロピー: 英数字のみの長い文字列（最小長・閾値）
HIGH_ENTROPY_MIN_LEN = 24
HIGH_ENTROPY_THRESHOLD = 4.0  # 1文字あたりのビット数おおよそ


def _entropy(s: str) -> float:
    """シャノンエントロピー（ビット）。"""
    if not s:
        return 0.0
    from collections import Counter
    n = len(s)
    counts = Counter(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _find_high_entropy(content: str, min_len: int = HIGH_ENTROPY_MIN_LEN) -> list[str]:
    """英数字の連続で高エントロピーな部分を検出。"""
    # 英数字の連続ブロック
    pattern = re.compile(r"[a-zA-Z0-9]{" + str(min_len) + r",}")
    findings = []
    for m in pattern.finditer(content):
        segment = m.group(0)
        if _entropy(segment) >= HIGH_ENTROPY_THRESHOLD:
            # マスクして報告（実際の値は出さない）
            findings.append(f"high_entropy_string (len={len(segment)}) at position {m.start()}")
    return findings


class ScanResult(NamedTuple):
    passed: bool
    findings: list[str]


def run_secret_scan(generated_code: dict[str, str]) -> ScanResult:
    """
    生成コード全体に対してシークレットスキャンを行う。
    1 件でも検出したら passed=False。
    """
    findings: list[str] = []

    for file_path, content in generated_code.items():
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, content):
                findings.append(f"[{file_path}] {label}")

        high = _find_high_entropy(content)
        for h in high:
            findings.append(f"[{file_path}] {h}")

    return ScanResult(passed=len(findings) == 0, findings=findings)


def run_lint_build_check(
    work_dir: str | Path,
    generated_code: dict[str, str],
) -> ScanResult:
    """
    作業ディレクトリで Lint / Build を実行する。
    サンドボックス（Docker/DevContainer）内で実行することを前提とする。
    プロジェクト種別は簡易判定: py ファイルがあれば ruff、package.json があれば npm run build。
    """
    work_dir = Path(work_dir)
    findings: list[str] = []

    has_py = any(p.endswith(".py") for p in generated_code) or (work_dir / "requirements.txt").exists()
    has_js = (work_dir / "package.json").exists()

    if has_py:
        try:
            r = subprocess.run(
                ["ruff", "check", "."],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode != 0 and r.stderr:
                findings.append(f"ruff: {r.stderr[:2000]}")
            if r.returncode != 0 and r.stdout:
                findings.append(f"ruff stdout: {r.stdout[:2000]}")
        except FileNotFoundError:
            # ruff が無い環境ではスキップ（DevContainer で入れる想定）
            pass
        except subprocess.TimeoutExpired:
            findings.append("ruff: timeout (120s)")

    if has_js:
        try:
            r = subprocess.run(
                ["npm", "run", "build"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if r.returncode != 0:
                findings.append(f"npm run build: {r.stderr[:2000] or r.stdout[:2000]}")
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            findings.append("npm run build: timeout (180s)")

    if not has_py and not has_js:
        # どちらもない場合はパス（何も実行しない）
        return ScanResult(passed=True, findings=[])

    return ScanResult(passed=len(findings) == 0, findings=findings)
