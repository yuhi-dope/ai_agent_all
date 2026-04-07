"""
共通BPO 反社チェックパイプライン

レジストリキー: backoffice/antisocial_screening
トリガー: イベント（取引先追加時）/ スケジュール（四半期）/ 手動
承認: 必須（要注意フラグ時はエスカレーション）
設計書: shachotwo/b_詳細設計/b_07_バックオフィスBPO詳細設計.md

Steps:
  Step 1: db_reader     対象取引先・会社情報をSupabase内部DBから取得
  Step 2: rule_matcher  社名・代表者名をブラックリストと照合（内部ナレッジDB）
  Step 3: rule_matcher  反社関連キーワードパターンマッチ（社名・住所・電話番号）
  Step 4: extractor     リスクスコア算出（0.0〜1.0）
  Step 5: generator     スクリーニングレポート生成
  Step 6: validator     チェック完了バリデーション・承認フラグ設定
"""
import re
import time
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput
from workers.micro.rule_matcher import run_rule_matcher
from workers.micro.extractor import run_structured_extractor
from workers.micro.generator import run_document_generator
from workers.micro.validator import run_output_validator
from workers.bpo.common.pipelines.pipeline_utils import (
    StepResult,
    make_step_adder,
    make_fail_factory,
)
from db.supabase import get_service_client

logger = logging.getLogger(__name__)

# ─── 反社関連定数 ────────────────────────────────────────────────────────────

ANTISOCIAL_KEYWORDS = [
    "一家", "興業", "事務所", "総業", "道会", "連合会",
    "山口組", "住吉会", "稲川会", "工藤会", "旭琉会",
    "フロント企業", "ダミー会社",
]

SUSPICIOUS_PATTERNS = [
    r"^[\u4e00-\u9fff]{1,2}(組|会|連合)$",  # 短い漢字+組/会/連合
    r"興業",
    r"総業",
]

# リスクスコア閾値
RISK_SCORE_DANGER = 0.8    # danger判定
RISK_SCORE_CAUTION = 0.4   # caution判定

# 編集距離の類似度閾値（0.0〜1.0）
SIMILARITY_THRESHOLD = 0.75


# ─── DataClass ──────────────────────────────────────────────────────────────

@dataclass
class AntisocialScreeningPipelineResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None
    approval_required: bool = True   # 反社チェックは常に承認必須
    risk_score: float = 0.0          # 0.0〜1.0
    risk_level: str = "safe"         # safe / caution / danger
    matched_flags: list[str] = field(default_factory=list)   # マッチしたフラグ一覧
    screening_target: str = ""       # チェック対象（社名等）
    report_generated: bool = False


# ─── ユーティリティ関数 ──────────────────────────────────────────────────────

def _levenshtein_similarity(a: str, b: str) -> float:
    """
    レーベンシュタイン距離ベースの文字列類似度（0.0〜1.0）を返す。
    完全一致で1.0。
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    len_a, len_b = len(a), len(b)
    dp = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev[j - 1] + cost)
    distance = dp[len_b]
    max_len = max(len_a, len_b)
    return round(1.0 - distance / max_len, 4)


def _check_keyword_match(text: str) -> list[str]:
    """
    テキストに反社キーワード・パターンが含まれているか確認し、
    マッチしたキーワード/パターンのリストを返す。
    """
    matched: list[str] = []
    if not text:
        return matched

    for kw in ANTISOCIAL_KEYWORDS:
        if kw in text:
            matched.append(f"キーワード一致: {kw}")

    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text):
            matched.append(f"パターン一致: {pattern}")

    return matched


def _calc_risk_score(
    blacklist_matches: list[dict[str, Any]],
    keyword_flags: list[str],
) -> tuple[float, str]:
    """
    ブラックリスト照合結果とキーワードフラグからリスクスコアとレベルを算出する。

    Returns:
        (risk_score, risk_level)  risk_level: "safe" | "caution" | "danger"
    """
    if not blacklist_matches and not keyword_flags:
        return 0.0, "safe"

    score = 0.0

    # ブラックリスト完全一致（similarity >= 0.99）
    perfect_matches = [m for m in blacklist_matches if m.get("similarity", 0.0) >= 0.99]
    if perfect_matches:
        score = max(score, 1.0)

    # ブラックリスト高類似（0.75〜0.99）
    high_matches = [
        m for m in blacklist_matches
        if 0.99 > m.get("similarity", 0.0) >= SIMILARITY_THRESHOLD
    ]
    if high_matches:
        best_sim = max(m["similarity"] for m in high_matches)
        score = max(score, 0.6 + 0.4 * best_sim)

    # キーワードフラグによる加点
    if keyword_flags:
        score = max(score, min(1.0, 0.3 + 0.1 * len(keyword_flags)))

    score = round(min(1.0, score), 4)

    if score >= RISK_SCORE_DANGER:
        level = "danger"
    elif score >= RISK_SCORE_CAUTION:
        level = "caution"
    else:
        level = "safe"

    return score, level


# ─── メイン関数 ──────────────────────────────────────────────────────────────

async def run_antisocial_screening_pipeline(
    company_id: str,
    input_data: dict[str, Any],
    **kwargs: Any,
) -> AntisocialScreeningPipelineResult:
    """
    反社チェックパイプライン実行。SaaS不使用・Supabase内部DBのみで完結。

    Args:
        company_id: テナントID（RLSのため）
        input_data: {
            "target_type": "vendor" | "company" | "person",  # チェック対象種別
            "target_name": str,       # 社名 or 人名
            "target_id": str,         # bpo_vendors.id or companies.id（任意）
            "address": str,           # 住所（任意）
            "phone": str,             # 電話番号（任意）
            "representative": str,    # 代表者名（任意）
        }
    """
    pipeline_start = int(time.time() * 1000)
    steps: list[StepResult] = []
    matched_flags: list[str] = []
    context: dict[str, Any] = {
        "company_id": company_id,
        "domain": "antisocial_screening",
        "check_date": date.today().isoformat(),
    }

    record_step = make_step_adder(steps)
    emit_fail = make_fail_factory(steps, pipeline_start, AntisocialScreeningPipelineResult)

    target_type: str = input_data.get("target_type", "vendor")
    target_name: str = input_data.get("target_name", "")
    target_id: str = input_data.get("target_id", "")
    address: str = input_data.get("address", "")
    phone: str = input_data.get("phone", "")
    representative: str = input_data.get("representative", "")

    screening_target = target_name or target_id or "不明"

    # ─── Step 1: db_reader ── 対象情報取得 + ブラックリスト取得 ─────────────
    s1_start = int(time.time() * 1000)
    try:
        db = get_service_client()

        # 対象エンティティの詳細取得（target_idが指定された場合）
        target_detail: dict[str, Any] = {}
        if target_id:
            if target_type == "vendor":
                resp = (
                    db.table("bpo_vendors")
                    .select("*")
                    .eq("company_id", company_id)
                    .eq("id", target_id)
                    .limit(1)
                    .execute()
                )
            else:
                resp = (
                    db.table("companies")
                    .select("*")
                    .eq("company_id", company_id)
                    .eq("id", target_id)
                    .limit(1)
                    .execute()
                )
            if resp.data:
                target_detail = resp.data[0]
                # target_nameが未指定の場合はDBから補完
                if not target_name:
                    target_name = (
                        target_detail.get("vendor_name")
                        or target_detail.get("name")
                        or target_detail.get("company_name")
                        or ""
                    )
                    screening_target = target_name or target_id

        # ブラックリスト取得（knowledge_items.metadata.type = "antisocial_blacklist"）
        bl_resp = (
            db.table("knowledge_items")
            .select("id, title, content, metadata, tags")
            .eq("company_id", company_id)
            .eq("is_active", True)
            .contains("metadata", {"type": "antisocial_blacklist"})
            .execute()
        )
        blacklist_entries: list[dict] = bl_resp.data or []

        s1_out = MicroAgentOutput(
            agent_name="db_reader",
            success=True,
            result={
                "target_detail": target_detail,
                "target_name": target_name,
                "blacklist_count": len(blacklist_entries),
                "blacklist_entries": blacklist_entries,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )
    except Exception as e:
        logger.error("antisocial_screening Step1 db_reader error: %s", e)
        s1_out = MicroAgentOutput(
            agent_name="db_reader",
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s1_start,
        )

    record_step(1, "db_reader", "db_reader", s1_out)
    if not s1_out.success:
        return emit_fail("db_reader")

    blacklist_entries = s1_out.result["blacklist_entries"]
    context["target_name"] = target_name
    context["blacklist_entries"] = blacklist_entries

    # ─── Step 2: rule_matcher ── 社名・代表者名をブラックリストと照合 ────────
    s2_start = int(time.time() * 1000)
    blacklist_matches: list[dict[str, Any]] = []

    # ブラックリストとの類似度計算（編集距離ベース）
    check_names: list[str] = [n for n in [target_name, representative] if n]
    for entry in blacklist_entries:
        entry_name: str = entry.get("title", "")
        for check_name in check_names:
            sim = _levenshtein_similarity(check_name, entry_name)
            if sim >= SIMILARITY_THRESHOLD:
                blacklist_matches.append({
                    "blacklist_id": entry.get("id", ""),
                    "blacklist_name": entry_name,
                    "check_name": check_name,
                    "similarity": sim,
                    "metadata": entry.get("metadata", {}),
                })
                matched_flags.append(
                    f"ブラックリスト類似一致: {check_name} ≈ {entry_name} (類似度{sim:.2f})"
                )

    try:
        s2_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "target_name": target_name,
                    "representative": representative,
                    "blacklist_matches": blacklist_matches,
                },
                "domain": "antisocial_screening",
                "category": "blacklist_check",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning("antisocial_screening Step2 rule_matcher error: %s", e)
        s2_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={
                "matched_rules": [],
                "applied_values": {"blacklist_matches": blacklist_matches},
                "unmatched_fields": [],
            },
            confidence=0.9,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s2_start,
        )

    record_step(2, "rule_matcher", "rule_matcher", s2_out)
    if not s2_out.success:
        return emit_fail("rule_matcher_blacklist")
    context["blacklist_matches"] = blacklist_matches

    # ─── Step 3: rule_matcher ── キーワード・パターンマッチ ─────────────────
    s3_start = int(time.time() * 1000)
    keyword_flags: list[str] = []

    # 社名・住所・電話番号に対してキーワード・パターンチェック
    check_texts: dict[str, str] = {
        "社名/人名": target_name,
        "代表者名": representative,
        "住所": address,
        "電話番号": phone,
    }
    for field_label, text_value in check_texts.items():
        if not text_value:
            continue
        hits = _check_keyword_match(text_value)
        for hit in hits:
            flag_msg = f"[{field_label}] {hit}"
            keyword_flags.append(flag_msg)
            matched_flags.append(flag_msg)

    try:
        s3_out = await run_rule_matcher(MicroAgentInput(
            company_id=company_id,
            agent_name="rule_matcher",
            payload={
                "extracted_data": {
                    "target_name": target_name,
                    "address": address,
                    "phone": phone,
                    "representative": representative,
                    "keyword_flags": keyword_flags,
                },
                "domain": "antisocial_screening",
                "category": "keyword_pattern",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning("antisocial_screening Step3 rule_matcher error: %s", e)
        s3_out = MicroAgentOutput(
            agent_name="rule_matcher",
            success=True,
            result={
                "matched_rules": keyword_flags,
                "applied_values": {"keyword_flags": keyword_flags},
                "unmatched_fields": [],
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s3_start,
        )

    record_step(3, "rule_matcher", "rule_matcher", s3_out)
    if not s3_out.success:
        return emit_fail("rule_matcher_keyword")
    context["keyword_flags"] = keyword_flags

    # ─── Step 4: extractor ── リスクスコア算出（0.0〜1.0）────────────────────
    s4_start = int(time.time() * 1000)
    risk_score, risk_level = _calc_risk_score(blacklist_matches, keyword_flags)

    try:
        s4_out = await run_structured_extractor(MicroAgentInput(
            company_id=company_id,
            agent_name="structured_extractor",
            payload={
                "text": (
                    f"反社チェック対象: {target_name}\n"
                    f"代表者: {representative}\n"
                    f"住所: {address}\n"
                    f"電話番号: {phone}\n"
                    f"ブラックリスト一致数: {len(blacklist_matches)}\n"
                    f"キーワードフラグ数: {len(keyword_flags)}\n"
                    f"フラグ詳細: {', '.join(keyword_flags) if keyword_flags else 'なし'}"
                ),
                "schema": {
                    "risk_score": "リスクスコア（0.0〜1.0）。ブラックリスト完全一致=1.0、キーワード一致=0.4〜0.8、クリーン=0.0",
                    "risk_level": "リスクレベル（safe/caution/danger）",
                    "summary": "スクリーニング結果の要約（100文字以内）",
                    "recommended_action": "推奨対応（取引継続可/要確認/取引停止推奨）",
                },
                "extra_context": {
                    "pre_calculated_score": risk_score,
                    "pre_calculated_level": risk_level,
                    "blacklist_matches": blacklist_matches,
                    "keyword_flags": keyword_flags,
                },
            },
            context=context,
        ))
        # LLMの出力でスコアを上書き（ただし事前計算値より大幅に乖離する場合は事前計算値を優先）
        llm_score = s4_out.result.get("risk_score")
        if isinstance(llm_score, (int, float)):
            # 事前計算との差が0.3以内の場合のみLLM値を採用
            if abs(float(llm_score) - risk_score) <= 0.3:
                risk_score = round(float(llm_score), 4)
        llm_level = s4_out.result.get("risk_level")
        if llm_level in ("safe", "caution", "danger"):
            risk_level = llm_level

    except Exception as e:
        logger.warning("antisocial_screening Step4 extractor error: %s", e)
        # LLM失敗時は事前計算値をそのまま使用
        s4_out = MicroAgentOutput(
            agent_name="structured_extractor",
            success=True,
            result={
                "risk_score": risk_score,
                "risk_level": risk_level,
                "summary": f"反社チェック完了。リスクスコア: {risk_score:.2f}",
                "recommended_action": (
                    "取引停止推奨" if risk_level == "danger"
                    else "要確認" if risk_level == "caution"
                    else "取引継続可"
                ),
                "fallback": True,
            },
            confidence=0.85,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s4_start,
        )

    record_step(4, "extractor", "structured_extractor", s4_out)
    if not s4_out.success:
        return emit_fail("extractor")
    context["risk_score"] = risk_score
    context["risk_level"] = risk_level
    context["extractor_result"] = s4_out.result

    # ─── Step 5: generator ── スクリーニングレポート生成 ────────────────────
    s5_start = int(time.time() * 1000)
    report_data = {
        "company_id": company_id,
        "check_date": context["check_date"],
        "target_type": target_type,
        "target_name": target_name,
        "target_id": target_id,
        "representative": representative,
        "address": address,
        "phone": phone,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "blacklist_matches": blacklist_matches,
        "keyword_flags": keyword_flags,
        "matched_flags": matched_flags,
        "summary": s4_out.result.get("summary", ""),
        "recommended_action": s4_out.result.get("recommended_action", ""),
        "approval_required": True,
    }
    try:
        s5_out = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template": "antisocial_screening_report",
                "data": report_data,
                "format": "markdown",
            },
            context=context,
        ))
    except Exception as e:
        logger.warning("antisocial_screening Step5 generator error: %s", e)
        # フォールバック: 簡易Markdownレポート
        risk_emoji_map = {"danger": "[危険]", "caution": "[要注意]", "safe": "[安全]"}
        risk_label = risk_emoji_map.get(risk_level, risk_level)
        report_lines = [
            f"## 反社チェックレポート ({context['check_date']})",
            f"",
            f"### 対象情報",
            f"- 種別: {target_type}",
            f"- 名称: {target_name or '（未設定）'}",
            f"- 代表者: {representative or '（未設定）'}",
            f"- 住所: {address or '（未設定）'}",
            f"- 電話番号: {phone or '（未設定）'}",
            f"",
            f"### 判定結果",
            f"- リスクスコア: {risk_score:.2f}",
            f"- リスクレベル: {risk_label}",
            f"- 推奨対応: {s4_out.result.get('recommended_action', '要確認')}",
            f"",
            f"### 検出フラグ（{len(matched_flags)}件）",
        ] + [f"- {flag}" for flag in matched_flags] + [
            f"",
            f"### ブラックリスト照合（{len(blacklist_matches)}件一致）",
        ] + [
            f"- {m['check_name']} ≈ {m['blacklist_name']} (類似度{m['similarity']:.2f})"
            for m in blacklist_matches
        ] + [
            f"",
            f"---",
            f"*本レポートは承認が必要です。担当者による最終確認を行ってください。*",
        ]
        s5_out = MicroAgentOutput(
            agent_name="document_generator",
            success=True,
            result={"document": "\n".join(report_lines), "fallback": True},
            confidence=0.85,
            cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - s5_start,
        )

    record_step(5, "generator", "document_generator", s5_out)
    report_generated = s5_out.success
    context["report"] = s5_out.result.get("document", "")

    # ─── Step 6: validator ── バリデーション + bpo_approvalsへのDB書き込み ────
    s6_start = int(time.time() * 1000)
    val_out = await run_output_validator(MicroAgentInput(
        company_id=company_id,
        agent_name="output_validator",
        payload={
            "document": {
                "target_name": target_name,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "check_date": context["check_date"],
                "report_generated": report_generated,
            },
            "required_fields": ["target_name", "risk_score", "risk_level", "check_date"],
            "numeric_fields": ["risk_score"],
            "positive_fields": [],
            "rules": [
                {"field": "risk_score", "op": "gte", "value": 0.0},
                {"field": "risk_score", "op": "lte", "value": 1.0},
            ],
        },
        context=context,
    ))
    record_step(6, "validator", "output_validator", val_out)

    # bpo_approvalsへの承認フロー登録（danger or caution の場合）
    if risk_level in ("danger", "caution"):
        try:
            db = get_service_client()
            approval_payload = {
                "company_id": company_id,
                "pipeline_key": "backoffice/antisocial_screening",
                "target_type": target_type,
                "target_id": target_id or None,
                "target_name": target_name,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "matched_flags": matched_flags,
                "report": context.get("report", ""),
                "status": "pending",
                "checked_at": context["check_date"],
            }
            db.table("bpo_approvals").insert(approval_payload).execute()
            logger.info(
                "antisocial_screening: bpo_approvals登録完了 target=%s risk_level=%s",
                target_name,
                risk_level,
            )
        except Exception as e:
            logger.error(
                "antisocial_screening: bpo_approvals登録失敗 target=%s error=%s",
                target_name,
                e,
            )

    # execution_logsへの記録
    try:
        db = get_service_client()
        db.table("execution_logs").insert({
            "company_id": company_id,
            "agent_name": "antisocial_screening_pipeline",
            "pipeline_key": "backoffice/antisocial_screening",
            "target_name": target_name,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "matched_flag_count": len(matched_flags),
            "total_cost_yen": sum(s.cost_yen for s in steps),
            "total_duration_ms": int(time.time() * 1000) - pipeline_start,
            "success": True,
            "executed_at": context["check_date"],
        }).execute()
    except Exception as e:
        logger.warning("antisocial_screening: execution_logs記録失敗: %s", e)

    total_cost_yen = sum(s.cost_yen for s in steps)
    total_duration = int(time.time() * 1000) - pipeline_start

    logger.info(
        "antisocial_screening_pipeline complete: target=%s risk_score=%.2f risk_level=%s "
        "flags=%d %dms",
        target_name,
        risk_score,
        risk_level,
        len(matched_flags),
        total_duration,
    )

    return AntisocialScreeningPipelineResult(
        success=True,
        steps=steps,
        final_output={
            "check_date": context["check_date"],
            "target_type": target_type,
            "target_name": target_name,
            "target_id": target_id,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "blacklist_matches": blacklist_matches,
            "keyword_flags": keyword_flags,
            "matched_flags": matched_flags,
            "summary": s4_out.result.get("summary", ""),
            "recommended_action": s4_out.result.get("recommended_action", ""),
            "report": context.get("report", ""),
            "approval_required": True,
            "validator_result": val_out.result,
        },
        total_cost_yen=total_cost_yen,
        total_duration_ms=total_duration,
        approval_required=True,
        risk_score=risk_score,
        risk_level=risk_level,
        matched_flags=matched_flags,
        screening_target=screening_target,
        report_generated=report_generated,
    )
