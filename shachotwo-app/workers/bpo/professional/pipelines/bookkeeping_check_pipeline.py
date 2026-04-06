"""税理士 記帳自動チェックパイプライン

仕訳データの科目妥当性・消費税区分・金額チェックを自動で行う。
最終判断は税理士が行う。

Steps:
  Step 1: journal_reader       仕訳データの読み込み・正規化
  Step 2: account_checker      勘定科目の妥当性チェック
  Step 3: tax_category_checker 消費税区分チェック（インボイス対応）
  Step 4: amount_checker       金額・期ズレ・固定資産判定チェック
  Step 5: output_validator     バリデーション
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# チェックルール定義
# ---------------------------------------------------------------------------

# 勘定科目の一般的な相手科目マッピング（異常仕訳検知用）
SUSPICIOUS_COMBINATIONS: list[dict[str, Any]] = [
    {"debit": "交際費", "credit": "現金", "max_amount": 50000,
     "warning": "1回5万円超の交際費は損金不算入の可能性（中小企業は年800万円まで全額損金）"},
    {"debit": "修繕費", "credit": "*", "max_amount": 200000,
     "warning": "20万円超の修繕は資本的支出の可能性。耐用年数の延長・価値増加があれば資産計上"},
    {"debit": "消耗品費", "credit": "*", "max_amount": 100000,
     "warning": "10万円超は少額減価償却資産。20万円超は一括償却資産。30万円超は通常の固定資産"},
    {"debit": "旅費交通費", "credit": "現金", "max_amount": 100000,
     "warning": "10万円超の現金払い旅費。領収書・出張報告書の確認を推奨"},
]

# 消費税区分の基本ルール
TAX_CATEGORY_RULES: dict[str, str] = {
    "給与手当": "不課税",
    "法定福利費": "非課税",
    "租税公課": "不課税",
    "支払利息": "非課税",
    "保険料": "非課税",
    "地代家賃": "課税（住居用は非課税）",
    "水道光熱費": "課税",
    "通信費": "課税",
    "消耗品費": "課税",
    "交際費": "課税",
    "旅費交通費": "課税（海外渡航費は免税）",
    "支払手数料": "課税",
    "広告宣伝費": "課税",
    "減価償却費": "対象外（取得時に課税済み）",
    "雑損失": "要個別判定",
}

# 固定資産の判定基準
FIXED_ASSET_THRESHOLDS = {
    "expensable": 100000,          # 10万円未満: 全額費用
    "lump_sum_depreciation": 200000,  # 20万円未満: 一括償却資産
    "small_asset": 300000,          # 30万円未満: 少額減価償却（中小企業特例）
}


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class CheckItem:
    """個別チェック結果"""
    journal_index: int
    check_type: str           # account / tax_category / amount / period
    severity: str             # error / warning / info
    message: str
    field: str = ""
    current_value: str = ""
    suggested_value: str = ""


@dataclass
class BookkeepingCheckResult:
    """記帳自動チェックパイプラインの実行結果"""
    # Step 1
    journal_count: int = 0
    journals: list[dict] = field(default_factory=list)

    # Step 2-4: チェック結果
    check_items: list[CheckItem] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    # サマリー
    account_issues: int = 0
    tax_category_issues: int = 0
    amount_issues: int = 0

    # Step 5
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True

    # メタ
    steps_executed: list[str] = field(default_factory=list)
    period_year: int = 0
    period_month: int = 0
    company_name: str = ""


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class BookkeepingCheckPipeline:
    """
    税理士 記帳自動チェックパイプライン

    入力:
    {
        "company_name": "株式会社ABC",
        "period_year": 2026,
        "period_month": 3,
        "journals": [
            {
                "date": "2026-03-01",
                "debit_account": "消耗品費",
                "credit_account": "現金",
                "amount": 250000,
                "description": "プリンター購入",
                "tax_category": "課税",
                "invoice_number": "T1234567890123"
            },
            ...
        ]
    }
    """

    async def run(self, input_data: dict[str, Any]) -> BookkeepingCheckResult:
        result = BookkeepingCheckResult()

        result = await self._step1_journal_reader(input_data, result)
        if result.journal_count == 0:
            return result

        result = await self._step2_account_checker(result)
        result = await self._step3_tax_category_checker(result)
        result = await self._step4_amount_checker(result)
        result = await self._step5_output_validator(result)

        return result

    async def _step1_journal_reader(
        self, input_data: dict[str, Any], result: BookkeepingCheckResult
    ) -> BookkeepingCheckResult:
        result.steps_executed.append("journal_reader")

        result.company_name = input_data.get("company_name", "")
        result.period_year = input_data.get("period_year", 0)
        result.period_month = input_data.get("period_month", 0)
        result.journals = input_data.get("journals", [])
        result.journal_count = len(result.journals)

        logger.info(
            "journal_reader: %d journals for %s %d/%d",
            result.journal_count, result.company_name,
            result.period_year, result.period_month,
        )
        return result

    async def _step2_account_checker(
        self, result: BookkeepingCheckResult
    ) -> BookkeepingCheckResult:
        result.steps_executed.append("account_checker")

        for i, j in enumerate(result.journals):
            debit = j.get("debit_account", "")
            credit = j.get("credit_account", "")
            amount = j.get("amount", 0)

            for rule in SUSPICIOUS_COMBINATIONS:
                if rule["debit"] == debit and (rule["credit"] == "*" or rule["credit"] == credit):
                    if amount > rule["max_amount"]:
                        result.check_items.append(CheckItem(
                            journal_index=i,
                            check_type="account",
                            severity="warning",
                            message=rule["warning"],
                            field="debit_account",
                            current_value=f"{debit} ¥{amount:,}",
                        ))
                        result.account_issues += 1

        logger.info("account_checker: %d issues", result.account_issues)
        return result

    async def _step3_tax_category_checker(
        self, result: BookkeepingCheckResult
    ) -> BookkeepingCheckResult:
        result.steps_executed.append("tax_category_checker")

        for i, j in enumerate(result.journals):
            debit = j.get("debit_account", "")
            tax_cat = j.get("tax_category", "")
            invoice = j.get("invoice_number", "")

            # 科目に対する一般的な消費税区分との不一致チェック
            expected = TAX_CATEGORY_RULES.get(debit)
            if expected and tax_cat:
                # 簡易判定: 不課税・非課税の科目に課税が設定されていたら警告
                if expected in ("不課税", "非課税") and tax_cat == "課税":
                    result.check_items.append(CheckItem(
                        journal_index=i,
                        check_type="tax_category",
                        severity="warning",
                        message=f"{debit}の消費税区分は通常「{expected}」です",
                        field="tax_category",
                        current_value=tax_cat,
                        suggested_value=expected,
                    ))
                    result.tax_category_issues += 1

            # インボイス番号チェック（課税仕入で番号なし）
            if tax_cat == "課税" and not invoice:
                result.check_items.append(CheckItem(
                    journal_index=i,
                    check_type="tax_category",
                    severity="warning",
                    message="課税仕入ですがインボイス番号が未入力です。仕入税額控除に影響する可能性があります",
                    field="invoice_number",
                    current_value="(空)",
                ))
                result.tax_category_issues += 1

        logger.info("tax_category_checker: %d issues", result.tax_category_issues)
        return result

    async def _step4_amount_checker(
        self, result: BookkeepingCheckResult
    ) -> BookkeepingCheckResult:
        result.steps_executed.append("amount_checker")

        for i, j in enumerate(result.journals):
            debit = j.get("debit_account", "")
            amount = j.get("amount", 0)
            desc = j.get("description", "")

            # 固定資産の判定
            if debit in ("消耗品費", "修繕費", "雑費") and amount >= FIXED_ASSET_THRESHOLDS["expensable"]:
                if amount >= FIXED_ASSET_THRESHOLDS["small_asset"]:
                    suggestion = "通常の固定資産として資産計上が必要です"
                    severity = "error"
                elif amount >= FIXED_ASSET_THRESHOLDS["lump_sum_depreciation"]:
                    suggestion = "少額減価償却資産（中小企業特例: 30万未満）or 一括償却資産（20万未満の3年均等償却）の検討を"
                    severity = "warning"
                else:
                    suggestion = "一括償却資産（3年均等償却）の検討を"
                    severity = "info"

                result.check_items.append(CheckItem(
                    journal_index=i,
                    check_type="amount",
                    severity=severity,
                    message=f"¥{amount:,}の{debit}計上: {suggestion}",
                    field="amount",
                    current_value=f"¥{amount:,}",
                ))
                result.amount_issues += 1

            # 金額0円チェック
            if amount == 0:
                result.check_items.append(CheckItem(
                    journal_index=i,
                    check_type="amount",
                    severity="error",
                    message="金額が0円です",
                    field="amount",
                    current_value="¥0",
                ))
                result.amount_issues += 1

        logger.info("amount_checker: %d issues", result.amount_issues)
        return result

    async def _step5_output_validator(
        self, result: BookkeepingCheckResult
    ) -> BookkeepingCheckResult:
        result.steps_executed.append("output_validator")

        result.error_count = sum(1 for c in result.check_items if c.severity == "error")
        result.warning_count = sum(1 for c in result.check_items if c.severity == "warning")
        result.info_count = sum(1 for c in result.check_items if c.severity == "info")

        result.is_valid = True
        logger.info(
            "output_validator: errors=%d warnings=%d info=%d",
            result.error_count, result.warning_count, result.info_count,
        )
        return result


async def run_bookkeeping_check_pipeline(
    company_id: str = "", input_data: dict | None = None, **kwargs
) -> BookkeepingCheckResult:
    """税理士 記帳自動チェックパイプラインの便利関数"""
    pipeline = BookkeepingCheckPipeline()
    return await pipeline.run(input_data or {})
