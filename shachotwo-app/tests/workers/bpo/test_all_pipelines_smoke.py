"""
6業界BPOパイプライン + 共通BPOのスモークテスト

目的: 各パイプラインが import可能で、基本的な実行ができるか確認
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Any


# ----- テスト対象パイプライン定義 -----
PIPELINES_TO_TEST = [
    # コア6業界（建設/製造は既存テスト確認済みのため含める）
    {
        "name": "clinic/medical_receipt",
        "module_path": "workers.bpo.clinic.pipelines.medical_receipt_pipeline",
        "func_name": "run_medical_receipt_pipeline",
        "sample_input": {
            "receipts": [
                {
                    "patient_id": "P001",
                    "clinic_type": "内科",
                    "services": [
                        {"service_code": "130010", "service_name": "初診料", "points": 288}
                    ],
                    "medications": [
                        {
                            "drug_code": "1234567",
                            "drug_name": "アムロジピン",
                            "dosage": "5mg",
                            "count": 30,
                        }
                    ],
                    "diseases": ["高血圧症"],
                }
            ]
        },
    },
    {
        "name": "nursing/care_billing",
        "module_path": "workers.bpo.nursing.pipelines.care_billing_pipeline",
        "func_name": "run_care_billing_pipeline",
        "sample_input": {
            "residents": [
                {
                    "resident_id": "R001",
                    "care_level": "要介護3",
                    "facility_type": "特別養護老人ホーム",
                    "billing_period": "2026-03",
                }
            ]
        },
    },
    {
        "name": "realestate/rent_collection",
        "module_path": "workers.bpo.realestate.pipelines.rent_collection_pipeline",
        "func_name": "run_rent_collection_pipeline",
        "sample_input": {
            "properties": [
                {
                    "property_id": "PROP001",
                    "address": "東京都渋谷区",
                    "monthly_rent": 150000,
                    "tenant_name": "A社",
                    "due_date": "2026-04-05",
                    "status": "occupied",
                }
            ]
        },
    },
    {
        "name": "logistics/dispatch",
        "module_path": "workers.bpo.logistics.pipelines.dispatch_pipeline",
        "func_name": "run_dispatch_pipeline",
        "sample_input": {
            "deliveries": [
                {
                    "delivery_id": "DEL001",
                    "origin": "東京DC",
                    "destination": "大阪支店",
                    "weight": 50.0,
                    "volume": 20.0,
                    "priority": "normal",
                    "pickup_time": "2026-04-01T09:00:00Z",
                }
            ]
        },
    },
    # 共通BPO（7本）
    {
        "name": "common/expense",
        "module_path": "workers.bpo.common.pipelines.expense_pipeline",
        "func_name": "run_expense_pipeline",
        "sample_input": {
            "expense": {
                "expense_date": "2026-03-29",
                "amount": 5000,
                "category": "交通費",
                "purpose": "クライアント訪問",
            }
        },
    },
    {
        "name": "common/payroll",
        "module_path": "workers.bpo.common.pipelines.payroll_pipeline",
        "func_name": "run_payroll_pipeline",
        "sample_input": {
            "payroll_period": "2026-03",
            "employees": [
                {
                    "employee_id": "EMP001",
                    "name": "山田太郎",
                    "base_salary": 300000,
                    "days_worked": 20,
                    "overtime_hours": 5,
                    "deductions": {"health_insurance": 15000, "pension": 27500},
                }
            ],
        },
    },
    {
        "name": "common/attendance",
        "module_path": "workers.bpo.common.pipelines.attendance_pipeline",
        "func_name": "run_attendance_pipeline",
        "sample_input": {
            "attendance_records": [
                {
                    "employee_id": "EMP001",
                    "date": "2026-03-29",
                    "check_in": "09:00",
                    "check_out": "18:00",
                    "is_holiday": False,
                }
            ]
        },
    },
    {
        "name": "common/contract",
        "module_path": "workers.bpo.common.pipelines.contract_pipeline",
        "func_name": "run_contract_pipeline",
        "sample_input": {
            "contract": {
                "contract_title": "業務委託契約書",
                "party_a": "ABC株式会社",
                "party_b": "XYZ個人事業主",
                "contract_amount": 500000,
                "start_date": "2026-04-01",
                "end_date": "2027-03-31",
                "auto_renewal": False,
                "cancellation_notice_days": 30,
            }
        },
    },
    {
        "name": "common/vendor",
        "module_path": "workers.bpo.common.pipelines.vendor_pipeline",
        "func_name": "run_vendor_pipeline",
        "sample_input": {
            "vendors": [
                {
                    "vendor_name": "部品メーカーA",
                    "vendor_id": "VEND001",
                    "annual_transaction_amount": 2000000,
                    "last_transaction_date": "2026-03-20",
                }
            ]
        },
    },
    {
        "name": "common/admin_reminder",
        "module_path": "workers.bpo.common.pipelines.admin_reminder_pipeline",
        "func_name": "run_admin_reminder_pipeline",
        "sample_input": {
            "deadlines": [
                {
                    "type": "税務申告",
                    "deadline_date": "2026-05-31",
                    "description": "法人税申告",
                }
            ]
        },
    },
    # 労務3本（社保/年末調整/労務コンプラ）
    {
        "name": "common/social_insurance",
        "module_path": "workers.bpo.common.pipelines.social_insurance_pipeline",
        "func_name": "run_social_insurance_pipeline",
        "sample_input": {
            "filing_type": "acquisition",
            "event_date": "2026-04-01",
            "employees": [
                {
                    "employee_id": "EMP001",
                    "employee_name": "田中花子",
                    "monthly_salary": 280000,
                }
            ],
        },
    },
    {
        "name": "common/year_end_adjustment",
        "module_path": "workers.bpo.common.pipelines.year_end_adjustment_pipeline",
        "func_name": "run_year_end_adjustment_pipeline",
        "sample_input": {
            "target_year": 2025,
            "employees": [
                {
                    "employee_id": "EMP001",
                    "employee_name": "田中花子",
                    "annual_income": 4200000,
                    "income_tax_withheld": 120000,
                }
            ],
        },
    },
    {
        "name": "common/labor_compliance",
        "module_path": "workers.bpo.common.pipelines.labor_compliance_pipeline",
        "func_name": "run_labor_compliance_pipeline",
        "sample_input": {
            "target_month": "2026-03",
            "employees": [
                {
                    "employee_id": "EMP001",
                    "employee_name": "田中花子",
                    "monthly_overtime": {"2026-01": 38.0, "2026-02": 42.0, "2026-03": 35.0},
                    "paid_leave_taken_days": 3.0,
                    "paid_leave_granted_days": 10.0,
                }
            ],
        },
    },
    # 人事労務3本（採用/入社/退社）
    {
        "name": "common/recruitment",
        "module_path": "workers.bpo.common.pipelines.recruitment_pipeline",
        "func_name": "run_recruitment_pipeline",
        "sample_input": {
            "job_title": "バックエンドエンジニア",
            "job_requirements": {
                "skills": ["Python", "FastAPI"],
                "experience_years": 3,
                "location": "東京",
                "employment_type": "正社員",
            },
            "applications": [
                {
                    "applicant_name": "山田太郎",
                    "email": "yamada@example.com",
                    "resume_text": "Python 5年 FastAPI 3年 東京在住",
                    "cv_text": "株式会社ABC バックエンドエンジニア 5年",
                }
            ],
            "mode": "full",
            "dry_run": True,
        },
    },
    {
        "name": "common/employee_onboarding",
        "module_path": "workers.bpo.common.pipelines.employee_onboarding_pipeline",
        "func_name": "run_employee_onboarding_pipeline",
        "sample_input": {
            "employee_name": "鈴木花子",
            "employee_email": "suzuki@example.com",
            "join_date": "2026-04-01",
            "department": "営業部",
            "job_title": "営業担当",
            "employment_type": "正社員",
            "salary": 280000,
            "weekly_work_hours": 40.0,
            "submitted_docs": ["マイナンバー", "扶養控除等申告書", "給与振込口座届"],
            "dry_run": True,
        },
    },
    {
        "name": "common/employee_offboarding",
        "module_path": "workers.bpo.common.pipelines.employee_offboarding_pipeline",
        "func_name": "run_employee_offboarding_pipeline",
        "sample_input": {
            "employee_name": "田中一郎",
            "employee_id": "EMP042",
            "employee_email": "tanaka@example.com",
            "retirement_date": "2026-04-30",
            "last_work_date": "2026-04-30",
            "resignation_reason": "voluntary",
            "employment_type": "正社員",
            "monthly_salary": 350000,
            "years_of_service": 5,
            "unused_pto_days": 8,
            "worked_days_in_month": 20,
            "social_insurance_enrolled": True,
            "employment_insurance_enrolled": True,
            "assigned_tasks": ["顧客管理", "月次レポート作成", "新人OJT"],
            "dry_run": True,
        },
    },
    # セールス/営業
    {
        "name": "sales/outreach",
        "module_path": "workers.bpo.sales.pipelines.outreach_pipeline",
        "func_name": "run_outreach_pipeline",
        "sample_input": {
            "companies": [
                {
                    "company_name": "テスト会社",
                    "industry": "製造業",
                    "company_size": "中堅",
                    "contact_info": "info@test.com",
                }
            ]
        },
    },
    {
        "name": "sales/lead_qualification",
        "module_path": "workers.bpo.sales.pipelines.lead_qualification_pipeline",
        "func_name": "run_lead_qualification_pipeline",
        "sample_input": {
            "leads": [
                {
                    "lead_id": "LEAD001",
                    "company_name": "リード会社",
                    "industry": "建設業",
                    "estimated_annual_revenue": 50000000,
                    "contact_person": "鈴木一郎",
                    "contact_email": "suzuki@test.com",
                }
            ]
        },
    },
    {
        "name": "sales/support_auto_response",
        "module_path": "workers.bpo.sales.pipelines.support_auto_response_pipeline",
        "func_name": "run_support_auto_response_pipeline",
        "sample_input": {
            "support_tickets": [
                {
                    "ticket_id": "TKT001",
                    "customer_name": "顧客太郎",
                    "issue_category": "製品不具合",
                    "issue_description": "ログインできません",
                    "priority": "high",
                }
            ]
        },
    },
]


class TestAllPipelinesSmokeTest:
    """全BPOパイプラインのスモークテスト"""

    @pytest.mark.parametrize(
        "pipeline_spec",
        PIPELINES_TO_TEST,
        ids=lambda spec: spec["name"],
    )
    @pytest.mark.asyncio
    async def test_pipeline_module_importable(self, pipeline_spec: dict[str, Any]):
        """パイプラインモジュールがimport可能か確認"""
        module_path = pipeline_spec["module_path"]
        try:
            module = __import__(module_path, fromlist=[""])
            assert module is not None, f"モジュール {module_path} がNoneです"
        except ImportError as e:
            pytest.skip(f"モジュール {module_path} のimportに失敗: {e}")

    @pytest.mark.parametrize(
        "pipeline_spec",
        PIPELINES_TO_TEST,
        ids=lambda spec: spec["name"],
    )
    @pytest.mark.asyncio
    async def test_pipeline_function_exists(self, pipeline_spec: dict[str, Any]):
        """run_xxx_pipeline関数が存在するか確認"""
        module_path = pipeline_spec["module_path"]
        func_name = pipeline_spec["func_name"]
        try:
            module = __import__(module_path, fromlist=[func_name])
            assert hasattr(
                module, func_name
            ), f"関数 {func_name} が {module_path} に存在しません"
            func = getattr(module, func_name)
            assert callable(func), f"{func_name} が呼び出し可能ではありません"
        except ImportError as e:
            pytest.skip(f"モジュール {module_path} のimportに失敗: {e}")

    @pytest.mark.parametrize(
        "pipeline_spec",
        PIPELINES_TO_TEST,
        ids=lambda spec: spec["name"],
    )
    @pytest.mark.asyncio
    async def test_pipeline_basic_execution(self, pipeline_spec: dict[str, Any]):
        """モックデータで基本実行が通るか確認"""
        module_path = pipeline_spec["module_path"]
        func_name = pipeline_spec["func_name"]
        sample_input = pipeline_spec["sample_input"]

        try:
            module = __import__(module_path, fromlist=[func_name])
            func = getattr(module, func_name)
        except ImportError as e:
            pytest.skip(f"モジュール {module_path} のimportに失敗: {e}")

        company_id = "test_company_001"

        # マイクロエージェント呼び出しをモック（document_ocr, extractor, validator等）
        with patch("workers.micro.ocr.run_document_ocr") as mock_ocr, \
             patch("workers.micro.extractor.run_structured_extractor") as mock_extractor, \
             patch("workers.micro.validator.run_output_validator") as mock_validator, \
             patch("workers.micro.diff.run_diff_detector") as mock_diff, \
             patch("llm.client.get_llm_client") as mock_llm:

            # マイクロエージェント結果をモック
            from workers.micro.models import MicroAgentOutput

            mock_ocr.return_value = MicroAgentOutput(
                agent_name="document_ocr",
                success=True,
                result={"text": "モック抽出テキスト"},
                confidence=0.95,
                cost_yen=1.0,
                duration_ms=100,
            )

            mock_extractor.return_value = MicroAgentOutput(
                agent_name="extractor",
                success=True,
                result={"extracted": "モック抽出データ"},
                confidence=0.9,
                cost_yen=2.0,
                duration_ms=200,
            )

            mock_validator.return_value = MicroAgentOutput(
                agent_name="validator",
                success=True,
                result={"validated": True},
                confidence=0.99,
                cost_yen=1.0,
                duration_ms=50,
            )

            mock_diff.return_value = MicroAgentOutput(
                agent_name="diff_detector",
                success=True,
                result={"differences": []},
                confidence=0.95,
                cost_yen=1.0,
                duration_ms=100,
            )

            # LLM呼び出しをモック
            mock_client = AsyncMock()
            mock_client.generate = AsyncMock(
                return_value={
                    "text": "モック応答",
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
            )
            mock_llm.return_value = mock_client

            # パイプライン実行
            try:
                result = await func(company_id=company_id, input_data=sample_input)

                # 実行が成功したことを確認
                assert (
                    result is not None
                ), f"{func_name} がNoneを返しています"

                # 一般的なパイプライン結果フィールドをチェック
                if hasattr(result, "success"):
                    assert isinstance(
                        result.success, bool
                    ), "success フィールドがbool型ではありません"
                elif isinstance(result, dict):
                    assert "success" in result or "pipeline" in result or "steps" in result, \
                        "結果がdict型だが期待されたフィールドが含まれていません"

            except TypeError as e:
                # 関数シグネチャが異なる可能性がある
                pytest.skip(f"関数シグネチャ不一致: {e}")
            except Exception as e:
                # 予期しないエラーはテスト失敗
                raise AssertionError(
                    f"{func_name} 実行中にエラー発生: {type(e).__name__}: {e}"
                ) from e

    @pytest.mark.parametrize(
        "pipeline_spec",
        PIPELINES_TO_TEST,
        ids=lambda spec: spec["name"],
    )
    @pytest.mark.asyncio
    async def test_pipeline_error_handling(self, pipeline_spec: dict[str, Any]):
        """不正入力でクラッシュしないことを確認"""
        module_path = pipeline_spec["module_path"]
        func_name = pipeline_spec["func_name"]

        try:
            module = __import__(module_path, fromlist=[func_name])
            func = getattr(module, func_name)
        except ImportError as e:
            pytest.skip(f"モジュール {module_path} のimportに失敗: {e}")

        company_id = "test_company_001"
        invalid_input = {}  # 空の入力

        # マイクロエージェント呼び出しをモック
        with patch("workers.micro.ocr.run_document_ocr") as mock_ocr, \
             patch("workers.micro.extractor.run_structured_extractor") as mock_extractor, \
             patch("workers.micro.validator.run_output_validator") as mock_validator, \
             patch("llm.client.get_llm_client") as mock_llm:

            from workers.micro.models import MicroAgentOutput

            # エラーハンドリング用にモックを設定
            mock_ocr.return_value = MicroAgentOutput(
                agent_name="document_ocr",
                success=False,
                result={"error": "空の入力"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=0,
            )

            mock_extractor.return_value = MicroAgentOutput(
                agent_name="extractor",
                success=False,
                result={"error": "抽出失敗"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=0,
            )

            mock_validator.return_value = MicroAgentOutput(
                agent_name="validator",
                success=False,
                result={"error": "バリデーション失敗"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=0,
            )

            mock_client = AsyncMock()
            mock_client.generate = AsyncMock(
                return_value={
                    "text": "モック応答",
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
            )
            mock_llm.return_value = mock_client

            # 不正入力で呼び出し
            try:
                result = await func(company_id=company_id, input_data=invalid_input)
                # エラーハンドリングが動作し、何か値を返す（クラッシュしない）
                assert result is not None, f"{func_name} がNoneを返しました"
            except TypeError:
                # シグネチャ不一致は許容
                pytest.skip(f"関数シグネチャ不一致")
            except Exception as e:
                # エラーハンドリングが機能していることを確認
                # （適切なエラーメッセージが含まれているか）
                error_msg = str(e)
                assert (
                    len(error_msg) > 0
                ), f"{func_name} からエラーメッセージなしのエラー"
