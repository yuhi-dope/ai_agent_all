"""BPO Manager — ConditionEvaluator。knowledge_relationsのtriggers連鎖を評価する。"""
import logging
from typing import Any

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 組み込み条件連鎖定義（セールス・CS ドメイン）
# --------------------------------------------------------------------------
# knowledge_relations に登録されていない場合のデフォルト連鎖ルール。
# 各エントリは "ある条件（source_condition）が真なら→ターゲットパイプラインを発火" を表す。
#
# フィールド:
#   name             : 連鎖の識別名（ログ用）
#   source_condition : 評価する条件（proactive_proposals / company_state_snapshots を参照）
#   target_pipeline  : 発火するパイプライン（PIPELINE_REGISTRY のキー）
#   execution_level  : ExecutionLevel 値（int）
#   estimated_impact : 0〜1
#   input_data       : パイプラインへ渡す追加パラメータ
#   description      : 人間向け説明
# --------------------------------------------------------------------------
BUILTIN_SALES_CONDITION_CHAINS: list[dict[str, Any]] = [
    {
        # ヘルススコアが 30 以下 → 解約リスクアラート + 解約フロー準備
        "name": "low_health_score_to_cancellation_risk",
        "source_condition": {
            "table": "proactive_proposals",
            "filter": {"proposal_type": "health_alert", "status": "active"},
            "threshold_field": "impact_score",
            "threshold_operator": "gte",
            "threshold_value": 0.7,
        },
        "target_pipeline": "sales/cancellation",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.8,
        "input_data": {"mode": "risk_alert", "reason": "health_score_low"},
        "description": "ヘルススコア低下 → 解約リスクアラート（解約フロー事前準備）",
    },
    {
        # 未解決チケットが SLA 超過 → エスカレーション通知
        "name": "sla_breach_to_escalation",
        "source_condition": {
            "table": "proactive_proposals",
            "filter": {"proposal_type": "sla_breach", "status": "active"},
            "threshold_field": "impact_score",
            "threshold_operator": "gte",
            "threshold_value": 0.6,
        },
        "target_pipeline": "sales/support_auto_response",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.7,
        "input_data": {"mode": "escalation"},
        "description": "SLA超過チケット → サポートエスカレーション",
    },
    {
        # アップセル提案スコア高 + 未対応 → フォローアップリマインダー
        "name": "upsell_proposal_followup",
        "source_condition": {
            "table": "proactive_proposals",
            "filter": {"proposal_type": "upsell_opportunity", "status": "pending"},
            "threshold_field": "impact_score",
            "threshold_operator": "gte",
            "threshold_value": 0.65,
        },
        "target_pipeline": "sales/upsell_briefing",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.65,
        "input_data": {"mode": "followup_reminder"},
        "description": "未対応アップセル提案 → フォローアップリマインダー生成",
    },
    {
        # 失注後 30 日以内に再提案候補 → PDCA ループへ投入
        "name": "lost_deal_reengagement",
        "source_condition": {
            "table": "proactive_proposals",
            "filter": {"proposal_type": "reengagement_candidate", "status": "active"},
            "threshold_field": "impact_score",
            "threshold_operator": "gte",
            "threshold_value": 0.5,
        },
        "target_pipeline": "sales/win_loss_feedback",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {"mode": "reengagement_pdca"},
        "description": "失注後再提案候補 → PDCA ループへ投入",
    },
]

# --------------------------------------------------------------------------
# 動的DB評価用 BUILTIN_CONDITION_CHAINS
# --------------------------------------------------------------------------
# proactive_proposals に依存せず、実際の業務テーブルを直接参照して条件を判定する。
# evaluator は company_id を受け取り、条件に合致するレコードリストを返す非同期関数。
# --------------------------------------------------------------------------
BUILTIN_CONDITION_CHAINS: list[dict[str, Any]] = [
    {
        "name": "health_score_low",
        "target_pipeline": "sales/cancellation",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.8,
        "input_data": {"mode": "risk_alert", "reason": "health_score_low"},
        "description": "ヘルススコアが閾値以下の顧客 → 解約リスクアラート",
        "evaluator_key": "health_score_low",
    },
    {
        "name": "sla_breach",
        "target_pipeline": "sales/support_auto_response",
        "execution_level": ExecutionLevel.NOTIFY_ONLY,
        "estimated_impact": 0.7,
        "input_data": {"mode": "escalation"},
        "description": "SLA超過チケット → サポートエスカレーション",
        "evaluator_key": "sla_breach",
    },
    {
        "name": "upsell_high",
        "target_pipeline": "sales/upsell_briefing",
        "execution_level": ExecutionLevel.DRAFT_CREATE,
        "estimated_impact": 0.65,
        "input_data": {"mode": "followup_reminder"},
        "description": "ヘルススコア高 + 契約6ヶ月以上の顧客 → アップセル提案",
        "evaluator_key": "upsell_high",
    },
    {
        "name": "lost_reengagement",
        "target_pipeline": "sales/win_loss_feedback",
        "execution_level": ExecutionLevel.DATA_COLLECT,
        "estimated_impact": 0.5,
        "input_data": {"mode": "reengagement_pdca"},
        "description": "失注後30日以内の案件 → 再エンゲージメントPDCA",
        "evaluator_key": "lost_reengagement",
    },
]

# --------------------------------------------------------------------------
# 動的条件評価関数の登録テーブル
# --------------------------------------------------------------------------
# 各キーは BUILTIN_CONDITION_CHAINS の evaluator_key に対応する。
# company_id を受け取り、条件に合致するレコード（dict のリスト）を返す。
# --------------------------------------------------------------------------

async def _eval_health_score_low(company_id: str, db: Any) -> list[dict]:
    """customersテーブルからhealth_score <= 30 のレコードを取得する。"""
    try:
        result = (
            db.table("customers")
            .select("id, name, health_score")
            .eq("company_id", company_id)
            .lte("health_score", 30)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"_eval_health_score_low error: {e}")
        return []


async def _eval_sla_breach(company_id: str, db: Any) -> list[dict]:
    """support_ticketsテーブルからSLA超過かつ未解決のチケットを取得する。"""
    try:
        result = (
            db.table("support_tickets")
            .select("id, title, sla_due_at, status, customer_id")
            .eq("company_id", company_id)
            .lt("sla_due_at", "now()")
            .not_.in_("status", ["resolved", "closed"])
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"_eval_sla_breach error: {e}")
        return []


async def _eval_upsell_high(company_id: str, db: Any) -> list[dict]:
    """customersテーブルからhealth_score >= 80 かつ契約6ヶ月以上の顧客を取得する。"""
    try:
        from datetime import datetime, timezone, timedelta
        six_months_ago = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        result = (
            db.table("customers")
            .select("id, name, health_score, contract_started_at")
            .eq("company_id", company_id)
            .gte("health_score", 80)
            .lte("contract_started_at", six_months_ago)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"_eval_upsell_high error: {e}")
        return []


async def _eval_lost_reengagement(company_id: str, db: Any) -> list[dict]:
    """opportunitiesテーブルからstage='lost'かつ30日以内に更新された案件を取得する。"""
    try:
        from datetime import datetime, timezone, timedelta
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        result = (
            db.table("opportunities")
            .select("id, title, stage, updated_at, customer_id")
            .eq("company_id", company_id)
            .eq("stage", "lost")
            .gte("updated_at", thirty_days_ago)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"_eval_lost_reengagement error: {e}")
        return []


# evaluator_key → 評価関数のマッピング
_EVALUATOR_REGISTRY: dict[str, Any] = {
    "health_score_low": _eval_health_score_low,
    "sla_breach": _eval_sla_breach,
    "upsell_high": _eval_upsell_high,
    "lost_reengagement": _eval_lost_reengagement,
}

# --------------------------------------------------------------------------
# knowledge_relations の condition フィールド動的評価
# --------------------------------------------------------------------------
# condition フォーマット:
#   {"field": "health_score", "operator": "<=", "threshold": 30, "table": "customers"}
# --------------------------------------------------------------------------

_OPERATOR_MAP: dict[str, Any] = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

_SUPPORTED_TABLES: frozenset[str] = frozenset(
    ["customers", "support_tickets", "opportunities", "execution_logs"]
)


async def _evaluate_dynamic_condition(
    company_id: str,
    db: Any,
    condition: dict[str, Any],
) -> list[dict]:
    """
    condition フィールドを解析してDBを動的に参照し、
    条件に合致するレコードリストを返す。

    condition フォーマット:
      {
        "field": "health_score",
        "operator": "<=",
        "threshold": 30,
        "table": "customers"   # 省略時は "customers"
      }
    """
    field = condition.get("field")
    operator_str = condition.get("operator")
    threshold = condition.get("threshold")
    table = condition.get("table", "customers")

    if not field or not operator_str or threshold is None:
        logger.debug(f"_evaluate_dynamic_condition: conditionフィールド不足 {condition}")
        return []

    if table not in _SUPPORTED_TABLES:
        logger.warning(f"_evaluate_dynamic_condition: 非対応テーブル '{table}'")
        return []

    operator_fn = _OPERATOR_MAP.get(operator_str)
    if operator_fn is None:
        logger.warning(f"_evaluate_dynamic_condition: 非対応operator '{operator_str}'")
        return []

    try:
        # DB から全レコードを取得し Python 側でフィルタ
        # （Supabase クライアントの動的operator生成の複雑さを回避）
        result = (
            db.table(table)
            .select(f"id, {field}")
            .eq("company_id", company_id)
            .execute()
        )
        rows = result.data or []

        matched = []
        for row in rows:
            actual = row.get(field)
            if actual is None:
                continue
            try:
                if operator_fn(actual, threshold):
                    matched.append(row)
            except (TypeError, ValueError):
                continue

        return matched

    except Exception as e:
        logger.warning(f"_evaluate_dynamic_condition error (table={table}, field={field}): {e}")
        return []


async def evaluate_knowledge_triggers(company_id: str) -> list[BPOTask]:
    """
    knowledge_relations テーブルの relation_type="triggers" を評価する。

    ソース知識アイテムの条件が満たされている場合に
    ターゲット知識アイテムをBPOTaskとして返す。

    例: "残業45時間超え" (source) → "産業医面談通知" (target pipeline)
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        # triggers関係を持つknowledge_relationsを取得
        relations_result = db.table("knowledge_relations").select(
            "source_id, target_id, metadata"
        ).eq("company_id", company_id).eq("relation_type", "triggers").execute()

        relations = relations_result.data or []

        tasks: list[BPOTask] = []

        # knowledge_relations がある場合のみ knowledge_items を取得して評価
        sources: dict[str, Any] = {}
        targets: dict[str, Any] = {}
        if relations:
            # ソースIDリスト取得
            source_ids = list({r["source_id"] for r in relations})
            sources_result = db.table("knowledge_items").select(
                "id, title, metadata, confidence, is_active"
            ).eq("company_id", company_id).in_("id", source_ids).execute()

            sources = {s["id"]: s for s in (sources_result.data or [])}

            # ターゲットIDリスト取得
            target_ids = list({r["target_id"] for r in relations})
            targets_result = db.table("knowledge_items").select(
                "id, title, metadata, confidence"
            ).eq("company_id", company_id).in_("id", target_ids).execute()

            targets = {t["id"]: t for t in (targets_result.data or [])}

        for relation in relations:
            source = sources.get(relation["source_id"])
            target = targets.get(relation["target_id"])

            if not source or not target:
                continue
            if not source.get("is_active"):
                continue

            source_meta = source.get("metadata") or {}
            target_meta = target.get("metadata") or {}
            relation_meta = relation.get("metadata") or {}

            pipeline = target_meta.get("pipeline", "")
            if not pipeline:
                continue

            condition_met = False

            # ── 動的条件評価（condition フィールドがある場合） ──────────────────
            condition_def = source_meta.get("condition") or relation_meta.get("condition")
            if condition_def and isinstance(condition_def, dict):
                try:
                    matched_records = await _evaluate_dynamic_condition(
                        company_id, db, condition_def
                    )
                    condition_met = len(matched_records) > 0
                    if condition_met:
                        logger.debug(
                            f"condition_evaluator dynamic: source={source['id']} "
                            f"matched {len(matched_records)} records"
                        )
                except Exception as e:
                    logger.warning(
                        f"condition_evaluator dynamic eval error "
                        f"(source={source['id']}): {e} — fallback to static flag"
                    )
                    # フォールバック: 静的フラグ方式
                    condition_met = source_meta.get("condition_met", False)
            else:
                # フォールバック: 既存の静的フラグ方式
                condition_met = source_meta.get("condition_met", False)

            if not condition_met:
                continue

            tasks.append(BPOTask(
                company_id=company_id,
                pipeline=pipeline,
                trigger_type=TriggerType.CONDITION,
                execution_level=ExecutionLevel(target_meta.get("execution_level", 2)),
                input_data=target_meta.get("input_data", {}),
                estimated_impact=float(relation_meta.get("impact", target.get("confidence", 0.7))),
                knowledge_item_ids=[source["id"], target["id"]],
            ))

        logger.info(f"condition_evaluator DB: {len(tasks)} triggered tasks for {company_id}")

        # ── Step 2: BUILTIN_CONDITION_CHAINS の動的評価 ─────────────────────────
        dynamic_tasks = await _evaluate_builtin_condition_chains(company_id, db)
        tasks.extend(dynamic_tasks)

        # ── Step 3: 組み込み条件連鎖の評価（proactive_proposals 参照・旧方式） ──
        builtin_tasks = await _evaluate_builtin_sales_chains(company_id, db)
        tasks.extend(builtin_tasks)

        logger.info(f"condition_evaluator: total {len(tasks)} tasks for {company_id}")
        return tasks

    except Exception as e:
        logger.error(f"condition_evaluator error: {e}")
        return []


async def _evaluate_builtin_condition_chains(
    company_id: str,
    db: Any,
) -> list[BPOTask]:
    """
    BUILTIN_CONDITION_CHAINS を評価する。

    各チェーンの evaluator_key に対応する評価関数を呼び出し、
    レコードが1件以上あれば BPOTask を生成して返す。
    """
    tasks: list[BPOTask] = []

    for chain in BUILTIN_CONDITION_CHAINS:
        evaluator_key = chain.get("evaluator_key", "")
        evaluator_fn = _EVALUATOR_REGISTRY.get(evaluator_key)
        if evaluator_fn is None:
            logger.warning(f"_evaluate_builtin_condition_chains: evaluator '{evaluator_key}' 未登録")
            continue

        try:
            matched_records = await evaluator_fn(company_id, db)
        except Exception as e:
            logger.warning(
                f"_evaluate_builtin_condition_chains: chain '{chain['name']}' "
                f"evaluator error: {e}"
            )
            continue

        if not matched_records:
            continue

        matched_ids = [r.get("id") for r in matched_records if r.get("id")]

        tasks.append(BPOTask(
            company_id=company_id,
            pipeline=chain["target_pipeline"],
            trigger_type=TriggerType.CONDITION,
            execution_level=ExecutionLevel(chain["execution_level"]),
            input_data=dict(chain.get("input_data", {})),
            estimated_impact=float(chain.get("estimated_impact", 0.5)),
            knowledge_item_ids=[],
            context={
                "builtin_dynamic": True,
                "chain_name": chain["name"],
                "description": chain.get("description", ""),
                "matched_record_ids": matched_ids,
                "matched_count": len(matched_records),
            },
        ))
        logger.info(
            f"condition_evaluator builtin_dynamic: chain '{chain['name']}' fired "
            f"({len(matched_records)} records) → {chain['target_pipeline']} for {company_id}"
        )

    return tasks


async def _evaluate_builtin_sales_chains(
    company_id: str,
    db: Any,
) -> list[BPOTask]:
    """
    BUILTIN_SALES_CONDITION_CHAINS を評価し、条件を満たすタスクを返す。

    各チェーンの source_condition は proactive_proposals テーブルを参照し、
    指定フィルタで絞り込んだレコードが存在し、かつ impact_score が閾値を超えた場合に発火する。
    """
    tasks: list[BPOTask] = []

    for chain in BUILTIN_SALES_CONDITION_CHAINS:
        cond = chain["source_condition"]
        table = cond.get("table", "proactive_proposals")
        filters = cond.get("filter", {})
        threshold_field = cond.get("threshold_field", "impact_score")
        threshold_op = cond.get("threshold_operator", "gte")
        threshold_value = float(cond.get("threshold_value", 0.5))

        try:
            query = db.table(table).select(
                f"id, {threshold_field}"
            ).eq("company_id", company_id)

            # 追加フィルタを適用
            for key, val in filters.items():
                query = query.eq(key, val)

            result = query.limit(1).execute()
            rows = result.data or []

            if not rows:
                continue

            # 閾値チェック
            actual = rows[0].get(threshold_field)
            if actual is None:
                continue

            condition_met = False
            try:
                if threshold_op == "gte":
                    condition_met = float(actual) >= threshold_value
                elif threshold_op == "gt":
                    condition_met = float(actual) > threshold_value
                elif threshold_op == "lte":
                    condition_met = float(actual) <= threshold_value
                elif threshold_op == "lt":
                    condition_met = float(actual) < threshold_value
                elif threshold_op == "eq":
                    condition_met = float(actual) == threshold_value
            except (TypeError, ValueError):
                continue

            if not condition_met:
                continue

            tasks.append(BPOTask(
                company_id=company_id,
                pipeline=chain["target_pipeline"],
                trigger_type=TriggerType.CONDITION,
                execution_level=ExecutionLevel(chain["execution_level"]),
                input_data=dict(chain.get("input_data", {})),
                estimated_impact=float(chain.get("estimated_impact", 0.5)),
                knowledge_item_ids=[],
                context={
                    "builtin": True,
                    "chain_name": chain["name"],
                    "description": chain.get("description", ""),
                    "source_record_id": rows[0].get("id"),
                },
            ))
            logger.info(
                f"condition_evaluator builtin: chain '{chain['name']}' fired → "
                f"{chain['target_pipeline']} for {company_id}"
            )

        except Exception as e:
            logger.warning(f"condition_evaluator builtin chain '{chain['name']}' error: {e}")
            continue

    return tasks
