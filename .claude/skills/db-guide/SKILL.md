---
name: db-guide
description: DB設計・テーブル構造・RLSルールのガイド。DBスキーマを確認したい時、テーブル追加・変更時、RLS設計を確認したい時に使用。「テーブル一覧」「DB設計」「スキーマ確認」等のリクエストにマッチ。
allowed-tools: Read, Grep, Glob
---

# DB設計（主要テーブル）

> 詳細は `shachotwo/c_事業計画/c_02_プロダクト設計.md` Section 5 参照
> **MVP: 12テーブル。Phase 2+で追加**

| テーブル | 用途 | MVP? |
|---|---|---|
| `companies` | テナント管理 | MVP |
| `users` | ロール(admin/editor)・部署 | MVP |
| `knowledge_items` | ナレッジ本体 + embedding + source + confidence | MVP |
| `knowledge_relations` | ナレッジ間関係(depends_on/contradicts/refines/part_of/triggers) | MVP |
| `knowledge_sessions` | 取り込みセッション管理 | MVP |
| `company_state_snapshots` | 5次元状態JSON（ヒト/プロセス/コスト/ツール/リスク） | MVP |
| `proactive_proposals` | 能動提案(risk_alert/improvement/rule_challenge/opportunity) | MVP |
| `decision_rules` | 意思決定ルール(formula/if_then/matrix/heuristic) | MVP |
| `tool_connections` | SaaS接続情報・ヘルスチェック | MVP |
| `execution_logs` | BPO実行ログ | MVP |
| `audit_logs` | CRUD基本監査ログ | MVP |
| `consent_records` | 同意管理 | MVP |
| `genome_templates` | 業界テンプレート | Phase 2+ |
| `whatif_simulations` | What-ifシナリオ・パラメータ変更・差分 | Phase 2+ |
| `anonymous_benchmarks` | 匿名ベンチマーク | Phase 2+ |
| `industry_patterns` | 業界パターン集約 | Phase 2+ |
| `inference_logs` | 行動推論ログ | Phase 2+ |
| `spec_documents` | スペック文書 | Phase 2+ |
| `spec_dialogue_logs` | スペック対話ログ | Phase 2+ |

**全テーブル `company_id` ベースのRLS必須。例外なし。**

## 実際のスキーマ確認

```bash
cat shachotwo-app/db/schema.sql
ls shachotwo-app/db/migrations/
```
