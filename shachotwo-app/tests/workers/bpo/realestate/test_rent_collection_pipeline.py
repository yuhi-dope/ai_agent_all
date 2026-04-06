"""不動産業 家賃回収パイプライン テスト"""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.bpo.realestate.pipelines.rent_collection_pipeline import (
    LATE_PAYMENT_RATE_DAILY,
    NOTICE_STAGES,
    OVERDUE_DAYS_THRESHOLDS,
    RentCollectionPipeline,
    RentCollectionPipelineResult,
)
from workers.micro.message import MessageDraftResult


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _make_tenant(
    tenant_id: str = "T001",
    tenant_name: str = "田中花子",
    room_number: str = "301",
    monthly_rent: int = 80_000,
    payment_due_date: str = "2026-03-27",
    actual_payment_date: str | None = None,
    actual_payment_amount: int | None = None,
) -> dict:
    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "room_number": room_number,
        "monthly_rent": monthly_rent,
        "payment_due_date": payment_due_date,
        "actual_payment_date": actual_payment_date,
        "actual_payment_amount": actual_payment_amount,
    }


def _mock_draft(document_type: str = "督促状（初回）") -> MessageDraftResult:
    return MessageDraftResult(
        subject=f"【{document_type}】家賃滞納について",
        body="本文ドラフトです。",
        document_type=document_type,
        model_used="gemini-2.5-flash",
        is_template_fallback=False,
    )


# ---------------------------------------------------------------------------
# テスト 1: 全員入金済みで滞納ゼロ
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_tenants_paid_no_arrears():
    """全テナントが入金済みの場合、滞納リストが空であること"""
    pipeline = RentCollectionPipeline()
    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-03-31",
        "tenants": [
            _make_tenant("T001", "田中花子", "301", 80_000, "2026-03-27",
                         actual_payment_date="2026-03-25", actual_payment_amount=80_000),
            _make_tenant("T002", "鈴木次郎", "102", 65_000, "2026-03-27",
                         actual_payment_date="2026-03-27", actual_payment_amount=65_000),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
    ) as mock_drafter:
        result = await pipeline.run(input_data)

    assert isinstance(result, RentCollectionPipelineResult)
    assert result.arrears_tenants == []
    assert result.total_arrears_amount == 0
    assert result.notices_required == []
    assert result.paid_count == 2
    assert result.total_tenants == 2
    mock_drafter.assert_not_called()


# ---------------------------------------------------------------------------
# テスト 2: 未払いテナントを検出
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unpaid_tenant_flagged():
    """未払いテナントが arrears_tenants に含まれること"""
    pipeline = RentCollectionPipeline()
    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-03-31",
        "tenants": [
            _make_tenant("T001", "田中花子", "301", 80_000, "2026-03-27"),  # 未払い
            _make_tenant("T002", "鈴木次郎", "102", 65_000, "2026-03-27",
                         actual_payment_date="2026-03-25", actual_payment_amount=65_000),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("督促状（初回）"),
    ):
        result = await pipeline.run(input_data)

    assert len(result.arrears_tenants) == 1
    assert result.arrears_tenants[0]["tenant_id"] == "T001"
    assert result.arrears_tenants[0]["status"] == "unpaid"
    assert result.paid_count == 1


# ---------------------------------------------------------------------------
# テスト 3: 15日超で督促状ステージ
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overdue_15days_stage1_notice():
    """滞納15日以内は stage=1（督促状（初回））であること"""
    pipeline = RentCollectionPipeline()
    # 期日 2026-03-27、参照日 2026-04-05 → 9日滞納
    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-04-05",
        "tenants": [
            _make_tenant("T001", "田中花子", "301", 80_000, "2026-03-27"),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("督促状（初回）"),
    ):
        result = await pipeline.run(input_data)

    assert len(result.arrears_tenants) == 1
    ar = result.arrears_tenants[0]
    assert ar["notice_stage"] == 1
    assert ar["notice_stage_label"] == "督促状（初回）"
    assert ar["overdue_days"] == 9


# ---------------------------------------------------------------------------
# テスト 4: 30日超で催告書
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overdue_30days_stage2_notice():
    """滞納16-30日は stage=2（催告書（2回目））であること"""
    pipeline = RentCollectionPipeline()
    # 期日 2026-03-27、参照日 2026-04-20 → 24日滞納
    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-04-20",
        "tenants": [
            _make_tenant("T001", "田中花子", "301", 80_000, "2026-03-27"),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("催告書（2回目）"),
    ):
        result = await pipeline.run(input_data)

    assert len(result.arrears_tenants) == 1
    ar = result.arrears_tenants[0]
    assert ar["notice_stage"] == 2
    assert ar["notice_stage_label"] == "催告書（2回目）"
    assert ar["overdue_days"] == 24


# ---------------------------------------------------------------------------
# テスト 5: 滞納損害金の計算正確性
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_late_fee_calculated():
    """滞納損害金が正しく計算されること（年6%日割り）"""
    pipeline = RentCollectionPipeline()
    monthly_rent = 100_000
    overdue_days = 10
    # 期日 2026-03-27 から 10日後
    reference_date_str = "2026-04-06"

    input_data = {
        "property_name": "テストマンション",
        "reference_date": reference_date_str,
        "tenants": [
            _make_tenant("T001", "テスト太郎", "101", monthly_rent, "2026-03-27"),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("督促状（初回）"),
    ):
        result = await pipeline.run(input_data)

    ar = result.arrears_tenants[0]
    expected_late_fee = int(monthly_rent * LATE_PAYMENT_RATE_DAILY * overdue_days)
    assert ar["late_fee"] == expected_late_fee
    assert ar["total_overdue"] == monthly_rent + expected_late_fee
    assert result.total_arrears_amount == monthly_rent + expected_late_fee


# ---------------------------------------------------------------------------
# テスト 6: 全5ステップ確認
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_5_steps_executed():
    """パイプライン実行後に全5ステップが steps_executed に含まれること"""
    pipeline = RentCollectionPipeline()
    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-03-31",
        "tenants": [
            _make_tenant("T001", "田中花子", "301", 80_000, "2026-03-27"),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("督促状（初回）"),
    ):
        result = await pipeline.run(input_data)

    expected_steps = [
        "tenant_reader",
        "payment_checker",
        "arrears_calculator",
        "notice_drafter",
        "output_validator",
    ]
    assert result.steps_executed == expected_steps


# ---------------------------------------------------------------------------
# テスト 7: 一部入金（不足）の検出
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_payment_detected():
    """一部入金（不足分）のテナントが滞納として正しく検出されること"""
    pipeline = RentCollectionPipeline()
    monthly_rent = 80_000
    partial_amount = 50_000  # 30,000円不足
    shortage = monthly_rent - partial_amount

    input_data = {
        "property_name": "テストマンション",
        "reference_date": "2026-03-31",
        "tenants": [
            _make_tenant(
                "T001", "田中花子", "301",
                monthly_rent, "2026-03-27",
                actual_payment_date="2026-03-25",
                actual_payment_amount=partial_amount,
            ),
        ],
    }

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        new_callable=AsyncMock,
        return_value=_mock_draft("督促状（初回）"),
    ):
        result = await pipeline.run(input_data)

    assert len(result.arrears_tenants) == 1
    ar = result.arrears_tenants[0]
    assert ar["status"] == "partial"
    assert ar["shortage"] == shortage
    # 一部払い後に期日超過しているため滞納損害金が発生
    assert ar["late_fee"] >= 0
    assert ar["total_overdue"] >= shortage


# ---------------------------------------------------------------------------
# テスト 8: 複数入居者処理
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_properties_processed():
    """複数テナントの混在（入金済み・未払い・一部払い）が正しく処理されること"""
    pipeline = RentCollectionPipeline()
    input_data = {
        "property_name": "複合テストマンション",
        "reference_date": "2026-04-10",
        "tenants": [
            # 入金済み
            _make_tenant("T001", "佐藤一郎", "101", 70_000, "2026-03-27",
                         actual_payment_date="2026-03-26", actual_payment_amount=70_000),
            # 未払い（14日滞納 → stage 1）
            _make_tenant("T002", "田中花子", "201", 80_000, "2026-03-27"),
            # 一部払い（30,000円不足 → stage 1）
            _make_tenant("T003", "山田次郎", "301", 90_000, "2026-03-27",
                         actual_payment_date="2026-03-30", actual_payment_amount=60_000),
            # 入金済み
            _make_tenant("T004", "鈴木三郎", "401", 65_000, "2026-03-27",
                         actual_payment_date="2026-03-27", actual_payment_amount=65_000),
        ],
    }

    call_count = 0

    async def mock_drafter(document_type, context, company_id=None, **kwargs):
        nonlocal call_count
        call_count += 1
        return _mock_draft(document_type)

    with patch(
        "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_message_drafter",
        side_effect=mock_drafter,
    ):
        result = await pipeline.run(input_data)

    assert result.total_tenants == 4
    assert result.paid_count == 2
    assert len(result.arrears_tenants) == 2  # T002, T003
    assert len(result.notices_required) == 2  # 各滞納テナントに1通
    assert result.total_arrears_amount > 0
    # run_message_drafter が滞納テナント数分呼ばれること
    assert call_count == 2

    # 各滞納テナントのIDが含まれること
    arrears_ids = {ar["tenant_id"] for ar in result.arrears_tenants}
    assert "T002" in arrears_ids
    assert "T003" in arrears_ids
