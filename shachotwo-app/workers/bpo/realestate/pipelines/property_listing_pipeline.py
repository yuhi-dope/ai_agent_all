"""不動産業 物件資料・広告作成AIパイプライン

Steps:
  Step 1: property_reader      物件情報取得・正規化（DBまたは入力から取得）
  Step 2: photo_processor      写真分類・メイン写真選定（Vision AI）
  Step 3: copy_generator       LLMでキャッチコピー・掲載文生成（300文字以内）
  Step 4: compliance_checker   公正競争規約チェック（禁止用語・必須表示バリデーション）
  Step 5: maisoku_generator    マイソクPDF生成（テンプレートエンジン）
  Step 6: portal_formatter     ポータルサイト別CSV/データ生成（SUUMO/HOME'S/at home/Yahoo!）
  Step 7: output_validator     バリデーション（記載事項完全性チェック）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.extractor import run_structured_extractor
from workers.micro.ocr import run_document_ocr
from workers.micro.generator import run_document_generator
from workers.micro.compliance import run_compliance_checker
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 不動産広告の禁止用語（不動産の表示に関する公正競争規約）
PROHIBITED_AD_WORDS = [
    "最高", "最上級", "格安", "掘出し", "完璧",
    "日本一", "業界No.1", "業界ナンバー1",
    "絶対", "完全", "必ず", "激安", "超お得",
]

# 必須表示事項
REQUIRED_AD_ITEMS = [
    "取引態様",  # 売主/代理/仲介（媒介）
    "免許番号",
    "物件所在地",
    "交通アクセス",
    "建築年月",
    "面積",
    "価格または賃料",
]

# 徒歩所要時間の算出ルール（公正競争規約: 80m=1分、端数切り上げ）
WALK_METERS_PER_MINUTE = 80

# 対応ポータルサイト
PORTAL_SITES = ["SUUMO", "HOMES", "athome", "Yahoo不動産"]


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
class PropertyListingResult:
    """物件資料・広告作成パイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 物件資料・広告作成パイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        violations = self.final_output.get("compliance_violations", [])
        lines.append(f"  規約違反: {len(violations)}件")
        portals = self.final_output.get("portal_data", {})
        lines.append(f"  ポータル掲載データ: {len(portals)}サイト分")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


def _calc_walk_time(distance_meters: int) -> int:
    """公正競争規約に基づく徒歩所要時間を計算（端数切り上げ）。"""
    import math
    return math.ceil(distance_meters / WALK_METERS_PER_MINUTE)


async def run_property_listing_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> PropertyListingResult:
    """
    物件資料・広告作成AIパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "property_id": str,            # 物件ID（DBから取得する場合）
            "property_data": dict,         # 物件情報（直接渡す場合。property_idより優先）
              # property_data の内訳:
              #   address: str, transport: str, land_area: float,
              #   building_area: float, structure: str, building_year_month: str,
              #   floor_plan: str, price: int (or rent: int),
              #   management_fee: int, zoning: str, coverage_ratio: float,
              #   floor_area_ratio: float, facilities: list[str],
              #   nearest_station: str, station_distance_meters: int,
              #   transaction_type: str,   # 売主/代理/仲介
              #   license_number: str,
            "photo_files": list[str],      # 写真ファイルパスのリスト
            "target_persona": str,         # 想定顧客層（例: "ファミリー", "単身者", "投資家"）
            "ad_type": str,                # maisoku / portal / both
        }

    Returns:
        PropertyListingResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {"company_id": company_id}

    def _add_step(
        step_no: int, step_name: str, agent_name: str, out: MicroAgentOutput
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f})"
        sr = StepResult(
            step_no=step_no, step_name=step_name, agent_name=agent_name,
            success=out.success, result=out.result, confidence=out.confidence,
            cost_yen=out.cost_yen, duration_ms=out.duration_ms, warning=warn,
        )
        steps.append(sr)
        return sr

    def _fail(step_name: str) -> PropertyListingResult:
        return PropertyListingResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: property_reader ─────────────────────────────────────────
    s1_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": str(input_data.get("property_data", input_data)),
            "schema": {
                "address": "str",
                "transport": "str",
                "land_area": "float",
                "building_area": "float",
                "structure": "str",
                "building_year_month": "str",
                "floor_plan": "str",
                "price_or_rent": "int",
                "nearest_station": "str",
                "station_distance_meters": "int",
                "transaction_type": "str",
                "license_number": "str",
                "facilities": "list",
            },
            "purpose": "物件情報の正規化・必須表示項目の確認",
        },
        context=context,
    ))
    _add_step(1, "property_reader", "structured_extractor", s1_out)
    if not s1_out.success:
        return _fail("property_reader")
    property_data = {
        **(input_data.get("property_data") or {}),
        **s1_out.result,
    }
    # 徒歩所要時間を計算
    station_distance = int(property_data.get("station_distance_meters", 800))
    property_data["station_walk_minutes"] = _calc_walk_time(station_distance)
    context["property_data"] = property_data

    # ─── Step 2: photo_processor ─────────────────────────────────────────
    photo_files: list[str] = input_data.get("photo_files", [])
    if photo_files:
        s2_out = await run_document_ocr(MicroAgentInput(
            company_id=company_id,
            agent_name="document_ocr",
            payload={
                "files": photo_files,
                "purpose": "写真分類・メイン写真選定（外観/内装/水回り/周辺環境）",
                "select_main": True,
            },
            context=context,
        ))
    else:
        s2_start = int(time.time() * 1000)
        s2_out = MicroAgentOutput(
            agent_name="document_ocr",
            success=True,
            result={"classified_photos": [], "main_photo": None, "skipped": True},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    _add_step(2, "photo_processor", "document_ocr", s2_out)
    classified_photos = s2_out.result.get("classified_photos", [])
    context["classified_photos"] = classified_photos

    # ─── Step 3: copy_generator ──────────────────────────────────────────
    s3_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "物件広告文_LLM生成",
            "variables": {
                "property_data": property_data,
                "target_persona": input_data.get("target_persona", "一般"),
                "prohibited_words": PROHIBITED_AD_WORDS,
                "max_length": 300,
                "selling_points_count": 3,
            },
            "purpose": "キャッチコピー・ポータル掲載文生成（公正競争規約準拠）",
        },
        context=context,
    ))
    _add_step(3, "copy_generator", "document_generator", s3_out)
    if not s3_out.success:
        return _fail("copy_generator")
    ad_copy = s3_out.result
    context["ad_copy"] = ad_copy

    # ─── Step 4: compliance_checker ──────────────────────────────────────
    s4_out = await run_compliance_checker(MicroAgentInput(
        company_id=company_id,
        agent_name="compliance_checker",
        payload={
            "check_type": "ad_fair_competition",
            "ad_text": ad_copy.get("ad_text", ""),
            "catch_copy": ad_copy.get("catch_copy", ""),
            "prohibited_words": PROHIBITED_AD_WORDS,
            "required_items": REQUIRED_AD_ITEMS,
            "property_data": property_data,
            "walk_time_rule": "80m=1分（端数切り上げ）",
        },
        context=context,
    ))
    _add_step(4, "compliance_checker", "compliance_checker", s4_out)
    violations = s4_out.result.get("violations", [])
    errors = [v for v in violations if v.get("severity") == "error"]
    if errors:
        logger.warning(
            f"[property_listing] 公正競争規約違反: {[e['message'] for e in errors]}"
        )
    compliant_copy = s4_out.result.get("corrected_text", ad_copy)
    context["violations"] = violations
    context["compliant_copy"] = compliant_copy

    # ─── Step 5: maisoku_generator ───────────────────────────────────────
    ad_type = input_data.get("ad_type", "both")
    if ad_type in ("maisoku", "both"):
        s5_out = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template": "マイソク",
                "variables": {
                    "property_data": property_data,
                    "catch_copy": compliant_copy.get("catch_copy", ""),
                    "ad_text": compliant_copy.get("ad_text", ""),
                    "classified_photos": classified_photos,
                    "required_display_items": REQUIRED_AD_ITEMS,
                },
                "output_format": "pdf",
            },
            context=context,
        ))
    else:
        s5_start = int(time.time() * 1000)
        s5_out = MicroAgentOutput(
            agent_name="document_generator",
            success=True,
            result={"skipped": True, "reason": "マイソク生成スキップ"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )
    _add_step(5, "maisoku_generator", "document_generator", s5_out)
    maisoku_pdf = s5_out.result
    context["maisoku_pdf"] = maisoku_pdf

    # ─── Step 6: portal_formatter ────────────────────────────────────────
    if ad_type in ("portal", "both"):
        s6_out = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template": "ポータルサイト掲載データ",
                "variables": {
                    "property_data": property_data,
                    "ad_text": compliant_copy.get("ad_text", ""),
                    "catch_copy": compliant_copy.get("catch_copy", ""),
                    "portal_sites": PORTAL_SITES,
                    "classified_photos": classified_photos,
                },
                "output_format": "json",
                "purpose": "各ポータルサイトのCSV/API投入形式にデータ変換",
            },
            context=context,
        ))
    else:
        s6_start = int(time.time() * 1000)
        s6_out = MicroAgentOutput(
            agent_name="document_generator",
            success=True,
            result={"skipped": True, "reason": "ポータルデータ生成スキップ"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s6_start,
        )
    _add_step(6, "portal_formatter", "document_generator", s6_out)
    portal_data = s6_out.result
    context["portal_data"] = portal_data

    # ─── Step 7: output_validator ─────────────────────────────────────────
    s7_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "property_data": property_data,
                "ad_copy": compliant_copy,
                "maisoku_pdf": maisoku_pdf,
                "portal_data": portal_data,
            },
            "required_fields": REQUIRED_AD_ITEMS,
            "check_type": "listing_completeness",
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", s7_out)

    final_output = {
        "property_data": property_data,
        "catch_copy": compliant_copy.get("catch_copy", ""),
        "ad_text": compliant_copy.get("ad_text", ""),
        "compliance_violations": violations,
        "maisoku_pdf": maisoku_pdf,
        "portal_data": portal_data,
        "classified_photos": classified_photos,
        "validation": s7_out.result,
    }

    return PropertyListingResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )
