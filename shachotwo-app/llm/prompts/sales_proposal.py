"""SFA/CRM: 提案書AI生成用プロンプト"""

# 製造業: 課題 → シャチョツーソリューション名のマッピング
# 製造業8パイプライン（見積/品質/在庫/生産計画/設備保全/ISO/調達/SOP）に対応
MANUFACTURING_SOLUTION_MAP: dict[str, str] = {
    "見積回答に時間がかかり受注機会を逃す": (
        "AI見積エンジン（3層見積: 類似実績→パラメトリック→AI推定）で回答時間を数日→数分に短縮"
    ),
    "品質トラブル時の原因特定に時間がかかる": (
        "品質管理AI（不良パターン分析・是正措置自動提案）で原因特定を即時化"
    ),
    "在庫の過不足が常態化している": (
        "在庫最適化AI（需要予測・安全在庫計算・発注点自動設定）で適正在庫を維持"
    ),
    "生産計画の作成に時間がかかる": (
        "生産計画AI（負荷山積み・山崩し・ボトルネック検出）で計画作成を自動化"
    ),
    "設備の突発故障で生産が止まる": (
        "設備保全AI（予防保全スケジュール・故障予兆検知）でダウンタイムを削減"
    ),
    "ISO文書の管理が煩雑": (
        "ISO文書管理AI（版数管理・レビュー期限通知・監査準備自動化）で工数を削減"
    ),
    "調達・発注業務が属人化している": (
        "調達AI（サプライヤー評価・発注最適化・納期管理）で調達を標準化"
    ),
    "作業手順書の更新が追いつかない": (
        "SOP管理AI（手順書自動生成・改訂管理・教育記録連携）で最新版を常に維持"
    ),
}

INDUSTRY_PAIN_POINTS: dict[str, list[str]] = {
    "construction": [
        "積算・見積作成に膨大な時間がかかる",
        "安全書類・施工計画書の作成負担",
        "下請管理・工程管理の属人化",
        "熟練技術者の退職によるノウハウ喪失",
    ],
    "manufacturing": list(MANUFACTURING_SOLUTION_MAP.keys()),
    "dental": [
        "レセプト点検・返戻対応の負担",
        "患者説明資料の作成に時間がかかる",
        "予約・リコール管理の属人化",
        "スタッフ教育の標準化が進まない",
    ],
    "nursing": [
        "介護記録・計画書作成の負担が大きい",
        "シフト作成に毎月数日かかる",
        "加算要件の確認漏れによる請求ミス",
        "ベテラン職員の退職でケアの質が低下",
    ],
    "logistics": [
        "配車計画の最適化が属人的",
        "荷主への見積回答が遅い",
        "ドライバーの労務管理（2024年問題）",
        "事故・クレーム対応の標準化不足",
    ],
    "restaurant": [
        "食材発注の最適化ができていない（廃棄ロス）",
        "シフト作成・人件費管理の負担",
        "メニュー原価計算が属人的",
        "衛生管理記録の作成負担",
    ],
    "clinic": [
        "レセプト点検・返戻対応の工数",
        "問診票→カルテ転記の二重入力",
        "患者説明・同意書作成の負担",
        "スタッフ間の申し送りミス",
    ],
    "pharmacy": [
        "調剤過誤チェックの負担",
        "薬歴記載に時間がかかる",
        "在庫管理・発注の最適化",
        "服薬指導の標準化が不十分",
    ],
    "beauty": [
        "予約管理・顧客カルテの整備不足",
        "スタッフ指名・シフト調整の負担",
        "リピート促進施策が場当たり的",
        "施術メニュー別の収益管理ができていない",
    ],
    "auto_repair": [
        "見積作成・部品発注が属人的",
        "車検・点検の進捗管理が紙ベース",
        "整備記録の検索・活用ができない",
        "顧客への作業説明に時間がかかる",
    ],
    "hotel": [
        "予約管理・料金設定の最適化不足",
        "清掃・メンテナンス管理の属人化",
        "多言語対応の負担",
        "口コミ対応・顧客満足度管理の遅れ",
    ],
    "ecommerce": [
        "商品登録・説明文作成の工数",
        "在庫管理・発注の最適化不足",
        "問い合わせ対応の負担",
        "売上分析・販促施策が場当たり的",
    ],
    "staffing": [
        "求職者と求人のマッチング精度が低い",
        "契約書・勤怠管理の手作業負担",
        "派遣スタッフのフォロー漏れ",
        "法令対応（派遣法改正等）の確認負担",
    ],
    "architecture": [
        "設計図書の整合性チェックが手作業",
        "確認申請書類の作成負担",
        "過去案件の検索・ナレッジ活用ができない",
        "コスト概算の精度が低い",
    ],
    "realestate": [
        "物件情報の登録・更新が手作業",
        "契約書類の作成・チェック負担",
        "顧客マッチングが属人的",
        "重要事項説明書の作成に時間がかかる",
    ],
    "professional": [
        "書類作成・申請手続きの工数が膨大",
        "法改正の追跡・対応が追いつかない",
        "顧問先ごとのナレッジが共有されない",
        "期限管理・リマインドが属人的",
    ],
}

_SYSTEM_PROPOSAL_TEMPLATE = """あなたはシャチョツー（社長2号）の営業提案書を作成する専門家です。
顧客企業の情報をもとに、最適な提案書をJSON形式で作成してください。

## シャチョツーとは
中小企業向けの「AI社長秘書」サービスです。
- ブレイン: 社長の頭の中にある暗黙知・判断基準をAIが学習し、社員がいつでもQ&Aで参照可能
- BPO: 業界特化の業務自動化（見積作成、書類生成、データ入力等）をAIが代行
- デジタルツイン: 会社の状態を5次元（ヒト/プロセス/コスト/ツール/リスク）で可視化

## 料金体系
- ブレイン: 月額{price_brain:,}円（ナレッジ蓄積・Q&A・デジタルツイン）
- BPOコア: 月額{price_bpo_core:,}円（業界特化の主要モジュール群）
- 追加モジュール: 月額{price_additional:,}円/個

## 出力形式（JSON）
{{
  "cover": {{
    "title": "提案書タイトル",
    "subtitle": "サブタイトル",
    "target_company": "企業名",
    "date": "作成日"
  }},
  "pain_points": [
    {{
      "category": "カテゴリ名",
      "description": "課題の詳細説明",
      "impact": "放置した場合のビジネスインパクト",
      "priority": "high|medium|low"
    }}
  ],
  "solution_map": [
    {{
      "pain_point": "対応する課題",
      "solution": "シャチョツーによる解決策",
      "module": "対応モジュール名",
      "effect": "期待される効果"
    }}
  ],
  "modules": [
    {{
      "name": "モジュール名",
      "description": "機能概要",
      "monthly_price": 月額料金（円）,
      "key_features": ["機能1", "機能2"]
    }}
  ],
  "pricing": {{
    "modules_total": 月額合計（円）,
    "annual_total": 年額合計（円）,
    "discount_note": "割引がある場合の説明"
  }},
  "roi_estimate": {{
    "current_cost_monthly": 現状の推定月間コスト（円）,
    "after_cost_monthly": 導入後の推定月間コスト（円）,
    "savings_monthly": 月間削減額（円）,
    "payback_months": 投資回収期間（月）,
    "calculation_basis": "算出根拠",
    "confidence": 0.0-1.0
  }},
  "timeline": [
    {{
      "phase": "Phase名",
      "period": "期間（例: 1-2週目）",
      "tasks": ["タスク1", "タスク2"],
      "milestone": "マイルストーン"
    }}
  ]
}}

## データの安全性（提案書に必ず含める）
- データ保管: 日本国内（東京）のAWSデータセンター（freee・kintoneと同じ基盤）
- サーバー: 日本国内（東京）のGCPで稼働
- 暗号化: AES-256-GCM（保存時）/ TLS 1.3（通信時）— 銀行と同水準
- AI処理: Google Geminiで一時処理。処理後のデータはGoogleに保存されない。AIの学習に顧客データは使用しない
- テナント分離: 他社データとは完全に隔離（Row Level Security）
- 解約時: 全データ完全削除 + 削除証明書発行

## 重要
- 日本語で出力してください
- 業種に特化したペインポイントと解決策を具体的に記載してください
- ROIは保守的に見積もり、根拠を明記してください（confidence 0.4-0.7）
- 導入タイムラインは現実的なスケジュールで（通常4-8週間）
- JSONのみを出力してください
"""

USER_PROPOSAL_TEMPLATE = """以下の企業情報をもとに提案書を作成してください。

## 企業情報
企業名: {company_name}
業種: {industry}
従業員数: {employee_count}名
ペインポイント（ヒアリング内容）: {pain_points}
検討モジュール: {selected_modules}

## 業種別の一般的な課題（参考）
{industry_pain_points}

{solution_map_section}ヒアリング内容を最優先し、業種別の一般課題は補足として活用してください。"""

# デフォルト料金でレンダリングした後方互換用定数
# 新規コードは build_proposal_system_prompt() を使うこと
SYSTEM_PROPOSAL: str = _SYSTEM_PROPOSAL_TEMPLATE.format(
    price_brain=30_000,
    price_bpo_core=250_000,
    price_additional=100_000,
)


def build_proposal_system_prompt(prices: dict[str, int] | None = None) -> str:
    """料金をDB管理の値で差し込んだシステムプロンプトを返す。

    Args:
        prices: get_all_module_prices() の返り値。None の場合はデフォルト料金を使用。

    Returns:
        システムプロンプト文字列
    """
    if prices is None:
        return SYSTEM_PROPOSAL
    return _SYSTEM_PROPOSAL_TEMPLATE.format(
        price_brain=prices.get("brain", 30_000),
        price_bpo_core=prices.get("bpo_core", 250_000),
        price_additional=prices.get("additional", 100_000),
    )


def build_user_proposal_prompt(
    company_name: str,
    industry: str,
    employee_count: int,
    pain_points: str,
    selected_modules: str,
) -> str:
    """製造業の場合はソリューションマップを注入したユーザープロンプトを返す。

    製造業（industry == "manufacturing"）のとき、MANUFACTURING_SOLUTION_MAP を
    プロンプトに追加し、LLM が各課題に対応するソリューション名を具体的に記載できるよう誘導する。
    その他業種では solution_map_section は空文字になる。

    Args:
        company_name: 顧客企業名
        industry: 業種コード（例: "manufacturing", "construction"）
        employee_count: 従業員数
        pain_points: ヒアリングしたペインポイント（自由記述）
        selected_modules: 検討中のモジュール名（自由記述）

    Returns:
        ユーザープロンプト文字列
    """
    industry_pain_points_list = INDUSTRY_PAIN_POINTS.get(industry, [])
    industry_pain_points_str = "\n".join(
        f"- {p}" for p in industry_pain_points_list
    ) if industry_pain_points_list else "（業種固有の課題データなし）"

    solution_map_section = ""
    if industry == "manufacturing":
        lines = [
            "## 製造業向けソリューションマップ（課題→シャチョツー機能名を必ず記載）"
        ]
        for pain, solution in MANUFACTURING_SOLUTION_MAP.items():
            lines.append(f"- {pain}: {solution}")
        lines.append("")
        solution_map_section = "\n".join(lines) + "\n"

    return USER_PROPOSAL_TEMPLATE.format(
        company_name=company_name,
        industry=industry,
        employee_count=employee_count,
        pain_points=pain_points,
        selected_modules=selected_modules,
        industry_pain_points=industry_pain_points_str,
        solution_map_section=solution_map_section,
    )
