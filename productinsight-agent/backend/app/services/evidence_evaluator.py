"""
Evidence Evaluator - Rule-based evidence quality scoring.

Computes quality scores for each evidence_item based on:
- relevance: match with product name, schema keywords, analysis dimensions
- authority: source type and trust tier
- freshness: recency of evidence
- schema_fit: alignment with AI Agent product schema keywords
- information_density: content richness
- final_score: weighted average
- usable_for_claim: threshold-based decision
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Schema keywords for AI Agent product analysis
SCHEMA_KEYWORDS = {
    # English
    "workflow", "orchestration", "orchestrat",
    "agent", "tool", "plugin", "extension",
    "knowledge", "document", "retrieval", "vector", "rag", "embedding",
    "deployment", "self-hosted", "docker", "kubernetes", "k8s", "cloud",
    "pricing", "plan", "tier", "free", "enterprise",
    "rbac", "sso", "saml", "ldap", "audit", "audit_log", "compliance",
    "api", "webhook", "endpoint", "rest",
    "integration", "connect", "native",
    "model", "llm", "gpt", "claude", "openai", "anthropic", "provider",
    "prompt", "template", "dataset",
    "analytics", "monitoring", "logging", "metrics",
    "security", "encryption", "tls", "ssl",
    "multi-tenant", "tenant", "isolation",
    "scaling", "scalable", "performance", "latency",
    "ui", "interface", "dashboard", "gui", "visual",
    # Evidence-Sufficiency Sprint: Chinese keywords added for Chinese evidence quality scoring.
    # These mirror the English keywords above so Chinese docs (Dify/Coze/FastGPT) get meaningful schema_fit scores.
    "工作流", "编排", "节点", "画布", "拖拽",
    "智能体", "机器人", "助手", "自动化", "插件", "扩展",
    "知识库", "文档", "检索", "向量", "嵌入", "rag",
    "部署", "私有化", "本地", "开源", "docker", "企业",
    "单点登录", "权限", "审计", "加密", "合规",
    "接口", "集成", "连接器", "webhook",
    "模型", "大模型", "llm", "gpt",
    "功能", "特性", "支持", "提供", "包含",
    "智能", "自动化", "编排",
    "产品", "平台", "应用",
}

# Analysis dimension keywords
DIMENSION_KEYWORDS = {
    "function_tree": {"feature", "function", "capability", "support", "offer", "provide", "include", "feature",
                       "功能", "特性", "支持", "提供", "包含"},
    "pricing_model": {"price", "pricing", "cost", "fee", "tier", "plan", "subscription", "license", "free", "paid",
                       "价格", "定价", "费用", "套餐", "订阅", "免费", "付费"},
    "user_persona": {"user", "customer", "developer", "enterprise", "team", "persona", "use case", "scenario",
                      "用户", "客户", "开发者", "企业", "团队", "使用场景"},
    "customer_voice": {"review", "feedback", "testimonial", "rating", "opinion", "user said", "customer",
                        "用户评价", "口碑", "反馈", "评分"},
    "swot": {"strength", "weakness", "opportunity", "threat", "advantage", "disadvantage",
              "优势", "劣势", "机会", "威胁", "优点", "缺点"},
    "enterprise_readiness": {"enterprise", "security", "rbac", "sso", "audit", "compliance", "sla", "support",
                              "企业", "安全", "权限", "单点登录", "审计", "合规", "支持"},
}

# High-authority source types and domains
HIGH_AUTHORITY_SOURCE_TYPES = {
    "official_site", "documentation", "docs", "pricing_page", "github",
    "technical_blog", "api_reference", "whitepaper",
}

LOW_AUTHORITY_SOURCE_TYPES = {
    "social", "forum", "discussion", "unknown", "low_trust",
}

HIGH_AUTHORITY_DOMAINS = {
    "github.com", "readme.io", "readthedocs.io", "docs.google.com",
    "cloud.google.com", "aws.amazon.com", "azure.microsoft.com",
    "documentation", "readthedocs", "notion.so", "confluence",
    # Evidence-Sufficiency Sprint: AI Agent product documentation domains
    # These are official docs pages which are authoritative sources
    "docs.coze.cn", "docs.coze.com",
    "dify.ai", "docs.dify.ai", "cloud.dify.ai",
    "docs.fastgpt.cn", "fastgpt.cn",
}

LOW_AUTHORITY_DOMAINS = {
    "twitter.com", "x.com", "facebook.com", "reddit.com",
    "quora.com", "linkedin.com", "medium.com", "blogspot.com",
}

# P1-2 Fix: Official domain registry for AI Agent platforms
# Only these domains should be classified as official sources
OFFICIAL_PRODUCT_DOMAINS = {
    "dify": {
        "dify.ai", "www.dify.ai", "docs.dify.ai", "cloud.dify.ai",
        "github.com/langgenius/dify",
        # NOTE: Third-party mirrors and blogs are NOT official
        # "dify-china.com" is a third-party mirror
    },
    "coze": {
        "coze.cn", "www.coze.cn", "docs.coze.cn",
        "coze.com", "www.coze.com", "docs.coze.com",
        "字节跳动coze.com",  # In case title contains this
    },
    "flowise": {
        "flowiseai.com", "www.flowiseai.com", "docs.flowiseai.com",
        "github.com/FlowiseAI/Flowise",
    },
    "fastgpt": {
        "fastgpt.cn", "www.fastgpt.cn", "docs.fastgpt.cn",
        "github.com/soeasygpt/fastgpt",
    },
    "langgraph": {
        "langchain.com", "www.langchain.com", "docs.langchain.com",
        "github.com/langchain-ai/langgraph",
    },
}

# Third-party domains that should NOT be classified as official
THIRD_PARTY_DOMAINS = {
    # Chinese tech blogs
    "cloud.tencent.com", "cloud.tencent.cn",
    "dify-china.com", "difychina.com",
    "juejin.cn", "segmentfault.com", "zhihu.com",
    "csdn.net", "cnblogs.com", "imooc.com",
    # General third-party
    "medium.com", "dev.to", "stackoverflow.com",
}

TRUST_TIER_SCORES = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
    "unknown": 0.5,
}

# Navigation/noise patterns
NOISE_PATTERNS = [
    r"^(home|menu|navigation|login|sign up|sign in|register|cookie)",
    r"^(copyright|privacy policy|terms of service|contact us|about)",
    r"^\s*$",
    r"^(click here|read more|learn more|get started|subscribe)",
]

# P0-3: Hard blacklist patterns — evidence containing these is NEVER usable
# P0-Rebuild: Extended to cover 404 pages, directory listings, nav fragments
# that were causing near-zero relevance evidence to slip through.
NOISE_BLACKLIST = [
    "skip to main content",
    "fetch the complete documentation",
    "fetch the complete documentation index",
    "you must be signed in",
    "change notification settings",
    "was this page helpful",
    "not available in your country",
    "not available in your region",
    "cookie consent",
    "cookie preferences",
    "privacy policy",
    "terms of service",
    "iubenda-cs-uspr-link",
    "accept all cookies",
    "edit this page on github",
    "sign in to github",
    "create an account",
    # P0-Rebuild: 404 page noise
    "page not found",
    "we couldn't find the page",
    "maybe you were looking for",
    # P0-Rebuild: Directory/file listing noise (GitHub repo pages)
    "pull requests", "issues", "actions", "releases",
    "packages", "environments", "wiki", "security", "insights",
    "settings", "profile", "your repositories",
    # P0-Rebuild: Navigation-only content
    "request rate limit",
    "model providers",
    # P1 (2026-06-22): Ad/branding noise from third-party pages
    "try langsmith",
    # P1 (2026-06-22): Product branding/intro nav fragments from homepage or docs hero
    "the name dify comes from",
    "do it for you",
    "dive into",
    "dive into dify",
    "dive into your",
    # P1 (2026-06-22): Pagination and feedback widgets
    "was this page helpful",
    "helpful?",
    # P1 (2026-06-22): LangChain/LangGraph ecosystem ads on docs pages
    "langsmith",
    "get a demo",
    "balance agent control with agency",
    "langgraph",
    # P1 (2026-06-22): Self-referential nav noise in docs
    "app toolkit",
    "api extension",
    "version control",
    "annotation system",
    "integrate in apps",
    "test retrieval",
]


def is_noise_evidence(snippet: str) -> bool:
    """
    Check if evidence snippet is pure navigation/noise content.
    
    Returns True if the snippet is just UI noise with no real analytical content.
    """
    if not snippet:
        return True
    
    # Only check the first 200 chars — noise usually appears at the start
    check_text = snippet.lower()[:200]
    
    # Check against blacklist
    for noise_pattern in NOISE_BLACKLIST:
        if noise_pattern in check_text:
            return True
    
    # Check if snippet starts with navigation/UI patterns
    nav_prefixes = [
        "skip to", "jump to", "back to", "return to",
        "sign in", "log in", "register", "create account",
        "copyright", "all rights reserved",
        "home", "menu", "navigation", "cookie",
        # P1 (2026-06-22): CTA and branding nav prefixes
        "dive into", "try", "get started with", "learn more about",
    ]
    for prefix in nav_prefixes:
        if check_text.startswith(prefix):
            return True
    
    return False


def is_meaningful_evidence(snippet: str, min_meaningful_words: int = 5) -> bool:
    """
    Check if snippet contains meaningful content (not just noise).

    Evidence-Sufficiency Sprint: now handles BOTH English and Chinese text.
    English: split by whitespace, filter stopwords, require min_meaningful_words.
    Chinese: use character count as proxy for meaningful content.
    """
    if not snippet or is_noise_evidence(snippet):
        return False

    # Evidence-Sufficiency Sprint: detect Chinese-heavy text
    chinese_chars = sum(1 for c in snippet if "一" <= c <= "鿿")
    chinese_ratio = chinese_chars / max(len(snippet), 1)
    is_chinese_heavy = chinese_ratio > 0.3

    if is_chinese_heavy:
        # Chinese text: use character count as meaningful content proxy.
        # 20+ Chinese characters ≈ 5+ meaningful 2-char words
        return chinese_chars >= 20

    # English: skip common stopwords and check for meaningful content words
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "or", "and", "but",
        "if", "then", "than", "so", "no", "not", "its", "it", "this", "that",
    }

    words = snippet.lower().split()
    meaningful_words = [w for w in words if w not in stopwords and len(w) > 2]

    return len(meaningful_words) >= min_meaningful_words


@dataclass
class EvidenceQuality:
    """Quality metrics for a single evidence item."""
    relevance: float = 0.0
    authority: float = 0.0
    freshness: float = 0.5
    schema_fit: float = 0.0
    information_density: float = 0.0
    final_score: float = 0.0
    usable_for_claim: bool = False
    reasons: list[str] = field(default_factory=list)


class EvidenceEvaluator:
    """
    Rule-based evidence quality evaluator.

    Scoring weights:
    - relevance: 0.25
    - authority: 0.20
    - freshness: 0.10
    - schema_fit: 0.25
    - information_density: 0.20
    """

    WEIGHTS = {
        "relevance": 0.25,
        "authority": 0.20,
        "freshness": 0.10,
        "schema_fit": 0.25,
        "information_density": 0.20,
    }

    # Thresholds for usable_for_claim
    # P0-3: Raised thresholds for better quality gate
    # Only evidence with reasonable scores should be usable for claims
    # Evidence-Sufficiency Sprint: lowered from 0.45 to 0.38.
    # The 0.45 threshold was too strict for short/medium-length evidence (e.g. pricing quotes
    # often are 50-150 chars and cannot reach 0.45 given 5-component weighted scoring).
    # With demo-quality artifacts (short snippets, mixed sources), 0.38 gives reasonable evidence
    # a fair chance while still filtering pure noise.
    FINAL_SCORE_THRESHOLD = 0.32
    # P1 Fix: Lowered from 0.30 to 0.20. The 0.30 threshold was too strict for Chinese-heavy
    # snippets where product names may appear as "扣子" instead of "Coze", resulting in
    # relevance < 0.30 even for high-quality official docs. Lowering to 0.20 lets quality
    # Chinese evidence (high authority + schema_fit + information_density) pass the gate,
    # while still filtering noise through FINAL_SCORE_THRESHOLD and information_density checks.
    RELEVANCE_THRESHOLD = 0.20
    SCHEMA_FIT_THRESHOLD = 0.0     # schema_fit is informational, not a gate

    def __init__(self) -> None:
        # Keep ALL keywords including Chinese.  The original `{k for k in SCHEMA_KEYWORDS if k.isascii()}`
        # silently dropped all Chinese keywords, making schema_fit=0 for Chinese evidence.
        self._schema_keywords_lower = {k.lower() for k in SCHEMA_KEYWORDS}
        self._noise_patterns = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]

    def evaluate(self, evidence: dict[str, Any]) -> EvidenceQuality:
        """
        Evaluate a single evidence item and return quality scores.

        All fields are optional and won't cause errors if missing.
        """
        quality = EvidenceQuality()

        snippet = evidence.get("snippet", "") or ""
        source_type = (evidence.get("source_type") or "").lower()
        url = evidence.get("url", "") or ""
        domain = self._extract_domain(url)
        trust_tier = evidence.get("trust_tier", "unknown")
        fetched_at = evidence.get("fetched_at") or evidence.get("created_at") or ""
        # Evidence-Sufficiency Sprint: evidence_items table has product_slug, not product_name.
        # product_slug contains the clean product name (e.g. "dify", "coze").
        # product_id contains the full compound ID (e.g. "run_xxx_dify") which is not useful for matching.
        product_name = (
            evidence.get("product_slug")  # Try product_slug first (clean name from DB)
            or evidence.get("product_name")  # Fallback to product_name
            or evidence.get("product_id", "")  # Last resort: full compound ID
        ).lower()
        schema_key = (evidence.get("schema_key") or "").lower()

        # P0-3: First check if this is pure noise evidence
        if is_noise_evidence(snippet):
            quality.final_score = 0.0
            quality.usable_for_claim = False
            quality.reasons = ["noise_content: navigation/UI text, not actual analytical content"]
            return quality

        # P0-3: Also check if snippet is meaningful
        if not is_meaningful_evidence(snippet):
            quality.final_score = 0.1
            quality.usable_for_claim = False
            quality.reasons = ["insufficient_meaningful_content: mostly noise or stopwords"]
            return quality

        # Calculate individual scores
        quality.relevance = self._calc_relevance(snippet, product_name, schema_key)
        quality.authority = self._calc_authority(source_type, trust_tier, domain)
        quality.freshness = self._calc_freshness(fetched_at)
        quality.schema_fit = self._calc_schema_fit(snippet)
        quality.information_density = self._calc_information_density(snippet)

        # Calculate final score
        quality.final_score = (
            quality.relevance * self.WEIGHTS["relevance"]
            + quality.authority * self.WEIGHTS["authority"]
            + quality.freshness * self.WEIGHTS["freshness"]
            + quality.schema_fit * self.WEIGHTS["schema_fit"]
            + quality.information_density * self.WEIGHTS["information_density"]
        )

        # P0-3: Raise thresholds for better quality gate
        # Only evidence with reasonable scores should be usable for claims
        quality.usable_for_claim = (
            quality.final_score >= self.FINAL_SCORE_THRESHOLD
            and quality.relevance >= self.RELEVANCE_THRESHOLD
            and quality.schema_fit >= self.SCHEMA_FIT_THRESHOLD
        )

        # Build reasons
        quality.reasons = self._build_reasons(evidence, quality)

        return quality

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        if not url:
            return ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return ""

    def _calc_relevance(
        self, snippet: str, product_name: str, schema_key: str
    ) -> float:
        """
        Calculate relevance score based on:
        - Product name match
        - Schema key match
        - Analysis dimension keywords
        """
        if not snippet:
            return 0.0

        snippet_lower = snippet.lower()
        score = 0.0
        max_score = 0.0

        # 1. Product name match (up to 0.3)
        if product_name and len(product_name) >= 2:
            max_score += 0.3
            # Check for exact product name or common variations
            variations = [
                product_name,
                product_name.replace("-", " "),
                product_name.replace("_", " "),
            ]
            for var in variations:
                if var in snippet_lower:
                    score += 0.3
                    break
            # Also check if first word matches (common in Chinese product names)
            snippet_words = set(snippet_lower.split())
            product_words = set(product_name.replace("-", " ").replace("_", " ").split())
            if product_words & snippet_words:
                score += 0.15

        # 2. Schema key match (up to 0.3)
        max_score += 0.3
        if schema_key:
            # Evidence-Sufficiency Sprint: Chinese text uses substring matching instead of token intersection.
            # For Chinese-heavy snippets, token.split() produces characters which never match schema_parts.
            chinese_ratio = sum(1 for c in snippet if "\u4e00" <= c <= "\u9fff") / max(len(snippet), 1)
            if chinese_ratio > 0.3:
                # Chinese-heavy: use substring match for schema key parts
                schema_lower = schema_key.lower()
                if any(part in snippet_lower for part in schema_lower.replace("_", " ").replace(".", " ").split()):
                    score += 0.25
            else:
                schema_parts = set(schema_key.replace("_", " ").replace(".", " ").split())
                snippet_words = set(snippet_lower.split())
                overlap = schema_parts & snippet_words
                if overlap:
                    score += min(0.3, 0.1 * len(overlap))

        # 3. Analysis dimension keywords (up to 0.4)
        # P1 Fix: Accumulate ALL matching dimension keywords, not just one per dimension.
        # The old break-after-first-match logic capped dim_score at 0.08 for Chinese-heavy
        # snippets where product names don't appear verbatim (e.g. "扣子" instead of "Coze").
        max_score += 0.4
        dim_score = 0.0
        for dim, keywords in DIMENSION_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in snippet_lower:
                    dim_score += 0.08
        score += min(0.4, dim_score)

        return min(1.0, score / max_score) if max_score > 0 else 0.5

    def _calc_authority(
        self, source_type: str, trust_tier: str, domain: str
    ) -> float:
        """
        Calculate authority score based on:
        - Source type (documentation, official sites get high scores)
        - Trust tier
        - Domain reputation
        """
        score = 0.0

        # 1. Source type (0-0.5)
        if source_type in HIGH_AUTHORITY_SOURCE_TYPES:
            score += 0.5
        elif source_type in LOW_AUTHORITY_SOURCE_TYPES:
            score += 0.1
        elif source_type:
            score += 0.3  # Moderate for unknown types

        # 2. Trust tier (0-0.3)
        score += TRUST_TIER_SCORES.get(trust_tier, 0.5) * 0.3

        # 3. Domain reputation (0-0.2)
        if domain:
            if domain in HIGH_AUTHORITY_DOMAINS:
                score += 0.2
            elif domain in LOW_AUTHORITY_DOMAINS:
                score += 0.05
            elif any(h in domain for h in [".io", ".dev", ".ai"]):
                score += 0.15

        return min(1.0, score)

    def _calc_freshness(self, fetched_at: str) -> float:
        """
        Calculate freshness score based on date.

        Returns 0.5 (medium) if date cannot be parsed.
        """
        if not fetched_at:
            return 0.5

        try:
            # Try ISO format
            if "T" in fetched_at:
                dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            else:
                # Try common formats
                for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"]:
                    try:
                        dt = datetime.strptime(fetched_at, fmt)
                        dt = dt.replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                else:
                    return 0.5

            now = datetime.now(timezone.utc)
            age_days = (now - dt).days

            # Score based on age
            if age_days < 0:  # Future date - suspicious
                return 0.3
            elif age_days <= 30:
                return 1.0
            elif age_days <= 90:
                return 0.8
            elif age_days <= 180:
                return 0.6
            elif age_days <= 365:
                return 0.4
            elif age_days <= 730:  # ~2 years
                return 0.2
            else:
                return 0.1

        except Exception:
            return 0.5

    def _calc_schema_fit(self, snippet: str) -> float:
        """
        Calculate schema fit based on AI Agent product schema keywords.

        For Chinese-heavy snippets, uses substring matching (so "编排" matches even
        within "编排的关键"). For English snippets, uses token intersection.
        """
        if not snippet:
            return 0.0

        snippet_lower = snippet.lower()
        chinese_ratio = sum(1 for c in snippet if "\u4e00" <= c <= "\u9fff") / max(len(snippet), 1)

        if chinese_ratio > 0.3:
            # Chinese-heavy: count how many schema keywords appear as substrings in snippet
            match_count = sum(
                1 for kw in self._schema_keywords_lower
                if kw in snippet_lower
            )
        else:
            # ASCII: token intersection
            ascii_pattern = re.compile(r"[a-z][a-z0-9]*(?:[-./][a-z0-9]+)*")
            ascii_tokens = ascii_pattern.findall(snippet_lower)
            snippet_tokens: set[str] = set()
            for token in ascii_tokens:
                if "/" in token:
                    snippet_tokens.update(token.split("/"))
                else:
                    snippet_tokens.add(token)
            matches = self._schema_keywords_lower & snippet_tokens
            match_count = len(matches)

        # Calculate score
        if match_count == 0:
            return 0.0
        elif match_count == 1:
            return 0.3
        elif match_count == 2:
            return 0.5
        elif match_count <= 4:
            return 0.7
        elif match_count <= 6:
            return 0.85
        else:
            return 1.0

    def _calc_information_density(self, snippet: str) -> float:
        """
        Calculate information density based on:
        - Length (too short = low density)
        - Numbers/statistics
        - Lists and structured content
        - Configuration/feature words
        - Noise pattern detection (penalize navigation noise)
        """
        if not snippet:
            return 0.0

        snippet_lower = snippet.lower()
        snippet_text = snippet.lower()  # Keep original case for noise detection
        score = 0.0

        # 0. Noise detection (penalize navigation/noise patterns) - up to -0.3
        noise_penalty = 0.0
        for pattern in self._noise_patterns:
            if pattern.search(snippet_text):
                noise_penalty += 0.1
        # Cap penalty at 0.3
        noise_penalty = min(0.3, noise_penalty)

        # 1. Length score (0-0.5)
        # P1-Fix: Raised ceiling from 0.3 to 0.5. The Evidence-Sufficiency Sprint
        # found that 500-char chunks (typical doc snippets) were capped at information_density=0.3,
        # which dragged final_score below the 0.32 threshold even for high-quality content.
        # Chinese text is semantically denser; apply a length_score boost for Chinese-heavy content.
        length = len(snippet)
        chinese_ratio = sum(1 for c in snippet if "\u4e00" <= c <= "\u9fff") / max(len(snippet), 1)
        is_chinese_heavy = chinese_ratio > 0.3
        if length < 50:
            length_score = 0.1
        elif length < 100:
            length_score = 0.2
        elif length < 200:
            length_score = 0.35
        elif length < 500:
            length_score = 0.45
        else:
            length_score = 0.5
        # Chinese text is semantically denser; apply a length_score boost for Chinese-heavy content
        if is_chinese_heavy:
            length_score = min(0.55, length_score * 1.15)
        score += length_score

        # 2. Number/digit density (0-0.25)
        numbers = re.findall(r"\d+(?:\.\d+)?", snippet)
        if numbers:
            # Count significant numbers (prices, versions, percentages)
            significant = [n for n in numbers if len(n) >= 2 or "." in n]
            num_score = min(0.25, len(significant) * 0.05)
            score += num_score

        # 3. List/structure indicators (0-0.2)
        list_indicators = [
            r"^\s*[-*•]\s",  # Bullet points
            r"^\s*\d+\.\s",  # Numbered lists
            r",\s*(?:and|or)\s",  # Enumerations
            r"\b(?:and|or|plus|including)\b.*\b(?:and|or|plus)\b",
            # Evidence-Sufficiency Sprint: Chinese list/structure patterns
            r"^\s*[\d一二三四五六七八九十]+\.\s",  # Chinese numbered lists
            r"\uff0c",  # Chinese comma (U+FF0C) as enumeration separator
            r"\u3001",  # Chinese enumeration comma (U+3001)
        ]
        for pattern in list_indicators:
            if re.search(pattern, snippet, re.MULTILINE | re.IGNORECASE):
                score += 0.05

        # 4. Feature/configuration words (0-0.25)
        # Evidence-Sufficiency Sprint: Added Chinese equivalents so Chinese evidence
        # (e.g. Dify/Coze/FastGPT official docs) scores meaningful feature_word matches.
        feature_words = [
            # English
            "support", "provide", "offer", "include", "feature",
            "config", "setting", "option", "enable", "disable",
            "api", "integration", "plugin", "extension",
            "deploy", "host", "install", "setup", "configure",
            "model", "prompt", "workflow", "automation",
            # Chinese equivalents (same semantic categories)
            "支持", "提供", "包含", "功能", "特性",
            "配置", "设置", "选项", "启用", "禁用",
            "接口", "集成", "插件", "扩展",
            "部署", "安装", "搭建", "编排",
            "模型", "提示词", "工作流", "自动化",
            # Additional high-signal Chinese tech keywords
            "知识库", "向量", "检索", "嵌入", "文档",
            "智能体", "智能助手", "机器人", "对话",
            "私有化", "本地", "开源", "企业版",
            "单点登录", "权限", "审计", "加密",
            "编排", "节点", "画布", "拖拽",
            "工作流", "流程", "自动化", "触发器",
            "调用", "API", "Webhook", "连接器",
            # English tech keywords that may appear in Chinese docs too
            "agent", "rag", "llm", "gpt", "embedding",
        ]
        feature_matches = sum(1 for w in feature_words if w in snippet_lower)
        feature_score = min(0.25, feature_matches * 0.04)
        score += feature_score

        # Apply noise penalty
        score = max(0.0, score - noise_penalty)

        return min(1.0, score)

    def _build_reasons(
        self, evidence: dict[str, Any], quality: EvidenceQuality
    ) -> list[str]:
        """Build human-readable reasons for the quality assessment."""
        reasons = []

        # Relevance
        if quality.relevance >= 0.7:
            reasons.append("high relevance to product/schema")
        elif quality.relevance >= 0.4:
            reasons.append("moderate relevance")
        elif quality.relevance > 0:
            reasons.append("low relevance")

        # Authority
        source_type = (evidence.get("source_type") or "").lower()
        if source_type in HIGH_AUTHORITY_SOURCE_TYPES:
            reasons.append(f"authoritative source type: {source_type}")
        elif quality.authority >= 0.7:
            reasons.append("high authority source")
        elif quality.authority < 0.3:
            reasons.append("low authority source")

        # Freshness
        fetched_at = evidence.get("fetched_at") or evidence.get("created_at") or ""
        if fetched_at:
            try:
                if "T" in fetched_at:
                    dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                else:
                    dt = datetime.strptime(fetched_at.split()[0], "%Y-%m-%d")
                age_days = (datetime.now(timezone.utc) - dt).days
                if age_days <= 30:
                    reasons.append("recent evidence")
                elif age_days <= 180:
                    reasons.append("moderately recent")
                elif age_days > 365:
                    reasons.append("dated evidence")
            except Exception:
                pass

        # Schema fit
        if quality.schema_fit >= 0.7:
            reasons.append("strong schema alignment")
        elif quality.schema_fit >= 0.4:
            reasons.append("some schema keywords match")
        elif quality.schema_fit > 0:
            reasons.append("few schema keywords")

        # Information density
        snippet_len = len(evidence.get("snippet", "") or "")
        if quality.information_density >= 0.7:
            reasons.append("rich information content")
        elif quality.information_density < 0.3:
            reasons.append("low information density")

        # Usable flag
        if quality.usable_for_claim:
            reasons.append("usable for claim support")
        else:
            if quality.final_score < self.FINAL_SCORE_THRESHOLD:
                reasons.append(f"score below threshold ({quality.final_score:.2f} < {self.FINAL_SCORE_THRESHOLD})")

        return reasons


def evaluate_evidence_items(
    evidence_items: list[dict[str, Any]],
    run_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Evaluate a list of evidence items and return enriched items + summary.

    Each evidence item is enriched with a 'quality' field containing:
    - relevance, authority, freshness, schema_fit, information_density
    - final_score, usable_for_claim, reasons

    Returns:
        Tuple of (enriched_evidence_items, summary_dict)
    """
    evaluator = EvidenceEvaluator()

    enriched_items = []
    summary = {
        "total_evidence": len(evidence_items),
        "usable_evidence": 0,
        "avg_final_score": 0.0,
        "low_quality_count": 0,  # final_score < 0.4
        "run_id": run_id or "",
    }

    if not evidence_items:
        return enriched_items, summary

    total_score = 0.0
    usable_count = 0
    low_quality = 0

    for evidence in evidence_items:
        try:
            quality = evaluator.evaluate(evidence)

            # Add quality data to evidence
            enriched = dict(evidence)
            enriched["quality"] = {
                "relevance": round(quality.relevance, 3),
                "authority": round(quality.authority, 3),
                "freshness": round(quality.freshness, 3),
                "schema_fit": round(quality.schema_fit, 3),
                "information_density": round(quality.information_density, 3),
                "final_score": round(quality.final_score, 3),
                "usable_for_claim": quality.usable_for_claim,
                "reasons": quality.reasons,
            }

            enriched_items.append(enriched)

            # Update summary stats
            total_score += quality.final_score
            if quality.usable_for_claim:
                usable_count += 1
            if quality.final_score < 0.4:
                low_quality += 1

        except Exception as e:
            # Gracefully handle individual evidence evaluation errors
            enriched = dict(evidence)
            enriched["quality"] = {
                "relevance": 0.5,
                "authority": 0.5,
                "freshness": 0.5,
                "schema_fit": 0.0,
                "information_density": 0.5,
                "final_score": 0.25,
                "usable_for_claim": False,
                "reasons": [f"evaluation error: {str(e)}"],
            }
            enriched_items.append(enriched)
            low_quality += 1

    # Compute averages
    if evidence_items:
        summary["avg_final_score"] = round(total_score / len(evidence_items), 3)
    summary["usable_evidence"] = usable_count
    summary["low_quality_count"] = low_quality

    return enriched_items, summary
