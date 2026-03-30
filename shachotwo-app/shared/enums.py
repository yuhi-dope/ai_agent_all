"""汎用ステータスEnum定義。

他プロジェクトへの転用を前提に、業界非依存の共通ステータスを集約。
業界固有のEnum（ProjectType, PriceSource等）は各 workers/bpo/{industry}/models.py に残す。

使い方:
    from shared.enums import ApprovalStatus, ProcessingStatus
"""
from enum import Enum


# ---------------------------------------------------------------------------
# A. ワークフロー系
# ---------------------------------------------------------------------------

class ProcessingStatus(str, Enum):
    """非同期処理の進行ステータス（LLM抽出・バッチ処理・データ変換等）"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"  # リトライ不可の恒久的失敗


class ApprovalStatus(str, Enum):
    """汎用承認ワークフロー（経費・見積・発注・AI出力等）"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"    # 承認者が修正して承認
    CANCELLED = "cancelled"


class DocumentStatus(str, Enum):
    """ドキュメントライフサイクル（提案書・見積書・契約書等）"""
    DRAFT = "draft"
    SENT = "sent"
    VIEWED = "viewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ProposalStatus(str, Enum):
    """AI提案・改善提案のライフサイクル"""
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IMPLEMENTED = "implemented"


# ---------------------------------------------------------------------------
# B. ビジネスドメイン系
# ---------------------------------------------------------------------------

class InvoiceStatus(str, Enum):
    """請求書ステータス"""
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    """支払い・決済ステータス"""
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    FAILED = "failed"


class ContractStatus(str, Enum):
    """契約ライフサイクル"""
    DRAFT = "draft"
    SENT = "sent"
    SIGNED = "signed"
    ACTIVE = "active"
    TERMINATED = "terminated"


class InvitationStatus(str, Enum):
    """ユーザー招待ステータス"""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class LeadStatus(str, Enum):
    """SFA リードステータス"""
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    NURTURING = "nurturing"


class OpportunityStage(str, Enum):
    """営業パイプライン ステージ"""
    PROPOSAL = "proposal"
    QUOTATION = "quotation"
    NEGOTIATION = "negotiation"
    CONTRACT = "contract"
    WON = "won"
    LOST = "lost"


class CustomerStatus(str, Enum):
    """顧客ライフサイクル（SaaS CRM向け）"""
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    CHURNED = "churned"


class TicketStatus(str, Enum):
    """サポートチケットステータス"""
    OPEN = "open"
    WAITING = "waiting"
    AI_RESPONDED = "ai_responded"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class FeatureRequestStatus(str, Enum):
    """機能リクエスト管理"""
    NEW = "new"
    REVIEWING = "reviewing"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DECLINED = "declined"


class EntityStatus(str, Enum):
    """マスタデータの有効/無効（ベンダー・従業員・下請等）"""
    ACTIVE = "active"
    INACTIVE = "inactive"


class WorkerStatus(str, Enum):
    """従業員の雇用状態"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    RETIRED = "retired"


class PermitStatus(str, Enum):
    """許認可・ライセンスのライフサイクル"""
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    RENEWED = "renewed"


# ---------------------------------------------------------------------------
# C. AI / LLM 特化系
# ---------------------------------------------------------------------------

class ModelTier(str, Enum):
    """LLMモデル選択ティア（コスト-品質トレードオフ）"""
    FAST = "fast"          # 高速・低コスト（Gemini Flash等）
    STANDARD = "standard"  # バランス型（Gemini Pro等）
    PREMIUM = "premium"    # 高精度（Claude Opus等）


class LLMCallStatus(str, Enum):
    """LLM呼び出し結果ステータス"""
    SUCCESS = "success"
    PARSE_ERROR = "parse_error"
    PARTIAL_RECOVERY = "partial_recovery"
    API_ERROR = "api_error"
    SAFETY_BLOCK = "safety_block"
    TIMEOUT = "timeout"


class ExecutionLevel(int, Enum):
    """AI自動化の権限レベル（段階的な信頼委譲）"""
    NOTIFY_ONLY = 0    # 通知のみ
    DATA_COLLECT = 1   # データ収集・レポート
    DRAFT_CREATE = 2   # ドラフト作成
    APPROVAL_GATED = 3 # 承認後実行
    AUTONOMOUS = 4     # 自律実行（trust_score >= 0.95）


class TriggerType(str, Enum):
    """パイプライン起動条件"""
    SCHEDULE = "schedule"    # 定時実行
    EVENT = "event"          # イベント駆動
    CONDITION = "condition"  # 条件トリガー
    PROACTIVE = "proactive"  # AI能動的提案


# ---------------------------------------------------------------------------
# D. インフラ / 運用系
# ---------------------------------------------------------------------------

class HealthStatus(str, Enum):
    """外部サービス・API接続の健全性"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ConnectionMethod(str, Enum):
    """外部ツール接続方式"""
    API = "api"
    IPAAS = "ipaas"
    RPA = "rpa"


class ToolType(str, Enum):
    """外部ツールの分類"""
    SAAS = "saas"
    CLI = "cli"
    API = "api"
    MANUAL = "manual"


class Priority(str, Enum):
    """汎用優先度（チケット・タスク・アラート等）"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class UserRole(str, Enum):
    """基本RBAC（MVP 2ロール。Phase 3+で拡張予定）"""
    ADMIN = "admin"
    EDITOR = "editor"


# ---------------------------------------------------------------------------
# E. データ分類系
# ---------------------------------------------------------------------------

class InputType(str, Enum):
    """知識・データの入力方式"""
    VOICE = "voice"
    TEXT = "text"
    DOCUMENT = "document"
    INTERACTIVE = "interactive"
    INFERRED = "inferred"


class KnowledgeItemType(str, Enum):
    """構造化知識の分類"""
    RULE = "rule"
    FLOW = "flow"
    DECISION_LOGIC = "decision_logic"
    FACT = "fact"
    TIP = "tip"


class SourceType(str, Enum):
    """データの発生源（信頼度評価に使用）"""
    EXPLICIT = "explicit"    # ユーザーが明示的に入力
    INFERRED = "inferred"    # AIが推定
    TEMPLATE = "template"    # テンプレートから生成


class CostType(str, Enum):
    """原価分類（製造原価・プロジェクト原価等）"""
    MATERIAL = "material"
    LABOR = "labor"
    SUBCONTRACT = "subcontract"
    EQUIPMENT = "equipment"
    OVERHEAD = "overhead"


class BillingType(str, Enum):
    """請求方式"""
    MONTHLY = "monthly"
    MILESTONE = "milestone"
    COMPLETION = "completion"
