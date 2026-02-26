# Develop-Agent-System MVP

Notion 等から入力された「要件」をトリガーに、要件定義・コーディング・テスト・GitHub push までを自律的に行う LangGraph エージェントです。
10 ジャンル自動分類・Spec Review Checkpoint・統合ダッシュボード対応。

## 技術スタック

- Python 3.11+
- LangGraph, LangChain
- LLM: Google Vertex AI (Gemini 1.5 Pro / Flash)
- ツール: Pydantic, GitHub (直接 push)
- DB: Supabase (PostgreSQL)
- Server: FastAPI + Uvicorn

## グラフ構成

### Auto-Execute ON（従来モード・全自動）

```
[Start] → [GenreClassifier] → [SpecAgent] → [CoderAgent] → [Review/Guardrails] → (OK) → [GitHubPublisher] → [End]
                                                                   |
                                                              (NG / Error)
                                                                   |
                                                                   v
                                                             [FixAgent] → [CoderAgent] へ戻る（最大 3 回）
```

### Auto-Execute OFF（確認モード）

```
Phase 1: [Start] → [GenreClassifier] → [SpecAgent] → [END]  ← ここで一時停止
                                                                  ↓ ダッシュボードで確認
Phase 2: [CoderAgent] → [Review/Guardrails] → (OK) → [GitHubPublisher] → [End]
                               |
                          (NG / Error)
                               |
                               v
                         [FixAgent] → [CoderAgent] へ戻る（最大 3 回）
```

- **Genre Classifier**: 要件を 10 ジャンルに自動分類（事務・法務・会計・情シス・SFA・CRM・ブレイン・M&A・DD・汎用）
- **Spec Agent**: 自然言語の指示を構造化 Markdown 設計書に変換（Gemini Pro）
- **Coder Agent**: 設計書に基づきコード生成（Gemini Flash）。ファイル読込は 20KB 以下・除外リスト適用
- **Review & Guardrails**: Secret Scan（必須）、Lint/Build。NG なら FixAgent へ
- **GitHub Publisher**: 全チェック合格時のみ main ブランチへ直接 push

## セットアップ

1. 仮想環境と依存関係

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. 環境変数（Vertex AI）

   - プロジェクトルートに `.env.local` を用意し、`GOOGLE_CLOUD_PROJECT` 等を設定する。書式は `.env.local.example` をコピーして `.env.local` を作成し、値を埋めるとよい。
   - `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`（省略時は `us-central1`）
   - 認証は `gcloud auth application-default login` 等で設定

3. GitHub push を行う場合

   - `GITHUB_TOKEN`: リポジトリへの push 権限
   - `GITHUB_REPOSITORY`: `owner/repo` 形式

## 実行環境のサンドボックス化（推奨）

Lint/Build は subprocess で実行するため、**ローカル PC で直接動かさず**、以下を推奨します。

- **MVP**: 開発環境を **Docker DevContainer** にする（`.devcontainer/` 参照）
- または develop_agent 全体を Docker コンテナ内で実行

## 使い方

### エンジンのみ（グラフ実行）

```python
from develop_agent import initial_state
from develop_agent.graph import invoke

state = initial_state(
    user_requirement="ユーザー登録 API を追加してほしい",
    workspace_root="./my_repo",  # 任意。省略時は "."
)
result = invoke(state)
# result["status"], result["error_logs"] 等
```

### HTTP サーバー起動

FastAPI サーバーで要件を送ると、エージェントを起動できます。

```bash
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

### API エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| `GET` | `/health` | ヘルスチェック |
| `GET` | `/dashboard` | 統合ダッシュボード UI |
| `POST` | `/run` | 要件を渡してエージェント実行（auto_execute に応じて全自動 or Phase 1 のみ） |
| `POST` | `/run-from-database` | Notion DB の「実装希望」行を一括処理 |
| `POST` | `/run/{run_id}/implement` | Spec Review 状態の run から実装フェーズを再開 |
| `GET` | `/api/runs` | run 一覧取得 |
| `GET` | `/api/runs/{run_id}/spec` | 要件定義書（spec_markdown）全文取得 |
| `GET` | `/api/features` | 機能要約一覧 |
| `GET` | `/api/settings` | サーバー設定取得（auto_execute 等） |
| `PUT` | `/api/settings` | サーバー設定更新 |
| `GET` | `/api/next-system-suggestion` | 蓄積データから生成した次システム提案 |
| `POST` | `/webhook/notion` | Notion Webhook 受信 |

### POST /run の例

```bash
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"requirement": "hello_world.py に greet(name) を追加"}'
```

body に `workspace_root`, `rules_dir`, `notion_page_id`, `genre` を指定可能。

### Notion データベーストリガー（実装希望で一括 run）

専用の Notion データベースで要件を一覧管理し、ステータスを「実装希望」にした行を一括処理できます。

1. **Notion でデータベースを作成**し、次のプロパティを用意する:
   - **名前** (Title): 要件の短いタイトル
   - **ステータス** (Select): `実装前` / `実装希望` / `実装中` / `完了済`（トリガー対象は `実装希望`）
   - **要件** (Rich text): 任意。未記入の場合はページ本文を要件として使う
   - **ジャンル** (Select): 任意。10 ジャンルから選択。ルール自動追記時にジャンル付きで記録
   - **run_id**: 実行後に書き戻す用（Rich text）

2. **POST /run-from-database** を呼ぶ:
   ```bash
   curl -X POST http://localhost:8000/run-from-database \
     -H "Content-Type: application/json" \
     -d '{"notion_database_id": "あなたのデータベースID"}'
   ```

### Spec Review Checkpoint（確認モード）

auto_execute を OFF にすると、要件定義完了後にパイプラインが一時停止します。

1. **ダッシュボード** (`/dashboard`) で auto_execute トグルを OFF に切り替え
2. `POST /run` で要件を投入 → Phase 1（分類 + 要件定義）のみ実行 → `spec_review` ステータスで停止
3. ダッシュボードの Run 一覧で要件定義書をプレビュー確認
4. **「実装開始」ボタン**を押すと Phase 2（実装 → レビュー → GitHub push）が実行される

### CLI（main.py）

```bash
python main.py "要件のテキスト"
# 改善ルール案を outputs/<run_id>/rules/ に出力する場合
python main.py "要件のテキスト" . --output-rules
```

## ルールの編集と改善ルールの出力

各フェーズの振る舞いは `rules/*.md` でカスタマイズできます。ファイルが無い場合はコード内のデフォルトが使われます。

| ファイル | 用途 |
|----------|------|
| `rules/spec_rules.md` | 要件定義書作成時のシステムプロンプト |
| `rules/stack_domain_rules.md` | スタック・ドメイン・自社前提。spec/coder が参照 |
| `rules/coder_rules.md` | コード生成時のハウスルール・出力形式 |
| `rules/review_rules.md` | レビュー観点 |
| `rules/fix_rules.md` | 修正指示の確認項目・エラー対処法 |
| `rules/publish_rules.md` | publish（push）時のコミットメッセージルール |

**review 合格**かつ `output_rules_improvement=True` の run では、改善案が自動で `rules/*.md` の末尾に追記されます。

## ダッシュボード

`/dashboard` にアクセスすると統合ダッシュボードが表示されます。

- **設定**: auto_execute トグル（ON/OFF 切り替え）
- **次システム提案**: 蓄積データから生成した「次に作るシステム」の提案
- **Run 一覧**: ステータス別フィルター（全件 / 要件確認 / 実装中 / 完了 / 失敗）
- **Run 詳細**: 要件定義書プレビュー、実装開始ボタン、生成ファイル一覧
- 30 秒ごとの自動リフレッシュ

## ディレクトリ構成

```
ai_agent/
├── develop_agent/        # エージェントエンジン
│   ├── graph.py          # LangGraph メイン（全体 / Spec / Impl グラフ）
│   ├── state.py          # AgentState 定義
│   ├── config.py         # 定数
│   ├── nodes/            # 各ノード（GenreClassifier, Spec, Coder, Review, Fix, Publisher）
│   ├── utils/            # guardrails, file_filter 等
│   └── llm/              # Vertex AI クライアント
├── server/               # HTTP API (FastAPI)
│   ├── main.py           # エンドポイント定義
│   ├── persist.py        # Supabase 永続化
│   ├── settings.py       # サーバー設定管理
│   ├── migrate.py        # Supabase マイグレーションランナー
│   └── static/dashboard/ # ダッシュボード UI
├── sandbox/              # Docker Sandbox（コード実行隔離）
│   ├── Dockerfile        # Sandbox イメージ定義
│   ├── mcp_server.py     # MCP サーバー（コンテナ内）
│   └── build.sh          # イメージビルドスクリプト
├── docs/                 # ドキュメント
│   ├── develop_agent.md  # 開発ロードマップ・技術設計
│   ├── business_plan.md  # 事業計画
│   ├── supabase_schema.sql
│   └── e2e-and-deploy.md
├── rules/                # カスタマイズ用ルールファイル
│   └── genre/            # 10ジャンル専門ルール・DBスキーマ
├── tests/                # テスト
├── output/               # 実行出力（gitignored）
└── data/                 # ランタイムデータ（settings.json 等）
```

## テスト

```bash
pytest tests/ -v
```

## ライセンス

（プロジェクトに合わせて記載）
