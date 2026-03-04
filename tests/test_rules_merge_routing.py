"""rules_merge.apply_approved_change のルーティングを検証。"""

import tempfile
from pathlib import Path

from server.rules_merge import apply_approved_change


def test_saas_learned_routes_to_learned_dir():
    """saas_learned_* は rules/saas/learned/ に書き出される。"""
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        rules_dir = ws / "rules" / "develop"
        rules_dir.mkdir(parents=True)

        change = {
            "rule_name": "saas_learned_kintone_営業",
            "added_block": "## 学習ルール\n- テスト",
            "run_id": "test_run_1",
            "genre": "sfa",
        }
        result = apply_approved_change(ws, "rules/develop", change)
        assert result is True

        learned_file = ws / "rules" / "saas" / "learned" / "kintone_営業_learned.md"
        assert learned_file.exists()
        content = learned_file.read_text(encoding="utf-8")
        assert "学習ルール" in content


def test_saas_routes_to_platform_dir():
    """saas_* は rules/saas/platform/ に書き出される。"""
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        rules_dir = ws / "rules" / "develop"
        rules_dir.mkdir(parents=True)

        change = {
            "rule_name": "saas_kintone",
            "added_block": "## kintone ルール\n- テスト",
            "run_id": "test_run_2",
            "genre": "",
        }
        result = apply_approved_change(ws, "rules/develop", change)
        assert result is True

        platform_file = ws / "rules" / "saas" / "platform" / "kintone_rules.md"
        assert platform_file.exists()
        content = platform_file.read_text(encoding="utf-8")
        assert "kintone ルール" in content


def test_non_saas_routes_to_rules_dir():
    """saas_ プレフィックスなしは rules_dir 内に書き出される。"""
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        rules_dir = ws / "rules" / "develop"
        rules_dir.mkdir(parents=True)

        change = {
            "rule_name": "spec_rules",
            "added_block": "## Spec rule\n- テスト",
            "run_id": "test_run_3",
            "genre": "",
        }
        result = apply_approved_change(ws, "rules/develop", change)
        assert result is True

        rule_file = rules_dir / "spec_rules.md"
        assert rule_file.exists()
