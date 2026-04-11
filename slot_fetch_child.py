"""Окремий процес: викликає fetch з main.py і пише HTML у файл (шлях у argv[1])."""

from __future__ import annotations

import sys
from pathlib import Path


def run() -> None:
    if len(sys.argv) < 2:
        print("usage: slot_fetch_child.py <output.html>", file=sys.stderr)
        sys.exit(2)
    out_path = Path(sys.argv[1])
    from main import fetch_html_playwright

    html = fetch_html_playwright()
    out_path.write_text(html, encoding="utf-8", errors="replace")


if __name__ == "__main__":
    run()
