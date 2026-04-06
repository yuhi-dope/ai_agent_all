"""anomaly_detector マイクロエージェント テスト。"""
import pytest

from workers.micro.models import MicroAgentInput, MicroAgentError
from workers.micro.anomaly_detector import run_anomaly_detector

COMPANY_ID = "test-company-001"


def _make_input(payload: dict) -> MicroAgentInput:
    return MicroAgentInput(
        company_id=COMPANY_ID,
        agent_name="anomaly_detector",
        payload=payload,
    )


# ─── 正常系 ──────────────────────────────────────────────────────────────────

class TestNormal:
    @pytest.mark.asyncio
    async def test_all_within_range(self):
        """全アイテムが expected_range 内 → 異常0件、passed=True、confidence=1.0"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 350000, "expected_range": [100000, 500000]},
                {"name": "労務費", "value": 200000, "expected_range": [100000, 400000]},
            ],
            "detect_modes": ["range"],
        }))
        assert out.success is True
        assert out.result["passed"] is True
        assert out.result["anomaly_count"] == 0
        assert out.result["total_checked"] == 2
        assert out.confidence == 1.0
        assert out.cost_yen == 0.0

    @pytest.mark.asyncio
    async def test_similar_values_no_digit_error(self):
        """近い値同士は digit_error に引っかからない"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 300000},
                {"name": "労務費", "value": 350000},
                {"name": "経費",   "value": 280000},
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.result["passed"] is True
        assert out.result["anomaly_count"] == 0

    @pytest.mark.asyncio
    async def test_zscore_within_normal(self):
        """Z-score が 2.5 以下なら正常"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 360000},
            ],
            "historical_values": {
                "材料費": [320000, 340000, 360000, 350000, 330000],
            },
            "detect_modes": ["zscore"],
        }))
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_rule_satisfied(self):
        """ルール条件を満たす場合は違反なし"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 800000},
            ],
            "rules": [
                {"field": "材料費", "operator": "lte", "threshold": 1000000,
                 "message": "材料費が100万円を超えています"},
            ],
            "detect_modes": ["rules"],
        }))
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_cost_yen_is_zero(self):
        """LLM不使用なので cost_yen は必ず 0"""
        out = await run_anomaly_detector(_make_input({
            "items": [{"name": "材料費", "value": 500000}],
            "detect_modes": [],
        }))
        assert out.cost_yen == 0.0


# ─── digit_error 検知 ────────────────────────────────────────────────────────

class TestDigitError:
    @pytest.mark.asyncio
    async def test_ten_times_larger(self):
        """他項目平均の10倍以上 → digit_error(high)。
        2アイテムの場合は互いに「他項目」扱いになるため両方が検知される。"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 350000},
                {"name": "加工費", "value": 8500000},  # 約24倍
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.result["passed"] is False
        anomalies = out.result["anomalies"]
        # 両アイテムが互いに外れ値として検知される（2件）
        assert len(anomalies) == 2
        fields = {a["field"] for a in anomalies}
        assert "加工費" in fields
        # 加工費の異常を確認
        a = next(x for x in anomalies if x["field"] == "加工費")
        assert a["type"] == "digit_error"
        assert a["severity"] == "high"
        assert "桁間違い" in a["message"]
        assert "¥850,000" in a["suggestion"]

    @pytest.mark.asyncio
    async def test_ten_times_smaller(self):
        """他項目平均の1/10以下 → digit_error(high)"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 300000},
                {"name": "労務費", "value": 350000},
                {"name": "経費",   "value": 10000},   # 約1/30
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.result["passed"] is False
        fields = [a["field"] for a in out.result["anomalies"]]
        assert "経費" in fields
        a = next(x for x in out.result["anomalies"] if x["field"] == "経費")
        assert a["type"] == "digit_error"
        assert a["severity"] == "high"

    @pytest.mark.asyncio
    async def test_exactly_ten_times_is_anomaly(self):
        """ちょうど10倍は異常（ratio >= 10）"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "A", "value": 100000},
                {"name": "B", "value": 1000000},
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.result["passed"] is False

    @pytest.mark.asyncio
    async def test_nine_times_is_not_digit_error(self):
        """9倍程度は digit_error にならない"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "A", "value": 100000},
                {"name": "B", "value": 900000},
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_confidence_decreases_with_anomalies(self):
        """異常件数に応じて confidence が下がる: count=2 → 1.0 - 0.4 = 0.6"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 350000},
                {"name": "加工費", "value": 8500000},
            ],
            "detect_modes": ["digit_error"],
        }))
        # 2アイテムが互いに外れ値として検知 → anomaly_count=2
        assert out.result["anomaly_count"] == 2
        assert abs(out.confidence - 0.6) < 0.01


# ─── zscore 検知 ──────────────────────────────────────────────────────────────

class TestZscore:
    @pytest.mark.asyncio
    async def test_high_zscore_detected(self):
        """Z-score > 3 → zscore(high)"""
        # 平均300000, std≈約10000 程度の分布に対して1000000を渡す
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 1000000},
            ],
            "historical_values": {
                "材料費": [290000, 300000, 310000, 295000, 305000],
            },
            "detect_modes": ["zscore"],
        }))
        assert out.result["passed"] is False
        a = out.result["anomalies"][0]
        assert a["type"] == "zscore"
        assert a["severity"] == "high"
        assert "Z-score" in a["message"]

    @pytest.mark.asyncio
    async def test_medium_zscore(self):
        """2.5 < Z-score <= 3 → zscore(medium)"""
        # 平均100, 母std≈6.32 の分布。値=117 → Z≈2.69 (medium)
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "単価", "value": 117},
            ],
            "historical_values": {
                "単価": [90, 100, 110, 100, 100],
            },
            "detect_modes": ["zscore"],
        }))
        assert out.result["passed"] is False
        a = out.result["anomalies"][0]
        assert a["type"] == "zscore"
        assert a["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_single_historical_value_skipped(self):
        """historical が1件のみ → Z-score計算スキップ（異常なし）"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 999999},
            ],
            "historical_values": {
                "材料費": [100000],
            },
            "detect_modes": ["zscore"],
        }))
        # 標本数 < 2 はスキップ
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_zero_variance_different_value(self):
        """historical が全て同値で分散ゼロ → 異なる値は high"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "固定費", "value": 999999},
            ],
            "historical_values": {
                "固定費": [100000, 100000, 100000],
            },
            "detect_modes": ["zscore"],
        }))
        assert out.result["passed"] is False
        assert out.result["anomalies"][0]["severity"] == "high"

    @pytest.mark.asyncio
    async def test_zero_variance_same_value(self):
        """historical が全て同値で値も一致 → 正常"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "固定費", "value": 100000},
            ],
            "historical_values": {
                "固定費": [100000, 100000, 100000],
            },
            "detect_modes": ["zscore"],
        }))
        assert out.result["passed"] is True


# ─── range 検知 ───────────────────────────────────────────────────────────────

class TestRange:
    @pytest.mark.asyncio
    async def test_above_range_high_severity(self):
        """範囲幅の2倍以上外れている → high"""
        # range=[100000, 200000], span=100000, value=400000 → 外れ量=200000 → ratio=2.0 → high
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 400000, "expected_range": [100000, 200000]},
            ],
            "detect_modes": ["range"],
        }))
        assert out.result["passed"] is False
        a = out.result["anomalies"][0]
        assert a["type"] == "range"
        assert a["severity"] == "high"
        assert "上限" in a["message"]

    @pytest.mark.asyncio
    async def test_below_range_detected(self):
        """下限を下回る場合も検知"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "労務費", "value": 50000, "expected_range": [100000, 300000]},
            ],
            "detect_modes": ["range"],
        }))
        assert out.result["passed"] is False
        a = out.result["anomalies"][0]
        assert a["type"] == "range"
        assert "下限" in a["message"]

    @pytest.mark.asyncio
    async def test_boundary_value_is_normal(self):
        """境界値（ちょうど上限/下限）は正常"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 500000, "expected_range": [100000, 500000]},
                {"name": "労務費", "value": 100000, "expected_range": [100000, 500000]},
            ],
            "detect_modes": ["range"],
        }))
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_medium_severity_range(self):
        """範囲幅の0.5〜1倍外れている → medium"""
        # range=[100000, 200000], span=100000, value=260000 → 外れ量=60000 → ratio=0.6 → medium
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 260000, "expected_range": [100000, 200000]},
            ],
            "detect_modes": ["range"],
        }))
        assert out.result["passed"] is False
        assert out.result["anomalies"][0]["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_suggestion_contains_range(self):
        """suggestion に期待範囲が含まれる"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "経費", "value": 5000000, "expected_range": [100000, 500000]},
            ],
            "detect_modes": ["range"],
        }))
        suggestion = out.result["anomalies"][0]["suggestion"]
        assert "¥100,000" in suggestion
        assert "¥500,000" in suggestion


# ─── カスタムルール違反 ───────────────────────────────────────────────────────

class TestRules:
    @pytest.mark.asyncio
    async def test_lte_violation(self):
        """lte ルール違反を検知"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 1500000},
            ],
            "rules": [
                {"field": "材料費", "operator": "lte", "threshold": 1000000,
                 "message": "材料費が100万円を超えています"},
            ],
            "detect_modes": ["rules"],
        }))
        assert out.result["passed"] is False
        a = out.result["anomalies"][0]
        assert a["type"] == "rule_violation"
        assert a["message"] == "材料費が100万円を超えています"

    @pytest.mark.asyncio
    async def test_gte_violation(self):
        """gte ルール違反を検知"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "利益率", "value": 3},
            ],
            "rules": [
                {"field": "利益率", "operator": "gte", "threshold": 10,
                 "message": "利益率が10%を下回っています"},
            ],
            "detect_modes": ["rules"],
        }))
        assert out.result["passed"] is False
        assert out.result["anomalies"][0]["message"] == "利益率が10%を下回っています"

    @pytest.mark.asyncio
    async def test_multiple_rules_same_field(self):
        """同一フィールドに複数ルール → 全て評価"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "単価", "value": 50},
            ],
            "rules": [
                {"field": "単価", "operator": "gte", "threshold": 100,
                 "message": "単価が低すぎます"},
                {"field": "単価", "operator": "lte", "threshold": 10000,
                 "message": "単価が高すぎます"},
            ],
            "detect_modes": ["rules"],
        }))
        # gte違反のみ（lteは満たしている）
        assert out.result["anomaly_count"] == 1
        assert out.result["anomalies"][0]["message"] == "単価が低すぎます"

    @pytest.mark.asyncio
    async def test_rule_for_other_field_ignored(self):
        """対象外フィールドのルールは無視"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 500000},
            ],
            "rules": [
                {"field": "労務費", "operator": "lte", "threshold": 100,
                 "message": "労務費が高い"},
            ],
            "detect_modes": ["rules"],
        }))
        assert out.result["passed"] is True

    @pytest.mark.asyncio
    async def test_all_operators(self):
        """全オペレータが動作する"""
        # gt/lt/eq/ne も検証
        cases = [
            ("gt",  50,   100, True),   # 50 > 100 → False → violation
            ("lt",  200,  100, True),   # 200 < 100 → False → violation
            ("eq",  300,  100, True),   # 300 == 100 → False → violation
            ("ne",  100,  100, True),   # 100 != 100 → False → violation
            ("gt",  200,  100, False),  # 200 > 100 → True → ok
        ]
        for op, value, threshold, should_violate in cases:
            out = await run_anomaly_detector(_make_input({
                "items": [{"name": "X", "value": value}],
                "rules": [{"field": "X", "operator": op, "threshold": threshold}],
                "detect_modes": ["rules"],
            }))
            if should_violate:
                assert out.result["passed"] is False, f"op={op} value={value} threshold={threshold}"
            else:
                assert out.result["passed"] is True, f"op={op} value={value} threshold={threshold}"


# ─── エラー系 ────────────────────────────────────────────────────────────────

class TestErrors:
    @pytest.mark.asyncio
    async def test_empty_items_raises(self):
        """items が空リスト → MicroAgentError"""
        with pytest.raises(MicroAgentError) as exc_info:
            await run_anomaly_detector(_make_input({"items": []}))
        assert "anomaly_detector" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_items_raises(self):
        """items キー自体がない → MicroAgentError"""
        with pytest.raises(MicroAgentError):
            await run_anomaly_detector(_make_input({}))

    @pytest.mark.asyncio
    async def test_missing_name_raises(self):
        """items[].name が未指定 → MicroAgentError"""
        with pytest.raises(MicroAgentError):
            await run_anomaly_detector(_make_input({
                "items": [{"value": 100000}],
            }))


# ─── 複合テスト ───────────────────────────────────────────────────────────────

class TestCombined:
    @pytest.mark.asyncio
    async def test_multiple_modes(self):
        """複数検知モードを同時使用 — 重複カウントに注意"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "材料費", "value": 350000, "expected_range": [100000, 500000]},
                {"name": "加工費", "value": 8500000},
            ],
            "rules": [
                {"field": "加工費", "operator": "lte", "threshold": 2000000,
                 "message": "加工費が上限を超えています"},
            ],
            "detect_modes": ["range", "digit_error", "rules"],
        }))
        assert out.result["passed"] is False
        # 加工費: digit_error + rule_violation の両方
        types = {a["type"] for a in out.result["anomalies"]}
        assert "digit_error" in types
        assert "rule_violation" in types

    @pytest.mark.asyncio
    async def test_detect_modes_empty_skips_all(self):
        """detect_modes=[] → 何もチェックしない → passed=True"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": "加工費", "value": 8500000},
            ],
            "detect_modes": [],
        }))
        assert out.result["passed"] is True
        assert out.result["anomaly_count"] == 0

    @pytest.mark.asyncio
    async def test_confidence_floor(self):
        """anomaly_count=5以上でも confidence >= 0.1"""
        out = await run_anomaly_detector(_make_input({
            "items": [
                {"name": f"項目{i}", "value": 10 ** (i + 2)}
                for i in range(6)
            ],
            "detect_modes": ["digit_error"],
        }))
        assert out.confidence >= 0.1

    @pytest.mark.asyncio
    async def test_total_checked_matches_items(self):
        """total_checked は items の件数と一致"""
        items = [{"name": f"費目{i}", "value": 100000 + i * 1000} for i in range(5)]
        out = await run_anomaly_detector(_make_input({
            "items": items,
            "detect_modes": [],
        }))
        assert out.result["total_checked"] == 5
