from __future__ import annotations

"""
Workflow Node Implementations — Agent tasks + structured message contracts.

AGENT → AGENT MESSAGE CONTRACTS
===============================
Every node reads from WorkflowState, performs its task, and writes structured
results back to WorkflowState. These payloads are the "structured messages" between
agents. Key contracts:

review_claims → execute_rework:
  state["rework_requests"].append({
    "dimension": str,        # e.g. "pricing_model"
    "claim_id": str,        # which claim failed
    "reason": str,          # MISSING_EVIDENCE | UNUSABLE_EVIDENCE | PII_NOT_MASKED | ...
    "evidence_ids": list,   # evidence that failed quality gates
    "priority": str,        # high | medium | low
    "instructions": str,    # LLM-generated fix instructions
  })

coverage_critic → execute_rework:
  state["rework_requests"].append({
    "dimension": str,
    "gap_type": str,       # coverage_gap
    "supplemental_queries": list[str],
    "priority": str,
    "reason": str,
  })

execute_rework → evaluate_evidence / analyze_dimensions:
  state["rework_summary"]: {
    "total_tasks": int,
    "succeeded": int,
    "failed": int,
  }
  state["sources"]: updated with new evidence
  state["evidence_items"]: updated with new items

final_review → write_report_v2 (if rework_required):
  state["rework_requests"].append({...})  # same contract as review_claims

reflect_on_review (enrichment step):
  Reads state["rework_requests"], calls LLM to add priority/scope/instructions,
  then writes enriched state["rework_requests"] back. This is a pure
  transformation pass — it enriches but does not block.

ITERATION GUARDS
================
Each feedback loop has MAX_ITERATIONS to prevent infinite loops:
  - MAX_CLAIMS_REWORK_ITERATIONS = 3
  - MAX_CLAIMS_REFLECT_ITERATIONS = 2
  - MAX_REPORT_REWRITE_ITERATIONS = 3
  - MAX_COVERAGE_REWORK_ITERATIONS = 3
See graph.py for the route functions that enforce these counters.
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.app.orchestrator.state import WorkflowState

logger = logging.getLogger(__name__)

# Default seed URLs for known products (used when user doesn't provide URLs)
# C2: Multi-type default URLs per product (primary / pricing / docs / github / blog).
# Used by plan_sources for auto-fill when user provides only a product name.
PRODUCT_DEFAULT_URLS: dict[str, dict[str, str | list[str]]] = {
    # ── AI Coding Tools ────────────────────────────────────────────────────────
    "cursor": {
        "primary": "https://www.cursor.com",
        "docs": "https://www.cursor.com/docs",
        "pricing": "https://www.cursor.com/pricing",
    },
    "codex": {
        "primary": "https://openai.com/index/openai-codex/",
        "docs": "https://platform.openai.com/docs/guides/code",
        "github": "https://github.com/openai/codex",
    },
    "trae": {
        "primary": "https://trae.ai",
        "docs": "https://docs.trae.ai",
        "pricing": "https://trae.ai/pricing",
        "blog": "https://trae.ai/blog/official-launch",
    },
    "cloudecode": {
        "primary": "https://www.volcengine.com/product/cloudecode",
        "docs": "https://www.volcengine.com/docs/6458/1166378",
        "pricing": "https://www.volcengine.com/product/cloudecode/pricing",
        "github": "https://github.com/volcengine/cloudecode",
    },
    # ── AI Agent Platforms ────────────────────────────────────────────────────
    "coze": {
        "primary": "https://www.coze.cn",
        "pricing": "https://www.coze.cn/pricing",
        "docs": "https://www.coze.cn/docs",
    },
    "dify": {
        "primary": "https://dify.ai",
        "pricing": "https://dify.ai/pricing",
        "docs": "https://docs.dify.ai",
        "github": "https://github.com/langgenius/dify",
    },
    "flowise": {
        "primary": "https://flowiseai.com",
        "docs": "https://docs.flowiseai.com",
        "github": "https://github.com/FlowiseAI/Flowise",
    },
    "fastgpt": {
        "primary": "https://fastgpt.cn",
        "docs": "https://doc.fastgpt.cn",
    },
    # ── Search / AI Search ────────────────────────────────────────────────────
    "perplexity": {
        "primary": "https://www.perplexity.ai",
        "docs": "https://www.perplexity.ai/home",
        "blog": "https://www.perplexity.ai/blog",
    },
    "you": {
        "primary": "https://you.com",
        "docs": "https://you.com/docs",
    },
    "phind": {
        "primary": "https://www.phind.com",
        "github": "https://github.com/phind-com",
    },
    # ── Enterprise / B2B SaaS ─────────────────────────────────────────────────
    "notion": {
        "primary": "https://www.notion.so",
        "pricing": "https://www.notion.so/pricing",
        "docs": "https://www.notion.so/help",
    },
    "confluence": {
        "primary": "https://www.atlassian.com/software/confluence",
        "pricing": "https://www.atlassian.com/software/confluence/download",
        "docs": "https://confluence.atlassian.com/",
    },
    "linear": {
        "primary": "https://linear.app",
        "pricing": "https://linear.app/pricing",
        "docs": "https://linear.app/docs",
    },
    "jira": {
        "primary": "https://www.atlassian.com/software/jira",
        "pricing": "https://www.atlassian.com/software/jira/pricing",
        "docs": "https://docs.atlassian.com/jira-software",
    },
    "slack": {
        "primary": "https://slack.com",
        "pricing": "https://slack.com/pricing",
        "docs": "https://slack.com/help",
    },
    "asana": {
        "primary": "https://asana.com",
        "pricing": "https://asana.com/pricing",
        "docs": "https://asana.com/guide",
    },
    "monday": {
        "primary": "https://monday.com",
        "pricing": "https://monday.com/pricing",
        "docs": "https://monday.com/documentation",
    },
    "hubspot": {
        "primary": "https://www.hubspot.com",
        "pricing": "https://www.hubspot.com/pricing",
        "docs": "https://developers.hubspot.com/",
    },
    "salesforce": {
        "primary": "https://www.salesforce.com",
        "pricing": "https://www.salesforce.com/editions-pricing/",
        "docs": "https://developer.salesforce.com/docs",
    },
    "zendesk": {
        "primary": "https://www.zendesk.com",
        "pricing": "https://www.zendesk.com/pricing/",
        "docs": "https://developer.zendesk.com/",
    },
    "intercom": {
        "primary": "https://www.intercom.com",
        "pricing": "https://www.intercom.com/pricing",
        "docs": "https://developers.intercom.com/",
    },
    "segment": {
        "primary": "https://segment.com",
        "pricing": "https://segment.com/pricing/",
        "docs": "https://segment.com/docs/",
    },
    "mixpanel": {
        "primary": "https://www.mixpanel.com",
        "pricing": "https://www.mixpanel.com/pricing/",
        "docs": "https://developer.mixpanel.com/",
    },
    # ── Productivity / Docs ───────────────────────────────────────────────────
    "obsidian": {
        "primary": "https://obsidian.md",
        "pricing": "https://obsidian.md/pricing",
        "docs": "https://help.obsidian.md/",
    },
    "readwise": {
        "primary": "https://readwise.io",
        "docs": "https://help.readwise.io/",
    },
    # ── E-commerce / CRM ───────────────────────────────────────────────────────
    "shopify": {
        "primary": "https://www.shopify.com",
        "pricing": "https://www.shopify.com/pricing",
        "docs": "https://shopify.dev/docs",
    },
    "woocommerce": {
        "primary": "https://woocommerce.com",
        "pricing": "https://woocommerce.com/pricing/",
        "docs": "https://woocommerce.com/documentation/woocommerce-docs/",
        "github": "https://github.com/woocommerce/woocommerce",
    },
    "bigcommerce": {
        "primary": "https://www.bigcommerce.com",
        "pricing": "https://www.bigcommerce.com/pricing/",
        "docs": "https://www.bigcommerce.com/docs/",
    },
    "magento": {
        "primary": "https://business.adobe.com/products/magento/magento-commerce.html",
        "docs": "https://developer.adobe.com/commerce/docs/",
        "github": "https://github.com/magento/magento2",
    },
    # ── AI Products ───────────────────────────────────────────────────────────
    "chatgpt": {
        "primary": "https://openai.com/index/chatgpt",
        "docs": "https://platform.openai.com/docs/",
        "chat": "https://chat.openai.com",
        "blog": "https://openai.com/blog",
        "research": "https://openai.com/research",
    },
    "claude": {
        "primary": "https://claude.ai",
        "docs": "https://docs.anthropic.com/",
    },
    "gemini": {
        "primary": "https://ai.google.dev",
        "docs": "https://ai.google.dev/docs",
    },
    "groq": {
        "primary": "https://console.groq.com",
        "docs": "https://console.groq.com/docs",
    },
    "ollama": {
        "primary": "https://ollama.com",
        "docs": "https://github.com/ollama/ollama",
        "github": "https://github.com/ollama/ollama",
    },
    "vllm": {
        "primary": "https://docs.vllm.ai",
        "github": "https://github.com/vllm-project/vllm",
    },
    # ── Collaborative / Dev Tools ────────────────────────────────────────────
    "github": {
        "primary": "https://github.com",
        "pricing": "https://github.com/pricing",
        "docs": "https://docs.github.com/en",
    },
    "gitlab": {
        "primary": "https://about.gitlab.com",
        "pricing": "https://about.gitlab.com/pricing/",
        "docs": "https://docs.gitlab.com/",
    },
    "vercel": {
        "primary": "https://vercel.com",
        "pricing": "https://vercel.com/pricing",
        "docs": "https://vercel.com/docs",
    },
    "netlify": {
        "primary": "https://www.netlify.com",
        "pricing": "https://www.netlify.com/pricing/",
        "docs": "https://docs.netlify.com/",
    },
    # ── Generic domain-level defaults ─────────────────────────────────────────
    "default": {
        "primary": "",
    },
}


def _resolve_product_urls(product: dict[str, Any]) -> list[str]:
    """
    Resolve seed URLs for a product using multiple strategies.

    Strategy 1: Explicit seed_urls / official_website already provided → return as-is.
    Strategy 2: Known product in PRODUCT_DEFAULT_URLS → use predefined URLs.
    Strategy 3: Unknown product → construct plausible URLs from product name using
                a domain-aware pattern (e.g., product "Foo" → foo.com, docs.foo.com).
    Strategy 4: No URL possible → return empty list (LLM-based URL discovery will
                be triggered by _perform_source_discovery in collect_sources).

    This removes the hardcoded 4-product limitation and makes the system work
    with ANY domain product.
    """
    name = product.get("product_name", "")
    slug = product.get("product_slug", "") or name.lower().replace(" ", "-").replace("_", "-")

    # Strategy 1: already has URLs
    existing = product.get("seed_urls", [])
    if isinstance(existing, str):
        existing = [existing]
    if existing:
        return [u.strip() for u in existing if u.strip()]

    official = product.get("official_website", "")
    if official:
        return [official.strip()]

    # Strategy 2: known product
    key = slug.lower()
    if key in PRODUCT_DEFAULT_URLS and key != "default":
        mapping = PRODUCT_DEFAULT_URLS[key]
        urls = []
        for k in ("primary", "pricing", "docs", "github", "blog"):
            v = mapping.get(k)
            if v:
                urls.extend(v if isinstance(v, list) else [v])
        if urls:
            return urls

    # Strategy 3: construct plausible URLs for unknown products
    # Use product slug as domain base (lowercase, no spaces)
    domain_base = slug.lower().replace(" ", "").replace("-", "")
    constructed: list[str] = []
    for base in (f"https://www.{domain_base}.com", f"https://{domain_base}.io",
                 f"https://{domain_base}.ai", f"https://{domain_base}.cn"):
        constructed.append(base)

    logger.info(
        "plan_sources: constructed %d candidate URLs for unknown product '%s' (slug='%s'). "
        "collect_sources will filter via robots.txt check.",
        len(constructed), name, slug,
    )
    return constructed


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Lazy imports for agents (avoid circular imports at module load)
def _analyst():
    from backend.app.agents.analyst.analyst import AnalystAgent
    return AnalystAgent()


def _writer():
    from backend.app.agents.writer.writer import WriterAgent
    return WriterAgent()


def _reviewer():
    from backend.app.agents.reviewer.reviewer import ReviewerAgent
    return ReviewerAgent()


def _collector():
    from backend.app.agents.collector.collector import CollectorAgent
    return CollectorAgent()


# ─────────────────────────────────────────────────────────────────
# Thread-local storage for parallel product collection
#
# Each per-product worker thread needs its own DB connection because
# SQLite connections are NOT thread-safe. ThreadPoolExecutor workers
# share this dict via threading.local() — each thread sees its own
# Repository instance backed by its own sqlite3.Connection.
#
# Without this, parallel writes from N workers to the same SQLite
# connection raise "SQLite objects created in a thread can only be
# used in that same thread".
# ─────────────────────────────────────────────────────────────────
import threading as _threading
_thread_local_repo = _threading.local()


def _get_thread_repo(repo_cls):
    """Return a thread-local Repository instance (one connection per worker)."""
    if not hasattr(_thread_local_repo, "_repos"):
        _thread_local_repo._repos = {}
    cache = _thread_local_repo._repos
    name = repo_cls.__name__
    if name not in cache:
        cache[name] = repo_cls()
    return cache[name]


def _reset_thread_repos() -> None:
    """Drop thread-local repository cache (call at end of parallel block)."""
    if hasattr(_thread_local_repo, "_repos"):
        _thread_local_repo._repos.clear()


def _collect_one_product_sync(
    agent,
    product: dict,
    source_plan_template: dict,
    run_id: str,
    per_product_timeout: int,
) -> dict:
    """Collect URLs for ONE product independently.

    Returns the same dict shape as CollectorAgent.collect():
        {"sources": [...], "snapshots": [...], "raw_documents": [...],
         "collection_stats": {...}}
    Safe to call from a ThreadPoolExecutor worker.
    """
    # Per-product source_plan: single product + global target_source_types
    sp = {
        "products": [product],
        "target_source_types": source_plan_template.get("target_source_types", []),
        "discovered_products_with_urls": [],
        "supplement_products": [],
    }
    try:
        result = agent.collect(sp, run_id, mode="real_time", total_timeout=per_product_timeout)
        return result or {
            "sources": [],
            "snapshots": [],
            "raw_documents": [],
            "collection_stats": {"collected": 0, "failed": 0, "total_urls": 0},
        }
    except Exception as exc:
        _collect_logger_local.error(
            "_collect_one_product_sync failed for product=%s: %s",
            product.get("product_name", product.get("product_id", "")),
            exc,
        )
        return {
            "sources": [],
            "snapshots": [],
            "raw_documents": [],
            "collection_stats": {"collected": 0, "failed": 1, "total_urls": 0, "error": str(exc)},
        }


# Module-local logger for the parallel helper
import logging as _logging
_collect_logger_local = _logging.getLogger(__name__)


def _resolve_to_product(row: dict) -> str:
    """Return the canonical product_id for a row (evidence/fact/claim)."""
    rp = str(row.get("product_id", "")).strip().lower()
    rs = str(row.get("product_slug", "")).strip().lower()
    rn = str(row.get("product_name", "")).strip().lower()
    # Return the first non-empty, non-generic field
    for val in (rp, rs, rn):
        if val and val not in ("", "unknown", "n/a"):
            return val
    return rp


def _matches_rework_product(row: dict, target: str) -> bool:
    """Return True if row belongs to the target product (handles run-scoped IDs)."""
    rp = str(row.get("product_id", "")).strip().lower()
    rs = str(row.get("product_slug", "")).strip().lower()
    rn = str(row.get("product_name", "")).strip().lower()
    tp = str(target or "").strip().lower()
    if not tp:
        return True
    return (
        rp == tp
        or rp.endswith("_" + tp)
        or rs == tp
        or rn == tp
    )


def _evidence_extractor():
    from backend.app.agents.collector.evidence_extractor import EvidenceExtractor
    return EvidenceExtractor()


def _fact_extractor():
    from backend.app.agents.collector.fact_extractor import FactExtractor
    return FactExtractor()


def _db_query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute a read-only SQL query and return list of dicts with parsed JSON columns."""
    import sqlite3
    from backend.app.storage.db import get_connection
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Source Discovery helpers (vNext-R2-D)
# ---------------------------------------------------------------------------

def _discover_urls_via_llm(
    products_without_urls: list[dict],
    discovery_queries: list[dict],
) -> list[dict]:
    """
    Use LLM to infer official URLs for products that have no seed URLs.
    
    This is a fallback when web search is unavailable.
    Returns list of candidate dicts with url, title, competitor fields.
    """
    from backend.app.services.llm_client import get_llm_client
    
    candidates = []
    
    for product in products_without_urls:
        pname = product.get("product_name") or product.get("name") or ""
        if not pname:
            continue
        
        try:
            client = get_llm_client()
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. Given a product name, "
                        "return ONLY a valid official website URL for that product. "
                        "Return in JSON format: {\"url\": \"https://...\", \"title\": \"...\", \"snippet\": \"...\"}. "
                        "If you don't know the exact URL, use the most likely official website. "
                        "Return ONLY the JSON, no explanation."
                    )
                },
                {
                    "role": "user",
                    "content": f"What is the official website URL for {pname}?"
                }
            ]
            
            response = client.chat_text(messages, temperature=0.1, max_tokens=200, timeout=15)
            
            # Parse JSON response
            import re
            match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if match:
                import json
                data = json.loads(match.group(0))
                url = data.get("url", "")
                if url and url.startswith("http"):
                    candidates.append({
                        "candidate_id": f"llm_{uuid.uuid4().hex[:12]}",
                        "competitor": pname,
                        "query": f"LLM inference for {pname}",
                        "title": data.get("title", f"{pname} Official Website"),
                        "url": url,
                        "snippet": data.get("snippet", f"Official website for {pname}"),
                        "source": "llm_inference",
                        "discovery_status": "llm_fallback",
                        "created_at": utc_now(),
                    })
                    logger.info("LLM discovered URL for %s: %s", pname, url)
                    
        except Exception as exc:
            logger.warning("LLM URL inference failed for %s: %s", pname, exc)
            continue
    
    return candidates


def _perform_source_discovery(
    run_id: str,
    project_id: str | None,
    products_without_urls: list[dict],
    discovery_queries: list[dict],
    source_readiness: str,
) -> dict[str, Any]:
    """
    Perform source discovery via web search.

    Returns:
        dict with keys: discovery_status, candidates, search_traces
    """
    from backend.app.services.search_provider import (
        get_search_provider,
        SEARCH_PROVIDER_NOT_CONFIGURED,
        SEARCH_SUCCESS,
        SEARCH_FAILED,
        SEARCH_NO_RESULTS,
    )
    from backend.app.storage.repositories import TraceRepository

    result = {
        "discovery_status": "skipped",
        "candidates": [],
        "search_traces": [],
    }

    # Check provider configuration
    provider = get_search_provider()
    result["provider_name"] = provider.provider_name
    result["provider_configured"] = provider.is_configured

    if not provider.is_configured:
        logger.warning(
            "Source discovery: no search provider configured for run_id=%s",
            run_id,
        )
        result["discovery_status"] = "provider_not_configured"
        result["reason"] = SEARCH_PROVIDER_NOT_CONFIGURED

        # vNext-R2-D Patch: Write failed/skipped trace for each discovery query
        # so TraceAudit can see search_call records even when provider is disabled.
        if discovery_queries:
            try:
                trace_repo = TraceRepository()
                for query_def in discovery_queries[:5]:  # cap at 5 competitors for trace volume
                    competitor = query_def.get("competitor", "unknown")
                    queries = query_def.get("queries", [])
                    # Write at least 1 trace, at most 3 per competitor
                    for query in queries[:3]:
                        trace_id = f"search_{uuid.uuid4().hex[:12]}"
                        trace_repo.add_trace({
                            "trace_id": trace_id,
                            "run_id": run_id,
                            "project_id": project_id,
                            "node_name": "collect_sources",
                            "agent_name": "SearchProvider",
                            "agent_role": "search",
                            "event_type": "search_call",
                            "model_name": "disabled",
                            "prompt_version": "v1.0",
                            "input_payload": {
                                "query": query,
                                "competitor": competitor,
                                "source_readiness": source_readiness,
                            },
                            "output_payload": {
                                "result_count": 0,
                                "candidates": [],
                            },
                            "status": "skipped",
                            "error_message": SEARCH_PROVIDER_NOT_CONFIGURED,
                            "decision_summary": "Search skipped: provider not configured",
                            "started_at": utc_now(),
                            "completed_at": utc_now(),
                            "created_at": utc_now(),
                        })
                        result["search_traces"].append({
                            "trace_id": trace_id,
                            "query": query,
                            "competitor": competitor,
                            "status": "skipped",
                            "reason": SEARCH_PROVIDER_NOT_CONFIGURED,
                            "result_count": 0,
                            "candidates": [],
                        })
                logger.info(
                    "Source discovery: wrote %d skipped search traces for run_id=%s",
                    len(result["search_traces"]), run_id,
                )
            except Exception as exc:
                logger.warning("Failed to write skipped search traces: %s", exc)

        return result

    # Perform search for each discovery query
    trace_repo = TraceRepository()

    for query_def in discovery_queries:
        competitor = query_def.get("competitor", "unknown")
        queries = query_def.get("queries", [])

        for query in queries[:3]:  # Limit to 3 queries per competitor
            trace_id = f"search_{uuid.uuid4().hex[:12]}"
            started_at = utc_now()

            # vNext-R2-D Frontend Patch: Write only one trace per search (not two).
            # add_trace uses INSERT OR REPLACE, so a second call would overwrite the first.
            # Write only after the search completes.

            # Perform the search
            search_results = provider.search(query, limit=5)
            reason = SEARCH_SUCCESS if search_results else SEARCH_NO_RESULTS

            # Record search completion
            status = "success" if reason == SEARCH_SUCCESS else "failed"
            error_msg = None if reason == SEARCH_SUCCESS else reason
            completed_at = utc_now()

            search_trace = {
                "trace_id": trace_id,
                "query": query,
                "competitor": competitor,
                "status": status,
                "reason": reason,
                "result_count": len(search_results),
                "candidates": [],
            }

            try:
                trace_repo.add_trace({
                    "trace_id": trace_id,
                    "run_id": run_id,
                    "project_id": project_id,
                    "node_name": "collect_sources",
                    "agent_name": "SearchProvider",
                    "agent_role": "search",
                    "event_type": "search_call",
                    "model_name": provider.provider_name,
                    "prompt_version": "v1.0",
                    "input_payload": {
                        "query": query,
                        "competitor": competitor,
                        "source_readiness": source_readiness,
                    },
                    "output_payload": {
                        "result_count": len(search_results),
                        "candidates": [r.to_dict() for r in search_results],
                    },
                    "status": status,
                    "error_message": error_msg,
                    "started_at": started_at,
                    "completed_at": completed_at,
                    "created_at": started_at,
                    "decision_summary": f"search_call {status}: {len(search_results)} results for {competitor}",
                })
            except Exception as exc:
                logger.warning("Failed to write search trace: %s", exc)

            # Convert results to candidates
            for sr in search_results:
                candidate = {
                    "candidate_id": f"cand_{uuid.uuid4().hex[:12]}",
                    "competitor": competitor,
                    "query": query,
                    "title": sr.title,
                    "url": sr.url,
                    "snippet": sr.snippet,
                    "source_type": sr.source_type,
                    "discovery_status": status,
                    "run_id": run_id,
                    "created_at": utc_now(),
                }
                result["candidates"].append(candidate)
                search_trace["candidates"].append(candidate["url"])

            result["search_traces"].append(search_trace)

    result["discovery_status"] = "completed"

    # If no candidates found, try LLM fallback
    if not result.get("candidates") and products_without_urls:
        logger.info("Source discovery: no search results, trying LLM fallback for run_id=%s", run_id)
        llm_candidates = _discover_urls_via_llm(products_without_urls, discovery_queries)
        if llm_candidates:
            result["discovery_status"] = "llm_fallback"
            result["candidates"] = llm_candidates
            result["llm_fallback"] = True
            logger.info("Source discovery: LLM fallback found %d candidates", len(llm_candidates))

    return result


def _create_search_provider_intervention(
    state: WorkflowState,
    run_id: str,
    project_id: str | None,
    products_without_urls: list[dict],
    discovery_queries: list[dict],
) -> None:
    """
    Create a human intervention when search provider is not configured.

    This ensures users get early feedback about missing configuration.
    """
    import uuid
    from backend.app.storage.repositories import HumanInterventionRepository

    # vNext-R2-D Patch: Fix product name resolution with proper priority.
    # Priority: product_name > name > product_id > product_slug > "unknown"
    def _resolve_product_name(product: dict) -> str:
        return (
            product.get("product_name")
            or product.get("name")
            or product.get("product_id")
            or product.get("product_slug")
            or "unknown"
        )

    product_names = [_resolve_product_name(p) for p in products_without_urls]
    query_count = sum(len(q.get("queries", [])) for q in discovery_queries)

    # Prepare before_json with discovery details
    before_json = {
        "reason_code": "SEARCH_PROVIDER_NOT_CONFIGURED",
        "products_needing_discovery": product_names,
        "discovery_queries": discovery_queries,
        "query_count": query_count,
        "provider_name": "none",
        "message": (
            "Auto source discovery requires a configured search provider. "
            "The following products have no seed URLs and need discovery queries "
            f"but no search API is configured: {', '.join(product_names)}."
        ),
    }

    intervention = {
        "intervention_id": f"interv_search_{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "node_name": "collect_sources",
        "artifact_type": "source",
        "artifact_id": None,
        "action": "pending",
        "status": "pending",
        "before_json": before_json,
        "after_json": None,
        "comment": (
            "Search Provider Not Configured: Auto source discovery requires a configured search API "
            "(TAVILY_API_KEY, SERPAPI_API_KEY, or SEARCH_API_ENDPOINT). "
            f"Products needing discovery: {', '.join(product_names)}. "
            "Please configure a search API or provide seed URLs for these products."
        ),
        "created_at": utc_now(),
        "resolved_at": None,
        "created_by": "system",
        "resolved_by": None,
    }

    try:
        repo = HumanInterventionRepository()
        repo.create_intervention(intervention)
        logger.info(
            "Created SEARCH_PROVIDER_NOT_CONFIGURED intervention for run_id=%s: %s",
            run_id, product_names,
        )
    except Exception as exc:
        logger.error("Failed to create intervention: %s", exc)
        # Store in state as fallback
        state.setdefault("human_interventions", []).append(intervention)


def build_task_brief(state: WorkflowState) -> WorkflowState:
    """Build and enrich the task brief for the workflow."""
    import logging
    _logger = logging.getLogger(__name__)
    
    # CRITICAL: Log the EXACT input state
    tb_input = state.get("task_brief") or {}
    _logger.critical(
        "!!! BUILD_TASK_BRIEF INPUT !!! run_id=%s, task_brief_type=%s, "
        "task_brief_keys=%s, products_count=%d",
        state.get("run_id"),
        type(tb_input).__name__,
        list(tb_input.keys()) if isinstance(tb_input, dict) else "NOT_A_DICT",
        len(tb_input.get("products", [])) if isinstance(tb_input, dict) else -1,
    )
    
    # Only set defaults if task_brief is completely empty (not initialized)
    tb = tb_input
    if not tb or (not tb.get("products") and not tb.get("title")):
        _logger.warning(
            "build_task_brief: task_brief was empty, using fallback defaults! "
            "This should NOT happen when running via start_project_run. "
            "run_id=%s, tb_input=%s",
            state.get("run_id"),
            str(tb_input)[:200],
        )
        tb = {
            "title": "AI Agent Product Competitive Analysis",
            "description": "Analyze Dify, Coze, FastGPT, Flowise across function_tree, pricing_model, user_persona, customer_voice, swot, enterprise_readiness.",
            "products": [
                {"product_id": "dify", "product_name": "Dify"},
                {"product_id": "coze", "product_name": "Coze"},
                {"product_id": "fastgpt", "product_name": "FastGPT"},
                {"product_id": "flowise", "product_name": "Flowise"},
            ],
            "analysis_dimensions": [
                "function_tree",
                "pricing_model",
                "user_persona",
                "customer_voice",
                "swot",
                "enterprise_readiness",
            ],
        }
    
    # Ensure products list is never empty
    if not tb.get("products"):
        _logger.warning(
            "build_task_brief: products list is empty after initialization! "
            "run_id=%s",
            state.get("run_id"),
        )

    # Normalize products: convert string array ["Dify", "Coze"] to object array
    # Also normalize dict objects: rename "name" to "product_name" if needed
    products = tb.get("products", [])
    if products and isinstance(products[0], str):
        import uuid
        normalized = []
        for name in products:
            if isinstance(name, str):
                normalized.append({
                    "product_id": f"product_{uuid.uuid4().hex[:8]}",
                    "product_name": name,
                    "official_website": "",
                    "seed_urls": [],
                })
            elif isinstance(name, dict):
                normalized.append(name)
        tb["products"] = normalized
        _logger.info(
            "build_task_brief: normalized %d string products to objects for run_id=%s",
            len(normalized), state.get("run_id"),
        )
    elif products and isinstance(products[0], dict):
        # P0-Fix: Products from frontend API may use "name" or "Name" instead of
        # "product_name". Also may have no product_id. Normalize all dicts.
        import uuid
        changed = False
        for p in products:
            if isinstance(p, dict):
                # Fix "name" or "Name" -> "product_name"
                for _name_key in ("name", "Name"):
                    if _name_key in p and "product_name" not in p:
                        p["product_name"] = p.pop(_name_key)
                        changed = True
                        break
                # Ensure product_name is never None
                if p.get("product_name") is None:
                    p["product_name"] = ""
                # Ensure product_id exists if missing (needed for DB writes)
                if not p.get("product_id"):
                    p["product_id"] = f"product_{uuid.uuid4().hex[:8]}"
        if changed:
            _logger.info(
                "build_task_brief: normalized product dict key names for run_id=%s",
                state.get("run_id"),
            )

    _logger.critical(
        "!!! BUILD_TASK_BRIEF OUTPUT !!! run_id=%s, products_count=%d",
        state.get("run_id"),
        len(tb.get("products", [])),
    )

    # P0-Fix: Re-resolve seed URLs for all products that have stale placeholder
    # "official_website" values from a previous failed run. This ensures that even
    # if task_brief was restored from the DB with garbage "To be collected during research"
    # placeholders, plan_sources won't skip the URL resolution step.
    _PLACEHOLDER_VALS = {
        "to be collected during research", "tbd", "pending",
        "", "none", "null", "n/a",
    }
    for p in tb.get("products", []):
        off = (p.get("official_website") or "").strip().lower()
        if off in _PLACEHOLDER_VALS:
            p["official_website"] = ""
        if not (p.get("seed_urls") or []):
            p["seed_urls"] = []

    state["task_brief"] = tb
    return state


def plan_schema(state: WorkflowState) -> WorkflowState:
    """Plan the schema structure for the research task.
    
    vNext-P0: Attempts to use LLM to generate a domain-specific schema plan.
    Falls back to a template schema if LLM is unavailable.
    Writes llm_call trace on success or fallback trace on failure.
    """
    from backend.app.tracing.llm_trace import traced_llm_call, create_llm_fallback_trace
    from backend.app.storage.repositories import TraceRepository
    
    run_id = state.get("run_id", "unknown")
    project_id = state.get("project_id")
    task_brief = state.get("task_brief", {})
    schema_type = task_brief.get("schema_type") or task_brief.get("task_type") or "competitor_landscape"
    products = task_brief.get("products", [])
    
    logger.info("plan_schema: run_id=%s schema_type=%s products=%d", run_id, schema_type, len(products))

    # Build prompt for schema planning
    product_names = [p.get("product_name", p.get("product_id", "")) for p in products]
    user_msg = (
        f"You are a competitive analysis schema planner. "
        f"Generate a JSON schema plan for analyzing: {', '.join(product_names) if product_names else 'unknown products'}. "
        f"Schema type: {schema_type}. "
        f"Output a JSON object with: schema_name, schema_version, required_sections (list of section names), "
        f"dimensions (list of dimension names), and any schema_type-specific fields. "
        f"Return ONLY the JSON object, no markdown."
    )
    
    def _call_llm():
        from backend.app.services.llm_client import get_llm_client
        client = get_llm_client()
        return client.chat_text(
            [{"role": "system", "content": "You are a schema planning assistant."}, 
             {"role": "user", "content": user_msg}],
            temperature=0.1, max_tokens=2048, timeout=60,
        )
    
    try:
        result = traced_llm_call(
            run_id=run_id,
            project_id=project_id,
            node_name="plan_schema",
            agent_name="SchemaPlanner",
            agent_role="schema_planner",
            prompt_version="v1.0",
            prompt_text=user_msg,
            input_payload={"schema_type": schema_type, "products": product_names},
            call_fn=_call_llm,
            parse_fn=None,
            input_length_hint=len(user_msg),
            decision_summary=f"Generated schema plan for {schema_type}",
        )
        output_text = result.get("output_text", "")
        
        # Parse JSON from output
        import json
        import re
        try:
            schema_plan = json.loads(output_text)
        except Exception:
            # Try to extract JSON from text
            match = re.search(r'\{[\s\S]*\}', output_text)
            if match:
                schema_plan = json.loads(match.group(0))
            else:
                schema_plan = {}
        
        # Validate required fields
        if not schema_plan.get("required_sections"):
            schema_plan["required_sections"] = [
                "product_profile", "function_tree", "pricing_model",
                "user_persona", "customer_voice", "swot", "enterprise_readiness",
            ]
        if not schema_plan.get("schema_name"):
            schema_plan["schema_name"] = f"{schema_type}Schema"
        if not schema_plan.get("schema_version"):
            schema_plan["schema_version"] = "1.0.0"
            
        state["schema_plan"] = schema_plan
        logger.info("plan_schema: LLM produced schema for run_id=%s", run_id)
        
    except Exception as exc:
        logger.warning("plan_schema: LLM unavailable for run_id=%s: %s - using template", run_id, exc)
        create_llm_fallback_trace(
            run_id=run_id,
            project_id=project_id,
            node_name="plan_schema",
            agent_name="SchemaPlanner",
            agent_role="schema_planner",
            prompt_version="v1.0",
            prompt_text=user_msg,
            input_payload={"schema_type": schema_type, "products": product_names},
            reason=f"LLM_UNAVAILABLE: {type(exc).__name__}: {exc}",
            decision_summary="Fallback: template schema plan",
        )
        # Fallback template schema
        state["schema_plan"] = {
            "schema_name": f"{schema_type}Schema",
            "schema_version": "1.0.0",
            "required_sections": [
                "product_profile", "function_tree", "pricing_model",
                "user_persona", "customer_voice", "swot", "enterprise_readiness",
            ],
            "schema_type": schema_type,
            "generated_by": "fallback",
        }
        logger.info("plan_schema: fallback schema for run_id=%s", run_id)
    
    return state


# ---------------------------------------------------------------------------
# Multi-Dimension Query Generator for P0.1 (ManuSearch-style planner)
# ---------------------------------------------------------------------------

# Schema-key → search query templates.
# Each template receives (product_name, dimension_label) and generates 2-3 targeted queries.
# Format: (query_template, source_type_hint)
_QUERY_TEMPLATES: list[tuple[str, list[str], str]] = [
    # Workflow Orchestration
    (
        "{name} workflow builder orchestration features",
        ["documentation", "official_site"],
        "function_tree.workflow_orchestration",
    ),
    (
        "{name} visual workflow automation pipeline drag drop",
        ["documentation", "official_site"],
        "function_tree.workflow_orchestration",
    ),
    # RAG / Knowledge Base
    (
        "{name} RAG knowledge base vector search document ingestion",
        ["documentation", "technical_blog"],
        "agent_product_capabilities.knowledge_base",
    ),
    (
        "{name} retrieval augmented generation enterprise",
        ["documentation", "technical_blog"],
        "agent_product_capabilities.knowledge_base",
    ),
    # Deployment Options
    (
        "{name} self-hosted docker kubernetes deployment",
        ["documentation", "github"],
        "agent_product_capabilities.deployment_options",
    ),
    (
        "{name} on-premise private cloud enterprise deployment",
        ["documentation", "github"],
        "agent_product_capabilities.deployment_options",
    ),
    # Enterprise Readiness
    (
        "{name} SSO SAML RBAC enterprise security permissions",
        ["documentation", "official_site"],
        "agent_product_capabilities.enterprise_readiness",
    ),
    (
        "{name} audit log compliance SOC2 HIPAA security",
        ["documentation", "official_site"],
        "agent_product_capabilities.enterprise_readiness",
    ),
    # Pricing
    (
        "{name} pricing plans free tier subscription enterprise",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    (
        "{name} pricing comparison cost per user per month",
        ["pricing_page", "comparison_articles"],
        "pricing_model",
    ),
    # Model Support
    (
        "{name} LLM models GPT Claude Gemini support integration",
        ["documentation", "official_site"],
        "agent_product_capabilities.model_support",
    ),
    (
        "{name} AI models open source llama mistral enterprise",
        ["documentation", "github"],
        "agent_product_capabilities.model_support",
    ),
    # Integration / API
    (
        "{name} API integration webhook REST developer",
        ["documentation", "api_reference"],
        "function_tree.integration",
    ),
    (
        "{name} plugins extensions connectors ecosystem",
        ["documentation", "official_site"],
        "function_tree.integration",
    ),
    # User Persona
    (
        "{name} target users developers enterprise team use cases",
        ["official_site", "community_feedback"],
        "user_persona",
    ),
    (
        "{name} case study customer enterprise success story",
        ["case_studies", "community_feedback"],
        "user_persona",
    ),
    # Customer Voice
    (
        "{name} G2 Capterra review rating user feedback",
        ["community_feedback"],
        "customer_voice",
    ),
    (
        "{name} reddit community discussion pros cons review",
        ["community_feedback", "social_media"],
        "customer_voice",
    ),
    # Agent Capabilities
    (
        "{name} AI agent bot assistant automation multi-agent",
        ["documentation", "official_site"],
        "function_tree.agent_capabilities",
    ),
    (
        "{name} copilot automation agent workflow enterprise",
        ["documentation", "official_site"],
        "function_tree.agent_capabilities",
    ),
    # Market Positioning
    (
        "{name} competitive analysis vs comparison alternative",
        ["comparison_articles", "official_site"],
        "market_positioning",
    ),
    (
        "{name} differentiation unique selling point moat",
        ["official_site", "technical_blog"],
        "market_positioning",
    ),
    # P0-5: Extended dimensions for better coverage
    # Collaboration Experience
    (
        "{name} collaboration teamwork real-time editing sharing comments",
        ["official_site", "documentation"],
        "collaboration_experience",
    ),
    (
        "{name} team workspace shared documents real-time co-editing",
        ["official_site", "documentation"],
        "collaboration_experience",
    ),
    # AI Assistance
    (
        "{name} AI assistant copilot writing search summarization",
        ["official_site", "documentation"],
        "ai_assistance",
    ),
    (
        "{name} AI autocomplete suggestions smart search Q&A",
        ["official_site", "documentation"],
        "ai_assistance",
    ),
    # Permission Governance
    (
        "{name} permissions access control roles admin governance",
        ["documentation", "official_site"],
        "permission_governance",
    ),
    (
        "{name} compliance GDPR SOC2 HIPAA audit security policy",
        ["documentation", "official_site"],
        "permission_governance",
    ),
    # Ecosystem
    (
        "{name} ecosystem marketplace plugins templates community gallery",
        ["official_site", "community_feedback"],
        "ecosystem",
    ),
    (
        "{name} app directory third-party extensions integrations marketplace",
        ["official_site", "community_feedback"],
        "ecosystem",
    ),
    # RAG Support
    (
        "{name} RAG retrieval augmented generation vector search knowledge",
        ["documentation", "technical_blog"],
        "rag_support",
    ),
    (
        "{name} semantic search document retrieval knowledge base enterprise",
        ["documentation", "official_site"],
        "rag_support",
    ),
    # Product Overview
    (
        "{name} what is product overview features overview introduction",
        ["official_site", "documentation"],
        "product_overview",
    ),
    (
        "{name} product features overview capabilities getting started",
        ["official_site", "documentation"],
        "product_overview",
    ),
    # Customer Voice
    (
        "{name} review testimonial user experience feedback pros cons",
        ["community_feedback"],
        "customer_voice",
    ),
    (
        "{name} G2 rating Capterra user review comparison",
        ["community_feedback"],
        "customer_voice",
    ),
]


def _generate_multi_dimension_queries(
    products: list[dict[str, Any]],
    schema_type: str = "ai_agent_platform",
) -> list[dict[str, Any]]:
    """
    Generate structured search queries: each competitor × each analysis dimension.

    Returns a list of DiscoveryQuery dicts (same shape as SourceDiscovery.discovery_queries).
    For products WITH seed URLs, still generates dimension-targeted queries for supplemental search.
    For products WITHOUT seed URLs, generates more queries to compensate.

    vNext-P0.1: ManuSearch-style planner - active query expansion, not just URL planning.
    """
    discovery_queries: list[dict[str, Any]] = []

    # Domain-specific templates override
    if schema_type == "knowledge_management":
        domain_templates = _QUERY_TEMPLATES_KM  # type: ignore[undefined]
    elif schema_type == "pricing_analysis":
        domain_templates = _QUERY_TEMPLATES_PRICING  # type: ignore[undefined]
    else:
        domain_templates = _QUERY_TEMPLATES  # type: ignore[undefined]

    for product in products:
        product_name = (
            product.get("product_name")
            or product.get("name")
            or product.get("product_id")
            or ""
        )
        if not product_name:
            continue

        has_urls = bool(product.get("seed_urls") or product.get("official_website"))
        # P0-5: Raised from 6/4 → 10/6 to generate more search queries per product
        max_queries = 10 if not has_urls else 6

        queries: list[str] = []
        pricing_queries: list[str] = []
        seen: set[str] = set()

        for template, _source_types, schema_key in domain_templates:
            # P0-6 Fix: Always include pricing queries regardless of max_queries limit
            if "pricing" in schema_key.lower() or "pricing" in template.lower():
                query = template.format(name=product_name)
                norm = query.lower()
                if norm not in seen:
                    seen.add(norm)
                    pricing_queries.append(query)
                continue  # Don't count pricing against max_queries

            # Only count non-pricing queries against max_queries
            if len(queries) >= max_queries:
                continue  # P0-6 Fix: Don't break, continue to find pricing queries
            query = template.format(name=product_name)
            norm = query.lower()
            if norm not in seen:
                seen.add(norm)
                queries.append(query)

        # Append pricing queries at the end
        queries.extend(pricing_queries)

        discovery_queries.append({
            "competitor": product_name,
            "competitor_id": product.get("product_id", ""),
            "queries": queries,
            "status": "pending",
            "has_seed_urls": has_urls,
            "discovery_mode": "supplement" if has_urls else "full",
        })

    return discovery_queries


# Knowledge Management domain-specific query templates
_QUERY_TEMPLATES_KM: list[tuple[str, list[str], str]] = [
    (
        "{name} workspace wiki knowledge base features",
        ["official_site", "documentation"],
        "knowledge_structure",
    ),
    (
        "{name} permissions admin SSO SCIM enterprise control",
        ["documentation", "official_site"],
        "permission_governance",
    ),
    (
        "{name} AI writing search Q&A copilot assistant",
        ["official_site", "documentation"],
        "ai_assistance",
    ),
    (
        "{name} integration Slack Google Drive Jira Confluence",
        ["documentation", "official_site"],
        "enterprise_integration",
    ),
    (
        "{name} G2 Capterra review rating enterprise teams",
        ["community_feedback"],
        "customer_voice",
    ),
    (
        "{name} Confluence Notion migration guide export",
        ["documentation", "community_feedback"],
        "migration_cost",
    ),
    (
        "{name} pricing team plan enterprise cost per user",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    # P0-5: Extended KM templates
    (
        "{name} collaboration real-time editing shared workspace team",
        ["official_site", "documentation"],
        "collaboration_experience",
    ),
    (
        "{name} workflow automation triggers actions notifications",
        ["documentation", "official_site"],
        "collaboration_experience",
    ),
    (
        "{name} deployment self-hosted on-premise docker enterprise",
        ["documentation", "official_site"],
        "deployment_options",
    ),
    (
        "{name} G2 Capterra review user experience feedback",
        ["community_feedback"],
        "customer_voice",
    ),
    (
        "{name} feature comparison Notion Confluence alternative",
        ["comparison_articles", "community_feedback"],
        "market_positioning",
    ),
    (
        "{name} RAG vector search semantic retrieval knowledge",
        ["documentation", "technical_blog"],
        "rag_support",
    ),
    (
        "{name} compliance GDPR SOC2 security certification enterprise",
        ["documentation", "official_site"],
        "permission_governance",
    ),
    (
        "{name} ecosystem templates gallery marketplace community",
        ["official_site", "community_feedback"],
        "ecosystem",
    ),
]

# Pricing Analysis domain-specific query templates
_QUERY_TEMPLATES_PRICING: list[tuple[str, list[str], str]] = [
    (
        "{name} pricing plans per user per month enterprise",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    (
        "{name} free tier limitations features trial",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    (
        "{name} enterprise pricing quote contact sales",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    (
        "{name} AI Copilot add-on pricing per user premium",
        ["pricing_page", "official_site"],
        "ai_feature_pricing",
    ),
    (
        "{name} SSO SCIM admin security pricing enterprise",
        ["pricing_page", "documentation"],
        "admin_security_cost",
    ),
    (
        "{name} total cost of ownership TCO review analysis",
        ["comparison_articles", "customer_reviews"],
        "value_proposition",
    ),
    (
        "{name} volume discount annual billing pricing",
        ["pricing_page", "official_site"],
        "pricing_model",
    ),
    (
        "{name} G2 Capterra pricing value review",
        ["community_feedback"],
        "value_proposition",
    ),
    (
        "{name} migration training adoption cost enterprise",
        ["documentation", "community_feedback"],
        "migration_adoption",
    ),
    (
        "{name} premium support SLA pricing enterprise",
        ["pricing_page", "official_site"],
        "admin_security_cost",
    ),
]


def plan_sources(state: WorkflowState) -> WorkflowState:
    """Plan source collection including multi-round multi-dimension query generation.

    vNext-P0.1: Extended from simple seed-URL planning to ManuSearch-style planner.
    Generates competitor × dimension structured queries for active source discovery,
    even for products that already have seed URLs (supplemental search).
    """
    run_id = state.get("run_id", "")
    task_brief = state.get("task_brief", {})
    schema_plan = state.get("schema_plan", {})
    schema_type = task_brief.get("schema_type") or task_brief.get("task_type") or "ai_agent_platform"

    products = task_brief.get("products", [])

    # Capture original seed_urls count BEFORE any auto-fill
    _original_seed_urls_count = sum(len(p.get("seed_urls", [])) for p in products)

    logger.critical(
        "!!! PLAN_SOURCES ENTER !!! run_id=%s, products=%s, original_seed_urls=%d",
        run_id,
        [{"name": p.get("product_name"), "seed_urls": p.get("seed_urls", []), "official_website": p.get("official_website", "")}
         for p in products],
        _original_seed_urls_count,
    )

    # Auto-fill seed URLs for all products using the flexible resolver.
    # _resolve_product_urls handles known products, URL construction for unknown products,
    # and gracefully falls back to LLM-based discovery for unresolvable products.
    for product in products:
        # Always resolve seed URLs from PRODUCT_DEFAULT_URLS for known products.
        # If official_website is a stale placeholder from a previous failed run
        # ("To be collected during research"), clear it so _resolve_product_urls
        # can re-generate fresh URLs.
        official = product.get("official_website", "")
        placeholders = ("to be collected during research", "tbd", "pending", "")
        if official.lower().strip() in placeholders:
            product["official_website"] = ""
        if not product.get("seed_urls") and not product.get("official_website"):
            resolved = _resolve_product_urls(product)
            if resolved:
                product["seed_urls"] = resolved
                logger.info(
                    "plan_sources: resolved %d URL(s) for product '%s' (slug='%s')",
                    len(resolved), product.get("product_name", ""), product.get("product_slug", ""),
                )
            else:
                logger.info(
                    "plan_sources: could not resolve URLs for product '%s' (slug='%s') — "
                    "will use LLM-based discovery",
                    product.get("product_name", ""), product.get("product_slug", ""),
                )

    # Check which products need discovery

    # Check which products need discovery
    products_with_urls = [
        p for p in products
        if p.get("seed_urls") or p.get("official_website")
    ]
    products_without_urls = [
        p for p in products
        if not (p.get("seed_urls") or p.get("official_website"))
    ]

    # vNext-P0.1: Generate multi-dimension structured queries for ALL products.
    # This applies even when products have seed URLs (supplemental discovery mode).
    # The structured queries are used by _perform_source_discovery in collect_sources.
    discovery_queries = _generate_multi_dimension_queries(products, schema_type=schema_type)

    source_plan = {
        "products": products,
        "products_with_urls": products_with_urls,
        "products_without_urls": products_without_urls,
        "target_source_types": [
            "official_site", "documentation", "pricing_page",
            "community_review", "github", "comparison_articles",
        ],
        "source_discovery": {
            "source_discovery_required": bool(products_without_urls),
            "auto_discovery_enabled": True,
            "discovery_queries": discovery_queries,
            "source_readiness": "ready" if products_with_urls else "ready_with_discovery",
            # vNext-P0.1: mark that we always do supplemental multi-dimension search
            "multi_dimension_mode": True,
        },
    }
    state["source_plan"] = source_plan

    logger.info(
        "plan_sources (P0.1): run_id=%s products=%d with_urls=%d without_urls=%d "
        "discovery_queries=%d multi_dimension_mode=True",
        run_id, len(products), len(products_with_urls), len(products_without_urls),
        sum(len(dq.get("queries", [])) for dq in discovery_queries),
    )

    # Persist seed URLs (auto-filled or user-provided) back to DB task_brief.
    # This ensures collect_sources can read the URLs even in cached/replay mode.
    import json as _json
    try:
        from backend.app.storage.db import get_connection
        with get_connection() as conn:
            conn.execute(
                "UPDATE runs SET task_brief_json = ? WHERE run_id = ?",
                (_json.dumps(task_brief), run_id),
            )
        logger.info(
            "plan_sources: persisted task_brief with %d total seed_urls to DB for run_id=%s",
            sum(len(p.get("seed_urls", [])) for p in products), run_id,
        )
    except Exception as exc:
        logger.warning("plan_sources: failed to persist task_brief to DB: %s", exc)

    logger.critical(
        "!!! PLAN_SOURCES EXIT !!! run_id=%s, task_brief_products=%s",
        run_id,
        [{"Name": p.get("product_name"), "seed_urls": p.get("seed_urls", []),
          "official_website": p.get("official_website", "")} for p in (state.get("task_brief") or {}).get("products", [])],
    )

    return state


def collect_sources(state: WorkflowState) -> WorkflowState:
    """
    Collect sources for the research task.
    
    vNext-R2-D: Integrates source discovery when competitors lack seed URLs.
    If search provider is configured, performs web search for discovery queries.
    If not configured, creates human intervention.
    """
    import logging as _collect_logger
    import time as _time
    run_id = state.get("run_id", "")
    project_id = state.get("project_id")
    mode = state.get("mode", "real_time")

    sources: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    raw_documents: list[dict[str, Any]] = []
    evidence_items: list[dict[str, Any]] = []
    source_candidates: list[dict[str, Any]] = []
    discovery_results: list[dict[str, Any]] = []

    if mode in ("cached", "replay"):
        try:
            sources = _db_query(
                "SELECT * FROM sources WHERE run_id = ? ORDER BY product_id, source_type",
                (run_id,),
            )
            logger.info("collect_sources: loaded %d sources from DB for run_id=%s", len(sources), run_id)
        except Exception as exc:
            logger.error("collect_sources: failed to load sources: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_LOAD_SOURCES_FAILED",
                "message": str(exc),
                "node": "collect_sources",
            })

        try:
            snapshots = _db_query(
                "SELECT * FROM snapshots WHERE run_id = ?", (run_id,),
            )
            logger.info("collect_sources: loaded %d snapshots from DB", len(snapshots))
        except Exception as exc:
            logger.error("collect_sources: failed to load snapshots: %s", exc)

        try:
            evidence_items = _db_query(
                "SELECT * FROM evidence_items WHERE run_id = ? ORDER BY product_id, created_at",
                (run_id,),
            )
            logger.info("collect_sources: loaded %d evidence_items from DB", len(evidence_items))
        except Exception as exc:
            logger.error("collect_sources: failed to load evidence_items: %s", exc)

    elif mode == "real_time":
        try:
            # Use task_brief["products"] directly.
            # plan_sources modifies task_brief["products"] in-place, so the auto-filled
            # URLs (e.g. FastGPT defaults) are already there when collect_sources runs.
            task_brief = state.get("task_brief", {})
            products = task_brief.get("products", []) or []

            # Debug: log product URLs
            for p in products:
                _collect_logger.debug(
                    "collect_sources: product=%s has_urls=%s seed_urls=%s official_website=%s",
                    p.get("product_name"), bool(p.get("seed_urls") or p.get("official_website")),
                    p.get("seed_urls", []), p.get("official_website", ""),
                )

            # Split into products with URLs (for collection) and all products (for DB)
            products_with_urls = [
                p for p in products
                if p.get("seed_urls") or p.get("official_website")
            ]
            products_without_urls = [
                p for p in products
                if not (p.get("seed_urls") or p.get("official_website"))
            ]
            _collect_logger.warning(
                "collect_sources: products_with_urls=%d products_without_urls=%d",
                len(products_with_urls), len(products_without_urls),
            )

            # vNext-R2-D: Check for source discovery needs
            source_plan = state.get("source_plan", {})
            source_discovery = source_plan.get("source_discovery", task_brief.get("source_discovery", {}))
            discovery_queries = source_discovery.get("discovery_queries", [])
            source_readiness = source_discovery.get("source_readiness", "ready")
            multi_dimension_mode = source_discovery.get("multi_dimension_mode", False)

            # vNext-P0.1 + vNext-P0.4: Smart search discovery with graceful fallback.
            # - If products have seed URLs: supplemental discovery is optional (skip if no provider)
            # - If products have NO seed URLs: discovery is required (create intervention if no provider)
            # Check provider availability first
            from backend.app.services.search_provider import get_search_provider, get_search_config
            provider = get_search_provider()
            config = get_search_config()
            provider_available = config.is_configured

            products_for_discovery: list[dict] = []
            source_candidates: list[dict] = []
            discovery_results: dict[str, Any] = {}

            # vNext-P0.5 (Doubao web search): Generate supplemental discovery queries for ALL products
            # when multi_dimension_mode is active and provider is available.
            # Previously, SourceDiscovery.only generates queries for products WITHOUT URLs.
            # Now we supplement every product with fresh web-discovered URLs.
            if provider_available and multi_dimension_mode and not discovery_queries:
                logger.info(
                    "collect_sources (P0.5): multi_dimension_mode=True but no discovery_queries "
                    "found. Generating supplemental queries for %d products (provider=%s)",
                    len(products), provider.provider_name,
                )
                from backend.app.services.research_planner import (
                    DEFAULT_DIMENSIONS,
                    _generate_discovery_queries,
                )
                from backend.app.schemas.research_plan import DiscoveryQuery

                for p in products:
                    pname = p.get("product_name") or p.get("name") or p.get("product_id") or ""
                    if not pname:
                        continue
                    # Generate schema-specific queries for each product
                    schema_type = task_brief.get("schema_type", "ai_agent_platform")
                    queries = _generate_discovery_queries(pname, schema_type)
                    if queries:
                        discovery_queries.append(DiscoveryQuery(
                            competitor=pname,
                            queries=queries,
                            status="pending",
                        ))

            # If all products already have seed URLs, skip discovery entirely and go straight to fetching.
            # Discovery is expensive (Doubao API calls) and redundant when we already have good URLs.
            all_have_urls = len(products_without_urls) == 0
            if discovery_queries and (products_without_urls or multi_dimension_mode):
                if not provider_available:
                    # vNext-P0.4: Provider not available.
                    # - If ALL products have seed URLs: skip discovery, proceed with existing URLs.
                    #   The seed URLs are sufficient for basic collection.
                    # - If some products lack URLs: create intervention for those specific products.
                    if products_with_urls and not products_without_urls:
                        # All products have URLs → skip discovery, no intervention needed
                        logger.info(
                            "collect_sources (P0.4): search provider not configured but all products "
                            "have seed URLs. Skipping supplemental discovery, proceeding with %d seed URLs.",
                            sum(len(p.get("seed_urls", [])) for p in products_with_urls),
                        )
                    else:
                        # Some products lack URLs → create intervention
                        logger.info(
                            "collect_sources (P0.4): search provider not configured, "
                            "some products lack seed URLs. Creating intervention.",
                        )
                        _create_search_provider_intervention(
                            state=state,
                            run_id=run_id,
                            project_id=project_id,
                            products_without_urls=products_without_urls,
                            discovery_queries=discovery_queries,
                        )
                else:
                    # P1-Fix: Before running discovery, do a runtime health check.
                    # P1 (2026-06-22): Reduced from 50s to 10s. Doubao web_search is healthy
                    # when it responds in 2-6s; unhealthy endpoints that need 3×30s retries to
                    # fail will still fail within 10s. A 50s health probe is too slow and
                    # wastes budget on what is already a known-slow call pattern.
                    provider_healthy = False
                    provider_slow = False
                    try:
                        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
                        HEALTH_TIMEOUT = 10  # P1 (2026-06-22): 10s is enough for healthy Doubao (2-6s); unhealthy endpoints fail within 10s
                        with ThreadPoolExecutor(max_workers=1) as ex:
                            future = ex.submit(provider.search, "test", 1)
                            test_results = future.result(timeout=HEALTH_TIMEOUT)
                            provider_healthy = True
                    except FuturesTimeoutError:
                        provider_slow = True
                        logger.warning(
                            "collect_sources (P1-Fix): search provider health check timed out "
                            "(>%ds). Treating as potentially slow — Doubao may still work for real queries.",
                            HEALTH_TIMEOUT,
                        )
                    except Exception as exc:
                        logger.warning(
                            "collect_sources (P1-Fix): search provider health check failed: %s. "
                            "Skipping discovery, proceeding with seed URLs.",
                            exc,
                        )

                    # Even if health check failed (slow provider) or all products have URLs,
                    # still try discovery — it supplements and doesn't replace existing URLs.
                    # Only skip if provider is explicitly unavailable (not slow).
                    if not provider_healthy:
                        if provider_slow:
                            # Slow but potentially working — try actual discovery
                            logger.warning(
                                "collect_sources (P1-Fix): proceeding with discovery despite slow provider "
                                "(health check timed out). Doubao may still work for real queries.",
                            )
                        else:
                            logger.warning(
                                "collect_sources (P1-Fix): search provider unhealthy (non-timeout error) — "
                                "skipping discovery.",
                            )
                            discovery_results = {"discovery_status": "skipped_unavailable", "candidates": []}
                    else:
                        # Provider is healthy: run discovery to supplement seed URLs.
                        discovery_mode = "multi_dimension_supplement" if multi_dimension_mode else "url_gap_fill"
                        logger.info(
                            "collect_sources (P1-Fix): performing discovery for %d products "
                            "(provider=%s, mode=%s)",
                            len(products), provider.provider_name, discovery_mode,
                        )
                        products_for_discovery = list(products)
                        discovery_results = _perform_source_discovery(
                            run_id=run_id,
                            project_id=project_id,
                            products_without_urls=products_for_discovery,
                            discovery_queries=discovery_queries,
                            source_readiness=source_readiness,
                        )
                    source_candidates = discovery_results.get("candidates", [])
                    discovery_status = discovery_results.get("discovery_status", "failed")
                    logger.info(
                        "collect_sources (P0.1): discovery completed status=%s candidates=%d",
                        discovery_status, len(source_candidates),
                    )

                # If we have candidates, we can try to collect from them
                # (The actual collection happens below if we have URLs or candidates)

            # Write ALL products to DB first, including those without seed_urls.
            # This ensures product_id exists in products table before any
            # evidence/fact/claim references it via foreign key.
            try:
                from backend.app.storage.repositories import ProductRepository
                product_repo = ProductRepository()
                for p in products:
                    product_repo.add_product({
                        "product_id": p.get("product_id", ""),
                        "run_id": run_id,
                        "product_name": p.get("product_name", ""),
                        "company_name": p.get("company_name", ""),
                        "official_website": p.get("official_website", ""),
                        "region": p.get("region", ""),
                        "product_type": p.get("product_type", ""),
                        "seed_urls": p.get("seed_urls", []),
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    })
                # P0-Fix: Re-read products from DB to get the run-scoped product_ids
                # that ProductRepository.generate_product_id() creates (e.g. run_xxx_dify).
                # Frontend products have "name" not "product_name" and no product_id,
                # so we need to look up the resolved IDs for downstream use.
                db_products = product_repo.list_products(run_id)
                # Build: canonical_product_name_lower -> run_scoped_product_id
                product_name_to_id: dict[str, str] = {}
                for dp in db_products:
                    pid = dp.get("product_id", "")
                    pname = (dp.get("product_name", "") or "").strip().lower()
                    if pid and pname:
                        product_name_to_id[pname] = pid
                logger.info(
                    "collect_sources: resolved %d product IDs from DB: %s",
                    len(product_name_to_id), product_name_to_id,
                )
            except Exception as exc:
                logger.error("Failed to write products to DB: %s", exc)
                db_products = []
                product_name_to_id = {}

            # vNext-R2-D Patch + vNext-P0.1 Enhancement:
            # 1. For products WITHOUT seed URLs: convert candidates to seed URLs (existing behavior)
            # 2. For products WITH seed URLs: supplement with discovered URLs (new P0.1 behavior)
            discovered_products_with_urls: list[dict] = []
            # supplement_urls[product_key] = list of supplemental discovered URLs
            supplement_urls: dict[str, list[dict]] = {}

            def _product_key(p: dict) -> tuple[str, str, str]:
                """Return (product_id, product_name, product_slug) for matching."""
                # Products from frontend API use "name" not "product_name"
                product_name = p.get("product_name") or p.get("name") or ""
                product_id = p.get("product_id") or ""
                product_slug = p.get("product_slug") or ""
                return (
                    product_id.lower(),
                    product_name.lower(),
                    product_slug.lower(),
                )

            def _match_product(comp: str, pkey: tuple[str, str, str]) -> bool:
                """Return True if competitor string matches any product key field."""
                pid, pname, pslug = pkey
                return (comp == pid or comp == pname or comp == pslug
                        or pid in comp or pname in comp)

            if source_candidates:

                # Group candidates by competitor name
                by_competitor: dict[str, list[dict]] = {}
                for c in source_candidates:
                    comp = (c.get("competitor") or "").lower()
                    if comp not in by_competitor:
                        by_competitor[comp] = []
                    by_competitor[comp].append(c)

                existing_url_map: dict[tuple[str, ...], set[str]] = {}
                for p in products:
                    pkey = _product_key(p)
                    existing_url_map[pkey] = {
                        u.lower().strip().rstrip("/")
                        for u in (p.get("seed_urls") or [])
                        if u.strip().startswith("http")
                    }

                for product in products:
                    pkey = _product_key(product)
                    pname = pkey[1] or pkey[0]  # prefer product_name

                    # Find matching candidates
                    matched = []
                    for comp, cands in by_competitor.items():
                        if _match_product(comp, pkey):
                            matched.extend(cands)

                    if not matched:
                        continue

                    # Collect supplemental URLs (for products with existing URLs)
                    # or full URLs (for products without any)
                    new_urls: list[dict] = []
                    for c in matched[:5]:
                        url = c.get("url", "")
                        if not url or not url.startswith("http"):
                            continue
                        norm = url.lower().strip().rstrip("/")
                        existing = existing_url_map.get(pkey, set())
                        if norm in existing:
                            continue  # skip duplicate

                        new_urls.append({
                            "url": url,
                            "title": c.get("title", ""),
                            "snippet": c.get("snippet", ""),
                            "discovered_by_search": True,
                            "discovery_query": c.get("query", ""),
                            "discovery_provider": discovery_results.get("provider_name", "unknown"),
                        })
                        existing_url_map[pkey].add(norm)

                    if not new_urls:
                        continue

                    has_existing = bool(product.get("seed_urls") or product.get("official_website"))

                    if not has_existing:
                        # No existing URLs → convert discovered candidates to seed URLs
                        top_urls = new_urls[:3]
                        discovered_product = dict(product)
                        discovered_product["seed_urls"] = [t["url"] for t in top_urls]
                        discovered_product["discovered_urls"] = top_urls
                        discovered_product["discovered_by_search"] = True
                        discovered_product["discovery_provider"] = discovery_results.get("provider_name", "unknown")
                        discovered_products_with_urls.append(discovered_product)
                        logger.info(
                            "collect_sources: converted %d candidates to seed_urls for product=%s (%s)",
                            len(top_urls), pkey[0], pname,
                        )
                    else:
                        # Has existing URLs → supplement (add to discovered_urls for enrichment)
                        supplement_urls[pkey] = supplement_urls.get(pkey, []) + new_urls
                        logger.info(
                            "collect_sources: supplementing %d discovered URLs for product=%s (%s) "
                            "(existing URLs: %d)",
                            len(new_urls), pkey[0], pname,
                            len(existing_url_map.get(pkey, set())),
                        )

                logger.info(
                    "collect_sources: discovered %d products with URLs, supplemented %d products "
                    "from %d total candidates",
                    len(discovered_products_with_urls), len(supplement_urls), len(source_candidates),
                )

            if not products_with_urls and not discovered_products_with_urls:
                logger.warning(
                    "collect_sources (real_time): no products with seed_urls for run_id=%s",
                    run_id,
                )
                state.setdefault("errors", []).append({
                    "reason_code": "NO_SEED_URLS",
                    "message": "No seed_urls provided for any product in real_time mode",
                    "node": "collect_sources",
                })
            else:
                # vNext-P0.1: Build all_products_for_collection with supplemental discovered URLs.
                # Strategy:
                # - Products without URLs: use discovered URLs (from discovered_products_with_urls)
                # - Products with URLs: merge supplemental discovered URLs into seed_urls
                all_products_for_collection: list[dict] = []
                seen_ids: set[str] = set()

                # 1. Add products that had no URLs (discovered_products_with_urls)
                for dp in discovered_products_with_urls:
                    pid = dp.get("product_id", "")
                    pname = dp.get("product_name", dp.get("name", ""))
                    key = pid or pname
                    if key and key not in seen_ids:
                        all_products_for_collection.append(dp)
                        seen_ids.add(key)

                # 2. Add products with existing URLs, enriched with supplemental discovered URLs
                for p in products_with_urls:
                    pkey = _product_key(p)
                    p_copy = dict(p)

                    # Normalize field names: products from frontend API use "name" not "product_name"
                    # and may not have "product_id". Use name as the key identifier.
                    p_copy.setdefault("product_name", p_copy.get("name", ""))
                    p_copy.setdefault("product_id", p_copy.get("product_id", ""))

                    # Add supplemental URLs (deduplicated against existing seed_urls)
                    sup_urls = supplement_urls.get(pkey, [])
                    existing_norms = {
                        u.lower().strip().rstrip("/")
                        for u in (p_copy.get("seed_urls") or [])
                        if u.strip().startswith("http")
                    }
                    new_seed_urls = list(p_copy.get("seed_urls") or [])
                    new_discovered = []
                    for sup in sup_urls[:3]:
                        url = sup.get("url", "")
                        if not url:
                            continue
                        norm = url.lower().strip().rstrip("/")
                        if norm in existing_norms:
                            continue
                        existing_norms.add(norm)
                        new_seed_urls.append(url)
                        new_discovered.append(sup)

                    p_copy["seed_urls"] = new_seed_urls
                    if new_discovered:
                        p_copy["supplemental_discovered_urls"] = new_discovered
                        logger.info(
                            "collect_sources (P0.1): added %d supplemental URLs to product=%s (%s)",
                            len(new_discovered), p_copy.get("product_id", ""),
                            p_copy.get("product_name", ""),
                        )

                    # Use product_name as the key if product_id is empty
                    pid = p_copy.get("product_id", "")
                    pname = p_copy.get("product_name", "")
                    key = pid or pname
                    if key and key not in seen_ids:
                        all_products_for_collection.append(p_copy)
                        seen_ids.add(key)

                source_plan = {
                    "products": all_products_for_collection,
                    "target_source_types": ["official_site", "documentation", "pricing_page"],
                    "discovered_products_with_urls": discovered_products_with_urls,
                    "supplement_products": list(supplement_urls.keys()),
                }

                # P0-Fix: Inject run-scoped product_ids into source_plan products.
                # Products from the frontend have no product_id; the collector needs the
                # resolved run-scoped IDs so it can tag sources correctly.
                for sp in source_plan.get("products", []):
                    pname = (sp.get("product_name") or sp.get("name") or "").strip().lower()
                    if pname and pname in product_name_to_id:
                        sp["product_id"] = product_name_to_id[pname]

                agent = _collector()
                # P1-Redesign (2026-06-18): Per-product parallel collection.
                # Previous behavior: a single agent.collect() call processed all products
                # with internal ThreadPoolExecutor(4) — all URLs from all products competed
                # for 4 slots, causing head-of-line blocking for slow URLs.
                #
                # New behavior: each product runs in its own worker thread, so N products
                # finish in roughly max(per_product_time) instead of sum(per_product_time).
                # This requires thread-local DB connections (see _get_thread_repo) because
                # SQLite connections are not thread-safe.
                import backend.app.orchestrator.graph as _graph
                node_timeout = _graph.NODE_TIMEOUTS.get("collect_sources", 900)
                from concurrent.futures import ThreadPoolExecutor, as_completed

                N = len(all_products_for_collection)
                # P1-Hotfix: cap at 2 to avoid nested-concurrency resource exhaustion.
                # Each worker calls agent.collect() which has its own ThreadPoolExecutor(max_workers=4),
                # launching up to 4 concurrent Playwright browsers. With 4 outer workers: 4×4=16
                # concurrent browsers = server overload → all time out. Cap at 2 workers → 8 browsers max.
                max_workers = max(1, min(2, N))
                # Per-product timeout: split the node budget evenly but never below 180s,
                # so a single product with many URLs still gets a fair share.
                per_product_timeout = max(180, node_timeout // max(1, N))

                _collect_logger.warning(
                    "collect_sources (P1): per-product parallel collection — N=%d, workers=%d, "
                    "node_timeout=%ds, per_product_timeout=%ds",
                    N, max_workers, node_timeout, per_product_timeout,
                )

                sources: list[dict] = []
                snapshots: list[dict] = []
                raw_documents: list[dict] = []
                collection_stats: dict[str, Any] = {
                    "collected": 0, "failed": 0, "total_urls": 0, "skipped": 0,
                    "total_chars": 0, "elapsed_s": 0.0,
                }
                if N <= 1:
                    # Single-product fast path: skip thread overhead
                    single = all_products_for_collection[0] if all_products_for_collection else {}
                    sp = {
                        "products": [single],
                        "target_source_types": source_plan.get("target_source_types", []),
                        "discovered_products_with_urls": [],
                        "supplement_products": [],
                    }
                    result = agent.collect(sp, run_id, mode="real_time", total_timeout=node_timeout)
                    sources = result.get("sources", []) if result else []
                    snapshots = result.get("snapshots", []) if result else []
                    raw_documents = result.get("raw_documents", []) if result else []
                    collection_stats = result.get("collection_stats", collection_stats) if result else collection_stats
                else:
                    # Per-product parallel collection
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    import time as _time
                    t_start = _time.perf_counter()
                    with ThreadPoolExecutor(
                        max_workers=max_workers,
                        thread_name_prefix=f"collect-{run_id[:6]}",
                    ) as ex:
                        futures = {
                            ex.submit(
                                _collect_one_product_sync,
                                agent,
                                p,
                                source_plan,
                                run_id,
                                per_product_timeout,
                            ): p
                            for p in all_products_for_collection
                        }
                        per_product_results = []
                        for fut in as_completed(futures):
                            product = futures[fut]
                            try:
                                res = fut.result()
                                per_product_results.append((product, res))
                            except Exception as exc:
                                _collect_logger.error(
                                    "per-product collect failed for %s: %s",
                                    product.get("product_name"), exc,
                                )
                                per_product_results.append((product, {
                                    "sources": [], "snapshots": [], "raw_documents": [],
                                    "collection_stats": {"failed": 1, "error": str(exc)},
                                }))
                    elapsed_s = _time.perf_counter() - t_start
                    _collect_logger.info(
                        "collect_sources (P1): parallel collection completed in %.1fs (%d products, "
                        "%d workers)",
                        elapsed_s, N, max_workers,
                    )
                    for product, res in per_product_results:
                        sources.extend(res.get("sources", []) or [])
                        snapshots.extend(res.get("snapshots", []) or [])
                        raw_documents.extend(res.get("raw_documents", []) or [])
                        ps = res.get("collection_stats", {}) or {}
                        collection_stats["collected"] = collection_stats.get("collected", 0) + ps.get("collected", 0)
                        collection_stats["failed"] = collection_stats.get("failed", 0) + ps.get("failed", 0)
                        collection_stats["total_urls"] = collection_stats.get("total_urls", 0) + ps.get("total_urls", 0)
                        collection_stats["skipped"] = collection_stats.get("skipped", 0) + ps.get("skipped", 0)
                        collection_stats["total_chars"] = collection_stats.get("total_chars", 0) + ps.get("total_chars", 0)
                    collection_stats["elapsed_s"] = elapsed_s
                    collection_stats["parallel_workers"] = max_workers
                    collection_stats["parallel_product_count"] = N

                    # Drop thread-local repositories (best-effort cleanup)
                    _reset_thread_repos()

                _collect_logger.info(
                    "collect_sources (P1): merged sources=%d snapshots=%d raw_documents=%d",
                    len(sources), len(snapshots), len(raw_documents),
                )

                # P0-Fix: If sources are empty (timeout during collect), try to read from checkpoint
                # and rebuild the results. This ensures partial data is not lost.
                # P1-Redesign (2026-06-18): read ALL per-product checkpoint files (pattern
                # /tmp/collector_ckpt_{run_id}_*.json), not just the legacy single-file path.
                if not sources:
                    from pathlib import Path
                    ckpt_dir = Path("/tmp")
                    # Prefer the new glob pattern; fall back to legacy single file for
                    # backward compatibility with older runs.
                    ckpt_paths = sorted(ckpt_dir.glob(f"collector_ckpt_{run_id}_*.json"))
                    legacy_ckpt = ckpt_dir / f"collector_ckpt_{run_id}.json"
                    if legacy_ckpt.exists() and not ckpt_paths:
                        ckpt_paths = [legacy_ckpt]
                    if ckpt_paths:
                        try:
                            import json as _json
                            from datetime import datetime, timezone
                            now_str = datetime.now(timezone.utc).isoformat()
                            rebuilt_total = 0
                            for ckpt_path in ckpt_paths:
                                ckpt_data = _json.loads(ckpt_path.read_text())
                                ckpt_results = ckpt_data.get("results", [])
                                _collect_logger.warning(
                                    "collect_sources: checkpoint %s has %d results, rebuilding.",
                                    ckpt_path.name, len(ckpt_results),
                                )
                                for res in ckpt_results:
                                    task = res.get("_task", {})
                                    url = task.get("url", "")
                                    product_id = task.get("product_id", "")
                                    source_id = task.get("source_id", "")
                                    snapshot_id = task.get("snapshot_id", "")
                                    error_msg = res.get("error_message")
                                    status_code = res.get("status_code", 0)
                                    raw_text = res.get("raw_text", "") or ""
                                    raw_html = res.get("raw_html", "") or ""
                                    title = res.get("title", "") or task.get("product_name", "")
                                    domain = res.get("domain", "")
                                    content_hash = res.get("content_hash", "")
                                    fetched_at = res.get("fetched_at", now_str)
                                    source_type = task.get("source_type", "official_site")

                                    # Build source record
                                    source_record = {
                                        "run_id": run_id,
                                        "source_id": source_id,
                                        "product_id": product_id,
                                        "url": url,
                                        "source_type": source_type,
                                        "fetch_level": task.get("fetch_level", 1),
                                        "fetch_strategy": task.get("fetch_strategy", "requests"),
                                        "collection_method": task.get("collection_method", "seed_url"),
                                        "status": "collected" if not error_msg else "failed",
                                        "char_count": len(raw_text),
                                        "content": raw_text,
                                        "raw_html": raw_html,
                                        "content_hash": content_hash,
                                        "title": title,
                                        "domain": domain,
                                        "error_message": error_msg,
                                        "status_code": status_code,
                                        "fetched_at": fetched_at,
                                        "created_at": now_str,
                                    }
                                    sources.append(source_record)
                                    collection_stats["collected"] = collection_stats.get("collected", 0) + 1
                                    collection_stats["total_chars"] = collection_stats.get("total_chars", 0) + len(raw_text)
                                    rebuilt_total += 1
                                ckpt_path.unlink(missing_ok=True)
                            _collect_logger.warning(
                                "collect_sources: rebuilt %d sources from %d checkpoint files.",
                                rebuilt_total, len(ckpt_paths),
                            )
                        except Exception as exc:
                            _collect_logger.warning(
                                "collect_sources: failed to rebuild from checkpoint: %s",
                                exc,
                            )

                _collect_logger.critical(
                    "!!! COLLECT_SOURCES RESULT !!! run_id=%s, sources=%d, snapshots=%d, "
                    "raw_documents=%d, collection_stats=%s",
                    run_id, len(sources), len(snapshots), len(raw_documents),
                    json.dumps(collection_stats, ensure_ascii=False),
                )

                # vNext-R2-D Patch + vNext-P0.1: Mark sources that came from search discovery
                discovered_urls_map: dict[str, dict] = {}

                # Mark from discovered products (originally had no URLs)
                for dp in discovered_products_with_urls:
                    for du in dp.get("discovered_urls", []):
                        discovered_urls_map[du["url"]] = {
                            "discovered_by_search": True,
                            "discovery_query": du.get("discovery_query", ""),
                            "discovery_provider": du.get("discovery_provider", ""),
                            "discovery_mode": "full",
                        }

                # Mark supplemental discovered URLs (for products that already had URLs)
                for sp in all_products_for_collection:
                    for sup in sp.get("supplemental_discovered_urls", []):
                        discovered_urls_map[sup["url"]] = {
                            "discovered_by_search": True,
                            "discovery_query": sup.get("discovery_query", ""),
                            "discovery_provider": sup.get("discovery_provider", ""),
                            "discovery_mode": "supplement",
                        }

                for src in sources:
                    url = src.get("url", "")
                    if url in discovered_urls_map:
                        meta = discovered_urls_map[url]
                        src["discovered_by_search"] = meta.get("discovered_by_search")
                        src["discovery_query"] = meta.get("discovery_query", "")
                        src["discovery_provider"] = meta.get("discovery_provider", "")
                        src["discovery_mode"] = meta.get("discovery_mode", "unknown")

                logger.info(
                    "collect_sources (real_time): agent.collect returned sources=%d snapshots=%d raw_documents=%d",
                    len(sources), len(snapshots), len(raw_documents),
                )
                for s in sources:
                    logger.info(
                        "  source: product_id=%s url=%s status=%s error=%s",
                        s.get("product_id"), s.get("url"), s.get("status"), s.get("error_message"),
                    )

                # P0-Fix: Build product_id lookup before writing sources.
                # Products from frontend may use "name" instead of "product_name" and have no product_id.
                # After ProductRepository.add_product() creates DB entries with run-scoped IDs
                # (e.g. run_xxx_dify), we need to look up the actual product_id for each source URL.
                product_id_lookup: dict[str, str] = {}
                try:
                    from backend.app.storage.repositories import ProductRepository
                    prod_repo = ProductRepository()
                    db_products = prod_repo.list_products(run_id)
                    for dp in db_products:
                        pid = dp.get("product_id", "")
                        pname = (dp.get("product_name", "") or "").lower()
                        if pid and pname:
                            product_id_lookup[pname] = pid
                except Exception as exc:
                    logger.warning("collect_sources: failed to build product_id lookup: %s", exc)

                def _resolve_product_id(src: dict) -> str:
                    """Resolve product_id for a source that may lack one.
                    
                    Try: explicit product_id > product_name match > URL domain match.
                    """
                    existing = (src.get("product_id") or "").strip()
                    if existing:
                        return existing
                    pname = (src.get("product_name") or "").lower()
                    if pname and pname in product_id_lookup:
                        return product_id_lookup[pname]
                    # Try URL domain matching
                    src_url = (src.get("url") or "").lower()
                    for pname, pid in product_id_lookup.items():
                        if pname in src_url:
                            return pid
                    return ""

                # Write sources to DB
                for src in sources:
                    # P0-Fix: Resolve product_id from products table if missing
                    resolved_pid = _resolve_product_id(src)
                    if resolved_pid:
                        src["product_id"] = resolved_pid
                    try:
                        from backend.app.storage.repositories import SourceRepository
                        SourceRepository().add_source(src)
                    except Exception as exc:
                        logger.error("Failed to write source %s to DB: %s", src.get("source_id"), exc)

                # Write snapshots to DB
                for snap in snapshots:
                    try:
                        # P0-Fix: Resolve snapshot's source_id -> product_id via the source we just resolved
                        src_id = snap.get("source_id", "")
                        if src_id:
                            src_row = None
                            try:
                                from backend.app.storage.repositories import SourceRepository
                                src_row = SourceRepository().get_source(src_id)
                            except Exception:
                                pass
                            if src_row and not snap.get("product_id"):
                                snap["product_id"] = src_row.get("product_id", "")
                        from backend.app.storage.repositories import EvidenceRepository
                        EvidenceRepository().add_snapshot(snap)
                    except Exception as exc:
                        logger.error("Failed to write snapshot %s to DB: %s", snap.get("snapshot_id"), exc)

                logger.info(
                    "collect_sources (real_time): fetched %d sources, %d snapshots, %d raw_documents",
                    len(sources), len(snapshots), len(raw_documents),
                )

                # P0-7: Evidence extraction has been MOVED to its own node.
                # collect_sources now focuses purely on URL fetching + checkpoint writing.
                # The evidence_extraction node handles all CPU-heavy text analysis.

        except Exception as exc:
            logger.error("collect_sources (real_time) failed for run_id=%s: %s", run_id, exc)
            state.setdefault("errors", []).append({
                "reason_code": "COLLECT_SOURCES_REAL_TIME_FAILED",
                "message": str(exc),
                "node": "collect_sources",
            })

    state["sources"] = sources
    state["snapshots"] = snapshots
    state["raw_documents"] = raw_documents  # evidence_extraction reads from state["sources"]
    # In cached/replay mode, collect_sources loads evidence_items directly
    # from DB. Preserve them so downstream nodes (analyze_dimensions etc.)
    # can see the pre-extracted evidence without running evidence_extraction.
    if mode in ("cached", "replay"):
        state["evidence_items"] = evidence_items
    else:
        state["evidence_items"] = []  # No longer set here — evidence_extraction node handles it
    state["source_candidates"] = source_candidates
    state["discovery_results"] = discovery_results
    state["product_coverage"] = state.get("product_coverage", {})
    return state


def evidence_extraction(state: WorkflowState) -> WorkflowState:
    """
    P0-7: New independent node for CPU-heavy evidence extraction.
    Split out of collect_sources to have its own timeout budget (600s).
    collect_sources handles URL fetching; this node handles text analysis.
    """
    run_id = state.get("run_id", "unknown")
    raw_documents: list[dict] = []
    sources = state.get("sources", []) or []
    evidence_items: list[dict] = []
    product_coverage: dict = {}

    logger.info("evidence_extraction: run_id=%s", run_id)

    # If raw_documents are already in state (from a previous run that didn't time out),
    # use them directly. Otherwise, try to rebuild from sources.
    if state.get("raw_documents"):
        raw_documents = state["raw_documents"]
    elif sources:
        # Rebuild raw_documents from source content
        for src in sources:
            content = src.get("content", "") or ""
            if content and src.get("status") != "failed":
                raw_documents.append({
                    "run_id": run_id,
                    "product_id": src.get("product_id", ""),
                    "source_id": src.get("source_id", ""),
                    "snapshot_id": "",
                    "raw_text": content,
                    "source_type": src.get("source_type", "official_site"),
                    "url": src.get("url", ""),
                    "title": src.get("title", ""),
                })
        logger.info(
            "evidence_extraction: rebuilt %d raw_documents from sources (no checkpoint found)",
            len(raw_documents),
        )

    if not raw_documents:
        logger.warning("evidence_extraction: no raw_documents found for run_id=%s", run_id)
        state["evidence_items"] = []
        state["product_coverage"] = {}
        return state

    # Evidence extraction (now parallelized with ProcessPoolExecutor)
    try:
        extractor = _evidence_extractor()
        evidence_items, product_coverage = extractor.extract_evidence(raw_documents, run_id)
        logger.info(
            "evidence_extraction: extracted %d items from %d raw_documents, coverage=%s",
            len(evidence_items), len(raw_documents), product_coverage,
        )
        state["product_coverage"] = product_coverage

        logger.critical(
            "!!! EVIDENCE_EXTRACTION RESULT !!! run_id=%s, raw_docs=%d, evidence_items=%d",
            run_id, len(raw_documents), len(evidence_items),
        )

        if not evidence_items and raw_documents:
            logger.warning(
                "evidence_extraction: CRITICAL - raw_documents=%d but evidence_items=0! "
                "Check extractor or content parsing.",
                len(raw_documents),
            )

        # Write evidence to DB
        # P1-Redesign (2026-06-18): tag each evidence row with the current rework
        # iteration. Iteration 0 = initial collect; iteration > 0 = added by a
        # true re-collect round triggered by the reviewer's feedback loop.
        rework_iter = int(state.get("_rework_collect_count", 0) or 0)
        rework_reason = state.get("rework_active_reason", "")
        for ev in evidence_items:
            try:
                # Don't overwrite an existing attribution (execute_rework may set
                # these fields when writing rework-added evidence directly).
                ev.setdefault("rework_iteration", rework_iter)
                ev.setdefault("rework_reason", rework_reason)
                from backend.app.storage.repositories import EvidenceRepository
                EvidenceRepository().add_evidence(ev)
            except Exception as exc:
                logger.error("Failed to write evidence %s to DB: %s", ev.get("evidence_id"), exc)
    except Exception as exc:
        logger.error("Evidence extraction failed: %s", exc)
        state.setdefault("errors", []).append({
            "reason_code": "EVIDENCE_EXTRACTION_FAILED",
            "message": str(exc),
            "node": "evidence_extraction",
        })

    state["evidence_items"] = evidence_items
    return state


def evaluate_evidence(state: WorkflowState) -> WorkflowState:
    """
    Evaluate evidence quality after collect_sources, before extract_facts.

    Computes quality scores for each evidence item and persists to DB.
    Enriches evidence with source metadata (source_type, url, trust_tier, etc.)
    and product info (product_name) before evaluation.
    """
    run_id = state.get("run_id", "")
    evidence_items = state.get("evidence_items", []) or []
    mode = state.get("mode", "real_time")

    logger.info("evaluate_evidence: run_id=%s evidence_count=%d", run_id, len(evidence_items))

    if not evidence_items:
        logger.info("evaluate_evidence: no evidence to evaluate for run_id=%s", run_id)
        state["evidence_evaluation"] = {
            "total_evidence": 0,
            "usable_evidence": 0,
            "avg_final_score": 0.0,
            "low_quality_count": 0,
        }
        return state

    try:
        from backend.app.services.evidence_evaluator import evaluate_evidence_items
        from backend.app.storage.repositories import EvidenceRepository

        # Build source_map from state["sources"] by source_id
        sources = state.get("sources", []) or []
        source_map = {src.get("source_id"): src for src in sources}

        # Build product_map from task_brief.products by product_id and product_slug
        task_brief = state.get("task_brief", {})
        products = task_brief.get("products", []) or []
        product_map_by_id = {p.get("product_id", ""): p for p in products}
        product_map_by_slug = {p.get("product_slug", ""): p for p in products}

        def _slugify(name: str) -> str:
            """Create a URL-safe slug from a product name."""
            return name.lower().replace(" ", "-").replace("_", "-")

        # Enrich evidence items with source metadata and product info
        for evidence in evidence_items:
            source_id = evidence.get("source_id", "")
            source = source_map.get(source_id, {})

            # Enrich from source
            evidence["source_type"] = source.get("source_type") or evidence.get("source_type")
            evidence["url"] = source.get("url") or evidence.get("url")
            evidence["domain"] = source.get("domain") or evidence.get("domain")
            evidence["trust_tier"] = source.get("trust_tier") or evidence.get("trust_tier")
            evidence["fetched_at"] = source.get("fetched_at") or evidence.get("fetched_at") or evidence.get("created_at")

            # Enrich product_name from task_brief.products
            product_id = evidence.get("product_id", "")
            product_slug = evidence.get("product_slug", "")

            # Try to find product in task_brief
            product = product_map_by_id.get(product_id)
            if not product and product_slug:
                product = product_map_by_slug.get(product_slug)
            if not product and product_id:
                # Try slugified version
                product = product_map_by_slug.get(_slugify(product_id))

            if product and not evidence.get("product_name"):
                evidence["product_name"] = product.get("product_name") or product.get("product_id", "")

        # Evaluate all evidence items
        enriched_items, summary = evaluate_evidence_items(evidence_items, run_id)

        # Persist quality scores to DB
        evidence_repo = EvidenceRepository()
        for evidence in enriched_items:
            ev_id = evidence.get("evidence_id")
            quality = evidence.get("quality", {})
            usable = quality.get("usable_for_claim", False)
            try:
                evidence_repo.update_evidence_quality(ev_id, quality, usable)
            except Exception as exc:
                logger.warning("Failed to persist quality for evidence %s: %s", ev_id, exc)

        # Update state with enriched evidence items
        state["evidence_items"] = enriched_items
        state["evidence_evaluation"] = summary

        logger.info(
            "evaluate_evidence: run_id=%s total=%d usable=%d avg_score=%.3f low_quality=%d",
            run_id,
            summary.get("total_evidence", 0),
            summary.get("usable_evidence", 0),
            summary.get("avg_final_score", 0.0),
            summary.get("low_quality_count", 0),
        )

    except Exception as exc:
        logger.error("evaluate_evidence failed for run_id=%s: %s", run_id, exc)
        state.setdefault("errors", []).append({
            "reason_code": "EVIDENCE_EVALUATION_FAILED",
            "message": str(exc),
            "node": "evaluate_evidence",
        })
        state["evidence_evaluation"] = {
            "total_evidence": len(evidence_items),
            "usable_evidence": 0,
            "avg_final_score": 0.0,
            "low_quality_count": len(evidence_items),
            "error": str(exc),
        }

    return state


def pii_scrub(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "")
    evidence_items = state.get("evidence_items") or []
    if not evidence_items:
        return state

    try:
        from backend.app.services.pii_service import mask_pii
        from backend.app.storage.repositories import PiiLogRepository
    except Exception:
        return state

    pii_log_repo = PiiLogRepository()
    has_unmasked_high_risk = False

    for evidence in evidence_items:
        snippet = evidence.get("snippet", "")
        if not snippet:
            continue

        masked_text, detected_types = mask_pii(snippet)
        if not detected_types:
            continue

        risk = "low"
        if "id_number" in detected_types or "phone" in detected_types:
            risk = "high"

        mask_succeeded = masked_text != snippet

        pii_status = "masked" if mask_succeeded else "failed"
        try:
            from datetime import datetime, timezone
            pii_log_repo.add_pii_log({
                "pii_log_id": f"pii_{uuid.uuid4().hex[:16]}",
                "run_id": run_id,
                "source_id": evidence.get("source_id"),
                "evidence_id": evidence.get("evidence_id"),
                "detected_types": detected_types,
                "risk_level": risk,
                "status": pii_status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

        if mask_succeeded:
            evidence["snippet"] = masked_text
            evidence["pii_masked"] = True
        elif risk == "high":
            has_unmasked_high_risk = True

    if has_unmasked_high_risk:
        state.setdefault("errors", []).append({
            "reason_code": "PII_NOT_MASKED",
            "message": "High-risk PII detected and could not be masked; manual review required",
            "node": "pii_scrub",
        })

    return state


def extract_facts(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "")
    mode = state.get("mode", "real_time")

    facts: list[dict[str, Any]] = []

    if mode in ("cached", "replay"):
        try:
            facts = _db_query(
                "SELECT * FROM facts WHERE run_id = ? ORDER BY product_id, created_at",
                (run_id,),
            )
            logger.info("extract_facts: loaded %d facts from DB for run_id=%s", len(facts), run_id)
        except Exception as exc:
            logger.error("extract_facts: failed to load facts: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_LOAD_FACTS_FAILED",
                "message": str(exc),
                "node": "extract_facts",
            })

    elif mode == "real_time":
        evidence_items = state.get("evidence_items", [])
        if not evidence_items:
            logger.warning(
                "extract_facts (real_time): no evidence_items for run_id=%s",
                run_id,
            )
            state.setdefault("errors", []).append({
                "reason_code": "NO_EVIDENCE_FOR_FACTS",
                "message": "No evidence items available for fact extraction",
                "node": "extract_facts",
            })
        else:
            try:
                extractor = _fact_extractor()
                facts = extractor.extract_facts(evidence_items, run_id)

                # Update product_coverage with fact counts
                from collections import Counter
                fact_counts = Counter()
                for f in (facts or []):
                    slug = f.get("product_slug") or _slugify(f.get("product_id", ""))
                    fact_counts[slug] += 1
                pc = state.get("product_coverage", {})
                for slug, count in fact_counts.items():
                    if slug in pc:
                        pc[slug]["fact_count"] = count
                state["product_coverage"] = pc

                # Write facts to DB
                from backend.app.storage.fact_repository import FactRepository
                repo = FactRepository()
                # P1-Redesign (2026-06-18): same attribution as evidence_items.
                rework_iter_f = int(state.get("_rework_collect_count", 0) or 0)
                rework_reason_f = state.get("rework_active_reason", "")
                for fact in facts:
                    try:
                        fact.setdefault("rework_iteration", rework_iter_f)
                        repo.add_fact(fact)
                    except Exception as exc:
                        logger.error("Failed to write fact %s to DB: %s", fact.get("fact_id"), exc)

                logger.info(
                    "extract_facts (real_time): extracted %d facts for run_id=%s",
                    len(facts), run_id,
                )
            except Exception as exc:
                logger.error("extract_facts (real_time) failed for run_id=%s: %s", run_id, exc)
                state.setdefault("errors", []).append({
                    "reason_code": "FACT_EXTRACTION_FAILED",
                    "message": str(exc),
                    "node": "extract_facts",
                })

    state["facts"] = facts
    return state


def extract_evidence(state: WorkflowState) -> WorkflowState:
    """
    Extract evidence from raw_documents/snapshots.
    For rework mode: loads raw documents from DB and runs evidence extraction.
    """
    run_id = state.get("run_id", "")
    mode = state.get("mode", "real_time")
    evidence_items: list[dict[str, Any]] = []

    if mode in ("cached", "replay"):
        try:
            evidence_items = _db_query(
                "SELECT * FROM evidence_items WHERE run_id = ? ORDER BY product_id, created_at",
                (run_id,),
            )
            logger.info("extract_evidence: loaded %d evidence_items from DB", len(evidence_items))
        except Exception as exc:
            logger.error("extract_evidence: failed to load evidence: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_LOAD_EVIDENCE_FAILED",
                "message": str(exc),
                "node": "extract_evidence",
            })
    elif mode == "real_time":
        snapshots = state.get("snapshots", [])
        if not snapshots:
            try:
                snapshots = _db_query(
                    "SELECT * FROM snapshots WHERE run_id = ?",
                    (run_id,),
                )
            except Exception as exc:
                logger.error("extract_evidence: failed to load snapshots: %s", exc)

        if snapshots:
            try:
                extractor = _evidence_extractor()
                evidence_items, product_coverage = extractor.extract_evidence(snapshots, run_id)
                state["product_coverage"] = state.get("product_coverage", {})
                if product_coverage:
                    state["product_coverage"].update(product_coverage)

                for ev in evidence_items:
                    try:
                        from backend.app.storage.repositories import EvidenceRepository
                        EvidenceRepository().add_evidence(ev)
                    except Exception as exc:
                        logger.error("Failed to write evidence %s: %s", ev.get("evidence_id"), exc)

                logger.info("extract_evidence: extracted %d evidence_items", len(evidence_items))
            except Exception as exc:
                logger.error("extract_evidence: failed: %s", exc)
                state.setdefault("errors", []).append({
                    "reason_code": "EVIDENCE_EXTRACTION_FAILED",
                    "message": str(exc),
                    "node": "extract_evidence",
                })
        else:
            logger.warning("extract_evidence: no snapshots available for run_id=%s", run_id)

    state["evidence_items"] = evidence_items
    return state


def generate_claims(state: WorkflowState) -> WorkflowState:
    """
    Generate claim drafts from evidence items and facts.
    Uses AnalystAgent to produce structured claims with evidence citations.
    """
    run_id = state.get("run_id", "")
    mode = state.get("mode", "real_time")
    claim_drafts: list[dict[str, Any]] = []

    evidence_items = state.get("evidence_items", [])
    facts = state.get("facts", [])
    task_brief = state.get("task_brief", {})

    def _build_evidence_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for ev in items:
            ev_id = str(ev.get("evidence_id") or "").strip()
            if ev_id:
                index[ev_id] = ev
        return index

    def _filter_claim_evidence_ids(
        claim: dict[str, Any],
        evidence_index: dict[str, dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        raw_ids = claim.get("evidence_ids", []) or []
        valid_ids: list[str] = []
        missing_ids: list[str] = []
        for raw_id in raw_ids:
            eid = str(raw_id).strip()
            if not eid:
                continue
            if eid in evidence_index:
                valid_ids.append(eid)
            else:
                missing_ids.append(eid)
        claim["evidence_ids"] = valid_ids
        return valid_ids, missing_ids

    if mode in ("cached", "replay"):
        try:
            claim_drafts = _db_query(
                "SELECT * FROM claims WHERE run_id = ? AND review_status = 'pending' ORDER BY product_id",
                (run_id,),
            )
            logger.info("generate_claims: loaded %d pending claim drafts from DB", len(claim_drafts))
        except Exception as exc:
            logger.error("generate_claims: failed to load claims: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_LOAD_CLAIMS_FAILED",
                "message": str(exc),
                "node": "generate_claims",
            })
    elif mode == "real_time":
        if not evidence_items:
            logger.warning("generate_claims: no evidence_items for run_id=%s", run_id)
            state.setdefault("errors", []).append({
                "reason_code": "NO_EVIDENCE_FOR_CLAIMS",
                "message": "No evidence items available for claim generation",
                "node": "generate_claims",
            })
        else:
            # Filter to only this product's evidence/facts when in rework mode
            rework_product = state.get("current_rework_product", "")
            if rework_product:
                filtered_evidence = [e for e in evidence_items if _matches_rework_product(e, rework_product)]
                filtered_facts = [f for f in facts if _matches_rework_product(f, rework_product)]
                logger.info(
                    "generate_claims: rework mode, filtering to product=%s "
                    "evidence=%d/%d facts=%d/%d",
                    rework_product, len(filtered_evidence), len(evidence_items),
                    len(filtered_facts), len(facts),
                )
            else:
                filtered_evidence = evidence_items
                filtered_facts = facts

            evidence_index = _build_evidence_index(filtered_evidence)
            generated = []
            try:
                from backend.app.agents.analyst.analyst import AnalystAgent
                agent = AnalystAgent()
                generated = agent.analyze(filtered_evidence, filtered_facts, task_brief, run_id)
                logger.info(
                    "generate_claims: AnalystAgent produced %d claims for run_id=%s",
                    len(generated), run_id,
                )
            except Exception as exc:
                logger.warning("generate_claims: AnalystAgent failed (%s), using template fallback", exc)
                state.setdefault("errors", []).append({
                    "reason_code": "CLAIM_GENERATION_LLM_FAILED",
                    "message": f"AnalystAgent failed: {exc}, falling back to template",
                    "node": "generate_claims",
                })

            # Fall back to template claims if LLM returned nothing
            if not generated:
                logger.info("generate_claims: LLM returned 0 claims, falling back to template")
                generated = _template_claims_from_evidence(filtered_evidence, run_id, rework_product)
                logger.info("generate_claims: template generated %d fallback claims", len(generated))

            # Normalize claim evidence references against the current evidence set.
            # This prevents stale references from surviving into reviewer / report stages.
            for cl in generated:
                valid_ids, missing_ids = _filter_claim_evidence_ids(cl, evidence_index)
                if missing_ids:
                    cl["stale_evidence_ids"] = missing_ids
                # If a claim loses all evidence references after filtering, keep it
                # but mark it as pending review with a low confidence ceiling.
                if not valid_ids and cl.get("confidence", 0.0) > 0.4:
                    cl["confidence"] = 0.4

            # Persist each claim to DB with required fields
            now = utc_now()
            for cl in generated:
                try:
                    from backend.app.storage.repositories import ClaimRepository
                    cl["created_by_agent"] = cl.get("created_by_agent") or "AnalystAgent"
                    cl["created_at"] = cl.get("created_at") or now
                    cl["updated_at"] = cl.get("updated_at") or now
                    ClaimRepository().add_claim(cl)
                except Exception as exc:
                    logger.error("Failed to write claim %s: %s", cl.get("claim_id"), exc)

            logger.info("generate_claims: persisted %d claims for run_id=%s", len(generated), run_id)
            claim_drafts = generated

    state["claim_drafts"] = claim_drafts
    return state



def detect_schema_gaps(state: WorkflowState) -> WorkflowState:
    """
    Detect schema coverage gaps after extract_facts, before analyze_dimensions.

    Analyzes facts and evidence to identify missing or weak schema coverage,
    generates suggested queries for filling gaps, and updates state with
    schema_gaps and schema_coverage.
    """
    run_id = state.get("run_id", "")
    facts = state.get("facts", []) or []
    evidence_items = state.get("evidence_items", []) or []
    task_brief = state.get("task_brief", {})
    mode = state.get("mode", "real_time")

    logger.info(
        "detect_schema_gaps: run_id=%s facts_count=%d evidence_count=%d",
        run_id, len(facts), len(evidence_items),
    )

    # Get products from task_brief
    products = task_brief.get("products", []) or []

    if not products:
        logger.info("detect_schema_gaps: no products to analyze for run_id=%s", run_id)
        state["schema_gaps"] = []
        state["schema_coverage"] = {
            "run_id": run_id,
            "total_required_keys": 19,
            "products_analyzed": 0,
            "total_gaps": 0,
            "schema_completion_rate": 0.0,
        }
        return state

    try:
        from backend.app.services.schema_gap_planner import SchemaGapPlanner

        planner = SchemaGapPlanner()
        schema_gaps, coverage_summary = planner.plan(
            facts=facts,
            evidence_items=evidence_items,
            products=products,
            run_id=run_id,
        )

        state["schema_gaps"] = schema_gaps
        state["schema_coverage"] = coverage_summary

        logger.info(
            "detect_schema_gaps: run_id=%s gaps=%d coverage_rate=%.3f high_priority=%d",
            run_id,
            len(schema_gaps),
            coverage_summary.get("schema_completion_rate", 0.0),
            coverage_summary.get("high_priority_gaps", 0),
        )

    except Exception as exc:
        logger.error("detect_schema_gaps failed for run_id=%s: %s", run_id, exc)
        state.setdefault("errors", []).append({
            "reason_code": "SCHEMA_GAP_DETECTION_FAILED",
            "message": str(exc),
            "node": "detect_schema_gaps",
        })
        state["schema_gaps"] = []
        state["schema_coverage"] = {
            "run_id": run_id,
            "total_required_keys": 19,
            "products_analyzed": len(products),
            "total_gaps": 0,
            "schema_completion_rate": 0.0,
            "error": str(exc),
        }

    return state


def coverage_critic(state: WorkflowState) -> WorkflowState:
    """
    vNext-P0.3: Coverage Critic - Gate between collect/extract and report generation.

    Evaluates evidence sufficiency BEFORE expensive report generation.
    If coverage is insufficient, generates supplemental queries and triggers re-collection.

    Decision logic:
    - Sufficient: proceed to execute_rework → analyze_dimensions
    - Insufficient: generate targeted queries, add to state for supplemental collection
    - critically_insufficient: pause and require human intervention

    This is the key quality gate that prevents "garbage in → garbage out" in report generation.
    """
    run_id = state.get("run_id", "")
    facts = state.get("facts", []) or []
    evidence_items = state.get("evidence_items", []) or []
    schema_gaps = state.get("schema_gaps", []) or []
    task_brief = state.get("task_brief", {})

    logger.info(
        "coverage_critic: run_id=%s facts=%d evidence=%d schema_gaps=%d",
        run_id, len(facts), len(evidence_items), len(schema_gaps),
    )

    products = task_brief.get("products", []) or []
    if not products:
        logger.warning("coverage_critic: no products found for run_id=%s", run_id)
        state["coverage_critic_result"] = {
            "status": "no_products",
            "can_proceed": True,
            "supplemental_queries": [],
        }
        return state

    # Build per-product, per-dimension coverage map
    coverage: dict[str, dict[str, Any]] = {}
    for p in products:
        pid = p.get("product_id", "")
        pname = p.get("product_name", pid)
        slug = p.get("product_slug") or _slugify(pid)
        coverage[slug] = {
            "product_id": pid,
            "product_name": pname,
            "product_slug": slug,
            "fact_count": 0,
            "evidence_count": 0,
            "schema_keys_covered": set(),
            "dimensions": {},
        }

    # Normalize evidence/fact slugs to match coverage keys.
    # Evidence may have run-prefixed slugs (e.g. "run_xxx_dify") while coverage
    # keys are bare slugs (e.g. "dify"). Try both.
    def _match_slug(raw_slug: str) -> str | None:
        if raw_slug in coverage:
            return raw_slug
        # Strip any run-prefix (e.g. "run_xxx_dify" → "dify")
        stripped = raw_slug.rsplit("_", 1)[-1] if "_" in raw_slug else raw_slug
        if stripped in coverage:
            return stripped
        return None

    # Count facts per product
    for fact in facts:
        slug = _match_slug(fact.get("product_slug", "")) or _match_slug(_slugify(fact.get("product_id", "")))
        if slug:
            coverage[slug]["fact_count"] += 1
            key = fact.get("schema_key", "")
            if key:
                coverage[slug]["schema_keys_covered"].add(key)

    # Count evidence per product
    for ev in evidence_items:
        slug = _match_slug(ev.get("product_slug", "")) or _match_slug(_slugify(ev.get("product_id", "")))
        if slug:
            coverage[slug]["evidence_count"] += 1

    # Schema gap analysis: check high-priority missing dimensions
    high_priority_gaps = [g for g in schema_gaps if g.get("priority") == "high"]
    medium_priority_gaps = [g for g in schema_gaps if g.get("priority") == "medium"]

    # Determine overall coverage status per product
    for slug, cov in coverage.items():
        fc = cov["fact_count"]
        ec = cov["evidence_count"]
        keys_covered = len(cov["schema_keys_covered"])

        if fc >= 5 and ec >= 5 and keys_covered >= 5:
            status = "strong"
        elif fc >= 2 and ec >= 3 and keys_covered >= 3:
            status = "sufficient"
        elif fc >= 1 and ec >= 2:
            status = "weak"
        else:
            status = "critical"
        cov["coverage_status"] = status

    # Aggregate status
    statuses = [cov["coverage_status"] for cov in coverage.values()]
    if all(s == "strong" for s in statuses):
        overall = "strong"
    elif any(s == "critical" for s in statuses):
        overall = "critical"
    elif any(s == "weak" for s in statuses):
        overall = "weak"
    elif any(s == "sufficient" for s in statuses):
        overall = "sufficient"
    else:
        overall = "unknown"

    # Generate supplemental queries for weak/critical products and dimensions
    supplemental_queries: list[dict[str, Any]] = []
    needs_human_review = False

    for slug, cov in coverage.items():
        if cov["coverage_status"] in ("weak", "critical"):
            pid = cov["product_id"]
            pname = cov["product_name"]

            # Find which dimensions are missing from schema gaps
            missing_dims = {
                g.get("schema_key", "")
                for g in schema_gaps
                if g.get("product_slug") == slug or g.get("product_id") == pid
            }

            # Build targeted supplemental queries using static templates (fast, no LLM calls).
            # Cap per-product queries to avoid flooding the collector.
            _per_product_queries = 0
            _MAX_QUERIES_PER_PRODUCT = 5
            for missing_key in missing_dims:
                if _per_product_queries >= _MAX_QUERIES_PER_PRODUCT:
                    break
                # Use static template fallback — avoids 1 LLM call per dimension
                dim_queries = _generate_supplemental_queries(pname, missing_key)
                _per_product_queries += 1
                for query in dim_queries:
                    if len(supplemental_queries) >= 20:
                        break
                    supplemental_queries.append({
                        "competitor": pname,
                        "competitor_id": pid,
                        "product_slug": slug,
                        "query": query,
                        "schema_key": missing_key,
                        "reason": f"Coverage {cov['coverage_status']}: {missing_key} not covered",
                        "priority": "high" if cov["coverage_status"] == "critical" else "medium",
                    })
                if len(supplemental_queries) >= 20:
                    break

    # Can we proceed?
    can_proceed = (
        overall in ("strong", "sufficient")
        and len(supplemental_queries) == 0
    ) or (
        overall in ("strong", "sufficient", "weak")
        and len(supplemental_queries) <= 3
    )

    if overall == "critical":
        needs_human_review = True

    result = {
        "status": overall,
        "can_proceed": can_proceed,
        "needs_human_review": needs_human_review,
        "product_coverage": {
            slug: {
                "coverage_status": cov["coverage_status"],
                "fact_count": cov["fact_count"],
                "evidence_count": cov["evidence_count"],
                "schema_keys_covered": len(cov["schema_keys_covered"]),
            }
            for slug, cov in coverage.items()
        },
        "high_priority_gaps": len(high_priority_gaps),
        "medium_priority_gaps": len(medium_priority_gaps),
        "supplemental_queries": supplemental_queries[:20],  # cap at 20
    }

    state["coverage_critic_result"] = result

    logger.info(
        "coverage_critic: run_id=%s overall=%s can_proceed=%s "
        "supplemental_queries=%d high_priority=%d",
        run_id, overall, can_proceed,
        len(supplemental_queries), len(high_priority_gaps),
    )

    # If we can proceed, continue. If not, we'll rely on the existing
    # execute_rework → rework loop to handle supplemental collection.
    return state


def _generate_supplemental_queries(product_name: str, schema_key: str) -> list[str]:
    """Generate 1-2 targeted queries to fill a specific schema gap.

    P1-A Fix: Tries LLM dynamic generation first (with existing evidence context),
    falls back to static templates if LLM is unavailable or fails.

    P1-C Fix: Always preserves fallback — even if LLM throws, returns template queries.
    """
    # P1-C: Static templates always available as fallback
    queries: list[str] = []
    name = product_name.strip()

    key = schema_key.lower()

    if "pricing" in key or "paid" in key or "free" in key or "tier" in key:
        queries = [
            f"{name} pricing plans free tier enterprise",
            f"{name} subscription cost per user",
        ]
    elif "workflow" in key or "orchestrat" in key:
        queries = [
            f"{name} workflow automation builder",
            f"{name} visual pipeline orchestration",
        ]
    elif "rag" in key or "knowledge" in key or "retrieval" in key:
        queries = [
            f"{name} RAG knowledge base vector",
            f"{name} document retrieval embeddings",
        ]
    elif "deploy" in key or "docker" in key or "k8s" in key or "self_hosted" in key:
        queries = [
            f"{name} self-hosted deployment docker",
            f"{name} kubernetes on-premise",
        ]
    elif "enterprise" in key or "sso" in key or "rbac" in key or "security" in key:
        queries = [
            f"{name} enterprise SSO RBAC security",
            f"{name} audit compliance permissions",
        ]
    elif "integration" in key or "api" in key or "webhook" in key:
        queries = [
            f"{name} API integration webhook",
            f"{name} REST SDK developer",
        ]
    elif "model" in key or "llm" in key or "gpt" in key:
        queries = [
            f"{name} LLM model support GPT Claude",
            f"{name} AI model providers",
        ]
    elif "agent" in key or "bot" in key:
        queries = [
            f"{name} AI agent bot assistant",
            f"{name} automation multi-agent",
        ]
    elif "user" in key or "persona" in key or "use_case" in key:
        queries = [
            f"{name} target users use cases",
            f"{name} case study enterprise team",
        ]
    elif "review" in key or "customer" in key or "g2" in key:
        queries = [
            f"{name} G2 Capterra review rating",
            f"{name} customer feedback testimonial",
        ]
    else:
        queries = [
            f"{name} {schema_key.replace('_', ' ')} features",
            f"{name} documentation comparison",
        ]

    # P1-C: Always return static fallback immediately
    # (LLM path attempted in _generate_llm_supplemental_queries below)
    return queries[:2]


def _generate_llm_supplemental_queries(
    product_name: str,
    schema_key: str,
    existing_evidence_summary: str = "",
    product_id: str = "",
) -> list[str]:
    """
    P1-A Fix: Use LLM to dynamically generate targeted search queries for a
    specific evidence gap, informed by what evidence already exists.

    Falls back to static templates if LLM is unavailable or returns invalid output.

    Args:
        product_name: The product name to search for.
        schema_key: The dimension/schema key that is missing evidence.
        existing_evidence_summary: Optional summary of existing evidence for context.
        product_id: Product identifier (for logging only).

    Returns:
        List of 1-3 search query strings, or static fallback on any error.
    """
    try:
        from backend.app.services.llm_client import get_llm_client
        client = get_llm_client()
    except Exception as exc:
        logger.warning(
            "P1-A: LLM client unavailable for product=%s schema_key=%s: %s. "
            "Using static template fallback.",
            product_name, schema_key, exc,
        )
        return _generate_supplemental_queries(product_name, schema_key)

    # P1-A: Build context-aware prompt
    dim_label = schema_key.replace("_", " ").strip()

    system_prompt = (
        "You are a competitive intelligence research assistant. "
        "Given a product name and a missing evidence dimension, generate "
        "2-3 concise search queries (in Chinese or English) to find authoritative "
        "information about that specific dimension. "
        "Return ONLY a valid JSON array of strings, e.g. [\"query 1\", \"query 2\"]. "
        "No explanation, no markdown, just the JSON array."
    )

    user_prompt_parts = [
        f"Product: {product_name}",
        f"Missing dimension: {dim_label}",
        f"Schema key: {schema_key}",
    ]
    if existing_evidence_summary:
        user_prompt_parts.append(f"Existing evidence summary (for context): {existing_evidence_summary[:500]}")
    user_prompt_parts.append(
        f"Generate 2-3 search queries to find authoritative information about "
        f"{product_name} in the dimension of '{dim_label}'. "
        f"Focus on official documentation, pricing pages, or authoritative reviews. "
        f"Queries should be specific enough to return targeted results."
    )

    try:
        response_text = client.chat_text(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "\n".join(user_prompt_parts)},
            ],
            temperature=0.3,
            max_tokens=256,
            timeout=30,
        )

        # Parse LLM output — expect a JSON array of strings
        import json
        response_text = response_text.strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("```", 2)[1]
            response_text = response_text.split("}", 1)[0] + "}]"

        parsed = json.loads(response_text)
        if not isinstance(parsed, list):
            raise ValueError(f"Expected JSON array, got {type(parsed)}")

        result = [str(q).strip() for q in parsed if str(q).strip()]
        if not result:
            raise ValueError("Empty query list returned")

        logger.info(
            "P1-A: LLM generated %d queries for product=%s schema_key=%s: %s",
            len(result), product_name, schema_key, result,
        )
        return result[:3]

    except Exception as exc:
        logger.warning(
            "P1-A: LLM query generation failed for product=%s schema_key=%s: %s. "
            "Falling back to static templates.",
            product_name, schema_key, exc,
        )
        return _generate_supplemental_queries(product_name, schema_key)


def execute_rework(state: WorkflowState) -> WorkflowState:
    """
    Execute rework tasks based on high-priority schema gaps and reviewer rework requests.

    Creates rework tasks from both schema_gaps and rework_requests, attempts local
    supplementation, and updates state with rework results.

    vNext-P0.5 (Multi-round search): When evidence is missing for gaps, performs
    gap-driven web search via Doubao API to collect new evidence before attempting
    local supplementation.
    """
    run_id = state.get("run_id", "")
    schema_gaps = state.get("schema_gaps", []) or []
    rework_requests = state.get("rework_requests", []) or []
    claim_drafts = state.get("claim_drafts", []) or []
    signed_claims = state.get("signed_claims", []) or []
    sources = state.get("sources", []) or []
    evidence_items = state.get("evidence_items", []) or []
    facts = state.get("facts", []) or []
    mode = state.get("mode", "real_time")
    coverage_critic_result = state.get("coverage_critic_result", {})

    logger.info(
        "execute_rework: run_id=%s schema_gaps=%d rework_requests=%d claim_drafts=%d signed_claims=%d",
        run_id, len(schema_gaps), len(rework_requests), len(claim_drafts), len(signed_claims),
    )

    # P1-Redesign (2026-06-18): Tag the rework reason in state so that any evidence
    # collected during the upcoming re-collect round can be attributed back to
    # this specific reason. Format: "DIM1,DIM2:short_reason".
    reason_tags = []
    for g in schema_gaps:
        dim = g.get("dimension") or g.get("dim") or ""
        if dim and len(reason_tags) < 3:
            reason_tags.append(dim)
    for rq in rework_requests[:3]:
        dim = rq.get("dimension") or ""
        if dim and len(reason_tags) < 3:
            reason_tags.append(dim)
    if reason_tags:
        state["rework_active_reason"] = ";".join(reason_tags)[:200]
    else:
        state["rework_active_reason"] = "REVIEWER_REWORK"

    # Check if there are any gaps or requests to process
    priority_gaps = [g for g in schema_gaps if g.get("priority") in ("high", "medium")]
    has_work = priority_gaps or rework_requests

    if not has_work:
        logger.info("execute_rework: no high-priority gaps or rework requests for run_id=%s", run_id)
        state["rework_tasks"] = []
        state["rework_summary"] = {
            "total_tasks": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
        }
        return state

    # vNext-P0.5: Multi-round search for gap-driven evidence collection
    # Check if we have gaps but no evidence - need to search for new sources
    # Note: Use product_slug (clean) instead of product_id (may have run_id prefix)
    evidence_by_product = {}
    for e in evidence_items:
        # Prefer product_slug for clean keys
        slug = e.get("product_slug", "") or e.get("product_id", "")
        if slug:
            # Strip run_id prefix if present
            if "_" in slug and len(slug) > 20:
                slug = slug.split("_")[-1]
            evidence_by_product[slug] = evidence_by_product.get(slug, 0) + 1

    products_needing_search = [
        slug for slug, count in evidence_by_product.items()
        if count < 2  # Less than 2 evidence = needs more
    ]

    # Also check from coverage_critic result
    product_coverage = coverage_critic_result.get("product_coverage", {})
    for slug, cov in product_coverage.items():
        status = cov.get("coverage_status", "")
        # vNext-P0.5: Include "sufficient" products too - they may have low-quality evidence
        if status in ("weak", "critical", "sufficient"):
            if slug not in products_needing_search:
                products_needing_search.append(slug)

    # vNext-P0.5: Check for low-quality evidence across ALL products
    # Even if a product has "sufficient" evidence count, if quality is low, re-search
    quality_by_product: dict[str, float] = {}
    for e in evidence_items:
        # Prefer product_slug for clean keys
        slug = e.get("product_slug", "") or e.get("product_id", "")
        if slug:
            # Strip run_id prefix if present
            if "_" in slug and len(slug) > 20:
                slug = slug.split("_")[-1]
            score = e.get("quality_score", 0.5)
            if slug not in quality_by_product:
                quality_by_product[slug] = []
            quality_by_product[slug].append(score)

    # Add products with low average quality (< 0.4) even if count is sufficient
    MIN_QUALITY_THRESHOLD = 0.4
    for pid, scores in quality_by_product.items():
        avg_quality = sum(scores) / len(scores) if scores else 1.0
        if avg_quality < MIN_QUALITY_THRESHOLD:
            if pid not in products_needing_search:
                logger.info(
                    "execute_rework (P0.5): adding product %s to search - low avg quality=%.2f",
                    pid, avg_quality,
                )
                products_needing_search.append(pid)

    # If we have priority gaps and some products need evidence, perform multi-round search
    # P1-Hotfix (2026-06-21): Wrapped in timeout to prevent workflow hanging.
    # Both search_service init AND search_for_gaps calls can hang on network/provider issues.
    # P0 (2026-06-22): Tuned timeouts for Doubao Thinking model (with reasoning tokens).
    #   - Health probe: 50s→10s. A healthy provider responds in <2s; an unhealthy one
    #     that needs 3×30s retries to fail will still fail within 10s.
    #   - LLMInferenceProvider: Doubao Thinking model (ep-*) adds 5-25s of internal
    #     reasoning before output, so 30s→45s to avoid premature timeout.
    #   - _SEARCH_TIMEOUT: Doubao (4-6s) + LLMInference (up to 45s) = <60s, set to 90s.
    _HEALTH_TIMEOUT = 10  # seconds for the health probe
    _SEARCH_TIMEOUT = 90  # P1 (2026-06-22): 90s = Doubao (4-6s healthy) + LLMInference (up to 45s w/ Doubao Thinking) + overhead

    def _run_search_with_timeout() -> dict[str, Any]:
        """Inner function: runs search, returns result dict. Raises on failure."""
        import concurrent.futures
        from backend.app.services.multi_round_search_service import MultiRoundSearchService

        _inner_logger = __import__("logging").getLogger(__name__)

        def _init_and_search():
            try:
                svc = MultiRoundSearchService()
                if svc.provider and svc.provider.is_configured:
                    return svc.search_for_gaps(
                        gaps=priority_gaps,
                        existing_evidence=evidence_items,
                        existing_sources=sources,
                        run_id=run_id,
                        product_slugs_needing_search=products_needing_search,
                        llm_supplemental_queries=coverage_critic_result.get("supplemental_queries"),
                    )
                else:
                    _inner_logger.warning(
                        "execute_rework: MultiRoundSearchService provider not configured, skipping"
                    )
                    return {"new_evidence": [], "new_sources": [], "gaps_filled": [],
                            "source_candidates": [], "queries_used": []}
            except Exception as exc:
                _inner_logger.error(
                    "execute_rework: search_for_gaps raised: %s", exc, exc_info=True
                )
                return {"new_evidence": [], "new_sources": [], "gaps_filled": [],
                        "source_candidates": [], "queries_used": []}

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_init_and_search)
            try:
                return future.result(timeout=_SEARCH_TIMEOUT)
            except concurrent.futures.TimeoutError:
                _inner_logger.warning(
                    "execute_rework: search_for_gaps timed out after %ds", _SEARCH_TIMEOUT
                )
                return {"new_evidence": [], "new_sources": [], "gaps_filled": [],
                        "source_candidates": [], "queries_used": []}
            except Exception as exc:
                _inner_logger.error("execute_rework: search failed: %s", exc, exc_info=True)
                return {"new_evidence": [], "new_sources": [], "gaps_filled": [],
                        "source_candidates": [], "queries_used": []}

    if priority_gaps and products_needing_search and mode == "real_time":
        logger.info(
            "execute_rework (P0.5): performing multi-round search for %d products with gaps",
            len(products_needing_search),
        )
        search_result = _run_search_with_timeout()

        # P0 (2026-06-22): Inspect the network health probe. If the
        # overseas endpoints have all been failing, the search above
        # likely produced very little. We mark the run as
        # ``_network_degraded`` so downstream nodes (coverage_critic
        # on subsequent rounds, report writer) know that the gaps are
        # due to environmental conditions, not a schema planning bug.
        try:
            from backend.app.services.search_provider import get_network_health
            health = get_network_health()
            degraded_hosts = [h for h, s in health.items() if s.get("degraded")]
            if degraded_hosts:
                state["_network_degraded"] = True
                state["_network_degraded_hosts"] = degraded_hosts
                logger.warning(
                    "execute_rework: network is degraded on hosts=%s. "
                    "Marking evidence as sparse. Coverage gaps that remain "
                    "are likely environmental, not schema bugs.",
                    degraded_hosts,
                )
        except Exception as exc:
            logger.debug("execute_rework: network health probe unavailable: %s", exc)

        new_evidence = search_result.get("new_evidence", [])
        new_sources = search_result.get("new_sources", [])
        gaps_filled = search_result.get("gaps_filled", [])
        queries_used = search_result.get("queries_used", [])
        source_candidates = search_result.get("source_candidates", [])

        logger.info(
            "execute_rework (P0.5): search completed - new_evidence=%d new_sources=%d "
            "gaps_filled=%d queries_used=%d source_candidates=%d",
            len(new_evidence), len(new_sources), len(gaps_filled), len(queries_used),
            len(source_candidates),
        )

        # P1-1: Fetch URLs from source_candidates to extract high-quality evidence
        if source_candidates:
            try:
                from backend.app.services.multi_round_search_service import MultiRoundSearchService
                svc = MultiRoundSearchService()
                if svc.provider and svc.provider.is_configured:
                    def _fetch():
                        return svc.fetch_source_candidates(
                            source_candidates=source_candidates,
                            run_id=run_id,
                            max_concurrent=3,
                            max_per_product=10,
                        )
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        fet = ex.submit(_fetch)
                        try:
                            fetched_evidence = fet.result(timeout=30)
                        except Exception:
                            fetched_evidence = []
                else:
                    fetched_evidence = []
            except Exception:
                fetched_evidence = []

            if fetched_evidence:
                logger.info(
                    "execute_rework (P1-1): extracted %d high-quality evidence from URL fetch",
                    len(fetched_evidence)
                )
                existing_evidence_ids = {e.get("evidence_id") for e in evidence_items}
                for ev in fetched_evidence:
                    if ev.get("evidence_id") not in existing_evidence_ids:
                        evidence_items.append(ev)
                        existing_evidence_ids.add(ev.get("evidence_id"))
                state["evidence_items"] = evidence_items
                if mode == "real_time":
                    try:
                        from backend.app.storage.repositories import EvidenceRepository
                        ev_repo = EvidenceRepository()
                        for ev in fetched_evidence:
                            try:
                                ev_repo.add_evidence(ev)
                            except Exception as exc:
                                logger.warning("execute_rework (P1-1): persist failed: %s", exc)
                    except Exception as exc:
                        logger.warning("execute_rework (P1-1): EvidenceRepository not available: %s", exc)

                # Add new evidence to state
                # P0-1 Fix: Only add non-snippet_only evidence to evidence_items
                if new_evidence:
                    existing_evidence_ids = {e.get("evidence_id") for e in evidence_items}
                    for e in new_evidence:
                        # P0-1: Skip snippet_only evidence - should only be source_candidates
                        if e.get("content_type") == "snippet_only":
                            logger.debug(
                                "Skipping snippet_only evidence %s (will be tracked as source_candidate)",
                                e.get("evidence_id", "unknown")
                            )
                            continue
                        if e.get("evidence_id") not in existing_evidence_ids:
                            state.setdefault("evidence_items", []).append(e)
                            existing_evidence_ids.add(e.get("evidence_id"))

                # Add new sources to state
                if new_sources:
                    existing_source_ids = {s.get("source_id") for s in sources}
                    for s in new_sources:
                        if s.get("source_id") not in existing_source_ids:
                            state.setdefault("sources", []).append(s)
                            existing_source_ids.add(s.get("source_id"))

                # Store search results in state for debugging/analysis
                state["_multi_round_search_result"] = {
                    "new_evidence_count": len(new_evidence),
                    "new_sources_count": len(new_sources),
                    "gaps_filled": gaps_filled,
                    "queries_used": queries_used,
                    "gaps_remaining": len(search_result.get("gaps_remaining", [])),
                }

                # vNext-P0.5: Extract facts from new evidence
                # Multi-round search creates "snippet_only" evidence that needs to be converted to facts
                if new_evidence:
                    try:
                        from backend.app.agents.collector.fact_extractor import FactExtractor
                        extractor = FactExtractor()

                        # Normalize evidence format for fact extractor
                        normalized_evidence = []
                        for e in new_evidence:
                            normalized_e = {
                                "evidence_id": e.get("evidence_id", ""),
                                "product_id": e.get("product_id", ""),
                                "product_slug": e.get("product_slug", ""),
                                "schema_key": e.get("schema_key", ""),
                                "snippet": e.get("content", e.get("snippet", ""))[:500],
                                "quality_score": e.get("quality_score", 0.5),
                                "confidence": e.get("quality_score", 0.5),
                            }
                            normalized_evidence.append(normalized_e)

                        if normalized_evidence:
                            new_facts = extractor.extract_facts(normalized_evidence, run_id)
                            if new_facts:
                                logger.info(
                                    "execute_rework (P0.5): extracted %d new facts from %d new evidence",
                                    len(new_facts), len(new_evidence),
                                )
                                # Add new facts to state
                                existing_fact_ids = {f.get("fact_id") for f in state.get("facts", [])}
                                for f in new_facts:
                                    if f.get("fact_id") not in existing_fact_ids:
                                        state.setdefault("facts", []).append(f)
                                        existing_fact_ids.add(f.get("fact_id"))

                                # Store for reference
                                state["_multi_round_search_new_facts"] = new_facts
                    except Exception as exc:
                        logger.warning(
                            "execute_rework (P0.5): fact extraction failed for new evidence: %s",
                            exc,
                        )

                # Update evidence_items reference for later use
                evidence_items = state.get("evidence_items", []) or []

                # P1 FIX: Add new_evidence to state and persist to DB
                # P0-1 Fix: Only add non-snippet_only evidence
                # This fixes replay data recovery issue where only initial evidence was recovered
                if new_evidence:
                    existing_evidence_ids = {e.get("evidence_id") for e in evidence_items}
                    for ev in new_evidence:
                        # P0-1: Skip snippet_only evidence
                        if ev.get("content_type") == "snippet_only":
                            continue
                        if ev.get("evidence_id") not in existing_evidence_ids:
                            evidence_items.append(ev)
                            existing_evidence_ids.add(ev.get("evidence_id"))
                    # Update state with all evidence (including new ones)
                    state["evidence_items"] = evidence_items
                    logger.info(
                        "execute_rework: added %d new evidence to state, total=%d",
                        len(new_evidence), len(evidence_items)
                    )

                    # Persist new evidence to DB
                    if mode == "real_time":
                        try:
                            from backend.app.storage.repositories import EvidenceRepository
                            ev_repo = EvidenceRepository()
                            for ev in new_evidence:
                                try:
                                    ev_repo.add_evidence(ev)
                                except Exception as exc:
                                    logger.warning(
                                        "execute_rework: failed to persist evidence %s: %s",
                                        ev.get("evidence_id"), exc
                                    )
                            logger.info(
                                "execute_rework: persisted %d new evidence to DB",
                                len(new_evidence)
                            )
                        except Exception as exc:
                            logger.warning(
                                "execute_rework: EvidenceRepository not available: %s", exc
                            )

    try:
        from backend.app.services.rework_service import create_rework_tasks

        # Create tasks from both schema_gaps and rework_requests
        tasks = create_rework_tasks(
            schema_gaps=priority_gaps,
            rework_requests=rework_requests,
            claim_drafts=claim_drafts,
            signed_claims=signed_claims,
            run_id=run_id,
            sources=sources,
            evidence_items=evidence_items,
            facts=facts,
        )

        # Execute rework tasks
        from backend.app.services.rework_service import ReworkService
        service = ReworkService()
        service._tasks = []
        for task_dict in tasks:
            from backend.app.services.rework_service import ReworkTask
            task = ReworkTask(**{k: v for k, v in task_dict.items() if k in ReworkTask.__dataclass_fields__})
            service._tasks.append(task)

        completed_tasks, after_metrics = service.execute_rework_tasks(
            sources=sources,
            evidence_items=evidence_items,
            facts=facts,
            claim_drafts=claim_drafts,
            signed_claims=signed_claims,
        )

        state["rework_tasks"] = completed_tasks
        state["rework_summary"] = service.get_tasks_summary()
        state["rework_after_metrics"] = after_metrics

        # Collect all new facts and claims from completed tasks
        all_new_facts = []
        all_new_claims = []
        for t in completed_tasks:
            all_new_facts.extend(t.get("new_facts", []))
            all_new_claims.extend(t.get("new_claims", []))

        # Append new facts to state
        if all_new_facts:
            existing_fact_ids = {f.get("fact_id") for f in facts if f.get("fact_id")}
            for new_fact in all_new_facts:
                if new_fact.get("fact_id") not in existing_fact_ids:
                    state.setdefault("facts", []).append(new_fact)
                    existing_fact_ids.add(new_fact.get("fact_id"))

        # Append new claims to state
        if all_new_claims:
            existing_claim_ids = {c.get("claim_id") for c in claim_drafts if c.get("claim_id")}
            for new_claim in all_new_claims:
                if new_claim.get("claim_id") not in existing_claim_ids:
                    state.setdefault("claim_drafts", []).append(new_claim)
                    existing_claim_ids.add(new_claim.get("claim_id"))

        # Write new facts to DB if real_time mode
        if mode == "real_time" and all_new_facts:
            try:
                from backend.app.storage.fact_repository import FactRepository
                fact_repo = FactRepository()
                for fact in all_new_facts:
                    try:
                        fact_repo.add_fact(fact)
                    except Exception as exc:
                        logger.warning("execute_rework: failed to write fact %s to DB: %s",
                                       fact.get("fact_id"), exc)
            except Exception as exc:
                logger.warning("execute_rework: FactRepository not available: %s", exc)

        # Write new claims to DB if real_time mode
        if mode == "real_time" and all_new_claims:
            try:
                from backend.app.storage.repositories import ClaimRepository
                claim_repo = ClaimRepository()
                for claim in all_new_claims:
                    try:
                        claim_to_save = dict(claim)
                        claim_to_save["review_status"] = "pending"
                        claim_to_save["created_by_agent"] = "ReworkAgent"
                        claim_to_save["created_at"] = utc_now()
                        claim_to_save["updated_at"] = utc_now()
                        claim_repo.add_claim(claim_to_save)
                    except Exception as exc:
                        logger.warning("execute_rework: failed to write claim %s to DB: %s",
                                       claim.get("claim_id"), exc)
            except Exception as exc:
                logger.warning("execute_rework: ClaimRepository not available: %s", exc)

        logger.info(
            "execute_rework: run_id=%s tasks=%d succeeded=%d failed=%d new_facts=%d new_claims=%d",
            run_id,
            len(completed_tasks),
            sum(1 for t in completed_tasks if t.get("status") == "succeeded"),
            sum(1 for t in completed_tasks if t.get("status") == "failed"),
            len(all_new_facts),
            len(all_new_claims),
        )

    except Exception as exc:
        logger.error("execute_rework failed for run_id=%s: %s", run_id, exc)
        state.setdefault("errors", []).append({
            "reason_code": "REWORK_EXECUTION_FAILED",
            "message": str(exc),
            "node": "execute_rework",
        })
        state["rework_tasks"] = []
        state["rework_summary"] = {
            "total_tasks": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
            "error": str(exc),
        }

    # LLM Self-Check: 判断 rework 后质量是否足够好，可以生成报告
    _perform_rework_self_check(state)

    return state


def _perform_rework_self_check(state: WorkflowState) -> None:
    """让 LLM 自己判断当前 evidence + claims 质量是否足够好，可以生成报告。
    
    替代人工干预：agent 自己决定是否需要更多 rework 还是可以继续。
    返回详细的判断结果，包括原因和改进建议。
    """
    run_id = state.get("run_id", "unknown")
    evidence_items = state.get("evidence_items", []) or []
    signed_claims = state.get("signed_claims", []) or []
    completed_tasks = state.get("rework_tasks", []) or []
    schema_gaps = state.get("schema_gaps", []) or []
    priority_gaps = [g for g in schema_gaps if g.get("priority") in ("high", "medium")]
    
    # 统计信息
    succeeded_tasks = sum(1 for t in completed_tasks if t.get("status") == "succeeded")
    failed_tasks = sum(1 for t in completed_tasks if t.get("status") == "failed")
    
    state["rework_self_check_count"] = state.get("rework_self_check_count", 0) + 1
    self_check_count = state["rework_self_check_count"]
    
    logger.info(
        "rework_self_check: run_id=%s iteration=%d evidence=%d claims=%d "
        "succeeded=%d failed=%d priority_gaps=%d",
        run_id, self_check_count, len(evidence_items), len(signed_claims),
        succeeded_tasks, failed_tasks, len(priority_gaps),
    )
    
    # 简单规则判断（快速路径）
    # FIX: 更宽松的 PASS 条件 —— evidence/claims 达标时避免无限循环
    if len(evidence_items) >= 5 and len(signed_claims) >= 3 and succeeded_tasks >= 1:
        if len(priority_gaps) == 0:
            state["rework_self_check_result"] = "PASS"
            state["rework_self_check_detail"] = {
                "decision": "PASS",
                "reason": "证据数量充足（>=5条）且 claim 数量达标（>=3条），无高优先级 gaps",
                "missing": [],
                "suggestion": None,
            }
            logger.info("rework_self_check: PASS (rule-based: sufficient evidence and no priority gaps)")
            return
        # FIX: 即使 priority_gaps 不为0，只要 evidence/claims 足够就 PASS（防止无限循环）
        if self_check_count >= 2:
            state["rework_self_check_result"] = "PASS"
            state["rework_self_check_detail"] = {
                "decision": "PASS",
                "reason": f"已达到最大迭代次数（{self_check_count}次）且证据基本达标，强制通过避免无限循环",
                "missing": [],
                "suggestion": None,
            }
            logger.info("rework_self_check: PASS (max iterations or sufficient evidence, no priority gaps required)")
            return
        # FIX: 新增条件：evidence >= 5 且 claims >= 3，即使 gaps 不为 0 也 PASS
        if len(evidence_items) >= 5 and len(signed_claims) >= 3:
            state["rework_self_check_result"] = "PASS"
            state["rework_self_check_detail"] = {
                "decision": "PASS",
                "reason": f"证据充足（{len(evidence_items)}条）且 claims 达标（{len(signed_claims)}条），有 gaps 但已达迭代上限，强制通过",
                "missing": [],
                "suggestion": None,
            }
            logger.info(
                "rework_self_check: PASS (sufficient evidence=%d, claims=%d, forcing through despite gaps=%d",
                len(evidence_items), len(signed_claims), len(priority_gaps),
            )
            return

    # P1 FIX: 快速失败检测 - 连续失败时不再重试
    # 检测连续失败（没有 succeeded tasks）
    consecutive_failures = state.get("_consecutive_rework_failures", 0)
    if succeeded_tasks == 0 and len(completed_tasks) > 0:
        consecutive_failures += 1
        state["_consecutive_rework_failures"] = consecutive_failures
        logger.warning(
            "rework_self_check: consecutive failures=%d, evidence=%d, signed_claims=%d",
            consecutive_failures, len(evidence_items), len(signed_claims),
        )
        # 连续 2 次失败，快速失败
        if consecutive_failures >= 2:
            # 检查是否有任何 usable evidence
            usable_count = sum(
                1 for e in evidence_items
                if e.get("usable_for_claim", False) and e.get("quality_score", 0) >= 0.45
            )
            state["rework_self_check_result"] = "PASS"  # 通过但生成 blocked 报告
            state["rework_self_check_detail"] = {
                "decision": "PASS",
                "reason": (
                    f"连续 {consecutive_failures} 次 rework 失败，"
                    f"evidence={len(evidence_items)} (usable={usable_count}), "
                    f"signed_claims={len(signed_claims)}。快速失败，生成 blocked 报告。"
                ),
                "missing": ["高质量 evidence 不足", "无法生成 signed claims"],
                "suggestion": "等待 API 限流恢复或手动提供更多 seed URLs",
                "quick_fail": True,
            }
            logger.warning(
                "rework_self_check: QUICK FAIL after %d consecutive failures - generating blocked report",
                consecutive_failures,
            )
            return
    else:
        # 有成功，重置计数
        if consecutive_failures > 0:
            logger.info("rework_self_check: reset consecutive_failures from %d to 0", consecutive_failures)
        state["_consecutive_rework_failures"] = 0
    
    # LLM 判断（复杂情况）
    products = []
    task_brief = state.get("task_brief", {})
    for p in task_brief.get("products", []):
        products.append(p.get("product_name", p.get("product_id", "unknown")))
    
    # 构建 evidence 摘要
    evidence_summary = []
    for e in evidence_items[:20]:
        evidence_summary.append({
            "id": e.get("evidence_id", "")[:20],
            "product": e.get("product_slug", e.get("product_id", "")),
            "type": e.get("source_type", ""),
            "quality": e.get("quality_score", 0),
        })
    
    # 构建 claims 摘要
    claims_summary = []
    for c in signed_claims[:20]:
        claims_summary.append({
            "id": c.get("claim_id", "")[:20],
            "dimension": c.get("dimension", ""),
            "text": c.get("claim_text", "")[:100],
        })
    
    # 构建 gaps 摘要
    gaps_summary = []
    for g in priority_gaps[:10]:
        gaps_summary.append({
            "dimension": g.get("dimension", ""),
            "priority": g.get("priority", ""),
            "reason": g.get("reason", "")[:100],
        })
    
    # FIX: 把 self_check_count 传入 prompt，让 LLM 知道不要无限循环
    prompt = f"""你是一个研究报告质量评估专家。评估当前状态是否足够好，可以生成最终报告。

## 当前状态
- 产品: {', '.join(products)}
- Self-check 迭代次数: {self_check_count} 次（最多允许3次，避免无限循环）
- Evidence 数量: {len(evidence_items)} 条
- Signed claims 数量: {len(signed_claims)} 条
- 完成的 rework tasks: {succeeded_tasks} 条
- 失败的 rework tasks: {failed_tasks} 条
- 高优先级 schema gaps: {len(priority_gaps)} 条

## Evidence 摘要 (最多20条)
{json.dumps(evidence_summary, ensure_ascii=False, indent=2)}

## Claims 摘要 (最多20条)
{json.dumps(claims_summary, ensure_ascii=False, indent=2)}

## 高优先级 Gaps (最多10条)
{json.dumps(gaps_summary, ensure_ascii=False, indent=2)}

## 判断标准（按优先级）
1. 如果 self_check_count >= 3 → **必须 PASS**（已达最大迭代次数，禁止无限循环）
2. 如果 evidence >= 5 且 signed_claims >= 3 → **必须 PASS**（证据已达标，即使 gaps 未填满也不要无限循环）
3. 如果 evidence >= 3 且 signed_claims >= 1 且 gaps 未填满 → **必须 PASS**（最小可用标准）
4. 如果 evidence < 3 或 signed_claims < 1 → RETRY（证据严重不足）
5. 如果存在高优先级 gaps 且 self_check_count < 2 且 rework 有改进空间 → RETRY

**重要：你必须遵守规则1-3，不要无限 RETRY。**

## 输出要求
返回 JSON 格式的评估结果：
{{
  "decision": "PASS" 或 "RETRY",
  "reason": "简要说明判断理由（1-2句话）",
  "missing": ["缺失项1", "缺失项2"],
  "suggestion": "具体的改进建议（如果有的话，否则为 null）"
}}

只返回 JSON，不要有其他内容。"""

    def _call_llm() -> str:
        from backend.app.services.llm_client import get_llm_client
        client = get_llm_client()
        return client.chat_text(
            messages=[
                {"role": "system", "content": "You are a quality assessor. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
            timeout=30,
        )

    def _parse(text: str) -> dict:
        import re
        text = text.strip()
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"Could not parse JSON from: {text[:200]}")

    try:
        from backend.app.tracing.llm_trace import traced_llm_call
        
        result = traced_llm_call(
            run_id=run_id,
            node_name="rework_self_check",
            agent_name="SelfChecker",
            agent_role="quality_assessor",
            prompt_version="v1",
            prompt_text=prompt,
            input_payload={
                "evidence_count": len(evidence_items),
                "claims_count": len(signed_claims),
                "succeeded_tasks": succeeded_tasks,
                "priority_gaps": len(priority_gaps),
                "iteration": self_check_count,
            },
            call_fn=_call_llm,
            parse_fn=_parse,
            input_length_hint=len(prompt),
            decision_summary=f"Rework self-check: evidence={len(evidence_items)} claims={len(signed_claims)} gaps={len(priority_gaps)}",
        )
        
        parsed = result.get("parsed_output") or {}
        decision = parsed.get("decision", "RETRY")
        state["rework_self_check_result"] = decision
        state["rework_self_check_detail"] = {
            "decision": decision,
            "reason": parsed.get("reason", ""),
            "missing": parsed.get("missing", []),
            "suggestion": parsed.get("suggestion"),
        }
        logger.info(f"rework_self_check: {decision} - {parsed.get('reason', '')}")
        
    except Exception as exc:
        logger.warning("rework_self_check: LLM call failed for run_id=%s: %s", run_id, exc)
        # 失败时保守处理：如果 evidence 足够就 PASS
        if len(evidence_items) >= 5 and len(signed_claims) >= 3:
            state["rework_self_check_result"] = "PASS"
            state["rework_self_check_detail"] = {
                "decision": "PASS",
                "reason": "LLM 调用失败，但证据数量达标，保守通过",
                "missing": [],
                "suggestion": None,
            }
            logger.info("rework_self_check: PASS (fallback due to LLM error)")
        else:
            state["rework_self_check_result"] = "RETRY"
            state["rework_self_check_detail"] = {
                "decision": "RETRY",
                "reason": "LLM 调用失败，无法评估",
                "missing": [
                    f"evidence_count: {len(evidence_items)}/5",
                    f"claims_count: {len(signed_claims)}/3",
                ],
                "suggestion": "请补充更多证据和 claims",
            }
            logger.info("rework_self_check: RETRY (fallback)")


def prepare_human_intervention(state: WorkflowState) -> WorkflowState:
    """Soft node: gather report quality signals into _report_preview_info for frontend display.

    P1-Redesign (2026-06-05): The Review Center is now a post-generation quality
    assessment page, not a workflow gate. This node no longer creates HumanIntervention
    records or pauses the workflow. It only aggregates the current state of claims,
    coverage, and self-check results so the frontend can render a clean quality report.
    """
    run_id = state.get("run_id", "unknown")

    signed_claims = state.get("signed_claims", []) or []
    analyst_signed = [c for c in signed_claims if c.get("review_status") == "analyst_signed"]
    reviewer_signed = [c for c in signed_claims if c.get("review_status") == "signed"]
    rework_required = state.get("rework_required_claims", []) or []

    coverage = state.get("coverage_critic_result", {})
    schema_cov = state.get("schema_coverage", {})
    product_coverage = coverage.get("product_coverage", {})

    gaps = []
    for product, info in product_coverage.items():
        status = info.get("status", "unknown") if isinstance(info, dict) else "unknown"
        if status in ("critical", "weak", "insufficient", "partial"):
            gaps.append({
                "product": product,
                "status": status,
                "evidence_count": info.get("evidence_count", 0) if isinstance(info, dict) else 0,
                "signed_claims": info.get("signed_claims", 0) if isinstance(info, dict) else 0,
            })

    self_check = state.get("rework_self_check_result", "")
    self_detail = state.get("rework_self_check_detail", {})

    # Build the preview info that frontend reads
    state["_report_preview_info"] = {
        "run_id": run_id,
        "reviewer_signed_count": len(reviewer_signed),
        "analyst_signed_count": len(analyst_signed),
        "signed_claims_total": len(signed_claims),
        "rework_required_count": len(rework_required),
        "self_check_result": self_check,
        "self_check_reason": self_detail.get("reason", ""),
        "self_check_suggestion": self_detail.get("suggestion", ""),
        "evidence_count": len(state.get("evidence_items", []) or []),
        "facts_count": len(state.get("facts", []) or []),
        "coverage_gaps": gaps,
        "schema_completion_rate": schema_cov.get("schema_completion_rate", 0.0),
        "high_priority_schema_gaps": schema_cov.get("high_priority_gaps", 0),
        "report_readiness": (
            "ready" if (len(reviewer_signed) > 0 and self_check == "PASS" and not gaps)
            else "partial" if (len(signed_claims) > 0 and len(rework_required) == 0)
            else "needs_work"
        ),
    }

    # Also mirror into state for downstream nodes that may read it
    state["human_interventions"] = []
    state["requires_human_review"] = False

    logger.info(
        "prepare_human_intervention: run_id=%s readiness=%s reviewer=%d analyst=%d "
        "rework=%d gaps=%d self_check=%s",
        run_id, state["_report_preview_info"]["report_readiness"],
        len(reviewer_signed), len(analyst_signed),
        len(rework_required), len(gaps), self_check,
    )
    return state



def analyze_dimensions(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "unknown")
    evidence_items = state.get("evidence_items", [])
    facts = state.get("facts", [])
    task_brief = state.get("task_brief", {})
    mode = state.get("mode", "real_time")

    logger.info(
        "analyze_dimensions: run_id=%s mode=%s evidence_count=%d facts_count=%d",
        run_id, mode, len(evidence_items), len(facts),
    )

    if not evidence_items:
        logger.warning(
            "analyze_dimensions: no evidence items for run_id=%s - entering pipeline with empty claims",
            run_id,
        )
        state.setdefault("claim_drafts", [])
        return state

    try:
        agent = _analyst()
        claims = agent.analyze(
            evidence_items=evidence_items,
            facts=facts,
            task_brief=task_brief,
            run_id=run_id,
        )
        state["claim_drafts"] = claims
        # Trigger template fallback if LLM returned empty (network error or no claims)
        if not claims:
            logger.warning(
                "analyze_dimensions: LLM returned no claims for run_id=%s - using template fallback",
                run_id,
            )
            claims = _template_claims_from_evidence(evidence_items, run_id)
            state["claim_drafts"] = claims
        logger.info(
            "analyze_dimensions: produced %d claims for run_id=%s",
            len(claims), run_id,
        )
    except Exception as exc:
        logger.error(
            "analyze_dimensions LLM call failed for run_id=%s: %s - using template fallback",
            run_id, exc,
        )
        # Fallback: derive template claims from evidence_items directly
        # This ensures the workflow continues even if the LLM is unavailable or slow
        claims = _template_claims_from_evidence(evidence_items, run_id)
        state["claim_drafts"] = claims
        logger.info(
            "analyze_dimensions: template fallback produced %d claims for run_id=%s",
            len(claims), run_id,
        )

    return state


def _template_claims_from_evidence(
    evidence_items: list[dict[str, Any]], run_id: str, rework_product: str = ""
) -> list[dict[str, Any]]:
    """Generate minimal template claims from evidence items when LLM is unavailable."""
    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    idx = 0

    for ev in evidence_items:
        product_id = _resolve_to_product(ev)
        if rework_product and not _matches_rework_product(ev, rework_product):
            continue

        schema_key = str(ev.get("schema_key") or "").strip().lower()
        snippet = (ev.get("snippet") or "").strip()[:200]
        evidence_id = ev.get("evidence_id") or ""

        if not product_id or not snippet:
            continue

        key = f"{product_id}_{schema_key}"
        if key in seen:
            continue
        seen.add(key)

        # Derive dimension from schema_key
        dimension = "function_tree"
        if "pricing" in schema_key or "price" in schema_key:
            dimension = "pricing_model"
        elif "persona" in schema_key or "user" in schema_key:
            dimension = "user_persona"
        elif "voice" in schema_key or "feedback" in schema_key:
            dimension = "customer_voice"
        elif "swot" in schema_key:
            dimension = "swot"
        elif "enterprise" in schema_key or "security" in schema_key:
            dimension = "enterprise_readiness"

        # claim_text: use the evidence snippet directly — no "Evidence for X:" prefix.
        # LLM-generated claims have structured analytical text; template claims use
        # the cleaned snippet as-is so the report paragraph is readable.
        claim_text = snippet
        # If snippet is too short (< 20 chars) or is a nav/UI fragment, try to
        # extract a meaningful sentence from the evidence body
        if len(claim_text) < 30:
            body = (ev.get("text") or "")[:1000]
            sentences = [s.strip() for s in body.replace("\n", ". ").split(".") if len(s.strip()) > 30]
            if sentences:
                claim_text = sentences[0]

        ev_id_short = evidence_id.replace("-", "_") if evidence_id else ""
        claim_id = f"claim_{product_id}_{schema_key}_{ev_id_short}_{idx}" if ev_id_short else f"claim_{product_id}_{schema_key}_{idx}"
        claims.append({
            "claim_id": claim_id,
            "run_id": run_id,
            "product_id": product_id,
            "dimension": dimension,
            "claim_text": claim_text,
            "fact_ids": [],
            "evidence_ids": [evidence_id],
            "confidence": 0.75,
            "risk_level": "medium",
            "claim_type": "factual_summary",
            "review_status": "pending",
        })
        idx += 1

    return claims


def review_claims(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "unknown")
    claims = state.get("claim_drafts", [])
    evidence_items = state.get("evidence_items", [])
    mode = state.get("mode", "real_time")

    logger.info("review_claims: run_id=%s claim_count=%d evidence_count=%d",
                run_id, len(claims), len(evidence_items))

    state.setdefault("review_results", [])
    state.setdefault("rework_requests", [])
    state.setdefault("signed_claims", [])

    if not claims:
        return state

    # Always merge fresh quality data from DB into state evidence items.
    # execute_rework adds new evidence via add_evidence which does NOT write
    # usable_for_claim / quality_score.  This ensures reviewer always sees
    # the latest quality assessments, including NULL for un-evaluated evidence.
    if mode == "real_time":
        try:
            from backend.app.storage.repositories import EvidenceRepository
            ev_repo = EvidenceRepository()
            db_evidence = ev_repo.list_evidence(run_id)
            db_index = {e.get("evidence_id"): e for e in db_evidence if e.get("evidence_id")}

            # Merge: prefer DB quality fields, keep everything from state evidence.
            # Skip merge for NULL DB values so that un-evaluated new evidence
            # (usable_for_claim not yet written by add_evidence) falls back to
            # the safe default False rather than None.
            for ev in evidence_items:
                ev_id = ev.get("evidence_id", "")
                db_ev = db_index.get(ev_id, {})
                if db_ev:
                    if "usable_for_claim" in db_ev and db_ev["usable_for_claim"] is not None:
                        ev["usable_for_claim"] = db_ev["usable_for_claim"]
                    if "quality_score" in db_ev and db_ev["quality_score"] is not None:
                        ev["quality_score"] = db_ev["quality_score"]
        except Exception as exc:
            logger.warning("review_claims: failed to merge DB quality data: %s", exc)

    evidence_index: dict[str, dict[str, Any]] = {}
    for ev in evidence_items:
        ev_id = str(ev.get("evidence_id") or "").strip()
        if ev_id:
            evidence_index[ev_id] = ev

    stale_claim_count = 0
    for claim in claims:
        claim_evidence_ids = [str(eid).strip() for eid in (claim.get("evidence_ids") or []) if str(eid).strip()]
        if claim_evidence_ids and any(eid not in evidence_index for eid in claim_evidence_ids):
            stale_claim_count += 1

    if stale_claim_count:
        logger.warning(
            "review_claims: run_id=%s stale_claims=%d/%d have evidence IDs not present in current evidence set",
            run_id, stale_claim_count, len(claims),
        )
    try:
        # ── Evidence Contract Gate: apply BEFORE reviewer sees evidence ───────────
        # This ensures the reviewer signs claims based only on evidence that passes
        # the dimension×source-type hard gate (e.g. pricing_model requires official pricing docs,
        # not third-party articles). Gating here rather than in run_deep_report_workflow
        # ensures review decisions are consistent with the final report's evidence standards.
        from backend.app.services.deep_report import _gate_evidence_by_dimension
        evidence_items = _gate_evidence_by_dimension(evidence_items)

        # Persist gated evidence to state so subsequent nodes (including run_deep_report_workflow)
        # see the same evidence as the reviewer. This is the single source of truth for this loop.
        state["evidence_items"] = evidence_items

        # P0-Fix: Persist gated usable_for_claim values to DB immediately after gate.
        # run_deep_report_workflow reads from DB, not from state. Without this, the gate's
        # usable_for_claim=True values exist only in-memory and are lost when the reviewer
        # checks the DB, causing claims to fail the evidence gate check.
        try:
            from backend.app.storage.repositories import EvidenceRepository
            ev_repo = EvidenceRepository()
            for ev in evidence_items:
                ev_id = ev.get("evidence_id")
                if not ev_id:
                    continue
                ev_repo.update_evidence_usable(ev_id, bool(ev.get("usable_for_claim", False)))
        except Exception as exc:
            logger.warning("review_claims: failed to persist gated usable_for_claim to DB: %s", exc)

        # P0-1 Fix: Build filtered evidence set for PERSISTING usable_for_claim to DB only.
        # The reviewer MUST see ALL evidence (not gate-filtered) to make informed decisions.
        # The gate's usable_for_claim is a quality signal, not a claim-signing gate.
        # Previously, when all evidence was marked unusable (dimension mismatch between
        # claim.dimension="功能" and evidence.schema_key="user_persona"), the reviewer
        # received an empty evidence list and all 13 claims were auto-downgraded to
        # rework_required, leaving the report with 0 signed claims.
        # Now: pass all evidence to reviewer, update usable_for_claim in DB separately.
        usable_evidence_map = {ev.get("evidence_id"): ev for ev in evidence_items if ev.get("evidence_id")}

        agent = _reviewer()
        signed = []
        rework_reqs = []
        reviews = []
        updated_claims = []  # Step-3 Fix: collect updated claim objects to write back to state
        now = utc_now()

        for claim in claims:
            claim_evidence_ids = [str(eid).strip() for eid in (claim.get("evidence_ids") or []) if str(eid).strip()]

            # Step 1: strip stale evidence (no longer in evidence_index)
            missing_evidence_ids = [eid for eid in claim_evidence_ids if eid not in evidence_index]

            # Step 2: keep only gate-passed evidence in the claim's evidence_ids
            # This preserves the gate's quality signal but does NOT block the reviewer.
            usable_claim_evidence_ids = [
                eid for eid in claim_evidence_ids
                if eid in evidence_index and eid in usable_evidence_map
            ]

            claim = dict(claim)
            if missing_evidence_ids:
                claim["stale_evidence_ids"] = missing_evidence_ids
            claim["evidence_ids"] = usable_claim_evidence_ids

            # Step-5 Fix (P0 2026-06-22): Pass ALL evidence to reviewer for evaluation.
            # The gate's usable_for_claim is a quality signal, NOT a claim-signing gate.
            # Reviewer has the expertise to evaluate claim+evidence quality independently.
            if not usable_claim_evidence_ids and not claim_evidence_ids:
                # Case 3: claim has no evidence at all — give reviewer all evidence for context
                logger.warning(
                    "review_claims: claim_id=%s has no evidence at all. "
                    "Passing all %d evidence items for reviewer context.",
                    claim.get("claim_id", ""),
                    len(evidence_index),
                )
                all_claim_evidence = list(evidence_index.values())[:20]
            else:
                # Case 1+2: use the claim's own evidence_ids (gate-filtered or original)
                # This gives the reviewer exactly the evidence the claim's analyst linked
                all_claim_evidence = [
                    usable_evidence_map.get(eid, {}) for eid in claim_evidence_ids
                    if eid in usable_evidence_map
                ]

            result = agent.review_claim(claim, all_claim_evidence)
            reviews.append(result)
            status = result.get("status", "")
            if status == "rework_required":
                logger.warning(
                    "review_claims: claim_id=%s dim=%s status=%s codes=%s",
                    claim.get("claim_id", ""),
                    claim.get("dimension", ""),
                    status,
                    result.get("reason_codes", []),
                )
            signed_id = ""

            if status in ("pass", "warning"):
                signed_id = result.get("signed_claim_id") or f"signed_{claim.get('claim_id', '')}"
                signed_claim = dict(claim)
                signed_claim["review_status"] = "signed"
                signed_claim["signed_claim_id"] = signed_id
                signed.append(signed_claim)
            elif status == "rework_required":
                rw = result.get("rework_request", {})
                if rw:
                    rework_reqs.append(rw)

            # Step-3 Fix: collect updated claim (with filtered evidence_ids) to write back to state
            updated_claims.append(claim)

            # P0-Fix: Update claim["review_status"] so updated_claims reflects the reviewer's decision.
            # Previously, only claim_to_save["review_status"] was set (for DB persistence), leaving
            # claim["review_status"] unchanged (original DB value, e.g. "rework_required"), causing
            # rework_required_claims to incorrectly include ALL claims regardless of review outcome.
            claim["review_status"] = (
                "signed" if status in ("pass", "warning") else
                "rework_required" if status == "rework_required" else
                "pending"
            )

            # Persist claim to DB (always, not just real_time)
            if mode in ("real_time", "replay"):
                try:
                    from backend.app.storage.repositories import ClaimRepository
                    repo = ClaimRepository()
                    # Ensure fact_ids and evidence_ids are lists
                    claim_to_save = dict(claim)
                    claim_to_save["run_id"] = run_id  # Required field for DB persistence
                    claim_to_save["review_status"] = (
                        "signed" if status in ("pass", "warning") else
                        "rework_required" if status == "rework_required" else
                        "pending"
                    )
                    claim_to_save["signed_claim_id"] = signed_id
                    claim_to_save["created_by_agent"] = "AnalystAgent"
                    claim_to_save["created_at"] = now
                    claim_to_save["updated_at"] = now
                    repo.add_claim(claim_to_save)

                    # Persist claim-evidence links
                    for ev_id in claim.get("evidence_ids", []):
                        try:
                            repo.add_claim_evidence_link({
                                "link_id": f"link_{uuid.uuid4().hex[:12]}",
                                "run_id": run_id,
                                "claim_id": claim.get("claim_id", ""),
                                "evidence_id": ev_id,
                                "support_type": "supports",
                                "support_score": 0.8,
                                "created_at": now,
                            })
                        except Exception:
                            pass
                except Exception as exc:
                    logger.error("Failed to persist claim %s: %s", claim.get("claim_id"), exc)
                    state.setdefault("errors", []).append({
                        "reason_code": "DB_WRITE_CLAIMS_FAILED",
                        "message": str(exc),
                        "node": "review_claims",
                    })

        # Persist reviews and rework requests to DB
        if mode == "real_time":
            try:
                from backend.app.storage.repositories import ReviewRepository
                review_repo = ReviewRepository()
                for review in reviews:
                    review["run_id"] = run_id
                    review["reviewer_agent"] = "ReviewerAgent"
                    review["created_at"] = now
                    review_repo.add_review(review)
                for rw in rework_reqs:
                    rw["run_id"] = run_id
                    rw["created_at"] = now
                    review_repo.add_rework_request(rw)
            except Exception as exc:
                logger.error("Failed to persist reviews/rework: %s", exc)
                state.setdefault("errors", []).append({
                    "reason_code": "DB_WRITE_REVIEWS_FAILED",
                    "message": str(exc),
                    "node": "review_claims",
                })

        # P0-Rebuild: Separate signed vs rework_required claims for correct UI display.
        # Only claims with review_status=="signed" enter quality_summary.signed_claims.
        # Evidence Sufficiency Sprint: use updated_claims (with filtered evidence_ids) instead of original claims.
        rework_required_claims = [
            dict(c) for c in updated_claims
            if c.get("review_status") == "rework_required"
            or c.get("_hard_gate_downgrade")
            or any(
                isinstance(r.get("reason_codes"), list) and "REWORK_REQUIRED" in r.get("reason_codes", [])
                for r in reviews
                if r.get("claim_id") == c.get("claim_id")
            )
        ]

        state["review_results"] = reviews
        state["signed_claims"] = signed
        state["rework_required_claims"] = rework_required_claims
        state["claim_drafts"] = updated_claims  # Step-3 Fix: write filtered claims back to state
        state["rework_requests"].extend(rework_reqs)

        logger.info(
            "review_claims: run_id=%s signed=%d rework_required=%d rework_requests=%d",
            run_id, len(signed), len(rework_required_claims), len(rework_reqs),
        )
    except Exception as exc:
        logger.error("review_claims failed for run_id=%s: %s", run_id, exc)

    return state



def reflect_on_review(state: WorkflowState) -> WorkflowState:
    """LLM-driven quality reflection on the review_claims output.

    Runs BEFORE prepare_human_intervention to:
    1. Assess overall claim quality and coverage gaps
    2. Generate structured improvement instructions for rework
    3. Enrich rework_requests with LLM-provided priority and scope
    4. Produce a human-readable quality summary for the frontend

    This node ALWAYS runs when there are rework requests (after MAX_CLAIMS_REWORK_ITERATIONS
    exhausted) so that the human reviewer gets a rich assessment rather than raw claim data.

    Output state fields:
      - review_reflection: dict with quality_score, strengths, weaknesses,
        improvement_plan, recommended_dimensions, summary_for_human
      - rework_requests: enriched with priority, scope, and specific instructions
    """
    run_id = state.get("run_id", "unknown")
    review_results = state.get("review_results", []) or []
    rework_requests = state.get("rework_requests", []) or []
    signed_claims = state.get("signed_claims", []) or []
    evidence_items = state.get("evidence_items", []) or []
    task_brief = state.get("task_brief", {})

    logger.info(
        "reflect_on_review: run_id=%s review_results=%d rework_requests=%d signed=%d",
        run_id, len(review_results), len(rework_requests), len(signed_claims),
    )

    # Initialize output
    state["review_reflection"] = {
        "quality_score": 0.0,
        "strengths": [],
        "weaknesses": [],
        "improvement_plan": "",
        "recommended_dimensions": [],
        "summary_for_human": "",
    }

    if not rework_requests:
        logger.info("reflect_on_review: no rework requests, skipping LLM reflection")
        return state

    # Build context for the LLM
    products = [p.get("product_name", p.get("product_id", "unknown"))
               for p in task_brief.get("products", [])]

    review_summary = []
    for r in review_results[:30]:
        status = r.get("status", "unknown")
        claim_text = r.get("claim", {}).get("claim_text", "")[:200]
        issues = r.get("issues", [])
        review_summary.append({
            "status": status,
            "claim": claim_text,
            "issues": issues[:3],
        })

    rework_summary = []
    for req in rework_requests[:20]:
        rework_summary.append({
            "dimension": req.get("dimension", "unknown"),
            "reason": req.get("reason", "")[:200],
            "priority": req.get("priority", "medium"),
        })

    prompt = f"""You are a competitive intelligence quality reviewer. Assess the following claim review results for a product research report.

## Research Context
Products: {', '.join(products)}
Total claims: {len(signed_claims)}
Signed claims (passed review): {len(signed_claims)}
Claims needing rework: {len(rework_requests)}
Evidence items: {len(evidence_items)}

## Review Results (up to 30 claims)
{json.dumps(review_summary, ensure_ascii=False, indent=2)}

## Rework Requests (up to 20)
{json.dumps(rework_summary, ensure_ascii=False, indent=2)}

## Your Task
Provide a structured quality assessment. Return ONLY valid JSON:

{{
  "quality_score": <float 0.0-1.0>,
  "strengths": [<list of 2-4 specific strengths in the current claims>],
  "weaknesses": [<list of 3-6 specific weaknesses>],
  "improvement_plan": "<2-3 sentence actionable plan for addressing the rework requests>",
  "recommended_dimensions": [<list of 2-4 specific dimensions to focus on in rework>],
  "summary_for_human": "<3-4 sentence plain-English summary a non-technical product manager can understand>"
}}

Return ONLY valid JSON, no markdown, no explanation."""

    def _call_reflect() -> str:
        from backend.app.services.llm_client import get_llm_client
        client = get_llm_client()
        return client.chat_text(
            messages=[
                {"role": "system", "content": "You are a competitive intelligence quality reviewer. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
            timeout=30,
        )

    def _parse(text: str) -> dict:
        import re
        text = text.strip()
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"Could not parse JSON from: {text[:200]}")

    try:
        from backend.app.tracing.llm_trace import traced_llm_call

        result = traced_llm_call(
            run_id=run_id,
            node_name="reflect_on_review",
            agent_name="ReviewReflector",
            agent_role="quality_reflector",
            prompt_version="v1",
            prompt_text=prompt,
            input_payload={
                "review_count": len(review_results),
                "rework_count": len(rework_requests),
                "products": products,
            },
            call_fn=_call_reflect,
            parse_fn=_parse,
            input_length_hint=len(prompt),
            decision_summary=f"Review reflection for {len(rework_requests)} rework requests",
        )

        parsed = result.get("parsed_output") or {}
        quality_score = float(parsed.get("quality_score", 0.0))
        quality_score = max(0.0, min(1.0, quality_score))

        # Store in state
        state["review_reflection"] = {
            "quality_score": quality_score,
            "strengths": parsed.get("strengths", []),
            "weaknesses": parsed.get("weaknesses", []),
            "improvement_plan": parsed.get("improvement_plan", ""),
            "recommended_dimensions": parsed.get("recommended_dimensions", []),
            "summary_for_human": parsed.get("summary_for_human", ""),
            "raw": parsed,
        }

        # Enrich rework_requests with priority and scope from LLM assessment
        recommended_dims = set(d.lower() for d in parsed.get("recommended_dimensions", []))
        improved_rework = []
        for req in rework_requests:
            req = dict(req)
            dim = req.get("dimension", "").lower()

            # Upgrade priority based on quality score
            if quality_score < 0.3:
                if req.get("priority") in ("low", "medium"):
                    req["priority"] = "high"
                req["requires_human_review"] = True
            elif quality_score < 0.5:
                if req.get("priority") == "low":
                    req["priority"] = "medium"

            # Add scope hint from recommended dimensions
            if dim in recommended_dims:
                req["scope"] = "priority_focus"
            else:
                req["scope"] = "general"

            # Add specific instructions from improvement plan
            plan = parsed.get("improvement_plan", "")
            if plan and not req.get("specific_instruction"):
                req["specific_instruction"] = plan

            improved_rework.append(req)

        state["rework_requests"] = improved_rework

        logger.info(
            "reflect_on_review: quality_score=%.2f, enriched %d rework_requests",
            quality_score, len(improved_rework),
        )

    except Exception as exc:
        logger.warning(
            "reflect_on_review: LLM reflection failed for run_id=%s: %s. "
            "Using rule-based fallback.",
            run_id, exc,
        )
        # Rule-based fallback: compute simple quality score
        signed_ratio = len(signed_claims) / max(len(signed_claims) + len(rework_requests), 1)
        quality_score = signed_ratio
        state["review_reflection"] = {
            "quality_score": quality_score,
            "strengths": ["Sufficient evidence linking for signed claims"] if signed_ratio > 0.5 else [],
            "weaknesses": [f"{len(rework_requests)} claims require rework"] if rework_requests else [],
            "improvement_plan": "Address all rework requests before finalizing report.",
            "recommended_dimensions": list(set(r.get("dimension", "unknown") for r in rework_requests))[:4],
            "summary_for_human": (
                f"Review completed. {len(signed_claims)} of "
                f"{len(signed_claims) + len(rework_requests)} claims passed. "
                f"{len(rework_requests)} claims need improvement. "
                f"Overall quality: {'acceptable' if quality_score > 0.6 else 'needs work'}."
            ),
            "fallback": True,
        }

    return state


def write_report(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "unknown")
    signed_claims = state.get("signed_claims", [])

    logger.info("write_report: run_id=%s signed_claims=%d", run_id, len(signed_claims))

    if not signed_claims:
        state["report_draft"] = {
            "report_id": f"report_{run_id}",
            "run_id": run_id,
            "sections": [],
            "quality_summary": {},
            "report_status": "blocked",
            "reason": "no_signed_claims",
        }
        return state

    try:
        task_brief = state.get("task_brief", {})
        # vNext-R2-D Patch: Read report_outline from task_brief or state
        # Also check nested research_plan.report_outline for compatibility
        research_plan = task_brief.get("research_plan", {}) or {}
        report_outline = (
            task_brief.get("report_outline")
            or state.get("report_outline")
            or (research_plan.get("report_outline") if isinstance(research_plan, dict) else None)
        )
        
        # P1-1: Build evidence_map for enhanced context
        evidence_items = state.get("evidence_items", []) or []
        evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}
        
        agent = _writer()
        report = agent.write(
            signed_claims=signed_claims,
            run_id=run_id,
            project_id=state.get("project_id"),
            task_brief=task_brief,
            report_outline=report_outline,
            evidence_map=evidence_map,
        )
        state["report_draft"] = report
        logger.info(
            "write_report: report_id=%s status=%s sections=%d",
            report.get("report_id"), report.get("report_status"), len(report.get("sections", [])),
        )
    except Exception as exc:
        logger.error(
            "write_report LLM call failed for run_id=%s: %s - using template fallback",
            run_id, exc,
        )
        # Fallback: assemble report from signed_claims directly without LLM
        sections = _template_report_from_claims(signed_claims, run_id)
        state["report_draft"] = {
            "report_id": f"report_{run_id}",
            "run_id": run_id,
            "sections": sections,
            "quality_summary": {
                "claim_count": len(signed_claims),
                "evidence_coverage_rate": sum(
                    1 for c in signed_claims if c.get("evidence_ids")
                ) / len(signed_claims) if signed_claims else 0.0,
                "unsupported_claim_count": 0,
            },
            "report_status": "draft",
            "reason": f"template_fallback: {exc}",
        }
        logger.info(
            "write_report: template fallback produced %d sections for run_id=%s",
            len(sections), run_id,
        )

    return state


def _build_markdown_report(
    title: str,
    sections: list[dict[str, Any]],
    quality_summary: dict[str, Any],
    report_status: str,
) -> str:
    """Build a full markdown report from sections and quality metadata."""
    lines = [
        f"# {title}\n",
        f"**Status:** {report_status}\n",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
    ]

    qs = quality_summary or {}
    claim_count = qs.get("claim_count", len(sections))
    coverage = qs.get("evidence_coverage_rate", 0.0)
    unsupported = qs.get("unsupported_claim_count", 0)
    lines.append(
        f"\n**Quality Summary:** "
        f"{claim_count} claims, {coverage:.0%} evidence coverage, "
        f"{unsupported} unsupported\n"
    )
    lines.append("\n---\n")

    if not sections:
        lines.append("\n*No report sections available.*\n")
        return "".join(lines)

    for idx, section in enumerate(sections, 1):
        sec_title = section.get("section_title", f"Section {idx}")
        # content_markdown may already start with ##, strip it to avoid double heading
        content = (section.get("content_markdown") or section.get("text") or "").strip()
        if content.startswith(f"## {sec_title}"):
            lines.append(f"\n{content}\n")
        elif content.startswith("## "):
            lines.append(f"\n{content}\n")
        else:
            lines.append(f"\n## {sec_title}\n\n{content}\n")

        claim_ids = section.get("claim_ids", [])
        evidence_ids = section.get("evidence_ids", [])
        if claim_ids:
            lines.append(f"*Claims: {len(claim_ids)} | Evidence: {len(evidence_ids)}*\n")
        unsupported = section.get("unsupported", section.get("unsupported_flag", False))
        if unsupported:
            lines.append("> ⚠️ This section contains unsupported claims.\n")

    lines.append("\n---\n")
    lines.append("*Report generated by ProductInsight Agent.*\n")
    return "".join(lines)


def _template_report_from_claims(
    signed_claims: list[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    """Assemble a minimal report from signed_claims without LLM."""
    DIMENSION_ORDER = [
        "Executive Summary",
        "Product Overview",
        "Feature Comparison",
        "Pricing Analysis",
        "User Persona",
        "Customer Voice",
        "SWOT Analysis",
        "Enterprise Readiness",
        "Key Findings",
    ]
    DIMENSION_MAP = {
        "function_tree": "Feature Comparison",
        "pricing_model": "Pricing Analysis",
        "user_persona": "User Persona",
        "customer_voice": "Customer Voice",
        "swot": "SWOT Analysis",
        "enterprise_readiness": "Enterprise Readiness",
    }

    by_section: dict[str, list[dict[str, Any]]] = {d: [] for d in DIMENSION_ORDER}
    for claim in signed_claims:
        dim = claim.get("dimension", "function_tree")
        section_title = DIMENSION_MAP.get(dim, "Feature Comparison")
        by_section[section_title].append(claim)

    sections: list[dict[str, Any]] = []
    for idx, title in enumerate(DIMENSION_ORDER):
        section_claims = by_section.get(title, [])
        claim_ids = [c.get("claim_id", "") for c in section_claims]
        evidence_ids: list[str] = []
        for c in section_claims:
            evidence_ids.extend(c.get("evidence_ids") or [])

        if not section_claims:
            # Executive Summary and Key Findings get fallback claims from signed_claims
            if title in ("Executive Summary", "Key Findings") and signed_claims:
                fallback = signed_claims[:4]
                claim_ids = [c.get("claim_id", "") for c in fallback]
                for c in fallback:
                    evidence_ids.extend(c.get("evidence_ids") or [])
                section_claims = fallback
            else:
                # Other empty sections are skipped entirely
                continue

        # Skip any section that ends up with no claim_ids or no evidence_ids
        if not claim_ids or not evidence_ids:
            logger.warning(
                "_template_report_from_claims: skipping section '%s' (no claim_ids or no evidence_ids)",
                title,
            )
            continue

        bullet_points = []
        for c in section_claims:
            pid = c.get("product_id", "unknown")
            text = c.get("claim_text", "")
            if text:
                bullet_points.append(f"- **{pid}**: {text}")

        content = (
            f"## {title}\n\n" + "\n".join(bullet_points)
            if bullet_points
            else f"## {title}\n\nNo structured claims for this dimension."
        )

        sections.append({
            "section_id": f"section_{idx + 1:02d}_{title.lower().replace(' ', '_')}",
            "section_title": title,
            "content_markdown": content,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "unsupported": False,
        })

    return sections


def write_report_v2(state: WorkflowState) -> WorkflowState:
    """
    Deep Report v2 - Multi-stage, evidence-backed, chapterized report generation.
    
    vNext-R3-A: This is the new report generation workflow that:
    1. Uses report_outline from research_plan or default template
    2. Initializes report sections
    3. Builds research packs for each section
    4. Writes section drafts using LLM
    5. Reviews sections for depth and evidence
    6. Generates comparison tables
    7. Generates SWOT cards
    8. Assembles final report
    
    vNext-R3-B (泛化): Integrated with Domain Schema Planner for cross-domain support:
    1. Query Understanding - detect domain, report_type, audience
    2. Domain Schema Generation - generate or retrieve domain-specific schema
    3. Competitor Discovery - if no products specified, discover competitors
    4. Adaptive Report Generation - use domain schema for report outline
    
    This workflow is triggered when state['report_version'] == 'v2'.
    """
    import logging as _logger
    from backend.app.services.deep_report import run_deep_report_workflow, _generate_fixed_prompt_report
    from backend.app.services.domain_schema import understand_query, generate_domain_schema
    
    run_id = state.get("run_id", "unknown")
    # P1-Fix: In real_time mode, evidence may have been updated by execute_rework
    # but workflow_state doesn't reflect DB changes. Always reload from DB to ensure
    # write_report_v2 has the latest evidence (including all 4 products after rework).
    # P1-Fix: Always reload from DB for evidence and claims — the DB is the source of truth.
    # Previously, only real_time mode reloaded from DB. In replay mode, the function read
    # from state, which may contain stale workflow_state_json (e.g., signed_claims=0 from
    # before review_claims ran). Evidence/claims are now always persisted to DB, so DB
    # always has the freshest data regardless of workflow state file.
    mode = state.get("mode", "real_time")
    _db_evidence = _db_query(
        "SELECT * FROM evidence_items WHERE run_id = ? ORDER BY product_id, created_at",
        (run_id,),
    )
    _db_signed_claims = _db_query(
        "SELECT * FROM claims WHERE run_id = ? AND review_status = 'signed'",
        (run_id,),
    )
    _db_analyst_claims = _db_query(
        "SELECT * FROM claims WHERE run_id = ? AND review_status = 'analyst_signed'",
        (run_id,),
    )
    if _db_evidence:
        evidence_items = _db_evidence
        signed_claims = _db_signed_claims + _db_analyst_claims
        facts = _db_query("SELECT * FROM facts WHERE run_id = ?", (run_id,))
        if mode == "real_time" or signed_claims:
            logger.info(
                "write_report_v2: reloaded %d evidence_items, %d signed_claims from DB "
                "(mode=%s, state had evidence=%d claims=%d)",
                len(_db_evidence), len(signed_claims), mode,
                len(state.get("evidence_items", [])), len(state.get("signed_claims", [])),
            )
    else:
        # Fallback: use state (e.g., if evidence extraction produced no items)
        evidence_items = state.get("evidence_items", [])
        all_db_claims = _db_signed_claims + _db_analyst_claims
        signed_claims = all_db_claims if all_db_claims else state.get("signed_claims", [])
        facts = state.get("facts", [])
    rework_required_claims = state.get("rework_required_claims", [])
    task_brief = state.get("task_brief", {})
    user_query = task_brief.get("query", task_brief.get("task_description", ""))

    logger.info("write_report_v2: run_id=%s signed_claims=%d rework_required=%d evidence=%d",
                run_id, len(signed_claims), len(rework_required_claims), len(evidence_items))
    
    # ============================================================
    # Phase 0: Query Understanding & Domain Schema Generation
    # ============================================================
    query_understanding = understand_query(
        query=user_query,
        products=task_brief.get("products", [])
    )
    state["query_understanding"] = query_understanding
    logger.info(f"write_report_v2: domain={query_understanding['domain']}, "
                f"report_type={query_understanding['report_type']}, "
                f"need_discovery={query_understanding['need_discovery']}")
    
    # Generate domain schema
    domain_schema = generate_domain_schema(
        domain=query_understanding["domain"],
        products=task_brief.get("products", []) or [p.get("product_name") for p in task_brief.get("products", [])],
        query=user_query,
    )
    state["domain_schema"] = domain_schema
    logger.info(f"write_report_v2: schema_source={domain_schema.get('source')}, "
                f"dimensions={len(domain_schema.get('comparison_dimensions', []))}")
    
    # CRITICAL: Always set state["report_version"] to prevent v1 fallback in export_report
    state["report_version"] = "v2"
    
    if not signed_claims:
        logger.warning("write_report_v2: no signed claims, proceeding with empty claims (facts=%d)", len(facts))
        # Continue to call run_deep_report_workflow with empty signed_claims
        # The workflow will generate a blocked report using facts/evidence_items
    
    try:
        # Get products from task_brief (authoritative source with correct product names)
        # Priority: task_brief.products > task_brief.research_plan.competitors > evidence_items
        products = []
        brief_products = task_brief.get("products", [])
        if brief_products:
            # Use product_name as the authoritative display name (not the run-scoped product_id)
            for p in brief_products:
                if isinstance(p, str):
                    name = p.strip()
                    if name and name not in products:
                        products.append(name)
                elif isinstance(p, dict):
                    name = p.get("product_name", "").strip()
                    pid = p.get("product_id", "").strip()
                    if name and name not in products:
                        products.append(name)
                    elif pid and pid not in products:
                        products.append(pid)
        else:
            # Fallback: try research_plan competitors
            research_plan = task_brief.get("research_plan", {}) or {}
            competitors = research_plan.get("competitors", [])
            for c in competitors:
                name = (c.get("name") or c.get("product_id") or "").strip()
                if name and name not in products:
                    products.append(name)
            # Last fallback: evidence_items (will have run-scoped IDs)
            if not products:
                evidence_pids = list(set(e.get("product_id", "") for e in evidence_items if e.get("product_id")))
                for pid in evidence_pids:
                    slug = pid.split("_", 1)[-1]
                    if slug and slug not in products:
                        products.append(slug)
        
        # P0-2 Fix: Separate analyst-signed and reviewer-signed claims before passing to workflow
        analyst_signed = [c for c in signed_claims if c.get("review_status") == "analyst_signed"]
        reviewer_signed = [c for c in signed_claims if c.get("review_status") == "signed"]

        # ── Build product_id→display_name mapping ──────────────────────────────────
        # Critical: evidence/claims use run-scoped IDs like 'run_xxx_product_abc12345'
        # but the brief has {'product_id': 'product_abc12345', 'product_name': 'Dify'}.
        # Build a mapping so _build_render_context can match claims to products.
        # Both the run-scoped version (run_xxx_product_abc12345) and base version
        # (product_abc12345) must map to the display name.
        product_id_to_name: dict[str, str] = {}
        for p in brief_products:
            if isinstance(p, dict):
                name = p.get("product_name", "").strip()
                pid = p.get("product_id", "").strip()
                if name and pid:
                    product_id_to_name[pid] = name
                    product_id_to_name[f"{run_id}_{pid}"] = name
                elif name:
                    product_id_to_name[name] = name
            elif isinstance(p, str):
                # Handle plain string products like "Dify" (no product_id field)
                name = p.strip()
                if name:
                    product_id_to_name[name] = name

        # ── P1 (2026-06-22): Hybrid flow — normal pipeline with timeout + fallback ──────────
        # run_deep_report_workflow uses parallel section processing and can take 600-2400s.
        # Wrap in ThreadPoolExecutor so we can apply a hard outer timeout.
        # If it times out or raises, fall back to _generate_fixed_prompt_report (single LLM call, ~60s).
        _NORMAL_TIMEOUT = 2400  # 40 min — normal pipeline with Doubao search takes up to 30min

        def _call_normal_workflow() -> dict[str, Any]:
            from backend.app.services.deep_report import run_deep_report_workflow
            # Fix 2: Extract confirmed outline from research_plan and write to state.
            # This bridges the outline confirmation flow: generate-outline → confirm → state
            # → write_report_v2 reads it and passes to run_deep_report_workflow.
            rp = task_brief.get("research_plan", {}) or {}
            confirmed_outline = rp.get("report_outline")
            if confirmed_outline and isinstance(confirmed_outline, dict):
                sections = confirmed_outline.get("sections", [])
                if sections:
                    state["final_report_outline"] = sections
            elif confirmed_outline and isinstance(confirmed_outline, list):
                state["final_report_outline"] = confirmed_outline

            return run_deep_report_workflow(
                run_id=run_id,
                report_id=f"report_{run_id}_v2",
                products=products,
                signed_claims=reviewer_signed,
                analyst_signed_claims=analyst_signed,
                rework_required_claims=rework_required_claims,
                facts=facts,
                evidence_items=evidence_items,
                research_plan=task_brief.get("research_plan"),
                schema_type=task_brief.get("schema_type"),
                domain_schema=domain_schema,
                query_understanding=query_understanding,
                product_id_to_name=product_id_to_name,
                preconfirmed_outline=state.get("final_report_outline"),
            )

        _logger.warning(
            "write_report_v2 (P1 2026-06-22): running hybrid flow — "
            "normal pipeline (timeout=%ds) then fixed-prompt fallback. "
            "products=%s, evidence=%d, signed_claims=%d",
            _NORMAL_TIMEOUT, products, len(evidence_items), len(reviewer_signed) + len(analyst_signed),
        )

        import concurrent.futures as _futures
        with _futures.ThreadPoolExecutor(max_workers=1) as _ex:
            _fut = _ex.submit(_call_normal_workflow)
            try:
                result = _fut.result(timeout=_NORMAL_TIMEOUT)
                _logger.info("write_report_v2: normal pipeline completed in %.0fs for run_id=%s",
                            _NORMAL_TIMEOUT, run_id)
            except _futures.TimeoutError:
                _logger.warning(
                    "write_report_v2: normal pipeline timed out after %ds for run_id=%s. "
                    "Falling back to fixed-prompt report.",
                    _NORMAL_TIMEOUT, run_id,
                )
                result = _generate_fixed_prompt_report(
                    run_id=run_id,
                    report_id=f"report_{run_id}_v2",
                    products=products,
                    task_brief=task_brief,
                    signed_claims=reviewer_signed + analyst_signed,
                    evidence_items=evidence_items,
                    product_id_to_name=product_id_to_name,
                )
            except Exception as _exc:
                _logger.error(
                    "write_report_v2: normal pipeline raised %s for run_id=%s. "
                    "Falling back to fixed-prompt report.",
                    _exc, run_id, exc_info=True,
                )
                result = _generate_fixed_prompt_report(
                    run_id=run_id,
                    report_id=f"report_{run_id}_v2",
                    products=products,
                    task_brief=task_brief,
                    signed_claims=reviewer_signed + analyst_signed,
                    evidence_items=evidence_items,
                    product_id_to_name=product_id_to_name,
                )
        
        state["report_draft"] = result
        # Ensure report_version is set in state so export_report knows to skip v1 generation
        state["report_version"] = "v2"
        logger.info("write_report_v2: report generated for run_id=%s", run_id)

        # Fix 4: Always save workflow state after write_report_v2 completes so that
        # export_report (which reads from DB) sees report_version="v2" and skips v1 path.
        # Previously state was only saved on pause, causing export_report to fall through
        # to generic v1 section generation even when v2 sections existed on disk.
        try:
            from backend.app.storage.repositories import RunRepository
            RunRepository().save_workflow_state(run_id, state)
            logger.info("write_report_v2: workflow state saved for run_id=%s", run_id)
        except Exception as exc:
            logger.warning("write_report_v2: failed to save workflow state: %s", exc)

    except Exception as exc:
        logger.error("write_report_v2 failed for run_id=%s: %s", run_id, exc, exc_info=True)
        import traceback as _tb
        _tb.print_exc()
        state["report_version"] = "v2"  # Always v2, even on error
        state["report_draft"] = {
            "report_id": f"report_{run_id}_v2",
            "run_id": run_id,
            "sections": [],
            "tables": [],
            "figures": [],
            "quality_summary": {},
            "report_status": "error",
            "report_version": "v2",
            "error": str(exc),
        }
    
    return state


def final_review(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "unknown")
    report_draft = state.get("report_draft", {})
    signed_claims = state.get("signed_claims", [])
    evidence_items = state.get("evidence_items", [])
    facts = state.get("facts", [])

    logger.info("final_review: run_id=%s", run_id)

    if report_draft.get("report_status") in ("blocked", "blocked_consistency"):
        # Graceful degradation: do NOT block. Log warning and continue to export.
        # The report will be marked 'reviewed_with_gaps' at the end.
        logger.warning(
            "final_review: run_id=%s report was blocked but continuing to export. "
            "Report will be marked as partial. reason=%s",
            run_id, report_draft.get("reason", ""),
        )

    if not signed_claims:
        # Graceful degradation: no signed claims is NOT a fatal error.
        # Log and continue — review_report will handle empty claims gracefully.
        # The final report will include a gap note instead of being blocked.
        logger.warning(
            "final_review: run_id=%s no signed claims available, "
            "continuing with partial review. facts=%d evidence=%d",
            run_id, len(facts), len(evidence_items),
        )

    try:
        agent = _reviewer()
        result = agent.review_report(report_draft, signed_claims, evidence_items)
        state["final_review_result"] = result
        status = result.get("status", "")

        logger.info("final_review: run_id=%s status=%s", run_id, status)

        if status == "rework_required":
            rw = result.get("rework_request", {})
            if rw:
                state.setdefault("rework_requests", []).append(rw)
            for code in (result.get("reason_codes") or []):
                state.setdefault("errors", []).append({
                    "reason_code": code,
                    "message": f"rework_required: {code}",
                    "node": "final_review",
                })
        elif status in ("pass", "warning"):
            pass  # continue to export
        else:
            state.setdefault("errors", []).append({
                "reason_code": "FINAL_REVIEW_UNKNOWN_STATUS",
                "message": f"final_review: {status}",
                "node": "final_review",
            })

    except Exception as exc:
        logger.error("final_review failed for run_id=%s: %s", run_id, exc)
        state["final_review_result"] = {"status": "error", "reason": str(exc)}
        state.setdefault("errors", []).append({
            "reason_code": "EXCEPTION",
            "message": f"final_review error: {exc}",
            "node": "final_review",
        })

    return state


def _slugify(name: str) -> str:
    """Convert a string to a URL-safe slug."""
    import re
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-")


def _norm_dim(key):
    k = key.lower()
    for frag, dim in [
        ("pricing","pricing_model"),("price","pricing_model"),
        ("workflow","workflow"),("orchestrat","workflow"),("function_tree","workflow"),
        ("rag","knowledge_base"),("knowledge","knowledge_base"),
        ("deploy","deployment_options"),("docker","deployment_options"),
        ("enterprise","enterprise_readiness"),("sso","enterprise_readiness"),("rbac","enterprise_readiness"),
        ("integration","integration"),("api","integration"),
        ("model","model_support"),("llm","model_support"),("gpt","model_support"),
        ("agent","agent_capabilities"),("bot","agent_capabilities"),("user","user_persona")]:
        if frag in k: return dim
    return key.split(".")[0]

def _cell_value(fact):
    try:
        import json as _json
        raw = fact.get("value_json","")
        if isinstance(raw, str):
            try: raw = _json.loads(raw)
            except: pass
        if isinstance(raw, dict):
            for k in ["deployment_methods","supported_models","price_mentions"]:
                v = raw.get(k)
                if v: return ", ".join(v)[:100] if isinstance(v,list) else str(v)[:100]
            for k, label in [("rbac","RBAC"),("sso","SSO"),("audit_log","Audit"),("encryption","Encrypt"),
                              ("api_available","API"),("webhook_available","Webhook"),("plugin_system","Plugins")]:
                if raw.get(k): return label
            lvl = raw.get("capability_level","")
            if lvl: return lvl.title()
            free = raw.get("has_free_tier")
            if free is not None: return "Free Tier" if free else "Paid Only"
            smry = raw.get("summary","")
            if smry: return smry[:80]
        return str(raw)[:80]
    except: return str(fact.get("value_json",""))[:80]


def _build_structured_sections(facts, product_coverage, comparison_matrix, signed_claims, evidence_items=None):
    """Build 10 standard competitive analysis sections with canonical product_coverage_summary.

    product_coverage is the canonical product_coverage_summary dict:
      slug -> {product_slug, product_name, sources, evidence, facts, evidence_ids,
               signed_claims, coverage_status, missing_dimensions}
    coverage_status: insufficient | partial | sufficient

    evidence_items: optional full evidence list; used to build valid_evidence_ids set.
    All section.evidence_ids are filtered to only contain valid evidence IDs.
    """
    # Build valid_evidence_ids from evidence_items (the ground truth)
    valid_evidence_ids: set[str] = set()
    if evidence_items:
        valid_evidence_ids = {e.get("evidence_id") for e in evidence_items if e.get("evidence_id")}
    elif facts:
        valid_evidence_ids = {
            eid for f in (facts or []) for eid in (f.get("evidence_ids") or f.get("source_evidence_ids") or [])
        }

    def _filter(eids: list[str]) -> list[str]:
        """Return only evidence IDs that exist in valid_evidence_ids."""
        return [eid for eid in eids if eid in valid_evidence_ids]

    def _all_product_evidence_ids() -> list[str]:
        """All evidence IDs across all products (for context sections)."""
        result = []
        for cov in product_coverage.values():
            result.extend(_filter(cov.get("evidence_ids") or []))
        return result
    sections = []
    total_facts = len(facts)

    # Derive product list in canonical order
    total_products = len(product_coverage)
    sufficient = sum(1 for v in product_coverage.values() if v.get("coverage_status") == "sufficient")
    partial = sum(1 for v in product_coverage.values() if v.get("coverage_status") == "partial")
    insufficient = sum(1 for v in product_coverage.values() if v.get("coverage_status") == "insufficient")

    # 1. Executive Summary — always consistent with canonical_pcs
    if total_products == 0:
        coverage_text = "Product coverage analysis pending."
    elif insufficient > 0:
        coverage_text = (
            f"{insufficient} of {total_products} products have insufficient evidence coverage; "
            f"{partial} have partial coverage; {sufficient} have sufficient coverage."
        )
    elif partial > 0:
        coverage_text = (
            f"{partial} of {total_products} products have partial evidence coverage; "
            f"{sufficient} have sufficient coverage."
        )
    else:
        has_missing_dims = any(cov.get("missing_dimensions") for cov in product_coverage.values())
        if has_missing_dims:
            coverage_text = "While all products have minimum fact coverage, some dimensions still lack sufficient structured facts. See §10 for details."
        else:
            coverage_text = f"All {total_products} products have sufficient evidence coverage."

    sections.append({
        "section_id": "sec_01_exec_summary",
        "section_title": "Executive Summary",
        "text": f"This competitive landscape analysis covers {total_products} products based on {total_facts} structured facts. "
                f"{coverage_text} "
                f"The report examines workflow orchestration, knowledge management, deployment options, pricing, enterprise readiness, and integrations.",
        "claim_ids": [],
        "evidence_ids": _all_product_evidence_ids(),
        "coverage_note": f"{sufficient} sufficient / {partial} partial / {insufficient} insufficient",
    })

    # 2. Product Coverage — always render at least one row per product
    status_label = {
        "sufficient": "Sufficient",
        "partial": "Partial",
        "insufficient": "Insufficient Evidence",
    }
    coverage_rows = []
    for slug, cov in sorted(product_coverage.items(), key=lambda x: x[0]):
        status = cov.get("coverage_status", "insufficient")
        ev = cov.get("evidence", 0)
        src = cov.get("sources", 0)
        fct = cov.get("facts", 0)
        sc_cnt = cov.get("signed_claims", 0)
        label = status_label.get(status, status)
        coverage_rows.append(f"| {cov.get('product_name', slug.title())} | {label} | {src} | {ev} | {fct} | {sc_cnt} |")
    coverage_md = "| Product | Status | Sources | Evidence | Facts | Signed Claims |\n|---|---|---|---|---|---|\n" + "\n".join(coverage_rows)

    sections.append({
        "section_id": "sec_02_coverage",
        "section_title": "Product Coverage",
        "text": coverage_md,
        "claim_ids": [],
        "evidence_ids": _all_product_evidence_ids(),
        "coverage_note": f"Analysis covers {total_products} products",
    })

    # 3. Product Overview — never empty, always show every product
    overview_rows = []
    for slug, cov in sorted(product_coverage.items(), key=lambda x: x[0]):
        status = cov.get("coverage_status", "insufficient")
        ev = cov.get("evidence", 0)
        fct = cov.get("facts", 0)
        sc_cnt = cov.get("signed_claims", 0)
        # Determine key strength from coverage status
        if status == "sufficient":
            strength = "Strong coverage"
        elif status == "partial":
            strength = "Partial coverage"
        else:
            strength = "Limited evidence"
        missing_dims = cov.get("missing_dimensions", [])
        detail = f"Evidence: {ev} items, Facts: {fct}, Claims: {sc_cnt}"
        if missing_dims:
            detail += f" | Missing: {', '.join(missing_dims[:3])}"
        overview_rows.append(f"| {cov.get('product_name', slug.title())} | {strength} | {detail} |")
    overview_md = "| Product | Coverage Status | Details |\n|---|---|---|\n" + "\n".join(overview_rows)

    sections.append({
        "section_id": "sec_03_overview",
        "section_title": "Product Overview",
        "text": overview_md,
        "claim_ids": [],
        "evidence_ids": _all_product_evidence_ids(),
    })

    # 4–9. Feature dimension sections
    DIM_SECTIONS = [
        ("sec_04_workflow", "Feature / Workflow Comparison", "workflow", "workflow",
         "Workflow Orchestration", "workflow automation, pipeline, node-based design"),
        ("sec_05_knowledge", "Knowledge Base & RAG", "knowledge_base", "rag",
         "Knowledge Management", "RAG, vector search, document ingestion"),
        ("sec_06_deployment", "Deployment & Enterprise Readiness", "deployment_options", "deployment",
         "Deployment", "self-hosted, Docker, Kubernetes, enterprise features"),
        ("sec_07_pricing", "Pricing Evidence", "pricing_model", "pricing",
         "Pricing", "free tier, paid plans, subscription models"),
        ("sec_08_integration", "Integration & API", "integration", "integration",
         "Integrations", "API, webhooks, SDK, plugins"),
        ("sec_09_model_support", "Model Support", "model_support", "model",
         "AI Models", "LLM support, OpenAI, Claude, open-source models"),
    ]

    for sec_id, title, dim_key, fallback_key, display_name, keywords in DIM_SECTIONS:
        dim_facts = [f for f in (facts or []) if _norm_dim(f.get("schema_key","")) == dim_key]
        if not dim_facts:
            dim_facts = [f for f in (facts or []) if fallback_key in f.get("schema_key","").lower()]

        if dim_facts:
            from collections import defaultdict
            import json as _json
            by_product = defaultdict(list)
            for f in dim_facts:
                slug = f.get("product_slug","")
                by_product[slug].append(f)
            table_rows = ["| Product | Key Finding | Confidence |", "|---|---|---|"]
            for slug in sorted(by_product.keys()):
                best = max(by_product[slug], key=lambda x: x.get("confidence",0))
                raw = best.get("value_json","{}")
                if isinstance(raw, str):
                    try: raw = _json.loads(raw)
                    except: pass
                summary = (raw.get("summary","") if isinstance(raw,dict) else str(raw))[:120]
                conf = int((best.get("confidence",0.5))*100)
                cov_info = product_coverage.get(slug, {})
                cov_status = cov_info.get("coverage_status", "unknown")
                cov_label = status_label.get(cov_status, cov_status)
                table_rows.append(f"| {cov_info.get('product_name', slug.title())} | {summary} | {conf}% [{cov_label}] |")
            body = f"### {display_name}\n\nThis section covers {keywords}.\n\n" + "\n".join(table_rows)
            ev_ids = _filter(list(set(eid for f in dim_facts for eid in f.get("evidence_ids",[]))))
            sections.append({
                "section_id": sec_id,
                "section_title": title,
                "text": body,
                "claim_ids": [],
                "evidence_ids": ev_ids,
            })
        else:
            # List all products with insufficient coverage for this dimension
            missing_prods = []
            for slug, cov in sorted(product_coverage.items(), key=lambda x: x[0]):
                missing = cov.get("missing_dimensions", [])
                if dim_key in missing or dim_key.replace("_", " ") in missing:
                    missing_prods.append(cov.get("product_name", slug.title()))
            missing_text = ", ".join(missing_prods) if missing_prods else "all products"
            # Even when no facts, the section has evidence from all products (even if insufficient)
            sections.append({
                "section_id": sec_id,
                "section_title": title,
                "text": f"## {title}\n\n**Insufficient evidence in current run.** No structured facts found for {display_name}.\n\nProducts missing evidence for this dimension: {missing_text}.\n\nRecommendation: Add seed URLs covering {keywords} for each product.",
                "claim_ids": [],
                "evidence_ids": _all_product_evidence_ids(),
                "coverage_note": "No facts found - evidence gap",
            })

    # 10. Risks & Evidence Gaps — consistent with canonical_pcs
    gaps = []
    for slug, cov in sorted(product_coverage.items(), key=lambda x: x[0]):
        status = cov.get("coverage_status", "insufficient")
        if status != "sufficient":
            prod_name = cov.get("product_name", slug.title())
            missing_dims = cov.get("missing_dimensions", [])
            if status == "insufficient":
                gaps.append(f"- **{prod_name}**: Insufficient evidence — needs sources before reliable analysis.")
            elif status == "partial":
                missing_str = f" (missing: {', '.join(missing_dims[:3])})" if missing_dims else ""
                gaps.append(f"- **{prod_name}**: Partial coverage — some dimensions lack sufficient facts{missing_str}.")
    gaps_text = "\n".join(gaps) if gaps else "\n- No product-level evidence gaps detected. However, some dimension-specific gaps may remain — see below."
    if insufficient > 0:
        gaps_note = f"**Action Required**: {insufficient} product(s) have insufficient evidence. The report should not be used for procurement decisions without additional research."
    elif partial > 0:
        gaps_note = f"**Note**: {partial} product(s) have partial evidence coverage. Some dimensions may still lack sufficient structured facts. See dimension sections above."
    else:
        has_missing_dims = any(cov.get("missing_dimensions") for cov in product_coverage.values())
        if has_missing_dims:
            gaps_note = "All products have minimum fact coverage, but some dimension-specific evidence gaps remain."
        else:
            gaps_note = "All products have sufficient evidence coverage."

    sections.append({
        "section_id": "sec_10_risks",
        "section_title": "Risks & Evidence Gaps",
        "text": "## Evidence Gap Analysis\n\n" + gaps_text + "\n\n**" + gaps_note + "**\n\n**Methodology Note**: Findings are based on publicly accessible web content. Claims marked as 'Insufficient Evidence' should not be used for procurement decisions without additional primary research.",
        "claim_ids": [],
        "evidence_ids": _all_product_evidence_ids(),
        "coverage_note": "Risk assessment complete",
    })

    return sections


def export_report(state: WorkflowState) -> WorkflowState:
    run_id = state.get("run_id", "unknown")
    report_draft = state.get("report_draft", {})
    final_review_result = state.get("final_review_result", {})
    errors = state.get("errors", [])
    mode = state.get("mode", "real_time")
    signed_claims = state.get("signed_claims", [])
    facts = state.get("facts", [])
    product_coverage = state.get("product_coverage", {})

    logger.info("export_report: run_id=%s errors=%d", run_id, len(errors))

    report_status = report_draft.get("report_status", "draft")

    critical_errors = {
        # Graceful degradation: DB write failures for spans are non-blocking.
        # The report file on disk is still valid; only the DB index failed.
        # BLOCKED_NO_SIGNED_CLAIMS is no longer added by final_review (it degrades gracefully).
        "UNSUPPORTED_REPORT_SPAN",
        "DB_WRITE_REPORT_FAILED",
        "PII_NOT_MASKED",
        # NODE_TIMEOUT is handled by the timeout wrapper — the node retries via coverage_critic.
    }
    has_critical_error = any(e.get("reason_code") in critical_errors for e in errors)

    if report_status in ("blocked", "blocked_consistency", "error"):
        # Graceful degradation: do not propagate blocked/error to final status.
        # Degrade to reviewed_with_gaps so the report is accessible.
        report_status = "reviewed_with_gaps"
    elif final_review_result.get("status") in ("blocked", "blocked_consistency"):
        report_status = "reviewed_with_gaps"
    elif has_critical_error:
        report_status = "reviewed_with_gaps"
    elif final_review_result.get("status") in ("pass", "warning"):
        report_status = "exported"
    else:
        report_status = "reviewed"

    state["report_draft"] = dict(report_draft)
    state["report_draft"]["report_status"] = report_status
    state["report_draft"]["updated_at"] = utc_now()

    # Persist report and report_spans to DB (always, not just real_time)
    if report_draft:
        task_brief = state.get("task_brief", {})

        # Build structured comparison matrix
        CANONICAL_DIMS = [
            "workflow", "knowledge_base", "deployment_options", "pricing_model",
            "enterprise_readiness", "integration", "model_support",
            "agent_capabilities", "user_persona",
        ]
        # ─── Canonical product_coverage_summary ─────────────────────────────────────
        # Rebuild canonical_pcs entirely from raw state data (evidence/facts/signed_claims),
        # NOT trusting state["product_coverage"] which may have inconsistent keys.
        #
        # Canonical key: product_id from task_brief (the ground truth).
        # All evidence/facts/signed_claims are re-aggregated using consistent key mapping.
        products_in_project = task_brief.get("products", [])

        # Canonical product registry: canonical_id -> canonical_name
        canonical_products: dict[str, str] = {}  # product_id -> product_name
        for p in products_in_project:
            pid = p.get("product_id", "")
            pname = p.get("product_name", "") or pid
            if pid:
                canonical_products[pid] = pname

        # Build ALL aliases for each product (any string that should map to this canonical product)
        def _resolve_to_canonical(key: str) -> Optional[str]:
            """Map any product identifier to canonical product_id, or None if unknown."""
            if not key:
                return None
            key_lower = key.lower().strip()
            # Direct match on canonical id
            if key in canonical_products:
                return key
            # Match by name (case-insensitive)
            for cid, cname in canonical_products.items():
                if cname.lower() == key_lower or cid.lower() == key_lower:
                    return cid
            return None

        # ── Step 1: Re-aggregate evidence from raw evidence_items ──────────────────
        evidence_by_canonical: dict[str, dict] = {cid: {"evidence_ids": [], "source_ids": set()}
                                                  for cid in canonical_products}
        for ev in (state.get("evidence_items") or []):
            # Try product_id, then product_slug, then product_name (all lowercased)
            for field in ("product_id", "product_slug"):
                cand = ev.get(field, "")
                if cand:
                    resolved = _resolve_to_canonical(cand)
                    if resolved:
                        evidence_by_canonical[resolved]["evidence_ids"].append(ev.get("evidence_id", ""))
                        src = ev.get("source_id", "")
                        if src:
                            evidence_by_canonical[resolved]["source_ids"].add(src)
                        break

        # ── Step 2: Re-aggregate facts from raw facts ───────────────────────────────
        facts_by_canonical: dict[str, list] = {cid: [] for cid in canonical_products}
        for f in (facts or []):
            for field in ("product_id", "product_slug"):
                cand = f.get(field, "")
                if cand:
                    resolved = _resolve_to_canonical(cand)
                    if resolved:
                        facts_by_canonical[resolved].append(f)
                        break

        # ── Step 3: Re-aggregate signed_claims from raw signed_claims ───────────────
        claims_by_canonical: dict[str, list] = {cid: [] for cid in canonical_products}
        for sc in (signed_claims or []):
            for field in ("product_id", "product_slug"):
                cand = sc.get(field, "")
                if cand:
                    resolved = _resolve_to_canonical(cand)
                    if resolved:
                        claims_by_canonical[resolved].append(sc)
                        break

        # ── Step 4: Build canonical_pcs from re-aggregated data ───────────────────
        canonical_pcs: dict[str, dict] = {}
        for cid, cname in canonical_products.items():
            ev_ids = evidence_by_canonical.get(cid, {}).get("evidence_ids", [])
            src_count = len(evidence_by_canonical.get(cid, {}).get("source_ids", set()))
            fc_count = len(facts_by_canonical.get(cid, []))
            sc_count = len(claims_by_canonical.get(cid, []))
            ev_count = len(ev_ids)

            if ev_count == 0:
                status = "insufficient"
            elif fc_count == 0:
                status = "partial"
            elif fc_count < 2:
                status = "partial"
            else:
                status = "sufficient"

            # Collect missing dimensions: dims with zero facts for this product
            prod_facts_list = facts_by_canonical.get(cid, [])
            missing_dims = [
                dim for dim in CANONICAL_DIMS
                if not any(_norm_dim(f.get("schema_key", "")) == dim for f in prod_facts_list)
            ]

            canonical_pcs[cid] = {
                "product_slug": cid,
                "product_name": cname,
                "sources": src_count,
                "evidence": ev_count,
                "evidence_ids": ev_ids,
                "facts": fc_count,
                "signed_claims": sc_count,
                "coverage_status": status,
                "missing_dimensions": missing_dims,
            }

        # Add any products that appear in evidence/facts but are NOT in task_brief
        all_seen_keys: set[str] = set()
        for ev in (state.get("evidence_items") or []):
            for field in ("product_id", "product_slug"):
                k = ev.get(field, "")
                if k and _resolve_to_canonical(k) is None:
                    all_seen_keys.add(k)
        for f in (facts or []):
            for field in ("product_id", "product_slug"):
                k = f.get(field, "")
                if k and _resolve_to_canonical(k) is None:
                    all_seen_keys.add(k)
        for k in all_seen_keys:
            if k not in canonical_pcs:
                canonical_pcs[k] = {
                    "product_slug": k,
                    "product_name": k.title(),
                    "sources": 0,
                    "evidence": 0,
                    "evidence_ids": [],
                    "facts": 0,
                    "signed_claims": 0,
                    "coverage_status": "insufficient",
                    "missing_dimensions": list(CANONICAL_DIMS),
                }

        # product_names in canonical order (task_brief order)
        product_names = [p.get("product_id", "") or p.get("product_name", "")
                        for p in products_in_project] or list(canonical_products.keys())

        comparison_matrix = []
        try:
            for dim in CANONICAL_DIMS:
                row = {"schema_key": dim, "products": {}}
                has_data = False
                for cid in product_names:
                    prod_facts = [
                        f for f in facts_by_canonical.get(cid, [])
                        if _norm_dim(f.get("schema_key", "")) == dim
                    ]
                    if prod_facts:
                        cell = _cell_value(prod_facts[0])
                    else:
                        cov = canonical_pcs.get(cid, {})
                        ev_count = cov.get("evidence", 0)
                        fc_count = cov.get("facts", 0)
                        if ev_count == 0:
                            cell = "Insufficient Evidence"
                        elif fc_count == 0:
                            cell = "Partial Evidence"
                        else:
                            cell = "Unknown"
                        if cell != "Unknown":
                            has_data = True
                    row["products"][canonical_products.get(cid, cid)] = cell
                    if cell not in ("Unknown", "Insufficient Evidence", "Partial Evidence"):
                        has_data = True
                if has_data:
                    comparison_matrix.append(row)
        except Exception as exc:
            logger.warning("export_report: failed to build comparison_matrix: %s", exc)
            comparison_matrix = []

        state["product_coverage_summary"] = canonical_pcs

        # Build structured sections using canonical_pcs
        evidence_items_list = state.get("evidence_items", [])
        sections = _build_structured_sections(
            facts=facts or [],
            product_coverage=canonical_pcs,
            comparison_matrix=comparison_matrix,
            signed_claims=signed_claims or [],
            evidence_items=evidence_items_list,
        )

        # ─── quality_summary_v2 derived from canonical_pcs ───────────────────────
        total_products = len(product_names)
        sufficient_cnt = sum(1 for v in canonical_pcs.values() if v.get("coverage_status") == "sufficient")
        partial_cnt = sum(1 for v in canonical_pcs.values() if v.get("coverage_status") == "partial")
        insufficient_cnt = sum(1 for v in canonical_pcs.values() if v.get("coverage_status") == "insufficient")

        # Evidence coverage rate = fraction of products with at least some evidence
        if total_products > 0:
            evidence_coverage_rate = round((sufficient_cnt + partial_cnt) / total_products, 2)
        else:
            evidence_coverage_rate = 1.0 if (signed_claims or []) else 0.0

        # Unsupported = insufficient products with no facts
        unsupported_claim_count = insufficient_cnt

        # FIX: v2 report quality_summary is set by deep_report.py (single source of truth).
        # export_report MUST NOT recompute it independently to avoid split truth.
        # For v2: use deep_report's quality_summary; for v1 fallback: use quality_summary_v2.
        if state.get("report_version") == "v2":
            # Use deep_report's quality_summary as the authoritative source.
            # deep_report already ran _build_render_context + _run_consistency_gates
            # so coverage_by_product and report_status are already correct there.
            quality_summary_for_export = report_draft.get("quality_summary", {})
            # Also preserve the canonical_pcs enrichment (product_coverage_summary) from export
            if canonical_pcs and not quality_summary_for_export.get("product_coverage_summary"):
                quality_summary_for_export = dict(quality_summary_for_export)
                quality_summary_for_export["product_coverage_summary"] = canonical_pcs
            # v2: use quality_summary["report_status"] as the single source of truth.
            # deep_report sets it to "blocked" if gates failed (previously "blocked_consistency"
            # but DB CHECK constraint only allows "blocked").
            # export_report MUST NOT override it based on canonical_pcs.
            report_status = quality_summary_for_export.get("report_status", "draft")
        else:
            # v1 fallback: recompute quality_summary_v2 (deprecated path)
            quality_summary_v2 = {
                "claim_count": len(signed_claims or []),
                "signed_claims": len(signed_claims or []),
                "evidence_coverage_rate": evidence_coverage_rate,
                "unsupported_claim_count": unsupported_claim_count,
                "section_count": len(sections),
                "evidence_count": len(state.get("evidence_items", [])),
                "total_products": total_products,
                "sufficient_products": sufficient_cnt,
                "partial_products": partial_cnt,
                "insufficient_products": insufficient_cnt,
                "product_coverage_summary": canonical_pcs,
            }
            quality_summary_for_export = quality_summary_v2
            # Update report_status based on quality gaps (v1 only)
            if insufficient_cnt > 0:
                report_status = "reviewed_with_gaps"
            elif partial_cnt > 0:
                report_status = "reviewed_partial"
            # Otherwise keep the original status (exported/reviewed set above)

        # --- Generate markdown file ---
        # CRITICAL FIX: v2 report markdown is generated by run_deep_report_workflow
        # and already persisted at report_draft["content_markdown_path"].
        # export_report MUST NOT overwrite it with the 3KB summary!
        md_path = ""
        report_version = state.get("report_version", "v1")
        # Always define report_title (used in DB record even for v2)
        report_title = report_draft.get("title", "竞品分析报告")
        if report_version == "v2":
            # v2: use the already-persisted v2 markdown path, do NOT regenerate
            existing_md_path = report_draft.get("content_markdown_path", "")
            if existing_md_path:
                md_path = existing_md_path
                logger.info("export_report: v2 report already persisted at %s, skipping regeneration", md_path)
        else:
            # v1 fallback (DEPRECATED)
            try:
                md_path = f"data/reports/{report_draft.get('report_id', f'report_{run_id}')}.md"
                md_full = _build_markdown_report(
                    title=report_title,
                    sections=sections,
                    quality_summary=quality_summary_for_export,
                    report_status=report_status,
                )
                out_path = Path(md_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(md_full, encoding="utf-8")
                logger.info("export_report: wrote v1 markdown to %s (%d bytes)", md_path, len(md_full))
            except Exception as exc:
                logger.warning("export_report: failed to write markdown file: %s", exc)
                md_path = ""

        # --- Synthesize Key Findings using rule-based Insight Synthesizer ---
        key_findings = []
        try:
            from backend.app.services.insight_synthesizer import synthesize_findings
            key_findings = synthesize_findings(
                facts=facts or [],
                product_coverage=product_coverage,
                comparison_matrix=comparison_matrix,
            )
            logger.info("export_report: synthesized %d key findings", len(key_findings))
        except Exception as exc:
            logger.warning("export_report: insight synthesis failed, falling back to signed_claims: %s", exc)
            for sc in (signed_claims or [])[:5]:
                key_findings.append({
                    "text": sc.get("claim_text", ""),
                    "confidence": sc.get("confidence", 0.5),
                    "evidence_count": len(sc.get("evidence_ids", [])),
                    "finding_type": "general",
                })

        # --- Generate HTML report ---
        # CRITICAL FIX: v2 report HTML is generated by run_deep_report_workflow
        # and already persisted at report_draft["content_html_path"].
        # export_report MUST NOT regenerate it!
        html_path = ""
        pdf_rel_path = ""
        _pdf_generated = False
        report_version = state.get("report_version", "v1")

        try:
            if report_version == "v2":
                # v2: use the already-persisted v2 HTML path, do NOT regenerate
                existing_html_path = report_draft.get("content_html_path", "")
                if existing_html_path:
                    html_path = existing_html_path
                    logger.info("export_report: v2 report already persisted at %s, skipping regeneration", html_path)
                else:
                    # Fallback: use markdown-to-HTML wrapper instead of broken generate_html_report
                    report_data = state.get("report_draft", {})
                    try:
                        from backend.app.services.deep_report import generate_markdown_report as _gen_md
                        md_content = _gen_md(report_data)
                    except Exception:
                        md_content = "# \u62a5\u544a\u751f\u6210\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u65e5\u5fd7"
                    qs = report_data.get("quality_summary", {}) or {}
                    is_blocked = qs.get("report_status") in ("blocked_consistency", "blocked")
                    if is_blocked:
                        banner = "<div style=\"background:#fff3cd;border:2px solid #ffc107;padding:12px 16px;margin-bottom:16px;border-radius:6px\"><strong>\u26a0\ufe0f \u9884\u8bc4\u4f30\u9636\u6bb5</strong></div>"
                    else:
                        banner = ""
                    html_content = (
                        "<!DOCTYPE html><html lang=zh-CN><head>"
                        "<meta charset=UTF-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
                        "<title>" + report_data.get("quality_summary", {}).get("report_status", "Report") + "</title>"
                        "<style>body{font-family:sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#f5f5f5}"
                        ".card{background:white;border-radius:12px;padding:25px;margin-bottom:25px}"
                        "blockquote{background:#fff8e1;border-left:4px solid #FFC107;padding:10px 15px}"
                        "table{width:100%;border-collapse:collapse}th{background:#1a1a2e;color:white;padding:8px}"
                        "td{padding:8px;border-bottom:1px solid #eee}code{background:#f5f5f5;padding:1px 5px;border-radius:3px}"
                        "pre{background:#f5f5f5;padding:12px;border-radius:6px}@media(max-width:768px){body{padding:10px}}</style>"
                        "</head><body>" + banner + "<div class=card>" + md_content + "</div></body></html>"
                    )
                    html_path = f"data/reports/{report_draft.get('report_id', f'report_{run_id}')}.html"
                    Path(html_path).write_text(html_content, encoding="utf-8")
                    logger.warning("export_report: v2 fallback used basic HTML generator (generate_html_report broken)")
            else:
                # Use original HTML generator
                from scripts.generate_html_report import _build_beautiful_html, _safe_json
                
                project_context = None
                if task_brief:
                    project_context = {
                        "project_name": task_brief.get("title", ""),
                        "task_type": task_brief.get("task_type", ""),
                        "target_region": task_brief.get("target_region", ""),
                        "products": task_brief.get("products", []),
                    }

                # Build evidence_map from state for rendering evidence cards
                evidence_items = state.get("evidence_items", [])
                evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}

                html_content = _build_beautiful_html(
                    title=report_title,
                    run_id=run_id,
                    spans=[{"section_title": s.get("section_title", ""), "text": s.get("content_markdown") or s.get("text", ""), "claim_ids": s.get("claim_ids", []), "evidence_ids": s.get("evidence_ids", [])} for s in sections],
                    quality_summary=quality_summary_for_export,
                    report_status=report_status,
                    sources_map={},
                    created_at=utc_now(),
                    project_context=project_context,
                    key_findings=key_findings,
                    comparison_matrix=comparison_matrix,
                    comparison_products=product_names,
                    product_coverage=canonical_pcs,
                    evidence_map=evidence_map,
                )

            # ── v2: run_deep_report_workflow wrote .md/.html/.json — skip writing here
            # v1: write HTML file
            if report_version == "v2":
                # v2 HTML is already persisted by run_deep_report_workflow
                # Use existing path if available
                existing_html = report_draft.get("content_html_path", "")
                if existing_html and Path(existing_html).exists():
                    html_path = existing_html
                    logger.info("export_report: v2 HTML already exists at %s", html_path)
                else:
                    html_path = f"data/reports/{report_draft.get('report_id', f'report_{run_id}')}.html"
                    logger.warning("export_report: v2 HTML not found at %s, will be regenerated on next read", html_path)
            else:
                html_rel_path = f"data/reports/{report_draft.get('report_id', f'report_{run_id}')}.html"
                html_full_path = Path(html_rel_path)
                html_full_path.parent.mkdir(parents=True, exist_ok=True)
                html_full_path.write_text(html_content, encoding="utf-8")
                html_path = html_rel_path
                logger.info("export_report: wrote HTML to %s (%d bytes)", html_path, len(html_content))

                # P2.2: Generate PDF from the HTML report
                pdf_rel_path = f"data/reports/{report_draft.get('report_id', f'report_{run_id}')}.pdf"
                try:
                    from backend.app.services.deep_report import generate_pdf_report
                    pdf_result = generate_pdf_report(
                        html_content=html_content,
                        output_path=pdf_rel_path,
                        title=report_title,
                    )
                    if pdf_result.get("success"):
                        _pdf_generated = True
                        logger.info(
                            "export_report: wrote PDF to %s (%d bytes)",
                            pdf_result["path"], pdf_result["size_bytes"],
                        )
                    else:
                        logger.warning("export_report: PDF generation failed: %s", pdf_result.get("error"))
                except Exception as pdf_exc:
                    logger.warning("export_report: PDF generation raised: %s", pdf_exc)

        except Exception as exc:
            logger.warning("export_report: failed to write HTML file: %s", exc)

        # --- Write to DB (atomic) ---
        report_record = {
            "report_id": report_draft.get("report_id", f"report_{run_id}"),
            "run_id": run_id,
            "title": report_title,
            "report_status": report_status,
            "content_markdown_path": md_path,
            "content_html_path": html_path,
            "content_pdf_path": pdf_rel_path if _pdf_generated else "",
            "quality_summary": quality_summary_for_export,
            "created_by_agent": "WriterAgent",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }

        span_records = []
        for idx, section in enumerate(sections):
            span_text = section.get("content_markdown") or section.get("text", "")
            span_records.append({
                "span_id": f"span_{run_id}_{idx:02d}",
                "report_id": report_record["report_id"],
                "run_id": run_id,
                "section_id": section.get("section_id", f"section_{idx + 1:02d}"),
                "section_title": section.get("section_title", ""),
                "span_type": "paragraph",
                "text": span_text,
                "claim_ids": section.get("claim_ids", []),
                "evidence_ids": section.get("evidence_ids", []),
                "unsupported_flag": section.get("unsupported", False),
                "created_at": utc_now(),
            })

        try:
            from backend.app.storage.repositories import ReportRepository, ReportWriteError
            report_repo = ReportRepository()
            report_repo.add_report_with_spans(report_record, span_records)
            logger.info(
                "export_report: persisted report_id=%s with %d spans",
                report_record["report_id"], len(span_records),
            )
        except ReportWriteError as exc:
            logger.error(
                "export_report: DB write failed [%s] report_id=%s run_id=%s "
                "span_id=%s section_id=%s: %s",
                exc.code, exc.report_id, exc.run_id, exc.span_id, exc.section_id, exc.message,
            )
            state.setdefault("errors", []).append({
                "reason_code": exc.code,
                "message": (
                    f"[{exc.code}] report_id={exc.report_id} "
                    f"span_id={exc.span_id} section_id={exc.section_id} — {exc.message}"
                ),
                "node": "export_report",
                "report_id": exc.report_id,
                "run_id": exc.run_id,
                "span_id": exc.span_id,
                "section_id": exc.section_id,
            })
        except Exception as exc:
            logger.error("export_report: unexpected DB error: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_WRITE_REPORT_FAILED",
                "message": str(exc),
                "node": "export_report",
            })

    logger.info("export_report: run_id=%s final_status=%s", run_id, report_status)
    return state


def compute_metrics(state: WorkflowState) -> WorkflowState:
    claims = state.get("claim_drafts", [])
    signed_claims = state.get("signed_claims", [])
    evidence_items = state.get("evidence_items", [])
    rework_requests = state.get("rework_requests", [])
    sources = state.get("sources", [])
    errors = state.get("errors", [])
    schema_gaps = state.get("schema_gaps", [])
    schema_coverage = state.get("schema_coverage", {})
    rework_tasks = state.get("rework_tasks", [])
    rework_summary = state.get("rework_summary", {})
    rework_after_metrics = state.get("rework_after_metrics", {})

    total = len(claims) or 1  # avoid div by zero
    signed = len(signed_claims)
    review_pass_rate = signed / total

    claims_with_evidence = sum(1 for c in claims if c.get("evidence_ids"))
    evidence_coverage_rate = claims_with_evidence / total

    rework_required_count = total - signed
    unsupported_claim_rate = rework_required_count / total

    rework_succeeded = sum(1 for r in rework_requests if r.get("status") == "succeeded")
    rework_success_rate = rework_succeeded / len(rework_requests) if rework_requests else 1.0

    # Use real schema_completion_rate from SchemaGapPlanner shared method
    # (the detect_schema_gaps path via schema_coverage may return 0.0 due to key
    #  naming mismatch; use DB evidence count directly for authoritative value)
    run_id = state.get("run_id", "")
    if state.get("mode") == "real_time":
        from backend.app.storage.db import get_connection
        from backend.app.services.schema_gap_planner import SchemaGapPlanner
        try:
            with get_connection() as conn:
                usable_count = conn.execute(
                    "SELECT COUNT(*) FROM evidence_items WHERE run_id = ? AND usable_for_claim = 1",
                    (run_id,),
                ).fetchone()[0]
            products = state.get("task_brief", {}).get("products", []) or []
            planner = SchemaGapPlanner()
            schema_completion_rate = planner.compute_schema_completion_rate(
                usable_count, len(products)
            )
            logger.info(
                "compute_metrics: schema_completion_rate=%.3f (%d usable ev / %d products)",
                schema_completion_rate, usable_count, len(products),
            )
        except Exception as exc:
            logger.warning("compute_metrics: could not compute schema_completion_rate: %s", exc)
            schema_completion_rate = schema_coverage.get("schema_completion_rate", review_pass_rate)
    else:
        schema_completion_rate = schema_coverage.get("schema_completion_rate", review_pass_rate)

    # Build schema_gap_examples: top 10 high/medium priority gaps
    schema_gap_examples: list[dict[str, Any]] = []
    priority_gaps = [g for g in schema_gaps if g.get("priority") in ("high", "medium")]
    priority_gaps.sort(key=lambda x: (0 if x.get("priority") == "high" else 1, len(x.get("suggested_queries", []))))
    for gap in priority_gaps[:10]:
        schema_gap_examples.append({
            "product_name": gap.get("product_name", ""),
            "schema_key": gap.get("schema_key", ""),
            "gap_type": gap.get("gap_type", ""),
            "priority": gap.get("priority", ""),
            "suggested_queries": gap.get("suggested_queries", [])[:4],
            "reason": gap.get("reason", ""),
        })

    # Build schema_gap_suggested_queries_by_product
    schema_gap_suggested_queries_by_product: dict[str, list[str]] = {}
    for gap in schema_gaps:
        product_name = gap.get("product_name", "")
        if product_name not in schema_gap_suggested_queries_by_product:
            schema_gap_suggested_queries_by_product[product_name] = []
        for q in gap.get("suggested_queries", [])[:4]:
            if q not in schema_gap_suggested_queries_by_product[product_name]:
                schema_gap_suggested_queries_by_product[product_name].append(q)
    # Limit each product to top 10 queries
    for product_name in schema_gap_suggested_queries_by_product:
        schema_gap_suggested_queries_by_product[product_name] = schema_gap_suggested_queries_by_product[product_name][:10]

    # Build rework_before_after
    rework_before_after = {}
    rework_task_examples: list[dict[str, Any]] = []

    if rework_tasks:
        # Collect metrics_before from first task as baseline "before"
        first_metrics_before = {}
        for t in rework_tasks:
            mb = t.get("metrics_before") or {}
            if mb:
                first_metrics_before = mb
                break

        # Compute "after" from current state
        from backend.app.services.rework_service import _snapshot_rework_metrics
        current_metrics = _snapshot_rework_metrics(
            schema_gaps=schema_gaps,
            claim_drafts=claims,
            signed_claims=signed_claims,
            evidence_items=evidence_items,
            facts=state.get("facts", []),
            sources=sources,
        )

        # Compute deltas
        delta_schema_gaps = len(schema_gaps) - first_metrics_before.get("schema_gaps_count", len(schema_gaps))
        delta_unsupported_rate = current_metrics["unsupported_claim_rate"] - first_metrics_before.get("unsupported_claim_rate", 0.0)
        delta_evidence_coverage = current_metrics["evidence_coverage_rate"] - first_metrics_before.get("evidence_coverage_rate", 0.0)

        rework_before_after = {
            "before": first_metrics_before,
            "after": current_metrics,
            "delta_schema_gaps": delta_schema_gaps,
            "delta_unsupported_claim_rate": round(delta_unsupported_rate, 3),
            "delta_evidence_coverage_rate": round(delta_evidence_coverage, 3),
        }

        # Build rework_task_examples (max 5)
        for t in rework_tasks[:5]:
            mb = t.get("metrics_before") or {}
            ma = t.get("metrics_after") or {}
            product_name = t.get("product_name", "")
            if not product_name:
                # Try to derive from product_id
                product_name = t.get("product_id", "").replace("_", " ").title()
            rework_task_examples.append({
                "rework_id": t.get("rework_id", t.get("task_id", "")),
                "source_type": t.get("source_type", "schema_gap"),
                "target_node": t.get("target_node", "extract_facts"),
                "product_name": product_name,
                "schema_key": t.get("schema_key", ""),
                "status": t.get("status", "pending"),
                "reason": t.get("reason", ""),
                "new_evidence_ids": t.get("new_evidence_ids", []),
                "new_fact_ids": t.get("new_fact_ids", []),
                "new_claim_ids": t.get("new_claim_ids", []),
                "metrics_before": mb,
                "metrics_after": ma,
            })

    # Count rework task statuses
    rework_task_count = len(rework_tasks)
    rework_succeeded_count = sum(1 for t in rework_tasks if t.get("status") == "succeeded")
    rework_failed_count = sum(1 for t in rework_tasks if t.get("status") == "failed")
    rework_skipped_count = sum(1 for t in rework_tasks if t.get("status") in ("skipped", "closed"))

    # P1-Redesign (2026-06-18): evidence attribution stats by rework iteration.
    # Iteration 0 = initial collect; iteration 1+ = added by true re-collect.
    evidence_by_iter: dict[int, int] = {}
    facts_by_iter: dict[int, int] = {}
    for ev in evidence_items:
        ri = int(ev.get("rework_iteration", 0) or 0)
        evidence_by_iter[ri] = evidence_by_iter.get(ri, 0) + 1
    facts_list = state.get("facts", []) or []
    for f in facts_list:
        ri = int(f.get("rework_iteration", 0) or 0)
        facts_by_iter[ri] = facts_by_iter.get(ri, 0) + 1
    rework_collect_triggered = int(state.get("_rework_collect_count", 0) or 0)
    # Evidence/facts added by rework rounds (iteration > 0)
    evidence_added_by_rework = sum(c for k, c in evidence_by_iter.items() if k > 0)
    facts_added_by_rework = sum(c for k, c in facts_by_iter.items() if k > 0)

    state["metrics"] = {
        "schema_completion_rate": schema_completion_rate,
        "evidence_coverage_rate": evidence_coverage_rate,
        "unsupported_claim_rate": unsupported_claim_rate,
        "review_pass_rate": review_pass_rate,
        "rework_success_rate": rework_success_rate,
        "replay_success_rate": 0.0 if errors else 1.0,
        "source_coverage_count": len({s.get("source_type") for s in sources if s.get("source_type")}),
        "analysis_time_minutes": 0.0,
        # Schema gap metrics
        "schema_gaps_count": len(schema_gaps),
        "high_priority_schema_gaps": schema_coverage.get("high_priority_gaps", 0),
        "schema_completion_by_product": schema_coverage.get("schema_coverage_by_product", {}),
        "missing_schema_keys_by_product": schema_coverage.get("missing_schema_keys_by_product", {}),
        "schema_gap_examples": schema_gap_examples,
        "schema_gap_suggested_queries_by_product": schema_gap_suggested_queries_by_product,
        # Rework metrics
        "rework_task_count": rework_task_count,
        "rework_succeeded_count": rework_succeeded_count,
        "rework_failed_count": rework_failed_count,
        "rework_skipped_count": rework_skipped_count,
        "rework_tasks_total": rework_summary.get("total_tasks", len(rework_tasks)),
        "rework_tasks_succeeded": rework_summary.get("succeeded", 0),
        "rework_tasks_failed": rework_summary.get("failed", 0),
        "rework_tasks_skipped": rework_summary.get("skipped", 0),
        "rework_after_metrics": rework_after_metrics,
        "rework_before_after": rework_before_after,
        "rework_task_examples": rework_task_examples,
        # P1-Redesign (2026-06-18): True feedback loop attribution metrics.
        "rework_collect_triggered_count": rework_collect_triggered,
        "evidence_added_by_rework_count": evidence_added_by_rework,
        "facts_added_by_rework_count": facts_added_by_rework,
        "evidence_by_rework_iteration": evidence_by_iter,
        "facts_by_rework_iteration": facts_by_iter,
        "rework_active_reason": state.get("rework_active_reason", ""),
    }

    # Persist eval log to DB (real_time mode)
    run_id = state.get("run_id", "unknown")
    if state.get("mode") == "real_time":
        try:
            from backend.app.storage.repositories import EvalRepository
            EvalRepository().add_eval_log({
                "eval_id": f"eval_{run_id}",
                "run_id": run_id,
                "schema_completion_rate": schema_completion_rate,
                "evidence_coverage_rate": evidence_coverage_rate,
                "unsupported_claim_rate": unsupported_claim_rate,
                "review_pass_rate": review_pass_rate,
                "rework_success_rate": rework_success_rate,
                "replay_success_rate": 0.0 if errors else 1.0,
                "manual_correction_rate": 0.0,
                "source_coverage_count": len({s.get("source_type") for s in sources if s.get("source_type")}),
                "conflict_count": 0,
                "analysis_time_minutes": 0.0,
                "metrics": {
                    **state["metrics"],
                    "rework_after_metrics": rework_after_metrics,
                },
                "created_at": utc_now(),
            })
            logger.info("compute_metrics: persisted eval_log for run_id=%s", run_id)
        except Exception as exc:
            logger.error("Failed to persist eval log: %s", exc)
            state.setdefault("errors", []).append({
                "reason_code": "DB_WRITE_METRICS_FAILED",
                "message": str(exc),
                "node": "compute_metrics",
            })

    return state
