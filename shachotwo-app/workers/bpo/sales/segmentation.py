"""製造業ターゲット企業のセグメント分類ロジック"""
from dataclasses import dataclass, field
from typing import Optional

# 売上規模セグメント（円）
REVENUE_SEGMENTS: dict[str, tuple[int, Optional[int]]] = {
    "micro":      (0,              100_000_000),      # 〜1億
    "small":      (100_000_000,    1_000_000_000),    # 1億〜10億
    "mid":        (1_000_000_000,  10_000_000_000),   # 10億〜100億
    "large":      (10_000_000_000, 50_000_000_000),   # 100億〜500億
    "enterprise": (50_000_000_000, None),              # 500億〜
}

# 利益セグメント（ターゲット: 5億〜500億）（円）
PROFIT_SEGMENTS: dict[str, tuple[int, Optional[int]]] = {
    "below_target":  (0,               500_000_000),    # 5億未満
    "target_core":   (500_000_000,     10_000_000_000), # 5億〜100億（コアターゲット）
    "target_upper":  (10_000_000_000,  50_000_000_000), # 100億〜500億（アッパーターゲット）
    "out_of_range":  (50_000_000_000,  None),           # 500億超（対象外）
}

# 従業員規模セグメント
# 注意: _classify_range は lo <= value < hi で判定するため上限は「含まない値+1」を指定する
EMPLOYEE_SEGMENTS: dict[str, tuple[int, Optional[int]]] = {
    "startup":    (1,    11),    # 1〜10名
    "small":      (11,   51),    # 11〜50名
    "mid":        (51,   201),   # 51〜200名
    "large":      (201,  1001),  # 201〜1000名
    "enterprise": (1001, None),  # 1001名〜
}

# 製造業サブ業種分類（キーワードで判定）
# ※ 金属加工・樹脂成形がコアターゲット。キーワードを厚めに設定。
MANUFACTURING_SUB_INDUSTRIES: dict[str, list[str]] = {
    "金属加工": [
        "金属", "鍛造", "鋳造", "プレス", "切削", "板金", "溶接", "研磨",
        "めっき", "メッキ", "熱処理", "焼入れ", "ダイカスト", "旋盤", "フライス",
        "マシニング", "ワイヤーカット", "放電加工", "表面処理", "アルミ", "ステンレス",
        "鋼材", "鉄鋼", "銅合金", "チタン", "超硬", "NC加工",
    ],
    "樹脂加工": [
        "樹脂", "プラスチック", "樹脂成形", "射出成形", "ブロー成形", "押出成形",
        "真空成形", "ゴム成形", "エラストマー", "シリコーン", "ナイロン", "POM",
        "ABS", "ポリカーボネート", "PEEK", "金型", "モールド",
    ],
    "機械製造":   ["機械", "工作機械", "産業機械", "精密機器", "ロボット", "装置", "治具"],
    "電子部品":   ["電子", "半導体", "基板", "センサー", "コネクタ", "プリント基板", "実装"],
    "食品製造":   ["食品", "飲料", "菓子", "水産加工", "畜産加工", "冷凍食品", "調味料"],
    "化学製品":   ["化学", "塗料", "接着剤", "化粧品原料", "インク", "溶剤", "触媒"],
    "自動車部品": ["自動車", "車両部品", "エンジン部品", "トランスミッション", "ブレーキ"],
    "その他製造": [],  # フォールバック
}


@dataclass
class CompanySegment:
    revenue_segment: str
    profit_segment: str
    employee_segment: str
    sub_industry: str
    priority_tier: str   # S / A / B / C
    is_target: bool
    reasons: list[str] = field(default_factory=list)


def _classify_range(
    value: int,
    segments: dict[str, tuple[int, Optional[int]]],
    fallback: str,
) -> str:
    """値を範囲辞書でセグメント名に変換する。"""
    for name, (lo, hi) in segments.items():
        if hi is None:
            if value >= lo:
                return name
        else:
            if lo <= value < hi:
                return name
    return fallback


def classify_revenue_segment(annual_revenue: Optional[int]) -> str:
    """売上高から規模セグメントを返す。情報なしは 'unknown'。"""
    if annual_revenue is None:
        return "unknown"
    return _classify_range(annual_revenue, REVENUE_SEGMENTS, "unknown")


def classify_profit_segment(operating_profit: Optional[int]) -> str:
    """営業利益からターゲット区分を返す。情報なしは 'unknown'。"""
    if operating_profit is None:
        return "unknown"
    return _classify_range(operating_profit, PROFIT_SEGMENTS, "unknown")


def classify_employee_segment(employee_count: Optional[int]) -> str:
    """従業員数から規模セグメントを返す。情報なしは 'unknown'。"""
    if employee_count is None:
        return "unknown"
    return _classify_range(employee_count, EMPLOYEE_SEGMENTS, "unknown")


def detect_sub_industry(industry_text: str) -> str:
    """業種テキストからサブ業種を判定する。

    MANUFACTURING_SUB_INDUSTRIES のキーワードと前方一致で照合し、
    最初にマッチしたカテゴリを返す。どれにもマッチしない場合は 'その他製造'。
    """
    for category, keywords in MANUFACTURING_SUB_INDUSTRIES.items():
        if category == "その他製造":
            continue
        for kw in keywords:
            if kw in industry_text:
                return category
    return "その他製造"


def classify_company(
    annual_revenue: Optional[int] = None,
    operating_profit: Optional[int] = None,
    employee_count: Optional[int] = None,
    industry_text: str = "",
    capital_stock: Optional[int] = None,
) -> CompanySegment:
    """企業をセグメント分類し、優先度を決定する。

    優先度決定ロジック:
    - S: 利益5億〜100億 + 従業員50〜300名 + 製造業確定（最適ターゲット）
    - A: 利益5億〜500億 + 従業員10〜1000名 + 製造業確定
    - B: 売上10億〜500億 + 製造業確定（利益情報なし）
    - C: 製造業だが情報不足、またはターゲット外
    """
    rev_seg = classify_revenue_segment(annual_revenue)
    prof_seg = classify_profit_segment(operating_profit)
    emp_seg = classify_employee_segment(employee_count)
    sub_ind = detect_sub_industry(industry_text)

    reasons: list[str] = []

    # --- 優先度 S ---
    if (
        prof_seg == "target_core"
        and emp_seg in ("mid", "large")
        and employee_count is not None
        and 50 <= employee_count <= 300
    ):
        tier = "S"
        is_target = True
        reasons.append("利益5億〜100億（コアターゲット）")
        reasons.append(f"従業員{employee_count}名（最適規模50〜300名）")
        return CompanySegment(
            revenue_segment=rev_seg,
            profit_segment=prof_seg,
            employee_segment=emp_seg,
            sub_industry=sub_ind,
            priority_tier=tier,
            is_target=is_target,
            reasons=reasons,
        )

    # --- 優先度 A ---
    if (
        prof_seg in ("target_core", "target_upper")
        and emp_seg in ("small", "mid", "large")
        and employee_count is not None
        and 10 <= employee_count <= 1000
    ):
        tier = "A"
        is_target = True
        reasons.append(
            "利益5億〜500億（ターゲット範囲）"
            if prof_seg == "target_core"
            else "利益100億〜500億（アッパーターゲット）"
        )
        reasons.append(f"従業員{employee_count}名")
        return CompanySegment(
            revenue_segment=rev_seg,
            profit_segment=prof_seg,
            employee_segment=emp_seg,
            sub_industry=sub_ind,
            priority_tier=tier,
            is_target=is_target,
            reasons=reasons,
        )

    # --- 優先度 B: 利益情報なし・売上で代替判定 ---
    if rev_seg in ("mid", "large") and annual_revenue is not None:
        tier = "B"
        is_target = True
        reasons.append("売上10億〜500億（利益情報なし・売上代替判定）")
        if sub_ind != "その他製造":
            reasons.append(f"サブ業種: {sub_ind}")
        return CompanySegment(
            revenue_segment=rev_seg,
            profit_segment=prof_seg,
            employee_segment=emp_seg,
            sub_industry=sub_ind,
            priority_tier=tier,
            is_target=is_target,
            reasons=reasons,
        )

    # --- 資本金代替判定（売上・利益どちらも不明の場合）---
    if (
        capital_stock is not None
        and annual_revenue is None
        and operating_profit is None
        and capital_stock >= 10_000_000  # 資本金1000万以上
        and emp_seg in ("small", "mid", "large")
    ):
        tier = "B"
        is_target = True
        reasons.append(f"資本金{capital_stock // 10_000}万円（売上・利益情報なし）")
        return CompanySegment(
            revenue_segment=rev_seg,
            profit_segment=prof_seg,
            employee_segment=emp_seg,
            sub_industry=sub_ind,
            priority_tier=tier,
            is_target=is_target,
            reasons=reasons,
        )

    # --- 優先度 C: それ以外 ---
    tier = "C"
    is_target = False

    if prof_seg == "out_of_range":
        reasons.append("利益500億超（対象外・大企業）")
    elif prof_seg == "below_target":
        reasons.append("利益5億未満（対象外・小規模）")
    elif rev_seg == "micro":
        reasons.append("売上1億未満（対象外・小規模）")
    elif rev_seg == "enterprise":
        reasons.append("売上500億超（対象外・大企業）")
    else:
        reasons.append("情報不足によりランク付け不可")

    return CompanySegment(
        revenue_segment=rev_seg,
        profit_segment=prof_seg,
        employee_segment=emp_seg,
        sub_industry=sub_ind,
        priority_tier=tier,
        is_target=is_target,
        reasons=reasons,
    )
