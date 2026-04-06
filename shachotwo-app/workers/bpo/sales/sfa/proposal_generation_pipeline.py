"""
SFA パイプライン② — 提案書AI生成・送付

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md § 4.2

トリガー:
    - リードスコア >= 70 の自動遷移（lead_qualification_pipeline から連鎖）
    - 営業担当者による手動トリガー（API 経由）

Steps:
    Step 1: saas_reader        — Supabase から leads / opportunities レコード取得
    Step 2: rule_matcher       — genome/data/{industry}.json から業種特性取得
    Step 3: document_generator — Gemini LLM で提案書 JSON 生成（SYSTEM_PROPOSAL プロンプト）
    Step 4: pdf_generator      — proposal_template.html + WeasyPrint で PDF 生成
    Step 5: saas_writer        — Supabase Storage に PDF をアップロード
    Step 6: message            — LLM でパーソナライズ送付メール生成
    Step 7: saas_writer        — SendGrid Email Connector 経由でメール送信
    Step 8: saas_writer        — proposals / opportunities テーブル更新 + Slack 通知

定数/設定:
    CONFIDENCE_WARNING_THRESHOLD: 0.70 未満のステップは warning を記録
    MAX_STORAGE_PATH_LEN: ストレージパス文字数上限
"""
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from llm.client import LLMTask, ModelTier, get_llm_client
from llm.prompts.sales_proposal import (
    INDUSTRY_PAIN_POINTS,
    USER_PROPOSAL_TEMPLATE,
    build_proposal_system_prompt,
)
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.pdf_generator import run_pdf_generator
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.saas_reader import run_saas_reader
from workers.micro.saas_writer import run_saas_writer

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
# 定数
# ────────────────────────────────────────────────────────────

CONFIDENCE_WARNING_THRESHOLD = 0.70

# 業種コード → 日本語ラベル
INDUSTRY_LABELS: dict[str, str] = {
    "construction":  "建設業",
    "manufacturing": "製造業",
    "dental":        "歯科医院",
    "nursing":       "介護・福祉",
    "logistics":     "物流・運送",
    "restaurant":    "飲食業",
    "clinic":        "医療クリニック",
    "pharmacy":      "調剤薬局",
    "beauty":        "美容・エステ",
    "auto_repair":   "自動車整備",
    "hotel":         "ホテル・旅館",
    "ecommerce":     "ECサイト・小売",
    "staffing":      "人材派遣・紹介",
    "architecture":  "設計・建築士事務所",
    "realestate":    "不動産",
    "professional":  "士業（税理士・社労士等）",
}

# モジュール標準料金フォールバック（DB未設定時に使用）
# 実際の料金はDB（pricing_modules）から取得する。
MODULE_PRICES: dict[str, int] = {
    "brain":    30_000,
    "bpo_core": 250_000,
}
ADDON_PRICE = 100_000  # 追加モジュール 1 個あたり（フォールバック）

# 提案書有効期限（発行日 + N 日）
PROPOSAL_VALID_DAYS = 30

# Supabase Storage バケット名
STORAGE_BUCKET = "proposals"

# genome データ検索パス
_GENOME_BASE = Path(__file__).resolve().parents[4] / "brain" / "genome" / "data"


# ────────────────────────────────────────────────────────────
# データクラス
# ────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """1 ステップの実行結果。"""
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
class ProposalGenerationResult:
    """パイプライン全体の実行結果。"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} 提案書生成パイプライン",
            f"  ステップ: {len(self.steps)}/8",
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
                f"confidence={s.confidence:.2f}, ¥{s.cost_yen:.2f}{warn}"
            )
        return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# 内部ユーティリティ
# ────────────────────────────────────────────────────────────

def _make_proposal_number() -> str:
    """提案番号を採番する。形式: PR-YYYYMM-XXXX"""
    today = date.today()
    suffix = str(uuid.uuid4().int)[:4].upper()
    return f"PR-{today.strftime('%Y%m')}-{suffix}"


async def _calc_pricing(company_id: str, modules: list[str]) -> dict[str, int]:
    """選択モジュールから料金を計算する。DB管理の料金を参照し、未設定時はフォールバック。

    Args:
        company_id: テナントID（DB料金参照に使用）
        modules: 選択モジュールのコードリスト

    Returns:
        monthly_total, tax, total_with_tax, annual_total を含む辞書
    """
    from db.pricing import get_all_module_prices
    db_prices = await get_all_module_prices(company_id)

    total = 0
    for m in modules:
        if m in db_prices:
            total += db_prices[m]
        elif m in MODULE_PRICES:
            total += MODULE_PRICES[m]
        else:
            # 追加モジュール（名称がMODULE_PRICES/DB未登録のもの）
            total += db_prices.get("additional", ADDON_PRICE)
    tax = int(total * 0.10)
    return {
        "monthly_total": total,
        "tax": tax,
        "total_with_tax": total + tax,
        "annual_total": total * 12,
    }


def _load_genome(industry: str) -> dict[str, Any]:
    """genome/data/{industry}.json を読み込む。存在しない場合は空辞書。"""
    # BPO サブディレクトリも探索する
    candidates = [
        _GENOME_BASE / f"{industry}.json",
        _GENOME_BASE / "bpo" / f"{industry}.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"genome load failed ({path}): {e}")
    logger.info(f"genome not found for industry={industry}, using empty dict")
    return {}


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """LLM 出力からコードフェンスを除去して JSON パースする。"""
    content = raw.strip()
    content = re.sub(r"^```(?:json)?\s*\n?", "", content)
    content = re.sub(r"\n?```\s*$", "", content)
    return json.loads(content)


async def _build_proposal_context(
    company_id: str,
    lead: dict[str, Any],
    opportunity: dict[str, Any],
    proposal_json: dict[str, Any],
    proposal_number: str,
    pdf_path: str,
) -> dict[str, Any]:
    """
    proposal_template.html に渡す proposal オブジェクトを組み立てる。

    テンプレート変数:
        proposal.issue_date          (date)
        proposal.valid_until         (date)
        proposal.proposal_number     (str)
        proposal.company_name        (str)
        proposal.industry            (str)
        proposal.industry_label      (str)
        proposal.pains               (list[dict])
        proposal.solutions           (list[dict])
        proposal.plan_name           (str)
        proposal.modules             (list[dict])
        proposal.monthly_total       (int)
        proposal.tax                 (int)
        proposal.total_with_tax      (int)
        proposal.roi                 (dict | None)
        proposal.timeline            (list[dict])
        proposal.discount_note       (str | None)
        proposal.contract_url        (str | None)
    """
    industry = (
        lead.get("industry")
        or opportunity.get("target_industry")
        or "construction"
    )
    modules: list[str] = opportunity.get("selected_modules") or ["brain", "bpo_core"]
    pricing = await _calc_pricing(company_id, modules)

    # pain_points: [{title, description, estimated_hours?}]
    pains = []
    for pp in proposal_json.get("pain_points", []):
        pains.append({
            "title": pp.get("category", pp.get("pain_point", "")),
            "description": pp.get("description", pp.get("impact", "")),
            "estimated_hours": None,
        })

    # solutions: [{module_name, description, automation_rate?}]
    solutions = []
    for sm in proposal_json.get("solution_map", []):
        solutions.append({
            "module_name": sm.get("module", sm.get("solution", "")),
            "description": sm.get("effect", sm.get("solution", "")),
            "automation_rate": None,
        })

    # modules: [{name, description, monthly_price, key_features}]
    template_modules = []
    for m in proposal_json.get("modules", []):
        template_modules.append({
            "name": m.get("name", ""),
            "description": m.get("description", ""),
            "monthly_price": m.get("monthly_price", 0),
            "key_features": m.get("key_features", []),
        })

    # ROI
    roi_raw = proposal_json.get("roi_estimate", {})
    roi = None
    if roi_raw:
        hours_saved = max(1, roi_raw.get("savings_monthly", 0) // 3000)  # 3000円/時換算
        roi = {
            "hours_saved_monthly": hours_saved,
            "cost_saved_monthly": roi_raw.get("savings_monthly", 0),
            "net_benefit_monthly": roi_raw.get("savings_monthly", 0) - pricing["monthly_total"],
        }

    # timeline
    timeline = []
    for t in proposal_json.get("timeline", []):
        timeline.append({
            "period": t.get("period", ""),
            "description": t.get("milestone", ", ".join(t.get("tasks", []))),
        })
    if not timeline:
        timeline = [
            {"period": "Week 1-2", "description": "導入設計・ナレッジ入力"},
            {"period": "Week 3-4", "description": "BPOパイプライン設定・テスト"},
            {"period": "Week 5-6", "description": "本番稼働・効果検証"},
        ]

    today = date.today()
    return {
        "issue_date": today,
        "valid_until": today + timedelta(days=PROPOSAL_VALID_DAYS),
        "proposal_number": proposal_number,
        "company_name": lead.get("company_name") or opportunity.get("target_company_name", ""),
        "industry": industry,
        "industry_label": INDUSTRY_LABELS.get(industry, industry),
        "pains": pains,
        "solutions": solutions,
        "plan_name": "BPOコア + ブレイン" if "bpo_core" in modules else "ブレインプラン",
        "modules": template_modules,
        "monthly_total": pricing["monthly_total"],
        "tax": pricing["tax"],
        "total_with_tax": pricing["total_with_tax"],
        "roi": roi,
        "timeline": timeline,
        "discount_note": proposal_json.get("pricing", {}).get("discount_note"),
        "contract_url": None,
    }


async def _upload_pdf_to_storage(
    company_id: str,
    proposal_number: str,
    pdf_bytes: bytes,
) -> str:
    """
    Supabase Storage に PDF をアップロードしてパスを返す。
    アップロード失敗時はローカルパスを返す（非致命的）。
    """
    storage_path = f"{company_id}/{proposal_number}.pdf"
    try:
        from db.supabase import get_service_client
        db = get_service_client()
        db.storage.from_(STORAGE_BUCKET).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf"},
        )
        logger.info(f"PDF uploaded to storage: {storage_path}")
        return storage_path
    except Exception as e:
        logger.warning(f"storage upload failed (non-fatal): {e}")
        # フォールバック: パスのみ返す（実ファイルなし）
        return storage_path


async def _send_email_via_sendgrid(
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    pdf_bytes: bytes,
    proposal_number: str,
) -> bool:
    """
    SendGrid 経由でメールを送信する。
    API キーが未設定の場合は dry-run ログを出力して True を返す。
    """
    import os
    api_key = os.getenv("SENDGRID_API_KEY", "")
    from_email = os.getenv("SENDGRID_FROM_EMAIL", "no-reply@shachotwo.com")
    from_name = os.getenv("SENDGRID_FROM_NAME", "シャチョツー 杉本")

    if not api_key:
        logger.info(
            f"SENDGRID_API_KEY 未設定 — dry-run: to={to_email}, subject={subject}"
        )
        return True

    try:
        import base64
        import httpx

        encoded_pdf = base64.b64encode(pdf_bytes).decode()
        payload = {
            "personalizations": [
                {
                    "to": [{"email": to_email, "name": to_name}],
                    "subject": subject,
                }
            ],
            "from": {"email": from_email, "name": from_name},
            "content": [{"type": "text/html", "value": body_html}],
            "attachments": [
                {
                    "content": encoded_pdf,
                    "type": "application/pdf",
                    "filename": f"{proposal_number}.pdf",
                    "disposition": "attachment",
                }
            ],
            "tracking_settings": {
                "open_tracking": {"enable": True},
                "click_tracking": {"enable": True, "enable_text": False},
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code in (200, 202):
            logger.info(f"SendGrid: 送信成功 to={to_email}")
            return True
        else:
            logger.warning(f"SendGrid: status={resp.status_code} body={resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"SendGrid 送信エラー: {e}")
        return False


# ────────────────────────────────────────────────────────────
# パイプライン本体
# ────────────────────────────────────────────────────────────

async def run_proposal_generation_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    dry_run: bool = False,
) -> ProposalGenerationResult:
    """
    SFA パイプライン② — 提案書AI生成・送付。

    Args:
        company_id:  シャチョツーの運営会社テナント ID（RLS 用）
        input_data:  必須キー:
                       "lead_id" (str)         — leads テーブルの UUID
                     任意キー:
                       "opportunity_id" (str)  — opportunities テーブルの UUID
                                                  未指定時は lead から自動生成
        dry_run:     True の場合、メール送信・DB 書き込みをスキップ

    Returns:
        ProposalGenerationResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "dry_run": dry_run,
    }

    # ──────────────────────────────────────────
    # ヘルパー
    # ──────────────────────────────────────────

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

    def _fail(step_name: str, extra: dict | None = None) -> ProposalGenerationResult:
        return ProposalGenerationResult(
            success=False,
            steps=steps,
            final_output=extra or {},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ──────────────────────────────────────────
    # Step 1: saas_reader — リード情報取得
    # ──────────────────────────────────────────
    lead_id: str = input_data.get("lead_id", "")
    opportunity_id: str = input_data.get("opportunity_id", "")

    if not lead_id:
        # lead_id 必須チェック
        steps.append(StepResult(
            step_no=1, step_name="saas_reader", agent_name="saas_reader",
            success=False,
            result={"error": "lead_id が必須です"},
            confidence=0.0, cost_yen=0.0, duration_ms=0,
        ))
        return _fail("saas_reader")

    s1_start = int(time.time() * 1000)
    try:
        lead_out = await run_saas_reader(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_reader",
            payload={
                "service": "supabase",
                "operation": "get_lead",
                "params": {
                    "table": "leads",
                    "select": "*",
                    "limit": 1,
                },
            },
            context={"filter_id": lead_id},
        ))
        # Supabase reader は汎用フィルタを持たないため、結果から id でフィルタ
        leads_data: list[dict] = lead_out.result.get("data", [])
        lead_record = next(
            (r for r in leads_data if str(r.get("id")) == lead_id),
            leads_data[0] if leads_data else None,
        )

        if not lead_record:
            # レコードが見つからない場合は input_data からフォールバック
            lead_record = {
                "id": lead_id,
                "company_name": input_data.get("company_name", ""),
                "contact_email": input_data.get("contact_email", ""),
                "contact_name": input_data.get("contact_name", ""),
                "industry": input_data.get("industry", "construction"),
                "employee_count": input_data.get("employee_count", 30),
            }
            logger.info(f"lead not found in DB, using input_data fallback: lead_id={lead_id}")

        s1_out = MicroAgentOutput(
            agent_name="saas_reader",
            success=True,
            result={"lead": lead_record},
            confidence=1.0 if not lead_out.result.get("mock") else 0.7,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    except Exception as e:
        logger.error(f"saas_reader (lead) error: {e}")
        s1_out = MicroAgentOutput(
            agent_name="saas_reader",
            success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    _add_step(1, "saas_reader", "saas_reader", s1_out)
    if not s1_out.success:
        return _fail("saas_reader")
    context["lead"] = s1_out.result["lead"]

    # opportunity が指定されている場合は取得
    context["opportunity"] = {}
    if opportunity_id:
        try:
            opp_out = await run_saas_reader(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_reader",
                payload={
                    "service": "supabase",
                    "operation": "get_opportunity",
                    "params": {"table": "opportunities", "select": "*", "limit": 50},
                },
                context={},
            ))
            opps: list[dict] = opp_out.result.get("data", [])
            opp_record = next(
                (o for o in opps if str(o.get("id")) == opportunity_id), {}
            )
            context["opportunity"] = opp_record
        except Exception as e:
            logger.warning(f"opportunity fetch failed (non-fatal): {e}")

    # ──────────────────────────────────────────
    # Step 2: rule_matcher — 業種テンプレート選択
    # ──────────────────────────────────────────
    lead = context["lead"]
    industry = (
        lead.get("industry")
        or context["opportunity"].get("target_industry")
        or input_data.get("industry", "construction")
    )

    s2_start = int(time.time() * 1000)
    try:
        # genome JSON から業種特性を取得（キャッシュ不要、小ファイル）
        genome_data = _load_genome(industry)

        # rule_matcher で knowledge_items からも照合を試みる
        rm_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "industry": industry,
                    "employee_count": lead.get("employee_count", 30),
                    "pain_points": lead.get("score_reasons", []),
                },
                "domain": "sales_proposal",
                "category": "template",
            },
            context=context,
        ))

        # genome + rule_matcher の結果をマージ
        industry_template = {
            "genome": genome_data,
            "matched_rules": rm_out.result.get("matched_rules", []),
            "applied_values": rm_out.result.get("applied_values", {}),
            "industry_pain_defaults": INDUSTRY_PAIN_POINTS.get(industry, []),
        }

        s2_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={"industry_template": industry_template, "industry": industry},
            confidence=max(rm_out.confidence, 0.6 if genome_data else 0.4),
            cost_yen=rm_out.cost_yen,
            duration_ms=int(time.time() * 1000) - s2_start,
        )
    except Exception as e:
        logger.warning(f"rule_matcher error (non-fatal): {e}")
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,  # テンプレートなしでも続行可能
            result={
                "industry_template": {
                    "genome": {},
                    "matched_rules": [],
                    "applied_values": {},
                    "industry_pain_defaults": INDUSTRY_PAIN_POINTS.get(industry, []),
                },
                "industry": industry,
            },
            confidence=0.5,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "rule_matcher", "rule_matcher", s2_out)
    context["industry_template"] = s2_out.result["industry_template"]
    context["industry"] = industry

    # ──────────────────────────────────────────
    # Step 3: document_generator — LLM 提案書 JSON 生成
    # ──────────────────────────────────────────
    s3_start = int(time.time() * 1000)
    try:
        from db.pricing import get_all_module_prices
        llm = get_llm_client()
        selected_modules: list[str] = (
            context["opportunity"].get("selected_modules")
            or input_data.get("selected_modules", ["brain", "bpo_core"])
        )
        # DB から最新料金を取得してシステムプロンプトを動的生成
        db_prices = await get_all_module_prices(company_id)
        system_prompt = build_proposal_system_prompt(db_prices)
        pain_points_input = (
            lead.get("score_reasons")
            or input_data.get("pain_points", [])
        )
        # score_reasons が dict リストの可能性があるため文字列に正規化
        if pain_points_input and isinstance(pain_points_input[0], dict):
            pain_points_input = [
                str(p.get("reason", p.get("description", p)))
                for p in pain_points_input
            ]

        user_prompt = USER_PROPOSAL_TEMPLATE.format(
            company_name=lead.get("company_name", ""),
            industry=INDUSTRY_LABELS.get(industry, industry),
            employee_count=lead.get("employee_count") or 30,
            pain_points=", ".join(pain_points_input) if pain_points_input else "（未入力）",
            selected_modules=", ".join(selected_modules),
            industry_pain_points="\n".join(
                f"- {p}"
                for p in context["industry_template"]["industry_pain_defaults"]
            ),
        )

        llm_response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.STANDARD,
            max_tokens=3000,
            temperature=0.35,
            company_id=company_id,
            task_type="proposal_generation",
        ))

        proposal_json = _parse_llm_json(llm_response.content)
        s3_out = MicroAgentOutput(
            agent_name="document_generator",
            success=True,
            result={
                "proposal_json": proposal_json,
                "tokens_in": llm_response.tokens_in,
                "tokens_out": llm_response.tokens_out,
            },
            confidence=0.85,
            cost_yen=llm_response.cost_yen,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except json.JSONDecodeError as e:
        logger.error(f"LLM 出力 JSON パース失敗: {e}")
        s3_out = MicroAgentOutput(
            agent_name="document_generator",
            success=False,
            result={"error": f"JSON parse error: {e}"},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        logger.error(f"document_generator error: {e}")
        s3_out = MicroAgentOutput(
            agent_name="document_generator",
            success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "document_generator", "document_generator", s3_out)
    if not s3_out.success:
        return _fail("document_generator")
    context["proposal_json"] = s3_out.result["proposal_json"]

    # ──────────────────────────────────────────
    # Step 4: pdf_generator — WeasyPrint PDF 生成
    # ──────────────────────────────────────────
    proposal_number = _make_proposal_number()
    context["proposal_number"] = proposal_number

    s4_start = int(time.time() * 1000)
    try:
        proposal_ctx = await _build_proposal_context(
            company_id=company_id,
            lead=context["lead"],
            opportunity=context["opportunity"],
            proposal_json=context["proposal_json"],
            proposal_number=proposal_number,
            pdf_path="",  # ストレージパスは Step 5 で確定
        )
        # テンプレートは Jinja2 が date オブジェクトを strftime で使うため
        # Python date オブジェクトをそのまま渡す（文字列化はテンプレート側）

        pdf_out = await run_pdf_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="pdf_generator",
            payload={
                "template_name": "proposal_template.html",
                "data": {"proposal": proposal_ctx},
            },
            context=context,
        ))
    except Exception as e:
        logger.error(f"pdf_generator error: {e}")
        pdf_out = MicroAgentOutput(
            agent_name="pdf_generator",
            success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    _add_step(4, "pdf_generator", "pdf_generator", pdf_out)
    if not pdf_out.success:
        return _fail("pdf_generator")
    context["pdf_bytes"] = pdf_out.result["pdf_bytes"]
    context["pdf_size_kb"] = pdf_out.result["size_kb"]

    # ──────────────────────────────────────────
    # Step 5: saas_writer — Supabase Storage アップロード
    # ──────────────────────────────────────────
    s5_start = int(time.time() * 1000)
    storage_path = ""
    try:
        if not dry_run:
            storage_path = await _upload_pdf_to_storage(
                company_id=company_id,
                proposal_number=proposal_number,
                pdf_bytes=context["pdf_bytes"],
            )
        else:
            storage_path = f"{company_id}/{proposal_number}.pdf"
            logger.info(f"dry_run: storage upload skipped, path={storage_path}")

        s5_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=True,
            result={
                "storage_path": storage_path,
                "size_kb": context["pdf_size_kb"],
                "dry_run": dry_run,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )
    except Exception as e:
        logger.error(f"storage upload error: {e}")
        storage_path = f"{company_id}/{proposal_number}.pdf"
        s5_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=True,  # ストレージ失敗は非致命的
            result={
                "storage_path": storage_path,
                "warning": str(e),
                "dry_run": dry_run,
            },
            confidence=0.8,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    _add_step(5, "saas_writer_storage", "saas_writer", s5_out)
    context["pdf_storage_path"] = storage_path

    # ──────────────────────────────────────────
    # Step 6: message — 送付メール生成
    # ──────────────────────────────────────────
    s6_start = int(time.time() * 1000)
    try:
        llm = get_llm_client()
        mail_system = (
            "あなたはシャチョツーの営業担当者です。"
            "提案書を添付した送付メールを件名と本文（HTML）で作成してください。"
            "JSON のみを出力: {\"subject\": \"...\", \"body_html\": \"...\"}"
        )
        mail_user = f"""
顧客企業名: {lead.get('company_name', '')}
担当者名: {lead.get('contact_name', '')}様
業種: {INDUSTRY_LABELS.get(industry, industry)}
従業員数: {lead.get('employee_count') or 30}名
提案番号: {proposal_number}

ポイント:
- 業種特有の課題（{', '.join(INDUSTRY_PAIN_POINTS.get(industry, [])[:2])}）に触れる
- シャチョツーが解決できる具体的な効果を 1 文で述べる
- PDF を確認後、返信または URL から日程調整をお願いする一文
- 丁寧だが簡潔に（300 字以内の本文）
"""
        mail_response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": mail_system},
                {"role": "user", "content": mail_user},
            ],
            tier=ModelTier.FAST,
            max_tokens=800,
            temperature=0.4,
            company_id=company_id,
            task_type="proposal_email",
        ))
        mail_data = _parse_llm_json(mail_response.content)
        email_subject = mail_data.get("subject", f"【ご提案書】{lead.get('company_name', '')} 様へ — シャチョツー")
        email_body_html = mail_data.get(
            "body_html",
            f"<p>{lead.get('contact_name', '')} 様<br><br>"
            f"提案書をお送りします。ご確認のほどよろしくお願いいたします。</p>",
        )

        s6_out = MicroAgentOutput(
            agent_name="message",
            success=True,
            result={
                "subject": email_subject,
                "body_html": email_body_html,
                "tokens_in": mail_response.tokens_in,
                "tokens_out": mail_response.tokens_out,
            },
            confidence=0.88,
            cost_yen=mail_response.cost_yen,
            duration_ms=int(time.time() * 1000) - s6_start,
        )
    except Exception as e:
        logger.warning(f"message generation error (non-fatal): {e}")
        # フォールバックメール
        fallback_subject = f"【ご提案書送付】{lead.get('company_name', '')} 様へ"
        fallback_body = (
            f"<p>{lead.get('contact_name', '')} 様<br><br>"
            f"お世話になっております。シャチョツーの杉本です。<br><br>"
            f"この度は、貴社の業務効率化についてご提案させていただく機会をいただき、"
            f"誠にありがとうございます。<br><br>"
            f"添付の提案書をご確認いただけますと幸いです。<br><br>"
            f"ご不明な点やご要望がございましたら、お気軽にご返信ください。<br><br>"
            f"シャチョツー　杉本 祐陽</p>"
        )
        s6_out = MicroAgentOutput(
            agent_name="message",
            success=True,
            result={
                "subject": fallback_subject,
                "body_html": fallback_body,
                "is_fallback": True,
            },
            confidence=0.6,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s6_start,
        )

    _add_step(6, "message", "message", s6_out)
    context["email_subject"] = s6_out.result["subject"]
    context["email_body_html"] = s6_out.result["body_html"]

    # ──────────────────────────────────────────
    # Step 7: saas_writer — メール送信（SendGrid）
    # ──────────────────────────────────────────
    s7_start = int(time.time() * 1000)
    to_email = lead.get("contact_email", "")
    to_name = lead.get("contact_name", "")
    email_sent = False

    try:
        if not dry_run and to_email:
            email_sent = await _send_email_via_sendgrid(
                to_email=to_email,
                to_name=to_name,
                subject=context["email_subject"],
                body_html=context["email_body_html"],
                pdf_bytes=context["pdf_bytes"],
                proposal_number=proposal_number,
            )
        else:
            logger.info(
                f"dry_run={dry_run} or no email: skip SendGrid "
                f"to={to_email or '(empty)'}"
            )
            email_sent = True  # dry-run は成功扱い

        s7_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=email_sent,
            result={
                "email_sent": email_sent,
                "to_email": to_email,
                "subject": context["email_subject"],
                "dry_run": dry_run,
            },
            confidence=1.0 if email_sent else 0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )
    except Exception as e:
        logger.error(f"email send error: {e}")
        s7_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=False,
            result={"error": str(e), "email_sent": False},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )

    _add_step(7, "saas_writer_email", "saas_writer", s7_out)
    # メール送信失敗は警告に留め、パイプラインは継続（DB 保存は行う）
    if not s7_out.success:
        steps[-1].warning = (
            steps[-1].warning or ""
        ) + " メール送信失敗（DB 保存は継続）"

    # ──────────────────────────────────────────
    # Step 8: saas_writer — proposals / opportunities 更新 + Slack 通知
    # ──────────────────────────────────────────
    s8_start = int(time.time() * 1000)
    sent_at_iso = date.today().isoformat() if email_sent else None

    # proposals テーブルへの INSERT
    proposal_record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "company_id": company_id,
        "opportunity_id": opportunity_id or str(uuid.uuid4()),  # なければ仮 UUID
        "version": 1,
        "title": f"{lead.get('company_name', '')} 様向け提案書 {proposal_number}",
        "content": context["proposal_json"],
        "pdf_storage_path": storage_path,
        "sent_to": to_email or None,
        "sent_at": sent_at_iso,
        "status": "sent" if email_sent else "draft",
    }

    try:
        db_out = await run_saas_writer(MicroAgentInput(
            company_id=company_id,
            agent_name="saas_writer",
            payload={
                "service": "supabase",
                "operation": "insert_proposal",
                "params": {
                    "table": "proposals",
                    "data": proposal_record,
                    "action": "insert",
                },
                "approved": True,
                "dry_run": dry_run,
            },
            context=context,
        ))
    except Exception as e:
        logger.error(f"saas_writer (proposals insert) error: {e}")
        db_out = MicroAgentOutput(
            agent_name="saas_writer",
            success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s8_start,
        )

    # opportunities ステージ更新（opportunity_id がある場合のみ）
    if opportunity_id and not dry_run:
        try:
            await run_saas_writer(MicroAgentInput(
                company_id=company_id,
                agent_name="saas_writer",
                payload={
                    "service": "supabase",
                    "operation": "update_opportunity_stage",
                    "params": {
                        "table": "opportunities",
                        "id": opportunity_id,
                        "data": {"stage": "proposal"},
                        "action": "update",
                    },
                    "approved": True,
                    "dry_run": False,
                },
                context=context,
            ))
        except Exception as e:
            logger.warning(f"opportunity stage update failed (non-fatal): {e}")

    # 通知（Slack未設定時はログ出力、失敗は無視）
    if not dry_run:
        try:
            notify_text = (
                f":memo: 提案書送付完了\n"
                f"*企業名*: {lead.get('company_name', '')}\n"
                f"*業種*: {INDUSTRY_LABELS.get(industry, industry)}\n"
                f"*提案番号*: {proposal_number}\n"
                f"*送付先*: {to_email or '（メールなし）'}\n"
                f"*PDF サイズ*: {context.get('pdf_size_kb', 0)} KB"
            )
            slack_url = os.environ.get("SLACK_WEBHOOK_URL")
            if slack_url:
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(slack_url, json={"text": notify_text, "channel": "#proposals"})
            else:
                logger.info(f"[通知][#proposals] {notify_text}")
        except Exception as e:
            logger.warning(f"通知失敗（無視）: {e}")

    s8_out = MicroAgentOutput(
        agent_name="saas_writer",
        success=db_out.success,
        result={
            "proposal_id": proposal_record["id"],
            "opportunity_id": opportunity_id,
            "db_operation_id": db_out.result.get("operation_id"),
            "dry_run": dry_run,
        },
        confidence=1.0 if db_out.success else 0.5,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s8_start,
    )

    _add_step(8, "saas_writer_db", "saas_writer", s8_out)

    # ──────────────────────────────────────────
    # anomaly_detector: 提案書金額の桁間違い検知
    # ──────────────────────────────────────────
    proposal_json_ctx = context.get("proposal_json", {})
    _anomaly_items: list[dict[str, Any]] = []
    for _mod in proposal_json_ctx.get("modules", []):
        _price = _mod.get("monthly_price")
        if _price is not None:
            _anomaly_items.append({"name": _mod.get("name", "月額料金"), "value": _price})
    _roi = proposal_json_ctx.get("roi_estimate", {})
    if _roi.get("initial_cost") is not None:
        _anomaly_items.append({"name": "初期費用", "value": _roi["initial_cost"]})
    if _roi.get("savings_monthly") is not None:
        _anomaly_items.append({"name": "月次削減額", "value": _roi["savings_monthly"]})

    if _anomaly_items:
        try:
            anomaly_out = await run_anomaly_detector(MicroAgentInput(
                company_id=company_id,
                agent_name="anomaly_detector",
                payload={
                    "items": _anomaly_items,
                    "detect_modes": ["digit_error", "range"],
                },
                context=context,
            ))
            _add_step(9, "anomaly_detector", "anomaly_detector", anomaly_out)
        except Exception as _ae:
            logger.warning(f"anomaly_detector 非致命的エラー（スキップ）: {_ae}")
            anomaly_out = None
    else:
        anomaly_out = None

    # ──────────────────────────────────────────
    # 最終結果
    # ──────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    final_output: dict[str, Any] = {
        "proposal_number": proposal_number,
        "proposal_id": proposal_record["id"],
        "pdf_storage_path": storage_path,
        "pdf_size_kb": context.get("pdf_size_kb", 0),
        "email_sent": email_sent,
        "to_email": to_email,
        "industry": industry,
        "proposal_json": context["proposal_json"],
    }

    if (
        anomaly_out is not None
        and anomaly_out.success
        and anomaly_out.result.get("anomaly_count", 0) > 0
    ):
        final_output["anomaly_warnings"] = anomaly_out.result["anomalies"]

    logger.info(
        f"proposal_generation_pipeline complete: "
        f"company={lead.get('company_name', '')}, "
        f"industry={industry}, "
        f"proposal={proposal_number}, "
        f"email_sent={email_sent}, "
        f"cost=¥{total_cost_yen:.2f}, "
        f"{total_duration}ms"
    )

    return ProposalGenerationResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
    )
