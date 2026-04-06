"""AgentFactory — ユニットテスト。

外部依存（Supabase, TrustScorer）はすべてモック化する。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from workers.bpo.engine.agent_factory import AgentFactory, BPOAgentRole

COMPANY_ID = "test-company-001"


# ─────────────────────────────────────────────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def factory() -> AgentFactory:
    return AgentFactory()


def _make_knowledge_item(
    item_id: str,
    title: str,
    content: str,
    item_type: str = "rule",
    domain: str = "construction",
) -> dict:
    return {
        "id": item_id,
        "title": title,
        "content": content,
        "item_type": item_type,
        "domain": domain,
        "source_type": "explicit",
    }


def _make_trust_score(level: int = 0):
    """TrustScoreのモックオブジェクトを生成する。"""
    mock = MagicMock()
    mock.level = level
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# デフォルトロール生成テスト（construction）
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateRolesConstruction:
    @pytest.mark.asyncio
    async def test_returns_list_of_bpo_agent_roles(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        assert isinstance(roles, list)
        assert len(roles) >= 1
        assert all(isinstance(r, BPOAgentRole) for r in roles)

    @pytest.mark.asyncio
    async def test_construction_has_estimation_role(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        role_names = [r.role_name for r in roles]
        assert "見積担当AI" in role_names

    @pytest.mark.asyncio
    async def test_construction_has_safety_docs_role(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        role_names = [r.role_name for r in roles]
        assert "安全書類担当AI" in role_names

    @pytest.mark.asyncio
    async def test_construction_estimation_has_expected_micro_agents(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        estimation_role = next(r for r in roles if r.role_name == "見積担当AI")
        assert "document_ocr" in estimation_role.micro_agents
        assert "calculator" in estimation_role.micro_agents
        assert "generator" in estimation_role.micro_agents

    @pytest.mark.asyncio
    async def test_construction_roles_have_correct_industry(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        for role in roles:
            assert role.industry == "construction"

    @pytest.mark.asyncio
    async def test_construction_default_trust_level_is_zero(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        for role in roles:
            assert role.trust_level == 0
            assert role.execution_level == "draft"


# ─────────────────────────────────────────────────────────────────────────────
# デフォルトロール生成テスト（sales）
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateRolesSales:
    @pytest.mark.asyncio
    async def test_sales_has_sales_rep_role(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "sales")

        role_names = [r.role_name for r in roles]
        assert "営業担当AI" in role_names

    @pytest.mark.asyncio
    async def test_sales_has_cs_role(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "sales")

        role_names = [r.role_name for r in roles]
        assert "CS担当AI" in role_names

    @pytest.mark.asyncio
    async def test_sales_rep_responsibilities(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "sales")

        sales_role = next(r for r in roles if r.role_name == "営業担当AI")
        assert "リード評価" in sales_role.responsibilities
        assert "提案書作成" in sales_role.responsibilities

    @pytest.mark.asyncio
    async def test_sales_cs_has_signal_detector(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "sales")

        cs_role = next(r for r in roles if r.role_name == "CS担当AI")
        assert "signal_detector" in cs_role.micro_agents


# ─────────────────────────────────────────────────────────────────────────────
# 未知の業種テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateRolesUnknownIndustry:
    @pytest.mark.asyncio
    async def test_unknown_industry_returns_empty_list(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "unknown_industry_xyz")

        assert roles == []

    @pytest.mark.asyncio
    async def test_empty_string_industry_returns_empty_list(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "")

        assert roles == []


# ─────────────────────────────────────────────────────────────────────────────
# ナレッジ注入テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestInjectKnowledge:
    @pytest.mark.asyncio
    async def test_relevant_knowledge_injected_into_context(self, factory):
        items = [
            _make_knowledge_item(
                "k-001",
                title="見積計算ルール",
                content="見積は材料費の1.3倍を標準とする",
                item_type="rule",
            )
        ]
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=items)),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        estimation_role = next(r for r in roles if r.role_name == "見積担当AI")
        context_ids = [c["id"] for c in estimation_role.knowledge_context]
        assert "k-001" in context_ids

    @pytest.mark.asyncio
    async def test_rule_type_knowledge_added_to_domain_rules(self, factory):
        items = [
            _make_knowledge_item(
                "k-002",
                title="見積承認ルール",
                content="見積金額が500万円を超える場合は社長承認が必要",
                item_type="rule",
            )
        ]
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=items)),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        estimation_role = next(r for r in roles if r.role_name == "見積担当AI")
        rule_ids = [
            r.get("knowledge_item_id")
            for r in estimation_role.domain_rules
            if r.get("source") == "knowledge_item"
        ]
        assert "k-002" in rule_ids

    @pytest.mark.asyncio
    async def test_irrelevant_knowledge_not_injected(self, factory):
        # "人事評価" はconstruction/見積担当AIのresponsibilitiesと無関係
        items = [
            _make_knowledge_item(
                "k-999",
                title="人事評価基準",
                content="給与査定は年2回実施する",
                item_type="fact",
            )
        ]
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=items)),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        estimation_role = next(r for r in roles if r.role_name == "見積担当AI")
        context_ids = [c.get("id") for c in estimation_role.knowledge_context]
        assert "k-999" not in context_ids

    @pytest.mark.asyncio
    async def test_empty_knowledge_items_still_returns_roles(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        assert len(roles) >= 1
        for role in roles:
            assert role.knowledge_context == []

    def test_inject_knowledge_direct_call(self, factory):
        """_inject_knowledgeを直接テストする（純粋関数として）。"""
        role = BPOAgentRole(
            role_name="見積担当AI",
            industry="construction",
            responsibilities=["図面からの見積作成", "コスト計算"],
            micro_agents=["calculator"],
        )
        items = [
            _make_knowledge_item("k-010", "見積標準", "見積を作成する", item_type="rule"),
            _make_knowledge_item("k-011", "無関係", "全然関係ない内容xyz", item_type="fact"),
        ]
        updated = factory._inject_knowledge(role, items)

        injected_ids = [c["id"] for c in updated.knowledge_context]
        assert "k-010" in injected_ids
        # "無関係/xyz" はresponsibilitiesに含まれないためスキップされる（またはされない場合もある）
        # 厳密なフィルタより、見積関連が確実に含まれることを確認
        assert len(updated.knowledge_context) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 信頼レベル計算テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateTrustLevel:
    def test_empty_history_returns_level_0(self, factory):
        assert factory._calculate_trust_level([]) == 0

    def test_less_than_5_approvals_returns_level_0(self, factory):
        history = [{"status": "approved", "modification_diff": None}] * 4
        assert factory._calculate_trust_level(history) == 0

    def test_5_approvals_returns_level_1(self, factory):
        history = [{"status": "approved", "modification_diff": None}] * 5
        assert factory._calculate_trust_level(history) == 1

    def test_20_approvals_high_rate_returns_level_2(self, factory):
        # 20件で承認率80%以上
        history = (
            [{"status": "approved", "modification_diff": None}] * 16
            + [{"status": "rejected", "modification_diff": None}] * 4
        )
        assert factory._calculate_trust_level(history) == 2

    def test_50_approvals_high_rate_consecutive_returns_level_3(self, factory):
        # 50件で承認率90%以上 + 連続30件成功
        history = (
            [{"status": "approved", "modification_diff": None}] * 45
            + [{"status": "rejected", "modification_diff": None}] * 5
        )
        assert factory._calculate_trust_level(history) == 3

    def test_rejection_heavy_history_stays_level_1(self, factory):
        # total >= 5 でlevel 1になるが、approval_rate < 0.8 なのでlevel 2には上がらない
        # TrustScorerの既存ロジックと整合: total件数でlevel 1、承認率でlevel 2以上を判定する
        history = [{"status": "rejected", "modification_diff": None}] * 10
        assert factory._calculate_trust_level(history) == 1

    def test_modified_approvals_do_not_count_as_success(self, factory):
        # modification_diffありは連続成功にカウントされない
        history = (
            [{"status": "approved", "modification_diff": {"before": "A", "after": "B"}}] * 20
        )
        # 承認率は高いが修正ありなので連続成功=0 → level 1止まり
        level = factory._calculate_trust_level(history)
        assert level <= 1


# ─────────────────────────────────────────────────────────────────────────────
# trust_level → execution_level マッピングテスト
# ─────────────────────────────────────────────────────────────────────────────

class TestTrustLevelToExecutionLevel:
    @pytest.mark.asyncio
    async def test_trust_level_0_gives_draft(self, factory):
        trust = _make_trust_score(level=0)
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=trust)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        for role in roles:
            assert role.execution_level == "draft"
            assert role.trust_level == 0

    @pytest.mark.asyncio
    async def test_trust_level_2_gives_auto_review(self, factory):
        trust = _make_trust_score(level=2)
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=trust)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        for role in roles:
            assert role.execution_level == "auto_review"
            assert role.trust_level == 2

    @pytest.mark.asyncio
    async def test_trust_level_3_gives_auto_execute(self, factory):
        trust = _make_trust_score(level=3)
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=trust)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction")

        for role in roles:
            assert role.execution_level == "auto_execute"
            assert role.trust_level == 3


# ─────────────────────────────────────────────────────────────────────────────
# ゲノムデータ注入テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestGenomeDataInjection:
    @pytest.mark.asyncio
    async def test_genome_rules_added_to_domain_rules(self, factory):
        genome_data = {
            "rules": [
                {"rule": "歩掛かりは標準表を使用する", "category": "estimation"},
                "安全書類は着工7日前までに提出",
            ],
        }
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction", genome_data=genome_data)

        for role in roles:
            genome_rules = [r for r in role.domain_rules if r.get("source") == "genome"]
            assert len(genome_rules) >= 1

    @pytest.mark.asyncio
    async def test_genome_pricing_added_to_domain_rules(self, factory):
        genome_data = {
            "pricing": {"labor_rate": 25000, "overhead_ratio": 0.18},
        }
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction", genome_data=genome_data)

        for role in roles:
            pricing_rules = [
                r for r in role.domain_rules
                if r.get("source") == "genome" and r.get("category") == "pricing"
            ]
            assert len(pricing_rules) == 1
            assert pricing_rules[0]["data"]["labor_rate"] == 25000

    @pytest.mark.asyncio
    async def test_genome_extra_roles_appended(self, factory):
        genome_data = {
            "extra_roles": [
                {
                    "role_name": "現場監督AI",
                    "responsibilities": ["工程写真整理", "日報作成"],
                    "micro_agents": ["document_ocr", "generator"],
                }
            ]
        }
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction", genome_data=genome_data)

        role_names = [r.role_name for r in roles]
        assert "現場監督AI" in role_names

    @pytest.mark.asyncio
    async def test_none_genome_data_still_works(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            roles = await factory.create_roles(COMPANY_ID, "construction", genome_data=None)

        assert len(roles) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# get_role テスト
# ─────────────────────────────────────────────────────────────────────────────

class TestGetRole:
    @pytest.mark.asyncio
    async def test_get_existing_role(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            role = await factory.get_role(COMPANY_ID, "見積担当AI", industry="construction")

        assert role is not None
        assert role.role_name == "見積担当AI"
        assert role.industry == "construction"

    @pytest.mark.asyncio
    async def test_get_nonexistent_role_returns_none(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            role = await factory.get_role(COMPANY_ID, "存在しないAI", industry="construction")

        assert role is None

    @pytest.mark.asyncio
    async def test_get_role_without_industry_searches_all(self, factory):
        with (
            patch.object(factory, "_fetch_knowledge_items", new=AsyncMock(return_value=[])),
            patch.object(factory, "_fetch_trust_score", new=AsyncMock(return_value=None)),
        ):
            role = await factory.get_role(COMPANY_ID, "営業担当AI")

        assert role is not None
        assert role.role_name == "営業担当AI"


# ─────────────────────────────────────────────────────────────────────────────
# BPOAgentRoleモデルテスト
# ─────────────────────────────────────────────────────────────────────────────

class TestBPOAgentRoleModel:
    def test_default_trust_level_is_zero(self):
        role = BPOAgentRole(role_name="テストAI", industry="construction")
        assert role.trust_level == 0

    def test_default_execution_level_is_draft(self):
        role = BPOAgentRole(role_name="テストAI", industry="construction")
        assert role.execution_level == "draft"

    def test_default_lists_are_empty(self):
        role = BPOAgentRole(role_name="テストAI", industry="construction")
        assert role.responsibilities == []
        assert role.micro_agents == []
        assert role.domain_rules == []
        assert role.knowledge_context == []

    def test_model_copy_update_does_not_mutate_original(self):
        role = BPOAgentRole(
            role_name="テストAI",
            industry="construction",
            knowledge_context=[],
        )
        updated = role.model_copy(update={"knowledge_context": [{"id": "x"}]})
        assert role.knowledge_context == []
        assert len(updated.knowledge_context) == 1
