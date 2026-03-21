"""製造業3層エンジン E2Eテスト"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from workers.bpo.manufacturing.engine import ManufacturingQuotingEngine, load_yaml_config, clear_yaml_cache
from workers.bpo.manufacturing.models import HearingInput, QuoteResult, CustomerOverrides


class TestEngineE2E:
    """3層エンジンのエンドツーエンドテスト"""

    @pytest.fixture(autouse=True)
    def setup(self):
        clear_yaml_cache()

    def _mock_db(self):
        """DB操作をモック"""
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[], count=0)
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "test-quote-id"}])
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
        return mock_client

    @pytest.mark.asyncio
    async def test_metalwork_yaml_layer(self):
        """Layer 1: 金属加工のYAML設定で工程推定できるか"""
        hearing = HearingInput(
            product_name="テストシャフト",
            specification="φ30×100mmの丸物シャフト",
            material="SUS304",
            quantity=10,
            shape_type="round",
            sub_industry="metalwork",
            company_id="test-company-id",
        )
        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()):
            result = await engine.run(hearing)

        assert isinstance(result, QuoteResult)
        assert result.sub_industry == "metalwork"
        assert len(result.processes) > 0
        assert result.costs is not None
        assert result.costs.total_amount > 0
        # YAML層が使われたことを確認
        process_layer = next((l for l in result.layers_used if l.field == "processes"), None)
        assert process_layer is not None
        assert process_layer.layer == "yaml"

    @pytest.mark.asyncio
    async def test_metalwork_with_surface_treatment(self):
        """表面処理付きの金属加工"""
        hearing = HearingInput(
            product_name="アルミブラケット",
            specification="板物、100×80×5mm",
            material="A5052",
            quantity=50,
            shape_type="plate",
            surface_treatment="アルマイト",
            sub_industry="metalwork",
            company_id="test-company-id",
        )
        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()):
            result = await engine.run(hearing)

        assert result.costs.total_amount > 0
        # 表面処理が外注工程として含まれているか
        outsource = [p for p in result.processes if p.is_outsource]
        assert len(outsource) > 0
        assert any("アルマイト" in p.process_name for p in outsource)

    @pytest.mark.asyncio
    async def test_plastics_plugin(self):
        """Layer 2: 樹脂成型プラグインが動作するか"""
        hearing = HearingInput(
            product_name="樹脂ケース",
            specification="ABS樹脂の筐体",
            material="ABS",
            quantity=1000,
            sub_industry="plastics",
            company_id="test-company-id",
        )
        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()):
            result = await engine.run(hearing)

        assert result.sub_industry == "plastics"
        assert len(result.processes) > 0
        # 金型償却が追加コストに含まれるか
        assert len(result.additional_costs) > 0
        assert any(c.cost_type == "mold_amortization" for c in result.additional_costs)
        # プラグイン層が使われたことを確認
        process_layer = next((l for l in result.layers_used if l.field == "processes"), None)
        assert process_layer.layer == "plugin"

    @pytest.mark.asyncio
    async def test_electronics_plugin(self):
        """Layer 2: 電子部品プラグインが動作するか"""
        hearing = HearingInput(
            product_name="制御基板",
            specification="4層基板、SMT+スルーホール混在",
            material="FR4",
            quantity=100,
            sub_industry="electronics",
            bom=[
                {"name": "マイコン", "unit_price": 500, "quantity": 1, "mount_type": "smt"},
                {"name": "コネクタ", "unit_price": 200, "quantity": 3, "mount_type": "through_hole"},
            ],
            company_id="test-company-id",
        )
        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()):
            result = await engine.run(hearing)

        assert result.sub_industry == "electronics"
        # BOM部品費が追加コストに含まれるか
        bom_cost = next((c for c in result.additional_costs if c.cost_type == "bom_components"), None)
        assert bom_cost is not None
        assert bom_cost.amount == 500 * 1 + 200 * 3  # 1100

    @pytest.mark.asyncio
    async def test_food_chemical_plugin(self):
        """Layer 2: 食品・化学プラグインが動作するか"""
        hearing = HearingInput(
            product_name="レトルトカレー",
            specification="200gパウチ",
            material="各種食材",
            quantity=5000,
            sub_industry="food_chemical",
            batch_size_kg=500.0,
            company_id="test-company-id",
        )
        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()):
            result = await engine.run(hearing)

        assert result.sub_industry == "food_chemical"
        # 配合ロスが追加コストに含まれるか
        loss = next((c for c in result.additional_costs if c.cost_type == "recipe_loss"), None)
        assert loss is not None

    @pytest.mark.asyncio
    async def test_unknown_industry_llm_fallback(self):
        """Layer 0: 未知の業種でLLMフォールバックが動作するか"""
        hearing = HearingInput(
            product_name="繊維製品",
            specification="ポリエステル生地の裁断・縫製",
            material="ポリエステル",
            quantity=100,
            sub_industry="textile",  # YAMLもPluginもない業種
            company_id="test-company-id",
        )

        # LLMをモック
        mock_llm_response = MagicMock()
        mock_llm_response.content = '{"processes": [{"sort_order": 1, "process_name": "裁断", "equipment_type": "cutting", "setup_time_min": 20, "cycle_time_min": 5, "is_outsource": false, "confidence": 0.4, "notes": "LLM推定"}], "overall_confidence": 0.4}'

        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=self._mock_db()), \
             patch("workers.bpo.manufacturing.engine.get_llm_client") as mock_llm:
            mock_client = AsyncMock()
            mock_client.generate.return_value = mock_llm_response
            mock_llm.return_value = mock_client
            result = await engine.run(hearing)

        assert result.sub_industry == "textile"
        # LLM層が使われたことを確認
        process_layer = next((l for l in result.layers_used if l.field == "processes"), None)
        assert process_layer.layer == "llm"

    @pytest.mark.asyncio
    async def test_customer_override_charge_rate(self):
        """顧客DBのチャージレートがYAMLデフォルトより優先されるか"""
        hearing = HearingInput(
            product_name="テストブロック",
            material="SS400",
            quantity=5,
            shape_type="block",
            sub_industry="metalwork",
            company_id="test-company-id",
        )

        # 顧客のチャージレートをDBに設定
        mock_client = self._mock_db()
        cr_result = MagicMock()
        cr_result.data = [{"equipment_name": "マシニングセンタ", "charge_rate": 15000, "setup_time_default": None}]
        mp_result = MagicMock()
        mp_result.data = []

        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name == "mfg_charge_rates":
                mock_table.select.return_value.eq.return_value.execute.return_value = cr_result
            elif table_name == "mfg_material_prices":
                mock_table.select.return_value.eq.return_value.execute.return_value = mp_result
            else:
                mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[], count=0)
                mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "test-id"}])
            return mock_table

        mock_client.table.side_effect = table_side_effect

        engine = ManufacturingQuotingEngine()
        with patch("workers.bpo.manufacturing.engine.get_service_client", return_value=mock_client):
            result = await engine.run(hearing)

        # チャージレートが顧客DBの値(15000)で使われたことを確認
        cr_layers = [l for l in result.layers_used if "charge_rate:マシニングセンタ" in l.field]
        assert len(cr_layers) > 0
        assert cr_layers[0].layer == "customer_db"
        assert cr_layers[0].value == "15000"

    @pytest.mark.asyncio
    async def test_jsic_code_routing(self):
        """JSICコードからsub_industryが正しく判定されるか"""
        engine = ManufacturingQuotingEngine()

        hearing_metal = HearingInput(jsic_code="E-24", company_id="test")
        assert engine._resolve_sub_industry(hearing_metal) == "metalwork"

        hearing_plastic = HearingInput(jsic_code="E-18", company_id="test")
        assert engine._resolve_sub_industry(hearing_plastic) == "plastics"

        hearing_food = HearingInput(jsic_code="E-09", company_id="test")
        assert engine._resolve_sub_industry(hearing_food) == "food_chemical"

        hearing_elec = HearingInput(jsic_code="E-28", company_id="test")
        assert engine._resolve_sub_industry(hearing_elec) == "electronics"

    @pytest.mark.asyncio
    async def test_yaml_config_loads(self):
        """metalwork.yamlが正常にロードできるか"""
        config = load_yaml_config("metalwork")
        assert config is not None
        assert "materials" in config
        assert "equipment" in config
        assert "process_routing_rules" in config
        assert "SS400" in config["materials"]
        assert "cnc_lathe" in config["equipment"]

    @pytest.mark.asyncio
    async def test_construction_plan_pipeline_import(self):
        """施工計画書パイプラインがインポートできるか"""
        from workers.bpo.construction.pipelines.construction_plan_pipeline import run_construction_plan_pipeline
        assert callable(run_construction_plan_pipeline)
