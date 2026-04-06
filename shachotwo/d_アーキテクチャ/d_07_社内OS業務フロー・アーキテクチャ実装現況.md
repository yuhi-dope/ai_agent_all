# d_07 社内OS 業務フロー・アーキテクチャ実装現況

> **目的**: 実装コードに基づく社内OS（全社自動化基盤）の業務フローとシステムアーキテクチャの現況記録。
> 設計ビジョン（d_04/d_06）ではなく、**今動いているもの**を記述する。
>
> **最終更新**: 2026-03-28
> **対象コード**: `shachotwo-app/` (commit: 0030ab8 以降)

---

## 1. システムアーキテクチャ全体像

> **設計原則**: パイプライン層を「全社共通基盤（Base OS）」と「業界特化プラグイン」に明確分離。
> 全社共通は全テナントで自動有効化。業界プラグインはテナント登録時に業界選択で有効化。
> マイクロエージェントは業界中立。業界固有ロジックはプラグイン内に閉じ込める。

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js)                           │
│  88ページ / 22モジュール / shadcn/ui + Tailwind                     │
│  ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐         │
│  │マーケ││営業  ││CRM   ││サポート││会計  ││人事  ││勤怠  │         │
│  │      ││SFA   ││      ││      ││給与  ││労務  ││在庫  │         │
│  └──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘         │
│  ┌──┴───┐┌──┴───┐┌──┴───┐┌──┴───┐┌──┴───┐                         │
│  │業界  ││ナレッジ││経営  ││銀行  ││BPO   │                         │
│  │BPO   ││Twin  ││ダッシュ││      ││承認  │                         │
│  └──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘                         │
└─────┼───────┼───────┼───────┼───────┼──────────────────────────────┘
      │ JWT + company_id
┌─────┼───────────────────────────────────────────────────────────────┐
│     ▼                                                               │
│     FastAPI (26ルーター / 133+エンドポイント)                        │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ セキュリティ: JWT / RLS 6ロール / AES-256 / PII / SoD     │     │
│  └────────────────────────────────────────────────────────────┘     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐      │
│  │Webhook   │ │Execution │ │Dashboard │ │Connector管理     │      │
│  │受信      │ │承認/HITL │ │KPI集計   │ │SaaS接続          │      │
│  └────┬─────┘ └────┬─────┘ └──────────┘ └──────────────────┘      │
└───────┼────────────┼──────────────────────────────────────────────┘
        │            │
┌───────┼────────────┼──────────────────────────────────────────────┐
│       ▼            ▼                                               │
│  BPO Manager層 (オーケストレーション)                               │
│  ┌────────────┐┌────────────┐┌────────────┐┌──────────────────┐   │
│  │Orchestrator││TaskRouter  ││Approval    ││Notifier          │   │
│  │毎分/5分/   ││Base+Industry││Engine     ││Slack通知         │   │
│  │30分サイクル ││2層レジストリ││多段階承認 ││完了/承認/エラー  │   │
│  └──┬─────────┘└──┬─────────┘└────────────┘└──────────────────┘   │
│     │              │                                               │
│  ┌──▼────────┐┌───▼────────┐┌────────────┐┌──────────────────┐   │
│  │Schedule   ││Event       ││Condition   ││Proactive         │   │
│  │Watcher    ││Listener    ││Evaluator   ││Scanner           │   │
│  │cron評価   ││webhook変換 ││連鎖トリガー││先読み提案        │   │
│  └───────────┘└────────────┘└────────────┘└──────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
        │
        │  ┌──────────────────────────────────────────────────────┐
        │  │ TaskRouter の2層レジストリ                           │
        │  │                                                      │
        │  │ BASE_PIPELINES:     全テナント自動有効化（36本）     │
        │  │ INDUSTRY_PIPELINES: テナントの業界選択で有効化       │
        │  │                                                      │
        │  │ 実行時:                                              │
        │  │   1. BASE_PIPELINES を先にチェック                   │
        │  │   2. なければ INDUSTRY_PIPELINES[tenant.industry]    │
        │  │   3. テナントの業界と不一致なら実行拒否              │
        │  └──────────────────────────────────────────────────────┘
        │
╔═══════╪═══════════════════════════════════════════════════════════╗
║       ▼                                                           ║
║  Layer A: 全社共通基盤 (Base OS) — 36本                           ║
║  テナント登録時に自動有効化。どの業界でも必ず使う。               ║
║                                                                   ║
║  ┌─────────────────────────────────────────────────────────────┐  ║
║  │ 営業SFA/CRM/CS (12本) — 全業種で営業活動は発生する         │  ║
║  │                                                             │  ║
║  │ outreach → lead_qual → proposal → quotation_contract       │  ║
║  │ → customer_lifecycle → support → upsell                    │  ║
║  │ → win_loss → cs_feedback → revenue_report                  │  ║
║  │ → consent_flow → cancellation                              │  ║
║  │                                                             │  ║
║  │ 特徴: 業種アウェア（業種判定・業種別テンプレート選択）     │  ║
║  │       マイクロエージェント最多利用（18個中15個）            │  ║
║  │       営業固有テンプレートを内包（templates/）              │  ║
║  └─────────────────────────────────────────────────────────────┘  ║
║  ┌─────────────────────────────────────────────────────────────┐  ║
║  │ バックオフィス (24本) — 全業種で経理・人事・総務は発生する  │  ║
║  │                                                             │  ║
║  │ 経理(8): expense/invoice/ar/ap/bank_recon/journal/close/tax │  ║
║  │ 労務(5): attendance/payroll/social_ins/year_end/labor_comp  │  ║
║  │ 人事(3): recruitment/onboarding/offboarding                 │  ║
║  │ 総務(3): contract/admin_reminder/asset_mgmt                 │  ║
║  │ 調達(2): vendor/purchase_order                              │  ║
║  │ 法務(2): compliance_check/antisocial_screening              │  ║
║  │ IT(1):   account_lifecycle                                  │  ║
║  │                                                             │  ║
║  │ 特徴: 業界非依存。どの業界でも同じ経費精算・給与計算。     │  ║
║  │       GL仕訳・社保届出・法定帳簿を自前で完結。             │  ║
║  └─────────────────────────────────────────────────────────────┘  ║
╚═══════════════════════════════════════════════════════════════════╝
        │
╔═══════╪═══════════════════════════════════════════════════════════╗
║       ▼                                                           ║
║  Layer B: 業界特化プラグイン (Industry Plugin) — 業界選択で有効化 ║
║  各プラグインは自己完結（独自モデル/エンジン/ルール/テンプレ）    ║
║  他業界プラグインとは完全隔離。Layer Aとも直接依存しない。       ║
║                                                                   ║
║  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              ║
║  │ 建設プラグイン │ │ 製造プラグイン │ │ 医療プラグイン │ ...        ║
║  │               │ │               │ │               │            ║
║  │ models/       │ │ models/       │ │ models/       │            ║
║  │  ProjectType  │ │  QuoteParams  │ │  ReceiptType  │            ║
║  │               │ │               │ │               │            ║
║  │ engine/       │ │ engine/       │ │ rules/        │            ║
║  │  Estimation   │ │  Quoting      │ │  医療費算定   │            ║
║  │  Engine       │ │  Engine       │ │               │            ║
║  │               │ │               │ │               │            ║
║  │ rules/        │ │ plugins/      │ │ pipelines/    │            ║
║  │  建設業法     │ │  樹脂/電子/  │ │  medical_rcpt │            ║
║  │  下請規制     │ │  食品化学    │ │  care_billing │            ║
║  │               │ │               │ │               │            ║
║  │ pipelines/    │ │ pipelines/    │ │               │            ║
║  │  8本          │ │  8本          │ │               │            ║
║  │               │ │               │ │               │            ║
║  │ templates/    │ │ templates/    │ │               │            ║
║  │  見積書       │ │  見積書       │ │               │            ║
║  │  安全書類     │ │  品質報告     │ │               │            ║
║  └──────────────┘ └──────────────┘ └──────────────┘              ║
║                                                                   ║
║  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              ║
║  │ 不動産       │ │ 物流         │ │ 凍結11業種   │              ║
║  │ rent_collect  │ │ dispatch     │ │ パートナー   │              ║
║  │              │ │              │ │ 主導で復活   │              ║
║  └──────────────┘ └──────────────┘ └──────────────┘              ║
╚═══════════════════════════════════════════════════════════════════╝
        │
┌───────┼───────────────────────────────────────────────────────────┐
│       ▼                                                            │
║  Layer C: 共有マイクロエージェント (業界中立 × 19個)               ║
║  業界固有ロジックは持たない。ルールは外から渡される。              ║
│                                                                    │
│  データI/O:  ocr / extractor / saas_reader / saas_writer          │
│              table_parser                                          │
│  処理:       rule_matcher / validator / calculator / diff          │
│  生成:       generator / message                                   │
│  専門:       company_researcher / signal_detector / calendar       │
│                                                                    │
│  ※ 業界中立化のため以下を移動済み（設計方針）:                    │
│    compliance.py の建設業法ルール → 建設プラグイン/rules/          │
│    compliance.py の製造品質ルール → 製造プラグイン/rules/          │
│    pdf_generator の営業テンプレ → Layer A 営業/templates/          │
│    pptx_generator の営業形式 → Layer A 営業/templates/             │
│    compliance.py 本体は汎用チェック関数のみ残す                    │
│    （check_rules(data, rules) ← rulesは各層から渡す）             │
└───────────────────────────────────────────────────────────────────┘
        │
┌───────┼───────────────────────────────────────────────────────────┐
│       ▼                                                            │
│  インフラ層                                                        │
│  ┌─────────────────┐  ┌────────────────────────────────────┐      │
│  │ Brain層          │  │ コネクタ層 (SaaS連携 × 8)          │      │
│  │                  │  │ ※外部SaaS依存は3件のみ:            │      │
│  │ ナレッジ検索     │  │   CloudSign / Bank API / gBizINFO  │      │
│  │ (vector+BM25)   │  │ ※残りはオプション同期:              │      │
│  │                  │  │   freee / SmartHR / kintone / Slack │      │
│  │ ゲノム           │  └────────────────────────────────────┘      │
│  │ (業界テンプレ)  │                                               │
│  │                  │  ┌────────────────────────────────────┐      │
│  │ デジタルツイン   │  │ LLM層                               │      │
│  │ (5次元)         │  │                                    │      │
│  │                  │  │ Gemini 2.5 Flash (主)              │      │
│  │ 精度モニター     │  │ Claude (副・フォールバック)         │      │
│  │                  │  │ 3ティア: FAST / STANDARD / PREMIUM │      │
│  │ 先読み分析       │  │ 月額予算: ¥50,000/テナント         │      │
│  └─────────────────┘  └────────────────────────────────────┘      │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ DB層 (Supabase PostgreSQL + pgvector)                    │      │
│  │ 22テーブル / RLS全テーブル / VECTOR(512) / 46マイグレ    │      │
│  └──────────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. 技術スタック

| レイヤー | 技術 | 備考 |
|---|---|---|
| Frontend | Next.js + Tailwind + shadcn/ui | 38ページ, SSR, PWA対応 |
| API | FastAPI (Python 3.11+) | 26ルーター, 118エンドポイント, async/await |
| LLM | Gemini 2.5 Flash (主) / Claude (副) | 3ティア: FAST/STANDARD/PREMIUM |
| Embedding | Voyage AI (voyage-3) | 512次元, 日本語業界用語対応 |
| Vector DB | Supabase pgvector (HNSW) | RLS + Auth + Storage統合 |
| DB | Supabase (PostgreSQL) | 22テーブル, 全テーブルRLS必須 |
| 認証 | Supabase Auth (JWT) | 2ロール: admin / editor |
| 暗号化 | AES-256-GCM | コネクタ認証情報, 機微フィールド |
| コネクタ | 8 SaaS (kintone/freee/Slack/CloudSign/Gmail/GSheets/gBizINFO/Playwright) | async対応 |
| Infra | GCP Cloud Run | サーバーレス, Blue/Green |
| CI/CD | GitHub Actions | lint + type check + test + security scan |

---

## 3. 業務フロー全体像: 1日の自動運転サイクル

### 3.1 朝のサイクル（08:00〜10:00）

```
08:00 ─── マーケ自動リサーチ ────────────────────────────────────────
  │
  │  [Orchestrator] → schedule_watcher → "sales/outreach" 発火
  │
  │  ┌─ outreach_pipeline (400社/日) ──────────────────────┐
  │  │ 1. Google Sheets → 未送信リスト取得                  │
  │  │ 2. gBizINFO → 企業情報エンリッチ                    │
  │  │ 3. LLM → 業種別ペインポイント推定                   │
  │  │ 4. Playwright → Webフォーム自動入力 OR メール送信    │
  │  │ 5. leads テーブルにINSERT (temperature=cold)        │
  │  └──────────────────────────────────────────────────────┘
  │
  │  [LP閲覧30秒以上] → webhook → temperature="warm"
  │  [CTAクリック]    → webhook → temperature="hot"
  │           │
  │           ▼ 自動チェーン発火
  │
  │  ┌─ lead_qualification_pipeline ────────────────────────┐
  │  │ 1. LLM抽出 → {業種, 従業員数, 緊急度, 予算}        │
  │  │ 2. スコアリング → 業種+30 / 規模+25 / 即導入+30    │
  │  │ 3. ルーティング:                                     │
  │  │    score≥70 → QUALIFIED → 提案書へ自動チェーン      │
  │  │    40-69   → REVIEW → Slack通知（営業判断待ち）      │
  │  │    <40     → NURTURING → メールシーケンス             │
  │  └──────────────────────────────────────────────────────┘
  │           │ QUALIFIED
  │           ▼ 自動チェーン
  │
  │  ┌─ proposal_generation_pipeline ──────────────────────┐
  │  │ 1. ゲノムJSON(建設/製造等) → 業種テンプレ選択       │
  │  │ 2. LLM → ROI試算付き提案書JSON生成                  │
  │  │ 3. WeasyPrint → PDF生成                             │
  │  │ 4. Supabase Storage → PDF保存                       │
  │  │ 5. SendGrid → パーソナライズメール送信               │
  │  │ 6. opportunities.stage = "proposal_sent"             │
  │  └──────────────────────────────────────────────────────┘
  │           │ 送信完了
  │           ▼ 自動チェーン
  │
  │  ┌─ quotation_contract_pipeline ───────────────────────┐
  │  │ Phase A: 見積書                                      │
  │  │  1. Decimal精度で料金計算(月額/年額/税込)            │
  │  │  2. PDF生成 → メール送信                             │
  │  │  3. ★ 承認待ち → Slack通知 → 営業が承認クリック     │
  │  │                                                      │
  │  │ Phase B: 契約書（承認後）                             │
  │  │  4. 契約書PDF生成                                    │
  │  │  5. CloudSign → 電子署名リクエスト送信               │
  │  │  6. freee → 請求書自動作成                           │
  │  └──────────────────────────────────────────────────────┘
  │
09:00 ─── 顧客ヘルスチェック ───────────────────────────────────────
  │
  │  [Orchestrator] → "sales/customer_lifecycle" (health_check mode)
  │
  │  全アクティブ顧客をスキャン:
  │  ┌─ customer_lifecycle_pipeline ────────────────────────┐
  │  │ 5次元ヘルススコア計算:                               │
  │  │   利用頻度(30%) + 機能活用(25%) + サポート(15%)     │
  │  │   + NPS(15%) + 拡張余地(15%) = 0-100点              │
  │  │                                                      │
  │  │ score<40  → ❌ Slack緊急アラート → CS介入            │
  │  │ 40-60     → ⚠️ 注意フラグ → 週次レポート            │
  │  │ ≥80+未使用 → 🎯 アップセルチャンス → 自動チェーン   │
  │  └──────────────────────────────────────────────────────┘
  │
10:00 ─── SLA違反チェック + アップセル ─────────────────────────────
  │
  │  ┌─ support_auto_response (SLAモード) ─────────────────┐
  │  │ 未解決チケットのSLA違反を検出 → エスカレーション     │
  │  └──────────────────────────────────────────────────────┘
  │
  │  ┌─ upsell_briefing_pipeline ──────────────────────────┐
  │  │ 4パターン検知:                                       │
  │  │   BPO利用≥80% + 未使用モジュール → 追加提案         │
  │  │   ブレインのみ + Q&A≥10回/週 → BPOコアアップグレード│
  │  │   6ヶ月以上 + ヘルス≥80 → バックオフィスBPO          │
  │  │   全BPO + カスタム要望3件+ → 自社開発BPO             │
  │  │                                                      │
  │  │ → Slackブリーフィング送信                            │
  │  │ → Google Calendarにミーティング枠追加                │
  │  └──────────────────────────────────────────────────────┘
```

### 3.2 日中: リアルタイムイベント処理

```
[顧客がサポート問い合わせ] ─────────────────────────────────────────
  │
  ▼ メール / チャット / フォーム
  ┌─ support_auto_response_pipeline ───────────────────────┐
  │ 1. LLM分類 → カテゴリ(account/billing/brain/bpo/bug)  │
  │              優先度(low/medium/high/urgent)             │
  │ 2. FAQベクトル検索 + ナレッジ照合                       │
  │ 3. LLM回答生成 + 信頼度スコア算出                      │
  │ 4. ルーティング:                                        │
  │    信頼度≥0.85 → 自動送信                              │
  │    0.5-0.85   → 人間レビューキュー                      │
  │    <0.5       → 即時エスカレーション                    │
  │    billing系  → 経理チームへ転送                        │
  │    urgent     → Slack緊急アラート                       │
  │ 5. SLAモニタリング: 初動1h / 解決24h / 緊急4h          │
  └──────────────────────────────────────────────────────────┘

[CloudSign署名完了] → Webhook受信 ──────────────────────────────────
  │
  ▼
  ┌─ Webhook Handler ──────────────────────────────────────┐
  │ 1. HMAC-SHA256署名検証                                  │
  │ 2. contracts.status = "signed"                         │
  │ 3. opportunities.stage = "won", probability = 100      │
  │ 4. customers レコード自動作成                           │
  │ 5. → customer_lifecycle(mode=onboarding) 自動チェーン  │
  │    → ゲノム適用 + ウェルカムメール                      │
  │    → Day1/3/7/14/30 フォローアップ計画                  │
  └──────────────────────────────────────────────────────────┘

[freee支払完了] → Webhook受信 ──────────────────────────────────────
  │
  ▼ revenue_records に記録 → MRR更新 → ヘルススコアに反映

[freee支払遅延] → Webhook受信 ──────────────────────────────────────
  │
  ▼ customer_health にマイナスデルタ → CSアラート
```

### 3.3 業界BPO: 建設業の1日

```
[現場担当が見積依頼書をアップロード] ──────────────────────────────
  │
  ▼ POST /bpo/construction/estimation
  ┌─ estimation_pipeline (8ステップ) ──────────────────────┐
  │ 1. OCR → 図面/仕様書からテキスト抽出                   │
  │ 2. 数量抽出 → {工種, 仕様, 数量, 単位}                │
  │ 3. 単価照会 → unit_price_master DBから検索             │
  │ 4. 原価計算 → 数量×単価 + 諸経費                      │
  │ 5. 諸経費計算 → 共通仮設費+現場管理費+一般管理費      │
  │ 6. 建設業法コンプラチェック                             │
  │ 7. 内訳書生成                                          │
  │ 8. ★ 承認待ち → Slack通知 → 社長がレビュー           │
  │                                                        │
  │ [修正された場合]                                        │
  │   → extraction_feedback に差分記録                      │
  │   → unit_price_master に学習データ蓄積                 │
  │   → 次回の推定精度が向上（フィードバックループ）       │
  └────────────────────────────────────────────────────────┘

[月末] ─────────────────────────────────────────────────────────────
  │
  ├─ billing_pipeline
  │  OCR → 出来高抽出 → 請求額計算 → インボイス制度準拠 → ★承認
  │
  ├─ cost_report_pipeline
  │  予算vs実績 → 差異分析 → 赤字プロジェクト検出 → アラート
  │
  └─ safety_docs_pipeline
     作業員名簿 → 資格有効期限(90日前アラート) → 安全計画書生成
```

### 3.4 製造BPOの主要フロー

```
[RFQ受領] → quoting_pipeline ──────────────────────────────────────
  仕様解析 → 工程分解(材料/加工/外注) → 利益率適用 → 見積PDF → ★承認

[品質検査] → quality_control_pipeline ─────────────────────────────
  検査データ → SPC分析(Cp/Cpk) → 管理図逸脱検出 → ISO 9001チェック → アラート

[生産計画] → production_planning_pipeline ─────────────────────────
  受注データ → 工程マスタ照合 → 山積み計算 → 設備負荷率チェック → ガントチャート
```

### 3.5 バックオフィス共通フロー（7領域24パイプライン）

> 詳細設計: `b_詳細設計/b_07_バックオフィスBPO詳細設計.md`

```
── 経理（8本）──────────────────────────────────────────────────────

[経費精算] → expense_pipeline ✅
  OCR → カテゴリ推定 → 経費規程チェック → 消費税分離 → ★承認待ち
  → journal_entry連鎖（経費仕訳）

[請求書発行] → invoice_issue_pipeline（月末自動）
  完了案件取得 → 請求対象構造化 → 金額計算 → インボイスチェック
  → PDF生成 → freee連携 → ★承認待ち
  → journal_entry連鎖（売上仕訳）→ ar_management連鎖（売掛計上）

[売掛管理] → ar_management_pipeline（毎日09:00自動）
  未入金一覧+銀行明細取得 → 入金消込マッチング → 滞納日数計算
  → aging分析(30/60/90日) → 督促文面生成 → 段階別アクション

[買掛管理] → ap_management_pipeline（毎月25日自動）
  未払買掛金取得 → 請求書OCR → 三者照合(発注書×検収×請求書)
  → 支払額計算 → インボイス番号検証 → 全銀振込ファイル生成 → ★承認

[銀行照合] → bank_reconciliation_pipeline（毎日18:00自動）
  銀行明細取得 → freee帳簿取得 → 自動マッチング
  → 不一致分類 → 調整後残高算出 → 銀行勘定調整表生成

[仕訳入力] → journal_entry_pipeline（他パイプライン連鎖）
  取引内容から仕訳推定 → 過去パターン照合 → 勘定科目チェック
  → freee仕訳API → 貸借一致検証
  ※学習ループ: ユーザー修正→次回精度向上

[月次決算] → monthly_close_pipeline（毎月5営業日目自動）
  freee試算表取得 → 未処理チェック → P&L計算 → 予算比差異分析
  → 月次レポート生成 → 異常値検出(±30%) → ★承認

[税務申告] → tax_filing_pipeline（四半期/年次）Phase 3
  年次決算データ → 消費税計算 → 法人税概算 → 申告書ドラフト → ★税理士レビュー

── 労務（5本）──────────────────────────────────────────────────────

[勤怠分析] → attendance_pipeline ✅
  出退勤データ → 残業集計(月45時間チェック) → 欠勤/有給追跡 → コンプラチェック

[給与計算] → payroll_pipeline ✅（毎月25日自動）
  勤怠集計 → 基本給計算 → 残業代(36協定チェック)
  → 社保/所得税/住民税 → 給与明細生成 → ★承認待ち
  → journal_entry連鎖（給与仕訳）

[社保届出] → social_insurance_pipeline（入社/退社/給与変更イベント）
  届出種別判定 → 従業員データ取得 → 標準報酬月額算出 → 保険料計算
  → 届出書ドラフト → 届出期限チェック → ★承認

[年末調整] → year_end_adjustment_pipeline（11-12月スケジュール）
  年間給与+源泉取得 → 控除情報抽出 → 年税額計算 → 過不足算出
  → 源泉徴収票生成 → ★承認

[労務コンプラ] → labor_compliance_pipeline（毎月1日自動）
  勤怠+給与+届出取得 → 36協定チェック → 最低賃金チェック
  → 有給5日義務チェック → 届出期限チェック → 50名閾値判定
  → レポート生成 → 違反時Slack緊急通知

── 人事（3本）──────────────────────────────────────────────────────

[採用] → recruitment_pipeline
  求人票生成 → 応募書類構造化 → スクリーニング → スコアリング
  → 面接質問リスト → 面接日程調整 → 合否連絡メール

[入社手続き] → employee_onboarding_pipeline（内定承諾イベント）
  入社書類セット生成 → SmartHR登録 → SaaSアカウント作成
  → オリエン資料 → 初日スケジュール → 入社前メール
  → social_insurance連鎖（資格取得届）

[退社手続き] → employee_offboarding_pipeline（退職届イベント）
  最終給与計算 → 離職票生成 → 退職証明書+源泉徴収票
  → SaaSアカウント無効化 → 届出期限チェック → 書類送付案内
  → social_insurance連鎖（資格喪失届）

── 総務（3本）──────────────────────────────────────────────────────

[契約書分析] → contract_pipeline ✅
  OCR → 条項抽出(甲乙/金額/期間/自動更新) → リスク条項検出 → 差分検出 → ★承認

[届出リマインダー] → admin_reminder_pipeline ✅
  期限スキャン → 優先度ソート(超過/7日/30日) → リマインダー生成

[固定資産] → asset_management_pipeline Phase 3
  固定資産台帳 → 減価償却計算 → 少額資産判定 → 償却資産申告チェック

── 調達（2本）──────────────────────────────────────────────────────

[仕入先管理] → vendor_pipeline ✅
  仕入先読込 → スコア計算(支払信頼40%/取引量30%/年数20%/事故10%) → リスク評価

[発注・検収] → purchase_order_pipeline
  発注要求構造化 → 推奨仕入先選定 → 発注書生成 → PDF
  → kintone+freee連携 → 仕入先メール → 予算チェック
  [検収時] 発注書×納品照合 → 差異検出 → ap_management連鎖

── 法務（2本）──────────────────────────────────────────────────────

[コンプラチェック] → compliance_check_pipeline（毎月1日自動）
  全社データ取得 → 許認可有効期限 → APPI対応 → ハラスメント防止
  → 人数閾値義務 → ダッシュボードレポート → Slackアラート

[反社チェック] → antisocial_screening_pipeline（新規取引先イベント）
  企業情報構造化 → gBizINFO+TDB/TSR照会 → ネガティブニュース検索
  → 反社DB照合 → GREEN/YELLOW/RED判定 → ★YELLOWは承認必要

── IT管理（1本）────────────────────────────────────────────────────

[アカウント管理] → account_lifecycle_pipeline（月次棚卸し）Phase 3
  全SaaSアカウント取得 → 従業員マスタ照合 → 孤立アカウント検出
  → 90日未使用検出 → コスト削減試算 → 棚卸しレポート → Slack通知
```

### 3.6 夕方〜夜: 学習サイクル

```
18:00 ─── アウトリーチPDCA ─────────────────────────────────────────
  │
  │  ┌─ win_loss_feedback_pipeline (PDCA mode) ────────────┐
  │  │ 1. A/Bテスト結果集計（メール件名バリアント）        │
  │  │ 2. 業種別レスポンス率分析                           │
  │  │ 3. 低パフォーマンス業種の改善提案                   │
  │  │ 4. スコアリングモデル更新 (+5pt 受注業種)           │
  │  └──────────────────────────────────────────────────────┘

月末 ─── CS品質レビュー ────────────────────────────────────────────
  │
  │  ┌─ cs_feedback_pipeline ──────────────────────────────┐
  │  │ 1. CSAT分布集計 (1-5)                               │
  │  │ 2. AI回答率 / 初動時間 / 解決時間 / SLA達成率      │
  │  │ 3. CSAT≥4 の回答 → FAQ自動追加 (confidence=0.90)   │
  │  │ 4. 自動送信の信頼度閾値を動的調整                   │
  │  └──────────────────────────────────────────────────────┘
```

### 3.7 バックグラウンドサイクル

```
[毎分] ─── スケジュールトリガー評価 ───────────────────────────────
  Orchestrator → schedule_watcher → cron式と現在時刻を照合
  → 該当パイプラインを全アクティブテナントに対して実行

[5分ごと] ─── 条件連鎖評価 ───────────────────────────────────────
  Orchestrator → condition_evaluator → 4つの組み込みチェーン:
    ・ヘルススコア≤30      → 解約リスクアラート
    ・SLA超過チケット      → エスカレーション
    ・未対応アップセル提案  → フォローアップリマインダー
    ・失注30日以内         → 再提案PDCA投入

[30分ごと] ─── 先読みスキャン ────────────────────────────────────
  Orchestrator → proactive_scanner → 全テナントの状態分析:
    ・デジタルツインスナップショット取得
    ・LLMでリスク検出 + 改善提案
    ・impact_score < 0.5 & level ≤ 1 → 自動実行
    ・それ以外 → proactive_proposals に保存 → 承認待ち
```

---

## 4. パイプラインチェーン（自動連鎖）

### 4.1 営業チェーン

```
outreach (08:00自動)
  │
  │ [LP CTAクリック] webhook
  ▼
lead_qualification
  │ score≥70
  ▼
proposal_generation
  │ 送信完了
  ▼
quotation_contract
  │ [CloudSign署名] webhook
  ▼
customer_lifecycle (onboarding)
  │ Day 1/3/7/14/30 自動フォロー
  │
  │ [日次 09:00] health_check
  │ score≥80 + unused_modules
  ▼
upsell_briefing
  │ (人間がここで商談判断)
  │
  │ [受注/失注] webhook
  ▼
win_loss_feedback → スコアモデル更新 → outreach精度向上
```

### 4.2 チェーン実装メカニズム

ファイル: `workers/bpo/sales/chain.py`

| ソースパイプライン | 条件 | ターゲット |
|---|---|---|
| lead_qualification | routing == "QUALIFIED" | proposal_generation |
| proposal_generation | proposal.status == "sent" | quotation_contract |
| quotation_contract | contract.status == "signed" | customer_lifecycle (onboarding) |
| customer_lifecycle | health ≥ 80 + unused_modules | upsell_briefing |
| opportunity_stage_changed | stage in ("won", "lost") | win_loss_feedback |

全チェーンは `asyncio.create_task()` で非同期実行（ノンブロッキング、失敗は伝播しない）。

---

## 5. 承認フロー（HITL）

### 5.1 承認判定ロジック

```
パイプライン完了
    │
    ▼
[bpo_hitl_requirements テーブル参照]
    │
    ├── requires_approval = false
    │   └── ✅ 自動完了（通知のみ）
    │
    ├── min_confidence_for_auto が設定済み
    │   └── trust_score ≥ 閾値
    │       └── ✅ 自動承認（HITL閾値クリア）
    │
    └── requires_approval = true & 閾値未達
        │
        ├── Slack通知: "🔔 BPO承認待ち: construction/estimation"
        │
        ▼ 管理者が承認画面を開く
        │
        ├── 承認
        │   → approval_status = "approved"
        │   → 出力確定
        │
        ├── 修正承認
        │   → approval_status = "modified"
        │   → modification_diff 記録
        │   → LLMが修正理由からルール自動抽出
        │   → 次回から自動適用（学習ループ）
        │
        └── 却下
            → approval_status = "rejected"
            → rejection_reason 記録
```

### 5.2 信頼スコアの自動昇格

| レベル | 名称 | 条件 | 動作 |
|---|---|---|---|
| Level 0 | 通知のみ | 初期状態 | 実行せず通知だけ |
| Level 1 | 下書き+承認必須 | 実行5回以上 | 実行後に承認待ち |
| Level 2 | 自動実行+事後確認 | 承認率≥80% + 20回以上 | 実行し事後レビュー |
| Level 3 | 完全自律 | 承認率≥90% + 30回連続成功 + CEO明示承認 | 異常時のみ通知 |

信頼スコア計算式:
```
weighted_score = (approved × 1.0 - rejected × 3.0 - modified × 0.5) / total
```

---

## 6. Webhook統合

### 6.1 受信Webhook一覧

| エンドポイント | ソース | 署名検証 | トリガーされるアクション |
|---|---|---|---|
| `POST /webhooks/cloudsign` | CloudSign | HMAC-SHA256 | 契約確定 → 顧客作成 → オンボーディング → freee請求 |
| `POST /webhooks/freee` | freee | HMAC-SHA256 | 入金確認 → MRR更新 / 支払遅延 → ヘルス低下 |
| `POST /webhooks/intercom` | Intercom | HMAC-SHA1 | チャット → サポートチケット作成 |
| `POST /webhooks/lp-event` | 自社LP | なし（内部） | LP閲覧/CTA/DL → リード温度更新 → 自動チェーン |

### 6.2 イベントリスナー（組み込みトリガー）

| イベント | パイプライン | 実行レベル |
|---|---|---|
| `lead_created` | sales/lead_qualification | Level 2 (DRAFT_CREATE) |
| `lead_score_gte_70` | sales/proposal_generation | Level 2 |
| `proposal_accepted` | sales/quotation_contract | Level 3 (APPROVAL_GATED) |
| `contract_signed` | sales/customer_lifecycle | Level 2 |
| `ticket_created` | sales/support_auto_response | Level 2 |
| `opportunity_won` | sales/win_loss_feedback | Level 1 (DATA_COLLECT) |
| `opportunity_lost` | sales/win_loss_feedback | Level 1 |
| `health_score_high` | sales/upsell_briefing | Level 2 |
| `cancellation_requested` | sales/cancellation | Level 3 |
| **`employee_joined`** | **backoffice/employee_onboarding** | **Level 2** |
| **`employee_left`** | **backoffice/employee_offboarding** | **Level 2** |
| **`salary_changed`** | **backoffice/social_insurance** | **Level 3 (APPROVAL_GATED)** |
| **`vendor_registered`** | **backoffice/antisocial_screening** | **Level 2** |
| **`purchase_requested`** | **backoffice/purchase_order** | **Level 2** |
| **`invoice_paid`** | **backoffice/ar_management** | **Level 1 (DATA_COLLECT)** |
| **`goods_received`** | **backoffice/purchase_order (検収)** | **Level 2** |

---

## 7. スケジュール一覧

### 7.1 組み込みスケジュール

| 時間 | パイプライン | 内容 |
|---|---|---|
| 毎日 08:00 | sales/outreach | 企業リサーチ&アウトリーチ 400件/日 |
| 毎日 09:00 | sales/customer_lifecycle | 顧客ヘルススコア日次計算 |
| **毎日 09:00** | **backoffice/ar_management** | **売掛管理・入金消込日次** |
| 毎日 10:00 | sales/support_auto_response | SLA違反チェック日次スキャン |
| 毎日 18:00 | sales/win_loss_feedback | アウトリーチPDCA |
| **毎日 18:00** | **backoffice/bank_reconciliation** | **銀行照合日次** |
| 毎週月曜 09:00 | sales/win_loss_feedback | 週次PDCAレビュー |
| 毎月1日 09:00 | sales/revenue_report | MRR/チャーンレポート |
| **毎月1日 09:00** | **backoffice/labor_compliance** | **労務コンプラ月次チェック** |
| **毎月1日 09:00** | **backoffice/compliance_check** | **コンプラチェック月次** |
| **毎月5営業日 09:00** | **backoffice/monthly_close** | **月次決算** |
| **毎月25日 09:00** | **common/payroll** | **給与計算月次** |
| **毎月25日 09:00** | **backoffice/ap_management** | **買掛支払処理** |
| **毎月末 09:00** | **backoffice/invoice_issue** | **請求書発行月末** |
| 毎月15日 09:00 | sales/revenue_report | 要望ランキング更新 |
| 毎月末 09:00 | sales/cs_feedback | CS品質月次レビュー |

### 7.2 オーケストレータサイクル

| 間隔 | コンポーネント | 処理内容 |
|---|---|---|
| 毎分 | ScheduleWatcher | 全テナントのcron式評価 → パイプライン発火 |
| 5分ごと | ConditionEvaluator | 条件連鎖トリガー評価 |
| 30分ごと | ProactiveScanner | 先読みスキャン → 提案生成 |

起動条件: 環境変数 `ENABLE_BPO_ORCHESTRATOR=1`

---

## 8. データフロー: テーブル間の関係

```
leads ──(score≥70)──→ opportunities ──(accepted)──→ proposals
                           │                            │
                           │ (won)                      │
                           ▼                            ▼
                      customers ◄──── contracts ◄─── quotations
                           │              ↑
                           │         CloudSign署名
                           ▼
                    customer_health ──(score≥80)──→ upsell_opportunities
                           │
                           │(score<40)
                           ▼
                    support_tickets ──→ ticket_messages
                           │
                           ▼
                    satisfaction_scores ──→ cs_feedback学習
```

### 横断テーブル

| テーブル | 用途 | 書き込み元 |
|---|---|---|
| execution_logs | 全パイプラインの実行記録（コスト・時間・承認状態） | 全パイプライン |
| knowledge_items | Brain層の全ナレッジ（ベクトル検索可能） | ingestion / genome |
| unit_price_master | 学習で蓄積される単価DB | estimation / billing |
| extraction_feedback | ユーザー修正の差分記録 | 承認ワークフロー |
| proactive_proposals | AI先読み提案 | proactive_scanner |
| bpo_hitl_requirements | パイプライン別承認ルール（29本登録済み） | マイグレーション |
| llm_call_logs | LLM呼び出し記録（コスト追跡） | llm/client.py |
| revenue_records | 収入記録 | freee webhook |
| tool_connections | SaaS接続設定（認証情報暗号化） | connector管理画面 |
| usage_metrics | BPO実行・QA・コネクタ同期の使用量計測（従量課金基盤） | billing router / パイプライン |

---

## 9. マイクロエージェント一覧

### 9.1 データI/O (5個)

| エージェント | 入力 | 出力 | 主な利用パイプライン |
|---|---|---|---|
| ocr | PDF/画像 | テキスト | estimation, billing, expense, contract, ap_management |
| extractor | テキスト + スキーマ | 構造化JSON | 全パイプライン（経理/労務/人事/法務含む） |
| saas_reader | テーブル名 + フィルタ | レコード配列 | CRM, support, upsell, ar/ap/bank_recon, monthly_close |
| saas_writer | テーブル名 + データ | 書き込み結果 | 全パイプライン（freee仕訳/SmartHR届出含む） |
| table_parser | HTML/Markdownテーブル | JSON配列 | quality_control |

### 9.2 処理 (5個)

| エージェント | 入力 | 出力 | 主な利用パイプライン |
|---|---|---|---|
| rule_matcher | データ + ドメインルール | マッチ結果 | lead_qual, payroll, quality, ar_matching, labor_compliance, antisocial |
| validator | 出力 + 必須フィールド | 検証結果 | 全パイプライン（最終ステップ） |
| calculator | 設定 + 項目 | 計算結果 | estimation, payroll, quoting, journal_entry, social_insurance, year_end |
| compliance | データ + 法規 | 違反リスト | safety, payroll, billing, labor_compliance, tax_filing |
| diff | 旧データ + 新データ | 差分レポート | contract, extraction_feedback |

### 9.3 生成 (4個)

| エージェント | 入力 | 出力 | 主な利用パイプライン |
|---|---|---|---|
| generator | コンテキスト | 文書JSON | proposal, support, safety_plan, invoice, jd, dunning, payslip |
| pdf_generator | HTMLテンプレ + データ | PDFバイト列 | proposal, quotation, billing, invoice_issue, purchase_order |
| pptx_generator | スライド定義 | PPTXファイル | 提案資料 |
| message | 文書種別 + コンテキスト | 件名+本文 | rent_collection, admin_reminder |

### 9.4 専門 (3個)

| エージェント | 入力 | 出力 | 主な利用パイプライン |
|---|---|---|---|
| company_researcher | 企業データ | ペインポイント+セグメント | outreach |
| signal_detector | エンゲージメントイベント | 温度スコア | outreach, LP webhook |
| calendar_booker | Google API + イベント情報 | イベントID+Meet URL | upsell, outreach |

---

## 10. コネクタ一覧

| コネクタ | 用途 | 使用箇所 |
|---|---|---|
| kintone | CRM/データベース連携 | SaaSポーリング（実装済み、パイプライン未接続） |
| freee | 会計・請求書・入金 | quotation_contract, webhook, SaaSポーリング |
| Slack | 通知・アラート | notifier, upsell_briefing, 各種アラート |
| CloudSign | 電子契約署名 | quotation_contract, webhook |
| Gmail | メール送信 | consent_flow |
| Google Sheets | バッチI/O | outreach (送信リスト管理) |
| gBizINFO | 法人番号・企業情報 | outreach (company_researcher) |
| Playwright | Webフォーム自動入力 | outreach (問合せフォーム送信) |

---

## 11. Brain層モジュール

| モジュール | 機能 | 状態 |
|---|---|---|
| knowledge/search | ベクトル+キーワードのハイブリッド検索 + クエリ拡張 + リランキング | 実装済み |
| knowledge/qa | Q&Aエンジン（検索→コンテキスト構築→LLM回答→信頼度付き） | 実装済み |
| knowledge/embeddings | Voyage AI (voyage-3) 512次元ベクトル生成 | 実装済み |
| knowledge/entity_extractor | NER（固有表現抽出）+ kg_entities/kg_relationsへのUPSERT + 関係推論 | 実装済み |
| knowledge/session_manager | BPOテーマ別セッション管理 + 自動compacting（10問で要約圧縮） | 実装済み |
| genome/applicator | 業界テンプレートJSON → knowledge_items一括登録 + ベクトル化 | 実装済み |
| genome/data/ | 6コア業界+12凍結業種のゲノムJSON | 実装済み |
| twin/analyzer | knowledge_itemsから5次元スナップショット自動生成 | 実装済み |
| proactive/analyzer | デジタルツイン+ナレッジ → LLMでリスク検出+改善提案 | 実装済み |
| inference/accuracy_monitor | パイプライン別ステップ精度集計 → 改善必要箇所特定 | 実装済み |
| inference/improvement_cycle | 精度モニター→失敗例収集→プロンプト最適化→適用 | 実装済み（未接続） |
| visualization | フロー図・決定木のMermaid生成 | 基本実装 |

---

## 12. 自動化度サマリ

### 12.1 時間帯別: 何が自動で何が手動か

| 時間帯 | 自動実行される処理 | 人間の作業 |
|---|---|---|
| 毎日08:00 | 400社リサーチ→フォーム送信→リード登録 | なし（完全自動） |
| ホットリード時 | スコアリング→提案書PDF→メール送信→見積書 | 見積書の承認クリック |
| **毎日09:00** | **ヘルスチェック + 売掛入金消込** | 要注意顧客への対応判断 |
| 毎日10:00 | SLA違反チェック→アップセル機会検知 | ブリーフィング確認→商談判断 |
| サポート問合せ時 | 分類→FAQ検索→AI回答生成(信頼度≥0.85で自動送信) | 信頼度低い回答のレビュー |
| CloudSign署名時 | 契約確定→顧客作成→ゲノム適用→freee請求 | なし（完全自動） |
| 見積依頼時 | OCR→数量抽出→単価照合→原価計算→コンプラ | 見積書の承認クリック |
| **毎日18:00** | **銀行照合 + 受注/失注分析→スコアモデル更新** | 不一致項目の確認 |
| **毎月1日** | **労務コンプラチェック + コンプラチェック** | 違反アラートへの対応 |
| **毎月5営業日** | **月次決算（P&L/BS/差異分析）** | 決算レポートの承認 |
| **毎月25日** | **給与計算 + 買掛支払処理** | 給与・支払の承認クリック |
| **毎月末** | **請求書発行 + 原価分析 + 経費バッチ + CS品質レビュー** | 請求書の承認クリック |
| **入社/退社時** | **入社書類生成→SaaS登録→社保届出 / 最終給与→離職票→無効化** | なし（自動チェーン） |
| **新規取引先登録時** | **反社チェック（gBizINFO+TDB照会→GREEN/YELLOW/RED判定）** | YELLOW/REDの判断 |
| 5分ごと | 条件連鎖チェック（SLA超過、解約リスク等） | アラートへの対応 |
| 30分ごと | 先読みスキャン（リスク・改善提案） | 提案の承認/却下 |

### 12.2 パイプライン別実装状況

| カテゴリ | 本数 | コード量 | 状態 |
|---|---|---|---|
| 営業系（マーケ→CS→学習） | 12本 | 11,782行 | 稼働中 |
| 建設BPO | 8本 | 3,622行 | 稼働中 |
| 製造BPO | 8本 | 3,101行 | 稼働中 |
| 共通BPO（Phase 1） | 6本 | 2,400行 | 稼働中 |
| **バックオフィス経理（Phase 2）** | **7本** | **実装中** | **invoice_issue/ar/ap/bank_recon/journal/monthly_close/tax** |
| **バックオフィス労務（Phase 2）** | **3本** | **実装中** | **social_insurance/year_end/labor_compliance** |
| **バックオフィス人事（Phase 2）** | **3本** | **実装中** | **recruitment/onboarding/offboarding** |
| **バックオフィス調達+法務+IT（Phase 2-3）** | **4本** | **実装中** | **purchase_order/compliance/antisocial/account** |
| その他コア業界 | 4本 | 3,150行 | 稼働中 |
| 凍結業種 | 11本 | 3,500行 | スケルトン（パートナー待ち） |
| **合計** | **66本** | **—** | **38本稼働 + 18本実装中 + 11本凍結** |

---

## 13. 既知の制約と今後の課題

### 13.1 Phase 1で残っている課題

| 課題 | 影響 | 対策案 |
|---|---|---|
| 建設/製造/共通にスケジュールトリガーなし | 月末の請求や給与が手動トリガーのみ | BUILTIN_SCHEDULE_TRIGGERSに追加 |
| kintoneコネクタが未使用 | CRM連携が手動 | パイプラインからの呼び出し追加 |
| 解約フロー(health<40)のチェーンがコメントアウト | 解約リスク検知後の自動対応なし | chain.py のコメント解除 |
| 推論エンジン(improvement_cycle)が未接続 | 自動プロンプト改善が動かない | orchestratorに週次サイクル追加 |
| 承認SLAエスカレーションなし | 承認待ちが放置される可能性 | notifierに7日超過リマインド追加 |

### 13.2 Phase 2で対応予定

| 項目 | 概要 |
|---|---|
| 企業横断学習 | k=5匿名化集計 → 業界平均精度の向上 |
| ナレッジ圧縮 | 週次LLMバッチで矛盾解消 |
| ゲノム自動更新 | 3社同一修正 → テンプレート自動更新 |
| LangGraph統合 | async承認 → interrupt()ノード移行 |
| リアルタイム解約予測 | 日次バッチ → リアルタイム利用低下検知 |

### 13.3 バックオフィス拡張（b_07設計書で24パイプライン定義済み）

> 詳細は `shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md` を参照。

| 領域 | 既存 | Phase 2追加 | Phase 3追加 |
|---|---|---|---|
| **経理（8本）** | expense | invoice_issue, ar_management, ap_management, bank_reconciliation, journal_entry, monthly_close | tax_filing |
| **労務（5本）** | attendance, payroll | social_insurance, year_end_adjustment, labor_compliance | — |
| **人事（3本）** | — | recruitment, employee_onboarding, employee_offboarding | — |
| **総務（3本）** | contract, admin_reminder | — | asset_management |
| **調達（2本）** | vendor | purchase_order | — |
| **法務（2本）** | — | compliance_check, antisocial_screening | — |
| **IT管理（1本）** | — | — | account_lifecycle |

### 13.4 「完全な社内OS」でさらに未カバーの領域

| 領域 | 具体例 | Phase |
|---|---|---|
| 経営ダッシュボード | リアルタイムKPI, 戦略計画, 競合分析 | Phase 3 |
| Agent Economy | エージェント間自律取引, 自己進化, メタエージェント | Phase 3 |

---

## 付録A: 起動方法

```bash
# 開発環境
cd shachotwo-app
uvicorn main:app --reload

# 全自動化有効
ENABLE_SALES_SCHEDULER=1 \
ENABLE_BPO_ORCHESTRATOR=1 \
SLACK_BOT_TOKEN=xoxb-... \
SLACK_NOTIFICATION_CHANNEL=#bpo-alerts \
uvicorn main:app
```

## 付録B: 関連設計書

| ファイル | 内容 |
|---|---|
| `d_00_BPOアーキテクチャ設計.md` | 3層アーキテクチャの設計思想 |
| `d_04_Agent_OS設計.md` | Agent OS 6層モデルの概念設計 |
| `d_05_Agent_Economy設計.md` | エージェント経済圏の設計 |
| `d_06_全エージェント連動マップ.md` | 123エージェントの一覧と接続関係 |
| `b_06_全社自動化設計_マーケSFA_CRM_CS.md` | マーケ→CS全10パイプラインの詳細仕様 |
| `d_02_フィードバック学習ループ設計.md` | 3層学習ループの設計 |
| `e_00_業界別BPO業務フロー総覧.md` | 16業界のBPO業務概要 |
