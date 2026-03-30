"""判断根拠の可視化 — decision_rules テーブルと execution_logs の二つのソースに対応。

- generate_decision_tree(company_id, department): decision_rules テーブルから意思決定ツリーを生成
- generate_decision_tree_from_log(execution_id, supabase): execution_logs からAI判断ステップを抽出しMermaid化
"""
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

_MAX_RULES = 20
_LABEL_MAX_LEN = 40


# ---------------------------------------------------------------------------
# Pydanticモデル
# ---------------------------------------------------------------------------

class DecisionNode(BaseModel):
    """判断ツリーのノード。"""
    id: str
    label: str
    node_type: str  # condition / action / result / hitl
    metadata: dict = Field(default_factory=dict)


class DecisionEdge(BaseModel):
    """判断ツリーのエッジ。"""
    from_node: str
    to_node: str
    label: str = ""


class DecisionTreeResult(BaseModel):
    """判断根拠ツリーの生成結果。"""
    mermaid: str
    nodes: list[DecisionNode]
    edges: list[DecisionEdge]
    source: str = "decision_rules"  # decision_rules / execution_log
    execution_id: str | None = None
    rule_count: int = 0
    model_used: str = ""
    cost_yen: float = 0.0


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _sanitize_mermaid_label(text: str) -> str:
    """Mermaid ノードラベルに使えない特殊文字を除去する。"""
    return re.sub(r'["\[\]{}|<>]', "", str(text)).strip()[:_LABEL_MAX_LEN]


def _extract_json(content: str) -> str:
    """LLMレスポンスからJSONを抽出する。オブジェクト {} を優先して抽出する。"""
    text = content.strip()
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()
    # オブジェクト {} を優先して探す（配列より前に）
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
    return text


# ---------------------------------------------------------------------------
# decision_rules テーブルからの生成（既存実装を拡張）
# ---------------------------------------------------------------------------

async def generate_decision_tree(
    company_id: str,
    department: str | None = None,
) -> dict:
    """decision_rules テーブルから Mermaid 意思決定ツリーを生成する。

    Args:
        company_id: テナントID（RLS のために必須）
        department: 部署名フィルタ（None の場合は全部署）

    Returns:
        {
            "mermaid": "flowchart TD\n    C0{条件1} ...",
            "nodes": [{"id": "C0", "label": "...", "type": "condition"}, ...],
            "edges": [{"from": "C0", "to": "A0", "label": "Yes"}, ...],
            "rule_count": 5,
        }
    """
    db = get_service_client()

    query = (
        db.table("decision_rules")
        .select("id, department, decision_name, context, logic_type, logic_definition, is_active")
        .eq("company_id", company_id)
        .eq("is_active", True)
    )
    if department:
        query = query.eq("department", department)

    result = query.order("decision_name").limit(_MAX_RULES).execute()
    rules: list[dict] = result.data or []

    if not rules:
        return {
            "mermaid": (
                "flowchart TD\n"
                "    A[意思決定ルールがありません]\n"
                "    B[decision_rules を追加してください]\n"
                "    A --> B"
            ),
            "nodes": [],
            "edges": [],
            "rule_count": 0,
        }

    nodes: list[dict] = []
    edges: list[dict] = []
    mermaid_lines: list[str] = ["flowchart TD"]

    for i, rule in enumerate(rules):
        cond_id = f"C{i}"
        act_id = f"A{i}"

        # 条件ラベル: decision_name + context を使う
        raw_condition = rule.get("decision_name") or rule.get("context") or f"条件{i + 1}"
        condition_label = _sanitize_mermaid_label(raw_condition)

        # アクションラベル: logic_definition から action を取得、なければ logic_type
        logic_def = rule.get("logic_definition") or {}
        if isinstance(logic_def, dict):
            raw_action = logic_def.get("action") or logic_def.get("then") or rule.get("logic_type") or "アクション"
        else:
            raw_action = rule.get("logic_type") or "アクション"
        action_label = _sanitize_mermaid_label(str(raw_action))

        nodes.append({"id": cond_id, "label": condition_label, "type": "condition"})
        nodes.append({"id": act_id, "label": action_label, "type": "action"})
        edges.append({"from": cond_id, "to": act_id, "label": "Yes"})

        mermaid_lines.append(f'    {cond_id}{{"{condition_label}"}}')
        mermaid_lines.append(f"    {act_id}[{action_label}]")
        mermaid_lines.append(f"    {cond_id} -->|Yes| {act_id}")

    return {
        "mermaid": "\n".join(mermaid_lines),
        "nodes": nodes,
        "edges": edges,
        "rule_count": len(rules),
    }


# ---------------------------------------------------------------------------
# execution_logs からの生成（新規実装）
# ---------------------------------------------------------------------------

_SYSTEM_DECISION_TREE = """あなたはAIシステムの判断根拠を可視化する専門家です。
実行ログから判断ステップを抽出し、Mermaid形式のフローチャートを生成してください。

## 出力形式（JSON）
{
  "mermaid": "flowchart TD\\n    A{入力データ確認} -->|OK| B[LLM分析]\\n    ...",
  "nodes": [
    {"id": "A", "label": "判断ポイント", "node_type": "condition"},
    {"id": "B", "label": "処理ステップ", "node_type": "action"},
    {"id": "H1", "label": "人間承認", "node_type": "hitl"}
  ],
  "edges": [
    {"from_node": "A", "to_node": "B", "label": "OK"}
  ]
}

## ノードタイプ
- condition: 判断・条件分岐（菱形 {} で表示）
- action: 実行ステップ（矩形 [] で表示）
- result: 最終結果（角丸 () で表示）
- hitl: 人間承認ポイント（二重矩形 [[]] で表示）

## Mermaidスタイル
- 人間承認ポイント (hitl) には style で黄色背景を付ける: style H1 fill:#FFD700,stroke:#B8860B
- エラーパスには赤色: style E1 fill:#FF6B6B,stroke:#CC0000

重要:
- 日本語ラベルを使用
- ノード数は最大15個
- Mermaid記法とJSONの両方を返す
- コードブロック（```）は使わず、JSONのみ返す
"""


async def generate_decision_tree_from_log(
    execution_id: str,
    supabase: Any,
) -> DecisionTreeResult:
    """実行ログからAIの判断根拠ツリーをMermaid形式で生成する。

    人間が「なぜAIがこの判断をしたか」を理解するためのもの。
    execution_logs.operations フィールドから判断ステップを抽出し、
    LLMがMermaid形式の判断ツリーを生成する。

    Args:
        execution_id: execution_logs テーブルのID
        supabase: Supabaseクライアント

    Returns:
        DecisionTreeResult — Mermaid形式の判断ツリー
    """
    # 1. 実行ログを取得
    log_result = supabase.table("execution_logs") \
        .select("id, company_id, operations, overall_success, triggered_by, created_at") \
        .eq("id", execution_id) \
        .limit(1) \
        .execute()

    logs = log_result.data or []
    if not logs:
        return DecisionTreeResult(
            mermaid=(
                "flowchart TD\n"
                f"    A[実行ログが見つかりません: {execution_id[:8]}...]\n"
                "    B[execution_id を確認してください]\n"
                "    A --> B"
            ),
            nodes=[],
            edges=[],
            source="execution_log",
            execution_id=execution_id,
        )

    log = logs[0]
    operations = log.get("operations") or {}
    company_id = log.get("company_id", "")

    # 2. LLMで判断ツリーを生成
    context = _build_log_context(log, operations)

    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": _SYSTEM_DECISION_TREE},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.FAST,
        task_type="decision_tree_visualization",
        company_id=company_id,
        max_tokens=2048,
        temperature=0.2,
    ))

    # 3. レスポンスをパース
    result = _parse_tree_result(response.content)
    result.source = "execution_log"
    result.execution_id = execution_id
    result.model_used = response.model_used
    result.cost_yen = response.cost_yen

    logger.info(
        "generate_decision_tree_from_log: execution_id=%s, nodes=%d, edges=%d",
        execution_id,
        len(result.nodes),
        len(result.edges),
    )

    return result


def _build_log_context(log: dict, operations: dict) -> str:
    """実行ログからコンテキスト文字列を構築する。"""
    parts = [
        "## 実行ログ\n",
        f"- 実行ID: {log.get('id', '不明')}\n",
        f"- トリガー: {log.get('triggered_by', '不明')}\n",
        f"- 成功/失敗: {'成功' if log.get('overall_success') else '失敗'}\n",
        f"- 実行日時: {log.get('created_at', '不明')}\n",
    ]

    if operations:
        parts.append("\n## 実行ステップ（operations）\n")
        ops_text = json.dumps(operations, ensure_ascii=False, indent=2)
        parts.append(ops_text[:3000])

    parts.append(
        "\n\n上記の実行ログから判断ステップを抽出し、"
        "人間が理解できるMermaid形式の判断根拠ツリーをJSON形式で出力してください。"
    )

    return "".join(parts)


def _parse_tree_result(content: str) -> DecisionTreeResult:
    """LLMレスポンスをDecisionTreeResultにパースする。"""
    try:
        text = _extract_json(content)
        data = json.loads(text)

        nodes = []
        for raw_node in data.get("nodes", []):
            try:
                nodes.append(DecisionNode(
                    id=str(raw_node.get("id", "")),
                    label=str(raw_node.get("label", ""))[:_LABEL_MAX_LEN],
                    node_type=str(raw_node.get("node_type", "action")),
                    metadata=raw_node.get("metadata", {}),
                ))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse tree node: %s", e)

        edges = []
        for raw_edge in data.get("edges", []):
            try:
                edges.append(DecisionEdge(
                    from_node=str(raw_edge.get("from_node", "")),
                    to_node=str(raw_edge.get("to_node", "")),
                    label=str(raw_edge.get("label", "")),
                ))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse tree edge: %s", e)

        mermaid = str(data.get("mermaid", ""))
        if not mermaid:
            # フォールバック: ノードから自動生成
            mermaid = _build_fallback_mermaid(nodes, edges)

        return DecisionTreeResult(
            mermaid=mermaid,
            nodes=nodes,
            edges=edges,
        )

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning("Decision tree parse failed: %s", e)
        return DecisionTreeResult(
            mermaid=(
                "flowchart TD\n"
                "    A[判断ログの解析]\n"
                "    B[データ処理]\n"
                "    C[結果出力]\n"
                "    A --> B --> C"
            ),
            nodes=[],
            edges=[],
        )


# ---------------------------------------------------------------------------
# 構造化データ → Mermaid 変換（render_decision_tree_mermaid / render_decision_tree_svg）
# ---------------------------------------------------------------------------

def _node_id_safe(raw_id: str) -> str:
    """Mermaid 互換のノードID（英数字のみ）に正規化する。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", raw_id)


def _render_node_line(node_id: str, label: str, node_type: str) -> str:
    """ノード種別に応じた Mermaid ノード行を返す。"""
    safe_id = _node_id_safe(node_id)
    safe_label = _sanitize_mermaid_label(label)
    if node_type == "decision":
        return f'    {safe_id}{{"{safe_label}"}}'
    elif node_type == "end":
        return f'    {safe_id}(["{safe_label}"])'
    elif node_type == "hitl":
        return f'    {safe_id}[["{safe_label}"]]'
    else:  # action / その他
        return f'    {safe_id}["{safe_label}"]'


def _traverse_tree(
    node: dict,
    lines: list[str],
    edges: list[str],
    visited: set[str],
    parent_id: str | None = None,
    edge_label: str = "",
) -> None:
    """決定木ノードを再帰的にトラバースして Mermaid 行を収集する。"""
    node_id = _node_id_safe(str(node.get("id", f"node_{len(visited)}")))
    label = str(node.get("label", node_id))
    node_type = str(node.get("type", "action"))

    if node_id not in visited:
        visited.add(node_id)
        lines.append(_render_node_line(node_id, label, node_type))

    if parent_id is not None:
        if edge_label:
            edges.append(f"    {_node_id_safe(parent_id)} -->|{edge_label}| {node_id}")
        else:
            edges.append(f"    {_node_id_safe(parent_id)} --> {node_id}")

    # children が条件分岐（yes/no 形式）の場合
    for child_entry in node.get("children", []):
        if not isinstance(child_entry, dict):
            continue
        condition = str(child_entry.get("condition", ""))
        yes_node = child_entry.get("yes")
        no_node = child_entry.get("no")

        if condition:
            # 条件ノードを挿入する
            cond_id = _node_id_safe(f"{node_id}_cond_{len(visited)}")
            cond_label = _sanitize_mermaid_label(condition)
            if cond_id not in visited:
                visited.add(cond_id)
                lines.append(f'    {cond_id}{{"{cond_label}"}}')
            edges.append(f"    {node_id} --> {cond_id}")
            if yes_node and isinstance(yes_node, dict):
                _traverse_tree(yes_node, lines, edges, visited, cond_id, "はい")
            if no_node and isinstance(no_node, dict):
                _traverse_tree(no_node, lines, edges, visited, cond_id, "いいえ")
        elif "id" in child_entry:
            # 通常の子ノード
            _traverse_tree(child_entry, lines, edges, visited, node_id, "")


def render_decision_tree_mermaid(tree_data: dict) -> str:
    """構造化された決定木データを Mermaid 記法に変換する。

    Args:
        tree_data: {
            "root": {
                "id": "start",
                "label": "見積依頼受付",
                "type": "action",   # "action" | "decision" | "end" | "hitl"
                "children": [
                    {
                        "condition": "金額100万以上?",
                        "yes": { ... },
                        "no": { ... },
                    }
                ]
            }
        }

    Returns:
        Mermaid 記法の文字列（graph TD 形式）。
    """
    if not isinstance(tree_data, dict):
        return "graph TD\n    A[無効なデータです]"

    root = tree_data.get("root")
    if not root or not isinstance(root, dict):
        return "graph TD\n    A[決定木データがありません]"

    node_lines: list[str] = []
    edge_lines: list[str] = []
    visited: set[str] = set()

    _traverse_tree(root, node_lines, edge_lines, visited)

    parts = ["graph TD"] + node_lines + edge_lines
    return "\n".join(parts)


def render_decision_tree_svg(tree_data: dict) -> str:
    """決定木データをフォールバック用テキスト SVG 文字列に変換する。

    Mermaid ライブラリが使用できない環境向けに、シンプルな SVG 表現を返す。
    各ノードを縦に並べ、矢印で接続する。

    Args:
        tree_data: render_decision_tree_mermaid と同形式の dict。

    Returns:
        SVG 文字列。
    """
    if not isinstance(tree_data, dict):
        return '<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">無効なデータ</text></svg>'

    root = tree_data.get("root")
    if not root or not isinstance(root, dict):
        return '<svg xmlns="http://www.w3.org/2000/svg"><text x="10" y="20">データなし</text></svg>'

    # ノードをフラットに列挙（幅優先）
    flat_nodes: list[dict] = []
    queue = [root]
    while queue:
        cur = queue.pop(0)
        if not isinstance(cur, dict):
            continue
        flat_nodes.append(cur)
        for child_entry in cur.get("children", []):
            if not isinstance(child_entry, dict):
                continue
            for key in ("yes", "no"):
                child = child_entry.get(key)
                if isinstance(child, dict):
                    queue.append(child)
            if "id" in child_entry and "label" in child_entry:
                queue.append(child_entry)

    box_w, box_h, gap_y = 200, 40, 30
    svg_width = box_w + 60
    svg_height = (box_h + gap_y) * max(len(flat_nodes), 1) + 40

    lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}">'
    ]

    for i, node in enumerate(flat_nodes):
        y = 20 + i * (box_h + gap_y)
        cx = svg_width // 2
        label = _sanitize_mermaid_label(str(node.get("label", "")))
        node_type = str(node.get("type", "action"))

        if node_type == "decision":
            # ひし形（近似: 回転した矩形）
            lines.append(
                f'<rect x="{cx - box_w // 2}" y="{y}" width="{box_w}" height="{box_h}" '
                f'fill="#FFF9C4" stroke="#F57F17" rx="4"/>'
            )
        else:
            lines.append(
                f'<rect x="{cx - box_w // 2}" y="{y}" width="{box_w}" height="{box_h}" '
                f'fill="#E3F2FD" stroke="#1565C0" rx="4"/>'
            )

        lines.append(
            f'<text x="{cx}" y="{y + box_h // 2 + 5}" text-anchor="middle" '
            f'font-size="13" font-family="sans-serif">{label}</text>'
        )

        if i > 0:
            prev_y = 20 + (i - 1) * (box_h + gap_y) + box_h
            lines.append(
                f'<line x1="{cx}" y1="{prev_y}" x2="{cx}" y2="{y}" '
                f'stroke="#555" stroke-width="1.5" marker-end="url(#arrow)"/>'
            )

    lines.append("</svg>")
    return "\n".join(lines)


def _build_fallback_mermaid(
    nodes: list[DecisionNode],
    edges: list[DecisionEdge],
) -> str:
    """ノードとエッジからMermaid文字列を構築するフォールバック。"""
    lines = ["flowchart TD"]
    hitl_ids = set()

    for node in nodes:
        label = _sanitize_mermaid_label(node.label)
        if node.node_type == "condition":
            lines.append(f'    {node.id}{{"{label}"}}')
        elif node.node_type == "result":
            lines.append(f'    {node.id}("{label}")')
        elif node.node_type == "hitl":
            lines.append(f'    {node.id}[["{label}"]]')
            hitl_ids.add(node.id)
        else:
            lines.append(f'    {node.id}["{label}"]')

    for edge in edges:
        if edge.label:
            lines.append(f'    {edge.from_node} -->|{edge.label}| {edge.to_node}')
        else:
            lines.append(f'    {edge.from_node} --> {edge.to_node}')

    # HITLノードにスタイル適用
    for hid in hitl_ids:
        lines.append(f'    style {hid} fill:#FFD700,stroke:#B8860B')

    return "\n".join(lines)
