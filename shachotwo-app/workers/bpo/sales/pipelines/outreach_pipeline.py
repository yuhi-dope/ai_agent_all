"""
マーケ パイプライン⓪ — 企業リサーチ＆アウトリーチ

吸収元: shachotwo-マーケAI/research/, outreach/, signals/, scheduling/

トリガー:
  - ScheduleWatcher 毎日 8:00（自動）
  - 手動トリガー（company_list を直接渡す場合）

Steps:
  Step 1: gbizinfo_enrich      Google Sheets の未送信リストを取得し gBizINFO で法人情報エンリッチ
  Step 2: company_researcher   企業情報 → LLMペイン推定・規模判定・提案トーン決定
  Step 3: lp_generator         業種×ペインに合わせたカスタム LP 内容を生成
  Step 4: outreach_composer    パーソナライズ件名＋本文メールを生成
  Step 5: outreach_executor    フォームあり → Playwright 送信 / なし → SendGrid メール送信
             + ペース制御（400件/日。特定電子メール法準拠）
  Step 6: signal_detector      LP閲覧・CTAクリック・資料DL シグナルを評価し温度判定
  Step 7: calendar_booker      hot リードに対して Google Calendar 空き枠提示 + Meet 予約作成
  Step 8: leads_writer         leads テーブル保存 + lead_activities ログ + Google Sheets 同期
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.company_researcher import run_company_researcher
from workers.micro.signal_detector import run_signal_detector
from workers.micro.calendar_booker import run_calendar_booker
from workers.micro.generator import run_document_generator
from workers.micro.saas_writer import run_saas_writer
from workers.connector.gbizinfo import GBizInfoConnector
from workers.connector.playwright_form import PlaywrightFormConnector
from workers.connector.google_sheets import GoogleSheetsConnector
from workers.connector.base import ConnectorConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 1日の最大アウトリーチ件数（特定電子メール法：商業メールは受信者の同意が必要。
# 問い合わせフォームは事業者間のBtoBを前提とし送信。）
DAILY_OUTREACH_LIMIT = 400

# confidence 警告ライン
CONFIDENCE_WARNING_THRESHOLD = 0.60

# 各企業への処理間隔（秒）—— 負荷分散 & レート制限対策
INTER_COMPANY_DELAY_SEC = 2.0

# ---------------------------------------------------------------------------
# 製造業特化定数
# ---------------------------------------------------------------------------

# デフォルトのターゲット業種
DEFAULT_TARGET_INDUSTRY = "manufacturing"

# 製造業向けペインポイント補完リスト
# company_researcher がペインを取得できなかった場合のフォールバック
MANUFACTURING_PAIN_FALLBACK: list[dict[str, str]] = [
    {
        "category": "見積業務",
        "detail": "図面・仕様書から見積金額を手計算しており、回答まで数日かかる",
        "appeal_message": "AI見積エンジンで図面解析〜金額算出を自動化、回答スピードを大幅短縮",
    },
    {
        "category": "暗黙知共有",
        "detail": "熟練工のノウハウが属人化しており、若手への技術伝承が難しい",
        "appeal_message": "音声・手順書から自動でナレッジDB化し、暗黙知をマニュアルに変換",
    },
    {
        "category": "多品種少量生産",
        "detail": "多品種少量の受注管理と工程スケジュール調整に多大な工数がかかっている",
        "appeal_message": "受注〜工程計画をAIが自動最適化し、段取り替えのロスを削減",
    },
    {
        "category": "品質管理",
        "detail": "検査結果の記録・集計が手作業で、不良原因の分析に時間がかかる",
        "appeal_message": "検査データをリアルタイム集計しAIが異常を即時検知、品質コストを削減",
    },
]

# 製造業向けメール件名テンプレート（バリアント別）
MANUFACTURING_SUBJECT_TEMPLATES: dict[str, str] = {
    "A": "【製造業向け】現場の暗黙知・見積業務をAIで自動化｜シャチョツー",
    "B": "導入事例あり｜製造業の見積回答を3日→即日に短縮した方法",
    "C": "多品種少量の工程管理・品質記録の負担、AIで解決できます",
}


# ---------------------------------------------------------------------------
# 結果モデル
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """1ステップの実行結果（estimation_pipeline と共通パターン）"""
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
class OutreachRecord:
    """企業1件分のアウトリーチ実行記録"""
    company_name: str
    corporate_number: str
    industry: str
    outreach_method: str          # "form" | "email" | "skipped"
    sent: bool
    temperature: str              # "hot" | "warm" | "cold" | "unknown"
    meeting_booked: bool
    lead_id: str | None           # leads テーブルへの保存後のID
    cost_yen: float
    duration_ms: int
    error: str | None = None


@dataclass
class OutreachPipelineResult:
    """アウトリーチパイプライン全体の実行結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    records: list[OutreachRecord] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        sent = sum(1 for r in self.records if r.sent)
        hot = sum(1 for r in self.records if r.temperature == "hot")
        booked = sum(1 for r in self.records if r.meeting_booked)
        lines = [
            f"{'OK' if self.success else 'NG'} アウトリーチパイプライン",
            f"  対象企業数: {len(self.records)}社",
            f"  送信成功: {sent}件",
            f"  HOTリード: {hot}件",
            f"  商談予約: {booked}件",
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


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_step(
    step_no: int,
    step_name: str,
    agent_name: str,
    out: MicroAgentOutput,
) -> StepResult:
    """MicroAgentOutput → StepResult 変換。confidence 警告付き。"""
    warn = None
    if out.confidence < CONFIDENCE_WARNING_THRESHOLD:
        warn = f"confidence低 ({out.confidence:.2f} < {CONFIDENCE_WARNING_THRESHOLD})"
    return StepResult(
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


def _fail(
    step_name: str,
    steps: list[StepResult],
    pipeline_start: int,
) -> OutreachPipelineResult:
    return OutreachPipelineResult(
        success=False,
        steps=steps,
        total_cost_yen=sum(s.cost_yen for s in steps),
        total_duration_ms=int(time.time() * 1000) - pipeline_start,
        failed_step=step_name,
    )


# ---------------------------------------------------------------------------
# メインパイプライン
# ---------------------------------------------------------------------------

async def run_outreach_pipeline(
    company_id: str,
    input_data: dict[str, Any],
) -> OutreachPipelineResult:
    """
    マーケ パイプライン⓪ — 企業リサーチ＆アウトリーチ。

    Args:
        company_id:
            テナントID。全マイクロエージェントに引き渡す。
        input_data:
            以下のいずれかを受け付ける:

            (A) Google Sheets から未送信企業を自動取得する場合:
                {
                    "source": "google_sheets",
                    "sheets_credentials_path": "/path/to/creds.json",
                    "spreadsheet_id": "1aBcD...",
                    "sheet_name": "営業リスト",          # optional, default: "営業リスト"
                    "limit": 50,                         # optional, 1回の実行件数上限
                }

            (B) 企業リストを直渡しする場合:
                {
                    "source": "direct",
                    "companies": [
                        {
                            "name": "株式会社サンプル",
                            "industry": "建設業",
                            "hp_url": "https://...",
                            "form_url": "https://...#contact",  # optional
                            "contact_email": "info@...",        # optional
                            "employee_count": 30,               # optional
                            "row_number": 5,                    # Sheets 行番号 optional
                        },
                        ...
                    ],
                }

            共通オプション:
                "gbizinfo_api_token": str     — gBizINFO API トークン
                "sendgrid_api_key": str       — メール送信用 SendGrid API キー
                "sender_name": str            — 送信者名（デフォルト: "シャチョツー")
                "sender_email": str           — 送信者メールアドレス
                "dry_run": bool               — True のとき送信をスキップし結果だけ返す
                "signals": list[dict]         — Step 6 に渡すシグナルイベントリスト（省略可）
                    各要素: {event_type, company_id, metadata}

    Returns:
        OutreachPipelineResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []

    # target_industry: デフォルトは製造業。industry パラメータで他業種に切り替え可能。
    target_industry: str = input_data.get("target_industry", DEFAULT_TARGET_INDUSTRY)
    is_manufacturing: bool = target_industry == "manufacturing"

    context: dict[str, Any] = {
        "company_id": company_id,
        "dry_run": input_data.get("dry_run", False),
        "target_industry": target_industry,
        "is_manufacturing": is_manufacturing,
    }

    # -----------------------------------------------------------------------
    # Step 1: gbizinfo_enrich
    #   - Google Sheets から未送信企業リストを取得（source="google_sheets"）
    #   - gBizINFO 製造業専用検索（source="gbizinfo_manufacturing"）
    #   - 直接リスト指定（source="direct"、デフォルト）
    #   - gBizINFO API で法人番号・従業員数・代表者名をエンリッチ
    #   - 製造業の場合: 従業員規模フィルタ + ペインポイント補完を実施
    # -----------------------------------------------------------------------
    s1_start = int(time.time() * 1000)
    raw_companies: list[dict[str, Any]] = []

    try:
        source = input_data.get("source", "direct")
        api_token = input_data.get("gbizinfo_api_token", "")

        if source == "google_sheets":
            sheets = GoogleSheetsConnector(ConnectorConfig(
                connector_type="google_sheets",
                company_id=company_id,
                credentials={
                    "credentials_path": input_data.get("sheets_credentials_path", ""),
                    "spreadsheet_id": input_data.get("spreadsheet_id", ""),
                },
            ))
            sheet_name = input_data.get("sheet_name", "営業リスト")
            limit = min(input_data.get("limit", DAILY_OUTREACH_LIMIT), DAILY_OUTREACH_LIMIT)
            all_rows = await sheets.get_unsent_companies(sheet_name)
            raw_companies = all_rows[:limit]
            logger.info(f"Sheets から {len(raw_companies)} 社取得（limit={limit}）")

        elif source == "gbizinfo_manufacturing" and api_token:
            # gBizINFO 製造業専用検索: 従業員10〜300名の中小製造業を直接取得
            gbiz = GBizInfoConnector(ConnectorConfig(
                tool_name="gbizinfo",
                credentials={"api_token": api_token},
            ))
            limit = min(input_data.get("limit", 50), DAILY_OUTREACH_LIMIT)
            raw_companies = await gbiz.search_manufacturing_companies(
                prefecture=input_data.get("prefecture", ""),
                min_employees=input_data.get("min_employees", 10),
                max_employees=input_data.get("max_employees", 300),
                limit=limit,
            )
            # search_manufacturing_companies は map_to_company_data 済み形式で返す
            # industry フィールドを "製造業" に統一
            for c in raw_companies:
                c.setdefault("industry", "製造業")
            logger.info(f"gBizINFO 製造業検索: {len(raw_companies)} 社取得")

        else:
            raw_companies = input_data.get("companies", [])
            logger.info(f"直接指定 {len(raw_companies)} 社")

        if not raw_companies:
            s1_out = MicroAgentOutput(
                agent_name="gbizinfo_enrich",
                success=False,
                result={"error": "処理対象企業が0件です"},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_start,
            )
            steps.append(_make_step(1, "gbizinfo_enrich", "gbizinfo_enrich", s1_out))
            return _fail("gbizinfo_enrich", steps, pipeline_start)

        # gBizINFO エンリッチ（APIトークンがある場合かつ source が gbizinfo_manufacturing 以外）
        enriched_companies: list[dict[str, Any]] = []
        enrich_success_count = 0

        if api_token and source != "gbizinfo_manufacturing":
            gbiz = GBizInfoConnector(ConnectorConfig(
                tool_name="gbizinfo",
                credentials={"api_token": api_token},
            ))
            for company in raw_companies:
                try:
                    records = await gbiz.read_records(
                        "search",
                        {"name": company.get("name", ""), "limit": 1},
                    )
                    if records:
                        enriched = GBizInfoConnector.map_to_company_data(records[0])
                        # 元データを上書きせず enriched で補完
                        merged = {**enriched, **{k: v for k, v in company.items() if v}}
                        enriched_companies.append(merged)
                        enrich_success_count += 1
                    else:
                        enriched_companies.append(company)
                except Exception as e:
                    logger.warning(f"gBizINFO エンリッチ失敗 ({company.get('name')}): {e}")
                    enriched_companies.append(company)
        else:
            enriched_companies = raw_companies
            if not api_token:
                logger.info("gbizinfo_api_token 未設定のためエンリッチをスキップ")
            enrich_success_count = len(raw_companies)

        # ------------------------------------------------------------------
        # 製造業フィルタ: source="direct" or "google_sheets" の場合に追加適用
        #   - target_industry=="manufacturing" のとき非製造業企業を除外
        #   - 製造業フラグを各企業に付与し、後続ステップの context を強化
        # ------------------------------------------------------------------
        if is_manufacturing and source in ("direct", "google_sheets"):
            from workers.connector.gbizinfo import MANUFACTURING_KEYWORDS
            before_count = len(enriched_companies)
            filtered: list[dict[str, Any]] = []
            for c in enriched_companies:
                industry_text = " ".join([
                    c.get("industry", ""),
                    c.get("business_overview", ""),
                    c.get("name", ""),
                ])
                if any(kw in industry_text for kw in MANUFACTURING_KEYWORDS) or \
                        "製造" in c.get("industry", ""):
                    filtered.append(c)
                else:
                    logger.debug(
                        f"製造業フィルタで除外: {c.get('name', '')} "
                        f"(industry={c.get('industry', '')})"
                    )
            enriched_companies = filtered
            logger.info(
                f"製造業フィルタ: {before_count}社 → {len(enriched_companies)}社"
                f"（{before_count - len(enriched_companies)}社除外）"
            )

        # 製造業ペインポイント補完: company_researcher に渡す前の前処理
        if is_manufacturing:
            for c in enriched_companies:
                c["_is_manufacturing_target"] = True
                if not c.get("industry") or c.get("industry") in ("", "その他"):
                    c["industry"] = "製造業"
                if not c.get("manufacturing_pain_hints"):
                    c["manufacturing_pain_hints"] = [
                        p["category"] for p in MANUFACTURING_PAIN_FALLBACK
                    ]

        enrich_rate = enrich_success_count / len(raw_companies) if raw_companies else 0.0
        s1_out = MicroAgentOutput(
            agent_name="gbizinfo_enrich",
            success=True,
            result={
                "company_count": len(enriched_companies),
                "enrich_success": enrich_success_count,
                "enrich_rate": round(enrich_rate, 3),
                "target_industry": target_industry,
                "manufacturing_filter_applied": is_manufacturing,
            },
            confidence=max(0.5, enrich_rate) if api_token else 0.7,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    except Exception as e:
        logger.error(f"gbizinfo_enrich error: {e}")
        s1_out = MicroAgentOutput(
            agent_name="gbizinfo_enrich",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    steps.append(_make_step(1, "gbizinfo_enrich", "gbizinfo_enrich", s1_out))
    if not s1_out.success:
        return _fail("gbizinfo_enrich", steps, pipeline_start)

    context["companies"] = enriched_companies

    # -----------------------------------------------------------------------
    # Step 2: company_researcher（ペイン推定・規模判定）
    #   全企業を並列処理。LLMコスト節約のため asyncio.gather で一括投げ。
    # -----------------------------------------------------------------------
    s2_start = int(time.time() * 1000)
    try:
        async def _research_one(company: dict[str, Any]) -> dict[str, Any]:
            payload: dict[str, Any] = {
                "name": company.get("name", ""),
                "industry": company.get("industry", ""),
                "employee_count": company.get("employee_count"),
                "business_overview": company.get("business_overview", ""),
                "raw_html": company.get("raw_html", ""),
                "job_postings": company.get("job_postings", []),
            }
            # 製造業ターゲットの場合: ペイン推定の優先カテゴリとフォールバックを渡す
            if company.get("_is_manufacturing_target"):
                payload["pain_focus_categories"] = company.get(
                    "manufacturing_pain_hints",
                    [p["category"] for p in MANUFACTURING_PAIN_FALLBACK],
                )
                payload["pain_fallback"] = MANUFACTURING_PAIN_FALLBACK
            out = await run_company_researcher(MicroAgentInput(
                company_id=company_id,
                agent_name="company_researcher",
                payload=payload,
                context=context,
            ))
            return {
                "company": company,
                "researcher_out": out,
            }

        research_results = await asyncio.gather(
            *[_research_one(c) for c in enriched_companies],
            return_exceptions=True,
        )

        researched: list[dict[str, Any]] = []
        total_research_cost = 0.0
        success_count = 0

        for res in research_results:
            if isinstance(res, Exception):
                logger.warning(f"company_researcher 例外: {res}")
                continue
            out: MicroAgentOutput = res["researcher_out"]
            total_research_cost += out.cost_yen
            if out.success:
                success_count += 1
            researched.append({
                **res["company"],
                "pain_points": out.result.get("pain_points", []),
                "scale": out.result.get("scale", "小規模"),
                "tone": out.result.get("tone", ""),
                "industry_tasks": out.result.get("industry_tasks", []),
                "industry_appeal": out.result.get("industry_appeal", ""),
                "researcher_success": out.success,
            })

        s2_confidence = success_count / len(enriched_companies) if enriched_companies else 0.0
        s2_out = MicroAgentOutput(
            agent_name="company_researcher",
            success=bool(researched),
            result={"researched_count": len(researched), "success_count": success_count},
            confidence=round(s2_confidence, 3),
            cost_yen=total_research_cost,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    except Exception as e:
        logger.error(f"company_researcher batch error: {e}")
        s2_out = MicroAgentOutput(
            agent_name="company_researcher",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    steps.append(_make_step(2, "company_researcher", "company_researcher", s2_out))
    if not s2_out.success:
        return _fail("company_researcher", steps, pipeline_start)

    context["researched"] = researched

    # -----------------------------------------------------------------------
    # Step 3: lp_generator
    #   業種×ペインに合わせたカスタム LP 内容を生成。
    #   generator.py（既存）の "outreach_lp" テンプレートを利用。
    # -----------------------------------------------------------------------
    s3_start = int(time.time() * 1000)
    try:
        async def _generate_lp_one(company: dict[str, Any]) -> dict[str, Any]:
            pain_points_raw = company.get("pain_points", [])
            pain_summary = "; ".join(
                p.get("detail", p) if isinstance(p, dict) else str(p)
                for p in pain_points_raw[:2]
            )
            out = await run_document_generator(MicroAgentInput(
                company_id=company_id,
                agent_name="lp_generator",
                payload={
                    "template_name": "outreach_lp",
                    "data": {
                        "company_name": company.get("name", ""),
                        "industry": company.get("industry", ""),
                        "scale": company.get("scale", ""),
                        "tone": company.get("tone", ""),
                        "pain_points": pain_summary,
                        "industry_appeal": company.get("industry_appeal", ""),
                    },
                    "format": "text",
                },
                context=context,
            ))
            return {"company": company, "lp_content": out.result.get("content", ""), "cost_yen": out.cost_yen}

        lp_results = await asyncio.gather(
            *[_generate_lp_one(c) for c in researched],
            return_exceptions=True,
        )

        lp_enriched: list[dict[str, Any]] = []
        total_lp_cost = 0.0

        for res in lp_results:
            if isinstance(res, Exception):
                logger.warning(f"lp_generator 例外: {res}")
                continue
            total_lp_cost += res["cost_yen"]
            lp_enriched.append({
                **res["company"],
                "lp_content": res["lp_content"],
            })

        s3_out = MicroAgentOutput(
            agent_name="lp_generator",
            success=bool(lp_enriched),
            result={"lp_count": len(lp_enriched)},
            confidence=0.85,
            cost_yen=total_lp_cost,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    except Exception as e:
        logger.error(f"lp_generator batch error: {e}")
        s3_out = MicroAgentOutput(
            agent_name="lp_generator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    steps.append(_make_step(3, "lp_generator", "document_generator", s3_out))
    if not s3_out.success:
        return _fail("lp_generator", steps, pipeline_start)

    context["lp_enriched"] = lp_enriched

    # -----------------------------------------------------------------------
    # Step 4: outreach_composer（パーソナライズメール生成）
    #   generator.py の "outreach_email" テンプレートで件名＋本文を生成。
    # -----------------------------------------------------------------------
    s4_start = int(time.time() * 1000)
    try:
        async def _compose_one(company: dict[str, Any]) -> dict[str, Any]:
            pain_points_raw = company.get("pain_points", [])
            pain_summary = "; ".join(
                p.get("appeal_message", p.get("detail", "")) if isinstance(p, dict) else str(p)
                for p in pain_points_raw[:2]
            )
            out = await run_document_generator(MicroAgentInput(
                company_id=company_id,
                agent_name="outreach_composer",
                payload={
                    "template_name": "outreach_email",
                    "data": {
                        "company_name": company.get("name", ""),
                        "industry": company.get("industry", ""),
                        "tone": company.get("tone", ""),
                        "pain_summary": pain_summary,
                        "industry_appeal": company.get("industry_appeal", ""),
                        "sender_name": input_data.get("sender_name", "シャチョツー"),
                    },
                    "format": "json",
                },
                context=context,
            ))
            # generator は JSON 文字列を返すことがある。パース試行。
            import json
            content_raw = out.result.get("content", "{}")
            try:
                email_data = json.loads(content_raw)
            except (json.JSONDecodeError, TypeError):
                # 製造業の場合は専用件名テンプレートにフォールバック
                if company.get("_is_manufacturing_target"):
                    fallback_subject = MANUFACTURING_SUBJECT_TEMPLATES.get("A")
                else:
                    fallback_subject = f"【{company.get('industry', '')}向け】シャチョツーご紹介"
                email_data = {"subject": fallback_subject, "body": content_raw}

            return {
                "company": company,
                "email_subject": email_data.get("subject", ""),
                "email_body": email_data.get("body", ""),
                "cost_yen": out.cost_yen,
            }

        compose_results = await asyncio.gather(
            *[_compose_one(c) for c in lp_enriched],
            return_exceptions=True,
        )

        composed: list[dict[str, Any]] = []
        total_compose_cost = 0.0

        for res in compose_results:
            if isinstance(res, Exception):
                logger.warning(f"outreach_composer 例外: {res}")
                continue
            total_compose_cost += res["cost_yen"]
            composed.append({
                **res["company"],
                "email_subject": res["email_subject"],
                "email_body": res["email_body"],
            })

        s4_out = MicroAgentOutput(
            agent_name="outreach_composer",
            success=bool(composed),
            result={"composed_count": len(composed)},
            confidence=0.85,
            cost_yen=total_compose_cost,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    except Exception as e:
        logger.error(f"outreach_composer batch error: {e}")
        s4_out = MicroAgentOutput(
            agent_name="outreach_composer",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    steps.append(_make_step(4, "outreach_composer", "document_generator", s4_out))
    if not s4_out.success:
        return _fail("outreach_composer", steps, pipeline_start)

    context["composed"] = composed

    # -----------------------------------------------------------------------
    # Step 5: outreach_executor
    #   フォームあり → Playwright 自動送信
    #   フォームなし + メール → SendGrid 送信
    #   dry_run=True のとき実際の送信をスキップ
    #   特定電子メール法準拠: 1日 DAILY_OUTREACH_LIMIT 件を上限とし、
    #   企業間に INTER_COMPANY_DELAY_SEC 秒のインターバルを挟む。
    # -----------------------------------------------------------------------
    s5_start = int(time.time() * 1000)
    records: list[OutreachRecord] = []
    dry_run: bool = context["dry_run"]
    sendgrid_api_key: str = input_data.get("sendgrid_api_key", "")
    sender_email: str = input_data.get("sender_email", "")
    sender_name: str = input_data.get("sender_name", "シャチョツー")

    try:
        form_connector = PlaywrightFormConnector(ConnectorConfig(
            tool_name="playwright_form",
            credentials={},
        ))

        sent_count = 0
        for company in composed:
            if sent_count >= DAILY_OUTREACH_LIMIT:
                logger.info(f"日次上限 {DAILY_OUTREACH_LIMIT} 件に到達。残件をスキップ。")
                break

            rec_start = int(time.time() * 1000)
            method = "skipped"
            sent = False
            error_msg: str | None = None

            form_url: str = company.get("form_url", "")
            contact_email: str = company.get("contact_email", "")

            try:
                if dry_run:
                    # dry_run: 送信せず成功扱い
                    method = "form" if form_url else ("email" if contact_email else "skipped")
                    sent = bool(form_url or contact_email)

                elif form_url:
                    # Playwright フォーム送信
                    result = await form_connector.write_record(form_url, {
                        "company": sender_name,
                        "name": sender_name,
                        "email": sender_email,
                        "message": company["email_body"],
                        "subject": company.get("email_subject", ""),
                    })
                    method = "form"
                    sent = result.get("status") == "success"
                    if not sent:
                        error_msg = result.get("detail", "")

                elif contact_email and sendgrid_api_key:
                    # SendGrid メール送信
                    import httpx
                    resp = await _send_via_sendgrid(
                        api_key=sendgrid_api_key,
                        to_email=contact_email,
                        from_email=sender_email,
                        from_name=sender_name,
                        subject=company.get("email_subject", f"【{company.get('industry', '')}向け】シャチョツーご紹介"),
                        body=company.get("email_body", ""),
                    )
                    method = "email"
                    sent = resp
                    if not sent:
                        error_msg = "SendGrid 送信失敗"
                else:
                    method = "skipped"
                    error_msg = "form_url も contact_email も未設定"

            except Exception as e:
                error_msg = str(e)
                logger.warning(f"アウトリーチ実行エラー ({company.get('name')}): {e}")

            if sent:
                sent_count += 1
                # 企業間インターバル（最後の1件はスキップ）
                if sent_count < min(len(composed), DAILY_OUTREACH_LIMIT):
                    await asyncio.sleep(INTER_COMPANY_DELAY_SEC)

            records.append(OutreachRecord(
                company_name=company.get("name", ""),
                corporate_number=company.get("corporate_number", ""),
                industry=company.get("industry", ""),
                outreach_method=method,
                sent=sent,
                temperature="unknown",
                meeting_booked=False,
                lead_id=None,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - rec_start,
                error=error_msg,
            ))

        s5_out = MicroAgentOutput(
            agent_name="outreach_executor",
            success=True,
            result={
                "total": len(records),
                "sent": sent_count,
                "form": sum(1 for r in records if r.outreach_method == "form"),
                "email": sum(1 for r in records if r.outreach_method == "email"),
                "skipped": sum(1 for r in records if r.outreach_method == "skipped"),
                "dry_run": dry_run,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    except Exception as e:
        logger.error(f"outreach_executor error: {e}")
        s5_out = MicroAgentOutput(
            agent_name="outreach_executor",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    steps.append(_make_step(5, "outreach_executor", "outreach_executor", s5_out))
    if not s5_out.success:
        return _fail("outreach_executor", steps, pipeline_start)

    # -----------------------------------------------------------------------
    # Step 6: signal_detector（温度判定）
    #   input_data["signals"] に外部から渡されたシグナルイベントを処理。
    #   シグナルがない場合は全件 "cold" 扱いで空結果を返す。
    # -----------------------------------------------------------------------
    s6_start = int(time.time() * 1000)
    signals_input: list[dict[str, Any]] = input_data.get("signals", [])

    if signals_input:
        try:
            s6_out = await run_signal_detector(MicroAgentInput(
                company_id=company_id,
                agent_name="signal_detector",
                payload={"events": signals_input},
                context=context,
            ))
        except Exception as e:
            logger.warning(f"signal_detector error: {e}")
            s6_out = MicroAgentOutput(
                agent_name="signal_detector",
                success=True,
                result={"classifications": [], "followup_actions": [], "summary": {}, "warning": str(e)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s6_start,
            )
    else:
        # シグナルなし — スキップ扱いで OK
        s6_out = MicroAgentOutput(
            agent_name="signal_detector",
            success=True,
            result={
                "classifications": [],
                "followup_actions": [],
                "summary": {"hot": 0, "confirmed": 0, "warm": 0, "cold": len(records), "total": len(records)},
                "note": "シグナルなし。全件 cold 扱い。",
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s6_start,
        )

    steps.append(_make_step(6, "signal_detector", "signal_detector", s6_out))
    # 温度をレコードに反映
    classifications = s6_out.result.get("classifications", [])
    company_temp_map: dict[str, str] = {
        c["company_id"]: c["temperature"] for c in classifications
    }
    for rec in records:
        rec.temperature = company_temp_map.get(rec.company_name, "cold")

    context["signal_summary"] = s6_out.result.get("summary", {})
    hot_companies = [
        c for rec, c in zip(records, composed)
        if rec.temperature == "hot"
    ]

    # -----------------------------------------------------------------------
    # Step 7: calendar_booker（hot リードへの商談予約）
    #   hot シグナルがある企業にのみ実行。空き枠を提示し Meet 予約を作成する。
    # -----------------------------------------------------------------------
    s7_start = int(time.time() * 1000)
    booked_count = 0

    if hot_companies:
        try:
            # 空き枠を1回取得して全 hot 企業に再利用
            slots_out = await run_calendar_booker(MicroAgentInput(
                company_id=company_id,
                agent_name="calendar_booker",
                payload={"action": "get_slots", "days_ahead": 5},
                context=context,
            ))

            slots = slots_out.result.get("slots", [])
            if slots and not dry_run:
                # 先頭スロットを最初の hot 企業に割り当て（1件ずつ別スロットが理想だが MVP では1枠）
                first_hot = hot_companies[0]
                meet_out = await run_calendar_booker(MicroAgentInput(
                    company_id=company_id,
                    agent_name="calendar_booker",
                    payload={
                        "action": "create_meeting",
                        "slot": slots[0],
                        "company_name": first_hot.get("name", ""),
                        "contact_name": first_hot.get("contact_name", ""),
                        "attendee_email": first_hot.get("contact_email", ""),
                    },
                    context=context,
                ))
                if meet_out.success:
                    booked_count = 1
                    # レコードに反映
                    for rec in records:
                        if rec.company_name == first_hot.get("name"):
                            rec.meeting_booked = True
                            break
                context["meeting_info"] = meet_out.result.get("meeting", {})

            s7_out = MicroAgentOutput(
                agent_name="calendar_booker",
                success=True,
                result={
                    "hot_count": len(hot_companies),
                    "slots_available": len(slots),
                    "booked_count": booked_count,
                    "dry_run": dry_run,
                },
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s7_start,
            )
        except Exception as e:
            logger.warning(f"calendar_booker error: {e}")
            s7_out = MicroAgentOutput(
                agent_name="calendar_booker",
                success=True,  # 非致命的
                result={"warning": str(e), "booked_count": 0},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s7_start,
            )
    else:
        s7_out = MicroAgentOutput(
            agent_name="calendar_booker",
            success=True,
            result={"hot_count": 0, "booked_count": 0, "note": "hot リードなし"},
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s7_start,
        )

    steps.append(_make_step(7, "calendar_booker", "calendar_booker", s7_out))

    # -----------------------------------------------------------------------
    # Step 8: leads_writer
    #   - leads テーブルへの保存（Supabase 直接 or saas_writer 経由）
    #   - lead_activities にアウトリーチログを記録
    #   - Google Sheets の送信済フラグを更新
    # -----------------------------------------------------------------------
    s8_start = int(time.time() * 1000)
    sheets_sync_count = 0

    try:
        # Google Sheets 更新（row_number があるレコードのみ）
        sheets_creds = {
            "credentials_path": input_data.get("sheets_credentials_path", ""),
            "spreadsheet_id": input_data.get("spreadsheet_id", ""),
        }
        has_sheets = bool(input_data.get("spreadsheet_id") and input_data.get("sheets_credentials_path"))

        if has_sheets and not dry_run:
            sheets = GoogleSheetsConnector(ConnectorConfig(
                connector_type="google_sheets",
                company_id=company_id,
                credentials=sheets_creds,
            ))
            now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for rec, company in zip(records, composed):
                row_num: int | None = company.get("row_number")
                if not row_num:
                    continue
                try:
                    if rec.sent:
                        await sheets.mark_sent(row_num, rec.outreach_method, now_ts)
                        sheets_sync_count += 1
                    elif rec.error:
                        await sheets.mark_failed(row_num, rec.error[:50])
                except Exception as e:
                    logger.warning(f"Sheets 更新失敗 (row={row_num}): {e}")

        # leads テーブル保存はルーター側で行う（パイプラインはDBに直接触れない設計）
        # final_output に保存用データを詰めて返す。
        leads_payload = [
            {
                "company_name": rec.company_name,
                "industry": rec.industry,
                "corporate_number": rec.corporate_number,
                "source": "outreach",
                "outreach_method": rec.outreach_method,
                "sent": rec.sent,
                "temperature": rec.temperature,
                "meeting_booked": rec.meeting_booked,
                "pain_points": next(
                    (c.get("pain_points", []) for c in composed if c.get("name") == rec.company_name),
                    [],
                ),
                "contact_email": next(
                    (c.get("contact_email", "") for c in composed if c.get("name") == rec.company_name),
                    "",
                ),
            }
            for rec in records
        ]

        s8_out = MicroAgentOutput(
            agent_name="leads_writer",
            success=True,
            result={
                "leads_payload_count": len(leads_payload),
                "sheets_synced": sheets_sync_count,
                "dry_run": dry_run,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s8_start,
        )

    except Exception as e:
        logger.error(f"leads_writer error: {e}")
        s8_out = MicroAgentOutput(
            agent_name="leads_writer",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s8_start,
        )

    steps.append(_make_step(8, "leads_writer", "leads_writer", s8_out))
    if not s8_out.success:
        # leads 保存失敗は警告扱い（アウトリーチ自体は完了しているため）
        for s in steps:
            if s.step_name == "leads_writer":
                s.warning = "leads 保存失敗（アウトリーチ完了）"

    # -----------------------------------------------------------------------
    # 最終集計
    # -----------------------------------------------------------------------
    total_cost = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    final_output: dict[str, Any] = {
        "total_companies": len(records),
        "sent_count": sum(1 for r in records if r.sent),
        "hot_count": sum(1 for r in records if r.temperature == "hot"),
        "warm_count": sum(1 for r in records if r.temperature == "warm"),
        "cold_count": sum(1 for r in records if r.temperature == "cold"),
        "meeting_booked_count": sum(1 for r in records if r.meeting_booked),
        "sheets_synced": s8_out.result.get("sheets_synced", 0),
        "leads_payload": leads_payload if s8_out.success else [],
        "followup_actions": s6_out.result.get("followup_actions", []),
        "meeting_info": context.get("meeting_info", {}),
        "dry_run": dry_run,
    }

    logger.info(
        f"outreach_pipeline complete: "
        f"sent={final_output['sent_count']}/{final_output['total_companies']}, "
        f"hot={final_output['hot_count']}, "
        f"booked={final_output['meeting_booked_count']}, "
        f"cost=¥{total_cost:.2f}, {total_duration}ms"
    )

    return OutreachPipelineResult(
        success=True,
        steps=steps,
        records=records,
        final_output=final_output,
        total_cost_yen=total_cost,
        total_duration_ms=total_duration,
    )


# ---------------------------------------------------------------------------
# ヘルパー: SendGrid 送信
# ---------------------------------------------------------------------------

async def _send_via_sendgrid(
    api_key: str,
    to_email: str,
    from_email: str,
    from_name: str,
    subject: str,
    body: str,
) -> bool:
    """
    SendGrid Web API v3 でテキストメールを送信する。

    Returns:
        True: 送信成功（HTTP 202）
        False: 送信失敗
    """
    import httpx

    if not api_key or not to_email or not from_email:
        logger.warning("SendGrid 送信: api_key / to_email / from_email のいずれかが未設定")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": from_name},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 202:
                return True
            logger.warning(f"SendGrid 非202レスポンス: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"SendGrid 送信例外: {e}")
        return False
