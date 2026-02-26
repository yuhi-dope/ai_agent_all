# マーケティング（Marketing）専門ルール

## ドメイン概要

マーケティングエージェントは、集客施策の企画・実行・効果測定を一元管理するシステムである。広告・SNS・メルマガ・LP 等のチャネル横断で ROI を可視化し、リード獲得からナーチャリングまでの施策管理を支援する。

---

## 必須エンティティ

| エンティティ | 説明 | 必須フィールド例 |
|---|---|---|
| **Campaign（施策）** | マーケティング施策 | name, channel, budget, start_date, end_date, status, goal |
| **CampaignMetrics（施策効果）** | 日次の施策数値 | campaign_id, date, impressions, clicks, conversions, cost |
| **Lead（リード）** | 獲得見込み客 | name, email, source_campaign_id, status, score |
| **Content（コンテンツ）** | 制作物 | title, type(記事/動画/バナー/LP), status, url, campaign_id |
| **Channel（チャネル）** | 集客チャネルマスタ | name, type(広告/SNS/メール/SEO/紹介), is_active |

---

## ビジネスルール（Spec Agent / Coder Agent 共通）

### 施策管理

1. **ステータス**: 企画中 → 承認済 → 実行中 → 完了 → 分析中
2. **予算管理**: 施策ごとの予算設定。消化率（実コスト/予算）をリアルタイム表示
3. **KPI 設定**: 施策ごとに目標値（リード数、CVR、CPA 等）を設定
4. **チャネル別集計**: 広告 / SNS / メール / SEO / 紹介 のチャネル横断比較

### 効果測定指標

- **CTR（クリック率）** = clicks / impressions × 100
- **CVR（コンバージョン率）** = conversions / clicks × 100
- **CPA（獲得単価）** = cost / conversions
- **ROI** = (売上貢献額 - cost) / cost × 100
- **ROAS** = 売上貢献額 / cost × 100

### リード管理

- リードソース（どの施策から獲得したか）を必ず記録
- リードスコアリング: 行動（LP閲覧、資料DL、問い合わせ）に応じてスコア加算
- スコア閾値超えで「ホットリード」としてSFA連携（将来対応を想定）

---

## 受入条件テンプレート

- [ ] 施策を作成し、予算・期間・目標KPIを設定できる
- [ ] 施策ごとに日次の効果数値（impression/click/conversion/cost）を記録できる
- [ ] CTR/CVR/CPA/ROI が自動計算されダッシュボードに表示される
- [ ] チャネル別の施策効果を比較表示できる
- [ ] リードの獲得元施策が記録され、施策別リード数を集計できる
- [ ] 予算消化率が 80% を超えた施策にアラートが表示される

---

## Coder 向け実装指針

- 効果指標の計算は Server Action またはビュー（SQL VIEW）で実装。0 除算ガード必須
- 施策一覧はチャネル・ステータスでフィルタリング + 期間指定
- ダッシュボードにはチャート（棒グラフ: チャネル別 CPA、折れ線: 日次 CVR 推移）を配置
- チャートライブラリ: Recharts（shadcn/ui と親和性が高い）
- リードスコアは `marketing_lead_events` テーブルに行動を記録し、SUM で算出
