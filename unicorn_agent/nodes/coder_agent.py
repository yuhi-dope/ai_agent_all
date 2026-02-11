"""Coder Agent: 設計書に基づきコードを生成。Gemini 1.5 Flash。File Filter 適用・パス正規化。"""
from __future__ import annotations

from pathlib import Path

from unicorn_agent.state import AgentState
from unicorn_agent.llm.vertex import get_chat_flash
from unicorn_agent.utils.file_filter import filter_readable_files
from unicorn_agent.utils.rule_loader import load_rule
from langchain_core.messages import HumanMessage, SystemMessage

from unicorn_agent.config import FILE_SIZE_LIMIT_BYTES


def _normalize_path(path: str) -> str:
    """相対パスを正規化（.. を解消、スラッシュ統一、先頭の / 除去）。"""
    path = path.replace("\\", "/").strip()
    if path.startswith("/"):
        path = path[1:]
    parts = []
    for p in path.split("/"):
        if p == "..":
            if parts:
                parts.pop()
        elif p and p != ".":
            parts.append(p)
    return "/".join(parts) if parts else path


def _read_context(workspace_root: str) -> str:
    """File Filter を適用した上で、読み込んでよいファイルの内容を連結して返す。"""
    allowed = filter_readable_files(workspace_root, size_limit=FILE_SIZE_LIMIT_BYTES)
    base = Path(workspace_root)
    parts = []
    for rel in allowed:
        try:
            content = (base / rel).read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {rel} ---\n{content}")
        except OSError:
            continue
    return "\n\n".join(parts) if parts else "(既存ファイルなし)"


def _parse_generated_files(text: str) -> dict[str, str]:
    """
    LLM 出力から「ファイルパス + 内容」を抽出。
    ```path/to/file.py または --- path/to/file.py --- のブロックを想定。
    """
    import re
    out: dict[str, str] = {}
    # パターン: ```rel/path.ext または --- rel/path.ext --- の次から次の ``` や --- まで
    block_start = re.compile(r"^```\s*(\S+)\s*$|^---\s*(\S+)\s*---\s*$", re.MULTILINE)
    pos = 0
    while True:
        m = block_start.search(text, pos)
        if not m:
            break
        path = (m.group(1) or m.group(2) or "").strip()
        if not path:
            pos = m.end()
            continue
        path = _normalize_path(path)
        start = m.end()
        # 次の ``` または --- を探す
        next_block = re.search(r"^\s*```|^\s*---", text[start:], re.MULTILINE)
        end = start + next_block.start() if next_block else len(text)
        content = text[start:end].strip()
        if path:
            out[path] = content
        pos = end
    return out


CODER_SYSTEM = """あなたは実装担当のエンジニアです。設計書（Markdown）と既存コードを踏まえ、必要なコードを生成してください。
出力形式:
- 各ファイルごとに、1行目に ```相対パス または --- 相対パス --- と書き、2行目以降にそのファイルの内容を書く。
- 相対パスはプロジェクトルートからのパス（例: src/main.py, package.json）とすること。
- 複数ファイルを出す場合は、ファイルごとに上記のブロックを繰り返す。
- 説明文は不要。コードのみ出力すること。"""


def coder_agent_node(state: AgentState) -> dict:
    """spec_markdown と（File Filter 済み）既存コードから generated_code を更新。"""
    spec = state.get("spec_markdown") or ""
    fix_instruction = state.get("fix_instruction") or ""
    workspace_root = state.get("workspace_root") or "."
    rules_dir_name = state.get("rules_dir") or "rules"
    rules_dir = Path(workspace_root) / rules_dir_name
    coder_prompt = load_rule(rules_dir, "coder_rules", CODER_SYSTEM)

    context = _read_context(workspace_root)

    user_content = f"""## 設計書\n{spec}\n\n## 既存コード（参考）\n{context}"""
    if fix_instruction:
        user_content += f"\n\n## 修正指示（必ず反映すること）\n{fix_instruction}"

    llm = get_chat_flash()
    messages = [
        SystemMessage(content=coder_prompt),
        HumanMessage(content=user_content),
    ]
    response = llm.invoke(messages)
    raw = response.content if hasattr(response, "content") else str(response)

    generated = _parse_generated_files(raw)
    # キーを正規化し、必要なディレクトリは後段（review で書き出す際）で自動作成する
    normalized: dict[str, str] = {}
    for k, v in generated.items():
        normalized[_normalize_path(k)] = v

    out: dict = {
        "generated_code": normalized,
        "status": "coding",
    }
    if state.get("output_rules_improvement"):
        files_list = ", ".join(normalized.keys()) if normalized else "(なし)"
        out["coder_rules_improvement"] = (
            f"# Coder フェーズ 改善・追加ルール案\n\n"
            f"## 設計書要約（先頭200文字）\n{spec[:200]}...\n\n"
            f"## 生成ファイル一覧\n{files_list}\n\n"
            f"## coder_rules.md への追加推奨\n"
            f"プロジェクトで定型的な import 順やフォーマット方針があれば、出力形式の前に記載してください。\n"
        )
    return out
