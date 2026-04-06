"""Agent Factory — ゲノムJSON + knowledge_items からBPOAgentRoleを自動生成する。

会社ごとにカスタマイズされたAIエージェントの役割定義（BPOAgentRole）を、
業種デフォルト設定とDB上のknowledge_itemsを組み合わせて構築する。
"""
from __future__ import annotations

import logging
from typing import Optional, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─────────────────────────────────────
# BPOAgentRoleモデル
# ─────────────────────────────────────

class BPOAgentRole(BaseModel):
    """AIエージェントの役割定義。

    ゲノムJSONと会社固有のknowledge_itemsを合成して生成される。
    task_routerがパイプラインを呼び出す際にコンテキストとして渡される。
    """
    role_name: str
    """例: "見積担当AI" """

    industry: str
    """例: "construction" """

    responsibilities: list[str] = Field(default_factory=list)
    """例: ["図面からの見積作成", "コスト計算"] """

    micro_agents: list[str] = Field(default_factory=list)
    """使用するマイクロエージェント名のリスト。"""

    domain_rules: list[dict[str, Any]] = Field(default_factory=list)
    """業界ルール（ゲノムから取得 + knowledge_itemsから注入）。"""

    knowledge_context: list[dict[str, Any]] = Field(default_factory=list)
    """会社固有のナレッジ（knowledge_itemsから取得）。"""

    trust_level: int = 0
    """現在の信頼レベル（0-3）。TrustScorerのlevelを転写する。"""

    execution_level: str = "draft"
    """draft / auto_review / auto_execute。trust_levelから導出される。"""


# ─────────────────────────────────────
# AgentFactory
# ─────────────────────────────────────

class AgentFactory:
    """ゲノム + ナレッジから BPOAgentRole を生成するファクトリー。

    Usage::

        factory = AgentFactory()
        roles = await factory.create_roles(company_id="acme", industry="construction")
    """

    # 業種 → デフォルトロール定義
    DEFAULT_ROLES: dict[str, list[dict[str, Any]]] = {
        "construction": [
            {
                "role_name": "見積担当AI",
                "responsibilities": ["図面からの見積作成", "コスト計算", "見積書生成"],
                "micro_agents": [
                    "document_ocr",
                    "structured_extractor",
                    "calculator",
                    "generator",
                    "anomaly_detector",
                ],
            },
            {
                "role_name": "安全書類担当AI",
                "responsibilities": ["安全書類の作成", "法令チェック"],
                "micro_agents": [
                    "document_ocr",
                    "structured_extractor",
                    "compliance",
                    "generator",
                ],
            },
            {
                "role_name": "工事管理担当AI",
                "responsibilities": ["工程管理", "原価レポート", "外注管理"],
                "micro_agents": [
                    "structured_extractor",
                    "calculator",
                    "generator",
                    "saas_reader",
                ],
            },
        ],
        "manufacturing": [
            {
                "role_name": "見積担当AI",
                "responsibilities": ["工程推定", "コスト計算", "見積書生成"],
                "micro_agents": [
                    "document_ocr",
                    "structured_extractor",
                    "calculator",
                    "generator",
                    "anomaly_detector",
                ],
            },
            {
                "role_name": "製造管理担当AI",
                "responsibilities": ["生産計画", "品質管理", "在庫管理"],
                "micro_agents": [
                    "structured_extractor",
                    "calculator",
                    "saas_reader",
                    "anomaly_detector",
                ],
            },
        ],
        "common": [
            {
                "role_name": "経理担当AI",
                "responsibilities": ["経費精算", "請求書処理", "支払管理"],
                "micro_agents": [
                    "document_ocr",
                    "structured_extractor",
                    "calculator",
                    "validator",
                    "anomaly_detector",
                ],
            },
            {
                "role_name": "労務担当AI",
                "responsibilities": ["勤怠集計", "給与計算準備"],
                "micro_agents": [
                    "structured_extractor",
                    "calculator",
                    "generator",
                ],
            },
        ],
        "sales": [
            {
                "role_name": "営業担当AI",
                "responsibilities": ["リード評価", "提案書作成", "見積作成"],
                "micro_agents": [
                    "company_researcher",
                    "generator",
                    "pdf_generator",
                    "anomaly_detector",
                ],
            },
            {
                "role_name": "CS担当AI",
                "responsibilities": ["問い合わせ対応", "解約防止"],
                "micro_agents": [
                    "saas_reader",
                    "generator",
                    "signal_detector",
                ],
            },
        ],
        "clinic": [
            {
                "role_name": "医事担当AI",
                "responsibilities": ["レセプト作成", "保険請求", "患者対応"],
                "micro_agents": [
                    "document_ocr",
                    "structured_extractor",
                    "validator",
                    "generator",
                ],
            },
        ],
        "nursing": [
            {
                "role_name": "介護請求担当AI",
                "responsibilities": ["介護給付費請求", "ケアプラン管理"],
                "micro_agents": [
                    "structured_extractor",
                    "calculator",
                    "validator",
                    "generator",
                ],
            },
        ],
        "realestate": [
            {
                "role_name": "物件管理担当AI",
                "responsibilities": ["家賃管理", "入退去処理", "契約更新"],
                "micro_agents": [
                    "saas_reader",
                    "structured_extractor",
                    "generator",
                    "anomaly_detector",
                ],
            },
        ],
        "logistics": [
            {
                "role_name": "配車管理担当AI",
                "responsibilities": ["配車計画", "ドライバー管理", "運行記録"],
                "micro_agents": [
                    "saas_reader",
                    "structured_extractor",
                    "calculator",
                    "generator",
                ],
            },
        ],
    }

    # trust_level → execution_level のマッピング
    _EXECUTION_LEVEL_MAP = {
        0: "draft",
        1: "draft",
        2: "auto_review",
        3: "auto_execute",
    }

    async def create_roles(
        self,
        company_id: str,
        industry: str,
        genome_data: Optional[dict[str, Any]] = None,
    ) -> list[BPOAgentRole]:
        """会社の業種に基づいてロール一覧を生成する。

        Args:
            company_id: テナントID
            industry: 業種コード（例: "construction", "manufacturing"）
            genome_data: ゲノムJSON（任意）。追加ロールやルールを注入する。

        Returns:
            BPOAgentRoleのリスト。未知の業種の場合は空リストを返す。
        """
        # 1. DEFAULT_ROLESから業種のデフォルトロールを取得
        default_templates = self.DEFAULT_ROLES.get(industry, [])
        if not default_templates:
            logger.warning(f"未知の業種: {industry}。DEFAULT_ROLESに定義がありません。")
            return []

        # 2. ゲノムデータからルールと追加ロールを展開
        genome_domain_rules: list[dict[str, Any]] = []
        genome_extra_roles: list[dict[str, Any]] = []
        if genome_data:
            genome_domain_rules = self._extract_genome_rules(genome_data)
            genome_extra_roles = genome_data.get("extra_roles", [])

        # 3. DBからknowledge_itemsを取得（失敗時は空で継続）
        knowledge_items = await self._fetch_knowledge_items(company_id, industry)

        # 4. approval履歴からtrust_levelを算出（失敗時は0）
        trust_score = await self._fetch_trust_score(company_id, industry)
        trust_level = trust_score.level if trust_score else 0
        execution_level = self._EXECUTION_LEVEL_MAP.get(trust_level, "draft")

        # 5. ロールオブジェクトを構築
        roles: list[BPOAgentRole] = []

        all_templates = list(default_templates) + genome_extra_roles
        for tmpl in all_templates:
            role = BPOAgentRole(
                role_name=tmpl["role_name"],
                industry=industry,
                responsibilities=list(tmpl.get("responsibilities", [])),
                micro_agents=list(tmpl.get("micro_agents", [])),
                domain_rules=list(genome_domain_rules),
                knowledge_context=[],
                trust_level=trust_level,
                execution_level=execution_level,
            )
            # ナレッジを注入
            role = self._inject_knowledge(role, knowledge_items)
            roles.append(role)

        logger.info(
            f"AgentFactory: company={company_id} industry={industry} "
            f"roles={[r.role_name for r in roles]} trust_level={trust_level}"
        )
        return roles

    async def get_role(
        self,
        company_id: str,
        role_name: str,
        industry: Optional[str] = None,
    ) -> Optional[BPOAgentRole]:
        """特定のロール名でロールを取得する。

        Args:
            company_id: テナントID
            role_name: ロール名（例: "見積担当AI"）
            industry: 業種コード。Noneの場合はすべての業種を検索する。

        Returns:
            マッチするBPOAgentRole。見つからない場合はNone。
        """
        industries = [industry] if industry else list(self.DEFAULT_ROLES.keys())

        for ind in industries:
            roles = await self.create_roles(company_id, ind)
            for role in roles:
                if role.role_name == role_name:
                    return role
        return None

    def _inject_knowledge(
        self,
        role: BPOAgentRole,
        knowledge_items: list[dict[str, Any]],
    ) -> BPOAgentRole:
        """ナレッジアイテムをロールに注入する。

        roleのresponsibilitiesに関連するknowledge_itemsをフィルタして
        knowledge_contextとdomain_rulesに追加する。

        Args:
            role: 注入先のBPOAgentRole
            knowledge_items: DBから取得したknowledge_items

        Returns:
            knowledge_contextとdomain_rulesが更新されたBPOAgentRole
        """
        if not knowledge_items:
            return role

        responsibilities_text = " ".join(role.responsibilities).lower()
        injected_context: list[dict[str, Any]] = []
        injected_rules: list[dict[str, Any]] = []

        # 日本語は split() で単語分割できないため、N-gram（2〜4文字）でキーワードを生成する
        def _ngrams(text: str, min_n: int = 2, max_n: int = 4) -> list[str]:
            """テキストから重複なしN-gramを生成する（日本語対応）。"""
            tokens: set[str] = set()
            # スペース区切りのトークン（英数字向け）
            for word in text.split():
                if len(word) >= min_n:
                    tokens.add(word)
            # 文字N-gram（日本語向け）
            for n in range(min_n, max_n + 1):
                for i in range(len(text) - n + 1):
                    tokens.add(text[i:i + n])
            return list(tokens)

        responsibility_keywords = _ngrams(responsibilities_text)

        for item in knowledge_items:
            item_domain = str(item.get("domain", "")).lower()
            item_content = str(item.get("content", "")).lower()
            item_title = str(item.get("title", "")).lower()
            item_type = str(item.get("item_type", "")).lower()

            # responsibilitiesとの関連度チェック（N-gramマッチ）
            is_relevant = any(
                keyword in item_content or keyword in item_title
                for keyword in responsibility_keywords
            ) or item_domain in responsibilities_text

            if not is_relevant:
                continue

            context_entry = {
                "id": item.get("id"),
                "title": item.get("title"),
                "content": item.get("content"),
                "item_type": item.get("item_type"),
                "source_type": item.get("source_type"),
            }
            injected_context.append(context_entry)

            # rule/decision_logic タイプはdomain_rulesにも追加
            if item_type in ("rule", "decision_logic"):
                injected_rules.append({
                    "source": "knowledge_item",
                    "knowledge_item_id": item.get("id"),
                    "rule": item.get("content"),
                    "title": item.get("title"),
                })

        updated_context = list(role.knowledge_context) + injected_context
        updated_rules = list(role.domain_rules) + injected_rules

        return role.model_copy(update={
            "knowledge_context": updated_context,
            "domain_rules": updated_rules,
        })

    def _calculate_trust_level(self, approval_history: list[dict[str, Any]]) -> int:
        """承認履歴から信頼レベルを計算する。

        TrustScorerの実装を参考にした簡易版。
        DBに接続しない純粋関数として提供する（テスト用途に適する）。

        Args:
            approval_history: bpo_approvalsテーブルの行リスト

        Returns:
            信頼レベル（0-3）
        """
        total = len(approval_history)
        if total == 0:
            return 0

        approved = sum(
            1 for r in approval_history
            if r.get("status") == "approved" and not r.get("modification_diff")
        )
        rejected = sum(1 for r in approval_history if r.get("status") == "rejected")

        approval_rate = approved / total

        # 連続成功カウント
        consecutive = 0
        for r in approval_history:
            if r.get("status") == "approved" and not r.get("modification_diff"):
                consecutive += 1
            else:
                break

        level = 0
        if total >= 5:
            level = 1
        if total >= 20 and approval_rate >= 0.8:
            level = 2
        if total >= 50 and approval_rate >= 0.9 and consecutive >= 30:
            level = 3

        return level

    # ─────────────────────────────────────
    # プライベートDBヘルパー
    # ─────────────────────────────────────

    async def _fetch_knowledge_items(
        self,
        company_id: str,
        domain: str,
    ) -> list[dict[str, Any]]:
        """knowledge_itemsテーブルから会社・業種でフィルタして取得する。

        DBに接続できない場合（テスト環境等）は空リストを返す。
        """
        try:
            from db.supabase import get_service_client
            db = get_service_client()
            result = db.table("knowledge_items").select(
                "id, title, content, item_type, domain, source_type"
            ).eq("company_id", company_id).eq("domain", domain).execute()
            return result.data or []
        except Exception as e:
            logger.debug(f"knowledge_items取得スキップ (company={company_id} domain={domain}): {e}")
            return []

    async def _fetch_trust_score(self, company_id: str, industry: str):
        """TrustScorerから信頼スコアを取得する。

        DBに接続できない場合（テスト環境等）はNoneを返す。
        """
        try:
            from workers.bpo.engine.approval_workflow import TrustScorer
            return await TrustScorer.calculate(company_id, target_type=industry)
        except Exception as e:
            logger.debug(f"TrustScore取得スキップ (company={company_id} industry={industry}): {e}")
            return None

    @staticmethod
    def _extract_genome_rules(genome_data: dict[str, Any]) -> list[dict[str, Any]]:
        """ゲノムJSONからdomain_rulesに相当するエントリを抽出する。

        ゲノムの構造例::

            {
                "rules": [{"rule": "...", "source": "genome"}],
                "pricing": {...},
                "workflows": [...]
            }
        """
        rules: list[dict[str, Any]] = []

        # rules キー直下
        for r in genome_data.get("rules", []):
            if isinstance(r, dict):
                rules.append({"source": "genome", **r})
            elif isinstance(r, str):
                rules.append({"source": "genome", "rule": r})

        # pricing情報をルール化
        pricing = genome_data.get("pricing", {})
        if pricing:
            rules.append({"source": "genome", "category": "pricing", "data": pricing})

        # workflows情報をルール化
        for wf in genome_data.get("workflows", []):
            if isinstance(wf, dict):
                rules.append({"source": "genome", "category": "workflow", **wf})

        return rules
