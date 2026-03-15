"""建設業 出来高・請求書エンジン"""
from decimal import Decimal

from db.supabase import get_client
from workers.bpo.engine.document_gen import ExcelGenerator


class BillingEngine:
    """出来高管理 + 請求書自動生成"""

    async def calculate_progress(
        self,
        contract_id: str,
        company_id: str,
        period_year: int,
        period_month: int,
        items: list[dict],
    ) -> dict:
        """
        出来高を計算

        items: [{item_name, contract_amount, progress_rate}]
        """
        client = await get_client()

        # 前月の累計を取得
        prev = await client.table("progress_records").select(
            "cumulative_amount"
        ).eq("contract_id", contract_id).order(
            "period_year", desc=True
        ).order("period_month", desc=True).limit(1).execute()

        previous_cumulative = prev.data[0]["cumulative_amount"] if prev.data else 0

        # 当月出来高計算
        progress_items = []
        cumulative = 0
        for item in items:
            amount = int(Decimal(str(item["contract_amount"])) * Decimal(str(item["progress_rate"])))
            cumulative += amount
            progress_items.append({
                "item_name": item["item_name"],
                "contract_amount": item["contract_amount"],
                "progress_rate": float(item["progress_rate"]),
                "progress_amount": amount,
            })

        current_amount = cumulative - previous_cumulative

        result = await client.table("progress_records").insert({
            "contract_id": contract_id,
            "company_id": company_id,
            "period_year": period_year,
            "period_month": period_month,
            "items": progress_items,
            "cumulative_amount": cumulative,
            "previous_cumulative": previous_cumulative,
            "current_amount": current_amount,
            "status": "draft",
        }).execute()

        return result.data[0] if result.data else {}

    async def generate_invoice(
        self,
        progress_record_id: str,
        company_id: str,
    ) -> bytes:
        """出来高から請求書Excelを生成"""
        client = await get_client()

        record = await client.table("progress_records").select(
            "*, construction_contracts(*)"
        ).eq("id", progress_record_id).single().execute()

        data = record.data
        contract = data.get("construction_contracts", {})

        tax_rate = Decimal(str(contract.get("tax_rate", "0.10")))
        subtotal = data["current_amount"]
        tax_amount = int(subtotal * tax_rate)
        total = subtotal + tax_amount

        # bpo_invoices に保存
        invoice_number = f"INV-{data['period_year']}{data['period_month']:02d}-{contract.get('contract_number', '001')}"
        await client.table("bpo_invoices").insert({
            "company_id": company_id,
            "invoice_number": invoice_number,
            "invoice_date": f"{data['period_year']}-{data['period_month']:02d}-25",
            "due_date": f"{data['period_year']}-{data['period_month'] + 1 if data['period_month'] < 12 else 1:02d}-末",
            "client_name": contract.get("client_name", ""),
            "subtotal": subtotal,
            "tax_rate": float(tax_rate),
            "tax_amount": tax_amount,
            "total": total,
            "items": data["items"],
            "status": "draft",
            "source_type": "progress_billing",
            "source_id": progress_record_id,
        }).execute()

        return ExcelGenerator.generate_from_template({
            "title": f"請求書 — {contract.get('project_name', '')}",
            "meta": {
                "請求書番号": invoice_number,
                "工事名": contract.get("project_name", ""),
                "発注者": contract.get("client_name", ""),
                "契約金額": f"¥{contract.get('contract_amount', 0):,}",
            },
            "headers": ["項目", "契約金額", "進捗率", "出来高金額"],
            "rows": [
                [item["item_name"], item["contract_amount"], f"{item['progress_rate']:.0%}", item["progress_amount"]]
                for item in data["items"]
            ],
            "totals": {
                "当月請求額（税抜）": f"¥{subtotal:,}",
                "消費税": f"¥{tax_amount:,}",
                "合計": f"¥{total:,}",
            },
        })


class CostReportEngine:
    """原価管理レポート"""

    async def generate_monthly_report(
        self,
        contract_id: str,
        company_id: str,
        year: int,
        month: int,
    ) -> dict:
        """月次原価レポート"""
        client = await get_client()

        contract = await client.table("construction_contracts").select("*").eq(
            "id", contract_id
        ).single().execute()

        costs = await client.table("cost_records").select("*").eq(
            "contract_id", contract_id
        ).execute()

        # 原価集計
        cost_by_type: dict[str, int] = {}
        total_cost = 0
        for c in (costs.data or []):
            t = c["cost_type"]
            cost_by_type[t] = cost_by_type.get(t, 0) + c["amount"]
            total_cost += c["amount"]

        contract_amount = contract.data.get("contract_amount", 0)
        profit = contract_amount - total_cost
        profit_rate = (Decimal(profit) / Decimal(contract_amount) * 100) if contract_amount else Decimal(0)

        return {
            "contract_id": contract_id,
            "project_name": contract.data.get("project_name", ""),
            "contract_amount": contract_amount,
            "total_cost": total_cost,
            "profit": profit,
            "profit_rate": float(profit_rate),
            "cost_by_type": cost_by_type,
        }
