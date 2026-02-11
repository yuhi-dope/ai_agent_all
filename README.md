# Unicorn-Agent-System MVP

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
- または unicorn_agent 全体を Docker コンテナ内で実行

## 使い方

### エンジンのみ（グラフ実行）

```python
from unicorn_agent import initial_state
from unicorn_agent.graph import invoke

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
# 3. unicorn_agent.graph.invoke(...) を呼び出す
# 4. 結果を Supabase と Notion に書き戻す
```

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
| `rules/coder_rules.md` | コード生成時のハウスルール・出力形式 |
| `rules/review_rules.md` | レビュー観点（将来の拡張用） |
| `rules/fix_rules.md` | 修正指示の前に挿入する確認項目・エラー対処法 |
| `rules/pr_rules.md` | PR の title/body を上書きする場合に `title:` / `body:` で記載 |

`--output-rules` を付けて実行すると、`outputs/<run_id>/` に以下が出力されます。

- `spec_markdown.md` … 今回の設計書
- `generated_code/` … 生成したファイル（任意）
- `rules/spec_rules_improvement.md` など … 各フェーズの「改善・追加ルール案」

これらを人手で確認し、有用な部分を `rules/*.md` にマージすると、次回以降の実行が改善されます。マージ用スクリプト（例: `scripts/merge_improvements.py`）はネクストアクションとして実装できます。

## ディレクトリ構成

```
unicorn_agent/
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
