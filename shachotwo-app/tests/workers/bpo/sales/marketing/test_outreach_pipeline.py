"""
マーケ パイプライン⓰ アウトリーチ テスト（marketing/ 配下）

A/Bテストバリアント自動割り当てロジックを含む全機能をカバーする。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.sales.marketing.outreach_pipeline import (
    OutreachPipelineResult,
    _get_ab_variant,
    run_outreach_pipeline,
)
from workers.micro.models import MicroAgentOutput

COMPANY_ID = "test-company-mkt-001"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _make_micro_out(agent_name: str, data: dict, cost_yen: float = 0.5) -> MicroAgentOutput:
    return MicroAgentOutput(
        agent_name=agent_name,
        success=True,
        result=data,
        confidence=0.9,
        cost_yen=cost_yen,
        duration_ms=100,
    )


def _researcher_out() -> MicroAgentOutput:
    return _make_micro_out(
        "company_researcher",
        {
            "pain_points": [{"detail": "人手不足", "appeal_message": "人手不足の課題を解消"}],
            "scale": "中規模",
            "tone": "丁寧",
            "industry_tasks": ["見積作成"],
            "industry_appeal": "建設業向け自動化",
        },
    )


def _lp_gen_out() -> MicroAgentOutput:
    return _make_micro_out("document_generator", {"content": "LP本文テキスト"})


def _compose_gen_out(variant: str = "A") -> MicroAgentOutput:
    """outreach_composer が返す MicroAgentOutput（JSON形式）。"""
    content = json.dumps(
        {"subject": f"【建設業向け】バリアント{variant}の件名", "body": f"バリアント{variant}の本文"},
        ensure_ascii=False,
    )
    return _make_micro_out("document_generator", {"content": content})


def _signal_out() -> MicroAgentOutput:
    return _make_micro_out(
        "signal_detector",
        {
            "classifications": [],
            "followup_actions": [],
            "summary": {"hot": 0, "warm": 0, "cold": 1, "total": 1},
        },
    )


def _calendar_out() -> MicroAgentOutput:
    return _make_micro_out("calendar_booker", {"slots": [], "booked_count": 0})


# ---------------------------------------------------------------------------
# _get_ab_variant のユニットテスト
# ---------------------------------------------------------------------------

class TestGetAbVariant:
    """A/Bバリアント自動割り当て関数のテスト。"""

    @pytest.mark.asyncio
    async def test_returns_a_or_b_when_no_db_data(self):
        """DBデータなし → "A" または "B" を返す。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .not_.is_.return_value.order.return_value.limit.return_value.execute.return_value \
            = MagicMock(data=[])

        # get_service_client は関数内部でローカルインポートされるため db.supabase を patch する
        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _get_ab_variant(COMPANY_ID, "建設業")

        assert result in ("A", "B")

    @pytest.mark.asyncio
    async def test_selects_min_sent_variant(self):
        """DBにバリアントが2種以上ある場合、送信数が少ない方を選択する。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .not_.is_.return_value.order.return_value.limit.return_value.execute.return_value \
            = MagicMock(data=[
                {"email_variant": "A", "sent_count": 100},
                {"email_variant": "B", "sent_count": 50},
            ])

        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _get_ab_variant(COMPANY_ID, "建設業")

        # B の方が送信数少ない
        assert result == "B"

    @pytest.mark.asyncio
    async def test_returns_default_on_exception(self):
        """DB接続失敗時も例外を上げず "A" or "B" を返す。"""
        with patch("db.supabase.get_service_client", side_effect=Exception("DB接続失敗")):
            result = await _get_ab_variant(COMPANY_ID, "建設業")

        assert result in ("A", "B")

    @pytest.mark.asyncio
    async def test_single_variant_in_db_returns_ab_random(self):
        """DBに1種しかないとき（均等配分不要）→ A/B ランダムにフォールバック。"""
        mock_db = MagicMock()
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .not_.is_.return_value.order.return_value.limit.return_value.execute.return_value \
            = MagicMock(data=[
                {"email_variant": "A", "sent_count": 50},
            ])

        with patch("db.supabase.get_service_client", return_value=mock_db):
            result = await _get_ab_variant(COMPANY_ID, "建設業")

        assert result in ("A", "B")


# ---------------------------------------------------------------------------
# パイプライン統合テスト
# ---------------------------------------------------------------------------

BASE_PATCHES = {
    "run_company_researcher": "workers.bpo.sales.marketing.outreach_pipeline.run_company_researcher",
    "run_document_generator": "workers.bpo.sales.marketing.outreach_pipeline.run_document_generator",
    "run_signal_detector": "workers.bpo.sales.marketing.outreach_pipeline.run_signal_detector",
    "run_calendar_booker": "workers.bpo.sales.marketing.outreach_pipeline.run_calendar_booker",
    "get_ab_variant": "workers.bpo.sales.marketing.outreach_pipeline._get_ab_variant",
    "playwright_form": "workers.bpo.sales.marketing.outreach_pipeline.PlaywrightFormConnector",
    "connector_config": "workers.bpo.sales.marketing.outreach_pipeline.ConnectorConfig",
}


class TestOutreachPipelineAbVariant:
    """A/Bテストバリアントがパイプライン全体に正しく流れることを確認。"""

    def _all_patches(self, gen_side_effect, ab_variant="A"):
        """テスト用のパッチコンテキストマネージャー一覧を返すヘルパー。"""
        mock_form = MagicMock()
        mock_form.return_value = MagicMock()
        mock_config = MagicMock()
        mock_config.return_value = MagicMock()
        return [
            patch(BASE_PATCHES["run_company_researcher"], new_callable=AsyncMock, return_value=_researcher_out()),
            patch(BASE_PATCHES["run_document_generator"], new_callable=AsyncMock, side_effect=gen_side_effect),
            patch(BASE_PATCHES["run_signal_detector"], new_callable=AsyncMock, return_value=_signal_out()),
            patch(BASE_PATCHES["run_calendar_booker"], new_callable=AsyncMock, return_value=_calendar_out()),
            patch(BASE_PATCHES["get_ab_variant"], new_callable=AsyncMock, return_value=ab_variant),
            patch(BASE_PATCHES["playwright_form"], mock_form),
            patch(BASE_PATCHES["connector_config"], mock_config),
        ]

    async def _run_pipeline_with_all_patches(self, gen_side_effect, ab_variant, input_data):
        """全パッチ適用済みパイプライン実行ヘルパー。"""
        patches = self._all_patches(gen_side_effect, ab_variant)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            return await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data=input_data,
            )

    @pytest.mark.asyncio
    async def test_pipeline_assigns_variant_a(self):
        """バリアントAが割り当てられた場合、leads_payloadにemail_variant='A'が含まれる。"""
        def _gen_side_effect(inp):
            return _compose_gen_out("A") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        result = await self._run_pipeline_with_all_patches(
            _gen_side_effect,
            "A",
            {
                "source": "direct",
                "target_industry": "construction",
                "companies": [
                    {
                        "name": "テスト建設株式会社",
                        "industry": "建設業",
                        "hp_url": "https://test.example.com",
                        "contact_email": "info@test.example.com",
                    }
                ],
                "dry_run": True,
            },
        )

        assert result.success
        leads = result.final_output.get("leads_payload", [])
        assert len(leads) == 1
        assert leads[0]["email_variant"] == "A"

    @pytest.mark.asyncio
    async def test_pipeline_assigns_variant_b(self):
        """バリアントBが割り当てられた場合、leads_payloadにemail_variant='B'が含まれる。"""
        def _gen_side_effect(inp):
            return _compose_gen_out("B") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        result = await self._run_pipeline_with_all_patches(
            _gen_side_effect,
            "B",
            {
                "source": "direct",
                "companies": [
                    {
                        "name": "テスト製造株式会社",
                        "industry": "製造業",
                        "hp_url": "https://mfg.example.com",
                        "contact_email": "info@mfg.example.com",
                    }
                ],
                "dry_run": True,
            },
        )

        assert result.success
        leads = result.final_output.get("leads_payload", [])
        assert leads[0]["email_variant"] == "B"

    @pytest.mark.asyncio
    async def test_company_level_email_variant_overrides_auto_assign(self):
        """company側に email_variant が指定されていれば、自動割り当てを上書きする。"""
        def _gen_side_effect(inp):
            return _compose_gen_out("C") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        mock_form = MagicMock()
        mock_form.return_value = MagicMock()
        mock_config = MagicMock()
        mock_config.return_value = MagicMock()

        with patch(BASE_PATCHES["run_company_researcher"], new_callable=AsyncMock, return_value=_researcher_out()), \
             patch(BASE_PATCHES["run_document_generator"], new_callable=AsyncMock, side_effect=_gen_side_effect), \
             patch(BASE_PATCHES["run_signal_detector"], new_callable=AsyncMock, return_value=_signal_out()), \
             patch(BASE_PATCHES["run_calendar_booker"], new_callable=AsyncMock, return_value=_calendar_out()), \
             patch(BASE_PATCHES["get_ab_variant"], new_callable=AsyncMock, return_value="A") as mock_assign, \
             patch(BASE_PATCHES["playwright_form"], mock_form), \
             patch(BASE_PATCHES["connector_config"], mock_config):

            result = await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "realestate",
                    "companies": [
                        {
                            "name": "テスト不動産株式会社",
                            "industry": "不動産業",
                            "hp_url": "https://re.example.com",
                            "contact_email": "info@re.example.com",
                            "email_variant": "C",  # 明示指定
                        }
                    ],
                    "dry_run": True,
                },
            )

        # _get_ab_variant は呼ばれない（company側で指定済みなので）
        mock_assign.assert_not_called()
        leads = result.final_output.get("leads_payload", [])
        assert leads[0]["email_variant"] == "C"

    @pytest.mark.asyncio
    async def test_pipeline_returns_result_basic(self):
        """パイプラインが OutreachPipelineResult を返す基本動作テスト。"""
        def _gen_side_effect(inp):
            return _compose_gen_out("A") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        result = await self._run_pipeline_with_all_patches(
            _gen_side_effect,
            "A",
            {
                "source": "direct",
                "target_industry": "construction",
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
        assert result.success
        assert len(result.steps) == 8

    @pytest.mark.asyncio
    async def test_pipeline_empty_companies_fails(self):
        """企業リストが空の場合、パイプラインは失敗を返す。"""
        result = await run_outreach_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "source": "direct",
                "companies": [],
                "dry_run": True,
            },
        )

        assert not result.success
        assert result.failed_step == "gbizinfo_enrich"

    def _instruction_patches(self, capture_generator, ab_variant):
        """variant_instruction 検証テスト用パッチ一覧。"""
        mock_form = MagicMock()
        mock_form.return_value = MagicMock()
        mock_config = MagicMock()
        mock_config.return_value = MagicMock()
        return [
            patch(BASE_PATCHES["run_company_researcher"], new_callable=AsyncMock, return_value=_researcher_out()),
            patch(BASE_PATCHES["run_document_generator"], side_effect=capture_generator),
            patch(BASE_PATCHES["run_signal_detector"], new_callable=AsyncMock, return_value=_signal_out()),
            patch(BASE_PATCHES["run_calendar_booker"], new_callable=AsyncMock, return_value=_calendar_out()),
            patch(BASE_PATCHES["get_ab_variant"], new_callable=AsyncMock, return_value=ab_variant),
            patch(BASE_PATCHES["playwright_form"], mock_form),
            patch(BASE_PATCHES["connector_config"], mock_config),
        ]

    @pytest.mark.asyncio
    async def test_variant_b_instruction_in_payload(self):
        """バリアントBのとき、variant_instructionが含まれたpayloadでgeneratorが呼ばれる。"""
        captured_payloads: list[dict] = []

        async def _capture_generator(inp):
            captured_payloads.append(inp.payload)
            return _compose_gen_out("B") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        patches = self._instruction_patches(_capture_generator, "B")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "logistics",
                    "companies": [
                        {
                            "name": "テスト物流株式会社",
                            "industry": "物流業",
                            "hp_url": "https://logistics.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        # outreach_composerへの呼び出しを特定
        composer_payload = next(
            (p for p in captured_payloads if p.get("template_name") == "outreach_email"),
            None,
        )
        assert composer_payload is not None
        variant_instr = composer_payload["data"].get("variant_instruction", "")
        assert "導入事例" in variant_instr or "数値実績" in variant_instr

    @pytest.mark.asyncio
    async def test_variant_c_instruction_in_payload(self):
        """バリアントCのとき、variant_instructionにペイン重視の指示が含まれる。"""
        captured_payloads: list[dict] = []

        async def _capture_generator(inp):
            captured_payloads.append(inp.payload)
            return _compose_gen_out("C") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        patches = self._instruction_patches(_capture_generator, "C")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "wholesale",
                    "companies": [
                        {
                            "name": "テスト卸売株式会社",
                            "industry": "卸売業",
                            "hp_url": "https://wholesale.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        composer_payload = next(
            (p for p in captured_payloads if p.get("template_name") == "outreach_email"),
            None,
        )
        assert composer_payload is not None
        variant_instr = composer_payload["data"].get("variant_instruction", "")
        assert "課題" in variant_instr or "ペイン" in variant_instr

    @pytest.mark.asyncio
    async def test_variant_a_empty_instruction(self):
        """バリアントAのとき、variant_instructionが空文字列。"""
        captured_payloads: list[dict] = []

        async def _capture_generator(inp):
            captured_payloads.append(inp.payload)
            return _compose_gen_out("A") if inp.agent_name == "outreach_composer" else _lp_gen_out()

        patches = self._instruction_patches(_capture_generator, "A")
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await run_outreach_pipeline(
                company_id=COMPANY_ID,
                input_data={
                    "source": "direct",
                    "target_industry": "healthcare",
                    "companies": [
                        {
                            "name": "テスト医療株式会社",
                            "industry": "医療業",
                            "hp_url": "https://medical.example.com",
                        }
                    ],
                    "dry_run": True,
                },
            )

        composer_payload = next(
            (p for p in captured_payloads if p.get("template_name") == "outreach_email"),
            None,
        )
        assert composer_payload is not None
        assert composer_payload["data"].get("variant_instruction", "") == ""
