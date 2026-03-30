"""Tests for brain/twin module.

カバレッジ:
- analyze_and_update_twin: item_type ごとの集計（10パターン以上）
- detect_risks: 閾値を超えるとリスクを返す（5パターン）
- simulate_whatif: before / after / delta の正確性（3ケース）
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.twin.analyzer import analyze_and_update_twin
from brain.twin.models import (
    CostState,
    PeopleState,
    ProcessState,
    RiskState,
    ToolState,
    TwinSnapshot,
)
from brain.twin.risk_detector import detect_risks
from brain.twin.whatif import simulate_whatif


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_company_id() -> str:
    return str(uuid4())


def _make_item(item_type: str, title: str) -> dict:
    return {
        "id": str(uuid4()),
        "title": title,
        "content": f"{title} のコンテンツ",
        "item_type": item_type,
        "department": "総務",
        "category": "general",
        "confidence": 0.8,
    }


def _mock_db():
    """DB呼び出しをモックするコンテキスト用 MagicMock。"""
    mock = MagicMock()
    mock.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
    return mock


# ---------------------------------------------------------------------------
# TestAnalyzeAndUpdateTwin
# ---------------------------------------------------------------------------

class TestAnalyzeAndUpdateTwin:

    @pytest.mark.asyncio
    async def test_rule_items_increment_decision_rules(self):
        """rule タイプが process.decision_rules に加算される。"""
        company_id = _make_company_id()
        items = [_make_item("rule", f"ルール{i}") for i in range(5)]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.process.decision_rules == 5

    @pytest.mark.asyncio
    async def test_flow_items_increment_documented_flows(self):
        """flow タイプが process.documented_flows に加算される。"""
        company_id = _make_company_id()
        items = [_make_item("flow", f"フロー{i}") for i in range(3)]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.process.documented_flows == 3

    @pytest.mark.asyncio
    async def test_role_items_populate_key_roles(self):
        """role タイプが people.key_roles に追加される。"""
        company_id = _make_company_id()
        items = [
            _make_item("role", "営業マネージャー"),
            _make_item("role", "経理担当"),
            _make_item("role", "エンジニアリード"),
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert "営業マネージャー" in snapshot.people.key_roles
        assert "経理担当" in snapshot.people.key_roles
        assert "エンジニアリード" in snapshot.people.key_roles
        assert len(snapshot.people.key_roles) == 3

    @pytest.mark.asyncio
    async def test_tool_items_populate_saas_tools(self):
        """tool タイプが tool.saas_tools に追加される。"""
        company_id = _make_company_id()
        items = [
            _make_item("tool", "Salesforce"),
            _make_item("tool", "Slack"),
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert "Salesforce" in snapshot.tool.saas_tools
        assert "Slack" in snapshot.tool.saas_tools

    @pytest.mark.asyncio
    async def test_cost_items_populate_top_cost_items(self):
        """cost タイプが cost.top_cost_items に追加される。"""
        company_id = _make_company_id()
        items = [
            _make_item("cost", "人件費"),
            _make_item("cost", "サーバー費"),
            _make_item("cost", "オフィス賃料"),
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert "人件費" in snapshot.cost.top_cost_items
        assert len(snapshot.cost.top_cost_items) == 3

    @pytest.mark.asyncio
    async def test_risk_items_populate_open_risks(self):
        """risk タイプが risk.open_risks に追加される。"""
        company_id = _make_company_id()
        items = [
            _make_item("risk", "キーパーソン依存"),
            _make_item("risk", "セキュリティパッチ未適用"),
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert "キーパーソン依存" in snapshot.risk.open_risks
        assert "セキュリティパッチ未適用" in snapshot.risk.open_risks

    @pytest.mark.asyncio
    async def test_completeness_caps_at_1(self):
        """10件以上のアイテムがあっても completeness は 1.0 を超えない。"""
        company_id = _make_company_id()
        items = [_make_item("rule", f"ルール{i}") for i in range(15)]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.process.completeness == 1.0

    @pytest.mark.asyncio
    async def test_completeness_partial_below_1(self):
        """5件のときは completeness = 0.5 になる。"""
        company_id = _make_company_id()
        items = [_make_item("tool", f"ツール{i}") for i in range(5)]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.tool.completeness == 0.5

    @pytest.mark.asyncio
    async def test_overall_completeness_is_average(self):
        """overall_completeness が5次元の平均になっている。"""
        company_id = _make_company_id()
        items = [
            _make_item("rule", "ルール1"),   # process +1
            _make_item("role", "ロール1"),   # people +1
            _make_item("cost", "コスト1"),   # cost +1
            _make_item("tool", "ツール1"),   # tool +1
            _make_item("risk", "リスク1"),   # risk +1
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        expected = round((
            snapshot.people.completeness
            + snapshot.process.completeness
            + snapshot.cost.completeness
            + snapshot.tool.completeness
            + snapshot.risk.completeness
        ) / 5, 4)
        assert snapshot.overall_completeness == expected

    @pytest.mark.asyncio
    async def test_empty_items_returns_zero_snapshot(self):
        """アイテムが空のとき全次元が 0 になる。"""
        company_id = _make_company_id()

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, [])

        assert snapshot.process.decision_rules == 0
        assert snapshot.process.documented_flows == 0
        assert snapshot.people.key_roles == []
        assert snapshot.overall_completeness == 0.0

    @pytest.mark.asyncio
    async def test_duplicate_titles_not_added_twice(self):
        """同一タイトルの role は重複して追加されない。"""
        company_id = _make_company_id()
        items = [
            _make_item("role", "営業マネージャー"),
            _make_item("role", "営業マネージャー"),  # 重複
        ]

        with patch("brain.twin.analyzer.get_service_client", return_value=_mock_db()):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.people.key_roles.count("営業マネージャー") == 1

    @pytest.mark.asyncio
    async def test_db_failure_does_not_raise(self):
        """DB保存に失敗してもスナップショット自体は返される。"""
        company_id = _make_company_id()
        mock_db = MagicMock()
        mock_db.table.side_effect = Exception("DB接続失敗")
        items = [_make_item("rule", "ルール1")]

        with patch("brain.twin.analyzer.get_service_client", return_value=mock_db):
            snapshot = await analyze_and_update_twin(company_id, items)

        assert snapshot.process.decision_rules == 1


# ---------------------------------------------------------------------------
# TestDetectRisks
# ---------------------------------------------------------------------------

class TestDetectRisks:

    def _base_snapshot(self, company_id: str | None = None) -> TwinSnapshot:
        """リスクが発生しない「健全な」スナップショットを返す。"""
        cid = company_id or _make_company_id()
        return TwinSnapshot(
            company_id=cid,
            people=PeopleState(skill_gaps=[], completeness=0.8),
            process=ProcessState(completeness=0.8),
            cost=CostState(monthly_fixed_cost=500_000, completeness=0.5),
            tool=ToolState(manual_tools=[], completeness=0.5),
            risk=RiskState(severity_high=0, completeness=0.5),
        )

    @pytest.mark.asyncio
    async def test_no_risks_when_all_healthy(self):
        """全指標が閾値内のときリスクは検出されない。"""
        snapshot = self._base_snapshot()
        risks = await detect_risks(snapshot)
        assert risks == []

    @pytest.mark.asyncio
    async def test_risk_process_documentation_insufficient(self):
        """process.completeness < 0.3 でリスクが検出される。"""
        snapshot = self._base_snapshot()
        snapshot.process.completeness = 0.1

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "process_documentation_insufficient" in types

        risk = next(r for r in risks if r["type"] == "process_documentation_insufficient")
        assert risk["severity"] == "high"

    @pytest.mark.asyncio
    async def test_risk_skill_gap_detected(self):
        """people.skill_gaps が空でないときリスクが検出される。"""
        snapshot = self._base_snapshot()
        snapshot.people.skill_gaps = ["Pythonスキル不足", "データ分析経験なし"]

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "skill_gap_detected" in types

        risk = next(r for r in risks if r["type"] == "skill_gap_detected")
        assert risk["severity"] == "medium"
        assert "Pythonスキル不足" in risk["message"]

    @pytest.mark.asyncio
    async def test_risk_manual_tools_excessive(self):
        """tool.manual_tools が 3件以上でリスクが検出される。"""
        snapshot = self._base_snapshot()
        snapshot.tool.manual_tools = ["Excel集計", "紙の申請書", "手書き台帳"]

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "manual_tools_excessive" in types

        risk = next(r for r in risks if r["type"] == "manual_tools_excessive")
        assert risk["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_risk_manual_tools_below_threshold_no_risk(self):
        """tool.manual_tools が 2件ならリスクは検出されない。"""
        snapshot = self._base_snapshot()
        snapshot.tool.manual_tools = ["Excel集計", "紙の申請書"]

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "manual_tools_excessive" not in types

    @pytest.mark.asyncio
    async def test_risk_high_severity_open(self):
        """risk.severity_high > 0 でリスクが検出される。"""
        snapshot = self._base_snapshot()
        snapshot.risk.severity_high = 3

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "high_severity_risk_open" in types

        risk = next(r for r in risks if r["type"] == "high_severity_risk_open")
        assert risk["severity"] == "high"
        assert "3件" in risk["message"]

    @pytest.mark.asyncio
    async def test_risk_cost_info_missing(self):
        """cost.monthly_fixed_cost == 0 でリスクが検出される。"""
        snapshot = self._base_snapshot()
        snapshot.cost.monthly_fixed_cost = 0

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "cost_info_missing" in types

        risk = next(r for r in risks if r["type"] == "cost_info_missing")
        assert risk["severity"] == "low"

    @pytest.mark.asyncio
    async def test_multiple_risks_detected_simultaneously(self):
        """複数のリスク条件が同時に成立する場合、全て検出される。"""
        snapshot = self._base_snapshot()
        snapshot.process.completeness = 0.0
        snapshot.risk.severity_high = 2
        snapshot.cost.monthly_fixed_cost = 0

        risks = await detect_risks(snapshot)
        types = [r["type"] for r in risks]
        assert "process_documentation_insufficient" in types
        assert "high_severity_risk_open" in types
        assert "cost_info_missing" in types


# ---------------------------------------------------------------------------
# TestSimulateWhatif
# ---------------------------------------------------------------------------

class TestSimulateWhatif:

    def _base_snapshot(self) -> TwinSnapshot:
        cid = _make_company_id()
        return TwinSnapshot(
            company_id=cid,
            cost=CostState(
                monthly_fixed_cost=1_000_000,
                monthly_variable_cost=500_000,
            ),
            people=PeopleState(headcount=20),
            process=ProcessState(automation_rate=0.3),
        )

    @pytest.mark.asyncio
    async def test_cost_reduction_before_after_delta(self):
        """月次固定費削減のシミュレーションで before / after / delta が正しい。"""
        snapshot = self._base_snapshot()
        changes = {
            "dimension": "cost",
            "field": "monthly_fixed_cost",
            "value": 900_000,
        }

        result = await simulate_whatif(snapshot, changes)

        assert result["before"]["monthly_fixed_cost"] == 1_000_000
        assert result["after"]["monthly_fixed_cost"] == 900_000
        assert result["delta"]["monthly_fixed_cost"] == -100_000
        assert "削減" in result["impact_summary"]

    @pytest.mark.asyncio
    async def test_headcount_increase(self):
        """人員増加シミュレーション: delta が正の値になる。"""
        snapshot = self._base_snapshot()
        changes = {
            "dimension": "people",
            "field": "headcount",
            "value": 25,
        }

        result = await simulate_whatif(snapshot, changes)

        assert result["before"]["headcount"] == 20
        assert result["after"]["headcount"] == 25
        assert result["delta"]["headcount"] == 5
        assert "25名" in result["impact_summary"]

    @pytest.mark.asyncio
    async def test_tool_list_change_no_numeric_delta(self):
        """saas_tools のリスト変更はdeltaに数値差分が含まれない。"""
        snapshot = self._base_snapshot()
        new_tools = ["Salesforce", "HubSpot", "kintone"]
        changes = {
            "dimension": "tool",
            "field": "saas_tools",
            "value": new_tools,
        }

        result = await simulate_whatif(snapshot, changes)

        assert result["before"]["saas_tools"] == []
        assert result["after"]["saas_tools"] == new_tools
        assert result["delta"] == {}  # 数値ではないためdeltaなし
        assert "tool.saas_tools" in result["impact_summary"]

    @pytest.mark.asyncio
    async def test_invalid_dimension_raises_value_error(self):
        """存在しない dimension を指定すると ValueError が発生する。"""
        snapshot = self._base_snapshot()
        changes = {
            "dimension": "invalid_dim",
            "field": "some_field",
            "value": 999,
        }

        with pytest.raises(ValueError, match="Invalid dimension"):
            await simulate_whatif(snapshot, changes)

    @pytest.mark.asyncio
    async def test_invalid_field_raises_value_error(self):
        """存在しない field を指定すると ValueError が発生する。"""
        snapshot = self._base_snapshot()
        changes = {
            "dimension": "cost",
            "field": "nonexistent_field",
            "value": 999,
        }

        with pytest.raises(ValueError, match="does not exist"):
            await simulate_whatif(snapshot, changes)

    @pytest.mark.asyncio
    async def test_automation_rate_improvement(self):
        """automation_rate 向上シミュレーション。"""
        snapshot = self._base_snapshot()
        changes = {
            "dimension": "process",
            "field": "automation_rate",
            "value": 0.7,
        }

        result = await simulate_whatif(snapshot, changes)

        assert result["before"]["automation_rate"] == pytest.approx(0.3)
        assert result["after"]["automation_rate"] == pytest.approx(0.7)
        assert result["delta"]["automation_rate"] == pytest.approx(0.4)
        assert "向上" in result["impact_summary"]
