# d_09 製造業向け経営ダッシュボードKPI設計

> **対象**: 中小製造業（金属加工/板金/樹脂成型/食品OEM等）10-300名規模
> **上位文書**: e_02c（経営インテリジェンス5モジュール）、e_02（製造業全貌）、c_02（プロダクト設計）
> **実装場所**: `routers/dashboard.py`（API）、`frontend/src/app/(authenticated)/dashboard/`（UI）
> **Phase**: Phase E（Week 6-7）— ⑨-⑬の経営インテリジェンスモジュールと同時投入
> **最終更新**: 2026-03-28

---

## 0. 設計思想

### なぜ「社長が5分で見るダッシュボード」か

```
中小製造業の社長の現実:
  ・PLは月末に経理が3日かけてまとめる → 見るのは月初の会議
  ・設備稼働率は工場長の感覚 →「だいたい70%くらい」
  ・品質は問題が起きてから対処 → 事後対応コスト年間売上の3-5%
  ・受注残は営業担当の頭の中 → 資金繰りの見通しが立たない
  ・キャッシュは通帳で確認 → 月末に「足りない」と気づく

  → 毎朝5分で「今、会社がどうなっているか」を把握できれば
  → 経営判断が3日早まる × 12ヶ月 = 年36日分の判断速度向上
  → 問題の早期発見で年間500-1,000万円の損失回避
```

### 参考フレームワーク

| フレームワーク | 活用箇所 |
|---|---|
| 中小企業庁「経営指標」（TKC BAST） | 粗利率・営業利益率の業界平均閾値 |
| OEE（設備総合効率）= 時間稼働率 x 性能稼働率 x 良品率 | 設備稼働KPIの計算式 |
| QCDS（Quality/Cost/Delivery/Safety） | KPI分類の軸 |
| トヨタ生産方式 | リードタイム・仕掛在庫のKPI化 |
| 原価計算基準（大蔵省企業会計審議会） | 原価按分ルールの根拠 |
| TKC BAST 製造業（2024年度版） | 赤/黄/緑の閾値設定根拠 |

### 参考ダッシュボードUI

| 製品 | 参考ポイント |
|---|---|
| Geckoboard | TV表示型のシンプルさ。7KPI以下。一目で赤/黄/緑 |
| Datadog | アラート設計が秀逸。Critical/Warning/Infoの3段階 |
| Tableau | ドリルダウン構造（L0→L1→L2→L3） |
| マネーフォワードクラウド | 中小企業向けの日本語UI。親しみやすい表現 |
| BECAUSE（製造業向けBI） | 製造業特有の指標（OEE、不良率）の見せ方 |

---

## A. 製造業の経営指標（業界標準整理）

### A.1 経営管理の4軸（QCDS拡張）

```
Q（Quality）: 品質 — 不良率、クレーム件数、Cpk
C（Cost）:    原価 — 粗利率、加工種別PL、光熱費比率
D（Delivery）: 納期 — 納期遵守率、平均リードタイム
S（Sales）:   営業 — 月次売上、受注残、新規引合い数
+F（Finance）: 財務 — キャッシュ残高、売掛金回転日数
+E（Equipment）: 設備 — 設備稼働率（OEE）、MTBF
```

### A.2 業界標準KPI一覧（候補プール）

| # | KPI名 | 計算式 | TKC BAST平均値 | 出所 |
|---|---|---|---|---|
| 1 | 売上高 | 月次売上合計 | 業種・規模で異なる | freee API / 手動入力 |
| 2 | 粗利率（売上総利益率） | (売上 - 製造原価) / 売上 x 100 | 20-30%（金属加工） | TKC BAST |
| 3 | 営業利益率 | 営業利益 / 売上 x 100 | 3-8% | TKC BAST |
| 4 | 受注残高 | 受注済み未出荷の売上合計 | - | ②生産計画AI |
| 5 | 受注残月数 | 受注残 / 月次売上平均 | 1.5-3.0ヶ月 | - |
| 6 | 設備稼働率（簡易OEE） | 実稼働時間 / 計画稼働時間 x 100 | 60-80% | ②生産計画AI / ⑥設備保全 |
| 7 | OEE（設備総合効率） | 時間稼働率 x 性能稼働率 x 良品率 | 世界平均60%、優良85%+ | ⑥設備保全 |
| 8 | 不良率（PPM） | 不良品数 / 総生産数 x 1,000,000 | 500-5,000 PPM | ③品質管理 |
| 9 | クレーム件数 | 月間顧客クレーム数 | 0-3件/月 | ③品質管理 |
| 10 | Cpk（工程能力指数） | min((USL-μ), (μ-LSL)) / 3σ | 1.33以上が目標 | ③品質管理 |
| 11 | 納期遵守率 | 期日内納品数 / 全納品数 x 100 | 95%以上が目標 | ②生産計画AI |
| 12 | 平均見積リードタイム | 見積依頼→回答の平均日数 | 3-5日（業界平均） | ①見積AI |
| 13 | キャッシュ残高 | 当座+普通預金残高 | - | freee API |
| 14 | 売掛金回転日数 | 売掛金 / (売上/365) | 60-90日 | freee API |
| 15 | 在庫回転率 | 売上原価 / 平均在庫金額 | 6-12回/年 | ④在庫最適化 |
| 16 | 労働生産性 | 付加価値 / 従業員数 | ¥600-900万/人/年 | TKC BAST |
| 17 | 1人当たり売上高 | 売上 / 従業員数 | ¥2,000-3,500万/人/年 | TKC BAST |

---

## B. 社長が毎朝5分で見るべきKPI（7個）

### B.1 選定基準

```
選定原則:
  1. 社長が「今日、何をすべきか」を判断できること
  2. データが①-⑧モジュールから自動取得できること（手動入力を極力排除）
  3. 7個以下（ミラーの法則: 人間の短期記憶は7±2）
  4. QCDS+F の5軸をカバーすること
  5. 閾値が業界標準で定義できること
```

### B.2 選定結果: 7 KPI

```
┌─────────────────────────────────────────────────────────────────┐
│  製造業 経営ダッシュボード                    2026年3月28日(木)  │
│                                                 07:02 更新      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ① 月次売上        ② 粗利率         ③ 受注残月数              │
│  ¥42,800,000       24.3%            2.1ヶ月                    │
│  [████████░░] 86%   [██████░░░░] 🟢   [██████░░░░] 🟢           │
│  目標比86%          目標25%           適正1.5-3.0               │
│                                                                 │
│  ④ 設備稼働率       ⑤ 不良率(PPM)    ⑥ 納期遵守率             │
│  73.2%             1,850 PPM        96.4%                      │
│  [███████░░░] 🟡    [████████░░] 🟡   [█████████░] 🟢           │
│  目標80%            目標1000          目標97%                    │
│                                                                 │
│  ⑦ キャッシュ残高                                               │
│  ¥18,500,000                                                    │
│  [██████░░░░] 🟡  月商比0.43（月商の0.5ヶ月以上が安全圏）       │
│                                                                 │
│  [!] アラート 2件                                                │
│  🔴 MC-3号機 異常停止（08:15発生）                               │
│  🟡 射出成型の月次粗利率が12.1%（業界平均15-25%）               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### B.3 各KPIの詳細定義

#### KPI-1: 月次売上高

| 項目 | 内容 |
|---|---|
| 計算式 | `SUM(当月の売上計上額)` |
| 単位 | 円（万円表示） |
| データソース | ⑨PLインテリジェンス（freee API or 手動入力） |
| 更新頻度 | 日次（売上計上タイミング） |
| 表示形式 | 金額 + 月間目標達成率のプログレスバー |
| 閾値 | 🟢 目標比 ≥ 90% / 🟡 70-90% / 🔴 < 70% |
| 閾値根拠 | 月間目標は前年同月実績 x 成長率で自動設定。70%未満は資金繰りリスク |
| 補足 | 月間目標は `company_settings.monthly_revenue_target` で設定。未設定時は前年同月実績 |

#### KPI-2: 粗利率（売上総利益率）

| 項目 | 内容 |
|---|---|
| 計算式 | `(売上高 - 製造原価) / 売上高 x 100` |
| 単位 | % |
| データソース | ⑨PLインテリジェンス（加工種別PLの全社集計） |
| 更新頻度 | 月次（月末締めの翌営業日） |
| 表示形式 | パーセント + 前月比矢印（↑↓→） |
| 閾値 | 🟢 ≥ 25% / 🟡 15-25% / 🔴 < 15% |
| 閾値根拠 | TKC BAST 金属加工業 黒字企業平均25-30%。15%未満は固定費カバー困難 |
| ドリルダウン | クリック → 加工種別PL比較（L1ビュー） |

#### KPI-3: 受注残月数

| 項目 | 内容 |
|---|---|
| 計算式 | `受注残高 / 直近3ヶ月平均月商` |
| 単位 | ヶ月 |
| データソース | ②生産計画AI（受注残テーブル）+ ⑨PLインテリジェンス（月商） |
| 更新頻度 | 日次（受注/出荷の都度更新） |
| 表示形式 | 数値 + 適正レンジのゲージ |
| 閾値 | 🟢 1.5-3.0ヶ月 / 🟡 0.5-1.5 or 3.0-4.5 / 🔴 < 0.5 or > 4.5 |
| 閾値根拠 | 0.5ヶ月未満=仕事枯れリスク、4.5ヶ月超=納期遅延・キャパオーバーリスク |
| 補足 | 受注残が少なすぎても多すぎても危険。両端がレッドゾーン |

#### KPI-4: 設備稼働率（簡易OEE）

| 項目 | 内容 |
|---|---|
| 計算式 | `実稼働時間 / 計画稼働時間 x 100`（Phase 1: 簡易版）|
| 計算式（Phase 2）| `時間稼働率 x 性能稼働率 x 良品率`（フルOEE） |
| 単位 | % |
| データソース | ②生産計画AI + ⑥設備保全（Phase 1: 手動入力 or IoTセンサー） |
| 更新頻度 | 日次（前日実績の翌朝反映） |
| 表示形式 | パーセント + 全設備平均 |
| 閾値 | 🟢 ≥ 80% / 🟡 60-80% / 🔴 < 60% |
| 閾値根拠 | 世界製造業OEE平均60%。80%以上はワールドクラス（TPM推進者協会） |
| ドリルダウン | クリック → 設備別稼働率一覧（L1ビュー） |

#### KPI-5: 不良率（PPM）

| 項目 | 内容 |
|---|---|
| 計算式 | `不良品数 / 総検査数 x 1,000,000` |
| 単位 | PPM（Parts Per Million） |
| データソース | ③品質管理AI（検査結果テーブル） |
| 更新頻度 | 日次（検査完了の都度更新） |
| 表示形式 | 数値 + 前月比トレンド矢印 |
| 閾値 | 🟢 < 1,000 PPM / 🟡 1,000-5,000 PPM / 🔴 > 5,000 PPM |
| 閾値根拠 | 自動車部品: 50 PPM以下が要求。一般部品: 1,000 PPM以下が業界水準 |
| 補足 | 業種・顧客要求で閾値をカスタマイズ可能（`company_settings.quality_target_ppm`） |
| ドリルダウン | クリック → 工程別・製品別不良分析（L1ビュー） |

#### KPI-6: 納期遵守率

| 項目 | 内容 |
|---|---|
| 計算式 | `期日内納品件数 / 全納品件数 x 100` |
| 単位 | % |
| データソース | ②生産計画AI（納品実績テーブル） |
| 更新頻度 | 日次 |
| 表示形式 | パーセント + 直近30日の推移ミニチャート |
| 閾値 | 🟢 ≥ 97% / 🟡 90-97% / 🔴 < 90% |
| 閾値根拠 | 自動車Tier1: 99%以上が必須。一般加工: 95%以上が取引継続の最低ライン |
| ドリルダウン | クリック → 顧客別納期遵守率（L2ビュー） |

#### KPI-7: キャッシュ残高

| 項目 | 内容 |
|---|---|
| 計算式 | `当座預金 + 普通預金 + 定期預金（すぐ解約可能なもの）` |
| 単位 | 円（万円表示） |
| データソース | freee API（口座残高取得）or 手動入力 |
| 更新頻度 | 日次（銀行API連携時）/ 週次（手動入力時） |
| 表示形式 | 金額 + 月商比率 |
| 閾値 | 🟢 月商比 ≥ 1.0 / 🟡 0.5-1.0 / 🔴 < 0.5 |
| 閾値根拠 | 中小企業庁推奨: 月商の1-2ヶ月分を手元に。0.5未満は資金ショートリスク |
| 補足 | 30日後キャッシュフロー予測（売掛入金 - 買掛支払い - 固定費）を併記 |

### B.4 閾値一覧表（赤/黄/緑）

| KPI | 🟢 緑（健全） | 🟡 黄（注意） | 🔴 赤（危険） | 根拠 |
|---|---|---|---|---|
| 月次売上 | 目標比 ≥ 90% | 70-90% | < 70% | 月間目標ベース |
| 粗利率 | ≥ 25% | 15-25% | < 15% | TKC BAST 金属加工業 |
| 受注残月数 | 1.5-3.0ヶ月 | 0.5-1.5 or 3.0-4.5 | < 0.5 or > 4.5 | 業界慣行 |
| 設備稼働率 | ≥ 80% | 60-80% | < 60% | TPM推進者協会基準 |
| 不良率(PPM) | < 1,000 | 1,000-5,000 | > 5,000 | ISO 9001ベースの一般水準 |
| 納期遵守率 | ≥ 97% | 90-97% | < 90% | 取引継続の最低ライン |
| キャッシュ残高 | 月商比 ≥ 1.0 | 0.5-1.0 | < 0.5 | 中小企業庁推奨 |

### B.5 閾値カスタマイズ

```python
# company_settingsテーブルで企業ごとにカスタマイズ可能
KPI_THRESHOLDS_DEFAULT = {
    "revenue_target_ratio":    {"green": 0.90, "yellow": 0.70},
    "gross_margin":            {"green": 0.25, "yellow": 0.15},
    "backlog_months":          {"green_low": 1.5, "green_high": 3.0,
                                "yellow_low": 0.5, "yellow_high": 4.5},
    "equipment_utilization":   {"green": 0.80, "yellow": 0.60},
    "defect_ppm":              {"green": 1000, "yellow": 5000},
    "delivery_compliance":     {"green": 0.97, "yellow": 0.90},
    "cash_months":             {"green": 1.0, "yellow": 0.5},
}
```

---

## C. アラート設計（Slack/LINE WORKS通知）

### C.1 アラート3段階

| 優先度 | 通知タイミング | 通知先 | 色 |
|---|---|---|---|
| CRITICAL | 即座（発生から5分以内） | 社長 + 工場長 + 担当者（個別メンション） | 赤 |
| WARNING | 当日中（朝のダッシュボード or 昼の定時通知） | 経営チャンネル | 黄 |
| INFO | 週次まとめ（毎週月曜AM 8:00） | 経営チャンネル | 青 |

### C.2 CRITICALアラート（即座通知）

| # | アラート名 | 検知条件 | データソース | 通知文テンプレート |
|---|---|---|---|---|
| C-1 | 設備異常停止 | 設備ステータスが `running` → `stopped` に変化（計画外） | ⑥設備保全 | `[CRITICAL] {equipment_name} 異常停止（{timestamp}）。直近MTBF: {mtbf}h` |
| C-2 | 重大品質クレーム | 顧客クレームの `severity` = `critical` | ③品質管理 | `[CRITICAL] {customer_name}から重大品質クレーム: {description}` |
| C-3 | キャッシュ枯渇予測 | 30日後キャッシュ残高の予測値 < 0 | freee API + 予測モデル | `[CRITICAL] {days_until_shortfall}日後にキャッシュ不足の見込み。現在残高: ¥{cash_balance}` |
| C-4 | 大口顧客の取引停止 | 月売上上位10%の顧客から `解約` or `取引中止` の連絡 | CRM/手動入力 | `[CRITICAL] {customer_name}（月商¥{monthly_revenue}）から取引中止の連絡` |
| C-5 | 材料欠品によるライン停止 | 在庫が安全在庫を下回り、発注リードタイム内に補充不可 | ④在庫最適化 | `[CRITICAL] {material_name}が欠品。{production_line}が{date}に停止見込み` |

### C.3 WARNINGアラート（当日中確認）

| # | アラート名 | 検知条件 | データソース | 通知文テンプレート |
|---|---|---|---|---|
| W-1 | 粗利率低下 | 月次粗利率が前月比 -5pt以上 | ⑨PLインテリジェンス | `[WARNING] 粗利率が{current}%（前月{prev}%、{diff}pt低下）` |
| W-2 | 在庫回転率悪化 | 在庫回転率が前月比 -20%以上 | ④在庫最適化 | `[WARNING] 在庫回転率が{current}回（前月{prev}回、{pct}%悪化）` |
| W-3 | 設備稼働率低下 | 週次設備稼働率 < 60% | ②生産計画AI | `[WARNING] 先週の設備稼働率{rate}%。受注不足 or 設備トラブルを確認` |
| W-4 | 納期遅延増加 | 直近7日の納期遵守率 < 90% | ②生産計画AI | `[WARNING] 直近7日の納期遵守率{rate}%。遅延案件: {delayed_orders}件` |
| W-5 | 不良率上昇 | 不良率が前月比 +50%以上 or 5,000 PPM超過 | ③品質管理 | `[WARNING] 不良率{current_ppm} PPM（前月{prev_ppm} PPM）。工程: {worst_process}` |
| W-6 | 受注残枯渇 | 受注残月数 < 1.0ヶ月 | ②生産計画AI | `[WARNING] 受注残{months}ヶ月。{weeks_until_idle}週後に稼働率低下見込み` |
| W-7 | 見積回答遅延 | 未回答見積が3日超過 | ①見積AI | `[WARNING] 未回答見積{count}件（最長{max_days}日経過）。受注機会逸失リスク` |
| W-8 | MTBF超過設備 | MTBFの80%を超過した稼働時間 | ⑥設備保全 | `[WARNING] {equipment_name}のMTBF{pct}%到達。予防保全を検討` |

### C.4 INFOアラート（週次まとめ）

| # | アラート名 | 検知条件 | データソース |
|---|---|---|---|
| I-1 | 新規引合い件数 | 週間の新規見積依頼数 | ①見積AI |
| I-2 | 品質Cpk改善 | Cpkが前週比で改善（1.33以上に回復） | ③品質管理 |
| I-3 | 設備保全完了 | 計画保全の完了報告 | ⑥設備保全 |
| I-4 | 在庫適正化達成 | 安全在庫割れゼロを1週間維持 | ④在庫最適化 |
| I-5 | 見積精度フィードバック | 見積 vs 実績原価の乖離レポート | ①見積AI |
| I-6 | 補助金公募情報 | ものづくり補助金等の公募開始/締切 | ⑩設備投資シミュレーター |
| I-7 | 業界トレンド要約 | 為替・材料相場の週次変動サマリー | ⑫市場インテリジェンス |

### C.5 通知チャンネル設計

```python
NOTIFICATION_CHANNELS = {
    "CRITICAL": {
        "channels": ["slack_dm", "line_works_dm", "push_notification"],
        "delay_seconds": 0,           # 即座
        "repeat_interval_minutes": 30, # 未対応なら30分おきにリマインド
        "escalation_minutes": 60,      # 1時間未対応で上位者にエスカレーション
    },
    "WARNING": {
        "channels": ["slack_channel", "dashboard_banner"],
        "delay_seconds": 0,
        "batch_window_hours": 4,       # 同種アラートは4時間でまとめる
    },
    "INFO": {
        "channels": ["weekly_digest_email", "dashboard_feed"],
        "schedule": "MON 08:00 JST",
    },
}
```

---

## D. ドリルダウン構造

### D.1 4階層構造

```
Level 0: 経営サマリー（7KPI + アラート一覧）
  │
  ├── Level 1: 加工種別ビュー
  │     各加工種別（旋盤/MC/板金/射出成型/...）のKPI比較
  │     表示: 加工種別PL比較表 + 稼働率比較 + 不良率比較
  │     遷移: KPI-2（粗利率）or KPI-4（稼働率）をタップ
  │
  ├── Level 2: 顧客別ビュー
  │     顧客A/B/C...の売上/粗利/納期遵守率
  │     表示: 顧客ランキング（売上順 or 粗利率順）
  │     遷移: L1の加工種別を選択 → その加工種別の顧客一覧
  │
  └── Level 3: 案件別ビュー
        個別案件の原価内訳/進捗/品質記録
        表示: 見積 vs 実績の原価比較 + ガントチャート
        遷移: L2の顧客を選択 → その顧客の案件一覧
```

### D.2 各レベルの詳細

#### Level 0: 経営サマリー

```
┌──────────────────────────────────────────────────────────┐
│  [7 KPI カード]                                          │
│  ※ B.2節の7KPIをカード形式で表示                         │
│                                                          │
│  [アラート一覧]                                          │
│  CRITICAL（赤）→ WARNING（黄）→ INFO（青）の順          │
│  最新5件表示。「すべて見る」で全件表示                    │
│                                                          │
│  [前月比サマリー]                                        │
│  7KPI全ての前月比を一行で。↑↓→ + 色で一目瞭然          │
│                                                          │
│  [AIインサイト]（⑨-⑬モジュールからの自動生成）          │
│  「射出成型の光熱費比率が業界平均の1.4倍です。            │
│   省エネ設備への更新で年¥180万の削減が見込めます」        │
└──────────────────────────────────────────────────────────┘
```

#### Level 1: 加工種別ビュー

```
┌──────────────────────────────────────────────────────────┐
│  加工種別比較                              期間: 2026年3月│
│                                                          │
│  [テーブル]                                              │
│  加工種別  | 売上    | 粗利率 | 稼働率 | 不良率 | 納期遵守│
│  CNC旋盤  | ¥1,500万| 28.2%🟢| 82%🟢 | 800🟢 | 98%🟢  │
│  MCセンタ  | ¥980万 | 22.5%🟡| 75%🟡 | 1,200🟡| 95%🟡  │
│  射出成型  | ¥620万 | 12.1%🔴| 55%🔴 | 3,500🟡| 88%🔴  │
│  板金      | ¥450万 | 25.8%🟢| 70%🟡 | 600🟢 | 97%🟢  │
│                                                          │
│  [ヒートマップ]                                          │
│  業界ベンチマーク比較（赤=平均未達、緑=平均超え）        │
│  → ⑨PLインテリジェンスのStep 4出力を可視化               │
│                                                          │
│  [トレンド]                                              │
│  選択した加工種別の3ヶ月推移グラフ                        │
└──────────────────────────────────────────────────────────┘
```

#### Level 2: 顧客別ビュー

```
┌──────────────────────────────────────────────────────────┐
│  顧客別分析     加工種別: CNC旋盤     期間: 2026年3月     │
│                                                          │
│  [ランキング]                                            │
│  # | 顧客名    | 月売上  | 粗利率 | 納期遵守 | リピート率│
│  1 | A精密(株) | ¥520万 | 32.1%  | 100%     | 月次      │
│  2 | B工業(株) | ¥380万 | 18.5%  | 94%      | 月次      │
│  3 | C電子(株) | ¥250万 | 28.7%  | 98%      | 四半期    │
│                                                          │
│  [顧客集中度]                                            │
│  上位3社で売上の65%。リスク分散の余地あり                 │
│                                                          │
│  [値上げ候補の自動提案]（⑨PLインテリジェンスから）       │
│  「B工業の粗利率18.5%は業界平均(25%)未達。                │
│   チャージレート¥500/h値上げで粗利率23%に改善可能」       │
└──────────────────────────────────────────────────────────┘
```

#### Level 3: 案件別ビュー

```
┌──────────────────────────────────────────────────────────┐
│  案件詳細     顧客: B工業(株)     案件: BK-2026-0342     │
│                                                          │
│  [基本情報]                                              │
│  品名: SUS304 精密シャフト    数量: 500個                 │
│  納期: 2026-04-15            ステータス: 加工中           │
│                                                          │
│  [見積 vs 実績 原価比較]                                 │
│              | 見積    | 実績    | 差異                    │
│  材料費      | ¥120万 | ¥125万 | +¥5万 (材料ロス)        │
│  加工費      | ¥180万 | ¥195万 | +¥15万 (段取り時間超過)  │
│  外注費      | ¥30万  | ¥30万  | ±0                      │
│  合計原価    | ¥330万 | ¥350万 | +¥20万                   │
│  粗利        | ¥70万  | ¥50万  | -¥20万 (粗利率12.5%)    │
│                                                          │
│  [工程進捗]（ガントチャート）                             │
│  素材切断  [████████████] 完了                            │
│  CNC旋盤  [████████░░░░] 65%                             │
│  研磨      [░░░░░░░░░░░░] 未着手                         │
│  検査      [░░░░░░░░░░░░] 未着手                         │
│                                                          │
│  [品質記録]                                              │
│  検査50個完了。不良2個(φ寸法外れ)。Cpk: 1.28             │
└──────────────────────────────────────────────────────────┘
```

### D.3 ナビゲーション設計

```
遷移パターン:
  L0 → L1: KPIカードをタップ → 関連する加工種別ビューへ
  L1 → L2: 加工種別の行をタップ → その加工種別の顧客一覧へ
  L2 → L3: 顧客の行をタップ → その顧客の案件一覧へ
  L3 → L0: パンくずリスト or 「ダッシュボードに戻る」ボタン

ショートカット:
  アラートカードをタップ → 関連するレベルに直接ジャンプ
    例: 「MC-3号機停止」タップ → L1の設備稼働率詳細
    例: 「射出成型の粗利率低下」タップ → L1の射出成型PL詳細
```

---

## E. APIエンドポイント設計

### E.1 既存dashboard.pyとの関係

```
既存（dashboard.py）:
  GET /dashboard/summary     → ナレッジ数・提案数・WAU（全業種共通）
  GET /dashboard/monthly-cost → 月間AIコスト

追加（製造業向け）:
  GET /dashboard/manufacturing/kpi          → L0: 7KPI + アラート
  GET /dashboard/manufacturing/process-type → L1: 加工種別ビュー
  GET /dashboard/manufacturing/customer     → L2: 顧客別ビュー
  GET /dashboard/manufacturing/order/{id}   → L3: 案件詳細ビュー
  GET /dashboard/manufacturing/alerts       → アラート一覧（全レベル）
  PUT /dashboard/manufacturing/settings     → 閾値カスタマイズ
```

### E.2 レスポンスモデル

```python
class ManufacturingKPIResponse(BaseModel):
    """L0: 製造業KPIダッシュボード"""
    period: str                              # YYYY-MM
    updated_at: datetime

    # 7 KPI
    monthly_revenue: int                     # 月次売上（円）
    monthly_revenue_target: int              # 月次売上目標（円）
    monthly_revenue_ratio: float             # 目標達成率
    monthly_revenue_status: str              # green/yellow/red

    gross_margin: float                      # 粗利率（%）
    gross_margin_prev: float                 # 前月粗利率（%）
    gross_margin_status: str

    backlog_months: float                    # 受注残月数
    backlog_amount: int                      # 受注残金額（円）
    backlog_status: str

    equipment_utilization: float             # 設備稼働率（%）
    equipment_utilization_status: str

    defect_ppm: int                          # 不良率（PPM）
    defect_ppm_prev: int                     # 前月不良率
    defect_ppm_status: str

    delivery_compliance: float               # 納期遵守率（%）
    delivery_compliance_status: str

    cash_balance: int                        # キャッシュ残高（円）
    cash_months: float                       # キャッシュ月商比
    cash_status: str

    # アラート（直近5件）
    alerts: list[DashboardAlert]

    # AIインサイト（⑨-⑬からの自動生成、最大3件）
    ai_insights: list[str]


class DashboardAlert(BaseModel):
    """ダッシュボードアラート"""
    id: UUID
    severity: str                            # CRITICAL/WARNING/INFO
    title: str
    description: str
    source_module: str                       # 発生元モジュール（①-⑬）
    created_at: datetime
    acknowledged: bool                       # 確認済みフラグ
    drill_down_level: int                    # ジャンプ先レベル（1-3）
    drill_down_params: dict                  # ジャンプ先パラメータ


class ProcessTypeKPIResponse(BaseModel):
    """L1: 加工種別ビュー"""
    period: str
    process_types: list[ProcessTypeKPI]
    benchmarks: dict                         # 業界ベンチマーク


class ProcessTypeKPI(BaseModel):
    """加工種別ごとのKPI"""
    process_type: str                        # CNC旋盤/MCセンタ/板金/...
    revenue: int
    gross_margin: float
    utilization: float
    defect_ppm: int
    delivery_compliance: float
    revenue_trend: list[float]               # 直近3ヶ月の推移
    margin_trend: list[float]


class CustomerKPIResponse(BaseModel):
    """L2: 顧客別ビュー"""
    period: str
    process_type: Optional[str]              # フィルタ（指定時）
    customers: list[CustomerKPI]
    concentration_risk: float                # 上位3社の売上集中度（%）


class CustomerKPI(BaseModel):
    """顧客ごとのKPI"""
    customer_id: UUID
    customer_name: str
    monthly_revenue: int
    gross_margin: float
    delivery_compliance: float
    repeat_frequency: str                    # 月次/四半期/スポット
    price_review_suggestion: Optional[str]   # 値上げ提案（⑨PLから）
```

---

## F. モバイル対応設計

### F.1 基本方針: PWA（ネイティブアプリ不要）

```
判断根拠:
  1. 社長の利用シーン = 工場巡回中にスマホでチラ見（5分）
  2. 操作は「見る」が95%。「入力」はPC前提
  3. PWAでプッシュ通知が可能（iOS 16.4+、Android は以前から対応）
  4. ネイティブアプリの開発・審査・メンテコストを回避
  5. MVP原則: 開発工数を最小化

  → PWAで十分。ネイティブアプリはPMF後の要望次第で検討
```

### F.2 モバイルUI設計原則

```
1. L0（7KPI）はスマホ1画面に収める
   - 2列グリッド x 4行 = 8カード（7KPI + アラートサマリー）
   - カードサイズ: 幅50%、高さ80px
   - タップで展開 or ドリルダウン

2. 数字は大きく。ラベルは小さく
   - KPI値: 24px bold
   - ラベル: 12px gray
   - ステータス: 色付きドット（12px）

3. スワイプでレベル移動
   - 左スワイプ: ドリルダウン（L0→L1→L2→L3）
   - 右スワイプ: 戻る
   - パンくずリストは画面上部に常時表示

4. 横向き非対応（縦固定）
   - 製造業社長は片手でスマホを見る
   - チャート/グラフはタップで全画面表示

5. オフライン対応
   - 最新のKPIデータをService Workerでキャッシュ
   - オフラインでも直近のスナップショットは表示可能
   - ネットワーク復帰時に自動更新
```

### F.3 プッシュ通知設計

```python
PUSH_NOTIFICATION_CONFIG = {
    "CRITICAL": {
        "title": "[緊急] {alert_title}",
        "body": "{alert_description}",
        "badge": True,
        "sound": "alert",               # 通知音あり
        "require_interaction": True,     # 明示的にタップで閉じる
        "ttl_seconds": 3600,             # 1時間有効
        "actions": [
            {"action": "view", "title": "詳細を見る"},
            {"action": "acknowledge", "title": "確認済み"},
        ],
    },
    "WARNING": {
        "title": "[注意] {alert_title}",
        "body": "{alert_description}",
        "badge": True,
        "sound": "default",             # デフォルト通知音
        "require_interaction": False,
        "ttl_seconds": 14400,            # 4時間有効
    },
    "INFO": {
        # プッシュ通知なし。ダッシュボード内のフィード + 週次メールのみ
        "push": False,
    },
}
```

### F.4 表示パフォーマンス要件

| 指標 | 目標値 | 手段 |
|---|---|---|
| 初回表示（L0） | < 2秒 | KPIデータはAPIレスポンス < 500ms + SSR |
| ドリルダウン遷移 | < 1秒 | クライアントサイドルーティング + データプリフェッチ |
| プッシュ通知到達 | < 30秒 | Firebase Cloud Messaging（FCM） |
| オフライン復帰 | < 5秒 | Service Worker + IndexedDB |

---

## G. データフロー（①-⑬ → ダッシュボード）

```
┌──────────────────────────────────────────────────────────────┐
│ BPOモジュール（データ生成）                                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ① 見積AI ──────→ 見積データ（リードタイム、件数）           │
│  ② 生産計画AI ──→ 受注残、稼働率、納期実績                  │
│  ③ 品質管理AI ──→ 不良率、Cpk、クレーム                     │
│  ④ 在庫最適化 ──→ 在庫回転率、欠品リスク                    │
│  ⑤ SOP管理 ────→ （直接KPIなし。⑬技術戦略に間接貢献）      │
│  ⑥ 設備保全 ────→ MTBF、設備ステータス、メンテ計画          │
│  ⑦ 仕入管理 ────→ 材料費実績、仕入先情報                    │
│  ⑧ ISO文書管理 ─→ （直接KPIなし。コンプライアンスステータス）│
│                                                              │
│  ⑨ PLインテリジェンス ──→ 月次売上、粗利率、加工種別PL      │
│  ⑩ 設備投資シミュレーター→ 投資判断レポート                  │
│  ⑪ 立地戦略AI ──────────→ （オンデマンド。常時KPIなし）      │
│  ⑫ 市場インテリジェンス──→ 為替・材料相場アラート            │
│  ⑬ 技術戦略AI ──────────→ （四半期レビュー。常時KPIなし）    │
│                                                              │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ KPI集計レイヤー（routers/dashboard.py）                        │
│                                                              │
│  日次バッチ（AM 6:00 JST）:                                   │
│    - ①-⑧の前日実績を集計                                     │
│    - 7KPIのスナップショットをDBに保存                          │
│    - 閾値判定 → アラート生成                                  │
│                                                              │
│  月次バッチ（月初2営業日目）:                                  │
│    - ⑨PLインテリジェンスの月次レポートをトリガー              │
│    - 前月比・トレンド計算                                      │
│    - 業界ベンチマーク比較                                      │
│                                                              │
│  リアルタイム:                                                │
│    - CRITICALアラートはイベント駆動で即座生成                 │
│    - 設備停止、クレーム受信時に即時通知                        │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│ フロントエンド（Next.js PWA）                                  │
│                                                              │
│  L0: 経営サマリー → L1: 加工種別 → L2: 顧客別 → L3: 案件別  │
│                                                              │
│  通知: FCM → Service Worker → Push Notification              │
│  キャッシュ: IndexedDB（オフライン対応）                       │
└──────────────────────────────────────────────────────────────┘
```

---

## H. DB設計（追加テーブル）

### H.1 KPIスナップショットテーブル

```sql
CREATE TABLE mfg_kpi_snapshots (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id),
    snapshot_date DATE NOT NULL,

    -- 7 KPI
    monthly_revenue BIGINT,                 -- 月次売上（円）
    monthly_revenue_target BIGINT,          -- 月次売上目標（円）
    gross_margin DECIMAL(5,2),              -- 粗利率（%）
    backlog_amount BIGINT,                  -- 受注残金額（円）
    backlog_months DECIMAL(4,2),            -- 受注残月数
    equipment_utilization DECIMAL(5,2),     -- 設備稼働率（%）
    defect_ppm INTEGER,                     -- 不良率（PPM）
    delivery_compliance DECIMAL(5,2),       -- 納期遵守率（%）
    cash_balance BIGINT,                    -- キャッシュ残高（円）
    cash_months DECIMAL(4,2),              -- キャッシュ月商比

    created_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE(company_id, snapshot_date)
);

-- RLS
ALTER TABLE mfg_kpi_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON mfg_kpi_snapshots
    USING (company_id = current_setting('app.company_id')::UUID);
```

### H.2 アラートテーブル

```sql
CREATE TABLE mfg_dashboard_alerts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id),
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'INFO')),
    title VARCHAR(200) NOT NULL,
    description TEXT,
    source_module VARCHAR(50),              -- 発生元モジュール名
    drill_down_level INTEGER,               -- 1-3
    drill_down_params JSONB DEFAULT '{}',
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by UUID REFERENCES users(id),
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    expires_at TIMESTAMPTZ                  -- アラート有効期限
);

-- RLS
ALTER TABLE mfg_dashboard_alerts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON mfg_dashboard_alerts
    USING (company_id = current_setting('app.company_id')::UUID);

-- Index
CREATE INDEX idx_mfg_alerts_company_severity
    ON mfg_dashboard_alerts(company_id, severity, created_at DESC);
```

### H.3 KPI閾値設定テーブル

```sql
CREATE TABLE mfg_kpi_settings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company_id UUID NOT NULL REFERENCES companies(id) UNIQUE,
    thresholds JSONB NOT NULL DEFAULT '{
        "revenue_target_ratio":    {"green": 0.90, "yellow": 0.70},
        "gross_margin":            {"green": 0.25, "yellow": 0.15},
        "backlog_months":          {"green_low": 1.5, "green_high": 3.0, "yellow_low": 0.5, "yellow_high": 4.5},
        "equipment_utilization":   {"green": 0.80, "yellow": 0.60},
        "defect_ppm":              {"green": 1000, "yellow": 5000},
        "delivery_compliance":     {"green": 0.97, "yellow": 0.90},
        "cash_months":             {"green": 1.0, "yellow": 0.5}
    }',
    monthly_revenue_target BIGINT,          -- 月次売上目標（手動設定）
    quality_target_ppm INTEGER DEFAULT 1000,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_kpi_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON mfg_kpi_settings
    USING (company_id = current_setting('app.company_id')::UUID);
```

---

## I. 実装計画

### I.1 Phase E-1（Week 6）: 最小ダッシュボード

```
実装範囲:
  - L0: 7KPIカード表示（データはモック + ⑨PLインテリジェンスから取得可能なもの）
  - アラート一覧（CRITICAL/WARNINGのみ）
  - モバイルレスポンシブ（2列グリッド）
  - DB: mfg_kpi_snapshots + mfg_dashboard_alerts 作成
  - API: GET /dashboard/manufacturing/kpi

依存:
  - ⑨PLインテリジェンスが月次PLデータを出力済みであること
  - 最低限 KPI-1（売上）と KPI-2（粗利率）はデータあり
  - 他のKPIはデータ未取得の場合「--」表示
```

### I.2 Phase E-2（Week 7）: ドリルダウン + アラート

```
実装範囲:
  - L1: 加工種別ビュー
  - L2: 顧客別ビュー（簡易版）
  - アラート通知（Slack連携）
  - 閾値カスタマイズ UI
  - API: GET /dashboard/manufacturing/process-type, /customer

依存:
  - ②生産計画AI、③品質管理AI、④在庫最適化からのデータ連携
```

### I.3 Phase 2+: フル機能

```
追加予定:
  - L3: 案件別ビュー（見積 vs 実績の原価比較）
  - フルOEE計算（IoTセンサー連携）
  - 30日キャッシュフロー予測
  - LINE WORKS通知対応
  - PWAプッシュ通知
  - ネイティブアプリ（要望次第）
  - 経営レポート自動生成（月次PDF）
```

---

## J. 制約・前提条件

1. Phase 1のデータソースは⑨PLインテリジェンス + 手動入力が中心。IoT連携はPhase 2+
2. OEEは簡易版（時間稼働率のみ）。性能稼働率 x 良品率を含むフル計算はPhase 2+
3. キャッシュ残高はfreee API連携 or 手動入力。銀行API直接連携はPhase 2+
4. 閾値はTKC BASTの金属加工業ベース。業種（食品/樹脂等）への自動切替はPhase 2+
5. ダッシュボードは製造業向け専用設計。他業種向けは別途設計が必要（構造は流用可能）
6. プッシュ通知はFCM経由。iOS Safari 16.4+が必要（それ以前はSlack通知で代替）
