# 製造業BPO詳細設計

> **スコープ**: 中小製造業（10-300名）、金属加工・機械・食品・化学・電子部品
> **実装時期**: Phase 2+（建設業BPOのPMF確認後）
> **設計の正**: この文書 + `d_00_BPOアーキテクチャ設計.md`

---

## 0. エグゼクティブサマリー

### なぜ製造業BPOか

```
製造業の中小企業（10-300名）: 約5.2万社（経産省 工業統計調査）
DX意欲あり層: 約15-20% = 約8,000-10,000社

製造業の中小企業は：
  ・見積に時間がかかりすぎて受注機会を逸失している
  ・生産計画がExcel職人の頭の中にしかない
  ・品質データは取っているが分析できていない
  ・在庫が多すぎるか足りないかの二択
  ・ISOの文書管理だけで専任1人分の工数
  → 全部AIで代行できる = BPO月額30-50万の価値
```

### 8モジュール全体像

```
┌──────────────────────────────────────────────────────────┐
│             製造業BPOパック（月額30-50万円）                 │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ★ Phase A: 最優先                                        │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ① 見積AI            │  │ ② 生産計画AI               │  │
│  │ 図面→工程→見積書    │  │ 受注→山積み→工程表        │  │
│  │ ¥10-30万/月の価値    │  │ ¥5-10万/月の価値           │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase B:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ③ 品質管理          │  │ ④ 在庫最適化               │  │
│  │ SPC+不良予測        │  │ 需要予測→発注提案          │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase C:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ⑤ SOP管理           │  │ ⑥ 設備保全                 │  │
│  │ 手順書AI作成・改訂   │  │ 保全計画+故障予測          │  │
│  └────────────────────┘  └────────────────────────────┘  │
│                                                          │
│  Phase D:                                                 │
│  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │ ⑦ 仕入管理          │  │ ⑧ ISO文書管理              │  │
│  │ BOM連動→発注→支払   │  │ 9001/14001文書体系         │  │
│  └────────────────────┘  └────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 1. 見積AI（★キラーフィーチャー）

### 1.1 問題の本質

```
製造業の見積の現状:
  1. 顧客から図面（PDF/DXF/STEP）+ 仕様書を受け取る
  2. 図面を読んで加工工程を推定する
     - 材料切断 → 旋盤加工 → MC加工 → 研磨 → 表面処理 → 検査
  3. 各工程の工数を算出する
     - 段取り時間 + 加工時間 × 数量
  4. チャージレート（設備の時間単価）を掛ける
     - 汎用旋盤: ¥4,000/h、CNC旋盤: ¥6,000/h、MC: ¥8,000/h 等
  5. 材料費・表面処理費・外注費・管理費を加算する
  6. 見積書を作成して顧客に回答する

  → 熟練者が1件30分〜数時間。月100件以上の引合いに対応
  → 回答が遅い = 受注機会の逸失（顧客は3社見積で最速回答に発注しがち）
  → 見積精度が低い = 赤字受注 or 高すぎて失注
  → 熟練見積担当の属人化。退職リスク
```

### 1.2 見積AIの3段階アプローチ

```
■ Level 1: 工程推定AI（最初に実装）
  入力: 図面PDF/画像 + 仕様（材質、数量、表面処理）
  処理: Document AI + LLM で形状・寸法を認識 → 加工工程を推定
  出力: 推定工程表（工程名、設備、推定工数）

  ユーザーがチャージレート・工数を確認/修正 → 見積書を自動生成

  価値: 見積作成時間の50%削減
  制約: 工数の精度は初期60-70%（人間の確認必須）

■ Level 2: 実績学習型見積（3ヶ月後）
  入力: Level 1 + 過去の見積実績 + 実際の加工実績
  処理:
    ① 類似部品の過去見積を検索（形状・材質・サイズで類似度判定）
    ② 過去実績から工数を推定（見積工数 vs 実績工数の補正）
    ③ 受注率分析（価格帯別の受注率 → 最適価格帯を提案）
  出力: 高精度な見積 + 受注率予測

  価値: 見積作成時間の80%削減 + 受注率向上

■ Level 3: ネットワーク効果（Phase 3+）
  - N社の匿名化見積データから市場単価を推定
  - 「同業他社のMC加工チャージレート: ¥7,500-9,000/h」
  - 業種別の利益率ベンチマーク
```

### 1.3 データモデル

```sql
-- 見積プロジェクト
CREATE TABLE mfg_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  quote_number TEXT NOT NULL,              -- 見積番号（自動採番）
  customer_name TEXT NOT NULL,             -- 顧客名
  project_name TEXT,                       -- 案件名
  quantity INTEGER NOT NULL DEFAULT 1,     -- 数量
  material TEXT,                           -- 材質（SS400, SUS304, A5052 等）
  surface_treatment TEXT,                  -- 表面処理（メッキ、アルマイト、塗装等）
  delivery_date DATE,                      -- 希望納期
  total_amount BIGINT,                     -- 見積金額（円）
  profit_margin DECIMAL(5,2),             -- 利益率（%）
  status TEXT DEFAULT 'draft',             -- draft / sent / won / lost / expired
  won_amount BIGINT,                       -- 受注金額（値引き後）
  lost_reason TEXT,                        -- 失注理由
  valid_until DATE,                        -- 見積有効期限
  file_url TEXT,                           -- 見積書PDF URL
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 見積明細（工程別）
CREATE TABLE mfg_quote_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  quote_id UUID NOT NULL REFERENCES mfg_quotes(id) ON DELETE CASCADE,
  company_id UUID NOT NULL REFERENCES companies(id),
  sort_order INTEGER NOT NULL,
  process_name TEXT NOT NULL,              -- 工程名（材料切断、旋盤、MC、研磨等）
  equipment TEXT,                          -- 使用設備（CNC旋盤、マシニングセンタ等）
  setup_time_min DECIMAL(8,1),            -- 段取り時間（分）
  cycle_time_min DECIMAL(8,1),            -- サイクルタイム（分/個）
  total_time_min DECIMAL(10,1),           -- 合計時間 = 段取り + (サイクル × 数量)
  charge_rate DECIMAL(10,0),              -- チャージレート（円/時間）
  process_cost BIGINT,                     -- 加工費 = 合計時間/60 × チャージレート
  material_cost BIGINT,                    -- 材料費（材料工程のみ）
  outsource_cost BIGINT,                   -- 外注費（外注工程のみ）
  cost_source TEXT,                        -- manual / past_record / ai_estimated
  confidence DECIMAL(3,2),                 -- 推定信頼度
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- チャージレートマスタ
CREATE TABLE mfg_charge_rates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  equipment_name TEXT NOT NULL,             -- 設備名
  equipment_type TEXT NOT NULL,             -- 設備種別（lathe / machining_center / grinder / press 等）
  charge_rate DECIMAL(10,0) NOT NULL,      -- チャージレート（円/時間）
  setup_time_default DECIMAL(8,1),         -- デフォルト段取り時間（分）
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 材料単価マスタ
CREATE TABLE mfg_material_prices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  material_code TEXT NOT NULL,              -- 材質コード（SS400, SUS304 等）
  material_name TEXT NOT NULL,              -- 材質名
  form TEXT NOT NULL,                       -- 形状（丸棒、板、パイプ等）
  size_spec TEXT,                           -- サイズ仕様（φ50, t10 等）
  unit TEXT NOT NULL,                       -- 単位（kg, m, 枚 等）
  unit_price DECIMAL(10,0) NOT NULL,       -- 単価
  supplier TEXT,                            -- 仕入先
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_quotes ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_quote_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_charge_rates ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_material_prices ENABLE ROW LEVEL SECURITY;
```

### 1.4 見積AIパイプライン

```python
# workers/bpo/manufacturing/quoting.py

class QuotingPipeline:
    """
    製造業 見積AIパイプライン

    Step 1: 図面の取り込み・解析
    Step 2: 加工工程の推定
    Step 3: 工数・コストの算出
    Step 4: 見積書生成
    """

    async def analyze_drawing(
        self,
        files: list[UploadFile],
        material: str,
        quantity: int,
    ) -> DrawingAnalysis:
        """
        図面を解析して形状・寸法・加工要素を抽出

        対応形式:
        - 図面PDF/画像 → Document AI + LLM
        - 3D CAD（STEP/IGES）→ 形状解析（Phase 2+）
        - Excel仕様書 → 構造化抽出
        """

    async def estimate_processes(
        self,
        analysis: DrawingAnalysis,
        material: str,
        surface_treatment: str | None,
    ) -> list[ProcessEstimate]:
        """
        加工工程を推定

        LLMプロンプト:
        「以下の部品の加工工程を推定してください。
         形状: {shape_description}
         材質: {material}
         寸法: {dimensions}
         表面処理: {surface_treatment}
         公差: {tolerances}

         出力: 工程順に [{process_name, equipment, setup_time_min, cycle_time_min, notes}]」

        過去実績がある場合:
        → 類似部品の実績工数で補正
        """

    async def calculate_costs(
        self,
        quote_id: str,
        processes: list[ProcessEstimate],
        quantity: int,
    ) -> QuoteCostBreakdown:
        """
        コスト計算

        見積金額 = 材料費 + Σ(加工費) + 表面処理費 + 外注費 + 管理費
        加工費 = (段取り時間 + サイクルタイム × 数量) / 60 × チャージレート
        管理費 = (材料費 + 加工費 + 表面処理 + 外注費) × 管理費率
        """

    async def generate_quote_document(
        self,
        quote_id: str,
        format: str = "pdf",
    ) -> bytes:
        """見積書PDF/Excel生成"""

    async def analyze_win_rate(
        self,
        quote_id: str,
    ) -> WinRateAnalysis:
        """
        受注率分析
        - この価格帯での過去の受注率
        - 「¥X万なら受注率70%、¥Y万なら50%」
        - 利益率と受注率のバランス提案
        """

    async def learn_from_actual(
        self,
        quote_id: str,
        actual_times: list[dict],
    ) -> None:
        """
        実績からの学習
        - 見積工数 vs 実績工数の差分を記録
        - 次回以降の工数推定精度を向上
        """
```

### 1.5 UI設計

```
■ 見積一覧: /bpo/manufacturing/quotes
  - 見積一覧（番号、顧客、金額、状態、受注率）
  - 新規作成 / CSV一括取り込み
  - フィルター（状態、顧客、期間）

■ 見積作成: /bpo/manufacturing/quotes/new
  Step 1: 基本情報（顧客、案件名、数量、材質、表面処理、納期）
  Step 2: 図面アップロード → AI解析中...
  Step 3: 工程確認・編集（AI推定結果をテーブル表示、行ごと編集可）
  Step 4: コスト確認（材料費、加工費、管理費の内訳表示）
  Step 5: 見積書出力（PDF/Excel）

■ チャージレート管理: /bpo/manufacturing/charge-rates
  - 設備別チャージレート一覧
  - 設備追加・編集

■ 材料単価管理: /bpo/manufacturing/materials
  - 材質別単価一覧
  - 仕入先別比較
```

---

## 2. 生産計画AI

### 2.1 問題の本質

```
中小製造業の生産計画:
  - ベテラン工場長がExcelとホワイトボードで管理
  - 「この設備は来週火曜まで空かない」が頭の中にしかない
  - 急な飛び込み受注で計画が崩壊 → 納期遅延
  - 設備稼働率が見えない → 投資判断ができない
```

### 2.2 データモデル

```sql
-- 受注（生産指示の元）
CREATE TABLE mfg_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  order_number TEXT NOT NULL,
  customer_name TEXT NOT NULL,
  product_name TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  due_date DATE NOT NULL,                   -- 納期
  priority TEXT DEFAULT 'normal',           -- urgent / high / normal / low
  quote_id UUID REFERENCES mfg_quotes(id),
  status TEXT DEFAULT 'pending',            -- pending / planned / in_progress / completed / shipped
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 設備マスタ
CREATE TABLE mfg_equipment (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,                       -- 設備名
  equipment_type TEXT NOT NULL,             -- lathe / mc / grinder / press / welder 等
  capacity_hours_per_day DECIMAL(4,1) DEFAULT 8.0,  -- 1日の稼働可能時間
  status TEXT DEFAULT 'active',             -- active / maintenance / inactive
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 生産スケジュール（設備×日×オーダー）
CREATE TABLE mfg_schedules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  order_id UUID NOT NULL REFERENCES mfg_orders(id),
  equipment_id UUID NOT NULL REFERENCES mfg_equipment(id),
  process_name TEXT NOT NULL,
  scheduled_date DATE NOT NULL,
  start_time TIME,
  duration_hours DECIMAL(5,1) NOT NULL,     -- 予定時間
  actual_hours DECIMAL(5,1),                -- 実績時間
  status TEXT DEFAULT 'planned',            -- planned / in_progress / completed / delayed
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_equipment ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_schedules ENABLE ROW LEVEL SECURITY;
```

### 2.3 生産計画エンジン

```python
# workers/bpo/manufacturing/production_plan.py

class ProductionPlanner:
    """
    生産計画AI

    山積み: 受注を納期順に並べて設備に積む
    山崩し: 設備キャパを超えた分を前倒し/外注/残業で調整
    """

    async def generate_plan(
        self,
        company_id: str,
        start_date: date,
        end_date: date,
    ) -> ProductionPlan:
        """
        生産計画を自動生成
        1. 未完了の受注を納期順にソート
        2. 各受注の工程を設備に割当（山積み）
        3. キャパ超過を検出（山崩し）
        4. ガントチャートデータを生成
        """

    async def simulate_new_order(
        self,
        order: NewOrderInput,
    ) -> SimulationResult:
        """
        新規受注のシミュレーション
        「この案件を受けたら既存の納期に影響するか？」
        → 影響ありの場合、対策案（残業/外注/納期調整）を提示
        """

    async def get_equipment_utilization(
        self,
        company_id: str,
        period: str,
    ) -> EquipmentUtilization:
        """設備稼働率レポート"""
```

---

## 3. 品質管理

### 3.1 データモデル

```sql
-- 検査記録
CREATE TABLE mfg_inspections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  order_id UUID REFERENCES mfg_orders(id),
  product_name TEXT NOT NULL,
  lot_number TEXT,
  inspection_type TEXT NOT NULL,            -- incoming / in_process / final / patrol
  inspector TEXT,
  inspection_date DATE NOT NULL,
  items JSONB NOT NULL,                     -- [{name, spec_min, spec_max, measured, unit, result}]
  overall_result TEXT NOT NULL,             -- pass / fail / conditional
  defect_count INTEGER DEFAULT 0,
  defect_details JSONB,                     -- [{type, count, description}]
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 不良記録
CREATE TABLE mfg_defects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  inspection_id UUID REFERENCES mfg_inspections(id),
  order_id UUID REFERENCES mfg_orders(id),
  defect_date DATE NOT NULL,
  product_name TEXT NOT NULL,
  defect_type TEXT NOT NULL,                -- dimensional / surface / material / assembly
  defect_detail TEXT NOT NULL,
  quantity INTEGER NOT NULL DEFAULT 1,
  cause TEXT,                               -- machine / material / method / man / measurement
  corrective_action TEXT,
  preventive_action TEXT,
  status TEXT DEFAULT 'open',               -- open / investigating / resolved
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_inspections ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_defects ENABLE ROW LEVEL SECURITY;
```

### 3.2 品質管理エンジン

```python
# workers/bpo/manufacturing/quality.py

class QualityManager:
    """
    品質管理AI
    - SPC（統計的工程管理）: Xbar-R管理図、Cp/Cpk計算
    - 不良分析: パレート図、特性要因図データ
    - 不良予測: トレンドから不良発生を予測
    """

    async def calculate_spc(
        self,
        product_name: str,
        measurement_name: str,
        period_days: int = 30,
    ) -> SPCResult:
        """Xbar-R管理図 + Cp/Cpk"""

    async def generate_quality_report(
        self,
        company_id: str,
        year: int,
        month: int,
    ) -> QualityReport:
        """月次品質レポート（不良率、パレート、トレンド）"""

    async def predict_defects(
        self,
        product_name: str,
    ) -> DefectPrediction:
        """不良予測（トレンド分析から）"""
```

---

## 4. 在庫最適化

### 4.1 データモデル

```sql
-- 在庫マスタ
CREATE TABLE mfg_inventory (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  item_code TEXT NOT NULL,
  item_name TEXT NOT NULL,
  item_type TEXT NOT NULL,                  -- raw_material / wip / finished / consumable
  unit TEXT NOT NULL,
  current_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
  safety_stock DECIMAL(12,2) DEFAULT 0,     -- 安全在庫
  reorder_point DECIMAL(12,2),              -- 発注点
  reorder_quantity DECIMAL(12,2),           -- 発注量（EOQ）
  lead_time_days INTEGER,                   -- リードタイム
  unit_cost DECIMAL(10,0),                  -- 単価
  location TEXT,                            -- 保管場所
  last_counted_at TIMESTAMPTZ,             -- 最終棚卸日
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(company_id, item_code)
);

-- 在庫移動ログ
CREATE TABLE mfg_inventory_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  item_id UUID NOT NULL REFERENCES mfg_inventory(id),
  transaction_type TEXT NOT NULL,            -- receive / issue / adjust / return
  quantity DECIMAL(12,2) NOT NULL,
  reference_type TEXT,                       -- order / purchase / adjustment
  reference_id UUID,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_inventory ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_inventory_transactions ENABLE ROW LEVEL SECURITY;
```

### 4.2 在庫最適化エンジン

```python
# workers/bpo/manufacturing/inventory.py

class InventoryOptimizer:
    """
    在庫最適化AI
    - 需要予測（過去実績 + 受注見込み → 将来消費量推定）
    - 安全在庫計算（需要変動 × リードタイム変動）
    - 発注提案（発注点割れ → 自動通知）
    - 棚卸サポート（差異分析）
    """

    async def suggest_reorder(
        self,
        company_id: str,
    ) -> list[ReorderSuggestion]:
        """発注点割れの品目を検出し、発注提案"""

    async def forecast_demand(
        self,
        item_id: str,
        months_ahead: int = 3,
    ) -> DemandForecast:
        """需要予測（移動平均 + 受注残からの推定）"""

    async def generate_inventory_report(
        self,
        company_id: str,
    ) -> InventoryReport:
        """在庫レポート（金額、回転率、滞留品）"""
```

---

## 5. SOP管理

### 5.1 データモデル

```sql
-- 作業手順書
CREATE TABLE mfg_sops (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  sop_number TEXT NOT NULL,                 -- 文書番号
  title TEXT NOT NULL,
  process_name TEXT NOT NULL,               -- 対象工程
  version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'draft',              -- draft / review / approved / obsolete
  content JSONB NOT NULL,                   -- [{step_number, description, image_url, caution, tools_needed}]
  approved_by UUID REFERENCES users(id),
  approved_at TIMESTAMPTZ,
  revision_history JSONB DEFAULT '[]',      -- [{version, date, changed_by, reason}]
  next_review_date DATE,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE mfg_sops ENABLE ROW LEVEL SECURITY;
```

### 5.2 SOP管理エンジン

```python
# workers/bpo/manufacturing/sop_manager.py

class SOPManager:
    """
    SOP管理AI
    - 手順書のAI生成（工程名+条件 → 手順書ドラフト）
    - 改訂管理（変更理由の記録、旧版アーカイブ）
    - レビューリマインド（定期見直し）
    - 多言語対応（外国人作業者向け翻訳）
    """

    async def generate_sop(
        self,
        process_name: str,
        conditions: str,
        equipment: str,
    ) -> SOPDraft:
        """LLMで手順書ドラフトを生成"""

    async def check_review_schedule(
        self,
        company_id: str,
    ) -> list[SOPReviewAlert]:
        """レビュー期限が近い手順書を検出"""
```

---

## 6. 設備保全

### 6.1 データモデル

```sql
-- 保全記録
CREATE TABLE mfg_maintenance_records (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  equipment_id UUID NOT NULL REFERENCES mfg_equipment(id),
  maintenance_type TEXT NOT NULL,            -- daily / weekly / monthly / annual / breakdown
  maintenance_date DATE NOT NULL,
  description TEXT NOT NULL,
  parts_replaced JSONB,                      -- [{part_name, quantity, cost}]
  downtime_hours DECIMAL(5,1),              -- 停止時間
  cost BIGINT,                               -- 保全費用
  performed_by TEXT,
  next_scheduled DATE,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE mfg_maintenance_records ENABLE ROW LEVEL SECURITY;
```

### 6.2 設備保全エンジン

```python
# workers/bpo/manufacturing/maintenance.py

class MaintenanceManager:
    """
    設備保全AI
    - 保全カレンダー生成（日次/週次/月次/年次）
    - 点検チェックリスト管理
    - 故障予兆検知（故障間隔のトレンド分析）
    - 保全コストレポート
    """

    async def generate_maintenance_calendar(
        self,
        company_id: str,
        month: int,
        year: int,
    ) -> MaintenanceCalendar:
        """保全カレンダー生成"""

    async def predict_failure(
        self,
        equipment_id: str,
    ) -> FailurePrediction:
        """故障予測（MTBF分析）"""
```

---

## 7. 仕入管理

### 7.1 データモデル

```sql
-- 部品表（BOM）
CREATE TABLE mfg_bom (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  product_code TEXT NOT NULL,
  product_name TEXT NOT NULL,
  items JSONB NOT NULL,                      -- [{item_code, item_name, quantity, unit}]
  version INTEGER DEFAULT 1,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 発注
CREATE TABLE mfg_purchase_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  po_number TEXT NOT NULL,
  vendor_id UUID NOT NULL REFERENCES bpo_vendors(id),
  order_date DATE NOT NULL,
  expected_date DATE,                        -- 希望納期
  items JSONB NOT NULL,                      -- [{item_code, item_name, quantity, unit, unit_price, amount}]
  total_amount BIGINT NOT NULL,
  status TEXT DEFAULT 'draft',               -- draft / sent / partially_received / received / cancelled
  received_at TIMESTAMPTZ,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_bom ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_purchase_orders ENABLE ROW LEVEL SECURITY;
```

### 7.2 仕入管理エンジン

```python
# workers/bpo/manufacturing/procurement.py

class ProcurementManager:
    """
    仕入管理
    - BOM展開 → 必要資材の自動算出
    - 発注書の自動生成
    - 検収管理（納品チェック）
    - 仕入先評価（QCD）
    """

    async def explode_bom(
        self,
        product_code: str,
        quantity: int,
    ) -> list[MaterialRequirement]:
        """BOM展開 → 所要量計算"""

    async def generate_purchase_order(
        self,
        requirements: list[MaterialRequirement],
    ) -> PurchaseOrder:
        """発注書自動生成"""
```

---

## 8. ISO文書管理

### 8.1 データモデル

```sql
-- ISO文書
CREATE TABLE mfg_iso_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  document_number TEXT NOT NULL,             -- 文書番号
  title TEXT NOT NULL,
  document_type TEXT NOT NULL,               -- manual / procedure / work_instruction / record / form
  iso_clause TEXT,                           -- 対応するISO条項（4.1, 7.1.5 等）
  version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'draft',               -- draft / review / approved / obsolete
  content_url TEXT,                          -- 文書ファイルURL
  approved_by UUID REFERENCES users(id),
  approved_at TIMESTAMPTZ,
  next_review_date DATE,
  revision_history JSONB DEFAULT '[]',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 内部監査
CREATE TABLE mfg_internal_audits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  audit_date DATE NOT NULL,
  auditor TEXT NOT NULL,
  department TEXT NOT NULL,
  iso_clauses TEXT[] NOT NULL,              -- 監査対象条項
  findings JSONB DEFAULT '[]',              -- [{type: observation/minor/major, clause, description, corrective_action, due_date, status}]
  overall_result TEXT,                       -- conforming / minor_nc / major_nc
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS
ALTER TABLE mfg_iso_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE mfg_internal_audits ENABLE ROW LEVEL SECURITY;
```

### 8.2 ISO文書管理エンジン

```python
# workers/bpo/manufacturing/iso_docs.py

class ISODocumentManager:
    """
    ISO文書管理AI
    - 文書体系の管理（マニュアル→手順書→作業指示書→記録）
    - 改訂管理（承認フロー付き）
    - 内部監査チェックリスト自動生成
    - 不適合の是正処置管理
    - 審査準備レポート
    """

    async def generate_audit_checklist(
        self,
        department: str,
        iso_standard: str = "9001",
    ) -> AuditChecklist:
        """内部監査チェックリスト生成（LLM + ISO条項）"""

    async def check_document_review(
        self,
        company_id: str,
    ) -> list[DocumentReviewAlert]:
        """レビュー期限が近い文書を検出"""

    async def generate_management_review_input(
        self,
        company_id: str,
        period: str,
    ) -> ManagementReviewReport:
        """マネジメントレビュー用インプットレポート自動生成"""
```

---

## 9. ディレクトリ構成

```
workers/bpo/manufacturing/
├── __init__.py
├── quoting.py               # ① 見積AI
├── production_plan.py       # ② 生産計画AI
├── quality.py               # ③ 品質管理
├── inventory.py             # ④ 在庫最適化
├── sop_manager.py           # ⑤ SOP管理
├── maintenance.py           # ⑥ 設備保全
├── procurement.py           # ⑦ 仕入管理
├── iso_docs.py              # ⑧ ISO文書管理
└── models.py                # Pydanticモデル

routers/bpo/manufacturing.py  # 全エンドポイント

frontend/src/app/(authenticated)/bpo/manufacturing/
├── quotes/                  # 見積
├── production/              # 生産計画
├── quality/                 # 品質管理
├── inventory/               # 在庫
├── sop/                     # SOP
├── maintenance/             # 設備保全
├── procurement/             # 仕入
└── iso/                     # ISO文書

db/migrations/
└── 008_bpo_manufacturing.sql
```

---

## 10. APIエンドポイント

```
# 見積
POST   /api/bpo/manufacturing/quotes                    # 見積作成
GET    /api/bpo/manufacturing/quotes                    # 一覧
GET    /api/bpo/manufacturing/quotes/{id}               # 詳細
PATCH  /api/bpo/manufacturing/quotes/{id}               # 更新
POST   /api/bpo/manufacturing/quotes/{id}/analyze       # 図面AI解析
POST   /api/bpo/manufacturing/quotes/{id}/export        # 見積書出力
GET    /api/bpo/manufacturing/charge-rates              # チャージレート一覧
POST   /api/bpo/manufacturing/charge-rates              # チャージレート登録
GET    /api/bpo/manufacturing/materials                 # 材料単価一覧

# 生産計画
POST   /api/bpo/manufacturing/orders                    # 受注登録
GET    /api/bpo/manufacturing/orders                    # 受注一覧
POST   /api/bpo/manufacturing/schedule/generate         # 生産計画生成
GET    /api/bpo/manufacturing/schedule                  # 生産計画表示
POST   /api/bpo/manufacturing/schedule/simulate         # 新規受注シミュレーション
GET    /api/bpo/manufacturing/equipment                 # 設備一覧
GET    /api/bpo/manufacturing/equipment/utilization      # 稼働率

# 品質管理
POST   /api/bpo/manufacturing/inspections               # 検査記録登録
GET    /api/bpo/manufacturing/inspections               # 検査記録一覧
GET    /api/bpo/manufacturing/quality/spc/{product}     # SPC分析
GET    /api/bpo/manufacturing/quality/report             # 品質レポート
POST   /api/bpo/manufacturing/defects                   # 不良登録

# 在庫
GET    /api/bpo/manufacturing/inventory                 # 在庫一覧
PATCH  /api/bpo/manufacturing/inventory/{id}            # 在庫更新
GET    /api/bpo/manufacturing/inventory/reorder          # 発注提案
GET    /api/bpo/manufacturing/inventory/report           # 在庫レポート

# SOP
POST   /api/bpo/manufacturing/sops                      # SOP作成
GET    /api/bpo/manufacturing/sops                      # SOP一覧
POST   /api/bpo/manufacturing/sops/{id}/generate        # AI生成
GET    /api/bpo/manufacturing/sops/review-alerts         # レビューアラート

# 設備保全
POST   /api/bpo/manufacturing/maintenance               # 保全記録登録
GET    /api/bpo/manufacturing/maintenance/calendar       # 保全カレンダー
GET    /api/bpo/manufacturing/maintenance/prediction/{eq_id}  # 故障予測

# 仕入
POST   /api/bpo/manufacturing/purchase-orders           # 発注書作成
GET    /api/bpo/manufacturing/purchase-orders           # 発注一覧
POST   /api/bpo/manufacturing/bom/explode               # BOM展開

# ISO
POST   /api/bpo/manufacturing/iso/documents             # 文書登録
GET    /api/bpo/manufacturing/iso/documents             # 文書一覧
POST   /api/bpo/manufacturing/iso/audit-checklist        # 監査チェックリスト生成
POST   /api/bpo/manufacturing/iso/audits                # 監査記録登録
```

---

## 11. SaaS連携戦略

```
Phase 1: kintone（多くの製造業が使用）→ 受注・在庫データ取得
Phase 2: zaico API → 在庫データ連携
Phase 3: freee/MF → 会計連携（原価計算）
Phase 4: techs 等の生産管理SaaS → API確認後に連携
```

---

## 12. 並列開発プラン

```
Phase A（2週間）: 見積AI + 生産計画 — 4エージェント
Phase B（1週間）: 品質管理 + 在庫 — 3エージェント
Phase C（1週間）: SOP + 設備保全 — 3エージェント
Phase D（1週間）: 仕入 + ISO — 3エージェント
```

---

## 13. リスクと対策

| リスク | 対策 |
|---|---|
| 図面解析の精度 | 最初はExcel仕様書取り込みを優先。図面OCRは補助 |
| チャージレートの設定 | ユーザーが自社設備ごとに設定。業界平均値をデフォルト提供 |
| 生産計画の複雑性 | MVPは単純なガントチャート。制約条件の最適化はPhase 2+ |
| SPC計算の誤り | 統計ライブラリ（scipy）使用。手計算結果と照合テスト |

---

## 14. KPI・成功基準

### 完了基準
- [ ] 見積作成時間が50%以上短縮
- [ ] 生産計画がガントチャートで可視化
- [ ] 品質レポートが自動生成
- [ ] パイロット3社が実業務で使用

### PMF指標
- [ ] NPS ≥ 30
- [ ] 「なくなったら困る」≥ 60%
- [ ] 月額30万円で「安い」と感じる社数 ≥ 50%
