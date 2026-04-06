# シャチョツー（社長2号） — 開発ガイド

> **このプロジェクトは一からリビルドする。**
> 設計の正（Source of Truth）は `shachotwo/` 配下のドキュメント。
> コードが設計と矛盾する場合、設計が正しい。

---

## プロジェクト構造原則（厳守）

> **保守運用コスト最小化のため、以下の構造を崩さない。**
> ファイルを新規作成する前に「どこに置くか」をこのルールで確認すること。

### リポジトリルート（5つだけ）

```
ai_agent/                          ← git root
├── CLAUDE.md                      ← 開発ガイド（このファイル）
├── shachotwo/                     ← 設計ドキュメント（Source of Truth）
├── shachotwo-app/                 ← ★全プロダクトコード（唯一のコードベース）
├── shachotwo-マーケAI/            ← アーカイブ予定（shachotwo-appに吸収中）
├── shachotwo-契約AI/              ← アーカイブ予定（shachotwo-appに吸収中）
└── shachotwo-X運用AI/             ← X運用ツール（独立小規模、GAS中心）
```

### 鉄則

1. **コードは `shachotwo-app/` に一元化**
   - ブレイン、BPO（16業種）、マーケ/SFA/CRM/CS、コネクタ、フロント — 全て `shachotwo-app/`
   - 新しいAIエージェントを作りたくなっても別リポジトリを切らない。`workers/bpo/{name}/` に追加する
   - 理由: DB・認証・LLM抽象化・マイクロエージェントの二重管理を防ぐ

2. **設計書は `shachotwo/` に一元化**
   - 新しい設計書 → `shachotwo/` の適切なサブディレクトリに配置
   - プロジェクトルートにmd/pptx/pyを散在させない
   - サブディレクトリ: `a_セキュリティ/` `b_詳細設計/` `c_事業計画/` `d_アーキテクチャ/` `e_業界別BPO/` `f_BPOガイド/` `g_ナレッジ/` `h_意思決定記録/` `z_その他/`

3. **マーケAI・契約AIは吸収統合する**（b_06設計書のPhase 0参照）
   - 使える部品 → `shachotwo-app/workers/micro/` `workers/connector/` に移植
   - 移植完了後 → 元リポジトリはアーカイブ（`shachotwo-マーケAI/` `shachotwo-契約AI/`）
   - 吸収対象の詳細: `shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md` Section 0.3

4. **1ファイル=1責務。main.pyに集約しない**
   - ルーター: 1ドメイン=1ファイル（`routers/sales.py`, `routers/crm.py`）
   - パイプライン: 1業務=1ファイル（`pipelines/estimation_pipeline.py`）
   - マイクロエージェント: 1原子操作=1ファイル（`micro/extractor.py`）

---

## AIエージェント体制

> エージェント・スキル・並列開発の詳細は `/agent-guide` スキルを参照（オンデマンドでロード）。
>
> **要点**: 3層アーキテクチャ（Manager → パイプライン → マイクロエージェント）
> **主要ワークフロー**: `/new-feature` `/ship` `/hotfix`

---

## 設計ドキュメント

> 設計書の完全な一覧は `/design-index` スキルを参照（オンデマンドでロード）。
>
> **最重要3件（常時参照）:**
> - `shachotwo/c_事業計画/c_02_プロダクト設計.md` — 全体アーキテクチャ・DB設計・API設計
> - `shachotwo/c_事業計画/c_03_実装計画.md` — Phase別タスク・完了基準
> - `shachotwo-app/frontend/UI_RULES.md` — フロントエンドUI/UXルール

---

## MVP原則（Elon Mode）

> **9週間でPMF検証に到達する。不要なものは全て切る。**

### スコープ制限
- **DB**: 12コアテーブル + invitations + BPO系9テーブル（合計22テーブル実装済み）
- **API**: 6業界BPOルーター（建設/製造/医療福祉/不動産/物流/卸売）+ 共通BPO + 基盤13ルーター。凍結10業種のルーターは残置（コード削除不要）
- **BPOパイプライン**: コア6業界のパイプラインに集中。凍結業種のパイプラインは残置・新規開発停止。ゲノム駆動化により新業種=JSON追加のみ
- **LangGraph**: Phase 1では不使用。async/awaitで十分。Phase 2のBPO HitLから導入
- **デジタルツイン**: 5次元（ヒト/プロセス/コスト/ツール/リスク）。⑥〜⑨はPhase 2+
- **RBAC**: 2ロール（admin/editor）。5ロールはPhase 3+
- **PII**: regex only。NER/LLMはPhase 2+
- **暗号化**: Supabase native。GCP KMSはEnterprise要件が出てから
- **コネクタ**: Tier 1 APIのみ（kintone/freee/Slack/LINE WORKS）
- **Embedding**: 768次元（Voyage AI voyage-3実装済み。1024はPhase 2+）

### 切ったもの（Phase 2+で復活）
- Issue Tree-Driven知識収集 → リニアQ&A + 自由記述で代替
- Half-lifeモデル → 手動リフレッシュ + 単純TTL
- セマンティックキャッシュ → 完全一致キャッシュ（Redis）
- Shadow Mode 30日検証 → ヒストリカルシミュレーション3日
- 3層コネクタ（API/iPaaS/Computer Use）→ API直接のみ
- ゲノム遺伝子合成 → 静的テンプレートJSON
- 1業種=1ディレクトリのパイプライン → ゲノム駆動型共通エンジン（Phase 2で統合）

### 料金体系（Salesforce型プラットフォーム + パートナー）
- **共通BPO**: ¥150,000/月（バックオフィス全部 + ブレイン込み）
- **業種特化BPO**: ¥300,000/月（共通BPO + 業種固有パイプライン全部まるっと）
- **人間サポート追加**: +¥150,000/月（パートナーによるコンサル型伴走。合計¥450,000）
- **超過分**: 従量課金（基本枠: BPO300回/月・Q&A500回/月を超えた分）
- **オンボーディング**: セルフ(無料) / コンサル(5万×2ヶ月) / フルサポート(30万×3ヶ月)
- **コンサル型デリバリー**: 顧客のITリテラシーが低い前提で設計。ツールを渡すのではなく「結果」を届ける

### 事業モデル（Salesforce型プラットフォーム × 業界パートナー）
- **モデル**: Salesforce = CRM基盤 + パートナー企業が導入支援。シャチョツー = AI BPO基盤 + 業界パートナー（社労士/税理士等）が導入・運用支援
- **Phase 1**: 1社で全業界対応（建設・製造でPMF検証）
- **Phase 2**: 成功業界から分社化（月商1000万超で判断）→ 業界パートナーに株を渡し経営委託
- **Phase 3**: パートナー主導で横展開（ゲノムJSON追加のみで新業種対応）
- **収益構造**: プラットフォーム利用料¥300,000（本体）/ パートナー報酬+¥150,000（パートナー取り分）
- **コア6業界**: 建設業(47万社) / 製造業(38万社) / 医療・福祉(25万施設) / 不動産業(35万社) / 運輸・物流(7万社) / 卸売業(25万社)
- **士業4業種（同時並行開発）**: 社労士（パートナー確定・先行）/ 税理士 / 行政書士 / 弁護士 — 士業グリップ→顧問先紹介で製造業等へ展開する営業チャネル戦略
- **凍結業種**: 歯科/飲食/宿泊/美容/整備/薬局/EC/派遣/設計/金融/農業（パートナーが来たらゲノムJSON追加で復活）

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

## ディレクトリ構成

> 詳細は `/agent-guide` スキルを参照。

```
shachotwo-app/
├── brain/       # デジタルツイン中核（10モジュール）
├── workers/     # 3層パイプライン + マイクロ + コネクタ
│   ├── base/        # Layer A: 全社共通基盤（営業12+バックオフィス24=36本）
│   ├── industry/    # Layer B: 業界特化プラグイン（建設8+製造8+他）
│   ├── micro/       # Layer C: 業界中立マイクロエージェント（19個）
│   ├── connector/   # SaaS連携（8本）
│   └── bpo/         # ※旧構造（base/industry/に移行予定）
├── security/    # セキュリティ横断（RLS 6ロール/SoD/マイナンバー）
├── llm/         # LLM抽象化
├── routers/     # FastAPI（1ドメイン=1ファイル）
├── db/          # DB + migrations（既存22+新規47=69テーブル）
├── auth/        # 認証
├── frontend/    # Next.js（88ページ/22モジュール予定）
├── tests/       # pytest
└── main.py      # エントリ（include_routerのみ）
```

---

## 設計書自動同期ルール（必須）

> **hookで自動通知される。通知を受けたら必ず以下を実行すること。**

1. **パイプライン追加/削除/変更時**:
   - `d_07` のアーキテクチャ図・パイプライン一覧・スケジュール・イベントトリガーを更新
   - `d_06` のLayer 3本数を更新
   - `PIPELINE_REGISTRY`（task_router.py）にエントリ追加/削除
   - `DESIGN_INDEX.md` に新規設計書があれば追加

2. **DBマイグレーション追加時**:
   - `d_08` のテーブル総数を更新
   - 関連パイプラインの設計書（b_07等）にテーブル参照を追加

3. **ルーター追加時**:
   - `main.py` に include_router を追加
   - `d_07` のエンドポイント数を更新

4. **業界プラグイン追加時**:
   - `d_06` のLayer 3bに追加
   - `e_業界別BPO/` に設計書を追加
   - ゲノムJSON（`brain/genome/data/`）を追加

**自動化の仕組み**: `.claude/hooks/sync-design-check.sh` がworkers/routers/migrations変更を検知し、コンテキストにリマインドを注入する。

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

## DB設計 & セキュリティ

> DB設計の詳細は `/db-guide` スキルを参照。
> セキュリティ原則の詳細は `/security-guide` スキルを参照。
>
> **鉄則**: 全テーブル `company_id` ベースのRLS必須。例外なし。
> **導入前7件**: `/sec-check` スキルで自動確認。
