"""後方互換用シム: agent.utils.rule_loader から re-export。"""

from agent.utils.rule_loader import (  # noqa: F401
    VALID_GENRES,
    load_rule,
    load_genre_rules,
    load_genre_db_schema,
)
