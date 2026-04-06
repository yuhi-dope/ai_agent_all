"""勤怠管理パイプライン（attendance_pipeline）のテスト。"""
import pytest
from unittest.mock import AsyncMock, patch


COMPANY_ID = "test-company-attendance"


def _make_employee(
    employee_id: str = "emp001",
    overtime_hours: float = 10.0,
    absent_days: int = 0,
    paid_leave_remaining: int = 10,
) -> dict:
    return {
        "employee_id": employee_id,
        "employee_name": f"社員{employee_id}",
        "work_days": 20,
        "work_hours": 160.0,
        "overtime_hours": overtime_hours,
        "absent_days": absent_days,
        "paid_leave_taken": 2,
        "paid_leave_remaining": paid_leave_remaining,
    }


class TestAttendancePipeline:
    # ─── テスト1: 直接employees渡しで正常完了 ───────────────────────────────
    @pytest.mark.asyncio
    async def test_direct_employees_success(self):
        """直接employees渡しで全ステップが正常完了すること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", overtime_hours=10.0),
                    _make_employee("emp002", overtime_hours=20.0),
                ]
            },
            period_year=2025,
            period_month=3,
        )

        assert result.success is True
        assert result.failed_step is None
        assert result.final_output["employee_count"] == 2
        assert result.final_output["period_year"] == 2025
        assert result.final_output["period_month"] == 3
        assert result.final_output["total_overtime_hours"] == pytest.approx(30.0)

    # ─── テスト2: 36協定超過でコンプライアンスアラートが出る ────────────────
    @pytest.mark.asyncio
    async def test_overtime_over_45h_triggers_alert(self):
        """overtime_hours > 45 の従業員がいる場合、compliance_alertsに36協定アラートが出ること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", overtime_hours=50.0),
                ]
            },
            period_year=2025,
            period_month=3,
        )

        assert result.success is True
        # Step 2 (overtime_analyzer) または Step 4 (compliance_checker) でアラートが出る
        assert any("36協定" in alert for alert in result.compliance_alerts)

    # ─── テスト3: 欠勤多い従業員でアラートが出る ────────────────────────────
    @pytest.mark.asyncio
    async def test_high_absence_triggers_alert(self):
        """absent_days > 3 の従業員がいる場合、compliance_alertsに欠勤アラートが出ること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", absent_days=5),
                ]
            },
            period_year=2025,
            period_month=3,
        )

        assert result.success is True
        assert any("欠勤" in alert and "要確認" in alert for alert in result.compliance_alerts)

    # ─── テスト4: 全5ステップが実行される ───────────────────────────────────
    @pytest.mark.asyncio
    async def test_all_5_steps_executed(self):
        """パイプラインが全5ステップを実行すること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", overtime_hours=8.0),
                ]
            },
            period_year=2025,
            period_month=4,
        )

        assert result.success is True
        assert len(result.steps) == 5
        step_names = [s.step_name for s in result.steps]
        assert "attendance_reader" in step_names
        assert "overtime_analyzer" in step_names
        assert "absence_checker" in step_names
        assert "compliance_checker" in step_names
        assert "output_validator" in step_names

    # ─── テスト5: employee_count が正しくカウントされる ─────────────────────
    @pytest.mark.asyncio
    async def test_employee_count_is_correct(self):
        """final_output の employee_count が実際の従業員数と一致すること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        employees = [_make_employee(f"emp{i:03d}") for i in range(1, 6)]
        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={"employees": employees},
            period_year=2025,
            period_month=5,
        )

        assert result.success is True
        assert result.final_output["employee_count"] == 5

    # ─── 追加テスト: 有給残日数不足でアラートが出る ─────────────────────────
    @pytest.mark.asyncio
    async def test_low_paid_leave_remaining_triggers_alert(self):
        """paid_leave_remaining < 5 の場合、compliance_alertsに有給残日数アラートが出ること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", paid_leave_remaining=2),
                ]
            },
            period_year=2025,
            period_month=3,
        )

        assert result.success is True
        assert any("有給残" in alert and "年5日取得義務" in alert for alert in result.compliance_alerts)

    # ─── 追加テスト: 平均残業40h超で過重労働リスクアラートが出る ─────────────
    @pytest.mark.asyncio
    async def test_avg_overtime_over_40h_triggers_alert(self):
        """全従業員の平均残業時間 > 40h の場合、過重労働リスクアラートが出ること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        result = await run_attendance_pipeline(
            company_id=COMPANY_ID,
            input_data={
                "employees": [
                    _make_employee("emp001", overtime_hours=42.0),
                    _make_employee("emp002", overtime_hours=44.0),
                ]
            },
            period_year=2025,
            period_month=3,
        )

        assert result.success is True
        assert any("過重労働リスク" in alert for alert in result.compliance_alerts)

    # ─── 追加テスト: CSV入力でextractorが呼ばれる ───────────────────────────
    @pytest.mark.asyncio
    async def test_csv_input_calls_structured_extractor(self):
        """csv_text 入力時に run_structured_extractor が呼び出されること。"""
        from workers.bpo.common.pipelines.attendance_pipeline import run_attendance_pipeline

        mock_extract = AsyncMock(return_value=type("Out", (), {
            "success": True,
            "result": {
                "employees": [
                    _make_employee("emp001", overtime_hours=10.0),
                ]
            },
            "confidence": 0.95,
            "cost_yen": 3.0,
            "duration_ms": 150,
        })())

        with patch(
            "workers.bpo.common.pipelines.attendance_pipeline.run_structured_extractor",
            mock_extract,
        ):
            result = await run_attendance_pipeline(
                company_id=COMPANY_ID,
                input_data={"csv_text": "employee_id,employee_name,...\nemp001,社員emp001,..."},
                period_year=2025,
                period_month=3,
            )

        assert mock_extract.called
        assert result.success is True
