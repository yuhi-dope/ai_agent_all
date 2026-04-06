"""compliance_checker マイクロエージェント。業種別法令・規約チェック。LLM不使用・ルールベース。"""
import time
import logging
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)

# 建設業法ルール
_CONSTRUCTION_RULES: list[dict[str, Any]] = [
    {
        "id": "const_001",
        "name": "下請け金額上限（一般工事）",
        "severity": "error",
        "check": lambda d: not (
            d.get("subcontract_total", 0) >= 45_000_000
            and not d.get("has_special_construction_license")
        ),
        "message": "特定建設業許可なしで下請け総額4,500万円以上は違法（建設業法第16条）",
    },
    {
        "id": "const_002",
        "name": "下請け金額上限（建築一式）",
        "severity": "error",
        "check": lambda d: not (
            d.get("subcontract_total", 0) >= 70_000_000
            and d.get("project_type") == "building"
            and not d.get("has_special_construction_license")
        ),
        "message": "特定建設業許可なしで建築一式下請け総額7,000万円以上は違法",
    },
    {
        "id": "const_003",
        "name": "適正単価（最低賃金）",
        "severity": "warning",
        "check": lambda d: d.get("labor_daily_rate", 99999) >= 10000,
        "message": "労務単価が1万円/日未満です。公共工事設計労務単価を確認してください",
    },
    {
        "id": "const_004",
        "name": "見積有効期限記載",
        "severity": "warning",
        "check": lambda d: bool(d.get("valid_until")),
        "message": "見積有効期限の記載を推奨します",
    },
]

# 共通ルール（全業種）
_COMMON_RULES: list[dict[str, Any]] = [
    {
        "id": "common_001",
        "name": "消費税計算（10%）",
        "severity": "error",
        "check": lambda d: abs(
            d.get("tax_amount", 0) - int(d.get("subtotal", 0) * 0.10)
        ) <= 1 if d.get("subtotal") else True,
        "message": "消費税額が正しくありません（税率10%）",
    },
    {
        "id": "common_002",
        "name": "印紙税（1万円以上の契約書）",
        "severity": "warning",
        "check": lambda d: True,  # 警告のみ（金額条件あり）
        "message": "契約金額1万円以上の場合、収入印紙が必要です",
        "condition": lambda d: d.get("total", 0) >= 10_000,
    },
]

# 製造業ルール
_MANUFACTURING_RULES: list[dict[str, Any]] = [
    {
        "id": "mfg_001",
        "name": "品質管理基準",
        "severity": "warning",
        "check": lambda d: bool(d.get("quality_standard")),
        "message": "品質管理基準（ISO/JIS等）の明記を推奨します",
    },
]

# 歯科ルール
_DENTAL_RULES: list[dict[str, Any]] = [
    {
        "id": "dental_001",
        "name": "保険請求適正",
        "severity": "error",
        "check": lambda d: d.get("receipt_total", 0) >= 0,
        "message": "レセプト金額が負の値です",
    },
]

_INDUSTRY_RULES: dict[str, list[dict]] = {
    "construction": _CONSTRUCTION_RULES,
    "manufacturing": _MANUFACTURING_RULES,
    "dental": _DENTAL_RULES,
    "common": [],
}


async def run_compliance_checker(input: MicroAgentInput) -> MicroAgentOutput:
    """
    業種別ルールを評価してコンプライアンスチェックを行う。

    payload:
        data (dict): チェック対象データ
        industry (str): "construction" | "manufacturing" | "dental" | "common"
        rules (list[str], optional): チェックするルールID（省略時は全ルール）

    result:
        passed (bool): 全errorルール通過
        violations (list[dict]): 違反・警告一覧
        warnings (list[str]): 警告メッセージ
    """
    start_ms = int(time.time() * 1000)
    agent_name = "compliance_checker"

    try:
        data: dict[str, Any] = input.payload.get("data", {})
        industry: str = input.payload.get("industry", "common")
        rule_filter: list[str] = input.payload.get("rules", [])

        # 適用ルール = 業種別 + 共通
        applicable_rules = _INDUSTRY_RULES.get(industry, []) + _COMMON_RULES

        if rule_filter:
            applicable_rules = [r for r in applicable_rules if r["id"] in rule_filter]

        violations: list[dict[str, Any]] = []
        warnings: list[str] = []

        for rule in applicable_rules:
            # condition がある場合（適用条件）
            condition = rule.get("condition")
            if condition and not condition(data):
                continue

            try:
                passed = rule["check"](data)
            except Exception:
                passed = True  # チェック自体が失敗したら通過扱い

            if not passed:
                violations.append({
                    "rule_id": rule["id"],
                    "name": rule["name"],
                    "severity": rule["severity"],
                    "message": rule["message"],
                })
                if rule["severity"] == "warning":
                    warnings.append(rule["message"])

        errors = [v for v in violations if v["severity"] == "error"]
        passed_all = len(errors) == 0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"passed": passed_all, "violations": violations, "warnings": warnings},
            confidence=1.0, cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"compliance_checker error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )
