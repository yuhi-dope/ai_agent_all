"""
LangGraph ベースのエージェント実行基盤。

AgentState をステートマシンで管理し、Human-in-the-Loop (HitL) 中断・再開を
execution_logs テーブルへの永続化を通じて実現する。

利用方法:
    executor = AgentExecutor(graph=my_graph, db_client=supabase)
    state = await executor.run(initial_state)
    # HitL中断時 → state.human_approval_pending == True
    # 承認後
    state = await executor.resume(execution_id, approved_output)
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypedDict

logger = logging.getLogger(__name__)

# LangGraph はオプション依存。未インストール時も型定義だけは利用できるよう遅延インポート。
try:
    from langgraph.graph import StateGraph, END  # type: ignore[import-not-found]
    _LANGGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGGRAPH_AVAILABLE = False
    END = "__end__"  # フォールバック定数


# ─── State 定義 ──────────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    """LangGraph ステートマシン全体で共有される状態。"""
    task_id: str
    company_id: str
    pipeline_name: str
    context: dict[str, Any]
    steps_completed: list[str]
    human_approval_pending: bool
    approval_execution_id: Optional[str]   # 承認待ちの execution_logs.id
    current_step: str
    final_output: Optional[dict[str, Any]]
    error: Optional[str]


def _default_state(
    task_id: str,
    company_id: str,
    pipeline_name: str,
    context: dict[str, Any] | None = None,
) -> AgentState:
    """初期状態を生成するファクトリ。"""
    return AgentState(
        task_id=task_id,
        company_id=company_id,
        pipeline_name=pipeline_name,
        context=context or {},
        steps_completed=[],
        human_approval_pending=False,
        approval_execution_id=None,
        current_step="",
        final_output=None,
        error=None,
    )


# ─── AgentExecutor ────────────────────────────────────────────────────────────

class AgentExecutor:
    """
    LangGraph グラフをラップし、HitL 永続化を担うエグゼキュータ。

    Args:
        graph: LangGraph の CompiledGraph（またはそれに準ずる callable）。
               ``None`` の場合はノードなしの no-op グラフとして動作する。
        db_client: Supabase クライアント（execution_logs の読み書き用）。
                   ``None`` の場合は永続化をスキップ（テスト用）。
    """

    def __init__(
        self,
        graph: Any | None = None,
        db_client: Any | None = None,
    ) -> None:
        self._graph = graph
        self._db = db_client

    # ------------------------------------------------------------------ run --

    async def run(self, state: AgentState) -> AgentState:
        """
        ステートマシンを実行する。

        - グラフが定義されていれば LangGraph の ``ainvoke`` を呼ぶ。
        - 途中で HitL が必要になった場合は ``human_approval_pending=True`` を
          セットし、execution_logs に ``approval_status='pending'`` で保存して
          サスペンドする。
        - グラフが None の場合は state をそのまま返す（テスト・フォールバック用）。
        """
        if self._graph is None:
            logger.debug(
                "AgentExecutor.run: graph is None — returning state as-is "
                "(task_id=%s)", state.get("task_id")
            )
            return state

        try:
            result: AgentState = await self._graph.ainvoke(state)
        except _HitLInterrupt as exc:
            # HitL ノードが raise した中断シグナルを捕捉
            result = exc.state
            result["human_approval_pending"] = True
            execution_id = await self._persist_pending(result)
            result["approval_execution_id"] = execution_id
            logger.info(
                "AgentExecutor.run: HitL 中断 — execution_id=%s pipeline=%s",
                execution_id, result.get("pipeline_name"),
            )
            return result
        except Exception as exc:
            logger.error(
                "AgentExecutor.run: 予期しないエラー — task_id=%s error=%s",
                state.get("task_id"), exc, exc_info=True,
            )
            state["error"] = str(exc)
            return state

        # HitL ノード自体が pending フラグを立てた場合もここで永続化
        if result.get("human_approval_pending") and not result.get("approval_execution_id"):
            execution_id = await self._persist_pending(result)
            result["approval_execution_id"] = execution_id

        return result

    # -------------------------------------------------------------- resume --

    async def resume(
        self, execution_id: str, approved_output: dict[str, Any]
    ) -> AgentState:
        """
        HitL 承認後にパイプラインを再開する。

        execution_logs から保存済み状態を読み出し、``approved_output`` をマージして
        グラフを再実行する。承認記録を execution_logs に書き戻す。

        Args:
            execution_id: ``approval_execution_id`` に保存した execution_logs.id。
            approved_output: 承認者が確認・修正した出力データ。
        """
        saved_state = await self._load_pending(execution_id)
        if saved_state is None:
            raise ValueError(
                f"execution_id={execution_id!r} が見つからないか、"
                "approval_status='pending' でありません"
            )

        # 承認済みデータをコンテキストにマージ
        saved_state["human_approval_pending"] = False
        saved_state["approval_execution_id"] = None
        saved_state["context"] = {
            **saved_state.get("context", {}),
            "approved_output": approved_output,
        }

        # execution_logs に承認記録を書き込む
        await self._mark_approved(execution_id, approved_output)

        # グラフを再実行（再開ポイントはグラフ定義側で管理）
        return await self.run(saved_state)

    # ----------------------------------------------------------- internals --

    async def _persist_pending(self, state: AgentState) -> str:
        """
        HitL 中断状態を execution_logs に保存し、生成した execution_id を返す。
        DB クライアントがない場合はダミー UUID を返す。
        """
        execution_id = str(uuid.uuid4())

        if self._db is None:
            logger.debug(
                "_persist_pending: db_client is None — skipping DB write "
                "(execution_id=%s)", execution_id
            )
            return execution_id

        payload: dict[str, Any] = {
            "id": execution_id,
            "company_id": state.get("company_id", ""),
            "pipeline_name": state.get("pipeline_name", ""),
            "task_id": state.get("task_id", ""),
            "approval_status": "pending",
            "original_output": json.dumps(
                state.get("final_output") or {}, ensure_ascii=False
            ),
            "context_snapshot": json.dumps(
                state.get("context", {}), ensure_ascii=False, default=str
            ),
            "steps_completed": state.get("steps_completed", []),
            "current_step": state.get("current_step", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._db.table("execution_logs").insert(payload).execute()
        except Exception as exc:
            logger.error(
                "_persist_pending: DB insert 失敗 — %s", exc, exc_info=True
            )

        return execution_id

    async def _load_pending(self, execution_id: str) -> AgentState | None:
        """
        execution_logs から承認待ちレコードを読み出して AgentState を復元する。
        DB クライアントがない場合は None を返す。
        """
        if self._db is None:
            return None

        try:
            resp = (
                await self._db
                .table("execution_logs")
                .select("*")
                .eq("id", execution_id)
                .eq("approval_status", "pending")
                .single()
                .execute()
            )
        except Exception as exc:
            logger.error(
                "_load_pending: DB select 失敗 — execution_id=%s error=%s",
                execution_id, exc, exc_info=True,
            )
            return None

        row: dict[str, Any] = resp.data
        if not row:
            return None

        context_raw = row.get("context_snapshot") or "{}"
        original_output_raw = row.get("original_output") or "{}"
        try:
            context = json.loads(context_raw) if isinstance(context_raw, str) else context_raw
        except json.JSONDecodeError:
            context = {}
        try:
            original_output = (
                json.loads(original_output_raw)
                if isinstance(original_output_raw, str)
                else original_output_raw
            )
        except json.JSONDecodeError:
            original_output = {}

        return AgentState(
            task_id=row.get("task_id", ""),
            company_id=row.get("company_id", ""),
            pipeline_name=row.get("pipeline_name", ""),
            context=context,
            steps_completed=row.get("steps_completed") or [],
            human_approval_pending=True,
            approval_execution_id=execution_id,
            current_step=row.get("current_step", ""),
            final_output=original_output or None,
            error=None,
        )

    async def _mark_approved(
        self, execution_id: str, approved_output: dict[str, Any]
    ) -> None:
        """execution_logs のレコードを approved 状態に更新する。"""
        if self._db is None:
            return

        try:
            await (
                self._db
                .table("execution_logs")
                .update({
                    "approval_status": "approved",
                    "approved_at": datetime.now(timezone.utc).isoformat(),
                    "modified_output": json.dumps(
                        approved_output, ensure_ascii=False
                    ),
                })
                .eq("id", execution_id)
                .execute()
            )
        except Exception as exc:
            logger.error(
                "_mark_approved: DB update 失敗 — execution_id=%s error=%s",
                execution_id, exc, exc_info=True,
            )


# ─── HitL 中断シグナル ────────────────────────────────────────────────────────

class _HitLInterrupt(Exception):
    """
    LangGraph ノード内で HitL 中断が必要な場合に raise する内部例外。
    AgentExecutor.run が捕捉し、pending 保存・サスペンドを行う。
    """

    def __init__(self, state: AgentState) -> None:
        self.state = state
        super().__init__("HitL interrupt")


def require_human_approval(state: AgentState) -> None:
    """
    LangGraph ノード内から呼び出すヘルパー。
    HitL が必要と判断した場合に _HitLInterrupt を raise する。

    Usage (ノード内):
        def node_hitl_check(state: AgentState) -> AgentState:
            if needs_approval(state):
                require_human_approval(state)
            return state
    """
    raise _HitLInterrupt(state)


# ─── グラフビルダーヘルパー ───────────────────────────────────────────────────

def build_simple_graph(
    nodes: list[tuple[str, Callable[[AgentState], AgentState]]],
    entry_point: str,
) -> Any:
    """
    シーケンシャルなノードリストから LangGraph の CompiledGraph を構築する。

    LangGraph が未インストールの場合は ``None`` を返す（フォールバック用）。

    Args:
        nodes: [(ノード名, ノード関数), ...] のリスト（順番通りに接続される）
        entry_point: 最初に実行するノード名

    Returns:
        CompiledGraph または None
    """
    if not _LANGGRAPH_AVAILABLE:
        logger.warning(
            "build_simple_graph: langgraph が未インストールのため None を返します。"
            "pip install langgraph でインストールしてください。"
        )
        return None

    graph: StateGraph = StateGraph(AgentState)  # type: ignore[arg-type]

    for name, fn in nodes:
        graph.add_node(name, fn)

    # シーケンシャル接続
    node_names = [name for name, _ in nodes]
    for i, name in enumerate(node_names):
        if i < len(node_names) - 1:
            graph.add_edge(name, node_names[i + 1])
        else:
            graph.add_edge(name, END)

    graph.set_entry_point(entry_point)
    return graph.compile()
