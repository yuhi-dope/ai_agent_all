# 歯科BPO詳細設計

> **スコープ**: 歯科医院（5-50名）、一般歯科・矯正・インプラント・審美
> **実装時期**: Phase 2+（建設業BPOのPMF確認後）
> **設計の正**: この文書 + `d_00_BPOアーキテクチャ設計.md`

---

## ⚠️ パイロット解禁条件（建設・製造よりも先行展開禁止）

歯科BPOはレセプトデータ（診療報酬明細書）を扱うため、**要配慮個人情報**に該当する医療情報を処理する。
以下3条件を全て満たすまで、パイロット企業への提供を禁止する。

### 条件1: 利用規約・プライバシーポリシーの整備

- [ ] 医療情報（診療報酬データ・レセプト・患者ID）の取扱い条項を追加
- [ ] 要配慮個人情報の取得・利用目的を明示（個人情報保護法第20条2項）
- [ ] 患者情報をLLMの学習に使用しない旨を明記
- [ ] データ保存期間・削除ポリシーを規定（歯科: 5年保存義務に対応）

### 条件2: 3省2ガイドライン準拠の安全管理措置

対象: 「医療情報システムの安全管理に関するガイドライン（厚労省）」「医療情報を取り扱う情報システム・サービスの提供事業者における安全管理ガイドライン（経産省・総務省）」

- [ ] 医療情報の暗号化（転送中・保存中の両方）
- [ ] アクセス制御ログの記録・6ヶ月保存
- [ ] 医療情報を扱うLLM APIのデータ処理地域の確認（日本国内または同等水準国）
- [ ] 外部送信先（Gemini/Claude API）のセキュリティ評価完了
- [ ] 不正アクセス時の患者情報漏洩を検知する監査ログの整備

### 条件3: DPA（データ処理契約）の整備

- [ ] Anthropic / Google との商用DPAの締結（LLM学習不使用の契約上の担保）
- [ ] 歯科医院との委託契約への個人情報保護条項の追加
- [ ] 再委託先（LLM API）の開示義務の履行

### 解禁判断フロー

```
上記3条件チェック完了
    ↓
法務レビュー（社内 or 外部弁護士）
    ↓
歯科医師・医療情報担当者によるテスト利用（クローズドβ）
    ↓
パイロット解禁（最短: PMFゲート通過後 Week 10〜）
```

> **現状 (2026-03-20)**: 建設業・製造業でPMF検証中。歯科は上記条件未充足のため UI上「準備中」グレーアウト。

---

---

## 0. エグゼクティブサマリー

### なぜ歯科BPOか

```
歯科医院数: 約67,000施設（厚労省 医療施設調査）
 → コンビニ（約56,000）より多い。競争が激しい

歯科医院の経営課題:
  ・レセプトの査定率が高い → 売上の数%が消える
  ・予約のキャンセル率30-40% → チェアが空いて機会損失
  ・中断患者の放置 → リコール率50%以下の医院が多い
  ・自費率が低い → 保険診療だけでは利益が出ない
  ・院長が全部やっている → 診療以外に1日2-3時間の事務作業
  → AIで代行 = BPOコア¥25万 + 追加モジュール¥10万/個の価値

DX意欲あり層（5名以上）: 約1.2万施設
SAM: 1.2万 × ¥100,000/月 × 12 = 約144億円/年
```

### 8モジュール全体像

```
┌──────────────────────────────────────────────────────────┐
│             歯科BPO（BPOコア¥250,000 + 追加モジュール¥100,000/個）                   │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ★ Phase A: 最優先                                        │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ① レセプトAI        │  │ ② 予約最適化AI             │  │
│  │ 査定リスク検知       │  │ キャンセル予測+収益最大化  │  │
│  │ ¥5-15万/月の価値    │  │ ¥3-5万/月の価値            │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase B:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ③ リコール管理      │  │ ④ 自費カウンセリング支援    │  │
│  │ 中断患者掘り起こし   │  │ 保険vs自費比較→見積→同意書 │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase C:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ⑤ 院内感染管理      │  │ ⑥ 技工指示管理             │  │
│  │ チェック+監査準備    │  │ 指示書→納期→品質管理      │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase D:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ⑦ 材料・在庫管理    │  │ ⑧ 届出・許認可管理         │  │
│  │ 消耗品の発注最適化   │  │ 保健所届出+期限管理        │  │
│  └────────────────────┘  └────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 1. レセプトAI（★キラーフィーチャー）

### 1.1 問題の本質

```
レセプト（診療報酬明細書）の現状:
  1. 毎月10日までに前月分を社保・国保に提出
  2. 月末にレセコンから出力 → 院長 or 事務長がチェック
  3. チェック項目:
     - 病名と処置の整合性（根管治療なのに歯髄炎の病名がない等）
     - 算定ルールの遵守（同月に算定不可の組み合わせ等）
     - 記載漏れ（部位、回数、点数）
     - 返戻・査定パターンの回避
  4. 問題のあるレセプトを修正して再提出

  → 院長が月末の2-3日をレセプトチェックに費やす
  → チェック漏れ → 査定（減額）で年間数十万〜数百万の損失
  → 査定率0.5%の改善で年間30-100万の収益改善
  → レセプト点検の外注: 月3-10万円
```

### 1.2 レセプトAIの3段階アプローチ

```
■ Level 1: ルールベースチェック（最初に実装）
  入力: レセコン（Dentis/ORCA等）から出力されたレセプトデータ
  処理: 保険算定ルールDB × レセプトデータ → 不整合検出
  出力: チェックレポート（エラー/警告/情報の3段階）

  チェック項目:
    - 病名漏れ（処置に対応する病名がない）
    - 算定不可の組み合わせ（同月併算定不可等）
    - 回数超過（算定上限を超えている）
    - 部位不整合（上顎の病名なのに下顎の処置）
    - 算定点数の誤り

  精度: 60-70%（一般的なエラーを検出）

■ Level 2: AI学習型チェック（3ヶ月後）
  入力: Level 1 + 過去の査定・返戻データ
  処理:
    ① 自院の過去の査定パターンを学習
    ② 「この組み合わせは過去に査定された」→ 警告
    ③ 審査機関ごとの傾向分析（社保は厳しい、国保は通りやすい等）
  出力: リスクスコア付きチェックレポート

  精度: 80-90%

■ Level 3: ネットワーク効果（Phase 3+）
  - N院の匿名化査定データから審査傾向を学習
  - 「全国の歯科医院で、この算定パターンの査定率は X%」
  - 地域別・審査機関別の傾向分析
```

### 1.3 データモデル

```sql
-- 患者マスタ（※要配慮個人情報。暗号化+厳格なアクセス制御）
CREATE TABLE dental_patients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_number TEXT NOT NULL,              -- 患者番号
  last_name TEXT NOT NULL,
  first_name TEXT NOT NULL,
  last_name_kana TEXT,
  first_name_kana TEXT,
  birth_date DATE,
  gender TEXT,                               -- male / female
  phone TEXT,
  email TEXT,
  address TEXT,
  insurance_type TEXT,                       -- social / national / elderly / self_pay
  insurance_number TEXT,
  first_visit_date DATE,
  last_visit_date DATE,
  next_appointment DATE,
  recall_interval_months INTEGER DEFAULT 3,  -- リコール間隔
  notes TEXT,
  status TEXT DEFAULT 'active',              -- active / inactive / transferred
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, patient_number)
);

-- レセプトデータ（レセコンから取り込み）
CREATE TABLE dental_receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_id UUID NOT NULL REFERENCES dental_patients(id),
  billing_year INTEGER NOT NULL,
  billing_month INTEGER NOT NULL,
  insurance_type TEXT NOT NULL,               -- social / national
  diagnoses JSONB NOT NULL,                   -- [{code, name, start_date, is_main}]
  procedures JSONB NOT NULL,                  -- [{code, name, date, tooth, points, quantity}]
  total_points INTEGER NOT NULL,              -- 合計点数
  patient_burden INTEGER,                     -- 患者負担額
  status TEXT DEFAULT 'draft',                -- draft / checked / submitted / assessed / returned
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, patient_id, billing_year, billing_month)
);

-- レセプトチェック結果
CREATE TABLE dental_receipt_checks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  receipt_id UUID NOT NULL REFERENCES dental_receipts(id),
  company_id UUID NOT NULL REFERENCES companies(id),
  check_type TEXT NOT NULL,                   -- rule_based / ai_predicted
  severity TEXT NOT NULL,                     -- error / warning / info
  rule_code TEXT,                              -- チェックルールコード
  message TEXT NOT NULL,                       -- エラーメッセージ
  detail JSONB,                                -- 詳細情報（対象病名、処置等）
  resolved BOOLEAN DEFAULT false,
  resolved_by UUID REFERENCES users(id),
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 査定・返戻履歴（学習用）
CREATE TABLE dental_assessment_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  receipt_id UUID REFERENCES dental_receipts(id),
  assessment_type TEXT NOT NULL,               -- assessment（査定）/ return（返戻）
  billing_year INTEGER NOT NULL,
  billing_month INTEGER NOT NULL,
  reason_code TEXT,
  reason_detail TEXT,
  original_points INTEGER,
  assessed_points INTEGER,                     -- 査定後点数
  reduction_amount INTEGER,                    -- 減額
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 保険算定ルールマスタ（全院共通）
CREATE TABLE dental_billing_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_code TEXT NOT NULL UNIQUE,
  rule_name TEXT NOT NULL,
  category TEXT NOT NULL,                      -- diagnosis / procedure / combination / frequency / tooth
  condition JSONB NOT NULL,                    -- ルール条件（JSON Logic形式）
  severity TEXT NOT NULL DEFAULT 'warning',
  message_template TEXT NOT NULL,
  effective_from DATE,
  effective_until DATE,
  source TEXT,                                 -- 算定基準の出典
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS（dental_billing_rules以外は全てRLS）
ALTER TABLE dental_patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_receipt_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_assessment_history ENABLE ROW LEVEL SECURITY;
```

### 1.4 レセプトAIパイプライン

```python
# workers/bpo/dental/receipt_checker.py

class ReceiptChecker:
    """
    レセプトAIチェックエンジン

    Step 1: レセコンからデータ取り込み（CSV/API）
    Step 2: ルールベースチェック（算定ルールDB照合）
    Step 3: AI予測チェック（過去査定パターンから予測）
    Step 4: チェックレポート生成
    """

    async def import_receipts(
        self,
        company_id: str,
        file: UploadFile,           # レセコン出力CSV
        format: str = "dentis",     # dentis / orca / other
    ) -> ImportResult:
        """レセプトデータ取り込み"""

    async def check_receipts(
        self,
        company_id: str,
        billing_year: int,
        billing_month: int,
    ) -> ReceiptCheckReport:
        """
        レセプト一括チェック

        1. 全レセプトを取得
        2. 各レセプトに対してルールベースチェック実行
        3. AI予測チェック実行（過去査定データがある場合）
        4. 結果をseverity順にソートして返却
        """

    async def rule_based_check(
        self,
        receipt: DentalReceipt,
    ) -> list[CheckResult]:
        """
        ルールベースチェック

        チェック例:
        - 病名「C」（う蝕）がないのにCR充填を算定 → エラー
        - 同一歯に対してPulp+Pulcの併算定 → エラー
        - SRP算定回数が歯数/4を超過 → 警告
        - 歯周基本検査なしで歯周治療を算定 → エラー
        - 初診算定後3ヶ月以内に再度初診 → 警告
        """

    async def ai_prediction_check(
        self,
        receipt: DentalReceipt,
    ) -> list[CheckResult]:
        """
        AI予測チェック（過去の査定パターンから予測）

        - 自院の過去査定パターンとのマッチング
        - 類似レセプトの査定率計算
        - リスクスコア算出
        """

    async def generate_check_report(
        self,
        company_id: str,
        billing_year: int,
        billing_month: int,
    ) -> bytes:
        """チェックレポートPDF生成"""

    async def learn_from_assessment(
        self,
        company_id: str,
        assessments: list[AssessmentInput],
    ) -> None:
        """査定結果からの学習"""
```

### 1.5 UI設計

```
■ レセプトチェック: /bpo/dental/receipts
  - 月別レセプト一覧（件数、点数合計、チェック状態）
  - 「チェック実行」ボタン → AI分析中... → 結果表示
  - エラー/警告別にフィルタ
  - 各レセプトの詳細（病名×処置の対応表、エラー箇所ハイライト）

■ 査定分析: /bpo/dental/receipts/analysis
  - 月別査定率トレンド
  - 査定パターン分析（多い査定理由TOP10）
  - 改善提案
```

---

## 2. 予約最適化AI

### 2.1 問題の本質

```
歯科医院の予約管理:
  - キャンセル率30-40%（業界平均）
  - 無断キャンセル（ドタキャン）で空きチェアが発生 → 機会損失
  - 自費治療の長時間枠を確保しにくい
  - 急患の対応で予約がずれる
  - チェアタイム（1枠の時間）の最適化ができていない

  チェア1台の年間売上ポテンシャル:
    8時間/日 × 250日 × ¥5,000/30分 = ¥2,000万/年
    稼働率70% → ¥1,400万、80% → ¥1,600万
    → 稼働率10%UP = ¥200万/年/チェアの増収
```

### 2.2 データモデル

```sql
-- チェアマスタ
CREATE TABLE dental_chairs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,                        -- チェア名（ユニット1、ユニット2等）
  chair_type TEXT DEFAULT 'general',         -- general / surgical / pediatric
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 予約
CREATE TABLE dental_appointments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_id UUID NOT NULL REFERENCES dental_patients(id),
  chair_id UUID REFERENCES dental_chairs(id),
  appointment_date DATE NOT NULL,
  start_time TIME NOT NULL,
  end_time TIME NOT NULL,
  duration_minutes INTEGER NOT NULL,
  treatment_type TEXT NOT NULL,               -- checkup / cleaning / filling / root_canal / extraction / prosthetic / ortho / implant / self_pay_other
  is_self_pay BOOLEAN DEFAULT false,
  doctor_name TEXT,
  status TEXT DEFAULT 'scheduled',            -- scheduled / confirmed / arrived / in_treatment / completed / cancelled / no_show
  cancel_reason TEXT,
  cancelled_at TIMESTAMPTZ,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_chairs ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_appointments ENABLE ROW LEVEL SECURITY;
```

### 2.3 予約最適化エンジン

```python
# workers/bpo/dental/appointment.py

class AppointmentOptimizer:
    """
    予約最適化AI
    - キャンセル予測（患者属性×過去履歴→キャンセル確率）
    - 空き枠アラート（直前キャンセル時にウェイティングリストに通知）
    - チェア稼働率分析
    - 収益最大化スケジューリング（自費枠の最適配置）
    """

    async def predict_cancellation(
        self,
        appointment_id: str,
    ) -> CancellationPrediction:
        """
        キャンセル予測
        特徴量: 曜日、時間帯、患者の過去キャンセル率、治療内容、天気
        → キャンセル確率を算出
        → 高リスク患者には前日リマインド強化
        """

    async def suggest_overbooking(
        self,
        company_id: str,
        date: date,
    ) -> list[OverbookingSuggestion]:
        """
        オーバーブッキング提案
        キャンセル率が高い枠にダブルブッキング候補を提案
        """

    async def get_chair_utilization(
        self,
        company_id: str,
        start_date: date,
        end_date: date,
    ) -> ChairUtilization:
        """チェア稼働率レポート"""

    async def optimize_self_pay_slots(
        self,
        company_id: str,
    ) -> ScheduleOptimization:
        """
        自費診療枠の最適配置
        - 自費は長時間枠（60-90分）が必要
        - キャンセルリスクが低い時間帯に配置
        - 院長のスケジュールと連動
        """
```

---

## 3. リコール管理

### 3.1 データモデル

```sql
-- リコールスケジュール
CREATE TABLE dental_recall_schedules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_id UUID NOT NULL REFERENCES dental_patients(id),
  recall_type TEXT NOT NULL,                  -- periodic_checkup / cleaning / ortho_check / implant_maintenance
  interval_months INTEGER NOT NULL DEFAULT 3,
  last_visit_date DATE,
  next_recall_date DATE NOT NULL,
  status TEXT DEFAULT 'pending',              -- pending / notified / booked / completed / lapsed
  notification_count INTEGER DEFAULT 0,       -- 通知回数
  last_notified_at TIMESTAMPTZ,
  channel TEXT,                               -- line / sms / email / postcard
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- リコール通知ログ
CREATE TABLE dental_recall_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  recall_id UUID NOT NULL REFERENCES dental_recall_schedules(id),
  company_id UUID NOT NULL REFERENCES companies(id),
  sent_at TIMESTAMPTZ NOT NULL,
  channel TEXT NOT NULL,
  message TEXT,
  status TEXT NOT NULL,                       -- sent / delivered / opened / clicked / bounced
  result TEXT,                                -- booked / no_response / declined
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_recall_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_recall_logs ENABLE ROW LEVEL SECURITY;
```

### 3.2 リコール管理エンジン

```python
# workers/bpo/dental/recall.py

class RecallManager:
    """
    リコール管理AI
    - リコール対象者の自動抽出
    - パーソナライズされたリマインドメッセージ生成
    - 中断患者の掘り起こし（最終来院から6ヶ月以上）
    - リコール率分析
    - マルチチャネル送信（LINE/SMS/メール/はがき）
    """

    async def get_recall_targets(
        self,
        company_id: str,
        target_date: date = None,
    ) -> list[RecallTarget]:
        """リコール対象者を抽出"""

    async def generate_recall_message(
        self,
        patient_id: str,
        channel: str,
    ) -> RecallMessage:
        """
        パーソナライズメッセージ生成
        「○○さん、前回の検診から3ヶ月が経ちました。
         歯周病の予防には定期的なクリーニングが大切です。」
        """

    async def find_lapsed_patients(
        self,
        company_id: str,
        months_since_last_visit: int = 6,
    ) -> list[LapsedPatient]:
        """中断患者の検出"""

    async def get_recall_analytics(
        self,
        company_id: str,
    ) -> RecallAnalytics:
        """リコール率分析（月別、チャネル別、治療内容別）"""
```

---

## 4. 自費カウンセリング支援

### 4.1 データモデル

```sql
-- 自費メニュー
CREATE TABLE dental_self_pay_menu (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  category TEXT NOT NULL,                     -- crown / inlay / implant / ortho / whitening / other
  name TEXT NOT NULL,                         -- メニュー名（セラミッククラウン等）
  description TEXT,
  price BIGINT NOT NULL,                      -- 税込価格
  insurance_alternative TEXT,                  -- 保険の代替治療名
  insurance_price_approx INTEGER,             -- 保険治療の概算費用
  advantages JSONB,                           -- 自費のメリット [{point}]
  warranty_years INTEGER,                      -- 保証年数
  treatment_time_minutes INTEGER,              -- 治療時間（分）
  visits_required INTEGER,                     -- 必要来院回数
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- カウンセリング記録
CREATE TABLE dental_counseling_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_id UUID NOT NULL REFERENCES dental_patients(id),
  counseling_date DATE NOT NULL,
  treatment_plan JSONB NOT NULL,               -- [{tooth, treatment, option_insurance, option_self_pay}]
  presented_options JSONB,                     -- 提示した選択肢
  patient_choice TEXT,                         -- insurance / self_pay / undecided
  chosen_menu_id UUID REFERENCES dental_self_pay_menu(id),
  estimate_amount BIGINT,
  consent_signed BOOLEAN DEFAULT false,
  consent_url TEXT,                             -- 同意書PDF URL
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_self_pay_menu ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_counseling_records ENABLE ROW LEVEL SECURITY;
```

### 4.2 カウンセリング支援エンジン

```python
# workers/bpo/dental/counseling.py

class CounselingSupport:
    """
    自費カウンセリング支援
    - 保険vs自費の比較資料を自動生成
    - 見積書の作成
    - 同意書テンプレート生成
    - 自費率分析
    """

    async def generate_comparison(
        self,
        patient_id: str,
        treatment_plan: list[TreatmentOption],
    ) -> ComparisonDocument:
        """保険vs自費の比較資料PDF生成"""

    async def generate_estimate(
        self,
        patient_id: str,
        selected_items: list[str],
    ) -> EstimateDocument:
        """見積書生成"""

    async def get_self_pay_analytics(
        self,
        company_id: str,
    ) -> SelfPayAnalytics:
        """自費率分析（月別、カテゴリ別、ドクター別）"""
```

---

## 5. 院内感染管理

### 5.1 データモデル

```sql
-- 感染管理チェックリスト
CREATE TABLE dental_infection_checklists (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  check_date DATE NOT NULL,
  check_type TEXT NOT NULL,                    -- daily / weekly / monthly
  items JSONB NOT NULL,                        -- [{item, checked, notes}]
  checked_by TEXT NOT NULL,
  all_passed BOOLEAN,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- BI（生物学的インジケータ）テスト記録
CREATE TABLE dental_bi_tests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  test_date DATE NOT NULL,
  autoclave_name TEXT NOT NULL,                -- 滅菌器名
  result TEXT NOT NULL,                        -- pass / fail
  lot_number TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_infection_checklists ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_bi_tests ENABLE ROW LEVEL SECURITY;
```

### 5.2 感染管理エンジン

```python
# workers/bpo/dental/infection_control.py

class InfectionControlManager:
    """
    院内感染管理
    - デジタルチェックリスト（日次/週次/月次）
    - 未実施アラート
    - BIテスト記録管理
    - 保健所立入検査準備資料の自動生成
    """

    async def get_todays_checklist(
        self,
        company_id: str,
    ) -> InfectionChecklist:
        """本日のチェックリスト取得"""

    async def generate_audit_report(
        self,
        company_id: str,
        period_months: int = 12,
    ) -> bytes:
        """保健所監査準備レポート生成"""
```

---

## 6. 技工指示管理

### 6.1 データモデル

```sql
-- 技工所マスタ
CREATE TABLE dental_labs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  contact_person TEXT,
  phone TEXT,
  email TEXT,
  specialties TEXT[],                          -- crown / denture / implant / ortho
  average_delivery_days INTEGER,               -- 平均納期（日）
  evaluation JSONB DEFAULT '{}',               -- {quality, delivery, communication, overall}
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 技工指示書
CREATE TABLE dental_lab_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  patient_id UUID NOT NULL REFERENCES dental_patients(id),
  lab_id UUID NOT NULL REFERENCES dental_labs(id),
  order_date DATE NOT NULL,
  due_date DATE NOT NULL,                      -- 希望納期
  prosthetic_type TEXT NOT NULL,               -- crown / bridge / denture / inlay / onlay / implant_abutment
  material TEXT NOT NULL,                       -- 材質（e.max, ジルコニア, 金属等）
  shade TEXT,                                   -- シェード（A2, A3等）
  tooth_number TEXT NOT NULL,                   -- 部位
  instructions JSONB,                           -- 技工指示詳細
  is_self_pay BOOLEAN DEFAULT false,
  price BIGINT,
  status TEXT DEFAULT 'ordered',                -- ordered / in_progress / shipped / received / set / redo
  received_date DATE,
  quality_check TEXT,                           -- pass / adjustment_needed / redo
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_labs ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_lab_orders ENABLE ROW LEVEL SECURITY;
```

### 6.2 技工指示管理エンジン

```python
# workers/bpo/dental/lab_orders.py

class LabOrderManager:
    """
    技工指示管理
    - 技工指示書の自動生成（治療計画から）
    - 納期管理カレンダー
    - 品質フィードバック管理
    - 技工所評価
    """

    async def generate_lab_order(
        self,
        patient_id: str,
        treatment_details: LabOrderInput,
    ) -> LabOrder:
        """技工指示書自動生成"""

    async def get_delivery_calendar(
        self,
        company_id: str,
        start_date: date,
        end_date: date,
    ) -> list[LabDelivery]:
        """納期カレンダー"""

    async def get_lab_performance(
        self,
        lab_id: str,
    ) -> LabPerformance:
        """技工所パフォーマンスレポート"""
```

---

## 7. 材料・在庫管理

### 7.1 データモデル

```sql
-- 材料マスタ
CREATE TABLE dental_materials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  material_code TEXT,
  name TEXT NOT NULL,
  category TEXT NOT NULL,                      -- composite / cement / impression / anesthetic / disposable / other
  unit TEXT NOT NULL,                           -- 本, 箱, 本, mL 等
  unit_price DECIMAL(10,0),
  supplier TEXT,                                -- ディーラー名
  reorder_point DECIMAL(8,1),
  reorder_quantity DECIMAL(8,1),
  expiry_alert_days INTEGER DEFAULT 90,        -- 使用期限アラート（日前）
  current_stock DECIMAL(8,1) DEFAULT 0,
  status TEXT DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 在庫ログ
CREATE TABLE dental_inventory_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  material_id UUID NOT NULL REFERENCES dental_materials(id),
  company_id UUID NOT NULL REFERENCES companies(id),
  transaction_type TEXT NOT NULL,               -- receive / use / adjust / dispose
  quantity DECIMAL(8,1) NOT NULL,
  lot_number TEXT,
  expiry_date DATE,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_inventory_logs ENABLE ROW LEVEL SECURITY;
```

### 7.2 材料在庫管理エンジン

```python
# workers/bpo/dental/dental_inventory.py

class DentalInventoryManager:
    """
    材料・在庫管理
    - 発注点アラート
    - 使用期限管理
    - 消費量分析 → 発注量最適化
    - 棚卸サポート
    """

    async def check_reorder_alerts(
        self,
        company_id: str,
    ) -> list[ReorderAlert]:
        """発注点割れの材料を検出"""

    async def check_expiry_alerts(
        self,
        company_id: str,
    ) -> list[ExpiryAlert]:
        """使用期限が近い材料を検出"""

    async def generate_order_list(
        self,
        company_id: str,
    ) -> OrderList:
        """ディーラー別発注リスト生成"""
```

---

## 8. 届出・許認可管理

### 8.1 データモデル

```sql
-- 歯科固有の届出・許認可
CREATE TABLE dental_permits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  permit_type TEXT NOT NULL,                   -- health_center / xray_area / waste / fire / facility_standard
  permit_name TEXT NOT NULL,
  details JSONB DEFAULT '{}',
  expiry_date DATE,
  renewal_lead_days INTEGER DEFAULT 90,
  status TEXT DEFAULT 'active',
  next_action TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 施設基準届出（保険点数に影響）
CREATE TABLE dental_facility_standards (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  standard_code TEXT NOT NULL,                  -- か強診、外来環、歯援診 等
  standard_name TEXT NOT NULL,
  is_certified BOOLEAN DEFAULT false,
  certified_date DATE,
  requirements JSONB,                           -- 要件チェックリスト
  expiry_date DATE,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE dental_permits ENABLE ROW LEVEL SECURITY;
ALTER TABLE dental_facility_standards ENABLE ROW LEVEL SECURITY;
```

### 8.2 届出管理エンジン

```python
# workers/bpo/dental/dental_permits.py

class DentalPermitManager:
    """
    届出・許認可管理
    - 施設基準の要件チェック（か強診、外来環等）
    - 更新期限管理
    - 届出書類テンプレート生成
    """

    async def check_facility_requirements(
        self,
        company_id: str,
        standard_code: str,
    ) -> FacilityRequirementCheck:
        """
        施設基準の要件充足チェック
        例: か強診 → 歯科衛生士1名以上、AED設置、
            訪問診療実績、研修受講、偶発症対応体制 等
        """

    async def get_expiring_permits(
        self,
        company_id: str,
    ) -> list[ExpiringPermit]:
        """期限が近い届出を検出"""
```

---

## 9. ディレクトリ構成

```
workers/bpo/dental/
├── __init__.py
├── receipt_checker.py       # ① レセプトAI
├── appointment.py           # ② 予約最適化
├── recall.py                # ③ リコール管理
├── counseling.py            # ④ 自費カウンセリング
├── infection_control.py     # ⑤ 院内感染管理
├── lab_orders.py            # ⑥ 技工指示管理
├── dental_inventory.py      # ⑦ 材料在庫管理
├── dental_permits.py        # ⑧ 届出・許認可
└── models.py                # Pydanticモデル

routers/bpo/dental.py         # 全エンドポイント

frontend/src/app/(authenticated)/bpo/dental/
├── receipts/                # レセプト
├── appointments/            # 予約
├── recall/                  # リコール
├── counseling/              # 自費カウンセリング
├── infection/               # 感染管理
├── lab-orders/              # 技工指示
├── inventory/               # 材料在庫
└── permits/                 # 届出・許認可

db/migrations/
└── 009_bpo_dental.sql
```

---

## 10. APIエンドポイント

```
# 患者
POST   /api/bpo/dental/patients                          # 患者登録
GET    /api/bpo/dental/patients                          # 患者一覧
GET    /api/bpo/dental/patients/{id}                     # 患者詳細
POST   /api/bpo/dental/patients/import                   # CSV一括取り込み

# レセプト
POST   /api/bpo/dental/receipts/import                   # レセコンデータ取り込み
POST   /api/bpo/dental/receipts/check                    # レセプトチェック実行
GET    /api/bpo/dental/receipts                          # レセプト一覧
GET    /api/bpo/dental/receipts/{id}                     # レセプト詳細+チェック結果
GET    /api/bpo/dental/receipts/analysis                 # 査定分析
POST   /api/bpo/dental/receipts/assessment               # 査定結果登録（学習用）

# 予約
POST   /api/bpo/dental/appointments                      # 予約登録
GET    /api/bpo/dental/appointments                      # 予約一覧
PATCH  /api/bpo/dental/appointments/{id}                 # 予約変更
GET    /api/bpo/dental/appointments/utilization           # チェア稼働率
GET    /api/bpo/dental/appointments/cancel-prediction     # キャンセル予測

# リコール
GET    /api/bpo/dental/recall/targets                    # リコール対象者
POST   /api/bpo/dental/recall/send                       # リマインド送信
GET    /api/bpo/dental/recall/lapsed                     # 中断患者
GET    /api/bpo/dental/recall/analytics                  # リコール率分析

# 自費カウンセリング
GET    /api/bpo/dental/self-pay/menu                     # 自費メニュー一覧
POST   /api/bpo/dental/self-pay/menu                     # メニュー登録
POST   /api/bpo/dental/counseling/comparison              # 保険vs自費比較資料生成
POST   /api/bpo/dental/counseling/estimate                # 見積書生成
GET    /api/bpo/dental/self-pay/analytics                # 自費率分析

# 感染管理
GET    /api/bpo/dental/infection/checklist                # 本日のチェックリスト
POST   /api/bpo/dental/infection/checklist                # チェック結果登録
POST   /api/bpo/dental/infection/bi-test                  # BIテスト記録
GET    /api/bpo/dental/infection/audit-report              # 監査準備レポート

# 技工指示
POST   /api/bpo/dental/lab-orders                        # 技工指示書作成
GET    /api/bpo/dental/lab-orders                        # 技工指示一覧
GET    /api/bpo/dental/lab-orders/calendar                # 納期カレンダー
GET    /api/bpo/dental/labs                              # 技工所一覧
GET    /api/bpo/dental/labs/{id}/performance              # 技工所評価

# 材料在庫
GET    /api/bpo/dental/materials                         # 材料一覧
POST   /api/bpo/dental/materials                         # 材料登録
GET    /api/bpo/dental/inventory/alerts                   # 発注・期限アラート
POST   /api/bpo/dental/inventory/order-list               # 発注リスト生成

# 届出・許認可
GET    /api/bpo/dental/permits                           # 届出一覧
POST   /api/bpo/dental/permits                           # 届出登録
GET    /api/bpo/dental/facility-standards                 # 施設基準一覧
POST   /api/bpo/dental/facility-standards/check           # 要件チェック
```

---

## 11. SaaS連携戦略

```
Phase 1: Dentis / ORCA（レセコン）→ レセプトデータ取り込み（CSV）
Phase 2: LINE公式アカウント → リコール・リマインド送信
Phase 3: ジニー / アポデント → 予約データ連携
Phase 4: freee/MF → 会計連携（自費売上管理）
Phase 5: 3Shape / iTero → デジタル印象データ連携（Phase 3+）
```

---

## 12. セキュリティ（歯科固有）

```
■ 要配慮個人情報
  歯科の患者データは「要配慮個人情報」（個人情報保護法 第2条3項）
  → 取得時に本人同意が必須
  → 通常の個人情報より厳格な管理が必要

■ 3省2ガイドライン準拠
  医療情報システムの安全管理に関するガイドライン（厚労省+経産省+総務省）
  → アクセスログの記録
  → 通信の暗号化（TLS 1.2以上）
  → バックアップ（3世代以上）
  → 端末管理（紛失時のリモートワイプ）

■ 実装対応
  - dental_patients テーブルは暗号化対象（Supabase native → Phase 2で AES-256-GCM）
  - 全アクセスを audit_logs に記録
  - 患者データのエクスポートには管理者承認が必要
  - 同意取得フロー（consent_records テーブル連携）
```

---

## 13. 並列開発プラン

```
Phase A（2週間）: レセプトAI + 予約最適化 — 4エージェント
Phase B（1週間）: リコール + 自費カウンセリング — 3エージェント
Phase C（1週間）: 感染管理 + 技工指示 — 3エージェント
Phase D（1週間）: 材料在庫 + 届出管理 — 3エージェント
```

---

## 14. リスクと対策

| リスク | 対策 |
|---|---|
| レセコン連携の壁（API未公開が多い） | CSV取り込みを基本。Dentis/ORCAの出力形式に対応 |
| 算定ルールの複雑さ・改定頻度 | ルールをDB化し、改定時にマスタ更新。LLMで改定内容を自動解析 |
| 要配慮個人情報の管理 | 3省2ガイドライン準拠。暗号化+監査ログ+同意管理 |
| 歯科医院のIT リテラシー | 極力シンプルなUI。LINE連携でスマホから操作可能に |
| 競合（ジニー、アポデント等） | 予約・リコール単体ではなく「全部入りBPOパック」で差別化 |
| 自費提案への抵抗感 | 「無理な勧誘」ではなく「情報提供」の姿勢。比較資料の中立性を担保 |

---

## 15. KPI・成功基準

### 完了基準
- [ ] レセプトチェックが動作し、査定リスクを検出できる
- [ ] 予約管理でキャンセル率が可視化される
- [ ] リコールリマインドがLINEで送信できる
- [ ] パイロット3院が実業務で使用開始

### PMF指標
- [ ] 査定率 0.3%以上改善（金額ベース）
- [ ] リコール率 10%以上改善
- [ ] 院長の事務作業時間 30%以上削減
- [ ] NPS ≥ 30
- [ ] 「なくなったら困る」≥ 60%
