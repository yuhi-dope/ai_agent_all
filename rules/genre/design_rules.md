# デザイン（Design）専門ルール

## ドメイン概要

デザインエージェントは、UI/UXデザイン・ブランディング・制作物管理・デザインシステムの運用を管理するシステムである。デザインレビューフローの電子化と制作物のバージョン管理を実現する。

---

## 必須エンティティ

| エンティティ | 説明 | 必須フィールド例 |
|---|---|---|
| **DesignProject（案件）** | デザイン案件 | title, client_or_team, type(Web/アプリ/印刷/ブランド), status, deadline |
| **Deliverable（制作物）** | 成果物 | project_id, name, format(figma/png/pdf/svg), version, file_url, status |
| **ReviewRequest（レビュー依頼）** | デザインレビュー | deliverable_id, requester, reviewers, status, deadline |
| **Feedback（フィードバック）** | レビューコメント | review_id, reviewer, comment, position(JSONB), resolved |
| **DesignToken（デザイントークン）** | デザインシステム定義 | category(color/typography/spacing), name, value, description |

---

## ビジネスルール（Spec Agent / Coder Agent 共通）

### 案件管理

1. **ステータス**: 要件整理 → デザイン中 → レビュー中 → 修正中 → 承認済 → 納品完了
2. **納期管理**: deadline を過ぎた案件は赤色アラート表示
3. **案件種別**: Web デザイン / アプリ UI / 印刷物 / ブランディング / その他

### 制作物バージョン管理

- 同一 deliverable の更新は version をインクリメント（v1, v2, v3...）
- 過去バージョンは閲覧可能だが編集不可
- 最新バージョンのみがレビュー対象

### デザインレビュー

1. **フロー**: レビュー依頼 → レビュアーがフィードバック → デザイナーが修正 → 再レビュー → 承認
2. **フィードバック**: 画像上の位置（座標）にピン留めコメントが可能（position JSONB）
3. **承認条件**: 全レビュアーが「承認」するまでステータスは「レビュー中」

### デザインシステム

- カラーパレット、タイポグラフィ、スペーシングをトークンとして管理
- トークンの変更履歴を保持
- コンポーネントカタログ（将来対応を想定した構造）

---

## 受入条件テンプレート

- [ ] デザイン案件を作成し、種別・納期を設定できる
- [ ] 制作物をアップロードし、バージョン管理される
- [ ] レビュー依頼を作成し、指定レビュアーにフィードバックを求められる
- [ ] フィードバックコメントを登録でき、解決済みマークが付けられる
- [ ] 全レビュアー承認で案件ステータスが「承認済」に遷移する
- [ ] デザイントークン（カラー・フォント・スペーシング）を一覧管理できる

---

## Coder 向け実装指針

- ファイルアップロードは Supabase Storage。サムネイル生成は将来対応として URL 構造のみ用意
- フィードバックの position は `{x: number, y: number, page?: number}` の JSONB
- レビュー承認判定は `design_feedbacks` テーブルで reviewer ごとの最新 action を集計
- デザイントークンは `design_tokens` テーブルに category 別で管理し、JSON エクスポート機能を想定
- 案件一覧はカンバン（ステータス別）とリスト表示の切替を用意
