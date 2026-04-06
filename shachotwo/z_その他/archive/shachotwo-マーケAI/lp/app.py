"""LP配信 + CTA受信 + トラッキング"""

from __future__ import annotations

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from lp.tracker import record_page_view, classify_signal

router = APIRouter(tags=["lp"])
templates = Jinja2Templates(directory="lp/templates")


# ---------- Models ----------

class CTAFormData(BaseModel):
    company_name: str
    contact_name: str
    phone: str


class ScheduleSelection(BaseModel):
    slot_id: str


class DownloadFormData(BaseModel):
    email: str


class TrackEvent(BaseModel):
    duration_sec: int
    page_url: str


# ---------- LP Pages ----------

@router.get("/lp/{company_id}", response_class=HTMLResponse)
async def resume_page(request: Request, company_id: str):
    """職務経歴書LP表示"""
    # TODO: company_id から企業情報・履歴書データを取得
    context = {
        "request": request,
        "company_id": company_id,
        "company_name": "（企業名）",
        "industry": "建設業",
        "monthly_price": "30,000",
        "strong_tasks": ["安全書類の自動作成", "日報・作業報告書の集計", "出来高請求書の自動生成"],
        "learnable_tasks": ["積算補助（数量拾い出し）", "原価管理レポート"],
        "benefits": [
            {"icon": "📊", "text": "月40時間の事務作業削減（年間480時間）"},
            {"icon": "💰", "text": "人件費換算で年間約200万円の効果"},
            {"icon": "👷", "text": "現場監督が本来業務に集中できる"},
        ],
    }
    return templates.TemplateResponse("resume_page.html", context)


@router.post("/lp/{company_id}/cta")
async def cta_submit(company_id: str, company_name: str = Form(...), contact_name: str = Form(...), phone: str = Form(...)):
    """「話を聞きたい」フォーム受信"""
    # TODO: apo_leads に INSERT / signals/detector でHOT判定 / Gmail通知
    return templates.TemplateResponse("schedule_pick.html", {
        "request": {},
        "company_id": company_id,
        "company_name": company_name,
        "contact_name": contact_name,
        "slots": [],  # TODO: scheduling/slot_picker から取得
    })


@router.get("/lp/{company_id}/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, company_id: str):
    """日程選択画面"""
    # TODO: 空き枠を取得して表示
    context = {
        "request": request,
        "company_id": company_id,
        "company_name": "（企業名）",
        "contact_name": "（担当者名）",
        "slots": [],
    }
    return templates.TemplateResponse("schedule_pick.html", context)


@router.post("/lp/{company_id}/schedule")
async def schedule_submit(company_id: str, slot_id: str = Form(...)):
    """日程選択の受信 → Calendar + Meet作成"""
    # TODO: scheduling/calendar_api で予定作成 / 確認メール送信
    return {"status": "confirmed", "company_id": company_id, "slot_id": slot_id}


@router.get("/lp/{company_id}/confirmed", response_class=HTMLResponse)
async def confirmed_page(request: Request, company_id: str):
    """日程確定完了ページ"""
    context = {
        "request": request,
        "company_id": company_id,
        "meeting_date": "（日時）",
        "meeting_url": "（Google Meet URL）",
    }
    return templates.TemplateResponse("confirmed.html", context)


@router.post("/lp/{company_id}/download")
async def download_submit(company_id: str, email: str = Form(...)):
    """資料DLフォーム受信"""
    # TODO: ホワイトペーパーPDFをメール送信 / WARM シグナル記録
    return {"status": "sent", "email": email}


@router.get("/unsubscribe/{company_id}", response_class=HTMLResponse)
async def unsubscribe_page(request: Request, company_id: str):
    """配信停止"""
    # TODO: apo_unsubscribes に INSERT
    return templates.TemplateResponse("unsubscribe.html", {"request": request})


@router.post("/lp/{company_id}/track")
async def track_event(company_id: str, event: TrackEvent):
    """LP閲覧トラッキング（JSから呼ばれる）"""
    await record_page_view(company_id, event.page_url, event.duration_sec)
    signal = classify_signal(event.duration_sec)
    return {"signal": signal}
