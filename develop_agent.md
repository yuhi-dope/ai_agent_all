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
| **起動方法** | POST /run（body: requirement または notion_page_id）、POST /run-from-database（Notion DB の「実装希望」一括） | API は FastAPI サーバー |
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
| **LLM (Worker)** | **Gemini 1.5 Flash** | コーディング・ログ解析（コスト重視） |
| **Orchestration** | **LangGraph** | エージェント制御・状態管理 |
| **Security** | **Regex Secret Scanner** | **(実装済み)** コード内のAPI Keyパターン検知 |
| **QA Pipeline** | **ESLint / Prettier / Playwright** | **(実装済み)** 段階的テスト実行 (Lint -> Unit -> E2E) |
| **Infrastructure** | **Google Cloud (GCP)** | 本番運用時は Cloud Run, Vertex AI, Secret Manager。手順は [docs/e2e-and-deploy.md](docs/e2e-and-deploy.md) を参照 |

---

## 3. 機能要件 (Functional Requirements)

### 3.1 入力インターフェース (Trigger)

- **Notion**: 上記「前提・用語」のトリガーで起動。起動方法は同表を参照（POST /run、POST /run-from-database）。Notion Webhook による自動起動は優先度中の実装予定。

### 3.2 エージェント構成と制約 (Agents)

### ① Spec Agent (要件定義)

- **役割**: Notion解析 → Markdown要件定義書作成。

### ② Coder Agent (実装) - 厳格化

- **役割**: コード生成。
- **(New) 入力制限**:
    - **ファイルサイズ制限**: **20KB** を超えるファイル（`package-lock.json`, ログ等）の読み込み禁止。
    - **除外設定**: 自動生成ファイル、バイナリファイルはコンテキストに含めない。
- **(New) 出力制限 (Secret Scan)**:
    - 生成コード内に `sk-`, `sbp_`, `API_KEY` 等の文字列、または高エントロピーな文字列が含まれる場合、**GitHubへのPush前にエラーとし、再生成させる。**

### ③ Test & Fix Agent (テスト・修正) - 段階的実行

- **役割**: テスト実行と自己修正。
- **(New) Fail Fast フロー**:
    1. **Lint & Build**: 構文エラー、型エラーがないか確認。NGなら即修正（E2Eは回さない）。
    2. **Unit Test**: ロジック単体テスト。
    3. **E2E (Playwright)**: 1と2がパスした場合のみ実行。受け入れ条件（AC）の網羅を確認。

---

## 4. ガバナンス・ルール (Agent Constitution v2)

AIエージェントに遵守させる「絶対ルール」。v2.0にて大幅に厳格化。

### 4.1 コスト・リソース制限 (Resource Limits)

| 項目 | 設定値 | 理由・挙動 |
| --- | --- | --- |
| **最大試行回数** | **3回** (旧5回) | **(実装済み)** 3回で直らないバグは「設計ミス」とみなし、早期に人間にエスカレーションする。 |
| **タイムアウト** | **Step毎 3分 / 全体 10分** | **(実装済み)** 無限ループ防止。 |
| **予算上限** | **$0.50 / Task** | トークン課金の青天井を防ぐ。計測・閾値は優先度中で実装予定。 |
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

GCP で本番運用する場合の手順は [docs/e2e-and-deploy.md](docs/e2e-and-deploy.md) を参照。以下は想定構成。

### 5.1 Cloud Run (Agent Runner)

- Notion Webhook または API で起動。
- 実行コンテナ内に `trivy` やカスタムスクリプトによるシークレットスキャン機能を内包させる（検討項目）。

### 5.2 Secret Manager

- 正しいAPIキーは全てここから環境変数として注入する。AIには `.env.example` のみを参照させる。

---

## 6. 運用フロー (Revised Workflow)

1. **PM**: Notionで要件定義 → Webhook起動。
2. **System (AI)**:
    - 要件定義・設計。
    - コーディング（**※ファイル読込制限 20KB**）。
    - **Secret Scan**: ハードコードがないかチェック（NGなら再生成）。
    - **Lint/Build Check**: 構文チェック（NGなら再生成）。
    - **E2E Test**: 動作確認（NGなら修正ループへ / Max 3回）。
    - GitHub PR作成。
3. **Coder**:
    - PR確認（Lint/Test/Scan 全てGreenであることを確認）。
    - **変更行数が200行以内**であることを確認し、Approve & Merge。

---

## 7. 実装・検証フェーズ (Next Steps)

### 完了済み

1. **Secret Scanの実装**: LangGraphのノードとして正規表現チェックを組み込み済み。
2. **ファイルフィルタの実装**: `package-lock.json`, `yarn.lock`, `.log` 等の無視リストを実装済み。
3. **段階的テストの実装**: Lint/Build → Unit → E2E の順で実行するよう実装済み。
4. **FastAPI サーバー**: POST /run, POST /run-from-database を提供。
5. **Notion DB 連携**: run-from-database で「実装希望」一括処理、ステータス・run_id・PR URL の書き戻し。
6. **Supabase 蓄積**: run / feature の保存（未設定時はスキップ）。
7. **次システム提案・Notion 進行希望**: next_system_suggestor、ルール自動マージ・ジャンル対応。
8. **Vercel 手順・目標E2E**: docs/e2e-and-deploy.md に記載。

### 優先度中（実装済み）

- **Notion Webhook 自動起動**: POST /webhook/notion でイベント受信し、ステータス「実装希望」のページをバックグラウンドで run。NOTION_WEBHOOK_SECRET で署名検証。
- **予算 $0.50 計測・閾値**: run 単位のトークン集計（spec/coder/next_system_suggestor）と概算コスト計算。MAX_COST_PER_TASK_USD（デフォルト 0.5）超過時はログ警告と RunResponse の budget_exceeded。

### 未実装・検討

- Cloud Run / Secret Manager のコード組み込み、DB DROP/DELETE のコード強制、外部アクセス制限、trivy 連携。