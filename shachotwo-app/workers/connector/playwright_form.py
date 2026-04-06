"""PlaywrightFormConnector — Playwright でフォーム自動送信するコネクタ。"""
import asyncio
import logging
import random
from enum import Enum
from typing import Any

from pydantic import BaseModel

from workers.connector.base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)


class SendStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CAPTCHA = "captcha_detected"


class SendResult(BaseModel):
    status: SendStatus
    detail: str = ""


class PlaywrightFormConnector(BaseConnector):
    """Playwright フォーム自動送信コネクタ。

    credentials:
        proxy (str, optional): プロキシサーバーURL

    resource:
        フォームのURL

    data:
        write_record に渡す dict — キーはフォームの name 属性、値は入力値
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)

    async def read_records(self, resource: str, filters: dict = {}) -> list[dict]:
        """フォームコネクタは読み取りをサポートしない。"""
        raise NotImplementedError("PlaywrightFormConnector は読み取りをサポートしません")

    async def write_record(self, resource: str, data: dict) -> dict:
        """Playwright でフォームに値を入力して送信する。

        Args:
            resource: フォームのURL
            data: フォームフィールドの name → value マッピング

        Returns:
            {"status": "success|failed|captcha_detected", "detail": "..."}
        """
        result = await self._send_form(resource, data)
        return result.model_dump()

    async def health_check(self) -> bool:
        """Playwright が起動できるか確認。"""
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                await browser.close()
                return True
        except Exception:
            return False

    async def _send_form(self, url: str, field_values: dict[str, str]) -> SendResult:
        """Playwright でフォームに値を入力して送信"""
        from playwright.async_api import async_playwright

        proxy = self.config.credentials.get("proxy")

        async with async_playwright() as p:
            launch_opts: dict[str, Any] = {"headless": True}
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
                logger.error(f"Form send failed: {url} — {e}")
                return SendResult(status=SendStatus.FAILED, detail=str(e))
            finally:
                await browser.close()
