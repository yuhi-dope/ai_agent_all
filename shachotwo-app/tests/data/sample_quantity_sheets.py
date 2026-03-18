"""
積算AI全レベル検証用 — リアルなサンプル数量計算書データ

公共工事の実態に基づいた3パターンのテストデータ。
各パターンで全レベル（数量抽出→単価推定→諸経費→内訳書）を検証する。
"""

# ─────────────────────────────────────
# パターン1: 道路改良工事（小規模・公共土木）
# 直接工事費 約1,500万円規模
# ─────────────────────────────────────

SAMPLE_1_TEXT = """
工事名: 市道○○線道路改良工事
工事種別: 公共土木
地域: 東京都
年度: 2026

【数量計算書】

工種: 土工
  種別: 掘削工
    細別: バックホウ掘削（0.8m3級）
    数量: 1,500 m3

  種別: 埋戻工
    細別: 埋戻し
    数量: 800 m3

  種別: 残土処理工
    細別: 残土運搬（DID10km以内）
    数量: 700 m3

工種: コンクリート工
  種別: 型枠工
    細別: 普通型枠
    数量: 250 m2

  種別: 鉄筋工
    細別: 鉄筋加工組立（SD345 D16）
    数量: 15,000 kg

  種別: コンクリート工
    細別: 生コンクリート（21-8-40 N/mm2）
    数量: 300 m3

工種: 舗装工
  種別: 下層路盤工
    細別: 砕石（RC-40）
    数量: 200 m3

  種別: 上層路盤工
    細別: 粒調砕石（M-30）
    数量: 150 m3

  種別: アスファルト舗装工
    細別: 表層（密粒度(20) t=5cm）
    数量: 2,000 m2

工種: 排水構造物工
  種別: 側溝工
    細別: プレキャスト側溝（300×300）
    数量: 500 m
"""

SAMPLE_1_EXPECTED_ITEMS = [
    {"category": "土工", "subcategory": "掘削工", "detail": "バックホウ掘削（0.8m3級）", "quantity": 1500, "unit": "m3"},
    {"category": "土工", "subcategory": "埋戻工", "detail": "埋戻し", "quantity": 800, "unit": "m3"},
    {"category": "土工", "subcategory": "残土処理工", "detail": "残土運搬（DID10km以内）", "quantity": 700, "unit": "m3"},
    {"category": "コンクリート工", "subcategory": "型枠工", "detail": "普通型枠", "quantity": 250, "unit": "m2"},
    {"category": "コンクリート工", "subcategory": "鉄筋工", "detail": "鉄筋加工組立（SD345 D16）", "quantity": 15000, "unit": "kg"},
    {"category": "コンクリート工", "subcategory": "コンクリート工", "detail": "生コンクリート（21-8-40 N/mm2）", "quantity": 300, "unit": "m3"},
    {"category": "舗装工", "subcategory": "下層路盤工", "detail": "砕石（RC-40）", "quantity": 200, "unit": "m3"},
    {"category": "舗装工", "subcategory": "上層路盤工", "detail": "粒調砕石（M-30）", "quantity": 150, "unit": "m3"},
    {"category": "舗装工", "subcategory": "アスファルト舗装工", "detail": "表層（密粒度(20) t=5cm）", "quantity": 2000, "unit": "m2"},
    {"category": "排水構造物工", "subcategory": "側溝工", "detail": "プレキャスト側溝（300×300）", "quantity": 500, "unit": "m"},
]

SAMPLE_1_META = {
    "name": "市道○○線道路改良工事",
    "project_type": "public_civil",
    "region": "東京都",
    "fiscal_year": 2026,
}

# ─────────────────────────────────────
# パターン2: 河川護岸工事（中規模・公共土木）
# 直接工事費 約3,000万円規模
# ─────────────────────────────────────

SAMPLE_2_TEXT = """
工事名: ○○川護岸復旧工事
工事種別: 公共土木
地域: 新潟県
年度: 2026

【数量計算書】

工種: 仮設工
  種別: 仮締切工
    細別: 大型土のう設置
    数量: 200 個
  種別: 仮設道路工
    細別: 仮設道路（敷鉄板）
    数量: 300 m2

工種: 土工
  種別: 掘削工
    細別: バックホウ掘削（0.45m3級）
    数量: 3,000 m3
  種別: 盛土工
    細別: 盛土（流用土）
    数量: 2,500 m3

工種: コンクリート工
  種別: コンクリート工
    細別: 生コンクリート（24-8-40 N/mm2）
    数量: 450 m3
  種別: 型枠工
    細別: 普通型枠
    数量: 600 m2
  種別: 鉄筋工
    細別: 鉄筋加工組立（SD345 D13）
    数量: 12,000 kg
    細別: 鉄筋加工組立（SD345 D19）
    数量: 8,000 kg

工種: 石・ブロック工
  種別: 護岸工
    細別: 連節ブロック張（t=150mm）
    数量: 1,200 m2
  種別: 根固工
    細別: 根固めブロック（2t型）
    数量: 150 個

工種: 付帯工
  種別: 天端コンクリート工
    細別: 天端コンクリート
    数量: 180 m
"""

SAMPLE_2_EXPECTED_ITEMS = [
    {"category": "仮設工", "subcategory": "仮締切工", "detail": "大型土のう設置", "quantity": 200, "unit": "個"},
    {"category": "仮設工", "subcategory": "仮設道路工", "detail": "仮設道路（敷鉄板）", "quantity": 300, "unit": "m2"},
    {"category": "土工", "subcategory": "掘削工", "detail": "バックホウ掘削（0.45m3級）", "quantity": 3000, "unit": "m3"},
    {"category": "土工", "subcategory": "盛土工", "detail": "盛土（流用土）", "quantity": 2500, "unit": "m3"},
    {"category": "コンクリート工", "subcategory": "コンクリート工", "detail": "生コンクリート（24-8-40 N/mm2）", "quantity": 450, "unit": "m3"},
    {"category": "コンクリート工", "subcategory": "型枠工", "detail": "普通型枠", "quantity": 600, "unit": "m2"},
    {"category": "コンクリート工", "subcategory": "鉄筋工", "detail": "鉄筋加工組立（SD345 D13）", "quantity": 12000, "unit": "kg"},
    {"category": "コンクリート工", "subcategory": "鉄筋工", "detail": "鉄筋加工組立（SD345 D19）", "quantity": 8000, "unit": "kg"},
    {"category": "石・ブロック工", "subcategory": "護岸工", "detail": "連節ブロック張（t=150mm）", "quantity": 1200, "unit": "m2"},
    {"category": "石・ブロック工", "subcategory": "根固工", "detail": "根固めブロック（2t型）", "quantity": 150, "unit": "個"},
    {"category": "付帯工", "subcategory": "天端コンクリート工", "detail": "天端コンクリート", "quantity": 180, "unit": "m"},
]

SAMPLE_2_META = {
    "name": "○○川護岸復旧工事",
    "project_type": "public_civil",
    "region": "新潟県",
    "fiscal_year": 2026,
}

# ─────────────────────────────────────
# パターン3: 民間駐車場舗装工事（小規模・民間土木）
# 直接工事費 約500万円規模
# ─────────────────────────────────────

SAMPLE_3_TEXT = """
工事名: ○○ビル駐車場舗装改修工事
工事種別: 民間土木
地域: 大阪府
年度: 2026

【数量計算書】

工種: 撤去工
  種別: 舗装版撤去
    細別: アスファルト舗装版撤去（t=5cm）
    数量: 800 m2
  種別: 産廃処分
    細別: アスファルト殻運搬処分
    数量: 50 t

工種: 土工
  種別: 路床整正工
    細別: 路床整正（不陸整正）
    数量: 800 m2

工種: 舗装工
  種別: 下層路盤工
    細別: 砕石（RC-40 t=15cm）
    数量: 120 m3
  種別: プライムコート
    細別: プライムコート（PK-3）
    数量: 800 m2
  種別: アスファルト舗装工
    細別: 基層（粗粒度(20) t=5cm）
    数量: 800 m2
  種別: アスファルト舗装工
    細別: 表層（密粒度(13) t=4cm）
    数量: 800 m2

工種: 区画線工
  種別: 区画線工
    細別: 溶融式区画線（W=150mm）
    数量: 350 m
  種別: 車止め設置
    細別: 車止めブロック
    数量: 40 個
"""

SAMPLE_3_EXPECTED_ITEMS = [
    {"category": "撤去工", "subcategory": "舗装版撤去", "detail": "アスファルト舗装版撤去（t=5cm）", "quantity": 800, "unit": "m2"},
    {"category": "撤去工", "subcategory": "産廃処分", "detail": "アスファルト殻運搬処分", "quantity": 50, "unit": "t"},
    {"category": "土工", "subcategory": "路床整正工", "detail": "路床整正（不陸整正）", "quantity": 800, "unit": "m2"},
    {"category": "舗装工", "subcategory": "下層路盤工", "detail": "砕石（RC-40 t=15cm）", "quantity": 120, "unit": "m3"},
    {"category": "舗装工", "subcategory": "プライムコート", "detail": "プライムコート（PK-3）", "quantity": 800, "unit": "m2"},
    {"category": "舗装工", "subcategory": "アスファルト舗装工", "detail": "基層（粗粒度(20) t=5cm）", "quantity": 800, "unit": "m2"},
    {"category": "舗装工", "subcategory": "アスファルト舗装工", "detail": "表層（密粒度(13) t=4cm）", "quantity": 800, "unit": "m2"},
    {"category": "区画線工", "subcategory": "区画線工", "detail": "溶融式区画線（W=150mm）", "quantity": 350, "unit": "m"},
    {"category": "区画線工", "subcategory": "車止め設置", "detail": "車止めブロック", "quantity": 40, "unit": "個"},
]

SAMPLE_3_META = {
    "name": "○○ビル駐車場舗装改修工事",
    "project_type": "private_civil",
    "region": "大阪府",
    "fiscal_year": 2026,
}

# ─────────────────────────────────────
# 全サンプルリスト
# ─────────────────────────────────────

ALL_SAMPLES = [
    {"text": SAMPLE_1_TEXT, "expected": SAMPLE_1_EXPECTED_ITEMS, "meta": SAMPLE_1_META},
    {"text": SAMPLE_2_TEXT, "expected": SAMPLE_2_EXPECTED_ITEMS, "meta": SAMPLE_2_META},
    {"text": SAMPLE_3_TEXT, "expected": SAMPLE_3_EXPECTED_ITEMS, "meta": SAMPLE_3_META},
]
