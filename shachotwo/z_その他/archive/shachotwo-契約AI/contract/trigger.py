"""契約意思の検知 & 契約フロー起動"""

from __future__ import annotations


async def on_cta_click(deal_id: str) -> None:
    """LP上の「契約に進む」ボタン"""
    await start_contract_flow(deal_id)


async def on_email_link_click(deal_id: str) -> None:
    """確認メール内の「契約する」リンク"""
    await start_contract_flow(deal_id)


async def on_manual_trigger(deal_id: str) -> None:
    """スプレッドシートで手動チェック"""
    await start_contract_flow(deal_id)


async def start_contract_flow(deal_id: str) -> None:
    """契約フローを起動: 見積書生成 → 契約書送信"""
    from contract.estimate import generate_and_send_estimate

    # TODO: deal_id から企業情報・商談情報を取得
    company_data = {}  # apo_deals + apo_companies から取得
    contact_data = {}  # apo_leads から取得

    # Step 1: 見積書生成 & 送付
    contract_id = await generate_and_send_estimate(deal_id, company_data, contact_data)

    # Step 2: 契約書生成 & CloudSign送信
    from contract.contract_generator import generate_contract_pdf
    from contract.cloudsign import create_document, send_for_signature

    contract_pdf = generate_contract_pdf(company_data)
    doc_id = await create_document(
        title=f"シャチョツー サービス利用契約書 — {company_data.get('name', '')}",
        pdf_content=contract_pdf,
    )
    await send_for_signature(
        document_id=doc_id,
        recipient_email=contact_data.get("email", ""),
        recipient_name=contact_data.get("name", ""),
        organization=company_data.get("name", ""),
    )

    # TODO: apo_contracts の status を 'contract_sent' に更新
    # TODO: apo_contract_events にイベント記録
