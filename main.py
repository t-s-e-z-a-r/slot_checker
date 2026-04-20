from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# (lead_id, url) — id для пошуку в JSON; url відкриває Playwright.
LEADS: list[tuple[str, str]] = [
    (
        "4751838302089254401",
        "https://www.binance.com/ru/copy-trading/lead-details/4751838302089254401",
    ),
    (
        "4944132044517674496",
        "https://www.binance.com/ru/copy-trading/lead-details/4944132044517674496"
    ),   
    (
        "4959039539940441856", 
        "https://www.binance.com/ru/copy-trading/lead-details/4959039539940441856"
    ),    
    # (
    #     "4532994172262753536",
    #     "https://www.binance.com/ru/copy-trading/lead-details/4532994172262753536"
    # ),
]

ITERATION_TIMEOUT_SEC = float(os.getenv("ITERATION_TIMEOUT_SEC", "60"))

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


def _slots_from_json(html: str, lead_id: str) -> tuple[int, int] | None:
    i = html.find(lead_id)
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


def parse_slots(html: str, lead_id: str) -> tuple[int, int] | None:
    pair = _slots_from_json(html, lead_id)
    if pair is not None:
        return pair
    return _slots_from_subtitle(html)


def fetch_html_playwright(page_url: str) -> str:
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
            page.goto(page_url, wait_until="commit", timeout=90_000)
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


def _terminate_fetch_subprocess(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        proc.kill()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            proc.kill()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def _fetch_html_with_iteration_timeout(seconds: float, page_url: str) -> str:
    """Fetch у дочірньому процесі; argv: child output_path page_url."""
    child = Path(__file__).resolve().parent / "slot_fetch_child.py"
    if not child.is_file():
        raise FileNotFoundError(f"Не знайдено {child}")

    fd, raw = tempfile.mkstemp(prefix="slot_", suffix=".html")
    os.close(fd)
    path = Path(raw)
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(child), str(path), page_url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=sys.platform != "win32",
        )
        try:
            stdout, stderr = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            _terminate_fetch_subprocess(proc)
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            raise subprocess.TimeoutExpired(proc.args, seconds) from None
        if proc.returncode != 0:
            err = (stderr or stdout or "").strip() or f"код виходу {proc.returncode}"
            raise RuntimeError(err)
        return path.read_text(encoding="utf-8", errors="replace")
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_fetch_subprocess(proc)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        path.unlink(missing_ok=True)


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
    prev_full: dict[str, bool | None] = {lid: None for lid, _ in LEADS}

    print(
        f"Опитування {len(LEADS)} лідів кожні {interval} с [Playwright], "
        f"таймаут на один запит: {ITERATION_TIMEOUT_SEC:.0f} с",
        flush=True,
    )
    for lid, u in LEADS:
        print(f"  • {lid}  {u}", flush=True)
    print("Підказка: playwright install chromium (один раз)", flush=True)

    while True:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        for lead_id, page_url in LEADS:
            try:
                html = _fetch_html_with_iteration_timeout(
                    ITERATION_TIMEOUT_SEC, page_url
                )
                pair = parse_slots(html, lead_id)
                if pair is None:
                    print(
                        f"[{ts}] {lead_id} — не знайдено currentCopyCount/maxCopyCount "
                        f"ані t-subtitle2 з N/M у HTML.",
                        flush=True,
                    )
                else:
                    current, maximum = pair
                    full = current >= maximum
                    status = "ЗАПОВНЕНО" if full else "Є ВІЛЬНІ МІСЦЯ"
                    print(
                        f"[{ts}] {lead_id}  {current}/{maximum} — {status}",
                        flush=True,
                    )
                    if not full and (
                        prev_full[lead_id] is None or prev_full[lead_id]
                    ):
                        send_telegram_message(
                            f"Binance Copy Trading (лід <code>{lead_id}</code>): "
                            f"є вільні місця <b>{current}/{maximum}</b>\n"
                            f'<a href="{page_url}">Відкрити ліда</a>'
                        )
                    prev_full[lead_id] = full
            except subprocess.TimeoutExpired:
                print(
                    f"[{ts}] {lead_id} — ітерація перевищила "
                    f"{ITERATION_TIMEOUT_SEC:.0f} с, наступний лід…",
                    flush=True,
                )
            except FetchTimeoutError:
                print(
                    f"[{ts}] {lead_id} — timeout Binance сторінки, наступний лід…",
                    flush=True,
                )
            except Exception as e:
                print(f"[{ts}] {lead_id} — помилка: {e}", flush=True)

        time.sleep(interval)


if __name__ == "__main__":
    main()
