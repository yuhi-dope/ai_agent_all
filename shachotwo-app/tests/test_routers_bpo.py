"""BPOルーターのテスト"""
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

# main.pyのインポートでエラーが出ないことの確認
class TestBPORouterImports:
    """BPOルーターが正しくインポートできるか"""

    def test_construction_router_imports(self):
        from routers.bpo.construction import router
        assert router is not None

    def test_construction_models_import(self):
        from workers.bpo.construction.models import (
            EstimationProjectCreate,
            EstimationProjectResponse,
            ConstructionSiteCreate,
            WorkerCreate,
            ConstructionContractCreate,
            ProgressRecordCreate,
            CostRecordCreate,
        )
        # 基本的なモデル作成テスト
        project = EstimationProjectCreate(
            name="テスト工事",
            project_type="public_civil",
            region="東京都",
            fiscal_year=2026,
        )
        assert project.name == "テスト工事"
        assert project.project_type == "public_civil"

    def test_engine_models_import(self):
        from workers.bpo.engine.models import (
            BPOInvoiceCreate,
            BPOExpenseCreate,
            BPOVendorCreate,
            BPOPermitCreate,
            ApprovalStatus,
        )
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"

    def test_estimator_imports(self):
        from workers.bpo.construction.estimator import EstimationPipeline
        pipeline = EstimationPipeline()
        assert pipeline is not None

    def test_safety_docs_imports(self):
        from workers.bpo.construction.safety_docs import SafetyDocumentGenerator
        gen = SafetyDocumentGenerator()
        assert gen is not None

    def test_billing_imports(self):
        from workers.bpo.construction.billing import BillingEngine, CostReportEngine
        assert BillingEngine is not None
        assert CostReportEngine is not None


class TestDocumentGenerator:
    """Excel生成エンジンのテスト"""

    def test_generate_table(self):
        from workers.bpo.engine.document_gen import ExcelGenerator

        result = ExcelGenerator.generate_table(
            title="テスト",
            headers=["A", "B", "C"],
            rows=[["1", "2", "3"], ["4", "5", "6"]],
        )
        # Excel (xlsx) のマジックバイト
        assert result[:2] == b'PK'  # zipフォーマット
        assert len(result) > 100

    def test_generate_from_template(self):
        from workers.bpo.engine.document_gen import ExcelGenerator

        result = ExcelGenerator.generate_from_template({
            "title": "テスト内訳書",
            "meta": {"工事名": "テスト工事"},
            "headers": ["項目", "金額"],
            "rows": [["土工", 1000000], ["コンクリート", 2000000]],
            "totals": {"合計": 3000000},
        })
        assert result[:2] == b'PK'
        assert len(result) > 100


class TestApprovalWorkflow:
    """承認ワークフローのテスト"""

    def test_approval_status_enum(self):
        from workers.bpo.engine.models import ApprovalStatus
        assert ApprovalStatus.PENDING.value == "pending"
        assert ApprovalStatus.APPROVED.value == "approved"
        assert ApprovalStatus.REJECTED.value == "rejected"
        assert ApprovalStatus.CANCELLED.value == "cancelled"


class TestConstructionModels:
    """建設業モデルの検証テスト"""

    def test_overhead_breakdown(self):
        from workers.bpo.construction.models import OverheadBreakdown
        from decimal import Decimal

        breakdown = OverheadBreakdown(
            direct_cost=10000000,
            common_temporary=500000,
            common_temporary_rate=Decimal("0.05"),
            site_management=2100000,
            site_management_rate=Decimal("0.20"),
            general_admin=1512000,
            general_admin_rate=Decimal("0.12"),
            total=14112000,
        )
        assert breakdown.total == 14112000
        assert breakdown.direct_cost == 10000000

    def test_project_type_enum(self):
        from workers.bpo.construction.models import ProjectType
        assert ProjectType.PUBLIC_CIVIL == "public_civil"
        assert ProjectType.PRIVATE_CIVIL == "private_civil"

    def test_price_source_enum(self):
        from workers.bpo.construction.models import PriceSource
        assert PriceSource.MANUAL == "manual"
        assert PriceSource.AI_ESTIMATED == "ai_estimated"
        assert PriceSource.PAST_RECORD == "past_record"
