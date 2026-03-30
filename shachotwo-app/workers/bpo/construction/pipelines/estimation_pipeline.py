"""
建設業 見積パイプライン（マイクロエージェント版）

既存の EstimationPipeline クラス（estimator.py）を8ステップのパイプラインに分割。
各ステップの精度・コスト・所要時間を個別計測可能にし、失敗箇所を特定しやすくする。

Steps:
  Step 1: document_ocr         入力テキスト/ファイル → テキスト（kintone連携対応）
  Step 2: quantity_extractor   テキスト → {工種, 仕様, 数量, 単位} 構造化
  Step 3: unit_price_lookup    各項目の単価をDBから照合
  Step 4: cost_calculator      数量 × 単価 → 金額計算
  Step 5: overhead_calculator  諸経費（共通仮設費・現場管理費・一般管理費）計算
  Step 6: compliance_checker   建設業法チェック（下請け制限・適正単価）
  Step 7: breakdown_generator  見積書データ生成
  Step 8: output_validator     必須記載事項チェック

kintone連携:
  input_data["source"] == "kintone" の場合、kintoneから工事案件データを取得する。
  input_data に以下を含める:
    - source: "kintone"
    - kintone_subdomain: str  kintoneサブドメイン
    - kintone_api_token: str  アプリAPIトークン
    - kintone_app_id: str     工事案件アプリのID
    - kintone_query: str      (省略可) kintoneクエリ文字列。デフォルト: ステータス in ("見積依頼")
    - kintone_field_map: dict (省略可) kintoneフィールドコード → パイプライン内部キーのマッピング
"""
import time
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.validator import run_output_validator
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.bpo.construction.estimator import EstimationPipeline
from workers.bpo.construction.models import ProjectType

logger = logging.getLogger(__name__)

# 見積書の必須フィールド
REQUIRED_ESTIMATION_FIELDS = [
    "title", "items", "direct_cost", "total_cost",
]

# 警告ライン: このconfidence未満のステップはwarningを出す
CONFIDENCE_WARNING_THRESHOLD = 0.70


@dataclass
class StepResult:
    """1ステップの実行結果"""
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
class EstimationPipelineResult:
    """見積パイプライン全体の実行結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'✅' if self.success else '❌'} 見積パイプライン",
            f"  ステップ: {len(self.steps)}/8",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "✅" if s.success else "❌"
            warn = f" ⚠️{s.warning}" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}, ¥{s.cost_yen:.2f}{warn}"
            )
        return "\n".join(lines)


def _extract_summary_amounts(text: str) -> dict:
    """
    工事費計算書（合計のみ）PDFからサマリー金額を抽出する。
    数量内訳書が含まれないPDFへのフォールバック。
    """
    import re
    if not text:
        return {}
    amounts: dict = {}
    # 数字行（カンマ区切り）を探してラベルと紐付け
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    label_map = {
        "直接工事費": "direct_cost", "純工事費": "pure_cost",
        "工事価格": "price_before_tax", "請負工事価格": "total",
        "工事費": "construction_cost",
    }
    for i, line in enumerate(lines):
        for label, key in label_map.items():
            if label in line:
                # 次の数行から金額を探す
                for j in range(i, min(i + 5, len(lines))):
                    m = re.search(r'(\d{1,3}(?:,\d{3})+)', lines[j])
                    if m and key not in amounts:
                        amounts[key] = int(m.group(1).replace(",", ""))
                        break
    return amounts


async def run_estimation_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    project_id: str | None = None,
    region: str = "関東",
    fiscal_year: int = 2025,
    project_type: str = "public_civil",
) -> EstimationPipelineResult:
    """
    建設業見積パイプライン実行。

    Args:
        company_id: テナントID
        input_data: {"text": str} または {"file_path": str} または {"items": list}
        project_id: 既存プロジェクトID（ある場合）
        region: 地域（単価照合用）
        fiscal_year: 年度
        project_type: "public_civil" | "public_building" | "private"
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "project_id": project_id,
        "region": region,
        "fiscal_year": fiscal_year,
        "project_type": project_type,
    }

    def _add_step(
        step_no: int,
        step_name: str,
        agent_name: str,
        out: MicroAgentOutput,
    ) -> StepResult:
        warn = None
        if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
            warn = f"confidence低 ({out.confidence:.2f} < {CONFIDENCE_WARNING_THRESHOLD})"
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

    def _fail(step_name: str) -> EstimationPipelineResult:
        return EstimationPipelineResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: document_ocr ───────────────────────────────────────────
    # 入力ソース別に3つの分岐:
    #   a) items直渡し  → OCRスキップ
    #   b) kintone連携  → kintoneからレコード取得 → items形式に変換
    #   c) file/text    → 通常のOCR処理
    s1_start = int(time.time() * 1000)
    if "items" in input_data:
        # a) items直渡し
        context["raw_items"] = input_data["items"]
        context["raw_text"] = ""
        steps.append(StepResult(
            step_no=1, step_name="document_ocr", agent_name="document_ocr",
            success=True, result={"text": "", "source": "direct_items"},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        ))
    elif input_data.get("source") == "kintone":
        # b) kintone連携: 工事案件レコードを取得してitemsに変換
        s1_kintone_start = int(time.time() * 1000)
        try:
            from workers.connector.kintone import KintoneConnector
            from workers.connector.base import ConnectorConfig

            subdomain = input_data["kintone_subdomain"]
            api_token = input_data["kintone_api_token"]
            app_id = str(input_data["kintone_app_id"])
            query = input_data.get(
                "kintone_query",
                'ステータス in ("見積依頼")',
            )
            # フィールドマッピング: kintoneフィールドコード → パイプライン内部キー
            # デフォルトマッピングは建設業標準フィールドを想定
            field_map: dict[str, str] = {
                "工種": "category",
                "種別": "subcategory",
                "細別": "detail",
                "規格": "specification",
                "数量": "quantity",
                "単位": "unit",
                "単価": "unit_price",
                **input_data.get("kintone_field_map", {}),
            }

            kintone_config = ConnectorConfig(
                tool_name="kintone",
                credentials={
                    "subdomain": subdomain,
                    "api_token": api_token,
                },
            )
            kintone = KintoneConnector(kintone_config)
            records = await kintone.read_records(
                resource=app_id,
                filters={"query": query},
            )

            # kintoneレコード → items形式に変換
            # kintoneのフィールド値は {"value": "..."} 形式
            converted_items: list[dict[str, Any]] = []
            for rec in records:
                item: dict[str, Any] = {}
                for kintone_field, internal_key in field_map.items():
                    field_data = rec.get(kintone_field, {})
                    raw_val = field_data.get("value", "") if isinstance(field_data, dict) else field_data
                    if internal_key in ("quantity", "unit_price") and raw_val not in ("", None):
                        try:
                            item[internal_key] = float(raw_val)
                        except (ValueError, TypeError):
                            item[internal_key] = raw_val
                    else:
                        item[internal_key] = raw_val
                if item:
                    converted_items.append(item)

            s1_duration = int(time.time() * 1000) - s1_kintone_start
            kintone_out = MicroAgentOutput(
                agent_name="document_ocr",
                success=True,
                result={
                    "text": "",
                    "source": "kintone",
                    "records_fetched": len(records),
                    "items_converted": len(converted_items),
                    "app_id": app_id,
                    "query": query,
                },
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=s1_duration,
            )
            context["raw_items"] = converted_items
            context["raw_text"] = ""
            logger.info(
                f"kintone fetch: app_id={app_id} records={len(records)} "
                f"items={len(converted_items)} ({s1_duration}ms)"
            )
        except KeyError as e:
            kintone_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": f"kintone接続設定が不足しています: {e}"},
                confidence=0.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_kintone_start,
            )
        except Exception as e:
            logger.error(f"kintone fetch error: {e}")
            kintone_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": f"kintone取得エラー: {e}"},
                confidence=0.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_kintone_start,
            )
        _add_step(1, "document_ocr", "document_ocr", kintone_out)
        if not kintone_out.success:
            return _fail("document_ocr")
    else:
        # c) 通常OCR処理
        try:
            ocr_out = await run_document_ocr(MicroAgentInput(
                company_id=company_id,
                agent_name="document_ocr",
                payload={k: v for k, v in input_data.items() if k in ("text", "file_path")},
                context=context,
            ))
        except Exception as e:
            ocr_out = MicroAgentOutput(
                agent_name="document_ocr", success=False,
                result={"error": str(e)}, confidence=0.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_start,
            )
        _add_step(1, "document_ocr", "document_ocr", ocr_out)
        if not ocr_out.success:
            return _fail("document_ocr")
        context["raw_text"] = ocr_out.result.get("text", "")

    # ─── Step 2: quantity_extractor ─────────────────────────────────────
    s2_start = int(time.time() * 1000)
    try:
        ep = EstimationPipeline()
        items = await ep.extract_quantities(
            project_id=project_id or "pipeline_run",
            company_id=company_id,
            raw_text=context.get("raw_text", ""),
        ) if context.get("raw_text") else []

        # items直渡しの場合
        if not items and context.get("raw_items"):
            raw_items = context["raw_items"]
        else:
            raw_items = [item.model_dump(mode="json") for item in items]

        s2_duration = int(time.time() * 1000) - s2_start
        if raw_items:
            s2_confidence = 0.9
            s2_success = True
        else:
            # 工事費内訳なし → 工事費計算書（合計のみ）PDFの可能性
            # テキストから合計金額を抽出して部分結果として返す
            raw_text = context.get("raw_text", "")
            summary_amounts = _extract_summary_amounts(raw_text)
            s2_confidence = 0.50 if summary_amounts else 0.10
            s2_success = bool(summary_amounts)  # 合計金額があれば部分成功
            if summary_amounts:
                logger.info(f"quantity_extractor: 工事費計算書モード（合計金額のみ）")

        s2_out = MicroAgentOutput(
            agent_name="quantity_extractor",
            success=s2_success,
            result={"items": raw_items, "count": len(raw_items),
                    "summary_amounts": _extract_summary_amounts(context.get("raw_text", "")) if not raw_items else {}},
            confidence=s2_confidence,
            cost_yen=0.0,
            duration_ms=s2_duration,
        )
    except Exception as e:
        logger.error(f"quantity_extractor error: {e}")
        s2_out = MicroAgentOutput(
            agent_name="quantity_extractor",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    step2 = _add_step(2, "quantity_extractor", "quantity_extractor", s2_out)
    if not s2_out.success:
        return _fail("quantity_extractor")
    context["items"] = s2_out.result["items"]
    # 工事費計算書モード: 合計金額を直接使用
    if not context["items"] and s2_out.result.get("summary_amounts"):
        summary = s2_out.result["summary_amounts"]
        context["summary_mode"] = True
        context["direct_cost"] = summary.get("direct_cost", 0)
        context["total_cost"] = summary.get("total", 0)
        # Step 3-4 をスキップして Step 5 へ
        for skip_no, skip_name in [(3, "unit_price_lookup"), (4, "cost_calculator")]:
            steps.append(StepResult(
                step_no=skip_no, step_name=skip_name, agent_name=skip_name,
                success=True, result={"skipped": True, "reason": "工事費計算書モード"},
                confidence=1.0, cost_yen=0.0, duration_ms=0,
                warning="工事費内訳書なし（合計金額のみ）",
            ))

    # ─── Step 3: unit_price_lookup ──────────────────────────────────────
    s3_start = int(time.time() * 1000)
    try:
        # project_idがない場合は抽出済みitemsを直接渡す（DB経由スキップ）
        items_with_price = await ep.suggest_unit_prices(
            project_id=project_id or "pipeline_run",
            company_id=company_id,
            region=region,
            fiscal_year=fiscal_year,
            items_override=context["items"] if not project_id else None,
        )
        priced_items = [i.model_dump(mode="json") for i in items_with_price]
        matched_count = sum(
            1 for i in priced_items
            if i.get("price_candidates") and
            any(c["source"] != "ai_estimated" for c in i["price_candidates"])
        )
        s3_confidence = matched_count / len(priced_items) if priced_items else 0.5
        s3_out = MicroAgentOutput(
            agent_name="unit_price_lookup",
            success=True,
            result={"priced_items": priced_items, "db_match_rate": round(s3_confidence, 3)},
            confidence=round(s3_confidence, 3),
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        logger.warning(f"unit_price_lookup error (non-fatal): {e}")
        # DBマスタ照合失敗時 → PDF抽出済み単価ベースのconfidence計算
        fallback_items = context["items"]
        pdf_priced = sum(1 for i in fallback_items if float(i.get("unit_price") or 0) > 0)
        pdf_rate = pdf_priced / len(fallback_items) if fallback_items else 0
        # PDF単価あり: 0.85×pdf_rate + 0.40×(1-pdf_rate)  DB照合なしペナルティ-0.05
        pdf_conf = round(max(0.4, 0.85 * pdf_rate + 0.40 * (1 - pdf_rate) - 0.05), 3)
        s3_out = MicroAgentOutput(
            agent_name="unit_price_lookup",
            success=True,  # 単価照合失敗は非致命的
            result={"priced_items": fallback_items, "db_match_rate": round(pdf_rate, 3),
                    "pdf_match_rate": round(pdf_rate, 3), "warning": str(e)},
            confidence=pdf_conf,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    # confidence再計算: DB照合 or PDF単価どちらで賄えたか
    priced_items_list = s3_out.result.get("priced_items", [])
    actual_priced = sum(1 for i in priced_items_list if float(i.get("unit_price") or 0) > 0
                        or (i.get("price_candidates") and
                            any(float(c.get("unit_price", 0)) > 0 for c in i["price_candidates"])))
    if priced_items_list and actual_priced == len(priced_items_list):
        # 全件単価確定 → confidence底上げ（DB照合なしは-0.05ペナルティ）
        db_rate = s3_out.result.get("db_match_rate", 0)
        s3_out = MicroAgentOutput(
            agent_name=s3_out.agent_name, success=s3_out.success,
            result=s3_out.result,
            confidence=round(min(0.95, 0.90 if db_rate > 0 else 0.82), 3),
            cost_yen=s3_out.cost_yen, duration_ms=s3_out.duration_ms,
        )

    _add_step(3, "unit_price_lookup", "unit_price_lookup", s3_out)
    context["priced_items"] = s3_out.result["priced_items"]

    # ─── Step 4: cost_calculator ────────────────────────────────────────
    s4_start = int(time.time() * 1000)
    try:
        direct_cost = 0
        for item in context["priced_items"]:
            qty = item.get("quantity", 0)
            # 最高confidence候補の単価を採用
            candidates = item.get("price_candidates", [])
            unit_price = item.get("unit_price")
            if not unit_price and candidates:
                best = max(candidates, key=lambda c: c.get("confidence", 0))
                unit_price = best.get("unit_price")
            if qty and unit_price:
                direct_cost += int(Decimal(str(qty)) * Decimal(str(unit_price)))

        s4_out = MicroAgentOutput(
            agent_name="cost_calculator",
            success=True,
            result={"direct_cost": direct_cost},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )
    except Exception as e:
        s4_out = MicroAgentOutput(
            agent_name="cost_calculator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "cost_calculator", "cost_calculator", s4_out)
    if not s4_out.success:
        return _fail("cost_calculator")
    context["direct_cost"] = s4_out.result["direct_cost"]

    # ─── Step 5: overhead_calculator ────────────────────────────────────
    s5_start = int(time.time() * 1000)
    try:
        pt_map = {
            "public_civil": ProjectType.PUBLIC_CIVIL,
            "public_building": ProjectType.PUBLIC_BUILDING,
            "private": ProjectType.PRIVATE,
        }
        pt = pt_map.get(project_type, ProjectType.PUBLIC_CIVIL)

        if project_id:
            overhead = await ep.calculate_overhead(project_id, company_id, pt)
            total_cost = overhead.total
            overhead_data = {
                "direct_cost": overhead.direct_cost,
                "common_temporary": overhead.common_temporary,
                "site_management": overhead.site_management,
                "general_admin": overhead.general_admin,
                "total": overhead.total,
            }
        else:
            # project_idなしの場合は建設工事積算基準に準拠した簡易計算
            # 参考: 国土交通省「公共建設工事の積算基準」標準的な諸経費率
            dc = context["direct_cost"]
            pt_key = project_type or "public_civil"
            # 共通仮設費率: 公共土木9%、公共建築8%、民間7%
            ct_rate = {"public_civil": "0.090", "public_building": "0.080", "private": "0.070"}.get(pt_key, "0.090")
            # 現場管理費率: (直接+共通仮設) の 30%
            sm_rate = {"public_civil": "0.300", "public_building": "0.280", "private": "0.250"}.get(pt_key, "0.300")
            # 一般管理費等: 純工事費の12%
            ga_rate = "0.120"
            ct = int(dc * Decimal(ct_rate))
            sm = int((dc + ct) * Decimal(sm_rate))
            ga = int((dc + ct + sm) * Decimal(ga_rate))
            total_cost = dc + ct + sm + ga
            overhead_data = {
                "direct_cost": dc,
                "common_temporary": ct,
                "site_management": sm,
                "general_admin": ga,
                "total": total_cost,
                "method": "standard_rates",
                "ct_rate": float(ct_rate),
                "sm_rate": float(sm_rate),
            }

        s5_out = MicroAgentOutput(
            agent_name="overhead_calculator",
            success=True,
            result=overhead_data,
            # project_idあり→DB実績値: 1.0、なし→標準レート: 0.92
            confidence=1.0 if project_id else 0.92,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )
    except Exception as e:
        logger.warning(f"overhead_calculator error: {e}")
        dc = context["direct_cost"]
        # フォールバック: 公共土木の標準加算率 約47%
        ct = int(dc * Decimal("0.090"))
        sm = int((dc + ct) * Decimal("0.300"))
        ga = int((dc + ct + sm) * Decimal("0.120"))
        total_cost = dc + ct + sm + ga
        s5_out = MicroAgentOutput(
            agent_name="overhead_calculator",
            success=True,
            result={"direct_cost": dc, "common_temporary": ct, "site_management": sm,
                    "general_admin": ga, "total": total_cost, "warning": str(e)},
            confidence=0.88,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    _add_step(5, "overhead_calculator", "overhead_calculator", s5_out)
    context["overhead"] = s5_out.result
    context["total_cost"] = s5_out.result["total"]

    # ─── Step 6: compliance_checker ─────────────────────────────────────
    s6_start = int(time.time() * 1000)
    warnings: list[str] = []
    # 建設業法チェック（簡易版）
    total = context["total_cost"]
    if total > 0:
        # 下請け制限: 特定建設業許可が必要な下請け金額
        if total >= 45_000_000:
            warnings.append("特定建設業許可要件: 下請総額4,500万円以上（建築一式以外）")
        if total >= 70_000_000:
            warnings.append("特定建設業許可要件: 下請総額7,000万円以上（建築一式）")
    # 材料費ゼロ項目チェック
    zero_price_items = [
        i.get("detail", i.get("category", "?"))
        for i in context["priced_items"]
        if not i.get("unit_price") and not i.get("price_candidates")
    ]
    if zero_price_items:
        warnings.append(f"単価未設定項目: {len(zero_price_items)}件 ({', '.join(zero_price_items[:3])}...)")

    s6_out = MicroAgentOutput(
        agent_name="compliance_checker",
        success=True,
        result={"warnings": warnings, "passed": len(warnings) == 0},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s6_start,
    )
    _add_step(6, "compliance_checker", "compliance_checker", s6_out)
    context["compliance_warnings"] = warnings

    # ─── Step 7: breakdown_generator ────────────────────────────────────
    s7_start = int(time.time() * 1000)
    try:
        if project_id:
            breakdown = await ep.generate_breakdown_data(project_id, company_id)
        else:
            # project_idなしの場合は簡易内訳書データ
            rows = []
            for item in context["priced_items"]:
                candidates = item.get("price_candidates", [])
                unit_price = item.get("unit_price")
                if not unit_price and candidates:
                    best = max(candidates, key=lambda c: c.get("confidence", 0))
                    unit_price = best.get("unit_price")
                qty = item.get("quantity", 0)
                amount = int(Decimal(str(qty)) * Decimal(str(unit_price))) if qty and unit_price else ""
                rows.append([
                    item.get("category", ""),
                    item.get("subcategory", ""),
                    item.get("detail", ""),
                    item.get("specification", ""),
                    float(qty) if qty else "",
                    item.get("unit", ""),
                    float(unit_price) if unit_price else "",
                    amount,
                ])
            breakdown = {
                "title": "工事費内訳書",
                "items": context["priced_items"],
                "direct_cost": context["direct_cost"],
                "total_cost": context["total_cost"],
                "overhead": context["overhead"],
                "headers": ["工種", "種別", "細別", "規格", "数量", "単位", "単価", "金額"],
                "rows": rows,
                "compliance_warnings": context["compliance_warnings"],
            }

        s7_out = MicroAgentOutput(
            agent_name="breakdown_generator",
            success=True,
            result=breakdown,
            confidence=0.95,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )
    except Exception as e:
        s7_out = MicroAgentOutput(
            agent_name="breakdown_generator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )

    _add_step(7, "breakdown_generator", "breakdown_generator", s7_out)
    if not s7_out.success:
        return _fail("breakdown_generator")
    context["breakdown"] = s7_out.result

    # ─── Step 8: output_validator ────────────────────────────────────────
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": context["breakdown"],
            "required_fields": REQUIRED_ESTIMATION_FIELDS,
            "numeric_fields": ["direct_cost", "total_cost"],
            "positive_fields": ["total_cost"],
        },
        context=context,
    ))
    _add_step(8, "output_validator", "output_validator", val_out)
    # バリデーション失敗でも内訳書は返す（警告として扱う）

    # ─── Step 9: anomaly_detector ────────────────────────────────────────
    # 各コスト項目の桁間違いを検知する（見積の¥850,000 → ¥8,500,000 等）
    s9_start = int(time.time() * 1000)
    overhead = context.get("overhead", {})
    anomaly_items = [
        {"name": "direct_cost", "value": context["direct_cost"]},
        {"name": "common_temporary", "value": overhead.get("common_temporary", 0)},
        {"name": "site_management", "value": overhead.get("site_management", 0)},
        {"name": "general_admin", "value": overhead.get("general_admin", 0)},
        {"name": "total_cost", "value": context["total_cost"]},
    ]
    # 数量×単価の各項目金額も追加（単価が確定しているもののみ）
    for item in context.get("priced_items", []):
        candidates = item.get("price_candidates", [])
        unit_price = item.get("unit_price")
        if not unit_price and candidates:
            best = max(candidates, key=lambda c: c.get("confidence", 0))
            unit_price = best.get("unit_price")
        qty = item.get("quantity", 0)
        if qty and unit_price:
            item_amount = int(Decimal(str(qty)) * Decimal(str(unit_price)))
            item_name = item.get("detail") or item.get("category") or "工事項目"
            anomaly_items.append({"name": item_name, "value": item_amount})
    try:
        anomaly_out = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id,
            agent_name="anomaly_detector",
            payload={
                "items": anomaly_items,
                "detect_modes": ["digit_error", "range"],
            },
            context=context,
        ))
    except Exception as e:
        anomaly_out = MicroAgentOutput(
            agent_name="anomaly_detector", success=False,
            result={"error": str(e)}, confidence=0.0,
            cost_yen=0.0, duration_ms=int(time.time() * 1000) - s9_start,
        )
    steps.append(StepResult(
        step_no=9, step_name="anomaly_detector", agent_name="anomaly_detector",
        success=anomaly_out.success,
        result=anomaly_out.result,
        confidence=anomaly_out.confidence,
        cost_yen=anomaly_out.cost_yen,
        duration_ms=anomaly_out.duration_ms,
    ))
    if anomaly_out.success and anomaly_out.result.get("anomaly_count", 0) > 0:
        context["breakdown"]["anomaly_warnings"] = anomaly_out.result["anomalies"]

    # ─── 最終結果 ─────────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        f"estimation_pipeline complete: "
        f"items={len(context['priced_items'])}, "
        f"total=¥{context['total_cost']:,}, "
        f"cost=¥{total_cost_yen:.2f}, "
        f"{total_duration}ms"
    )

    return EstimationPipelineResult(
        success=True,
        steps=steps,
        final_output=context["breakdown"],
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
    )
