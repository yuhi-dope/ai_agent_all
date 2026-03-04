# アクションスケジュール

**最終更新:** 2026-03-04
**対象:** bpo_agent 統一アーキテクチャ（v5.0）の実装ロードマップ

---

## 完了済み

| 項目 | 完了日 | 成果物 |
| --- | --- | --- |
| Phase 1 MVP: 6 SaaS 接続基盤 | 済 | `server/saas/mcp/` アダプタ群、OAuth、トークンリフレッシュ、監査ログ |
| 4層ルール合成 | 済 | `server/rules_merge.py`, `agent/utils/rule_loader.py` |
| BPO計画の確信度・警告 | 済 | `bpo_agent/nodes/`, `server/saas/task_persist.py` |
| **Typed Function Tools 導入** | 2026-03-04 | `server/saas/tools/` — 6 SaaS, 46ツール。旧アダプタ ~1,590行→~900行 |

---

## Phase 1 拡張（Month 4-6）

**目的:** Typed Function Tools 上で各 SaaS の実 API 呼び出しを完成させ、BPO 実務に耐えるツール基盤にする。

| # | タスク | 詳細 | 対象ファイル | 見積 |
| --- | --- | --- | --- | --- |
| 1-1 | kintone ツールの結合テスト | 実 kintone 環境（テストアプリ）でレコード CRUD・フィールド追加・デプロイを E2E 検証 | `tests/test_kintone_integration.py` | 1日 |
| 1-2 | Salesforce ツールの結合テスト | Sandbox 環境で SOQL, レコード作成・更新, Describe を検証 | `tests/test_salesforce_integration.py` | 1日 |
| 1-3 | freee ツールの結合テスト | テスト事業所で仕訳作成・一覧・試算表・消込を検証 | `tests/test_freee_integration.py` | 1日 |
| 1-4 | Slack/Google/SmartHR 結合テスト | 各SaaS のテスト環境で基本操作を検証 | `tests/test_*_integration.py` | 2日 |
| 1-5 | スケジューラ実装 | Cloud Scheduler → Cloud Tasks → bpo_agent の定期実行（月次締め、日次リマインド等） | `server/scheduler.py`, `server/main.py` | 3日 |
| 1-6 | イベントリスナー実装 | SaaS 側 Webhook 受信 → bpo_agent タスク自動起動 | `server/webhooks.py` | 2日 |
| 1-7 | SaaS 構造スキャン機能 | ToolRegistry の読み取り系ツール実行結果を `saas_structure_knowledge` に自動蓄積 | `server/saas/executor.py`, migration | 2日 |
| 1-8 | 専門化成熟度スコア基盤 | `bpo_specialist_maturity` テーブル + `server/saas/specialist.py` + API 2本 | 新規 | 2日 |
| 1-9 | 旧 mcp/ アダプタの廃止検討 | main.py の OAuth 周りが旧 adapter_cls を使用中。ToolRegistry に OAuth メタ情報を追加して完全移行できるか評価 | `server/saas/tools/registry.py`, `server/main.py` | 1日 |

**Phase 1 拡張 完了条件:**
- 6 SaaS の結合テストが全てパス
- スケジューラで月次締めの定期実行が動作
- 成熟度スコア API が動作

---

## Phase 1.5 専門化（Month 5-8）

**目的:** BPO 実務蓄積から専門エージェントが自律分岐する仕組みと、SaaS 構造ナレッジのテンプレート出力機能を実装。

| # | タスク | 詳細 | 対象ファイル | 見積 |
| --- | --- | --- | --- | --- |
| 1.5-1 | specialist_resolver 実装 | 成熟度スコア ≥ 0.7 で「専門モード」ON。ルール重み付け変更（learned: 40%） | `server/saas/specialist.py` | 2日 |
| 1.5-2 | 専門家ペルソナ注入 | task_planner で system_prompt に専門家ペルソナ（実績N社・Mタスク）を動的注入 | `bpo_agent/nodes/task_planner.py` | 1日 |
| 1.5-3 | SaaS 構造ナレッジ自動蓄積 | executor の読み取り系ツール実行時に構造データを `saas_structure_knowledge` へ自動保存 | `server/saas/executor.py` | 2日 |
| 1.5-4 | テンプレート生成エンジン | 蓄積した構造ナレッジを匿名化し、横展開可能なテンプレートとして `saas_structure_templates` に保存 | `server/saas/template_engine.py` | 3日 |
| 1.5-5 | テンプレート出力 API | `POST /api/saas/template/export` — テンプレートを bpo_agent code_tools 入力形式で出力 | `server/main.py` | 1日 |
| 1.5-6 | 成熟度ダッシュボード | ジャンル×SaaS の成熟度マトリクス表示。専門化閾値の可視化 | ダッシュボード側 | 3日 |
| 1.5-7 | ナレッジ一覧ダッシュボード | 蓄積済み SaaS 構造ナレッジの一覧・詳細表示 | ダッシュボード側 | 2日 |
| 1.5-8 | DB マイグレーション | `bpo_specialist_maturity`, `saas_structure_knowledge`, `saas_structure_templates` の3テーブル | `docs/migrations/` | 1日 |

**Phase 1.5 完了条件:**
- 会計（freee）ジャンルで成熟度スコア ≥ 0.7 達成時に専門モードが自動発動
- kintone アプリ構造がナレッジとして自動蓄積される
- ダッシュボードでテンプレート選択→出力ができる

---

## Phase 2 理解（Month 7-9）

**目的:** BPO 操作ログから業務パターンを自動検出し、企業固有の業務モデルを構築する。

| # | タスク | 詳細 | 対象ファイル | 見積 |
| --- | --- | --- | --- | --- |
| 2-1 | パターン検出エンジンの本番投入 | `server/pattern_detector.py`（実装済み）を定期実行ジョブとして組み込み | `server/scheduler.py` | 1日 |
| 2-2 | シーケンス検出の精度向上 | 時間窓の最適化、ノイズフィルタリング。実運用データでチューニング | `server/pattern_detector.py` | 3日 |
| 2-3 | フィールドマッピング精度向上 | SaaS 間のフィールド対応推定を、構造ナレッジ（Phase 1.5）と組み合わせて高精度化 | `server/pattern_detector.py` | 3日 |
| 2-4 | 企業業務モデル自動生成 | 検出パターン + 構造ナレッジ → 企業業務モデル JSON 生成。LLM で自然言語サマリ付与 | `server/business_model.py` | 5日 |
| 2-5 | 業務フロー可視化ダッシュボード | 企業業務モデルを Mermaid フローチャートで表示 | ダッシュボード側 | 3日 |
| 2-6 | 業務モデル API | `GET /api/business-model/{company_id}`, `GET /api/patterns` | `server/main.py` | 1日 |

**Phase 2 完了条件:**
- 1社以上で「月次締めワークフロー」が自動検出される
- ダッシュボードで業務フローが可視化される
- 企業業務モデル JSON が生成される

---

## Phase 3 独立（Month 10-12）

**目的:** 蓄積した SaaS 構造テンプレートと企業業務モデルから、個社最適の自社システムを自動生成する。

| # | タスク | 詳細 | 対象ファイル | 見積 |
| --- | --- | --- | --- | --- |
| 3-1 | code_tools の bpo_agent 統合 | 旧 develop_agent のノード群を bpo_agent の capability として統合 | `bpo_agent/capabilities/code/` | 5日 |
| 3-2 | Supabase スキーマ自動生成 | `saas_structure_templates` → CREATE TABLE + RLS ポリシー自動生成 | `server/saas/schema_generator.py` | 3日 |
| 3-3 | Next.js ページ自動生成 | `operation_patterns` → ページコンポーネント + ルーティング自動生成 | code_tools 経由 | 5日 |
| 3-4 | 統合ロジック自動生成 | `cross_tool_mappings` → SaaS 間連携を自社 DB 内ロジックに変換 | code_tools 経由 | 3日 |
| 3-5 | データ移行パイプライン | SaaS API → 自社 DB への全件データ移行。差分同期。ロールバック機能 | `server/migration/` | 5日 |
| 3-6 | 移行計画 API | `POST /api/migration/plan`, `POST /api/migration/execute` | `server/main.py` | 2日 |
| 3-7 | 1社実証 | 実際の顧客企業で SaaS → 自社システム移行を実施。フィードバック反映 | 全体 | 10日 |

**Phase 3 完了条件:**
- 1社で kintone → 自社システム移行が完了
- 移行後のシステムが元の kintone と同等の操作感で動作
- データ移行パイプラインが差分同期で稼働

---

## 依存関係

```
Phase 1 拡張
  └─ Phase 1.5 専門化（1-7, 1-8 が前提）
       ├─ Phase 2 理解（構造ナレッジが前提）
       │    └─ Phase 3 独立（業務モデルが前提）
       └─ Phase 3 独立（テンプレートが前提）
```

## Phase 移行の判断基準

| 移行 | トリガー |
| --- | --- |
| Phase 1拡張 → Phase 1.5 | 6 SaaS の結合テスト完了 & 成熟度スコア基盤が動作 |
| Phase 1.5 → Phase 2 | 3ジャンル以上で成熟度 ≥ 0.5 & 構造ナレッジが50件以上蓄積 |
| Phase 2 → Phase 3 | 1社以上で業務モデル生成完了 & 顧客が自社システム移行を希望 |
