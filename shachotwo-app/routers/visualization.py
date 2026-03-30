"""可視化エンドポイント — フロー図・充足度マップ・意思決定ツリー。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.jwt import JWTClaims
from auth.middleware import get_current_user
from brain.visualization import (
    generate_completeness_radar,
    generate_decision_tree,
    generate_process_flow,
    generate_process_flow_from_knowledge,
    render_decision_tree_mermaid,
    render_decision_tree_svg,
    render_process_flow_mermaid,
)
from db.supabase import get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/visualization/completeness")
async def get_completeness(
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """5次元デジタルツイン充足度をレーダーチャートデータとして返す。

    最新の company_state_snapshots からスナップショットを取得し、
    各次元の completeness を集計する。スナップショットが存在しない場合は
    全次元 0 のデータを返す。
    """
    from brain.twin.models import (
        CostState,
        PeopleState,
        ProcessState,
        RiskState,
        ToolState,
        TwinSnapshot,
    )

    db = get_service_client()
    result = (
        db.table("company_state_snapshots")
        .select(
            "people_state, process_state, cost_state, tool_state, risk_state, snapshot_at"
        )
        .eq("company_id", str(user.company_id))
        .order("snapshot_at", desc=True)
        .limit(1)
        .execute()
    )

    rows = result.data or []
    if not rows:
        # スナップショットがない場合は全次元 0 の空スナップショットを返す
        snapshot = TwinSnapshot(company_id=str(user.company_id))
        snapshot.recalculate_overall_completeness()
        radar = generate_completeness_radar(snapshot)
        return {**radar, "snapshot_at": None}

    row = rows[0]

    def _parse_state(raw: dict | None, model_class):
        if not raw:
            return model_class()
        try:
            return model_class(**raw)
        except Exception:
            return model_class()

    snapshot = TwinSnapshot(
        company_id=str(user.company_id),
        people=_parse_state(row.get("people_state"), PeopleState),
        process=_parse_state(row.get("process_state"), ProcessState),
        cost=_parse_state(row.get("cost_state"), CostState),
        tool=_parse_state(row.get("tool_state"), ToolState),
        risk=_parse_state(row.get("risk_state"), RiskState),
    )
    snapshot.recalculate_overall_completeness()

    radar = generate_completeness_radar(snapshot)
    return {**radar, "snapshot_at": row.get("snapshot_at")}


@router.get("/visualization/process-flow")
async def get_process_flow(
    flow_type: str = Query(default="flowchart", pattern="^(flowchart|sequence|stateDiagram)$"),
    department: Optional[str] = Query(default=None),
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """業務フロー Mermaid 図を返す。

    knowledge_items の item_type='flow' を取得して Mermaid 文字列を生成する。
    3件以上あれば LLM で生成、未満はルールベース。
    """
    db = get_service_client()
    query = (
        db.table("knowledge_items")
        .select("id, title, content, item_type, department, category")
        .eq("company_id", str(user.company_id))
        .eq("item_type", "flow")
        .eq("is_active", True)
    )
    if department:
        query = query.eq("department", department)

    result = query.order("created_at", desc=True).limit(30).execute()
    items: list[dict] = result.data or []

    mermaid = await generate_process_flow(
        knowledge_items=items,
        flow_type=flow_type,
        company_id=str(user.company_id),
    )

    return {
        "mermaid": mermaid,
        "flow_type": flow_type,
        "item_count": len(items),
    }


@router.get("/visualization/decision-tree")
async def get_decision_tree(
    department: Optional[str] = Query(default=None),
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """意思決定ツリーデータを返す。

    decision_rules テーブルから Mermaid 図とグラフデータを生成する。
    """
    result = await generate_decision_tree(
        company_id=str(user.company_id),
        department=department,
    )
    return result


@router.get("/visualization/decision-tree/{snapshot_id}")
async def get_decision_tree_by_snapshot(
    snapshot_id: str,
    format: str = Query(default="mermaid", pattern="^(mermaid|svg)$"),
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """スナップショットIDに対応する決定木の Mermaid 記法または SVG を返す。

    company_state_snapshots からスナップショットを取得し、
    process_state.decision_tree_data（あれば）を render_decision_tree_mermaid() に渡す。
    decision_tree_data がない場合は decision_rules テーブルから生成する。

    Args:
        snapshot_id: company_state_snapshots テーブルの UUID
        format: "mermaid"（デフォルト）または "svg"（フォールバック用テキストSVG）
    """
    db = get_service_client()
    snap_result = (
        db.table("company_state_snapshots")
        .select("id, company_id, process_state, snapshot_at")
        .eq("id", snapshot_id)
        .eq("company_id", str(user.company_id))
        .limit(1)
        .execute()
    )

    rows = snap_result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="スナップショットが見つかりません")

    row = rows[0]
    process_state: dict = row.get("process_state") or {}
    tree_data: dict | None = process_state.get("decision_tree_data")

    if tree_data and isinstance(tree_data, dict) and tree_data.get("root"):
        # スナップショットに埋め込まれた構造データを使用
        if format == "svg":
            output = render_decision_tree_svg(tree_data)
        else:
            output = render_decision_tree_mermaid(tree_data)
        return {
            "output": output,
            "format": format,
            "source": "snapshot",
            "snapshot_id": snapshot_id,
            "snapshot_at": row.get("snapshot_at"),
        }

    # フォールバック: decision_rules テーブルから生成
    result = await generate_decision_tree(
        company_id=str(user.company_id),
    )
    return {
        "output": result["mermaid"],
        "format": "mermaid",
        "source": "decision_rules",
        "snapshot_id": snapshot_id,
        "snapshot_at": row.get("snapshot_at"),
        "rule_count": result.get("rule_count", 0),
    }


@router.get("/visualization/process-flow/from-knowledge")
async def get_process_flow_from_knowledge(
    department: Optional[str] = Query(default=None),
    highlight_bottleneck: bool = Query(default=True),
    user: JWTClaims = Depends(get_current_user),
) -> dict:
    """ナレッジベースから業務プロセスフローを LLM 生成して Mermaid 記法を返す。

    knowledge_items テーブルのデータを LLM に渡し、
    フロー構造（steps + connections）を生成したうえで Mermaid 文字列に変換する。
    既存の /visualization/process-flow とは異なり、item_type 問わず全ナレッジを使用する。

    Args:
        department: 部署フィルタ（省略時は全部署）
        highlight_bottleneck: ボトルネックをオレンジでハイライトするか（デフォルト True）
    """
    db = get_service_client()
    query = (
        db.table("knowledge_items")
        .select("id, title, content, item_type, department, category")
        .eq("company_id", str(user.company_id))
        .eq("is_active", True)
    )
    if department:
        query = query.eq("department", department)

    result = query.order("created_at", desc=True).limit(20).execute()
    items: list[dict] = result.data or []

    flow_data = await generate_process_flow_from_knowledge(
        knowledge_items=items,
        department=department or "",
        company_id=str(user.company_id),
    )

    mermaid = render_process_flow_mermaid(
        flow_data=flow_data,
        highlight_bottleneck=highlight_bottleneck,
    )

    return {
        "mermaid": mermaid,
        "flow_data": flow_data,
        "item_count": len(items),
        "department": department,
    }
