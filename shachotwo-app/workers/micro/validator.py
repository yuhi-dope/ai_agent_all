"""output_validator マイクロエージェント。生成物の必須フィールド・整合性をチェックする。"""
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)


async def run_output_validator(input: MicroAgentInput) -> MicroAgentOutput:
    """
    生成されたドキュメント・データの整合性を検証する（LLM不使用）。

    payload:
        document (dict): 検証対象データ
        required_fields (list[str]): 必須フィールド名リスト
        numeric_fields (list[str], optional): 数値であるべきフィールド
        positive_fields (list[str], optional): 正の数であるべきフィールド
        rules (list[dict], optional): カスタム検証ルール
            例: [{"field": "amount", "op": "gte", "value": 0}]

    result:
        valid (bool): 全必須チェック通過かどうか
        missing (list[str]): 存在しない必須フィールド
        empty (list[str]): 空値の必須フィールド
        type_errors (list[str]): 型エラーのフィールド
        warnings (list[str]): 警告（失敗ではないが注意が必要）
    """
    start_ms = int(time.time() * 1000)
    agent_name = "output_validator"

    try:
        document: dict[str, Any] = input.payload.get("document", {})
        required_fields: list[str] = input.payload.get("required_fields", [])
        numeric_fields: list[str] = input.payload.get("numeric_fields", [])
        positive_fields: list[str] = input.payload.get("positive_fields", [])
        custom_rules: list[dict] = input.payload.get("rules", [])

        missing: list[str] = []
        empty: list[str] = []
        type_errors: list[str] = []
        warnings: list[str] = []

        # 必須フィールドチェック
        for field in required_fields:
            if field not in document:
                missing.append(field)
            elif document[field] is None or document[field] == "" or document[field] == []:
                empty.append(field)

        # 数値型チェック
        for field in numeric_fields:
            if field in document and document[field] is not None:
                try:
                    float(document[field])
                except (TypeError, ValueError):
                    type_errors.append(f"{field}: 数値ではありません（{document[field]}）")

        # 正の数チェック
        for field in positive_fields:
            if field in document and document[field] is not None:
                try:
                    val = float(document[field])
                    if val < 0:
                        warnings.append(f"{field}: 負の値です（{val}）")
                    elif val == 0:
                        warnings.append(f"{field}: ゼロです")
                except (TypeError, ValueError):
                    pass

        # カスタムルール
        _OPS = {
            "gte": lambda a, b: a >= b,
            "lte": lambda a, b: a <= b,
            "gt":  lambda a, b: a > b,
            "lt":  lambda a, b: a < b,
            "eq":  lambda a, b: a == b,
            "ne":  lambda a, b: a != b,
        }
        for rule in custom_rules:
            field = rule.get("field", "")
            op = rule.get("op", "")
            threshold = rule.get("value")
            if field in document and op in _OPS:
                try:
                    if not _OPS[op](float(document[field]), float(threshold)):
                        warnings.append(
                            f"{field}: ルール違反（{document[field]} {op} {threshold}）"
                        )
                except (TypeError, ValueError):
                    pass

        # 合格判定（missing と empty と type_errors がなければOK）
        valid = not missing and not empty and not type_errors

        # confidence: エラー数に応じて下げる
        total_checks = len(required_fields) + len(numeric_fields) + len(positive_fields) + len(custom_rules)
        error_count = len(missing) + len(empty) + len(type_errors)
        confidence = max(0.0, 1.0 - (error_count / total_checks)) if total_checks > 0 else 1.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,  # バリデーター自体は常に成功（結果はresult.validで確認）
            result={
                "valid": valid,
                "missing": missing,
                "empty": empty,
                "type_errors": type_errors,
                "warnings": warnings,
            },
            confidence=round(confidence, 3),
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"output_validator error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
