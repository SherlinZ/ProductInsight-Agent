"""
Web fetcher that uses requests (primary) + Playwright (SPA fallback) + Search API (last resort).

Rationale:
- requests is fast and handles static pages well
- Playwright handles SPA/JS-rendered pages (coze.cn, dify.ai, etc.)
- Search API provides snippet fallback when both fail
"""

from __future__ import annotations

import re
import hashlib
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)

# Minimal user agent to avoid blocking
USER_AGENT = (
    "Mozilla/5.0 (compatible; ResearchBot/1.0; +Research Agent Platform) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# HTML noise tags to strip
NOISE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"}


def _compute_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc
    except Exception:
        return ""


def _extract_title(soup) -> str:
    try:
        title = soup.find("title")
        if title:
            return title.get_text(strip=True)
    except Exception:
        pass
    return ""


def _clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"[ \t]+", " ", raw)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _page_to_text(html: str) -> tuple[str, str]:
    """Parse HTML to extract visible text. Returns (raw_text, title)."""
    from bs4 import BeautifulSoup as _bs

    soup = _bs(html, "html.parser")

    # Remove noise tags
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()

    # Remove HTML comments
    from bs4 import Comment as _bc

    for comment in soup.find_all(string=lambda t: isinstance(t, _bc)):
        comment.extract()

    title = _extract_title(soup)
    body = soup.find("body") or soup
    raw_text = body.get_text(separator="\n", strip=False)
    raw_text = _clean_text(raw_text)

    return raw_text, title


def fetch_url(url: str, timeout: int = 20) -> dict[str, Any]:
    """
    Fetch a single URL with urllib (no external dependencies).

    Returns dict with keys:
        url, status_code, final_url, domain, title,
        raw_html, raw_text, content_hash, fetched_at, error_message
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return {
            "url": url,
            "status_code": 0,
            "final_url": url,
            "domain": "",
            "title": "",
            "raw_html": "",
            "raw_text": "",
            "content_hash": "",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error_message": f"Unsupported scheme: {parsed.scheme}",
        }

    domain = _extract_domain(url)
    now = datetime.now(timezone.utc).isoformat()

    try:
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            },
        )
        response = urlopen(req, timeout=timeout)
        status_code = response.getcode()
        final_url = response.geturl()

        raw_html_bytes = response.read()
        content_encoding = response.headers.get("Content-Encoding", "")

        if content_encoding == "gzip":
            import gzip
            raw_html_bytes = gzip.decompress(raw_html_bytes)
        elif content_encoding == "deflate":
            import zlib
            raw_html_bytes = zlib.decompress(raw_html_bytes)

        raw_html_str = raw_html_bytes.decode("utf-8", errors="replace")
        raw_text, title = _page_to_text(raw_html_str)
        content_hash = _compute_hash(raw_text)

        return {
            "url": url,
            "status_code": status_code,
            "final_url": final_url,
            "domain": domain,
            "title": title or url,
            "raw_html": raw_html_str[:500000],
            "raw_text": raw_text[:500000],
            "content_hash": content_hash,
            "fetched_at": now,
            "error_message": None,
        }

    except HTTPError as e:
        return {
            "url": url,
            "status_code": e.code,
            "final_url": url,
            "domain": domain,
            "title": "",
            "raw_html": "",
            "raw_text": "",
            "content_hash": "",
            "fetched_at": now,
            "error_message": f"HTTP Error {e.code}: {e.reason}",
        }
    except URLError as e:
        return {
            "url": url,
            "status_code": 0,
            "final_url": url,
            "domain": domain,
            "title": "",
            "raw_html": "",
            "raw_text": "",
            "content_hash": "",
            "fetched_at": now,
            "error_message": f"Request failed: {e.reason}",
        }
    except Exception as e:
        return {
            "url": url,
            "status_code": 0,
            "final_url": url,
            "domain": domain,
            "title": "",
            "raw_html": "",
            "raw_text": "",
            "content_hash": "",
            "fetched_at": now,
            "error_message": f"Unexpected error: {e}",
        }


def _fetch_with_playwright(url: str, timeout: int = 20) -> tuple[str, str, str]:
    """
    Fetch URL using Playwright (headless Chromium) to handle JS-rendered SPA pages.

    Wrapped in ThreadPoolExecutor with HARD_TIMEOUT to prevent indefinite hangs.
    Playwright is called directly (not as subprocess) since it works fine inside
    ThreadPoolExecutor in the default Python environment.

    Returns (raw_text, title, error_message).
    """
    HARD_TIMEOUT = 15  # Playwright subprocess-level timeout

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    def _run():
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "", "", "Playwright not installed"

        browser = None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent=USER_AGENT,
                    extra_http_headers={
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                )
                page = ctx.new_page()

                # Use 'load' event — fires when HTML is parsed.
                # 'networkidle' never resolves on pages with persistent connections.
                try:
                    page.goto(url, wait_until="load", timeout=timeout * 1000)
                except Exception:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                    except Exception:
                        pass

                page.wait_for_timeout(3000)

                title = page.title() or ""

                # Get text from body
                try:
                    body_text = page.locator("body").inner_text(timeout=3000)
                    if len(body_text.strip()) < 200:
                        page.wait_for_timeout(3000)
                        body_text = page.locator("body").inner_text(timeout=3000)
                except Exception:
                    body_text = ""

                return _clean_text(body_text), title, ""

        except Exception as exc:
            return "", "", f"Playwright error: {exc}"
        finally:
            if browser is not None:
                try:
                    browser.close(timeout=3000)
                except Exception:
                    try:
                        browser.kill()
                    except Exception:
                        pass

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_run)
            return future.result(timeout=HARD_TIMEOUT)
    except FuturesTimeoutError:
        return "", "", f"Playwright exceeded hard timeout of {HARD_TIMEOUT}s"
    except Exception as exc:
        return "", "", f"Playwright thread error: {exc}"


def fetch_url_with_fallback(
    url: str,
    per_url_timeout: int = 8,
    search_provider=None,
    product_name: str | None = None,
) -> dict[str, Any]:
    """
    Fetch a URL using a 3-level fallback strategy.

    Level 1: requests (fast, handles static pages)
    Level 2: Playwright (handles SPA/JS-rendered pages)
    Level 3: Search API (snippet fallback when both fail)

    Returns the same dict shape as fetch_url(), with additional fields:
        fetch_level: 1 | 2 | 3 | "failed"
        fetch_strategy: "requests" | "playwright" | "search_api" | "none"
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── Level 1: requests ─────────────────────────────────────────────────
    result = fetch_url(url, timeout=per_url_timeout)

    if not result.get("error_message") and len(result.get("raw_text", "")) >= 200:
        result["fetch_level"] = 1
        result["fetch_strategy"] = "requests"
        return result

    # Got a response but content is thin — try Playwright (for any response including timeouts).
    # We pass "level 1 gave us something" to Playwright so it can render SPAs.
    # This is triggered when: (a) status_code is success but text < 200 chars, OR
    # (b) status_code is None/timeout (requests couldn't get meaningful content).
    _has_response = result.get("status_code") is not None
    _content_ok = len(result.get("raw_text", "")) >= 200
    if _has_response and not _content_ok:
        pw_text, pw_title, pw_err = _fetch_with_playwright(url, timeout=per_url_timeout)
        if pw_text and len(pw_text) >= 200:
            result = {
                "url": url,
                "status_code": 200,
                "final_url": url,
                "domain": _extract_domain(url),
                "title": pw_title or result.get("title", ""),
                "raw_html": "",
                "raw_text": pw_text[:500000],
                "content_hash": _compute_hash(pw_text),
                "fetched_at": now,
                "error_message": None,
                "fetch_level": 2,
                "fetch_strategy": "playwright",
            }
            return result
        else:
            logger.info(
                "fetch_url_with_fallback L2 Playwright failed for %s (%s chars, err=%s), "
                "trying Level 3 (search API)",
                url, len(pw_text), pw_err[:60],
            )

    # ── Level 3: Search API fallback ─────────────────────────────────────
    if search_provider is not None and hasattr(search_provider, "search"):
        try:
            from urllib.parse import urlparse as _urlparse

            path_segments = _urlparse(url).path.strip("/").split("/")
            noise_segments = {
                "en", "zh", "zh-cn", "zh-hans", "v1", "v2", "v3",
                "docs", "documentation", "guide", "guides",
                "index", "home", "api", "developer",
            }
            path_keywords = [
                seg for seg in path_segments
                if seg.lower() not in noise_segments and len(seg) > 2
            ]

            query_parts = []
            if product_name and product_name not in (url, _urlparse(url).netloc):
                query_parts.append(product_name.strip())
            query_parts.extend(path_keywords)

            if not query_parts:
                domain = _extract_domain(url)
                if domain:
                    clean_domain = domain
                    for tld in (".com", ".cn", ".io", ".ai"):
                        if clean_domain.endswith(tld):
                            clean_domain = clean_domain[: -len(tld)]
                            break
                    if clean_domain:
                        query_parts.append(clean_domain)

            search_query = " ".join(query_parts) if query_parts else url

            search_results = search_provider.search(search_query, top_k=3)
            if search_results:
                best = search_results[0]
                raw_text = f"[Source: {best.title}]\n{best.snippet}"
                return {
                    "url": url,
                    "status_code": 200,
                    "final_url": url,
                    "domain": _extract_domain(url),
                    "title": best.title or search_query,
                    "raw_html": "",
                    "raw_text": raw_text,
                    "content_hash": _compute_hash(raw_text),
                    "fetched_at": now,
                    "error_message": None,
                    "fetch_level": 3,
                    "fetch_strategy": "search_api",
                }
        except Exception as exc:
            logger.warning(
                "fetch_url_with_fallback L3 search failed for %s: %s",
                url, exc,
            )

    # All levels failed — return what we have from Level 1
    result["fetch_level"] = "failed"
    result["fetch_strategy"] = "none"
    return result
