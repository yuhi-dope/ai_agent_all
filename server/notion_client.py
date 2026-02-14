"""
Notion ページ本文を取得するクライアント。
NOTION_API_KEY が設定された環境で、ページ ID を渡すと blocks の plain_text を連結して返す。
"""

import os
from typing import Any, List, Optional

from notion_client import Client


def _block_plain_text(block: dict[str, Any]) -> str:
    """1 ブロックから rich_text の plain_text を連結して返す。"""
    block_type = block.get("type")
    if not block_type:
        return ""
    content = block.get(block_type) or {}
    rich_text: List[dict] = content.get("rich_text") or []
    return "".join(r.get("plain_text", "") for r in rich_text)


def _normalize_page_id(page_id: str) -> str:
    """ハイフンなし 32 文字の場合は UUID 形式（8-4-4-4-12）に正規化する。"""
    s = page_id.strip().replace("-", "")
    if len(s) != 32:
        return page_id
    return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"


def fetch_page_content(page_id: str) -> str:
    """
    Notion のページ ID に対し、Retrieve block children で子ブロックを
    ページネーション取得し、各ブロックの rich_text の plain_text を改行で連結して返す。
    再帰は行わず、トップレベルのブロックのみ取得する。
    404/403 等は呼び出し元で捕捉して 400 に変換すること。
    """
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        raise ValueError("NOTION_API_KEY is not set")

    client = Client(auth=api_key)
    block_id = _normalize_page_id(page_id)
    lines: List[str] = []
    cursor: str | None = None
    page_size = 100

    while True:
        params: dict[str, Any] = {"block_id": block_id, "page_size": page_size}
        if cursor is not None:
            params["start_cursor"] = cursor

        response = client.blocks.children.list(**params)
        results: List[dict] = response.get("results") or []
        next_cursor = response.get("next_cursor")

        for block in results:
            line = _block_plain_text(block)
            if line:
                lines.append(line)

        if not next_cursor:
            break
        cursor = next_cursor

    return "\n".join(lines)


def _get_client() -> Client:
    """NOTION_API_KEY で Client を返す。未設定時は ValueError。"""
    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        raise ValueError("NOTION_API_KEY is not set")
    return Client(auth=api_key)


def query_pages_by_status(
    database_id: str,
    status_value: str = "実装希望",
) -> List[dict]:
    """
    Notion データベースをクエリし、指定したステータス（Select プロパティ「ステータス」）のページ一覧を返す。
    返却: list[dict]。各要素は Notion の page オブジェクト（id, properties 等を含む）。
    """
    client = _get_client()
    s = database_id.strip().replace("-", "")
    db_id = _normalize_page_id(s) if len(s) == 32 else database_id.strip()
    response = client.databases.query(
        database_id=db_id,
        filter={
            "property": "ステータス",
            "select": {"equals": status_value},
        },
    )
    return list(response.get("results") or [])


def _extract_rich_text(prop_value: Any) -> str:
    """Notion の rich_text プロパティから plain text を連結して返す。"""
    if not isinstance(prop_value, dict):
        return ""
    rich_text: List[dict] = prop_value.get("rich_text") or []
    return "".join(r.get("plain_text", "") for r in rich_text)


def get_page_properties(page_id: str) -> Optional[dict]:
    """
    Notion のページを取得し、properties を返す。
    取得失敗時は None。Webhook 等でページのステータスを確認するために使う。
    """
    try:
        client = _get_client()
        pid = _normalize_page_id(page_id.strip().replace("-", ""))
        if len(pid.replace("-", "")) != 32:
            return None
        page = client.pages.retrieve(page_id=pid)
        return (page or {}).get("properties") if isinstance(page, dict) else None
    except Exception:
        return None


def get_select_property(properties: dict, key: str) -> Optional[str]:
    """
    Notion ページの properties から Select プロパティの値を返す。
    キーに対応するプロパティが Select でなければ None。未設定も None。
    """
    if not isinstance(properties, dict) or key not in properties:
        return None
    prop = properties.get(key)
    if not isinstance(prop, dict) or prop.get("type") != "select":
        return None
    select = prop.get("select")
    if not select or not isinstance(select, dict):
        return None
    name = select.get("name")
    return str(name).strip() if name else None


def get_requirement_from_page(page_id: str, properties: dict) -> str:
    """
    ページの要件を取得する。properties に「要件」Rich text があればその内容を返す。
    なければ fetch_page_content(page_id) でページ本文を返す。
    """
    if isinstance(properties, dict) and "要件" in properties:
        text = _extract_rich_text(properties["要件"])
        if text.strip():
            return text.strip()
    return fetch_page_content(page_id).strip()


def update_page_status(
    page_id: str,
    status: str,
    run_id: Optional[str] = None,
    pr_url: Optional[str] = None,
) -> None:
    """
    Notion ページの「ステータス」「run_id」「PR URL」を更新する。
    プロパティ名はテンプレート仕様に合わせる（ステータス / run_id / PR URL）。
    """
    client = _get_client()
    pid = _normalize_page_id(page_id)
    props: dict = {"ステータス": {"select": {"name": status}}}
    if run_id is not None:
        props["run_id"] = {"rich_text": [{"text": {"content": run_id[:2000]}}]}
    if pr_url is not None:
        props["PR URL"] = {"url": pr_url[:2000] if pr_url else None}
    client.pages.update(page_id=pid, properties=props)


# Notion rich_text 1 要素あたりの最大文字数
_NOTION_RICH_TEXT_MAX = 2000


def _chunk_text(text: str, max_len: int = _NOTION_RICH_TEXT_MAX) -> List[str]:
    """テキストを max_len 以下に分割する。改行はなるべく境界にする。"""
    if not text:
        return []
    chunks: List[str] = []
    rest = text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        segment = rest[:max_len]
        last_nl = segment.rfind("\n")
        if last_nl > 0:
            chunks.append(segment[: last_nl + 1])
            rest = rest[last_nl + 1 :]
        else:
            chunks.append(segment)
            rest = rest[max_len:]
    return chunks


def set_page_content(page_id: str, content: str) -> None:
    """
    指定 Notion ページの本文を content で置き換える。
    既存の子ブロックを削除してから、content を段落ブロックで追加する。
    rich_text は 2000 文字制限のため分割して追加する。
    NOTION_API_KEY 未設定・ページ不存在・権限不足時は例外を投げる。
    """
    client = _get_client()
    pid = _normalize_page_id(page_id)
    cursor: Optional[str] = None
    page_size = 100
    while True:
        params: dict[str, Any] = {"block_id": pid, "page_size": page_size}
        if cursor is not None:
            params["start_cursor"] = cursor
        response = client.blocks.children.list(**params)
        results: List[dict] = response.get("results") or []
        for block in results:
            bid = block.get("id")
            if bid:
                try:
                    client.blocks.delete(block_id=bid)
                except Exception:
                    pass
        next_cursor = response.get("next_cursor")
        if not next_cursor:
            break
        cursor = next_cursor
    chunks = _chunk_text(content)
    if not chunks:
        return
    # Notion API は 1 回あたり最大 100 ブロックまで
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        children: List[dict] = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": c}}],
                },
            }
            for c in batch
        ]
        client.blocks.children.append(block_id=pid, children=children)
