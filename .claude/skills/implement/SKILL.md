---
name: implement
description: シャチョツーのモジュールを実装する。パスに応じて適切なサブエージェントに自動振り分け。コードの実装・モジュール作成・機能追加時に使用。「○○を作って」「○○を実装して」「○○を追加して」等のリクエストにマッチ。
argument-hint: "[module-path] (例: brain/ingestion, routers/digital_twin, frontend/login, workers/micro/rule_matcher, workers/bpo/construction/billing)"
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Agent
---

## シャチョツー モジュール実装

対象モジュール: $ARGUMENTS

### サブエージェント振り分けルール

パスプレフィックスに応じて以下のエージェントに委譲する：

| パスプレフィックス | 使うエージェント |
|---|---|
| `brain/` | `Agent brain-module: $ARGUMENTS` |
| `routers/` | `Agent router-impl: $ARGUMENTS` |
| `frontend/` | `Agent frontend-page: $ARGUMENTS` |
| `workers/micro/` | `Agent micro-agent: $ARGUMENTS` |
| `workers/bpo/` | `Agent bpo-pipeline: $ARGUMENTS` |
| `workers/connector/` | `Agent connector-impl: $ARGUMENTS` |
| その他（security/, llm/, db/ 等） | 直接実装（以下の手順に従う） |

### 直接実装の手順（security/, llm/, db/ 等）

1. **設計確認**: `shachotwo/c_02_プロダクト設計.md` から対象モジュールの仕様を読む
2. **既存コード確認**: 依存先モジュールの現在の実装を読む
3. **実装**:
   - 型ヒント必須
   - async/await で外部API呼び出し
   - Pydantic で入出力モデル定義
   - company_id ベースのRLS意識
   - LLM呼び出しは `llm/client.py` 経由
   - DB操作は `db/supabase.py` 経由
4. **テスト作成**: `tests/` にモックテストを作成
5. **テスト実行**: `/test-module $ARGUMENTS` で確認
6. **設計書同期**: `/sync-design $ARGUMENTS` で関連設計書を自動更新
