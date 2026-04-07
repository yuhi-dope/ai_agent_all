# シャチョツー AIエージェントレジストリ

> **このファイルは `/review-agents` スキルが自動更新します。**
> 手動編集する場合は `/add-pipeline` または各エージェントのMDファイルも合わせて更新してください。
> 最終更新: 2026-03-29（マイクロ20体・エンジンコア3件追加・anomaly_detector全パイプライン統合）

---

## アーキテクチャ概観

```
Layer 0: BPO Manager（オーケストレーター）
    ↓ タスク発見・ルーティング
Layer 1: 業種Domainエージェント（パイプライン）
    ↓ マイクロエージェント呼び出し
Layer 2: 共通マイクロエージェント（原子的処理）
```

---

## Layer 0: BPO Manager（`workers/bpo/manager/`）

| コンポーネント | ファイル | 状態 | 責務 |
|---|---|---|---|
| ScheduleWatcher | `schedule_watcher.py` | ✅ 実装済み | Cron条件評価（7スケジュール登録済み） |
| EventListener | `event_listener.py` | ✅ 実装済み | Webhook/SaaS変更検知（9イベント登録済み） |
| ConditionEvaluator | `condition_evaluator.py` | ✅ 実装済み | knowledge_relations連鎖評価（4条件連鎖登録済み） |
| ProactiveScanner | `proactive_scanner.py` | ✅ 実装済み | 能動タスクスキャン |
| TaskRouter | `task_router.py` | ✅ 実装済み | パイプラインルーティング（業種29+sales12=41本） |
| Orchestrator | `orchestrator.py` | ✅ 実装済み | バックグラウンドループ（1分/5分/30分サイクル） |
| Notifier | `notifier.py` | ✅ 実装済み | Slack/メール通知 |

### エンジンコア（`workers/bpo/engine/`）

| コンポーネント | ファイル | 状態 | 責務 |
|---|---|---|---|
| BasePipeline | `base_pipeline.py` | ✅ 実装済み | 7ステップ共通パイプラインテンプレート（OCR→抽出→補完→計算→検証→異常検知→生成） |
| GenomeRegistry | `genome_registry.py` | ✅ 実装済み | brain/genome/data/*.json → 動的パイプラインレジストリ生成。task_routerと統合済み |
| AgentFactory | `agent_factory.py` | ✅ 実装済み | ゲノム + knowledge_items → BPOAgentRole自動生成（業種別AIロール定義） |
| TrustScorer | `approval_workflow.py` | ✅ 実装済み | 4段階信頼レベル（Level 0-3）+ 承認ワークフロー |

---

## Layer 2: 共通マイクロエージェント（`workers/micro/`）

| エージェント | ファイル | 状態 | モデル | 優先度 |
|---|---|---|---|---|
| document_ocr | `ocr.py` | ✅ 実装済み | FAST | **P0** |
| structured_extractor | `extractor.py` | ✅ 実装済み | STANDARD | **P0** |
| rule_matcher | `rule_matcher.py` | ✅ 実装済み | FAST | **P0** |
| output_validator | `validator.py` | ✅ 実装済み | FAST | **P0** |
| cost_calculator | `calculator.py` | ✅ 実装済み | FAST | P1 |
| document_generator | `generator.py` | ✅ 実装済み | STANDARD | P1 |
| compliance_checker | `compliance.py` | ✅ 実装済み | FAST | P1 |
| table_parser | `table_parser.py` | ✅ 実装済み | FAST | P1 |
| diff_detector | `diff.py` | ✅ 実装済み | FAST | P2 |
| saas_reader | `saas_reader.py` | ✅ 実装済み | FAST | P2 |
| saas_writer | `saas_writer.py` | ✅ 実装済み | FAST | P2 |
| message_drafter | `message.py` | ✅ 実装済み | FAST | P2 |
| company_researcher | `company_researcher.py` | ✅ 実装済み | FAST | **P0** |
| signal_detector | `signal_detector.py` | ✅ 実装済み | — (ルールベース) | **P0** |
| pdf_generator | `pdf_generator.py` | ✅ 実装済み | — (WeasyPrint) | **P0** |
| calendar_booker | `calendar_booker.py` | ✅ 実装済み | — (Google API) | P1 |
| pptx_generator | `pptx_generator.py` | ✅ 実装済み | — (python-pptx) | P1 |
| anomaly_detector | `anomaly_detector.py` | ✅ 実装済み | — (ルールベース・LLM不使用) | **P0** |
| image_classifier | `image_classifier.py` | ✅ 実装済み | FAST (Gemini Vision) | P1 |
| llm_summarizer | `llm_summarizer.py` | ✅ 実装済み | FAST | P1 |

**全20体 実装完了 ✅**

### anomaly_detector 接続済みパイプライン
- 営業: proposal_generation / quotation_contract / revenue_request / lead_qualification
- 共通: expense_pipeline
- 建設: estimation_pipeline / billing_pipeline

---

## Layer 1: 業種Domainパイプライン

### 建設業（`workers/bpo/construction/pipelines/`）

| パイプライン | ファイル | 状態 | ステップ数 | 優先度 |
|---|---|---|---|---|
| 見積 | `estimation_pipeline.py` | ✅ パイプライン実装済み（8ステップ） | 8 | **P0** |
| 請求 | `billing_pipeline.py` | ✅ 実装済み（5ステップ） | 5 | **P1** |
| 安全書類 | `safety_docs_pipeline.py` | ✅ 実装済み（6ステップ） | 6 | **P1** |
| 原価報告 | `cost_report_pipeline.py` | ✅ 実装済み（4ステップ） | 4 | P2 |
| 写真整理 | `photo_organize_pipeline.py` | ✅ 実装済み（5ステップ） | 5 | P2 |
| 下請管理 | `subcontractor_pipeline.py` | ❌ 未実装 | 6 | P2 |
| 許認可申請 | `permit_pipeline.py` | ❌ 未実装 | 7 | P2 |

### 製造業（`workers/bpo/manufacturing/pipelines/`）— #1: 見積AI

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **見積** | `quoting_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 生産計画〜ISO書類（7本） | — | ❌ 未実装 | 拡張 |

### 歯科（`workers/bpo/dental/pipelines/`）— #1: レセプト点検

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **レセプト点検** | `receipt_check_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 予約〜届出（7本） | — | ❌ 未実装 | 拡張 |

### 不動産管理（`workers/bpo/realestate/pipelines/`）— #1: 家賃管理・督促

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **家賃管理・督促** | `rent_collection_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 入居者対応〜届出（6本） | — | ❌ 未実装 | 拡張 |

### 士業事務所（`workers/bpo/professional/pipelines/`）— #1: 期限管理

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **期限管理** | `deadline_mgmt_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| CRM〜研修（6本） | — | ❌ 未実装 | 拡張 |

### 介護・福祉（`workers/bpo/nursing/pipelines/`）— #1: 介護報酬請求AI【A群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **介護報酬請求** | `care_billing_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| ケアプラン〜事故報告（7本） | — | ❌ 未実装 | 拡張 |

### 物流・運送（`workers/bpo/logistics/pipelines/`）— #1: 配車計画AI【A群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **配車計画** | `dispatch_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 運行管理〜届出（7本） | — | ❌ 未実装 | 拡張 |

### 飲食業（`workers/bpo/restaurant/pipelines/`）— #1: 原価管理【A群】67万事業所

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **原価管理（FLコスト）** | `fl_cost_pipeline.py` | ✅ 実装済み（6ステップ） | **#1 ✅** |
| シフト〜メニュー分析（6本） | — | ❌ 未実装 | 拡張（e_06参照） |

### 医療クリニック（`workers/bpo/clinic/pipelines/`）— #1: レセプト点検AI【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **レセプト点検** | `medical_receipt_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 予約〜届出（7本） | — | ❌ 未実装 | 拡張 |

### 調剤薬局（`workers/bpo/pharmacy/pipelines/`）— #1: 調剤報酬請求AI【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **調剤報酬請求** | `dispensing_billing_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 在庫〜届出（5本） | — | ❌ 未実装 | 拡張 |

### 美容・エステ（`workers/bpo/beauty/pipelines/`）— #1: 予約・リコール【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **予約管理・リコール** | `recall_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| スタッフ管理〜採用（5本） | — | ❌ 未実装 | 拡張 |

### 自動車整備（`workers/bpo/auto_repair/pipelines/`）— #1: 見積・請求AI【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **見積・請求** | `repair_quoting_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 車検予約〜採用（6本） | — | ❌ 未実装 | 拡張 |

### ホテル・旅館（`workers/bpo/hotel/pipelines/`）— #1: レベニューマネジメント【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **レベニューマネジメント** | `revenue_mgmt_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| OTA管理〜インバウンド（6本） | — | ❌ 未実装 | 拡張 |

### EC・小売（`workers/bpo/ecommerce/pipelines/`）— #1: 商品登録・掲載文AI【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **商品登録・掲載文** | `listing_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 在庫〜売上分析（5本） | — | ❌ 未実装 | 拡張 |

### 人材派遣（`workers/bpo/staffing/pipelines/`）— #1: 契約管理・抵触日【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **契約管理・抵触日** | `dispatch_contract_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| マッチング〜教育管理（6本） | — | ❌ 未実装 | 拡張 |

### 建築設計（`workers/bpo/architecture/pipelines/`）— #1: 確認申請書類【B群】

| パイプライン | ファイル | 状態 | 優先度 |
|---|---|---|---|
| **確認申請書類** | `building_permit_pipeline.py` | ✅ 実装済み | **#1 ✅** |
| 監理記録〜経理（5本） | — | ❌ 未実装 | 拡張 |

### 全社自動化AI社員（`workers/bpo/sales/`）— ★2026-03-22 構築

#### マーケ担当（`sales/marketing/`）

| パイプライン | ファイル | 状態 | ステップ数 |
|---|---|---|---|
| アウトリーチ400件/日 | `outreach_pipeline.py` | ✅ 実装済み | 8 |

#### 営業担当（`sales/sfa/`）

| パイプライン | ファイル | 状態 | ステップ数 |
|---|---|---|---|
| リードクオリフィケーション | `lead_qualification_pipeline.py` | ✅ 実装済み | 6 |
| 提案書AI生成(PDF+PPTX) | `proposal_generation_pipeline.py` | ✅ 実装済み | 8 |
| 見積・契約・署名・請求 | `quotation_contract_pipeline.py` | ✅ 実装済み | 8 |
| アプリ内電子同意 | `consent_flow.py` | ✅ 実装済み | 7 |

#### 顧客管理担当（`sales/crm/`）

| パイプライン | ファイル | 状態 | ステップ数 |
|---|---|---|---|
| オンボード＆ヘルススコア | `customer_lifecycle_pipeline.py` | ✅ 実装済み | 7 |
| MRR集計＆要望管理 | `revenue_request_pipeline.py` | ✅ 実装済み | 6 |

#### CS担当（`sales/cs/`）

| パイプライン | ファイル | 状態 | ステップ数 |
|---|---|---|---|
| FAQ自動回答＆SLA監視 | `support_auto_response_pipeline.py` | ✅ 実装済み | 7 |
| アップセル支援ブリーフィング | `upsell_briefing_pipeline.py` | ✅ 実装済み | 5 |
| 解約フロー | `cancellation_pipeline.py` | ✅ 実装済み | 6 |

#### 学習・改善（`sales/learning/`）

| パイプライン | ファイル | 状態 | ステップ数 |
|---|---|---|---|
| 受注/失注→スコア自動改善 | `win_loss_feedback_pipeline.py` | ✅ 実装済み | 7 |
| CS品質→FAQ自動更新 | `cs_feedback_pipeline.py` | ✅ 実装済み | 5 |

**全12本 実装完了 ✅（テスト303件パス）**

---

### コネクタ（`workers/connector/`）

| コネクタ | ファイル | 状態 | 連携先 |
|---|---|---|---|
| kintone | `kintone.py` | ✅ | kintone |
| freee | `freee.py` | ✅ | freee会計・請求書 |
| slack | `slack.py` | ✅ (ログフォールバック) | Slack（チーム化後有効化） |
| cloudsign | `cloudsign.py` | ✅ | CloudSign電子署名 |
| gbizinfo | `gbizinfo.py` | ✅ | gBizINFO法人情報 |
| google_sheets | `google_sheets.py` | ✅ | Google Sheets |
| playwright_form | `playwright_form.py` | ✅ | Webフォーム自動送信 |
| email (Gmail) | `email.py` | ✅ | Gmail API（2,000件/日） |

**全8種 実装完了 ✅**

---

### 共通BPO（`workers/bpo/common/pipelines/`）

| パイプライン | ファイル | 状態 | ステップ数 | 優先度 |
|---|---|---|---|---|
| 経費処理 | `expense_pipeline.py` | ✅ 実装済み（6ステップ） | 6 | **P1** |
| 給与処理 | `payroll_pipeline.py` | ✅ 実装済み（7ステップ） | 7 | **P1** |
| 勤怠管理 | `attendance_pipeline.py` | ✅ 実装済み（5ステップ） | 5 | P2 |
| 契約管理 | `contract_pipeline.py` | ✅ 実装済み（5ステップ） | 5 | P2 |
| 期限リマインダ | `admin_reminder_pipeline.py` | ✅ 実装済み | 3 | P2 |
| 取引先管理 | `vendor_pipeline.py` | ✅ 実装済み | 4 | P2 |
| 月次レポート | `report_pipeline.py` | ❌ 未実装 | 5 | P3 |

---

## Claude Codeサブエージェント（`.claude/agents/`）

| エージェント | ファイル | 用途 |
|---|---|---|
| brain-module | `brain-module.md` | brain/モジュール実装 |
| router-impl | `router-impl.md` | FastAPIルーター実装 |
| frontend-page | `frontend-page.md` | Next.jsページ実装（UI_RULES準拠） |
| test-runner | `test-runner.md` | テスト修正専用（実行は親で行い、大規模修正時のみ委譲） |
| micro-agent | `micro-agent.md` | 共通マイクロエージェント実装 |
| bpo-pipeline | `bpo-pipeline.md` | 業種別パイプライン実装 |
| bpo-orchestrator | `bpo-orchestrator.md` | BPO Managerオーケストレーター実装 |
| agent-architect | `agent-architect.md` | エージェント体制レビュー（/review-agentsから呼び出し） |
| **ui-reviewer** | `ui-reviewer.md` | UI_RULES.mdベースの自動レビュー（/ui-reviewから呼び出し） |
| **connector-impl** | `connector-impl.md` | SaaSコネクタ実装（kintone/freee/Slack等） |

---

## スキル（`.claude/skills/`）

### 開発ワークフロー

| スキル | コマンド | 用途 | タイミング |
|---|---|---|---|
| implement | `/implement {module}` | モジュール実装（サブエージェント自動振り分け） | 各モジュール開発時 |
| test-module | `/test-module {module}` | テスト実行・修正（test-runner委譲） | 実装後 |
| db-migrate | `/db-migrate {description}` | DBマイグレーションSQL作成 | スキーマ変更時 |
| add-pipeline | `/add-pipeline {industry}/{name}` | 新パイプライン追加 | 新業種・新業務フロー |
| run-pipeline | `/run-pipeline {industry}/{name}` | パイプライン動作・精度テスト | 実装後の精度検証 |
| ui-review | `/ui-review` | UI/UXルール違反チェック・修正 | フロント実装後に必ず |

### 品質・セキュリティ

| スキル | コマンド | 用途 | タイミング |
|---|---|---|---|
| sec-check | `/sec-check` | 導入前セキュリティ7件確認 | パイロット投入前 |
| pre-deploy | `/pre-deploy` | テスト・CORS・環境変数・マイグレーション一括確認 | デプロイ前 |

### 事業KPI

| スキル | コマンド | 用途 | タイミング |
|---|---|---|---|
| review-agents | `/review-agents` | エージェント体制レビュー | **週1回** |
| pilot-prep | `/pilot-prep` | パイロット企業投入準備確認 | Week 7 |
| pmf-check | `/pmf-check` | NPS/WAU/「ないと困る」達成確認 | Week 8-9 |
| cost-check | `/cost-check` | テナント別LLMコスト確認 | **月次** |

---

## 直近のアクションキュー

```
✅ 1〜11. Layer 0/2 + 建設業5本 + 共通4本 実装済み
✅ 12. /run-pipeline construction/estimation — 実データ精度検証（conf=0.941）
✅ 13. restaurant/fl_cost — 飲食業原価管理 実装済み
✅ 14. 16業界BPO設計書作成完了（e_04〜e_14）
✅ 15. 全16業界 #1パイプライン実装完了 (697 tests pass)
✅ 16. security: 暗号化/PII/同意管理 実装
✅ 17. brain/inference: 精度向上自律ループ (Gemini-first)
✅ 18. brain/twin: 5次元モデル + What-if
✅ 19. brain/visualization: flow/decision_tree/completeness_map
✅ 20. workers/connector: kintone/freee/slack
✅ 21. frontend: BPO/twin/onboarding ページ

--- 次のアクション ---
22. パイロット3-5社投入準備（Week 7: onboarding flow確認）
23. PMFゲート計測体制（NPS/WAU/「ないと困る」）
24. common/report_pipeline.py 実装（P3）
25. construction/subcontractor・permit（P2残り）
```

---

## 実装統計（2026-03-22更新）

| カテゴリ | 実装済み | 合計 | 進捗 |
|---|---|---|---|
| マイクロエージェント | 17 | 17 | **100% ✅** |
| BPO Manager | 5 | 5 | **100% ✅**（+7スケジュール+9イベント+4条件連鎖） |
| BPOパイプライン（#1） | 16 | 16 | **100% ✅** |
| **全社自動化パイプライン** | **12** | **12** | **100% ✅** |
| コネクタ | 8 | 8 | **100% ✅** |
| — ★建設業 | 5/7 | 7 | 71% |
| — ★製造業 | 1/8 | 8 | 13% ✅#1 |
| — ★歯科 | 1/6 | 6 | 17% ✅#1 |
| — ★不動産管理 | 1/7 | 7 | 14% ✅#1 |
| — ★士業 | 1/7 | 7 | 14% ✅#1 |
| — A:介護 | 1/8 | 8 | 13% ✅#1 |
| — A:物流 | 1/8 | 8 | 13% ✅#1 |
| — A:飲食 | 1/7 | 7 | 14% ✅#1 |
| — B:医療 | 1/8 | 8 | 13% ✅#1 |
| — B:調剤薬局 | 1/6 | 6 | 17% ✅#1 |
| — B:美容 | 1/6 | 6 | 17% ✅#1 |
| — B:自動車整備 | 1/7 | 7 | 14% ✅#1 |
| — B:ホテル | 1/7 | 7 | 14% ✅#1 |
| — B:EC | 1/6 | 6 | 17% ✅#1 |
| — B:人材派遣 | 1/7 | 7 | 14% ✅#1 |
| — B:建築設計 | 1/6 | 6 | 17% ✅#1 |
| — 共通BPO | 6/7 | 7 | 86% |
| brain/全モジュール | 8 | 8 | **100% ✅** |
| routers/ | 18 | 18 | **100% ✅** |
| security/ | 3 | 3 | **100% ✅** |
| workers/connector/ | 8 | 8 | **100% ✅** |
| frontend/ 主要ページ | 26 | 26 | **100% ✅**（既存7+sales19） |
| DB CRUD（sales用） | 38関数 | 38 | **100% ✅** |
| LLMプロンプト | 7 | 7 | **100% ✅** |
| 設計書 | 17 | 17 | **100% ✅** |
| **テスト合計** | **1,000+ pass** | — | — |

---

## 定期メンテナンス

- **週1回**: `/review-agents` で体制チェック
- **新業種追加時**: `/add-pipeline {industry}/{name}` でスケルトン生成 → `Agent bpo-pipeline` で実装
- **設計書更新時**: `agent-architect` エージェントでギャップ再分析
