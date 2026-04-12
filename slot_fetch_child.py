"""Окремий процес: fetch_html_playwright(url) → файл (argv[1]), URL у argv[2]."""

from __future__ import annotations

import sys
from pathlib import Path


def run() -> None:
    if len(sys.argv) < 3:
        print("usage: slot_fetch_child.py <output.html> <page_url>", file=sys.stderr)
        sys.exit(2)
    out_path = Path(sys.argv[1])
    page_url = sys.argv[2]
    from main import fetch_html_playwright

    html = fetch_html_playwright(page_url)
    out_path.write_text(html, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    run()
