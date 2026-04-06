"""士業事務所 期限管理AIパイプライン テスト"""
import pytest
from datetime import date, timedelta

from workers.bpo.professional.pipelines.deadline_mgmt_pipeline import (
    DeadlineMgmtPipeline,
    DeadlineMgmtPipelineResult,
    PRIORITY_LEVELS,
    CASE_TYPES,
)

# ---------------------------------------------------------------------------
# テスト用フィクスチャ
# ---------------------------------------------------------------------------

REFERENCE_DATE = "2026-03-20"
_REF = date.fromisoformat(REFERENCE_DATE)


def _make_case(
    case_id: str,
    case_type: str,
    days_delta: int,
    status: str = "pending",
    client_name: str = "テスト株式会社",
    assigned_staff: str = "山田太郎",
) -> dict:
    """テスト用案件を生成するヘルパー。"""
    deadline = _REF + timedelta(days=days_delta)
    return {
        "case_id": case_id,
        "client_name": client_name,
        "case_type": case_type,
        "description": f"{case_type} テスト案件",
        "deadline_date": deadline.isoformat(),
        "assigned_staff": assigned_staff,
        "status": status,
    }


@pytest.fixture
def pipeline() -> DeadlineMgmtPipeline:
    return DeadlineMgmtPipeline()


# ---------------------------------------------------------------------------
# 1. 直渡しで全4ステップ正常完了
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_direct_cases_success(pipeline: DeadlineMgmtPipeline) -> None:
    """直渡しの案件リストで全4ステップが正常に完了すること。"""
    input_data = {
        "cases": [
            _make_case("C001", "法人登記", 5),    # critical
            _make_case("C002", "確定申告", 60),   # medium
        ],
        "reference_date": REFERENCE_DATE,
        "office_name": "山田行政書士事務所",
    }

    result: DeadlineMgmtPipelineResult = await pipeline.run(input_data)

    assert result.is_valid is True
    assert len(result.active_cases) == 2
    assert len(result.analyzed_cases) == 2
    assert len(result.task_list) == 2
    assert result.validation_errors == []
    assert result.office_name == "山田行政書士事務所"
    assert result.reference_date == _REF


# ---------------------------------------------------------------------------
# 2. 期限超過案件の検出
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overdue_case_detected(pipeline: DeadlineMgmtPipeline) -> None:
    """期限が過ぎている案件が overdue として検出されること。"""
    input_data = {
        "cases": [
            _make_case("C_OD", "法人登記", -5),  # 5日前に期限切れ
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert len(result.overdue_cases) == 1
    assert result.overdue_cases[0]["case_id"] == "C_OD"
    assert result.overdue_cases[0]["priority"] == "overdue"
    assert result.overdue_cases[0]["days_to_deadline"] == -5


# ---------------------------------------------------------------------------
# 3. 7日以内で critical 判定
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_critical_7days_case(pipeline: DeadlineMgmtPipeline) -> None:
    """残り7日以内の案件が critical と判定されること。"""
    input_data = {
        "cases": [
            _make_case("C_CRIT", "建設業許可更新", 7),   # 7日後 → critical
            _make_case("C_HIGH", "確定申告", 8),          # 8日後 → high
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    priorities = {c["case_id"]: c["priority"] for c in result.analyzed_cases}
    assert priorities["C_CRIT"] == "critical"
    assert priorities["C_HIGH"] == "high"
    # critical_cases には overdue + critical が含まれる
    assert any(c["case_id"] == "C_CRIT" for c in result.critical_cases)


# ---------------------------------------------------------------------------
# 4. 完了案件はスキップ
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completed_case_skipped(pipeline: DeadlineMgmtPipeline) -> None:
    """status == 'completed' の案件は分析対象から除外されること。"""
    input_data = {
        "cases": [
            _make_case("C_DONE", "確定申告", 10, status="completed"),
            _make_case("C_PEND", "社会保険算定", 20, status="pending"),
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert len(result.active_cases) == 1
    assert result.active_cases[0]["case_id"] == "C_PEND"
    assert all(c["case_id"] != "C_DONE" for c in result.analyzed_cases)


# ---------------------------------------------------------------------------
# 5. 全4ステップが実行されること
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_4_steps_executed(pipeline: DeadlineMgmtPipeline) -> None:
    """パイプライン実行後、steps_executed に4ステップ全てが記録されること。"""
    input_data = {
        "cases": [_make_case("C001", "法人登記", 3)],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert "case_reader" in result.steps_executed
    assert "deadline_analyzer" in result.steps_executed
    assert "task_generator" in result.steps_executed
    assert "output_validator" in result.steps_executed
    assert len(result.steps_executed) == 4


# ---------------------------------------------------------------------------
# 6. 優先度の順序が正しいこと（overdue > critical > high > medium > low）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_priority_ordering(pipeline: DeadlineMgmtPipeline) -> None:
    """analyzed_cases が overdue → critical → high → medium → low の順にソートされること。"""
    input_data = {
        "cases": [
            _make_case("LOW",      "確定申告",           120),  # low
            _make_case("MEDIUM",   "確定申告",           60),   # medium
            _make_case("HIGH",     "確定申告",           20),   # high
            _make_case("CRITICAL", "法人登記",           3),    # critical
            _make_case("OVERDUE",  "法人登記",           -2),   # overdue
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    priorities = [c["priority"] for c in result.analyzed_cases]
    priority_order = ["overdue", "critical", "high", "medium", "low"]

    # 後続の優先度が前の優先度より先に来ていないことを確認
    last_rank = -1
    for p in priorities:
        rank = priority_order.index(p)
        assert rank >= last_rank, (
            f"優先度順序が崩れている: {priorities}"
        )
        last_rank = rank


# ---------------------------------------------------------------------------
# 7. 複数案件種別の処理
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_case_types(pipeline: DeadlineMgmtPipeline) -> None:
    """CASE_TYPES に定義された複数種別の案件を正しく処理できること。"""
    input_data = {
        "cases": [
            _make_case("C1", "法人登記",           5,   client_name="A社"),
            _make_case("C2", "商標登録",           40,  client_name="B社"),
            _make_case("C3", "確定申告",           -1,  client_name="C社"),
            _make_case("C4", "社会保険算定",       80,  client_name="D社"),
            _make_case("C5", "労働保険申告",       100, client_name="E社"),
            _make_case("C6", "建設業許可更新",     15,  client_name="F社"),
            _make_case("C7", "外国人在留資格更新", 0,   client_name="G社"),
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert len(result.analyzed_cases) == 7
    assert result.is_valid is True

    # 各案件に priority が付与されていること
    for case in result.analyzed_cases:
        assert "priority" in case
        assert case["priority"] in PRIORITY_LEVELS


# ---------------------------------------------------------------------------
# 8. タスクジェネレーターが呼ばれること（task_list にデータが存在する）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_generator_called(pipeline: DeadlineMgmtPipeline) -> None:
    """task_generator が呼ばれ、各案件にタスクリストが生成されること。"""
    input_data = {
        "cases": [
            _make_case("C001", "法人登記",  5),
            _make_case("C002", "確定申告", 50),
        ],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert len(result.task_list) == 2

    for entry in result.task_list:
        assert "tasks" in entry
        assert len(entry["tasks"]) > 0, f"案件 {entry['case_id']} のタスクが空。"

        for task in entry["tasks"]:
            assert "task" in task
            assert "document" in task
            assert "responsible" in task

    # 法人登記には登記申請書タスクが含まれること
    houjin_entry = next(e for e in result.task_list if e["case_id"] == "C001")
    task_names = [t["task"] for t in houjin_entry["tasks"]]
    assert any("登記申請書" in t for t in task_names)


# ---------------------------------------------------------------------------
# 追加: 空案件リストでも valid であること
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_cases_is_valid(pipeline: DeadlineMgmtPipeline) -> None:
    """案件が0件の場合でもパイプラインが正常終了すること。"""
    input_data = {
        "cases": [],
        "reference_date": REFERENCE_DATE,
    }

    result = await pipeline.run(input_data)

    assert result.is_valid is True
    assert result.analyzed_cases == []
    assert result.task_list == []
    assert result.overdue_cases == []
    assert result.critical_cases == []
    assert result.upcoming_deadlines == []


# ---------------------------------------------------------------------------
# 追加: reference_date 省略時に today が使われること
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reference_date_defaults_to_today(pipeline: DeadlineMgmtPipeline) -> None:
    """reference_date 省略時に today() が基準日として使われること。"""
    input_data = {
        "cases": [_make_case("C001", "法人登記", 10)],
        # reference_date を意図的に省略
    }

    result = await pipeline.run(input_data)

    assert result.reference_date == date.today()
    assert result.is_valid is True
