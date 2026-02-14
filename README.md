# Develop-Agent-System MVP

Notion 等から入力された「要件」をトリガーに、要件定義・コーディング・テスト・PR 作成までを自律的に行う LangGraph エージェントです。

## 技術スタック

- Python 3.11+
- LangGraph, LangChain
- LLM: Google Vertex AI (Gemini 1.5 Pro / Flash)
- ツール: Pydantic, GitHub API (PR 作成)

## グラフ構成

```
[Start] → [SpecAgent] → [CoderAgent] → [Review/Guardrails] → (OK) → [GitHubPublisher] → [End]
                                              |
                                         (NG / Error)
                                              |
                                              v
                                        [FixAgent] → [CoderAgent] へ戻る（最大 3 回）
```

- **Spec Agent**: 自然言語の指示を構造化 Markdown 設計書に変換（Gemini Pro）
- **Coder Agent**: 設計書に基づきコード生成（Gemini Flash）。ファイル読込は 20KB 以下・除外リスト適用
- **Review & Guardrails**: Secret Scan（必須）、Lint/Build。NG なら FixAgent へ
- **GitHub Publisher**: 全チェック合格時のみブランチ・PR 作成

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

3. GitHub PR 作成を行う場合

   - `GITHUB_TOKEN`: リポジトリへの push / PR 作成権限
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
# result["pr_url"], result["status"], result["error_logs"] 等
```

### 外側のラッパー（Notion / Supabase）

Notion Webhook や Supabase との接続は、このパッケージの外側で行います。

```python
# main.py または server.py のイメージ
# 1. Notion から Webhook 受信
# 2. Supabase に「タスク開始」を書き込み
# 3. develop_agent.graph.invoke(...) を呼び出す
# 4. 結果を Supabase と Notion に書き戻す
```

### HTTP で実行（Phase 2.1）

FastAPI サーバーで `POST /run` に要件を送ると、エージェントを起動できます。プロジェクトルートで:

```bash
uvicorn server.main:app --reload --host 0.0.0.0 --port 8000
```

呼び出し例:

```bash
curl -X POST http://localhost:8000/run -H "Content-Type: application/json" -d '{"requirement": "hello_world.py に greet(name) を追加"}'
```

body に `workspace_root` や `rules_dir` を指定することもできます。`notion_page_id` を渡すと、その Notion ページの本文を要件として取得します。**専門家ジャンル**（事務・法務・会計・情シス・SFA・CRM・ブレイン・M&A・DD）を渡す場合は `genre` を指定すると、review 合格時のルール自動追記にジャンルが記録されます。生存確認は `GET /health` で `{"status": "ok"}` を返します。

### Notion データベーストリガー（実装希望で一括 run）

専用の Notion データベースで要件を一覧管理し、ステータスを「実装希望」にした行を一括処理できます。

1. **Notion でデータベースを作成**し、次のプロパティを用意する:
   - **名前** (Title): 要件の短いタイトル
   - **ステータス** (Select): `実装前` / `実装希望` / `実装中` / `完了済`（トリガー対象は `実装希望`）
   - **要件** (Rich text): 任意。未記入の場合はページ本文を要件として使う
   - **ジャンル** (Select): 任意。事務・法務・会計・情シス・SFA・CRM・ブレイン・M&A・DD など。指定すると run に渡され、ルール自動追記時にジャンル付きで記録される
   - **run_id**, **PR URL**: 実行後に書き戻す用（Rich text または URL）

2. データベースを Notion 連携でこのアプリに接続し、**実装希望**にしたい行のステータスを「実装希望」に変更する。

3. **POST /run-from-database** を呼ぶ:
   ```bash
   curl -X POST http://localhost:8000/run-from-database -H "Content-Type: application/json" \
     -d '{"notion_database_id": "あなたのデータベースID"}'
   ```
   その時点で「実装希望」の行が順次 run され、完了後に Notion の該当行が「完了済」と run_id・PR URL で更新されます。

### CLI（main.py）

サンプル起動用の `main.py` を同梱しています。

```bash
python main.py "要件のテキスト"
# 改善ルール案を outputs/<run_id>/rules/ に出力する場合
python main.py "要件のテキスト" . --output-rules
# ルールディレクトリや run_id を指定する場合
python main.py "要件" . --output-rules --rules-dir rules --run-id my-run-1
```

## ルールの編集と改善ルールの出力

各フェーズの振る舞いは `rules/*.md` でカスタマイズできます。ファイルが無い場合はコード内のデフォルトが使われます。

| ファイル | 用途 |
|----------|------|
| `rules/spec_rules.md` | 要件定義書作成時のシステムプロンプト |
| `rules/stack_domain_rules.md` | スタック（Next.js+Supabase）・ドメイン（CRM/SFA・事務・会計・法務）・自社前提。spec/coder が参照。 |
| `rules/coder_rules.md` | コード生成時のハウスルール・出力形式 |
| `rules/review_rules.md` | レビュー観点（将来の拡張用） |
| `rules/fix_rules.md` | 修正指示の前に挿入する確認項目・エラー対処法 |
| `rules/pr_rules.md` | PR の title/body を上書きする場合に `title:` / `body:` で記載 |

`--output-rules` を付けて実行すると、`outputs/<run_id>/` に以下が出力されます。

- `spec_markdown.md` … 今回の設計書
- `generated_code/` … 生成したファイル（任意）
- `rules/spec_rules_improvement.md` など … 各フェーズの「改善・追加ルール案」

**review 合格**（全自動チェック通過）かつ `output_rules_improvement=True` の run では、改善案が自動で `rules/*.md` の末尾に追記されます（重複は簡易検出でスキップ）。手動マージは不要です。

### ダッシュボードと蓄積

- **GET /dashboard**: run 一覧・詳細と「次に作るシステムの提案」を表示する簡易 UI。
- **GET /api/runs**, **GET /api/features**: run 一覧・機能要約（Supabase に蓄積されている場合）。
- **GET /api/next-system-suggestion**: 蓄積から生成した「次に作るシステム」の提案文（`data/next_system_suggestion.md`）。

run 実行時に Supabase（`SUPABASE_URL` と `SUPABASE_SERVICE_KEY` を .env に設定）が利用可能な場合、run 結果が自動で蓄積されます。「次に作るシステムの提案」はデフォルトでは requirement に注入されず、`data/next_system_suggestion.md` に書き出されます。提案を requirement の先頭に付けたい場合は `POST /run` の body に `"skip_accumulation_inject": false` を指定してください。**進行希望用 Notion ページ**に提案を書き出したい場合は、`NOTION_API_KEY` に加え `NOTION_PROGRESS_HOPE_PAGE_ID`（進行希望用ページの ID）を .env に設定すると、提案生成時にそのページの本文が更新されます。スキーマは `docs/supabase_schema.sql` を Supabase の SQL Editor で実行して作成します。目標 E2E・本番エラーゼロの定義・Vercel デプロイ手順の詳細は [docs/e2e-and-deploy.md](docs/e2e-and-deploy.md) を参照してください。

## ディレクトリ構成

```
server/             # Phase 2.1: HTTP API (FastAPI)
develop_agent/
├── graph.py          # LangGraph メイン
├── state.py          # State 定義
├── config.py         # 定数
├── nodes/            # Spec, Coder, Review, Fix, GitHub Publisher
├── utils/            # guardrails.py（Secret Scan, Lint/Build）, file_filter.py
└── llm/              # Vertex AI クライアント
```

## テスト

```bash
pytest tests/ -v
```

## ライセンス

（プロジェクトに合わせて記載）
