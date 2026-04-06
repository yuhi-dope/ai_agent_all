"""brain/visualization — デジタルツイン可視化モジュール。

フロントエンドに渡すためのデータ生成を担当する。
レンダリング自体はフロントエンド側が行う。

提供する機能:
- process flow: Mermaid フローチャート文字列生成
- completeness map: 5次元レーダーチャートデータ生成
- decision tree: 意思決定ツリーデータ生成
"""
from brain.visualization.flow_generator import (
    generate_process_flow,
    generate_process_flow_from_knowledge,
    render_process_flow_mermaid,
)
from brain.visualization.completeness_map import generate_completeness_radar
from brain.visualization.decision_tree import (
    generate_decision_tree,
    render_decision_tree_mermaid,
    render_decision_tree_svg,
)

__all__ = [
    "generate_process_flow",
    "generate_process_flow_from_knowledge",
    "render_process_flow_mermaid",
    "generate_completeness_radar",
    "generate_decision_tree",
    "render_decision_tree_mermaid",
    "render_decision_tree_svg",
]
