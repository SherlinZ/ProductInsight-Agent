"""
Standalone Playwright runner — invoked as a subprocess so the parent can
terminate the entire process tree (including the Playwright Node.js driver)
on timeout, preventing thread/process leaks that hang the collector.

Output protocol: prints a single JSON line on stdout and exits.
  Success: {"ok": true, "text": "...", "title": "..."}
  Failure: {"ok": false, "error": "..."}

Usage:
    python playwright_runner.py <url> <timeout_seconds>
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": "usage: playwright_runner.py <url> <timeout>"}))
        return 2

    url = sys.argv[1]
    try:
        timeout = int(sys.argv[2])
    except ValueError:
        timeout = 20

    user_agent = (
        "Mozilla/5.0 (compatible; ResearchBot/1.0; +Research Agent Platform) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    browser = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(json.dumps({"ok": False, "error": "Playwright not installed"}))
        return 3

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=user_agent,
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            )
            page = ctx.new_page()

            try:
                page.goto(url, wait_until="load", timeout=timeout * 1000)
            except Exception:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                except Exception:
                    pass

            try:
                page.wait_for_timeout(3000)
            except Exception:
                pass

            try:
                title = page.title() or ""
            except Exception:
                title = ""

            try:
                body_text = page.locator("body").inner_text(timeout=3000)
                if len(body_text.strip()) < 200:
                    try:
                        page.wait_for_timeout(3000)
                        body_text = page.locator("body").inner_text(timeout=3000)
                    except Exception:
                        pass
            except Exception:
                body_text = ""

            print(json.dumps({"ok": True, "text": body_text or "", "title": title}, ensure_ascii=False))
            return 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Playwright error: {exc}"}, ensure_ascii=False))
        return 1
    finally:
        if browser is not None:
            try:
                browser.close(timeout=3000)
            except Exception:
                try:
                    browser.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())