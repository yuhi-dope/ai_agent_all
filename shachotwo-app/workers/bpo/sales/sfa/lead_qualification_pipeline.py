"""
SFA パイプライン① — リードクオリフィケーション

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.1

トリガー: フォーム送信 / Webhook / メール着信

Steps:
  Step 1: structured_extractor  フォームデータ → 企業情報を構造化抽出
  Step 2: rule_matcher           スコアリングルールをknowledge_itemsから照合
  Step 3: score_calculator       スコアリング実行（設計書の加点ルールを直接適用）
  Step 4: routing_evaluator      score ≥ 70 / 40-69 / < 40 に振り分け
  Step 5: saas_writer            leads テーブルに保存 + Slack通知
  Step 6: message_generator      初回お礼メール生成

スコアリングルール（設計書 Section 4.1 より）:
  業種マッチ(16業種):  +30pt  /  業種外: +5pt
  従業員 10-50名:     +25pt  /  51-300名: +20pt  /  他: +5pt
  ニーズ緊急度 即導入: +30pt  /  検討中: +15pt  /  情報収集: +5pt
  予算 BPOコア以上:   +20pt  /  ブレインのみ: +10pt
  流入元 紹介:        +15pt（ボーナス）
  流入元 イベント:    +10pt（ボーナス）

振り分けロジック:
  score ≥ 70  → QUALIFIED     自動で提案書パイプラインへ
  score 40-69 → REVIEW        Slack通知 → 営業判断待ち
  score < 40  → NURTURING     ナーチャリングシーケンスへ
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from llm.client import LLMTask, ModelTier, get_llm_client
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.micro.extractor import run_structured_extractor
from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.saas_writer import run_saas_writer

logger = logging.getLogger(__name__)

# ─── 定数 ────────────────────────────────────────────────────────────────────

# シャチョツーが対応する16業種コード（設計書 e_00 より）
SUPPORTED_INDUSTRIES: set[str] = {
    "construction",    # 建設業
    "manufacturing",   # 製造業
    "dental",          # 歯科
    "restaurant",      # 飲食
    "realestate",      # 不動産
    "professional",    # 士業
    "nursing",         # 介護
    "logistics",       # 物流
    "clinic",          # 医療クリニック
    "pharmacy",        # 調剤薬局
    "beauty",          # 美容エステ
    "auto_repair",     # 自動車整備
    "hotel",           # ホテル旅館
    "ecommerce",       # EC小売
    "staffing",        # 人材派遣
    "architecture",    # 建築設計
}

# 業種名の日本語→コードマッピング（フォームの自由入力を正規化）
INDUSTRY_ALIAS: dict[str, str] = {
    "建設": "construction", "建設業": "construction", "建設会社": "construction",
    "土木": "construction", "工務店": "construction",
    "製造": "manufacturing", "製造業": "manufacturing", "工場": "manufacturing",
    "歯科": "dental", "歯科医院": "dental", "歯医者": "dental",
    "飲食": "restaurant", "飲食業": "restaurant", "飲食店": "restaurant",
    "不動産": "realestate", "不動産業": "realestate",
    "士業": "professional", "弁護士": "professional", "税理士": "professional",
    "介護": "nursing", "介護業": "nursing", "デイサービス": "nursing",
    "物流": "logistics", "運送": "logistics", "倉庫": "logistics",
    "クリニック": "clinic", "医療": "clinic", "病院": "clinic",
    "薬局": "pharmacy", "調剤": "pharmacy",
    "美容": "beauty", "エステ": "beauty", "美容院": "beauty",
    "自動車整備": "auto_repair", "整備": "auto_repair",
    "ホテル": "hotel", "旅館": "hotel",
    "EC": "ecommerce", "通販": "ecommerce", "ネット販売": "ecommerce",
    "人材": "staffing", "人材派遣": "staffing", "派遣": "staffing",
    "建築設計": "architecture", "設計事務所": "architecture",
}

CONFIDENCE_WARNING_THRESHOLD = 0.60

# ─── データモデル ──────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """1ステップの実行結果（コスト・時間を個別計測）"""
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
class LeadQualificationResult:
    """リードクオリフィケーションパイプライン全体の実行結果"""
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    # 主要な出力サマリー（呼び出し側が参照しやすいように最上位に配置）
    lead_score: int = 0
    routing: str = ""          # "QUALIFIED" | "REVIEW" | "NURTURING"
    lead_id: str | None = None # DBに保存されたleads.id
    score_reasons: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'OK' if self.success else 'NG'} リードクオリフィケーション",
            f"  ステップ: {len(self.steps)}/6",
            f"  スコア: {self.lead_score}点 → {self.routing}",
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


# ─── スコアリングロジック ─────────────────────────────────────────────────────

def _normalize_industry(raw: str | None) -> str | None:
    """フォームの自由入力業種テキストを内部コードに正規化する。"""
    if not raw:
        return None
    for alias, code in INDUSTRY_ALIAS.items():
        if alias in raw:
            return code
    return raw.lower().strip()


def _calculate_score(extracted: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    """
    設計書 Section 4.1 のルールでスコアを計算する。

    Returns:
        (total_score, score_reasons)
        score_reasons は [{factor, condition, points, matched: bool}] のリスト
    """
    score = 0
    reasons: list[dict[str, Any]] = []

    # ── 業種マッチ ────────────────────────────────────────────────────────────
    raw_industry = extracted.get("industry", "")
    industry_code = _normalize_industry(raw_industry)
    if industry_code and industry_code in SUPPORTED_INDUSTRIES:
        score += 30
        reasons.append({
            "factor": "業種マッチ",
            "condition": f"{raw_industry}（{industry_code}）は対応16業種に該当",
            "points": 30,
            "matched": True,
        })
    else:
        score += 5
        reasons.append({
            "factor": "業種マッチ",
            "condition": f"{raw_industry or '未回答'} は対応業種外",
            "points": 5,
            "matched": False,
        })

    # ── 従業員規模 ────────────────────────────────────────────────────────────
    employee_count = extracted.get("employee_count")
    try:
        emp = int(employee_count) if employee_count is not None else None
    except (ValueError, TypeError):
        emp = None

    if emp is not None and 10 <= emp <= 50:
        score += 25
        reasons.append({
            "factor": "従業員規模",
            "condition": f"{emp}名（コアターゲット 10-50名）",
            "points": 25,
            "matched": True,
        })
    elif emp is not None and 51 <= emp <= 300:
        score += 20
        reasons.append({
            "factor": "従業員規模",
            "condition": f"{emp}名（51-300名）",
            "points": 20,
            "matched": True,
        })
    else:
        score += 5
        reasons.append({
            "factor": "従業員規模",
            "condition": f"{emp}名（300名超 or 10名未満 or 未回答）",
            "points": 5,
            "matched": False,
        })

    # ── ニーズ緊急度 ──────────────────────────────────────────────────────────
    urgency = str(extracted.get("urgency", "")).lower()
    if any(kw in urgency for kw in ["すぐ", "即", "今すぐ", "急", "immediate", "asap"]):
        score += 30
        reasons.append({
            "factor": "ニーズ緊急度",
            "condition": "即導入希望",
            "points": 30,
            "matched": True,
        })
    elif any(kw in urgency for kw in ["検討", "considering", "3ヶ月", "半年", "検討中"]):
        score += 15
        reasons.append({
            "factor": "ニーズ緊急度",
            "condition": "検討中",
            "points": 15,
            "matched": True,
        })
    else:
        score += 5
        reasons.append({
            "factor": "ニーズ緊急度",
            "condition": f"情報収集フェーズ（urgency='{urgency or '未回答'}'）",
            "points": 5,
            "matched": False,
        })

    # ── 予算感 ────────────────────────────────────────────────────────────────
    budget = str(extracted.get("budget", "")).lower()
    if any(kw in budget for kw in ["25万", "250,000", "250000", "bpoコア", "bpo_core", "enterprise", "フル"]):
        score += 20
        reasons.append({
            "factor": "予算感",
            "condition": "BPOコア以上（月25万+）",
            "points": 20,
            "matched": True,
        })
    elif any(kw in budget for kw in ["3万", "30,000", "30000", "ブレイン", "brain"]):
        score += 10
        reasons.append({
            "factor": "予算感",
            "condition": "ブレインのみ（月3万）",
            "points": 10,
            "matched": True,
        })
    else:
        reasons.append({
            "factor": "予算感",
            "condition": f"予算感未回答または不明（budget='{budget or '未回答'}'）",
            "points": 0,
            "matched": False,
        })

    # ── 流入元ボーナス ────────────────────────────────────────────────────────
    source = str(extracted.get("source", "")).lower()
    if any(kw in source for kw in ["紹介", "referral", "紹介者"]):
        score += 15
        reasons.append({
            "factor": "流入元ボーナス",
            "condition": "紹介経由",
            "points": 15,
            "matched": True,
        })
    elif any(kw in source for kw in ["イベント", "event", "展示会", "セミナー"]):
        score += 10
        reasons.append({
            "factor": "流入元ボーナス",
            "condition": "イベント経由",
            "points": 10,
            "matched": True,
        })

    return score, reasons


def _determine_routing(score: int) -> str:
    """スコアに基づいて振り分け先を決定する。"""
    if score >= 70:
        return "QUALIFIED"    # 提案書パイプラインへ自動進行
    elif score >= 40:
        return "REVIEW"       # Slack通知 → 営業判断待ち
    else:
        return "NURTURING"    # ナーチャリングシーケンスへ


# ─── 初回お礼メール生成 ───────────────────────────────────────────────────────

_THANK_YOU_EMAIL_SYSTEM = """あなたはシャチョツー（社長業務AIアシスタントSaaS）の営業担当アシスタントです。
お問い合わせへの初回お礼メールを、温かみのある丁寧な文体で作成してください。
以下のJSON形式のみを返してください（説明文不要）:
{"subject": "件名", "body": "本文全文"}"""


async def _generate_thank_you_email(
    extracted: dict[str, Any],
    routing: str,
    score: int,
    company_id: str,
) -> dict[str, str]:
    """
    初回お礼メールをLLMで生成する。失敗時はテンプレートにフォールバック。

    Returns:
        {"subject": str, "body": str, "is_fallback": bool}
    """
    contact_name = extracted.get("contact_name", "ご担当者")
    company_name = extracted.get("company_name", "貴社")
    industry = extracted.get("industry", "")
    need = extracted.get("need", "")

    # routing別のCTA
    if routing == "QUALIFIED":
        cta = "近日中に担当者よりご提案書をお送りいたします。"
    elif routing == "REVIEW":
        cta = "担当者より改めてご連絡させていただきます。"
    else:
        cta = "お役に立てる情報をご提供してまいります。"

    user_prompt = f"""以下の情報をもとに初回お礼メールを作成してください。

お名前: {contact_name} 様
企業名: {company_name}
業種: {industry}
ご相談内容: {need}
次のアクション: {cta}

シャチョツーは中小企業の社長業務をAIで自動化するSaaSです。
AIエージェントが見積・書類・スケジュール管理等を代行します。"""

    llm = get_llm_client()
    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _THANK_YOU_EMAIL_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.FAST,
            task_type="lead_thank_you_email",
            company_id=company_id,
            temperature=0.3,
            max_tokens=512,
        ))
        raw = response.content.strip()
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        parsed = json.loads(raw)
        return {
            "subject": parsed.get("subject", ""),
            "body": parsed.get("body", ""),
            "cost_yen": response.cost_yen,
            "is_fallback": False,
        }
    except Exception as e:
        logger.warning(f"thank_you_email LLM失敗→テンプレートフォールバック: {e}")
        return {
            "subject": f"【シャチョツー】お問い合わせありがとうございます — {company_name}",
            "body": (
                f"{contact_name} 様\n\n"
                f"この度はシャチョツーへのお問い合わせありがとうございます。\n\n"
                f"{cta}\n\n"
                f"ご不明点がございましたら、お気軽にご返信ください。\n\n"
                f"シャチョツー 営業担当"
            ),
            "cost_yen": 0.0,
            "is_fallback": True,
        }


# ─── メインパイプライン ───────────────────────────────────────────────────────

async def run_lead_qualification_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    dry_run: bool = False,
) -> LeadQualificationResult:
    """
    SFA リードクオリフィケーションパイプライン。

    Args:
        company_id:
            テナントID（シャチョツー自社の company_id を渡す）
        input_data:
            フォームデータ。以下のいずれかの形式で渡す。

            形式A — フォームテキストのまま渡す:
                {"raw_text": "フォームの全入力テキスト"}

            形式B — 既に構造化済みのデータを渡す（Step1をスキップ）:
                {
                    "company_name": "株式会社〇〇",
                    "contact_name": "山田太郎",
                    "contact_email": "yamada@example.com",
                    "industry": "建設業",
                    "employee_count": 30,
                    "urgency": "すぐ導入したい",
                    "budget": "BPOコア",
                    "source": "紹介",
                    "need": "見積作業を自動化したい",
                }

        dry_run:
            True の場合 DB への書き込みをスキップ（テスト用）

    Returns:
        LeadQualificationResult
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "dry_run": dry_run,
    }

    # ── ヘルパー ──────────────────────────────────────────────────────────────

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

    def _fail(step_name: str) -> LeadQualificationResult:
        return LeadQualificationResult(
            success=False,
            steps=steps,
            final_output={},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            failed_step=step_name,
        )

    # ─── Step 1: structured_extractor ────────────────────────────────────────
    # フォームデータを構造化JSONに変換する。
    # 既に構造化済み（raw_text なし）の場合はスキップ。

    if "raw_text" in input_data and input_data["raw_text"]:
        s1_start = int(time.time() * 1000)
        try:
            s1_out = await run_structured_extractor(MicroAgentInput(
                company_id=company_id,
                agent_name="structured_extractor",
                payload={
                    "text": input_data["raw_text"],
                    "schema": {
                        "company_name": "企業・会社名",
                        "contact_name": "担当者名",
                        "contact_email": "メールアドレス",
                        "contact_phone": "電話番号",
                        "industry": "業種（建設・製造・歯科など）",
                        "employee_count": "従業員数（数値）",
                        "urgency": "導入緊急度（即導入 / 検討中 / 情報収集 等）",
                        "budget": "予算感（BPOコア / ブレインのみ 等）",
                        "source": "流入元（紹介 / イベント / Web 等）",
                        "need": "相談・問い合わせ内容",
                    },
                    "domain": "lead_qualification",
                },
                context=context,
            ))
        except Exception as e:
            s1_out = MicroAgentOutput(
                agent_name="structured_extractor",
                success=False,
                result={"error": str(e)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - s1_start,
            )

        _add_step(1, "structured_extractor", "structured_extractor", s1_out)
        if not s1_out.success:
            return _fail("structured_extractor")

        extracted: dict[str, Any] = s1_out.result.get("extracted", {})
        context["extracted"] = extracted

    else:
        # 構造化済みデータをそのまま使用
        # 必須フィールドのみ抽出（余分なキーは無視）
        extracted = {
            k: input_data.get(k)
            for k in (
                "company_name", "contact_name", "contact_email", "contact_phone",
                "industry", "employee_count", "urgency", "budget", "source", "need",
            )
        }
        steps.append(StepResult(
            step_no=1, step_name="structured_extractor", agent_name="structured_extractor",
            success=True,
            result={"extracted": extracted, "missing_fields": [], "source": "pre_structured"},
            confidence=1.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - pipeline_start,
        ))
        context["extracted"] = extracted

    # ─── Step 2: rule_matcher ────────────────────────────────────────────────
    # knowledge_items から "lead_scoring" ドメインのルールを照合し、
    # カスタムルール（例: 特定キーワードで +10pt 等）があれば applied_values に反映する。
    # ルールが存在しない場合も非致命的（次ステップのデフォルトスコアリングで代替）。

    s2_start = int(time.time() * 1000)
    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "extracted_data": context["extracted"],
                "domain": "lead_scoring",
                "category": "scoring_rule",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning(f"rule_matcher 非致命的エラー（スキップ）: {e}")
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,  # 非致命的
            result={
                "matched_rules": [],
                "applied_values": context["extracted"],
                "unmatched_fields": list(context["extracted"].keys()),
                "warning": str(e),
            },
            confidence=0.5,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    _add_step(2, "rule_matcher", "rule_matcher", s2_out)
    # rule_matcher の applied_values でextractedを更新（カスタムルールによる補完）
    context["applied_extracted"] = s2_out.result.get("applied_values", context["extracted"])
    context["matched_rules"] = s2_out.result.get("matched_rules", [])

    # ─── Step 3: score_calculator ────────────────────────────────────────────
    # 設計書ルールに基づきスコアを計算する。
    # このステップは純粋な計算処理なのでLLM呼び出しなし。

    s3_start = int(time.time() * 1000)
    try:
        score, score_reasons = _calculate_score(context["applied_extracted"])
        routing = _determine_routing(score)

        s3_out = MicroAgentOutput(
            agent_name="score_calculator",
            success=True,
            result={
                "score": score,
                "routing": routing,
                "score_reasons": score_reasons,
                "matched_custom_rules": len(context["matched_rules"]),
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )
    except Exception as e:
        s3_out = MicroAgentOutput(
            agent_name="score_calculator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    _add_step(3, "score_calculator", "score_calculator", s3_out)
    if not s3_out.success:
        return _fail("score_calculator")

    context["score"] = s3_out.result["score"]
    context["routing"] = s3_out.result["routing"]
    context["score_reasons"] = s3_out.result["score_reasons"]

    logger.info(
        f"lead_qualification score={context['score']} routing={context['routing']} "
        f"company='{context['applied_extracted'].get('company_name', '?')}'"
    )

    # ─── Step 4: routing_evaluator ───────────────────────────────────────────
    # 振り分け先を確定し、次アクションの指示を生成する。
    # QUALIFIED → 提案書パイプラインのトリガー情報を付与
    # REVIEW    → Slack通知メッセージを生成
    # NURTURING → ナーチャリングシーケンス開始フラグ

    s4_start = int(time.time() * 1000)
    routing = context["routing"]
    score = context["score"]
    ext = context["applied_extracted"]

    if routing == "QUALIFIED":
        next_action = {
            "type": "auto_proposal",
            "pipeline": "proposal_generation_pipeline",
            "message": (
                f"スコア {score}点（閾値70）を超えました。"
                f"提案書パイプラインを自動起動します。"
            ),
            "slack_notification": (
                f":rocket: *リードQUALIFIED* — {ext.get('company_name', '?')}\n"
                f"スコア: *{score}pt* / 業種: {ext.get('industry', '?')} / "
                f"担当: {ext.get('contact_name', '?')}\n"
                f"提案書パイプラインを自動起動しました。"
            ),
        }
    elif routing == "REVIEW":
        next_action = {
            "type": "sales_review",
            "pipeline": None,
            "message": (
                f"スコア {score}点（40-69）。営業担当の判断が必要です。"
            ),
            "slack_notification": (
                f":mag: *リードREVIEW要* — {ext.get('company_name', '?')}\n"
                f"スコア: *{score}pt* / 業種: {ext.get('industry', '?')} / "
                f"担当: {ext.get('contact_name', '?')}\n"
                f"提案に進むか判断してください。 /lead qualify または /lead decline"
            ),
        }
    else:  # NURTURING
        next_action = {
            "type": "nurturing",
            "pipeline": None,
            "message": (
                f"スコア {score}点（40未満）。ナーチャリングシーケンスを開始します。"
            ),
            "slack_notification": (
                f":seedling: *リード受信（ナーチャリング）* — {ext.get('company_name', '?')}\n"
                f"スコア: *{score}pt* / 業種: {ext.get('industry', '?')}\n"
                f"ナーチャリングメールシーケンスを開始しました。"
            ),
        }

    s4_out = MicroAgentOutput(
        agent_name="routing_evaluator",
        success=True,
        result={"routing": routing, "score": score, "next_action": next_action},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=int(time.time() * 1000) - s4_start,
    )
    _add_step(4, "routing_evaluator", "routing_evaluator", s4_out)
    context["next_action"] = next_action

    # ─── Step 5: saas_writer ─────────────────────────────────────────────────
    # leads テーブルに保存 + Slack通知
    # approved=True（パイプライン内で承認済み扱い）

    s5_start = int(time.time() * 1000)
    lead_data = {
        "company_name": ext.get("company_name") or "不明",
        "contact_name": ext.get("contact_name"),
        "contact_email": ext.get("contact_email"),
        "contact_phone": ext.get("contact_phone"),
        "industry": _normalize_industry(ext.get("industry")) or ext.get("industry"),
        "employee_count": ext.get("employee_count"),
        "source": ext.get("source") or "website",
        "source_detail": ext.get("need"),
        "score": score,
        "score_reasons": score_reasons,
        "status": (
            "qualified" if routing == "QUALIFIED"
            else "new" if routing == "REVIEW"
            else "nurturing"
        ),
        "first_contact_at": None,  # Supabase側でNOW()が入る
    }

    s5_out = await run_saas_writer(MicroAgentInput(
        company_id=company_id,
        agent_name="saas_writer",
        payload={
            "service": "supabase",
            "operation": "insert_lead",
            "params": {
                "table": "leads",
                "data": lead_data,
                "action": "insert",
            },
            "approved": True,
            "dry_run": dry_run,
        },
        context=context,
    ))
    _add_step(5, "saas_writer", "saas_writer", s5_out)

    # DB保存失敗は非致命的（スコアリング結果は返す）
    lead_id: str | None = None
    if s5_out.success:
        lead_id = s5_out.result.get("operation_id")
        context["lead_id"] = lead_id
    else:
        logger.warning(f"leads DB保存失敗（非致命的）: {s5_out.result.get('error')}")

    # ─── Step 6: message_generator ───────────────────────────────────────────
    # 初回お礼メールを生成する（送信はEmailConnectorが担当）。

    s6_start = int(time.time() * 1000)
    try:
        email_result = await _generate_thank_you_email(
            extracted=ext,
            routing=routing,
            score=score,
            company_id=company_id,
        )
        email_cost = email_result.pop("cost_yen", 0.0)
        s6_out = MicroAgentOutput(
            agent_name="message_generator",
            success=True,
            result={
                "email": email_result,
                "recipient": ext.get("contact_email"),
                "is_fallback": email_result.get("is_fallback", False),
            },
            confidence=0.95 if not email_result.get("is_fallback") else 0.70,
            cost_yen=email_cost,
            duration_ms=int(time.time() * 1000) - s6_start,
        )
    except Exception as e:
        logger.warning(f"message_generator エラー（非致命的）: {e}")
        s6_out = MicroAgentOutput(
            agent_name="message_generator",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s6_start,
        )

    _add_step(6, "message_generator", "message_generator", s6_out)
    if s6_out.success:
        context["thank_you_email"] = s6_out.result["email"]

    # ─── anomaly_detector: 企業情報の矛盾検知 ────────────────────────────────
    _lead_anomaly_items: list[dict[str, Any]] = []
    _emp = ext.get("employee_count")
    try:
        _emp_int = int(_emp) if _emp is not None else None
    except (ValueError, TypeError):
        _emp_int = None
    if _emp_int is not None:
        _lead_anomaly_items.append({"name": "従業員数", "value": _emp_int})

    _lead_anomaly_warnings: list[dict] = []
    if _lead_anomaly_items:
        try:
            _lead_anomaly_out = await run_anomaly_detector(MicroAgentInput(
                company_id=company_id,
                agent_name="anomaly_detector",
                payload={
                    "items": _lead_anomaly_items,
                    "rules": [
                        {"field": "従業員数", "operator": "gte", "threshold": 0},
                        {"field": "従業員数", "operator": "lte", "threshold": 100000,
                         "message": "従業員数が10万人超です。桁間違いの可能性があります"},
                    ],
                    "detect_modes": ["digit_error", "rules"],
                },
                context=context,
            ))
            if _lead_anomaly_out.success and _lead_anomaly_out.result.get("anomaly_count", 0) > 0:
                _lead_anomaly_warnings = _lead_anomaly_out.result["anomalies"]
        except Exception as _ae:
            logger.warning(f"anomaly_detector (lead) 非致命的エラー（スキップ）: {_ae}")

    # ─── 最終結果 ─────────────────────────────────────────────────────────────
    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration_ms = int(time.time() * 1000) - pipeline_start

    final_output = {
        "lead": {
            "id": lead_id,
            **lead_data,
        },
        "score": score,
        "routing": routing,
        "score_reasons": score_reasons,
        "next_action": context["next_action"],
        "thank_you_email": context.get("thank_you_email"),
        "slack_message": context["next_action"]["slack_notification"],
    }

    if _lead_anomaly_warnings:
        final_output["anomaly_warnings"] = _lead_anomaly_warnings

    logger.info(
        f"lead_qualification_pipeline complete: "
        f"score={score} routing={routing} "
        f"lead_id={lead_id} "
        f"cost=¥{total_cost_yen:.2f} "
        f"{total_duration_ms}ms"
    )

    result_obj = LeadQualificationResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration_ms,
        lead_score=score,
        routing=routing,
        lead_id=lead_id,
        score_reasons=score_reasons,
    )

    # ─── 自動チェーン ─────────────────────────────────────────────────────────
    try:
        from workers.bpo.sales.chain import trigger_next_pipeline
        chain_result = await trigger_next_pipeline(
            "lead_qualification_pipeline", result_obj, company_id, context
        )
        final_output["chain"] = chain_result
    except Exception as chain_err:
        logger.warning(f"chain trigger failed (non-fatal): {chain_err}")

    return result_obj
