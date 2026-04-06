"""能動提案（proactive）。

analyzer は google genai 等に依存するため遅延インポートする。
contradiction / freshness / resolution は直接エクスポート。
"""

from importlib import import_module

from brain.proactive.models import Proposal, ProactiveAnalysisResult
from brain.proactive.contradiction import detect_contradictions
from brain.proactive.freshness import detect_stale_knowledge
from brain.proactive.resolution import (
    review_proposal,
    accept_proposal,
    reject_proposal,
    get_pending_proposals,
)

__all__ = [
    "analyze_and_propose",
    "Proposal",
    "ProactiveAnalysisResult",
    "detect_contradictions",
    "detect_stale_knowledge",
    "review_proposal",
    "accept_proposal",
    "reject_proposal",
    "get_pending_proposals",
]


def __getattr__(name: str):
    if name == "analyze_and_propose":
        return import_module("brain.proactive.analyzer").analyze_and_propose
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
