"""業務フロー図生成モジュール。

- generate_process_flow(knowledge_items, flow_type, company_id): ナレッジから業務フローをMermaid生成
- generate_flow_diagram(pipeline_name, execution_id, supabase): パイプライン定義からフロー図を生成

ルール:
- item_type="flow" のナレッジが 3件以上 → LLM (ModelTier.FAST) で Mermaid 生成
- 3件未満 → シンプルなリスト形式の Mermaid を返す（LLM 不使用）
- generate_flow_diagram: パイプライン定義 + 実行状態を色分けしてMermaid生成
"""
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

_FLOW_TYPES = {"flowchart", "sequence", "stateDiagram"}

# パイプラインステップの実行状態
_STEP_STATUS_STYLES = {
    "completed": "fill:#90EE90,stroke:#228B22",   # 緑
    "running": "fill:#87CEEB,stroke:#1E90FF",      # 青
    "pending": "fill:#D3D3D3,stroke:#808080",      # グレー
    "error": "fill:#FF6B6B,stroke:#CC0000",        # 赤
    "hitl": "fill:#FFD700,stroke:#B8860B",         # 黄（人間承認待ち）
    "skipped": "fill:#F5DEB3,stroke:#DAA520",      # 薄茶
}


# ---------------------------------------------------------------------------
# Pydanticモデル（generate_flow_diagram 用）
# ---------------------------------------------------------------------------

class PipelineStep(BaseModel):
    """パイプラインの1ステップ定義。"""
    id: str
    name: str
    step_type: str = "action"   # action / condition / hitl / start / end
    next_steps: list[str] = Field(default_factory=list)
    condition_label: str = ""   # 条件分岐のラベル（Yes/No等）


class FlowDiagramResult(BaseModel):
    """フロー図の生成結果。"""
    mermaid: str
    pipeline_name: str
    execution_id: str | None = None
    step_statuses: dict[str, str] = Field(default_factory=dict)  # step_id -> status
    total_steps: int = 0
    completed_steps: int = 0
    model_used: str = ""
    cost_yen: float = 0.0


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _sanitize_label(text: str) -> str:
    """Mermaid ノードラベルに使えない文字を除去する。"""
    return re.sub(r'["\[\]{}|<>]', "", text).strip()[:60]


def _extract_json(content: str) -> str:
    """LLMレスポンスからJSONを抽出する。"""
    text = content.strip()
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()
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
# knowledge_items からの業務フロー生成（既存実装を拡張）
# ---------------------------------------------------------------------------

def _build_simple_mermaid(flow_items: list[dict], flow_type: str) -> str:
    """3件未満のフローアイテムからシンプルな Mermaid 文字列を生成する（ルールベース）。"""
    if not flow_items:
        return f"{flow_type} TD\n    A[フロー情報がありません]"

    lines: list[str] = [f"{flow_type} TD"]
    prev_id: str | None = None

    for idx, item in enumerate(flow_items):
        node_id = f"N{idx}"
        label = _sanitize_label(item.get("title", f"ステップ{idx + 1}"))
        lines.append(f"    {node_id}[{label}]")
        if prev_id is not None:
            lines.append(f"    {prev_id} --> {node_id}")
        prev_id = node_id

    return "\n".join(lines)


async def _build_llm_mermaid(
    flow_items: list[dict],
    flow_type: str,
    company_id: str | None,
) -> str:
    """LLM を使って Mermaid 文字列を生成する。"""
    summaries = "\n".join(
        f"- {item.get('title', '')}: {item.get('content', '')[:200]}"
        for item in flow_items
    )

    type_instructions = {
        "flowchart": "flowchart TD 形式のフローチャート",
        "sequence": "sequenceDiagram 形式のシーケンス図",
        "stateDiagram": "stateDiagram-v2 形式のステート図",
    }
    diagram_hint = type_instructions.get(flow_type, "flowchart TD 形式のフローチャート")

    system_prompt = (
        "あなたは業務フローをMermaid記法で可視化する専門家です。"
        "与えられた業務フロー情報を元に、正確なMermaid記法の図を生成してください。"
        "コードブロック（```）は使わず、Mermaid記法のみを返してください。"
    )
    user_prompt = (
        f"以下の業務フロー情報を元に、{diagram_hint}を生成してください。\n\n"
        f"フロー情報:\n{summaries}\n\n"
        "要件:\n"
        "- 日本語ラベルを使用\n"
        "- ノード数は最大15個\n"
        "- 分岐がある場合は菱形ノード（{条件}）を使用\n"
        "- Mermaid記法のみ返すこと（コードブロック不要）"
    )

    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tier=ModelTier.FAST,
        task_type="visualization_flow",
        company_id=company_id,
        max_tokens=1024,
        temperature=0.3,
    ))

    content = response.content.strip()
    # コードブロックが含まれていた場合は除去する
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    return content


async def generate_process_flow(
    knowledge_items: list[dict],
    flow_type: str = "flowchart",
    company_id: str | None = None,
) -> str:
    """knowledge_items の item_type='flow' から Mermaid 図を生成する。

    Args:
        knowledge_items: knowledge_items テーブルの行リスト。
            各行に "item_type", "title", "content" を期待する。
        flow_type: "flowchart" | "sequence" | "stateDiagram"
        company_id: LLM コスト追跡用テナントID（省略可）

    Returns:
        Mermaid 記法の文字列。

    Examples:
        flowchart TD
            A[受注] --> B[見積作成]
            B --> C{承認}
            C -->|Yes| D[発注]
            C -->|No| E[修正]
    """
    if flow_type not in _FLOW_TYPES:
        logger.warning("Unknown flow_type=%s, defaulting to flowchart", flow_type)
        flow_type = "flowchart"

    # item_type="flow" のみ抽出
    flow_items = [
        item for item in knowledge_items
        if item.get("item_type") == "flow"
    ]

    if len(flow_items) >= 3:
        try:
            return await _build_llm_mermaid(flow_items, flow_type, company_id)
        except Exception as exc:
            logger.warning(
                "LLM flow generation failed, falling back to rule-based: %s", exc
            )
            return _build_simple_mermaid(flow_items, flow_type)
    else:
        return _build_simple_mermaid(flow_items, flow_type)


# ---------------------------------------------------------------------------
# パイプライン定義からのフロー図生成（新規実装）
# ---------------------------------------------------------------------------

_SYSTEM_FLOW_DIAGRAM = """あなたは業務パイプラインのフロー図を生成する専門家です。
パイプライン定義と実行状態から、Mermaid形式のフローチャートを生成してください。

## 出力形式（JSON）
{
  "mermaid": "flowchart TD\\n    START([開始]) --> S1\\n    S1[ステップ1] --> H1\\n    H1[[人間承認]] --> S2\\n    ...",
  "steps": [
    {"id": "S1", "name": "ステップ名", "step_type": "action", "next_steps": ["H1"]},
    {"id": "H1", "name": "人間承認", "step_type": "hitl", "next_steps": ["S2"]}
  ],
  "style_directives": [
    "style H1 fill:#FFD700,stroke:#B8860B"
  ]
}

## ノードタイプとMermaid記法
- start/end: 角丸 ([テキスト])
- action: 矩形 [テキスト]
- condition: 菱形 {テキスト}
- hitl: 二重矩形 [[テキスト]] + 黄色スタイル

## 実行状態の色分け
- completed（完了）: fill:#90EE90
- running（実行中）: fill:#87CEEB
- pending（未実行）: fill:#D3D3D3
- error（エラー）: fill:#FF6B6B
- hitl（承認待ち）: fill:#FFD700

重要:
- 全てのHITLポイントに黄色スタイルを付けること
- 日本語ラベルを使用
- JSONのみ返すこと（コードブロック不要）
"""


async def generate_flow_diagram(
    pipeline_name: str,
    execution_id: str | None,
    supabase: Any,
) -> FlowDiagramResult:
    """パイプラインのフロー図をMermaid形式で生成する。

    パイプライン定義からフロー図を生成し、実行ログがある場合は
    各ステップの実行状態（完了/進行中/未実行/エラー）を色分けして表示。
    HITLポイントは黄色で明示する。

    Args:
        pipeline_name: パイプライン名（例: "estimation_pipeline"）
        execution_id: execution_logs のID（None の場合は定義のみ表示）
        supabase: Supabaseクライアント

    Returns:
        FlowDiagramResult — Mermaid形式のフロー図
    """
    # 1. 実行ログから実行状態を取得（execution_id がある場合）
    step_statuses: dict[str, str] = {}
    company_id: str = ""

    if execution_id:
        log_result = supabase.table("execution_logs") \
            .select("id, company_id, operations, overall_success") \
            .eq("id", execution_id) \
            .limit(1) \
            .execute()

        logs = log_result.data or []
        if logs:
            log = logs[0]
            company_id = log.get("company_id", "")
            operations = log.get("operations") or {}
            step_statuses = _extract_step_statuses(operations, log.get("overall_success"))

    # 2. パイプライン定義を取得または推定
    pipeline_steps = _get_pipeline_definition(pipeline_name)

    # 3. LLMでフロー図生成
    context = _build_flow_context(pipeline_name, pipeline_steps, step_statuses)

    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": _SYSTEM_FLOW_DIAGRAM},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.FAST,
        task_type="flow_diagram_generation",
        company_id=company_id or None,
        max_tokens=2048,
        temperature=0.2,
    ))

    # 4. レスポンスをパース
    result = _parse_flow_result(pipeline_name, response.content, step_statuses)
    result.execution_id = execution_id
    result.model_used = response.model_used
    result.cost_yen = response.cost_yen

    logger.info(
        "generate_flow_diagram: pipeline=%s, execution_id=%s, steps=%d",
        pipeline_name,
        execution_id,
        result.total_steps,
    )

    return result


def _extract_step_statuses(operations: dict, overall_success: bool | None) -> dict[str, str]:
    """実行ログのoperationsからステップの実行状態を抽出する。"""
    statuses: dict[str, str] = {}

    if not isinstance(operations, dict):
        return statuses

    # operations は {"steps": [...]} または {"step_name": {"status": "..."}} 形式
    steps = operations.get("steps", [])
    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                step_id = step.get("id") or step.get("name") or f"step_{i}"
                status = step.get("status", "pending")
                # HITLフラグがある場合
                if step.get("requires_human") or step.get("hitl"):
                    status = "hitl"
                statuses[str(step_id)] = status

    # フラット形式 {"operation_name": "success/failed/..."}
    for key, val in operations.items():
        if key == "steps":
            continue
        if isinstance(val, str):
            if val in ("success", "completed", "done"):
                statuses[key] = "completed"
            elif val in ("failed", "error"):
                statuses[key] = "error"
            elif val in ("running", "in_progress"):
                statuses[key] = "running"

    return statuses


# パイプライン定義のデフォルトテンプレート
_PIPELINE_DEFINITIONS: dict[str, list[dict]] = {
    "estimation_pipeline": [
        {"id": "S0", "name": "ドキュメント受信", "step_type": "start", "next_steps": ["S1"]},
        {"id": "S1", "name": "OCR・テキスト抽出", "step_type": "action", "next_steps": ["S2"]},
        {"id": "S2", "name": "LLM項目抽出", "step_type": "action", "next_steps": ["H1"]},
        {"id": "H1", "name": "人間承認（見積確認）", "step_type": "hitl", "next_steps": ["S3"]},
        {"id": "S3", "name": "見積書生成", "step_type": "action", "next_steps": ["END"]},
        {"id": "END", "name": "完了", "step_type": "end", "next_steps": []},
    ],
    "billing_pipeline": [
        {"id": "S0", "name": "請求データ収集", "step_type": "start", "next_steps": ["S1"]},
        {"id": "S1", "name": "金額計算", "step_type": "action", "next_steps": ["C1"]},
        {"id": "C1", "name": "金額確認", "step_type": "condition", "next_steps": ["H1", "S3"]},
        {"id": "H1", "name": "人間承認（請求確認）", "step_type": "hitl", "next_steps": ["S2"]},
        {"id": "S2", "name": "修正", "step_type": "action", "next_steps": ["S3"]},
        {"id": "S3", "name": "請求書発行", "step_type": "action", "next_steps": ["END"]},
        {"id": "END", "name": "完了", "step_type": "end", "next_steps": []},
    ],
    "default": [
        {"id": "S0", "name": "開始", "step_type": "start", "next_steps": ["S1"]},
        {"id": "S1", "name": "データ収集", "step_type": "action", "next_steps": ["S2"]},
        {"id": "S2", "name": "AI分析", "step_type": "action", "next_steps": ["H1"]},
        {"id": "H1", "name": "人間承認", "step_type": "hitl", "next_steps": ["S3"]},
        {"id": "S3", "name": "実行", "step_type": "action", "next_steps": ["END"]},
        {"id": "END", "name": "完了", "step_type": "end", "next_steps": []},
    ],
}


def _get_pipeline_definition(pipeline_name: str) -> list[dict]:
    """パイプライン名からステップ定義を取得する。"""
    # 完全一致で検索
    if pipeline_name in _PIPELINE_DEFINITIONS:
        return _PIPELINE_DEFINITIONS[pipeline_name]
    # 部分一致で検索
    for key in _PIPELINE_DEFINITIONS:
        if key in pipeline_name or pipeline_name in key:
            return _PIPELINE_DEFINITIONS[key]
    return _PIPELINE_DEFINITIONS["default"]


def _build_flow_context(
    pipeline_name: str,
    steps: list[dict],
    step_statuses: dict[str, str],
) -> str:
    """フロー図生成用のコンテキスト文字列を構築する。"""
    parts = [f"## パイプライン: {pipeline_name}\n\n## ステップ定義\n"]

    for step in steps:
        step_id = step.get("id", "")
        name = step.get("name", "")
        step_type = step.get("step_type", "action")
        next_steps = step.get("next_steps", [])
        status = step_statuses.get(step_id, "pending") if step_statuses else None

        status_str = f" [状態: {status}]" if status else ""
        next_str = f" → {', '.join(next_steps)}" if next_steps else ""
        parts.append(f"- {step_id}: {name} ({step_type}){status_str}{next_str}\n")

    if step_statuses:
        parts.append(f"\n## 実行状態\n{json.dumps(step_statuses, ensure_ascii=False)}\n")

    parts.append(
        "\n上記のパイプライン定義から、"
        "実行状態を色分けしHITLポイントを黄色で明示したMermaidフローチャートをJSON形式で生成してください。"
    )

    return "".join(parts)


def _parse_flow_result(
    pipeline_name: str,
    content: str,
    step_statuses: dict[str, str],
) -> FlowDiagramResult:
    """LLMレスポンスをFlowDiagramResultにパースする。"""
    try:
        text = _extract_json(content)
        data = json.loads(text)

        mermaid = str(data.get("mermaid", ""))
        steps_raw = data.get("steps", [])

        # スタイルディレクティブを適用
        style_directives = data.get("style_directives", [])
        if style_directives and mermaid:
            mermaid = mermaid + "\n" + "\n".join(f"    {s}" for s in style_directives)

        if not mermaid:
            mermaid = _build_fallback_flow_mermaid(steps_raw, step_statuses)

        total_steps = len(steps_raw)
        completed_steps = sum(
            1 for s in step_statuses.values() if s == "completed"
        )

        return FlowDiagramResult(
            mermaid=mermaid,
            pipeline_name=pipeline_name,
            step_statuses=step_statuses,
            total_steps=total_steps,
            completed_steps=completed_steps,
        )

    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.warning("Flow diagram parse failed: %s — using fallback", e)
        return FlowDiagramResult(
            mermaid=(
                f"flowchart TD\n"
                f"    A([{_sanitize_label(pipeline_name)}])\n"
                f"    B[AI処理]\n"
                f"    H[[人間承認]]\n"
                f"    C([完了])\n"
                f"    A --> B --> H --> C\n"
                f"    style H fill:#FFD700,stroke:#B8860B"
            ),
            pipeline_name=pipeline_name,
            step_statuses=step_statuses,
        )


# ---------------------------------------------------------------------------
# render_process_flow_mermaid — 構造化フローデータ → Mermaid 変換
# ---------------------------------------------------------------------------

def _safe_node_id(raw: str) -> str:
    """Mermaid 互換ノードID（英数字 + アンダースコアのみ）に変換する。"""
    return re.sub(r"[^A-Za-z0-9_]", "_", raw)


def render_process_flow_mermaid(
    flow_data: dict,
    highlight_bottleneck: bool = True,
) -> str:
    """プロセスフローデータを Mermaid 記法に変換する。

    Args:
        flow_data: {
            "steps": [
                {"id": "order", "label": "受注", "type": "process", "duration_hours": 2},
                ...
            ],
            "connections": [
                {"from": "order", "to": "design", "label": ""},
                {"from": "inspect", "to": "rework", "label": "不合格", "condition": true},
                ...
            ],
            "bottleneck_step_id": "manufacturing",  # ボトルネックのstep id
        }
        highlight_bottleneck: ボトルネックステップを黄色でハイライトする（デフォルト True）

    Returns:
        Mermaid 記法の文字列（graph LR 形式）。
        ボトルネックは style 文でオレンジ色にハイライトされる。
    """
    if not isinstance(flow_data, dict):
        return "graph LR\n    A[無効なデータです]"

    steps: list[dict] = flow_data.get("steps", [])
    connections: list[dict] = flow_data.get("connections", [])
    bottleneck_id: str = str(flow_data.get("bottleneck_step_id", ""))

    if not steps:
        return "graph LR\n    A[フローデータがありません]"

    lines: list[str] = ["graph LR"]
    hitl_ids: list[str] = []

    # ノード定義
    for step in steps:
        raw_id = str(step.get("id", ""))
        node_id = _safe_node_id(raw_id)
        label = _sanitize_label(str(step.get("label", raw_id)))
        step_type = str(step.get("type", "process")).lower()

        if step_type in ("start", "end"):
            lines.append(f'    {node_id}(["{label}"])')
        elif step_type in ("decision", "condition"):
            lines.append(f'    {node_id}{{"{label}"}}')
        elif step_type == "hitl":
            lines.append(f'    {node_id}[["{label}"]]')
            hitl_ids.append(node_id)
        else:
            lines.append(f'    {node_id}["{label}"]')

    # エッジ定義
    for conn in connections:
        from_id = _safe_node_id(str(conn.get("from", "")))
        to_id = _safe_node_id(str(conn.get("to", "")))
        edge_label = str(conn.get("label", "")).strip()

        if not from_id or not to_id:
            continue

        if edge_label:
            lines.append(f"    {from_id} -->|{edge_label}| {to_id}")
        else:
            lines.append(f"    {from_id} --> {to_id}")

    # スタイル定義
    for hid in hitl_ids:
        lines.append(f"    style {hid} fill:#FFD700,stroke:#B8860B")

    if highlight_bottleneck and bottleneck_id:
        safe_bn = _safe_node_id(bottleneck_id)
        lines.append(f"    style {safe_bn} fill:#FF9800,stroke:#E65100")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# generate_process_flow_from_knowledge — ナレッジからフローを LLM 生成
# ---------------------------------------------------------------------------

_SYSTEM_FLOW_FROM_KNOWLEDGE = """あなたは業務プロセス分析の専門家です。
与えられたナレッジ情報から、業務フロー構造（steps + connections）を生成してください。

## 出力形式（JSONのみ）
{
  "steps": [
    {"id": "step1", "label": "受注確認", "type": "process", "duration_hours": 1},
    {"id": "step2", "label": "在庫確認", "type": "decision"},
    {"id": "step3", "label": "人間承認", "type": "hitl"}
  ],
  "connections": [
    {"from": "step1", "to": "step2", "label": ""},
    {"from": "step2", "to": "step3", "label": "在庫なし", "condition": true}
  ],
  "bottleneck_step_id": "step3"
}

## ステップ種別
- process: 通常の処理ステップ（矩形）
- decision: 条件分岐（菱形）
- hitl: 人間承認が必要なステップ（二重矩形）
- start: 開始点
- end: 終了点

## 制約
- ステップ数は最大15個
- step の id は英数字とアンダースコアのみ使用
- label は日本語でわかりやすく
- ボトルネックは最も時間がかかる・詰まりやすいステップを指定
- JSONのみ返すこと（コードブロック不要）
"""


async def generate_process_flow_from_knowledge(
    knowledge_items: list[dict],
    department: str = "",
    company_id: str | None = None,
) -> dict:
    """ナレッジベースからプロセスフローを自動生成する（LLM 使用）。

    knowledge_items から業務フロー構造データを生成する。
    生成されたデータは render_process_flow_mermaid() に渡してMermaidに変換できる。

    Args:
        knowledge_items: knowledge_items テーブルの行リスト。
            各行に "title", "content", "item_type" を期待する。
        department: 対象部署名（コンテキストとして使用）
        company_id: LLM コスト追跡用テナントID（省略可）

    Returns:
        render_process_flow_mermaid() に渡せる flow_data dict。
        {"steps": [...], "connections": [...], "bottleneck_step_id": "..."}
    """
    if not knowledge_items:
        return {
            "steps": [{"id": "no_data", "label": "データなし", "type": "process"}],
            "connections": [],
            "bottleneck_step_id": "",
        }

    # ナレッジのサマリーを構築
    summaries = "\n".join(
        f"- [{item.get('item_type', 'unknown')}] {item.get('title', '')}: "
        f"{str(item.get('content', ''))[:200]}"
        for item in knowledge_items[:20]
    )

    dept_hint = f"対象部署: {department}\n\n" if department else ""
    user_prompt = (
        f"{dept_hint}以下のナレッジ情報から業務プロセスフロー構造を生成してください。\n\n"
        f"ナレッジ情報:\n{summaries}\n\n"
        "上記をもとに、業務フロー構造をJSON形式で出力してください。"
    )

    llm = get_llm_client()
    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_FLOW_FROM_KNOWLEDGE},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.FAST,
            task_type="flow_from_knowledge",
            company_id=company_id,
            max_tokens=1500,
            temperature=0.2,
        ))
    except Exception as exc:
        logger.warning("generate_process_flow_from_knowledge LLM call failed: %s", exc)
        response = None

    if response is None:
        # LLM 呼び出し自体が失敗した場合のフォールバック
        simple_steps = []
        for i, item in enumerate(knowledge_items[:8]):
            simple_steps.append({
                "id": f"s{i}",
                "label": _sanitize_label(str(item.get("title", f"ステップ{i + 1}"))),
                "type": "process",
            })
        connections = [
            {"from": f"s{i}", "to": f"s{i + 1}", "label": ""}
            for i in range(len(simple_steps) - 1)
        ]
        return {
            "steps": simple_steps,
            "connections": connections,
            "bottleneck_step_id": "",
        }

    try:
        text = _extract_json(response.content)
        data = json.loads(text)

        # 最低限の構造を検証
        if not isinstance(data.get("steps"), list):
            raise ValueError("steps が配列でない")
        if not isinstance(data.get("connections"), list):
            data["connections"] = []

        # step id を安全な文字列に正規化
        for step in data["steps"]:
            step["id"] = _safe_node_id(str(step.get("id", "step")))

        logger.info(
            "generate_process_flow_from_knowledge: steps=%d, connections=%d",
            len(data["steps"]),
            len(data["connections"]),
        )
        return data

    except (json.JSONDecodeError, ValueError, TypeError, KeyError) as e:
        logger.warning("generate_process_flow_from_knowledge parse failed: %s", e)
        # フォールバック: シンプルなリニアフロー
        simple_steps = []
        for i, item in enumerate(knowledge_items[:8]):
            simple_steps.append({
                "id": f"s{i}",
                "label": _sanitize_label(str(item.get("title", f"ステップ{i + 1}"))),
                "type": "process",
            })
        connections = [
            {"from": f"s{i}", "to": f"s{i + 1}", "label": ""}
            for i in range(len(simple_steps) - 1)
        ]
        return {
            "steps": simple_steps,
            "connections": connections,
            "bottleneck_step_id": "",
        }


def _build_fallback_flow_mermaid(
    steps: list[dict],
    step_statuses: dict[str, str],
) -> str:
    """フォールバック用のMermaid文字列を生成する。"""
    if not steps:
        return "flowchart TD\n    A[フロー情報がありません]"

    lines = ["flowchart TD"]
    hitl_ids: list[str] = []

    for step in steps:
        step_id = str(step.get("id", ""))
        name = _sanitize_label(str(step.get("name", "")))
        step_type = str(step.get("step_type", "action"))

        if step_type in ("start", "end"):
            lines.append(f'    {step_id}(["{name}"])')
        elif step_type == "condition":
            lines.append(f'    {step_id}{{"{name}"}}')
        elif step_type == "hitl":
            lines.append(f'    {step_id}[["{name}"]]')
            hitl_ids.append(step_id)
        else:
            lines.append(f'    {step_id}["{name}"]')

        for next_id in step.get("next_steps", []):
            lines.append(f'    {step_id} --> {next_id}')

    # 実行状態スタイルを付与
    for step_id, status in step_statuses.items():
        style = _STEP_STATUS_STYLES.get(status)
        if style:
            lines.append(f'    style {step_id} {style}')

    # HITLスタイルを優先的に付与
    for hid in hitl_ids:
        lines.append(f'    style {hid} {_STEP_STATUS_STYLES["hitl"]}')

    return "\n".join(lines)
