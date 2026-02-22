### 開発エージェント開発システム 要件定義書 (v2.0)
**Project Name:** Develop-Agent-System
**Target:** 1人あたり20社を担当可能な「AI並列開発基盤」の構築
**Last Updated:** 2025-02-11

---

## 前提・用語（まとめ）

トリガー名・ステータス値・起動API などは **ここだけ変更すればよい**。本文では「上記の前提・用語に従う」と参照する。

| 項目 | 値 | 備考 |
| --- | --- | --- |
| **トリガー（ドキュメント表記）** | Ready to Build | 計画・資料での呼び方 |
| **運用上のステータス値** | 実装希望 | Notion の Select で使う値。run-from-database はこのステータスのページを処理する |
| **起動方法** | POST /run（body: requirement または notion_page_id）、POST /run-from-database（Notion DB の「実装希望」一括）、POST /run/{run_id}/implement（spec_review から実装再開） | API は FastAPI サーバー |
| **Notion ステータスプロパティ名** | ステータス | Notion ページのプロパティ名（要は「実装希望」が選ばれている列） |
| **Notion ジャンルプロパティ名** | ジャンル | 事務・法務・会計・情シス・SFA・CRM 等の Select |

---

## 1. プロジェクト概要

### 1.1 目的

PM（30名）が策定した「業務の型」に基づき、AIエージェントが自律的に設計・実装・テストを行い、コーダー（160名）が承認のみを行う開発体制を構築する。
**v2.0の主眼:** AIの「うっかりミス（鍵の流出）」と「暴走（無限ループ・巨大ファイル読込）」を物理的に阻止するガードレールの実装。

### 1.2 目標KPI

- **担当社数**: コーダー1名あたり20クライアント
- **AI自律完結率**: 90%以上
- **PR生成コスト**: 1タスクあたり **$0.50** 以下
- **セキュリティ事故**: **0件**（シークレット混入の完全阻止）

---

## 2. システムアーキテクチャ

### 2.1 全体構成

PMの入力（Notion）をトリガーに、GCP上で稼働するLangGraphエージェント群が並列処理を行う。
**変更点:** コード生成プロセスに「シークレットスキャン」と「Lintチェック」の関門を追加し、GitHubへのPush前に不正なコードを弾く。

### 2.2 技術スタック (Update)

| レイヤー | 技術選定 | 備考 |
| --- | --- | --- |
| **API / Server** | **FastAPI** | 実サーバー。POST /run, /run-from-database を提供 |
| **LLM (Brain)** | **Gemini 1.5 Pro** | 設計・レビュー・高度な判断 |
| **LLM (Worker)** | **Gemini 1.5 Flash** | コーディング・ログ解析・ジャンル分類（コスト重視） |
| **Orchestration** | **LangGraph** | エージェント制御・状態管理 |
| **Security** | **Regex Secret Scanner** | **(実装済み)** コード内のAPI Keyパターン検知 |
| **QA Pipeline** | **ESLint / Prettier / Playwright** | **(実装済み)** 段階的テスト実行 (Lint -> Unit -> E2E) |
| **Infrastructure** | **Google Cloud (GCP)** | Cloud Run, Vertex AI, Secret Manager。手順は [docs/e2e-and-deploy.md](docs/e2e-and-deploy.md) を参照 |
| **CI/CD** | **GitHub Actions** | **(実装済み)** CI（ruff + pytest）・Cloud Run 自動デプロイ（`.github/workflows/`） |
| **Container** | **Docker** | **(実装済み)** Python 3.11-slim、非root、ヘルスチェック付き（`Dockerfile`） |
| **Migration** | **scripts/migrate.py** | **(実装済み)** `docs/migrations/` の SQL を Supabase PostgreSQL へ冪等適用 |
| **環境変数テンプレート** | **.env.example** | **(実装済み)** 全環境変数のサンプル・ドキュメント |

---

## 3. 機能要件 (Functional Requirements)

### 3.1 入力インターフェース (Trigger)

- **Notion**: 上記「前提・用語」のトリガーで起動。起動方法は同表を参照（POST /run、POST /run-from-database）。Notion Webhook による自動起動は実装済み。

### 3.2 エージェント構成と制約 (Agents)

**パイプラインフロー（自動実行モード）**: `genre_classifier → spec_agent → coder_agent → review_guardrails → (fix_agent ↔ review) → github_publisher`

**パイプラインフロー（確認モード）**:
- **Phase 1**: `genre_classifier → spec_agent → [spec_review で一時停止]`
- **Phase 2**: ダッシュボードで要件確認後 → `coder_agent → review_guardrails → (fix_agent ↔ review) → github_publisher`

ダッシュボードの auto_execute トグルで切替可能。OFF 時は Phase 1 完了後にステータス `spec_review` で停止し、ダッシュボード上で要件定義書を確認してから「実装開始」ボタンで Phase 2 を起動する。

### ① Genre Classifier (ジャンル自動分類) - New

- **役割**: 要件テキストからジャンル（sfa / crm / accounting 等 10種）を AI 自動判定。
- **実装**: `develop_agent/nodes/genre_classifier.py`。Gemini Flash 使用。分類ルールは `rules/genre_rules.md`。
- **ユーザー指定との関係**: 指定なし → AI 判定を使用。指定あり → confidence >= 0.85 かつ別ジャンルの場合のみ上書き。
- **出力**: `genre`, `genre_subcategory`, `genre_override_reason` を state に設定。

### ② Spec Agent (要件定義)

- **役割**: Notion解析 → Markdown要件定義書作成。
- ジャンルコンテキスト（`genre`, `genre_subcategory`）と `rules/client_dashboard_rules.md` をプロンプトに注入。

### ③ Coder Agent (実装) - 厳格化

- **役割**: コード生成。
- ジャンルコンテキストと `rules/client_dashboard_rules.md` をプロンプトに注入。
- **入力制限**:
    - **ファイルサイズ制限**: **20KB** を超えるファイル（`package-lock.json`, ログ等）の読み込み禁止。
    - **除外設定**: 自動生成ファイル、バイナリファイルはコンテキストに含めない。
- **出力制限 (Secret Scan)**:
    - 生成コード内に `sk-`, `sbp_`, `API_KEY` 等の文字列、または高エントロピーな文字列が含まれる場合、**GitHubへのPush前にエラーとし、再生成させる。**

### ④ Test & Fix Agent (テスト・修正) - 段階的実行

- **役割**: テスト実行と自己修正。
- **Fail Fast フロー**:
    1. **Lint & Build**: 構文エラー、型エラーがないか確認。NGなら即修正（E2Eは回さない）。
    2. **Unit Test**: ロジック単体テスト。
    3. **E2E (Playwright)**: 1と2がパスした場合のみ実行。受け入れ条件（AC）の網羅を確認。

### 3.3 ルールファイル構成

| ファイル | 用途 | 読み込み元 |
| --- | --- | --- |
| `rules/spec_rules.md` | 設計書の書き方・スコープ制約・受入条件品質 | Spec Agent |
| `rules/coder_rules.md` | 型安全・エラーハンドリング・既存コード再利用・200行制約 | Coder Agent |
| `rules/review_rules.md` | Fail Fast チェック順序・AI セマンティックレビュー | Review Guardrails |
| `rules/fix_rules.md` | エラーカテゴリ別対処法テーブル | Fix Agent |
| `rules/stack_domain_rules.md` | 技術スタック・ドメイン用語・Supabase RLS 標準パターン | Spec / Coder |
| `rules/genre_rules.md` | 10ジャンル定義・判定シグナル・分類ルール | Genre Classifier |
| `rules/client_dashboard_rules.md` | 統合ダッシュボード標準構造・DB一元管理 | Spec / Coder |

---

## 4. ガバナンス・ルール (Agent Constitution v2)

AIエージェントに遵守させる「絶対ルール」。v2.0にて大幅に厳格化。

### 4.1 コスト・リソース制限 (Resource Limits)

| 項目 | 設定値 | 理由・挙動 |
| --- | --- | --- |
| **最大試行回数** | **3回** (旧5回) | **(実装済み)** 3回で直らないバグは「設計ミス」とみなし、早期に人間にエスカレーションする。 |
| **タイムアウト** | **Step毎 3分 / 全体 10分** | **(実装済み)** 無限ループ防止。 |
| **予算上限** | **$0.50 / Task** | **(実装済み)** トークン課金の青天井を防ぐ。MAX_COST_PER_TASK_USD 超過時はログ警告 + budget_exceeded。 |
| **読込制限** | **Max 20KB / File** | **(実装済み)** 巨大ファイルの読み込みによる「思考停止」と「トークン浪費」を防ぐ。 |

### 4.2 品質とエスカレーション (Quality & Escalation)

| 項目 | 設定値 | 理由・挙動 |
| --- | --- | --- |
| **コード変更量** | **Max 200行 / PR** (旧500行) | **(実装済み)** レビュー負荷軽減。 |
| **諦め条件** | **同一エラー 3回** | **(実装済み)** ハマり状態の検知。 |
| **テスト合格基準** | **Lint/Build (100%) -> Unit -> E2E (100%)** | **(実装済み)** 構文エラーレベルでのE2E実行を禁止。 |

### 4.3 セキュリティ規定 (Security Boundaries)

- **シークレットスキャン必須**: **(実装済み)**
    - 正規表現マッチングによる事前検査。ハードコード検出時は PR 作成を Reject。
- **DB操作**: `DROP`, `DELETE` 禁止。ReadOnly推奨。（ルール・プロンプト上の規定。コード強制は未実装）
- **外部アクセス**: 公式ドキュメントのみ許可（ホワイトリスト方式）。（同上）

---

## 5. インフラ構成 (Infrastructure on GCP)

GCP で本番運用する場合の手順は [e2e-and-deploy.md](e2e-and-deploy.md) を参照。

### 5.1 Cloud Run (Agent Runner)

- **(実装済み)** `Dockerfile`（Python 3.11-slim、非rootユーザー、ヘルスチェック付き）で Cloud Run にデプロイ。
- **(実装済み)** GitHub Actions（`.github/workflows/deploy-cloud-run.yml`）で main push 時に自動デプロイ。Workload Identity Federation で認証。
- Notion Webhook または API で起動。

### 5.2 Secret Manager

- 正しいAPIキーは全てここから環境変数として注入する。AIには `.env.example` のみを参照させる。

### 5.3 CI/CD

- **(実装済み)** `.github/workflows/ci.yml`: 全ブランチ push / main PR で ruff lint + pytest を実行。
- **(実装済み)** `.github/workflows/deploy-cloud-run.yml`: main push で Docker build → Artifact Registry → Cloud Run デプロイ。

### 5.4 DB マイグレーション

- **(実装済み)** `scripts/migrate.py`: `docs/migrations/` 配下の SQL を Supabase PostgreSQL に冪等適用。`migration_history` テーブルで適用済み管理。

---

## 6. 運用フロー (Revised Workflow)

### 6.1 自動実行モード（auto_execute: ON）

1. **PM**: Notionで要件定義 → Webhook起動。
2. **System (AI)**:
    - **ジャンル自動分類**: 要件テキストから 10ジャンルのいずれかを AI 判定。
    - 要件定義・設計（ジャンルコンテキスト + ダッシュボード標準構造を反映）。
    - コーディング（**※ファイル読込制限 20KB**、ジャンル別ルール適用）。
    - **Secret Scan**: ハードコードがないかチェック（NGなら再生成）。
    - **Lint/Build Check**: 構文チェック（NGなら再生成）。
    - **AI セマンティックレビュー**: バグ・セキュリティ・N+1 等の重大問題を検出。
    - **E2E Test**: 動作確認（NGなら修正ループへ / Max 3回）。
    - GitHub PR作成（genre, genre_override_reason を Supabase に記録）。
3. **Coder**:
    - PR確認（Lint/Test/Scan 全てGreenであることを確認）。
    - **変更行数が200行以内**であることを確認し、Approve & Merge。

### 6.2 確認モード（auto_execute: OFF）— **(実装済み)**

1. **PM**: Notionで要件定義 → Webhook起動。
2. **System (AI Phase 1)**:
    - ジャンル自動分類 + 要件定義・設計まで実行。
    - ステータスを `spec_review` に設定し、**state_snapshot を Supabase に保存**して停止。
3. **IT担当者**: ダッシュボード (`GET /dashboard`) で要件定義書を確認。
    - 問題なければ **「実装開始」ボタン**をクリック（`POST /run/{run_id}/implement`）。
4. **System (AI Phase 2)**:
    - 保存した state から再開し、コーディング → テスト → レビュー → PR 作成まで実行。
    - 完了後に Notion ステータスを「完了済」に更新。
5. **Coder**: PR確認 → Approve & Merge。

ダッシュボードの **auto_execute トグル**で両モードを切替可能（`PUT /api/settings`）。

---

## 7. 実装・検証フェーズ (Next Steps)

### 完了済み

1. **Secret Scanの実装**: LangGraphのノードとして正規表現チェックを組み込み済み。
2. **ファイルフィルタの実装**: `package-lock.json`, `yarn.lock`, `.log` 等の無視リストを実装済み。
3. **段階的テストの実装**: Lint/Build → Unit → E2E の順で実行するよう実装済み。
4. **FastAPI サーバー**: POST /run, POST /run-from-database を提供。
5. **Notion DB 連携**: run-from-database で「実装希望」一括処理、ステータス・run_id・PR URL の書き戻し。
6. **Supabase 蓄積**: run / feature / rule_changes の保存（未設定時はスキップ）。genre, genre_override_reason カラム追加済み。
7. **次システム提案・Notion 進行希望**: next_system_suggestor、ルール自動マージ・ジャンル対応。
8. **Vercel 手順・目標E2E**: docs/e2e-and-deploy.md に記載。
9. **Notion Webhook 自動起動**: POST /webhook/notion でイベント受信し、ステータス「実装希望」のページをバックグラウンドで run。NOTION_WEBHOOK_SECRET で署名検証。
10. **予算 $0.50 計測・閾値**: run 単位のトークン集計と概算コスト計算。MAX_COST_PER_TASK_USD 超過時はログ警告 + budget_exceeded。
11. **Genre Classifier**: 要件テキストからジャンルを AI 自動判定する LangGraph ノード。`rules/genre_rules.md` に基づく 10ジャンル分類。
12. **ジャンルコンテキスト注入**: Spec / Coder Agent にジャンル情報と `rules/client_dashboard_rules.md` を自動注入。
13. **ルール大幅強化**: spec_rules（ステップ式・仮説明示・受入条件品質）、coder_rules（型安全・エラーハンドリング・既存コード再利用）、review_rules（AI セマンティックレビュー）、fix_rules（エラーカテゴリ別対処法）、stack_domain_rules（Supabase RLS 標準パターン）。
14. **Dockerfile / Cloud Run デプロイ**: Python 3.11-slim、非root、ヘルスチェック付き。GitHub Actions で自動デプロイ。
15. **CI（GitHub Actions）**: ruff lint + pytest を全ブランチで実行。
16. **DB マイグレーションランナー**: `scripts/migrate.py` で `docs/migrations/` の SQL を Supabase に冪等適用。
17. **環境変数テンプレート**: `.env.example` に全環境変数をドキュメント化。
18. **Spec Review Checkpoint（要件確認チェックポイント）**: パイプラインを Phase 1（ジャンル分類 + 要件定義）と Phase 2（実装 → テスト → PR）に分割。ダッシュボードの auto_execute トグルで ON/OFF 切替可能。OFF 時は要件定義完了後にステータス `spec_review` で停止し、ダッシュボード上で要件書を確認してから「実装開始」ボタンで Phase 2 を起動。`POST /run/{run_id}/implement` で再開。Supabase に `state_snapshot`（JSONB）を保存して状態を復元。
19. **Settings API**: `GET /api/settings`、`PUT /api/settings` で auto_execute 設定を管理。`data/settings.json` にファイルベースで保存。
20. **ダッシュボード拡張**: auto_execute トグル、ステータスフィルター、ステータスバッジ、要件定義書プレビュー、「実装開始」ボタン、30秒自動リフレッシュ。

### 未実装・検討

- DB DROP/DELETE のコード強制、外部アクセス制限、trivy 連携。