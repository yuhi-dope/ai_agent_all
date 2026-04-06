"""行政書士 許可申請書自動生成パイプライン

建設業許可・運輸・産廃等の許認可申請書類のドラフトを自動生成する。
最終確認・提出は行政書士が行う。

Steps:
  Step 1: input_reader         入力データの読み込み・許可種別の特定
  Step 2: requirement_checker  許可要件の充足チェック
  Step 3: document_generator   申請書ドラフト＋添付書類リスト生成
  Step 4: compliance_checker   法定要件・期限チェック
  Step 5: output_validator     バリデーション
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 許認可種別の定義
# ---------------------------------------------------------------------------

PERMIT_TYPES: dict[str, dict[str, Any]] = {
    "construction_new": {
        "name": "建設業許可（新規）",
        "category": "建設業",
        "requirements": [
            {"id": "manager", "name": "経営業務管理責任者", "description": "建設業に関し5年以上の経営経験"},
            {"id": "engineer", "name": "専任技術者", "description": "所定の資格or実務経験10年以上"},
            {"id": "finance", "name": "財産的基礎", "description": "自己資本500万円以上 or 500万円以上の残高証明"},
            {"id": "integrity", "name": "誠実性", "description": "請負契約に関して不正または不誠実な行為をするおそれがないこと"},
            {"id": "disqualification", "name": "欠格要件非該当", "description": "破産者・禁錮以上の刑等に該当しないこと"},
        ],
        "documents": [
            "建設業許可申請書（様式第1号）",
            "工事経歴書（様式第2号）",
            "直前3年の施工金額（様式第3号）",
            "使用人数（様式第4号）",
            "誓約書（様式第6号）",
            "経営業務管理責任者証明書（様式第7号）",
            "専任技術者証明書（様式第8号）",
            "財務諸表（様式第15-17号）",
            "登記事項証明書",
            "納税証明書",
            "残高証明書（500万円以上）",
            "定款の写し",
            "役員等の略歴書",
            "健康保険等の加入状況",
        ],
        "submission_to": "都道府県知事（知事許可）/ 地方整備局（大臣許可）",
        "processing_days": 30,
    },
    "construction_renewal": {
        "name": "建設業許可（更新）",
        "category": "建設業",
        "requirements": [
            {"id": "current_permit", "name": "現在の許可", "description": "有効期間満了の30日前までに申請"},
            {"id": "annual_reports", "name": "決算変更届", "description": "毎年度の決算変更届が提出済みであること"},
            {"id": "manager", "name": "経営業務管理責任者", "description": "引き続き要件を満たしていること"},
            {"id": "engineer", "name": "専任技術者", "description": "引き続き要件を満たしていること"},
        ],
        "documents": [
            "建設業許可申請書（様式第1号）",
            "誓約書（様式第6号）",
            "経営業務管理責任者証明書（様式第7号）",
            "専任技術者証明書（様式第8号）",
            "登記事項証明書",
            "納税証明書",
            "健康保険等の加入状況",
        ],
        "submission_to": "都道府県知事（知事許可）/ 地方整備局（大臣許可）",
        "processing_days": 30,
    },
    "construction_annual_report": {
        "name": "建設業 決算変更届",
        "category": "建設業",
        "requirements": [
            {"id": "deadline", "name": "提出期限", "description": "事業年度終了後4ヶ月以内"},
        ],
        "documents": [
            "変更届出書（様式第22号の2）",
            "工事経歴書（様式第2号）",
            "直前3年の施工金額（様式第3号）",
            "使用人数（様式第4号）",
            "財務諸表（建設業用: 様式第15-17号）",
        ],
        "submission_to": "都道府県知事（知事許可）/ 地方整備局（大臣許可）",
        "processing_days": 0,
    },
    "cargo_transport": {
        "name": "一般貨物自動車運送事業許可",
        "category": "運輸",
        "requirements": [
            {"id": "vehicles", "name": "車両要件", "description": "5台以上の事業用自動車"},
            {"id": "garage", "name": "車庫", "description": "全車両を収容できる車庫"},
            {"id": "office", "name": "営業所", "description": "使用権原のある営業所"},
            {"id": "driver", "name": "運転者", "description": "5人以上の運転者"},
            {"id": "manager", "name": "運行管理者", "description": "運行管理者資格者"},
            {"id": "mechanic", "name": "整備管理者", "description": "整備管理者"},
            {"id": "finance", "name": "資金計画", "description": "所要資金の確保"},
        ],
        "documents": [
            "一般貨物自動車運送事業経営許可申請書",
            "事業計画書",
            "資金計画書",
            "車両一覧表",
            "車庫の図面・写真",
            "営業所の図面",
            "運行管理者の資格証明",
            "残高証明書",
            "登記事項証明書",
        ],
        "submission_to": "地方運輸局",
        "processing_days": 90,
    },
    "waste_collection": {
        "name": "産業廃棄物収集運搬業許可",
        "category": "産廃",
        "requirements": [
            {"id": "course", "name": "講習会修了", "description": "日本産業廃棄物処理振興センターの講習会修了"},
            {"id": "vehicle", "name": "運搬車両", "description": "適切な運搬車両の確保"},
            {"id": "facility", "name": "運搬施設", "description": "飛散防止等の基準を満たす施設"},
            {"id": "finance", "name": "経理的基礎", "description": "事業を的確に行うに足りる経理的基礎"},
            {"id": "disqualification", "name": "欠格要件非該当", "description": "廃棄物処理法の欠格要件に該当しないこと"},
        ],
        "documents": [
            "産業廃棄物収集運搬業許可申請書",
            "事業計画書",
            "講習会修了証の写し",
            "車両の写真・車検証の写し",
            "事務所・車庫の図面",
            "登記事項証明書",
            "直近3年の財務諸表",
            "納税証明書",
            "役員の住民票・登記されていないことの証明書",
        ],
        "submission_to": "都道府県知事（積替え保管なしの場合は積み地・降ろし地の各都道府県）",
        "processing_days": 60,
    },
}


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class RequirementCheckItem:
    requirement_id: str
    requirement_name: str
    is_met: bool
    evidence: str = ""
    note: str = ""


@dataclass
class PermitGenerationResult:
    """許可申請書自動生成パイプラインの実行結果"""
    # Step 1
    permit_type: str = ""
    permit_name: str = ""
    category: str = ""
    applicant_data: dict = field(default_factory=dict)

    # Step 2
    requirement_checks: list[RequirementCheckItem] = field(default_factory=list)
    all_requirements_met: bool = False
    unmet_requirements: list[str] = field(default_factory=list)

    # Step 3
    generated_documents: list[dict] = field(default_factory=list)
    required_attachments: list[str] = field(default_factory=list)

    # Step 4
    compliance_warnings: list[str] = field(default_factory=list)
    estimated_processing_days: int = 0

    # Step 5
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True

    # メタ
    steps_executed: list[str] = field(default_factory=list)
    company_name: str = ""


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class PermitGenerationPipeline:
    """
    行政書士 許可申請書自動生成パイプライン

    入力:
    {
        "permit_type": "construction_new",
        "company_name": "株式会社ABC建設",
        "applicant_data": {
            "representative": "山田太郎",
            "manager_name": "佐藤次郎",
            "manager_experience_years": 8,
            "engineer_name": "田中三郎",
            "engineer_qualification": "1級建築施工管理技士",
            "capital": 10000000,
            "permit_expiry": "2026-06-30",
            ...
        },
        "reference_date": "2026-04-04"
    }
    """

    async def run(self, input_data: dict[str, Any]) -> PermitGenerationResult:
        result = PermitGenerationResult()

        result = await self._step1_input_reader(input_data, result)
        if not result.permit_type:
            return result

        result = await self._step2_requirement_checker(result)
        result = await self._step3_document_generator(result)
        result = await self._step4_compliance_checker(input_data, result)
        result = await self._step5_output_validator(result)

        return result

    async def _step1_input_reader(
        self, input_data: dict[str, Any], result: PermitGenerationResult
    ) -> PermitGenerationResult:
        result.steps_executed.append("input_reader")

        permit_type = input_data.get("permit_type", "")
        if permit_type not in PERMIT_TYPES:
            result.validation_errors.append(
                f"未対応の許可種別: {permit_type}. "
                f"対応種別: {', '.join(PERMIT_TYPES.keys())}"
            )
            result.is_valid = False
            return result

        pdef = PERMIT_TYPES[permit_type]
        result.permit_type = permit_type
        result.permit_name = pdef["name"]
        result.category = pdef["category"]
        result.company_name = input_data.get("company_name", "")
        result.applicant_data = input_data.get("applicant_data", {})

        logger.info("input_reader: type=%s company=%s", permit_type, result.company_name)
        return result

    async def _step2_requirement_checker(
        self, result: PermitGenerationResult
    ) -> PermitGenerationResult:
        result.steps_executed.append("requirement_checker")

        pdef = PERMIT_TYPES[result.permit_type]
        data = result.applicant_data
        checks: list[RequirementCheckItem] = []
        unmet: list[str] = []

        for req in pdef["requirements"]:
            is_met, evidence, note = self._check_requirement(req, data)
            checks.append(RequirementCheckItem(
                requirement_id=req["id"],
                requirement_name=req["name"],
                is_met=is_met,
                evidence=evidence,
                note=note,
            ))
            if not is_met:
                unmet.append(f"{req['name']}: {note or req['description']}")

        result.requirement_checks = checks
        result.unmet_requirements = unmet
        result.all_requirements_met = len(unmet) == 0

        logger.info(
            "requirement_checker: %d/%d met",
            len(checks) - len(unmet), len(checks),
        )
        return result

    async def _step3_document_generator(
        self, result: PermitGenerationResult
    ) -> PermitGenerationResult:
        result.steps_executed.append("document_generator")

        pdef = PERMIT_TYPES[result.permit_type]
        data = result.applicant_data

        # 各書類のドラフト情報を生成
        docs: list[dict] = []
        for doc_name in pdef["documents"]:
            docs.append({
                "document_name": doc_name,
                "status": "draft",
                "auto_fillable": self._is_auto_fillable(doc_name),
                "note": "※ AIが生成したドラフトです。行政書士による最終確認が必要です。",
            })

        result.generated_documents = docs
        result.required_attachments = pdef["documents"]
        result.estimated_processing_days = pdef["processing_days"]

        logger.info("document_generator: %d documents", len(docs))
        return result

    async def _step4_compliance_checker(
        self, input_data: dict[str, Any], result: PermitGenerationResult
    ) -> PermitGenerationResult:
        result.steps_executed.append("compliance_checker")

        warnings: list[str] = []
        data = result.applicant_data

        # 更新許可の期限チェック
        if result.permit_type == "construction_renewal":
            expiry_raw = data.get("permit_expiry")
            if expiry_raw:
                try:
                    expiry = date.fromisoformat(str(expiry_raw))
                    ref_raw = input_data.get("reference_date")
                    ref = date.fromisoformat(ref_raw) if ref_raw else date.today()
                    days_left = (expiry - ref).days

                    if days_left < 0:
                        warnings.append(f"許可が既に失効しています（{expiry.isoformat()}）。新規申請が必要です。")
                    elif days_left < 30:
                        warnings.append(f"許可満了まで{days_left}日。30日前までの申請が必要です。")
                    elif days_left < 90:
                        warnings.append(f"許可満了まで{days_left}日。更新準備を開始してください。")
                except (ValueError, TypeError):
                    pass

        # 決算変更届の未提出チェック
        if result.permit_type in ("construction_renewal",):
            annual_reports_filed = data.get("annual_reports_filed", False)
            if not annual_reports_filed:
                warnings.append("決算変更届の提出状況を確認してください。未提出の年度がある場合、更新ができません。")

        # 要件未充足の警告
        if result.unmet_requirements:
            warnings.append(
                f"以下の要件が未充足です: {'; '.join(result.unmet_requirements)}"
            )

        result.compliance_warnings = warnings
        logger.info("compliance_checker: %d warnings", len(warnings))
        return result

    async def _step5_output_validator(
        self, result: PermitGenerationResult
    ) -> PermitGenerationResult:
        result.steps_executed.append("output_validator")

        errors: list[str] = []
        if not result.generated_documents:
            errors.append("書類が生成されていません")

        result.validation_errors.extend(errors)
        result.is_valid = len(result.validation_errors) == 0

        logger.info("output_validator: %s", "passed" if result.is_valid else f"{len(result.validation_errors)} errors")
        return result

    # ヘルパー
    def _check_requirement(
        self, req: dict, data: dict
    ) -> tuple[bool, str, str]:
        """要件の充足を簡易チェック。(is_met, evidence, note)"""
        rid = req["id"]

        if rid == "manager":
            years = data.get("manager_experience_years", 0)
            name = data.get("manager_name", "")
            if years >= 5 and name:
                return True, f"{name}（経験{years}年）", ""
            return False, "", f"経営経験{years}年（要5年以上）" if years else "経営業務管理責任者の情報なし"

        if rid == "engineer":
            qual = data.get("engineer_qualification", "")
            name = data.get("engineer_name", "")
            if qual and name:
                return True, f"{name}（{qual}）", ""
            return False, "", "専任技術者の情報なし"

        if rid == "finance":
            capital = data.get("capital", 0)
            if capital >= 5000000:
                return True, f"自己資本¥{capital:,}", ""
            return False, "", f"自己資本¥{capital:,}（要500万円以上）"

        # デフォルト: データがあればOK
        val = data.get(rid)
        if val:
            return True, str(val), ""
        return False, "", f"{req['name']}の情報なし"

    @staticmethod
    def _is_auto_fillable(doc_name: str) -> bool:
        """自動入力可能な書類かどうか"""
        auto_fillable = ["様式第1号", "様式第2号", "様式第3号", "様式第4号",
                         "様式第6号", "様式第22号", "変更届出書", "申請書"]
        return any(kw in doc_name for kw in auto_fillable)


async def run_permit_generation_pipeline(
    company_id: str = "", input_data: dict | None = None, **kwargs
) -> PermitGenerationResult:
    """行政書士 許可申請書自動生成パイプラインの便利関数"""
    pipeline = PermitGenerationPipeline()
    return await pipeline.run(input_data or {})
