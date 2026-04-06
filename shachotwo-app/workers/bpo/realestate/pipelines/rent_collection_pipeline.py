"""不動産業 家賃回収AIパイプライン"""
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from workers.micro.message import run_message_drafter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# 滞納損害金（民法改正後の法定利率）
LATE_PAYMENT_RATE_ANNUAL = Decimal("0.06")  # 年6%
LATE_PAYMENT_RATE_DAILY = LATE_PAYMENT_RATE_ANNUAL / 365

# 督促ステージ
NOTICE_STAGES: dict[int, str] = {
    1: "督促状（初回）",    # 滞納1-15日
    2: "催告書（2回目）",   # 滞納16-30日
    3: "内容証明郵便",      # 滞納31-60日
    4: "法的措置予告",      # 滞納61日以上
}

OVERDUE_DAYS_THRESHOLDS: dict[str, int] = {
    "warning": 3,    # 3日以上で警告
    "notice1": 15,   # 15日以上で督促状
    "notice2": 30,   # 30日以上で催告書
    "legal":   61,   # 61日以上で法的措置予告
}

# 家賃の支払期日（一般的に毎月27日払い）
DEFAULT_PAYMENT_DUE_DAY = 27


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------

@dataclass
class RentCollectionPipelineResult:
    """家賃回収パイプライン実行結果"""
    property_name: str
    reference_date: date
    total_tenants: int
    paid_count: int
    arrears_tenants: list[dict] = field(default_factory=list)
    total_arrears_amount: int = 0
    notices_required: list[dict] = field(default_factory=list)
    steps_executed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パイプライン
# ---------------------------------------------------------------------------

class RentCollectionPipeline:
    """
    不動産業 家賃回収AIパイプライン

    Step 1: tenant_reader      — 入居者・家賃データ取得
    Step 2: payment_checker    — 入金確認（当月入金状況チェック）
    Step 3: arrears_calculator — 滞納計算（滞納日数・滞納額・滞納損害金）
    Step 4: notice_drafter     — 催告書・督促状ドラフト生成
    Step 5: output_validator   — バリデーション
    """

    # ------------------------------------------------------------------
    # Step 1: tenant_reader
    # ------------------------------------------------------------------

    async def tenant_reader(self, input_data: dict) -> dict:
        """
        入居者・家賃データを取得・正規化する。

        input_data["tenants"] にリストが渡される場合はそのまま利用する。
        reference_date が省略された場合は today() を使用する。

        Returns:
            {
                "tenants": [...],
                "reference_date": date,
                "property_name": str,
            }
        """
        tenants_raw: list[dict] = input_data.get("tenants", [])
        property_name: str = input_data.get("property_name", "不明物件")

        # reference_date の解決
        ref_raw = input_data.get("reference_date")
        if ref_raw:
            if isinstance(ref_raw, date):
                reference_date = ref_raw
            else:
                reference_date = date.fromisoformat(str(ref_raw))
        else:
            reference_date = date.today()

        # 各テナントのフィールドを正規化
        tenants: list[dict] = []
        for raw in tenants_raw:
            tenant = dict(raw)
            # payment_due_date を date 型に
            pdd = raw.get("payment_due_date")
            if pdd and not isinstance(pdd, date):
                tenant["payment_due_date"] = date.fromisoformat(str(pdd))
            # actual_payment_date を date 型に（None はそのまま）
            apd = raw.get("actual_payment_date")
            if apd and not isinstance(apd, date):
                tenant["actual_payment_date"] = date.fromisoformat(str(apd))
            else:
                tenant["actual_payment_date"] = None
            tenants.append(tenant)

        logger.info(
            f"[Step1/tenant_reader] 物件={property_name}, "
            f"入居者数={len(tenants)}, 参照日={reference_date}"
        )

        return {
            "tenants": tenants,
            "reference_date": reference_date,
            "property_name": property_name,
        }

    # ------------------------------------------------------------------
    # Step 2: payment_checker
    # ------------------------------------------------------------------

    async def payment_checker(self, step1_result: dict) -> dict:
        """
        各テナントの入金状況を確認し、未払い・一部払いを検出する。

        Returns:
            step1_result に "payment_status" を追加したdict。
            payment_status: list[dict] — 各テナントの入金判定結果
        """
        tenants = step1_result["tenants"]
        reference_date: date = step1_result["reference_date"]

        payment_status: list[dict] = []
        for tenant in tenants:
            monthly_rent: int = int(tenant.get("monthly_rent", 0))
            actual_amount = tenant.get("actual_payment_amount")
            actual_date = tenant.get("actual_payment_date")
            due_date: date = tenant["payment_due_date"]

            if actual_date is not None and actual_amount is not None:
                actual_amount = int(actual_amount)
                if actual_amount >= monthly_rent:
                    status = "paid"
                else:
                    # 一部入金（不足）
                    shortage = monthly_rent - actual_amount
                    status = "partial"
            else:
                actual_amount = 0
                shortage = monthly_rent
                if reference_date <= due_date:
                    # まだ支払期日を過ぎていない
                    status = "pending"
                else:
                    status = "unpaid"

            payment_status.append({
                "tenant_id": tenant.get("tenant_id"),
                "tenant_name": tenant.get("tenant_name"),
                "room_number": tenant.get("room_number"),
                "monthly_rent": monthly_rent,
                "payment_due_date": due_date,
                "actual_payment_date": actual_date,
                "actual_payment_amount": actual_amount,
                "status": status,
                "shortage": shortage if status in ("unpaid", "partial") else 0,
            })

        logger.info(
            f"[Step2/payment_checker] "
            f"支払済={sum(1 for p in payment_status if p['status'] == 'paid')}, "
            f"未払={sum(1 for p in payment_status if p['status'] == 'unpaid')}, "
            f"一部払={sum(1 for p in payment_status if p['status'] == 'partial')}"
        )

        return {**step1_result, "payment_status": payment_status}

    # ------------------------------------------------------------------
    # Step 3: arrears_calculator
    # ------------------------------------------------------------------

    async def arrears_calculator(self, step2_result: dict) -> dict:
        """
        滞納テナントの滞納日数・滞納損害金・督促ステージを計算する。

        Returns:
            step2_result に "arrears" を追加したdict。
            arrears: list[dict] — 滞納テナント毎の計算結果
        """
        reference_date: date = step2_result["reference_date"]
        payment_status: list[dict] = step2_result["payment_status"]

        arrears: list[dict] = []
        total_arrears_amount = 0

        for ps in payment_status:
            if ps["status"] not in ("unpaid", "partial"):
                continue

            due_date: date = ps["payment_due_date"]
            shortage: int = ps["shortage"]
            overdue_days = (reference_date - due_date).days

            if overdue_days <= 0:
                # 期日未到来の一部払い — 警告のみ
                arrears.append({
                    **ps,
                    "overdue_days": 0,
                    "late_fee": 0,
                    "total_overdue": shortage,
                    "notice_stage": None,
                    "notice_stage_label": None,
                    "is_warning": True,
                })
                continue

            # 滞納損害金 = 不足額 × 日利 × 滞納日数
            late_fee = int(shortage * LATE_PAYMENT_RATE_DAILY * overdue_days)
            total_overdue = shortage + late_fee
            total_arrears_amount += total_overdue

            # 督促ステージ判定
            if overdue_days <= OVERDUE_DAYS_THRESHOLDS["notice1"]:
                stage = 1
            elif overdue_days <= OVERDUE_DAYS_THRESHOLDS["notice2"]:
                stage = 2
            elif overdue_days < OVERDUE_DAYS_THRESHOLDS["legal"]:
                stage = 3
            else:
                stage = 4

            arrears.append({
                **ps,
                "overdue_days": overdue_days,
                "late_fee": late_fee,
                "total_overdue": total_overdue,
                "notice_stage": stage,
                "notice_stage_label": NOTICE_STAGES[stage],
                "is_warning": overdue_days >= OVERDUE_DAYS_THRESHOLDS["warning"],
            })

        logger.info(
            f"[Step3/arrears_calculator] 滞納テナント={len(arrears)}, "
            f"滞納総額={total_arrears_amount:,}円"
        )

        return {
            **step2_result,
            "arrears": arrears,
            "total_arrears_amount": total_arrears_amount,
        }

    # ------------------------------------------------------------------
    # Step 4: notice_drafter
    # ------------------------------------------------------------------

    async def notice_drafter(
        self,
        step3_result: dict,
        company_id: str | None = None,
    ) -> dict:
        """
        滞納テナント毎に督促状・催告書等のドラフトを生成する。

        run_message_drafter を使用。LLM失敗時はテンプレートで継続。

        Returns:
            step3_result に "notices" を追加したdict。
            notices: list[dict] — 各テナントの文書ドラフト
        """
        arrears: list[dict] = step3_result["arrears"]
        property_name: str = step3_result["property_name"]
        reference_date: date = step3_result["reference_date"]

        notices: list[dict] = []

        for ar in arrears:
            stage_label = ar.get("notice_stage_label")
            if stage_label is None:
                # 期日前の一部払いは通知不要
                continue

            context = {
                "property_name": property_name,
                "tenant_name": ar["tenant_name"],
                "room_number": ar["room_number"],
                "monthly_rent": ar["monthly_rent"],
                "payment_due_date": ar["payment_due_date"].isoformat()
                    if isinstance(ar["payment_due_date"], date)
                    else str(ar["payment_due_date"]),
                "overdue_days": ar["overdue_days"],
                "late_fee": ar["late_fee"],
                "total_overdue": ar["total_overdue"],
                "reference_date": reference_date.isoformat(),
            }

            try:
                draft = await run_message_drafter(
                    document_type=stage_label,
                    context=context,
                    company_id=company_id,
                )
                notices.append({
                    "tenant_id": ar["tenant_id"],
                    "tenant_name": ar["tenant_name"],
                    "room_number": ar["room_number"],
                    "notice_stage": ar["notice_stage"],
                    "notice_stage_label": stage_label,
                    "document_subject": draft.subject,
                    "document_body": draft.body,
                    "model_used": draft.model_used,
                    "is_template_fallback": draft.is_template_fallback,
                })
            except Exception as e:
                logger.error(
                    f"[Step4/notice_drafter] ドラフト生成失敗 "
                    f"tenant={ar['tenant_id']}: {e}"
                )
                notices.append({
                    "tenant_id": ar["tenant_id"],
                    "tenant_name": ar["tenant_name"],
                    "room_number": ar["room_number"],
                    "notice_stage": ar["notice_stage"],
                    "notice_stage_label": stage_label,
                    "document_subject": f"【{stage_label}】家賃滞納について",
                    "document_body": "(生成失敗 — 手動作成が必要)",
                    "model_used": None,
                    "is_template_fallback": True,
                    "error": str(e),
                })

        logger.info(f"[Step4/notice_drafter] 生成文書数={len(notices)}")

        return {**step3_result, "notices": notices}

    # ------------------------------------------------------------------
    # Step 5: output_validator
    # ------------------------------------------------------------------

    async def output_validator(self, step4_result: dict) -> RentCollectionPipelineResult:
        """
        パイプライン全体の出力を検証し、最終結果を構築する。

        Returns:
            RentCollectionPipelineResult
        """
        arrears: list[dict] = step4_result.get("arrears", [])
        notices: list[dict] = step4_result.get("notices", [])
        payment_status: list[dict] = step4_result.get("payment_status", [])
        total_arrears_amount: int = step4_result.get("total_arrears_amount", 0)
        property_name: str = step4_result.get("property_name", "")
        reference_date: date = step4_result["reference_date"]

        warnings: list[str] = []

        # 滞納テナントに通知が漏れていないか確認
        stage_tenant_ids = {
            ar["tenant_id"] for ar in arrears if ar.get("notice_stage") is not None
        }
        notice_tenant_ids = {n["tenant_id"] for n in notices}
        missed = stage_tenant_ids - notice_tenant_ids
        if missed:
            warnings.append(f"通知ドラフト未生成のテナントID: {missed}")

        # 滞納合計額の検証
        calculated_total = sum(
            ar["total_overdue"] for ar in arrears
            if ar.get("notice_stage") is not None
        )
        if calculated_total != total_arrears_amount:
            warnings.append(
                f"滞納総額の不一致: 記録={total_arrears_amount}, 再計算={calculated_total}"
            )

        paid_count = sum(1 for ps in payment_status if ps["status"] == "paid")

        logger.info(
            f"[Step5/output_validator] "
            f"入居者総数={len(payment_status)}, 入金済={paid_count}, "
            f"滞納={len(arrears)}, 通知={len(notices)}, 警告={len(warnings)}"
        )

        return RentCollectionPipelineResult(
            property_name=property_name,
            reference_date=reference_date,
            total_tenants=len(payment_status),
            paid_count=paid_count,
            arrears_tenants=arrears,
            total_arrears_amount=total_arrears_amount,
            notices_required=notices,
            steps_executed=[
                "tenant_reader",
                "payment_checker",
                "arrears_calculator",
                "notice_drafter",
                "output_validator",
            ],
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # メインエントリ
    # ------------------------------------------------------------------

    async def run(
        self,
        input_data: dict,
        company_id: str | None = None,
    ) -> RentCollectionPipelineResult:
        """
        5ステップのパイプラインを順番に実行する。

        Args:
            input_data: {
                "tenants": [...],
                "reference_date": "YYYY-MM-DD" (省略可),
                "property_name": "物件名",
            }
            company_id: テナントID（LLMコスト追跡用）

        Returns:
            RentCollectionPipelineResult
        """
        step1 = await self.tenant_reader(input_data)
        step2 = await self.payment_checker(step1)
        step3 = await self.arrears_calculator(step2)
        step4 = await self.notice_drafter(step3, company_id=company_id)
        result = await self.output_validator(step4)
        return result


async def run_rent_collection_pipeline(company_id: str = "", input_data: dict | None = None, **kwargs) -> RentCollectionPipelineResult:
    """不動産家賃管理パイプラインを実行する便利関数"""
    pipeline = RentCollectionPipeline()
    return await pipeline.run(input_data or {}, company_id=company_id)
