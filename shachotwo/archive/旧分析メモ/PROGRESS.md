# シャチョツー MVP 進捗トラッキング

> 最終更新: 2026-03-20 (戦略改訂: 建設業垂直集中 + RAG強化 + HitL導入)
> PMFタイムライン: Week 1-3 基盤 → Week 4-6 ブレインMVP → **Week 7 パイロット投入** → Week 8-9 PMFゲート

---

## Phase 0: 基盤構築 ✅ 完了

| タスク | 状態 | 備考 |
|---|---|---|
| DB スキーマ（12コアテーブル + RLS） | ✅ | schema.sql + 11マイグレーション |
| Supabase クライアント | ✅ | db/supabase.py |
| LLM 抽象化（Gemini/Claude/GPT） | ✅ | llm/client.py Claude 4.6系 + 自動フォールバック |
| Auth（JWT + Supabase Auth） | ✅ | auth/jwt.py + middleware.py |
| 招待制メンバー管理 | ✅ | routers/invitations.py + DB invitations テーブル |
| RBAC 2ロール（admin/editor） | ✅ | auth/middleware.py require_role() |
| FastAPI エントリ + ルーター登録 | ✅ | main.py + 30ルーター（17業界BPO + 基盤13） |
| Frontend 基盤（Next.js + shadcn/ui） | ✅ | 17ページ実装 |
| PII 検出（regex） | ✅ | security/pii_handler.py |
| 監査ログ | ✅ | security/audit.py |
| レート制限 | ✅ | security/rate_limiter.py |
| LLMコスト上限 | ✅ | llm/cost_tracker.py テナント別¥50,000/月 |
| Docker / docker-compose | ✅ | |

---

## Phase 1: ブレイン MVP ✅ 完了

### ナレッジ入力・構造化

| タスク | 状態 | 備考 |
|---|---|---|
| テキスト入力 → LLM構造化 → 保存 | ✅ | brain/extraction/pipeline.py |
| ファイル入力（PDF/Excel/Word/txt） | ✅ | brain/ingestion/file.py |
| 業種テンプレート適用（16業種） | ✅ | brain/genome/ 4テンプレートJSON（bpo/配下12本分離済み） |
| 音声入力（Whisper） | ⬜ | Phase 2+ |
| OCR（Document AI） | ⬜ | Phase 2+ |
| 対話型引き出し | ⬜ | Phase 2+（LLM追加質問生成） |

### Q&A エンジン

| タスク | 状態 | 備考 |
|---|---|---|
| ベクトル検索（pgvector HNSW） | ✅ | brain/knowledge/search.py hybrid_search |
| Embedding 生成（Voyage AI） | ✅ | brain/knowledge/embeddings.py |
| LLM 回答生成 + 引用表示 | ✅ | brain/knowledge/qa.py |
| Q&A 履歴保存 | ✅ | DB qa_sessions テーブル |

### 能動提案

| タスク | 状態 | 備考 |
|---|---|---|
| リスク検出 + 改善提案生成 | ✅ | brain/proactive/analyzer.py |
| 提案一覧 + ステータス管理 | ✅ | routers/proactive.py + frontend/proposals |

### デジタルツイン

| タスク | 状態 | 備考 |
|---|---|---|
| 5次元スナップショット GET/POST | ✅ | brain/twin/ + routers/digital_twin.py |
| フロー図（Mermaid） | ✅ | brain/visualization/flow_generator.py |
| 意思決定ツリー | ✅ | brain/visualization/decision_tree.py |
| 充足度マップ | ✅ | brain/visualization/completeness_radar.py |
| What-if シミュレーション | ⬜ | Phase 2+ |

---

## BPO ✅ 完了（16業界 #1パイプライン全実装）

### 共通マイクロエージェント（12体）

| エージェント | 状態 |
|---|---|
| document_ocr / structured_extractor / rule_matcher / output_validator | ✅ P0完了 |
| cost_calculator / document_generator / compliance_checker / table_parser | ✅ P1完了 |
| diff_detector / saas_reader / saas_writer / message_drafter | ✅ P2完了 |

### BPOパイプライン（29本実装済み）

| 業界カテゴリ | #1パイプライン | 追加パイプライン |
|---|---|---|
| ★ 建設業 | ✅ 見積 | ✅ 請求/安全書類/原価報告/写真整理（計5本） |
| ★ 製造業 | ✅ 見積AI | — |
| ★ 歯科 | ✅ レセプト点検 | — |
| A群: 不動産/士業/介護/物流/飲食 | ✅ 各業界#1 | — |
| B群: 医療/薬局/美容/整備/ホテル/EC/派遣/設計 | ✅ 各業界#1 | — |
| 共通BPO | ✅ 経費/給与/勤怠/契約/リマインダ/取引先（計6本） | — |

### SaaS コネクタ

| コネクタ | 状態 |
|---|---|
| kintone / freee / Slack | ✅ workers/connector/ |

---

## セキュリティ ✅ 導入前7件全クリア

| # | 項目 | 状態 |
|---|---|---|
| 1 | エラーメッセージ安全化 | ✅ |
| 2 | レート制限（slowapi） | ✅ |
| 3 | LLMコスト上限（¥50,000/月） | ✅ |
| 4 | タイムアウト設定（LLM 30秒） | ✅ |
| 5 | 同時実行制御（Semaphore 3） | ✅ |
| 6 | マルチテナント分離テスト | ✅ |
| 7 | CORS制限 | ✅ |

---

## フロントエンド ✅ 完了

| ページ | 状態 | 備考 |
|---|---|---|
| ログイン / 登録 | ✅ | |
| ダッシュボード | ✅ | 統計 + 月間AIコスト |
| ナレッジ入力 | ✅ | テキスト + ファイル + テンプレート |
| ナレッジ一覧 | ✅ | 検索 + 編集 + 削除 |
| Q&A チャット | ✅ | 技術用語除去・UX改善済み |
| 提案一覧 | ✅ | |
| デジタルツイン | ✅ | |
| 業務自動化（BPO実行） | ✅ | 日本語UI・サンプルデータ説明付き |
| オンボーディング | ✅ | 準備中業種グレーアウト対応 |
| メンバー管理 + 招待 | ✅ | |
| UI/UX 全面改善 | ✅ | 非IT人材向け・技術用語除去 |

---

## Claude Code エージェント体制 ✅ 最適化済み

| 種別 | 数 | 備考 |
|---|---|---|
| サブエージェント | 10体 | ui-reviewer/connector-impl追加済み |
| スキル | 12本 | 開発/品質/事業KPIの3カテゴリ整理済み |
| テスト | 697 pass | 6 skip |

---

## 戦略改訂サマリー（2026-03-20: 外部視点レビュー反映）

> Elon Musk / Warren Buffett / Anthropic・OpenAI・xAI エンジニア視点を統合した修正。
> 詳細は `shachotwo/c_事業計画/c_07_グロースロードマップ.md` 参照。

| 修正項目 | 変更前 | 変更後 | ADR |
|---|---|---|---|
| パイロット戦略 | 建設業・製造業並行 | **建設業主軸**（製造業はセカンダリ） | ADR-008 |
| RAGパイプライン | hybrid_search のみ | **クエリ拡張+再ランキング** enhanced_search | ADR-007 |
| BPO実行フロー | 自動実行 | **HitL承認必須**（見積・請求・給与等） | ADR-009 |
| ナレッジ管理 | 半減期なし | **half_life_days + フィードバック**追加 | ADR-010 |
| Eval基盤 | なし | **ゴールデンデータセット**週次精度測定 | ADR-007 |
| グロース展開 | 未定義 | **Phase A→D ロードマップ**定義 | c_07 |

---

## Phase 1.5: パイロット前強化タスク ✅ 完了 / ⬜ 残り

| タスク | 状態 | 備考 |
|---|---|---|
| RAG クエリ拡張（enhanced_search） | ✅ | brain/knowledge/search.py |
| RAG 再ランキング（rerank_results） | ✅ | brain/knowledge/search.py |
| Eval フレームワーク（eval.py） | ✅ | brain/knowledge/eval.py |
| BPO HitL 承認フロー（DB migration） | ✅ | migrations/015_execution_hitl.sql |
| ナレッジ半減期・フィードバック（DB） | ✅ | migrations/016_knowledge_half_life.sql |
| ADR-007〜010 意思決定記録 | ✅ | h_意思決定記録/ |
| グロースロードマップ文書 | ✅ | c_07_グロースロードマップ.md |
| HitL 承認UIフロントエンド | ⬜ | bpo/page.tsx に承認待ちタブ追加 |
| BPOルーター HitL対応（pending状態保存） | ⬜ | routers/execution.py |
| 建設業 Eval ゴールデンデータセット100問 | ⬜ | パイロット後に収集 |

---

## PMFゲート（Week 8-9） ⬜ 未実施

| 指標 | 目標 | 現状 |
|---|---|---|
| NPS | ≥ **40**（建設業特化で上方修正） | 未計測 |
| WAU（週次アクティブ率） | ≥ 60% | 未計測 |
| 「ないと困る」率 | ≥ **70%**（上方修正） | 未計測 |
| 有償継続意向 | ≥ 3社 | 未計測 |
| Q&A精度（keyword hit rate） | ≥ 75% | Eval未実施 |
| HitL事故件数 | 0件 | 未計測 |
| パイロット投入 | **建設業3社以上**・製造業1〜2社（歯科は法的文書整備後） | ⬜ 未デプロイ |

---

## 直近の優先アクション（Week 7）

1. ~~HitL UIフロントエンド実装~~ ✅ bpo/page.tsx 実装済み
2. **BPOルーター HitL対応** ✅ execution.py: pending状態保存 + 承認API実装済み
3. **`/pilot-prep`** でオンボーディングフロー・招待フロー・BPOパイプラインの最終確認
4. **GCP Cloud Run デプロイ** + Supabase本番環境
5. **建設業パイロット3社投入**（元請・売上50億以上を優先 → ADR-008参照）
6. **PMF計測体制**（NPS送付タイミング・WAU自動集計）
7. **ANDPADパートナー申請**（ADR-011: パイロット前に申請開始）

---

## Phase 2+: 未着手（優先順位を改訂）

| タスク | Phase | 備考 |
|---|---|---|
| LangGraph 統合（軽量HitLをLangGraphに置換） | 2 | ADR-003・ADR-009 |
| 業界ベンチマーク機能（匿名単価集計） | 2 | **建設業20社到達後に最優先** |
| 階層的ナレッジ圧縮（knowledge_summaries） | 2 | ADR-010 |
| 対話型引き出し（LLM追加質問） | 2 | |
| brain/inference/（行動推論） | 2 | |
| 音声入力（Whisper） | 2 | |
| OCR（Document AI） | 2 | |
| セマンティックキャッシュ（Redis） | 2 | |
| PMF 計測基盤（NPS/WAU 自動集計） | 2 | |
| LLMプロンプトインジェクション対策 | 2 | |
| 建設業 #2〜パイプライン拡張 | 2 | **製造業より建設業を優先** |
| RBAC 5ロール拡張 | 3 | |
| workers/engineer/（要件定義→スペック対話） | 3 | |
| network/（匿名ベンチマーク・業界パターン） | 2〜3 | **Phase Bの核心機能** |
| ASEAN展開準備（タイ・ベトナム建設/製造） | 4 | |
| GCP KMS 暗号化（Enterprise） | 3+ | |
