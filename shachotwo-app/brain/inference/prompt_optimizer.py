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
        .select("id, operations, approval_status, rejection_reason, created_at")
        .eq("company_id", company_id)
        .eq("approval_status", "rejected")
        .gte("created_at", since)
    )
    if pipeline_name:
        # operations JSONB内の pipeline フィールドはサーバー側フィルタが難しいため
        # 全件取得後にPython側でフィルタする
        result = query.execute()
        rows = [
            r for r in (result.data or [])
            if (r.get("operations") or {}).get("pipeline") == pipeline_name
        ]
    else:
        result = query.execute()
        rows = result.data or []

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
