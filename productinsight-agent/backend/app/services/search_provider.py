"""
Search Provider Abstraction Layer

Provides a unified interface for different search/collection strategies:
- DoubaoWebSearchProvider: Web search via Doubao API
- SeedUrlProvider: Uses predefined seed URLs
- FixtureProvider: Mock data for testing
- PublicWebProvider: Direct web scraping (future)

This abstraction enables the system to work across domains without
hardcoding specific data sources.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Network Health Probe (P0: Resilience)
# ============================================================================
# Module-level singleton that records recent network calls (per host)
# and short-circuits providers that have been failing in this process.
# Prevents 30s × 3 retry × 30 queries = 45 minutes of wall-clock
# spinning when the egress to overseas APIs is blocked.

class _NetworkHealthProbe:
    """Thread-safe, process-level probe for outbound network health.

    Records the outcome of recent search attempts. When a host has
    failed (timeout / connection error) >= FAILURE_THRESHOLD times
    in a row, ``is_degraded(host)`` returns True and downstream
    providers should fast-fail rather than retry.
    """

    FAILURE_THRESHOLD = 3
    RECOVERY_AFTER_S = 60  # how long to remember failures

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}  # host -> [ts, ts, ...]
        self._total_calls: dict[str, int] = {}
        self._total_failures: dict[str, int] = {}

    def record(self, host: str, success: bool) -> None:
        now = time.time()
        with self._lock:
            self._total_calls[host] = self._total_calls.get(host, 0) + 1
            if not success:
                self._failures.setdefault(host, []).append(now)
                self._total_failures[host] = self._total_failures.get(host, 0) + 1
                # Prune old entries
                cutoff = now - self.RECOVERY_AFTER_S
                self._failures[host] = [t for t in self._failures[host] if t >= cutoff]
            else:
                # success clears the failure streak
                self._failures.pop(host, None)

    def is_degraded(self, host: str) -> bool:
        """True if the host has been failing in the recent window."""
        with self._lock:
            streak = len(self._failures.get(host, []))
            return streak >= self.FAILURE_THRESHOLD

    def stats(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {
                h: {
                    "calls": self._total_calls.get(h, 0),
                    "failures": self._total_failures.get(h, 0),
                    "recent_failures": len(self._failures.get(h, [])),
                    "degraded": self.is_degraded(h),
                }
                for h in set(self._total_calls) | set(self._failures)
            }


_HEALTH_PROBE = _NetworkHealthProbe()


def get_network_health() -> dict[str, dict[str, int]]:
    """Expose the network probe for observability."""
    return _HEALTH_PROBE.stats()


def is_degraded(host: str) -> bool:
    """Convenience check used by individual providers."""
    return _HEALTH_PROBE.is_degraded(host)

# Auto-load .env file from the project root (productinsight-agent/)
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)

# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class SearchResult:
    """Unified search result format from any provider."""
    title: str
    url: str
    snippet: str
    source_type: str = "web"  # web, api, document, fixture
    provider: str = "unknown"
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_source_dict(self) -> dict[str, Any]:
        """Convert to Source format for storage."""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_type": self.source_type,
            "provider": self.provider,
            "metadata_json": json.dumps(self.metadata, ensure_ascii=False),
        }
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to plain dict for tracing/serialization."""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source_type": self.source_type,
            "provider": self.provider,
            "rank": self.rank,
            "metadata": self.metadata,
        }


@dataclass
class CollectionConfig:
    """Configuration for collection mode."""
    mode: str = "doubao_web_search"  # doubao_web_search, seed_urls, public_web, fixture, hybrid
    max_results_per_query: int = 10
    timeout_seconds: int = 30
    retry_count: int = 3
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CollectionConfig":
        return cls(
            mode=data.get("mode", "doubao_web_search"),
            max_results_per_query=data.get("max_results_per_query", 10),
            timeout_seconds=data.get("timeout_seconds", 30),
            retry_count=data.get("retry_count", 3),
        )


# ============================================================================
# SearchProvider Interface
# ============================================================================

class SearchProvider(ABC):
    """Abstract base class for search providers."""
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass
    
    @abstractmethod
    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """
        Execute a search query and return results.
        
        Args:
            query: Search query string
            top_k: Maximum number of results to return
            limit: Alias for top_k (for backward compatibility)
            
        Returns:
            List of SearchResult objects
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this provider is available/accessible.
        
        Returns:
            True if the provider can be used
        """
        pass
    
    @property
    def is_configured(self) -> bool:
        """
        Check if this provider is configured (not in fallback mode).
        
        Override in subclasses if needed.
        """
        return True
    
    def batch_search(self, queries: list[str], top_k: int = 5) -> list[SearchResult]:
        """
        Execute multiple search queries.
        
        Default implementation calls search() for each query.
        Providers can override for optimization.
        """
        results = []
        for i, query in enumerate(queries):
            try:
                query_results = self.search(query, top_k)
                for r in query_results:
                    r.rank = i  # Mark query order
                results.extend(query_results)
            except Exception as e:
                logger.warning(f"Search failed for query '{query}': {e}")
        return results


# ============================================================================
# Doubao Web Search Provider
# ============================================================================

class DoubaoWebSearchProvider(SearchProvider):
    """
    Web search provider using Doubao Responses API with web_search tool.
    
    Uses the Doubao model with tools=[{"type": "web_search"}] to search
    the internet and return structured results with URLs.
    """
    
    def __init__(
        self,
        model: str | None = None,
        timeout: int = 30,
        retry_count: int = 3,
    ):
        # Load model from environment if not provided
        if model is None:
            import os as _os
            model = _os.getenv("MODEL_NAME", "ep-20260514111325-xjmj7")
        self.model = model
        self.timeout = timeout
        self.max_retries = retry_count
        self._client = None
    
    @property
    def provider_name(self) -> str:
        return "doubao_web_search"
    
    def _get_client(self) -> Any:
        """Lazy load the LLM client."""
        if self._client is None:
            from backend.app.services.llm_client import get_llm_client
            self._client = get_llm_client()
        return self._client
    
    def is_available(self) -> bool:
        """Check if Doubao API is configured."""
        import os
        api_key = os.environ.get("MODEL_API_KEY") or os.environ.get("DOUBAO_API_KEY") or os.environ.get("ARK_API_KEY")
        if not api_key:
            logger.warning("Doubao API key not found in environment")
            return False
        return True
    
    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """
        Search using Doubao Chat Completions API with web_search tool.

        Args:
            query: Search query
            top_k: Maximum results (alias: limit)
            limit: Alias for top_k (for compatibility)

        Returns:
            List of SearchResult objects
        """
        if limit is not None:
            top_k = limit

        import os
        import requests
        import json
        import time

        # Rate limit detection
        rate_limit_until = getattr(self, '_rate_limit_until', 0)
        if rate_limit_until and time.time() < rate_limit_until:
            logger.warning(
                "Doubao API rate limited, waiting %ds before retry",
                int(rate_limit_until - time.time())
            )
            time.sleep(min(rate_limit_until - time.time(), 30))

        max_retries = self.max_retries
        retry_delay = 10

        for attempt in range(max_retries):
            try:
                api_key = os.environ.get("MODEL_API_KEY")
                endpoint = os.environ.get("MODEL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3")
                model = os.environ.get("MODEL_NAME", "ep-20260514111325-xjmj7")

                if not api_key:
                    logger.warning("Doubao API key not found")
                    return []

                # Use Chat Completions API (NOT Responses API which ep-* models don't support).
                # We ask the model to return URLs in text as JSON, then parse them.
                # DO NOT use tools/web_search with ep-* models — they recognize the tool but
                # return no structured results, and forcing tool_choice blocks text output.
                chat_url = f"{endpoint}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }

                payload = {
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"Search the web for: {query}. "
                            f"Return the top {top_k} results as a JSON array with objects "
                            f'containing: url, title, and snippet (max 200 chars each). '
                            f'Format: [{{"url":"https://...","title":"...","snippet":"..."}}]. '
                            f"If you cannot find results, return an empty array []. "
                            f"Focus on official websites, documentation, and pricing pages."
                        )
                    }],
                    "max_tokens": 2000,
                    "temperature": 0.1,
                }

                response = requests.post(chat_url, headers=headers, json=payload, timeout=self.timeout)

                if response.status_code == 429:
                    logger.warning(
                        "Doubao API rate limited (attempt %d/%d)",
                        attempt + 1, max_retries
                    )
                    if attempt < max_retries - 1:
                        self._rate_limit_until = time.time() + retry_delay * (attempt + 1)
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        return []

                if response.status_code != 200:
                    logger.error(f"Doubao Chat API returned status {response.status_code}: {response.text[:200]}")
                    return []

                self._rate_limit_until = 0

                data = response.json()
                results = self._parse_chat_response(data, query)
                logger.info(f"Doubao search for '{query}' returned {len(results)} results")
                return results

            except requests.exceptions.Timeout as e:
                logger.warning(f"Doubao web search timeout (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                logger.error(f"Doubao web search failed after {max_retries} attempts: {e}")
                return []

            except Exception as e:
                logger.error(f"Doubao web search failed: {e}")
                return []

        return []

    def _parse_chat_response(self, data: Any, query: str) -> list["SearchResult"]:
        """
        Parse Doubao Chat Completions API response.

        The ep-* thinking model doesn't return structured tool results from web_search.
        Instead, it returns URLs directly in the text content. We parse the message
        content to extract URLs.
        """
        import re
        results = []
        seen_urls = set()

        try:
            choices = data.get('choices', [])
            for choice in choices:
                message = choice.get('message', {})

                # Try structured tool results first
                tool_calls = message.get('tool_calls', [])
                for tc in tool_calls:
                    func = tc.get('function', {})
                    if func.get('name') != 'web_search':
                        continue
                    tool_output = (tc.get('tool_call_output') or tc.get('output') or '')
                    if isinstance(tool_output, str):
                        try:
                            parsed = json.loads(tool_output)
                            if isinstance(parsed, list):
                                for i, item in enumerate(parsed):
                                    if isinstance(item, dict):
                                        url = item.get('url', '')
                                        if url and url not in seen_urls and not any(n in url.lower() for n in ['api_key', 'token', 'secret', 'password']):
                                            seen_urls.add(url)
                                            results.append(SearchResult(
                                                title=item.get('title', f"Result {i+1}")[:100],
                                                url=url,
                                                snippet=item.get('snippet', item.get('description', ''))[:200],
                                                source_type="web",
                                                provider=self.provider_name,
                                                rank=i,
                                            ))
                        except Exception:
                            pass

                # PRIMARY: Extract URLs directly from message content.
                # The ep-* model returns URLs either as structured JSON or plain text.
                content = message.get('content', '')
                if content:
                    # Try parsing as JSON first
                    try:
                        # Find JSON array in content
                        match = re.search(r'\[[\s\S]*\]', content)
                        if match:
                            parsed = json.loads(match.group(0))
                            if isinstance(parsed, list):
                                for i, item in enumerate(parsed):
                                    if isinstance(item, dict):
                                        url = item.get('url', '')
                                        if url and url not in seen_urls and not any(n in url.lower() for n in ['api_key', 'token', 'secret', 'password']):
                                            seen_urls.add(url)
                                            results.append(SearchResult(
                                                title=item.get('title', f"Result {i+1}")[:100],
                                                url=url,
                                                snippet=item.get('snippet', item.get('description', ''))[:200],
                                                source_type="web",
                                                provider=self.provider_name,
                                                rank=i,
                                            ))
                    except Exception:
                        pass

                    # Fallback: extract URLs from raw text
                    if not results:
                        urls = re.findall(r'https?://[^\s<>\[\]()\'\"\n]{10,}', content)
                        for i, raw_url in enumerate(urls):
                            url = raw_url.rstrip('.,;:!?，。；：！？')
                            if url and url not in seen_urls and not any(n in url.lower() for n in ['api_key', 'token', 'secret', 'password']):
                                seen_urls.add(url)
                                pos = content.find(url)
                                ctx_start = max(0, pos - 50)
                                ctx_end = min(len(content), pos + len(url) + 100)
                                snippet = content[ctx_start:ctx_end].strip()
                                results.append(SearchResult(
                                    title=f"Result {len(results)+1}",
                                    url=url,
                                    snippet=snippet[:200],
                                    source_type="web",
                                    provider=self.provider_name,
                                    rank=len(results),
                                ))

            # Fallback: scan entire response for URLs
            if not results:
                text = json.dumps(data)
                urls = re.findall(r'https?://[^\s<>\[\]()\'\"]{10,}', text)
                for i, raw_url in enumerate(urls[:5]):
                    url = raw_url.rstrip('.,;:!?').rstrip('.,;:!?')
                    if url and url not in seen_urls and not any(n in url.lower() for n in ['api_key', 'token', 'secret', 'password']):
                        seen_urls.add(url)
                        results.append(SearchResult(
                            title=f"Result {i+1}",
                            url=url,
                            snippet=f"URL found: {url[:80]}",
                            source_type="web",
                            provider=self.provider_name,
                            rank=i,
                        ))

        except Exception as e:
            logger.warning(f"DoubaoChat: failed to parse response: {e}")

        return results

    def _parse_response(self, response: Any, query: str) -> list[SearchResult]:
        """
        Parse Doubao Responses API output into SearchResult objects.
        
        The response format is a list of output items:
        - reasoning: Model reasoning steps
        - web_search_call: Search tool calls with results
        - message: Final text output with URLs
        """
        results = []
        seen_urls = set()
        
        try:
            # Handle response as dict with output list
            if isinstance(response, dict):
                output_list = response.get('output', [])
            elif isinstance(response, list):
                output_list = response
            else:
                output_list = []
            
            for item in output_list:
                item_type = item.get('type', '')
                
                # web_search_call contains the search results
                if item_type == 'web_search_call':
                    search_results = item.get('results', [])
                    for i, r in enumerate(search_results):
                        url = r.get('url', '')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            results.append(SearchResult(
                                title=r.get('title', f"Result {i+1}"),
                                url=url,
                                snippet=r.get('snippet', r.get('description', ''))[:200],
                                source_type="web",
                                provider=self.provider_name,
                                rank=i,
                            ))
                
                # message type might contain URLs in text and annotations
                elif item_type == 'message':
                    content = item.get('content', [])
                    if isinstance(content, list):
                        for c in content:
                            if c.get('type') == 'output_text':
                                text = c.get('text', '')
                                # Extract URLs from text
                                import re
                                urls = re.findall(r'https?://[^\s<>\[\]()\'"]+', text)
                                for j, url in enumerate(urls):
                                    # Filter out noise URLs
                                    if (url not in seen_urls and 
                                        not any(n in url.lower() for n in ['api_key', 'token', 'secret', 'password'])):
                                        seen_urls.add(url)
                                        # Clean snippet - remove markdown formatting
                                        clean_snippet = re.sub(r'\*+|:+\s*', '', text)[:200]
                                        results.append(SearchResult(
                                            title=f"Web result {len(results)+1}",
                                            url=url,
                                            snippet=clean_snippet,
                                            source_type="web",
                                            provider=self.provider_name,
                                            rank=len(results),
                                        ))
                                
                                # Extract url_citation annotations from output_text
                                annotations = c.get('annotations', [])
                                if isinstance(annotations, list):
                                    for ann in annotations:
                                        if ann.get('type') == 'url_citation':
                                            url = ann.get('url', '')
                                            if url and url not in seen_urls:
                                                seen_urls.add(url)
                                                results.append(SearchResult(
                                                    title=ann.get('title', f"Web result {len(results)+1}")[:100],
                                                    url=url,
                                                    snippet=ann.get('summary', text)[:200],
                                                    source_type="web",
                                                    provider=self.provider_name,
                                                    rank=len(results),
                                                ))
                
                # Check for citations in item
                citations = item.get('citations', [])
                for i, citation in enumerate(citations):
                    if isinstance(citation, dict):
                        url = citation.get('url', '')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            results.append(SearchResult(
                                title=query,
                                url=url,
                                snippet=citation.get('snippet', '')[:200],
                                source_type="web",
                                provider=self.provider_name,
                                rank=i,
                            ))
            
            # If still no results, try to extract from raw response text
            if not results and isinstance(response, dict):
                text = json.dumps(response)
                import re
                urls = re.findall(r'https?://[^\s<>\[\]()\'"]+', text)
                for i, url in enumerate(urls[:5]):
                    if (url not in seen_urls and
                        any(domain in url for domain in ['.com/', '.cn/', '.io/', '.org/', '.net/'])):
                        seen_urls.add(url)
                        results.append(SearchResult(
                            title=f"URL result {i+1}",
                            url=url,
                            snippet=f"Found: {url}",
                            source_type="web",
                            provider=self.provider_name,
                            rank=i,
                        ))
                    
        except Exception as e:
            logger.warning(f"Failed to parse Doubao response: {e}")
        
        return results


# ============================================================================
# Seed URL Provider
# ============================================================================

class SeedUrlProvider(SearchProvider):
    """
    Provider that uses predefined seed URLs from domain schema.
    
    Instead of searching, it returns URLs based on:
    - Domain schema default seed URLs
    - User-provided seed URLs
    - Product official websites
    """
    
    # Default seed URLs by domain (fallback when no seed_urls provided)
    DEFAULT_SEED_URLS: dict[str, list[str]] = {
        "ai_agent_platform": [
            "https://docs.dify.ai",
            "https://dify.ai/pricing",
            "https://python.langchain.com",
            "https://www.coze.com/docs",
        ],
        "coffee_chain": [
            "https://www.luckincoffeecn.com",
            "https://www.starbucks.com.cn",
            "https://www.manorshop.com",
        ],
        "ev_automobile": [
            "https://www.tesla.cn",
            "https://www.byd.com",
            "https://www.xiaomiev.com",
        ],
        "hr_saas": [
            "https://www.beisen.com",
            "https://www.xinrenxinshi.com",
            "https://www.workday.com",
        ],
        "productivity_app": [
            "https://www.notion.so",
            "https://www.notion.so/pricing",
            "https://www.atlassian.com/software/confluence",
            "https://coda.io",
        ],
    }

    # -------------------------------------------------------------------------
    # C2: Per-product multi-type URL map (primary / pricing / docs / github / blog)
    # -------------------------------------------------------------------------
    PRODUCT_SEED_URLS: dict[str, dict[str, str | list[str]]] = {
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
        "langgraph": {
            "primary": "https://langchain-ai.github.io/langgraph/",
            "docs": "https://python.langchain.com/docs/langgraph",
            "github": "https://github.com/langchain-ai/langgraph",
        },
        # Legacy domain-level defaults (kept for backward compatibility)
        "coffee_chain": {
            "primary": "https://www.luckincoffeecn.com",
        },
        "ev_automobile": {
            "primary": "https://www.tesla.cn",
        },
        "hr_saas": {
            "primary": "https://www.beisen.com",
        },
        "productivity_app": {
            "primary": "https://www.notion.so",
        },
    }

    # GitHub org/repo heuristics for products not in PRODUCT_SEED_URLS
    _GITHUB_HEURISTICS: dict[str, str] = {
        "dify.ai": "github.com/langgenius/dify",
        "coze.cn": "github.com/coze-project",
        "coze.com": "github.com/coze-project",
        "flowiseai.com": "github.com/FlowiseAI/Flowise",
        "langchain-ai.github.io": "github.com/langchain-ai/langgraph",
        "notion.so": "github.com/notionhq/notion",
        "tesla.cn": None,   # not open source
        "byd.com": None,
        "xiaomiev.com": None,
        "beisen.com": None,
        "xinrenxinshi.com": None,
        "workday.com": None,
        "luckincoffeecn.com": None,
        "starbucks.com.cn": None,
        "manorshop.com": None,
    }

    def __init__(
        self,
        seed_urls: list[str] | dict[str, list[str]] | None = None,
        domain: str = "general",
    ):
        """
        Args:
            seed_urls: List of URLs or dict mapping products to URLs
            domain: Domain identifier for logging and default URLs
        """
        self._user_urls = seed_urls or []
        self.domain = domain
        self._url_cache: dict[str, SearchResult] = {}

    @property
    def seed_urls(self) -> list[str]:
        """Get effective seed URLs (user provided or defaults)."""
        if self._user_urls:
            if isinstance(self._user_urls, list):
                return self._user_urls
            elif isinstance(self._user_urls, dict):
                # Flat dict → list
                return [url for urls in self._user_urls.values() for url in urls]
        # Fallback to legacy domain defaults
        return self.DEFAULT_SEED_URLS.get(self.domain, [])

    def get_product_seed_urls(self, product_name: str, domain: str) -> list[str]:
        """
        C2: Return comprehensive seed URLs for a specific product.
        Priority: PRODUCT_SEED_URLS match > C1 auto-inference > domain defaults.

        Args:
            product_name: product_name from task_brief (e.g. "Coze", "Dify")
            domain: official_website URL (e.g. "https://www.coze.cn")
        Returns:
            List of URLs covering primary/pricing/docs/github/blog types.
        """
        name_key = product_name.lower()
        if name_key in self.PRODUCT_SEED_URLS:
            mapping = self.PRODUCT_SEED_URLS[name_key]
            urls = []
            for key in ("primary", "pricing", "docs", "github", "blog"):
                val = mapping.get(key)
                if not val:
                    continue
                if isinstance(val, list):
                    urls.extend(val)
                else:
                    urls.append(val)
            if urls:
                return urls

        # C1: auto-infer for unknown products
        inferred = self._infer_additional_urls(domain)
        if inferred:
            return inferred

        # Ultimate fallback: use domain defaults
        return self.DEFAULT_SEED_URLS.get(self.domain, [])

    def _infer_additional_urls(self, domain: str) -> list[str]:
        """
        C1: Given an official website URL, construct plausible sub-page URLs
        and return the ones that look viable (no HTTP probe — too slow at scale).
        """
        from urllib.parse import urlparse
        parsed = urlparse(domain if domain.startswith("http") else f"https://{domain}")
        base = f"{parsed.scheme}://{parsed.netloc}"

        candidates = [
            f"{base}/pricing",
            f"{base}/zh/pricing",
            f"{base}/docs",
            f"{base}/docs/",
            f"{base}/zh/docs",
            f"{base}/zh",
            f"{base}/blog",
            f"{base}/zh/blog",
            f"{base}/open-source",
            f"{base}/changelog",
        ]

        # Deduplicate while preserving order
        seen, urls = set(), []
        for url in candidates:
            if url not in seen:
                seen.add(url)
                urls.append(url)

        # Append GitHub if we can guess it
        github = self._guess_github_url(domain)
        if github and github not in seen:
            urls.append(github)

        return urls

    def _guess_github_url(self, domain: str) -> str | None:
        """Try to construct a plausible GitHub URL from a product domain."""
        from urllib.parse import urlparse
        parsed = urlparse(domain if domain.startswith("http") else f"https://{domain}")
        host = parsed.netloc.removeprefix("www.").removeprefix("docs.")
        gh_path = self._GITHUB_HEURISTICS.get(host)
        if gh_path:
            return f"https://{gh_path}"
        return None

    @property
    def provider_name(self) -> str:
        return "seed_urls"

    def is_available(self) -> bool:
        """Seed URLs are always available if configured."""
        return bool(self.seed_urls)

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """
        Return seed URLs that match the query context.

        This is not a real search - it returns predefined URLs
        that are relevant to the domain/products.
        """
        results = []

        # Get effective URLs (user provided or defaults)
        all_urls = self.seed_urls

        # Create SearchResult for each URL
        for i, url in enumerate(all_urls[:top_k]):
            if url not in self._url_cache:
                self._url_cache[url] = SearchResult(
                    title=self._extract_title(url),
                    url=url,
                    snippet=f"Seed URL for {self.domain}: {url}",
                    source_type="seed",
                    provider=self.provider_name,
                    rank=i,
                )
            results.append(self._url_cache[url])

        logger.info(f"SeedUrlProvider returned {len(results)} URLs for domain={self.domain}")
        return results
    
    def _extract_title(self, url: str) -> str:
        """Extract a title from URL."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        
        # Use last path segment as title
        if "/" in path:
            title = path.split("/")[-1].replace("-", " ").replace("_", " ")
        else:
            title = parsed.netloc.replace("www.", "").replace(".com", "").replace(".cn", "")
        
        return title or "Unknown"
    
    def add_seed_url(self, url: str, title: str = "") -> None:
        """Add a new seed URL."""
        if url not in self.seed_urls:
            self.seed_urls.append(url)
        if title:
            self._url_cache[url] = SearchResult(
                title=title,
                url=url,
                snippet=f"Seed URL: {url}",
                source_type="seed",
                provider=self.provider_name,
            )


# ============================================================================
# Fixture Provider (Mock Data)
# ============================================================================

class FixtureProvider(SearchProvider):
    """
    Mock data provider for testing the pipeline without real data.
    
    Returns predefined search results that simulate what would be
    collected from real sources. Used for:
    - Testing the generalization pipeline
    - Demo without network access
    - Ensuring deterministic test results
    """
    
    # Predefined fixtures by domain
    FIXTURES: dict[str, dict[str, list[dict]]] = {
        "ai_agent_platform": {
            "queries": [
                {
                    "query_pattern": "*",
                    "results": [
                        {"title": "Dify Official Documentation", "url": "https://docs.dify.ai", "snippet": "Dify is an open-source LLM app development platform"},
                        {"title": "Dify Pricing", "url": "https://dify.ai/pricing", "snippet": "Dify offers cloud and self-hosted options"},
                        {"title": "LangChain Documentation", "url": "https://python.langchain.com", "snippet": "LangChain is a framework for developing applications powered by LLMs"},
                        {"title": "LangGraph Documentation", "url": "https://langchain-ai.github.io/langgraph/", "snippet": "Build stateful multi-actor applications with LangGraph"},
                        {"title": "Coze Documentation", "url": "https://www.coze.com/docs", "snippet": "Coze is a bot development platform by ByteDance"},
                    ]
                }
            ]
        },
        "coffee_chain": {
            "queries": [
                {
                    "query_pattern": "*",
                    "results": [
                        {"title": "Luckin Coffee Official", "url": "https://www.luckincoffeecn.com", "snippet": "Luckin Coffee - China's leading coffee chain"},
                        {"title": "Starbucks China", "url": "https://www.starbucks.com.cn", "snippet": "Starbucks China - Premium coffee and tea experience"},
                        {"title": "Manner Coffee", "url": "https://www.manorshop.com", "snippet": "Manner - Specialty coffee with affordable prices"},
                        {"title": "瑞幸咖啡财报", "url": "https://investor.luckincoffee.com", "snippet": "Luckin Coffee financial reports and investor information"},
                        {"title": "星巴克中国财报", "url": "https://stories.starbucks.com", "snippet": "Starbucks global and China market reports"},
                    ]
                }
            ]
        },
        "ev_automobile": {
            "queries": [
                {
                    "query_pattern": "*",
                    "results": [
                        {"title": "Tesla China", "url": "https://www.tesla.cn", "snippet": "Tesla China - Electric vehicles with Autopilot"},
                        {"title": "BYD Official", "url": "https://www.byd.com", "snippet": "BYD - Build Your Dreams, leading EV manufacturer"},
                        {"title": "小米汽车 SU7", "url": "https://www.xiaomiev.com", "snippet": "Xiaomi SU7 - Smart electric vehicle"},
                        {"title": "懂车帝", "url": "https://www.dongchedi.com", "snippet": "懂车帝 - 汽车消费决策平台"},
                        {"title": "汽车之家", "url": "https://www.autohome.com.cn", "snippet": "汽车之家 - 专业汽车网站"},
                    ]
                }
            ]
        },
        "hr_saas": {
            "queries": [
                {
                    "query_pattern": "*",
                    "results": [
                        {"title": "Beisen Official", "url": "https://www.beisen.com", "snippet": "北森 - 一体化HR SaaS平台"},
                        {"title": "Xinrenxinshi", "url": "https://www.xinrenxinshi.com", "snippet": "薪人薪事 - 智能HR系统"},
                        {"title": "Moka HR", "url": "https://www.mokahr.com", "snippet": "Moka - 智能化招聘管理平台"},
                        {"title": "Workday", "url": "https://www.workday.com", "snippet": "Workday - Enterprise HCM software"},
                        {"title": "SAP SuccessFactors", "url": "https://www.sap.com/products/hcm.html", "snippet": "SAP SuccessFactors - Cloud HCM solutions"},
                    ]
                }
            ]
        },
        "productivity_app": {
            "queries": [
                {
                    "query_pattern": "*",
                    "results": [
                        {"title": "Notion Official", "url": "https://www.notion.so", "snippet": "Notion - All-in-one workspace for notes, docs, and wikis"},
                        {"title": "Notion Pricing", "url": "https://www.notion.so/pricing", "snippet": "Notion pricing plans: Free, Plus, Business, Enterprise"},
                        {"title": "Confluence", "url": "https://www.atlassian.com/software/confluence", "snippet": "Confluence - Team workspace for knowledge management"},
                        {"title": "Coda", "url": "https://coda.io", "snippet": "Coda - Documents and tables, perfected"},
                        {"title": "Slite", "url": "https://slite.com", "snippet": "Slite - Simple tool for async team knowledge"},
                    ]
                }
            ]
        },
    }
    
    def __init__(
        self,
        domain: str = "general",
        custom_fixtures: dict[str, list[dict]] | None = None,
    ):
        """
        Args:
            domain: Domain identifier to use fixtures for
            custom_fixtures: Override fixtures (for testing)
        """
        self.domain = domain
        self.custom_fixtures = custom_fixtures
        self._call_count = 0
    
    @property
    def provider_name(self) -> str:
        return "fixture"
    
    def is_available(self) -> bool:
        """Fixtures are always available."""
        return True
    
    @property
    def is_configured(self) -> bool:
        """
        Fixture is always "not configured" (it's a fallback/mock mode).
        Set FORCE_FIXTURE=1 to enable fixture as primary mode.
        """
        return os.environ.get("FORCE_FIXTURE", "") == "1"
    
    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """
        Return mock search results for the given query.
        
        Results are deterministic based on domain and query.
        """
        # Support both top_k and limit parameter names
        if limit is not None:
            top_k = limit
            
        self._call_count += 1
        
        # Get fixtures for domain
        fixtures = self.custom_fixtures or self.FIXTURES.get(self.domain, {})
        
        if not fixtures:
            # Generic fixtures
            return self._get_generic_fixtures(query, top_k)
        
        # Extract query keywords
        query_lower = query.lower()
        results = []
        
        # Get all results from fixtures
        all_results = fixtures.get("queries", [{}])[0].get("results", [])
        
        # Filter and return relevant results
        for i, result in enumerate(all_results[:top_k]):
            # Add some variation based on query
            title = result.get("title", "")
            snippet = result.get("snippet", "")
            
            # Check if result is relevant to query
            if any(kw in (title + snippet).lower() for kw in query_lower.split()[:3]):
                relevance_score = 1.0
            else:
                relevance_score = 0.5
            
            results.append(SearchResult(
                title=title,
                url=result.get("url", ""),
                snippet=snippet,
                source_type="fixture",
                provider=self.provider_name,
                rank=i,
                metadata={"relevance": relevance_score, "call_count": self._call_count},
            ))
        
        logger.info(f"FixtureProvider returned {len(results)} mock results for domain={self.domain}, query='{query}'")
        return results
    
    def _get_generic_fixtures(self, query: str, top_k: int) -> list[SearchResult]:
        """Return generic fixtures when domain has no specific fixtures."""
        return [
            SearchResult(
                title=f"Fixture Result {i+1} for: {query[:30]}",
                url=f"https://fixture.example.com/result-{i+1}",
                snippet=f"This is mock data simulating search results for: {query}",
                source_type="fixture",
                provider=self.provider_name,
                rank=i,
            )
            for i in range(min(top_k, 3))
        ]


# ============================================================================
# DuckDuckGo HTML Fallback Provider (no API key required)
# ============================================================================

class DuckDuckGoProvider(SearchProvider):
    """
    Fallback search provider using DuckDuckGo's HTML interface.

    This provider is used when the primary Doubao search fails (timeout,
    rate-limit, or network issue). It scrapes DuckDuckGo HTML results
    using the 0-click (zero-click) JSON API endpoint which is free
    and requires no API key.

    Endpoint: https://api.duckduckgo.com/?q=<query>&format=json&no_html=1

    Note: This is a best-effort fallback. For production use with high
    volume, consider paid providers (SerpAPI, Tavily, Brave Search).
    """

    DUCKDUCKGO_API = "https://api.duckduckgo.com/"

    def __init__(self, timeout: int = 15, top_k: int = 5):
        self.timeout = timeout
        self.top_k = top_k

    @property
    def provider_name(self) -> str:
        return "duckduckgo"

    def is_available(self) -> bool:
        """Always available - no API key needed."""
        return True

    @property
    def is_configured(self) -> bool:
        """Always considered configured since no credentials needed."""
        return True

    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """Search via DuckDuckGo zero-click JSON API."""
        if limit is not None:
            top_k = limit

        # P0: Short-circuit when the network probe has marked this host degraded.
        host = "api.duckduckgo.com"
        if is_degraded(host):
            logger.warning(
                "DuckDuckGo search: skipping '%s' (host degraded)", query[:40]
            )
            return []

        results = []
        seen_urls: set[str] = set()

        try:
            import requests as _requests
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
                "t": "productinsight_agent",
            }
            resp = _requests.get(
                self.DUCKDUCKGO_API,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ProductInsightBot/1.0)"},
            )
            if resp.status_code != 200:
                logger.warning(f"DuckDuckGo returned {resp.status_code} for query: {query}")
                _HEALTH_PROBE.record(host, success=False)
                return []
            _HEALTH_PROBE.record(host, success=True)

            data = resp.json()

            # Extract from RelatedTopics (main web results)
            for item in data.get("RelatedTopics", []):
                if len(results) >= top_k:
                    break

                if not isinstance(item, dict):
                    continue

                url = item.get("FirstURL", "")
                if not url or url in seen_urls:
                    continue
                if not url.startswith("http"):
                    continue

                seen_urls.add(url)
                text = item.get("Text", "")
                snippet = text[:200] if text else ""

                results.append(SearchResult(
                    title=item.get("Text", "Result")[:100],
                    url=url,
                    snippet=snippet,
                    source_type="web",
                    provider=self.provider_name,
                    rank=len(results),
                ))

            # Also extract from Results (top results)
            for item in data.get("Results", []):
                if len(results) >= top_k:
                    break

                if not isinstance(item, dict):
                    continue

                url = item.get("URL", "")
                if not url or url in seen_urls:
                    continue

                seen_urls.add(url)
                text = item.get("Text", "")
                snippet = text[:200] if text else ""

                results.append(SearchResult(
                    title=text[:100] if text else "Result",
                    url=url,
                    snippet=snippet,
                    source_type="web",
                    provider=self.provider_name,
                    rank=len(results),
                ))

        except Exception as exc:
            logger.warning(f"DuckDuckGo search failed for '{query}': {exc}")
            return []

        return results[:top_k]


# ============================================================================
# LLM Inference URL Provider (no external network required)
# ============================================================================

class LLMInferenceProvider(SearchProvider):
    """
    LLM-based URL inference provider.

    Asks the LLM directly to return authoritative URLs for a product,
    bypassing the need for external network access. This is the last-resort
    fallback when both Doubao search and DuckDuckGo fail.

    Since this uses the LLM (not external search APIs), it only works when
    the LLM API endpoint is accessible (which is always for this codebase).
    """

    # Class-level cache: same (query, top_k) -> same answer
    # Prevents re-asking the LLM for queries we've already answered
    # when multiple nodes (collect_sources + execute_rework) issue them.
    _QUERY_CACHE: dict[tuple[str, int], list[SearchResult]] = {}
    _CACHE_LOCK = threading.Lock()
    _CACHE_MAX = 256

    def __init__(self, timeout: int = 45):
        # P1 (2026-06-22): Doubao Thinking model (ep-*) adds 5-25s of internal
        # reasoning tokens before producing output. Default increased from 30s to 45s.
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return "llm_inference"

    def is_available(self) -> bool:
        """Available if LLM API is accessible."""
        try:
            from backend.app.services.llm_client import get_llm_client
            return True
        except Exception:
            return False

    @property
    def is_configured(self) -> bool:
        return True

    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """
        Ask LLM to return authoritative URLs matching the query.

        The LLM's training knowledge is used to infer the most likely
        authoritative URLs (official sites, documentation, pricing pages).
        """
        if limit is not None:
            top_k = limit

        cache_key = (query.strip().lower(), top_k)
        with self._CACHE_LOCK:
            if cache_key in self._QUERY_CACHE:
                logger.debug("LLMInferenceProvider: cache hit for '%s'", query[:40])
                return list(self._QUERY_CACHE[cache_key])

        try:
            from backend.app.services.llm_client import get_llm_client
            client = get_llm_client()

            # Clean up query - extract product names
            clean_query = query.strip()
            # Build a targeted prompt asking for URLs
            prompt = (
                "You are a web research assistant. For the search query below, "
                "return the top authoritative URLs that match. "
                "Focus on: official websites, documentation, pricing pages, and official blogs. "
                "Return ONLY a JSON array with objects: [{\"url\": \"https://...\", \"title\": \"...\", \"snippet\": \"...\"}]. "
                "Return 3-5 results. If you don't know, return an empty array []. "
                f"Query: {clean_query}"
            )

            response = client.chat_text(
                messages=[
                    {"role": "system", "content": "You are a web research assistant. Return ONLY JSON arrays."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=500,
                timeout=self.timeout,
            )

            # Parse JSON response
            import re, json as _json
            # Find JSON array in response
            match = re.search(r'\[[\s\S]*\]', response)
            if not match:
                logger.warning(f"LLMInferenceProvider: no JSON found in response for '{query}'")
                return []

            data = _json.loads(match.group(0))
            if not isinstance(data, list):
                return []

            results = []
            for item in data[:top_k]:
                if not isinstance(item, dict):
                    continue
                url = item.get("url", "")
                if not url or not url.startswith("http"):
                    continue
                if any(n in url.lower() for n in ["api_key", "token", "secret", "password"]):
                    continue

                results.append(SearchResult(
                    title=item.get("title", clean_query)[:100],
                    url=url,
                    snippet=str(item.get("snippet", ""))[:200],
                    source_type="llm_inference",
                    provider=self.provider_name,
                    rank=len(results),
                    # P0: Mark all LLM-inferred results as unverified.
                    # Downstream evidence evaluators can use this to
                    # cap the trust tier and the report writer can
                    # surface a "based on LLM knowledge" caveat.
                    metadata={"_unverified_external": True,
                              "_origin": "llm_knowledge"},
                ))

            # Write to cache for subsequent calls (collect_sources + execute_rework
            # may issue the same query; LLM call is expensive at ~4-6s each).
            with self._CACHE_LOCK:
                if len(self._QUERY_CACHE) >= self._CACHE_MAX:
                    # Drop the oldest half (FIFO). Insertion order is
                    # preserved in Python 3.7+ dicts.
                    keep = self._CACHE_MAX // 2
                    for k in list(self._QUERY_CACHE.keys())[:-keep]:
                        self._QUERY_CACHE.pop(k, None)
                self._QUERY_CACHE[cache_key] = list(results)
            return results

        except Exception as exc:
            logger.warning(f"LLMInferenceProvider search failed for '{query}': {exc}")
            return []


# ============================================================================
# Hybrid Provider (Combination)
# ============================================================================

class HybridSearchProvider(SearchProvider):
    """
    Combines multiple providers for more robust results.
    
    Strategy:
    1. Try primary provider (e.g., Doubao web search)
    2. Fallback to seed URLs if primary fails
    3. Add fixture data if needed for coverage
    """
    
    def __init__(
        self,
        providers: list[SearchProvider],
        fallback_order: list[str] | None = None,
    ):
        """
        Args:
            providers: List of providers to use
            fallback_order: Priority order of provider names
        """
        self.providers = {p.provider_name: p for p in providers}
        self.fallback_order = fallback_order or list(self.providers.keys())
    
    @property
    def provider_name(self) -> str:
        return "hybrid"
    
    def is_available(self) -> bool:
        """Available if at least one provider is available."""
        return any(p.is_available() for p in self.providers.values())
    
    def search(self, query: str, top_k: int = 5, limit: int | None = None) -> list[SearchResult]:
        """
        Search using providers in fallback order.

        P0: Adds a per-provider wall-clock budget. If a provider takes
        longer than PROVIDER_BUDGET_S, the call is abandoned (the
        underlying request may still complete; we just stop waiting)
        and we move to the next provider. This prevents a single slow
        provider (e.g. an overseas endpoint that needs 90s of retries
        to time out) from blocking the entire chain.
        """
        # Support both top_k and limit parameter names
        if limit is not None:
            top_k = limit

        # P1 (2026-06-22): Raised from 25s to 45s. The Doubao Thinking model
        # (ep-*) uses internal reasoning tokens that add 5-25s before output.
        # A healthy Doubao web_search call with reasoning takes 8-30s; a healthy
        # LLMInference call takes 15-40s. Setting 45s allows both to complete
        # without being killed mid-reasoning.
        PROVIDER_BUDGET_S = 45.0

        seen_urls = set()
        all_results = []
        providers_tried: list[tuple[str, float, int]] = []  # (name, elapsed, count)

        for provider_name in self.fallback_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue

            t0 = time.time()
            try:
                # Run provider.search under a thread so we can apply a
                # wall-clock budget without trusting the provider to honor
                # its own timeout.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(provider.search, query, top_k)
                    try:
                        results = fut.result(timeout=PROVIDER_BUDGET_S)
                    except concurrent.futures.TimeoutError:
                        logger.warning(
                            "HybridSearchProvider: %s exceeded %.1fs budget for '%s', skipping",
                            provider_name, PROVIDER_BUDGET_S, query[:40],
                        )
                        results = []

                elapsed = time.time() - t0
                providers_tried.append((provider_name, elapsed, len(results)))

                # Deduplicate by URL
                for result in results:
                    if result.url not in seen_urls:
                        seen_urls.add(result.url)
                        all_results.append(result)

                # Stop if we have enough results
                if len(all_results) >= top_k:
                    break

            except Exception as e:
                logger.warning(f"Provider {provider_name} failed: {e}")
                providers_tried.append((provider_name, time.time() - t0, 0))

        # P0: Emit a single audit line per hybrid search so we can see
        # which provider actually delivered results in the log.
        if providers_tried:
            summary = ", ".join(
                f"{n}={elapsed:.1f}s/{c}" for n, elapsed, c in providers_tried
            )
            logger.info("HybridSearch '%s' → %d results via [%s]", query[:40], len(all_results), summary)

        return all_results[:top_k]


# ============================================================================
# Factory Function
# ============================================================================

def create_search_provider(
    mode: str = "doubao_web_search",
    domain: str = "general",
    seed_urls: list[str] | dict[str, list[str]] | None = None,
    config: CollectionConfig | None = None,
) -> SearchProvider:
    """
    Factory function to create the appropriate search provider.
    
    Args:
        mode: Collection mode (doubao_web_search, seed_urls, fixture, hybrid)
        domain: Domain identifier for domain-specific providers
        seed_urls: URLs for SeedUrlProvider
        config: Optional collection configuration
        
    Returns:
        Configured SearchProvider instance
    """
    config = config or CollectionConfig(mode=mode)
    
    if mode == "doubao_web_search":
        return DoubaoWebSearchProvider(timeout=30, retry_count=3)

    elif mode == "duckduckgo":
        return DuckDuckGoProvider(timeout=15, top_k=5)

    elif mode == "seed_urls":
        return SeedUrlProvider(seed_urls=seed_urls, domain=domain)

    elif mode == "fixture":
        return FixtureProvider(domain=domain)

    elif mode == "llm_knowledge":
        # P0: LLM-knowledge-only mode for fully degraded networks.
        # The LLM generates both authoritative URLs AND fact snippets
        # from its training data. All results are tagged with
        # ``_unverified_external: true`` so downstream nodes (the
        # evidence evaluator, the report writer) know to surface a
        # "based on LLM knowledge, not externally verified" caveat.
        providers = [LLMInferenceProvider(timeout=30)]
        return HybridSearchProvider(
            providers=providers,
            fallback_order=["llm_inference"],
        )

    elif mode == "hybrid":
        # Smart hybrid: Doubao → LLM Inference → DuckDuckGo → Seed URLs → Fixture
        # P0: LLM Inference is moved earlier in the chain. It uses the
        # LLM's training knowledge (no external network needed beyond
        # the volces chat endpoint, which is domestic) and is the
        # fastest *reliable* fallback. In the search_engines test we
        # saw all overseas endpoints (Doubao web_search, DuckDuckGo,
        # OpenAI, Perplexity) time out — only the LLM endpoint and
        # direct-fetchable docs (github.com, *.cn) worked.
        providers = []

        # Primary: Doubao web search (best when network is healthy)
        doubao = DoubaoWebSearchProvider(timeout=15, retry_count=2)
        if doubao.is_available():
            providers.append(doubao)

        # Second: LLM knowledge inference (uses domestic volces chat).
        # timeout=30s — the web_search LLM call is heavier (reasoning +
        # 500 max_tokens); we measured 4-6s when healthy.
        providers.append(LLMInferenceProvider(timeout=30))

        # Third: DuckDuckGo (free, but overseas → often times out)
        providers.append(DuckDuckGoProvider(timeout=10, top_k=5))

        # Fourth: seed URLs
        if seed_urls:
            providers.append(SeedUrlProvider(seed_urls=seed_urls, domain=domain))

        # Final: fixture
        providers.append(FixtureProvider(domain=domain))

        return HybridSearchProvider(
            providers=providers,
            fallback_order=["doubao_web_search", "llm_inference", "duckduckgo",
                           "seed_url", "fixture"],
        )
    
    else:
        logger.warning(f"Unknown collection mode '{mode}', using fixture")
        return FixtureProvider(domain=domain)


# ============================================================================
# Query Generator (for Domain Schema)
# ============================================================================

def generate_search_queries(
    domain: str,
    products: list[str],
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """
    Generate search queries based on domain and products.
    
    This is used by the Domain Schema Planner to generate
    queries for the search/collection phase.
    
    Args:
        domain: Domain identifier
        products: List of products to analyze
        schema: Optional domain schema with dimensions
        
    Returns:
        List of search query strings
    """
    queries = []
    
    # Product-focused queries
    for product in products:
        queries.append(f"{product} 官方")
        queries.append(f"{product} 官网 定价")
        queries.append(f"{product} 产品 功能")
    
    # Domain-specific dimension queries
    if schema and "comparison_dimensions" in schema:
        for dim in schema["comparison_dimensions"]:
            dim_name = dim.get("chinese", dim.get("dimension", ""))
            
            # Add dimension-specific queries
            for product in products[:2]:  # Limit to first 2 products per dimension
                queries.append(f"{product} {dim_name}")
    
    # Remove duplicates while preserving order
    seen = set()
    unique_queries = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique_queries.append(q)
    
    return unique_queries


# ============================================================================
# Default Seed URLs by Domain
# ============================================================================

DEFAULT_SEED_URLS: dict[str, list[str]] = {
    "ai_agent_platform": [
        "https://docs.dify.ai",
        "https://dify.ai/pricing",
        "https://python.langchain.com",
        "https://www.coze.com/docs",
        "https://docs.flowiseai.com",
    ],
    "coffee_chain": [
        "https://www.luckincoffeecn.com",
        "https://www.starbucks.com.cn",
        "https://www.manorshop.com",
    ],
    "ev_automobile": [
        "https://www.tesla.cn",
        "https://www.byd.com",
        "https://www.xiaomiev.com",
    ],
    "hr_saas": [
        "https://www.beisen.com",
        "https://www.xinrenxinshi.com",
        "https://www.workday.com",
    ],
    "productivity_app": [
        "https://www.notion.so",
        "https://www.notion.so/pricing",
        "https://www.atlassian.com/software/confluence",
        "https://coda.io",
        "https://slite.com",
    ],
}


# ============================================================================
# Search Provider Configuration & Factory
# ============================================================================

# Status constants for search results
SEARCH_SUCCESS = "success"
SEARCH_FAILED = "failed"
SEARCH_NO_RESULTS = "no_results"
SEARCH_PROVIDER_NOT_CONFIGURED = "provider_not_configured"


class SearchProviderConfig:
    """
    Configuration for the search provider system.
    
    Determines which provider to use based on environment and settings.
    """
    
    def __init__(self):
        self.mode = self._detect_mode()
        self.domain = os.environ.get("SEARCH_DOMAIN", "general")
        self._provider_instance: SearchProvider | None = None
    
    def _detect_mode(self) -> str:
        """Detect which collection mode to use based on configuration."""
        # Check explicit mode setting
        explicit_mode = os.environ.get("COLLECTION_MODE", "").lower()
        if explicit_mode in ["doubao_web_search", "duckduckgo", "seed_urls", "fixture", "hybrid", "public_web"]:
            return explicit_mode

        # FORCE_FIXTURE=1 forces fixture mode (for testing)
        if os.environ.get("FORCE_FIXTURE", "") == "1":
            return "fixture"

        # Default to hybrid: Doubao → DuckDuckGo → Seed URLs → Fixture
        # This provides the best resilience - Doubao is primary if working,
        # DuckDuckGo auto-fallback if Doubao times out/rate-limited.
        return "hybrid"
    
    @property
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        # Always true if we have an API key (doubao mode is configured)
        if self.mode == "doubao_web_search":
            return bool(os.environ.get("MODEL_API_KEY"))
        # DuckDuckGo needs no credentials
        if self.mode == "duckduckgo":
            return True
        # Seed URLs mode is always available
        if self.mode == "seed_urls":
            return True
        # Hybrid mode is always available (has fallbacks)
        if self.mode == "hybrid":
            return True
        # Fixture mode - configurable
        if self.mode == "fixture":
            return os.environ.get("FORCE_FIXTURE", "") == ""
        return True
    
    def get_domain(self) -> str:
        """Get the domain from environment or default."""
        return self.domain


# Global config instance
_config: SearchProviderConfig | None = None


def get_search_config() -> SearchProviderConfig:
    """Get the global search provider configuration."""
    global _config
    if _config is None:
        _config = SearchProviderConfig()
    return _config


def get_search_provider() -> SearchProvider:
    """
    Get or create the configured search provider.
    
    This is the main entry point for the workflow to get a search provider.
    
    Returns:
        Configured SearchProvider instance
    """
    config = get_search_config()
    
    if config._provider_instance is not None:
        return config._provider_instance
    
    # Create provider based on mode
    provider = create_search_provider(
        mode=config.mode,
        domain=config.domain,
    )
    
    config._provider_instance = provider
    return provider


def reset_search_provider() -> None:
    """Reset the global provider instance (for testing)."""
    global _config
    _config = None
