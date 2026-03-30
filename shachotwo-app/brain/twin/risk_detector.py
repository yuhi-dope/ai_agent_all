"""リスク検知モジュール — ルールベース + LLMベースの二段構成。

- detect_risks(snapshot): TwinSnapshot からルールベースでリスクを列挙する（Phase 1 MVP）
- detect_risks_with_llm(twin_snapshot, knowledge_items, ...): ルールベース＋LLMの統合詳細分析
- detect_risks_llm(company_id, supabase): DB から状態を取得しLLMで深層分析、proactive_proposals に書き込む
"""
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from brain.twin.models import TwinSnapshot
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

# ルールベース閾値
_PROCESS_COMPLETENESS_LOW_THRESHOLD = 0.3
_MANUAL_TOOLS_HIGH_THRESHOLD = 3

# LLM分析で取得するナレッジの最大件数
_MAX_KNOWLEDGE_ITEMS = 60
_MAX_SNAPSHOT_HISTORY = 3


# ---------------------------------------------------------------------------
# Pydanticモデル
# ---------------------------------------------------------------------------

class RiskAlert(BaseModel):
    """LLMが検知した個別リスクアラート。"""
    category: str  # personnel / finance / customer / compliance / operations
    title: str
    description: str
    severity: int = Field(ge=1, le=5)      # 1=低〜5=緊急
    confidence: float = Field(ge=0, le=1)  # LLMの確信度
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str = ""


class RiskDetectionResult(BaseModel):
    """LLMリスク検知の実行結果。"""
    alerts: list[RiskAlert]
    model_used: str
    cost_yen: float
    proposal_ids: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    """detect_risks_with_llm が返す統合リスクアイテム。

    ルールベース検出・LLM検出のどちらでも生成される共通フォーマット。
    """
    risk_id: str
    category: str  # operation / compliance / finance / personnel / market
    severity: str  # critical / high / medium / low
    probability: float = Field(ge=0.0, le=1.0)
    title: str
    description: str
    impact: str = ""
    mitigation: str = ""
    data_basis: str = ""
    source: str = "rule"  # "rule" | "llm"


# ---------------------------------------------------------------------------
# ルールベース実装（Phase 1 MVP）
# ---------------------------------------------------------------------------

async def detect_risks(snapshot: TwinSnapshot) -> list[dict]:
    """現在のツインスナップショットからリスクをルールベースで検出する。

    検出ルール:
    1. process.completeness < 0.3    → "業務フロー文書化が不足"
    2. people.skill_gaps が空でない  → "スキルギャップあり: {gaps}"
    3. tool.manual_tools が3件以上  → "手動業務が多い: 自動化機会あり"
    4. risk.severity_high > 0        → "高リスク {n}件 未対応"
    5. cost.monthly_fixed_cost == 0  → "コスト情報未登録"

    Args:
        snapshot: TwinSnapshot — 現在の会社状態

    Returns:
        検出されたリスクのリスト。各要素は以下のキーを持つ dict:
        - type (str): リスク種別
        - severity (str): "high" / "medium" / "low"
        - message (str): ユーザー向けメッセージ
    """
    risks: list[dict] = []

    # 1. 業務フロー文書化不足
    if snapshot.process.completeness < _PROCESS_COMPLETENESS_LOW_THRESHOLD:
        risks.append({
            "type": "process_documentation_insufficient",
            "severity": "high",
            "message": "業務フロー文書化が不足しています（充足度 "
                       f"{snapshot.process.completeness:.0%}）。ナレッジ登録を進めてください。",
        })

    # 2. スキルギャップあり
    if snapshot.people.skill_gaps:
        gaps_text = "、".join(snapshot.people.skill_gaps[:5])
        risks.append({
            "type": "skill_gap_detected",
            "severity": "medium",
            "message": f"スキルギャップあり: {gaps_text}",
        })

    # 3. 手動ツールが多い
    if len(snapshot.tool.manual_tools) >= _MANUAL_TOOLS_HIGH_THRESHOLD:
        risks.append({
            "type": "manual_tools_excessive",
            "severity": "medium",
            "message": (
                f"手動業務が多い（{len(snapshot.tool.manual_tools)}件）: "
                "自動化機会があります。コネクタ連携を検討してください。"
            ),
        })

    # 4. 高リスク未対応
    if snapshot.risk.severity_high > 0:
        risks.append({
            "type": "high_severity_risk_open",
            "severity": "high",
            "message": f"高リスク {snapshot.risk.severity_high}件 が未対応です。早急に対処してください。",
        })

    # 5. コスト情報未登録
    if snapshot.cost.monthly_fixed_cost == 0:
        risks.append({
            "type": "cost_info_missing",
            "severity": "low",
            "message": "コスト情報が未登録です。月次固定費を入力すると収益分析が有効になります。",
        })

    logger.debug(
        "detect_risks: company_id=%s, detected %d risks",
        snapshot.company_id,
        len(risks),
    )
    return risks


# ---------------------------------------------------------------------------
# LLMベース実装（深層分析 + proactive_proposals 書き込み）
# ---------------------------------------------------------------------------

_SYSTEM_RISK_DETECTION = """あなたは企業リスク分析の専門家です。
提供された会社の状態スナップショットとナレッジベースを分析し、潜在的なリスクを検知してください。

## リスクカテゴリ
- personnel: 人材リスク（退職リスク、キーパーソン依存、スキル不足）
- finance: 財務リスク（資金繰り、コスト増大、収益悪化）
- customer: 顧客リスク（取引集中、解約リスク、クレーム傾向）
- compliance: コンプライアンスリスク（法令違反、規制変更対応不足）
- operations: 業務リスク（属人化、プロセス不備、システム障害）

## 出力形式（JSON配列）
[
  {
    "category": "personnel|finance|customer|compliance|operations",
    "title": "リスクタイトル（30文字以内）",
    "description": "リスクの詳細説明",
    "severity": 1-5,
    "confidence": 0.0-1.0,
    "evidence": ["根拠となる情報1", "根拠となる情報2"],
    "recommended_action": "推奨対応策"
  }
]

重要:
- severity 4-5 は本当に深刻なリスクのみ
- confidence は根拠の明確さに基づいて厳密に評価
- 最大5件まで、優先度の高いものから列挙
- 日本語で出力
"""


def _extract_json_array(content: str) -> str:
    """LLMレスポンスからJSON配列を抽出する。"""
    text = content.strip()
    # コードブロックを除去
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip()
    # JSON配列を直接探す
    start = text.find("[")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


async def detect_risks_llm(
    company_id: str,
    supabase: Any,
) -> RiskDetectionResult:
    """会社の状態とナレッジベースからLLMでリスクを検知し、提案を生成する。

    1. company_state_snapshots から最新状態を取得
    2. knowledge_items からリスク関連ナレッジを取得
    3. LLM（STANDARD）でリスク分析
    4. proactive_proposals に status="proposed" で書き込み（人間承認待ち）

    Args:
        company_id: テナントID（RLS必須）
        supabase: Supabaseクライアント

    Returns:
        RiskDetectionResult — 検知されたリスクと提案ID一覧
    """
    # 1. 最新スナップショットを取得
    snap_result = supabase.table("company_state_snapshots") \
        .select("*") \
        .eq("company_id", company_id) \
        .order("snapshot_at", desc=True) \
        .limit(_MAX_SNAPSHOT_HISTORY) \
        .execute()
    snapshots = snap_result.data or []

    # 2. リスク関連ナレッジを取得
    knowledge_result = supabase.table("knowledge_items") \
        .select("id, title, content, department, category, item_type, confidence") \
        .eq("company_id", company_id) \
        .eq("is_active", True) \
        .order("created_at", desc=True) \
        .limit(_MAX_KNOWLEDGE_ITEMS) \
        .execute()
    knowledge_items = knowledge_result.data or []

    # 3. コンテキスト構築
    context = _build_risk_context(snapshots, knowledge_items)

    # 4. LLM呼び出し
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": _SYSTEM_RISK_DETECTION},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.STANDARD,
        task_type="risk_detection",
        company_id=company_id,
        max_tokens=2048,
        temperature=0.2,
    ))

    # 5. レスポンスをパース
    alerts = _parse_risk_alerts(response.content)

    # 6. proactive_proposals に書き込み
    proposal_ids = await _save_risk_proposals(supabase, company_id, alerts)

    logger.info(
        "detect_risks_llm: company_id=%s, detected %d alerts, saved %d proposals",
        company_id,
        len(alerts),
        len(proposal_ids),
    )

    return RiskDetectionResult(
        alerts=alerts,
        model_used=response.model_used,
        cost_yen=response.cost_yen,
        proposal_ids=proposal_ids,
    )


def _build_risk_context(snapshots: list[dict], knowledge_items: list[dict]) -> str:
    """リスク分析用のコンテキスト文字列を構築する。"""
    parts: list[str] = []

    if snapshots:
        parts.append("## 会社の現在状態（最新スナップショット）\n")
        latest = snapshots[0]
        for dim in ["people_state", "process_state", "cost_state", "tool_state", "risk_state"]:
            val = latest.get(dim)
            if val:
                label = dim.replace("_state", "")
                parts.append(f"- {label}: {json.dumps(val, ensure_ascii=False)[:400]}\n")
    else:
        parts.append("## 会社の現在状態\n- スナップショットなし（初期状態）\n")

    if knowledge_items:
        parts.append("\n## ナレッジベース（直近登録）\n")
        for i, item in enumerate(knowledge_items, 1):
            parts.append(
                f"[{i}] [{item.get('department', '不明')}] {item['title']} "
                f"(type={item.get('item_type', '?')}, "
                f"confidence={item.get('confidence', '?')})\n"
                f"  {item['content'][:200]}\n"
            )
    else:
        parts.append("\n## ナレッジベース\n- 登録ナレッジなし\n")

    parts.append("\n上記の情報から潜在的なリスクを検知し、JSON形式で出力してください。")
    return "".join(parts)


def _parse_risk_alerts(content: str) -> list[RiskAlert]:
    """LLMレスポンスをRiskAlertのリストにパースする。"""
    try:
        text = _extract_json_array(content)
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]

        alerts = []
        for raw in data:
            try:
                alerts.append(RiskAlert(
                    category=raw.get("category", "operations"),
                    title=str(raw.get("title", "リスク検知"))[:50],
                    description=str(raw.get("description", "")),
                    severity=max(1, min(5, int(raw.get("severity", 3)))),
                    confidence=float(raw.get("confidence", 0.5)),
                    evidence=raw.get("evidence", []),
                    recommended_action=str(raw.get("recommended_action", "")),
                ))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse risk alert: %s", e)
                continue

        return alerts

    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Risk alert parse failed: %s — raw content truncated: %s", e, content[:200])
        return []


async def _save_risk_proposals(
    supabase: Any,
    company_id: str,
    alerts: list[RiskAlert],
) -> list[str]:
    """リスクアラートを proactive_proposals テーブルに書き込む。

    status="proposed" で登録し、人間の承認を待つ。

    Returns:
        作成された提案のID一覧
    """
    if not alerts:
        return []

    rows = [
        {
            "company_id": company_id,
            "proposal_type": "risk_alert",
            "title": alert.title,
            "description": alert.description,
            "impact_estimate": {
                "severity": alert.severity,
                "confidence": alert.confidence,
                "risk_category": alert.category,
            },
            "evidence": {
                "signals": [
                    {"source": "llm_analysis", "value": ev, "score": alert.confidence}
                    for ev in alert.evidence
                ]
            },
            "status": "proposed",
        }
        for alert in alerts
    ]

    try:
        result = supabase.table("proactive_proposals").insert(rows).execute()
        inserted = result.data or []
        return [str(row["id"]) for row in inserted if "id" in row]
    except Exception as e:
        logger.error("Failed to save risk proposals to DB: %s", e)
        return []


# ---------------------------------------------------------------------------
# LLMベース詳細リスク検出（detect_risks_with_llm）
# ---------------------------------------------------------------------------

RISK_DETECTION_SYSTEM_PROMPT = """あなたは中小製造業の経営リスク分析の専門家です。
企業の現状データ（デジタルツイン5次元）とナレッジベースの情報から、
経営者が見落としがちなリスクを検出してください。

出力フォーマット（JSON）:
[
  {
    "risk_id": "risk_001",
    "category": "operation" | "compliance" | "finance" | "personnel" | "market",
    "severity": "critical" | "high" | "medium" | "low",
    "probability": 0.0-1.0,
    "title": "リスクの名称（20文字以内）",
    "description": "リスクの詳細説明（100文字以内）",
    "impact": "発生した場合の影響（50文字以内）",
    "mitigation": "推奨対策（50文字以内）",
    "data_basis": "この判断の根拠となったデータ"
  }
]

ルール:
- 最大10件のリスクを検出
- 重大度（severity）が高い順に並べる
- 根拠のないリスクは出さない
- 製造業特有のリスクを重視する

製造業で特に重要な検出パターン:
[コンプライアンスリスク]
- 労基法・安衛法・化審法等の法令改正への未対応
- 許認可の期限切れリスク
- 監査対応の準備不足

[オペレーションリスク]
- 属人化度が高い業務の担当者退職リスク
- 設備の老朽化による突発故障リスク
- 品質管理基準の形骸化リスク

[財務リスク]
- 原価率の上昇トレンド
- 特定顧客への売上依存度
- 運転資金の不足兆候

[人材リスク]
- 技術者の高齢化（暗黙知の消失リスク）
- 人手不足による残業増加
- 教育・訓練の不足
"""

# ルールベースリスクから severity 文字列への変換マップ
_SEVERITY_LABEL_MAP: dict[str, str] = {
    "high": "high",
    "medium": "medium",
    "low": "low",
}

# ルールベースリスクのカテゴリマッピング（type → category）
_RULE_CATEGORY_MAP: dict[str, str] = {
    "process_documentation_insufficient": "operation",
    "skill_gap_detected": "personnel",
    "manual_tools_excessive": "operation",
    "high_severity_risk_open": "operation",
    "cost_info_missing": "finance",
}

# ルールベース severity → probability のデフォルト値
_RULE_PROBABILITY_MAP: dict[str, float] = {
    "high": 0.7,
    "medium": 0.5,
    "low": 0.3,
}


def _rule_risk_to_risk_item(rule_risk: dict, index: int) -> RiskItem:
    """ルールベース検出結果（dict）を RiskItem に変換する。"""
    severity_str = rule_risk.get("severity", "medium")
    risk_type = rule_risk.get("type", f"rule_{index}")
    return RiskItem(
        risk_id=f"rule_{index:03d}_{risk_type}",
        category=_RULE_CATEGORY_MAP.get(risk_type, "operation"),
        severity=_SEVERITY_LABEL_MAP.get(severity_str, "medium"),
        probability=_RULE_PROBABILITY_MAP.get(severity_str, 0.5),
        title=rule_risk.get("message", "")[:20] or risk_type,
        description=rule_risk.get("message", ""),
        data_basis="rule_based_detection",
        source="rule",
    )


def _build_detailed_risk_context(
    twin_snapshot: TwinSnapshot,
    knowledge_items: list[dict],
    company_industry: str,
    existing_rule_risks: list[dict],
) -> str:
    """詳細リスク分析用コンテキストを構築する。"""
    parts: list[str] = []

    # 業種情報
    parts.append(f"## 対象企業の業種\n{company_industry}\n\n")

    # デジタルツイン5次元の状態
    parts.append("## デジタルツイン現状（5次元スナップショット）\n")
    parts.append(f"- 総合充足度: {twin_snapshot.overall_completeness:.0%}\n")

    # 人材次元
    people = twin_snapshot.people
    parts.append(
        f"- 人材: 従業員数={people.headcount}名, "
        f"主要役職={people.key_roles}, "
        f"スキルギャップ={people.skill_gaps}, "
        f"充足度={people.completeness:.0%}\n"
    )

    # プロセス次元
    process = twin_snapshot.process
    parts.append(
        f"- プロセス: 文書化フロー数={process.documented_flows}, "
        f"自動化率={process.automation_rate:.0%}, "
        f"ボトルネック={process.bottlenecks}, "
        f"充足度={process.completeness:.0%}\n"
    )

    # コスト次元
    cost = twin_snapshot.cost
    parts.append(
        f"- コスト: 月次固定費={cost.monthly_fixed_cost:,}円, "
        f"月次変動費={cost.monthly_variable_cost:,}円, "
        f"主要コスト項目={cost.top_cost_items}, "
        f"充足度={cost.completeness:.0%}\n"
    )

    # ツール次元
    tool = twin_snapshot.tool
    parts.append(
        f"- ツール: SaaSツール数={len(tool.saas_tools)}, "
        f"手動業務={tool.manual_tools}, "
        f"自動化機会={tool.automation_opportunities}, "
        f"充足度={tool.completeness:.0%}\n"
    )

    # リスク次元
    risk = twin_snapshot.risk
    parts.append(
        f"- リスク: 未対応リスク={risk.open_risks}, "
        f"高リスク件数={risk.severity_high}, "
        f"コンプライアンス項目={risk.compliance_items}, "
        f"充足度={risk.completeness:.0%}\n"
    )

    # ルールベース検出済みリスク
    if existing_rule_risks:
        parts.append("\n## ルールベース検出済みリスク\n")
        for i, r in enumerate(existing_rule_risks, 1):
            parts.append(
                f"[{i}] [{r.get('severity', '?')}] {r.get('message', '')}\n"
            )

    # ナレッジベース
    if knowledge_items:
        parts.append("\n## ナレッジベース（関連情報）\n")
        for i, item in enumerate(knowledge_items[:30], 1):
            parts.append(
                f"[{i}] [{item.get('department', '不明')}] {item.get('title', '')} "
                f"(type={item.get('item_type', '?')})\n"
                f"  {str(item.get('content', ''))[:150]}\n"
            )
    else:
        parts.append("\n## ナレッジベース\n- 登録ナレッジなし\n")

    parts.append(
        "\n上記の全情報を踏まえて、隠れたリスクを含む潜在的な経営リスクをJSON形式で出力してください。"
        "ルールベース検出リスクと重複しないものを優先してください。"
    )
    return "".join(parts)


def _parse_risk_items(content: str) -> list[RiskItem]:
    """LLMレスポンス（JSON配列）を RiskItem のリストにパースする。"""
    try:
        text = _extract_json_array(content)
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]

        items: list[RiskItem] = []
        for i, raw in enumerate(data):
            try:
                severity = str(raw.get("severity", "medium")).lower()
                if severity not in ("critical", "high", "medium", "low"):
                    severity = "medium"
                probability = float(raw.get("probability", 0.5))
                probability = max(0.0, min(1.0, probability))

                items.append(RiskItem(
                    risk_id=str(raw.get("risk_id", f"llm_{i:03d}")),
                    category=str(raw.get("category", "operation")),
                    severity=severity,
                    probability=probability,
                    title=str(raw.get("title", "リスク"))[:20],
                    description=str(raw.get("description", ""))[:100],
                    impact=str(raw.get("impact", ""))[:50],
                    mitigation=str(raw.get("mitigation", ""))[:50],
                    data_basis=str(raw.get("data_basis", "")),
                    source="llm",
                ))
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse risk item [%d]: %s", i, e)
                continue

        return items

    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Risk item parse failed: %s — raw content truncated: %s", e, content[:200])
        return []


def _dedup_risk_items(
    rule_items: list[RiskItem],
    llm_items: list[RiskItem],
) -> list[RiskItem]:
    """ルールベースとLLMの結果をマージして重複を排除する。

    重複判定: title の先頭10文字が一致するアイテムは後発側（LLM）を除去。
    severity 順（critical > high > medium > low）に並び替えて返す。
    """
    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

    seen_prefixes: set[str] = set()
    merged: list[RiskItem] = []

    for item in rule_items:
        prefix = item.title[:10]
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            merged.append(item)

    for item in llm_items:
        prefix = item.title[:10]
        if prefix not in seen_prefixes:
            seen_prefixes.add(prefix)
            merged.append(item)

    merged.sort(key=lambda r: _SEVERITY_ORDER.get(r.severity, 99))
    return merged


async def detect_risks_with_llm(
    twin_snapshot: TwinSnapshot,
    knowledge_items: list[dict],
    company_industry: str = "manufacturing",
    existing_rule_risks: list[dict] | None = None,
) -> list[RiskItem]:
    """LLMを使った詳細リスク検出。

    1. ルールベース検出（既存 detect_risks）を呼び出す
    2. TwinSnapshot・ナレッジ・ルールベース結果をコンテキストとしてLLMに渡す
    3. LLMが隠れたリスク（製造業特化パターン含む）を分析
    4. ルールベース結果とLLM結果を重複排除してマージ・返却

    Args:
        twin_snapshot: デジタルツインの現在スナップショット
        knowledge_items: ナレッジベースのアイテム一覧（dict形式）
        company_industry: 業種名（デフォルト: "manufacturing"）
        existing_rule_risks: 呼び出し元が既に実行したルールベース検出結果。
            None の場合はこの関数内で detect_risks を実行する。

    Returns:
        severity 降順でソートされた RiskItem のリスト（最大20件）
    """
    # 1. ルールベース検出
    if existing_rule_risks is None:
        existing_rule_risks = await detect_risks(twin_snapshot)

    rule_items = [
        _rule_risk_to_risk_item(r, i)
        for i, r in enumerate(existing_rule_risks)
    ]

    # 2. コンテキスト構築
    context = _build_detailed_risk_context(
        twin_snapshot=twin_snapshot,
        knowledge_items=knowledge_items,
        company_industry=company_industry,
        existing_rule_risks=existing_rule_risks,
    )

    # 3. LLM呼び出し
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": RISK_DETECTION_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        tier=ModelTier.STANDARD,
        task_type="risk_detection_detailed",
        company_id=twin_snapshot.company_id,
        max_tokens=3000,
        temperature=0.2,
    ))

    # 4. LLMレスポンスをパース
    llm_items = _parse_risk_items(response.content)

    # 5. 重複排除してマージ
    merged = _dedup_risk_items(rule_items, llm_items)

    logger.info(
        "detect_risks_with_llm: company_id=%s, rule=%d, llm=%d, merged=%d",
        twin_snapshot.company_id,
        len(rule_items),
        len(llm_items),
        len(merged),
    )

    # 最大20件に制限
    return merged[:20]
