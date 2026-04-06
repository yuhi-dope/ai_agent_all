"""見積書自動生成"""

from __future__ import annotations

from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from models import EstimateData, EstimateItem, PlanInfo

PLANS = {
    "starter": PlanInfo(name="starter", monthly_amount=30000, description="Starterプラン", features=["ユーザー5名", "ナレッジ1,000件", "LINE/メール/Web接続"]),
    "growth": PlanInfo(name="growth", monthly_amount=30000, description="Growthプラン（基本料+従量課金）", features=["ユーザー無制限", "ナレッジ無制限", "全コネクタ接続", "従量課金"]),
}

_counter = 0


def determine_plan(company_data: dict) -> PlanInfo:
    """企業情報から最適プランを判定"""
    emp = company_data.get("employee_count") or 0
    if emp <= 20:
        return PLANS["starter"]
    return PLANS["growth"]


def generate_estimate_number() -> str:
    """見積番号を生成: EST-YYYYMMDD-NNN"""
    global _counter
    _counter += 1
    return f"EST-{date.today().strftime('%Y%m%d')}-{_counter:03d}"


def generate_estimate_pdf(data: EstimateData) -> bytes:
    """見積書HTMLテンプレートからPDF生成"""
    env = Environment(loader=FileSystemLoader("contract/templates"))
    template = env.get_template("estimate_template.html")
    html_str = template.render(est=data)
    return HTML(string=html_str).write_pdf()


async def generate_and_send_estimate(deal_id: str, company_data: dict, contact_data: dict) -> str:
    """見積書を生成してメール送付"""
    plan = determine_plan(company_data)
    est_number = generate_estimate_number()
    today = date.today()

    items = [
        EstimateItem(name=f"{plan.description} 月額基本料", quantity=1, unit_price=plan.monthly_amount, amount=plan.monthly_amount),
        EstimateItem(name="初月無料", quantity=1, unit_price=0, amount=-plan.monthly_amount),
    ]
    subtotal = sum(i.amount for i in items)
    tax = int(subtotal * 0.1)

    est_data = EstimateData(
        estimate_number=est_number,
        issue_date=today,
        valid_until=today + timedelta(days=30),
        company_name=company_data.get("name", ""),
        contact_name=contact_data.get("name", ""),
        plan=plan,
        items=items,
        subtotal=subtotal,
        tax=tax,
        total=subtotal + tax,
    )

    pdf = generate_estimate_pdf(est_data)

    # TODO: Gmail APIで見積書PDF添付メール送信
    # TODO: apo_contracts に INSERT
    # TODO: apo_contract_events にイベント記録

    return deal_id  # contract_id を返すべきだが仮
