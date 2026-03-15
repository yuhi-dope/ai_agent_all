"""建設業 積算AIパイプライン"""
import json
import logging
from decimal import Decimal

from db.supabase import get_service_client as get_client
from llm.client import LLMClient
from llm.prompts.construction import (
    SYSTEM_QUANTITY_EXTRACTION,
    SYSTEM_UNIT_PRICE_ESTIMATION,
)
from workers.bpo.construction.models import (
    EstimationItemCreate,
    EstimationItemWithPrice,
    OverheadBreakdown,
    IngestionResult,
    PriceSource,
    ProjectType,
)

logger = logging.getLogger(__name__)


class EstimationPipeline:
    """
    積算AIパイプライン

    Step 1: 図面・数量計算書の取り込み
    Step 2: 数量の構造化抽出（LLM）
    Step 3: 単価の推定・候補表示
    Step 4: 諸経費計算
    Step 5: 内訳書生成
    """

    def __init__(self) -> None:
        self.llm = LLMClient()

    async def extract_quantities(
        self,
        project_id: str,
        company_id: str,
        raw_text: str,
    ) -> list[EstimationItemCreate]:
        """
        テキストから数量を構造化抽出

        対応:
        - 数量計算書のテキスト（Excel→テキスト変換済み）
        - 設計書PDFのテキスト
        - 手入力テキスト
        """
        response = await self.llm.generate(
            system_prompt=SYSTEM_QUANTITY_EXTRACTION,
            user_prompt=f"以下の設計書・数量計算書から数量を抽出してください:\n\n{raw_text}",
            model_tier="standard",
        )

        try:
            items_data = json.loads(response.content)
        except json.JSONDecodeError:
            # LLMの出力がJSON以外の場合、再パース試行
            import re
            json_match = re.search(r'\[.*\]', response.content, re.DOTALL)
            if json_match:
                items_data = json.loads(json_match.group())
            else:
                logger.error(f"Failed to parse LLM output as JSON: {response.content[:200]}")
                return []

        items = []
        for item in items_data:
            items.append(EstimationItemCreate(
                sort_order=item.get("sort_order", len(items) + 1),
                category=item["category"],
                subcategory=item.get("subcategory"),
                detail=item.get("detail"),
                specification=item.get("specification"),
                quantity=Decimal(str(item["quantity"])),
                unit=item["unit"],
                source_document=item.get("source_document"),
                notes=item.get("notes"),
            ))

        # DBに保存
        client = get_client()
        for item in items:
            client.table("estimation_items").insert({
                "project_id": project_id,
                "company_id": company_id,
                **item.model_dump(mode="json"),
            }).execute()

        return items

    async def suggest_unit_prices(
        self,
        project_id: str,
        company_id: str,
        region: str,
        fiscal_year: int,
    ) -> list[EstimationItemWithPrice]:
        """
        各項目に単価候補を付与

        優先順位:
        1. 自社過去実績（同一工種・同一地域・直近2年以内）
        2. 公共工事設計労務単価（労務費の場合）
        3. 自社過去実績（類似工種）
        4. LLM推定
        """
        client = get_client()

        # 積算明細を取得
        items_result = client.table("estimation_items").select("*").eq(
            "project_id", project_id
        ).order("sort_order").execute()

        if not items_result.data:
            return []

        results = []
        for item_data in items_result.data:
            candidates = []

            # 1. 自社過去実績
            past_prices = client.table("unit_price_master").select("*").eq(
                "company_id", company_id
            ).eq("category", item_data["category"]).eq(
                "region", region
            ).order("updated_at", desc=True).limit(3).execute()

            for pp in (past_prices.data or []):
                candidates.append({
                    "source": PriceSource.PAST_RECORD.value,
                    "unit_price": pp["unit_price"],
                    "confidence": 0.9,
                    "detail": f"自社実績 ({pp.get('source_detail', '')})",
                })

            # 2. 公共工事設計労務単価
            labor_rates = client.table("public_labor_rates").select("*").eq(
                "fiscal_year", fiscal_year
            ).eq("region", region).execute()

            for lr in (labor_rates.data or []):
                if lr["occupation"].lower() in item_data.get("detail", "").lower():
                    candidates.append({
                        "source": PriceSource.LABOR_RATE.value,
                        "unit_price": lr["daily_rate"],
                        "confidence": 0.95,
                        "detail": f"公共工事設計労務単価 {lr['occupation']} {lr['fiscal_year']}年度",
                    })

            # 候補がない場合のみLLM推定
            if not candidates:
                candidates.append({
                    "source": PriceSource.AI_ESTIMATED.value,
                    "unit_price": None,
                    "confidence": 0.3,
                    "detail": "AI推定（要確認）",
                })

            item_with_price = EstimationItemWithPrice(
                **item_data,
                price_candidates=candidates,
            )
            results.append(item_with_price)

        return results

    async def calculate_overhead(
        self,
        project_id: str,
        company_id: str,
        project_type: ProjectType,
    ) -> OverheadBreakdown:
        """
        諸経費を計算

        公共土木:
          共通仮設費率 / 現場管理費率 / 一般管理費等率
          → 工事規模（直接工事費）によって率が変わる
        民間:
          会社設定の諸経費率（デフォルト27%）
        """
        client = get_client()

        # 直接工事費を集計
        items = client.table("estimation_items").select(
            "quantity, unit_price"
        ).eq("project_id", project_id).execute()

        direct_cost = 0
        for item in (items.data or []):
            if item["unit_price"]:
                qty = Decimal(str(item["quantity"]))
                price = Decimal(str(item["unit_price"]))
                direct_cost += int(qty * price)

        # 諸経費率の決定
        if project_type in (ProjectType.PUBLIC_CIVIL, ProjectType.PUBLIC_BUILDING):
            # 公共工事の標準諸経費率（簡易版）
            common_temp_rate = Decimal("0.05")   # 共通仮設費 5%
            site_mgmt_rate = Decimal("0.20")     # 現場管理費 20%
            general_rate = Decimal("0.12")       # 一般管理費等 12%
        else:
            # 民間工事
            common_temp_rate = Decimal("0.03")
            site_mgmt_rate = Decimal("0.12")
            general_rate = Decimal("0.12")

        common_temporary = int(direct_cost * common_temp_rate)
        site_management = int((direct_cost + common_temporary) * site_mgmt_rate)
        general_admin = int((direct_cost + common_temporary + site_management) * general_rate)
        total = direct_cost + common_temporary + site_management + general_admin

        breakdown = OverheadBreakdown(
            direct_cost=direct_cost,
            common_temporary=common_temporary,
            common_temporary_rate=common_temp_rate,
            site_management=site_management,
            site_management_rate=site_mgmt_rate,
            general_admin=general_admin,
            general_admin_rate=general_rate,
            total=total,
        )

        # プロジェクトの積算金額を更新
        client.table("estimation_projects").update({
            "estimated_amount": total,
            "overhead_rates": {
                "common_temporary": float(common_temp_rate),
                "site_management": float(site_mgmt_rate),
                "general_admin": float(general_rate),
            },
        }).eq("id", project_id).execute()

        return breakdown

    async def generate_breakdown_data(
        self,
        project_id: str,
        company_id: str,
    ) -> dict:
        """内訳書データを生成（Excel生成用）"""
        client = get_client()

        project = client.table("estimation_projects").select("*").eq(
            "id", project_id
        ).single().execute()

        items = client.table("estimation_items").select("*").eq(
            "project_id", project_id
        ).order("sort_order").execute()

        proj = project.data
        rows = []
        for item in (items.data or []):
            amount = None
            if item["unit_price"] and item["quantity"]:
                amount = int(Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"])))
            rows.append([
                item["category"],
                item.get("subcategory", ""),
                item.get("detail", ""),
                item.get("specification", ""),
                float(item["quantity"]),
                item["unit"],
                float(item["unit_price"]) if item["unit_price"] else "",
                amount or "",
            ])

        return {
            "title": f"工事費内訳書 — {proj['name']}",
            "meta": {
                "工事名": proj["name"],
                "発注者": proj.get("client_name", ""),
                "地域": proj["region"],
                "年度": proj["fiscal_year"],
            },
            "headers": ["工種", "種別", "細別", "規格", "数量", "単位", "単価", "金額"],
            "rows": rows,
            "totals": {
                "直接工事費": proj.get("estimated_amount", 0),
            },
        }

    async def learn_from_result(
        self,
        project_id: str,
        company_id: str,
    ) -> int:
        """
        ユーザーが確定した単価をunit_price_masterに反映

        Returns: 保存された単価レコード数
        """
        client = get_client()

        project = client.table("estimation_projects").select(
            "region, fiscal_year"
        ).eq("id", project_id).single().execute()

        items = client.table("estimation_items").select("*").eq(
            "project_id", project_id
        ).not_.is_("unit_price", "null").execute()

        count = 0
        for item in (items.data or []):
            client.table("unit_price_master").insert({
                "company_id": company_id,
                "category": item["category"],
                "subcategory": item.get("subcategory"),
                "detail": item.get("detail"),
                "specification": item.get("specification"),
                "unit": item["unit"],
                "unit_price": item["unit_price"],
                "price_type": "composite",
                "region": project.data["region"],
                "year": project.data["fiscal_year"],
                "source": "past_estimation",
                "source_detail": f"Project: {project_id}",
            }).execute()
            count += 1

        return count
