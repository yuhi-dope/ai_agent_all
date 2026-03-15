"""建設業 安全書類自動生成エンジン"""
import logging
from datetime import date, timedelta

from db.supabase import get_client
from llm.client import LLMClient
from llm.prompts.construction import SYSTEM_SAFETY_PLAN
from workers.bpo.engine.document_gen import ExcelGenerator
from workers.bpo.construction.models import (
    SafetyDocumentResponse,
    ExpiringQualification,
)

logger = logging.getLogger(__name__)


class SafetyDocumentGenerator:
    """安全書類自動生成エンジン"""

    def __init__(self) -> None:
        self.llm = LLMClient()

    async def generate_worker_roster(
        self,
        site_id: str,
        company_id: str,
        as_of_date: date | None = None,
    ) -> bytes:
        """
        第5号: 作業員名簿を自動生成

        1. site_worker_assignments から対象作業員を取得
        2. construction_workers マスタから個人情報を取得
        3. worker_qualifications から資格情報を取得
        4. 全建統一様式 第5号フォーマットでExcel生成
        """
        if as_of_date is None:
            as_of_date = date.today()

        client = await get_client()

        # 現場情報
        site = await client.table("construction_sites").select("*").eq(
            "id", site_id
        ).single().execute()

        # アサインされた作業員
        assignments = await client.table("site_worker_assignments").select(
            "*, construction_workers(*)"
        ).eq("site_id", site_id).is_("exit_date", "null").execute()

        headers = [
            "No.", "氏名", "フリガナ", "生年月日", "血液型",
            "住所", "電話番号", "雇入年月日", "経験年数",
            "最新健診日", "資格・免許", "入場年月日", "職種",
        ]

        rows = []
        for idx, asgn in enumerate(assignments.data or [], 1):
            worker = asgn.get("construction_workers", {})

            # 資格取得
            quals = await client.table("worker_qualifications").select(
                "qualification_name"
            ).eq("worker_id", worker["id"]).execute()
            qual_names = ", ".join(q["qualification_name"] for q in (quals.data or []))

            rows.append([
                idx,
                f"{worker.get('last_name', '')} {worker.get('first_name', '')}",
                f"{worker.get('last_name_kana', '')} {worker.get('first_name_kana', '')}",
                worker.get("birth_date", ""),
                worker.get("blood_type", ""),
                worker.get("address", ""),
                worker.get("phone", ""),
                worker.get("hire_date", ""),
                worker.get("experience_years", ""),
                worker.get("health_check_date", ""),
                qual_names,
                asgn.get("entry_date", ""),
                asgn.get("role", ""),
            ])

        # 安全書類レコード保存
        doc_data = {
            "site_name": site.data["name"],
            "as_of_date": as_of_date.isoformat(),
            "worker_count": len(rows),
        }
        await client.table("safety_documents").insert({
            "site_id": site_id,
            "company_id": company_id,
            "document_type": "worker_roster",
            "document_number": "5",
            "generated_data": doc_data,
            "status": "draft",
        }).execute()

        return ExcelGenerator.generate_table(
            title=f"作業員名簿 — {site.data['name']}",
            headers=headers,
            rows=rows,
            column_widths=[5, 15, 15, 12, 6, 25, 14, 12, 8, 12, 25, 12, 10],
        )

    async def generate_qualification_list(
        self,
        site_id: str,
        company_id: str,
    ) -> bytes:
        """第9号: 有資格者一覧表"""
        client = await get_client()

        site = await client.table("construction_sites").select("name").eq(
            "id", site_id
        ).single().execute()

        assignments = await client.table("site_worker_assignments").select(
            "worker_id, construction_workers(id, last_name, first_name)"
        ).eq("site_id", site_id).is_("exit_date", "null").execute()

        headers = ["No.", "氏名", "資格・免許名", "証書番号", "取得日", "有効期限", "発行機関"]
        rows = []
        idx = 1
        for asgn in (assignments.data or []):
            worker = asgn.get("construction_workers", {})
            quals = await client.table("worker_qualifications").select("*").eq(
                "worker_id", worker["id"]
            ).execute()
            for q in (quals.data or []):
                rows.append([
                    idx,
                    f"{worker.get('last_name', '')} {worker.get('first_name', '')}",
                    q["qualification_name"],
                    q.get("certificate_number", ""),
                    q.get("issued_date", ""),
                    q.get("expiry_date", ""),
                    q.get("issuer", ""),
                ])
                idx += 1

        return ExcelGenerator.generate_table(
            title=f"有資格者一覧表 — {site.data['name']}",
            headers=headers,
            rows=rows,
        )

    async def check_expiring_qualifications(
        self,
        company_id: str,
        days_ahead: int = 90,
    ) -> list[ExpiringQualification]:
        """資格有効期限アラート"""
        client = await get_client()
        cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()

        quals = await client.table("worker_qualifications").select(
            "*, construction_workers(last_name, first_name)"
        ).eq("company_id", company_id).lte(
            "expiry_date", cutoff
        ).not_.is_("expiry_date", "null").execute()

        results = []
        for q in (quals.data or []):
            worker = q.get("construction_workers", {})
            exp = date.fromisoformat(q["expiry_date"])
            results.append(ExpiringQualification(
                worker_id=q["worker_id"],
                worker_name=f"{worker.get('last_name', '')} {worker.get('first_name', '')}",
                qualification_name=q["qualification_name"],
                expiry_date=exp,
                days_until_expiry=(exp - date.today()).days,
            ))

        return sorted(results, key=lambda x: x.days_until_expiry)

    async def generate_safety_plan(
        self,
        site_id: str,
        company_id: str,
        work_details: str,
    ) -> bytes:
        """第6号: 工事安全衛生計画書（LLM生成）"""
        client = await get_client()

        site = await client.table("construction_sites").select("*").eq(
            "id", site_id
        ).single().execute()

        prompt = f"""工事情報:
工事名: {site.data['name']}
住所: {site.data.get('address', '不明')}
工事内容: {work_details}
"""
        response = await self.llm.generate(
            system_prompt=SYSTEM_SAFETY_PLAN,
            user_prompt=prompt,
            model_tier="standard",
        )

        # 安全書類レコード保存
        await client.table("safety_documents").insert({
            "site_id": site_id,
            "company_id": company_id,
            "document_type": "safety_plan",
            "document_number": "6",
            "generated_data": {"content": response.content},
            "status": "draft",
        }).execute()

        return ExcelGenerator.generate_table(
            title=f"工事安全衛生計画書 — {site.data['name']}",
            headers=["項目", "内容"],
            rows=[["安全衛生計画", response.content]],
        )
