# シャチョツー（社長2号）全体アーキテクチャ

> **このファイル1つでシステム全体がわかる。**
> 最終更新: 2026-03-28

---

## 1. エグゼクティブサマリー

- **何をするか**: 中小企業の社長の頭の中（意思決定・暗黙知・業務フロー）をデジタルツインとして完全モデル化し、ナレッジQ&A・能動提案・BPO業務自動実行を一気通貫で提供する
- **誰が使うか**: 中小企業（10〜300名）の社長・経営者。初期6業界（建設/製造/医療福祉/不動産/物流/卸売）
- **どう稼ぐか**: プラットフォーム本体 + 業界別子会社モデル。ブレイン月3万円 + BPO従量課金（30〜300円/タスク）。子会社売上の30%がプラットフォーム利用料として本体に還流

---

## 2. システム全体図

```
┌──────────────────────────────────────────────────────────────────────┐
│                   シャチョツー — Company Digital Twin                  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Brain（中核）                                                  │  │
│  │  8モジュール: extraction / genome / knowledge / inference       │  │
│  │              ingestion / twin / proactive / visualization       │  │
│  │  デジタルツイン 5次元: ヒト/プロセス/コスト/ツール/リスク        │  │
│  └────────────────────────┬───────────────────────────────────────┘  │
│                           │ ナレッジ・指示提供                       │
│                           ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Layer 0: BPO Manager（オーケストレーター）                      │  │
│  │  ┌──────────────┐ ┌─────────────┐ ┌───────────────────────┐   │  │
│  │  │ScheduleWatcher│ │EventListener│ │ConditionEvaluator    │   │  │
│  │  │(7スケジュール)│ │(9イベント)  │ │(4条件連鎖)           │   │  │
│  │  └──────────────┘ └─────────────┘ └───────────────────────┘   │  │
│  │  ┌────────────────┐ ┌─────────────┐ ┌─────────────────────┐  │  │
│  │  │ProactiveScanner│ │ TaskRouter  │ │ Orchestrator        │  │  │
│  │  │                │ │(71本+動的)  │ │(1分/5分/30分サイクル)│  │  │
│  │  └────────────────┘ └─────────────┘ └─────────────────────┘  │  │
│  └────────────────────────┬───────────────────────────────────────┘  │
│                           │ タスクルーティング                        │
│                           ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Layer 1: 業種パイプライン（ゲノム駆動）                         │  │
│  │  建設8本 / 製造1本 / 医療福祉7本 / 不動産8本 / 物流8本          │  │
│  │  卸売6本 / 共通バックオフィス20本 / 営業11本                     │  │
│  │  + GenomeRegistryによる動的追加                                  │  │
│  └────────────────────────┬───────────────────────────────────────┘  │
│                           │ マイクロエージェント呼び出し              │
│                           ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Layer 2: マイクロエージェント（原子的処理・全20体）              │  │
│  │  入力5体 / 検証4体 / 計算3体 / 生成4体 / 外部連携4体            │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                           │                                         │
│                           ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  SaaSコネクタ（8種）                                            │  │
│  │  kintone / freee / Slack / CloudSign / gBizINFO                │  │
│  │  Google Sheets / Playwright(RPA) / Email(Gmail)                │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  横断: セキュリティ（RLS/暗号化/PII/同意管理/監査ログ）          │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. ゲノム駆動アーキテクチャ

### 何が「ゲノム」なのか

`brain/genome/data/*.json` に格納された業界テンプレートJSON。
各業界の業務定義・用語・ルール・パイプライン構成・バリデーション基準を
構造化データとして保持する。企業のDNAに相当する。

```
brain/genome/data/
├── construction.json    # 建設業の業務定義・用語・ルール
├── manufacturing.json   # 製造業
├── wholesale.json       # 卸売業
├── common.json          # 全業種共通バックオフィス
└── ...                  # 新業種 = JSONを1個追加するだけ
```

### ゲノムJSONの構造

```json
{
  "template_id": "construction_general_v1",
  "industry": "建設業",
  "version": "1.0.0",
  "dimensions": {
    "people":  { "default_roles": [...], "org_patterns": [...] },
    "process": { "common_flows": [...], "bottleneck_patterns": [...] },
    "cost":    { "cost_categories": [...], "margin_benchmarks": {...} },
    "tool":    { "common_saas": [...], "integration_patterns": [...] },
    "risk":    { "industry_risks": [...], "compliance_items": [...] }
  },
  "initial_knowledge": [...],
  "qa_seeds": [...],
  "pipelines": [...],
  "validation_rules": { "min": ..., "max": ..., "deviation": ... }
}
```

### 4つのコアコンポーネント

| コンポーネント | ファイル | 役割 |
|---|---|---|
| **GenomeRegistry** | `engine/genome_registry.py` | ゲノムJSONを動的に解析し、パイプラインレジストリを構築。静的レジストリ（ハードコード）をベースに、JSONから発見したパイプラインを追加マージ。衝突時は静的が勝つ |
| **AgentFactory** | `engine/agent_factory.py` | ゲノム + knowledge_items から BPOAgentRole（AI社員）を自動生成。部門ごとにグルーピングし、ロール合成・ツールバインディング・トリガー発見を行う |
| **BasePipeline** | `engine/base_pipeline.py` | 7ステップ共通テンプレート: OCR → 抽出 → 補完 → 計算 → 検証 → 異常検知 → 生成。全パイプラインがこのテンプレートを継承 |
| **TrustScorer** | `engine/approval_workflow.py` | 4段階信頼レベル（Level 0-3）の計算と承認ワークフロー制御 |

### 新業種追加の手順

1. `brain/genome/data/{industry}.json` を1ファイル追加
2. GenomeRegistryが自動検出 → パイプラインレジストリに動的登録
3. AgentFactoryがナレッジからAI社員ロールを自動生成
4. **コード変更不要**

---

## 4. マイクロエージェント一覧（全20体）

### 入力処理（5体）

| エージェント | ファイル | モデル | 用途 |
|---|---|---|---|
| document_ocr | `ocr.py` | FAST | 帳票・図面のOCR読み取り |
| structured_extractor | `extractor.py` | STANDARD | OCR結果から構造化データ抽出 |
| table_parser | `table_parser.py` | FAST | テーブル形式データの解析 |
| image_classifier | `image_classifier.py` | FAST (Gemini Vision) | 画像分類（工事写真等） |
| llm_summarizer | `llm_summarizer.py` | FAST | テキスト要約 |

### 検証（4体）

| エージェント | ファイル | モデル | 用途 |
|---|---|---|---|
| output_validator | `validator.py` | FAST | 出力データの妥当性検証 |
| compliance_checker | `compliance.py` | FAST | 法令・規制準拠チェック |
| diff_detector | `diff.py` | FAST | 変更差分の検出 |
| anomaly_detector | `anomaly_detector.py` | ルールベース | 過去データとの乖離・異常値検知 |

### 計算（3体）

| エージェント | ファイル | モデル | 用途 |
|---|---|---|---|
| cost_calculator | `calculator.py` | FAST | 原価・見積計算 |
| rule_matcher | `rule_matcher.py` | FAST | 業務ルール照合 |
| signal_detector | `signal_detector.py` | ルールベース | アップセル・解約シグナル検知 |

### 生成（4体）

| エージェント | ファイル | モデル | 用途 |
|---|---|---|---|
| document_generator | `generator.py` | STANDARD | ドキュメント生成 |
| pdf_generator | `pdf_generator.py` | WeasyPrint | PDF出力 |
| pptx_generator | `pptx_generator.py` | python-pptx | PowerPoint出力 |
| message_drafter | `message.py` | FAST | メッセージ・通知文面作成 |

### 外部連携（4体）

| エージェント | ファイル | モデル | 用途 |
|---|---|---|---|
| saas_reader | `saas_reader.py` | FAST | SaaSからのデータ読み取り |
| saas_writer | `saas_writer.py` | FAST | SaaSへのデータ書き込み |
| calendar_booker | `calendar_booker.py` | Google API | カレンダー予約管理 |
| company_researcher | `company_researcher.py` | FAST | 企業情報調査（gBizINFO等） |

---

## 5. BPO Manager（オーケストレーター）

Layer 0に位置し、全パイプラインの起動を制御する。

### 5つのコンポーネント

| コンポーネント | 責務 | 登録状況 |
|---|---|---|
| **ScheduleWatcher** | Cron式で定期タスクを評価。月末請求、月初レポート等 | 7スケジュール登録済み |
| **EventListener** | Webhook/SaaS変更を検知してタスク起動。freee入金通知、kintoneレコード更新等 | 9イベント登録済み |
| **ConditionEvaluator** | knowledge_relationsの連鎖条件を評価。「残業45h超→アラート」等 | 4条件連鎖登録済み |
| **ProactiveScanner** | 能動的にタスクを発見。未処理の請求書、期限切れ間近の契約等 | 常時スキャン |
| **TaskRouter** | 発見されたタスクを適切なパイプラインにルーティング | 71本登録済み + GenomeRegistry動的追加 |

### Orchestratorのバックグラウンドループ

```
1分サイクル:  EventListener（リアルタイムイベント処理）
5分サイクル:  ScheduleWatcher + ConditionEvaluator
30分サイクル: ProactiveScanner（能動タスク発見）
```

### Notifier

Slack/メール通知を統合管理。承認依頼・実行結果・異常アラートを配信。

---

## 6. 信頼レベルと承認ワークフロー

### 4段階の信頼レベル

| Level | 名称 | 権限 | 昇格条件 |
|---|---|---|---|
| **Level 0** | 通知のみ | READ。リマインダー・集計レポートを表示 | 初期状態 |
| **Level 1** | 下書き作成 | READ + WRITE:DRAFT。SaaSに下書きを作るが確定しない | 5回以上の実行実績 |
| **Level 2** | 自動実行+事後レビュー | READ + WRITE:ALL（承認後）。ユーザー承認で確定 | 20回以上 & 承認率80%以上 |
| **Level 3** | 完全自律 | READ + WRITE:ALL（自律）。事後レポートのみ | 50回以上 & 承認率90%以上 & 30回連続成功 + 社長明示承認 |

### TrustScorerの計算ロジック

```python
class TrustScorer:
    def calculate_score(self, agent) -> TrustScore:
        approval_rate = approved / total  # 承認率
        # 承認   → trust_score +1, 連続成功 +1
        # 修正   → trust_score 変更なし（学習機会）
        # 却下   → trust_score -2, 連続成功リセット

    def check_promotion(self, agent, score):
        # Level 0→1: total >= 5
        # Level 1→2: approval_rate >= 0.8 & total >= 20
        # Level 2→3: approval_rate >= 0.9 & consecutive >= 30 + CEO承認
```

### 降格・停止条件

- 承認率が基準を下回った場合 → 自動降格
- エラー率5%超 or テスト2件以上fail → 即座停止
- 停止時は前回正常スナップショットに切替（5分以内復帰）

---

## 7. Brain（ナレッジ・デジタルツイン）

Brain はシステムの「頭」。8モジュールで構成される。

| モジュール | ディレクトリ | 責務 |
|---|---|---|
| **extraction** | `brain/extraction/` | ドキュメント・音声からの構造化データ抽出 |
| **genome** | `brain/genome/` | 業界テンプレートJSON管理・ゲノム適用 |
| **knowledge** | `brain/knowledge/` | ナレッジ検索（pgvector + Voyage AI embedding） |
| **inference** | `brain/inference/` | 精度向上自律ループ（Gemini-first） |
| **ingestion** | `brain/ingestion/` | データ取り込み（音声/テキスト/OCR/対話） |
| **twin** | `brain/twin/` | デジタルツイン5次元モデル + What-if分析 |
| **proactive** | `brain/proactive/` | 能動提案エンジン（リスク検出・改善提案） |
| **visualization** | `brain/visualization/` | フロー/決定木/完成度マップの可視化 |

### デジタルツインの5次元（MVP）

| 次元 | 内容 | 例 |
|---|---|---|
| ヒト | 組織構造・スキルマップ | 「積算できるのは田中さんだけ」 |
| プロセス | 業務フロー・意思決定ルール | 「500万超は社長決裁」 |
| コスト | 原価構造・予算・マージン | 「粗利率25%が基準」 |
| ツール | 使用SaaS・データ連携 | 「freee + kintone利用中」 |
| リスク | 属人化・法令・人員不足 | 「経理の山田さん退職予定」 |

※ 6〜9次元（顧客/情報/文化/成長）はPhase 2+で追加。

---

## 8. SaaSコネクタ

全8種実装済み。`workers/connector/` に配置。

| コネクタ | 連携先 | 用途 |
|---|---|---|
| **kintone** | kintone | 顧客管理・案件管理のCRUD |
| **freee** | freee会計・請求書 | 仕訳・請求書・経費の読み書き |
| **slack** | Slack | チーム通知・承認依頼 |
| **cloudsign** | CloudSign | 電子署名・契約締結 |
| **gbizinfo** | gBizINFO | 法人情報の自動取得 |
| **google_sheets** | Google Sheets | スプレッドシート読み書き |
| **playwright_form** | Webフォーム | RPA的なフォーム自動送信 |
| **email** | Gmail API | メール送受信（2,000件/日） |

---

## 9. DB構成

- **コアテーブル**: 12テーブル（companies, users, knowledge_items, audit_logs 等）
- **BPOテーブル**: 9テーブル（bpo_tasks, execution_logs, tool_connections 等）
- **追加テーブル**: invitations
- **合計**: 22テーブル実装済み

### 鉄則

- **全テーブルにRLS（Row Level Security）必須**
- `company_id` ベースのテナント分離。例外なし
- Supabase PostgreSQL + pgvector（HNSW）

---

## 10. 技術スタック

| レイヤー | 技術 | 備考 |
|---|---|---|
| API | FastAPI (Python 3.11+) | async/await, OpenAPI自動生成 |
| LLM | Gemini 2.5 Flash (MVP) | Claude フォールバック。model_tier: fast/standard/premium |
| Embedding | Voyage AI (voyage-3) | 日本語業界用語に強い。768次元 |
| Vector DB | Supabase pgvector (HNSW) | RLS + Auth + Storage 統合 |
| DB | Supabase (PostgreSQL) | 全テーブルRLS必須 |
| Agent | LangGraph（Phase 2+） | Phase 1はasync/awaitで代替 |
| Frontend | Next.js + Tailwind + shadcn/ui | SSR, PWA対応 |
| OCR | Google Document AI | 日本語手書き・帳票対応 |
| STT | OpenAI Whisper | 日本語音声 |
| Infra | GCP Cloud Run | サーバーレス, Blue/Green |
| CI/CD | GitHub Actions | lint + type check + test + security scan |

---

## 11. 業種対応状況

### コア6業種 + 営業 + 共通

| 業種 | パイプライン数 | 実装済み | #1モジュール |
|---|---|---|---|
| 建設業 | 8本 | 5/7 + ゲノム | 積算AI（図面→数量→単価→内訳書） |
| 製造業 | 8本 | 1/8 | 見積AI（3層: 即時/標準/精密） |
| 医療・福祉 | 8本 | 1/8 | レセプト点検AI・介護報酬請求AI |
| 不動産業 | 8本 | 1/7 | 家賃管理・督促AI |
| 運輸・物流 | 8本 | 1/8 | 配車計画AI |
| 卸売業 | 6本 | 1/6 | 受発注AI（FAX→OCR→基幹転記） |
| 営業（全社自動化） | 11本 | **11/11 完了** | マーケ→SFA→CRM→CS→学習 |
| 共通バックオフィス | 7本 | 6/7 | 経費/給与/勤怠/契約/リマインド/ベンダー |

### 凍結10業種（パートナーが来たらゲノムJSON追加で復活）

歯科 / 飲食業 / 宿泊業 / 美容エステ / 自動車整備 / 調剤薬局 / EC小売 / 人材派遣 / 建築設計 / 士業

※ 凍結業種は各1本の#1パイプラインが実装済み。残置・新規開発停止。

### 自社 vs 顧客企業の提供範囲

| 機能 | 自社（シャチョツー本体） | 顧客企業（建設会社等） |
|---|---|---|
| Brain（ナレッジ・Q&A・ツイン） | 使う | **提供する製品** |
| 業種BPO（見積・請求等） | 使う | **提供する製品** |
| 共通バックオフィス | 使う | **提供する製品** |
| 営業パイプライン（0-9） | **自社専用** | 提供しない |
| CRM/SFA専用DB | **自社専用** | 提供しない |
| SaaSコネクタ | 使う | 顧客のSaaSに接続 |

営業パイプラインは自社の営業・顧客管理を自動化するためのもの。顧客には業種BPO + Brain + バックオフィスを提供する。

### 営業パイプラインのチェーン構造（自動発火）

```
マーケ(0) ──→ SFA(1) ──→ SFA(2) ──→ SFA(3) ──→ CRM(4) ──→ バックオフィス
 毎朝8:00     リード評価    提案書生成    見積・契約    顧客管理     請求書発行
 400件/日    (QUALIFIED→)  (sent→)     (signed→)   (onboarding)  (月次自動)
                                                        │
                                         ┌──────────────┤
                                         ▼              ▼
                                     CS(6) 自動応答  CRM(5) MRR集計
                                         │              │
                                         ▼              ▼
                                     Learning(9)    バックオフィス
                                     CS品質改善      請求書発行
                                         │
                               health≧80 ▼
                                     CS(7) アップセル → 人間が判断
```

**人間が介入するポイント:**
- リードスコア 40-69点 → 人間レビュー（QUALIFIED/NURTURINGの判断）
- 見積・契約（Pipeline 3） → 金額確定は承認必須（TrustLevel問わず）
- アップセル（Pipeline 7） → AIがブリーフ作成、提案は人間
- サポート確信度 50-85% → 人間レビューキュー

**完全自動:**
- マーケ・リード評価・提案書・オンボーディング・ヘルスチェック・MRR集計
- Learning（パターン学習・FAQ更新・スコア重み調整）
- CRM→バックオフィス（MRR集計完了→請求書発行を自動チェーン）

---

## 12. 料金体系

### 導入期（オンボーディング）
| プラン | 月額 | 期間 | 内容 |
|---|---|---|---|
| セルフ | 無料 | - | テンプレート自動適用 + メール + AIチャット |
| コンサル | 5万円/月 | 2ヶ月 | 週1 Meet + ナレッジ投入サポート |
| フルサポート | 30万円/月 | 3ヶ月 | 週1定例 + ナレッジ代行 + カスタムBPO + 専任Slack |

### 運用期（4ヶ月目〜）
| プラン | 月額 | 内容 |
|---|---|---|
| 共通BPO | 15万円/月 | バックオフィス全部 + ブレイン（Q&A・ナレッジ・ツイン）込み |
| 業種特化BPO | 30万円/月 | 共通BPO全部 + 業種固有パイプライン全部（まるっと） |
| 超過分 | 従量課金 | 基本枠（BPO 300回/月等）を超えた分 |
| 人間サポート追加 | +20万円/月 | AIだけでは対応しきれない業務 |

### 従量課金の基本枠（30万/月に含まれる）
- BPO実行: 300回/月（超過¥500/回）
- Q&A: 500回/月（超過¥100/回）
- ドキュメント生成: 100件/月（超過¥300/件）

---

## 13. 事業モデル

### フェーズ別展開

| Phase | 内容 | 判断基準 |
|---|---|---|
| **Phase 1** | 1社で全業界対応。建設・製造でPMF検証 | NPS>=30 / WAU>=60% / 「ないと困る」>=60% |
| **Phase 2** | 成功業界から分社化。業界パートナーに株を渡し経営委託 | 月商1,000万超 |
| **Phase 3** | パートナー主導で横展開。ゲノムJSON追加のみで新業種対応 | 子会社黒字化 |
| **Phase 3+** | 個社専用アプリ自動生成。企業ゲノム→DB+UI+WF自動構築 | データ蓄積1-2年 |

### Phase 3+: 個社専用アプリ自動生成
- 1-2年のデータ蓄積（knowledge_items + execution_logs + approval_workflow + digital_twin）から「企業ゲノム」を自動生成
- 企業ゲノム → DB設計 + UI + ワークフローを丸ごと自動構築
- シャチョツーが「BPO代行サービス」から「アプリ工場」に進化
- 個社アプリを月額課金で提供（プラットフォーム利用料30%モデルの延長線上）

### 収益構造

```
子会社売上の 30% = プラットフォーム利用料（本体の収益）
子会社売上の 70% = 営業・運用利益（子会社の収益）

本体が提供するもの:
  - 共通AIエンジン（BasePipeline + マイクロエージェント20体）
  - インフラ（Supabase + GCP Cloud Run）
  - AI進化（inference + feedback loop）

子会社が担うもの:
  - ゲノムJSON定義（業界ドメイン知識）
  - 業界営業・顧客獲得
  - オンボーディング・カスタマーサクセス
```

### 市場規模

- **TAM**: 約6,360億円/年（中小企業53万社）
- **SAM**: 約420億円/年（初期6業界のDX意欲層3.5万社）
- **SOM（3年目）**: 約18億円/年（1,500社）

### PMF検証タイムライン

```
Week 1-3:  Phase 0 基盤（DB + Auth + LLM + スケルトン）
Week 4-6:  Phase 1 ブレインMVP（Q&A + テンプレート）
Week 7:    パイロット3-5社投入
Week 8-9:  PMFゲート判定
```
