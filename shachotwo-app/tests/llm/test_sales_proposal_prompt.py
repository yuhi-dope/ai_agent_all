"""llm/prompts/sales_proposal.py のユニットテスト。"""
import pytest

from llm.prompts.sales_proposal import (
    SYSTEM_PROPOSAL,
    build_proposal_system_prompt,
)


class TestBuildProposalSystemPrompt:
    def test_none_returns_default_prices(self):
        """`prices=None` の場合はデフォルト料金（30,000 / 250,000 / 100,000）が含まれる。"""
        prompt = build_proposal_system_prompt(None)
        assert "30,000" in prompt
        assert "250,000" in prompt
        assert "100,000" in prompt

    def test_custom_prices_embedded(self):
        """カスタム料金を渡すとプロンプトに反映される。"""
        prices = {"brain": 25_000, "bpo_core": 200_000, "additional": 80_000}
        prompt = build_proposal_system_prompt(prices)
        assert "25,000" in prompt
        assert "200,000" in prompt
        assert "80,000" in prompt

    def test_missing_keys_use_defaults(self):
        """`prices` に一部キーがなくてもクラッシュしない。"""
        prices = {"brain": 28_000}  # bpo_core / additional が欠けている
        prompt = build_proposal_system_prompt(prices)
        assert "28,000" in prompt
        assert "250,000" in prompt  # bpo_coreのデフォルト
        assert "100,000" in prompt  # additionalのデフォルト

    def test_returns_string(self):
        """戻り値は文字列である。"""
        assert isinstance(build_proposal_system_prompt(), str)
        assert isinstance(build_proposal_system_prompt({}), str)

    def test_system_proposal_constant_matches_default(self):
        """後方互換SYSTEM_PROPOSAL定数はbuild_proposal_system_prompt()と同値。"""
        assert SYSTEM_PROPOSAL == build_proposal_system_prompt(None)

    def test_prompt_contains_required_sections(self):
        """シャチョツーのサービス説明・料金体系・出力形式が含まれる。"""
        prompt = build_proposal_system_prompt()
        assert "シャチョツー" in prompt
        assert "料金体系" in prompt
        assert "JSON" in prompt
        assert "ROI" in prompt or "roi_estimate" in prompt
