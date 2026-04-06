"""卸売業 受発注AIパイプライン

Steps:
  Step 1: document_receiver   注文ドキュメント受信・分類（種別判定+発注元特定）
  Step 2: ocr_extractor       OCRテキスト抽出（FAX/画像→テキスト。メールはスキップ）
  Step 3: order_structurer    注文内容の構造化抽出（LLM。商品名/数量/単位/納期+確信度）
  Step 4: product_matcher     商品マスタ照合（完全一致→ファジー→Embedding→履歴パターン）
  Step 5: inventory_checker   在庫確認・引当（有効在庫算出→不足分発注提案→納期回答）
  Step 6: confirmation_gen    受注確認書自動生成・返信
  Step 7: output_validator    バリデーション（必須フィールド/数量異常値/与信限度額）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.ocr import run_document_ocr
from workers.micro.extractor import run_structured_extractor
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.calculator import run_cost_calculator
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator

logger = logging.getLogger(__name__)

# 確信度閾値（これ未満はHITLキューへ）
CONFIDENCE_HITL_THRESHOLD = 0.85
CONFIDENCE_WARNING_THRESHOLD = 0.70

# 数量異常値チェック（過去平均の何倍超でアラート）
QUANTITY_ANOMALY_FACTOR = 5.0

# 与信限度額チェック閾値（限度額の何%超でアラート）
CREDIT_ALERT_RATIO = 0.90


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
class OrderProcessingResult:
    """受発注AIパイプラインの最終結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 受発注AIパイプライン",
            f"  ステップ: {len(self.steps)}/7",
            f"  コスト: ¥{self.total_cost_yen:.2f}",
            f"  処理時間: {self.total_duration_ms}ms",
        ]
        if self.failed_step:
            lines.append(f"  失敗ステップ: {self.failed_step}")
        for s in self.steps:
            status = "OK" if s.success else "NG"
            warn = f" [{s.warning}]" if s.warning else ""
            lines.append(
                f"  Step {s.step_no} {status} {s.step_name}: "
                f"confidence={s.confidence:.2f}{warn}"
            )
        return "\n".join(lines)


async def run_order_processing_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> OrderProcessingResult:
    """
    卸売業 受発注AIパイプライン実行。

    Args:
        company_id: テナントID
        input_data: {
            "document_id": str,
            "source_type": "fax" | "email" | "line",
            "source_identifier": str,   # FAX番号 or メールアドレス
            "received_at": str,         # ISO8601
            "document_url": str | None, # FAX/画像の場合はGCS URL
            "raw_text": str | None,     # メールの場合はテキスト直渡し
            "customer_id": str | None,  # 既知の場合は事前セット
            "product_master": list[dict],  # 商品マスタ（簡易版）
            "inventory_data": list[dict],  # 在庫データ
            "customer_master": dict | None, # 得意先マスタ（与信情報含む）
        }

    Returns:
        OrderProcessingResult
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

    def _fail(step_name: str) -> OrderProcessingResult:
        return OrderProcessingResult(
            success=False, steps=steps, final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    source_type = input_data.get("source_type", "email")

    # ─── Step 1: document_receiver ─────────────────────────────────────────
    # 注文ドキュメント受信・分類（種別判定+発注元特定）
    s1_start = int(time.time() * 1000)
    document_type = _classify_document_type(input_data)
    customer_id = input_data.get("customer_id") or _lookup_customer(
        source_type=source_type,
        source_identifier=input_data.get("source_identifier", ""),
    )
    s1_out = MicroAgentOutput(
        agent_name="document_receiver",
        success=True,
        result={
            "document_type": document_type,
            "customer_id": customer_id,
            "source_type": source_type,
            "needs_routing": document_type != "order",
        },
        confidence=0.90,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s1_start,
    )
    _add_step(1, "document_receiver", "document_receiver", s1_out)
    if document_type != "order":
        logger.info(
            f"order_processing_pipeline: 注文書以外のドキュメント "
            f"(type={document_type})。ルーティング対象"
        )
    context["document_type"] = document_type
    context["customer_id"] = customer_id

    # ─── Step 2: ocr_extractor ─────────────────────────────────────────────
    # FAX/画像はOCR、メール/テキストはスキップ
    if source_type in ("fax", "image") and input_data.get("document_url"):
        s2_out = await run_document_ocr(MicroAgentInput(
            company_id=company_id,
            agent_name="document_ocr",
            payload={
                "file_path": input_data["document_url"],
                "language": "ja",
            },
            context=context,
        ))
        _add_step(2, "ocr_extractor", "document_ocr", s2_out)
        if not s2_out.success:
            return _fail("ocr_extractor")
        extracted_text = s2_out.result.get("text", "")
    else:
        # メール/LINE: テキスト直渡しのためOCRスキップ
        extracted_text = input_data.get("raw_text", "")
        s2_start = int(time.time() * 1000)
        s2_out = MicroAgentOutput(
            agent_name="document_ocr",
            success=True,
            result={"text": extracted_text, "skipped": True, "reason": "text_input"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
        _add_step(2, "ocr_extractor", "document_ocr", s2_out)
    context["extracted_text"] = extracted_text

    # ─── Step 3: order_structurer ──────────────────────────────────────────
    # 注文内容の構造化抽出（LLM）
    s3_out = await run_structured_extractor(MicroAgentInput(
        company_id=company_id,
        agent_name="structured_extractor",
        payload={
            "text": extracted_text,
            "schema": {
                "customer_name": "string",
                "order_date": "string (YYYY-MM-DD)",
                "desired_delivery_date": "string (YYYY-MM-DD)",
                "items": "list[{line_no: int, raw_text: str, product_name: str, "
                         "quantity: float, unit: str, product_code: str|null}]",
                "notes": "string",
            },
            "prompt_hint": (
                "卸売業の注文書から受注情報を構造化抽出してください。"
                "単位は「個/本/箱/袋/ケース/C/ダース」等を正規化してください。"
                "各フィールドの確信度が低い場合は confidence: low を付与してください。"
            ),
        },
        context=context,
    ))
    _add_step(3, "order_structurer", "structured_extractor", s3_out)
    if not s3_out.success:
        return _fail("order_structurer")
    structured_order = s3_out.result
    context["structured_order"] = structured_order

    # ─── Step 4: product_matcher ────────────────────────────────────────────
    # 商品マスタ照合（ファジーマッチ）
    order_items = structured_order.get("items", [])
    product_master = input_data.get("product_master", [])
    s4_out = await run_rule_matcher(MicroAgentInput(
        company_id=company_id,
        agent_name="rule_matcher",
        payload={
            "items": order_items,
            "rule_type": "product_master_match",
            "master": product_master,
            "match_config": {
                "exact_weight": 0.40,
                "fuzzy_weight": 0.25,
                "embedding_weight": 0.20,
                "history_weight": 0.15,
                "hitl_threshold": CONFIDENCE_HITL_THRESHOLD,
            },
        },
        context=context,
    ))
    _add_step(4, "product_matcher", "rule_matcher", s4_out)
    if not s4_out.success:
        return _fail("product_matcher")
    matched_products = s4_out.result.get("matched_items", [])
    hitl_items = s4_out.result.get("hitl_required", [])
    context["matched_products"] = matched_products
    context["hitl_items"] = hitl_items

    # ─── Step 5: inventory_checker ──────────────────────────────────────────
    # 在庫確認・引当
    inventory_data = input_data.get("inventory_data", [])
    s5_out = await run_cost_calculator(MicroAgentInput(
        company_id=company_id,
        agent_name="cost_calculator",
        payload={
            "calc_type": "inventory_allocation",
            "matched_items": matched_products,
            "inventory_data": inventory_data,
            # 有効在庫 = 現在庫 - 引当済み + 入荷予定
        },
        context=context,
    ))
    _add_step(5, "inventory_checker", "cost_calculator", s5_out)
    if not s5_out.success:
        return _fail("inventory_checker")
    inventory_result = s5_out.result
    context["inventory_result"] = inventory_result

    # ─── Step 6: confirmation_gen ────────────────────────────────────────────
    # 受注確認書自動生成
    s6_out = await run_document_generator(MicroAgentInput(
        company_id=company_id,
        agent_name="document_generator",
        payload={
            "template": "受注確認書",
            "variables": {
                "customer_name": structured_order.get("customer_name", ""),
                "order_date": structured_order.get("order_date", ""),
                "desired_delivery_date": structured_order.get("desired_delivery_date", ""),
                "matched_items": matched_products,
                "hitl_items": hitl_items,
                "inventory_result": inventory_result,
                "reply_channel": source_type,  # fax/email/line
            },
        },
        context=context,
    ))
    _add_step(6, "confirmation_gen", "document_generator", s6_out)
    context["confirmation_doc"] = s6_out.result

    # ─── Step 7: output_validator ────────────────────────────────────────────
    # バリデーション（必須フィールド/数量異常値/与信チェック）
    customer_master = input_data.get("customer_master") or {}
    credit_limit = customer_master.get("credit_limit", 0)
    current_receivable = customer_master.get("current_receivable", 0)
    order_total = inventory_result.get("total_amount", 0)

    validation_alerts: list[str] = []
    if credit_limit > 0:
        used_ratio = (current_receivable + order_total) / credit_limit
        if used_ratio >= CREDIT_ALERT_RATIO:
            validation_alerts.append(
                f"与信限度額アラート: 使用率 {used_ratio*100:.0f}% "
                f"(限度額¥{credit_limit:,} / 使用¥{current_receivable+order_total:,})"
            )

    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "structured_order": structured_order,
                "matched_products": matched_products,
                "hitl_required": hitl_items,
                "confirmation_doc": s6_out.result,
            },
            "required_fields": ["customer_name", "items"],
            "validation_alerts": validation_alerts,
        },
        context=context,
    ))
    _add_step(7, "output_validator", "output_validator", val_out)
    context["validation_alerts"] = validation_alerts

    final_output = {
        "document_type": document_type,
        "customer_id": customer_id,
        "structured_order": structured_order,
        "matched_products": matched_products,
        "hitl_required": hitl_items,
        "inventory_result": inventory_result,
        "confirmation_doc": s6_out.result,
        "validation_alerts": validation_alerts,
        "hitl_count": len(hitl_items),
    }

    return OrderProcessingResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
    )


def _classify_document_type(input_data: dict[str, Any]) -> str:
    """ドキュメント種別を簡易判定する（order/quote_request/inquiry/other）"""
    # TODO: LLMを使った高精度分類に置き換える
    raw_text = input_data.get("raw_text", "").lower()
    if any(kw in raw_text for kw in ["注文", "発注", "ご注文", "お願い", "頼む"]):
        return "order"
    if any(kw in raw_text for kw in ["見積", "御見積", "価格確認"]):
        return "quote_request"
    if any(kw in raw_text for kw in ["在庫", "確認", "問い合わせ"]):
        return "inquiry"
    return "order"  # FAXはデフォルトで注文書扱い


def _lookup_customer(source_type: str, source_identifier: str) -> str | None:
    """発信元から得意先IDを特定する（TODO: DB照合に置き換える）"""
    # TODO: 得意先マスタDBとの照合実装
    return None
