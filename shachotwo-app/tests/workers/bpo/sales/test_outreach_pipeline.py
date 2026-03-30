"""マーケ パイプライン⓪ アウトリーチ テスト"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.pipelines.outreach_pipeline import (
    run_outreach_pipeline,
    OutreachPipelineResult,
    DEFAULT_TARGET_INDUSTRY,
    MANUFACTURING_PAIN_FALLBACK,
    MANUFACTURING_SUBJECT_TEMPLATES,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = "test-company-001"


def _make_micro_out(agent_name: str, data: dict) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=data,
        confidence=0.9,
        cost_yen=0.5,
        duration_ms=100,
    )


def _make_patches():
    """共通パッチセットを返す。"""
    researcher_out = _make_micro_out(
        "company_researcher",
        {
            "company_name": "テスト製造株式会社",
            "industry": "製造業",
            "pain_points": [{"category": "見積業務", "detail": "手作業で時間がかかる", "appeal_message": "AI化で短縮"}],
            "scale": "中規模",
            "tone": "丁寧",
            "industry_tasks": ["見積", "品質管理"],
            "industry_appeal": "製造業向け特化",
        },
    )
    signal_out = _make_micro_out(
        "signal_detector",
        {"classifications": [], "followup_actions": [], "summary": {"hot": 0, "warm": 0, "cold": 1, "total": 1}},
    )
    gen_out = _make_micro_out(
        "document_generator",
        {"content": '{"subject": "製造業向けご提案", "body": "本文テスト"}'},
    )
    calendar_out = _make_micro_out(
        "calendar_booker",
        {"slots": [], "booked_count": 0},
    )
    return researcher_out, signal_out, gen_out, calendar_out


class TestOutreachPipeline:
    """アウトリーチパイプラインの基本動作テスト。"""

    @pytest.mark.asyncio
    async def test_pipeline_returns_result(self):
        """パイプラインがOutreachPipelineResultを返すことを確認。"""
        researcher_out = _make_micro_out(
            "company_researcher",
            {"company_name": "テスト建設株式会社", "industry": "construction", "pain_points": ["人手不足"]},
        )
        signal_out = _make_micro_out(
            "signal_detector",
            {"signals": [], "temperature": "cold"},
        )
        gen_out = _make_micro_out(
            "document_generator",
            {"subject": "テスト件名", "body": "テスト本文"},
        )

        with patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_company_researcher",
            new_callable=AsyncMock,
            return_value=researcher_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_signal_detector",
            new_callable=AsyncMock,
            return_value=signal_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=gen_out,
        ):
            result = await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "companies": [
                        {
                            "name": "テスト建設株式会社",
                            "industry": "建設業",
                            "hp_url": "https://test.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )
            assert isinstance(result, OutreachPipelineResult)


class TestManufacturingFilter:
    """製造業特化フィルタのテスト。"""

    def test_default_target_industry_is_manufacturing(self):
        """デフォルトターゲット業種が製造業であることを確認。"""
        assert DEFAULT_TARGET_INDUSTRY == "manufacturing"

    def test_manufacturing_pain_fallback_has_required_categories(self):
        """MANUFACTURING_PAIN_FALLBACK に必須カテゴリが含まれることを確認。"""
        categories = {p["category"] for p in MANUFACTURING_PAIN_FALLBACK}
        assert "見積業務" in categories
        assert "暗黙知共有" in categories
        assert "多品種少量生産" in categories
        assert "品質管理" in categories

    def test_manufacturing_pain_fallback_has_appeal_message(self):
        """各ペインに appeal_message が含まれることを確認。"""
        for pain in MANUFACTURING_PAIN_FALLBACK:
            assert "appeal_message" in pain
            assert pain["appeal_message"]

    def test_manufacturing_subject_templates_cover_all_variants(self):
        """製造業件名テンプレートが A/B/C 全バリアントを網羅することを確認。"""
        for variant in ("A", "B", "C"):
            assert variant in MANUFACTURING_SUBJECT_TEMPLATES
            assert MANUFACTURING_SUBJECT_TEMPLATES[variant]

    @pytest.mark.asyncio
    async def test_manufacturing_filter_excludes_non_manufacturing(self):
        """製造業以外の企業が target_industry=manufacturing 時にフィルタされることを確認。"""
        researcher_out, signal_out, gen_out, calendar_out = _make_patches()

        with patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_company_researcher",
            new_callable=AsyncMock,
            return_value=researcher_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_signal_detector",
            new_callable=AsyncMock,
            return_value=signal_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=gen_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_calendar_booker",
            new_callable=AsyncMock,
            return_value=calendar_out,
        ):
            result = await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "manufacturing",
                    "companies": [
                        {
                            "name": "テスト製造株式会社",
                            "industry": "製造業",
                            "hp_url": "https://mfg.example.com",
                        },
                        {
                            "name": "テスト飲食株式会社",
                            "industry": "飲食業",
                            "hp_url": "https://restaurant.example.com",
                        },
                    ],
                    "dry_run": True,
                },
            )
        # パイプライン自体は成功する
        assert isinstance(result, OutreachPipelineResult)
        # Step 1 の result に製造業フィルタ適用フラグが立つ
        step1 = next(s for s in result.steps if s.step_name == "gbizinfo_enrich")
        assert step1.result.get("manufacturing_filter_applied") is True
        # 製造業のみが残り、飲食業は除外される
        assert step1.result["company_count"] == 1

    @pytest.mark.asyncio
    async def test_manufacturing_target_flag_is_set_on_companies(self):
        """製造業ターゲット企業に _is_manufacturing_target フラグが付くことを確認。"""
        captured_payloads: list[dict] = []

        async def mock_researcher(inp):
            captured_payloads.append(inp.payload)
            return _make_micro_out(
                "company_researcher",
                {
                    "pain_points": MANUFACTURING_PAIN_FALLBACK[:2],
                    "scale": "中規模",
                    "tone": "丁寧",
                    "industry_tasks": [],
                    "industry_appeal": "",
                },
            )

        _, signal_out, gen_out, calendar_out = _make_patches()

        with patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_company_researcher",
            side_effect=mock_researcher,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_signal_detector",
            new_callable=AsyncMock,
            return_value=signal_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=gen_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_calendar_booker",
            new_callable=AsyncMock,
            return_value=calendar_out,
        ):
            await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "manufacturing",
                    "companies": [
                        {
                            "name": "テスト精機株式会社",
                            "industry": "製造業",
                            "hp_url": "https://seiki.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        # company_researcher に pain_focus_categories と pain_fallback が渡されている
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert "pain_focus_categories" in payload
        assert "見積業務" in payload["pain_focus_categories"]
        assert "pain_fallback" in payload
        assert len(payload["pain_fallback"]) > 0

    @pytest.mark.asyncio
    async def test_non_manufacturing_industry_skips_filter(self):
        """target_industry が manufacturing 以外のとき製造業フィルタが適用されないことを確認。"""
        researcher_out, signal_out, gen_out, calendar_out = _make_patches()

        with patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_company_researcher",
            new_callable=AsyncMock,
            return_value=researcher_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_signal_detector",
            new_callable=AsyncMock,
            return_value=signal_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=gen_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_calendar_booker",
            new_callable=AsyncMock,
            return_value=calendar_out,
        ):
            result = await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "construction",  # 建設業指定
                    "companies": [
                        {
                            "name": "テスト建設株式会社",
                            "industry": "建設業",
                            "hp_url": "https://kensetsu.example.com",
                        },
                        {
                            "name": "テスト飲食株式会社",
                            "industry": "飲食業",
                            "hp_url": "https://restaurant.example.com",
                        },
                    ],
                    "dry_run": True,
                },
            )

        step1 = next(s for s in result.steps if s.step_name == "gbizinfo_enrich")
        # 製造業フィルタは適用されない
        assert step1.result.get("manufacturing_filter_applied") is False
        # 全2社がそのまま通る
        assert step1.result["company_count"] == 2


class TestGBizInfoManufacturingSource:
    """source=gbizinfo_manufacturing のテスト。"""

    @pytest.mark.asyncio
    async def test_gbizinfo_manufacturing_source_calls_search_method(self):
        """source=gbizinfo_manufacturing のとき search_manufacturing_companies が呼ばれることを確認。"""
        researcher_out, signal_out, gen_out, calendar_out = _make_patches()

        mock_manufacturing_companies = [
            {
                "name": "株式会社テスト精密",
                "corporate_number": "1234567890123",
                "industry": "製造業",
                "employee_count": 50,
                "prefecture": "愛知県",
            }
        ]

        with patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.GBizInfoConnector"
        ) as MockGBiz, patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_company_researcher",
            new_callable=AsyncMock,
            return_value=researcher_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_signal_detector",
            new_callable=AsyncMock,
            return_value=signal_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=gen_out,
        ), patch(
            "workers.bpo.sales.pipelines.outreach_pipeline.run_calendar_booker",
            new_callable=AsyncMock,
            return_value=calendar_out,
        ):
            mock_instance = AsyncMock()
            mock_instance.search_manufacturing_companies = AsyncMock(
                return_value=mock_manufacturing_companies
            )
            MockGBiz.return_value = mock_instance

            result = await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "gbizinfo_manufacturing",
                    "gbizinfo_api_token": "test-token",
                    "prefecture": "愛知県",
                    "min_employees": 10,
                    "max_employees": 300,
                    "limit": 50,
                    "dry_run": True,
                },
            )

        assert isinstance(result, OutreachPipelineResult)
        mock_instance.search_manufacturing_companies.assert_called_once_with(
            prefecture="愛知県",
            min_employees=10,
            max_employees=300,
            limit=50,
        )
        step1 = next(s for s in result.steps if s.step_name == "gbizinfo_enrich")
        assert step1.success is True
        assert step1.result["company_count"] == 1
