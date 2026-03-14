"""Tests for brain/genome (templates, applicator)."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.genome.models import GenomeTemplate
from brain.genome.templates import get_template, get_template_for_industry, list_templates, load_templates
from brain.genome.applicator import apply_template


class TestTemplateLoading:
    def test_load_templates(self):
        load_templates()
        templates = list_templates()
        assert len(templates) >= 2

    def test_get_construction_template(self):
        load_templates()
        t = get_template("construction")
        assert t is not None
        assert t.name == "建設業"
        assert t.total_items > 20

    def test_get_manufacturing_template(self):
        load_templates()
        t = get_template("manufacturing")
        assert t is not None
        assert t.name == "製造業"
        assert t.total_items > 25

    def test_get_nonexistent_template(self):
        load_templates()
        assert get_template("nonexistent") is None

    def test_industry_matching(self):
        load_templates()
        t = get_template_for_industry("建設")
        assert t is not None
        assert t.id == "construction"

        t = get_template_for_industry("製造")
        assert t is not None
        assert t.id == "manufacturing"

    def test_industry_matching_no_match(self):
        load_templates()
        assert get_template_for_industry("農業") is None


class TestTemplateStructure:
    def test_construction_departments(self):
        load_templates()
        t = get_template("construction")
        dept_names = {d.name for d in t.departments}
        assert "現場管理" in dept_names
        assert "営業" in dept_names
        assert "経理" in dept_names
        assert "安全管理" in dept_names
        assert "総務" in dept_names

    def test_manufacturing_departments(self):
        load_templates()
        t = get_template("manufacturing")
        dept_names = {d.name for d in t.departments}
        assert "製造" in dept_names
        assert "品質管理" in dept_names
        assert "生産管理" in dept_names
        assert "営業" in dept_names

    def test_all_items_have_required_fields(self):
        load_templates()
        for t in list_templates():
            for dept in t.departments:
                for item in dept.items:
                    assert item.title
                    assert item.content
                    assert item.category
                    assert item.item_type in ("rule", "flow", "decision_logic", "fact", "tip")
                    assert item.department
                    assert 0 <= item.confidence <= 1


class TestApplyTemplate:
    @pytest.mark.asyncio
    async def test_apply_full_template(self):
        load_templates()
        company_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            result = await apply_template("construction", company_id)

        assert result.template_id == "construction"
        assert result.items_created > 20
        assert "現場管理" in result.departments
        # Verify DB was called
        mock_db.table.return_value.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_with_department_filter(self):
        load_templates()
        company_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            result = await apply_template(
                "construction",
                company_id,
                customizations={"departments": ["営業", "経理"]},
            )

        assert set(result.departments) == {"営業", "経理"}
        assert result.items_created < get_template("construction").total_items

    @pytest.mark.asyncio
    async def test_apply_nonexistent_template(self):
        with pytest.raises(ValueError, match="Template not found"):
            await apply_template("nonexistent", str(uuid4()))
