"""utils/rule_loader の load_rule を検証。"""

import tempfile
from pathlib import Path

from develop_agent.utils.rule_loader import load_rule


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
