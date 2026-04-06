# b_11: AI OS 要件定義書 — Enterprise AI OSへの進化ロードマップ

> **目的**: 海外ベンチマーク（Palantir/Glean/Microsoft Copilot）との差を埋め、
> シャチョツーを「企業データを全統合する真のAI OS」に育てるための要件定義。
>
> **前提**: 現在の実装状況（2026-04-05時点）は c_03 実装計画を参照。
> Phase 0・1は完了済み。本書はPhase 1.5以降の設計正。
>
> **読み方**: 各フェーズは前のフェーズの完了を前提とする。
> 各要件には「なぜ必要か（Why）」「何を作るか（What）」「完了基準（DoD）」を記載。

---

## 0. 全体ロードマップ

```
現在地                Phase 1.5               Phase 2              Phase 3              Phase 4
(PMF検証中)           (信頼基盤)               (AI OS化)             (プラットフォーム化)    (Enterprise化)
[2026/04-06]         [2026/06-09]             [2026/09-2027/03]    [2027/03-2027/12]    [2028/01-]

パイロット投入 ──→ HitL + XAI         ──→ コネクタ拡張     ──→ Marketplace  ──→ IoT/Private LLM
                   コネクタ最小セット      (Tier 2-3 16本)    + スケール課金    + On-premise
                   (Tier 1 先行3本)        + Ontology          + SOC2取得       + B2B Economy
                   Land&Expand            + Agentic
                   価格レバー設計          Framework
```

> **⚠️ 重要**: コネクタは「最優先ギャップ1位」なので最小セットを Phase 1.5 に前倒し。
> パイロット企業が使っているSaaSに繋がらないと「全データハック」の価値提案がデモにすらならない。
> Tier 1全8本はPhase 2で完了。

---

## Phase 1.5 — 信頼基盤と拡張レバー（2026/06〜09）

> **目的**: パイロット企業を「確実に継続顧客」にしながら、ARPUを伸ばす仕組みを作る。
> PMFが取れる前に複雑機能を作っても無駄。最小コストで「辞められない」状態を作る。

---

### REQ-1501: Human-in-the-Loop（HitL）承認フロー

**Why**: AIが「勝手にやった」への不信が日本SMB経営者に強い。
パイロット段階でこれがないと、BPOパイプラインの自律実行が不信につながる。
Palantir/Gleanはこれを「信頼のUI」として最重要機能として扱っている。

**What**:

```
HitLの4段階を設計:
  Level 0: 全自動（バッチ処理・レポート生成など低リスク）
  Level 1: 通知のみ（Slack/メールで完了通知。人間確認は任意）
  Level 2: 確認後実行（「以下の操作を実行しますか？」→ 承認ボタン）
  Level 3: 必須承認（金額閾値超・外部送信・契約関連は必ず人間承認）
```

**実装内容**:

1. **承認キューUI** (`/bpo/approvals`)
   - ペンディング中のAIアクション一覧を表示
   - アクション種別・金額・影響範囲・根拠（なぜこのアクションか）を表示
   - 承認/却下/修正して承認 の3択
   - Slack通知連携（Slackから直接承認できる）

2. **リスク分類エンジン** (`workers/base/approval_classifier.py`)
   - 各BPOパイプラインのアクションにリスクレベルを自動付与
   - 金額閾値: デフォルト¥100,000超でLevel 3（会社ごとに設定可能）
   - 外部送信判定: メール送信・API呼び出し・ファイル出力は最低Level 2

3. **承認履歴テーブル** (`approval_actions`)
   ```sql
   id, company_id, pipeline_name, action_type, risk_level,
   payload_json, approver_user_id, approved_at, rejection_reason,
   created_at
   ```

**完了基準（DoD)**:
- [ ] 承認キューUIが動作する
- [ ] 建設BPO（積算AI・請求書生成）がLevel 2-3で止まる
- [ ] Slack通知から承認できる
- [ ] 承認/却下がaudit_logsに記録される

---

### REQ-1502: AI意思決定の説明可能性（XAI: Explainable AI）

**Why**: 「なぜこの見積金額になったか」を説明できないAIは企業に採用されない。
特に経営者に「根拠を見せろ」と言われた時に答えられないと即解約。
法的にも（医療・金融）説明義務が課される場面がある。

**What**:

```
各AIアクションに「推論トレース」を添付する:

例: 積算AI
  ├─ 根拠1: 過去類似案件「○○ビル新築 (2024-08)」の単価¥12,500/㎡ [類似度: 94%]
  ├─ 根拠2: 単価マスタ「コンクリート打設 ¥8,200/㎡」[出典: 自社実績 718件]
  ├─ 根拠3: 地域補正係数: 東京都 × 1.15
  └─ 算出式: (8,200 × 1.15 + 調整) × 面積 = ¥12,650,000
```

**実装内容**:

1. **推論トレースの構造体**
   ```python
   class ReasoningTrace(BaseModel):
       action_summary: str          # 「積算金額を¥12,650,000と算出しました」
       confidence_score: float      # 0.0-1.0
       evidence: list[Evidence]     # 根拠リスト
       data_sources: list[str]      # 参照したデータソース
       assumptions: list[str]       # 前提条件・不確かな点
       alternatives: list[str]      # 他の選択肢があった場合
   ```

2. **LLMプロンプト改修**: 全パイプラインのLLM呼び出しに「思考過程をJSONで返せ」を追加
   - `llm/client.py` に `with_trace=True` オプション追加

3. **推論トレースUI**: 各BPO結果画面に「根拠を見る」ボタン追加

**完了基準（DoD)**:
- [ ] 積算AI・請求書生成の全出力に推論トレースが添付される
- [ ] UIで「根拠を見る」が動作する
- [ ] `confidence_score < 0.7` の場合は黄色警告を表示

---

### REQ-1503: ARPUスケール設計（課金レバーの追加）

**Why**: 現在のフラット月額（¥150,000/¥300,000）はユーザー数・データ量・
実行量に関わらず同一。5人会社も300人会社も同じ料金は設計ミス。
ARPUを上げるバルブがなければ、顧客数を増やすしか売上を伸ばせない。

**What**:

```
3層の課金レバーを設計:

Layer 1: 基本料（席数 × 月額）
  - 管理者シート: ¥15,000/人/月
  - 閲覧シート: ¥5,000/人/月
  - 最低: ¥30,000/月（2管理者）

Layer 2: BPOパイプライン実行従量
  - 基本枠: 300回/月（基本料に含む）
  - 超過: ¥500/回（AIが処理した書類・見積・タスク1件）
  - 音声文字起こし: ¥50/分

Layer 3: Add-on モジュール
  - ゲノムカスタマイズ: +¥30,000/月
  - カスタムコネクタ: +¥20,000/月/本
  - 優先サポート（3営業日以内回答保証）: +¥50,000/月
```

**実装内容**:

1. **使用量計測テーブル** (`usage_metrics`)
   ```sql
   id, company_id, metric_type (pipeline_run/seat/connector),
   quantity, unit_price, period_month, created_at
   ```

2. **使用量ダッシュボード** (`/settings/billing`)
   - 今月の実行回数・残り枠・推定請求額をリアルタイム表示
   - 超過見込みになると事前アラート

3. **Stripeインテグレーション**: 従量課金のメーター送信自動化

**完了基準（DoD)**:
- [ ] pipeline実行時に `usage_metrics` にレコードが積まれる
- [ ] `/settings/billing` で今月の使用量が見える
- [ ] 超過時にStripeに従量メーターが送信される

---

### REQ-1504: Land and Expand プレイブック設計

**Why**: 「パートナー（士業）経由で初期導入」はできている。
しかし「1部署→全社展開」への具体的なトリガーと手順がなく、
ARPUが初期値で止まる。Gleanはこの展開プレイブックを製品機能として実装している。

**What**:

```
Expand の4段階トリガーを設計:

Stage 1 (初期): 1部署 × 1パイプライン（例: 積算部門 × 積算AI）
  → 30日後にTriggered Expansion Emailを自動送信

Stage 2 (拡張): 2-3部署 × 複数パイプライン
  → 「連携するとこう便利になります」の具体例をAIが自動生成

Stage 3 (全社): 全部門 × Brain統合（Q&Aに全社ナレッジ参照可）
  → Brainへのデータ提供を全部門に促すオンボーディングフロー

Stage 4 (深化): カスタムゲノム + 業界ベンチマーク
  → 「同業他社平均と比較すると...」提案
```

**実装内容**:

1. **Expansion Score** (`brain/proactive/expansion_scorer.py`)
   - 現在の利用パターンから「次に繋げるべき機能」をスコアリング
   - 利用率 < 40% の機能は「使い方ガイド」、> 80% は「次の機能提案」

2. **In-app Expansion提案UI**
   - ダッシュボードに「次のステップ」カードを表示
   - 「製造部門もつなぐと見積→原価→請求が自動化できます」等の具体的提案

3. **Customer Success自動化**
   - Day 7 / Day 30 / Day 90 のトリガーメール自動送信
   - 各メールにExpansion Scoreに基づいた個別提案を含める

**完了基準（DoD)**:
- [ ] Expansion Scoreが日次で計算される
- [ ] ダッシュボードに「次のステップ」が表示される
- [ ] Day 7/30/90 メールが自動送信される

---

### REQ-1505-PRE: トークン管理・自律学習フィードバックループ設計（2026-04追記）

**Why**: 自律学習フィードバックループと会話型ナレッジ収集は、長期運用でコンテキスト爆発・
フィードバック件数爆発・改善サイクル暴走の3リスクを抱える。PMF前に対処を設計する。

**What（3つの対処）**:

#### A. BPOテーマ別セッション + 自動compacting

```
ナレッジ収集セッションをゲノムのdepartment単位で分割する。
各テーマは独立したセッションを持ち、LLMには該当テーマのセッションのみ渡す。

自動compacting (COMPACT_THRESHOLD = 10問):
  question_count >= 10 → 生会話をLLMで要約 → compressed_context に置換
  生データは raw_context_archive に退避（監査・振り返り用）
  以降は compressed_context + 直近3ターンのみLLMに渡す

実装: brain/knowledge/session_manager.py
DB: knowledge_sessions.bpo_theme / question_count / compressed_context / raw_context_archive
UI: /knowledge/session?theme=xxx — テーマ選択 + 進捗バー表示
```

#### B. フィードバック種別の分離

```
👎フィードバックを2種類に明示的に分ける:

  feedback_type = 'prompt_improvement_only':
    → プロンプト改善専用（LLMの出し方を改善する）
    → 会社のナレッジルールには絶対に入れない
    → デフォルト動作。ユーザーは意識しない

  feedback_type = 'rule_candidate':
    → ユーザーが明示的にルール追加を選んだ場合のみ
    → 過去に同内容でprompt_improvement_onlyがあれば確認ダイアログ表示
    → 承認後: rule_add_confirmed=TRUE → knowledge_itemsに追加

実装: brain/inference/prompt_optimizer.py の analyze_rejections で
      feedback_type='rule_candidate' を除外してプロンプト改善対象を絞る
      フィードバック取得上限: 直近50件（ハードリミット）
```

#### C. 改善サイクルの参照範囲制御

```
improvement_cycle の execution_logs 参照範囲:
  - 直近30日ウィンドウに固定（古いデータで誤判定しない）
  - improvement_applied_at が 7日以内のステップはスキップ（二重改善防止）
  - スキップ理由を improvement_skip_reason に記録

効果: 「古いデータによる誤判定」「改善済みステップの再改善ループ」を防ぐ
```

**完了基準（DoD）**:
- [ ] knowledge_sessions に bpo_theme / question_count / compressed_context カラムが存在
- [ ] 10問到達で auto_compact が発動し compressed_context に要約が入る
- [ ] /knowledge でテーマ別進捗バーが表示される
- [ ] execution_logs に feedback_type カラムが存在
- [ ] analyze_rejections が直近50件・rule_candidate除外で動作
- [ ] improvement_cycle が30日ウィンドウ・改善済みスキップで動作

---

### REQ-1505: コネクタ最小セット（Tier 1 先行3本）

**Why**: コネクタ不足はギャップ分析の最優先課題1位。
しかしTier 1全8本をPhase 1.5で作ろうとすると工数過多でHitL/XAIと競合する。
パイロット企業の実態に合わせ、「今すぐ繋がっていないと価値提案が崩れる」3本だけを先行実装する。

残り5本（SmartHR・ジョブカン・backlog・Notion・Microsoft 365）はPhase 2（REQ-2001）で完了させる。

**What（先行3本の選定根拠）**:

```
① マネーフォワード ME for Business
  理由: 建設業・製造業パイロット企業の会計SaaSシェアNo.1。
       「売上・原価・利益が繋がる」デモが作れないと受注できない。
       freee（既存）との差分: 請求書・経費・法人カード データが取れる。

② Google Workspace（Gmail + Calendar + Drive）
  理由: ほぼ全パイロット企業が使用。
       「社長のメール・会議・書類が全部繋がる」がAI OSの最もわかりやすい訴求。
       GWS同期基盤は実装済み（project_gws_sync）—— 残りはBrain連携のみ。

③ 弥生会計
  理由: 中小製造業の会計SaaSシェアNo.1（マネーフォワードと並ぶ）。
       製造業BPO（原価管理・利益率改善）の根幹データが弥生に入っている。
       これなしに「製造業の原価を全部見る」は不可能。
```

**実装内容**:

1. **コネクタ基底クラス**（Phase 2 REQ-2001 の先行実装）
   ```python
   # workers/connector/base.py
   class BaseConnector(ABC):
       @abstractmethod
       async def authenticate(self) -> bool: ...
       @abstractmethod
       async def fetch_data(self, entity_type: str, since: datetime) -> list[dict]: ...
       async def health_check(self) -> ConnectorHealth: ...
   ```
   Phase 2 で残りのコネクタを追加する際にこのクラスを継承するだけでよい設計にする。

2. **各コネクタ実装**
   - `workers/connector/money_forward/client.py` — OAuth2 + 仕訳・請求書・経費API
   - `workers/connector/google_workspace/client.py` — Gmail Watch + Calendar Watch（既存GWS同期と統合）
   - `workers/connector/yayoi/client.py` — 弥生クラウド API（仕訳・売上・経費）

3. **コネクタ管理UI（最小版）** (`/settings/connectors`)
   - 接続済み/未接続/エラーの表示のみ（Phase 2でフル実装）
   - ワンクリックOAuth認可フロー

4. **Brain連携**: 取り込んだデータをknowledge_itemsに自動インデックス

**完了基準（DoD)**:
- [ ] マネーフォワードから請求書・仕訳データを取得できる
- [ ] Google Workspaceのメール・カレンダーがBrainに連携される
- [ ] 弥生会計から売上・原価データを取得できる
- [ ] `/settings/connectors` で接続ステータスが確認できる
- [ ] 追加コネクタを2日以内に実装できる基底クラスが整備されている

---

## Phase 2 — AI OS化（2026/09〜2027/03）

> **目的**: 「賢い検索ツール」から「企業の全データを統合するAI OS」へ昇格させる。
> コネクタ・オントロジー・自律実行の3点が揃って初めてAI OSを名乗れる。

---

### REQ-2001: コネクタ拡張（Tier 1 残り5本 + Tier 2-3）

> **前提**: REQ-1505（Phase 1.5）でTier 1のうちマネーフォワード・Google Workspace・弥生の3本は実装済み。
> 本REQはその続き。基底クラスが整備されているため1本あたりの追加コストは大幅に低い。

**Why**: 「全データをハック」の前提が今は崩れている。
REQ-1505の3本だけでは会計・会議のみ。HR・タスク・CRM・業種特化データがまだ取れない。
コネクタ数がAI OSの「データ深度」を直接決める。

**What**:

```
Tier 1 残り5本（Phase 2前半）:
  ├─ マネーフォワード ME for Business（会計・給与）
  ├─ 弥生会計（中小製造業シェアNo.1）
  ├─ SmartHR（HR・労務。社労士パートナー接続に必須）
  ├─ ジョブカン（勤怠・給与）
  ├─ Google Workspace（Gmail・Calendar・Drive 双方向）
  ├─ Microsoft 365（Teams・Outlook・SharePoint）
  ├─ Notion（プロジェクト管理）
  └─ backlog（タスク管理。IT企業・製造業のエンジニア部門）

Tier 2（2027Q1）: 業種特化 8本
  ├─ 建設キャリアアップシステム CCUS（建設業必須）
  ├─ Buildee（建設業積算）
  ├─ 勘定奉行（製造業会計）
  ├─ GLOVIA（製造業ERP）
  ├─ Sansan（名刺・顧客管理）
  ├─ Salesforce（大手向け）
  ├─ HubSpot（SMB CRM）
  └─ LINE WORKS（小売・介護・飲食）

Tier 3（2027Q2〜）: 拡張 8本
  ├─ ZOHO CRM
  ├─ Chatwork
  ├─ Garoon（サイボウズ）
  ├─ Freee HR（人事）
  ├─ マネーフォワードクラウド給与
  ├─ Amazon出品者Central（卸売・EC）
  ├─ 楽天RMS（卸売）
  └─ カスタムCSV/Excel インポート（汎用）
```

**実装内容**:

1. **コネクタ基底クラス** (`workers/connector/base.py`)
   ```python
   class BaseConnector(ABC):
       @abstractmethod
       async def authenticate(self) -> bool: ...
       @abstractmethod
       async def fetch_data(self, entity_type: str, since: datetime) -> list[dict]: ...
       @abstractmethod
       async def push_data(self, entity_type: str, payload: dict) -> dict: ...
       async def health_check(self) -> ConnectorHealth: ...
   ```

2. **コネクタごとのファイル**: `workers/connector/{name}/client.py`
   - OAuth2フロー（認可コード・リフレッシュトークン自動更新）
   - レート制限対応（429ハンドリング・指数バックオフ）
   - データ正規化（各SaaSの独自フォーマット → シャチョツー内部スキーマへ変換）

3. **同期スケジューラー** (`workers/connector/sync_scheduler.py`)
   - 各コネクタの差分同期（フル同期は初回のみ）
   - 同期ログ: `connector_sync_logs` テーブルに記録
   - エラー時: Slack通知 + 管理者ダッシュボードに警告表示

4. **コネクタ管理UI** (`/settings/connectors`)
   - 接続済み・未接続・エラー状態の一覧
   - OAuth認可フロー（ワンクリックで接続）
   - 最終同期時刻・同期済みレコード数の表示

**完了基準（DoD)**:
- [ ] Tier 1の8本が動作する
- [ ] 各コネクタが差分同期できる（フル再同期不要）
- [ ] エラー時にSlack通知が飛ぶ
- [ ] `/settings/connectors` でステータス確認できる
- [ ] コネクタ追加時のテンプレートが存在する（新規追加 < 2日で実装可能）

---

### REQ-2002: オントロジー（Knowledge Graph）

**Why**: pgvector（ベクトル検索）は「似た文書を探す」だけ。
「A社の○○案件の仕入先Bの担当者Cが持つ契約Dに関連する過去トラブルE」
という構造的・多ホップの推論ができない。
Palantirの最大差別化はここにあり、RAGとAI OSの本質的な違いもここにある。

**What**:

```
エンティティと関係の定義:

エンティティ種別:
  Company（企業）、Person（人）、Project（案件）、
  Contract（契約）、Product（製品/資材）、
  Transaction（取引）、Document（書類）、Task（タスク）

関係種別:
  BELONGS_TO（所属）、OWNS（所有）、RELATED_TO（関連）、
  SUPPLIED_BY（仕入先）、EXECUTED_BY（担当）、
  DERIVED_FROM（根拠元）、DEPENDS_ON（依存）

例（建設業）:
  Project[東京タワー改修] ──RELATED_TO──→ Contract[2024-建-001]
  Contract[2024-建-001] ──SUPPLIED_BY──→ Company[鉄筋商事株式会社]
  Company[鉄筋商事株式会社] ──OWNS──→ Person[田中一郎]
  Person[田中一郎] ──RELATED_TO──→ Document[過去トラブル報告書]
```

**実装方針**:

Phase 2では「軽量Knowledge Graph」として実装。
Apache AGE（PostgreSQL拡張）は依存を増やすため、まずは関係テーブルで代替。
Phase 3でグラフDBへの移行を判断する。

**実装内容**:

1. **エンティティテーブル** (`kg_entities`)
   ```sql
   id uuid, company_id uuid, entity_type text,
   entity_key text,        -- 外部システムの一意キー
   display_name text,
   properties jsonb,       -- エンティティ固有の属性
   embedding vector(768),  -- ベクトル検索用
   source_connector text,  -- どのコネクタから来たか
   created_at timestamptz
   ```

2. **関係テーブル** (`kg_relations`)
   ```sql
   id uuid, company_id uuid,
   from_entity_id uuid, relation_type text, to_entity_id uuid,
   properties jsonb,       -- 関係の属性（強度・日付等）
   confidence_score float, -- 自動推論の場合の確信度
   source text,            -- manual / auto_extracted / connector
   created_at timestamptz
   ```

3. **自動エンティティ抽出** (`brain/knowledge/entity_extractor.py`)
   - コネクタから取り込んだデータを自動解析
   - LLMで人名・企業名・案件名をNER（固有表現抽出）
   - 既存エンティティとの重複チェック（名寄せ）
   - 関係を自動推論（「この請求書はこの案件に紐づく」等）

4. **グラフ検索API** (`routers/knowledge_graph.py`)
   - `GET /kg/entities/{id}/related` — N ホップ先の関連エンティティを返す
   - `GET /kg/search` — 自然言語でグラフを検索（LLM + グラフ traversal）
   - Q&A・BPOパイプライン両方からこのAPIを呼ぶ

**完了基準（DoD)**:
- [ ] コネクタからのデータ取り込み時に自動エンティティ抽出が動く
- [ ] 企業・人・案件・契約の関係が `kg_relations` に記録される
- [ ] Q&Aで「○○案件の仕入先の連絡先は？」が答えられる
- [ ] `/kg/entities/{id}/related` が動作する

---

### REQ-2003: Agentic Framework（自律マルチステップ実行）

**Why**: 現在のBPOパイプラインは「1ステップ自動化」。
「見積が承認されたら → 発注書を自動生成し → 仕入先にメール送信し → kintoneに記録する」
という複数ステップの自律実行ができない。
これがないと「使うと楽になる」で止まり「使わないと業務が回らない」にならない。

**What**:

```
3タイプのエージェント実行を設計:

Type 1: Sequential Agent（直列実行）
  タスクA → タスクB → タスクC
  例: 見積確定 → 請求書生成 → 経理システム登録

Type 2: Parallel Agent（並列実行）
  タスクA ─┬─ タスクB ─┬─ タスクD
            └─ タスクC ─┘
  例: 入金確認 + 工程確認 → 出来高報告書生成

Type 3: Reactive Agent（イベント駆動）
  トリガー（入金確認） → エージェント起動 → マルチステップ実行
  例: 入金検知 → 売掛金消込 → 収益確定 → 月次レポート更新
```

**実装内容**:

Phase 2ではLangGraphを導入（Phase 1で先送りしていたもの）。

1. **LangGraph基盤** (`workers/base/agent_executor.py`)
   ```python
   # ステートマシンとしてのエージェント定義
   class AgentState(TypedDict):
       task_id: str
       company_id: str
       context: dict
       steps_completed: list[str]
       human_approval_pending: bool
       final_output: dict | None
   ```

2. **ツールレジストリ** (`workers/base/tool_registry.py`)
   - 各BPOパイプラインをツールとして登録
   - コネクタ操作（kintone書き込み等）をツールとして登録
   - HitLツール（承認待ち状態でサスペンド）を追加

3. **既存パイプラインの移行**
   - 建設BPO・製造BPOパイプラインをLangGraphのノードとして再定義
   - 後方互換: 既存のasync関数はそのままノードとして使える

4. **実行ログ** (`agent_execution_logs`)
   ```sql
   id, company_id, agent_name, run_id,
   state_history jsonb,   -- 各ステップの入出力
   current_step text, status text,
   started_at, completed_at, error_message
   ```

**完了基準（DoD)**:
- [ ] LangGraph上で建設BPOが動作する（既存機能の回帰なし）
- [ ] 承認待ち（HitL Level 2-3）でエージェントがサスペンドできる
- [ ] `/bpo/runs/{id}` でリアルタイム実行状況が見える
- [ ] 失敗ステップからのリトライができる

---

### REQ-2004: ネットワーク効果（横断匿名ベンチマーク）

**Why**: Gleanは「業界ベンチマーク」（匿名化された他社データとの比較）を
差別化として使っている。「同業他社と比べてあなたの会社は...」は強力な提案ネタ。
30社以上になったら発動できる（現設計 c_02 L3に記載あり）。

**What**:

```
ベンチマーク提供の4種類:

1. 業界平均値（例: 建設業の平均粗利率 18.2%）
2. 業務処理時間比較（例: 見積作成 自社3日 / 業界平均1.2日）
3. 典型的なリスクパターン（例: 「外壁工事の未収金は業界で多い」）
4. 改善成功事例（例: 「類似規模の建設会社が実施した原価改善施策」）
```

**実装内容**:

1. **匿名化集計パイプライン** (`brain/analytics/benchmark_aggregator.py`)
   - 個社データを集計・匿名化（company_idを除去、k-匿名化で個社特定を防止）
   - 業界×規模×地域の3軸で分類
   - 最低5社以上のデータがないセグメントは非公開

2. **ベンチマーク提案UI**
   - ダッシュボードに「業界比較カード」を追加
   - 「あなたの粗利率: 16.2% / 業界平均: 18.2%（-2.0%）」を可視化
   - 「改善のためのアクション」ボタンでBPOパイプラインに誘導

**完了基準（DoD)**:
- [ ] 30社到達後に業界別集計が動作する
- [ ] k-匿名化（最低5社）が担保される
- [ ] ダッシュボードに業界比較カードが表示される

---

## Phase 3 — プラットフォーム化（2027/03〜12）

> **目的**: 「自分たちが全部作る」から「パートナーが拡張できる基盤」に変わる。
> Salesforceがパートナーエコシステムで急成長したモデルを踏む。

---

### REQ-3001: Partner Marketplace（アプリエコシステム）

**Why**: 士業パートナーが「シャチョツーの上でアプリを作って売れる」仕組みがなければ
「Salesforce型プラットフォーム」は名乗れない。
Palantir AIPアプリ・Glean Appsが既にこれを実装している。

**What**:

```
Marketplace の2層:

Layer 1: パートナーアプリ
  - 士業パートナーが顧問先向けにカスタムBPOフローを作成
  - 例: 社労士が「社会保険算定基礎届の自動作成」アプリを作成・販売
  - 収益分配: 月額販売価格の30%をシャチョツーに、70%をパートナーに

Layer 2: カスタムゲノムテンプレート
  - 業界特化のゲノムJSON（業務フロー・Q&Aテンプレ・単価マスタ）を販売
  - 例: 「介護施設向けゲノムパック」¥100,000/テナント（パートナーが作成・販売）
```

**実装内容**:

1. **App Builder** (`/partner/builder`)
   - ノーコードでBPOパイプラインを組み合わせるUI
   - コネクタ・マイクロエージェントをブロックとして繋ぐ
   - プレビュー実行・テストデータでの動作確認

2. **Marketplace UI** (`/marketplace`)
   - アプリ一覧・カテゴリ・レビュー・価格
   - ワンクリックインストール（会社ごとの設定画面自動生成）

3. **収益分配エンジン** (`workers/billing/revenue_share.py`)
   - パートナーの売上を自動集計
   - 月次でStripeに支払いを実行

4. **パートナーポータル** (`/partner`)
   - アプリの利用状況・収益・レビューを可視化
   - アプリのバージョン管理・公開/非公開切替

**完了基準（DoD)**:
- [ ] 社労士パートナーが「社会保険書類自動生成」アプリを作れる
- [ ] Marketplaceでアプリをインストールできる
- [ ] 収益分配が月次で自動計算される

---

### REQ-3002: セキュリティ第三者認証取得

**Why**: 医療・製造大手へのセールス時に「最初のセキュリティ審査で弾かれる」を防ぐ。
SOC2取得なしでは大手病院・上場企業への営業が不可能。

**What**:

```
取得ロードマップ:

2027/Q1: ISMSクラウドセキュリティ（ISO27017）準備開始
2027/Q2: SOC2 Type I 審査開始（準備期間2-3ヶ月）
2027/Q3: SOC2 Type I 取得
2027/Q4: SOC2 Type II 審査開始（6ヶ月の観察期間）
2028/Q2: SOC2 Type II 取得
```

**実装内容（事前整備が必要なコード側の作業）**:

1. **監査ログの完全化** (`audit_logs`)
   - 全APIアクション（Read含む）の記録
   - ログの改ざん不可設計（追記専用テーブル + CloudWatch連携）

2. **アクセス制御強化**
   - 最小権限原則の徹底（社員が見られるのは自社データのみ、例外なし）
   - 非アクティブアカウントの自動無効化（90日）
   - MFA強制（管理者ロール）

3. **インシデント対応手順書**
   - セキュリティインシデント発生時の通知→対応→報告フローを文書化

**完了基準（DoD)**:
- [ ] 全APIリクエストがaudit_logsに記録される
- [ ] MFA強制が管理者ロールで動作する
- [ ] SOC2 Type I 審査申請完了

---

### REQ-3003: スケール課金モデルの完全実装

**Why**: Phase 1.5で設計したARPUレバーをプロダクトに完全統合する。
ユーザー数増加・データ量増加・エージェント実行増加が
全て収益に直結する設計にする。

**What**:

```
完全な価格モデル:

Free（トライアル）:
  - 2管理者まで / ¥0 / 14日間
  - BPO実行: 10回まで
  - コネクタ: 1本まで

Standard（¥30,000〜/月）:
  - 管理者: ¥15,000/人/月 × 2人込み
  - 追加: ¥10,000/人/月
  - BPO実行: 100回/月込み / 超過¥500/回
  - コネクタ: 2本

Pro（¥80,000〜/月）:
  - 管理者: ¥15,000/人/月 × 4人込み
  - BPO実行: 500回/月込み / 超過¥300/回
  - コネクタ: 8本
  - ネットワークベンチマーク: 含む

Enterprise（カスタム）:
  - 人数無制限
  - BPO実行: 無制限（固定月額 ¥300,000〜）
  - コネクタ: 無制限
  - SSO/SAML: 含む
  - SLAボリューム保証
  - 専任CSM
```

**完了基準（DoD)**:
- [ ] Stripeで全プランの自動課金が動作する
- [ ] プラン制限に達したらUIでアップグレード促進が出る
- [ ] Enterpriseカスタム見積フローが整備される

---

## Phase 4 — Enterprise化（2028/01〜）

> **目的**: 大手企業・医療・金融へのセールスを可能にする。
> この段階で「SMB向けAI BPO」から「Enterprise AI OS」に完全移行する。

---

### REQ-4001: リアルタイムデータ処理（IoT / ストリーミング）

**Why**: 製造業の原価最適化・建設業の工程管理を「本当の意味で」自動化するには
機械センサーのリアルタイムデータが必要。
現在は人間がデータを手入力しているため、BPOの効果が限定的。

**What**:

```
接続するデータストリーム（Phase 4）:

製造業:
  - 機械センサー（稼働率/温度/振動）→ OEE（設備総合効率）自動算出
  - QC検査カメラ → 不良率リアルタイム検知 → 工程停止アラート

建設業:
  - 現場カメラ → 作業員の危険行動検知（ヘルメット未着用等）
  - 重機GPS → 稼働率・位置記録 → 日報自動生成
```

**実装内容**:
- Apache Kafka / Cloud Pub Sub でのストリーミング基盤
- TimescaleDB（時系列データ）の追加検討
- エッジ処理（現場でのローカル推論）の設計

---

### REQ-4002: プライベートLLM / Fine-tuning

**Why**: 大手企業・医療・金融は「外部LLMにデータを送れない」という制約がある。
Gemini/ClaudeへのAPI依存はこの層に入れない。
また、顧客固有データでfine-tuningした専用モデルは精度が大幅に向上する。

**What**:

```
3段階のLLM戦略:

Level 1（現在）: Gemini/Claude API（外部SaaS）
  → SMBはこれで十分。コスト最小。

Level 2（Phase 4）: Private Deployment
  → Vertex AI上に顧客専用の推論エンドポイントをデプロイ
  → GCP VPC内完結（データが外部に出ない）

Level 3（Phase 4+）: Fine-tuned Model
  → 顧客の承認済みデータでGeminiをfine-tuning
  → 業界用語・自社固有ルールを学習済みのモデルを専有
```

**完了基準（DoD）**:
- [ ] `llm/client.py` が `deployment_mode: saas | private | fine-tuned` を切替可能
- [ ] Private Deploymentで建設BPOが動作する

---

### REQ-4003: マルチデプロイオプション

**Why**: 大手エンタープライズはオンプレまたはプライベートクラウドを要求する。
Cloud Run（SaaS）のみでは大手病院・製造大手に入れない。

**What**:

```
デプロイオプション:

Option A: SaaS（現在）
  Cloud Run + Supabase（マルチテナント）

Option B: Private Cloud
  顧客のGCP/AWS/Azure上にシャチョツーをデプロイ
  Supabase → PostgreSQL（顧客管理）に切替

Option C: On-premise（要件が出た時点で設計）
  Kubernetes（EKS/GKE）+ PostgreSQL（自社管理）
```

**実装内容**:
- `docker-compose.yml` でのローカル完結動作の保証
- 環境変数だけで `DB_HOST / AUTH_MODE / LLM_ENDPOINT` を切替可能な設計
- Helm Chart（Kubernetes用）の整備

---

## 非機能要件（全Phase共通）

```
可用性: SLA 99.5%（Standard）/ 99.9%（Enterprise）
レスポンス: API p95 < 500ms（BPO非同期処理を除く）
スケーラビリティ: テナント1,000社まで垂直スケールで対応可能
データ保持: audit_logs 7年 / 業務データ 10年（法定要件）
バックアップ: 日次 + Point-in-Time Recovery（Supabase機能）
暗号化: 転送中（TLS 1.3）/ 保存時（AES-256。Supabaseネイティブ）
PII: マイナンバー・個人情報はregex検出 + 暗号化カラムに分離
```

---

## 開発リソース見積もり（参考）

| Phase | 主要タスク | 工数（人月・1人開発+Claude並列） |
|---|---|---|
| 1.5 | HitL + XAI + ARPUレバー + L&E | 2.5ヶ月 |
| 2 | コネクタ8本 + Ontology + LangGraph | 6ヶ月 |
| 3 | Marketplace + SOC2 + 課金完全実装 | 6ヶ月 |
| 4 | IoT + Private LLM + On-premise | 6ヶ月以上（Enterprise要件次第） |

---

## 実装優先順位まとめ

```
★★★ 即着手（PMF獲得の直接条件）:
  REQ-1501: Human-in-the-Loop
  REQ-1502: XAI（推論説明）
  REQ-1503: ARPUスケール設計
  REQ-1505: コネクタ最小セット（マネーフォワード・GWS・弥生 先行3本）← ★追加

★★☆ 3ヶ月以内（AI OS化の必要条件）:
  REQ-1504: Land and Expand プレイブック
  REQ-2001: コネクタ拡張（Tier 1 残り5本 + Tier 2）
  REQ-2002: オントロジー

★☆☆ 6ヶ月以内（プラットフォーム化に向けた基盤）:
  REQ-2003: Agentic Framework（LangGraph）
  REQ-2004: ネットワーク効果
  REQ-3002: セキュリティ第三者認証（準備開始）

☆☆☆ 1年以上（Enterprise拡張）:
  REQ-3001: Partner Marketplace
  REQ-3003: 完全課金モデル
  REQ-4001〜4003: IoT/Private LLM/On-premise
```

---

*最終更新: 2026-04-05 | 作成: ベンチマークギャップ分析（b_11前文）に基づく*
