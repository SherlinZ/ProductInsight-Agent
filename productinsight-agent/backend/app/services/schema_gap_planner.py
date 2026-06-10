"""
Schema Gap Planner - Detects missing or weak schema coverage for AI Agent products.

Analyzes facts and evidence to identify gaps in schema coverage,
generates suggested queries for filling gaps, and provides coverage metrics.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Required schema keys for AI Agent product analysis
REQUIRED_SCHEMA_KEYS = [
    "workflow_orchestration",
    "agent_builder",
    "tool_calling",
    "knowledge_base",
    "document_ingestion",
    "rag_support",
    "cloud_hosted",
    "self_hosted",
    "docker_support",
    "private_deployment",
    "free_tier",
    "paid_plans",
    "enterprise_plan",
    "rbac",
    "sso",
    "audit_log",
    "api_support",
    "webhook",
    "model_provider_support",
]

# Schema key normalization mapping
# Maps variations of schema keys to canonical required keys
SCHEMA_KEY_NORMALIZATION = {
    # workflow
    "workflow": "workflow_orchestration",
    "orchestration": "workflow_orchestration",
    "orchestrat": "workflow_orchestration",
    "flow": "workflow_orchestration",
    "workflow_orchestration": "workflow_orchestration",
    # agent
    "agent_builder": "agent_builder",
    "agent": "agent_builder",
    "agentic": "agent_builder",
    "bot": "agent_builder",
    # tool
    "tool_calling": "tool_calling",
    "tool": "tool_calling",
    "plugin": "tool_calling",
    "extension": "tool_calling",
    # knowledge
    "knowledge_base": "knowledge_base",
    "knowledge": "knowledge_base",
    "rag": "rag_support",
    "retrieval": "knowledge_base",
    "vector": "rag_support",
    "embedding": "rag_support",
    # document
    "document_ingestion": "document_ingestion",
    "document": "document_ingestion",
    "ingestion": "document_ingestion",
    "document_processing": "document_ingestion",
    # rag
    "rag_support": "rag_support",
    "rag": "rag_support",
    # deployment
    "cloud_hosted": "cloud_hosted",
    "cloud": "cloud_hosted",
    "saas": "cloud_hosted",
    "self_hosted": "self_hosted",
    "self-hosted": "self_hosted",
    "selfhosted": "self_hosted",
    "on_premise": "self_hosted",
    "on-premise": "self_hosted",
    "onpremise": "self_hosted",
    "docker_support": "docker_support",
    "docker": "docker_support",
    "kubernetes": "docker_support",
    "k8s": "docker_support",
    "private_deployment": "private_deployment",
    "private-deployment": "private_deployment",
    "private_deploy": "private_deployment",
    "deploy": "private_deployment",
    "deployment": "private_deployment",
    # pricing
    "free_tier": "free_tier",
    "free-tier": "free_tier",
    "free": "free_tier",
    "free_plan": "free_tier",
    "paid_plans": "paid_plans",
    "paid": "paid_plans",
    "paid_plan": "paid_plans",
    "pricing": "paid_plans",
    "price": "paid_plans",
    "subscription": "paid_plans",
    "enterprise_plan": "enterprise_plan",
    "enterprise": "enterprise_plan",
    # enterprise features
    "rbac": "rbac",
    "role_based": "rbac",
    "role-based": "rbac",
    "sso": "sso",
    "saml": "sso",
    "ldap": "sso",
    "oauth": "sso",
    "audit_log": "audit_log",
    "audit": "audit_log",
    "audit-log": "audit_log",
    "logging": "audit_log",
    "compliance": "audit_log",
    # integration
    "api_support": "api_support",
    "api": "api_support",
    "rest": "api_support",
    "webhook": "webhook",
    "webhooks": "webhook",
    "integration": "api_support",
    "model_provider_support": "model_provider_support",
    "model_provider": "model_provider_support",
    "model_support": "model_provider_support",
    "llm": "model_provider_support",
    "gpt": "model_provider_support",
    "claude": "model_provider_support",
    "provider": "model_provider_support",
}

# Required source types for each schema key (suggested priority)
SCHEMA_SOURCE_TYPES = {
    "workflow_orchestration": ["documentation", "official_site", "technical_blog"],
    "agent_builder": ["documentation", "official_site"],
    "tool_calling": ["documentation", "github", "api_reference"],
    "knowledge_base": ["documentation", "technical_blog"],
    "document_ingestion": ["documentation", "api_reference"],
    "rag_support": ["documentation", "technical_blog", "api_reference"],
    "cloud_hosted": ["official_site", "pricing_page", "documentation"],
    "self_hosted": ["documentation", "github", "technical_blog"],
    "docker_support": ["github", "documentation"],
    "private_deployment": ["documentation", "github", "technical_blog"],
    "free_tier": ["pricing_page", "official_site", "documentation"],
    "paid_plans": ["pricing_page", "official_site", "documentation"],
    "enterprise_plan": ["pricing_page", "official_site", "documentation"],
    "rbac": ["documentation", "official_site", "technical_blog"],
    "sso": ["documentation", "official_site", "technical_blog"],
    "audit_log": ["documentation", "official_site", "technical_blog"],
    "api_support": ["api_reference", "documentation", "github"],
    "webhook": ["api_reference", "documentation", "github"],
    "model_provider_support": ["documentation", "api_reference", "technical_blog"],
}


@dataclass
class SchemaGap:
    """Represents a gap in schema coverage for a product."""
    gap_id: str
    run_id: str
    product_id: str
    product_name: str
    product_slug: str
    schema_key: str
    gap_type: str  # missing_fact | weak_evidence | low_confidence | stale_source
    priority: str  # high | medium | low
    required_source_types: list[str]
    suggested_queries: list[str]
    reason: str
    related_evidence_ids: list[str]
    created_at: str


class SchemaGapPlanner:
    """
    Analyzes schema coverage and identifies gaps.

    Detects four types of gaps:
    1. missing_fact: No fact exists for a required schema key
    2. weak_evidence: Fact exists but evidence quality is low
    3. low_confidence: Fact confidence is below threshold
    4. stale_source: Evidence freshness is below threshold
    """

    USABLE_SCORE_THRESHOLD = 0.6
    CONFIDENCE_THRESHOLD = 0.55
    FRESHNESS_THRESHOLD = 0.4

    # Priority mapping for gap types
    GAP_TYPE_PRIORITY = {
        "missing_fact": "high",
        "low_confidence": "medium",
        "weak_evidence": "medium",
        "stale_source": "low",
    }

    def __init__(self) -> None:
        self._schema_key_set = set(REQUIRED_SCHEMA_KEYS)

    def plan(
        self,
        facts: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
        products: list[dict[str, Any]],
        run_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Analyze schema coverage and generate gaps.

        Args:
            facts: List of extracted facts
            evidence_items: List of evidence items with quality data
            products: List of products from task_brief
            run_id: Current run ID

        Returns:
            Tuple of (schema_gaps, coverage_summary)
        """
        if not products:
            return [], self._empty_coverage_summary(run_id)

        # Build fact map: {(product_slug, normalized_schema_key): fact}
        fact_map = self._build_fact_map(facts)

        # Build evidence map: {(product_slug, normalized_schema_key): [evidence]}
        evidence_map = self._build_evidence_map(evidence_items)

        # Build evidence_by_id: {evidence_id: evidence}
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for ev in evidence_items:
            ev_id = ev.get("evidence_id", "")
            if ev_id:
                evidence_by_id[ev_id] = ev

        gaps: list[dict[str, Any]] = []
        coverage: dict[str, dict[str, Any]] = {}

        for product in products:
            product_slug = self._normalize_product_slug(product)
            product_id = product.get("product_id", "")
            product_name = product.get("product_name", product_id)

            product_coverage: dict[str, Any] = {
                "product_id": product_id,
                "product_name": product_name,
                "product_slug": product_slug,
                "filled_keys": [],
                "missing_keys": [],
                "gap_count": 0,
                "coverage_rate": 0.0,
            }

            for schema_key in REQUIRED_SCHEMA_KEYS:
                key_fact = fact_map.get((product_slug, schema_key))
                key_evidence = evidence_map.get((product_slug, schema_key), [])

                if key_fact is None:
                    # Missing fact
                    gap = self._create_missing_fact_gap(
                        product_id, product_name, product_slug,
                        schema_key, run_id, key_evidence
                    )
                    gaps.append(gap)
                    product_coverage["missing_keys"].append(schema_key)
                    product_coverage["gap_count"] += 1
                else:
                    product_coverage["filled_keys"].append(schema_key)
                    # Check for quality issues - pass fact and evidence_by_id
                    gap_type, reason, related_ids = self._check_quality_issues(
                        key_fact, key_evidence, evidence_by_id
                    )
                    if gap_type:
                        gap = self._create_quality_gap(
                            product_id, product_name, product_slug,
                            schema_key, run_id, gap_type, reason, related_ids
                        )
                        gaps.append(gap)
                        product_coverage["gap_count"] += 1

            # Calculate coverage rate
            total_keys = len(REQUIRED_SCHEMA_KEYS)
            filled_keys = len(product_coverage["filled_keys"])
            product_coverage["coverage_rate"] = filled_keys / total_keys if total_keys > 0 else 0.0
            coverage[product_slug] = product_coverage

        # Build summary
        summary = self._build_summary(gaps, coverage, run_id)

        return gaps, summary

    def _normalize_product_slug(self, product: dict[str, Any]) -> str:
        """Normalize product slug from product dict."""
        slug = product.get("product_slug", "")
        if not slug:
            pid = product.get("product_id", "")
            slug = pid.lower().replace(" ", "-").replace("_", "-")
        return slug

    def _normalize_schema_key(self, raw_key: str) -> str:
        """Normalize a raw schema key to canonical form.

        Handles hierarchical keys like:
        - deployment_options.private_deployment
        - pricing_model.free_tier
        - enterprise_readiness.sso

        Priority:
        1. Match complete key in SCHEMA_KEY_NORMALIZATION
        2. Match last segment (most specific) in SCHEMA_KEY_NORMALIZATION
        3. Match any token in SCHEMA_KEY_NORMALIZATION
        4. Return as-is if no match
        """
        if not raw_key:
            return ""
        key_lower = raw_key.lower().strip()

        # 1. Try exact match first
        if key_lower in SCHEMA_KEY_NORMALIZATION:
            return SCHEMA_KEY_NORMALIZATION[key_lower]

        # 2. Split by ".", "_", "-", "/", " " and process tokens
        import re
        # Split on common delimiters
        tokens = re.split(r'[\./_\-\s]+', key_lower)
        # Remove empty tokens
        tokens = [t for t in tokens if t]

        if not tokens:
            return key_lower

        # 3. Check if any required schema key is contained in the original key
        # This handles cases like "private_deployment" in "deployment_options.private_deployment"
        for required_key in self._schema_key_set:
            if required_key in key_lower:
                return required_key

        # 4. Try to match from most specific (last token) to least specific (first token)
        for token in reversed(tokens):
            if token in SCHEMA_KEY_NORMALIZATION:
                return SCHEMA_KEY_NORMALIZATION[token]
            # Also check if token contains a required key
            for required_key in self._schema_key_set:
                if required_key in token:
                    return required_key

        # 5. Check tokens in order
        for token in tokens:
            if token in SCHEMA_KEY_NORMALIZATION:
                return SCHEMA_KEY_NORMALIZATION[token]

        # 6. No match found - return the most specific token (last one)
        return tokens[-1] if tokens else key_lower

    def _build_fact_map(
        self, facts: list[dict[str, Any]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Build a map of (product_slug, normalized_schema_key) -> fact."""
        fact_map: dict[tuple[str, str], dict[str, Any]] = {}

        for fact in facts:
            product_slug = fact.get("product_slug", "")
            if not product_slug:
                pid = fact.get("product_id", "")
                product_slug = pid.lower().replace(" ", "-").replace("_", "-")

            raw_key = fact.get("schema_key", "")
            normalized_key = self._normalize_schema_key(raw_key)

            if not product_slug or not normalized_key:
                continue

            # Keep the fact with highest confidence if duplicate
            key = (product_slug, normalized_key)
            if key not in fact_map:
                fact_map[key] = fact
            else:
                existing_conf = fact_map[key].get("confidence", 0)
                new_conf = fact.get("confidence", 0)
                if new_conf > existing_conf:
                    fact_map[key] = fact

        return fact_map

    def _build_evidence_map(
        self, evidence_items: list[dict[str, Any]]
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Build a map of (product_slug, normalized_schema_key) -> [evidence]."""
        evidence_map: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for ev in evidence_items:
            product_slug = ev.get("product_slug", "")
            if not product_slug:
                pid = ev.get("product_id", "")
                product_slug = pid.lower().replace(" ", "-").replace("_", "-")

            raw_key = ev.get("schema_key", "")
            normalized_key = self._normalize_schema_key(raw_key)

            if not product_slug or not normalized_key:
                continue

            key = (product_slug, normalized_key)
            if key not in evidence_map:
                evidence_map[key] = []
            evidence_map[key].append(ev)

        return evidence_map

    def _check_quality_issues(
        self,
        fact: dict[str, Any],
        evidence: list[dict[str, Any]],
        evidence_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str | None, str, list[str]]:
        """Check for quality issues with fact and evidence.

        Priority for finding supporting evidence:
        1. Use fact["evidence_ids"] if available
        2. Fallback to schema-key matched evidence

        Returns (gap_type, reason, related_evidence_ids) or (None, "", []) if no issues.
        """
        # Check confidence first
        fact_confidence = fact.get("confidence", 0.5)
        if fact_confidence < self.CONFIDENCE_THRESHOLD:
            return "low_confidence", f"Fact confidence {fact_confidence:.2f} below threshold", []

        # Get evidence IDs from fact
        fact_evidence_ids = fact.get("evidence_ids", [])
        # Handle JSON string format
        if isinstance(fact_evidence_ids, str):
            try:
                import json
                fact_evidence_ids = json.loads(fact_evidence_ids)
            except Exception:
                fact_evidence_ids = []

        # Determine which evidence to use
        supporting_evidence: list[dict[str, Any]] = []
        related_ids: list[str] = []

        if fact_evidence_ids and evidence_by_id:
            # Priority 1: Use fact["evidence_ids"]
            for ev_id in fact_evidence_ids:
                if ev_id in evidence_by_id:
                    supporting_evidence.append(evidence_by_id[ev_id])
                    related_ids.append(ev_id)

            # If fact has evidence_ids but none found in evidence_by_id
            if not supporting_evidence and len(fact_evidence_ids) > 0:
                return "weak_evidence", "Fact has evidence_ids but supporting evidence was not found", related_ids
        elif evidence:
            # Priority 2: Fallback to schema-key matched evidence
            supporting_evidence = evidence
            related_ids = [e.get("evidence_id", "") for e in evidence if e.get("evidence_id")]
        else:
            # No evidence available
            if fact_evidence_ids:
                # Fact claims to have evidence but none found
                return "weak_evidence", "Fact has evidence_ids but supporting evidence was not found", related_ids
            else:
                # Fact has no evidence_ids and no matched evidence
                return "weak_evidence", "Fact has no supporting evidence", related_ids

        if not supporting_evidence:
            return "weak_evidence", "No supporting evidence found for this fact", related_ids

        # Check evidence quality
        usable_evidence = []
        stale_evidence = []

        for ev in supporting_evidence:
            ev_id = ev.get("evidence_id", "")
            if ev_id not in related_ids:
                related_ids.append(ev_id)

            # Check quality if present
            quality = ev.get("quality", {})
            if quality:
                final_score = quality.get("final_score", 0.0)
                usable_for_claim = quality.get("usable_for_claim", False)
                freshness = quality.get("freshness", 0.5)

                # Check freshness first - stale evidence is always a concern
                if freshness < self.FRESHNESS_THRESHOLD:
                    stale_evidence.append(ev)

                # Check usability
                if usable_for_claim and final_score >= self.USABLE_SCORE_THRESHOLD:
                    usable_evidence.append(ev)
            else:
                # No quality data - consider it potentially weak
                usable_evidence.append(ev)

        # Determine gap type - check stale first since it's data quality concern
        if stale_evidence:
            stale_count = len(stale_evidence)
            total_count = len(supporting_evidence)
            if stale_count == total_count:
                return "stale_source", "All evidence is stale (>1 year old)", related_ids
            elif stale_count > total_count / 2:
                return "stale_source", f"{stale_count}/{total_count} evidence items are stale", related_ids

        if not usable_evidence:
            return "weak_evidence", "No usable evidence for this fact", related_ids

        return None, "", related_ids

    def _create_missing_fact_gap(
        self,
        product_id: str,
        product_name: str,
        product_slug: str,
        schema_key: str,
        run_id: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a missing_fact gap."""
        # Check if any evidence exists for this schema key
        related_ids = [e.get("evidence_id", "") for e in evidence if e.get("evidence_id")]

        # Determine reason
        if evidence:
            reason = f"No fact extracted despite {len(evidence)} evidence items"
        else:
            reason = "No fact or evidence found for this schema key"

        gap = SchemaGap(
            gap_id=f"gap_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            product_id=product_id,
            product_name=product_name,
            product_slug=product_slug,
            schema_key=schema_key,
            gap_type="missing_fact",
            priority=self.GAP_TYPE_PRIORITY["missing_fact"],
            required_source_types=SCHEMA_SOURCE_TYPES.get(schema_key, ["documentation"]),
            suggested_queries=self._generate_queries(product_name, schema_key),
            reason=reason,
            related_evidence_ids=related_ids,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._gap_to_dict(gap)

    def _create_quality_gap(
        self,
        product_id: str,
        product_name: str,
        product_slug: str,
        schema_key: str,
        run_id: str,
        gap_type: str,
        reason: str,
        related_ids: list[str],
    ) -> dict[str, Any]:
        """Create a quality-related gap."""
        gap = SchemaGap(
            gap_id=f"gap_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            product_id=product_id,
            product_name=product_name,
            product_slug=product_slug,
            schema_key=schema_key,
            gap_type=gap_type,
            priority=self.GAP_TYPE_PRIORITY.get(gap_type, "medium"),
            required_source_types=SCHEMA_SOURCE_TYPES.get(schema_key, ["documentation"]),
            suggested_queries=self._generate_queries(product_name, schema_key),
            reason=reason,
            related_evidence_ids=related_ids,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return self._gap_to_dict(gap)

    def _generate_queries(self, product_name: str, schema_key: str) -> list[str]:
        """Generate suggested search queries for filling a gap."""
        queries = []
        name = product_name.strip()

        # Generate 2-4 queries based on schema key category
        if schema_key in ("self_hosted", "docker_support", "private_deployment"):
            queries.extend([
                f"{name} self hosted deployment",
                f"{name} docker deployment documentation",
                f"{name} kubernetes self hosted",
            ])
        elif schema_key in ("free_tier", "paid_plans", "enterprise_plan"):
            queries.extend([
                f"{name} pricing plans",
                f"{name} free tier",
                f"{name} enterprise pricing",
            ])
        elif schema_key in ("rbac", "sso", "audit_log"):
            queries.extend([
                f"{name} enterprise SSO RBAC",
                f"{name} audit log compliance",
                f"{name} security features documentation",
            ])
        elif schema_key in ("api_support", "webhook"):
            queries.extend([
                f"{name} API documentation",
                f"{name} webhook integration",
                f"{name} REST API reference",
            ])
        elif schema_key in ("model_provider_support",):
            queries.extend([
                f"{name} supported AI models",
                f"{name} LLM provider integration",
                f"{name} GPT Claude integration",
            ])
        elif schema_key == "workflow_orchestration":
            queries.extend([
                f"{name} workflow automation",
                f"{name} orchestration features",
                f"{name} visual workflow builder",
            ])
        elif schema_key == "agent_builder":
            queries.extend([
                f"{name} AI agent builder",
                f"{name} agent configuration",
                f"{name} custom agent setup",
            ])
        elif schema_key in ("knowledge_base", "rag_support", "document_ingestion"):
            queries.extend([
                f"{name} RAG knowledge base",
                f"{name} document ingestion",
                f"{name} vector search",
            ])
        else:
            # Generic query
            queries.extend([
                f"{name} {schema_key.replace('_', ' ')}",
                f"{name} features documentation",
            ])

        return queries[:4]  # Max 4 queries

    def _gap_to_dict(self, gap: SchemaGap) -> dict[str, Any]:
        """Convert SchemaGap dataclass to dict."""
        return {
            "gap_id": gap.gap_id,
            "run_id": gap.run_id,
            "product_id": gap.product_id,
            "product_name": gap.product_name,
            "product_slug": gap.product_slug,
            "schema_key": gap.schema_key,
            "gap_type": gap.gap_type,
            "priority": gap.priority,
            "required_source_types": gap.required_source_types,
            "suggested_queries": gap.suggested_queries,
            "reason": gap.reason,
            "related_evidence_ids": gap.related_evidence_ids,
            "created_at": gap.created_at,
        }

    def _empty_coverage_summary(self, run_id: str) -> dict[str, Any]:
        """Return empty coverage summary."""
        return {
            "run_id": run_id,
            "total_required_keys": len(REQUIRED_SCHEMA_KEYS),
            "products_analyzed": 0,
            "total_gaps": 0,
            "high_priority_gaps": 0,
            "medium_priority_gaps": 0,
            "low_priority_gaps": 0,
            "schema_completion_rate": 0.0,
            "schema_coverage_by_product": {},
            "missing_schema_keys_by_product": {},
        }

    def _build_summary(
        self,
        gaps: list[dict[str, Any]],
        coverage: dict[str, dict[str, Any]],
        run_id: str,
    ) -> dict[str, Any]:
        """Build coverage summary from gaps and coverage data."""
        high_priority = sum(1 for g in gaps if g.get("priority") == "high")
        medium_priority = sum(1 for g in gaps if g.get("priority") == "medium")
        low_priority = sum(1 for g in gaps if g.get("priority") == "low")

        # Calculate overall coverage rate
        total_keys = len(REQUIRED_SCHEMA_KEYS)
        total_filled = sum(len(c.get("filled_keys", [])) for c in coverage.values())
        total_possible = total_keys * len(coverage) if coverage else 1
        overall_rate = total_filled / total_possible if total_possible > 0 else 0.0

        # Missing keys by product
        missing_by_product = {
            slug: cov.get("missing_keys", [])
            for slug, cov in coverage.items()
        }

        return {
            "run_id": run_id,
            "total_required_keys": total_keys,
            "products_analyzed": len(coverage),
            "total_gaps": len(gaps),
            "high_priority_gaps": high_priority,
            "medium_priority_gaps": medium_priority,
            "low_priority_gaps": low_priority,
            "schema_completion_rate": round(overall_rate, 3),
            "schema_coverage_by_product": {
                slug: {
                    "coverage_rate": cov.get("coverage_rate", 0.0),
                    "filled_keys": cov.get("filled_keys", []),
                }
                for slug, cov in coverage.items()
            },
            "missing_schema_keys_by_product": missing_by_product,
        }


def detect_schema_gaps(
    facts: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    products: list[dict[str, Any]],
    run_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Convenience function to detect schema gaps.

    Returns (schema_gaps, coverage_summary).
    """
    planner = SchemaGapPlanner()
    return planner.plan(facts, evidence_items, products, run_id)
