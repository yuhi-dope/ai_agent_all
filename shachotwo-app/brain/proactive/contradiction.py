"""ナレッジ矛盾検知エンジン。

同一カテゴリ・部署のナレッジをペアで比較し、矛盾を検出。
検出した矛盾は knowledge_relations（contradicts）に記録し、
proactive_proposals（rule_challenge）として提案を生成する。
"""
import asyncio
import json
import logging
from itertools import combinations

from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

CONTRADICTION_PROMPT = """あなたはナレッジ管理の専門家です。
以下の2つのナレッジ項目が矛盾していないか判定してください。

## ナレッジA
タイトル: {title_a}
内容: {content_a}

## ナレッジB
タイトル: {title_b}
内容: {content_b}

## 判定基準
- 数値の不一致（例: 上限3万円 vs 3.5万円）
- ルールの衝突（例: 「必須」vs「任意」）
- 条件の矛盾（例: 適用対象が重複するのに異なる結論）

## 出力形式（JSON）
{{
  "is_contradiction": true/false,
  "confidence": 0.0-1.0,
  "explanation": "矛盾の説明（矛盾なしならnull）",
  "suggested_resolution": "解決案（矛盾なしならnull）"
}}
"""

# 1回の実行で比較するペア数上限
_MAX_PAIRS = 30
_LLM_TIMEOUT = 30


async def detect_contradictions(
    company_id: str,
    department: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """同一カテゴリ・部署内のナレッジ矛盾を検出。

    Args:
        company_id: テナントID（RLS用）
        department: 部署名フィルタ（省略時は全部署）
        category: カテゴリフィルタ（省略時は全カテゴリ）

    Returns:
        list of {"item_a": {...}, "item_b": {...}, "explanation": str, "confidence": float}
    """
    db = get_service_client()

    q = db.table("knowledge_items") \
        .select("id, title, content, department, category, item_type, confidence, updated_at") \
        .eq("company_id", company_id) \
        .eq("is_active", True) \
        .order("category") \
        .limit(100)

    if department:
        q = q.eq("department", department)
    if category:
        q = q.eq("category", category)

    result = q.execute()
    items = result.data or []

    if len(items) < 2:
        return []

    # 同一カテゴリ内のペアを生成
    by_category: dict[str, list[dict]] = {}
    for item in items:
        cat = item["category"]
        by_category.setdefault(cat, []).append(item)

    pairs: list[tuple[dict, dict]] = []
    for cat_items in by_category.values():
        if len(cat_items) < 2:
            continue
        for a, b in combinations(cat_items, 2):
            pairs.append((a, b))
            if len(pairs) >= _MAX_PAIRS:
                break
        if len(pairs) >= _MAX_PAIRS:
            break

    if not pairs:
        return []

    # LLMで矛盾判定（並列実行）
    llm = get_llm_client()

    async def check_pair(item_a: dict, item_b: dict) -> dict | None:
        prompt = CONTRADICTION_PROMPT.format(
            title_a=item_a["title"],
            content_a=item_a["content"][:500],
            title_b=item_b["title"],
            content_b=item_b["content"][:500],
        )
        try:
            resp = await asyncio.wait_for(
                llm.generate(LLMTask(
                    messages=[{"role": "user", "content": prompt}],
                    tier=ModelTier.FAST,
                    task_type="contradiction_check",
                    company_id=company_id,
                    max_tokens=512,
                )),
                timeout=_LLM_TIMEOUT,
            )
            data = json.loads(resp.content)
            if data.get("is_contradiction") and data.get("confidence", 0) >= 0.6:
                return {
                    "item_a": {"id": item_a["id"], "title": item_a["title"]},
                    "item_b": {"id": item_b["id"], "title": item_b["title"]},
                    "explanation": data.get("explanation", ""),
                    "suggested_resolution": data.get("suggested_resolution", ""),
                    "confidence": data.get("confidence", 0.5),
                }
        except asyncio.TimeoutError:
            logger.warning("contradiction check timed out for pair: %s / %s", item_a["id"], item_b["id"])
        except json.JSONDecodeError as e:
            logger.warning("contradiction check JSON parse failed: %s", e)
        except Exception as e:
            logger.warning("contradiction check failed: %s", e)
        return None

    tasks = [check_pair(a, b) for a, b in pairs]
    results = await asyncio.gather(*tasks)
    contradictions = [r for r in results if r is not None]

    # DBに矛盾関係を記録
    for c in contradictions:
        _record_contradiction(db, company_id, c)

    return contradictions


def _record_contradiction(db, company_id: str, contradiction: dict) -> None:
    """knowledge_relationsに矛盾を記録し、proactive_proposalsに提案を作成。"""
    # knowledge_relationsに追加（重複チェック）
    existing = db.table("knowledge_relations") \
        .select("id") \
        .eq("company_id", company_id) \
        .eq("source_id", contradiction["item_a"]["id"]) \
        .eq("target_id", contradiction["item_b"]["id"]) \
        .eq("relation_type", "contradicts") \
        .execute()

    if not existing.data:
        db.table("knowledge_relations").insert({
            "company_id": company_id,
            "source_id": contradiction["item_a"]["id"],
            "target_id": contradiction["item_b"]["id"],
            "relation_type": "contradicts",
            "description": contradiction["explanation"],
        }).execute()

    # proactive_proposalsに提案を作成
    db.table("proactive_proposals").insert({
        "company_id": company_id,
        "proposal_type": "rule_challenge",
        "title": f"ナレッジの矛盾検出: {contradiction['item_a']['title']} vs {contradiction['item_b']['title']}",
        "description": (
            f"以下のナレッジ間に矛盾が検出されました。\n\n"
            f"**ナレッジA**: {contradiction['item_a']['title']}\n"
            f"**ナレッジB**: {contradiction['item_b']['title']}\n\n"
            f"**矛盾内容**: {contradiction['explanation']}\n\n"
            f"**解決案**: {contradiction['suggested_resolution']}"
        ),
        "impact_estimate": json.dumps({
            "risk_reduction": contradiction["confidence"],
            "confidence": contradiction["confidence"],
            "calculation_basis": "ナレッジ矛盾はAI回答の信頼性を低下させるため優先的に解決が必要",
        }),
        "evidence": json.dumps({
            "signals": [
                {"source": "knowledge", "value": contradiction["item_a"]["title"], "score": contradiction["confidence"]},
                {"source": "knowledge", "value": contradiction["item_b"]["title"], "score": contradiction["confidence"]},
            ]
        }),
        "related_knowledge_ids": [contradiction["item_a"]["id"], contradiction["item_b"]["id"]],
        "status": "proposed",
    }).execute()
