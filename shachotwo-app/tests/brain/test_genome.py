"""Tests for brain/genome (templates, applicator)."""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from brain.genome.models import GenomeTemplate, TemplateVariable
from brain.genome.templates import get_template, get_template_for_industry, list_templates, load_templates
from brain.genome.applicator import apply_template, _substitute_variables, _substitute_row_variables, _build_variables


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
        assert t.total_items >= 15

    def test_get_manufacturing_template(self):
        load_templates()
        t = get_template("manufacturing")
        assert t is not None
        assert t.name == "製造業"
        assert t.total_items >= 15

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
        assert "安全管理" in dept_names
        assert "建設業法務" in dept_names

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
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(count=0, data=[])

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            result = await apply_template("construction", company_id)

        assert result.template_id == "construction"
        assert result.items_created >= 15
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
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(count=0, data=[])

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            result = await apply_template(
                "construction",
                company_id,
                customizations={"departments": ["営業", "建設業法務"]},
            )

        assert "営業" in result.departments
        assert "建設業法務" in result.departments
        # commonテンプレートも含まれるため、constructionのフィルター分 + common分になる
        assert result.items_created > 0

    @pytest.mark.asyncio
    async def test_apply_nonexistent_template(self):
        with pytest.raises(ValueError, match="Template not found"):
            await apply_template("nonexistent", str(uuid4()))


class TestVariableSubstitution:
    """Tests for {{variable}} placeholder substitution in genome templates."""

    def test_substitute_variables_basic(self):
        text = "締め日は毎月{{expense_deadline_day}}日です。"
        result = _substitute_variables(text, {"expense_deadline_day": "10"})
        assert result == "締め日は毎月10日です。"

    def test_substitute_variables_multiple(self):
        text = "{{approval_tier_1_limit}}円以下は部門長、{{approval_tier_2_limit}}円以下は部長が承認。"
        result = _substitute_variables(text, {
            "approval_tier_1_limit": "50,000",
            "approval_tier_2_limit": "300,000",
        })
        assert result == "50,000円以下は部門長、300,000円以下は部長が承認。"

    def test_substitute_variables_unknown_key_preserved(self):
        """Placeholders with no matching key must be left as-is, not replaced with empty string."""
        text = "{{unknown_var}}は未定義です。"
        result = _substitute_variables(text, {})
        assert result == "{{unknown_var}}は未定義です。"

    def test_substitute_variables_no_placeholders(self):
        text = "プレースホルダーなしのテキスト。"
        result = _substitute_variables(text, {"some_var": "value"})
        assert result == "プレースホルダーなしのテキスト。"

    def test_substitute_row_variables_title_and_content(self):
        row = {
            "title": "{{payday}}日払い給与",
            "content": "給与は毎月{{payday}}日に支払われます。",
            "conditions": ["全社員に適用"],
            "examples": ["{{payday}}日が土日の場合は前営業日"],
            "exceptions": [],
        }
        result = _substitute_row_variables(row, {"payday": "25"})
        assert result["title"] == "25日払い給与"
        assert result["content"] == "給与は毎月25日に支払われます。"
        assert result["examples"] == ["25日が土日の場合は前営業日"]

    def test_substitute_row_variables_none_fields_safe(self):
        """Rows with None conditions/examples/exceptions must not raise errors."""
        row = {
            "title": "{{company_name}}テスト",
            "content": "本文{{company_name}}",
            "conditions": None,
            "examples": None,
            "exceptions": None,
        }
        result = _substitute_row_variables(row, {"company_name": "株式会社テスト"})
        assert result["title"] == "株式会社テストテスト"
        assert result["content"] == "本文株式会社テスト"
        assert result["conditions"] is None
        assert result["examples"] is None

    def test_build_variables_uses_template_defaults(self):
        template = GenomeTemplate(
            id="test",
            name="テスト",
            description="テスト",
            industry="テスト",
            sub_industries=[],
            typical_employee_range="10-50",
            variables={
                "payday": TemplateVariable(label="給料日", default="25", type="number"),
            },
            departments=[],
        )
        result = _build_variables(template, None)
        assert result["payday"] == "25"

    def test_build_variables_user_overrides_default(self):
        template = GenomeTemplate(
            id="test",
            name="テスト",
            description="テスト",
            industry="テスト",
            sub_industries=[],
            typical_employee_range="10-50",
            variables={
                "payday": TemplateVariable(label="給料日", default="25", type="number"),
                "bonus_months": TemplateVariable(label="賞与月", default="6月・12月", type="text"),
            },
            departments=[],
        )
        result = _build_variables(template, {"variables": {"payday": "20"}})
        assert result["payday"] == "20"         # user override applied
        assert result["bonus_months"] == "6月・12月"  # default kept


class TestCommonTemplateVariables:
    """Tests that common.json loads with the variables section correctly."""

    def test_common_template_has_variables(self):
        load_templates()
        t = get_template("common")
        assert t is not None
        assert len(t.variables) > 0

    def test_common_template_expense_deadline_day(self):
        load_templates()
        t = get_template("common")
        assert "expense_deadline_day" in t.variables
        var = t.variables["expense_deadline_day"]
        assert var.default == "10"
        assert var.type == "number"
        assert var.label == "経費精算の締め日"

    def test_common_template_company_name(self):
        load_templates()
        t = get_template("common")
        assert "company_name" in t.variables
        assert t.variables["company_name"].default == "当社"

    def test_common_template_all_variables_have_defaults(self):
        load_templates()
        t = get_template("common")
        for key, var in t.variables.items():
            assert var.default != "", f"Variable '{key}' has empty default"
            assert var.label != "", f"Variable '{key}' has empty label"
            assert var.type in ("text", "number"), f"Variable '{key}' has invalid type: {var.type}"

    @pytest.mark.asyncio
    async def test_apply_template_substitutes_variables(self):
        """End-to-end: applying a template with variables should replace placeholders in DB rows."""
        load_templates()
        company_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(count=0, data=[])

        inserted_rows: list[dict] = []

        def capture_insert(rows):
            inserted_rows.extend(rows)
            return mock_db.table.return_value.insert.return_value

        mock_db.table.return_value.insert.side_effect = capture_insert

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            await apply_template(
                "common",
                company_id,
                include_common=False,
                customizations={"variables": {"expense_deadline_day": "15", "company_name": "株式会社テスト"}},
            )

        # Find the row with 経費精算フロー
        expense_row = next((r for r in inserted_rows if "経費精算" in r.get("title", "")), None)
        assert expense_row is not None, "経費精算フローの行が見つからない"
        # The default value 10 should be replaced by the user-supplied 15
        assert "{{expense_deadline_day}}" not in expense_row["content"], \
            "expense_deadline_day placeholder was not substituted"
        assert "15" in expense_row["content"], \
            "User-supplied value '15' was not applied to expense_deadline_day"

        # Find the row with 来客・電話対応 (contains {{company_name}})
        phone_row = next((r for r in inserted_rows if "来客" in r.get("title", "")), None)
        assert phone_row is not None, "来客・電話対応の行が見つからない"
        assert "{{company_name}}" not in phone_row["content"], \
            "company_name placeholder was not substituted"
        assert "株式会社テスト" in phone_row["content"], \
            "User-supplied company_name was not applied"

    @pytest.mark.asyncio
    async def test_apply_template_default_substitution(self):
        """Without user customizations, template defaults should be substituted."""
        load_templates()
        company_id = str(uuid4())
        mock_db = MagicMock()
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(count=0, data=[])

        inserted_rows: list[dict] = []

        def capture_insert(rows):
            inserted_rows.extend(rows)
            return mock_db.table.return_value.insert.return_value

        mock_db.table.return_value.insert.side_effect = capture_insert

        with patch("brain.genome.applicator.get_service_client", return_value=mock_db):
            await apply_template("common", company_id, include_common=False)

        # Check that no {{...}} placeholders remain in any row
        for row in inserted_rows:
            for field in ("title", "content"):
                value = row.get(field, "") or ""
                assert "{{" not in value, \
                    f"Unsubstituted placeholder in {field} of row '{row.get('title')}': {value}"
            for field in ("conditions", "examples", "exceptions"):
                for item in (row.get(field) or []):
                    if isinstance(item, str):
                        assert "{{" not in item, \
                            f"Unsubstituted placeholder in {field} of row '{row.get('title')}': {item}"
