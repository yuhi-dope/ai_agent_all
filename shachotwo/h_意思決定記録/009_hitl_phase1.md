# ADR-009: Human-in-the-Loop をPhase 1で導入（BPO承認チェックポイント）

## ステータス

**承認** (2026-03-20)

## 日付

2026-03-20

## コンテキスト

現在の設計ではLangGraph（Human-in-the-Loop）はPhase 2+となっており、Phase 1のBPOパイプラインはLLM判断を自動的に実行する。

外部からの指摘:
> 「LangGraph Phase 2は間違い。今すぐ入れろ。HitLがないパイプラインは『AIが勝手に見積書を送った』事故が起きた瞬間に全部終わる」

具体的なリスクシナリオ:
- **建設業見積パイプライン**: LLMがconfidence 0.72で見積書を自動送付 → 単価誤りで受注後に大幅赤字
- **製造業見積パイプライン**: 材料費の単価を古いデータで計算 → 競合より20%安い見積で受注 → 利益なし
- **共通BPO（請求書）**: 金額の桁間違いを検知できずに送付 → 取引先との信頼喪失

現在の実装: `execution_level` で承認要否を判定するロジックはあるが、実際のUIと通知機能が未実装。

## 検討した選択肢

### 選択肢A: Phase 2まで待つ（LangGraph統合時）
- メリット: 実装コストを後回しにできる
- デメリット: パイロット中に「AIが勝手に実行した」事故が起きるとサービス終了リスク

### 選択肢B: LangGraphなしで軽量HitLを今すぐ実装（採用）
- メリット: async/awaitで十分実装可能。LangGraphなしで安全性を担保
- デメリット: 後でLangGraph統合時にリファクタが必要

### 選択肢C: Confidence閾値での自動/手動分岐のみ
- メリット: 既存の`execution_level`ロジックを活用
- デメリット: Confidenceが誤っていた場合の保護がない

## 決定

**選択肢B**: LangGraphなしの軽量HitLをPhase 1に実装する。

### 実装方針

#### 軽量HitLの設計
```
BPOパイプライン実行
        ↓
結果を "pending_approval" 状態でDBに保存
        ↓
[承認待ち画面] ユーザーが内容を確認
        ↓
✅ 承認 → execution_logsにstatus="approved"、外部送信実行
❌ 却下 → status="rejected"、理由を記録
✏️ 修正 → status="modified"、修正内容を記録してから実行
```

#### 対象パイプライン（全件HitL必須）

金額・外部送付が伴う全てのBPOパイプライン:
- `construction/estimation` (見積書)
- `construction/billing` (請求書)
- `manufacturing/estimation` (製造見積)
- 共通BPO: expense/payroll/contract（経費・給与・契約）

金額が伴わない分析系は自動実行OK:
- Q&Aエンジン
- リスク検知・能動提案
- 写真整理・安全書類チェック

#### DB変更
`execution_logs` テーブルに以下カラムを追加:
- `approval_status`: pending / approved / rejected / modified
- `approved_by`: UUID (users)
- `approved_at`: timestamptz
- `rejection_reason`: text
- `original_output`: jsonb (承認前の元データ)
- `modified_output`: jsonb (修正後データ、modifiedの場合)

#### フロントエンド
- BPO実行画面に「承認待ち」タブを追加
- 承認待ちがある場合はダッシュボードにバッジ表示
- 承認/却下/修正の3ボタンUI

### LangGraph移行時の互換性

Phase 2でLangGraphに移行する際は:
1. `approval_status`フィールドはそのまま再利用
2. LangGraph `interrupt()` ノードを承認待ち保存ロジックに置き換えるだけ
3. フロントエンドUIは変更不要

## 影響

- **UX変化**: BPOが「即時実行」から「承認して実行」になる。初回は学習コストあり
- **価値向上**: 「AIが暴走しない安心感」がパイロット企業の信頼に直結
- **実装コスト**: DB migration 1本 + フロントエンドUI改修 + BPOルーター修正
- **LangGraph移行**: Phase 2のリファクタが若干楽になる

## 関連

- ADR-003: LangGraph for agent orchestration（Phase 2統合予定）
- `shachotwo-app/routers/execution.py`
- `shachotwo-app/db/migrations/012_execution_hitl.sql`（新規）
- `shachotwo-app/frontend/src/app/(authenticated)/bpo/page.tsx`
