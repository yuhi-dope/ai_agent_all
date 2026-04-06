"""LLMを使ってプロンプトを自動改善する。Gemini最優先→Anthropicフォールバック。

モジュール構成:
- PromptVersion: プロンプトのバージョン管理データクラス
- OptimizationResult: 最適化結果データクラス
- optimize_prompt: 既存のシンプルなプロンプト改善関数（後方互換）
- analyze_rejections: execution_logs から却下パターンを分析
- generate_prompt_improvement: 却下例からプロンプト改善案を生成
- run_optimization_cycle: 全パイプラインの最適化サイクルを実行
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

# 却下理由パターン分類のキーワードマッピング
_REJECTION_PATTERN_KEYWORDS: dict[str, list[str]] = {
    "精度不足": ["精度", "不正確", "間違い", "誤り", "おかしい", "違う", "wrong", "incorrect", "inaccurate"],
    "フォーマット違反": ["フォーマット", "形式", "書式", "format", "構造", "レイアウト", "出力形式"],
    "業界知識不足": ["業界", "専門", "知識", "用語", "業務", "慣習", "ルール", "規定"],
    "計算ミス": ["計算", "数値", "金額", "数字", "合計", "集計", "算出", "ミス", "calculation"],
}

_SYSTEM_ANALYZE = (
    "あなたはAIプロンプトエンジニアリングの専門家です。"
    "失敗事例を分析し、信頼度を0.85以上に改善するプロンプト改善案を提示してください。"
    "改善プロンプト全文をコードブロック(```prompt ... ```)で出力してください。"
)

_SYSTEM_OPTIMIZE = """\
あなたはAIプロンプトエンジニアリングの専門家です。
以下のプロンプトが使われたBPOパイプラインで、ユーザーから却下されたケースがあります。

【現在のプロンプト】
{current_prompt}

【却下された実行例】
{rejection_examples}

【却下理由のパターン】
{rejection_patterns}

以下の観点でプロンプトを改善してください:
1. 却下理由に直接対応する修正
2. 出力フォーマットの明確化
3. 業界知識の補強
4. エッジケースへの対応

改善版プロンプトを出力してください。必ず ```prompt ... ``` ブロックで囲んでください。
"""


@dataclass
class PromptVersion:
    """プロンプトのバージョン管理レコード。"""
    prompt_key: str          # パイプライン名+ステップ名（例: construction_estimation_extract）
    version: int
    prompt_text: str
    created_at: str
    accuracy_before: float
    accuracy_after: float | None
    feedback_summary: str    # 改善の根拠


@dataclass
class OptimizationResult:
    """プロンプト最適化の結果。"""
    prompt_key: str
    current_version: int
    rejection_count: int
    rejection_patterns: dict[str, int]   # {"精度不足": 5, "計算ミス": 3}
    proposed_changes: list[str]          # 改善案リスト
    new_prompt: str | None               # 生成された改善版プロンプト
    confidence: float                    # 改善提案の確信度 (0.0〜1.0)


# ---------------------------------------------------------------------------
# 既存関数（後方互換維持）
# ---------------------------------------------------------------------------

async def optimize_prompt(
    pipeline: str,
    step_name: str,
    failing_examples: list[dict[str, Any]],
    current_prompt: str,
) -> str | None:
    """失敗事例と現在プロンプトからLLMが改善プロンプトを生成。

    Gemini API優先（ModelTier.FAST）、失敗時はAnthropicにフォールバック。
    改善案を生成できない場合は None を返す。
    """
    llm = get_llm_client()

    examples_text = "\n\n".join([
        f"【事例{i+1}】\n入力: {ex.get('input','')[:300]}\n出力: {ex.get('output','')[:300]}\n信頼度: {ex.get('confidence', '?')}"
        for i, ex in enumerate(failing_examples[:5])
    ])

    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_ANALYZE},
                {"role": "user", "content": (
                    f"パイプライン: {pipeline} / ステップ: {step_name}\n\n"
                    f"## 現在のプロンプト\n{current_prompt}\n\n"
                    f"## 低信頼度の事例\n{examples_text}\n\n"
                    "改善プロンプトを提案してください。"
                )},
            ],
            tier=ModelTier.FAST,
            task_type="prompt_optimization",
            max_tokens=2048,
        ))
        return _extract_prompt_block(response.content)
    except Exception as e:
        logger.error("prompt_optimizer error: %s", e)
        return None


# ---------------------------------------------------------------------------
# 新規関数
# ---------------------------------------------------------------------------

async def analyze_rejections(
    company_id: str,
    pipeline_name: str | None = None,
    days: int = 30,
) -> list[dict[str, Any]]:
    """直近N日間の却下フィードバックを分析し、パターンを抽出する。

    Args:
        company_id: テナントID（RLS準拠）
        pipeline_name: 対象パイプライン名（Noneの場合は全パイプライン）
        days: 分析対象日数

    Returns:
        パイプライン+ステップ別の却下分析リスト。各要素は以下のキーを持つ:
        - pipeline: str
        - step_name: str
        - rejection_count: int
        - rejection_reasons: list[str]
        - rejection_patterns: dict[str, int]
        - examples: list[dict]  最大5件の却下事例
    """
    db = get_service_client()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    query = (
        db.table("execution_logs")
        .select("id, operations, approval_status, rejection_reason, feedback_type, created_at")
        .eq("company_id", company_id)
        .eq("approval_status", "rejected")
        .gte("created_at", since)
        .order("created_at", desc=True)
        .limit(50)  # トークン爆発防止: 直近50件でハードリミット
    )
    result = query.execute()
    raw_rows: list[dict[str, Any]] = result.data or []

    # feedback_type フィルタ:
    # 'prompt_improvement_only' または NULL（既存レコード後方互換）のみ対象。
    # 'rule_candidate' はルール候補なのでプロンプト改善には使わない。
    filtered_rows = [
        r for r in raw_rows
        if r.get("feedback_type") in ("prompt_improvement_only", None)
    ]

    # pipeline フィルタ (Python側: JSONB内フィールドはサーバーフィルタが困難)
    if pipeline_name:
        filtered_rows = [
            r for r in filtered_rows
            if (r.get("operations") or {}).get("pipeline") == pipeline_name
        ]

    # 同一 rejection_reason パターンの重複排除: 代表1件（最新）だけ残す
    rows = _deduplicate_by_rejection_pattern(filtered_rows)

    # pipeline+step ごとに集計
    agg: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        ops: dict[str, Any] = row.get("operations") or {}
        pipeline = ops.get("pipeline", "unknown")
        rejection_reason: str = row.get("rejection_reason") or ""

        steps: list[dict[str, Any]] = ops.get("steps", [])
        # ステップ情報がない場合はパイプライン全体を1ステップとして扱う
        if not steps:
            steps = [{"step": "__pipeline__", "result": ops.get("output", "")}]

        for step in steps:
            step_name: str = step.get("step", "unknown")
            key = (pipeline, step_name)
            if key not in agg:
                agg[key] = {
                    "pipeline": pipeline,
                    "step_name": step_name,
                    "rejection_count": 0,
                    "rejection_reasons": [],
                    "examples": [],
                }
            agg[key]["rejection_count"] += 1
            if rejection_reason:
                agg[key]["rejection_reasons"].append(rejection_reason)
            if len(agg[key]["examples"]) < 5:
                agg[key]["examples"].append({
                    "input": str(ops.get("input_data", ""))[:300],
                    "output": str(step.get("result", ""))[:300],
                    "rejection_reason": rejection_reason,
                    "execution_id": row.get("id"),
                })

    # 各集計エントリに却下パターン分類を追加
    results: list[dict[str, Any]] = []
    for data in agg.values():
        all_reasons_text = " ".join(data["rejection_reasons"])
        patterns = _classify_rejection_patterns(all_reasons_text)
        results.append({
            **data,
            "rejection_patterns": patterns,
        })

    # 却下件数の多い順でソート
    results.sort(key=lambda x: x["rejection_count"], reverse=True)
    return results


async def generate_prompt_improvement(
    prompt_key: str,
    current_prompt: str,
    rejection_examples: list[dict[str, Any]],
) -> OptimizationResult:
    """却下例を元にLLMがプロンプト改善案を生成する。

    Args:
        prompt_key: パイプライン+ステップ識別子（例: "construction_estimation_extract"）
        current_prompt: 現在使用中のプロンプトテキスト
        rejection_examples: analyze_rejections で収集した却下事例リスト

    Returns:
        OptimizationResult: 改善案・パターン・新プロンプトを含む結果
    """
    llm = get_llm_client()

    # 却下パターンを集計
    all_reasons_text = " ".join(
        ex.get("rejection_reason", "") for ex in rejection_examples
    )
    rejection_patterns = _classify_rejection_patterns(all_reasons_text)
    rejection_count = len(rejection_examples)

    # 却下事例をテキスト形式に整形
    examples_text = "\n\n".join([
        (
            f"【却下事例{i+1}】\n"
            f"入力: {ex.get('input', '')[:300]}\n"
            f"出力: {ex.get('output', '')[:300]}\n"
            f"却下理由: {ex.get('rejection_reason', '理由不明')}"
        )
        for i, ex in enumerate(rejection_examples[:5])
    ])

    patterns_text = "\n".join(
        f"- {pattern}: {count}件"
        for pattern, count in rejection_patterns.items()
        if count > 0
    ) or "パターン不明"

    system_content = _SYSTEM_OPTIMIZE.format(
        current_prompt=current_prompt,
        rejection_examples=examples_text or "（却下事例なし）",
        rejection_patterns=patterns_text,
    )

    new_prompt: str | None = None
    proposed_changes: list[str] = []
    confidence: float = 0.0

    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": (
                    f"prompt_key: {prompt_key}\n"
                    f"却下件数: {rejection_count}件\n\n"
                    "上記のシステムプロンプトの指示に従い、改善版プロンプトを生成してください。"
                    "また、主な改善点を箇条書きで3〜5点挙げてください（「改善点:」の見出し後に記述）。"
                )},
            ],
            tier=ModelTier.FAST,
            task_type="prompt_optimization",
            max_tokens=3000,
        ))
        content = response.content
        new_prompt = _extract_prompt_block(content)

        # 改善点リストを抽出
        proposed_changes = _extract_proposed_changes(content)

        # 確信度を計算: 却下パターンが明確で事例が多いほど高い
        pattern_count = sum(1 for v in rejection_patterns.values() if v > 0)
        confidence = min(
            0.5
            + (min(rejection_count, 10) / 10) * 0.3   # 事例数ボーナス (最大+0.3)
            + (pattern_count / len(_REJECTION_PATTERN_KEYWORDS)) * 0.2,  # パターン明確性 (最大+0.2)
            1.0,
        )

        logger.info(
            "generate_prompt_improvement: key=%s rejections=%d patterns=%s confidence=%.2f",
            prompt_key, rejection_count, rejection_patterns, confidence,
        )

    except Exception as exc:
        logger.error("generate_prompt_improvement error for key=%s: %s", prompt_key, exc)
        proposed_changes = ["LLM呼び出し失敗のため改善案を生成できませんでした"]
        confidence = 0.0

    return OptimizationResult(
        prompt_key=prompt_key,
        current_version=1,
        rejection_count=rejection_count,
        rejection_patterns=rejection_patterns,
        proposed_changes=proposed_changes,
        new_prompt=new_prompt,
        confidence=confidence,
    )


async def save_prompt_version(
    pipeline: str,
    step_name: str,
    prompt_text: str,
    accuracy_before: float | None = None,
    accuracy_after: float | None = None,
    change_reason: str | None = None,
    company_id: str | None = None,
    created_by: str = "system",
) -> str:
    """新しいプロンプトバージョンをDBに保存する。

    既存の is_active=True バージョンを False に更新してから、
    max(version)+1 の新バージョンを is_active=True で INSERT する。

    Args:
        pipeline: パイプライン名（例: "construction/estimation"）
        step_name: ステップ名（例: "extract"）
        prompt_text: 保存するプロンプトテキスト
        accuracy_before: 改善前の精度スコア（0.0〜1.0）
        accuracy_after: 改善後の精度スコア（0.0〜1.0）
        change_reason: 変更理由のテキスト
        company_id: テナントID（None の場合は全社共通）
        created_by: 作成者識別子

    Returns:
        新規作成されたレコードのID（UUID文字列）

    Raises:
        MicroAgentError相当のException: DB操作失敗時
    """
    db = get_service_client()

    # 既存アクティブバージョンを非アクティブ化
    deactivate_query = (
        db.table("prompt_versions")
        .update({"is_active": False})
        .eq("pipeline", pipeline)
        .eq("step_name", step_name)
        .eq("is_active", True)
    )
    if company_id is not None:
        deactivate_query = deactivate_query.eq("company_id", company_id)
    else:
        deactivate_query = deactivate_query.is_("company_id", "null")
    deactivate_query.execute()

    # max(version) を取得して次バージョン番号を決定
    version_query = (
        db.table("prompt_versions")
        .select("version")
        .eq("pipeline", pipeline)
        .eq("step_name", step_name)
        .order("version", desc=True)
        .limit(1)
    )
    if company_id is not None:
        version_query = version_query.eq("company_id", company_id)
    else:
        version_query = version_query.is_("company_id", "null")
    version_result = version_query.execute()

    max_version = 0
    if version_result.data:
        max_version = version_result.data[0].get("version", 0)
    next_version = max_version + 1

    # 新バージョンを INSERT
    insert_data: dict[str, Any] = {
        "pipeline": pipeline,
        "step_name": step_name,
        "version": next_version,
        "prompt_text": prompt_text,
        "is_active": True,
        "created_by": created_by,
    }
    if company_id is not None:
        insert_data["company_id"] = company_id
    if accuracy_before is not None:
        insert_data["accuracy_before"] = accuracy_before
    if accuracy_after is not None:
        insert_data["accuracy_after"] = accuracy_after
    if change_reason is not None:
        insert_data["change_reason"] = change_reason

    insert_result = db.table("prompt_versions").insert(insert_data).execute()
    new_id: str = insert_result.data[0]["id"]

    logger.info(
        "save_prompt_version: pipeline=%s step=%s version=%d id=%s",
        pipeline, step_name, next_version, new_id,
    )
    return new_id


async def rollback_prompt_version(
    pipeline: str,
    step_name: str,
    company_id: str | None = None,
) -> bool:
    """現在のアクティブバージョンをロールバックし、1つ前のバージョンを復帰させる。

    処理フロー:
    1. 現在の is_active=True バージョンを取得
    2. is_active=True を False に更新
    3. version番号が1つ前のレコードを is_active=True に復帰

    Args:
        pipeline: パイプライン名
        step_name: ステップ名
        company_id: テナントID（None の場合は全社共通）

    Returns:
        True: ロールバック成功 / False: 前バージョンが存在しないため不可
    """
    db = get_service_client()

    # 現在のアクティブバージョンを取得
    active_query = (
        db.table("prompt_versions")
        .select("id, version")
        .eq("pipeline", pipeline)
        .eq("step_name", step_name)
        .eq("is_active", True)
    )
    if company_id is not None:
        active_query = active_query.eq("company_id", company_id)
    else:
        active_query = active_query.is_("company_id", "null")
    active_result = active_query.maybe_single().execute()

    if not active_result or not active_result.data:
        logger.warning(
            "rollback_prompt_version: no active version found for %s/%s",
            pipeline, step_name,
        )
        return False

    current_id: str = active_result.data["id"]
    current_version: int = active_result.data["version"]
    prev_version = current_version - 1

    if prev_version < 1:
        logger.warning(
            "rollback_prompt_version: already at version 1 for %s/%s",
            pipeline, step_name,
        )
        return False

    # 前バージョンのレコードを取得
    prev_query = (
        db.table("prompt_versions")
        .select("id")
        .eq("pipeline", pipeline)
        .eq("step_name", step_name)
        .eq("version", prev_version)
    )
    if company_id is not None:
        prev_query = prev_query.eq("company_id", company_id)
    else:
        prev_query = prev_query.is_("company_id", "null")
    prev_result = prev_query.maybe_single().execute()

    if not prev_result or not prev_result.data:
        logger.warning(
            "rollback_prompt_version: previous version %d not found for %s/%s",
            prev_version, pipeline, step_name,
        )
        return False

    prev_id: str = prev_result.data["id"]

    # 現在バージョンを非アクティブ化
    db.table("prompt_versions").update({"is_active": False}).eq("id", current_id).execute()
    # 前バージョンをアクティブ化
    db.table("prompt_versions").update({"is_active": True}).eq("id", prev_id).execute()

    logger.info(
        "rollback_prompt_version: %s/%s rolled back from v%d to v%d",
        pipeline, step_name, current_version, prev_version,
    )
    return True


async def get_active_prompt(
    pipeline: str,
    step_name: str,
    company_id: str | None = None,
) -> str | None:
    """DBから is_active=True のプロンプトテキストを返す。

    該当レコードが存在しない場合は None を返す。
    company_id が指定されている場合は個社設定を優先し、
    存在しなければ全社共通（company_id IS NULL）にフォールバックする。

    Args:
        pipeline: パイプライン名
        step_name: ステップ名
        company_id: テナントID（None の場合は全社共通のみ参照）

    Returns:
        プロンプトテキスト文字列、または None
    """
    db = get_service_client()

    async def _fetch(cid: str | None) -> str | None:
        q = (
            db.table("prompt_versions")
            .select("prompt_text")
            .eq("pipeline", pipeline)
            .eq("step_name", step_name)
            .eq("is_active", True)
        )
        if cid is not None:
            q = q.eq("company_id", cid)
        else:
            q = q.is_("company_id", "null")
        result = q.maybe_single().execute()
        if result and result.data:
            return result.data.get("prompt_text")
        return None

    # 個社設定を優先
    if company_id is not None:
        text = await _fetch(company_id)
        if text is not None:
            return text

    # 全社共通にフォールバック
    return await _fetch(None)


async def run_optimization_cycle(
    company_id: str,
    min_rejections: int = 5,
) -> list[OptimizationResult]:
    """全パイプラインの最適化サイクルを実行。

    却下件数が min_rejections 以上のパイプライン+ステップに対して
    プロンプト改善案を生成する。

    Args:
        company_id: テナントID（RLS準拠）
        min_rejections: 最低却下件数（この件数未満は対象外）

    Returns:
        最適化結果のリスト（却下件数の多い順）
    """
    # 却下分析（直近30日）
    rejection_analyses = await analyze_rejections(
        company_id=company_id,
        days=30,
    )

    # min_rejections 件以上の却下があるステップだけ対象
    targets = [
        analysis for analysis in rejection_analyses
        if analysis["rejection_count"] >= min_rejections
    ]

    logger.info(
        "run_optimization_cycle: company=%s total_steps=%d targets=%d",
        company_id, len(rejection_analyses), len(targets),
    )

    results: list[OptimizationResult] = []
    for analysis in targets:
        pipeline = analysis["pipeline"]
        step_name = analysis["step_name"]
        prompt_key = f"{pipeline.replace('/', '_')}_{step_name}"
        current_prompt = _read_current_prompt(pipeline, step_name)

        result = await generate_prompt_improvement(
            prompt_key=prompt_key,
            current_prompt=current_prompt,
            rejection_examples=analysis["examples"],
        )
        # analyze_rejections の集計値で上書き（より正確な件数）
        result.rejection_count = analysis["rejection_count"]
        result.rejection_patterns = analysis["rejection_patterns"]
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _deduplicate_by_rejection_pattern(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 rejection_reason パターンの重複を排除し、代表1件（最新）のみ返す。

    rows はすでに created_at DESC でソート済みであることを前提とする。
    rejection_reason が同一文字列のものは最初（最新）だけ残す。
    rejection_reason が空の行はパターン重複判定の対象外として全件残す。
    """
    seen_patterns: set[str] = set()
    deduplicated: list[dict[str, Any]] = []
    for row in rows:
        reason: str = (row.get("rejection_reason") or "").strip()
        if not reason:
            # 理由なし → 重複判定しない（全件残す）
            deduplicated.append(row)
            continue
        if reason in seen_patterns:
            continue
        seen_patterns.add(reason)
        deduplicated.append(row)
    return deduplicated


def _extract_prompt_block(content: str) -> str | None:
    """LLMレスポンスから ```prompt...``` または ``` ``` ブロックを抽出する。"""
    if "```prompt" in content:
        start = content.index("```prompt") + len("```prompt")
        end = content.index("```", start)
        extracted = content[start:end].strip()
    elif "```" in content:
        start = content.index("```") + 3
        end = content.index("```", start)
        extracted = content[start:end].strip()
    else:
        extracted = content.strip()
    return extracted or None


def _extract_proposed_changes(content: str) -> list[str]:
    """LLMレスポンスから「改善点:」以降の箇条書きリストを抽出する。"""
    changes: list[str] = []
    # 「改善点:」「改善点：」「主な改善点」などのパターンを探す
    pattern = re.compile(
        r"(?:改善点[：:]|主な改善点[：:]?|変更点[：:])\s*\n(.*?)(?:\n\n|\Z)",
        re.DOTALL,
    )
    match = pattern.search(content)
    if match:
        lines = match.group(1).strip().split("\n")
        for line in lines:
            cleaned = re.sub(r"^[\s\-・•*\d.]+", "", line).strip()
            if cleaned:
                changes.append(cleaned)

    # 箇条書きがなければ最初の200文字を要約として使用
    if not changes and content.strip():
        summary = content.strip()[:200]
        changes.append(summary)

    return changes[:5]


def _classify_rejection_patterns(reasons_text: str) -> dict[str, int]:
    """却下理由テキストをパターン分類し、各パターンの出現回数を返す。"""
    patterns: dict[str, int] = {key: 0 for key in _REJECTION_PATTERN_KEYWORDS}
    reasons_lower = reasons_text.lower()
    for pattern_name, keywords in _REJECTION_PATTERN_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in reasons_lower:
                patterns[pattern_name] += reasons_lower.count(keyword.lower())
                break  # 同一パターンのカウントは1キーワードのみで十分
    return patterns


def _read_current_prompt(pipeline: str, step_name: str) -> str:
    """llm/prompts/ から現在のプロンプトを読む。なければデフォルト文字列を返す。"""
    import os
    safe_name = pipeline.replace("/", "_") + "_" + step_name
    path = f"llm/prompts/{safe_name}.txt"
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return f"# {pipeline} / {step_name}\n# (プロンプトファイル未作成)"
