"""
Multi-Round Search Service - Research Loop with Gap-Driven Search.

This service implements a critic-driven rework loop that:
1. Analyzes evidence gaps by product and dimension
2. Generates targeted search queries based on gaps
3. Performs web search via configured provider (Doubao API)
4. Fetches and extracts evidence from discovered URLs
5. Returns new evidence to be integrated into the workflow

This is the key component for the "Coverage Critic → Rework Loop" pattern
described in the search research report.

vNext-P0.5 Enhancement:
- Added quality-aware gap filtering
- Force search for products with low-quality evidence
- Support for SPA/fallback-averse websites (like Coze)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.app.services.search_provider import (
    SearchProvider,
    SearchResult,
    get_search_provider,
    SEARCH_SUCCESS,
)

logger = logging.getLogger(__name__)


# Minimum evidence needed per product/dimension to consider it "covered"
MIN_EVIDENCE_PER_GAP = 2

# Minimum quality score threshold (evidence with lower score is considered "low quality")
MIN_QUALITY_THRESHOLD = 0.4

# Maximum queries per gap round
MAX_QUERIES_PER_GAP = 5

# Maximum new evidence to collect per gap round
MAX_NEW_EVIDENCE_PER_ROUND = 30  # Increased from 15 to ensure fallback gaps get processed

# Maximum queries per search round (increased to ensure fallback gaps are processed)
MAX_QUERIES_PER_ROUND = 30

# Force search if product has evidence but quality is low
FORCE_SEARCH_ON_LOW_QUALITY = True

# Minimum distinct schema_keys needed per product
MIN_DISTINCT_SCHEMA_KEYS = 2


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MultiRoundSearchService:
    """
    Service for performing gap-driven multi-round search.

    This service is invoked by execute_rework when evidence is missing
    for specific product/dimension combinations.
    """

    def __init__(self):
        self.provider: SearchProvider | None = None
        self._init_provider()

    def _init_provider(self) -> None:
        """Initialize search provider."""
        try:
            self.provider = get_search_provider()
            if self.provider.is_configured:
                logger.info(
                    "MultiRoundSearchService: initialized with provider=%s",
                    self.provider.provider_name,
                )
            else:
                logger.warning(
                    "MultiRoundSearchService: no search provider configured"
                )
        except Exception as exc:
            logger.error(
                "MultiRoundSearchService: failed to initialize provider: %s",
                exc,
            )
            self.provider = None

    def search_for_gaps(
        self,
        gaps: list[dict[str, Any]],
        existing_evidence: list[dict[str, Any]],
        existing_sources: list[dict[str, Any]],
        run_id: str,
        product_slugs_needing_search: list[str] | None = None,
        llm_supplemental_queries: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """
        Perform gap-driven search to fill evidence gaps.

        P1-B Fix: Accepts llm_supplemental_queries from coverage_critic (LLM-generated
        via _generate_llm_supplemental_queries) and injects them as highest-priority
        queries into the search pipeline. Falls back to static _generate_gap_queries
        for any gaps not covered by LLM queries.

        Args:
            gaps: List of schema gaps (from detect_schema_gaps)
            existing_evidence: Current evidence items
            existing_sources: Current sources (URLs already fetched)
            run_id: Current run ID
            product_slugs_needing_search: Optional list of product slugs that need search
                (fallback when gaps don't cover all products)
            llm_supplemental_queries: P1-A LLM-generated queries from coverage_critic,
                each dict with keys: competitor, competitor_id, product_slug, query,
                schema_key, reason, priority

        Returns:
            {
                "new_evidence": [...],
                "new_sources": [...],
                "gaps_filled": [...],
                "gaps_remaining": [...],
                "queries_used": [...],
            }
        """
        if not self.provider or not self.provider.is_configured:
            logger.warning(
                "MultiRoundSearchService: no provider, cannot search for gaps"
            )
            return {
                "new_evidence": [],
                "new_sources": [],
                "gaps_filled": [],
                "gaps_remaining": gaps,
                "queries_used": [],
                "error": "No search provider configured",
            }

        # Build coverage map from existing evidence
        coverage = self._build_coverage_map(existing_evidence)

        # vNext-P0.5: Pass evidence to enable quality-aware filtering
        actionable_gaps = self._filter_actionable_gaps(gaps, coverage, existing_evidence)

        # vNext-P0.5: Always check for fallback gaps if product_slugs_needing_search is provided
        # This ensures products with low-quality evidence get searched even if schema gaps exist
        logger.info(
            "MultiRoundSearchService: product_slugs_needing_search=%s (len=%d)",
            product_slugs_needing_search,
            len(product_slugs_needing_search) if product_slugs_needing_search else 0,
        )
        if product_slugs_needing_search:
            fallback_gaps = self._create_fallback_gaps(
                product_slugs_needing_search, existing_evidence
            )
            logger.info(
                "MultiRoundSearchService: created %d fallback gaps for %d products (needed_search=%s)",
                len(fallback_gaps),
                len(product_slugs_needing_search),
                product_slugs_needing_search,
            )
            # Merge fallback gaps with actionable gaps (avoid duplicates)
            # vNext-P0.5: Put fallback gaps FIRST so they're processed before schema gaps
            # This ensures products with collection issues (like Coze SPA) get searched
            existing_gap_ids = {g.get("gap_id", "") for g in actionable_gaps}
            fallback_gaps_list = []
            for fg in fallback_gaps:
                if fg.get("gap_id", "") not in existing_gap_ids:
                    fallback_gaps_list.append(fg)
            # Prepend fallback gaps so they're prioritized
            actionable_gaps = fallback_gaps_list + actionable_gaps
            logger.info(
                "MultiRoundSearchService: merged %d fallback + %d existing gaps = %d total",
                len(fallback_gaps_list),
                len(actionable_gaps) - len(fallback_gaps_list),
                len(actionable_gaps),
            )

        if not actionable_gaps:
            logger.info(
                "MultiRoundSearchService: no actionable gaps (all covered)"
            )
            return {
                "new_evidence": [],
                "new_sources": [],
                "gaps_filled": [],
                "gaps_remaining": gaps,
                "queries_used": [],
            }

        # P1-B: Generate static queries for each gap
        queries = self._generate_gap_queries(actionable_gaps)

        # P1-B: Inject LLM-generated queries (from coverage_critic) as highest priority.
        # These are searched FIRST so their results are used before static queries.
        # For each gap, check if we have an LLM query for that (product_slug, schema_key) pair.
        llm_queries_injected: set[tuple[str, str]] = set()
        if llm_supplemental_queries:
            logger.info(
                "P1-B: Injecting %d LLM-generated queries as highest priority "
                "(before %d static queries)",
                len(llm_supplemental_queries), len(queries),
            )
            llm_query_list: list[dict[str, Any]] = []
            for lq in llm_supplemental_queries:
                key = (lq.get("product_slug", ""), lq.get("schema_key", ""))
                if key in llm_queries_injected:
                    continue
                llm_queries_injected.add(key)
                llm_query_list.append({
                    "competitor": lq.get("competitor", ""),
                    "competitor_id": lq.get("competitor_id", ""),
                    "product_slug": lq.get("product_slug", ""),
                    "query": lq.get("query", ""),
                    "schema_key": lq.get("schema_key", ""),
                    "priority": lq.get("priority", "high"),
                    "gap_id": f"llm_gap_{uuid.uuid4().hex[:8]}",
                    "quality_context": "llm_generated",
                    "reason": lq.get("reason", ""),
                })
            # Prepend LLM queries so they're searched before static queries
            queries = llm_query_list + queries
            logger.info(
                "P1-B: After injection — %d LLM queries + %d static queries = %d total",
                len(llm_query_list), len(queries) - len(llm_query_list), len(queries),
            )

        # Perform searches and collect evidence
        return self._search_and_extract(
            queries=queries,
            actionable_gaps=actionable_gaps,
            existing_sources=existing_sources,
            run_id=run_id,
        )

    def _create_fallback_gaps(
        self,
        product_slugs: list[str],
        existing_evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Create fallback gaps for products that need evidence but have no schema gaps.

        This handles cases like Coze where evidence exists but under wrong schema_key,
        or products with low-quality evidence that doesn't match any schema requirement.
        """
        gaps = []

        # Get existing evidence by product_slug (not product_id which may have run_id prefix)
        evidence_by_product: dict[str, list[dict]] = {}
        for e in existing_evidence:
            # Use product_slug as the clean identifier, fallback to product_id
            slug = e.get("product_slug", "") or e.get("product_id", "")
            if slug:
                # Strip run_id prefix if present (e.g., "e2e_xxx_coze" -> "coze")
                # Pattern: run_id is ~20+ chars followed by underscore and slug
                if "_" in slug and len(slug) > 20:
                    slug = slug.split("_")[-1]

                if slug not in evidence_by_product:
                    evidence_by_product[slug] = []
                evidence_by_product[slug].append(e)

        # Known dimension mappings for search
        DIMENSION_KEYS = [
            "workflow_orchestration",
            "rag_support",
            "tool_calling",
            "pricing_model",
            "enterprise_readiness",
        ]

        logger.info(
            "_create_fallback_gaps: called with %d product_slugs: %s",
            len(product_slugs),
            product_slugs,
        )

        for slug in product_slugs:
            # Check if this product has evidence
            product_evidence = evidence_by_product.get(slug, [])

            logger.info(
                "_create_fallback_gaps: checking slug=%s, found %d evidence",
                slug, len(product_evidence)
            )

            # If product has evidence but quality is low, create gap for each dimension
            if product_evidence:
                quality_scores = [e.get("quality_score", 0.5) for e in product_evidence]
                avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 1.0

                # Also check for short snippets (indicative of SPA/fallback failure)
                snippet_lens = [len(e.get("snippet", "")) for e in product_evidence]
                avg_snippet_len = sum(snippet_lens) / len(snippet_lens) if snippet_lens else 1000
                is_short_snippets = avg_snippet_len < 200  # < 200 chars indicates fallback failure

                # Create fallback gaps if quality is low OR snippets are very short
                # vNext-P0.5: Lower threshold to 0.7 and add snippet length check
                # to ensure products like Coze (SPA with short fallback snippets) get re-searched
                if avg_quality < 0.7 or is_short_snippets:
                    for dim_key in DIMENSION_KEYS[:3]:  # Top 3 dimensions
                        gap = {
                            "gap_id": f"fallback_{slug}_{dim_key}",
                            "product_id": slug,
                            "product_slug": slug,
                            "product_name": slug.replace("_", " ").title(),
                            "schema_key": dim_key,
                            "priority": "medium",
                            "gap_type": "low_quality_evidence",
                            "reason": f"avg_quality={avg_quality:.2f}<0.7 or short_snippets={is_short_snippets}",
                            "current_evidence_count": len(product_evidence),
                            "needed_evidence": 2,
                            "avg_quality": avg_quality,
                            "avg_snippet_len": avg_snippet_len,
                        }
                        gaps.append(gap)
                        logger.info(
                            "_create_fallback_gaps: added gap for %s/%s (quality=%.2f, snippet_len=%.0f)",
                            slug, dim_key, avg_quality, avg_snippet_len
                        )

        logger.info(
            "_create_fallback_gaps: returning %d gaps",
            len(gaps),
        )

        return gaps[:15]

    def _build_coverage_map(
        self, evidence: list[dict[str, Any]]
    ) -> dict[str, dict[str, int]]:
        """
        Build coverage map: {product_slug: {schema_key: count}}.

        Counts how many evidence items exist per product/dimension.
        """
        coverage: dict[str, dict[str, int]] = {}

        for e in evidence:
            product_slug = e.get("product_slug", "") or _slugify(
                e.get("product_id", "")
            )
            schema_key = e.get("schema_key", "")

            if not product_slug or not schema_key:
                continue

            if product_slug not in coverage:
                coverage[product_slug] = {}
            coverage[product_slug][schema_key] = (
                coverage[product_slug].get(schema_key, 0) + 1
            )

        return coverage

    def _filter_actionable_gaps(
        self,
        gaps: list[dict[str, Any]],
        coverage: dict[str, dict[str, int]],
        evidence: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Filter gaps that need evidence collection.

        A gap is actionable if:
        - It's high/medium priority
        - Evidence count < MIN_EVIDENCE_PER_GAP
        OR
        - Force search on low quality evidence is enabled AND evidence quality is below threshold

        vNext-P0.5 Enhancement: Added quality-aware filtering and product-level fallback.
        Products with low-quality evidence (e.g., short snippets from SPA fallbacks)
        will be prioritized for re-search.

        If a product has evidence but with mismatched schema keys, we'll still
        include it as an actionable gap for that product.
        """
        actionable = []
        evidence_by_key: dict[str, list[dict]] = {}
        evidence_by_product: dict[str, list[dict]] = {}

        # Build evidence lookup by product/schema_key AND by product
        # Note: Use product_slug for clean keys (product_id may have run_id prefix)
        if evidence:
            for e in (evidence or []):
                # Prefer product_slug, fallback to product_id
                pid = e.get("product_slug", "") or e.get("product_id", "")
                key = e.get("schema_key", "")
                if pid:
                    # Strip run_id prefix if present
                    if "_" in pid and len(pid) > 20:
                        pid = pid.split("_")[-1]

                    ev_key = f"{pid}:{key}"
                    if ev_key not in evidence_by_key:
                        evidence_by_key[ev_key] = []
                    evidence_by_key[ev_key].append(e)

                    if pid not in evidence_by_product:
                        evidence_by_product[pid] = []
                    evidence_by_product[pid].append(e)

        for gap in gaps:
            priority = gap.get("priority", "low")
            if priority == "low":
                continue

            product_slug = (
                gap.get("product_slug", "") or _slugify(gap.get("product_id", ""))
            )
            schema_key = gap.get("schema_key", "")
            product_id = gap.get("product_id", "")

            if not product_slug or not schema_key:
                continue

            current_count = 0
            avg_quality = 1.0
            min_quality = 1.0
            is_matched = False  # Did we find matching evidence?

            # Get evidence for this product/schema_key
            ev_key = f"{product_id}:{schema_key}"
            ev_key_slug = f"{product_slug}:{schema_key}"
            relevant_evidence = evidence_by_key.get(ev_key) or evidence_by_key.get(ev_key_slug) or []

            if relevant_evidence:
                is_matched = True
                current_count = len(relevant_evidence)
                quality_scores = [e.get("quality_score", 0.5) for e in relevant_evidence]
                avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 1.0
                min_quality = min(quality_scores) if quality_scores else 1.0
            elif product_slug in coverage and schema_key in coverage[product_slug]:
                current_count = coverage[product_slug][schema_key]

            # Check if action is needed
            needs_action = False
            reason = ""

            if current_count < MIN_EVIDENCE_PER_GAP:
                needs_action = True
                reason = f"low_count({current_count}<{MIN_EVIDENCE_PER_GAP})"
            elif FORCE_SEARCH_ON_LOW_QUALITY and avg_quality < MIN_QUALITY_THRESHOLD:
                needs_action = True
                reason = f"low_quality(avg={avg_quality:.2f}<{MIN_QUALITY_THRESHOLD})"
            elif FORCE_SEARCH_ON_LOW_QUALITY and min_quality < MIN_QUALITY_THRESHOLD * 0.5:
                needs_action = True
                reason = f"very_low_quality(min={min_quality:.2f})"

            # vNext-P0.5: Fallback for products with mismatched evidence
            # If we have evidence for this product but NOT for this schema_key,
            # still include it as actionable (regardless of needs_action state)
            if product_id in evidence_by_product:
                product_evidence = evidence_by_product.get(product_id, [])
                if product_evidence:
                    # Check if any evidence has this schema_key
                    has_matching = any(e.get("schema_key") == schema_key for e in product_evidence)
                    if not has_matching:
                        # Product has evidence but not for this key - mark as schema_mismatch
                        needs_action = True
                        reason = "schema_mismatch"
                        current_count = len(product_evidence)  # Use total count

            if needs_action:
                actionable.append({
                    **gap,
                    "product_slug": product_slug,
                    "current_evidence_count": current_count,
                    "needed_evidence": max(0, MIN_EVIDENCE_PER_GAP - current_count),
                    "avg_quality": avg_quality,
                    "reason": reason,
                    "is_matched": is_matched,
                })

        # Sort by priority and evidence deficit
        priority_order = {"high": 0, "medium": 1, "low": 2}
        actionable.sort(
            key=lambda g: (
                priority_order.get(g.get("priority", "low"), 2),
                -g.get("needed_evidence", 0),
                g.get("avg_quality", 1.0),
            )
        )

        return actionable[:15]  # Limit to top 15 gaps per round

    def _generate_gap_queries(
        self, gaps: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Generate search queries for each actionable gap.

        vNext-P0.5 Enhancement:
        - Generates more queries for products with low-quality evidence
        - Adds queries for specific schema dimensions
        - Prioritizes known high-quality sources

        Returns list of {competitor, query, schema_key, gap_info}
        """
        queries = []

        # Known high-quality sources for Chinese products
        # Coze: coze.cn is accessible from overseas, add third-party sources
        HIGH_QUALITY_SOURCES = {
            "coze": [
                "site:coze.cn",  # Main CN site (accessible from overseas)
                "site:36kr.com/in coze",  # 36kr product article
                "site:zhihu.com/question coze",  # Zhihu Q&A
                "site:byteplus.com/coze",
            ],
            "dify": [
                "site:dify.ai",
                "site:github.com/langgenius/dify",
                "site:reddit.com dify",
            ],
        }

        for gap in gaps:
            product_name = gap.get("product_name", gap.get("product_id", ""))
            schema_key = gap.get("schema_key", "")
            priority = gap.get("priority", "medium")
            avg_quality = gap.get("avg_quality", 1.0)
            product_slug = gap.get("product_slug", "").lower()

            if not product_name:
                continue

            # Generate dimension-specific queries
            gap_queries = _generate_dimension_queries(product_name, schema_key)

            # Add more queries if evidence quality is low
            # vNext-P0.5: Use 0.7 threshold to match _create_fallback_gaps logic
            # This ensures low-quality evidence products get more queries
            num_queries = 2  # default
            if avg_quality < 0.7:  # Match the threshold in _create_fallback_gaps
                num_queries = 4  # Generate more queries for low quality

            # Add site-specific queries for products with known collection issues
            if product_slug in HIGH_QUALITY_SOURCES:
                for site_query in HIGH_QUALITY_SOURCES[product_slug][:2]:
                    site_specific_query = f"{product_name} {schema_key} {site_query}"
                    gap_queries.append(site_specific_query)
                    num_queries += 1

            for query in gap_queries[:num_queries]:
                queries.append({
                    "competitor": product_name,
                    "competitor_id": gap.get("product_id", ""),
                    "product_slug": gap.get("product_slug", ""),
                    "query": query,
                    "schema_key": schema_key,
                    "priority": priority,
                    "gap_id": gap.get("gap_id", f"gap_{uuid.uuid4().hex[:8]}"),
                    "quality_context": f"avg_quality={avg_quality:.2f}",
                })

        return queries

    def _search_and_extract(
        self,
        queries: list[dict[str, Any]],
        actionable_gaps: list[dict[str, Any]],
        existing_sources: list[dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        """
        Perform searches and extract evidence from results.
        
        Uses parallel execution for search queries to reduce total time.
        With ~30s per search, parallel execution is critical:
        - Serial: 30 queries × 30s = 900s (15 min)
        - Parallel (5 concurrent): 30 queries / 5 × 30s = 180s (3 min)
        """
        # Deduplicate URLs from existing sources
        existing_urls = set()
        for s in existing_sources:
            url = s.get("url", "")
            if url:
                existing_urls.add(_normalize_url(url))

        new_evidence: list[dict[str, Any]] = []
        new_sources: list[dict[str, Any]] = []
        gaps_filled: list[dict[str, Any]] = []
        gaps_remaining = list(actionable_gaps)
        queries_used: list[dict[str, Any]] = []

        # Track which gaps are being filled
        filled_keys: set[str] = set()

        # Limit queries per round and use parallel execution
        queries_to_run = queries[:MAX_QUERIES_PER_ROUND]
        max_concurrent = min(5, len(queries_to_run))  # Limit concurrent searches

        # P2-Fix: Skip searches if provider is unreachable (network/timeout).
        # _search_single() will time out on each query if provider is down.
        # Adding a 6s health check up front avoids burning 15s × N queries.
        if self.provider:
            import time as _time
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeoutError
                HEALTH_TIMEOUT = 20  # Doubao Responses API typically needs 15-25s
                with ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(self.provider.search, "health_check_probe", 1)
                    future.result(timeout=HEALTH_TIMEOUT)
                provider_healthy = True
                logger.info(
                    "_search_and_extract: provider %s is healthy, proceeding with %d queries",
                    self.provider.provider_name, len(queries_to_run),
                )
            except (_FuturesTimeoutError, Exception) as exc:
                provider_healthy = False
                logger.warning(
                    "_search_and_extract: provider unhealthy (%s). "
                    "Skipping %d searches to avoid timeout delays. "
                    "Evidence pool will use existing sources only.",
                    exc, len(queries_to_run),
                )
                return {
                    "new_evidence": [],
                    "new_sources": [],
                    "gaps_filled": [],
                    "gaps_remaining": gaps_remaining,
                    "queries_used": [],
                    "error": f"Search provider unhealthy: {exc}",
                }
        else:
            provider_healthy = False
            logger.warning("_search_and_extract: no provider available, skipping all searches")
            return {
                "new_evidence": [],
                "new_sources": [],
                "gaps_filled": [],
                "gaps_remaining": gaps_remaining,
                "queries_used": [],
                "error": "No search provider",
            }

        def _search_single(query_def: dict[str, Any]) -> dict[str, Any]:
            """Execute a single search query."""
            competitor = query_def.get("competitor", "")
            query = query_def.get("query", "")
            schema_key = query_def.get("schema_key", "")
            gap_id = query_def.get("gap_id", "")
            
            if not query or not competitor:
                return {"query_def": query_def, "search_results": [], "error": "empty query"}
            
            try:
                # Use shorter timeout for parallel execution
                search_results = self.provider.search(query, limit=5)
                return {
                    "query_def": query_def,
                    "search_results": search_results,
                    "error": None,
                    "success": bool(search_results),
                }
            except Exception as exc:
                logger.warning(f"Search failed for '{query}': {exc}")
                return {
                    "query_def": query_def,
                    "search_results": [],
                    "error": str(exc),
                    "success": False,
                }
        
        # Execute searches in parallel
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            logger.info(
                f"_search_and_extract: executing {len(queries_to_run)} queries "
                f"with {max_concurrent} concurrent workers"
            )
            
            with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
                # Submit all search tasks
                future_to_query = {
                    executor.submit(_search_single, q): q 
                    for q in queries_to_run
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_query):
                    try:
                        result = future.result(timeout=60)  # 60s timeout per query
                    except Exception as exc:
                        result = {"query_def": future_to_query[future], "search_results": [], "error": str(exc), "success": False}
                    
                    query_def = result.get("query_def", {})
                    search_results = result.get("search_results", [])
                    competitor = query_def.get("competitor", "")
                    schema_key = query_def.get("schema_key", "")
                    gap_id = query_def.get("gap_id", "")
                    
                    queries_used.append({
                        **query_def,
                        "results_count": len(search_results),
                        "status": "success" if result.get("success") else "failed",
                        "reason": result.get("error", "no_results"),
                    })
                    
                    # Process search results
                    for sr in search_results:
                        url = sr.url
                        if not url or _normalize_url(url) in existing_urls:
                            continue

                        # Mark URL as seen
                        existing_urls.add(_normalize_url(url))

                        # Create source record
                        source = {
                            "source_id": f"src_{uuid.uuid4().hex[:12]}",
                            "run_id": run_id,
                            "product_id": query_def.get("competitor_id", ""),
                            "product_slug": query_def.get("product_slug", ""),
                            "url": url,
                            "title": sr.title,
                            "source_type": _infer_source_type(url),
                            "status": "pending",
                            "error": None,
                            "discovered_via": "multi_round_search",
                            "discovery_query": query_def.get("query", ""),
                            "schema_key": schema_key,
                            "gap_id": gap_id,
                            "created_at": utc_now(),
                        }
                        new_sources.append(source)

                        # Create evidence from snippet
                        evidence = {
                            "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                            "run_id": run_id,
                            "product_id": query_def.get("competitor_id", ""),
                            "product_slug": query_def.get("product_slug", ""),
                            "source_url": url,
                            "source_title": sr.title,
                            "schema_key": schema_key,
                            "content": sr.snippet,
                            "content_type": "snippet_only",
                            "extraction_status": "snippet_only",
                            "gap_id": gap_id,
                            "quality_score": _score_snippet(sr.snippet, schema_key),
                            "created_at": utc_now(),
                        }
                        new_evidence.append(evidence)

                        # Track filled gaps
                        gap_key = f"{query_def.get('product_slug')}:{schema_key}"
                        if gap_key not in filled_keys:
                            filled_keys.add(gap_key)
                            gaps_filled.append({
                                "product_slug": query_def.get("product_slug", ""),
                                "product_name": competitor,
                                "schema_key": schema_key,
                                "gap_id": gap_id,
                                "evidence_added": 1,
                                "source_url": url,
                            })
                    
                    # Check if we have enough new evidence
                    if len(new_evidence) >= MAX_NEW_EVIDENCE_PER_ROUND:
                        logger.info(
                            f"_search_and_extract: reached MAX_NEW_EVIDENCE_PER_ROUND "
                            f"({MAX_NEW_EVIDENCE_PER_ROUND}), stopping early"
                        )
                        break
            
            logger.info(
                f"_search_and_extract: completed {len(queries_used)} queries, "
                f"collected {len(new_evidence)} evidence from {len(new_sources)} sources"
            )
            
        except ImportError:
            # Fallback to serial execution if concurrent.futures not available
            logger.warning("ThreadPoolExecutor not available, falling back to serial execution")
            for query_def in queries_to_run:
                result = _search_single(query_def)
                search_results = result.get("search_results", [])
                
                queries_used.append({
                    **query_def,
                    "results_count": len(search_results),
                    "status": "success" if result.get("success") else "failed",
                    "reason": result.get("error", "no_results"),
                })
                
                competitor = query_def.get("competitor", "")
                schema_key = query_def.get("schema_key", "")
                gap_id = query_def.get("gap_id", "")
                
                for sr in search_results:
                    url = sr.url
                    if not url or _normalize_url(url) in existing_urls:
                        continue

                    existing_urls.add(_normalize_url(url))

                    source = {
                        "source_id": f"src_{uuid.uuid4().hex[:12]}",
                        "run_id": run_id,
                        "product_id": query_def.get("competitor_id", ""),
                        "product_slug": query_def.get("product_slug", ""),
                        "url": url,
                        "title": sr.title,
                        "source_type": _infer_source_type(url),
                        "status": "pending",
                        "error": None,
                        "discovered_via": "multi_round_search",
                        "discovery_query": query_def.get("query", ""),
                        "schema_key": schema_key,
                        "gap_id": gap_id,
                        "created_at": utc_now(),
                    }
                    new_sources.append(source)

                    evidence = {
                        "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                        "run_id": run_id,
                        "product_id": query_def.get("competitor_id", ""),
                        "product_slug": query_def.get("product_slug", ""),
                        "source_url": url,
                        "source_title": sr.title,
                        "schema_key": schema_key,
                        "content": sr.snippet,
                        "content_type": "snippet_only",
                        "extraction_status": "snippet_only",
                        "gap_id": gap_id,
                        "quality_score": _score_snippet(sr.snippet, schema_key),
                        "created_at": utc_now(),
                    }
                    new_evidence.append(evidence)

                    gap_key = f"{query_def.get('product_slug')}:{schema_key}"
                    if gap_key not in filled_keys:
                        filled_keys.add(gap_key)
                        gaps_filled.append({
                            "product_slug": query_def.get("product_slug", ""),
                            "product_name": competitor,
                            "schema_key": schema_key,
                            "gap_id": gap_id,
                            "evidence_added": 1,
                            "source_url": url,
                        })

                if len(new_evidence) >= MAX_NEW_EVIDENCE_PER_ROUND:
                    break

        # Update gaps_remaining
        remaining_keys = filled_keys
        gaps_remaining = [
            g for g in actionable_gaps
            if f"{g.get('product_slug')}:{g.get('schema_key')}" not in remaining_keys
        ]

        # P0-1 Fix: Filter out snippet_only evidence before returning
        # snippet_only should only go into source_candidates, not evidence_items
        filtered_evidence = [
            e for e in new_evidence 
            if e.get("content_type") != "snippet_only"
        ]
        snippet_count = len(new_evidence) - len(filtered_evidence)
        if snippet_count > 0:
            logger.info(
                f"P0-1 Fix: Filtered out {snippet_count} snippet_only evidence items "
                f"(will be tracked as source_candidates instead)"
            )
        
        return {
            "new_evidence": filtered_evidence[:MAX_NEW_EVIDENCE_PER_ROUND],
            "new_sources": new_sources,
            "gaps_filled": gaps_filled,
            "gaps_remaining": gaps_remaining,
            "queries_used": queries_used,
            "source_candidates": [  # P0-1: Track snippet_only as candidates for future fetch
                {"url": e.get("source_url"), "title": e.get("source_title"), 
                 "product": e.get("product_slug"), "schema_key": e.get("schema_key")}
                for e in new_evidence 
                if e.get("content_type") == "snippet_only"
            ],
        }

    def fetch_source_candidates(
        self,
        source_candidates: list[dict[str, Any]],
        run_id: str,
        max_concurrent: int = 3,
        max_per_product: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Fetch actual page content from source_candidates and extract evidence.
        
        This is the P1-1 fix: After multi-round search creates snippet_only evidence,
        we now actually fetch the URLs and extract high-quality evidence passages.
        
        Args:
            source_candidates: List of {"url", "title", "product", "schema_key"}
            run_id: Current run ID
            max_concurrent: Max parallel fetch workers
            max_per_product: Max URLs to fetch per product
        
        Returns:
            List of evidence items extracted from fetched content (content_type="fetched")
        """
        if not source_candidates:
            logger.info("fetch_source_candidates: no candidates to fetch")
            return []
        
        # Deduplicate and limit URLs per product
        seen_urls = set()
        by_product: dict[str, list[dict]] = {}
        
        for c in source_candidates:
            url = c.get("url", "")
            product = c.get("product", c.get("product_slug", ""))
            if not url or not product:
                continue
            
            # Deduplicate
            normalized = _normalize_url(url)
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            
            if product not in by_product:
                by_product[product] = []
            
            # Limit per product
            if len(by_product[product]) < max_per_product:
                by_product[product].append({
                    "url": url,
                    "title": c.get("title", ""),
                    "product": product,
                    "schema_key": c.get("schema_key", ""),
                })
        
        total_candidates = sum(len(v) for v in by_product.values())
        logger.info(
            f"fetch_source_candidates: fetching {total_candidates} URLs from {len(by_product)} products "
            f"(max_concurrent={max_concurrent}, max_per_product={max_per_product})"
        )
        
        all_evidence = []
        
        # Fetch URLs in parallel
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def _fetch_single(candidate: dict[str, Any]) -> list[dict[str, Any]]:
                """Fetch a single URL and extract evidence passages.

                P1-Fix: Uses fetch_url_with_fallback (3-level strategy: requests →
                Playwright → Search API) instead of bare fetch_url. This is critical
                for anti-bot sites (coze.cn, Notion, Confluence) where requests and
                Playwright both fail but Search API can still get relevant snippets.
                """
                from backend.app.services.web_fetcher import fetch_url_with_fallback

                url = candidate.get("url", "")
                product = candidate.get("product", "")
                schema_key = candidate.get("schema_key", "")
                title = candidate.get("title", "")

                if not url:
                    return []

                try:
                    result = fetch_url_with_fallback(
                        url,
                        per_url_timeout=15,
                        search_provider=self.provider,
                        product_name=product,
                    )
                    raw_text = result.get("raw_text", "")
                    fetched_title = result.get("title", title) or title
                    fetch_level = result.get("fetch_level", 0)

                    if not raw_text or len(raw_text) < 100:
                        logger.debug(
                            f"fetch_source_candidates: insufficient content for {url} "
                            f"(level={fetch_level}, {len(raw_text)} chars)"
                        )
                        return []

                    # Log whether we got real content or just search snippet
                    if fetch_level == 3:
                        logger.info(
                            f"fetch_source_candidates: got search-snippet content for {url} "
                            f"(L3 fallback, {len(raw_text)} chars)"
                        )
                    elif fetch_level == "failed":
                        logger.warning(
                            f"fetch_source_candidates: all fetch levels failed for {url}"
                        )
                        return []

                    # Extract evidence passages from fetched text
                    passages = self._extract_passages_from_text(
                        text=raw_text,
                        url=url,
                        title=fetched_title,
                        product_slug=product,
                        schema_key=schema_key,
                        run_id=run_id,
                    )

                    logger.info(
                        f"fetch_source_candidates: extracted {len(passages)} passages from {url} "
                        f"(level={fetch_level})"
                    )
                    return passages

                except Exception as exc:
                    logger.debug(
                        f"fetch_source_candidates: error fetching {url}: {exc}"
                    )
                    return []
            
            with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
                futures = []
                for product, candidates in by_product.items():
                    for candidate in candidates:
                        futures.append(
                            (executor.submit(_fetch_single, candidate), candidate)
                        )
                
                for future, candidate in futures:
                    try:
                        passages = future.result(timeout=60)
                        all_evidence.extend(passages)
                    except Exception as exc:
                        logger.debug(
                            f"fetch_source_candidates: future failed for {candidate.get('url')}: {exc}"
                        )
            
        except ImportError:
            logger.warning("ThreadPoolExecutor not available, falling back to serial fetch")
            for product, candidates in by_product.items():
                for candidate in candidates:
                    try:
                        passages = self._fetch_single_serial(candidate, run_id)
                        all_evidence.extend(passages)
                    except Exception as exc:
                        logger.debug(f"fetch_source_candidates: serial fetch failed: {exc}")
        
        logger.info(
            f"fetch_source_candidates: completed - extracted {len(all_evidence)} evidence "
            f"from {total_candidates} candidates"
        )
        return all_evidence

    def _fetch_single_serial(
        self, 
        candidate: dict[str, Any], 
        run_id: str
    ) -> list[dict[str, Any]]:
        """Fetch a single URL serially (fallback for no ThreadPoolExecutor)."""
        from backend.app.services.web_fetcher import fetch_url_with_fallback

        url = candidate.get("url", "")
        product = candidate.get("product", "")
        schema_key = candidate.get("schema_key", "")
        title = candidate.get("title", "")
        
        if not url:
            return []
        
        try:
            result = fetch_url_with_fallback(
                url,
                per_url_timeout=15,
                search_provider=self.provider,
                product_name=product,
            )
            raw_text = result.get("raw_text", "")
            fetched_title = result.get("title", title) or title
            fetch_level = result.get("fetch_level", 0)
            
            if not raw_text or len(raw_text) < 100:
                return []
            
            if fetch_level == "failed":
                logger.warning(f"_fetch_single_serial: all levels failed for {url}")
                return []
            
            return self._extract_passages_from_text(
                text=raw_text,
                url=url,
                title=fetched_title,
                product_slug=product,
                schema_key=schema_key,
                run_id=run_id,
            )
        except Exception:
            return []

    def _extract_passages_from_text(
        self,
        text: str,
        url: str,
        title: str,
        product_slug: str,
        schema_key: str,
        run_id: str,
        min_length: int = 80,
        max_length: int = 400,
    ) -> list[dict[str, Any]]:
        """
        Extract evidence passages from fetched text.
        
        Strategy:
        - Split text into sentences (Chinese + English)
        - Filter out: questions, opinions, ads, navigation
        - Extract factual passages of 80-400 chars
        - Assign quality score based on content relevance
        - Determine trust tier based on URL domain
        
        Args:
            text: Raw text content from URL
            url: Source URL
            title: Page title
            product_slug: Product identifier
            schema_key: Schema dimension key
            run_id: Current run ID
            min_length: Minimum passage length
            max_length: Maximum passage length
        
        Returns:
            List of evidence dicts
        """
        import re
        
        passages = []
        
        # Split into sentences - handle both Chinese and English
        # Chinese punctuation: 。？！
        # English punctuation: . ! ?
        # Also split on newlines for better coverage
        
        # Normalize text
        text = text.strip()
        if not text:
            return []
        
        # Split by sentence-ending punctuation, preserving the punctuation
        sentence_pattern = r'(?<=[。！？.!?])\s*'
        sentences = re.split(sentence_pattern, text)
        
        # Also add paragraphs (double newlines)
        paragraphs = re.split(r'\n\n+', text)
        sentences.extend(paragraphs)
        
        # Filter and combine sentences into passages
        current_passage = ""
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            
            # Skip obvious noise
            sent_lower = sent.lower()
            noise_patterns = [
                "登录", "注册", "copyright", "©", "privacy policy", "terms of service",
                "cookie", "subscribe", "newsletter", "advertisement", "广告",
                "javascript", "loading", "please wait",
            ]
            if any(noise in sent_lower for noise in noise_patterns):
                continue
            
            # Skip pure navigation
            if len(sent) < 20:
                continue
            
            # Skip obvious questions (asking user)
            if sent.endswith("?") or sent.endswith("？"):
                if any(q in sent_lower for q in ["如何", "怎么", "是否", "能不能", "有没有", "what", "how", "is it", "can i"]):
                    continue
            
            # Add to current passage
            if current_passage:
                current_passage += " " + sent
            else:
                current_passage = sent
            
            # If passage is long enough, finalize it
            if len(current_passage) >= min_length:
                # Trim to max_length if too long
                if len(current_passage) > max_length:
                    # Try to find a good break point
                    trim_point = current_passage.rfind("。", len(current_passage) - max_length)
                    if trim_point > min_length:
                        current_passage = current_passage[:trim_point + 1]
                
                # Skip if passage is mostly noise (check ratio)
                alpha_ratio = sum(c.isalpha() for c in current_passage) / max(len(current_passage), 1)
                if alpha_ratio < 0.3:
                    current_passage = ""
                    continue
                
                # Create evidence passage
                trust_tier = _determine_trust_tier(url)
                source_type = _infer_source_type(url)
                
                # Generate source_id from URL for linking evidence to source
                source_id = f"src_{uuid.uuid4().hex[:12]}"
                
                # Score based on length, schema relevance, and content quality
                # Require minimum 100 chars and reasonable content diversity
                quality_score = 0.0
                if len(current_passage) >= 100:
                    quality_score = min(0.5, len(current_passage) / 400)  # Base from length
                    quality_score += _score_snippet(current_passage, schema_key) * 0.3  # Schema relevance
                    # Bonus for multiple sentences (content diversity)
                    sentence_count = current_passage.count("。") + current_passage.count(". ")
                    if sentence_count >= 2:
                        quality_score = min(1.0, quality_score + 0.1)
                    quality_score = min(1.0, quality_score)
                
                # P1-1: Set usable_for_claim based on quality_score and trust_tier
                # A/B tier with quality >= 0.5 is usable
                # E tier (low trust) is not usable unless quality is very high
                usable = quality_score >= 0.5 and trust_tier != "low"
                
                evidence = {
                    "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                    "run_id": run_id,
                    "source_id": source_id,  # Required for DB persistence
                    "product_id": product_slug,
                    "product_slug": product_slug,
                    "source_url": url,
                    "source_title": title,
                    "schema_key": schema_key,
                    "snippet": current_passage[:500],  # Store first 500 chars as snippet
                    "content": current_passage,
                    "content_type": "fetched",  # Mark as actually fetched content
                    "extraction_status": "full_content",
                    "quality_score": quality_score,
                    "confidence": quality_score,
                    "evidence_type": "text",  # Required by DB schema
                    "trust_tier": trust_tier,
                    "source_type": source_type,
                    "usable_for_claim": usable,
                    "created_at": utc_now(),
                }
                passages.append(evidence)
                current_passage = ""
        
        # Don't forget the last passage if it's long enough
        if len(current_passage) >= min_length:
            trust_tier = _determine_trust_tier(url)
            source_type = _infer_source_type(url)
            source_id = f"src_{uuid.uuid4().hex[:12]}"  # Generate source_id for DB
            
            # Same quality scoring as above
            quality_score = 0.0
            if len(current_passage) >= 100:
                quality_score = min(0.5, len(current_passage) / 400)
                quality_score += _score_snippet(current_passage, schema_key) * 0.3
                sentence_count = current_passage.count("。") + current_passage.count(". ")
                if sentence_count >= 2:
                    quality_score = min(1.0, quality_score + 0.1)
                quality_score = min(1.0, quality_score)
            
            # P1-1: Set usable_for_claim based on quality_score and trust_tier
            usable = quality_score >= 0.5 and trust_tier != "low"
            
            evidence = {
                "evidence_id": f"ev_{uuid.uuid4().hex[:12]}",
                "run_id": run_id,
                "source_id": source_id,  # Required for DB persistence
                "product_id": product_slug,
                "product_slug": product_slug,
                "source_url": url,
                "source_title": title,
                "schema_key": schema_key,
                "snippet": current_passage[:500],  # Store first 500 chars as snippet
                "content": current_passage,
                "content_type": "fetched",
                "extraction_status": "full_content",
                "quality_score": quality_score,
                "confidence": quality_score,
                "evidence_type": "text",  # Required by DB schema
                "trust_tier": trust_tier,
                "source_type": source_type,
                "usable_for_claim": usable,
                "created_at": utc_now(),
            }
            passages.append(evidence)
        
        return passages


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def _normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return (parsed.netloc.lower() + parsed.path.lower()).rstrip("/")


def _infer_source_type(url: str) -> str:
    """Infer source type from URL."""
    url_lower = url.lower()
    if "docs" in url_lower or "documentation" in url_lower:
        return "documentation"
    if "pricing" in url_lower or "price" in url_lower or "plans" in url_lower:
        return "pricing_page"
    if "github" in url_lower:
        return "github"
    return "web_page"


def _generate_dimension_queries(product_name: str, schema_key: str) -> list[str]:
    """Generate search queries for a specific dimension."""
    name = product_name.strip()
    key = schema_key.lower()

    if "pricing" in key or "paid" in key or "free" in key or "tier" in key:
        return [
            f"{name} pricing plans free tier enterprise 2024 2025",
            f"{name} subscription cost per user per month",
        ]
    elif "workflow" in key or "orchestrat" in key or "automation" in key:
        return [
            f"{name} workflow automation builder visual",
            f"{name} orchestration pipeline features",
        ]
    elif "rag" in key or "knowledge" in key or "retrieval" in key:
        return [
            f"{name} RAG knowledge base vector search",
            f"{name} document retrieval embeddings",
        ]
    elif "model" in key or "llm" in key or "ai_model" in key:
        return [
            f"{name} LLM model support OpenAI Claude Gemini",
            f"{name} AI models supported features",
        ]
    elif "integration" in key or "api" in key or "webhook" in key:
        return [
            f"{name} API integration webhook plugins",
            f"{name} third-party integrations extensions",
        ]
    elif "deployment" in key or "host" in key or "cloud" in key:
        return [
            f"{name} deployment self-hosted cloud Docker",
            f"{name} install setup Kubernetes",
        ]
    elif "enterprise" in key or "security" in key or "sso" in key:
        return [
            f"{name} enterprise security SSO SAML",
            f"{name} compliance GDPR SOC2",
        ]
    elif "pricing" in key or "cost" in key:
        return [
            f"{name} pricing model cost comparison",
            f"{name} free trial open source",
        ]
    else:
        # Generic fallback
        return [
            f"{name} features capabilities overview",
            f"{name} documentation tutorial guide",
        ]


def _score_snippet(snippet: str, schema_key: str) -> float:
    """
    Score a search snippet based on relevance to schema_key.

    Returns a score between 0.0 and 1.0.
    """
    if not snippet:
        return 0.0

    snippet_lower = snippet.lower()
    key_lower = schema_key.lower()

    # Keywords that indicate relevance
    keywords = _get_dimension_keywords(key_lower)

    score = 0.0
    for kw in keywords:
        if kw in snippet_lower:
            score += 0.3

    # Bonus for product name mention
    if len(snippet) > 50:
        score = min(1.0, score)

    # Ensure minimum score for non-empty snippets
    if score == 0.0 and len(snippet) > 30:
        score = 0.3

    return score


def _get_dimension_keywords(schema_key: str) -> list[str]:
    """Get keywords for a schema dimension."""
    key = schema_key.lower()

    if "pricing" in key:
        return ["pricing", "free", "tier", "cost", "plan", "subscription", "paid"]
    elif "workflow" in key:
        return ["workflow", "automation", "pipeline", "orchestration", "builder"]
    elif "rag" in key or "knowledge" in key:
        return ["rag", "knowledge", "vector", "retrieval", "embeddings", "document"]
    elif "model" in key or "llm" in key:
        return ["llm", "model", "gpt", "claude", "gemini", "openai"]
    elif "integration" in key or "api" in key:
        return ["api", "integration", "webhook", "plugin", "extension"]
    elif "deployment" in key or "host" in key:
        return ["deploy", "host", "cloud", "docker", "kubernetes", "self-hosted"]
    elif "enterprise" in key or "security" in key:
        return ["enterprise", "security", "sso", "saml", "compliance"]
    else:
        return [key.replace("_", " ").split()[0] if key else ""]


# Official product domains - high trust
OFFICIAL_DOMAINS = {
    # Dify
    "dify.ai", "dify.dev", "langgenius.github.io",
    # Coze
    "coze.cn", "coze.com", "byteplus.com",
    # FastGPT
    "fastgpt.io", "fastgpt.cn", "doc.fastgpt.cn",
    # Flowise
    "flowiseai.com", "flowiseai.com",
    # GitHub (open source projects)
    "github.com", "gitlab.com",
    # Documentation sites
    "readme.com", "docsend.com",
}

# Third-party domains - low trust
THIRD_PARTY_DOMAINS = {
    "juejin.cn", "segmentfault.com", "zhihu.com", "csdn.net",
    "cnblogs.com", "toutiao.com", "baidu.com", "sina.com.cn",
    "bilibili.com", "weibo.com", "twitter.com", "x.com",
    "reddit.com", "medium.com", "dev.to", "hashnode.com",
    "36kr.com", "lieyunwang.com", "ithome.com",
}

# Blog/case study domains - medium trust
BLOG_DOMAINS = {
    "blog.google.com", "blog.aws.amazon.com", "blog.cloudflare.com",
    "engineering.fb.com", "techcrunch.com", "venturebeat.com",
    "thenewstack.io", "infoq.com", "dzone.com",
    "cloud.tencent.com", "cloud.tencent.cn",
    "developer.volcengine.com",
}

# Social/review domains - low trust
SOCIAL_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "linkedin.com",
    "reddit.com", "discord.com", "slack.com",
    "stackoverflow.com", "stackexchange.com",
    "g2.com", "capterra.com", "trustpilot.com",
    "producthunt.com", " AlternativeTo",
}


def _determine_trust_tier(url: str) -> str:
    """
    Determine trust tier based on URL domain.
    
    Returns: "high", "medium", or "low"
    """
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc.lower()
        # Remove www prefix
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        return "low"
    
    # Check exact match
    if domain in OFFICIAL_DOMAINS:
        return "high"
    
    # Check if it's a known third-party domain
    if domain in THIRD_PARTY_DOMAINS:
        return "low"
    
    if domain in BLOG_DOMAINS:
        return "medium"
    
    if domain in SOCIAL_DOMAINS:
        return "low"
    
    # Check for official subdomains
    for official in OFFICIAL_DOMAINS:
        if domain.endswith("." + official) or domain == official:
            return "high"
    
    # Check for GitHub (usually high trust for open source)
    if "github.com" in domain:
        return "high"
    
    # Check for documentation domains
    if "docs." in domain or "documentation" in domain:
        return "medium"
    
    # Default to medium for unknown domains
    return "medium"
