# BPOアーキテクチャ設計 — 全業種共通 + 業種別

> **位置づけ**: 本書はd_04 Agent OSのLayer 2（ゲノム駆動エンジン層）に該当する。上位設計はd_04を参照。
>
> **設計思想**: ベースBPO（全業種共通）+ 業種別BPO（プラグイン方式）
> **実装順序**: 建設業のみ先行実装。他業種は設計だけ先にやっておく。
> **Source of Truth**: この文書がBPO全体アーキテクチャの正

---

## 0. なぜこの構造か

```
シャチョツーのBPOは「業種ごとに全く違う」部分と
「どの業種でも同じ」部分がある。

  共通（ベースBPO）:
    請求書処理、経費精算、給与計算準備、勤怠管理、
    契約書管理、行政手続きリマインド、ベンダー管理

  業種固有（プラグインBPO）:
    建設: 積算、安全書類、出来高、工事写真
    製造: 生産計画、品質管理、在庫、SOP
    歯科: レセプト、予約、リコール、院内感染管理

→ ベースを1回作れば全業種で使える
→ 業種プラグインを追加するだけで新業種に展開できる
→ これがCompound Startup戦略の技術的基盤
```

---

## 1. 全体アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│                    シャチョツー BPO Layer                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ ベースBPO（全業種共通）                                  │  │
│  │                                                       │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │  │
│  │  │請求・入金 │ │経費精算  │ │給与準備  │ │勤怠集計  │ │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │  │
│  │  │契約管理  │ │行政手続き│ │ベンダー  │ │レポート  │ │  │
│  │  │          │ │リマインド│ │管理      │ │生成      │ │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ │  │
│  └───────────────────────────────────────────────────────┘  │
│                           │                                 │
│              ┌────────────┼────────────┐                    │
│              ▼            ▼            ▼                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ 建設業BPO     │ │ 製造業BPO     │ │ 歯科BPO       │  ...  │
│  │ (プラグイン)  │ │ (プラグイン)  │ │ (プラグイン)  │        │
│  │              │ │              │ │              │        │
│  │ ・積算AI     │ │ ・生産計画AI  │ │ ・レセプトAI  │        │
│  │ ・安全書類   │ │ ・品質管理    │ │ ・予約最適化  │        │
│  │ ・出来高     │ │ ・在庫最適化  │ │ ・リコール    │        │
│  │ ・工事写真   │ │ ・SOP管理     │ │ ・自費提案    │        │
│  │ ・原価管理   │ │ ・原価管理    │ │ ・院内感染    │        │
│  │ ・施工計画書 │ │ ・設備保全    │ │ ・技工指示    │        │
│  │ ・下請管理   │ │ ・仕入管理    │ │ ・材料発注    │        │
│  │ ・許可更新   │ │ ・ISO文書     │ │ ・許認可更新  │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 共通基盤                                                │  │
│  │  ・ドキュメント生成エンジン（Excel/PDF/Word）             │  │
│  │  ・承認ワークフローエンジン                               │  │
│  │  ・SaaS連携コネクタ                                      │  │
│  │  ・学習・フィードバックループ                              │  │
│  │  ・エンジン3層アーキテクチャ（下記参照）                   │  │
│  │  ・anomaly_detector（クロスパイプライン品質ゲート）        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. ベースBPO（全業種共通）

> **Phase 2拡張**: 7領域24パイプラインの詳細設計は `b_詳細設計/b_07_バックオフィスBPO詳細設計.md` を参照。
> 本セクション(2.1〜2.7)は概念設計。b_07が実装仕様のSource of Truth。

### 2.1 請求・入金管理

```
■ どの業種でも必要な理由
  - 請求書を作って送る → 入金を確認する → 未入金を督促する
  - 中小企業では社長 or 経理1人がExcelでやっている
  - 月末に集中して死ぬ

■ やること
  ① 請求書の自動生成（テンプレート + 取引データ）
  ② 請求書のPDF/Excel出力
  ③ 送付管理（メール添付 or 郵送リスト）
  ④ 入金消込（銀行明細との突合）
  ⑤ 未入金アラート（期日超過の自動通知）
  ⑥ 資金繰り予測（請求残 + 支払予定 → キャッシュフロー予測）

■ 電子帳簿保存法対応
  - クラウド保存 + 改ざん防止ログ
  - 検索機能（日付・金額・取引先）
  - 7年保存

■ 業種別の違い
  建設: 出来高ベースの請求 → 建設BPOプラグインが処理
  製造: 納品ベースの請求（納品書→請求書の変換）
  歯科: 保険請求（レセプト）+ 自費請求 → 歯科BPOプラグインが処理
  共通: 上記以外の一般的な請求（コンサル報酬、サービス利用料等）
```

### 2.2 経費精算

```
■ やること
  ① 領収書のOCR読み取り（金額・日付・店名を自動抽出）
  ② 勘定科目の自動推定（LLM）
  ③ 承認ワークフロー（金額別の決裁ルート）
  ④ 会計ソフト連携（freee / MF / 弥生）
  ⑤ 月次経費レポート生成

■ 業種別の違い
  建設: 現場ごとの経費按分（工事原価 vs 一般管理費）
  製造: 製造原価 vs 販管費の分類
  歯科: 医療費 vs 一般経費の分類
```

### 2.3 給与計算準備

```
■ やること（給与計算そのものではなく「準備」）
  ① 勤怠データの集計（勤怠SaaSから取得 or 手動入力）
  ② 残業時間の自動計算（36協定超過アラート付き）
  ③ 有給残日数の管理
  ④ 給与計算ソフトへの入力データ整形
  ⑤ 社会保険料の変更通知（算定基礎届の時期にアラート）

■ なぜ「準備」止まりか
  - 給与計算自体はfreee/MF/ジョブカン等の既存SaaSが強い
  - シャチョツーは「データを整えて渡す」役割
  - 間違えると労基法違反 → 慎重にレベルアップ
```

### 2.4 勤怠集計

```
■ やること
  ① 勤怠SaaS（ジョブカン/King of Time等）からデータ取得
  ② 月次勤怠サマリー生成
  ③ 36協定チェック（月45h/年360h超過アラート）
  ④ 有給取得率チェック（年5日未満アラート）
  ⑤ 異常検知（打刻漏れ、深夜勤務続き等）

■ 業種別の違い
  建設: 現場ごとの勤怠管理（直行直帰対応）
  製造: シフト勤務対応（2交代/3交代）
  歯科: 半日勤務（水曜午後休み等）対応
```

### 2.5 契約管理

```
■ やること
  ① 契約書のテンプレート生成（LLM + テンプレート）
  ② 契約期限管理（更新/終了のリマインド）
  ③ 契約書のOCR + 構造化保存
  ④ 契約条件の検索（「○○社との支払条件は？」）

■ 業種別の違い
  建設: 工事請負契約、下請契約 → 建設業法の要件
  製造: 取引基本契約、個別注文書
  歯科: 自費診療同意書、業務委託契約
```

### 2.6 行政手続きリマインド

```
■ やること
  ① 各種許認可・届出の期限管理
  ② 更新必要書類のチェックリスト生成
  ③ 届出書類の下書き生成（AI）

■ 業種別の違い
  建設: 建設業許可更新(5年)、経営事項審査(毎年)、入札参加資格(2年)
  製造: ISO更新(3年)、環境許可、消防届出
  歯科: 保健所届出、X線管理区域届、廃棄物処理
  共通: 社会保険算定基礎届(年1)、労働保険年度更新(年1)、36協定届(年1)
```

### 2.7 ベンダー・仕入先管理

```
■ やること
  ① ベンダーマスタ（基本情報、評価、支払条件）
  ② 発注管理（注文書発行）
  ③ 支払管理（請求書受取→支払処理）
  ④ 年次評価

■ 業種別の違い
  建設: 下請業者管理（建設業法の要件。安全書類連携）
  製造: 部品・原材料仕入先管理（QCD評価）
  歯科: 技工所管理（納期・品質評価）、材料ディーラー管理
```

### 2.8 レポート自動生成

```
■ やること
  ① 月次経営レポート（売上・利益・キャッシュ）
  ② KPIダッシュボード
  ③ 予実管理レポート
  ④ カスタムレポート（LLMで自由記述→レポート生成）

■ brain/ のproactive（能動提案）と連携
  - レポートの数値から異常を検知 → 提案を自動生成
```

---

## 3. 業種別BPO — 建設業

> **詳細設計**: `e_01_建設業BPO設計.md` を参照
> **実装**: Phase A で先行実装

```
■ モジュール一覧（8個）

  ① 積算AI — 図面→数量→単価→内訳書（★キラー）
  ② 安全書類自動生成 — 作業員名簿、施工体制台帳等
  ③ 出来高・請求書 — 月次出来高計算→請求書生成
  ④ 原価管理レポート — 予実分析、赤字工事予測
  ⑤ 施工計画書AI生成 — テンプレート×LLM
  ⑥ 工事写真整理 — AI分類+黒板OCR+電子納品
  ⑦ 下請管理 — 評価+発注+支払（ベースBPOのベンダー管理を拡張）
  ⑧ 許可更新・経審 — 書類生成+期限管理（ベースBPOの行政手続きを拡張）

■ 建設業固有のデータモデル
  - estimation_projects / estimation_items（積算）
  - unit_price_master / public_labor_rates（単価）
  - construction_sites（現場）
  - workers / worker_qualifications（作業員・資格）
  - site_worker_assignments（現場×作業員）
  - safety_documents（安全書類）
  - subcontractors（下請業者）
  - construction_contracts / progress_records（工事台帳・出来高）
  - cost_records（原価実績）

■ SaaS連携
  - ANDPAD（API あり）: 施工管理データ取得
  - グリーンサイト: CSV出力（API未公開）
  - どっと原価3（WEB-API あり）: 原価データ連携
  - freee/MF: 会計連携

■ 建設業固有の法令対応
  - 建設業法（下請代金支払、施工体制台帳）
  - 労働安全衛生法（安全書類、KY活動）
  - 建設リサイクル法（再資源化計画）
```

---

## 4. 業種別BPO — 製造業

> **実装**: Phase 2+（設計のみ先行）

```
■ モジュール一覧（8個）

  ① 見積AI — 図面→工程分析→コスト→見積書（★キラー）
     入力: 図面PDF/STEP/IGES + 仕様書
     処理: 形状認識→加工工程推定→工数算出→見積自動生成
     ※ 建設の積算AIと同じ「拾い出し→単価→出力」パターン
     ※ ただし対象が「工事数量」ではなく「加工工程」

  ② 生産計画AI — 受注→生産スケジュール自動生成
     入力: 受注データ + 設備稼働状況 + 在庫
     処理: 納期逆算→設備割当→山積み/山崩し→工程表生成
     出力: 生産スケジュール（ガントチャート）

  ③ 品質管理 — 検査記録→統計分析→不良予測
     入力: 検査データ（寸法、外観等）
     処理: SPC（統計的工程管理）、Cp/Cpk計算、トレンド分析
     出力: 品質レポート + 異常アラート

  ④ 在庫最適化 — 需要予測→適正在庫→発注提案
     入力: 受注履歴 + 在庫データ + リードタイム
     処理: 需要予測 → 安全在庫計算 → 発注点管理
     出力: 発注提案リスト

  ⑤ SOP管理 — 作業手順書のAI作成・更新
     入力: 作業内容の記述 or 動画
     処理: LLMで手順書フォーマットに整形
     出力: 手順書（Word/PDF） + 改訂履歴管理

  ⑥ 設備保全 — 保全計画→点検記録→故障予測
     入力: 設備台帳 + 点検記録 + 故障履歴
     処理: 予防保全スケジュール + 故障予兆検知
     出力: 保全カレンダー + アラート

  ⑦ 仕入管理 — 発注→検収→支払（ベースBPOのベンダー管理を拡張）
     製造業固有: 部品表（BOM）連動、ロット管理、トレーサビリティ

  ⑧ ISO文書管理 — ISO9001/14001の文書体系管理
     入力: 既存文書 + 審査指摘事項
     処理: 文書体系の整理、改訂管理、内部監査チェックリスト生成
     出力: マニュアル + 記録帳票

■ 製造業固有のデータモデル
  - products（製品マスタ）
  - bom_items（部品表）
  - production_orders（生産指示）
  - inspection_records（検査記録）
  - equipment（設備台帳）
  - maintenance_records（保全記録）
  - inventory（在庫）
  - suppliers（仕入先）→ ベースの vendors を拡張

■ SaaS連携
  - techs（生産管理）: API要確認
  - zaico（在庫管理）: API あり
  - freee/MF: 会計連携
  - kintone: 多くの製造業が使用

■ 製造業固有の法令対応
  - 製造物責任法（PL法）
  - ISO 9001 / 14001
  - 化学物質管理（SDS）
  - 輸出管理（該非判定）
```

---

## 5. 業種別BPO — 歯科

> **実装**: Phase 2+（設計のみ先行）

```
■ モジュール一覧（8個）

  ① レセプトAI — 診療記録→レセプト自動チェック（★キラー）
     入力: 電子カルテの診療記録
     処理: 保険点数計算の整合性チェック、査定リスク検知
     出力: レセプトチェックレポート + 修正提案
     ※ レセコン（Dentis等）が生成したレセプトの「二重チェック」
     ※ 査定率を下げる = 直接的な収益改善

  ② 予約最適化AI — 予約枠→患者動線→収益最大化
     入力: 予約データ + 診療内容 + チェアタイム実績
     処理: 予約枠の最適配置、キャンセル予測、空き枠アラート
     出力: 最適化された予約スケジュール + 提案

  ③ リコール管理 — 定期検診の自動リマインド
     入力: 患者データ + 最終来院日 + 治療履歴
     処理: リコール対象者の自動抽出、リマインドメッセージ生成
     出力: リコールリスト + SMS/LINE送信
     ※ 中断患者の掘り起こし = 収益改善

  ④ 自費カウンセリング支援 — 治療計画→見積→同意書
     入力: 治療計画
     処理: 保険vs自費の比較資料生成、見積書作成、同意書テンプレート
     出力: カウンセリング資料（PDF）
     ※ 自費率アップ = 収益改善

  ⑤ 院内感染管理 — チェックリスト→記録→監査準備
     入力: 日次のチェック実施結果
     処理: 記録管理、未実施アラート、保健所監査準備資料生成
     出力: 感染管理記録 + 監査準備書類

  ⑥ 技工指示管理 — 技工指示書→納期管理→品質フィードバック
     入力: 治療計画から技工物の仕様
     処理: 技工指示書自動生成、技工所への送信、納期追跡
     出力: 技工指示書 + 納期カレンダー

  ⑦ 材料・在庫管理 — 消耗品の発注最適化
     入力: 使用実績 + 在庫
     処理: 消費予測→発注点管理→ディーラーへの自動発注
     出力: 発注リスト

  ⑧ 届出・許認可管理 — 保健所届出、X線管理区域等
     入力: 施設情報、設備情報
     処理: 届出期限管理、書類テンプレート生成
     出力: 届出書類 + リマインド

■ 歯科固有のデータモデル
  - patients（患者マスタ）※ 個人情報保護法に特に注意
  - appointments（予約）
  - treatment_records（診療記録サマリー）← カルテ連携
  - recall_schedules（リコールスケジュール）
  - lab_orders（技工指示）
  - dental_inventory（材料在庫）

■ SaaS連携
  - Dentis（レセコン/患者管理）: API要確認
  - ジニー / アポデント（予約管理）: API要確認
  - LINE公式アカウント: リコール送信用

■ 歯科固有の法令対応
  - 医療法
  - 個人情報保護法（要配慮個人情報 = 診療情報）
  - 医療広告ガイドライン
  - 歯科衛生士法
```

---

## 6. 業種別BPOの共通パターン

全業種のBPOモジュールを並べると、**パターンが見える**:

```
■ パターン1: 見積/積算AI（★各業種のキラー）
  建設: 図面→数量→単価→内訳書
  製造: 図面→工程→工数→見積書
  歯科: 治療計画→保険点数→自費見積
  共通: 「入力→AI構造化→単価適用→出力」のパイプライン

■ パターン2: 書類自動生成
  建設: 安全書類（全建統一様式）
  製造: ISO文書、SOP
  歯科: 同意書、技工指示書
  共通: 「マスタデータ→テンプレート→PDF/Excel」

■ パターン3: 実績管理→請求
  建設: 出来高→請求書
  製造: 納品→請求書
  歯科: 診療→レセプト + 自費請求
  共通: 「実績データ→金額計算→請求書出力」

■ パターン4: 原価/収益管理
  建設: 工事別原価管理、赤字工事予測
  製造: 製品別原価管理、不良コスト分析
  歯科: チェア別収益分析、自費率分析
  共通: 「コストデータ→分析→レポート→提案」

■ パターン5: 人材/リソース管理
  建設: 作業員管理、資格管理
  製造: 作業者スキルマップ、多能工管理
  歯科: スタッフ配置、勤務シフト
  共通: 「人→スキル→配置→最適化」

■ パターン6: 仕入/ベンダー管理
  建設: 下請業者管理
  製造: 部品仕入先管理
  歯科: 技工所・ディーラー管理
  共通: 「発注→検収→支払→評価」

■ パターン7: 許認可管理
  建設: 建設業許可、経審
  製造: ISO、環境許可
  歯科: 保健所届出
  共通: 「期限管理→書類準備→届出」

■ パターン8: 現場/施設管理
  建設: 工事現場管理
  製造: 工場ライン管理
  歯科: チェア・設備管理
  共通: 「場所→リソース配置→稼働管理」
```

**この8パターンがベースBPOエンジンの抽象化レイヤーになる。**

---

## 6.1 エンジン3層アーキテクチャ（共通基盤の中核）

共通基盤の中核として、以下の3ファイルがBPOエンジンを構成する。

```
engine/
├── base_pipeline.py       # 7ステップ共通パイプラインテンプレート
│                           OCR → 抽出 → 補完 → 計算 → 検証 → 異常検知 → 生成
│                           全業種の全パイプラインがこのテンプレートを継承する
│
├── genome_registry.py     # brain/genome/data/*.json からパイプラインレジストリを動的生成
│                           ゲノムJSONを読み込み、業種×業務のマッピングを自動構築
│
└── agent_factory.py       # ゲノム + knowledge_items → BPOAgentRole 自動生成
                            ナレッジDBの業務知識をAI社員ロールに変換する
```

### anomaly_detector（クロスパイプライン品質ゲート）

`anomaly_detector` は全パイプラインの出力を横断的に検証する品質ゲートとして機能する。
`base_pipeline.py` の7ステップのうち「異常検知」ステップで呼び出され、
業種を問わず統一的な品質チェックを実施する。

- **入力**: パイプラインの計算結果（見積金額、請求額、原価等）
- **検証**: 過去データとの乖離率、業界標準値との比較、論理整合性チェック
- **出力**: anomaly_score + 異常フラグ + 人間レビュー要否判定
- **位置づけ**: Phase 1.5クリティカル（全パイプライン品質の最終防壁）

### マイクロエージェント（20個）

マイクロエージェント数: **20個**（業種に依存しない原子操作群）

---

## 7. ディレクトリ構成（全体）

```
shachotwo-app/
├── workers/
│   └── bpo/
│       ├── __init__.py
│       │
│       ├── base/                        # ベースBPO（全業種共通）
│       │   ├── __init__.py
│       │   ├── invoicing.py             # 請求・入金管理
│       │   ├── expenses.py              # 経費精算
│       │   ├── payroll_prep.py          # 給与計算準備
│       │   ├── attendance.py            # 勤怠集計
│       │   ├── contracts.py             # 契約管理
│       │   ├── permits.py               # 行政手続き・許認可リマインド
│       │   ├── vendors.py               # ベンダー・仕入先管理
│       │   └── reports.py               # レポート自動生成
│       │
│       ├── engine/                      # BPO共通エンジン
│       │   ├── __init__.py
│       │   ├── base_pipeline.py         # ★新: 7ステップ共通パイプラインテンプレート
│       │   ├── genome_registry.py       # ★新: ゲノムJSON動的ロード→レジストリ生成
│       │   ├── agent_factory.py         # ★新: ナレッジ→AI社員ロール自動生成
│       │   ├── document_gen.py          # Excel/PDF/Word生成
│       │   ├── template_engine.py       # テンプレートエンジン
│       │   ├── approval_workflow.py     # 承認ワークフロー
│       │   ├── scheduler.py             # スケジューラ（リマインド・定期実行）
│       │   ├── learning.py              # フィードバック学習
│       │   └── models.py               # 共通Pydanticモデル
│       │
│       ├── construction/                # 建設業BPO ← 先行実装
│       │   ├── __init__.py
│       │   ├── estimator.py             # ① 積算AI
│       │   ├── safety_docs.py           # ② 安全書類生成
│       │   ├── billing.py               # ③ 出来高・請求書
│       │   ├── cost_report.py           # ④ 原価管理レポート
│       │   ├── plan_writer.py           # ⑤ 施工計画書
│       │   ├── photo_organizer.py       # ⑥ 工事写真整理
│       │   ├── subcontractor.py         # ⑦ 下請管理
│       │   ├── license_support.py       # ⑧ 許可更新・経審
│       │   └── models.py               # 建設業Pydanticモデル
│       │
│       ├── manufacturing/               # 製造業BPO ← Phase 2+
│       │   ├── __init__.py
│       │   ├── quoting.py               # ① 見積AI
│       │   ├── production_plan.py       # ② 生産計画AI
│       │   ├── quality.py               # ③ 品質管理
│       │   ├── inventory.py             # ④ 在庫最適化
│       │   ├── sop_manager.py           # ⑤ SOP管理
│       │   ├── maintenance.py           # ⑥ 設備保全
│       │   ├── procurement.py           # ⑦ 仕入管理
│       │   ├── iso_docs.py              # ⑧ ISO文書管理
│       │   └── models.py
│       │
│       └── dental/                      # 歯科BPO ← Phase 2+
│           ├── __init__.py
│           ├── receipt_checker.py       # ① レセプトAI
│           ├── appointment.py           # ② 予約最適化
│           ├── recall.py                # ③ リコール管理
│           ├── counseling.py            # ④ 自費カウンセリング支援
│           ├── infection_control.py     # ⑤ 院内感染管理
│           ├── lab_orders.py            # ⑥ 技工指示管理
│           ├── dental_inventory.py      # ⑦ 材料・在庫管理
│           ├── dental_permits.py        # ⑧ 届出・許認可管理
│           └── models.py
│
├── routers/
│   ├── bpo/                             # BPOルーター（業種別にグループ化）
│   │   ├── __init__.py
│   │   ├── base.py                      # ベースBPO共通エンドポイント
│   │   ├── construction.py              # 建設業BPOエンドポイント ← 先行実装
│   │   ├── manufacturing.py             # 製造業 ← Phase 2+
│   │   └── dental.py                    # 歯科 ← Phase 2+
│   └── ... (既存ルーター)
│
├── frontend/src/app/(authenticated)/
│   └── bpo/
│       ├── layout.tsx                   # BPO共通レイアウト（業種に応じてメニュー切替）
│       ├── page.tsx                     # BPOダッシュボード
│       ├── common/                      # ベースBPO画面
│       │   ├── invoices/                # 請求管理
│       │   ├── expenses/                # 経費精算
│       │   └── vendors/                 # ベンダー管理
│       ├── construction/                # 建設業BPO画面 ← 先行実装
│       │   ├── estimation/
│       │   ├── sites/
│       │   ├── safety/
│       │   ├── billing/
│       │   └── costs/
│       ├── manufacturing/               # Phase 2+
│       └── dental/                      # Phase 2+
│
└── db/migrations/
    ├── 006_bpo_base.sql                 # ベースBPOテーブル
    ├── 007_bpo_construction.sql         # 建設業BPOテーブル
    ├── 008_bpo_manufacturing.sql        # Phase 2+
    └── 009_bpo_dental.sql               # Phase 2+
```

---

## 8. DB設計 — ベースBPOテーブル

```sql
-- ============================================
-- ベースBPO共通テーブル（全業種で使用）
-- ============================================

-- 請求書（汎用）
CREATE TABLE bpo_invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  invoice_number TEXT NOT NULL,
  invoice_date DATE NOT NULL,
  due_date DATE NOT NULL,
  client_name TEXT NOT NULL,
  subtotal BIGINT NOT NULL,
  tax_rate DECIMAL(4,3) DEFAULT 0.10,
  tax_amount BIGINT NOT NULL,
  total BIGINT NOT NULL,
  items JSONB NOT NULL,                  -- [{description, quantity, unit_price, amount}]
  status TEXT DEFAULT 'draft',           -- draft / sent / paid / overdue / cancelled
  source_type TEXT,                      -- manual / progress_billing / delivery / treatment
  source_id UUID,                        -- 元データへの参照（出来高ID、納品ID等）
  file_url TEXT,
  sent_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 経費記録
CREATE TABLE bpo_expenses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  user_id UUID NOT NULL REFERENCES users(id),
  expense_date DATE NOT NULL,
  category TEXT NOT NULL,                -- transportation / supplies / entertainment / etc.
  description TEXT NOT NULL,
  amount BIGINT NOT NULL,
  tax_included BOOLEAN DEFAULT true,
  receipt_url TEXT,                       -- 領収書画像URL
  ocr_data JSONB,                        -- OCR読み取り結果
  account_code TEXT,                     -- 勘定科目（AI推定 or 手動）
  cost_center TEXT,                      -- 原価センター（現場ID、部門等）
  approval_status TEXT DEFAULT 'pending', -- pending / approved / rejected
  approved_by UUID REFERENCES users(id),
  approved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ベンダー（仕入先・外注先の共通マスタ）
CREATE TABLE bpo_vendors (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL,
  vendor_type TEXT NOT NULL,             -- subcontractor / supplier / service_provider / lab
  representative TEXT,
  address TEXT,
  phone TEXT,
  email TEXT,
  payment_terms TEXT,                    -- 支払条件
  bank_info JSONB DEFAULT '{}',
  license_info JSONB DEFAULT '{}',       -- 許認可情報（業種により異なる）
  evaluation JSONB DEFAULT '{}',         -- 評価スコア
  evaluation_date DATE,
  status TEXT DEFAULT 'active',
  industry_data JSONB DEFAULT '{}',      -- 業種固有の追加データ
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 許認可・届出管理
CREATE TABLE bpo_permits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  permit_type TEXT NOT NULL,             -- construction_license / iso_cert / health_center / etc.
  permit_name TEXT NOT NULL,
  permit_number TEXT,
  issued_date DATE,
  expiry_date DATE,
  renewal_lead_days INTEGER DEFAULT 180, -- 更新準備開始日（期限のN日前）
  status TEXT DEFAULT 'active',          -- active / expiring / expired / renewed
  required_documents JSONB DEFAULT '[]', -- 必要書類リスト
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 承認ワークフロー（汎用）
CREATE TABLE bpo_approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id),
  target_type TEXT NOT NULL,             -- invoice / expense / estimation / progress / etc.
  target_id UUID NOT NULL,
  requested_by UUID NOT NULL REFERENCES users(id),
  approver_id UUID REFERENCES users(id),
  status TEXT DEFAULT 'pending',         -- pending / approved / rejected / cancelled
  comment TEXT,
  requested_at TIMESTAMPTZ DEFAULT now(),
  decided_at TIMESTAMPTZ
);

-- RLS 全テーブル
ALTER TABLE bpo_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpo_expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpo_vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpo_permits ENABLE ROW LEVEL SECURITY;
ALTER TABLE bpo_approvals ENABLE ROW LEVEL SECURITY;
```

---

## 9. 業種判定 → BPOメニュー切替の仕組み

```
■ companies テーブルに industry カラム追加

  ALTER TABLE companies ADD COLUMN industry TEXT;
  -- 値: 'construction' / 'manufacturing' / 'dental' / 'general' / etc.

■ フロントエンドのBPOレイアウトで業種判定

  // bpo/layout.tsx
  const company = useCompany();

  const bpoModules = {
    // 全業種共通
    common: ['invoices', 'expenses', 'vendors', 'permits'],

    // 業種固有
    construction: ['estimation', 'sites', 'safety', 'progress', 'costs', 'photos', 'subcontractors'],
    manufacturing: ['quoting', 'production', 'quality', 'inventory', 'sop', 'maintenance'],
    dental: ['receipts', 'appointments', 'recall', 'counseling', 'lab_orders'],
  };

  const activeModules = [
    ...bpoModules.common,
    ...(bpoModules[company.industry] || []),
  ];

■ ルーターでも同様に業種チェック

  # 建設業BPOのエンドポイントは industry='construction' の会社のみアクセス可能
  # → 403 Forbidden で弾く
```

---

## 10. 実装ロードマップ

```
■ Phase A（Week 1-2）: 建設業BPO + ベースBPO基盤
  ┌─────────────────────────────────────────────────────┐
  │ 並列6エージェント                                      │
  │                                                     │
  │ Agent 1: DB migrations (006_bpo_base + 007_bpo_construction) │
  │ Agent 2: workers/bpo/engine/ (document_gen, template, approval) │
  │ Agent 3: workers/bpo/construction/estimator.py       │
  │ Agent 4: workers/bpo/construction/safety_docs.py     │
  │ Agent 5: routers/bpo/ (base + construction)          │
  │ Agent 6: frontend/bpo/ (layout + estimation + sites + safety) │
  └─────────────────────────────────────────────────────┘

■ Phase B（Week 3）: 建設業BPO 続き
  ┌─────────────────────────────────────────────────────┐
  │ Agent 1: workers/bpo/construction/billing.py         │
  │ Agent 2: workers/bpo/construction/cost_report.py     │
  │ Agent 3: workers/bpo/base/invoicing.py + expenses.py │
  │ Agent 4: frontend 出来高・請求・原価画面               │
  └─────────────────────────────────────────────────────┘

■ Phase C（Week 4）: 建設業BPO 完成
  ┌─────────────────────────────────────────────────────┐
  │ Agent 1: plan_writer.py + photo_organizer.py         │
  │ Agent 2: subcontractor.py + license_support.py       │
  │ Agent 3: frontend 残り全画面 + BPOダッシュボード       │
  └─────────────────────────────────────────────────────┘

■ Phase D（Week 5）: テスト・パイロット準備
  ┌─────────────────────────────────────────────────────┐
  │ Agent 1: E2Eテスト                                   │
  │ Agent 2: パイロット企業向けデモデータ投入              │
  │ Agent 3: 建設業genome/construction.json 拡張          │
  └─────────────────────────────────────────────────────┘

■ Phase 2+: 製造業・歯科BPO
  → 建設業のPMF確認後に着手
  → ベースBPO + engine は再利用。業種プラグインのみ追加
```

---

## 11. 各業種の「キラーフィーチャー」まとめ

```
■ キラーフィーチャー = そのBPOモジュール単体で月額10万+の価値

  建設: 積算AI
    → 1件5-20万の外注費が不要に。月2件でも10-40万の削減
    → 熟練積算士の人件費 月50-80万の代替

  製造: 見積AI
    → 見積回答の速度が3日→数時間に。受注機会の逸失防止
    → 外注見積の精度チェック（ぼったくり防止）

  歯科: レセプトAI
    → 査定率0.5%改善で年間数十万の収益改善
    → レセプト点検の外注費（月3-10万）の削減

■ 共通パターン: 「専門人材の人件費 or 外注費」を代替する
  → だからBPOコア¥25万 + 追加モジュール¥10万/個でも「安い」と感じる
```
