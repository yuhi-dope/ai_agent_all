"""TaskRouter — 全41パイプライン + sales系12パイプライン ルーティング検証テスト。

検証内容:
1. PIPELINE_REGISTRY に全41本が登録されていること
2. sales系12本（ドメイン別エイリアスキー）が全て登録されていること
3. 各パイプラインキーのパス形式が正しいこと（モジュール.関数 形式）
4. route_and_execute がsales系各キーで正しいパイプライン関数を呼び出すこと
5. ドメイン別エイリアスと sales/ プレフィックスキーが同一パイプライン関数を指すこと
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.manager.models import BPOTask, TriggerType, ExecutionLevel
from workers.bpo.manager.task_router import PIPELINE_REGISTRY, route_and_execute

COMPANY_ID = "テスト会社-001"


def _make_task(pipeline: str, level: int = 1, impact: float = 0.3) -> BPOTask:
    """テスト用BPOTaskを生成する（デフォルトは承認不要のLevel 1）。"""
    return BPOTask(
        company_id=COMPANY_ID,
        pipeline=pipeline,
        trigger_type=TriggerType.SCHEDULE,
        execution_level=ExecutionLevel(level),
        input_data={"テスト": "データ"},
        estimated_impact=impact,
    )


# ─── 全パイプライン登録確認 ──────────────────────────────────────────────────

class TestPipelineRegistryCompleteness:
    """PIPELINE_REGISTRY の登録内容が完全であることを検証する。"""

    # BPO系18本（Phase 1コア6業界 + 共通BPO）
    # 凍結業種（歯科・飲食・不動産・士業・調剤・美容・整備・ホテル・EC・派遣・設計）はPhase 2で復活
    BPO_PIPELINES = [
        # 建設業 (8本)
        "construction/estimation",
        "construction/billing",
        "construction/safety_docs",
        "construction/cost_report",
        "construction/photo_organize",
        "construction/subcontractor",
        "construction/permit",
        "construction/construction_plan",
        # 製造業 (1本)
        "manufacturing/quoting",
        # 共通BPO (6本)
        "common/expense",
        "common/payroll",
        "common/attendance",
        "common/contract",
        "common/admin_reminder",
        "common/vendor",
        # 介護・福祉 (1本)
        "nursing/care_billing",
        # 物流・運送 (1本)
        "logistics/dispatch",
        # 医療クリニック (1本)
        "clinic/medical_receipt",
        # 不動産管理 (1本)
        "realestate/rent_collection",
        # 卸売業 (5本)
        "wholesale/order_processing",
        "wholesale/inventory_management",
        "wholesale/accounts_receivable",
        "wholesale/accounts_payable",
        "wholesale/shipping",
        "wholesale/sales_intelligence",
    ]

    # sales系12本（ドメイン別エイリアスキー）
    SALES_ALIAS_PIPELINES = [
        "marketing/outreach",
        "sfa/lead_qualification",
        "sfa/proposal_generation",
        "sfa/quotation_contract",
        "sfa/consent_flow",
        "crm/customer_lifecycle",
        "crm/revenue_request",
        "cs/support_auto_response",
        "cs/upsell_briefing",
        "cs/cancellation",
        "learning/win_loss_feedback",
        "learning/cs_feedback",
    ]

    # sales系（sales/ プレフィックス内部キー）
    SALES_INTERNAL_PIPELINES = [
        "sales/outreach",
        "sales/lead_qualification",
        "sales/proposal_generation",
        "sales/quotation_contract",
        "sales/consent_flow",
        "sales/customer_lifecycle",
        "sales/support_auto_response",
        "sales/win_loss_feedback",
        "sales/upsell_briefing",
        "sales/cancellation",
        "sales/revenue_report",
        "sales/cs_feedback",
    ]

    @pytest.mark.parametrize("pipeline_key", BPO_PIPELINES)
    def test_bpo_pipeline_registered(self, pipeline_key: str):
        """BPO系29本が全てPIPELINE_REGISTRYに登録されていること。"""
        assert pipeline_key in PIPELINE_REGISTRY, (
            f"BPOパイプライン '{pipeline_key}' がPIPELINE_REGISTRYに未登録"
        )

    @pytest.mark.parametrize("pipeline_key", SALES_ALIAS_PIPELINES)
    def test_sales_alias_pipeline_registered(self, pipeline_key: str):
        """sales系ドメイン別エイリアス12本が全て登録されていること。"""
        assert pipeline_key in PIPELINE_REGISTRY, (
            f"sales系エイリアス '{pipeline_key}' がPIPELINE_REGISTRYに未登録"
        )

    @pytest.mark.parametrize("pipeline_key", SALES_INTERNAL_PIPELINES)
    def test_sales_internal_pipeline_registered(self, pipeline_key: str):
        """sales/ プレフィックスの内部キー12本が全て登録されていること。"""
        assert pipeline_key in PIPELINE_REGISTRY, (
            f"sales内部キー '{pipeline_key}' がPIPELINE_REGISTRYに未登録"
        )

    def test_total_pipeline_count_is_at_least_60(self):
        """PIPELINE_REGISTRY の総数が最低60本以上であること。"""
        # Phase 1: コア6業界BPO(18) + 共通BPO(12) + sales系(24) + 内部パイプライン(3) + バックオフィス(12) = 69本
        assert len(PIPELINE_REGISTRY) >= 60, (
            f"パイプライン総数が不足: {len(PIPELINE_REGISTRY)} < 60"
        )

    def test_all_entries_have_valid_module_path_format(self):
        """全エントリが 'モジュール.関数名' のドット区切り形式であること。"""
        for key, path in PIPELINE_REGISTRY.items():
            assert "." in path, f"'{key}' のパス形式不正（ドットなし）: {path}"
            module_path, func_name = path.rsplit(".", 1)
            # internal/ プレフィックスのパイプラインは brain.* モジュール由来を許可
            if not key.startswith("internal/"):
                assert module_path.startswith("workers."), (
                    f"'{key}' のモジュールパスが workers. で始まっていない: {module_path}"
                )
            assert "pipeline" in func_name or "flow" in func_name or "cycle" in func_name, (
                f"'{key}' の関数名にpipeline/flow/cycleが含まれていない: {func_name}"
            )


# ─── ドメイン別エイリアスと sales/ キーの同一性検証 ──────────────────────────

class TestSalesAliasEquivalence:
    """ドメイン別エイリアスが sales/ キーと同一パイプライン関数を指すことを検証する。"""

    ALIAS_TO_INTERNAL = {
        "marketing/outreach":         "sales/outreach",
        "sfa/lead_qualification":     "sales/lead_qualification",
        "sfa/proposal_generation":    "sales/proposal_generation",
        "sfa/quotation_contract":     "sales/quotation_contract",
        "sfa/consent_flow":           "sales/consent_flow",
        "crm/customer_lifecycle":     "sales/customer_lifecycle",
        "crm/revenue_request":        "sales/revenue_report",
        "cs/support_auto_response":   "sales/support_auto_response",
        "cs/upsell_briefing":         "sales/upsell_briefing",
        "cs/cancellation":            "sales/cancellation",
        "learning/win_loss_feedback": "sales/win_loss_feedback",
        "learning/cs_feedback":       "sales/cs_feedback",
    }

    @pytest.mark.parametrize("alias_key,internal_key", ALIAS_TO_INTERNAL.items())
    def test_alias_points_to_same_function_as_internal(self, alias_key: str, internal_key: str):
        """エイリアスキーと内部キーが同一のパイプライン関数パスを指すこと。"""
        alias_path = PIPELINE_REGISTRY[alias_key]
        internal_path = PIPELINE_REGISTRY[internal_key]
        assert alias_path == internal_path, (
            f"'{alias_key}' と '{internal_key}' が異なる関数を指している\n"
            f"  エイリアス: {alias_path}\n"
            f"  内部キー:   {internal_path}"
        )


# ─── sales系12本 ルーティング実行テスト ──────────────────────────────────────

class TestSalesPipelinesRouting:
    """sales系12本のパイプラインがroute_and_executeで正しくルーティングされることを検証する。"""

    SALES_PIPELINES_WITH_DESCRIPTIONS = [
        ("marketing/outreach",         "アウトリーチパイプライン（企業リサーチ＆送信）"),
        ("sfa/lead_qualification",     "リード資格審査パイプライン"),
        ("sfa/proposal_generation",    "提案書自動生成パイプライン"),
        ("sfa/quotation_contract",     "見積・契約書作成パイプライン"),
        ("sfa/consent_flow",           "電子同意フローパイプライン"),
        ("crm/customer_lifecycle",     "顧客ライフサイクルパイプライン"),
        ("crm/revenue_request",        "収益・要望レポートパイプライン"),
        ("cs/support_auto_response",   "サポート自動応答パイプライン"),
        ("cs/upsell_briefing",         "アップセル提案パイプライン"),
        ("cs/cancellation",            "解約フローパイプライン"),
        ("learning/win_loss_feedback", "受注/失注フィードバックパイプライン"),
        ("learning/cs_feedback",       "CS品質月次レビューパイプライン"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("pipeline_key,description", SALES_PIPELINES_WITH_DESCRIPTIONS)
    async def test_sales_pipeline_routes_to_correct_function(
        self, pipeline_key: str, description: str
    ):
        """各sales系パイプラインがPIPELINE_REGISTRYの正しい関数にルーティングされること。"""
        task = _make_task(pipeline=pipeline_key, level=1, impact=0.3)
        expected_path = PIPELINE_REGISTRY[pipeline_key]
        expected_module, expected_func = expected_path.rsplit(".", 1)

        mock_pipeline = AsyncMock(return_value=MagicMock(
            success=True,
            steps=[],
            final_output={"result": f"{description} 完了"},
            total_cost_yen=10.0,
            total_duration_ms=100,
            failed_step=None,
        ))

        with patch(f"workers.bpo.manager.task_router.importlib.import_module") as mock_import:
            mock_module = MagicMock()
            setattr(mock_module, expected_func, mock_pipeline)
            mock_import.return_value = mock_module

            result = await route_and_execute(task)

        # import_module が正しいモジュールパスで呼ばれたこと（最後の呼び出しをチェック）
        calls = mock_import.call_args_list
        assert any(call[0][0] == expected_module for call in calls), (
            f"expected_module {expected_module} が import_module で呼ばれていない"
        )
        # パイプライン関数が呼ばれたこと
        mock_pipeline.assert_called_once()
        # 成功結果が返ること
        assert result.success is True, (
            f"{description} ({pipeline_key}) が失敗: {result.final_output}"
        )
        assert result.approval_pending is False
        assert result.pipeline == pipeline_key

    @pytest.mark.asyncio
    async def test_unregistered_sales_pipeline_returns_failure(self):
        """未登録のsales系キーを指定した場合は失敗を返すこと。"""
        task = _make_task(pipeline="sales/nonexistent_pipeline")
        result = await route_and_execute(task)
        assert result.success is False
        assert "未登録" in result.final_output.get("error", "")
        assert result.failed_step == "task_router"

    @pytest.mark.asyncio
    async def test_cancellation_pipeline_requires_approval_by_default(self):
        """解約フロー（cs/cancellation）はインパクト高（0.9）でLevel 3なら承認待ちになること。"""
        task = _make_task(pipeline="cs/cancellation", level=3, impact=0.9)

        with patch(
            "workers.bpo.manager.task_router._save_approval_pending",
            new_callable=AsyncMock,
        ):
            result = await route_and_execute(task)

        assert result.approval_pending is True
        assert result.success is True
        assert result.pipeline == "cs/cancellation"

    @pytest.mark.asyncio
    async def test_outreach_pipeline_executes_without_approval_at_level1(self):
        """outreach（marketing/outreach）はLevel 1（データ収集）で承認不要で実行されること。"""
        task = _make_task(pipeline="marketing/outreach", level=1, impact=0.2)
        expected_func = PIPELINE_REGISTRY["marketing/outreach"].rsplit(".", 1)[1]

        mock_pipeline = AsyncMock(return_value=MagicMock(
            success=True,
            steps=[],
            final_output={"送信件数": 400},
            total_cost_yen=5.0,
            total_duration_ms=200,
            failed_step=None,
        ))

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            setattr(mock_module, expected_func, mock_pipeline)
            mock_import.return_value = mock_module

            result = await route_and_execute(task)

        assert result.success is True
        assert result.approval_pending is False
        mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_lead_qualification_passes_company_id_to_pipeline(self):
        """リード資格審査パイプラインにcompany_idが渡されること。"""
        task = _make_task(pipeline="sfa/lead_qualification", level=1, impact=0.3)
        task.input_data = {"lead_id": "LEAD-テスト-001", "会社名": "テスト株式会社"}
        expected_func = PIPELINE_REGISTRY["sfa/lead_qualification"].rsplit(".", 1)[1]

        mock_pipeline = AsyncMock(return_value=MagicMock(
            success=True,
            steps=[],
            final_output={"スコア": 75},
            total_cost_yen=3.0,
            total_duration_ms=150,
            failed_step=None,
        ))

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            setattr(mock_module, expected_func, mock_pipeline)
            mock_import.return_value = mock_module

            result = await route_and_execute(task)

        assert result.success is True
        call_kwargs = mock_pipeline.call_args.kwargs
        assert call_kwargs.get("company_id") == COMPANY_ID
        assert call_kwargs.get("input_data") == task.input_data


# ─── BPO系29本 ルーティング登録確認 ──────────────────────────────────────────

class TestBpoPipelinesRegistration:
    """BPO系29本が正しいモジュールパスを持つことを検証する。"""

    EXPECTED_MODULE_PREFIXES = {
        "construction/estimation":        "workers.bpo.construction.",
        "construction/billing":           "workers.bpo.construction.",
        "construction/safety_docs":       "workers.bpo.construction.",
        "construction/cost_report":       "workers.bpo.construction.",
        "construction/photo_organize":    "workers.bpo.construction.",
        "construction/subcontractor":     "workers.bpo.construction.",
        "construction/permit":            "workers.bpo.construction.",
        "construction/construction_plan": "workers.bpo.construction.",
        "manufacturing/quoting":          "workers.bpo.manufacturing.",
        "common/expense":                 "workers.bpo.common.",
        "common/payroll":                 "workers.bpo.common.",
        "common/attendance":              "workers.bpo.common.",
        "common/contract":                "workers.bpo.common.",
        "common/admin_reminder":          "workers.bpo.common.",
        "common/vendor":                  "workers.bpo.common.",
        "nursing/care_billing":           "workers.bpo.nursing.",
        "logistics/dispatch":             "workers.bpo.logistics.",
        "clinic/medical_receipt":         "workers.bpo.clinic.",
        "realestate/rent_collection":     "workers.bpo.realestate.",
        "wholesale/order_processing":     "workers.bpo.wholesale.",
        "wholesale/inventory_management": "workers.bpo.wholesale.",
        "wholesale/accounts_receivable":  "workers.bpo.wholesale.",
        "wholesale/accounts_payable":     "workers.bpo.wholesale.",
        "wholesale/shipping":             "workers.bpo.wholesale.",
        "wholesale/sales_intelligence":   "workers.bpo.wholesale.",
    }

    @pytest.mark.parametrize("pipeline_key,expected_prefix", EXPECTED_MODULE_PREFIXES.items())
    def test_bpo_pipeline_module_prefix(self, pipeline_key: str, expected_prefix: str):
        """各BPOパイプラインのモジュールパスが正しい業種プレフィックスを持つこと。"""
        assert pipeline_key in PIPELINE_REGISTRY
        path = PIPELINE_REGISTRY[pipeline_key]
        assert path.startswith(expected_prefix), (
            f"'{pipeline_key}' のモジュールパスが期待するプレフィックスと異なる\n"
            f"  期待: {expected_prefix}...\n"
            f"  実際: {path}"
        )


# ─── sales系モジュールパス検証 ────────────────────────────────────────────────

class TestSalesPipelineModulePaths:
    """sales系パイプラインが正しいモジュール配下を指すことを検証する。"""

    EXPECTED_SALES_PATHS = {
        "marketing/outreach":         "workers.bpo.sales.marketing.",
        "sfa/lead_qualification":     "workers.bpo.sales.sfa.",
        "sfa/proposal_generation":    "workers.bpo.sales.sfa.",
        "sfa/quotation_contract":     "workers.bpo.sales.sfa.",
        "sfa/consent_flow":           "workers.bpo.sales.sfa.",
        "crm/customer_lifecycle":     "workers.bpo.sales.crm.",
        "crm/revenue_request":        "workers.bpo.sales.crm.",
        "cs/support_auto_response":   "workers.bpo.sales.cs.",
        "cs/upsell_briefing":         "workers.bpo.sales.cs.",
        "cs/cancellation":            "workers.bpo.sales.cs.",
        "learning/win_loss_feedback": "workers.bpo.sales.learning.",
        "learning/cs_feedback":       "workers.bpo.sales.learning.",
    }

    @pytest.mark.parametrize("pipeline_key,expected_prefix", EXPECTED_SALES_PATHS.items())
    def test_sales_pipeline_module_path(self, pipeline_key: str, expected_prefix: str):
        """sales系各パイプラインが正しいサブドメイン配下のモジュールを指すこと。"""
        assert pipeline_key in PIPELINE_REGISTRY
        path = PIPELINE_REGISTRY[pipeline_key]
        assert path.startswith(expected_prefix), (
            f"'{pipeline_key}' のモジュールパスが期待するプレフィックスと異なる\n"
            f"  期待: {expected_prefix}...\n"
            f"  実際: {path}"
        )

    def test_consent_flow_points_to_correct_function(self):
        """sfa/consent_flow が run_consent_flow_pipeline を指すこと。"""
        path = PIPELINE_REGISTRY["sfa/consent_flow"]
        assert path.endswith("run_consent_flow_pipeline"), (
            f"sfa/consent_flow の関数名が不正: {path}"
        )

    def test_revenue_request_alias_points_to_revenue_request_pipeline(self):
        """crm/revenue_request が revenue_request_pipeline を指すこと。"""
        path = PIPELINE_REGISTRY["crm/revenue_request"]
        assert "revenue_request_pipeline" in path, (
            f"crm/revenue_request のモジュールパスが不正: {path}"
        )
