"""
Evidence Gap Module

Handles identification and reporting of evidence gaps in the competitive analysis.
An evidence gap is a dimension or metric that cannot be supported by available evidence.

Key concepts:
- Not all dimensions need to have full evidence
- Some gaps are acceptable (not every feature matters)
- Some gaps are critical (missing key decision data)
- Vertical APIs can fill premium gaps in future iterations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Evidence Gap Types
# ============================================================================

@dataclass
class EvidenceGapType:
    """Classification of evidence gaps by cause."""
    
    # Gap caused by missing vertical API access
    PREMIUM_DATA_NEEDED = "premium_data_needed"
    
    # Gap caused by anti-scraping measures
    ANTI_SCRAPING = "anti_scraping"
    
    # Gap caused by login/registration requirements
    LOGIN_REQUIRED = "login_required"
    
    # Gap caused by no public source available
    NO_PUBLIC_SOURCE = "no_public_source"
    
    # Gap caused by low-quality scraped content
    LOW_QUALITY_CONTENT = "low_quality_content"
    
    # Gap caused by region restrictions
    REGION_RESTRICTED = "region_restricted"
    
    # Gap caused by outdated information
    OUTDATED = "outdated"
    
    # Gap for optional/nice-to-have features
    OPTIONAL_FEATURE = "optional_feature"


# ============================================================================
# Evidence Gap
# ============================================================================

@dataclass
class EvidenceGap:
    """
    Represents a gap in evidence for a specific dimension or claim.
    
    Attributes:
        dimension: The analysis dimension with missing evidence
        product: The product/product category with the gap
        gap_type: Classification of why evidence is missing
        impact: Impact level on decision-making (critical, moderate, low)
        description: Human-readable description of the gap
        suggested_resolution: How to potentially fill the gap
        alternative_evidence: Any partial evidence that could substitute
    """
    
    dimension: str
    product: str
    gap_type: str
    impact: str = "moderate"  # critical, moderate, low
    description: str = ""
    suggested_resolution: str = ""
    alternative_evidence: list[str] = field(default_factory=list)
    
    # Metadata
    confidence_impact: float = 0.0  # How much this gap affects overall confidence
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage/JSON."""
        return {
            "dimension": self.dimension,
            "product": self.product,
            "gap_type": self.gap_type,
            "impact": self.impact,
            "description": self.description,
            "suggested_resolution": self.suggested_resolution,
            "alternative_evidence": self.alternative_evidence,
            "confidence_impact": self.confidence_impact,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceGap":
        """Create from dictionary."""
        return cls(
            dimension=data.get("dimension", ""),
            product=data.get("product", ""),
            gap_type=data.get("gap_type", EvidenceGapType.NO_PUBLIC_SOURCE),
            impact=data.get("impact", "moderate"),
            description=data.get("description", ""),
            suggested_resolution=data.get("suggested_resolution", ""),
            alternative_evidence=data.get("alternative_evidence", []),
            confidence_impact=data.get("confidence_impact", 0.0),
        )


# ============================================================================
# Evidence Gap Detector
# ============================================================================

class EvidenceGapDetector:
    """
    Detects and classifies evidence gaps in the competitive analysis.
    
    Usage:
        detector = EvidenceGapDetector(schema=domain_schema)
        gaps = detector.detect_gaps(collected_evidence, required_dimensions)
    """
    
    # Patterns that indicate low-quality content (should be filtered)
    LOW_QUALITY_PATTERNS = [
        "skip to main content",
        "you must be signed in",
        "cookie",
        "api key",
        "password",
        "token",
        "not available in your region",
        "this page could not be loaded",
        "403 forbidden",
        "404 not found",
        "access denied",
        "javascript is required",
        "enable javascript",
    ]
    
    # Dimensions that typically require premium APIs
    PREMIUM_DIMENSIONS = {
        "coffee_chain": {
            "store_coverage": "Store density data requires Dianping API",
            "membership": "Member stats require platform data sharing",
            "delivery": "Delivery metrics require Meituan API",
            "坪效": "Revenue per sqm requires financial disclosures",
        },
        "ev_automobile": {
            "sales_volume": "Sales data requires CADA or official reports",
            "user_reviews": "User reviews require Dongchedi API",
        },
        "hr_saas": {
            "customer_count": "Customer count requires vendor disclosure",
            "nps_score": "NPS requires G2 or vendor data",
        },
    }
    
    def __init__(
        self,
        schema: dict[str, Any] | None = None,
        domain: str = "general",
    ):
        """
        Initialize the gap detector.
        
        Args:
            schema: Domain schema with comparison_dimensions
            domain: Domain identifier for premium dimension lookup
        """
        self.schema = schema or {}
        self.domain = domain
        self.comparison_dimensions = schema.get("comparison_dimensions", []) if schema else []
    
    def detect_gaps(
        self,
        evidence_by_dimension: dict[str, list[dict]],
        required_dimensions: list[str] | None = None,
    ) -> list[EvidenceGap]:
        """
        Detect evidence gaps from collected evidence.
        
        Args:
            evidence_by_dimension: Dict mapping dimension to list of evidence items
            required_dimensions: List of dimensions that must have evidence
            
        Returns:
            List of detected EvidenceGap objects
        """
        gaps = []
        required = set(required_dimensions or [d["dimension"] for d in self.comparison_dimensions])
        
        for dimension in required:
            dimension_evidence = evidence_by_dimension.get(dimension, [])
            
            # Check for missing evidence
            if not dimension_evidence:
                gap = self._create_gap_for_missing_dimension(dimension)
                gaps.append(gap)
                continue
            
            # Check for low-quality evidence
            low_quality_count = self._count_low_quality_evidence(dimension_evidence)
            if low_quality_count == len(dimension_evidence):
                gap = self._create_gap_for_low_quality(dimension, dimension_evidence)
                gaps.append(gap)
        
        logger.info(f"Detected {len(gaps)} evidence gaps for domain={self.domain}")
        return gaps
    
    def _create_gap_for_missing_dimension(self, dimension: str) -> EvidenceGap:
        """Create a gap when dimension has no evidence at all."""
        # Check if this is a known premium dimension
        premium_hints = self.PREMIUM_DIMENSIONS.get(self.domain, {}).get(dimension)
        
        if premium_hints:
            return EvidenceGap(
                dimension=dimension,
                product="all",
                gap_type=EvidenceGapType.PREMIUM_DATA_NEEDED,
                impact="moderate",
                description=f"Evidence for '{dimension}' requires access to premium data sources",
                suggested_resolution=premium_hints,
                confidence_impact=0.3,
            )
        
        # Check if dimension is in schema
        dim_info = self._get_dimension_info(dimension)
        
        if dim_info:
            return EvidenceGap(
                dimension=dimension,
                product="all",
                gap_type=EvidenceGapType.NO_PUBLIC_SOURCE,
                impact="low",
                description=f"No public evidence found for dimension: {dimension}",
                suggested_resolution=f"Search for {dim_info.get('chinese', dimension)} in official sources",
                confidence_impact=0.2,
            )
        
        return EvidenceGap(
            dimension=dimension,
            product="all",
            gap_type=EvidenceGapType.NO_PUBLIC_SOURCE,
            impact="low",
            description=f"No evidence found for: {dimension}",
            confidence_impact=0.1,
        )
    
    def _create_gap_for_low_quality(
        self,
        dimension: str,
        evidence: list[dict],
    ) -> EvidenceGap:
        """Create a gap when all evidence is low quality."""
        # Check for anti-scraping patterns
        has_anti_scraping = any(
            any(pattern in str(e.get("content", "")).lower() 
                for pattern in self.LOW_QUALITY_PATTERNS)
            for e in evidence
        )
        
        if has_anti_scraping:
            return EvidenceGap(
                dimension=dimension,
                product="all",
                gap_type=EvidenceGapType.ANTI_SCRAPING,
                impact="moderate",
                description=f"Evidence for '{dimension}' was blocked by anti-scraping measures",
                suggested_resolution="Use official APIs or manual research for this dimension",
                confidence_impact=0.4,
            )
        
        return EvidenceGap(
            dimension=dimension,
            product="all",
            gap_type=EvidenceGapType.LOW_QUALITY_CONTENT,
            impact="moderate",
            description=f"All collected evidence for '{dimension}' is low quality",
            suggested_resolution="Rely on official documentation and press releases",
            confidence_impact=0.3,
        )
    
    def _count_low_quality_evidence(self, evidence: list[dict]) -> int:
        """Count how many evidence items are low quality."""
        count = 0
        for e in evidence:
            content = str(e.get("content", "")).lower()
            snippet = str(e.get("snippet", "")).lower()
            combined = content + snippet
            
            if any(pattern in combined for pattern in self.LOW_QUALITY_PATTERNS):
                count += 1
        
        return count
    
    def _get_dimension_info(self, dimension: str) -> dict | None:
        """Get dimension info from schema."""
        for dim in self.comparison_dimensions:
            if dim.get("dimension") == dimension:
                return dim
        return None
    
    def classify_gap_type_from_error(self, error: Exception) -> str:
        """
        Classify gap type from an exception that occurred during collection.
        
        Args:
            error: Exception from evidence collection
            
        Returns:
            Gap type string
        """
        error_str = str(error).lower()
        
        if "403" in error_str or "forbidden" in error_str:
            return EvidenceGapType.ANTI_SCRAPING
        elif "login" in error_str or "sign in" in error_str or "authentication" in error_str:
            return EvidenceGapType.LOGIN_REQUIRED
        elif "region" in error_str or "not available in" in error_str:
            return EvidenceGapType.REGION_RESTRICTED
        elif "timeout" in error_str or "connection" in error_str:
            return EvidenceGapType.NO_PUBLIC_SOURCE
        else:
            return EvidenceGapType.NO_PUBLIC_SOURCE


# ============================================================================
# Evidence Gap Reporter
# ============================================================================

class EvidenceGapReporter:
    """
    Generates reports from evidence gaps.
    
    Provides methods to:
    - Generate summary text for report sections
    - Calculate overall confidence score
    - Suggest report disclaimers
    """
    
    def __init__(self, gaps: list[EvidenceGap]):
        self.gaps = gaps
    
    def generate_summary(self) -> dict[str, Any]:
        """Generate a summary of evidence gaps."""
        if not self.gaps:
            return {
                "has_gaps": False,
                "gap_count": 0,
                "critical_gaps": [],
                "confidence_impact": 0.0,
            }
        
        # Group by impact
        critical = [g for g in self.gaps if g.impact == "critical"]
        moderate = [g for g in self.gaps if g.impact == "moderate"]
        low = [g for g in self.gaps if g.impact == "low"]
        
        # Calculate confidence impact
        total_impact = sum(g.confidence_impact for g in self.gaps)
        avg_impact = total_impact / len(self.gaps) if self.gaps else 0
        
        return {
            "has_gaps": True,
            "gap_count": len(self.gaps),
            "critical_gaps": [g.dimension for g in critical],
            "moderate_gaps": [g.dimension for g in moderate],
            "low_gaps": [g.dimension for g in low],
            "confidence_impact": min(avg_impact, 1.0),
            "confidence_level": self._get_confidence_level(avg_impact),
        }
    
    def _get_confidence_level(self, impact: float) -> str:
        """Get confidence level from impact score."""
        if impact < 0.2:
            return "high"
        elif impact < 0.4:
            return "medium"
        else:
            return "low"
    
    def generate_disclaimer(self) -> str:
        """Generate a disclaimer for the report based on gaps."""
        if not self.gaps:
            return ""
        
        summary = self.generate_summary()
        
        # Group by gap type
        gap_types = {}
        for g in self.gaps:
            gap_types.setdefault(g.gap_type, []).append(g.dimension)
        
        # Generate type-specific messages
        type_messages = []
        
        if "premium_data_needed" in gap_types:
            dims = ", ".join(gap_types["premium_data_needed"])
            type_messages.append(f"以下维度的精确数据需要垂直API支持：{dims}。当前结论基于公开信息推断。")
        
        if "anti_scraping" in gap_types or "login_required" in gap_types:
            type_messages.append("部分信息因反爬或登录限制无法直接获取，已通过替代来源估算。")
        
        if "no_public_source" in gap_types:
            dims = ", ".join(gap_types["no_public_source"])
            type_messages.append(f"以下维度缺乏公开权威来源：{dims}。报告结论仅供参考。")
        
        # Build disclaimer
        disclaimer_parts = [
            "**数据说明**：",
            *type_messages,
            f"本报告置信度：{summary['confidence_level']}。",
        ]
        
        return " ".join(disclaimer_parts)
    
    def generate_gap_section(self) -> dict[str, Any]:
        """Generate content for a report section about evidence gaps."""
        if not self.gaps:
            return {
                "title": "数据完整性",
                "content": "本报告基于充分的公开信息编制，数据覆盖完整。",
                "confidence": "high",
            }
        
        summary = self.generate_summary()
        
        # Build gap descriptions
        gap_descriptions = []
        for g in self.gaps:
            if g.impact in ["critical", "moderate"]:
                gap_descriptions.append({
                    "dimension": g.dimension,
                    "gap_type": g.gap_type,
                    "description": g.description,
                    "resolution": g.suggested_resolution,
                })
        
        return {
            "title": "数据完整性说明",
            "content": self.generate_disclaimer(),
            "confidence": summary["confidence_level"],
            "gaps": gap_descriptions,
            "gap_count": len(self.gaps),
        }
    
    def should_block_report(self) -> bool:
        """
        Determine if gaps are severe enough to block report generation.
        
        Returns:
            True if critical gaps exist that should block the report
        """
        critical_gaps = [g for g in self.gaps if g.impact == "critical"]
        return len(critical_gaps) > 0


# ============================================================================
# Utility Functions
# ============================================================================

def create_gap_from_collection_failure(
    dimension: str,
    product: str,
    error: Exception,
    schema: dict[str, Any] | None = None,
) -> EvidenceGap:
    """
    Create an evidence gap from a collection failure.
    
    Args:
        dimension: The dimension that failed
        product: The product that failed
        error: The exception that occurred
        schema: Optional schema for context
        
    Returns:
        EvidenceGap object
    """
    detector = EvidenceGapDetector(schema=schema)
    gap_type = detector.classify_gap_type_from_error(error)
    
    return EvidenceGap(
        dimension=dimension,
        product=product,
        gap_type=gap_type,
        impact="moderate",
        description=f"Failed to collect evidence: {str(error)[:100]}",
        suggested_resolution=_get_resolution_for_gap_type(gap_type),
        confidence_impact=0.3,
    )


def _get_resolution_for_gap_type(gap_type: str) -> str:
    """Get suggested resolution for a gap type."""
    resolutions = {
        EvidenceGapType.PREMIUM_DATA_NEEDED: "Requires subscription to premium data API (e.g., Dianping, Meituan, G2)",
        EvidenceGapType.ANTI_SCRAPING: "Use official APIs or request data directly from vendor",
        EvidenceGapType.LOGIN_REQUIRED: "Use publicly available information or request vendor documentation",
        EvidenceGapType.NO_PUBLIC_SOURCE: "Rely on alternative sources (news, industry reports)",
        EvidenceGapType.LOW_QUALITY_CONTENT: "Filter low-quality sources and rely on authoritative sources",
        EvidenceGapType.REGION_RESTRICTED: "Use regional databases or local research partners",
        EvidenceGapType.OUTDATED: "Seek more recent sources or verify information with vendors",
        EvidenceGapType.OPTIONAL_FEATURE: "This gap is for an optional feature and does not affect core analysis",
    }
    return resolutions.get(gap_type, "Manual research recommended")
