"""後方互換用シム: agent.state から re-export。"""

from agent.state import (  # noqa: F401
    AgentState,
    initial_state,
    BPOState,
    initial_bpo_state,
)
