"""建設業 工事写真整理パイプライン テスト。"""
import pytest
from unittest.mock import AsyncMock, patch

from workers.bpo.construction.pipelines.photo_organize_pipeline import (
    run_photo_organize_pipeline,
    PhotoOrganizePipelineResult,
    PHOTO_CATEGORIES,
    REQUIRED_PHASES,
)

COMPANY_ID = "test-company-001"

# テスト用写真リスト（着工前・掘削・完成を含む）
MOCK_PHOTOS_FULL = [
    {"filename": "001_着工前.jpg", "taken_at": "2025-04-01T09:00:00", "description": "着工前の状況"},
    {"filename": "002_掘削工事.jpg", "taken_at": "2025-04-10T10:00:00", "description": "掘削作業中"},
    {"filename": "003_型枠設置.jpg", "taken_at": "2025-04-15T11:00:00", "description": "型枠を設置した"},
    {"filename": "004_コンクリート打設.jpg", "taken_at": "2025-04-20T14:00:00", "description": "生コン打ち込み"},
    {"filename": "005_完成.jpg", "taken_at": "2025-05-30T15:00:00", "description": "工事完了後"},
]

# 着工前写真なし（missing_photos に入るケース）
MOCK_PHOTOS_NO_BEFORE = [
    {"filename": "001_掘削工事.jpg", "taken_at": "2025-04-10T10:00:00", "description": "掘削作業中"},
    {"filename": "002_完成.jpg", "taken_at": "2025-05-30T15:00:00", "description": "竣工写真"},
]

# 完成写真なし
MOCK_PHOTOS_NO_AFTER = [
    {"filename": "001_着工前.jpg", "taken_at": "2025-04-01T09:00:00", "description": "起工時の状況"},
    {"filename": "002_掘削.jpg", "taken_at": "2025-04-10T10:00:00", "description": "掘削"},
]


def _mock_gen_out():
    """run_document_generator の成功レスポンスを返す AsyncMock。"""
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"content": "工事写真台帳サンプル", "format": "text", "char_count": 12},
        confidence=0.9,
        cost_yen=1.5,
        duration_ms=200,
    )


def _mock_val_out():
    """run_output_validator の成功レスポンスを返す AsyncMock。"""
    from workers.micro.models import MicroAgentOutput
    return MicroAgentOutput(
        agent_name="output_validator",
        success=True,
        result={"valid": True, "missing": [], "empty": [], "type_errors": [], "warnings": []},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=5,
    )


class TestPhotoOrganizePipelineHappyPath:
    """正常系テスト"""

    @pytest.mark.asyncio
    async def test_photos_list_input_completes_5_steps(self):
        """写真リスト直渡しで全5ステップが実行され正常完了する"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result: PhotoOrganizePipelineResult = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
                site_id="site-001",
                project_type="public_civil",
            )

        assert result.success is True
        assert result.failed_step is None
        assert len(result.steps) == 5
        # ステップ名の確認
        step_names = [s.step_name for s in result.steps]
        assert step_names == [
            "photo_reader",
            "category_classifier",
            "sequence_checker",
            "report_generator",
            "output_validator",
        ]
        assert result.total_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_all_5_steps_are_executed(self):
        """全5ステップが実行されること（ステップ番号の連続性確認）"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
            )

        assert len(result.steps) == 5
        for i, step in enumerate(result.steps, start=1):
            assert step.step_no == i

    @pytest.mark.asyncio
    async def test_total_cost_is_sum_of_steps(self):
        """total_cost_yen が各ステップの合計と一致する"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
            )

        step_total = sum(s.cost_yen for s in result.steps)
        assert abs(result.total_cost_yen - step_total) < 0.001

    @pytest.mark.asyncio
    async def test_final_output_contains_required_fields(self):
        """final_output に必須フィールドが含まれる"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
                site_id="site-001",
            )

        assert "photo_count" in result.final_output
        assert "categories" in result.final_output
        assert "sequence_summary" in result.final_output
        assert result.final_output["photo_count"] == len(MOCK_PHOTOS_FULL)
        assert result.final_output["site_id"] == "site-001"


class TestPhotoOrganizePipelineMissingPhotos:
    """不足写真アラートのテスト"""

    @pytest.mark.asyncio
    async def test_missing_before_photo_triggers_alert(self):
        """着工前写真がない場合、missing_photos に '着工前' が含まれる"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_NO_BEFORE},
                project_type="public_civil",
            )

        assert result.success is True
        assert "着工前" in result.missing_photos

    @pytest.mark.asyncio
    async def test_missing_after_photo_triggers_alert(self):
        """完成写真がない場合、missing_photos に '完成' が含まれる"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_NO_AFTER},
                project_type="public_civil",
            )

        assert result.success is True
        assert "完成" in result.missing_photos

    @pytest.mark.asyncio
    async def test_all_required_phases_present_no_alert(self):
        """着工前・完成が両方あれば missing_photos は空"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
                project_type="public_civil",
            )

        assert result.success is True
        assert result.missing_photos == []


class TestCategoryClassifier:
    """カテゴリ分類のテスト"""

    @pytest.mark.asyncio
    async def test_category_classification_is_correct(self):
        """ファイル名・説明文のキーワードで正しくカテゴリが付与される"""
        photos = [
            {"filename": "001_着工前現況.jpg", "taken_at": "", "description": ""},
            {"filename": "002_掘削.jpg", "taken_at": "", "description": ""},
            {"filename": "003_型枠.jpg", "taken_at": "", "description": ""},
            {"filename": "004_生コン.jpg", "taken_at": "", "description": "コンクリート打設"},
            {"filename": "005_埋戻.jpg", "taken_at": "", "description": ""},
            {"filename": "006_アスファルト.jpg", "taken_at": "", "description": "舗装工事"},
            {"filename": "007_完了写真.jpg", "taken_at": "", "description": "竣工"},
            {"filename": "008_misc.jpg", "taken_at": "", "description": "その他作業"},
        ]
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": photos},
            )

        step2 = result.steps[1]  # category_classifier
        assert step2.step_name == "category_classifier"
        assert step2.success is True

        categories: dict[str, int] = step2.result["categories"]
        assert categories.get("着工前", 0) >= 1
        assert categories.get("掘削", 0) >= 1
        assert categories.get("型枠", 0) >= 1
        assert categories.get("コンクリート打設", 0) >= 1
        assert categories.get("埋戻", 0) >= 1
        assert categories.get("舗装", 0) >= 1
        assert categories.get("完成", 0) >= 1

    @pytest.mark.asyncio
    async def test_unknown_filename_falls_back_to_sonota(self):
        """キーワードに一致しないファイルは 'その他' に分類される"""
        photos = [
            {"filename": "IMG_9999.jpg", "taken_at": "", "description": ""},
        ]
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": photos},
            )

        step2 = result.steps[1]
        classified = step2.result["photos"]
        assert classified[0]["category"] == "その他"

    @pytest.mark.asyncio
    async def test_category_counts_match_photo_list(self):
        """categories の合計枚数が写真総数と一致する"""
        photos = [
            {"filename": "before.jpg", "taken_at": "", "description": "着工前"},
            {"filename": "after.jpg", "taken_at": "", "description": "完成"},
            {"filename": "misc.jpg", "taken_at": "", "description": ""},
        ]
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": photos},
            )

        step2 = result.steps[1]
        categories: dict[str, int] = step2.result["categories"]
        assert sum(categories.values()) == len(photos)


class TestPhotoOrganizePipelineErrors:
    """エラーハンドリングのテスト"""

    @pytest.mark.asyncio
    async def test_empty_photos_list_fails(self):
        """写真リストが空なら photo_reader で失敗する"""
        result = await run_photo_organize_pipeline(
            company_id=COMPANY_ID,
            input_data={"photos": []},
        )

        assert result.success is False
        assert result.failed_step == "photo_reader"

    @pytest.mark.asyncio
    async def test_no_input_fails(self):
        """photos も photo_dir も指定されない場合は photo_reader で失敗する"""
        result = await run_photo_organize_pipeline(
            company_id=COMPANY_ID,
            input_data={},
        )

        assert result.success is False
        assert result.failed_step == "photo_reader"

    @pytest.mark.asyncio
    async def test_invalid_photo_dir_fails(self):
        """存在しない photo_dir は photo_reader で失敗する"""
        result = await run_photo_organize_pipeline(
            company_id=COMPANY_ID,
            input_data={"photo_dir": "/nonexistent/path/photos"},
        )

        assert result.success is False
        assert result.failed_step == "photo_reader"

    @pytest.mark.asyncio
    async def test_report_generator_failure_stops_pipeline(self):
        """report_generator が失敗した場合、パイプラインが停止する"""
        from workers.micro.models import MicroAgentOutput
        failed_gen_out = MicroAgentOutput(
            agent_name="document_generator",
            success=False,
            result={"error": "LLM接続エラー"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=100,
        )
        with patch(
            "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
            new_callable=AsyncMock,
            return_value=failed_gen_out,
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
            )

        assert result.success is False
        assert result.failed_step == "report_generator"
        # Step 4 まで実行されている（1:photo_reader, 2:classifier, 3:sequence, 4:report_generator）
        assert len(result.steps) == 4


class TestSequenceSummary:
    """sequence_summary 文字列フォーマットのテスト"""

    @pytest.mark.asyncio
    async def test_sequence_summary_format(self):
        """sequence_summary が '{N}フェーズ中{M}フェーズ撮影済み' 形式になる"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_FULL},
                project_type="public_civil",
            )

        summary = result.final_output["sequence_summary"]
        # public_civil では必須フェーズが2つ（着工前・完成）、MOCK_PHOTOS_FULL は両方含む
        assert "フェーズ中" in summary
        assert "フェーズ撮影済み" in summary
        # 2フェーズ中2フェーズ撮影済み
        assert summary == "2フェーズ中2フェーズ撮影済み"

    @pytest.mark.asyncio
    async def test_sequence_summary_partial_coverage(self):
        """一部フェーズのみ撮影済みの場合の sequence_summary"""
        with (
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_document_generator",
                new_callable=AsyncMock,
                return_value=_mock_gen_out(),
            ),
            patch(
                "workers.bpo.construction.pipelines.photo_organize_pipeline.run_output_validator",
                new_callable=AsyncMock,
                return_value=_mock_val_out(),
            ),
        ):
            result = await run_photo_organize_pipeline(
                company_id=COMPANY_ID,
                input_data={"photos": MOCK_PHOTOS_NO_BEFORE},
                project_type="public_civil",
            )

        # 着工前なし → 2フェーズ中1フェーズ撮影済み
        assert result.final_output["sequence_summary"] == "2フェーズ中1フェーズ撮影済み"
