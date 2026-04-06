"""社労士 手続き書類自動生成パイプライン

1号業務の各種届出書類（資格取得届・喪失届・算定基礎届等）の
ドラフトを自動生成する。最終確認・提出は社労士が行う。

Steps:
  Step 1: input_reader       入力データの読み込み・正規化
  Step 2: requirement_checker 届出種別の要件チェック・必要書類判定
  Step 3: form_generator     届出書類のドラフト生成
  Step 4: compliance_checker  法定要件の準拠チェック
  Step 5: output_validator   バリデーション
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 届出種別の定義
# ---------------------------------------------------------------------------

PROCEDURE_TYPES: dict[str, dict[str, Any]] = {
    "health_insurance_acquisition": {
        "name": "健康保険・厚生年金 資格取得届",
        "deadline_days": 5,
        "deadline_from": "入社日",
        "required_fields": [
            "employee_name", "birth_date", "gender", "basic_pension_number",
            "my_number", "monthly_remuneration", "employment_date",
        ],
        "optional_fields": ["dependents", "previous_insurer"],
        "submission_to": "年金事務所（または健保組合）",
    },
    "health_insurance_loss": {
        "name": "健康保険・厚生年金 資格喪失届",
        "deadline_days": 5,
        "deadline_from": "退職日の翌日",
        "required_fields": [
            "employee_name", "insured_number", "loss_date", "loss_reason",
        ],
        "optional_fields": ["needs_separation_certificate", "voluntary_continuation"],
        "submission_to": "年金事務所（または健保組合）",
    },
    "employment_insurance_acquisition": {
        "name": "雇用保険 資格取得届",
        "deadline_days": 10,
        "deadline_from": "入社日の翌月10日",
        "required_fields": [
            "employee_name", "birth_date", "my_number",
            "employment_date", "wage_type", "weekly_hours",
        ],
        "optional_fields": ["previous_employer"],
        "submission_to": "ハローワーク",
    },
    "employment_insurance_loss": {
        "name": "雇用保険 資格喪失届・離職票",
        "deadline_days": 10,
        "deadline_from": "離職日の翌日",
        "required_fields": [
            "employee_name", "insured_number", "separation_date",
            "separation_reason", "wage_history",
        ],
        "optional_fields": ["needs_separation_certificate"],
        "submission_to": "ハローワーク",
    },
    "monthly_remuneration_change": {
        "name": "月額変更届",
        "deadline_days": 0,
        "deadline_from": "速やかに",
        "required_fields": [
            "employee_name", "insured_number",
            "change_month", "remuneration_3months",
            "current_grade", "new_grade",
        ],
        "optional_fields": [],
        "submission_to": "年金事務所",
    },
    "standard_remuneration_determination": {
        "name": "算定基礎届",
        "deadline_days": 0,
        "deadline_from": "7月1日〜10日",
        "required_fields": [
            "employee_name", "insured_number",
            "remuneration_april", "remuneration_may", "remuneration_june",
            "payment_base_days_april", "payment_base_days_may", "payment_base_days_june",
        ],
        "optional_fields": ["annual_average_application"],
        "submission_to": "年金事務所",
    },
    "bonus_payment_report": {
        "name": "賞与支払届",
        "deadline_days": 5,
        "deadline_from": "賞与支給日",
        "required_fields": [
            "employee_name", "insured_number",
            "bonus_amount", "payment_date",
        ],
        "optional_fields": [],
        "submission_to": "年金事務所",
    },
    "article36_agreement": {
        "name": "36協定届",
        "deadline_days": 0,
        "deadline_from": "有効期間開始前",
        "required_fields": [
            "company_name", "business_description",
            "worker_representative", "overtime_limit_monthly",
            "overtime_limit_annual", "valid_from", "valid_to",
        ],
        "optional_fields": ["special_clause"],
        "submission_to": "所轄労働基準監督署",
    },
}


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class ProcedureGenerationResult:
    """手続き書類自動生成パイプラインの実行結果"""
    # Step 1
    procedure_type: str = ""
    procedure_name: str = ""
    input_data: dict = field(default_factory=dict)

    # Step 2
    requirement_check: dict = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    is_eligible: bool = False

    # Step 3
    generated_form: dict = field(default_factory=dict)
    form_fields: list[dict] = field(default_factory=list)

    # Step 4
    compliance_warnings: list[str] = field(default_factory=list)
    deadline_date: date | None = None
    is_compliant: bool = True

    # Step 5
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True

    # メタ
    steps_executed: list[str] = field(default_factory=list)
    company_name: str = ""


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class ProcedureGenerationPipeline:
    """
    社労士 手続き書類自動生成パイプライン

    入力:
    {
        "procedure_type": "health_insurance_acquisition",
        "company_name": "株式会社ABC",
        "employee_data": { ... },
        "reference_date": "2026-04-04"
    }
    """

    async def run(self, input_data: dict[str, Any]) -> ProcedureGenerationResult:
        result = ProcedureGenerationResult()

        result = await self._step1_input_reader(input_data, result)
        if not result.procedure_type:
            return result

        result = await self._step2_requirement_checker(result)
        result = await self._step3_form_generator(result)
        result = await self._step4_compliance_checker(input_data, result)
        result = await self._step5_output_validator(result)

        return result

    # Step 1: 入力データの読み込み・正規化
    async def _step1_input_reader(
        self, input_data: dict[str, Any], result: ProcedureGenerationResult
    ) -> ProcedureGenerationResult:
        result.steps_executed.append("input_reader")

        proc_type = input_data.get("procedure_type", "")
        if proc_type not in PROCEDURE_TYPES:
            result.validation_errors.append(
                f"未対応の届出種別: {proc_type}. "
                f"対応種別: {', '.join(PROCEDURE_TYPES.keys())}"
            )
            result.is_valid = False
            return result

        result.procedure_type = proc_type
        result.procedure_name = PROCEDURE_TYPES[proc_type]["name"]
        result.company_name = input_data.get("company_name", "")
        result.input_data = input_data.get("employee_data", {})

        logger.info("input_reader: type=%s company=%s", proc_type, result.company_name)
        return result

    # Step 2: 届出種別の要件チェック・必要書類判定
    async def _step2_requirement_checker(
        self, result: ProcedureGenerationResult
    ) -> ProcedureGenerationResult:
        result.steps_executed.append("requirement_checker")

        proc_def = PROCEDURE_TYPES[result.procedure_type]
        required = proc_def["required_fields"]
        provided = set(result.input_data.keys())

        missing = [f for f in required if f not in provided]
        result.missing_fields = missing

        result.requirement_check = {
            "required_fields": required,
            "optional_fields": proc_def["optional_fields"],
            "provided_fields": list(provided),
            "missing_fields": missing,
            "submission_to": proc_def["submission_to"],
        }

        # 必須項目がすべて揃っていれば eligible
        result.is_eligible = len(missing) == 0

        if missing:
            logger.warning("requirement_checker: missing fields: %s", missing)
        else:
            logger.info("requirement_checker: all required fields present")

        return result

    # Step 3: 届出書類のドラフト生成
    async def _step3_form_generator(
        self, result: ProcedureGenerationResult
    ) -> ProcedureGenerationResult:
        result.steps_executed.append("form_generator")

        proc_def = PROCEDURE_TYPES[result.procedure_type]
        data = result.input_data

        # フォームフィールドの生成（実データがある場合は埋める、なければ空欄）
        form_fields: list[dict] = []
        all_fields = proc_def["required_fields"] + proc_def["optional_fields"]

        for field_name in all_fields:
            value = data.get(field_name, "")
            is_required = field_name in proc_def["required_fields"]
            form_fields.append({
                "field_name": field_name,
                "field_label": self._field_label(field_name),
                "value": value,
                "is_required": is_required,
                "is_filled": bool(value),
            })

        result.form_fields = form_fields
        result.generated_form = {
            "procedure_type": result.procedure_type,
            "procedure_name": result.procedure_name,
            "company_name": result.company_name,
            "submission_to": proc_def["submission_to"],
            "fields": form_fields,
            "generated_at": datetime.now().isoformat(),
            "status": "draft",
            "note": "※ 本書類はAIが生成したドラフトです。社労士による最終確認が必要です。",
        }

        filled = sum(1 for f in form_fields if f["is_filled"])
        logger.info(
            "form_generator: %d/%d fields filled", filled, len(form_fields)
        )
        return result

    # Step 4: 法定要件の準拠チェック
    async def _step4_compliance_checker(
        self, input_data: dict[str, Any], result: ProcedureGenerationResult
    ) -> ProcedureGenerationResult:
        result.steps_executed.append("compliance_checker")

        proc_def = PROCEDURE_TYPES[result.procedure_type]
        warnings: list[str] = []

        # 期限チェック
        ref_date_raw = input_data.get("reference_date")
        if ref_date_raw:
            try:
                ref_date = date.fromisoformat(ref_date_raw)
            except (ValueError, TypeError):
                ref_date = date.today()
        else:
            ref_date = date.today()

        event_date_raw = (
            result.input_data.get("employment_date")
            or result.input_data.get("loss_date")
            or result.input_data.get("separation_date")
            or result.input_data.get("payment_date")
        )

        if event_date_raw and proc_def["deadline_days"] > 0:
            try:
                event_date = date.fromisoformat(str(event_date_raw))
                from datetime import timedelta
                deadline = event_date + timedelta(days=proc_def["deadline_days"])
                result.deadline_date = deadline

                if ref_date > deadline:
                    warnings.append(
                        f"期限超過: {proc_def['name']}の提出期限({deadline.isoformat()})を"
                        f"過ぎています。{proc_def['deadline_from']}から"
                        f"{proc_def['deadline_days']}日以内に提出が必要です。"
                    )
                elif (deadline - ref_date).days <= 3:
                    warnings.append(
                        f"期限間近: {proc_def['name']}の提出期限は"
                        f"{deadline.isoformat()}です（残り{(deadline - ref_date).days}日）。"
                    )
            except (ValueError, TypeError):
                pass

        # 必須項目の未入力チェック
        if result.missing_fields:
            warnings.append(
                f"必須項目が未入力です: {', '.join(self._field_label(f) for f in result.missing_fields)}"
            )

        # マイナンバーの取扱い注意
        if result.input_data.get("my_number"):
            warnings.append(
                "マイナンバーが含まれています。取扱いに注意してください。"
                "（安全管理措置・利用目的の明示が必要）"
            )

        result.compliance_warnings = warnings
        result.is_compliant = not any("期限超過" in w for w in warnings)

        logger.info("compliance_checker: %d warnings", len(warnings))
        return result

    # Step 5: バリデーション
    async def _step5_output_validator(
        self, result: ProcedureGenerationResult
    ) -> ProcedureGenerationResult:
        result.steps_executed.append("output_validator")

        errors: list[str] = []
        if not result.generated_form:
            errors.append("フォームが生成されていません")
        if not result.procedure_type:
            errors.append("届出種別が未設定です")

        result.validation_errors.extend(errors)
        result.is_valid = len(result.validation_errors) == 0

        if errors:
            logger.warning("output_validator: %d errors", len(errors))
        else:
            logger.info("output_validator: passed")

        return result

    # ヘルパー: フィールド名→日本語ラベル
    @staticmethod
    def _field_label(field_name: str) -> str:
        labels = {
            "employee_name": "従業員氏名",
            "birth_date": "生年月日",
            "gender": "性別",
            "basic_pension_number": "基礎年金番号",
            "my_number": "マイナンバー",
            "monthly_remuneration": "報酬月額",
            "employment_date": "入社日",
            "dependents": "被扶養者",
            "previous_insurer": "前職の保険者",
            "insured_number": "被保険者番号",
            "loss_date": "喪失日",
            "loss_reason": "喪失理由",
            "needs_separation_certificate": "離職票の要否",
            "voluntary_continuation": "任意継続の希望",
            "separation_date": "離職日",
            "separation_reason": "離職理由",
            "wage_history": "賃金履歴",
            "wage_type": "賃金形態",
            "weekly_hours": "週所定労働時間",
            "previous_employer": "前職情報",
            "change_month": "変動月",
            "remuneration_3months": "変動後3ヶ月の報酬",
            "current_grade": "現在の等級",
            "new_grade": "改定後の等級",
            "remuneration_april": "4月報酬",
            "remuneration_may": "5月報酬",
            "remuneration_june": "6月報酬",
            "payment_base_days_april": "4月支払基礎日数",
            "payment_base_days_may": "5月支払基礎日数",
            "payment_base_days_june": "6月支払基礎日数",
            "annual_average_application": "年間平均申立て",
            "bonus_amount": "賞与額",
            "payment_date": "支給日",
            "company_name": "事業所名",
            "business_description": "業務の種類",
            "worker_representative": "労働者代表",
            "overtime_limit_monthly": "月の上限時間",
            "overtime_limit_annual": "年の上限時間",
            "valid_from": "有効期間開始",
            "valid_to": "有効期間終了",
            "special_clause": "特別条項",
        }
        return labels.get(field_name, field_name)


async def run_procedure_generation_pipeline(
    company_id: str = "", input_data: dict | None = None, **kwargs
) -> ProcedureGenerationResult:
    """社労士 手続き書類自動生成パイプラインの便利関数"""
    pipeline = ProcedureGenerationPipeline()
    return await pipeline.run(input_data or {})
