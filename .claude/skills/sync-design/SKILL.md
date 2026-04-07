---
name: sync-design
description: コード変更に連動して設計ドキュメントを自動更新する。BPO業界追加・削除、パイプライン変更、ルーター追加、DB変更時に関連する設計書を特定して修正する。「設計書を同期」「ドキュメント更新」「設計反映」等にマッチ。コード変更後に自動実行推奨。
argument-hint: "[変更内容の説明] (例: 卸売業パイプライン追加, logistics ルーター変更)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

# Sync Design — コード↔設計書 自動同期

変更内容: $ARGUMENTS

## 目的

コードの変更に合わせて、関連する設計ドキュメントを自動的に更新する。
設計書はSource of Truth（CLAUDE.md参照）なので、コード変更→設計書反映を徹底する。

## Step 1: 変更検出

直近のgit diff（またはユーザ指定の変更内容）から、影響範囲を特定する。

```bash
# 未コミット差分を確認
cd /Users/sugimotoyuuhi/code/ai_agent && git diff --name-only
git diff --cached --name-only
```

## Step 2: 影響する設計書を特定

以下のマッピングに従い、変更されたコードに対応する設計書を特定する。

### コード→設計書マッピング

| コードパス | 関連設計書 |
|---|---|
| `workers/bpo/{industry}/` | `shachotwo/e_業界別BPO/e_*_{industry}*.md` + `e_00_業界別BPO業務フロー総覧.md` |
| `workers/bpo/manager/` | `shachotwo/d_アーキテクチャ/d_01_BPOエージェントエンジン設計.md` |
| `workers/bpo/common/` | `shachotwo/e_業界別BPO/e_00a_バックオフィス業務実態調査.md` |
| `brain/` | `shachotwo/b_詳細設計/b_01_ブレイン編.md` + `c_02_プロダクト設計.md` |
| `brain/genome/` | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（Layer 0セクション） |
| `routers/bpo/` | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（APIセクション） |
| `routers/` (非BPO) | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（APIセクション） |
| `db/migrations/` | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（DBセクション） + `db/schema.sql` |
| `db/schema.sql` | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（DBセクション） |
| `main.py` | `CLAUDE.md`（APIカウント） |
| `frontend/` | `shachotwo-app/frontend/UI_RULES.md` の対象ページリスト |
| `workers/connector/` | `shachotwo/b_詳細設計/b_03_自社システム編.md` |
| `security/` | `shachotwo/a_セキュリティ/a_01_セキュリティ設計.md` |
| `llm/` | `shachotwo/c_事業計画/c_02_プロダクト設計.md`（技術スタック） |
| `CLAUDE.md` | — (自身がSource of Truth) |

### 常に確認する設計書
- `CLAUDE.md` — スコープ制限・業界数・パイプライン数・テーブル数の整合
- `shachotwo/c_事業計画/c_02_プロダクト設計.md` — 全体アーキテクチャの整合

## Step 3: 設計書を更新

対応する設計書それぞれについて、以下をサブエージェントで並列実行する:

1. 設計書を読む
2. コード変更に基づいて矛盾する記述を特定
3. 矛盾箇所を修正（数値・リスト・図・説明）
4. 修正が不要な場合はスキップ

**修正ルール:**
- 数値（業界数、テーブル数、パイプライン数等）は実コードからカウントして正確な値を記載
- 業界リスト・モジュールリストは実コードと完全一致させる
- 図やASCII artは内容が変わった場合のみ更新
- 設計意図・方針の記述は変更しない（コード側が設計に従うべき）

## Step 4: CLAUDE.mdの整合チェック

最後にCLAUDE.mdの以下の数値を実コードと照合:
- BPOルーター数（`main.py`のinclude_router行をカウント）
- パイプライン数（`PIPELINE_REGISTRY`のエントリ数）
- テーブル数（`db/schema.sql`のCREATE TABLE数 + migrations数）

## 完了条件

- 変更されたコードに関連する全設計書が最新化
- CLAUDE.mdの数値がコードと一致
- 矛盾する記述が0件
