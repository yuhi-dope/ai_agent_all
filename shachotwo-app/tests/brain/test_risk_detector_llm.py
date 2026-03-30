"""Tests for brain/twin/risk_detector.py — LLMベース実装のテスト。

ルールベース実装(detect_risks)は tests/brain/test_twin.py でカバー済み。
このファイルでは以下をテストする:
- detect_risks_llm: DB取得＋LLM分析＋proactive_proposals書き込み
- detect_risks_with_llm: TwinSnapshot＋ナレッジ＋LLMの統合詳細リスク検出
- RiskItem モデル・パーサー・コンテキストビルダー
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from brain.twin.models import (
    CostState,
    PeopleState,
    ProcessState,
    RiskState,
    ToolState,
    TwinSnapshot,
)
from brain.twin.risk_detector import (
    RiskAlert,
    RiskDetectionResult,
    RiskItem,
    _build_detailed_risk_context,
    _build_risk_context,
    _dedup_risk_items,
    _parse_risk_alerts,
    _parse_risk_items,
    detect_risks_llm,
    detect_risks_with_llm,
)


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_company_id() -> str:
    return str(uuid4())


def _make_snapshot_row(company_id: str) -> dict:
    return {
        "id": str(uuid4()),
        "company_id": company_id,
        "people_state": {
            "headcount": 15,
            "key_roles": ["営業マネージャー", "エンジニアリード"],
            "skill_gaps": ["Pythonスキル不足"],
            "completeness": 0.5,
        },
        "process_state": {
            "documented_flows": 3,
            "automation_rate": 0.2,
            "completeness": 0.3,
        },
        "cost_state": {
            "monthly_fixed_cost": 2_000_000,
            "completeness": 0.4,
        },
        "tool_state": {
            "manual_tools": ["Excel集計", "紙の申請書", "手書き台帳"],
            "completeness": 0.3,
        },
        "risk_state": {
            "open_risks": ["キーパーソン依存"],
            "severity_high": 1,
            "completeness": 0.2,
        },
        "snapshot_at": "2025-01-01T00:00:00Z",
    }


def _make_knowledge_item(title: str, item_type: str = "risk") -> dict:
    return {
        "id": str(uuid4()),
        "title": title,
        "content": f"{title}に関する詳細情報",
        "department": "全社",
        "category": "risk",
        "item_type": item_type,
        "confidence": 0.8,
    }


def _make_supabase_mock(
    snapshots: list[dict],
    knowledge_items: list[dict],
    proposal_ids: list[str] | None = None,
) -> MagicMock:
    """Supabaseクライアントのモックを生成する。"""
    mock_db = MagicMock()

    # chain用モック生成ヘルパー
    def _make_chain(data: list):
        result = MagicMock()
        result.data = data
        chain = MagicMock()
        chain.eq.side_effect = lambda *a, **kw: chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = result
        return chain

    # テーブル別にモックを設定
    proposal_rows = [{"id": pid} for pid in (proposal_ids or [str(uuid4())])]
    insert_result = MagicMock()
    insert_result.data = proposal_rows

    insert_chain = MagicMock()
    insert_chain.execute.return_value = insert_result

    snap_chain = _make_chain(snapshots)
    know_chain = _make_chain(knowledge_items)

    def _table_side_effect(table_name: str):
        if table_name == "company_state_snapshots":
            return MagicMock(select=lambda *a, **kw: snap_chain)
        elif table_name == "knowledge_items":
            return MagicMock(select=lambda *a, **kw: know_chain)
        elif table_name == "proactive_proposals":
            tbl = MagicMock()
            tbl.insert.return_value = insert_chain
            return tbl
        return MagicMock()

    mock_db.table.side_effect = _table_side_effect
    return mock_db


MOCK_LLM_ALERTS = json.dumps([
    {
        "category": "personnel",
        "title": "キーパーソン退職リスク",
        "description": "エンジニアリードが1名しかおらず、退職時に技術継承が困難になるリスクがあります",
        "severity": 4,
        "confidence": 0.75,
        "evidence": ["エンジニアリードが1名", "スキル文書化不足"],
        "recommended_action": "技術ドキュメントの整備と副担当者の育成を検討してください",
    },
    {
        "category": "operations",
        "title": "業務の属人化リスク",
        "description": "手動業務が3件以上あり、担当者不在時に業務停止するリスクがあります",
        "severity": 3,
        "confidence": 0.85,
        "evidence": ["Excel集計", "紙の申請書", "手書き台帳"],
        "recommended_action": "デジタル化・自動化の優先順位を設定してください",
    },
])


# ---------------------------------------------------------------------------
# TestRiskAlertModel
# ---------------------------------------------------------------------------

class TestRiskAlertModel:

    def test_risk_alert_valid_fields(self):
        """RiskAlert の必須フィールドが正しく設定される。"""
        alert = RiskAlert(
            category="personnel",
            title="退職リスク",
            description="テスト説明",
            severity=4,
            confidence=0.75,
        )
        assert alert.category == "personnel"
        assert alert.severity == 4
        assert alert.confidence == 0.75

    def test_severity_bounds(self):
        """severity は 1-5 の範囲。"""
        alert = RiskAlert(
            category="finance",
            title="テスト",
            description="テスト",
            severity=1,
            confidence=0.5,
        )
        assert alert.severity == 1

    def test_confidence_bounds(self):
        """confidence は 0-1 の範囲。"""
        alert = RiskAlert(
            category="compliance",
            title="テスト",
            description="テスト",
            severity=3,
            confidence=1.0,
        )
        assert alert.confidence == 1.0

    def test_default_evidence_and_action(self):
        """evidence と recommended_action はデフォルトで空。"""
        alert = RiskAlert(
            category="customer",
            title="テスト",
            description="テスト",
            severity=2,
            confidence=0.6,
        )
        assert alert.evidence == []
        assert alert.recommended_action == ""


# ---------------------------------------------------------------------------
# TestParseRiskAlerts
# ---------------------------------------------------------------------------

class TestParseRiskAlerts:

    def test_parse_valid_json_alerts(self):
        """正常なJSON配列をパースしてRiskAlertリストを返す。"""
        alerts = _parse_risk_alerts(MOCK_LLM_ALERTS)
        assert len(alerts) == 2
        assert alerts[0].category == "personnel"
        assert alerts[0].severity == 4
        assert alerts[1].category == "operations"

    def test_parse_with_codeblock(self):
        """コードブロックに囲まれたJSONも正しくパースできる。"""
        content = f"```json\n{MOCK_LLM_ALERTS}\n```"
        alerts = _parse_risk_alerts(content)
        assert len(alerts) == 2

    def test_parse_invalid_json_returns_empty(self):
        """無効なJSONは空リストを返す（クラッシュしない）。"""
        alerts = _parse_risk_alerts("これはJSONではありません")
        assert alerts == []

    def test_parse_severity_clamped_to_range(self):
        """severity が範囲外の場合でも安全にパースされる。"""
        raw = json.dumps([{
            "category": "finance",
            "title": "テスト",
            "description": "テスト",
            "severity": 10,  # 範囲外（5が上限）
            "confidence": 0.5,
        }])
        # severity=5 にクランプされること
        alerts = _parse_risk_alerts(raw)
        assert len(alerts) == 1
        assert alerts[0].severity == 5

    def test_parse_confidence_defaults_to_half(self):
        """confidence が指定されない場合 0.5 になる。"""
        raw = json.dumps([{
            "category": "operations",
            "title": "テスト",
            "description": "テスト",
            "severity": 2,
        }])
        alerts = _parse_risk_alerts(raw)
        assert len(alerts) == 1
        assert alerts[0].confidence == 0.5

    def test_parse_missing_category_defaults(self):
        """category が未指定でも "operations" をデフォルトにする。"""
        raw = json.dumps([{
            "title": "テスト",
            "description": "テスト",
            "severity": 2,
            "confidence": 0.5,
        }])
        alerts = _parse_risk_alerts(raw)
        assert len(alerts) == 1
        assert alerts[0].category == "operations"


# ---------------------------------------------------------------------------
# TestBuildRiskContext
# ---------------------------------------------------------------------------

class TestBuildRiskContext:

    def test_context_contains_scenario_info(self):
        """スナップショットがある場合、コンテキストに状態情報が含まれる。"""
        company_id = _make_company_id()
        snap = _make_snapshot_row(company_id)
        context = _build_risk_context([snap], [])
        assert "会社の現在状態" in context

    def test_context_no_snapshot_message(self):
        """スナップショットがない場合、初期状態メッセージが含まれる。"""
        context = _build_risk_context([], [])
        assert "スナップショットなし" in context

    def test_context_contains_knowledge_items(self):
        """ナレッジアイテムがある場合、コンテキストに含まれる。"""
        items = [_make_knowledge_item("退職リスク")]
        context = _build_risk_context([], items)
        assert "退職リスク" in context

    def test_context_contains_prompt_instruction(self):
        """コンテキストの末尾にLLMへの指示が含まれる。"""
        context = _build_risk_context([], [])
        assert "JSON形式で出力" in context


# ---------------------------------------------------------------------------
# TestDetectRisksLLM
# ---------------------------------------------------------------------------

class TestDetectRisksLLM:

    @pytest.mark.asyncio
    async def test_returns_risk_detection_result(self):
        """detect_risks_llm が RiskDetectionResult を返す。"""
        company_id = _make_company_id()
        snap = _make_snapshot_row(company_id)
        mock_db = _make_supabase_mock([snap], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ALERTS
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.05
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            result = await detect_risks_llm(company_id, mock_db)

        assert isinstance(result, RiskDetectionResult)
        assert len(result.alerts) == 2
        assert result.model_used == "gemini-2.0-flash"
        assert result.cost_yen == 0.05

    @pytest.mark.asyncio
    async def test_alerts_have_correct_categories(self):
        """検知されたアラートのカテゴリが正しい。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ALERTS
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.03
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            result = await detect_risks_llm(company_id, mock_db)

        categories = {a.category for a in result.alerts}
        assert "personnel" in categories
        assert "operations" in categories

    @pytest.mark.asyncio
    async def test_db_fetch_uses_company_id_filter(self):
        """DB取得時に company_id フィルタが適用される。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            await detect_risks_llm(company_id, mock_db)

        # table() が company_state_snapshots と knowledge_items で呼ばれていること
        table_calls = [call[0][0] for call in mock_db.table.call_args_list]
        assert "company_state_snapshots" in table_calls
        assert "knowledge_items" in table_calls

    @pytest.mark.asyncio
    async def test_proposals_saved_to_db(self):
        """検知されたリスクが proactive_proposals テーブルに保存される。"""
        company_id = _make_company_id()
        proposal_id = str(uuid4())
        mock_db = _make_supabase_mock([], [], proposal_ids=[proposal_id])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_ALERTS
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.05
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            result = await detect_risks_llm(company_id, mock_db)

        # proactive_proposals への insert が呼ばれていること
        table_calls = [call[0][0] for call in mock_db.table.call_args_list]
        assert "proactive_proposals" in table_calls

    @pytest.mark.asyncio
    async def test_empty_alerts_when_no_data(self):
        """データがない場合でも空のリストで正常に返る。"""
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            result = await detect_risks_llm(company_id, mock_db)

        assert isinstance(result, RiskDetectionResult)
        assert result.alerts == []

    @pytest.mark.asyncio
    async def test_llm_task_uses_standard_tier(self):
        """LLMタスクが STANDARD tier で呼ばれる。"""
        from llm.client import ModelTier
        company_id = _make_company_id()
        mock_db = _make_supabase_mock([], [])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "brain.twin.risk_detector.get_llm_client", return_value=mock_llm
        ):
            await detect_risks_llm(company_id, mock_db)

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        assert task.tier == ModelTier.STANDARD


# ---------------------------------------------------------------------------
# TestRiskItemModel
# ---------------------------------------------------------------------------

class TestRiskItemModel:

    def test_risk_item_valid_fields(self):
        """RiskItem の必須フィールドが正しく設定される。"""
        item = RiskItem(
            risk_id="risk_001",
            category="operation",
            severity="high",
            probability=0.7,
            title="設備老朽化リスク",
            description="主要設備の老朽化により突発故障リスクが高まっている",
        )
        assert item.risk_id == "risk_001"
        assert item.category == "operation"
        assert item.severity == "high"
        assert item.probability == 0.7
        assert item.source == "rule"  # デフォルト値

    def test_risk_item_source_llm(self):
        """source='llm' を指定できる。"""
        item = RiskItem(
            risk_id="llm_001",
            category="compliance",
            severity="critical",
            probability=0.9,
            title="労基法違反リスク",
            description="残業時間が法令上限を超過している可能性",
            source="llm",
        )
        assert item.source == "llm"

    def test_probability_bounds(self):
        """probability は 0.0-1.0 の範囲内。"""
        item = RiskItem(
            risk_id="risk_002",
            category="finance",
            severity="medium",
            probability=0.0,
            title="テスト",
            description="テスト",
        )
        assert item.probability == 0.0

    def test_optional_fields_default_empty(self):
        """impact / mitigation / data_basis はデフォルトで空文字。"""
        item = RiskItem(
            risk_id="risk_003",
            category="personnel",
            severity="low",
            probability=0.3,
            title="テスト",
            description="テスト",
        )
        assert item.impact == ""
        assert item.mitigation == ""
        assert item.data_basis == ""


# ---------------------------------------------------------------------------
# TestParseRiskItems
# ---------------------------------------------------------------------------

MOCK_LLM_RISK_ITEMS = json.dumps([
    {
        "risk_id": "risk_001",
        "category": "compliance",
        "severity": "high",
        "probability": 0.75,
        "title": "安衛法未対応",
        "description": "安全衛生法の最新改正に未対応の可能性があります",
        "impact": "行政処分・業務停止",
        "mitigation": "安全管理者への研修実施",
        "data_basis": "コンプライアンス項目未登録",
    },
    {
        "risk_id": "risk_002",
        "category": "personnel",
        "severity": "critical",
        "probability": 0.85,
        "title": "熟練工退職リスク",
        "description": "60代の熟練工が3名おり、暗黙知が継承されていないリスク",
        "impact": "製造品質の急激な低下",
        "mitigation": "技能伝承プログラムの緊急整備",
        "data_basis": "人材データ・スキルギャップ情報",
    },
])


class TestParseRiskItems:

    def test_parse_valid_json(self):
        """正常なJSON配列をパースして RiskItem リストを返す。"""
        items = _parse_risk_items(MOCK_LLM_RISK_ITEMS)
        assert len(items) == 2
        assert items[0].category == "compliance"
        assert items[0].severity == "high"
        assert items[1].severity == "critical"
        assert items[1].source == "llm"

    def test_parse_with_codeblock(self):
        """コードブロックに囲まれたJSONも正しくパースできる。"""
        content = f"```json\n{MOCK_LLM_RISK_ITEMS}\n```"
        items = _parse_risk_items(content)
        assert len(items) == 2

    def test_parse_invalid_json_returns_empty(self):
        """無効なJSONは空リストを返す（クラッシュしない）。"""
        items = _parse_risk_items("これはJSONではありません")
        assert items == []

    def test_parse_severity_normalized(self):
        """severity が範囲外の値でも 'medium' にフォールバックする。"""
        raw = json.dumps([{
            "risk_id": "r001",
            "category": "finance",
            "severity": "UNKNOWN_VALUE",
            "probability": 0.5,
            "title": "テスト",
            "description": "テスト",
        }])
        items = _parse_risk_items(raw)
        assert len(items) == 1
        assert items[0].severity == "medium"

    def test_parse_probability_clamped(self):
        """probability が範囲外（> 1.0）の場合 1.0 にクランプされる。"""
        raw = json.dumps([{
            "risk_id": "r001",
            "category": "operation",
            "severity": "low",
            "probability": 99.9,
            "title": "テスト",
            "description": "テスト",
        }])
        items = _parse_risk_items(raw)
        assert len(items) == 1
        assert items[0].probability == 1.0

    def test_parse_title_truncated_to_20_chars(self):
        """title が20文字を超える場合に切り詰められる。"""
        raw = json.dumps([{
            "risk_id": "r001",
            "category": "operation",
            "severity": "medium",
            "probability": 0.5,
            "title": "あ" * 30,
            "description": "テスト",
        }])
        items = _parse_risk_items(raw)
        assert len(items[0].title) <= 20


# ---------------------------------------------------------------------------
# TestBuildDetailedRiskContext
# ---------------------------------------------------------------------------

def _make_twin_snapshot(company_id: str) -> TwinSnapshot:
    return TwinSnapshot(
        company_id=company_id,
        people=PeopleState(
            headcount=20,
            key_roles=["製造部長", "品質管理者"],
            skill_gaps=["CAD操作", "ISO資格"],
            completeness=0.6,
        ),
        process=ProcessState(
            documented_flows=5,
            automation_rate=0.3,
            bottlenecks=["手書き作業報告"],
            completeness=0.4,
        ),
        cost=CostState(
            monthly_fixed_cost=3_000_000,
            monthly_variable_cost=1_500_000,
            top_cost_items=["人件費", "材料費"],
            completeness=0.5,
        ),
        tool=ToolState(
            saas_tools=["kintone"],
            manual_tools=["Excel集計", "紙台帳", "手書き日報"],
            completeness=0.3,
        ),
        risk=RiskState(
            open_risks=["設備老朽化"],
            compliance_items=["安全衛生法"],
            severity_high=1,
            completeness=0.4,
        ),
        overall_completeness=0.44,
    )


class TestBuildDetailedRiskContext:

    def test_context_contains_industry(self):
        """業種情報がコンテキストに含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        context = _build_detailed_risk_context(snap, [], "manufacturing", [])
        assert "manufacturing" in context

    def test_context_contains_twin_dimensions(self):
        """5次元の状態情報がコンテキストに含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        context = _build_detailed_risk_context(snap, [], "製造業", [])
        assert "人材" in context
        assert "プロセス" in context
        assert "コスト" in context
        assert "ツール" in context
        assert "リスク" in context

    def test_context_contains_knowledge_items(self):
        """ナレッジアイテムがコンテキストに含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        items = [
            {"title": "設備保守記録", "content": "主要設備の保守履歴",
             "department": "製造", "item_type": "process"},
        ]
        context = _build_detailed_risk_context(snap, items, "製造業", [])
        assert "設備保守記録" in context

    def test_context_contains_rule_risks(self):
        """ルールベースリスクがコンテキストに含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        rule_risks = [{"severity": "high", "message": "業務フロー文書化が不足しています"}]
        context = _build_detailed_risk_context(snap, [], "製造業", rule_risks)
        assert "業務フロー文書化が不足" in context

    def test_context_ends_with_json_instruction(self):
        """コンテキストの末尾にJSON出力の指示が含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        context = _build_detailed_risk_context(snap, [], "製造業", [])
        assert "JSON形式で出力" in context

    def test_context_no_knowledge_shows_message(self):
        """ナレッジなしの場合に適切なメッセージが含まれる。"""
        snap = _make_twin_snapshot(str(uuid4()))
        context = _build_detailed_risk_context(snap, [], "製造業", [])
        assert "登録ナレッジなし" in context


# ---------------------------------------------------------------------------
# TestDedupRiskItems
# ---------------------------------------------------------------------------

class TestDedupRiskItems:

    def _make_item(self, risk_id: str, title: str, severity: str, source: str = "rule") -> RiskItem:
        return RiskItem(
            risk_id=risk_id,
            category="operation",
            severity=severity,
            probability=0.5,
            title=title,
            description="テスト",
            source=source,
        )

    def test_no_duplicates_merged(self):
        """重複なしの場合は全件マージされる。"""
        rule = [self._make_item("r001", "スキルギャップ", "medium", "rule")]
        llm = [self._make_item("l001", "設備老朽化リスク", "high", "llm")]
        merged = _dedup_risk_items(rule, llm)
        assert len(merged) == 2

    def test_duplicate_title_prefix_removed(self):
        """title の先頭10文字が同じ場合はLLM側が除外される。"""
        # "スキルギャップ検知対" = 10文字（先頭10文字が一致）
        common_prefix = "スキルギャップ検知対"  # 10文字
        rule = [self._make_item("r001", common_prefix + "応状況", "medium", "rule")]
        llm = [self._make_item("l001", common_prefix + "応詳細", "high", "llm")]
        merged = _dedup_risk_items(rule, llm)
        assert len(merged) == 1
        assert merged[0].source == "rule"

    def test_sorted_by_severity(self):
        """マージ結果は severity 降順（critical > high > medium > low）でソートされる。"""
        rule = [
            self._make_item("r001", "低リスク", "low", "rule"),
            self._make_item("r002", "中リスク", "medium", "rule"),
        ]
        llm = [
            self._make_item("l001", "重大リスク", "critical", "llm"),
            self._make_item("l002", "高リスク", "high", "llm"),
        ]
        merged = _dedup_risk_items(rule, llm)
        severities = [item.severity for item in merged]
        assert severities == ["critical", "high", "medium", "low"]

    def test_empty_inputs_returns_empty(self):
        """両方空の場合は空リストを返す。"""
        merged = _dedup_risk_items([], [])
        assert merged == []


# ---------------------------------------------------------------------------
# TestDetectRisksWithLLM
# ---------------------------------------------------------------------------

MOCK_LLM_DETAILED_ITEMS = json.dumps([
    {
        "risk_id": "risk_001",
        "category": "compliance",
        "severity": "high",
        "probability": 0.75,
        "title": "安衛法未対応リスク",
        "description": "安全衛生法改正に未対応のリスクがあります",
        "impact": "行政処分リスク",
        "mitigation": "コンプライアンス研修の実施",
        "data_basis": "コンプライアンス項目: 安全衛生法",
    },
    {
        "risk_id": "risk_002",
        "category": "personnel",
        "severity": "critical",
        "probability": 0.85,
        "title": "熟練工暗黙知消失",
        "description": "熟練工の高齢化により技能継承が困難になるリスク",
        "impact": "製品品質の低下",
        "mitigation": "技能伝承プログラムの整備",
        "data_basis": "スキルギャップ: CAD操作",
    },
])


class TestDetectRisksWithLLM:

    @pytest.mark.asyncio
    async def test_returns_risk_item_list(self):
        """detect_risks_with_llm が RiskItem のリストを返す。"""
        company_id = str(uuid4())
        snap = _make_twin_snapshot(company_id)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_DETAILED_ITEMS
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.08
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(snap, [], "manufacturing")

        assert isinstance(result, list)
        assert all(isinstance(item, RiskItem) for item in result)

    @pytest.mark.asyncio
    async def test_includes_rule_based_items(self):
        """ルールベース検出結果がマージに含まれる。"""
        company_id = str(uuid4())
        # process.completeness < 0.3 でルールベースリスクを発火させる
        snap = TwinSnapshot(
            company_id=company_id,
            process=ProcessState(completeness=0.1),
        )

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"  # LLMは空で返す
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(snap, [], "manufacturing")

        # ルールベースリスクが1件以上含まれる
        rule_items = [r for r in result if r.source == "rule"]
        assert len(rule_items) >= 1

    @pytest.mark.asyncio
    async def test_llm_items_have_source_llm(self):
        """LLM検出アイテムの source が 'llm' になっている。"""
        company_id = str(uuid4())
        snap = _make_twin_snapshot(company_id)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = MOCK_LLM_DETAILED_ITEMS
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.05
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(snap, [], "manufacturing")

        llm_items = [r for r in result if r.source == "llm"]
        assert len(llm_items) >= 1

    @pytest.mark.asyncio
    async def test_accepts_existing_rule_risks(self):
        """existing_rule_risks を渡すと detect_risks を再実行しない。"""
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)
        existing = [{"type": "cost_info_missing", "severity": "low", "message": "コスト情報未登録"}]

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(
                snap, [], "manufacturing", existing_rule_risks=existing
            )

        # ルールベースリスクが含まれていること
        assert any(r.source == "rule" for r in result)

    @pytest.mark.asyncio
    async def test_result_sorted_by_severity(self):
        """結果が severity 降順でソートされている。"""
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)

        # LLMが mixed severity のアイテムを返す
        mixed_items = json.dumps([
            {"risk_id": "r1", "category": "operation", "severity": "low",
             "probability": 0.3, "title": "低リスク項目", "description": "低"},
            {"risk_id": "r2", "category": "compliance", "severity": "critical",
             "probability": 0.9, "title": "緊急コンプライア", "description": "緊急"},
        ])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = mixed_items
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.02
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(snap, [], "manufacturing")

        _ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severities = [_ORDER[r.severity] for r in result]
        assert severities == sorted(severities)

    @pytest.mark.asyncio
    async def test_result_capped_at_20(self):
        """結果は最大20件に制限される。"""
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)

        # LLMが25件返す（制限テスト）
        items_25 = json.dumps([
            {
                "risk_id": f"risk_{i:03d}",
                "category": "operation",
                "severity": "medium",
                "probability": 0.5,
                "title": f"リスク{i:03d}",
                "description": f"テスト{i}",
            }
            for i in range(25)
        ])

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = items_25
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.05
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            result = await detect_risks_with_llm(snap, [], "manufacturing")

        assert len(result) <= 20

    @pytest.mark.asyncio
    async def test_uses_standard_tier(self):
        """LLMタスクが STANDARD tier で呼ばれる。"""
        from llm.client import ModelTier
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-pro"
        mock_response.cost_yen = 0.1
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            await detect_risks_with_llm(snap, [], "manufacturing")

        call_args = mock_llm.generate.call_args
        task = call_args[0][0]
        assert task.tier == ModelTier.STANDARD

    @pytest.mark.asyncio
    async def test_task_type_is_risk_detection_detailed(self):
        """LLMタスクの task_type が 'risk_detection_detailed' になっている。"""
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            await detect_risks_with_llm(snap, [], "manufacturing")

        task = mock_llm.generate.call_args[0][0]
        assert task.task_type == "risk_detection_detailed"

    @pytest.mark.asyncio
    async def test_knowledge_items_in_context(self):
        """ナレッジアイテムがLLMに渡されるコンテキストに含まれる。"""
        company_id = str(uuid4())
        snap = TwinSnapshot(company_id=company_id)
        knowledge = [
            {"title": "主要設備保守履歴", "content": "旋盤Aの保守記録",
             "department": "製造", "item_type": "process"},
        ]

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "[]"
        mock_response.model_used = "gemini-2.0-flash"
        mock_response.cost_yen = 0.01
        mock_llm.generate = AsyncMock(return_value=mock_response)

        with patch("brain.twin.risk_detector.get_llm_client", return_value=mock_llm):
            await detect_risks_with_llm(snap, knowledge, "manufacturing")

        # LLMに渡されたメッセージにナレッジが含まれる
        task = mock_llm.generate.call_args[0][0]
        user_content = task.messages[1]["content"]
        assert "主要設備保守履歴" in user_content
