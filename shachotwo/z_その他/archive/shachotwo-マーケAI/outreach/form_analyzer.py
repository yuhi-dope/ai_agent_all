"""フォームHTML解析 — フィールド抽出 & CAPTCHA検出"""

from __future__ import annotations

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel


class FormField(BaseModel):
    label: str = ""
    name: str = ""
    field_type: str = "text"  # text / email / tel / select / textarea / radio / checkbox
    required: bool = False
    options: list[str] = []  # select/radioの選択肢
    placeholder: str = ""


class FormAnalysis(BaseModel):
    form_url: str
    action_url: str = ""
    method: str = "POST"
    fields: list[FormField] = []
    has_captcha: bool = False
    captcha_type: str = ""  # recaptcha / hcaptcha / image


async def analyze_form(url: str) -> FormAnalysis | None:
    """企業HPから問い合わせフォームを検出し、全フィールドを抽出"""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    form = _find_contact_form(soup)
    if not form:
        return None

    fields = _extract_fields(form)
    has_captcha, captcha_type = _detect_captcha(soup)
    action = form.get("action", url)
    if action and not action.startswith("http"):
        base = url.rsplit("/", 1)[0]
        action = base + "/" + action.lstrip("/")

    return FormAnalysis(
        form_url=url,
        action_url=action,
        method=form.get("method", "POST").upper(),
        fields=fields,
        has_captcha=has_captcha,
        captcha_type=captcha_type,
    )


def _find_contact_form(soup: BeautifulSoup):
    """問い合わせフォームを検出"""
    for form in soup.find_all("form"):
        text = form.get_text().lower()
        if any(kw in text for kw in ["問い合わせ", "お問い合わせ", "contact", "inquiry", "送信", "submit"]):
            return form
    forms = soup.find_all("form")
    return forms[0] if forms else None


def _extract_fields(form) -> list[FormField]:
    """フォームからフィールドを抽出"""
    fields = []
    for inp in form.find_all(["input", "textarea", "select"]):
        tag = inp.name
        input_type = inp.get("type", "text") if tag == "input" else tag
        if input_type in ("hidden", "submit", "button", "image"):
            continue

        name = inp.get("name", "")
        label_text = _find_label(inp, form)
        required = inp.has_attr("required") or "必須" in label_text

        options = []
        if tag == "select":
            options = [opt.get_text(strip=True) for opt in inp.find_all("option") if opt.get("value")]

        fields.append(FormField(
            label=label_text,
            name=name,
            field_type=input_type,
            required=required,
            options=options,
            placeholder=inp.get("placeholder", ""),
        ))

    return fields


def _find_label(element, form) -> str:
    """input要素に対応するlabelテキストを検索"""
    elem_id = element.get("id", "")
    if elem_id:
        label = form.find("label", {"for": elem_id})
        if label:
            return label.get_text(strip=True)
    parent = element.parent
    if parent and parent.name in ("label", "div", "td", "th", "dt"):
        return parent.get_text(strip=True).split("\n")[0].strip()
    prev = element.find_previous(["label", "th", "dt", "span"])
    if prev:
        return prev.get_text(strip=True)
    return element.get("name", "")


def _detect_captcha(soup: BeautifulSoup) -> tuple[bool, str]:
    """CAPTCHA検出"""
    html = str(soup)
    if "recaptcha" in html.lower() or "g-recaptcha" in html:
        return True, "recaptcha"
    if "hcaptcha" in html.lower() or "h-captcha" in html:
        return True, "hcaptcha"
    captcha_imgs = soup.find_all("img", {"alt": lambda x: x and "captcha" in x.lower()})
    if captcha_imgs:
        return True, "image"
    return False, ""
