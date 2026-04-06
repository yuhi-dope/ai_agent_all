"""Playwrightでフォーム自動送信"""

from __future__ import annotations

import asyncio
import random
from enum import Enum

from playwright.async_api import async_playwright
from pydantic import BaseModel


class SendStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CAPTCHA = "captcha_detected"


class SendResult(BaseModel):
    status: SendStatus
    detail: str = ""


async def send_form(url: str, field_values: dict[str, str], proxy: str | None = None) -> SendResult:
    """Playwrightでフォームに値を入力して送信"""
    async with async_playwright() as p:
        launch_opts = {"headless": True}
        if proxy:
            launch_opts["proxy"] = {"server": proxy}

        browser = await p.chromium.launch(**launch_opts)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            # ランダム待機（人間らしい挙動）
            await asyncio.sleep(random.uniform(1, 3))

            for name, value in field_values.items():
                try:
                    el = page.locator(f"[name='{name}']").first
                    tag = await el.evaluate("el => el.tagName.toLowerCase()")

                    if tag == "select":
                        await el.select_option(label=value)
                    elif tag == "textarea":
                        await el.fill(value)
                    elif tag == "input":
                        input_type = await el.get_attribute("type") or "text"
                        if input_type == "radio":
                            await page.locator(f"input[name='{name}'][value='{value}']").click()
                        elif input_type == "checkbox":
                            await el.check()
                        else:
                            await el.fill(value)

                    await asyncio.sleep(random.uniform(0.3, 0.8))
                except Exception:
                    continue

            # 送信ボタンをクリック
            submit = page.locator("button[type='submit'], input[type='submit']").first
            await submit.click()
            await page.wait_for_load_state("networkidle", timeout=10000)

            return SendResult(status=SendStatus.SUCCESS, detail="フォーム送信完了")

        except Exception as e:
            return SendResult(status=SendStatus.FAILED, detail=str(e))
        finally:
            await browser.close()
