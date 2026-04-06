"""デジタルツインの5次元充足度をレーダーチャートデータとして生成する。"""
from brain.twin.models import TwinSnapshot

_DIMENSION_LABELS = ["ヒト", "プロセス", "コスト", "ツール", "リスク"]

# 各次元に対応する充足度の推奨メッセージテンプレート
# (label, threshold, message)
_RECOMMENDATION_TEMPLATES: list[tuple[str, float, str]] = [
    (
        "ヒト",
        0.4,
        "人材・組織情報の登録が少ない（{pct}%）。"
        "キーパーソン・役割・スキルギャップを追加してください。",
    ),
    (
        "プロセス",
        0.4,
        "業務フロー・意思決定ルールの登録が少ない（{pct}%）。"
        "主要な業務フローと判断基準を追加してください。",
    ),
    (
        "コスト",
        0.4,
        "コスト情報の登録が少ない（{pct}%）。"
        "月次固定費・変動費を追加してください。",
    ),
    (
        "ツール",
        0.4,
        "SaaS・ツール情報の登録が少ない（{pct}%）。"
        "利用中のシステム・ツールを追加してください。",
    ),
    (
        "リスク",
        0.4,
        "リスク情報の登録が少ない（{pct}%）。"
        "既知のリスク・コンプライアンス上の懸念を追加してください。",
    ),
]


def generate_completeness_radar(snapshot: TwinSnapshot) -> dict:
    """5次元の completeness からレーダーチャートデータを返す。

    Args:
        snapshot: デジタルツインのスナップショット

    Returns:
        {
            "labels": ["ヒト", "プロセス", "コスト", "ツール", "リスク"],
            "values": [0.8, 0.6, 0.3, 0.5, 0.7],
            "overall": 0.58,
            "recommendations": [
                {
                    "dimension": "コスト",
                    "message": "コスト情報の登録が少ない（30%）。..."
                }
            ]
        }
    """
    values = [
        round(snapshot.people.completeness, 4),
        round(snapshot.process.completeness, 4),
        round(snapshot.cost.completeness, 4),
        round(snapshot.tool.completeness, 4),
        round(snapshot.risk.completeness, 4),
    ]

    # overall は snapshot の値を使う（再計算は analyzer 側の責務）
    overall = round(snapshot.overall_completeness, 4)

    # overall が 0 かつ values に値がある場合は平均を算出
    if overall == 0.0 and any(v > 0 for v in values):
        overall = round(sum(values) / len(values), 4)

    # レコメンデーション生成：閾値を下回る次元に対して生成
    recommendations: list[dict[str, str]] = []
    for (label, threshold, template), value in zip(_RECOMMENDATION_TEMPLATES, values):
        if value < threshold:
            pct = int(value * 100)
            recommendations.append({
                "dimension": label,
                "message": template.format(pct=pct),
            })

    return {
        "labels": _DIMENSION_LABELS,
        "values": values,
        "overall": overall,
        "recommendations": recommendations,
    }
