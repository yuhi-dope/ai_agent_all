# シャチョツー プロジェクト全体マップ

> 全ファイル・フォルダの役割を一覧化したドキュメント。
> 新しいファイルを追加したらここも更新すること。

---

## Claude Code カスタマイズ構成（19スキル / 10エージェント / 7フック）

### スキル（19件） — `.claude/skills/`

タスクにマッチした時にオンデマンドでロードされる指示セット。

| # | スキル | 説明 | 種別 |
|---|---|---|---|
| 1 | `/implement` | モジュール実装。パスに応じてサブエージェントに自動振り分け | 実装 |
| 2 | `/add-pipeline` | 新BPOパイプライン追加（スケルトン・テスト・レジストリ一括） | 実装 |
| 3 | `/db-migrate` | DBマイグレーションSQL作成（テーブル追加・カラム変更・RPC） | 実装 |
| 4 | `/test-module` | テスト実行＆失敗修正（test-runnerエージェントに委譲） | 検証 |
| 5 | `/run-pipeline` | BPOパイプラインをモックデータで動作確認・精度検証 | 検証 |
| 6 | `/ui-review` | UI_RULES.md準拠チェック＆違反修正 | 検証 |
| 7 | `/sec-check` | 導入前セキュリティ7件自動確認 | 検証 |
| 8 | `/pre-deploy` | デプロイ前全体チェック（テスト・型・環境変数・CORS等） | 検証 |
| 9 | `/cost-check` | LLMコスト・テナント別使用量確認（月次） | 運用 |
| 10 | `/pilot-prep` | パイロット企業投入前の準備チェック | 運用 |
| 11 | `/pmf-check` | PMFゲート指標確認（NPS/WAU/継続率） | 運用 |
| 12 | `/review-agents` | エージェント体制レビュー（設計vs実装の差分検出、週1推奨） | 運用 |
| 13 | `/ship` | リリース一括フロー: test → sec-check → pre-deploy | ワークフロー |
| 14 | `/new-feature` | 新機能一気通貫: 設計確認 → 実装 → テスト → レビュー | ワークフロー |
| 15 | `/hotfix` | 緊急修正: 原因特定 → 最小修正 → テスト → diff確認 | ワークフロー |
| 16 | `/design-index` | 設計ドキュメント一覧（shachotwo/ 全件の場所・内容・参照タイミング） | ガイド |
| 17 | `/agent-guide` | エージェント体制・並列開発戦略・モジュール独立性マトリクス | ガイド |
| 18 | `/db-guide` | DB設計・テーブル構造・RLSルール一覧 | ガイド |
| 19 | `/security-guide` | セキュリティ原則・BPOセキュリティ・導入前7件・Phase 2対応 | ガイド |

> 個人スキル（`~/.claude/skills/`）: `commit-format`（コミットメッセージ）、`pr-description`（PR説明）

### エージェント（10件） — `.claude/agents/`

サブエージェントとして別コンテキストで実行される専門エージェント。

| # | エージェント | 説明 | モデル | 読込スキル |
|---|---|---|---|---|
| 1 | `brain-module` | brain/ サブモジュール実装（extraction, knowledge, genome等） | sonnet | test-module |
| 2 | `router-impl` | FastAPI ルーター実装（スケルトン→brain接続） | sonnet | test-module |
| 3 | `frontend-page` | Next.js ページ実装（shadcn/ui + Tailwind） | sonnet | ui-review |
| 4 | `bpo-pipeline` | 業種別BPOパイプライン実装（建設/製造/歯科等） | sonnet | test-module, run-pipeline |
| 5 | `bpo-orchestrator` | BPO Manager 4コンポーネント実装 | sonnet | test-module |
| 6 | `micro-agent` | 共通マイクロエージェント実装（OCR/抽出/検証等） | sonnet | test-module |
| 7 | `connector-impl` | SaaSコネクタ実装（kintone/freee/Slack/LINE WORKS） | sonnet | test-module |
| 8 | `test-runner` | テスト実行＆失敗修正（pytest専門） | haiku | — |
| 9 | `agent-architect` | エージェント体制設計・レビュー（設計vs実装照合） | opus | — |
| 10 | `ui-reviewer` | UI_RULES.md準拠チェック＆違反修正 | haiku | — |

> モデル階層: **opus**=高精度設計判断 / **sonnet**=実装 / **haiku**=軽量チェック

### フック（7件） — `.claude/settings.json`

ファイル編集時に自動実行されるバリデーション。

| # | トリガー | フック名 | 内容 | タイムアウト |
|---|---|---|---|---|
| 0 | Pre: Read/Grep/Glob | .envアクセスガード | `.env` ファイルへのアクセスをブロック | 5秒 |
| 1 | Post: Edit/Write | TypeScript型チェック | `frontend/*.tsx` 変更時に `tsc --noEmit` | 60秒 |
| 2 | Post: Edit/Write | Python型チェック | `*.py` 変更時に `pyright` | 60秒 |
| 3 | Post: Edit/Write | RLSテナント分離 | supabase呼び出しに `company_id` 必須（違反でブロック） | 10秒 |
| 4 | Post: Edit/Write | 関連テスト自動実行 | 編集ファイルに対応する pytest を自動実行 | 60秒 |
| 5 | Post: Write/Edit | 関数名重複チェック | `workers/micro/` `llm/prompts/` で同名関数を検出 | 15秒 |
| 6 | Post: Write | マイグレーション連番 | `db/migrations/*.sql` 作成時に連番抜けを検出 | 5秒 |
| 7 | Post: Write/Edit | UI禁止ワード | `.tsx` 変更時にUI_RULES.md禁止ワードをgrep | 5秒 |

### 権限設定 — `.claude/settings.local.json`

| 区分 | 内容 |
|---|---|
| **allow（自動許可）** | Read, Edit, Write, Grep, Glob, Bash(*), WebFetch(*), Agent(*), Skill(*), TodoWrite |
| **deny（完全禁止）** | `rm -rf deploy/`, `gcloud run/functions/revisions delete`, `supabase db reset` |
| **ask（都度確認）** | `rm` deploy/Dockerfile/main.py, `git push/reset --hard`, `gcloud run deploy/builds submit`, `supabase migration/db push` |

---

## プロジェクトルート

```
ai_agent/
├── CLAUDE.md              ← 開発ガイド（構造原則・設計ドキュメント参照・コーディング規約）
├── PROJECT_MAP.md         ← このファイル（全体マップ）
├── shachotwo/             ← 設計ドキュメント（Source of Truth）
├── shachotwo-app/         ← 全プロダクトコード（唯一のコードベース）
└── shachotwo-X運用AI/     ← X(旧Twitter)運用ツール（独立小規模・GAS中心）
```

> ※ `_convert_md_to_pdf.py` / `generate_manufacturing_deck.py` はルートから `shachotwo/z_その他/` に移動済み

---

## shachotwo/ — 設計ドキュメント

```
shachotwo/
├── a_セキュリティ/
│   ├── a_01_セキュリティ設計.md        暗号化・RBAC・PII・監査ログ
│   └── a_02_コンプライアンス設計.md    法令遵守・業界別規制対応
│
├── b_詳細設計/
│   ├── b_01_ブレイン編.md              ブレインLayer（ナレッジ・Q&A・デジタルツイン）
│   ├── b_02_BPO編.md                  BPO Worker（SaaS自動化・Shadow Mode）
│   ├── b_03_自社システム編.md          自社システム連携設計
│   ├── b_04_製造業見積エンジン3層設計.md 製造業3層見積エンジン
│   ├── b_05_建設CAD対応設計.md         建設業CAD図面解析
│   └── b_06_全社自動化設計_マーケSFA_CRM_CS.md  ★マーケ・SFA・CRM・CS全自動化+学習ループ
│
├── c_事業計画/
│   ├── c_01_事業計画.md                料金・収支・GTM・競合
│   ├── c_02_プロダクト設計.md          全体アーキテクチャ・DB・API・セキュリティ
│   ├── c_03_実装計画.md                Phase別タスク・完了基準
│   ├── c_04_営業資料.md                営業トーク・提案書素材
│   ├── c_05_社内スタート計画.md        社内展開計画
│   ├── c_06_初期顧客共創.md            パイロット企業との共創方針
│   ├── c_07_グロースロードマップ.md    成長戦略・フェーズ別計画
│   └── c_08_建設業vs製造業BPO比較.md   業種比較分析
│
├── d_アーキテクチャ/
│   ├── d_00_BPOアーキテクチャ設計.md   BPO全体アーキテクチャ
│   ├── d_01_BPOエージェントエンジン設計.md エージェントエンジン詳細
│   ├── d_02_フィードバック学習ループ設計.md 学習ループ設計
│   └── d_03_国際化設計.md              多言語・多リージョン（Phase 3+）
│
├── e_業界別BPO/
│   ├── e_00_業界別BPO業務フロー総覧.md 16業界BPOマスター（優先度・横展開マップ）
│   ├── e_00a_バックオフィス業務実態調査.md バックオフィス共通業務
│   ├── e_01_建設業BPO設計.md           建設業#1モジュール+拡張
│   ├── e_02_製造業BPO設計.md           製造業
│   ├── e_03_歯科BPO設計.md             歯科
│   ├── e_04〜e_14                      介護/物流/飲食/医療/薬局/美容/整備/ホテル/EC/派遣/設計
│
├── f_BPOガイド/                        業界ドメイン知識
│   ├── f_01_製造業BPO完全ガイド.md
│   ├── f_02_建設業BPO初学者ガイド.md
│   └── f_03〜f_05                      歯科/不動産/士業
│
├── g_ナレッジ/                          業界構造理解
│   └── g_01〜g_05                       建設積算/製造見積/歯科レセプト/不動産家賃/士業期限
│
├── h_意思決定記録/                      ADR（Architecture Decision Records）
│   └── 001〜011                         LLM選定/DB選定/エージェント構成等
│
├── z_その他/
│   ├── archive/
│   │   ├── shachotwo-マーケAI/         吸収済み（→ shachotwo-app に移植完了）
│   │   ├── shachotwo-契約AI/           吸収済み（→ shachotwo-app に移植完了）
│   │   └── ARCHIVED.md                移植先マッピング一覧
│   └── (散在ファイル整理先)
│
├── PROGRESS.md                         実装進捗トラッカー
├── PILOT_TARGETS.md                    パイロット企業リスト
└── ANDPAD_PARTNER_GUIDE.md             ANDPAD連携ガイド
```

---

## shachotwo-app/ — プロダクトコード

### 全体構造

```
shachotwo-app/
├── main.py                 FastAPIエントリポイント（include_routerのみ）
├── requirements.txt        Python依存パッケージ
├── Dockerfile              本番コンテナ
├── docker-compose.yml      ローカル開発環境
├── conftest.py             pytest共通フィクスチャ
│
├── brain/                  Layer 2: デジタルツイン中核
├── workers/                Layer 3: 実行エージェント（BPO + マイクロ + コネクタ）
├── routers/                FastAPI ルーター（1ドメイン=1ファイル）
├── frontend/               Next.js フロントエンド
├── db/                     DB関連（スキーマ・マイグレーション・クライアント）
├── llm/                    LLM抽象化レイヤー
├── auth/                   認証（JWT・ミドルウェア）
├── security/               セキュリティ（暗号化・PII・監査・同意管理）
├── tests/                  pytest テスト
└── deploy/                 デプロイ設定（Cloud Run等）
```

---

### brain/ — デジタルツイン中核（社長の脳）

```
brain/
├── extraction/             テキスト → 構造化パイプライン（LLM抽出）
├── ingestion/              音声(Whisper) + OCR(Document AI) + 対話型取り込み
├── knowledge/              Q&Aエンジン・ベクトル検索・エンベディング
│   ├── qa.py               Q&A応答エンジン
│   ├── search.py           ハイブリッド検索（ベクトル+キーワード）
│   ├── embeddings.py       Voyage AI エンベディング生成
│   ├── eval.py             回答品質評価
│   └── entity_extractor.py ★ NER → KGエンティティ自動抽出（Phase 2）
├── genome/                 業界テンプレート（16業種JSON）
│   ├── loader.py           テンプレート読み込み
│   ├── applicator.py       テンプレート適用
│   └── data/               業種別JSON（construction.json等）
├── twin/                   9次元デジタルツインモデル
├── proactive/              能動提案（リスク検出・改善提案）
│   └── expansion_scorer.py ★ Land & Expand ステージ検出・次機能提案（Phase 1.5）
├── inference/              行動推論 + 精度モニタリング + プロンプト最適化
├── analytics/              ★ ネットワーク効果ベンチマーク集計（k匿名性5社以上）
├── billing_guard.py        ★ プラン別フィーチャーフラグ（common_bpo/industry_bpo/support）
└── visualization/          フロー図・意思決定ツリー・充足度マップ
```

---

### workers/ — 実行エージェント

#### workers/bpo/sales/ — 全社自動化AI社員（★今回構築）

```
workers/bpo/sales/
│
├── marketing/              【マーケ担当AI】
│   └── outreach_pipeline.py    企業発掘→gBizINFOエンリッチ→ペイン推定→
│                               LP生成→フォーム/メール400件日→シグナル検知→商談予約
│
├── sfa/                    【営業担当AI】
│   ├── lead_qualification_pipeline.py   リードスコアリング（0-120pt）→自動/手動/育成振り分け
│   ├── proposal_generation_pipeline.py  提案書AI生成（PDF+PPTX）→メール送付→開封追跡
│   ├── quotation_contract_pipeline.py   見積自動計算→PDF→承認→契約書→CloudSign→freee請求書
│   └── consent_flow.py                 アプリ内電子同意（トークン認証→同意記録→透かしPDF）
│
├── crm/                    【顧客管理AI】
│   ├── customer_lifecycle_pipeline.py   オンボード自動化（ゲノム適用→ウェルカム→Day1/3/7/14/30）
│   │                                   ＋ ヘルススコア日次計算（5次元: 利用/エンゲージ/サポ/NPS/拡張）
│   └── revenue_request_pipeline.py      MRR/ARR/NRR/チャーン率月次集計 ＋ 要望AI分類・優先度付け
│
├── cs/                     【カスタマーサポートAI】
│   ├── support_auto_response_pipeline.py  FAQ自動回答（confidence判定→自動/レビュー/エスカレ5経路）
│   │                                      ＋ SLA監視（初回応答1h/解決24h）
│   ├── upsell_briefing_pipeline.py        アップセルタイミング検知→コンサル用ブリーフィング自動生成
│   └── cancellation_pipeline.py           解約受付→データエクスポートZIP→最終請求→テナント無効化→学習
│
├── learning/               【学習・改善AI】
│   ├── win_loss_feedback_pipeline.py      受注→成功テンプレ保存＋スコア重み+5pt
│   │                                      失注→理由ヒアリング＋月次失注分析
│   │                                      PDCA→業種別反応率＋メールA/Bテスト自動生成
│   └── cs_feedback_pipeline.py            CSAT分析→good回答をFAQに自動追加
│                                           ＋ confidence閾値自動調整（CSAT<4.0→厳しく/≥4.5→緩く）
│
├── pipelines/              後方互換（旧パスからのre-export。新規開発ではposition別を使う）
│
└── templates/              【共有テンプレート】
    ├── proposal_template.html     提案書HTML（WeasyPrint PDF用）
    ├── quotation_template.html    見積書HTML
    ├── contract_template.html     契約書HTML
    ├── welcome_email.html         ウェルカムメール
    ├── nurture_3day.html          3日後フォロー
    ├── nurture_7day.html          7日後フォロー
    ├── post_meeting.html          商談後フォロー
    ├── lost_survey.html           失注理由アンケート
    └── resume_templates/          LP/レジュメ（建設/製造/歯科/介護/士業/不動産）
```

#### workers/bpo/{industry}/ — 業界別BPOパイプライン

```
workers/bpo/
├── construction/           建設業（積算/請求/安全書類/写真整理/下請管理/許可申請/原価報告）
├── manufacturing/          製造業（3層見積エンジン）
├── dental/                 歯科（レセプト点検）
├── common/                 共通（契約管理/経費精算/勤怠/届出）
├── restaurant/             飲食業
├── clinic/                 医療クリニック
├── pharmacy/               調剤薬局
├── nursing/                介護福祉
├── logistics/              物流運送
├── beauty/                 美容エステ
├── auto_repair/            自動車整備
├── hotel/                  ホテル旅館
├── ecommerce/              EC小売
├── staffing/               人材派遣
├── architecture/           建築設計
├── realestate/             不動産
├── professional/           士業事務所
└── manager/                BPO Manager（オーケストレーター）
    ├── schedule_watcher.py     スケジュールトリガー（7定期ジョブ登録済み）
    ├── event_listener.py       イベントトリガー（9イベント登録済み）
    ├── condition_evaluator.py  条件連鎖（4パターン登録済み）
    ├── task_router.py          パイプライン選択＆実行
    └── proactive_scanner.py    能動的タスク発見
```

#### workers/micro/ — 共通マイクロエージェント（23種）

```
workers/micro/
├── extractor.py            LLMテキスト構造化抽出
├── generator.py            ドキュメント生成（LLM）
├── rule_matcher.py         ルール照合（スコアリング・判定）
├── calculator.py           数値計算（見積・MRR等）
├── message.py              メール/メッセージ文面生成
├── validator.py            出力バリデーション・品質チェック
├── saas_reader.py          外部SaaSからデータ読取
├── saas_writer.py          外部SaaSへデータ書込
├── ocr.py                  Google Document AI OCR
├── compliance.py           法令・ルール準拠チェック
├── diff.py                 変更差分検出
├── table_parser.py         表形式データ抽出
├── anomaly_detector.py     異常検知（統計的外れ値検出）
├── image_classifier.py     画像分類（現場写真・書類判別）
├── llm_summarizer.py       LLM要約（長文→要点抽出）
├── industry_directory_scraper.py  業界団体ディレクトリスクレイピング
├── contact_extractor.py    連絡先抽出（メール・電話・住所）
├── company_researcher.py   企業リサーチ＆ペイン推定
├── signal_detector.py      シグナル温度判定 hot/warm/cold
├── web_searcher.py         Web検索（Serper→Google CSE自動フォールバック）
├── pdf_generator.py        HTML→PDF変換（WeasyPrint）
├── calendar_booker.py      Google Calendar空き枠＆Meet予約
├── pptx_generator.py       PPTXスライド提案書生成（python-pptx）
└── models.py               MicroAgentInput/Output 共通モデル
```

#### workers/billing/ — 収益分配エンジン（Phase 3 新規）

```
workers/billing/
└── revenue_share.py        パートナー収益分配（月次バッチ・Stripe Connect送金）
```

#### workers/base/ — 基盤エージェント

```
workers/base/
├── agent_executor.py       LangGraph AgentExecutor（HitL suspend/resume）
└── inactive_account_disabler.py  非アクティブアカウント自動無効化（SOC2 90日ルール）
```

#### workers/connector/ — SaaS連携コネクタ（20種）

```
workers/connector/
├── base.py                 BaseConnector 抽象基底クラス
├── factory.py              コネクタファクトリ（名前→インスタンス）
│
│ ── Tier 1（MVP必須） ──
├── kintone.py              kintone連携
├── freee.py                freee会計・請求書連携
├── slack.py                Slack通知
├── email.py                Gmail API送受信（2,000件/日上限管理）
├── cloudsign.py            CloudSign電子署名
│
│ ── Tier 1.5（Phase 1.5 追加） ──
├── money_forward.py        ★ マネーフォワード（請求書/経費/仕訳）
├── yayoi.py                ★ 弥生会計（売上/経費/仕訳）
├── jobcan.py               ★ ジョブカン（勤怠/従業員）
├── backlog.py              ★ Backlog（課題/プロジェクト）
├── notion.py               ★ Notion（データベース/ページ）
├── microsoft365.py         ★ Microsoft 365（メール/カレンダー/Teams）
├── king_of_time.py         ★ KING OF TIME（勤怠）
├── smarthr.py              ★ SmartHR（従業員/手続き）
├── stripe_billing.py       ★ Stripe（課金/Checkout/Portal/Webhook/口座振替）
│
│ ── GWS（Google Workspace） ──
├── gmail_watch.py          Gmail Watch（リアルタイム受信）
├── google_calendar.py      Google Calendar連携
├── google_drive.py         Google Drive連携
│
│ ── その他 ──
├── gbizinfo.py             gBizINFO法人情報API
├── google_sheets.py        Google Sheets読み書き
└── playwright_form.py      Playwrightフォーム自動送信
```

---

### routers/ — FastAPI ルーター

```
routers/
│
│ ── 全社自動化系（★今回構築） ──
├── marketing.py            マーケ（アウトリーチ実行/ステータス/パフォーマンス/リサーチ/シグナル/ABテスト）
├── sales.py                SFA（リードCRUD/スコアリング/商談/提案書/見積/契約/売上予測）
├── crm.py                  CRM（顧客一覧/360度ビュー/ヘルス/タイムライン/売上集計/コホート/要望）
├── support.py              CS（チケットCRUD/メッセージ/エスカレ/受信Webhook/KPI）
├── upsell.py               アップセル（候補一覧/ブリーフィング生成）
├── learning.py             学習（受注失注フィードバック/スコアリング再学習/PDCA/CS品質）
├── webhooks.py             Webhook受信（CloudSign署名完了/freee入金/Intercom）
├── consent.py              電子同意（トークン認証・同意記録・PDF）
│
│ ── 基盤系 ──
├── company.py              企業管理
├── users.py                ユーザー管理
├── dashboard.py            ダッシュボード（NextStepsCard・BenchmarkCard含む）
├── knowledge.py            ナレッジ（Q&A・検索・セッション）
├── ingestion.py            取り込み（音声/テキスト/ドキュメント）
├── digital_twin.py         デジタルツイン（9次元モデル）
├── genome.py               業界テンプレート
├── proactive.py            能動提案
├── visualization.py        可視化（フロー図・マップ）
├── connector.py            SaaS接続管理
├── execution.py            BPO実行
├── invitations.py          招待管理
├── onboarding.py           オンボーディング
├── accuracy.py             精度モニタリング・グレースフルデグラデーション
│
│ ── Phase 1.5〜3 追加 ──
├── approvals.py            ★ HitL承認フロー（pending/approve/reject/modify）
├── billing.py              ★ 使用量計測・Stripe課金・手動請求書・口座振替
├── knowledge_graph.py      ★ KGエンティティ・リレーション（オントロジー）
├── mfa.py                  ★ MFA設定・TOTP検証（SOC2）
├── partner.py              ★ Partner Marketplace（登録/アプリ管理/収益確認）
├── security_admin.py       ★ 非アクティブアカウント管理（SOC2管理者）
├── webhooks_gws.py         GWS Webhook受信（Gmail Watch・Calendar）
│
│ ── 業界別BPO（17業種） ──
└── bpo/
    ├── construction.py     建設業
    ├── manufacturing.py    製造業
    ├── dental.py           歯科
    ├── common.py           共通BPO
    └── ... (17業種)
```

---

### frontend/ — Next.js フロントエンド

```
frontend/src/app/(authenticated)/
│
│ ── 全社自動化系（★今回構築・19ページ） ──
├── marketing/
│   ├── outreach/page.tsx       アウトリーチダッシュボード（送信数/反応数/ホットリード/実行ボタン）
│   ├── research/page.tsx       リサーチ企業一覧（ペインポイント・温度・エンリッチボタン）
│   └── ab-tests/page.tsx       A/Bテスト結果（バリアント比較・勝ちパターン）
│
├── sales/
│   ├── leads/page.tsx          リードカンバン（new/contacted/qualified/nurturing + スコア + 提案書生成）
│   ├── pipeline/page.tsx       商談パイプライン（5ステージカンバン + 金額 + 確度）
│   ├── proposals/page.tsx      提案書管理（一覧 + PDFプレビュー + 送付 + 開封追跡）
│   ├── contracts/page.tsx      見積・契約（見積書タブ + 契約書タブ + 署名状況）
│   ├── forecast/page.tsx       売上予測（加重予測グラフ + 商談一覧）
│   └── consent/[token]/page.tsx 電子同意画面（パブリック・トークン認証）
│
├── crm/
│   ├── customers/page.tsx      顧客一覧（ヘルススコア順 + MRR + ステータスバッジ）
│   ├── customers/[id]/page.tsx 顧客360度ビュー（5タブ: ヘルス/活動/チケット/要望/契約）
│   ├── revenue/page.tsx        売上ダッシュボード（MRR推移 + NRR + コホート分析）
│   └── requests/page.tsx       要望ボード（ランキング + MRRインパクト + ステータス更新）
│
├── support/
│   ├── tickets/page.tsx        チケット一覧（SLA残時間 + 優先度 + AI/人間フィルタ）
│   ├── tickets/[id]/page.tsx   チケット詳細（メッセージスレッド + AI回答 + エスカレボタン）
│   └── metrics/page.tsx        CS KPI（CSAT + FRT + AI対応率 + SLA達成率）
│
├── upsell/
│   ├── page.tsx                アップセル候補一覧（拡張タイミング + 推奨モジュール + 商談予約）
│   └── [customer_id]/page.tsx  コンサルブリーフィング（顧客分析 + 推奨アクション + 見積シミュ）
│
├── learning/
│   └── page.tsx                学習ダッシュボード（4タブ: スコアリング/PDCA/CS品質/受注失注）
│
│ ── 基盤系 ──
├── dashboard/page.tsx          メインダッシュボード（NextSteps・ベンチマーク）
├── knowledge/                  ナレッジ管理（入力/検索/Q&A）
├── bpo/                        BPO管理（建設積算/製造見積/実行）
│   └── run/page.tsx            ★ ガイド付き入力フォーム（2カラム・PDFアップロード）
├── twin/page.tsx               デジタルツイン
├── onboarding/page.tsx         オンボーディング
├── settings/                   設定（メンバー管理）
│
│ ── Phase 3 追加 ──
├── marketplace/
│   ├── page.tsx                ★ アプリ一覧（カテゴリタブ・インストールボタン）
│   └── [id]/page.tsx           ★ アプリ詳細・レビュー投稿
└── partner/
    └── page.tsx                ★ パートナーポータル（登録申請/収益/アプリ管理）
```

---

### db/ — データベース

```
db/
├── schema.sql              初期スキーマ（12コアテーブル）
├── supabase.py             Supabaseクライアント
├── crud_sales.py           SFA/CRM/CS用CRUD関数（38関数）
└── migrations/             001〜045（合計45マイグレーション）
    ├── 001〜022             コアテーブル・SFA/CRM/CS・学習
    ├── 023〜037             営業強化・リード管理・BPO士業
    ├── 038_usage_metrics.sql        ★ 使用量計測（Phase 1.5）
    ├── 039_knowledge_graph.sql      ★ KGエンティティ・リレーション（Phase 2）
    ├── 040_audit_logs_v2.sql        ★ 監査ログ完全化（Phase 3 SOC2）
    ├── 041_mfa_settings.sql         ★ MFA設定（Phase 3 SOC2）
    ├── 042_subscriptions.sql        ★ Stripeサブスクリプション（Phase 3）
    ├── 043_manual_invoices.sql      ★ 手動請求書・口座振替（Phase 3）
    ├── 044_partner_apps.sql         ★ Marketplace（partners/partner_apps/installations/reviews）
    └── 045_revenue_share.sql        ★ 収益分配レコード
```

---

### security/ — セキュリティ横断

```
security/
├── audit.py                監査ログ書き込み（actor_user_id/resource_type/metadata）
├── audit_middleware.py      ★ 全リクエストの ip_address/user_agent 自動付与（Phase 3）
├── headers_middleware.py    ★ セキュリティヘッダー全付与（HSTS/CSP/X-Frame-Options）（Phase 3）
├── consent.py              電子同意管理
├── encryption.py           フィールド暗号化
├── error_handler.py        セキュアエラーハンドリング
├── pii_handler.py          PII検出・マスキング（regex）
└── rate_limiter.py         レートリミット
```

---

### llm/ — LLM抽象化

```
llm/
├── client.py               Gemini→Claude→GPT 切替可能クライアント
├── cost_tracker.py          テナント別LLMコスト追跡
└── prompts/
    ├── extraction.py        構造化抽出プロンプト
    ├── construction.py      建設業専用プロンプト
    ├── manufacturing.py     製造業専用プロンプト
    ├── sales_proposal.py    ★ 提案書生成（16業種ペインマッピング付き）
    ├── sales_qualification.py ★ リード判定（4軸100点スコアリング）
    ├── support_response.py  ★ CS自動回答（8カテゴリ分類+confidence）
    └── outreach_personalize.py ★ アウトリーチメール個別化
```

---

### BPO Managerトリガー設定

#### スケジュール（自動実行）

| 時刻 | パイプライン | 内容 |
|---|---|---|
| 毎日 08:00 | marketing/outreach | 企業リサーチ&アウトリーチ |
| 毎日 09:00 | crm/customer_lifecycle | ヘルススコア日次計算 |
| 毎日 10:00 | cs/support_auto_response | SLA違反チェック |
| 毎週月曜 09:00 | learning/win_loss_feedback | アウトリーチPDCA |
| 毎月1日 09:00 | crm/revenue_request | MRR/チャーンレポート |
| 毎月15日 09:00 | crm/revenue_request | 要望ランキング更新 |
| 毎月末 09:00 | learning/cs_feedback | CS品質月次レビュー |

#### イベント（リアルタイム）

| イベント | パイプライン | 条件 |
|---|---|---|
| lead_created | sfa/lead_qualification | 新規リード登録 |
| lead_score_gte_70 | sfa/proposal_generation | スコア70以上 |
| proposal_accepted | sfa/quotation_contract | 提案承認 |
| contract_signed | crm/customer_lifecycle | 契約署名完了 |
| ticket_created | cs/support_auto_response | チケット作成 |
| opportunity_won | learning/win_loss_feedback | 受注 |
| opportunity_lost | learning/win_loss_feedback | 失注 |
| health_score_high | cs/upsell_briefing | health≥80+未使用モジュール |
| cancellation_requested | cs/cancellation | 解約申請 |
