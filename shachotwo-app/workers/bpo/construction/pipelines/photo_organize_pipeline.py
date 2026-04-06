"""
建設業 工事写真整理パイプライン（マイクロエージェント版）

Steps:
  Step 1: photo_reader          写真メタデータ読み込み
  Step 2: category_classifier   工種・施工段階の自動分類（ルールベース）
  Step 3: sequence_checker      撮影順序・必要写真の充足チェック
  Step 4: report_generator      工事写真台帳データ生成（run_document_generator使用）
  Step 5: output_validator      台帳バリデーション
"""
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 工種・施工段階キーワードマップ
PHOTO_CATEGORIES: dict[str, list[str]] = {
    "着工前": ["着工前", "before", "施工前", "起工"],
    "掘削": ["掘削", "切削", "excavat"],
    "型枠": ["型枠", "formwork"],
    "コンクリート打設": ["打設", "生コン", "コンクリート", "打ち込み"],
    "埋戻": ["埋戻", "埋め戻し", "backfill"],
    "舗装": ["舗装", "As", "アスファルト"],
    "完成": ["完成", "完了", "after", "竣工"],
    "その他": [],  # デフォルト
}

# 公共土木で必要なフェーズ
REQUIRED_PHASES: dict[str, list[str]] = {
    "public_civil": ["着工前", "完成"],
}

REQUIRED_FIELDS = ["photo_count", "categories", "sequence_summary"]

PHOTO_EXTENSIONS = {".jpg", ".png", ".JPG", ".PNG"}


@dataclass
class StepResult:
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


@dataclass
class PhotoOrganizePipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    missing_photos: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 工事写真整理パイプライン",
            f"  ステップ: {len(self.steps)}/5",
            f"  コスト: \u00a5{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        if self.missing_photos:
            lines.append(f"  不足フェーズ: {', '.join(self.missing_photos)}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_photo_organize_pipeline(
    company_id: str,
    input_data: dict[str, Any],  # {"photos": list} or {"photo_dir": str}
    site_id: str | None = None,
    project_type: str = "public_civil",
) -> PhotoOrganizePipelineResult:
    """
    建設業 工事写真整理パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"photos": list[dict]} または {"photo_dir": str}
                    photos 形式: [{"filename": str, "taken_at": str, "description": str}]
        site_id: 工事現場ID
        project_type: "public_civil" など。必要フェーズの判定に使用
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "site_id": site_id,
        "project_type": project_type,
    }
    missing_photos: list[str] = []

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no,
            step_name=step_name,
            agent_name=agent_name,
            success=out.success,
            result=out.result,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
            warning=warn,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> PhotoOrganizePipelineResult:
        return PhotoOrganizePipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
            missing_photos=missing_photos,
        )

    # ─── Step 1: photo_reader ────────────────────────────────────────────
    s1_start = int(time.time() * 1000)
    photos: list[dict[str, Any]] = []

    if "photos" in input_data:
        photos = list(input_data["photos"])
        s1_result = {"source": "direct_input", "photo_count": len(photos)}
        s1_confidence = 1.0
    elif "photo_dir" in input_data:
        photo_dir: str = input_data["photo_dir"]
        try:
            filenames = [
                f for f in os.listdir(photo_dir)
                if os.path.splitext(f)[1] in PHOTO_EXTENSIONS
            ]
            photos = [
                {"filename": f, "taken_at": "", "description": ""}
                for f in sorted(filenames)
            ]
            s1_result = {"source": "directory", "photo_count": len(photos), "dir": photo_dir}
            s1_confidence = 1.0
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="photo_reader",
                success=False,
                result={"error": str(e)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_start,
            )
            _add_step(1, "photo_reader", "photo_reader", s1_out)
            return _fail("photo_reader")
    else:
        s1_out = MicroAgentOutput(
            agent_name="photo_reader",
            success=False,
            result={"error": "photos または photo_dir が必要です"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
        _add_step(1, "photo_reader", "photo_reader", s1_out)
        return _fail("photo_reader")

    if not photos:
        s1_out = MicroAgentOutput(
            agent_name="photo_reader",
            success=False,
            result={"error": "写真が0件です"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
        _add_step(1, "photo_reader", "photo_reader", s1_out)
        return _fail("photo_reader")

    s1_out = MicroAgentOutput(
        agent_name="photo_reader",
        success=True,
        result=s1_result,
        confidence=s1_confidence,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "photo_reader", "photo_reader", s1_out)
    context["photos"] = photos

    # ─── Step 2: category_classifier ─────────────────────────────────────
    s2_start = int(time.time() * 1000)

    def _classify_photo(photo: dict[str, Any]) -> str:
        """ファイル名と説明文のキーワードで工種・施工段階を分類する。"""
        target = (photo.get("filename", "") + " " + photo.get("description", "")).lower()
        for category, keywords in PHOTO_CATEGORIES.items():
            if category == "その他":
                continue
            for kw in keywords:
                if kw.lower() in target:
                    return category
        return "その他"

    classified_photos: list[dict[str, Any]] = []
    categories: dict[str, int] = {}
    for photo in photos:
        category = _classify_photo(photo)
        enriched = {**photo, "category": category}
        classified_photos.append(enriched)
        categories[category] = categories.get(category, 0) + 1

    s2_out = MicroAgentOutput(
        agent_name="category_classifier",
        success=True,
        result={"photos": classified_photos, "categories": categories},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s2_start,
    )
    _add_step(2, "category_classifier", "category_classifier", s2_out)
    context["classified_photos"] = classified_photos
    context["categories"] = categories

    # ─── Step 3: sequence_checker ─────────────────────────────────────────
    s3_start = int(time.time() * 1000)
    required_phases = REQUIRED_PHASES.get(project_type, REQUIRED_PHASES["public_civil"])
    phase_counts: dict[str, int] = {phase: categories.get(phase, 0) for phase in required_phases}
    missing_photos = [phase for phase in required_phases if categories.get(phase, 0) == 0]

    total_phases = len(required_phases)
    covered_phases = total_phases - len(missing_photos)

    s3_out = MicroAgentOutput(
        agent_name="sequence_checker",
        success=True,
        result={
            "required_phases": required_phases,
            "phase_counts": phase_counts,
            "missing_phases": missing_photos,
            "covered_phases": covered_phases,
            "total_phases": total_phases,
        },
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s3_start,
    )
    _add_step(3, "sequence_checker", "sequence_checker", s3_out)
    context["missing_phases"] = missing_photos
    context["phase_counts"] = phase_counts

    # ─── Step 4: report_generator ─────────────────────────────────────────
    sequence_summary = f"{total_phases}フェーズ中{covered_phases}フェーズ撮影済み"
    payload = {
        "template": "工事写真台帳",
        "variables": {
            "site_id": site_id or "未指定",
            "photo_count": len(classified_photos),
            "categories": categories,
            "missing_phases": missing_photos,
            "sequence_summary": sequence_summary,
        },
    }
    gen_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload=payload,
        context=context,
    ))
    _add_step(4, "report_generator", "document_generator", gen_out)
    if not gen_out.success:
        return _fail("report_generator")
    context["generated_report"] = gen_out.result

    # ─── Step 5: output_validator ─────────────────────────────────────────
    doc = {
        "photo_count": len(classified_photos),
        "categories": categories,
        "sequence_summary": sequence_summary,
        "site_id": site_id or "未指定",
        "missing_phases": missing_photos,
        "phase_counts": phase_counts,
    }
    if gen_out.result.get("content"):
        doc["report_content"] = gen_out.result["content"]

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": doc,
            "required_fields": REQUIRED_FIELDS,
        },
        context=context,
    ))
    _add_step(5, "output_validator", "output_validator", val_out)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"photo_organize_pipeline complete: project_type={project_type}, "
        f"photos={len(classified_photos)}, missing={missing_photos}, {total_duration}ms"
    )

    return PhotoOrganizePipelineResult(
        success=True,
        steps=steps,
        final_output=doc,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        missing_photos=missing_photos,
    )
