from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

URL = "https://www.binance.com/ru/copy-trading/lead-details/4751838302089254401"
LEAD_ID = "4751838302089254401"

SLOTS_RE = re.compile(
    r"\bt-subtitle2\b[^>]*>(\d+)\s*/\s*(\d+)\s*</div>",
    re.IGNORECASE,
)
COPY_COUNT_CC = re.compile(r'"currentCopyCount"\s*:\s*(\d+)')
COPY_COUNT_MC = re.compile(r'"maxCopyCount"\s*:\s*(\d+)')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,uk;q=0.9,en;q=0.8",
}


class FetchTimeoutError(Exception):
    """Тимчасовий timeout під час завантаження Binance сторінки."""


def _slots_from_json(html: str) -> tuple[int, int] | None:
    i = html.find(LEAD_ID)
    if i >= 0:
        chunk = html[i : i + 400_000]
        cc = COPY_COUNT_CC.search(chunk)
        mc = COPY_COUNT_MC.search(chunk)
        if cc and mc:
            return int(cc.group(1)), int(mc.group(1))
    cc = COPY_COUNT_CC.search(html)
    mc = COPY_COUNT_MC.search(html)
    if cc and mc:
        return int(cc.group(1)), int(mc.group(1))
    return None


def _slots_from_subtitle(html: str) -> tuple[int, int] | None:
    m = SLOTS_RE.search(html)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_slots(html: str) -> tuple[int, int] | None:
    pair = _slots_from_json(html)
    if pair is not None:
        return pair
    return _slots_from_subtitle(html)


def fetch_html_playwright() -> str:
    from playwright.sync_api import sync_playwright
    from playwright.sync_api import TimeoutError as PWTimeout

    launch_args: list[str] = ["--disable-dev-shm-usage"]
    if os.getenv("DOCKER", "").lower() in ("1", "true", "yes"):
        launch_args.append("--no-sandbox")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=launch_args)
        try:
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="ru-RU",
            )
            page = context.new_page()
            page.goto(URL, wait_until="commit", timeout=90_000)
            try:
                page.wait_for_function(
                    r"""() => {
                        const h = document.documentElement.innerHTML;
                        return (h.includes('currentCopyCount') && h.includes('maxCopyCount'))
                            || /\bt-subtitle2\b[^>]*>\d+\s*\/\s*\d+\s*<\/div>/i.test(h);
                    }""",
                    timeout=90_000,
                )
            except PWTimeout:
                pass
            return page.content()
        except PWTimeout as e:
            raise FetchTimeoutError("Playwright timeout while loading page") from e
        finally:
            browser.close()


def send_telegram_message(message: str) -> None:
    """Надсилає повідомлення в Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram не налаштовано: відсутній токен бота або ID чату")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=data, timeout=10)
        if response.ok:
            print("Повідомлення надіслано в Telegram.", flush=True)
        else:
            print(f"Помилка Telegram API: {response.text}", flush=True)
    except Exception as e:
        print(f"Помилка надсилання в Telegram: {e}", flush=True)


def main() -> None:
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    # None — ще не було успішного парсу; True/False — чи було останнє значення «заповнено»
    prev_full: bool | None = None

    print(f"Опитування кожні {interval} с [Playwright]: {URL}", flush=True)
    print("Підказка: playwright install chromium (один раз)", flush=True)

    while True:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        try:
            html = fetch_html_playwright()
            pair = parse_slots(html)
            if pair is None:
                print(
                    f"[{ts}] Не знайдено currentCopyCount/maxCopyCount у JSON "
                    f"ані t-subtitle2 з N/M у HTML.",
                    flush=True,
                )
            else:
                current, maximum = pair
                full = current >= maximum
                status = "ЗАПОВНЕНО" if full else "Є ВІЛЬНІ МІСЦЯ"
                print(f"[{ts}] {current}/{maximum} — {status}", flush=True)
                if not full and (prev_full is None or prev_full):
                    send_telegram_message(
                        f"Binance Copy Trading: є вільні місця <b>{current}/{maximum}</b>\n"
                        f'<a href="{URL}">Відкрити ліда</a>'
                    )
                prev_full = full
        except FetchTimeoutError:
            print(f"[{ts}] Timeout Binance сторінки, повтор через {interval} с.", flush=True)
        except Exception as e:
            print(f"[{ts}] Помилка: {e}", flush=True)

        time.sleep(interval)


if __name__ == "__main__":
    main()
