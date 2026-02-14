"""
蓄積（runs / features）から「次に作るシステム」の提案を LLM で生成し、
data/next_system_suggestion.md に書き出す。NOTION_PROGRESS_HOPE_PAGE_ID が設定されていれば
その Notion ページの本文も進行希望として更新する。requirement への注入は呼び出し元のオプション。
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# プロジェクトルートは main で path に追加済みのため、server からは persist 経由で get_runs 等を使う
from server import persist  # noqa: E402


PROMPT_SYSTEM = """あなたはプロダクトの次の機能を提案する担当です。
これまでに実装した run の一覧と機能要約が渡されます。それらを踏まえ、次に作るシステム・機能として合理的な提案を 1〜3 個、短文で出力してください。
出力は Markdown の箇条書きで、各提案は 1〜2 文で簡潔に。余計な前置きは不要です。"""


def _usage_from_response(response) -> tuple[int, int]:
    """LLM レスポンスから input/output トークン数を返す。(input_tokens, output_tokens)"""
    usage = getattr(response, "response_metadata", None) or {}
    usage = usage.get("usage_metadata") or usage
    in_tok = int(usage.get("prompt_token_count") or usage.get("input_tokens") or 0)
    out_tok = int(usage.get("candidates_token_count") or usage.get("output_tokens") or 0)
    return in_tok, out_tok


def generate_and_save(workspace_root: Path, limit_runs: int = 10) -> tuple[Optional[str], int, int]:
    """
    Supabase から直近の runs / features を取得し、LLM で提案文を生成する。
    生成した内容を data/next_system_suggestion.md に書き出し、提案文を返す。
    戻り値: (提案文 or None, input_tokens, output_tokens)。
    蓄積が空または LLM 失敗時は (None, 0, 0) を返す。
    """
    runs = persist.get_runs(limit=limit_runs)
    if not runs:
        return None, 0, 0
    features = persist.get_features(limit=limit_runs * 2)
    run_ids = {r.get("run_id") for r in runs}
    features_by_run = {}
    for f in features:
        rid = f.get("run_id")
        if rid in run_ids:
            features_by_run.setdefault(rid, []).append(f)

    lines = ["## これまでに作ったもの\n"]
    for r in runs:
        rid = r.get("run_id") or ""
        purpose = (r.get("spec_purpose") or r.get("requirement_summary") or "-")[:200]
        lines.append(f"- **{rid}**: {purpose}")
        for feat in features_by_run.get(rid, [])[:1]:
            lines.append(f"  - 要約: {(feat.get('summary') or '-')[:150]}")
    body = "\n".join(lines)

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from develop_agent.llm.vertex import get_chat_flash
        llm = get_chat_flash()
        response = llm.invoke([
            SystemMessage(content=PROMPT_SYSTEM),
            HumanMessage(content=body),
        ])
        content = (response.content if hasattr(response, "content") else str(response)).strip()
        in_tok, out_tok = _usage_from_response(response)
        if not content:
            return None, in_tok, out_tok
    except Exception:
        return None, 0, 0

    data_dir = workspace_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "next_system_suggestion.md"
    updated = datetime.now(timezone.utc).isoformat()
    path.write_text(f"最終更新: {updated}\n\n{content}", encoding="utf-8")

    page_id = os.environ.get("NOTION_PROGRESS_HOPE_PAGE_ID", "").strip()
    if page_id:
        try:
            from server import notion_client  # noqa: E402
            notion_client.set_page_content(page_id, content)
        except Exception:
            pass
    return content, in_tok, out_tok
