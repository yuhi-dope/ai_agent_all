"""デジタルツイン自動更新 — knowledge_items からツイン各次元を集計する。

item_type ごとに対応する次元へ集計し、completeness = min(count / 10, 1.0) で算出する。
LLM不要のルールベース実装（Phase 1 MVP）。
"""
import logging

from brain.twin.models import (
    CostState,
    PeopleState,
    ProcessState,
    RiskState,
    ToolState,
    TwinSnapshot,
)
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# item_type ごとの件数をcompletenessに変換するときの分母
_COMPLETENESS_DENOMINATOR = 10


def _completeness(count: int) -> float:
    """count件のナレッジから充足度 0.0-1.0 を算出する。"""
    return round(min(count / _COMPLETENESS_DENOMINATOR, 1.0), 4)


async def analyze_and_update_twin(
    company_id: str,
    knowledge_items: list[dict],
) -> TwinSnapshot:
    """ナレッジアイテムのリストからデジタルツインを更新する。

    knowledge_item の item_type 対応:
      - "rule"  → process.decision_rules カウント
      - "flow"  → process.documented_flows カウント
      - "role"  → people.key_roles に追加
      - "tool"  → tool.saas_tools に追加
      - "cost"  → cost.top_cost_items に追加
      - "risk"  → risk.open_risks に追加

    スナップショットはDBの company_state_snapshots テーブルに保存される。

    Args:
        company_id: テナントID
        knowledge_items: knowledge_items テーブルの行リスト
            各行に "item_type", "title", "content" を期待する

    Returns:
        更新後の TwinSnapshot
    """
    snapshot = TwinSnapshot(company_id=company_id)

    # 集計用カウンタ
    role_items: list[str] = []
    tool_items: list[str] = []
    cost_items: list[str] = []
    risk_items: list[str] = []
    flow_count = 0
    rule_count = 0

    for item in knowledge_items:
        item_type = item.get("item_type", "")
        title = item.get("title", "")

        if item_type == "rule":
            rule_count += 1
        elif item_type == "flow":
            flow_count += 1
        elif item_type == "role":
            if title and title not in role_items:
                role_items.append(title)
        elif item_type == "tool":
            if title and title not in tool_items:
                tool_items.append(title)
        elif item_type == "cost":
            if title and title not in cost_items:
                cost_items.append(title)
        elif item_type == "risk":
            if title and title not in risk_items:
                risk_items.append(title)
        else:
            logger.debug("Unknown item_type=%s, skipping", item_type)

    # --- people 次元 ---
    snapshot.people = PeopleState(
        key_roles=role_items,
        completeness=_completeness(len(role_items)),
    )

    # --- process 次元 ---
    process_count = rule_count + flow_count
    snapshot.process = ProcessState(
        documented_flows=flow_count,
        decision_rules=rule_count,
        completeness=_completeness(process_count),
    )

    # --- cost 次元 ---
    snapshot.cost = CostState(
        top_cost_items=cost_items,
        completeness=_completeness(len(cost_items)),
    )

    # --- tool 次元 ---
    snapshot.tool = ToolState(
        saas_tools=tool_items,
        completeness=_completeness(len(tool_items)),
    )

    # --- risk 次元 ---
    snapshot.risk = RiskState(
        open_risks=risk_items,
        severity_high=0,
        severity_medium=0,
        completeness=_completeness(len(risk_items)),
    )

    # overall_completeness を再計算
    snapshot.recalculate_overall_completeness()

    # DB に保存（非同期不要だが一貫性のため try/except で保護）
    try:
        _save_snapshot(company_id, snapshot)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save twin snapshot to DB: %s", exc)

    return snapshot


def _save_snapshot(company_id: str, snapshot: TwinSnapshot) -> None:
    """スナップショットを company_state_snapshots テーブルに INSERT する。"""
    db = get_service_client()
    db.table("company_state_snapshots").insert({
        "company_id": company_id,
        "people_state": snapshot.people.model_dump(),
        "process_state": snapshot.process.model_dump(),
        "cost_state": snapshot.cost.model_dump(),
        "tool_state": snapshot.tool.model_dump(),
        "risk_state": snapshot.risk.model_dump(),
    }).execute()
