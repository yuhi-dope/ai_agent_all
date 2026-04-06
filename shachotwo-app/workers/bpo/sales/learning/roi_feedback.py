"""ROI実績計測フィードバック — 導入企業の実績データから提案精度を自動改善する。

導入企業のBPO実行ログを分析し、実際の削減時間・コストを計測。
その結果を win_loss_patterns テーブルに保存し、
次の提案書生成時にLLMプロンプトに注入して精度を向上させる。
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def calculate_roi_actuals(
    company_id: str,
    customer_id: str,
) -> dict[str, Any]:
    """導入企業のBPO実行ログから実際のROIを計算する。

    計測対象:
    - BPO実行回数（execution_logs）
    - 平均処理時間（execution_logs.duration_ms）
    - 手作業換算時間（業種別ベンチマーク × 実行回数）
    - 実際の削減時間 = 手作業換算 - BPO処理時間
    - 月額費用（customers.mrr）
    - ROI = 削減時間の金銭換算 / 月額費用

    Returns:
        {
            "customer_id": str,
            "industry": str,
            "period_days": int,
            "bpo_executions": int,
            "total_bpo_duration_hours": float,
            "estimated_manual_hours": float,
            "actual_saved_hours": float,
            "actual_saved_yen": float,
            "mrr": int,
            "roi_ratio": float,
            "confidence": float,
        }
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        # 顧客情報取得
        cust_result = db.table("customers").select("*").eq("id", customer_id).limit(1).execute()
        if not cust_result.data:
            return {"error": "customer not found", "confidence": 0.0}
        customer = cust_result.data[0]
        industry = customer.get("industry", "unknown")
        mrr = customer.get("mrr", 0)

        # 過去30日のBPO実行ログ
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        logs_result = (
            db.table("execution_logs")
            .select("pipeline, duration_ms, status")
            .eq("company_id", company_id)
            .gte("executed_at", since)
            .eq("status", "completed")
            .execute()
        )
        logs = logs_result.data or []

        if not logs:
            return {"error": "no execution logs", "confidence": 0.0}

        bpo_executions = len(logs)
        total_bpo_ms = sum(l.get("duration_ms", 0) for l in logs)
        total_bpo_hours = total_bpo_ms / (1000 * 60 * 60)

        # 業種別の手作業換算ベンチマーク（1タスクあたりの平均時間）
        MANUAL_HOURS_PER_TASK: dict[str, float] = {
            "construction": 2.0,   # 積算・安全書類等
            "manufacturing": 1.5,  # 見積・品質管理等
            "dental": 1.0,         # レセプト・予約等
            "nursing": 1.5,        # ケアプラン・記録等
            "realestate": 1.5,     # 査定・契約書等
            "logistics": 1.0,      # 配車・伝票等
            "wholesale": 1.0,      # 受発注・在庫等
        }
        manual_per_task = MANUAL_HOURS_PER_TASK.get(industry, 1.5)
        estimated_manual_hours = bpo_executions * manual_per_task
        actual_saved_hours = max(0.0, estimated_manual_hours - total_bpo_hours)

        # 金銭換算（時給3000円）
        HOURLY_RATE = 3000
        actual_saved_yen = actual_saved_hours * HOURLY_RATE

        # ROI = 削減金額 / 月額費用
        roi_ratio = actual_saved_yen / mrr if mrr > 0 else 0.0

        # 信頼度（実行回数が多いほど高い）
        confidence = min(0.95, 0.5 + (bpo_executions / 100) * 0.45)

        result: dict[str, Any] = {
            "customer_id": customer_id,
            "industry": industry,
            "period_days": 30,
            "bpo_executions": bpo_executions,
            "total_bpo_duration_hours": round(total_bpo_hours, 2),
            "estimated_manual_hours": round(estimated_manual_hours, 2),
            "actual_saved_hours": round(actual_saved_hours, 2),
            "actual_saved_yen": round(actual_saved_yen),
            "mrr": mrr,
            "roi_ratio": round(roi_ratio, 2),
            "confidence": confidence,
        }

        # win_loss_patternsに実績データとして保存
        db.table("win_loss_patterns").insert({
            "company_id": company_id,
            "outcome": "roi_actual",
            "industry": industry,
            "pattern_data": result,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

        logger.info(
            f"ROI actual calculated: customer={customer_id} "
            f"roi={roi_ratio:.2f} saved_hours={actual_saved_hours:.1f}"
        )
        return result

    except Exception as e:
        logger.error(f"ROI actual calculation failed: {e}")
        return {"error": str(e), "confidence": 0.0}


async def get_industry_roi_benchmarks(company_id: str, industry: str) -> dict[str, Any]:
    """業種別のROI実績ベンチマークを取得する。

    win_loss_patternsテーブルから同業種のroi_actualデータを集計し、
    提案書生成時のROI試算に使える平均値・中央値を返す。
    """
    try:
        from db.supabase import get_service_client
        db = get_service_client()

        result = (
            db.table("win_loss_patterns")
            .select("pattern_data")
            .eq("company_id", company_id)
            .eq("outcome", "roi_actual")
            .eq("industry", industry)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )

        if not result.data:
            return {"has_benchmark": False, "sample_size": 0}

        saved_hours_list: list[float] = []
        saved_yen_list: list[float] = []
        roi_list: list[float] = []

        for row in result.data:
            pd = row.get("pattern_data", {})
            if pd.get("actual_saved_hours"):
                saved_hours_list.append(pd["actual_saved_hours"])
            if pd.get("actual_saved_yen"):
                saved_yen_list.append(pd["actual_saved_yen"])
            if pd.get("roi_ratio"):
                roi_list.append(pd["roi_ratio"])

        def _avg(lst: list[float]) -> float:
            return sum(lst) / len(lst) if lst else 0.0

        def _median(lst: list[float]) -> float:
            if not lst:
                return 0.0
            sorted_lst = sorted(lst)
            mid = len(sorted_lst) // 2
            return (
                sorted_lst[mid]
                if len(sorted_lst) % 2
                else (sorted_lst[mid - 1] + sorted_lst[mid]) / 2
            )

        return {
            "has_benchmark": True,
            "industry": industry,
            "sample_size": len(result.data),
            "avg_saved_hours_monthly": round(_avg(saved_hours_list), 1),
            "median_saved_hours_monthly": round(_median(saved_hours_list), 1),
            "avg_saved_yen_monthly": round(_avg(saved_yen_list)),
            "median_saved_yen_monthly": round(_median(saved_yen_list)),
            "avg_roi_ratio": round(_avg(roi_list), 2),
            "median_roi_ratio": round(_median(roi_list), 2),
        }

    except Exception as e:
        logger.warning(f"ROI benchmark fetch failed: {e}")
        return {"has_benchmark": False, "sample_size": 0, "error": str(e)}
