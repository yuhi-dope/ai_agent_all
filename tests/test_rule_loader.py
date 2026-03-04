"""utils/rule_loader の load_rule / load_bpo_rules を検証。"""

import tempfile
from pathlib import Path

from agent.utils.rule_loader import load_rule, load_bpo_rules


def test_load_rule_file_exists():
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        (rules_dir / "spec_rules.md").write_text("custom spec prompt", encoding="utf-8")
        out = load_rule(rules_dir, "spec_rules", "default")
        assert out == "custom spec prompt"


def test_load_rule_file_missing_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        default = "fallback prompt"
        out = load_rule(rules_dir, "spec_rules", default)
        assert out == default


def test_load_rule_empty_file_returns_stripped():
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        (rules_dir / "foo.md").write_text("  \n  ", encoding="utf-8")
        out = load_rule(rules_dir, "foo", "default")
        assert out == ""


def test_load_bpo_rules_full_layers():
    """4層すべてが存在する場合に合成される。"""
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        (rules_dir / "general_rules.md").write_text("general", encoding="utf-8")
        platform = rules_dir / "platform"
        platform.mkdir()
        (platform / "kintone_rules.md").write_text("kintone", encoding="utf-8")
        genre = rules_dir / "genre"
        genre.mkdir()
        (genre / "営業_rules.md").write_text("sales", encoding="utf-8")
        learned = rules_dir / "learned"
        learned.mkdir()
        (learned / "kintone_営業_learned.md").write_text("learned", encoding="utf-8")

        result = load_bpo_rules(rules_dir, "kintone", "sfa")
        assert "general" in result
        assert "kintone" in result
        assert "sales" in result
        assert "learned" in result
        assert result.count("---") == 3


def test_load_bpo_rules_missing_layers():
    """存在しないレイヤーはスキップされる。"""
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        (rules_dir / "general_rules.md").write_text("general", encoding="utf-8")

        result = load_bpo_rules(rules_dir, "kintone", "sfa")
        assert result == "general"
        assert "---" not in result


def test_load_bpo_rules_no_genre():
    """genre が空のとき genre/learned レイヤーはスキップ。"""
    with tempfile.TemporaryDirectory() as tmp:
        rules_dir = Path(tmp)
        (rules_dir / "general_rules.md").write_text("general", encoding="utf-8")
        platform = rules_dir / "platform"
        platform.mkdir()
        (platform / "kintone_rules.md").write_text("kintone", encoding="utf-8")

        result = load_bpo_rules(rules_dir, "kintone", "")
        assert "general" in result
        assert "kintone" in result
        assert result.count("---") == 1
