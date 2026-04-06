"""
建設業 見積パイプライン — LangGraph ノード版 (estimation_pipeline_lg.py)

既存の estimation_pipeline.py（8ステップ関数型）を LangGraph ノードとして再定義。
AgentExecutor + HitL 承認フローに対応する。

後方互換:
    既存の run_estimation_pipeline() は変更しない。
    本ファイルは estimation_pipeline.py と並置して使用する。

ノード構成:
    node_extract_quantities  — OCR + LLM で数量抽出（Step 1-2）
    node_lookup_unit_prices  — 単価マスタ検索（Step 3）
    node_calculate_total     — 合計金額計算 + 諸経費（Step 4-5）
    node_hitl_check          — HitL 要否判定（bpo_hitl_requirements 参照）
    node_finalize            — 結果確定・execution_logs に保存（Step 6-9）

使い方:
    from workers.bpo.construction.pipelines.estimation_pipeline_lg import (
        build_estimation_graph,
    )
    from workers.base.agent_executor import AgentExecutor, AgentState

    graph = build_estimation_graph()
    executor = AgentExecutor(graph=graph, db_client=supabase)
    initial: AgentState = {
        "task_id": "...",
        "company_id": "...",
        "pipeline_name": "construction/estimation",
        "context": {
            "input_data": {"text": "..."},
            "region": "関東",
            "fiscal_year": 2025,
            "project_type": "public_civil",
        },
        "steps_completed": [],
        "human_approval_pending": False,
        "approval_execution_id": None,
        "current_step": "",
        "final_output": None,
        "error": None,
    }
    result = await executor.run(initial)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from workers.base.agent_executor import (
    AgentState,
    _HitLInterrupt,
    build_simple_graph,
)

logger = logging.getLogger(__name__)

# HitL 判定: bpo_hitl_requirements に "construction/estimation" は常に True
PIPELINE_KEY = "construction/estimation"

# 信頼度の警告ライン（既存パイプラインと揃える）
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 見積書の必須フィールド（既存パイプラインと揃える）
REQUIRED_ESTIMATION_FIELDS = [
    "title", "items", "direct_cost", "total_cost",
]


# ─── ノード実装 ───────────────────────────────────────────────────────────────

async def node_extract_quantities(state: AgentState) -> AgentState:
    """
    Step 1-2: OCR（またはkintone/items直渡し）+ LLM による数量抽出。

    context キー:
        input_data   : dict  入力データ（text / file_path / items / kintone）
        company_id   : str   会社ID（コンテキストに重複格納）
        project_id   : str   (省略可)
        region       : str   (省略可, default="関東")
        fiscal_year  : int   (省略可, default=2025)
        project_type : str   (省略可, default="public_civil")

    出力 context キー:
        raw_text  : str
        raw_items : list[dict]
    """
    state["current_step"] = "extract_quantities"
    steps_completed: list[str] = list(state.get("steps_completed") or [])
    context: dict[str, Any] = dict(state.get("context") or {})
    company_id: str = state.get("company_id", "")
    input_data: dict[str, Any] = context.get("input_data", {})
    project_id: Optional[str] = context.get("project_id")

    try:
        if "items" in input_data:
            # a) items 直渡し
            context["raw_items"] = input_data["items"]
            context["raw_text"] = ""

        elif input_data.get("source") == "kintone":
            # b) kintone 連携
            from workers.connector.kintone import KintoneConnector
            from workers.connector.base import ConnectorConfig

            subdomain = input_data["kintone_subdomain"]
            api_token = input_data["kintone_api_token"]
            app_id = str(input_data["kintone_app_id"])
            query = input_data.get("kintone_query", 'ステータス in ("見積依頼")')
            field_map: dict[str, str] = {
                "工種": "category",
                "種別": "subcategory",
                "細別": "detail",
                "規格": "specification",
                "数量": "quantity",
                "単位": "unit",
                "単価": "unit_price",
                **input_data.get("kintone_field_map", {}),
            }

            kintone_config = ConnectorConfig(
                tool_name="kintone",
                credentials={"subdomain": subdomain, "api_token": api_token},
            )
            kintone = KintoneConnector(kintone_config)
            records = await kintone.read_records(
                resource=app_id, filters={"query": query}
            )

            converted: list[dict[str, Any]] = []
            for rec in records:
                item: dict[str, Any] = {}
                for kf, ik in field_map.items():
                    fd = rec.get(kf, {})
                    raw_val = fd.get("value", "") if isinstance(fd, dict) else fd
                    if ik in ("quantity", "unit_price") and raw_val not in ("", None):
                        try:
                            item[ik] = float(raw_val)
                        except (ValueError, TypeError):
                            item[ik] = raw_val
                    else:
                        item[ik] = raw_val
                if item:
                    converted.append(item)

            context["raw_items"] = converted
            context["raw_text"] = ""
            logger.info(
                "node_extract_quantities: kintone fetch app_id=%s records=%d",
                app_id, len(records),
            )

        else:
            # c) 通常 OCR
            from workers.micro.models import MicroAgentInput
            from workers.micro.ocr import run_document_ocr

            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id,
                agent_name="document_ocr",
                payload={k: v for k, v in input_data.items() if k in ("text", "file_path")},
                context=context,
            ))
            if not ocr_out.success:
                state["error"] = f"OCR 失敗: {ocr_out.result.get('error', '不明')}"
                state["context"] = context
                return state
            context["raw_text"] = ocr_out.result.get("text", "")
            context["raw_items"] = []

        # LLM による数量抽出（raw_text がある場合のみ）
        raw_text: str = context.get("raw_text", "")
        if raw_text and not context.get("raw_items"):
            from workers.bpo.construction.estimator import EstimationPipeline
            ep = EstimationPipeline()
            items, _ = await ep.extract_quantities(
                project_id=project_id or "pipeline_run",
                company_id=company_id,
                raw_text=raw_text,
            )
            context["raw_items"] = [i.model_dump(mode="json") for i in items]

    except Exception as exc:
        logger.error("node_extract_quantities error: %s", exc, exc_info=True)
        state["error"] = f"数量抽出エラー: {exc}"
        state["context"] = context
        return state

    steps_completed.append("extract_quantities")
    state["steps_completed"] = steps_completed
    state["context"] = context
    return state


async def node_lookup_unit_prices(state: AgentState) -> AgentState:
    """
    Step 3: 単価マスタ照合。

    context 入力キー:
        raw_items  : list[dict]
        company_id : str (state から取得)
        project_id : str (省略可)
        region     : str
        fiscal_year: int

    context 出力キー:
        priced_items : list[dict]
    """
    if state.get("error"):
        return state

    state["current_step"] = "lookup_unit_prices"
    steps_completed: list[str] = list(state.get("steps_completed") or [])
    context: dict[str, Any] = dict(state.get("context") or {})
    company_id: str = state.get("company_id", "")
    project_id: Optional[str] = context.get("project_id")
    region: str = context.get("region", "関東")
    fiscal_year: int = int(context.get("fiscal_year", 2025))
    raw_items: list[dict[str, Any]] = context.get("raw_items", [])

    try:
        from workers.bpo.construction.estimator import EstimationPipeline
        ep = EstimationPipeline()
        items_with_price = await ep.suggest_unit_prices(
            project_id=project_id or "pipeline_run",
            company_id=company_id,
            region=region,
            fiscal_year=fiscal_year,
            items_override=raw_items if not project_id else None,
        )
        priced_items = [i.model_dump(mode="json") for i in items_with_price]

    except Exception as exc:
        logger.warning(
            "node_lookup_unit_prices: DB 照合失敗（フォールバック）— %s", exc
        )
        # PDF から取得済みの単価でフォールバック
        priced_items = raw_items

    context["priced_items"] = priced_items
    steps_completed.append("lookup_unit_prices")
    state["steps_completed"] = steps_completed
    state["context"] = context
    return state


async def node_calculate_total(state: AgentState) -> AgentState:
    """
    Step 4-5: 合計金額計算 + 諸経費計算。

    context 入力キー:
        priced_items : list[dict]
        project_id   : str (省略可)
        project_type : str

    context 出力キー:
        direct_cost  : int
        overhead     : dict
        total_cost   : int
    """
    if state.get("error"):
        return state

    state["current_step"] = "calculate_total"
    steps_completed: list[str] = list(state.get("steps_completed") or [])
    context: dict[str, Any] = dict(state.get("context") or {})
    company_id: str = state.get("company_id", "")
    project_id: Optional[str] = context.get("project_id")
    project_type: str = context.get("project_type", "public_civil")
    priced_items: list[dict[str, Any]] = context.get("priced_items", [])

    # Step 4: cost_calculator — 直接工事費
    direct_cost = 0
    for item in priced_items:
        qty = item.get("quantity", 0)
        candidates = item.get("price_candidates", [])
        unit_price = item.get("unit_price")
        if not unit_price and candidates:
            best = max(candidates, key=lambda c: c.get("confidence", 0))
            unit_price = best.get("unit_price")
        if qty and unit_price:
            direct_cost += int(Decimal(str(qty)) * Decimal(str(unit_price)))

    context["direct_cost"] = direct_cost

    # Step 5: overhead_calculator — 諸経費
    pt_key = project_type or "public_civil"
    try:
        if project_id:
            from workers.bpo.construction.estimator import EstimationPipeline
            from workers.bpo.construction.models import ProjectType

            pt_map = {
                "public_civil": ProjectType.PUBLIC_CIVIL,
                "public_building": ProjectType.PUBLIC_BUILDING,
                "private": ProjectType.PRIVATE,
            }
            pt = pt_map.get(pt_key, ProjectType.PUBLIC_CIVIL)
            ep = EstimationPipeline()
            overhead = await ep.calculate_overhead(project_id, company_id, pt)
            total_cost = overhead.total
            overhead_data: dict[str, Any] = {
                "direct_cost": overhead.direct_cost,
                "common_temporary": overhead.common_temporary,
                "site_management": overhead.site_management,
                "general_admin": overhead.general_admin,
                "total": overhead.total,
            }
        else:
            dc = direct_cost
            ct_rate = {
                "public_civil": "0.090",
                "public_building": "0.080",
                "private": "0.070",
            }.get(pt_key, "0.090")
            sm_rate = {
                "public_civil": "0.300",
                "public_building": "0.280",
                "private": "0.250",
            }.get(pt_key, "0.300")
            ct = int(dc * Decimal(ct_rate))
            sm = int((dc + ct) * Decimal(sm_rate))
            ga = int((dc + ct + sm) * Decimal("0.120"))
            total_cost = dc + ct + sm + ga
            overhead_data = {
                "direct_cost": dc,
                "common_temporary": ct,
                "site_management": sm,
                "general_admin": ga,
                "total": total_cost,
                "method": "standard_rates",
            }
    except Exception as exc:
        logger.warning("node_calculate_total: 諸経費計算フォールバック — %s", exc)
        dc = direct_cost
        ct = int(dc * Decimal("0.090"))
        sm = int((dc + ct) * Decimal("0.300"))
        ga = int((dc + ct + sm) * Decimal("0.120"))
        total_cost = dc + ct + sm + ga
        overhead_data = {
            "direct_cost": dc,
            "common_temporary": ct,
            "site_management": sm,
            "general_admin": ga,
            "total": total_cost,
            "warning": str(exc),
        }

    context["overhead"] = overhead_data
    context["total_cost"] = total_cost

    steps_completed.append("calculate_total")
    state["steps_completed"] = steps_completed
    state["context"] = context
    return state


async def node_hitl_check(state: AgentState) -> AgentState:
    """
    HitL 要否判定ノード。

    bpo_hitl_requirements テーブルを参照し、要否を判断する。
    DB が参照できない場合はデフォルト True（常に承認必須）として扱う。

    requires_approval == True の場合:
        - state に final_output（ドラフト）を格納した上で _HitLInterrupt を raise。
        - AgentExecutor が捕捉し、execution_logs に pending 保存してサスペンドする。
    """
    if state.get("error"):
        return state

    state["current_step"] = "hitl_check"
    context: dict[str, Any] = dict(state.get("context") or {})

    # ─── HitL 要否をDBから確認 ─────────────────────────────────────────────
    requires_approval: bool = True  # デフォルト: 常に HitL

    db_client = context.get("_db_client")  # テスト時はコンテキストに注入可能
    if db_client is not None:
        try:
            resp = (
                await db_client
                .table("bpo_hitl_requirements")
                .select("requires_approval, min_confidence_for_auto")
                .eq("pipeline_key", PIPELINE_KEY)
                .single()
                .execute()
            )
            row = resp.data
            if row:
                requires_approval = bool(row.get("requires_approval", True))
                min_conf = row.get("min_confidence_for_auto")
                # min_confidence_for_auto が設定されており、信頼度が閾値以上なら自動承認
                if not requires_approval or (
                    min_conf is not None
                    and _calc_overall_confidence(context) >= float(min_conf)
                ):
                    requires_approval = False
        except Exception as exc:
            logger.warning(
                "node_hitl_check: bpo_hitl_requirements 取得失敗 — %s (HitL=True で継続)",
                exc,
            )

    if not requires_approval:
        steps_completed = list(state.get("steps_completed") or [])
        steps_completed.append("hitl_check_skipped")
        state["steps_completed"] = steps_completed
        state["context"] = context
        return state

    # ─── HitL 必要 → ドラフト出力を格納してサスペンド ───────────────────────
    draft_output = _build_draft_output(context)
    state["final_output"] = draft_output
    state["context"] = context

    steps_completed = list(state.get("steps_completed") or [])
    steps_completed.append("hitl_check_pending")
    state["steps_completed"] = steps_completed

    logger.info(
        "node_hitl_check: HitL 中断 — pipeline=%s total_cost=%s",
        PIPELINE_KEY,
        context.get("total_cost"),
    )
    raise _HitLInterrupt(state)


async def node_finalize(state: AgentState) -> AgentState:
    """
    Step 6-9 相当: コンプライアンスチェック、内訳書生成、バリデーション、異常検知。
    結果を final_output に格納する。

    HitL 承認後に再開される場合、context["approved_output"] がマージ済みであれば
    承認者の修正を反映した状態で確定する。
    """
    if state.get("error"):
        return state

    state["current_step"] = "finalize"
    steps_completed: list[str] = list(state.get("steps_completed") or [])
    context: dict[str, Any] = dict(state.get("context") or {})
    company_id: str = state.get("company_id", "")
    project_id: Optional[str] = context.get("project_id")

    priced_items: list[dict[str, Any]] = context.get("priced_items", [])
    direct_cost: int = context.get("direct_cost", 0)
    total_cost: int = context.get("total_cost", 0)
    overhead: dict[str, Any] = context.get("overhead", {})

    # ─── 承認者の修正を反映（HitL 後の再開時）───────────────────────────────
    approved: dict[str, Any] = context.get("approved_output", {})
    if approved:
        direct_cost = approved.get("direct_cost", direct_cost)
        total_cost = approved.get("total_cost", total_cost)
        priced_items = approved.get("items", priced_items)

    # ─── コンプライアンスチェック（Step 6 相当）──────────────────────────────
    warnings: list[str] = []
    if total_cost >= 45_000_000:
        warnings.append("特定建設業許可要件: 下請総額4,500万円以上（建築一式以外）")
    if total_cost >= 70_000_000:
        warnings.append("特定建設業許可要件: 下請総額7,000万円以上（建築一式）")
    zero_price_items = [
        i.get("detail", i.get("category", "?"))
        for i in priced_items
        if not i.get("unit_price") and not i.get("price_candidates")
    ]
    if zero_price_items:
        warnings.append(
            f"単価未設定項目: {len(zero_price_items)}件 "
            f"({', '.join(zero_price_items[:3])}...)"
        )

    # ─── 内訳書生成（Step 7 相当）─────────────────────────────────────────────
    rows = []
    for item in priced_items:
        candidates = item.get("price_candidates", [])
        unit_price = item.get("unit_price")
        if not unit_price and candidates:
            best = max(candidates, key=lambda c: c.get("confidence", 0))
            unit_price = best.get("unit_price")
        qty = item.get("quantity", 0)
        amount: Any = int(Decimal(str(qty)) * Decimal(str(unit_price))) if qty and unit_price else ""
        rows.append([
            item.get("category", ""),
            item.get("subcategory", ""),
            item.get("detail", ""),
            item.get("specification", ""),
            float(qty) if qty else "",
            item.get("unit", ""),
            float(unit_price) if unit_price else "",
            amount,
        ])

    breakdown: dict[str, Any] = {
        "title": "工事費内訳書",
        "items": priced_items,
        "direct_cost": direct_cost,
        "total_cost": total_cost,
        "overhead": overhead,
        "headers": ["工種", "種別", "細別", "規格", "数量", "単位", "単価", "金額"],
        "rows": rows,
        "compliance_warnings": warnings,
    }

    # ─── 異常検知（Step 9 相当、エラーは非致命的）────────────────────────────
    try:
        from workers.micro.models import MicroAgentInput
        from workers.micro.anomaly_detector import run_anomaly_detector

        anomaly_items = [
            {"name": "direct_cost", "value": direct_cost},
            {"name": "total_cost", "value": total_cost},
        ]
        anomaly_items += [
            {"name": overhead_k, "value": overhead.get(overhead_k, 0)}
            for overhead_k in ("common_temporary", "site_management", "general_admin")
        ]
        anomaly_out = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id,
            agent_name="anomaly_detector",
            payload={"items": anomaly_items, "detect_modes": ["digit_error", "range"]},
            context=context,
        ))
        if anomaly_out.success and anomaly_out.result.get("anomaly_count", 0) > 0:
            breakdown["anomaly_warnings"] = anomaly_out.result["anomalies"]
    except Exception as exc:
        logger.warning("node_finalize: 異常検知スキップ — %s", exc)

    # ─── execution_logs に保存（DB クライアントが注入されている場合）──────────
    db_client = context.get("_db_client")
    if db_client is not None:
        try:
            await db_client.table("execution_logs").insert({
                "id": str(uuid.uuid4()),
                "company_id": company_id,
                "pipeline_name": PIPELINE_KEY,
                "task_id": state.get("task_id", ""),
                "approval_status": "approved",
                "original_output": json.dumps(breakdown, ensure_ascii=False),
                "created_at": _now_iso(),
            }).execute()
        except Exception as exc:
            logger.error("node_finalize: execution_logs 保存失敗 — %s", exc, exc_info=True)

    steps_completed.append("finalize")
    state["steps_completed"] = steps_completed
    state["final_output"] = breakdown
    state["context"] = context

    logger.info(
        "node_finalize: 完了 — total_cost=¥%s items=%d",
        f"{total_cost:,}", len(priced_items),
    )
    return state


# ─── グラフ構築 ───────────────────────────────────────────────────────────────

def build_estimation_graph() -> Any:
    """
    建設業見積パイプラインの LangGraph CompiledGraph を構築して返す。

    LangGraph が未インストールの場合は None を返す。
    AgentExecutor は graph=None でも動作する（フォールバック）。

    Returns:
        CompiledGraph または None
    """
    nodes = [
        ("extract_quantities", node_extract_quantities),
        ("lookup_unit_prices", node_lookup_unit_prices),
        ("calculate_total",    node_calculate_total),
        ("hitl_check",         node_hitl_check),
        ("finalize",           node_finalize),
    ]
    return build_simple_graph(nodes, entry_point="extract_quantities")


# ─── ユーティリティ ───────────────────────────────────────────────────────────

def _calc_overall_confidence(context: dict[str, Any]) -> float:
    """
    コンテキストから信頼度スコアを概算する。
    priced_items の単価充足率を指標とする。
    """
    priced_items: list[dict[str, Any]] = context.get("priced_items", [])
    if not priced_items:
        return 0.0
    has_price = sum(
        1 for i in priced_items
        if i.get("unit_price") or i.get("price_candidates")
    )
    return has_price / len(priced_items)


def _build_draft_output(context: dict[str, Any]) -> dict[str, Any]:
    """HitL 中断前に保存するドラフト出力を生成する。"""
    return {
        "title": "工事費内訳書（承認待ち）",
        "items": context.get("priced_items", []),
        "direct_cost": context.get("direct_cost", 0),
        "total_cost": context.get("total_cost", 0),
        "overhead": context.get("overhead", {}),
        "status": "pending_approval",
    }


def _now_iso() -> str:
    from datetime import timezone
    return __import__("datetime").datetime.now(timezone.utc).isoformat()
