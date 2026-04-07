# shared/ — 汎用ステータス・設定モジュール

## 概要

業界・プロジェクト非依存の共通Enum定義を集約したモジュール。
他プロジェクトへの転用を前提に設計されている。

## ファイル構成

| ファイル | 内容 |
|---|---|
| `enums.py` | 汎用ステータスEnum（30+定義） |
| `__init__.py` | パッケージ初期化 |

## カテゴリ一覧

| カテゴリ | Enum | 用途 |
|---|---|---|
| **ワークフロー** | `ProcessingStatus` | 非同期処理（pending→completed/failed） |
| | `ApprovalStatus` | 承認フロー（pending→approved/rejected） |
| | `DocumentStatus` | ドキュメント（draft→accepted/rejected） |
| | `ProposalStatus` | 提案ライフサイクル |
| **ビジネス** | `InvoiceStatus` | 請求書 |
| | `PaymentStatus` | 支払い・決済 |
| | `ContractStatus` | 契約 |
| | `InvitationStatus` | ユーザー招待 |
| | `LeadStatus` | SFAリード |
| | `OpportunityStage` | 営業パイプライン |
| | `CustomerStatus` | 顧客ライフサイクル |
| | `TicketStatus` | サポートチケット |
| | `FeatureRequestStatus` | 機能リクエスト |
| | `EntityStatus` | マスタ有効/無効 |
| | `WorkerStatus` | 従業員状態 |
| | `PermitStatus` | 許認可・ライセンス |
| **AI/LLM** | `ModelTier` | LLMモデル選択（fast/standard/premium） |
| | `LLMCallStatus` | LLM呼び出し結果 |
| | `ExecutionLevel` | AI自動化権限（0-4段階） |
| | `TriggerType` | パイプライン起動条件 |
| **インフラ** | `HealthStatus` | サービス健全性 |
| | `ConnectionMethod` | 接続方式（api/ipaas/rpa） |
| | `ToolType` | ツール分類 |
| | `Priority` | 優先度（low→urgent） |
| | `UserRole` | RBAC（admin/editor） |
| **データ分類** | `InputType` | データ入力方式 |
| | `KnowledgeItemType` | 知識分類 |
| | `SourceType` | データ発生源 |
| | `CostType` | 原価分類 |
| | `BillingType` | 請求方式 |

## 他プロジェクトへの転用方法

1. `shared/` ディレクトリごとコピー
2. 不要なEnumを削除、プロジェクト固有のEnumを追加
3. `from shared.enums import ApprovalStatus` で利用開始

## 設計ルール

- **3〜6値が最適**: ステータスの粒度はこの範囲に収める
- **業界固有は入れない**: 建設の `ProjectType` や製造の `ShapeType` は各業種modelsに残す
- **DBと一致させる**: CHECK制約の値とEnum値を必ず同期する
- **デフォルト値を明示**: 最初の状態を常にデフォルトにする（`default 'draft'` 等）
