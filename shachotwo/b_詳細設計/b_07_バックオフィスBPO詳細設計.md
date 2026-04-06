# b_07 バックオフィスBPO詳細設計

> **目的**: 全業界共通のバックオフィス業務を、建設BPO(8本)・製造BPO(8本)と同じ粒度で定義する。
> 各パイプラインの入力・ステップ・マイクロエージェント・出力・承認要否・トリガーを明記し、
> そのまま実装に着手できるレベルの仕様とする。
>
> **料金**: ¥200,000/月（全業界共通、Phase 2+）
> **対象**: 従業員10-300名の中小企業
> **最終更新**: 2026-03-28

---

## 0. 全体構成

### 0.1 パイプライン一覧（7領域 × 24本）

| # | 領域 | パイプライン | Phase | 既存 |
|---|---|---|---|---|
| **経理（8本）** | | | | |
| 1 | 経理 | expense（経費精算） | 1 | ✅ 実装済み |
| 2 | 経理 | invoice_issue（請求書発行） | 2 | 新規 |
| 3 | 経理 | ar_management（売掛管理・入金消込） | 2 | 新規 |
| 4 | 経理 | ap_management（買掛管理・支払処理） | 2 | 新規 |
| 5 | 経理 | bank_reconciliation（銀行照合） | 2 | 新規 |
| 6 | 経理 | journal_entry（仕訳入力） | 2 | 新規 |
| 7 | 経理 | monthly_close（月次決算） | 2 | 新規 |
| 8 | 経理 | tax_filing（税務申告支援） | 3 | 新規 |
| **労務（5本）** | | | | |
| 9 | 労務 | attendance（勤怠分析） | 1 | ✅ 実装済み |
| 10 | 労務 | payroll（給与計算） | 1 | ✅ 実装済み |
| 11 | 労務 | social_insurance（社保届出） | 2 | 新規 |
| 12 | 労務 | year_end_adjustment（年末調整） | 2 | 新規 |
| 13 | 労務 | labor_compliance（労務コンプラ統合） | 2 | 新規 |
| **人事（3本）** | | | | |
| 14 | 人事 | recruitment（採用パイプライン） | 2 | 新規 |
| 15 | 人事 | employee_onboarding（入社手続き） | 2 | 新規 |
| 16 | 人事 | employee_offboarding（退社手続き） | 2 | 新規 |
| **総務（3本）** | | | | |
| 17 | 総務 | contract（契約書分析） | 1 | ✅ 実装済み |
| 18 | 総務 | admin_reminder（届出リマインダー） | 1 | ✅ 実装済み |
| 19 | 総務 | asset_management（備品・固定資産） | 3 | 新規 |
| **調達（2本）** | | | | |
| 20 | 調達 | vendor（仕入先管理） | 1 | ✅ 実装済み |
| 21 | 調達 | purchase_order（発注・検収） | 2 | 新規 |
| **法務（2本）** | | | | |
| 22 | 法務 | compliance_check（コンプラチェック） | 2 | 新規 |
| 23 | 法務 | antisocial_screening（反社チェック） | 2 | 新規 |
| **IT管理（1本）** | | | | |
| 24 | IT | account_lifecycle（アカウント管理） | 3 | 新規 |

### 0.2 Phase別実装順序

```
Phase 1（実装済み）: expense, attendance, payroll, contract, admin_reminder, vendor
Phase 2（次期）:     invoice_issue, ar_management, ap_management, bank_reconciliation,
                     journal_entry, monthly_close, social_insurance, year_end_adjustment,
                     labor_compliance, recruitment, employee_onboarding, employee_offboarding,
                     purchase_order, compliance_check, antisocial_screening
Phase 3（将来）:     tax_filing, asset_management, account_lifecycle
```

### 0.3 必要コネクタ

| コネクタ | 用途 | 既存 |
|---|---|---|
| freee | 会計・請求・経費・給与 | ✅ |
| kintone | 案件管理・マスタ | ✅ |
| Slack | 通知・承認アラート | ✅ |
| CloudSign | 電子契約 | ✅ |
| Gmail | メール送信 | ✅ |
| Google Sheets | データI/O | ✅ |
| SmartHR | 社保手続き・従業員マスタ | 新規 |
| KING OF TIME | 勤怠データ | 新規 |
| Bank API (全銀) | 入出金データ・振込 | 新規 |
| e-Gov | 行政電子申請 | 新規 |
| TDB/TSR | 企業信用調査 | 新規 |

---

## 1. 経理パイプライン（8本）

---

### 1.1 expense（経費精算）✅ 実装済み

**レジストリキー**: `common/expense`
**トリガー**: 手動（従業員が領収書アップロード）/ 月末バッチ
**承認**: 必須（金額問わず）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | ocr | 領収書画像からテキスト抽出 |
| 2 | extractor | 日付/金額/科目/目的/税区分を構造化 |
| 3 | rule_matcher | 経費規程照合（上限額、許可カテゴリ、勘定科目） |
| 4 | calculator | 上限チェック、消費税分離、純額計算 |
| 5 | compliance | インボイス制度チェック（登録番号、適格請求書） |
| 6 | validator | 必須フィールド検証 |

**出力**: ExpenseResult（承認待ちフラグ、コンプラアラート配列）

---

### 1.2 invoice_issue（請求書発行）🆕

**レジストリキー**: `backoffice/invoice_issue`
**トリガー**: スケジュール（毎月末）/ 手動 / パイプライン連鎖（billing完了後）
**承認**: 必須（金額確認は人間の責務）
**コネクタ**: freee（請求書作成API）、Gmail（送付）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 当月の完了案件・納品データを取得（execution_logs / kintone） |
| 2 | extractor | 請求対象を構造化: {取引先, 品目[], 数量, 単価, 税率} |
| 3 | calculator | 請求額計算（小計→消費税→合計、Decimal精度） |
| 4 | compliance | インボイス制度チェック（登録番号、税率区分、端数処理） |
| 5 | pdf_generator | 請求書PDF生成（テンプレートHTML + データ） |
| 6 | saas_writer | freee請求書API → 請求レコード作成 |
| 7 | validator | 必須フィールド検証（請求番号、発行日、支払期限） |

**出力**:
```python
InvoiceIssueResult:
  invoices: [{invoice_number, client_name, amount, tax, total, due_date, pdf_path}]
  total_amount: Decimal
  freee_synced: bool
  compliance_alerts: [str]
```

**スケジュール追加**:
```python
{"cron_expr": "0 9 28-31 * *", "pipeline": "backoffice/invoice_issue", "input_data": {"last_day_only": True}}
```

---

### 1.3 ar_management（売掛管理・入金消込）🆕

**レジストリキー**: `backoffice/ar_management`
**トリガー**: スケジュール（毎日09:00）/ freee webhook（入金通知）
**承認**: 不要（消込は自動、督促は承認必要）
**コネクタ**: freee（入金API）、Bank API（入出金明細）、Gmail（督促メール）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | freee未入金請求書一覧 + 銀行入金明細を取得 |
| 2 | rule_matcher | 入金消込マッチング（金額完全一致→自動、差異あり→フラグ） |
| 3 | calculator | 滞納日数計算、遅延損害金算出（年3%法定利率） |
| 4 | extractor | 年齢分析（aging）: 30日/60日/90日/90日超に分類 |
| 5 | generator | 督促文面生成（段階別: 初回→催告→内容証明→法的措置予告） |
| 6 | message | 督促メール送信 or Slackアラート（初回は自動、2回目以降は承認） |
| 7 | saas_writer | freee消込処理 + revenue_records更新 |

**出力**:
```python
ARManagementResult:
  matched_count: int        # 自動消込件数
  unmatched_count: int      # 手動確認必要件数
  overdue_invoices: [{client, amount, days_overdue, aging_category}]
  dunning_actions: [{client, stage, action_taken}]
  total_outstanding: Decimal
```

**スケジュール追加**:
```python
{"cron_expr": "0 9 * * *", "pipeline": "backoffice/ar_management"}
```

---

### 1.4 ap_management（買掛管理・支払処理）🆕

**レジストリキー**: `backoffice/ap_management`
**トリガー**: スケジュール（毎月25日=支払日前5日）/ 手動
**承認**: 必須（支払実行は人間承認）
**コネクタ**: freee（支払API）、Bank API（振込ファイル生成）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | freee未払買掛金 + 受領済み請求書一覧 |
| 2 | ocr | 受領請求書のOCR（紙の場合） |
| 3 | extractor | 請求内容の構造化（発注書番号、品目、金額） |
| 4 | rule_matcher | 三者照合: 発注書 × 検収記録 × 請求書の整合チェック |
| 5 | calculator | 支払額計算（早期支払割引の適用判定含む） |
| 6 | compliance | インボイス番号検証（適格請求書発行事業者かチェック） |
| 7 | generator | 全銀フォーマット振込ファイル生成 |
| 8 | validator | 支払スケジュール検証（資金繰りとの整合） |

**出力**:
```python
APManagementResult:
  payables: [{vendor, invoice_number, amount, due_date, match_status}]
  three_way_match_ok: int
  three_way_match_ng: int   # 人間確認必要
  transfer_file_path: str   # 全銀フォーマットファイル
  total_payment: Decimal
  early_payment_savings: Decimal
```

---

### 1.5 bank_reconciliation（銀行照合）🆕

**レジストリキー**: `backoffice/bank_reconciliation`
**トリガー**: スケジュール（毎日18:00）/ 月末バッチ
**承認**: 差異あり時のみ承認必要
**コネクタ**: Bank API（入出金明細CSV/API）、freee（帳簿残高）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 銀行入出金明細取得（CSV or API） |
| 2 | saas_reader | freee帳簿上の入出金記録取得 |
| 3 | rule_matcher | 自動マッチング（日付+金額+摘要の組み合わせ） |
| 4 | extractor | 不一致項目の原因分類（タイミング差/二重計上/未記帳/不明） |
| 5 | calculator | 調整後残高の算出 |
| 6 | generator | 銀行勘定調整表（Bank Reconciliation Statement）生成 |
| 7 | validator | 差額ゼロ検証 or 差異レポート |

**出力**:
```python
BankReconciliationResult:
  bank_balance: Decimal
  book_balance: Decimal
  adjusted_balance: Decimal
  auto_matched: int
  unmatched: [{date, amount, description, source, reason}]
  reconciled: bool           # 差額ゼロならTrue
```

---

### 1.6 journal_entry（仕訳入力）🆕

**レジストリキー**: `backoffice/journal_entry`
**トリガー**: 他パイプライン連鎖（expense/invoice/payroll完了後）/ 手動
**承認**: 金額 ≥ ¥100,000 の場合は承認必要
**コネクタ**: freee（仕訳API）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | extractor | 取引内容から仕訳要素を推定: {借方科目, 貸方科目, 金額, 摘要, 税区分} |
| 2 | rule_matcher | 過去仕訳パターン照合（同一取引先+科目の履歴から推定精度向上） |
| 3 | compliance | 勘定科目の妥当性チェック（借貸バランス、税区分整合） |
| 4 | saas_writer | freee仕訳API → 仕訳レコード作成 |
| 5 | validator | 貸借一致検証 |

**出力**:
```python
JournalEntryResult:
  entries: [{debit_account, credit_account, amount, description, tax_category}]
  auto_classified: int       # AI自動分類件数
  manual_review: int         # 人間確認必要件数
  freee_synced: bool
```

**学習ループ**: ユーザーが科目を修正 → 次回同一取引先の推定精度向上

---

### 1.7 monthly_close（月次決算）🆕

**レジストリキー**: `backoffice/monthly_close`
**トリガー**: スケジュール（毎月5営業日目 09:00）
**承認**: 必須（最終確認は経理責任者）
**コネクタ**: freee（試算表API）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | freee試算表（残高試算表）取得 |
| 2 | rule_matcher | 未処理チェック: 未消込入金/未記帳経費/未計上売上 |
| 3 | calculator | 月次P&L計算（売上-原価-販管費=営業利益） |
| 4 | calculator | 前月比・予算比の差異分析 |
| 5 | generator | 月次決算レポート生成（P&L、BS要約、KPI） |
| 6 | compliance | 異常値検出（前月比±30%超の科目をフラグ） |
| 7 | validator | 貸借対照表の貸借一致検証 |

**出力**:
```python
MonthlyCloseResult:
  pnl: {revenue, cogs, gross_profit, sga, operating_profit}
  variance: {vs_prior_month: dict, vs_budget: dict}
  anomalies: [{account, amount, change_pct, reason}]
  unclosed_items: [{type, count, amount}]
  report_pdf_path: str
```

---

### 1.8 tax_filing（税務申告支援）🆕 Phase 3

**レジストリキー**: `backoffice/tax_filing`
**トリガー**: スケジュール（四半期: 消費税 / 年次: 法人税）
**承認**: 必須（税理士レビュー前提）
**コネクタ**: freee、e-Gov（e-Tax連携）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | freee年次決算データ取得 |
| 2 | calculator | 消費税計算（課税売上/仕入税額控除/簡易課税判定） |
| 3 | calculator | 法人税概算（所得800万以下15%/超23.2%） |
| 4 | generator | 申告書ドラフト生成（別表一〜十六の主要項目） |
| 5 | compliance | 青色申告要件チェック、電子帳簿保存法対応確認 |
| 6 | validator | 計算整合性検証 |

**出力**: 税理士レビュー用のドラフト資料。最終申告は税理士が実施。

---

## 2. 労務パイプライン（5本）

---

### 2.1 attendance（勤怠分析）✅ 実装済み

省略（既存パイプライン）。5ステップ: 勤怠読込→残業集計→欠勤チェック→コンプラ→検証。

### 2.2 payroll（給与計算）✅ 実装済み

省略（既存パイプライン）。7ステップ: 勤怠→基本給→残業→控除→コンプラ→明細→検証。

---

### 2.3 social_insurance（社会保険届出）🆕

**レジストリキー**: `backoffice/social_insurance`
**トリガー**: イベント（入社/退社/給与変更）/ スケジュール（算定基礎: 7月、労保年度更新: 6月）
**承認**: 必須（届出前に内容確認）
**コネクタ**: SmartHR（届出API）、e-Gov（電子申請）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | extractor | 届出種別判定: {資格取得/喪失/算定基礎/月額変更/賞与支払/産休育休} |
| 2 | saas_reader | 従業員マスタ + 給与データ取得（SmartHR or freee） |
| 3 | calculator | 標準報酬月額算出（4-6月平均 or 変更時の3ヶ月平均） |
| 4 | calculator | 保険料計算（健保+厚年+雇保、事業主/本人負担按分） |
| 5 | generator | 届出書ドラフト生成（様式に準拠したJSON→PDF） |
| 6 | compliance | 届出期限チェック（取得=5日以内、喪失=5日以内、算定=7/10） |
| 7 | validator | 必須フィールド・計算整合性検証 |

**出力**:
```python
SocialInsuranceResult:
  filing_type: str           # "acquisition" | "loss" | "standard_base" | "monthly_change" | "bonus"
  employees: [{name, filing_data, standard_monthly_pay, premium}]
  filing_deadline: date
  days_until_deadline: int
  draft_pdf_path: str
  egov_ready: bool           # e-Gov電子申請可能か
```

**イベントトリガー追加**:
```python
{"event_type": "employee_joined", "pipeline": "backoffice/social_insurance", "input_data": {"filing_type": "acquisition"}}
{"event_type": "employee_left", "pipeline": "backoffice/social_insurance", "input_data": {"filing_type": "loss"}}
```

---

### 2.4 year_end_adjustment（年末調整）🆕

**レジストリキー**: `backoffice/year_end_adjustment`
**トリガー**: スケジュール（11月1日: 申告書配布 / 12月10日: 計算・還付）
**承認**: 必須
**コネクタ**: freee人事労務（年末調整機能）、SmartHR

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 年間給与データ + 源泉徴収済み税額取得 |
| 2 | extractor | 従業員提出書類から控除情報抽出（保険料控除、住宅ローン、扶養） |
| 3 | calculator | 年税額計算（給与所得控除→基礎控除→各種控除→税率適用） |
| 4 | calculator | 過不足税額算出（年税額 - 源泉徴収済み = 還付/追徴） |
| 5 | generator | 源泉徴収票ドラフト生成 |
| 6 | compliance | 配偶者控除・扶養控除の所得制限チェック |
| 7 | validator | 計算整合性検証 |

**出力**:
```python
YearEndAdjustmentResult:
  employees: [{name, annual_income, total_deductions, tax_due, tax_paid, refund_or_additional}]
  total_refund: Decimal
  total_additional: Decimal
  withholding_slips: [{employee_name, pdf_path}]
```

---

### 2.5 labor_compliance（労務コンプライアンス統合）🆕

**レジストリキー**: `backoffice/labor_compliance`
**トリガー**: スケジュール（毎月1日: 月次チェック / 毎年4月: 年次チェック）
**承認**: アラートのみ（違反検出時はSlack緊急通知）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 勤怠データ + 給与データ + 届出履歴取得 |
| 2 | rule_matcher | 36協定チェック（月45h/年360h、特別条項月100h/年720h） |
| 3 | rule_matcher | 最低賃金チェック（都道府県別、時給換算） |
| 4 | rule_matcher | 有給休暇5日取得義務チェック（年10日付与者全員） |
| 5 | rule_matcher | 届出期限チェック（36協定届/就業規則届/定期健診報告） |
| 6 | calculator | 従業員50名判定（ストレスチェック・産業医・衛生管理者の義務発生） |
| 7 | generator | コンプライアンスレポート生成（違反/リスク/推奨アクション） |
| 8 | message | 違反検出時: Slack緊急通知 + メール |

**出力**:
```python
LaborComplianceResult:
  violations: [{type, employee_or_company, detail, penalty_risk, deadline}]
  warnings: [{type, detail, days_until_risk}]
  headcount_obligations: [{threshold, obligation, status}]  # 50名/100名/300名
  report_pdf_path: str
```

**法定チェック項目**:
| チェック | 罰則 | 頻度 |
|---|---|---|
| 36協定超過 | 6ヶ月懲役 or ¥30万 | 毎月 |
| 有給5日未取得 | ¥30万/人 | 年次 |
| 最低賃金割れ | ¥50万 | 毎月 |
| 就業規則未届 | ¥30万 | 変更時 |
| 定期健康診断未実施 | ¥50万 | 年次 |
| ストレスチェック未実施(50名↑) | 指導勧告 | 年次 |

---

## 3. 人事パイプライン（3本）

---

### 3.1 recruitment（採用パイプライン）🆕

**レジストリキー**: `backoffice/recruitment`
**トリガー**: 手動（求人開始時）/ イベント（応募受信）
**承認**: 面接設定は自動、内定は承認必須

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | generator | 求人票ドラフト生成（職種+条件→JD文面） |
| 2 | extractor | 応募書類から候補者情報を構造化（履歴書/職務経歴書→JSON） |
| 3 | rule_matcher | スクリーニング: 必須条件照合（資格/経験年数/勤務地） |
| 4 | calculator | 候補者スコアリング（条件一致率 + 経験年数重み付け） |
| 5 | generator | 面接質問リスト生成（職種別テンプレ + 候補者固有の深掘り質問） |
| 6 | calendar_booker | 面接日程調整（面接官カレンダー空き検索→候補日送信） |
| 7 | message | 合否連絡メール生成（通過/不通過/内定） |

**出力**:
```python
RecruitmentResult:
  candidates: [{name, score, screening_result, interview_scheduled}]
  jd_draft: str
  interview_questions: [str]
  offers_pending_approval: int
```

---

### 3.2 employee_onboarding（入社手続き）🆕

**レジストリキー**: `backoffice/employee_onboarding`
**トリガー**: イベント（内定承諾）/ 手動
**承認**: 不要（チェックリスト駆動）
**コネクタ**: SmartHR、Google Workspace（アカウント作成）、Slack（チャンネル招待）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | generator | 入社書類セット生成（雇用契約書、身元保証書、秘密保持誓約書、マイナンバー届出） |
| 2 | saas_writer | SmartHR → 従業員マスタ登録 |
| 3 | saas_writer | 各種SaaSアカウント作成（Google/Slack/社内ツール） |
| 4 | generator | 入社オリエンテーション資料生成（会社概要、就業規則要約、部署紹介） |
| 5 | calendar_booker | 初日スケジュール作成（オリエン/部署紹介/ツール設定/ランチ） |
| 6 | message | 入社前メール送信（持ち物リスト、初日の案内、書類提出依頼） |

**連鎖トリガー**: → `backoffice/social_insurance`（資格取得届を自動発火）

**出力**:
```python
OnboardingResult:
  employee_name: str
  documents_generated: [str]
  accounts_created: [str]    # ["google", "slack", "freee", ...]
  orientation_scheduled: bool
  social_insurance_triggered: bool
  checklist: [{item, status, due_date}]
```

---

### 3.3 employee_offboarding（退社手続き）🆕

**レジストリキー**: `backoffice/employee_offboarding`
**トリガー**: イベント（退職届受理）/ 手動
**承認**: 最終給与は承認必要
**コネクタ**: SmartHR、freee、Google Workspace

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | calculator | 最終給与計算（日割り+未消化有給買取+退職金） |
| 2 | generator | 離職票ドラフト生成（離職理由コード、賃金台帳12ヶ月） |
| 3 | generator | 退職証明書、源泉徴収票生成 |
| 4 | saas_writer | 各種SaaSアカウント無効化スケジュール設定（最終出勤日+1） |
| 5 | compliance | 退職手続き期限チェック（社保喪失届5日以内、源泉徴収票1ヶ月以内） |
| 6 | message | 退職者への書類送付案内メール |

**連鎖トリガー**: → `backoffice/social_insurance`（資格喪失届を自動発火）

**出力**:
```python
OffboardingResult:
  employee_name: str
  final_salary: Decimal
  unused_pto_payout: Decimal
  documents: [{type, pdf_path}]
  accounts_to_disable: [{service, disable_date}]
  social_insurance_triggered: bool
  filing_deadlines: [{filing, deadline, days_remaining}]
```

---

## 4. 総務パイプライン（3本）

---

### 4.1 contract（契約書分析）✅ 実装済み

省略。5ステップ: OCR→条項抽出→リスクチェック→差分検出→検証。

### 4.2 admin_reminder（届出リマインダー）✅ 実装済み

省略。3ステップ: 期限スキャン→優先度ソート→リマインダー生成。

---

### 4.3 asset_management（備品・固定資産管理）🆕 Phase 3

**レジストリキー**: `backoffice/asset_management`
**トリガー**: スケジュール（毎月1日: 棚卸確認）/ イベント（購入時）
**承認**: 取得 ≥ ¥100,000 は承認必要

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 固定資産台帳取得（freee or スプレッドシート） |
| 2 | calculator | 減価償却計算（定額法/定率法、耐用年数別） |
| 3 | rule_matcher | 少額資産判定（¥10万未満=即時費用/¥10-20万=一括償却/¥20万超=通常償却） |
| 4 | compliance | 償却資産申告対象チェック（1/31期限） |
| 5 | generator | 固定資産台帳レポート + 償却スケジュール生成 |
| 6 | validator | 帳簿価額と実地棚卸の照合 |

---

## 5. 調達パイプライン（2本）

---

### 5.1 vendor（仕入先管理）✅ 実装済み

省略。4ステップ: 仕入先読込→スコア計算→リスク評価→検証。

---

### 5.2 purchase_order（発注・検収）🆕

**レジストリキー**: `backoffice/purchase_order`
**トリガー**: 手動（発注依頼）/ 在庫閾値割れ（製造業連携）
**承認**: 金額 ≥ ¥50,000 は承認必要
**コネクタ**: kintone（発注管理）、freee（買掛連携）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | extractor | 発注要求から構造化: {品目, 数量, 希望納期, 予算上限} |
| 2 | rule_matcher | 推奨仕入先選定（vendor_scoreベース + 品目カテゴリマッチ） |
| 3 | generator | 発注書ドラフト生成（テンプレート + 仕入先情報 + 品目明細） |
| 4 | pdf_generator | 発注書PDF |
| 5 | saas_writer | kintone発注レコード作成 + freee買掛金予約 |
| 6 | message | 仕入先へ発注メール送信 |
| 7 | validator | 予算超過チェック + 納期整合性 |

**検収フロー（発注後）**:
```python
# 納品後に手動で検収トリガー
input_data = {"po_number": "PO-2026-001", "received_items": [...], "inspection_result": "ok"}
```

| Step | 処理 |
|---|---|
| 1 | 発注書と納品内容の照合（品目/数量/品質） |
| 2 | 差異検出（数量不足/品質不良/誤品） |
| 3 | 検収完了 → AP管理パイプラインへ連鎖（支払処理トリガー） |

---

## 6. 法務パイプライン（2本）

---

### 6.1 compliance_check（コンプライアンスチェック）🆕

**レジストリキー**: `backoffice/compliance_check`
**トリガー**: スケジュール（毎月1日）/ イベント（法改正通知）
**承認**: 不要（レポート生成のみ）

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 全社データ取得（従業員数、売上、業種、許認可） |
| 2 | rule_matcher | 業界許認可の有効期限チェック（建設業許可5年更新等） |
| 3 | rule_matcher | APPI（個人情報保護法）対応状況チェック |
| 4 | rule_matcher | ハラスメント防止措置の実施状況チェック |
| 5 | rule_matcher | 人数閾値義務チェック（50名→ストレスチェック等） |
| 6 | generator | コンプライアンスダッシュボードレポート生成 |
| 7 | message | 期限接近項目のSlackアラート |

---

### 6.2 antisocial_screening（反社チェック）🆕

**レジストリキー**: `backoffice/antisocial_screening`
**トリガー**: イベント（新規取引先登録時）/ 手動
**承認**: ヒットあり時は必ず承認（取引可否判断）
**コネクタ**: TDB/TSR API（企業信用調査）、gBizINFO

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | extractor | 対象企業情報の構造化（法人名、代表者名、所在地） |
| 2 | company_researcher | gBizINFO + TDB/TSRで企業情報取得 |
| 3 | rule_matcher | ネガティブニュース検索（企業名+代表者名で過去記事照合） |
| 4 | rule_matcher | 反社データベース照合（外部API or 内部リスト） |
| 5 | generator | スクリーニングレポート生成（Green/Yellow/Red判定） |
| 6 | validator | 判定結果の根拠検証 |

**出力**:
```python
AntisocialScreeningResult:
  target_company: str
  judgment: "GREEN" | "YELLOW" | "RED"
  risk_factors: [{source, detail, severity}]
  report_pdf_path: str
  requires_manual_review: bool  # YELLOW/REDはTrue
```

---

## 7. IT管理パイプライン（1本）

---

### 7.1 account_lifecycle（アカウントライフサイクル管理）🆕 Phase 3

**レジストリキー**: `backoffice/account_lifecycle`
**トリガー**: 連鎖（onboarding/offboarding完了後）/ スケジュール（月次棚卸し）
**承認**: 削除時は承認必要

| Step | マイクロエージェント | 処理内容 |
|---|---|---|
| 1 | saas_reader | 全SaaSのアカウント一覧取得（IDaaS or 個別API） |
| 2 | saas_reader | 従業員マスタ取得（SmartHR） |
| 3 | rule_matcher | 照合: アカウントあり+従業員なし=孤立アカウント検出 |
| 4 | rule_matcher | 90日未使用アカウント検出（コスト削減対象） |
| 5 | calculator | ライセンスコスト試算（不要アカウント削除時の月次節約額） |
| 6 | generator | アカウント棚卸しレポート生成 |
| 7 | message | 孤立アカウント削除提案をSlack通知 |

---

## 8. スケジュール追加一覧

Phase 2実装時に `BUILTIN_SCHEDULE_TRIGGERS` に追加するトリガー:

```python
# 経理
{"cron_expr": "0 9 * * *",     "pipeline": "backoffice/ar_management",     "description": "売掛管理・入金消込日次"},
{"cron_expr": "0 18 * * *",    "pipeline": "backoffice/bank_reconciliation","description": "銀行照合日次"},
{"cron_expr": "0 9 28-31 * *", "pipeline": "backoffice/invoice_issue",     "description": "請求書発行月末", "input_data": {"last_day_only": True}},
{"cron_expr": "0 9 25 * *",    "pipeline": "backoffice/ap_management",     "description": "買掛支払処理（月末5日前）"},
{"cron_expr": "0 9 5 * *",     "pipeline": "backoffice/monthly_close",     "description": "月次決算（5営業日目）"},

# 労務
{"cron_expr": "0 9 1 * *",     "pipeline": "backoffice/labor_compliance",  "description": "労務コンプラ月次チェック"},
{"cron_expr": "0 9 25 * *",    "pipeline": "common/payroll",               "description": "給与計算月次（25日）"},

# 法務
{"cron_expr": "0 9 1 * *",     "pipeline": "backoffice/compliance_check",  "description": "コンプライアンス月次チェック"},
```

## 9. イベントトリガー追加一覧

```python
{"event_type": "employee_joined",     "pipeline": "backoffice/employee_onboarding"},
{"event_type": "employee_left",       "pipeline": "backoffice/employee_offboarding"},
{"event_type": "salary_changed",      "pipeline": "backoffice/social_insurance", "input_data": {"filing_type": "monthly_change"}},
{"event_type": "vendor_registered",   "pipeline": "backoffice/antisocial_screening"},
{"event_type": "purchase_requested",  "pipeline": "backoffice/purchase_order"},
{"event_type": "invoice_paid",        "pipeline": "backoffice/ar_management"},
{"event_type": "goods_received",      "pipeline": "backoffice/purchase_order", "input_data": {"mode": "inspection"}},
```

## 10. パイプライン連鎖マップ

```
employee_joined
  → employee_onboarding
    → social_insurance (資格取得届)
    → account_lifecycle (アカウント作成)

employee_left
  → employee_offboarding
    → social_insurance (資格喪失届)
    → account_lifecycle (アカウント無効化)
    → payroll (最終給与計算)

invoice_issue (月末)
  → journal_entry (売上仕訳)
  → ar_management (売掛計上)

goods_received
  → purchase_order (検収)
    → ap_management (買掛計上)
      → journal_entry (仕入仕訳)

expense (承認完了)
  → journal_entry (経費仕訳)

payroll (承認完了)
  → journal_entry (給与仕訳)
  → bank_reconciliation (振込後照合)

monthly_close (月初)
  ← ar_management (売掛残高)
  ← ap_management (買掛残高)
  ← bank_reconciliation (現預金残高)
  ← journal_entry (全仕訳)
  → generator (月次レポート)
```
