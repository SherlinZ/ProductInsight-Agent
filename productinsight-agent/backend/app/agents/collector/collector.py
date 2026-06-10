"""
Collector Agent — handles real-time source collection.
In cached/replay mode, returns empty (DB loading is done in nodes.py).
In real_time mode, fetches URLs in parallel, saves snapshots, and returns structured data.
"""
from __future__ import annotations

import json
import logging
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.app.services.web_fetcher import fetch_url_with_fallback

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
RUNS_DIR = PROJECT_ROOT / "data" / "runs"

# Max parallel fetch workers — balance between speed and server load
# P1-Fix: Raised from 2 to 4 to increase coverage on slow/blocked sites.
MAX_PARALLEL_FETCHES = 4   # P1: 4 workers = batch of 4 × 40s = ~160s for 8 URLs. More parallel = more coverage.

# P1-2 Fix: Official domain registry for AI Agent platforms
OFFICIAL_PRODUCT_DOMAINS = {
    "dify": {
        "dify.ai", "www.dify.ai", "docs.dify.ai", "cloud.dify.ai",
        "github.com/langgenius/dify",
    },
    "coze": {
        "coze.cn", "www.coze.cn", "docs.coze.cn",
        "coze.com", "www.coze.com", "docs.coze.com",
    },
    "flowise": {
        "flowiseai.com", "www.flowiseai.com", "docs.flowiseai.com",
        "github.com/FlowiseAI/Flowise",
    },
    "fastgpt": {
        "fastgpt.cn", "www.fastgpt.cn", "doc.fastgpt.cn",
        "fastgpt.run", "doc.fastgpt.run",
    },
}

# C2: Multi-type default URLs for known products.
PRODUCT_DEFAULT_URLS: dict[str, dict[str, str | list[str]]] = {
    "fastgpt": {
        "primary": "https://fastgpt.cn",
        "docs": "https://doc.fastgpt.cn",
    },
    "dify": {
        "primary": "https://dify.ai",
        "pricing": "https://dify.ai/pricing",
        "docs": "https://docs.dify.ai",
        "github": "https://github.com/langgenius/dify",
    },
    "coze": {
        "primary": "https://www.coze.cn",
        "pricing": "https://www.coze.cn/pricing",
        "docs": "https://www.coze.cn/docs",
    },
    "flowise": {
        "primary": "https://flowiseai.com",
        "docs": "https://docs.flowiseai.com",
        "github": "https://github.com/FlowiseAI/Flowise",
    },
}

THIRD_PARTY_DOMAINS = {
    "cloud.tencent.com", "cloud.tencent.cn",
    "dify-china.com", "difychina.com",
    "juejin.cn", "segmentfault.com", "zhihu.com",
    "csdn.net", "cnblogs.com", "imooc.com",
}


def _infer_source_type(url: str) -> str:
    url_lower = url.lower()
    if "docs" in url_lower or "documentation" in url_lower:
        return "documentation"
    if "pricing" in url_lower or "price" in url_lower or "plans" in url_lower:
        return "pricing_page"
    return "official_site"


def _determine_trust_tier(url: str, product_id: str = "") -> str:
    """
    P1-2 Fix: Determine trust tier based on domain and product.
    
    - If domain matches official_product_domains for the product -> "high"
    - If domain is in third_party_domains -> "low"
    - Otherwise -> "medium"
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Check if this is an official domain for the product
    product_id_lower = product_id.lower()
    official_domains = OFFICIAL_PRODUCT_DOMAINS.get(product_id_lower, set())
    if domain in official_domains:
        return "high"
    
    # Check if this is a known third-party domain
    if domain in THIRD_PARTY_DOMAINS:
        return "low"
    
    # Check if it's a GitHub repo
    if "github.com" in domain:
        return "medium"
    
    # Default to medium
    return "medium"


def _source_dir(run_id: str) -> Path:
    d = RUNS_DIR / run_id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


class CollectorAgent:
    """
    In real_time mode: fetches seed URLs, saves raw text/HTML to disk,
    returns sources + snapshots + raw_documents dicts.

    In cached/replay mode: this agent is not used;
    nodes.py loads from DB directly.
    """

    def collect(
        self,
        source_plan: dict[str, Any],
        run_id: str,
        mode: str = "real_time",
        total_timeout: int = 600,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Collect sources from seed URLs and official_website.
        Uses parallel fetching with URL-level isolation and multi-level fallback.

        Level 1: requests (8s) → Level 2: Playwright (18s) → Level 3: Search API

        Key improvements over raw parallel fetch:
        - Each URL has its own time budget; one slow URL can't block others.
        - Total collection has a global timeout (default 10 min); when exhausted,
          any remaining URLs are skipped rather than blocking indefinitely.
        - Search API is used as final fallback for sites that block all scraping.

        Args:
            source_plan: dict with "products" list (each has product_id, product_name, seed_urls, official_website)
            run_id: current run ID
            mode: "real_time" | "cached" | "replay"
            total_timeout: seconds before giving up and returning whatever was collected (default 600s / 10 min)

        Returns:
            {
                "sources": [...],
                "snapshots": [...],
                "raw_documents": [...],
                "collection_stats": { total_urls, collected, failed, skipped, elapsed_s }
            }
        """
        if mode in ("cached", "replay"):
            return {"sources": [], "snapshots": [], "raw_documents": [], "collection_stats": {}}

        products = source_plan.get("products", [])
        if not products:
            logger.warning("CollectorAgent: no products in source_plan")
            return {"sources": [], "snapshots": [], "raw_documents": [], "collection_stats": {}}

        now = datetime.now(timezone.utc).isoformat()
        overall_start = time.perf_counter()

        # --- Phase 1: Build flat list of (product, url) pairs ---
        url_tasks: list[dict[str, Any]] = []
        for product in products:
            product_id = product.get("product_id", "")
            product_name = product.get("product_name", product_id)

            urls: list[str] = []
            if product.get("official_website"):
                urls.append(str(product["official_website"]).strip().rstrip('/'))
            for u in product.get("seed_urls", []):
                u = u.strip().rstrip('/')
                if u:
                    urls.append(u)

            seen_normalized: set[str] = set()
            for u in urls:
                parsed = urlparse(u)
                norm = (parsed.netloc.lower() + parsed.path.lower()).rstrip('/')
                if norm not in seen_normalized:
                    seen_normalized.add(norm)
                    url_tasks.append({
                        "url": u,
                        "product_id": product_id,
                        "product_name": product_name,
                        "source_id": f"src_{uuid.uuid4().hex[:16]}",
                        "snapshot_id": f"snap_{uuid.uuid4().hex[:16]}",
                    })

        # Cap at 5 URLs per product to limit total fetch time
        urls_to_fetch = url_tasks[:5 * len(products)]
        logger.info(
            "CollectorAgent: collecting %d URLs across %d products (total_timeout=%ds)",
            len(urls_to_fetch), len(products), total_timeout,
        )

        # --- Phase 2: Parallel fetch with per-URL time budget and global timeout ---
        # Per-URL hard limit: prevents Playwright from blocking indefinitely on anti-bot sites.
        # Uses thread.submit time tracking + future cancellation to enforce the budget.
        PER_URL_TIMEOUT = 15  # P1-Fix: reduced from 40s to 15s. If a URL can't connect in 15s, it's unreachable from this server.
                              # 8 URLs × 4 workers = ~30s total, well within typical node timeout budgets.
        # File-based checkpoint: write partial results incrementally so timeout
        # doesn't lose data. Main thread reads this on timeout; cleaned up on success.
        _ckpt_path = Path(f"/tmp/collector_ckpt_{run_id}.json")
        _ckpt_lock_path = Path(f"/tmp/collector_ckpt_{run_id}.lock")
        _results: list[dict[str, Any]] = []
        _ckpt_collected = 0
        _ckpt_failed = 0
        _ckpt_skipped = 0

        def _write_ckpt() -> None:
            """Write incremental checkpoint to disk."""
            try:
                data = {
                    "results": _results,
                    "collected": _ckpt_collected,
                    "failed": _ckpt_failed,
                    "skipped": _ckpt_skipped,
                }
                _ckpt_lock_path.write_text("1")
                _ckpt_path.write_text(json.dumps(data, ensure_ascii=False))
                _ckpt_lock_path.unlink(missing_ok=True)
            except Exception:
                pass

        def _fetch_one(task: dict[str, Any]) -> dict[str, Any]:
            """Fetch one URL. Hard timeout enforced by the main loop's future tracking."""
            return fetch_url_with_fallback(
                task["url"],
                per_url_timeout=8,
                search_provider=self._get_search_provider(),
                product_name=task.get("product_name"),
            )

        def _time_remaining() -> float:
            return max(0, total_timeout - (time.perf_counter() - overall_start))

        search_provider = self._get_search_provider()
        logger.info(
            "CollectorAgent: search provider available: %s",
            "yes" if search_provider else "no",
        )
        logger.info(
            "CollectorAgent: starting parallel fetch of %d URLs (MAX_PARALLEL=%d, PER_URL_TIMEOUT=%ds, TOTAL_TIMEOUT=%ds)",
            len(urls_to_fetch), MAX_PARALLEL_FETCHES, PER_URL_TIMEOUT, total_timeout,
        )

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as executor:
            pending_futures: dict = {}  # future -> (task, submit_time)

            # Track fast failures for early exit: if multiple URLs time out quickly,
            # the network/server likely can't reach these hosts — don't waste time on all URLs.
            _consecutive_fast_fails = 0
            _fast_fail_threshold = 3  # if 3 URLs fail within 8s each, abort remaining

            # Submit initial batch
            for task in urls_to_fetch:
                if _time_remaining() <= 0:
                    logger.warning("CollectorAgent: total timeout reached before all URLs submitted, skipping remaining")
                    skipped_count += len(urls_to_fetch) - len(pending_futures)
                    break
                future = executor.submit(_fetch_one, task)
                pending_futures[future] = (task, time.perf_counter())

            # Collect results as they complete, reaping each one
            while pending_futures:
                done_futures = []
                exit_while = False
                for future in list(pending_futures.keys()):
                    task, submit_time = pending_futures[future]
                    elapsed = time.perf_counter() - submit_time

                    if future.done():
                        done_futures.append(future)
                    elif elapsed >= PER_URL_TIMEOUT:
                        # Per-URL hard timeout — cancel this future
                        future.cancel()
                        logger.warning(
                            "CollectorAgent: cancelled URL %s (elapsed=%.1fs >= PER_URL_TIMEOUT=%ds)",
                            task.get("url"), elapsed, PER_URL_TIMEOUT,
                        )
                        done_futures.append(future)
                    elif _time_remaining() <= 0:
                        # Global timeout — cancel this future and continue checking others
                        future.cancel()
                        exit_while = True

                # Process completed / cancelled futures
                for future in done_futures:
                    task, _ = pending_futures.pop(future)
                    try:
                        result = future.result(timeout=1)
                    except Exception as exc:
                        result = {
                            "error_message": f"Thread exception: {exc}",
                            "status_code": 0,
                            "raw_text": "",
                            "raw_html": "",
                            "title": "",
                            "domain": "",
                            "content_hash": "",
                            "fetched_at": now,
                        }
                    result["_task"] = task
                    _results.append(result)

                    if result.get("error_message"):
                        _ckpt_failed += 1
                    else:
                        _ckpt_collected += 1
                        _consecutive_fast_fails = 0  # reset on success

                    # Track fast consecutive timeouts for early exit
                    if result.get("error_message") and elapsed < 5:
                        _consecutive_fast_fails += 1
                        if _consecutive_fast_fails >= _fast_fail_threshold:
                            logger.warning(
                                "CollectorAgent: %d consecutive fast failures detected — "
                                "network/server cannot reach these hosts. Cancelling remaining URLs.",
                                _consecutive_fast_fails,
                            )
                            for f in pending_futures:
                                if f not in done_futures:
                                    f.cancel()
                            pending_futures.clear()
                            exit_while = True
                            break

                    if (_ckpt_collected + _ckpt_failed) % 5 == 0:
                        _write_ckpt()

                    logger.info(
                        "CollectorAgent: [%s/%s] url=%s level=%s strategy=%s text_len=%d",
                        _ckpt_collected + _ckpt_failed,
                        len(urls_to_fetch),
                        task["url"][:60],
                        result.get("fetch_level", "?"),
                        result.get("fetch_strategy", "?"),
                        len(result.get("raw_text", "")),
                    )

                # Check if we should exit the while loop
                if exit_while:
                    logger.warning("CollectorAgent: global timeout reached, cancelling remaining")
                    for f in pending_futures:
                        f.cancel()
                    pending_futures.clear()
                    break
                if not pending_futures:
                    break
                time.sleep(0.5)

        # Read from checkpoint (works whether we finished or timed out)
        if _ckpt_path.exists():
            try:
                ckpt_data = json.loads(_ckpt_path.read_text())
                _results = ckpt_data.get("results", _results)
                _ckpt_collected = ckpt_data.get("collected", _ckpt_collected)
                _ckpt_failed = ckpt_data.get("failed", _ckpt_failed)
                _ckpt_skipped = ckpt_data.get("skipped", _ckpt_skipped)
            except Exception:
                pass
            finally:
                try:
                    _ckpt_path.unlink(missing_ok=True)
                except Exception:
                    pass

        results = _results
        collected_count = _ckpt_collected
        failed_count = _ckpt_failed
        skipped_count = _ckpt_skipped

        elapsed = time.perf_counter() - overall_start
        logger.info(
            "CollectorAgent: parallel fetch done in %.1fs — collected=%d failed=%d skipped=%d",
            elapsed, collected_count, failed_count, skipped_count,
        )

        # --- Phase 3: Build sources, snapshots, raw_documents from results ---
        sources: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        raw_documents: list[dict[str, Any]] = []
        failed_count = 0

        for result in results:
            task = result["_task"]
            url = task["url"]
            product_id = task["product_id"]
            product_name = task["product_name"]
            source_id = task["source_id"]
            snapshot_id = task["snapshot_id"]

            error_msg = result.get("error_message")
            status_code = result.get("status_code", 0)
            raw_text = result.get("raw_text", "")
            raw_html = result.get("raw_html", "")
            title = result.get("title", "") or product_name
            domain = result.get("domain", "")
            content_hash = result.get("content_hash", "")
            fetched_at = result.get("fetched_at", now)
            source_type = _infer_source_type(url)

            if error_msg:
                failed_count += 1

            # Save snapshot files
            snap_dir = _source_dir(run_id) / snapshot_id
            raw_text_path = ""
            html_path = ""

            if not error_msg and raw_text:
                try:
                    snap_dir.mkdir(parents=True, exist_ok=True)
                    raw_text_path = str(snap_dir / "raw.txt")
                    (snap_dir / "raw.txt").write_text(raw_text[:500000], encoding="utf-8")
                    html_path = str(snap_dir / "page.html")
                    (snap_dir / "page.html").write_text(raw_html[:500000], encoding="utf-8")
                    raw_doc = {
                        "run_id": run_id,
                        "product_id": product_id,
                        "source_id": source_id,
                        "snapshot_id": snapshot_id,
                        "raw_text": raw_text[:500000],
                        "source_type": source_type,
                        "url": url,
                        "title": title,
                    }
                    (snap_dir / "raw_document.json").write_text(
                        json.dumps(raw_doc, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                except OSError as exc:
                    logger.warning("Failed to write snapshot for %s: %s", url, exc)

            metadata: dict[str, Any] = {"title": title, "domain": domain, "status_code": status_code, "url": url}
            if error_msg:
                metadata["valid"] = False
                metadata["error_message"] = error_msg

            collection_method_map = {
                "requests": "crawl",
                "playwright": "crawl_spa",
                "search_api": "search_snippet",
                "none": "failed",
            }
            source_record = {
                "source_id": source_id,
                "run_id": run_id,
                "product_id": product_id,
                "source_type": source_type,
                "title": title[:500],
                "url": url,
                "domain": domain,
                "collection_method": collection_method_map.get(result.get("fetch_strategy", ""), "crawl"),
                "robots_status": "unknown",
                "terms_note": "public page fetched from user-provided seed URL",
                "trust_tier": _determine_trust_tier(url, product_id),
                "fetched_at": fetched_at,
                "content_hash": content_hash,
                "status": "failed" if error_msg else "collected",
                "error_message": error_msg,
                "fetch_level": result.get("fetch_level", 0),
                "fetch_strategy": result.get("fetch_strategy", "none"),
                "char_count": len(raw_text),
                "created_at": now,
                "updated_at": now,
            }
            sources.append(source_record)

            snapshot_record = {
                "snapshot_id": snapshot_id,
                "source_id": source_id,
                "run_id": run_id,
                "raw_text_path": raw_text_path,
                "html_path": html_path,
                "metadata": metadata,
                "content_hash": content_hash,
                "token_count": len(raw_text) // 4 if raw_text else 0,
                "created_at": now,
            }
            snapshots.append(snapshot_record)

            if not error_msg and raw_text:
                raw_documents.append({
                    "run_id": run_id,
                    "product_id": product_id,
                    "source_id": source_id,
                    "snapshot_id": snapshot_id,
                    "raw_text": raw_text,
                    "source_type": source_type,
                    "url": url,
                    "title": title,
                })

            logger.info(
                "CollectorAgent: collected source_id=%s title='%s' text_len=%d error=%s",
                source_id, title[:50], len(raw_text), bool(error_msg),
            )

        logger.info(
            "CollectorAgent: collected %d sources, %d snapshots, %d raw_documents (%d failed)",
            len(sources), len(snapshots), len(raw_documents), failed_count,
        )
        return {
            "sources": sources,
            "snapshots": snapshots,
            "raw_documents": raw_documents,
            "collection_stats": {
                "total_urls": len(urls_to_fetch),
                "collected": collected_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "elapsed_s": round(elapsed, 1),
                "total_timeout_s": total_timeout,
                "total_chars": sum(len(r.get("raw_text", "")) for r in results),
            },
        }

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _get_search_provider(self):
        """Lazily load the search provider (Doubao web search) as L3 fallback."""
        if not hasattr(self, "_search_provider"):
            try:
                from backend.app.services.search_provider import get_search_provider
                self._search_provider = get_search_provider()
                if not self._search_provider.is_available():
                    self._search_provider = None
            except Exception:
                self._search_provider = None
        return self._search_provider
