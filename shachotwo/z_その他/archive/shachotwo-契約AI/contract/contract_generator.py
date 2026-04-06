"""契約書自動生成（テンプレート差し込み → PDF）"""

from __future__ import annotations

from datetime import date

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from models import ContractData


def generate_contract_pdf(company_data: dict) -> bytes:
    """企業情報を契約書テンプレートに差し込みPDF化"""
    data = ContractData(
        company_name=company_data.get("name", ""),
        representative=company_data.get("representative", ""),
        address=company_data.get("address", ""),
        plan_name=company_data.get("plan", "starter"),
        monthly_amount=company_data.get("monthly_amount", 30000),
        start_date=date.today(),
    )

    env = Environment(loader=FileSystemLoader("contract/templates"))
    template = env.get_template("contract_template.html")
    html_str = template.render(c=data)
    return HTML(string=html_str).write_pdf()
