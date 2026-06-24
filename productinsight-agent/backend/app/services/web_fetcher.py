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

    P2-FIX (2026-06-20): Subprocess-based isolation.

    Previous implementation ran Playwright inside a ThreadPoolExecutor worker. When
    Playwright's browser.close() hung on a misbehaving site (e.g. coda.io), the
    worker thread could not be terminated by future.cancel(). The orphan Node.js
    driver and Chromium subprocess survived, eventually pinning the entire
    uvicorn worker (19 threads blocked on the collect_sources wait() loop) for
    30+ minutes.

    New approach: launch the Playwright work in a fresh Python subprocess running
    playwright_runner.py. On hard timeout we kill the entire process group (Popen
    start_new_session=True + os.killpg(SIGKILL)), which takes down the Node.js
    driver and Chromium children. The subprocess always exits within
    HARD_TIMEOUT seconds, so this function never hangs.
    """
    import json as _json
    import os
    import subprocess
    import sys
    from pathlib import Path

    HARD_TIMEOUT = max(timeout + 2, 8)  # grace period beyond page goto timeout

    runner_path = Path(__file__).parent / "playwright_runner.py"
    if not runner_path.exists():
        return "", "", f"Playwright runner missing: {runner_path}"

    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, str(runner_path), url, str(timeout)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # new process group so we can killpg the tree
        )
        try:
            stdout, _stderr = proc.communicate(timeout=HARD_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)  # SIGKILL the entire group (driver + chromium)
            except (ProcessLookupError, PermissionError):
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return "", "", f"Playwright exceeded hard timeout of {HARD_TIMEOUT}s"

        if proc.returncode != 0:
            return "", "", f"Playwright runner exited with code {proc.returncode}"

        try:
            payload = _json.loads(stdout.decode("utf-8", errors="replace") or "{}")
        except Exception as exc:
            return "", "", f"Playwright runner JSON parse error: {exc}"

        if not payload.get("ok"):
            return "", "", payload.get("error") or "Playwright returned ok=false"

        text = payload.get("text", "") or ""
        title = payload.get("title", "") or ""
        if len(text.strip()) < 50:
            return text, title, "Playwright returned <50 chars (likely blocked)"
        return _clean_text(text), title, ""

    except FileNotFoundError as exc:
        return "", "", f"Playwright runner not found: {exc}"
    except Exception as exc:
        return "", "", f"Playwright subprocess error: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, 9)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


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

            from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
            SEARCH_TIMEOUT = 20  # seconds — Doubao API typically responds in 5-15s
            try:
                with ThreadPoolExecutor(max_workers=1) as _sp_ex:
                    _sp_future = _sp_ex.submit(search_provider.search, search_query, 3)
                    search_results = _sp_future.result(timeout=SEARCH_TIMEOUT)
            except FuturesTimeoutError:
                logger.warning("fetch_url_with_fallback L3 search timed out (%ds) for %s", SEARCH_TIMEOUT, url)
                search_results = []
            except Exception as _sp_exc:
                logger.warning("fetch_url_with_fallback L3 search failed for %s: %s", url, _sp_exc)
                search_results = []
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
