# No.2 / 経営エージェント専門ルール

## ドメイン概要

No.2エージェントは、経営者の「右腕」として KPI 管理・経営分析・戦略提言を行うシステムである。偉大な経営者（松下幸之助、孫正義、ジョブズ等）の思考フレームワークを活用し、データに基づく経営判断を支援する。

---

## 必須エンティティ

| エンティティ | 説明 | 必須フィールド例 |
|---|---|---|
| **KPI（重要指標）** | 経営 KPI 定義 | name, category(売上/利益/顧客/業務), target_value, current_value, unit, period |
| **KPIRecord（KPI実績）** | KPI の時系列データ | kpi_id, period_start, period_end, actual_value, note |
| **Insight（経営提言）** | AI からの提言 | title, category, summary, detail, persona, priority, status |
| **StrategicGoal（戦略目標）** | 中長期目標 | title, description, target_date, status, kpi_ids(JSONB) |
| **MeetingAgenda（経営会議）** | 経営会議アジェンダ | meeting_date, topics(JSONB), decisions(JSONB), action_items(JSONB) |

---

## ビジネスルール（Spec Agent / Coder Agent 共通）

### KPI 管理

1. **KPI カテゴリ**: 売上系 / 利益系 / 顧客系 / 業務効率系 / 人事系
2. **目標 vs 実績**: 各 KPI に目標値と実績値を設定し、達成率を自動計算
3. **アラート**: 達成率 80% 未満の KPI は赤色ハイライト、80-100% は黄色、100% 以上は緑
4. **トレンド**: 過去 12ヶ月の推移をグラフ表示。前月比・前年同月比を自動計算
5. **期間**: 月次 / 四半期 / 年次の集計を切替可能

### 偉人ペルソナ経営提言

- AI が KPI データと経営状況を分析し、偉人の思考フレームワークで提言を生成
- **松下幸之助**: 「水道哲学」「衆知を集める」— コスト削減・組織力強化の視点
- **孫正義**: 「タイムマシン経営」「ナンバーワン戦略」— 先行投資・市場支配の視点
- **スティーブ・ジョブズ**: 「シンプルの追求」「顧客体験」— プロダクト磨き込みの視点
- 提言には必ず `persona`（どの偉人の視点か）と `priority`（高/中/低）を付与

### 戦略目標

- OKR 形式: Objective（目標）+ Key Results（KPI との紐づけ）
- ステータス: 計画中 → 実行中 → 達成 / 未達
- 各目標に関連 KPI を紐づけ、KPI の達成状況から目標の進捗を自動算出

### 経営会議

- 定期会議のアジェンダを事前作成
- 決定事項（decisions）とアクションアイテム（action_items）を記録
- 未完了のアクションアイテムを次回会議に自動引き継ぎ

---

## 受入条件テンプレート

- [ ] KPI を登録し、目標値・実績値を設定できる
- [ ] KPI ダッシュボードで達成率がカラーコード（赤/黄/緑）で表示される
- [ ] KPI の過去 12ヶ月推移がグラフ表示され、前月比が確認できる
- [ ] 戦略目標を作成し、関連 KPI を紐づけて進捗率が自動算出される
- [ ] 経営会議のアジェンダ・決定事項・アクションアイテムを記録できる
- [ ] AI 提言が偉人ペルソナ付きで表示される（将来の AI 連携を想定した構造）

---

## Coder 向け実装指針

- KPI ダッシュボードは Recharts で折れ線グラフ（推移）+ ゲージチャート（達成率）
- 達成率の計算: actual_value / target_value × 100。target_value = 0 のガード必須
- 偉人ペルソナ提言は将来的に LLM API で生成する想定。現段階では `no2_insights` テーブルに手動登録 + 表示 UI を実装
- 経営会議の topics / decisions / action_items は JSONB 配列 `[{content, assignee?, due_date?, done?}]`
- KPI 一覧はカテゴリ別タブ切替 + カード表示
