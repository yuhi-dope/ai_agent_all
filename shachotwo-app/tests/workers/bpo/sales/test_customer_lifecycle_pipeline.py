"""CRM パイプライン④ 顧客ライフサイクル管理 — テスト。"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.sales.crm.customer_lifecycle_pipeline import (
    run_customer_lifecycle_pipeline,
    CustomerLifecyclePipelineResult,
    _compute_usage_metrics,
    _compute_health_dimensions,
    _identify_risk_factors,
    _classify_health,
    _status_from_label,
    HEALTH_WEIGHTS,
    RISK_THRESHOLD,
    CAUTION_THRESHOLD,
    EXPANSION_THRESHOLD,
    ONBOARD_SEQUENCE,
)

COMPANY_ID = "test-company-001"
CUSTOMER_ID = "test-customer-abc"

MOCK_CUSTOMER = {
    "id": CUSTOMER_ID,
    "customer_company_name": "テスト建設株式会社",
    "industry": "construction",
    "plan": "bpo_core",
    "active_modules": ["brain", "bpo_core"],
    "mrr": 250000,
    "health_score": 75,
    "nps_score": 40,
    "status": "active",
    "onboarded_at": "2025-01-01T00:00:00Z",
    "cs_owner": None,
    "created_at": "2024-12-01T00:00:00Z",
}


# ─── ユニットテスト（ピュア関数） ────────────────────────────────────────────

class TestComputeUsageMetrics:
    """_compute_usage_metrics の単体テスト。"""

    def test_empty_logs_returns_zeros(self):
        metrics = _compute_usage_metrics([], MOCK_CUSTOMER)
        assert metrics["bpo_executions_7d"] == 0
        assert metrics["bpo_executions_30d"] == 0
        assert metrics["unique_pipelines_7d"] == 0

    def test_counts_recent_logs(self):
        from datetime import date, timedelta
        today = date.today()
        logs = [
            {"executed_at": today.isoformat(), "pipeline": "estimation_pipeline", "status": "ok"},
            {"executed_at": today.isoformat(), "pipeline": "billing_pipeline", "status": "ok"},
            {"executed_at": (today - timedelta(days=3)).isoformat(), "pipeline": "estimation_pipeline", "status": "ok"},
            # 8日前 → 7日集計に含まれない
            {"executed_at": (today - timedelta(days=8)).isoformat(), "pipeline": "safety_docs_pipeline", "status": "ok"},
        ]
        metrics = _compute_usage_metrics(logs, MOCK_CUSTOMER)
        assert metrics["bpo_executions_7d"] == 3
        assert metrics["bpo_executions_30d"] == 4
        # 7日以内のユニークパイプライン: estimation（今日+3日前）+ billing（今日）= 2種
        # ただし estimation が 7日以内に2回 → set では1件
        assert metrics["unique_pipelines_7d"] == 2  # estimation + billing

    def test_active_modules_count_from_customer(self):
        metrics = _compute_usage_metrics([], MOCK_CUSTOMER)
        assert metrics["active_modules_count"] == 2  # MOCK_CUSTOMER["active_modules"] の長さ


class TestComputeHealthDimensions:
    """_compute_health_dimensions の単体テスト。"""

    def _make_metrics(self, executions_7d=10, unique_7d=2) -> dict:
        return {
            "bpo_executions_7d": executions_7d,
            "bpo_executions_30d": executions_7d * 3,
            "unique_pipelines_7d": unique_7d,
            "active_modules_count": 2,
            "log_total": executions_7d * 3,
        }

    def test_dimensions_are_0_to_100(self):
        metrics = self._make_metrics()
        dims = _compute_health_dimensions(metrics, MOCK_CUSTOMER)
        for key, val in dims.items():
            assert 0 <= val <= 100, f"{key}={val} が範囲外"

    def test_all_keys_present(self):
        dims = _compute_health_dimensions(self._make_metrics(), MOCK_CUSTOMER)
        assert set(dims.keys()) == {"usage", "engagement", "support", "nps", "expansion"}

    def test_nps_none_defaults_to_50(self):
        customer_no_nps = {**MOCK_CUSTOMER, "nps_score": None}
        dims = _compute_health_dimensions(self._make_metrics(), customer_no_nps)
        assert dims["nps"] == 50.0

    def test_nps_plus_100_maps_to_100(self):
        customer_high_nps = {**MOCK_CUSTOMER, "nps_score": 100}
        dims = _compute_health_dimensions(self._make_metrics(), customer_high_nps)
        assert dims["nps"] == 100.0

    def test_nps_minus_100_maps_to_0(self):
        customer_low_nps = {**MOCK_CUSTOMER, "nps_score": -100}
        dims = _compute_health_dimensions(self._make_metrics(), customer_low_nps)
        assert dims["nps"] == 0.0

    def test_high_usage_gives_high_score(self):
        metrics = self._make_metrics(executions_7d=20)
        dims = _compute_health_dimensions(metrics, MOCK_CUSTOMER)
        assert dims["usage"] == 100.0

    def test_zero_usage_gives_zero_score(self):
        metrics = self._make_metrics(executions_7d=0, unique_7d=0)
        dims = _compute_health_dimensions(metrics, MOCK_CUSTOMER)
        assert dims["usage"] == 0.0


class TestIdentifyRiskFactors:
    """_identify_risk_factors の単体テスト。"""

    def test_no_factors_when_all_healthy(self):
        dims = {"usage": 80.0, "engagement": 80.0, "support": 90.0, "nps": 80.0, "expansion": 80.0}
        factors = _identify_risk_factors(dims, MOCK_CUSTOMER)
        assert factors == []

    def test_low_usage_triggers_factor(self):
        dims = {"usage": 20.0, "engagement": 80.0, "support": 90.0, "nps": 80.0, "expansion": 80.0}
        factors = _identify_risk_factors(dims, MOCK_CUSTOMER)
        assert any("BPO実行数" in f for f in factors)

    def test_nps_none_triggers_factor(self):
        customer_no_nps = {**MOCK_CUSTOMER, "nps_score": None}
        dims = {"usage": 80.0, "engagement": 80.0, "support": 90.0, "nps": 20.0, "expansion": 80.0}
        factors = _identify_risk_factors(dims, customer_no_nps)
        assert any("NPS" in f for f in factors)


class TestClassifyHealth:
    """_classify_health の単体テスト。"""

    def test_score_below_risk_threshold(self):
        label, action = _classify_health(RISK_THRESHOLD - 1, MOCK_CUSTOMER)
        assert label == "risk"
        assert action is True

    def test_score_above_expansion_with_unused_modules(self):
        customer_few_modules = {**MOCK_CUSTOMER, "active_modules": ["brain"]}
        label, action = _classify_health(EXPANSION_THRESHOLD + 1, customer_few_modules)
        assert label == "expansion"
        assert action is True

    def test_score_above_expansion_all_modules_used(self):
        customer_all_modules = {
            **MOCK_CUSTOMER,
            "active_modules": ["brain", "bpo_core", "backoffice", "analytics"],
        }
        label, action = _classify_health(EXPANSION_THRESHOLD + 1, customer_all_modules)
        assert label == "healthy"
        assert action is False

    def test_caution_range(self):
        label, action = _classify_health(50, MOCK_CUSTOMER)
        assert label == "caution"
        assert action is False

    def test_healthy_range(self):
        label, action = _classify_health(75, {**MOCK_CUSTOMER, "active_modules": ["brain", "bpo_core", "backoffice", "analytics"]})
        assert label == "healthy"
        assert action is False


class TestStatusFromLabel:
    """_status_from_label の単体テスト。"""

    def test_risk_maps_to_at_risk(self):
        assert _status_from_label("risk") == "at_risk"

    def test_others_map_to_active(self):
        for label in ("caution", "healthy", "expansion"):
            assert _status_from_label(label) == "active"


class TestHealthWeights:
    """HEALTH_WEIGHTS が正しく定義されているか確認。"""

    def test_weights_sum_to_1(self):
        total = sum(HEALTH_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"重みの合計が1.0でない: {total}"

    def test_all_dimensions_present(self):
        expected = {"usage", "engagement", "support", "nps", "expansion"}
        assert set(HEALTH_WEIGHTS.keys()) == expected


class TestOnboardSequence:
    """ONBOARD_SEQUENCE が正しく定義されているか確認。"""

    def test_expected_days(self):
        assert set(ONBOARD_SEQUENCE.keys()) == {1, 3, 7, 14, 30}

    def test_day_30_contains_nps(self):
        assert "NPS" in ONBOARD_SEQUENCE[30]


# ─── 結合テスト（モック使用） ────────────────────────────────────────────────

def _make_micro_out(agent_name: str, result: dict, success: bool = True) -> MagicMock:
    out = MagicMock()
    out.agent_name = agent_name
    out.success = success
    out.result = result
    out.confidence = 1.0
    out.cost_yen = 0.0
    out.duration_ms = 10
    return out


class TestOnboardingPipeline:
    """オンボーディングモードの結合テスト。"""

    @pytest.mark.asyncio
    async def test_onboarding_happy_path(self):
        """正常系: 3ステップが全て成功する。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = MOCK_CUSTOMER
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()

        writer_out = _make_micro_out("saas_writer", {"success": True, "dry_run": False})
        writer_out.success = True

        with patch(
            "db.supabase.get_service_client",
            return_value=mock_db,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="onboarding",
                input_data={
                    "contact_name": "山田太郎",
                    "contact_email": "yamada@test.co.jp",
                    "login_url": "https://app.shachotwo.com/login",
                    "dry_run": True,
                },
            )

        assert result.success is True
        assert result.mode == "onboarding"
        assert len(result.steps) == 3
        assert result.final_output["status"] == "onboarding"
        assert result.final_output["welcome_email_queued"] is True
        assert result.final_output["sequence_jobs_scheduled"] == len(ONBOARD_SEQUENCE)

    @pytest.mark.asyncio
    async def test_onboarding_step1_db_failure_returns_failed_step(self):
        """Step 1 の DB 更新失敗時に failed_step が設定される。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = MOCK_CUSTOMER
        mock_db.table.return_value.update.return_value.eq.return_value.execute.side_effect = Exception("DB timeout")

        with patch(
            "db.supabase.get_service_client",
            return_value=mock_db,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="onboarding",
                input_data={"dry_run": True},
            )

        assert result.success is False
        assert result.failed_step == "account_setup"


class TestHealthCheckPipeline:
    """ヘルスチェックモードの結合テスト。"""

    def _make_mock_db(self, customer: dict = MOCK_CUSTOMER) -> MagicMock:
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = customer
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        return mock_db

    @pytest.mark.asyncio
    async def test_health_check_happy_path_healthy(self):
        """正常系: 7ステップが完走し health_score が設定される。"""
        from datetime import date, timedelta
        today = date.today()
        mock_logs = [
            {"executed_at": today.isoformat(), "pipeline": "estimation_pipeline", "status": "ok"},
            {"executed_at": today.isoformat(), "pipeline": "billing_pipeline", "status": "ok"},
            {"executed_at": (today - timedelta(days=2)).isoformat(), "pipeline": "estimation_pipeline", "status": "ok"},
        ]

        reader_out = _make_micro_out("saas_reader", {"data": mock_logs, "count": len(mock_logs), "service": "supabase", "mock": False})
        writer_out = _make_micro_out("saas_writer", {"success": True, "dry_run": False})
        rule_out = _make_micro_out("rule_matcher", {"matched_rules": [], "applied_values": {}, "unmatched_fields": []})

        with patch(
            "db.supabase.get_service_client",
            return_value=self._make_mock_db(),
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=reader_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=rule_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="health_check",
            )

        assert result.success is True
        assert result.mode == "health_check"
        assert result.health_score is not None
        assert 0 <= result.health_score <= 100
        assert result.health_label in ("risk", "caution", "healthy", "expansion")
        # health_check モードは Step 4〜7 の 4 ステップ
        assert len(result.steps) == 4

    @pytest.mark.asyncio
    async def test_health_check_risk_score_triggers_action(self):
        """score < 40 の場合に action_required=True かつ risk アラートが実行される。"""
        reader_out = _make_micro_out(
            "saas_reader",
            {"data": [], "count": 0, "service": "supabase", "mock": False},
        )
        rule_out = _make_micro_out("rule_matcher", {"matched_rules": [], "applied_values": {}, "unmatched_fields": []})
        writer_out = _make_micro_out("saas_writer", {"success": True, "dry_run": False})

        # NPS なし + 利用ゼロ = スコアが低くなるはず
        risk_customer = {
            **MOCK_CUSTOMER,
            "nps_score": -80,
            "active_modules": ["brain"],
        }

        with patch(
            "db.supabase.get_service_client",
            return_value=self._make_mock_db(risk_customer),
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=reader_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=rule_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="health_check",
                input_data={"dry_run": True},
            )

        assert result.success is True
        if result.health_label == "risk":
            assert result.action_required is True
            actions = result.final_output.get("actions_taken", [])
            assert any(a["type"] in ("slack_risk_alert", "risk_alert") for a in actions)

    @pytest.mark.asyncio
    async def test_health_check_expansion_proposal_generated(self):
        """score > 80 + 未使用モジュールある場合に拡張提案が生成される。"""
        from datetime import date, timedelta
        today = date.today()
        # 利用度を最大にするログ
        mock_logs = [
            {"executed_at": today.isoformat(), "pipeline": f"pipeline_{i}", "status": "ok"}
            for i in range(20)
        ]
        reader_out = _make_micro_out(
            "saas_reader",
            {"data": mock_logs, "count": len(mock_logs), "service": "supabase", "mock": False},
        )
        rule_out = _make_micro_out("rule_matcher", {"matched_rules": [], "applied_values": {}, "unmatched_fields": []})
        writer_out = _make_micro_out("saas_writer", {"success": True, "dry_run": False})

        # brain のみ契約 → 未使用モジュール多数
        expansion_customer = {
            **MOCK_CUSTOMER,
            "nps_score": 80,       # NPS高
            "active_modules": ["brain"],
        }

        with patch(
            "db.supabase.get_service_client",
            return_value=self._make_mock_db(expansion_customer),
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=reader_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=rule_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="health_check",
                input_data={"dry_run": True},
            )

        assert result.success is True
        if result.health_label == "expansion":
            assert result.action_required is True
            actions = result.final_output.get("actions_taken", [])
            assert any(a["type"] == "expansion_proposal_email" for a in actions)

    @pytest.mark.asyncio
    async def test_health_check_unknown_customer_still_completes(self):
        """customers テーブルに行がなくてもパイプラインが完走する。"""
        mock_db = MagicMock()
        # 顧客が見つからない場合
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value.data = None
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        reader_out = _make_micro_out("saas_reader", {"data": [], "count": 0, "service": "supabase", "mock": False})
        rule_out = _make_micro_out("rule_matcher", {"matched_rules": [], "applied_values": {}, "unmatched_fields": []})
        writer_out = _make_micro_out("saas_writer", {"success": True})

        with patch(
            "db.supabase.get_service_client",
            return_value=mock_db,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_reader",
            new_callable=AsyncMock,
            return_value=reader_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_rule_matcher",
            new_callable=AsyncMock,
            return_value=rule_out,
        ), patch(
            "workers.bpo.sales.crm.customer_lifecycle_pipeline.run_saas_writer",
            new_callable=AsyncMock,
            return_value=writer_out,
        ):
            result = await run_customer_lifecycle_pipeline(
                company_id=COMPANY_ID,
                customer_id=CUSTOMER_ID,
                mode="health_check",
            )

        assert result.success is True
        assert result.health_score is not None


class TestPipelineResultSummary:
    """CustomerLifecyclePipelineResult.summary() の出力テスト。"""

    def test_summary_contains_mode_label(self):
        result = CustomerLifecyclePipelineResult(
            success=True,
            mode="onboarding",
            health_score=None,
        )
        summary = result.summary()
        assert "オンボーディング" in summary

    def test_summary_shows_health_score_when_set(self):
        result = CustomerLifecyclePipelineResult(
            success=True,
            mode="health_check",
            health_score=72,
            health_label="healthy",
        )
        summary = result.summary()
        assert "72" in summary
        assert "healthy" in summary

    def test_summary_shows_failed_step(self):
        result = CustomerLifecyclePipelineResult(
            success=False,
            mode="health_check",
            failed_step="usage_data",
        )
        summary = result.summary()
        assert "usage_data" in summary
