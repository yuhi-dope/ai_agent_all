"""弁護士 契約書レビューパイプライン

契約書のリスク条項を自動検出し、修正案を提示する。
最終判断・助言は弁護士が行う。

Steps:
  Step 1: contract_reader      契約書データの読み込み・種別判定
  Step 2: clause_extractor     条項の抽出・分類
  Step 3: risk_analyzer        リスク条項の検出・スコアリング
  Step 4: suggestion_generator 修正案の生成
  Step 5: output_validator     バリデーション
"""
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 契約書種別の定義
# ---------------------------------------------------------------------------

CONTRACT_TYPES: dict[str, dict[str, Any]] = {
    "nda": {
        "name": "秘密保持契約（NDA）",
        "key_clauses": [
            "秘密情報の定義", "目的外利用の禁止", "第三者開示の禁止",
            "有効期間", "残存条項", "損害賠償", "返還義務",
        ],
    },
    "service_agreement": {
        "name": "業務委託契約",
        "key_clauses": [
            "業務内容", "委託料・支払条件", "納期", "検収",
            "知的財産の帰属", "再委託", "秘密保持", "損害賠償",
            "解除", "競業避止", "反社排除",
        ],
    },
    "sales_agreement": {
        "name": "売買契約",
        "key_clauses": [
            "売買目的物", "代金・支払条件", "引渡し", "所有権移転",
            "契約不適合責任", "危険負担", "損害賠償", "解除", "反社排除",
        ],
    },
    "license_agreement": {
        "name": "ライセンス契約",
        "key_clauses": [
            "ライセンスの範囲", "利用料・ロイヤリティ", "独占/非独占",
            "サブライセンス", "知的財産の保証", "監査権",
            "契約期間・更新", "解除", "損害賠償",
        ],
    },
    "employment_contract": {
        "name": "雇用契約",
        "key_clauses": [
            "業務内容", "勤務地", "労働時間", "賃金", "賞与",
            "休日・休暇", "退職・解雇", "秘密保持", "競業避止",
            "知的財産の帰属", "試用期間",
        ],
    },
    "lease_agreement": {
        "name": "賃貸借契約",
        "key_clauses": [
            "賃貸物件", "賃料・共益費", "契約期間・更新",
            "敷金・保証金", "原状回復", "禁止事項",
            "解約予告期間", "修繕義務", "反社排除",
        ],
    },
    "terms_of_service": {
        "name": "利用規約",
        "key_clauses": [
            "サービス内容", "利用料金", "禁止事項",
            "知的財産", "免責事項", "サービス変更・終了",
            "個人情報", "準拠法・管轄",
        ],
    },
    "other": {
        "name": "その他の契約",
        "key_clauses": [
            "契約目的", "対価・支払", "期間", "解除",
            "損害賠償", "秘密保持", "反社排除", "管轄",
        ],
    },
}

# ---------------------------------------------------------------------------
# リスクパターン定義
# ---------------------------------------------------------------------------

RISK_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "unlimited_liability",
        "name": "損害賠償の上限なし",
        "keywords": ["損害賠償", "一切の損害", "全ての損害"],
        "exclude_keywords": ["上限", "限度額", "委託料の範囲"],
        "severity": "high",
        "suggestion": "損害賠償の上限を設定する（例: 本契約に基づく委託料の総額を上限とする）",
    },
    {
        "id": "unilateral_termination",
        "name": "一方的解除権",
        "keywords": ["いつでも解除", "催告なく解除", "直ちに解除", "理由の如何を問わず"],
        "severity": "high",
        "suggestion": "解除事由を限定列挙し、催告期間（例: 30日）を設ける",
    },
    {
        "id": "auto_renewal_trap",
        "name": "自動更新の通知期間が短い",
        "keywords": ["自動的に更新", "自動更新"],
        "severity": "medium",
        "suggestion": "更新拒絶の通知期間を確認（30日以上が望ましい）。自動更新を望まない場合はカレンダー管理が必要",
    },
    {
        "id": "broad_ip_assignment",
        "name": "知的財産の広範な譲渡",
        "keywords": ["一切の知的財産", "著作権を含む一切の権利", "全ての権利を譲渡"],
        "severity": "high",
        "suggestion": "譲渡範囲を成果物に限定し、バックグラウンドIPは除外する。著作者人格権の不行使条項にも注意",
    },
    {
        "id": "excessive_noncompete",
        "name": "過度な競業避止",
        "keywords": ["競業避止", "競合する事業", "同種の事業"],
        "severity": "medium",
        "suggestion": "競業避止の期間（2年以内）・地域・範囲が合理的か確認。過度な制限は公序良俗違反で無効の可能性",
    },
    {
        "id": "no_limitation_period",
        "name": "契約不適合責任の期間が長い/無期限",
        "keywords": ["永久に", "期間の制限なく", "無期限"],
        "severity": "medium",
        "suggestion": "契約不適合責任の期間を明記する（例: 引渡しから1年以内）",
    },
    {
        "id": "governing_law_foreign",
        "name": "外国法の準拠法",
        "keywords": ["外国法", "ニューヨーク州法", "カリフォルニア州法", "英国法", "シンガポール法"],
        "severity": "medium",
        "suggestion": "日本法を準拠法とするよう交渉。やむを得ない場合は外国法の内容を確認",
    },
    {
        "id": "missing_antisocial",
        "name": "反社排除条項の欠如",
        "keywords": [],
        "check_type": "absence",
        "absence_keywords": ["反社会的勢力", "暴力団"],
        "severity": "medium",
        "suggestion": "反社排除条項を追加する（相互の表明保証+解除権）",
    },
    {
        "id": "blanket_indemnity",
        "name": "包括的な補償条項",
        "keywords": ["補償する", "免責する", "一切の責任を負わない"],
        "severity": "high",
        "suggestion": "補償の範囲を限定し、故意・重過失の場合は免責しない旨を明記",
    },
    {
        "id": "consumer_unfair",
        "name": "消費者契約法上の問題条項",
        "keywords": ["一切の責任を負わない", "いかなる場合も返金しない"],
        "severity": "high",
        "suggestion": "消費者契約法第8-10条により無効となる可能性。事業者の故意・重過失による免責は無効",
        "contract_types": ["terms_of_service"],
    },
]


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class RiskItem:
    """個別リスク検出結果"""
    risk_id: str
    risk_name: str
    severity: str             # high / medium / low
    clause_text: str = ""     # 該当する条項テキスト
    suggestion: str = ""      # 修正案
    clause_index: int = -1


@dataclass
class ContractReviewResult:
    """契約書レビューパイプラインの実行結果"""
    # Step 1
    contract_type: str = ""
    contract_type_name: str = ""
    contract_text: str = ""

    # Step 2
    clauses: list[dict] = field(default_factory=list)
    clause_count: int = 0
    key_clauses_found: list[str] = field(default_factory=list)
    key_clauses_missing: list[str] = field(default_factory=list)

    # Step 3
    risks: list[RiskItem] = field(default_factory=list)
    risk_score: int = 0       # 0-100 (高いほどリスク大)
    high_risks: int = 0
    medium_risks: int = 0

    # Step 4
    suggestions: list[dict] = field(default_factory=list)

    # Step 5
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True

    # メタ
    steps_executed: list[str] = field(default_factory=list)
    company_name: str = ""
    counterparty: str = ""


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class ContractReviewPipeline:
    """
    弁護士 契約書レビューパイプライン

    入力:
    {
        "contract_type": "service_agreement",
        "company_name": "顧問先 株式会社ABC",
        "counterparty": "株式会社XYZ",
        "contract_text": "業務委託契約書\\n\\n第1条（目的）...",
        "clauses": [
            {"index": 1, "title": "目的", "text": "..."},
            {"index": 2, "title": "業務内容", "text": "..."},
            ...
        ]
    }
    """

    async def run(self, input_data: dict[str, Any]) -> ContractReviewResult:
        result = ContractReviewResult()

        result = await self._step1_contract_reader(input_data, result)
        result = await self._step2_clause_extractor(result)
        result = await self._step3_risk_analyzer(result)
        result = await self._step4_suggestion_generator(result)
        result = await self._step5_output_validator(result)

        return result

    async def _step1_contract_reader(
        self, input_data: dict[str, Any], result: ContractReviewResult
    ) -> ContractReviewResult:
        result.steps_executed.append("contract_reader")

        ctype = input_data.get("contract_type", "other")
        if ctype not in CONTRACT_TYPES:
            ctype = "other"

        result.contract_type = ctype
        result.contract_type_name = CONTRACT_TYPES[ctype]["name"]
        result.company_name = input_data.get("company_name", "")
        result.counterparty = input_data.get("counterparty", "")
        result.contract_text = input_data.get("contract_text", "")

        # 条項が直接渡された場合
        if input_data.get("clauses"):
            result.clauses = input_data["clauses"]

        logger.info(
            "contract_reader: type=%s (%s vs %s)",
            ctype, result.company_name, result.counterparty,
        )
        return result

    async def _step2_clause_extractor(
        self, result: ContractReviewResult
    ) -> ContractReviewResult:
        result.steps_executed.append("clause_extractor")

        # 条項が未抽出の場合、テキストから簡易抽出
        if not result.clauses and result.contract_text:
            result.clauses = self._extract_clauses_from_text(result.contract_text)

        result.clause_count = len(result.clauses)

        # キー条項の充足チェック
        ctype_def = CONTRACT_TYPES[result.contract_type]
        expected = set(ctype_def["key_clauses"])
        found_titles = {c.get("title", "") for c in result.clauses}

        # 簡易マッチ（部分一致）
        found_keys = []
        for key in expected:
            if any(key in title for title in found_titles):
                found_keys.append(key)

        result.key_clauses_found = found_keys
        result.key_clauses_missing = [k for k in expected if k not in found_keys]

        logger.info(
            "clause_extractor: %d clauses, %d/%d key clauses found",
            result.clause_count, len(found_keys), len(expected),
        )
        return result

    async def _step3_risk_analyzer(
        self, result: ContractReviewResult
    ) -> ContractReviewResult:
        result.steps_executed.append("risk_analyzer")

        full_text = result.contract_text
        if not full_text:
            full_text = " ".join(c.get("text", "") for c in result.clauses)

        risks: list[RiskItem] = []

        for pattern in RISK_PATTERNS:
            # 契約種別フィルタ
            if "contract_types" in pattern:
                if result.contract_type not in pattern["contract_types"]:
                    continue

            check_type = pattern.get("check_type", "presence")

            if check_type == "absence":
                # 条項が存在しない場合にリスク
                absence_kws = pattern.get("absence_keywords", [])
                if not any(kw in full_text for kw in absence_kws):
                    risks.append(RiskItem(
                        risk_id=pattern["id"],
                        risk_name=pattern["name"],
                        severity=pattern["severity"],
                        suggestion=pattern["suggestion"],
                    ))
            else:
                # キーワードが存在する場合にリスク
                keywords = pattern.get("keywords", [])
                exclude = pattern.get("exclude_keywords", [])

                for kw in keywords:
                    if kw in full_text:
                        # 除外キーワードがあればスキップ
                        if exclude and any(ex in full_text for ex in exclude):
                            continue

                        # 該当条項を特定
                        clause_text = ""
                        clause_idx = -1
                        for c in result.clauses:
                            if kw in c.get("text", ""):
                                clause_text = c.get("text", "")[:200]
                                clause_idx = c.get("index", -1)
                                break

                        risks.append(RiskItem(
                            risk_id=pattern["id"],
                            risk_name=pattern["name"],
                            severity=pattern["severity"],
                            clause_text=clause_text,
                            suggestion=pattern["suggestion"],
                            clause_index=clause_idx,
                        ))
                        break  # 同じパターンで複数マッチしない

        # 欠落条項もリスクとして追加
        for missing in result.key_clauses_missing:
            risks.append(RiskItem(
                risk_id=f"missing_{missing}",
                risk_name=f"条項の欠落: {missing}",
                severity="medium",
                suggestion=f"「{missing}」に関する条項を追加することを推奨します",
            ))

        result.risks = risks
        result.high_risks = sum(1 for r in risks if r.severity == "high")
        result.medium_risks = sum(1 for r in risks if r.severity == "medium")

        # リスクスコア算出 (0-100)
        score = result.high_risks * 20 + result.medium_risks * 10
        result.risk_score = min(score, 100)

        logger.info(
            "risk_analyzer: %d risks (high=%d, medium=%d), score=%d",
            len(risks), result.high_risks, result.medium_risks, result.risk_score,
        )
        return result

    async def _step4_suggestion_generator(
        self, result: ContractReviewResult
    ) -> ContractReviewResult:
        result.steps_executed.append("suggestion_generator")

        suggestions: list[dict] = []

        # リスクに基づく修正提案
        for risk in result.risks:
            suggestions.append({
                "risk_id": risk.risk_id,
                "risk_name": risk.risk_name,
                "severity": risk.severity,
                "current": risk.clause_text[:100] if risk.clause_text else "(条項なし)",
                "suggestion": risk.suggestion,
                "priority": 1 if risk.severity == "high" else 2,
            })

        # 優先度順にソート
        suggestions.sort(key=lambda s: s["priority"])
        result.suggestions = suggestions

        logger.info("suggestion_generator: %d suggestions", len(suggestions))
        return result

    async def _step5_output_validator(
        self, result: ContractReviewResult
    ) -> ContractReviewResult:
        result.steps_executed.append("output_validator")

        errors: list[str] = []
        if result.clause_count == 0 and not result.contract_text:
            errors.append("契約書テキストも条項データもありません")

        result.validation_errors = errors
        result.is_valid = len(errors) == 0

        logger.info("output_validator: %s", "passed" if result.is_valid else f"{len(errors)} errors")
        return result

    # ヘルパー
    @staticmethod
    def _extract_clauses_from_text(text: str) -> list[dict]:
        """テキストから「第X条」パターンで条項を簡易抽出"""
        import re
        pattern = re.compile(r"第(\d+)条[（\(]([^）\)]+)[）\)]")
        clauses: list[dict] = []

        lines = text.split("\n")
        current_clause: dict | None = None

        for line in lines:
            match = pattern.search(line)
            if match:
                if current_clause:
                    clauses.append(current_clause)
                current_clause = {
                    "index": int(match.group(1)),
                    "title": match.group(2),
                    "text": line,
                }
            elif current_clause:
                current_clause["text"] += "\n" + line

        if current_clause:
            clauses.append(current_clause)

        return clauses


async def run_contract_review_pipeline(
    company_id: str = "", input_data: dict | None = None, **kwargs
) -> ContractReviewResult:
    """弁護士 契約書レビューパイプラインの便利関数"""
    pipeline = ContractReviewPipeline()
    return await pipeline.run(input_data or {})
