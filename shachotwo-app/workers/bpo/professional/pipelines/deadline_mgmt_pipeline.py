"""士業事務所 期限管理AIパイプライン"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

PROFESSIONAL_TYPES = [
    "司法書士",
    "行政書士",
    "税理士",
    "社会保険労務士",
    "弁理士",
    "公認会計士",
    "中小企業診断士",
]

CASE_TYPES: dict[str, dict[str, Any]] = {
    "法人登記": {"deadline_days": 14, "penalty": "過料"},
    "商標登録": {"deadline_days": None, "note": "優先日から1年"},
    "確定申告": {"deadline_days": None, "note": "翌年3月15日"},
    "社会保険算定": {"deadline_days": None, "note": "7月10日"},
    "労働保険申告": {"deadline_days": None, "note": "7月10日"},
    "建設業許可更新": {"deadline_days": -30, "note": "満了30日前に申請"},
    "外国人在留資格更新": {"deadline_days": 90, "note": "期限90日前から申請可"},
}

PRIORITY_LEVELS: dict[str, str] = {
    "overdue": "期限超過（即時対応）",
    "critical": "7日以内",
    "high": "30日以内",
    "medium": "90日以内",
    "low": "90日超",
}

# 依頼者への連絡タイミング（期限前X日）
CLIENT_NOTICE_DAYS: list[int] = [90, 30, 7, 1]

# 優先度ソート順
_PRIORITY_ORDER = ["overdue", "critical", "high", "medium", "low"]


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class DeadlineMgmtPipelineResult:
    """期限管理パイプラインの実行結果"""

    # Step 1: 読み込んだ案件一覧（完了除く）
    active_cases: list[dict] = field(default_factory=list)

    # Step 2: 期限分析済み案件（優先度付き）
    analyzed_cases: list[dict] = field(default_factory=list)

    # Step 3: タスク・提出物リスト
    task_list: list[dict] = field(default_factory=list)

    # Step 4: バリデーション結果
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True

    # サマリー（フィルタ済み）
    critical_cases: list[dict] = field(default_factory=list)   # 緊急案件（critical + overdue）
    upcoming_deadlines: list[dict] = field(default_factory=list)  # 直近期限（high + medium）
    overdue_cases: list[dict] = field(default_factory=list)    # 期限超過

    # メタ情報
    reference_date: date | None = None
    office_name: str = ""
    steps_executed: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class DeadlineMgmtPipeline:
    """
    士業事務所 期限管理AIパイプライン

    Step 1: case_reader         案件・期限データ取得（直渡し or テキスト抽出）
    Step 2: deadline_analyzer   期限分析（法定期限・提出期限・更新期限）
    Step 3: task_generator      タスク・提出物リスト生成
    Step 4: output_validator    バリデーション
    """

    # ------------------------------------------------------------------
    # パブリックAPI
    # ------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> DeadlineMgmtPipelineResult:
        """
        パイプライン全体を実行する。

        input_data の形式:
        {
            "cases": [...],         # 案件リスト（必須）
            "reference_date": str,  # ISO形式 "YYYY-MM-DD"（省略時は today）
            "office_name": str,     # 事務所名（省略可）
        }
        """
        result = DeadlineMgmtPipelineResult()

        # Step 1: case_reader
        result = await self._step1_case_reader(input_data, result)

        # Step 2: deadline_analyzer
        result = await self._step2_deadline_analyzer(result)

        # Step 3: task_generator
        result = await self._step3_task_generator(result)

        # Step 4: output_validator
        result = await self._step4_output_validator(result)

        return result

    # ------------------------------------------------------------------
    # Step 1: case_reader
    # ------------------------------------------------------------------

    async def _step1_case_reader(
        self,
        input_data: dict[str, Any],
        result: DeadlineMgmtPipelineResult,
    ) -> DeadlineMgmtPipelineResult:
        """
        案件・期限データを取得する。

        - cases が直渡しされている場合はそのまま使用
        - reference_date が指定されていれば解析、なければ today()
        """
        result.steps_executed.append("case_reader")

        # 基準日の決定
        ref_date_raw = input_data.get("reference_date")
        if ref_date_raw:
            try:
                result.reference_date = date.fromisoformat(ref_date_raw)
            except (ValueError, TypeError):
                logger.warning("Invalid reference_date '%s', falling back to today.", ref_date_raw)
                result.reference_date = date.today()
        else:
            result.reference_date = date.today()

        result.office_name = input_data.get("office_name", "")

        # 案件の取得
        raw_cases: list[dict] = input_data.get("cases", [])

        # 完了案件を除外しつつ、必須フィールドのサニタイズ
        active: list[dict] = []
        for case in raw_cases:
            status = case.get("status", "pending")
            if status == "completed":
                logger.debug("Skipping completed case: %s", case.get("case_id"))
                continue

            # deadline_date が文字列の場合は date に変換
            deadline_raw = case.get("deadline_date")
            if isinstance(deadline_raw, str):
                try:
                    case = dict(case)
                    case["deadline_date"] = date.fromisoformat(deadline_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "Invalid deadline_date '%s' for case %s — skipping.",
                        deadline_raw,
                        case.get("case_id"),
                    )
                    continue
            elif isinstance(deadline_raw, datetime):
                case = dict(case)
                case["deadline_date"] = deadline_raw.date()

            active.append(case)

        result.active_cases = active
        logger.info(
            "case_reader: %d active cases (excluded %d completed)",
            len(active),
            len(raw_cases) - len(active),
        )
        return result

    # ------------------------------------------------------------------
    # Step 2: deadline_analyzer
    # ------------------------------------------------------------------

    async def _step2_deadline_analyzer(
        self,
        result: DeadlineMgmtPipelineResult,
    ) -> DeadlineMgmtPipelineResult:
        """
        各案件の期限を分析し、優先度を付与する。

        優先度ロジック:
            days_to_deadline < 0          → "overdue"
            0 <= days_to_deadline <= 7    → "critical"
            8 <= days_to_deadline <= 30   → "high"
            31 <= days_to_deadline <= 90  → "medium"
            91 +                          → "low"
        """
        result.steps_executed.append("deadline_analyzer")

        ref = result.reference_date or date.today()
        analyzed: list[dict] = []

        for case in result.active_cases:
            deadline: date = case["deadline_date"]
            days_to_deadline = (deadline - ref).days
            priority = self._calc_priority(days_to_deadline)

            # 案件種別の法定情報
            case_type = case.get("case_type", "")
            case_type_info = CASE_TYPES.get(case_type, {})

            # 次回依頼者連絡日
            next_notice_day = self._next_notice_day(days_to_deadline)

            analyzed_case = {
                **case,
                "days_to_deadline": days_to_deadline,
                "priority": priority,
                "priority_label": PRIORITY_LEVELS[priority],
                "case_type_info": case_type_info,
                "next_client_notice_day": next_notice_day,
            }
            analyzed.append(analyzed_case)
            logger.debug(
                "case %s: days=%d priority=%s",
                case.get("case_id"),
                days_to_deadline,
                priority,
            )

        # 優先度順にソート
        analyzed.sort(key=lambda c: _PRIORITY_ORDER.index(c["priority"]))

        result.analyzed_cases = analyzed

        # サマリー分類
        result.overdue_cases = [c for c in analyzed if c["priority"] == "overdue"]
        result.critical_cases = [c for c in analyzed if c["priority"] in ("overdue", "critical")]
        result.upcoming_deadlines = [c for c in analyzed if c["priority"] in ("high", "medium")]

        logger.info(
            "deadline_analyzer: overdue=%d critical=%d high/medium=%d low=%d",
            len(result.overdue_cases),
            sum(1 for c in analyzed if c["priority"] == "critical"),
            len(result.upcoming_deadlines),
            sum(1 for c in analyzed if c["priority"] == "low"),
        )
        return result

    # ------------------------------------------------------------------
    # Step 3: task_generator
    # ------------------------------------------------------------------

    async def _step3_task_generator(
        self,
        result: DeadlineMgmtPipelineResult,
    ) -> DeadlineMgmtPipelineResult:
        """
        各案件のタスク・提出物リストを生成する。

        run_document_generator パターンに倣い、案件種別ごとの
        標準タスクテンプレートを展開する。
        """
        result.steps_executed.append("task_generator")

        task_list: list[dict] = []
        for case in result.analyzed_cases:
            tasks = self._run_document_generator(case)
            task_list.append({
                "case_id": case.get("case_id"),
                "client_name": case.get("client_name"),
                "case_type": case.get("case_type"),
                "priority": case["priority"],
                "deadline_date": case["deadline_date"].isoformat()
                if isinstance(case["deadline_date"], date)
                else str(case["deadline_date"]),
                "tasks": tasks,
                "task_count": len(tasks),
            })

        result.task_list = task_list
        logger.info("task_generator: generated tasks for %d cases", len(task_list))
        return result

    # ------------------------------------------------------------------
    # Step 4: output_validator
    # ------------------------------------------------------------------

    async def _step4_output_validator(
        self,
        result: DeadlineMgmtPipelineResult,
    ) -> DeadlineMgmtPipelineResult:
        """
        出力データのバリデーションを行う。

        - analyzed_cases が空でないこと（入力が空の場合は警告のみ）
        - 全案件に priority が付与されていること
        - task_list の件数が analyzed_cases と一致すること
        """
        result.steps_executed.append("output_validator")

        errors: list[str] = []

        # 案件が0件でも valid とみなす（入力そのものが空のケース）
        if not result.analyzed_cases and result.active_cases:
            errors.append("deadline_analyzer が案件を出力していない。")

        for case in result.analyzed_cases:
            if "priority" not in case:
                errors.append(f"case {case.get('case_id')}: priority が未設定。")
            if "days_to_deadline" not in case:
                errors.append(f"case {case.get('case_id')}: days_to_deadline が未設定。")

        if len(result.task_list) != len(result.analyzed_cases):
            errors.append(
                f"task_list 件数 ({len(result.task_list)}) が "
                f"analyzed_cases 件数 ({len(result.analyzed_cases)}) と不一致。"
            )

        result.validation_errors = errors
        result.is_valid = len(errors) == 0

        if errors:
            logger.warning("output_validator: %d errors: %s", len(errors), errors)
        else:
            logger.info("output_validator: all checks passed.")

        return result

    # ------------------------------------------------------------------
    # ヘルパー
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_priority(days_to_deadline: int) -> str:
        """days_to_deadline から優先度文字列を返す。"""
        if days_to_deadline < 0:
            return "overdue"
        elif days_to_deadline <= 7:
            return "critical"
        elif days_to_deadline <= 30:
            return "high"
        elif days_to_deadline <= 90:
            return "medium"
        else:
            return "low"

    @staticmethod
    def _next_notice_day(days_to_deadline: int) -> int | None:
        """
        CLIENT_NOTICE_DAYS の中で、次に連絡すべき残り日数を返す。
        既に全ての連絡タイミングを過ぎていれば None。
        """
        for notice in sorted(CLIENT_NOTICE_DAYS, reverse=True):
            if days_to_deadline <= notice:
                return notice
        return None

    @staticmethod
    def _run_document_generator(case: dict[str, Any]) -> list[dict[str, str]]:
        """
        案件種別に応じた標準タスク・提出物リストを生成する。

        Returns:
            list of {"task": str, "document": str, "responsible": str}
        """
        case_type = case.get("case_type", "")
        assigned_staff = case.get("assigned_staff", "担当者")
        priority = case.get("priority", "low")

        # 共通タスク（全案件共通）
        common_tasks: list[dict[str, str]] = [
            {
                "task": "依頼者への進捗報告",
                "document": "進捗報告書",
                "responsible": assigned_staff,
            },
        ]

        # 期限超過の場合、追加タスク
        if priority == "overdue":
            common_tasks.insert(0, {
                "task": "期限超過のため即時対応・依頼者へ謝罪連絡",
                "document": "対応報告書",
                "responsible": assigned_staff,
            })

        # 案件種別別タスク
        type_tasks: dict[str, list[dict[str, str]]] = {
            "法人登記": [
                {"task": "登記申請書の作成", "document": "登記申請書", "responsible": assigned_staff},
                {"task": "添付書類の収集", "document": "株主総会議事録・就任承諾書等", "responsible": assigned_staff},
                {"task": "法務局への申請", "document": "申請一式", "responsible": assigned_staff},
            ],
            "商標登録": [
                {"task": "商標調査（先行商標確認）", "document": "商標調査報告書", "responsible": assigned_staff},
                {"task": "商標登録願の作成", "document": "商標登録願", "responsible": assigned_staff},
                {"task": "特許庁への出願", "document": "出願書類一式", "responsible": assigned_staff},
            ],
            "確定申告": [
                {"task": "決算書・帳簿の確認", "document": "貸借対照表・損益計算書", "responsible": assigned_staff},
                {"task": "確定申告書の作成", "document": "確定申告書", "responsible": assigned_staff},
                {"task": "税務署への提出", "document": "申告書一式", "responsible": assigned_staff},
            ],
            "社会保険算定": [
                {"task": "4・5・6月の報酬データ収集", "document": "給与台帳", "responsible": assigned_staff},
                {"task": "算定基礎届の作成", "document": "算定基礎届", "responsible": assigned_staff},
                {"task": "年金事務所への提出", "document": "算定基礎届一式", "responsible": assigned_staff},
            ],
            "労働保険申告": [
                {"task": "賃金総額の集計", "document": "賃金集計表", "responsible": assigned_staff},
                {"task": "労働保険申告書の作成", "document": "労働保険申告書", "responsible": assigned_staff},
                {"task": "労働局への提出", "document": "申告書一式", "responsible": assigned_staff},
            ],
            "建設業許可更新": [
                {"task": "更新申請書類の準備", "document": "建設業許可更新申請書", "responsible": assigned_staff},
                {"task": "決算変更届の確認", "document": "決算変更届（直近5年分）", "responsible": assigned_staff},
                {"task": "都道府県への申請", "document": "更新申請書類一式", "responsible": assigned_staff},
            ],
            "外国人在留資格更新": [
                {"task": "在留資格更新申請書の作成", "document": "在留資格更新許可申請書", "responsible": assigned_staff},
                {"task": "添付書類の収集", "document": "パスポート・在留カード・雇用証明書等", "responsible": assigned_staff},
                {"task": "入国管理局への申請", "document": "申請書類一式", "responsible": assigned_staff},
            ],
        }

        specific_tasks = type_tasks.get(case_type, [
            {"task": "案件内容の確認", "document": "確認チェックシート", "responsible": assigned_staff},
            {"task": "必要書類の収集", "document": "必要書類一覧", "responsible": assigned_staff},
            {"task": "申請・提出", "document": "申請書類一式", "responsible": assigned_staff},
        ])

        return specific_tasks + common_tasks


async def run_deadline_mgmt_pipeline(company_id: str = "", input_data: dict | None = None, **kwargs) -> DeadlineMgmtPipelineResult:
    """士業期限管理パイプラインを実行する便利関数"""
    pipeline = DeadlineMgmtPipeline()
    return await pipeline.run(input_data or {})
