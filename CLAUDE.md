# シャチョツー（社長2号） — 開発ガイド

> **このプロジェクトは一からリビルドする。**
> 設計の正（Source of Truth）は `shachotwo/` 配下のドキュメント。
> コードが設計と矛盾する場合、設計が正しい。

## 設計ドキュメント（実装前に必ず参照）

| ドキュメント | 内容 | いつ読むか |
|---|---|---|
| `shachotwo/c_02_プロダクト設計.md` | **全体アーキテクチャ・DB設計・API設計・セキュリティ** | 実装前に必ず |
| `shachotwo/c_03_実装計画.md` | Phase別タスクリスト・完了基準・開発ステップ（旧04統合済） | タスク着手前 |
| `shachotwo/c_01_事業計画.md` | 料金・収支・GTM・競合 | ビジネスロジック実装時 |
| `shachotwo/b_01_ブレイン編.md` | ブレインLayer詳細設計・ナレッジ構造化・Q&A | brain/ 実装時 |
| `shachotwo/b_02_BPO編.md` | BPO Worker詳細設計・SaaS自動化・Shadow Mode | workers/ 実装時 |
| `shachotwo/b_03_自社システム編.md` | 自社システム移行・ブラウザ拡張・SaaS解約 | workers/engineer/ 実装時 |
| `shachotwo/a_01_セキュリティ設計.md` | セキュリティ詳細（暗号化・RBAC・PII・監査） | security/ 実装時 |
| `shachotwo/a_02_コンプライアンス設計.md` | 法令対応・同意管理・独禁法・データ保護 | compliance実装時 |

---

## MVP原則（Elon Mode）

> **9週間でPMF検証に到達する。不要なものは全て切る。**

### スコープ制限
- **DB**: 12テーブル（25→12。genome/whatif/benchmark等はPhase 2+）
- **API**: 21エンドポイント（40→21。visualization詳細/benchmark/spec_dialogue削除、ユーザー管理3件追加。詳細は c_02 §15 が正）
- **LangGraph**: Phase 1では不使用。async/awaitで十分。Phase 2のBPO HitLから導入
- **デジタルツイン**: 5次元（ヒト/プロセス/コスト/ツール/リスク）。⑥〜⑨はPhase 2+
- **RBAC**: 2ロール（admin/editor）。5ロールはPhase 3+
- **PII**: regex only。NER/LLMはPhase 2+
- **暗号化**: Supabase native。GCP KMSはEnterprise要件が出てから
- **コネクタ**: Tier 1 APIのみ（kintone/freee/Slack/LINE WORKS）
- **Embedding**: 512次元（1024はPhase 2+）

### 切ったもの（Phase 2+で復活）
- Issue Tree-Driven知識収集 → リニアQ&A + 自由記述で代替
- Half-lifeモデル → 手動リフレッシュ + 単純TTL
- セマンティックキャッシュ → 完全一致キャッシュ（Redis）
- Shadow Mode 30日検証 → ヒストリカルシミュレーション3日
- 3層コネクタ（API/iPaaS/Computer Use）→ API直接のみ
- ゲノム遺伝子合成 → 静的テンプレートJSON

### PMF検証タイムライン
- Week 1-3: Phase 0 基盤（DB + Auth + LLM + スケルトン）
- Week 4-6: Phase 1 ブレインMVP（Q&A + テンプレート）
- Week 7: パイロット3-5社投入
- Week 8-9: PMFゲート（NPS≥30 / WAU≥60% / 「ないと困る」≥60%）

---

## 技術スタック

| レイヤー | 技術 | 備考 |
|---|---|---|
| API | FastAPI (Python 3.11+) | async/await, OpenAPI自動生成 |
| LLM | Gemini 2.5 Flash (MVP) | Claude フォールバック。model_tier: fast/standard/premium |
| Embedding | Voyage AI (voyage-3) | 日本語業界用語に強い |
| Vector DB | Supabase pgvector (HNSW) | RLS + Auth + Storage 統合 |
| DB | Supabase (PostgreSQL) | 全テーブルRLS必須 |
| Agent | LangGraph（Phase 2+） | 状態管理・Human-in-the-Loop。Phase 1はasync/awaitで代替 |
| Frontend | Next.js + Tailwind + shadcn/ui | SSR, PWA対応 |
| OCR | Google Document AI | 日本語手書き・帳票対応 |
| STT | OpenAI Whisper | 日本語音声 |
| Infra | GCP Cloud Run | サーバーレス, Blue/Green |
| CI/CD | GitHub Actions | lint + type check + test + security scan |

---

## 目標ディレクトリ構成（並列開発最適化）

```
shachotwo-app/           # 新規プロジェクトルート
├── brain/               # Layer 2: デジタルツイン中核
│   ├── ingestion/       #   音声・テキスト・ドキュメント・対話型取り込み
│   ├── inference/       #   行動推論（Slack/SaaS操作ログ → 暗黙ルール）
│   ├── extraction/      #   LLM構造化（テキスト → ルール/フロー/判断基準）
│   ├── twin/            #   9次元モデル・What-if・リスク検出
│   ├── proactive/       #   能動提案・ルール改善・リスクアラート
│   ├── knowledge/       #   Q&Aエンジン・ベクトル検索・グラフ・バージョニング
│   ├── visualization/   #   フロー図・意思決定ツリー・充足度マップ
│   └── genome/          #   業界テンプレート・遺伝子合成・フィードバック
│
├── workers/             # Layer 3: 実行エージェント
│   ├── bpo/             #   BPO Worker（SaaS操作自動化）
│   ├── engineer/        #   Engineer Worker（要件定義→スペック対話）
│   └── connector/       #   統合コネクタ（API/iPaaS自動選択）
│
├── security/            # 横断: セキュリティ
│   ├── encryption.py    #   AES-256-GCM
│   ├── access_control.py #  RBAC (CEO/Executive/Director/Manager/Employee)
│   ├── audit.py         #   監査ログ
│   ├── pii_handler.py   #   PII検出+マスク
│   ├── consent.py       #   同意管理
│   └── compliance.py    #   法令チェック
│
├── network/             # Layer 4: ネットワーク知性
│   ├── anonymizer.py    #   k-匿名化
│   ├── benchmark.py     #   ベンチマーク計算
│   ├── patterns.py      #   業界パターン集約
│   └── insights.py      #   因果・予測パターン
│
├── llm/                 # LLM抽象化
│   ├── client.py        #   Gemini→Claude→GPT 切替可能
│   └── prompts/         #   用途別プロンプト
│
├── routers/             # FastAPI ルーター（1ドメイン=1ファイル）
│   ├── company.py
│   ├── ingestion.py
│   ├── inference.py
│   ├── knowledge.py
│   ├── digital_twin.py
│   ├── proactive.py
│   ├── visualization.py
│   ├── execution.py     #   BPO
│   ├── connector.py
│   ├── spec_dialogue.py
│   ├── dashboard.py
│   └── benchmark.py
│
├── db/                  # DB関連
│   ├── schema.sql       #   初期スキーマ
│   ├── migrations/      #   マイグレーション（連番SQL）
│   └── supabase.py      #   Supabaseクライアント
│
├── auth/                # 認証
│   ├── jwt.py           #   JWT検証
│   └── middleware.py    #   認証ミドルウェア
│
├── frontend/            # Next.js フロント
│   ├── app/             #   App Router
│   ├── components/      #   UIコンポーネント
│   └── lib/             #   APIクライアント等
│
├── tests/               # pytest
│   ├── brain/
│   ├── workers/
│   ├── security/
│   └── routers/
│
├── main.py              # FastAPIエントリ（include_routerのみ）
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

---

## 並列サブエージェント開発戦略

### モジュール独立性マトリクス

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
| `brain/inference/` | `llm/` | ○ | 3 | **Phase 2+** |
| `workers/bpo/` | `llm/`, `workers/connector/` | ○ | 2 | MVP |
| `workers/connector/` | 外部SaaS API | ◎ 完全独立 | 2 | MVP |
| `workers/engineer/` | `llm/` | ○ | 3 | **Phase 2+** |
| `network/` | `db/`, `brain/knowledge/` | ○ | 4 | **Phase 2+** |
| `routers/*` | 対応するbrainモジュール | ○ 同時開発可 | 各Step | MVP |
| `frontend/` | `routers/` のAPI仕様 | ○ モック可 | 各Step | MVP |

### Step 0 並列化プラン（5エージェント同時）— MVP版

```
Agent 1: db/schema.sql（12テーブル）+ db/migrations/ + db/supabase.py
Agent 2: llm/client.py + llm/prompts/ (Gemini Flash抽象化)
Agent 3: auth/jwt.py + auth/middleware.py（admin/editor 2ロール）
Agent 4: main.py + routers/ MVPスケルトン（18エンドポイント）+ docker-compose.yml
Agent 5: frontend/ 基盤 (Next.js + Tailwind + shadcn/ui セットアップ)
→ 全部独立。衝突なし。音声/OCR/LINE BotはPhase 1以降。
```

### Step 1 並列化プラン（6+エージェント同時）

```
Agent 1: brain/extraction/ (テキスト→構造化パイプライン)
Agent 2: brain/ingestion/ (音声Whisper + OCR Document AI)
Agent 3: brain/knowledge/ (Q&A + ベクトル検索 + セマンティックキャッシュ)
Agent 4: brain/genome/ (テンプレート基盤 + 建設業テンプレート)
Agent 5: brain/genome/ (製造業テンプレート) ← Agent4と別ファイル
Agent 6: brain/visualization/ (Mermaid図 + 充足度マップ)
Agent 7: brain/proactive/ (リスク検出 + 改善提案)
Agent 8: brain/twin/ (9次元モデル + What-if)
Agent 9: routers/ (各ルーターをブレインと同時開発)
Agent 10: frontend/ (画面実装)
→ テンプレートは業種ごとに完全独立。
```

### 並列化の鉄則

1. **1ファイル=1責務**: main.pyに集約しない。ルーターは1ドメイン1ファイル
2. **インターフェースファースト**: モジュール間はPydanticモデルで契約定義 → 中身は後から
3. **worktree分離**: 同一ファイルを触るリスクがある場合は `isolation: "worktree"`
4. **テストは実装と同時**: 各Agentが自分のモジュールのテストも書く
5. **DBスキーマ変更はmigrationsに追加のみ**: 既存ファイルを変更しない

---

## コーディング規約

- **型ヒント必須**: 関数シグネチャに型アノテーション
- **async/await**: FastAPIエンドポイントは原則async
- **Pydantic**: リクエスト/レスポンスはPydanticモデル
- **RLS意識**: DB操作は必ず `company_id` フィルタ。テナント分離を破る操作は禁止
- **LLM呼び出し**: `llm/client.py` の抽象化レイヤーを通す
- **テスト**: `pytest`、`tests/` にミラー構造で配置
- **Git**: `feat:`, `fix:`, `chore:`, `docs:` + 日本語OK

---

## DB設計（主要テーブル）

> 詳細は `shachotwo/c_02_プロダクト設計.md` Section 5 参照
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

---

## セキュリティ原則

- RLS全テーブル適用（company_idテナント分離）
- Supabase native暗号化（MVP）→ AES-256-GCM + GCP KMS（Enterprise）
- RBAC: admin / editor（MVP）→ 5ロール（Phase 3+）
- PII検出: regex only（MVP）→ regex + NER + LLM（Phase 2+）
- 監査ログ: CRUD基本ログ（MVP）→ 全操作記録（Phase 2）
- LLMモデル学習にデータ不使用（商用API DPA）
- .env / credentials をコミットしない
