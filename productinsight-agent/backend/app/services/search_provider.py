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
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

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
        Search using Doubao Responses API with web_search tool.
        
        Args:
            query: Search query
            top_k: Maximum results (alias: limit)
            limit: Alias for top_k (for compatibility)
            
        Returns:
            List of SearchResult objects
        """
        # Support both top_k and limit parameter names
        if limit is not None:
            top_k = limit

        import os
        import requests
        import json
        import time

        # P2 FIX: Add rate limit detection and backoff
        # Check for persistent rate limit state
        rate_limit_until = getattr(self, '_rate_limit_until', 0)
        if rate_limit_until and time.time() < rate_limit_until:
            logger.warning(
                "Doubao API rate limited, waiting %ds before retry",
                int(rate_limit_until - time.time())
            )
            time.sleep(min(rate_limit_until - time.time(), 30))  # Cap at 30s

        max_retries = self.max_retries
        retry_delay = 10  # seconds

        for attempt in range(max_retries):
            try:
                # Get credentials from environment
                api_key = os.environ.get("MODEL_API_KEY")
                endpoint = os.environ.get("MODEL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3")

                if not api_key:
                    logger.warning("Doubao API key not found")
                    return []

                url = f"{endpoint}/responses"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }

                payload = {
                    "model": self.model,
                    "input": [{"role": "user", "content": f"Search the web for: {query}. Return the top {top_k} results with their URLs and brief descriptions. Focus on official websites and documentation."}],
                    "tools": [{"type": "web_search"}],
                }

                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout)

                # P2 FIX: Detect rate limit (429) and set backoff
                if response.status_code == 429:
                    error_text = response.text[:500]
                    logger.warning(
                        "Doubao API rate limited (attempt %d/%d): %s",
                        attempt + 1, max_retries, error_text
                    )
                    if attempt < max_retries - 1:
                        # Set persistent rate limit state
                        self._rate_limit_until = time.time() + retry_delay * (attempt + 1)
                        logger.info(
                            "Doubao API: backing off for %ds (attempt %d/%d)",
                            retry_delay * (attempt + 1), attempt + 1, max_retries
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    else:
                        logger.error(
                            "Doubao API rate limited after %d attempts, giving up",
                            max_retries
                        )
                        return []

                if response.status_code != 200:
                    logger.error(f"Doubao API returned status {response.status_code}: {response.text[:200]}")
                    return []

                # Clear rate limit state on success
                self._rate_limit_until = 0

                data = response.json()
                results = self._parse_response(data, query)
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
                return []

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

    def __init__(self, timeout: int = 20):
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
                ))

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
        """
        # Support both top_k and limit parameter names
        if limit is not None:
            top_k = limit
            
        seen_urls = set()
        all_results = []
        
        for provider_name in self.fallback_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.is_available():
                continue
            
            try:
                results = provider.search(query, top_k)
                
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

    elif mode == "hybrid":
        # Smart hybrid: Doubao → DuckDuckGo → LLM Inference → Seed URLs → Fixture
        providers = []

        # Try to add Doubao web search (primary)
        doubao = DoubaoWebSearchProvider(timeout=30, retry_count=3)
        if doubao.is_available():
            providers.append(doubao)

        # Add DuckDuckGo as second fallback (free, no API key)
        providers.append(DuckDuckGoProvider(timeout=15, top_k=5))

        # Add LLM inference as third fallback (uses LLM knowledge, no external search needed)
        providers.append(LLMInferenceProvider(timeout=20))

        # Add seed URL provider
        if seed_urls:
            providers.append(SeedUrlProvider(seed_urls=seed_urls, domain=domain))

        # Always add fixture as final fallback
        providers.append(FixtureProvider(domain=domain))

        return HybridSearchProvider(
            providers=providers,
            fallback_order=["doubao_web_search", "duckduckgo", "llm_inference",
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
