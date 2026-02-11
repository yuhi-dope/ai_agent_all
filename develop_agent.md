### 開発エージェント開発システム 要件定義書 (v2.0)
**Project Name:** Unicorn-Agent-System
**Target:** 1人あたり20社を担当可能な「AI並列開発基盤」の構築
**Last Updated:** 202X-XX-XX (Security & Efficiency Update)

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
| **LLM (Brain)** | **Gemini 1.5 Pro** | 設計・レビュー・高度な判断 |
| **LLM (Worker)** | **Gemini 1.5 Flash** | コーディング・ログ解析（コスト重視） |
| **Orchestration** | **LangGraph** | エージェント制御・状態管理 |
| **Security** | **Regex Secret Scanner** | **(New)** コード内のAPI Keyパターン検知 |
| **QA Pipeline** | **ESLint / Prettier / Playwright** | **(New)** 段階的テスト実行 (Lint -> E2E) |
| **Infrastructure** | **Google Cloud (GCP)** | Cloud Run, Vertex AI, Secret Manager |

---

## 3. 機能要件 (Functional Requirements)

### 3.1 入力インターフェース (Trigger)

- **Notion**: ステータス「Ready to Build」でWebhook起動。

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
| **最大試行回数** | **3回** (旧5回) | 3回で直らないバグは「設計ミス」とみなし、早期に人間にエスカレーションする。 |
| **タイムアウト** | **Step毎 3分 / 全体 10分** | 無限ループ防止。1回の生成・実行に3分以上かけさせない。 |
| **予算上限** | **$0.50 / Task** | トークン課金の青天井を防ぐ。 |
| **(New) 読込制限** | **Max 20KB / File** | 巨大ファイルの読み込みによる「思考停止」と「トークン浪費」を物理的に防ぐ。 |

### 4.2 品質とエスカレーション (Quality & Escalation)

| 項目 | 設定値 | 理由・挙動 |
| --- | --- | --- |
| **コード変更量** | **Max 200行 / PR** (旧500行) | レビュー負荷軽減。大規模な変更は分割させる。 |
| **諦め条件** | **同一エラー 3回** | ハマり状態の検知。 |
| **テスト合格基準** | **Lint/Build (100%) -> E2E (100%)** | 構文エラーレベルでのE2E実行（時間浪費）を禁止する。 |

### 4.3 セキュリティ規定 (Security Boundaries)

- **(New) シークレットスキャン必須**:
    - 正規表現マッチングによる事前検査を導入。
    - `const SUPABASE_KEY = "..."` のようなハードコードが検出された場合、**PR作成プロセスを強制終了（Reject）**する。
- **DB操作**: `DROP`, `DELETE` 禁止。ReadOnly推奨。
- **外部アクセス**: 公式ドキュメントのみ許可（ホワイトリスト方式）。

---

## 5. インフラ構成 (Infrastructure on GCP)

### 5.1 Cloud Run (Agent Runner)

- Notion Webhookで起動。
- 実行コンテナ内に `trivy` やカスタムスクリプトによるシークレットスキャン機能を内包させる。

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

1. **Secret Scanの実装**: LangGraphのノードとして「正規表現チェック」を組み込む。
2. **ファイルフィルタの実装**: `package-lock.json`, `yarn.lock`, `.log` を無視リストに入れる処理を追加。
3. **段階的テストの実装**: いきなり `playwright test` を叩かず、まず `npm run lint && npm run build` を叩くようにプロンプトとコマンドを調整。