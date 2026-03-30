"""
共通BPO 採用パイプライン（バックオフィスBPO）

レジストリキー: backoffice/recruitment
トリガー: 手動（求人開始時）/ イベント（応募受信）
承認: 面接設定は自動、内定は承認必須

Steps:
  Step 1: generator        求人票ドラフト生成（職種+条件→JD文面）
  Step 2: extractor        応募書類から候補者情報を構造化（履歴書/職務経歴書→JSON）
  Step 3: rule_matcher     スクリーニング: 必須条件照合（資格/経験年数/勤務地）
  Step 4: calculator       候補者スコアリング（条件一致率 + 経験年数重み付け）
  Step 5: anomaly_detector スコア異常検知（外れ値・桁間違い・全員落選/全員通過の警告）
  Step 6: generator        面接質問リスト生成（職種別テンプレ + 候補者固有の深掘り質問）
  Step 7: calendar_booker  面接日程調整（面接官カレンダー空き検索→候補日送信）
  Step 8: message          合否連絡メール生成（通過/不通過/内定）

設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md セクション3.1
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.generator import run_document_generator
from workers.micro.extractor import run_structured_extractor
from workers.micro.anomaly_detector import run_anomaly_detector
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
    pipeline_summary,
)

logger = logging.getLogger(__name__)

# 候補者スコアリングの重み
SCORE_WEIGHT_SKILL_MATCH = 0.50
SCORE_WEIGHT_EXPERIENCE = 0.30
SCORE_WEIGHT_EDUCATION = 0.10
SCORE_WEIGHT_OTHER = 0.10

# 面接推奨スコア閾値
INTERVIEW_PASS_THRESHOLD = 0.65
# 内定判定スコア閾値（承認必須）
OFFER_SCORE_THRESHOLD = 0.80


@dataclass
class RecruitmentPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    jd_draft: str = ""
    interview_questions: list[str] = field(default_factory=list)
    offers_pending_approval: int = 0
    approval_required: bool = False

    anomaly_warnings: list[str] = field(default_factory=list)

    def to_recruitment_summary(self) -> str:
        extra = [f"  候補者数: {len(self.candidates)}名"]
        if self.offers_pending_approval:
            extra.append(f"  内定承認待ち: {self.offers_pending_approval}件（承認者確認が必要）")
        if self.anomaly_warnings:
            for w in self.anomaly_warnings:
                extra.append(f"  [異常検知] {w}")
        return pipeline_summary(
            label="採用パイプライン",
            total_steps=8,
            steps=self.steps,
            total_cost_yen=self.total_cost_yen,
            total_duration_ms=self.total_duration_ms,
            failed_step=self.failed_step,
            extra_lines=extra,
        )


async def run_recruitment_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> RecruitmentPipelineResult:
    """
    採用パイプライン実行。

    Args:
        company_id: テナントID
        input_data:
            "job_title"          (str):  職種名（例: "バックエンドエンジニア"）
            "job_requirements"   (dict): 必須条件 {skills, experience_years, location, employment_type}
            "applications"       (list): 応募書類リスト
              各要素: {applicant_name, resume_text, cv_text, email}
            "mode"               (str):  "jd_only" | "screening" | "full"（デフォルト: "full"）
            "interviewer_emails" (list): 面接官のカレンダーメールアドレス
            "dry_run"            (bool): True=メール/カレンダー操作を実行しない
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, RecruitmentPipelineResult)

    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "recruitment",
        "dry_run": input_data.get("dry_run", False),
    }

    job_title: str = input_data.get("job_title", "")
    job_requirements: dict = input_data.get("job_requirements", {})
    applications: list[dict] = input_data.get("applications", [])
    mode: str = input_data.get("mode", "full")
    dry_run: bool = context["dry_run"]

    # ─── Step 1: generator ── 求人票ドラフト生成 ─────────────────────────────
    s1_out = await run_document_generator(MicroAgentInput(
        company_id=company_id, agent_name="document_generator",
        payload={
            "template": "求人票",
            "variables": {
                "job_title": job_title,
                "job_requirements": job_requirements,
                "prompt": (
                    f"職種: {job_title}\n"
                    f"必須条件: {job_requirements}\n"
                    "上記に基づいて読みやすい求人票を生成してください。"
                    "業務内容・必須スキル・歓迎スキル・待遇・応募方法を含めること。"
                ),
            },
        },
        context=context,
    ))
    record_step(1, "jd_generator", "document_generator", s1_out)
    if not s1_out.success:
        return emit_fail("jd_generator")

    jd_draft: str = s1_out.result.get("content", s1_out.result.get("document", ""))
    context["jd_draft"] = jd_draft

    if mode == "jd_only":
        return RecruitmentPipelineResult(
            success=True, steps=steps,
            final_output={"jd_draft": jd_draft},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            jd_draft=jd_draft,
        )

    if not applications:
        logger.info(f"recruitment_pipeline: 応募書類なし（company={company_id}）")
        return RecruitmentPipelineResult(
            success=True, steps=steps,
            final_output={"jd_draft": jd_draft, "candidates": []},
            total_cost_yen=sum(s.cost_yen for s in steps),
            total_duration_ms=int(time.time() * 1000) - pipeline_start,
            jd_draft=jd_draft,
        )

    # ─── Step 2: extractor ── 応募書類の構造化 ──────────────────────────────
    candidate_schema = {
        "applicant_name": "str",
        "email": "str",
        "skills": "list[str]",
        "experience_years": "int",
        "education": "str",
        "location": "str",
        "work_history": "list[{company, role, years}]",
        "certifications": "list[str]",
    }
    structured_candidates: list[dict] = []
    total_extract_cost = 0.0
    total_extract_ms = 0

    for app in applications:
        resume_text = (app.get("resume_text") or "") + "\n" + (app.get("cv_text") or "")
        out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id, agent_name="structured_extractor",
            payload={
                "text": resume_text,
                "schema": candidate_schema,
                "hint": "日本語の履歴書・職務経歴書から候補者情報を抽出",
            },
            context=context,
        ))
        total_extract_cost += out.cost_yen
        total_extract_ms += out.duration_ms
        if out.success:
            cand = {**out.result}
            cand.setdefault("applicant_name", app.get("applicant_name", ""))
            cand.setdefault("email", app.get("email", ""))
            structured_candidates.append(cand)
        else:
            logger.warning(f"recruitment: 書類抽出失敗 ({app.get('applicant_name', '?')})")

    s2_out = MicroAgentOutput(
        agent_name="structured_extractor",
        success=len(structured_candidates) > 0,
        result={"candidates": structured_candidates, "extracted": len(structured_candidates)},
        confidence=len(structured_candidates) / max(len(applications), 1),
        cost_yen=total_extract_cost,
        duration_ms=total_extract_ms,
    )
    record_step(2, "application_extractor", "structured_extractor", s2_out)
    if not s2_out.success:
        return emit_fail("application_extractor")
    context["candidates_raw"] = structured_candidates

    # ─── Step 3: rule_matcher ── スクリーニング ──────────────────────────────
    required_skills: list[str] = job_requirements.get("skills", [])
    required_exp_years: int = job_requirements.get("experience_years", 0)
    required_location: str = job_requirements.get("location", "")

    screening_results: list[dict] = []
    for cand in structured_candidates:
        cand_skills: list[str] = cand.get("skills", [])
        cand_exp: int = cand.get("experience_years", 0)
        cand_location: str = cand.get("location", "")

        matched_skills = [s for s in required_skills if any(s in cs for cs in cand_skills)]
        skill_match_rate = len(matched_skills) / max(len(required_skills), 1)
        exp_ok = cand_exp >= required_exp_years
        location_ok = (not required_location) or (required_location in cand_location)
        screening_pass = skill_match_rate >= 0.5 and exp_ok and location_ok

        screening_results.append({
            **cand,
            "skill_match_rate": round(skill_match_rate, 2),
            "matched_skills": matched_skills,
            "exp_ok": exp_ok,
            "location_ok": location_ok,
            "screening_pass": screening_pass,
        })

    pass_count = sum(1 for c in screening_results if c["screening_pass"])
    s3_out = MicroAgentOutput(
        agent_name="rule_matcher",
        success=True,
        result={"candidates": screening_results, "pass_count": pass_count},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(3, "screening", "rule_matcher", s3_out)
    context["screening_results"] = screening_results

    # ─── Step 4: calculator ── 候補者スコアリング ────────────────────────────
    scored_candidates: list[dict] = []
    for cand in screening_results:
        skill_score = cand.get("skill_match_rate", 0.0) * SCORE_WEIGHT_SKILL_MATCH
        exp_years = cand.get("experience_years", 0)
        exp_score = min(exp_years / max(required_exp_years * 2, 1), 1.0) * SCORE_WEIGHT_EXPERIENCE
        edu = cand.get("education", "")
        edu_base = (
            0.10 if "大学院" in edu else
            0.08 if "大学" in edu else
            0.05 if "専門" in edu else
            0.03
        )
        edu_score = edu_base
        location_score = (SCORE_WEIGHT_OTHER if cand.get("location_ok") else 0.0)
        total_score = round(skill_score + exp_score + edu_score + location_score, 3)

        scored_candidates.append({
            **cand,
            "total_score": total_score,
            "interview_recommend": total_score >= INTERVIEW_PASS_THRESHOLD,
            "score_breakdown": {
                "skill": round(skill_score, 3),
                "experience": round(exp_score, 3),
                "education": round(edu_score, 3),
                "other": round(location_score, 3),
            },
        })

    scored_candidates.sort(key=lambda c: c["total_score"], reverse=True)
    s4_out = MicroAgentOutput(
        agent_name="calculator",
        success=True,
        result={"candidates": scored_candidates},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(4, "scorer", "calculator", s4_out)
    context["scored_candidates"] = scored_candidates

    interview_candidates = [c for c in scored_candidates if c.get("interview_recommend")]

    # ─── Step 5: anomaly_detector ── スコア異常検知 ──────────────────────────
    # スコアリング結果の外れ値・全員落選/全員通過などを検知する
    anomaly_items = [
        {
            "name": c.get("applicant_name", f"候補者{i}"),
            "value": int(c.get("total_score", 0) * 1000),  # 0〜1000に変換して整数比較
            "expected_range": [0, 1000],
        }
        for i, c in enumerate(scored_candidates)
    ]
    pass_rate = len(interview_candidates) / max(len(scored_candidates), 1)
    anomaly_rules: list[dict] = []
    # 全員通過 or 全員不通過の警告ルール（スコア合計で疑似チェック）
    if pass_rate == 1.0:
        logger.warning(f"recruitment: 全候補者が面接通過判定（company={company_id}）")
    if pass_rate == 0.0 and len(scored_candidates) > 0:
        logger.warning(f"recruitment: 全候補者が面接不通過判定（company={company_id}）")

    if anomaly_items:
        s5_out = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id, agent_name="anomaly_detector",
            payload={
                "items": anomaly_items,
                "rules": anomaly_rules,
                "detect_modes": ["zscore", "digit_error"],
            },
            context=context,
        ))
    else:
        s5_out = MicroAgentOutput(
            agent_name="anomaly_detector", success=True,
            result={"anomalies": [], "total_checked": 0, "anomaly_count": 0, "passed": True},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(5, "score_anomaly_detector", "anomaly_detector", s5_out)
    anomaly_warnings: list[str] = [
        a["message"] for a in s5_out.result.get("anomalies", [])
    ]
    if pass_rate == 1.0:
        anomaly_warnings.append("全候補者が面接通過判定になっています。スクリーニング条件を確認してください。")
    if pass_rate == 0.0 and len(scored_candidates) > 0:
        anomaly_warnings.append("全候補者が面接不通過判定になっています。必須条件が厳しすぎる可能性があります。")
    context["anomaly_warnings"] = anomaly_warnings

    # ─── Step 6: generator ── 面接質問リスト生成 ────────────────────────────
    questions_by_candidate: dict[str, list[str]] = {}
    q_total_cost = 0.0
    q_total_ms = 0

    for cand in interview_candidates[:5]:
        cand_name = cand.get("applicant_name", "候補者")
        missing_skills = [
            sk for sk in required_skills
            if sk not in (cand.get("matched_skills") or [])
        ]
        q_out = await run_document_generator(MicroAgentInput(
            company_id=company_id, agent_name="document_generator",
            payload={
                "template": "面接質問リスト",
                "variables": {
                    "job_title": job_title,
                    "candidate_name": cand_name,
                    "candidate_skills": cand.get("skills", []),
                    "missing_skills": missing_skills,
                    "experience_years": cand.get("experience_years", 0),
                    "work_history": cand.get("work_history", []),
                },
            },
            context=context,
        ))
        q_total_cost += q_out.cost_yen
        q_total_ms += q_out.duration_ms
        if q_out.success:
            raw = q_out.result.get("content", "")
            questions_by_candidate[cand_name] = (
                raw if isinstance(raw, list)
                else [ln.strip() for ln in str(raw).split("\n") if ln.strip()]
            )

    all_questions: list[str] = []
    for qs in questions_by_candidate.values():
        for q in qs:
            if q not in all_questions:
                all_questions.append(q)

    s6_out = MicroAgentOutput(
        agent_name="document_generator",
        success=True,
        result={"questions_by_candidate": questions_by_candidate, "all_questions": all_questions},
        confidence=0.90 if all_questions else 0.60,
        cost_yen=q_total_cost,
        duration_ms=q_total_ms,
    )
    record_step(6, "interview_question_generator", "document_generator", s6_out)
    context["interview_questions"] = all_questions

    # ─── Step 7: calendar_booker ── 面接日程調整 ─────────────────────────────
    interviewer_emails: list[str] = input_data.get("interviewer_emails", [])
    if interview_candidates and not dry_run and interviewer_emails:
        try:
            from workers.micro.calendar_booker import run_calendar_booker
            s7_out = await run_calendar_booker(MicroAgentInput(
                company_id=company_id, agent_name="calendar_booker",
                payload={
                    "operation": "find_and_propose",
                    "attendees": interviewer_emails,
                    "candidates": [
                        {"name": c.get("applicant_name"), "email": c.get("email")}
                        for c in interview_candidates
                    ],
                    "event_title": f"{job_title} 面接",
                    "duration_minutes": 60,
                    "slot_count": 3,
                },
                context=context,
            ))
        except Exception as exc:
            logger.warning(f"calendar_booker failed: {exc}")
            s7_out = MicroAgentOutput(
                agent_name="calendar_booker", success=True,
                result={"scheduled_count": 0, "warning": str(exc)},
                confidence=0.5, cost_yen=0.0, duration_ms=0,
            )
    else:
        note = (
            "dry_run" if dry_run else
            "面接官メールアドレス未指定" if not interviewer_emails else
            "面接対象者なし"
        )
        s7_out = MicroAgentOutput(
            agent_name="calendar_booker", success=True,
            result={"scheduled_count": 0, "note": note},
            confidence=1.0, cost_yen=0.0, duration_ms=0,
        )
    record_step(7, "interview_scheduler", "calendar_booker", s7_out)

    # ─── Step 8: message ── 合否連絡メール生成 ──────────────────────────────
    mail_results: list[dict] = []
    offers_pending = 0

    for cand in scored_candidates:
        cand_name = cand.get("applicant_name", "候補者")
        cand_email = cand.get("email", "")
        is_offer = (
            cand.get("total_score", 0) >= OFFER_SCORE_THRESHOLD
            and cand.get("screening_pass", False)
        )
        if is_offer:
            mail_type = "内定通知"
            offers_pending += 1
        elif cand.get("interview_recommend"):
            mail_type = "面接通過"
        else:
            mail_type = "書類選考不通過"

        try:
            from workers.micro.message import run_message_drafter
            msg = await run_message_drafter(
                document_type=f"採用連絡（{mail_type}）",
                context={
                    "applicant_name": cand_name,
                    "job_title": job_title,
                    "result": mail_type,
                    "company_id": company_id,
                },
                company_id=company_id,
            )
            mail_results.append({
                "applicant_name": cand_name,
                "email": cand_email,
                "mail_type": mail_type,
                "subject": msg.subject,
                "body": msg.body,
                "sent": False,
            })
        except Exception as exc:
            logger.warning(f"message draft failed for {cand_name}: {exc}")
            mail_results.append({
                "applicant_name": cand_name,
                "email": cand_email,
                "mail_type": mail_type,
                "subject": f"{job_title} 選考結果のご連絡",
                "body": f"{cand_name} 様\n\n選考結果について、担当者よりご連絡いたします。",
                "sent": False,
            })

    s8_out = MicroAgentOutput(
        agent_name="message",
        success=True,
        result={"mail_results": mail_results, "offers_pending": offers_pending},
        confidence=1.0,
        cost_yen=0.0,
        duration_ms=0,
    )
    record_step(8, "result_notifier", "message", s8_out)

    # ─── 最終出力 ──────────────────────────────────────────────────────────────
    final_candidates = [
        {
            "name": c.get("applicant_name"),
            "score": c.get("total_score"),
            "screening_result": "通過" if c.get("screening_pass") else "不通過",
            "interview_scheduled": any(
                r["applicant_name"] == c.get("applicant_name") and r["mail_type"] == "面接通過"
                for r in mail_results
            ),
        }
        for c in scored_candidates
    ]

    final_output = {
        "jd_draft": jd_draft,
        "candidates": final_candidates,
        "interview_questions": all_questions[:20],
        "mail_drafts": mail_results,
        "offers_pending_approval": offers_pending,
        "anomaly_warnings": anomaly_warnings,
    }

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start
    logger.info(
        f"recruitment_pipeline complete: company={company_id}, "
        f"candidates={len(scored_candidates)}, offers={offers_pending}, "
        f"anomalies={len(anomaly_warnings)}, {total_duration}ms"
    )

    return RecruitmentPipelineResult(
        success=True,
        steps=steps,
        final_output=final_output,
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        candidates=final_candidates,
        jd_draft=jd_draft,
        interview_questions=all_questions[:20],
        offers_pending_approval=offers_pending,
        approval_required=offers_pending > 0,
        anomaly_warnings=anomaly_warnings,
    )
