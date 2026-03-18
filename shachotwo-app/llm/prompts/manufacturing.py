"""製造業BPO用 LLMプロンプト"""

DRAWING_ANALYSIS_PROMPT = """以下の部品情報を解析して、加工に必要な情報をJSON形式で出力してください。

## 部品情報
説明: {description}
材質: {material}
数量: {quantity}
表面処理: {surface_treatment}

## 出力形式（JSON）
{{
  "shape_type": "round" | "block" | "plate" | "complex",
  "dimensions": {{
    "outer_diameter": mm（丸物の場合）,
    "inner_diameter": mm（中空の場合）,
    "length": mm,
    "width": mm（角物・板物の場合）,
    "height": mm（角物の場合）,
    "thickness": mm（板物の場合）
  }},
  "tolerances": {{
    "general": "±0.1" | "±0.05" | "±0.01",
    "tight_dimensions": ["φ30 ±0.01", "長さ100 ±0.05"]
  }},
  "surface_roughness": "Ra 6.3" | "Ra 1.6" | "Ra 0.4",
  "hardness": "HRC 50-55"（指定がある場合のみ）,
  "weight_kg": 推定重量（kg）,
  "features": [
    {{"feature_type": "hole", "description": "φ10貫通穴", "dimensions": {{"diameter": 10, "depth": 30}}}},
    {{"feature_type": "thread", "description": "M8ネジ穴", "dimensions": {{"size": "M8", "depth": 15}}}},
    {{"feature_type": "chamfer", "description": "C1面取り", "dimensions": {{}}}},
    {{"feature_type": "slot", "description": "キー溝", "dimensions": {{"width": 5, "depth": 3}}}}
  ],
  "notes": "特記事項"
}}

## 判断基準
- 丸物（round）: シャフト、ピン、ブッシュ、スリーブ等。φ（外径）の記載がある
- 角物（block）: ブロック、プレート加工品。幅×高さ×長さの記載がある
- 板物（plate）: ブラケット、カバー。厚さ（t=）が薄い（〜20mm）
- 複雑形状（complex）: 上記に当てはまらない、または5軸加工が必要

## 材質の特性（参考）
- SS400: 一般鋼。安価。加工しやすい
- S45C: 焼入れ可能。硬度指定があることが多い
- SUS304: ステンレス。加工しにくい（サイクルタイム1.5-2倍）
- A5052/A6061: アルミ。軽い。加工しやすい
- C3604: 真鍮。快削。最も加工しやすい

説明文に寸法が明記されていない場合は、一般的な部品サイズを推定してください。
JSONのみを出力してください。"""


PROCESS_ESTIMATION_PROMPT = """以下の部品の加工工程を推定してJSON配列で出力してください。

## 部品情報
形状: {shape_type}
寸法: {dimensions}
材質: {material}
公差: {tolerances}
表面粗さ: {surface_roughness}
表面処理: {surface_treatment}
加工要素: {features}
硬度指定: {hardness}

## 出力形式（JSON配列）
[
  {{
    "process_name": "工程名",
    "equipment": "使用設備",
    "equipment_type": "設備種別",
    "setup_time_min": 段取り時間（分）,
    "cycle_time_min": サイクルタイム（分/個）,
    "notes": "備考"
  }}
]

## 工程推定の基準

### 形状→基本工程
- 丸物: 材料切断→CNC旋盤→（MC穴加工）→研磨→検査
- 角物: 材料切断→マシニングセンタ→研磨→検査
- 板物: レーザー切断→（曲げ）→（溶接）→検査
- 複雑: 材料切断→MC→ワイヤーカット→（放電加工）→研磨→検査

### 追加工程の判断
- ネジ穴あり → タップ加工（MCに含む）
- 公差±0.05以下 → 研磨追加
- Ra 0.4以下 → 鏡面研磨追加
- 硬度指定（HRC）あり → 焼入れ・焼戻し（外注）→ 研磨
- 表面処理あり → メッキ/アルマイト等（外注）

### サイクルタイムの目安（SS400基準）
- CNC旋盤: φ30×100mm → 5-8分/個
- MC: 100×100×50mm → 15-25分/個
- 研磨: 1面 → 5-10分/個
- レーザー: 外周500mm → 2-5分/個

### 材質による補正
- SUS304: サイクルタイム ×1.7
- アルミ: サイクルタイム ×0.8
- 真鍮(C3604): サイクルタイム ×0.7
- S45C焼入れ後: サイクルタイム ×1.5

工程順に出力してください。JSONのみを出力してください。"""
