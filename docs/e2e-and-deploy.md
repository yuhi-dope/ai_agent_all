# 目標 E2E とデプロイ・本番水準

このドキュメントでは、目標とするエンドツーエンドの流れ、既存フローとの対応、本番エラーゼロの定義、および Vercel を用いた自動デプロイ手順をまとめます。

---

## 1. 目標 E2E の整理

目指す一連の流れは次の 5 ステップです。

| ステップ | 内容 |
|----------|------|
| 1. Notion で要望を投げる | 要件・希望を Notion のページ／DB で管理し、トリガー用選択肢（例: 実装希望）で「この要望を develop_agent に回す」とする。 |
| 2. develop_agent が動く | Notion から要件を取得し、Spec → Coder → Review のパイプラインで自社システム（Next.js + Supabase）向けの実装・修正を行う。 |
| 3. 自社システムを最適化 | 生成・修正したコードをワークスペースに反映し、既存の Next.js と Supabase 構成に組み込む。 |
| 4. テストまで実施して使える状態で届ける | Lint / Build / Unit Test / E2E Test を通過したうえで、**本番環境でエラーが出ない状態**まで持っていく。review 合格がそのゲートとなる。 |
| 5. Vercel で GitHub プッシュ時に自動デプロイ | 成果物を GitHub に push し、Vercel と連携して push したら自動でデプロイされるようにする。本番は Vercel 上のデプロイ結果とする。 |

### 既存フローとの対応

| 目標ステップ | 既存の実装 |
|--------------|------------|
| 要望を投げる | **POST /run**（`requirement` または `notion_page_id`）、**POST /run-from-database**（Notion DB の「実装希望」行を一括処理）。トリガー用選択肢 = ステータス「実装希望」。 |
| develop_agent が動く | `develop_agent.graph.invoke(state)`。Spec → Coder → Review →（合格時）GitHub Publisher。 |
| 最適化・テスト | Review 内で生成コードを `work_dir` に書き出し、Lint/Build/Unit/E2E を実行。合格時のみ status が `published` となり GitHub に直接 push。 |
| 本番エラーゼロ | review 合格条件（後述）を「本番でエラーが出ない状態」のゲートとして扱う。 |
| Vercel 自動デプロイ | 企業側の Next.js リポジトリを Vercel に連携する手順を「3. Vercel で GitHub プッシュ時に自動デプロイする手順」に記載。 |

### 用語の対応表

| 計画・ドキュメントでの呼び方 | 実装上の対応 |
|------------------------------|--------------|
| トリガー用選択肢 | Notion の「ステータス」プロパティの値「実装希望」。run-from-database はこのステータスのページを run する。 |
| 専門家ジャンル | API の `genre`（POST /run の body）、Notion の「ジャンル」(Select) プロパティ。事務・法務・会計・情シス・SFA・CRM・ブレイン・M&A・DD など。 |
| review 合格 | `status == "published"`。Secret Scan / Lint・Build / Unit / E2E のすべて通過かつ変更量 200 行以内。 |

---

## 2. 本番エラーゼロの定義

**review 合格**を「本番でエラーが出ない状態」のゲートとして扱います。合格となる条件は以下です。

- **Secret Scan 通過**: 生成コードにシークレット（API キー、Bearer トークン、秘密鍵など）が含まれていないこと。
- **Lint / Build 通過**: `work_dir` で `ruff check .`（Python）または `npm run build`（JS/TS）が成功すること。
- **Unit Test 通過**: `work_dir` で pytest または package.json の test スクリプトが成功すること。未定義・未導入の場合はスキップ（合格扱い）。
- **E2E Test 通過**: `work_dir` で Playwright による E2E が成功すること。Playwright が未導入の場合はスキップ（合格扱い）。
- **変更量**: 生成コードの総行数が 200 行以内であること（`MAX_LINES_PER_PUSH`）。

これらは [develop_agent/nodes/review_guardrails.py](develop_agent/nodes/review_guardrails.py) で順に実行され、すべて通過した場合にのみ status が `published` となり、GitHub Publisher で main ブランチに直接 push されます。E2E は [develop_agent/utils/guardrails.py](develop_agent/utils/guardrails.py) の `run_e2e_test` で Playwright を実行し、未導入時は `passed=True` でスキップされます。

---

## 3. Vercel で GitHub プッシュ時に自動デプロイする手順

企業側が自社の **Next.js（＋ Supabase）** リポジトリを、GitHub に push したタイミングで Vercel に自動デプロイするための手順です。**この ai_agent リポジトリ自体を Vercel にデプロイするものではありません**（FastAPI サーバーは別ホスト想定）。あくまで「企業が作る Next.js アプリ」のデプロイ手順です。

1. **自社の Next.js リポジトリを GitHub に push する**  
   develop_agent が main ブランチに直接 push し、成果が含まれている状態にする。

2. **Vercel でプロジェクトをインポートする**  
   - [Vercel](https://vercel.com) にログインし、**Add New** → **Project** を選択。  
   - **Import Git Repository** から、自社の Next.js リポジトリを選択する。  
   - フレームワークは Next.js として自動検出される。ルートディレクトリやビルドコマンドは必要に応じて変更する。

3. **環境変数を設定する**  
   - **Settings** → **Environment Variables** で、本番・プレビューに必要な変数を追加する。  
   - 例: `SUPABASE_URL`, `SUPABASE_ANON_KEY`（または `SUPABASE_SERVICE_KEY`）、その他 Next.js アプリが参照する環境変数。

4. **デプロイする**  
   - 初回は **Deploy** でデプロイが開始される。  
   - 以降、**Production Branch**（通常は `main`）への push ごとに自動でデプロイされる。  
   - develop_agent は main ブランチに直接 push するため、Vercel の Production Branch を `main` にしておくと push 時点で本番に反映される。

---

## 4. クリーンアップ方針

- 成果物は `output/<run_id>/` に出力されます。`output/` ディレクトリは .gitignore で除外することを推奨します（リポジトリにコミットしない）。古い run の成果物は必要に応じて手動で削除して構いません。
