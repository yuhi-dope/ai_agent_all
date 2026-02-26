# SFA（営業管理）専門ルール

## ドメイン概要

SFA（Sales Force Automation）は、商談の発生からクローズまでの営業プロセスを管理し、売上予測・パイプライン可視化・営業活動の効率化を実現するシステムである。

---

## 必須エンティティ

| エンティティ | 説明 | 必須フィールド例 |
|---|---|---|
| **Deal（商談）** | 営業案件の単位 | title, amount, stage, probability, expected_close_date, assigned_to |
| **Stage（ステージ）** | 商談の進捗状態 | name, order, probability_default, is_won, is_lost |
| **Lead（リード）** | 見込み顧客 | name, email, phone, source, status |
| **Activity（活動）** | 営業活動記録 | type(訪問/架電/メール), deal_id, note, activity_date |
| **Quote（見積）** | 見積書 | deal_id, items, subtotal, tax, total, valid_until |

---

## ビジネスルール（Spec Agent / Coder Agent 共通）

### 商談パイプライン・ステージ遷移

1. **デフォルトステージ**: リード獲得 → アポ取得 → ヒアリング → 提案 → 見積提出 → 交渉 → 受注 / 失注
2. **前進のみ**: ステージは基本的に前進方向のみ（戻しは管理者権限が必要）
3. **確度連動**: 各ステージに「受注確度（%）」をデフォルト設定（例: 提案=40%, 見積提出=60%, 交渉=80%）
4. **クローズ制約**: 「受注」にはamount > 0 と expected_close_date が必須。「失注」には失注理由（lost_reason）が必須

### 売上予測・KPI

- **パイプライン金額** = 各ステージの deal.amount × probability の合計
- **受注率** = 受注数 / (受注数 + 失注数) × 100
- **平均商談期間** = created_at から close_date までの平均日数
- **営業担当別集計**: 担当者ごとの商談数・受注金額・活動数をダッシュボードに表示

### 見積書

- 見積書は商談に紐づく（deal_id 必須）
- 明細行（line_items）: 品名・数量・単価・小計
- 有効期限（valid_until）のデフォルト: 発行日 + 30日
- PDF 出力機能を想定した構造にする

---

## 受入条件テンプレート

Spec Agent が受入条件を書く際、以下を必ず含めること：

- [ ] 商談を新規作成し、ステージ「リード獲得」で保存できる
- [ ] ステージを前進方向に変更できる（戻しは不可）
- [ ] パイプライン一覧で商談をステージ別にカンバン表示できる
- [ ] 受注時に金額・クローズ日が入力されていないとエラーになる
- [ ] 失注時に失注理由が必須
- [ ] 売上予測（パイプライン金額合計）がダッシュボードに表示される

---

## Coder 向け実装指針

- ステージ遷移は `sfa_stages` マスタテーブルの `order` カラムで制御する
- ステージ変更時は変更前後のステージを `sfa_deal_history` に記録する
- カンバンUIは Drag & Drop 対応（shadcn/ui の Sortable またはdnd-kit）
- 金額は `BIGINT`（整数・円単位）で保存。表示時にフォーマット
