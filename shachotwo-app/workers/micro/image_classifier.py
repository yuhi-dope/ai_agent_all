"""image_classifier マイクロエージェント。画像をカテゴリに分類する。

建設業の工事写真分類、医療のレセプトタイプ判定等に使用する。
LLM Vision API（Gemini Flash）を使った画像分類を提供し、
API利用不可時はフォールバックとして低確信度のmock結果を返す。
"""
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from workers.micro.models import MicroAgentError, MicroAgentInput, MicroAgentOutput
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

AGENT_NAME = "image_classifier"

_SYSTEM_PROMPT = """あなたは画像分類の専門家です。
与えられた画像を指定されたカテゴリのいずれかに分類してください。

出力形式（JSONのみ）:
{
  "primary_category": "最も確率の高いカテゴリ名",
  "confidence_score": 0.85,
  "all_scores": {
    "カテゴリA": 0.85,
    "カテゴリB": 0.10,
    "カテゴリC": 0.05
  },
  "description": "画像の内容の簡潔な説明",
  "labels": ["カテゴリA"]
}

ルール:
- all_scores の値の合計は 1.0 になるようにする
- confidence_score は primary_category のスコアと一致させる
- multi_label が true の場合、labels にはスコア 0.2 以上のカテゴリを全て含める
- multi_label が false の場合、labels には primary_category のみ含める
- 必ずJSONのみを返す（説明文不要）
"""


def _load_image_base64(payload: dict[str, Any]) -> str:
    """image_path または image_base64 から base64 文字列を取得する。"""
    if "image_base64" in payload and payload["image_base64"]:
        return payload["image_base64"]

    if "image_path" in payload and payload["image_path"]:
        image_path = Path(payload["image_path"])
        if not image_path.exists():
            raise MicroAgentError(
                AGENT_NAME,
                "image_load",
                f"画像ファイルが存在しません: {payload['image_path']}",
            )
        if not image_path.is_file():
            raise MicroAgentError(
                AGENT_NAME,
                "image_load",
                f"指定されたパスはファイルではありません: {payload['image_path']}",
            )
        try:
            with open(image_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except OSError as e:
            raise MicroAgentError(
                AGENT_NAME,
                "image_load",
                f"画像の読み込みに失敗しました: {e}",
            ) from e

    raise MicroAgentError(
        AGENT_NAME,
        "input_validation",
        "image_path または image_base64 のいずれかが必要です",
    )


def _build_user_prompt(
    image_base64: str,
    categories: list[str],
    context: str,
    multi_label: bool,
) -> str:
    """LLMに送るユーザープロンプトを構築する。"""
    categories_str = "\n".join(f"- {c}" for c in categories)
    multi_label_note = (
        "複数のカテゴリが当てはまる場合はスコア0.2以上を全てlabelsに含めてください。"
        if multi_label
        else "最も確率の高い1つのカテゴリのみをlabelsに含めてください。"
    )

    return f"""コンテキスト: {context}

分類カテゴリ一覧:
{categories_str}

{multi_label_note}

この画像を上記カテゴリに分類してください。画像データ（base64）:
data:image/jpeg;base64,{image_base64[:50]}... （省略）

JSONのみで回答してください。"""


def _build_vision_message(
    image_base64: str,
    categories: list[str],
    context: str,
    multi_label: bool,
) -> str:
    """Vision対応のユーザーメッセージを構築する（画像をbase64埋め込み）。"""
    categories_str = "\n".join(f"- {c}" for c in categories)
    multi_label_note = (
        "複数のカテゴリが当てはまる場合はスコア0.2以上を全てlabelsに含めてください。"
        if multi_label
        else "最も確率の高い1つのカテゴリのみをlabelsに含めてください。"
    )

    return f"""コンテキスト: {context}

分類カテゴリ一覧:
{categories_str}

{multi_label_note}

添付の画像を上記カテゴリに分類し、JSONのみで回答してください。

[画像データ (base64)]: {image_base64}"""


def _parse_llm_response(
    raw: str,
    categories: list[str],
    multi_label: bool,
) -> dict[str, Any]:
    """LLMのレスポンスからJSONを抽出してバリデーションする。"""
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        raise MicroAgentError(
            AGENT_NAME,
            "parse",
            f"LLMがJSONを返しませんでした: {raw[:200]}",
        )

    parsed: dict[str, Any] = json.loads(json_match.group())

    # 必須フィールドの補完
    primary_category = parsed.get("primary_category", categories[0] if categories else "その他")
    confidence_score = float(parsed.get("confidence_score", 0.5))
    confidence_score = max(0.0, min(1.0, confidence_score))

    all_scores: dict[str, float] = parsed.get("all_scores", {})
    if not all_scores:
        # all_scores が空の場合は primary に confidence を割り当て、残りを均等分配
        remaining = 1.0 - confidence_score
        other_categories = [c for c in categories if c != primary_category]
        if other_categories:
            per_other = remaining / len(other_categories)
            all_scores = {c: per_other for c in other_categories}
        all_scores[primary_category] = confidence_score

    description = parsed.get("description", "")

    # labels の決定
    if multi_label:
        labels = [c for c, s in all_scores.items() if s >= 0.2]
        if not labels:
            labels = [primary_category]
    else:
        labels = [primary_category]

    return {
        "primary_category": primary_category,
        "confidence_score": confidence_score,
        "all_scores": all_scores,
        "description": description,
        "labels": labels,
    }


def _make_fallback_result(categories: list[str], multi_label: bool) -> dict[str, Any]:
    """LLM Vision API利用不可時のフォールバック結果を生成する。"""
    primary = categories[0] if categories else "その他"
    fallback_confidence = 0.3

    if len(categories) > 1:
        remaining = 1.0 - fallback_confidence
        per_other = remaining / (len(categories) - 1)
        all_scores = {c: per_other for c in categories if c != primary}
    else:
        all_scores = {}
    all_scores[primary] = fallback_confidence

    return {
        "primary_category": primary,
        "confidence_score": fallback_confidence,
        "all_scores": all_scores,
        "description": "フォールバック: Vision APIが利用できないため低確信度の結果を返します",
        "labels": [primary],
        "fallback": True,
    }


async def run_image_classifier(inp: MicroAgentInput) -> MicroAgentOutput:
    """
    画像をLLM Vision APIで指定カテゴリに分類する。

    payload:
        image_path (str): 画像ファイルのパス（image_base64と排他）
        image_base64 (str): base64エンコードされた画像データ（image_pathと排他）
        categories (list[str]): 分類カテゴリのリスト
        context (str, optional): 分類コンテキストの説明
        multi_label (bool, optional): Trueの場合、複数カテゴリを返す（デフォルト: False）

    result:
        primary_category (str): 最も確率の高いカテゴリ
        confidence_score (float): primary_categoryの確信度（0.0〜1.0）
        all_scores (dict): 各カテゴリのスコア
        description (str): 画像の内容説明
        labels (list[str]): 分類ラベル（multi_label時は複数）
        fallback (bool, optional): Vision API利用不可時のみTrue
    """
    start_ms = int(time.time() * 1000)
    llm = get_llm_client()

    # 入力バリデーション
    categories: list[str] = inp.payload.get("categories", [])
    if not categories:
        raise MicroAgentError(AGENT_NAME, "input_validation", "categories が空です")

    context: str = inp.payload.get("context", "この画像を分類してください")
    multi_label: bool = bool(inp.payload.get("multi_label", False))

    # 画像データ取得（失敗時は MicroAgentError を伝播）
    image_base64 = _load_image_base64(inp.payload)

    # LLM Vision API 呼び出し
    vision_message = _build_vision_message(image_base64, categories, context, multi_label)

    try:
        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": vision_message},
            ],
            tier=ModelTier.FAST,
            task_type=AGENT_NAME,
            company_id=inp.company_id,
            requires_vision=True,
            temperature=0.1,
        ))

        try:
            result = _parse_llm_response(response.content, categories, multi_label)
        except (MicroAgentError, json.JSONDecodeError) as parse_err:
            logger.warning("image_classifier: LLM response parse failed, using fallback: %s", parse_err)
            result = _make_fallback_result(categories, multi_label)
        cost_yen = response.cost_yen
        confidence = result["confidence_score"]

    except Exception as e:
        logger.warning("image_classifier: LLM Vision API unavailable, using fallback: %s", e)
        result = _make_fallback_result(categories, multi_label)
        cost_yen = 0.0
        confidence = result["confidence_score"]

    duration_ms = int(time.time() * 1000) - start_ms
    return MicroAgentOutput(
        agent_name=AGENT_NAME,
        success=True,
        result=result,
        confidence=confidence,
        cost_yen=cost_yen,
        duration_ms=duration_ms,
    )
