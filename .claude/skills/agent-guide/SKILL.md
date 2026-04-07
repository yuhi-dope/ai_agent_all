---
name: agent-guide
description: AIエージェント体制・サブエージェント・並列開発戦略のガイド。エージェントの使い分け、並列開発の進め方、モジュール独立性を確認したい時に使用。「どのエージェントを使う」「並列で進めたい」「実装の順番」等のリクエストにマッチ。
allowed-tools: Read, Grep, Glob
---

# AIエージェント体制 & 並列開発戦略

> **エージェントレジストリ**: `.claude/AGENT_REGISTRY.md` を参照
> **週1回 `/review-agents` を実行**して体制を最新に保つ

## 3層マイクロエージェントアーキテクチャ

```
Layer 0: BPO Manager（workers/bpo/manager/）← オーケストレーター
Layer 1: 業種Domainパイプライン（workers/bpo/{industry}/pipelines/）
Layer 2: 共通マイクロエージェント（workers/micro/）← 全業種で再利用
```

## 実装に使うサブエージェント

| 何を作るか | 使うエージェント |
|---|---|
| 共通マイクロエージェント（OCR/抽出/検証等） | `Agent micro-agent: {agent_names}` |
| 業種別パイプライン（見積/請求/安全書類等） | `Agent bpo-pipeline: {industry}/{name}` |
| BPO Managerオーケストレーター | `Agent bpo-orchestrator: {component}` |
| brain/モジュール | `Agent brain-module: {module}` |
| FastAPIルーター | `Agent router-impl: {router}` |
| Next.jsページ | `Agent frontend-page: {page}` |
| SaaSコネクタ | `Agent connector-impl: {saas_name}` |

## よく使うスキル

| コマンド | 用途 | タイミング |
|---|---|---|
| `/review-agents` | エージェント体制レビュー | **週1回** |
| `/add-pipeline {industry}/{name}` | 新パイプライン追加 | 新業種・新業務フロー追加時 |
| `/run-pipeline {industry}/{name}` | パイプライン動作テスト | 実装後の精度検証 |
| `/implement {module}` | モジュール実装 | 各モジュール開発時 |
| `/test-module {module}` | テスト実行 | 実装後 |
| `/db-migrate` | DBマイグレーション作成 | スキーマ変更時 |
| `/ui-review` | UI/UXルール違反チェック | フロント実装後に必ず |
| `/sec-check` | 導入前セキュリティ7件確認 | パイロット投入前・デプロイ前 |
| `/pre-deploy` | デプロイ前全体チェック | 本番リリース前 |
| `/ship` | 実装→テスト→セキュリティ→デプロイ前一括 | リリース前 |

## 新業種を追加するとき

```
1. /add-pipeline {industry}/{pipeline_name}  # スケルトン生成
2. Agent bpo-pipeline: {industry}/{name}     # 実装
3. /run-pipeline {industry}/{name}           # 精度検証
4. /review-agents                           # レジストリ更新
```

## モジュール独立性マトリクス

| モジュール | 依存先 | 並列度 | Step | MVP? |
|---|---|---|---|---|
| `llm/` | 外部APIのみ | ◎ 完全独立 | 0 | MVP |
| `db/` | なし | ◎ 完全独立 | 0 | MVP |
| `auth/` | `db/` | ○ db完成後 | 0 | MVP |
| `security/encryption` | なし | ◎ 完全独立 | 0 | MVP |
| `brain/extraction/` | `llm/` | ○ llm完成後 | 1 | MVP |
| `brain/ingestion/` | `llm/`, `brain/extraction/` | ○ | 1 | MVP |
| `brain/knowledge/` | `llm/`, `db/` | ○ | 1 | MVP |
| `brain/genome/` | `db/` | ○ | 1 | MVP |
| `brain/visualization/` | `brain/knowledge/` | ○ | 1 | MVP |
| `brain/proactive/` | `brain/twin/`, `llm/` | ○ | 1 | MVP |
| `brain/twin/` | `brain/knowledge/`, `db/` | ○ | 1 | MVP |
| `workers/bpo/` | `llm/`, `workers/connector/` | ○ | 2 | MVP |
| `workers/connector/` | 外部SaaS API | ◎ 完全独立 | 2 | MVP |
| `routers/*` | 対応するbrainモジュール | ○ 同時開発可 | 各Step | MVP |
| `frontend/` | `routers/` のAPI仕様 | ○ モック可 | 各Step | MVP |

## 並列化の鉄則

1. **1ファイル=1責務**: main.pyに集約しない。ルーターは1ドメイン1ファイル
2. **インターフェースファースト**: モジュール間はPydanticモデルで契約定義 → 中身は後から
3. **worktree分離**: 同一ファイルを触るリスクがある場合は `isolation: "worktree"`
4. **テストは実装と同時**: 各Agentが自分のモジュールのテストも書く
5. **DBスキーマ変更はmigrationsに追加のみ**: 既存ファイルを変更しない
