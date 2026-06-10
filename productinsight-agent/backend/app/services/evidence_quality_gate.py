"""
Evidence Quality Gate Module

Evaluates and filters evidence based on quality criteria.
Implements the quality gate that prevents low-quality content from entering reports.

Quality Gate Checklist:
- Filter cookie/login pages
- Filter skip-to-content placeholders
- Filter region restrictions
- Filter API keys, tokens, secrets
- Filter navigation text
- Filter non-content pages
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Quality Scores
# ============================================================================

@dataclass
class QualityScore:
    """Quality score for a piece of evidence."""
    
    # Overall quality score (0.0 - 1.0)
    score: float
    
    # Individual dimension scores
    relevance: float = 0.0
    authority: float = 0.0
    currency: float = 0.0
    accuracy: float = 0.0
    
    # Issues found
    issues: list[str] = field(default_factory=list)
    
    # Whether this should be filtered
    filtered: bool = False
    filter_reason: str = ""
    
    def is_acceptable(self) -> bool:
        """Check if evidence meets minimum quality threshold."""
        return self.score >= 0.3 and not self.filtered
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "relevance": self.relevance,
            "authority": self.authority,
            "currency": self.currency,
            "accuracy": self.accuracy,
            "issues": self.issues,
            "filtered": self.filtered,
            "filter_reason": self.filter_reason,
        }


# ============================================================================
# Quality Gate
# ============================================================================

class EvidenceQualityGate:
    """
    Quality gate for evidence evaluation.
    
    Evaluates evidence against multiple quality criteria:
    1. Content quality (no boilerplate, no errors)
    2. Relevance (matches the query/dimension)
    3. Authority (official source vs third-party)
    4. Currency (recency of information)
    5. Accuracy (factual correctness signals)
    
    Usage:
        gate = EvidenceQualityGate()
        score = gate.evaluate(evidence_content, dimension=..., source=...)
        if score.is_acceptable():
            accept_evidence(evidence)
        else:
            log_rejection(score.filter_reason)
    """
    
    # Minimum quality score to accept evidence
    MIN_QUALITY_SCORE = 0.3
    
    # Patterns that indicate low-quality content (immediate rejection)
    REJECT_PATTERNS = [
        # Cookie consent pages
        (r"cookie|cookies", "cookie_notice"),
        (r"accept.*cookie|cookie.*policy|cookie.*consent", "cookie_notice"),
        
        # Login/signup pages
        (r"sign in|log in|login|sign up|register", "login_required"),
        (r"you must be signed in|please sign in|must be signed in", "login_required"),
        (r"access denied.*sign|signed in to access", "login_required"),
        
        # Skip to content placeholders
        (r"skip to (main )?content", "skip_to_content"),
        (r"skip.*navigation", "skip_to_content"),
        
        # Region restrictions
        (r"not available in your region|not available in.*region", "region_restricted"),
        (r"content.*not available.*your country", "region_restricted"),
        (r"403.*forbidden|access denied", "access_denied"),
        (r"404.*not found", "not_found"),
        
        # API keys, tokens, secrets - STRICT filtering
        (r"api[_-]?key|api[_-]?secret", "contains_api_key"),
        (r"bearer.*token|token.*secret|secret.*key", "contains_token"),
        (r"password\s*=\s*[\w-]+|secret\s*=\s*[\w-]+", "contains_secret"),
        (r"(sk-|rk-)[a-zA-Z0-9]{20,}", "contains_api_key"),
        (r"-----BEGIN.*PRIVATE KEY", "contains_private_key"),
        
        # Boilerplate/non-content
        (r"javascript is required|enable javascript", "requires_javascript"),
        (r"page could not be loaded", "page_load_error"),
        
        # Navigation menus
        (r"^home$|^menu$|^search$|^contact$|^about$", "navigation_text"),
    ]
    
    # Patterns that indicate high-quality content
    QUALITY_SIGNALS = [
        # Official documentation
        (r"official|documentation|docs\.", "official_docs", 0.2),
        (r"pricing|plan|tier|subscription", "pricing_page", 0.15),
        (r"feature|capability|support", "feature_page", 0.1),
        
        # Specific high-authority sources
        (r"github\.com", "github", 0.15),
        (r"stackoverflow|stack exchange", "community", 0.1),
        (r"medium\.com|dev\.to", "blog", 0.05),
    ]
    
    def __init__(self, min_score: float = MIN_QUALITY_SCORE):
        self.min_score = min_score
        
        # Compile reject patterns
        self._reject_patterns = [
            (re.compile(pattern, re.IGNORECASE), reason)
            for pattern, reason in self.REJECT_PATTERNS
        ]
        
        # Compile quality signal patterns
        self._quality_patterns = [
            (re.compile(pattern, re.IGNORECASE), name, bonus)
            for pattern, name, bonus in self.QUALITY_SIGNALS
        ]
    
    def evaluate(
        self,
        content: str,
        url: str = "",
        dimension: str = "",
        query: str = "",
    ) -> QualityScore:
        """
        Evaluate evidence quality.
        
        Args:
            content: The evidence content (text)
            url: Source URL
            dimension: Analysis dimension this evidence is for
            query: Original search query
            
        Returns:
            QualityScore with evaluation results
        """
        issues = []
        score_details = {
            "relevance": 0.5,
            "authority": 0.5,
            "currency": 0.5,
            "accuracy": 0.5,
        }
        
        # Step 1: Check for rejection patterns (STRICT)
        content_lower = content.lower()
        for pattern, reason in self._reject_patterns:
            if pattern.search(content):
                issues.append(reason)
                # Hard rejection for security issues
                if reason in ["contains_api_key", "contains_token", "contains_secret", "contains_private_key"]:
                    score_details["accuracy"] = 0.0
                    overall = 0.0
                    return QualityScore(
                        score=0.0,
                        relevance=score_details["relevance"],
                        authority=0.0,
                        currency=score_details["currency"],
                        accuracy=0.0,
                        issues=issues,
                        filtered=True,
                        filter_reason=reason,
                    )
                # Soft rejection for other issues
                elif reason in ["cookie_notice", "login_required", "skip_to_content"]:
                    score_details["accuracy"] *= 0.0
                elif reason in ["region_restricted", "access_denied", "not_found"]:
                    score_details["accuracy"] *= 0.0
        
        # Step 2: Check URL for quality signals
        url_lower = url.lower()
        for pattern, name, bonus in self._quality_patterns:
            if pattern.search(url_lower):
                score_details["authority"] += bonus
        
        # Step 3: Evaluate content length
        if len(content) < 100:
            issues.append("too_short")
            score_details["accuracy"] *= 0.5
        elif len(content) > 50000:
            # Very long content might be raw HTML
            issues.append("possibly_raw_html")
            score_details["accuracy"] *= 0.8
        
        # Step 4: Check for HTML artifacts
        if self._has_html_artifacts(content):
            issues.append("html_artifacts")
            score_details["accuracy"] *= 0.7
        
        # Step 5: Evaluate relevance to dimension/query
        if dimension:
            dim_words = dimension.lower().split()
            matches = sum(1 for w in dim_words if w in content_lower)
            relevance = min(1.0, matches / max(1, len(dim_words)))
            score_details["relevance"] = 0.3 + (relevance * 0.5)
        
        # Step 6: Check for factual indicators
        factual_indicators = [
            r"\d+%",  # Percentages
            r"\$\d+",  # Prices
            r"\d{4}",  # Years
            r"(since|from|in) \d{4}",  # Time references
        ]
        
        factual_count = sum(
            len(re.findall(pattern, content, re.IGNORECASE))
            for pattern in factual_indicators
        )
        if factual_count > 0:
            score_details["accuracy"] += min(0.2, factual_count * 0.05)
        
        # Calculate overall score
        weights = {"relevance": 0.3, "authority": 0.25, "currency": 0.2, "accuracy": 0.25}
        overall = sum(score_details[k] * v for k, v in weights.items())
        
        # Determine if filtered
        filtered = bool(issues) and overall < self.min_score
        filter_reason = "; ".join(issues) if filtered else ""
        
        return QualityScore(
            score=overall,
            relevance=score_details["relevance"],
            authority=score_details["authority"],
            currency=score_details["currency"],
            accuracy=score_details["accuracy"],
            issues=issues,
            filtered=filtered,
            filter_reason=filter_reason,
        )
    
    def _has_html_artifacts(self, content: str) -> bool:
        """Check if content contains HTML artifacts."""
        html_patterns = [
            r"<[^>]+>",  # HTML tags
            r"&[a-z]+;",  # HTML entities
            r"\{[^}]+\}",  # Might be JSON/JS
        ]
        
        count = sum(len(re.findall(p, content)) for p in html_patterns)
        # If more than 5% of content is HTML-like, flag it
        return count > len(content) * 0.05


# ============================================================================
# Quality Gate for Evidence Items
# ============================================================================

class EvidenceItemQualityGate:
    """
    Quality gate that works with Evidence items (structured evidence).
    
    This is a higher-level gate that evaluates complete evidence items
    with metadata, not just raw content.
    """
    
    def __init__(self, min_score: float = 0.3):
        self.content_gate = EvidenceQualityGate(min_score)
    
    def evaluate_evidence_item(
        self,
        evidence_item: dict[str, Any],
    ) -> QualityScore:
        """
        Evaluate a structured evidence item.
        
        Expected evidence_item structure:
        {
            "content": str,  # Main content
            "url": str,      # Source URL
            "title": str,    # Title
            "snippet": str,  # Search snippet or summary
            ...
        }
        """
        # Combine relevant text fields
        content_fields = [
            evidence_item.get("content", ""),
            evidence_item.get("title", ""),
            evidence_item.get("snippet", ""),
        ]
        combined_content = " ".join(content_fields)
        
        url = evidence_item.get("url", "")
        dimension = evidence_item.get("dimension", "")
        
        score = self.content_gate.evaluate(
            content=combined_content,
            url=url,
            dimension=dimension,
        )
        
        # Additional checks on structured data
        if not evidence_item.get("url"):
            score.issues.append("no_url")
            score.score *= 0.9
        
        if not evidence_item.get("title"):
            score.issues.append("no_title")
            score.score *= 0.95
        
        return score
    
    def filter_evidence_list(
        self,
        evidence_items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Filter a list of evidence items.
        
        Returns:
            Tuple of (accepted, rejected) evidence lists
        """
        accepted = []
        rejected = []
        
        for item in evidence_items:
            score = self.evaluate_evidence_item(item)
            
            # Add score to item for transparency
            item["quality_score"] = score.to_dict()
            
            if score.is_acceptable():
                accepted.append(item)
            else:
                rejected.append(item)
                logger.debug(f"Evidence filtered: {score.filter_reason}")
        
        logger.info(f"Quality gate: {len(accepted)} accepted, {len(rejected)} rejected")
        return accepted, rejected


# ============================================================================
# URL Quality Checker
# ============================================================================

class URLQualityChecker:
    """
    Specialized checker for URL quality.
    
    Some URLs are inherently low quality and should be filtered.
    """
    
    # URL patterns that are likely low quality
    LOW_QUALITY_PATTERNS = [
        # Social media login pages
        r"(login|signin).*\.(facebook|twitter|linkedin)\.com",
        # Redirect/tracking URLs
        r"out\.|click\.|track\.|redirect",
        # PDF that might be login-gated
        r"\.(pdf|docx?)",
        # Video sites (hard to extract structured data)
        r"(youtube|vimeo|dailymotion)\.com",
        # Image URLs
        r"\.(jpg|jpeg|png|gif|svg|webp)",
    ]
    
    # URL patterns that are high quality
    HIGH_QUALITY_PATTERNS = [
        # Official documentation
        r"docs?\.",
        # Official pricing
        r"pricing",
        # GitHub repositories
        r"github\.com/[\w-]+/[\w-]+",
        # Help/knowledge base
        r"/help|/support|/kb|/knowledge",
    ]
    
    def __init__(self):
        self._low_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.LOW_QUALITY_PATTERNS
        ]
        self._high_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.HIGH_QUALITY_PATTERNS
        ]
    
    def check_url(self, url: str) -> tuple[bool, str]:
        """
        Check URL quality.
        
        Returns:
            Tuple of (is_acceptable, reason)
        """
        if not url:
            return False, "empty_url"
        
        # Check low quality patterns
        for pattern in self._low_patterns:
            if pattern.search(url):
                return False, f"low_quality_url:{pattern.pattern}"
        
        # Check high quality patterns
        for pattern in self._high_patterns:
            if pattern.search(url):
                return True, "high_quality_url"
        
        # Neutral - not high quality but not low quality
        return True, "acceptable_url"


# ============================================================================
# Convenience Functions
# ============================================================================

def evaluate_evidence(
    content: str,
    url: str = "",
    dimension: str = "",
    min_score: float = 0.3,
) -> QualityScore:
    """
    Convenience function to evaluate evidence quality.
    
    Usage:
        score = evaluate_evidence(content, url="https://...")
        if score.is_acceptable():
            use_evidence()
    """
    gate = EvidenceQualityGate(min_score)
    return gate.evaluate(content, url, dimension)


def filter_evidence_batch(
    evidence_items: list[dict[str, Any]],
    min_score: float = 0.3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Convenience function to filter a batch of evidence items.
    
    Usage:
        good, bad = filter_evidence_batch(evidence_list)
        for item in good:
            store_evidence(item)
    """
    gate = EvidenceItemQualityGate(min_score)
    return gate.filter_evidence_list(evidence_items)
