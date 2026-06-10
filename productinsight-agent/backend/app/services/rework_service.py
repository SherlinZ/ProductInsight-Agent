"""
Rework Service - Automatic gap remediation based on Schema Gap analysis and reviewer requests.

This service:
1. Creates ReworkTask from high-priority schema gaps and reviewer rework requests
2. Tracks task status: pending/running/succeeded/failed/skipped/closed
3. Records before_metrics by snapshotting current state
4. Attempts local fact extraction to fill gaps
5. Records after_metrics and new fact/claim IDs
6. Writes results to state for metrics
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReworkTask:
    """Represents a single rework task for gap remediation."""
    # Core fields (required)
    rework_id: str
    run_id: str
    source_type: str  # schema_gap | reviewer
    source_id: str  # gap_id or rework_request_id
    target_node: str  # extract_facts | analyze_dimensions
    target_agent: str  # ExtractorAgent | AnalystAgent
    product_id: str
    product_name: str
    schema_key: str
    reason: str
    # Primary ID
    task_id: str = ""  # Compatibility alias
    # Context
    required_actions: dict[str, Any] = field(default_factory=dict)
    affected_objects: list[str] = field(default_factory=list)
    # Tracking
    status: str = "pending"  # pending | running | succeeded | failed | skipped | closed | evidence_extraction_failed
    retry_count: int = 0
    max_retry: int = 2
    # Metrics
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after: dict[str, Any] = field(default_factory=dict)
    # New items produced
    new_evidence_ids: list[str] = field(default_factory=list)
    new_fact_ids: list[str] = field(default_factory=list)
    new_facts: list[dict[str, Any]] = field(default_factory=list)
    new_claim_ids: list[str] = field(default_factory=list)
    new_claims: list[dict[str, Any]] = field(default_factory=list)
    # Timestamps
    created_at: str = ""
    completed_at: str = ""
    # Legacy compat
    error_message: str = ""

    def __post_init__(self):
        if not self.task_id:
            self.task_id = self.rework_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "rework_id": self.rework_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "target_node": self.target_node,
            "target_agent": self.target_agent,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "schema_key": self.schema_key,
            "reason": self.reason,
            "required_actions": self.required_actions,
            "affected_objects": self.affected_objects,
            "status": self.status,
            "retry_count": self.retry_count,
            "max_retry": self.max_retry,
            "metrics_before": self.metrics_before,
            "metrics_after": self.metrics_after,
            "new_evidence_ids": self.new_evidence_ids,
            "new_fact_ids": self.new_fact_ids,
            "new_facts": self.new_facts,
            "new_claim_ids": self.new_claim_ids,
            "new_claims": self.new_claims,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
        }


def _snapshot_rework_metrics(
    schema_gaps: list[dict[str, Any]],
    claim_drafts: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute lightweight metrics snapshot from current state."""
    schema_gaps_count = len(schema_gaps)
    high_priority_gaps = sum(1 for g in schema_gaps if g.get("priority") in ("high", "medium"))
    claim_count = len(claim_drafts)
    signed_count = len(signed_claims)

    # Unsupported claim rate
    unsupported = claim_count - signed_count
    unsupported_rate = unsupported / claim_count if claim_count > 0 else 0.0

    # Evidence coverage
    claims_with_evidence = sum(1 for c in claim_drafts if c.get("evidence_ids"))
    evidence_coverage = claims_with_evidence / claim_count if claim_count > 0 else 0.0

    # Schema completion rate
    total_keys = 19  # REQUIRED_SCHEMA_KEYS length
    unique_products = len({f.get("product_id") or f.get("product_slug") for f in facts if f.get("product_id") or f.get("product_slug")})
    filled_keys = len({(f.get("product_id") or f.get("product_slug"), f.get("schema_key")) for f in facts})
    schema_completion = filled_keys / (total_keys * max(unique_products, 1)) if total_keys > 0 else 0.0

    return {
        "schema_completion_rate": round(schema_completion, 3),
        "schema_gaps_count": schema_gaps_count,
        "high_priority_schema_gaps": high_priority_gaps,
        "claim_count": claim_count,
        "signed_claim_count": signed_count,
        "unsupported_claim_rate": round(unsupported_rate, 3),
        "evidence_coverage_rate": round(evidence_coverage, 3),
        "facts_count": len(facts),
        "evidence_count": len(evidence_items),
        "sources_count": len(sources),
    }


class ReworkService:
    """
    Service for creating and executing rework tasks based on schema gaps and reviewer requests.

    This is a rule-based rework service that:
    1. Converts high-priority schema gaps and reviewer requests into rework tasks
    2. Attempts local fact extraction using existing evidence
    3. Tracks task status and metrics
    4. Does NOT perform real network searches or LLM calls
    """

    def __init__(self) -> None:
        self._tasks: list[ReworkTask] = []

    def create_rework_tasks_from_schema_gaps(
        self,
        schema_gaps: list[dict[str, Any]],
        run_id: str,
        metrics_before: dict[str, Any],
    ) -> list[ReworkTask]:
        """
        Create rework tasks from high-priority schema gaps.

        Selects at most 5 gaps, prioritizing missing_fact and weak_evidence types.
        """
        tasks = []
        created_at = datetime.now(timezone.utc).isoformat()

        # Sort: high priority first, then missing_fact > weak_evidence > others
        priority_order = {"high": 0, "medium": 1, "low": 2}
        gap_type_order = {"missing_fact": 0, "weak_evidence": 1, "low_confidence": 2, "stale_source": 3}

        sorted_gaps = sorted(
            schema_gaps,
            key=lambda g: (
                priority_order.get(g.get("priority", "low"), 2),
                gap_type_order.get(g.get("gap_type", ""), 4),
            )
        )

        # Take at most 5
        for gap in sorted_gaps[:5]:
            rework_id = f"rework_{uuid.uuid4().hex[:12]}"
            gap_type = gap.get("gap_type", "missing_fact")

            task = ReworkTask(
                rework_id=rework_id,
                run_id=run_id,
                source_type="schema_gap",
                source_id=gap.get("gap_id", ""),
                target_node="extract_facts",
                target_agent="ExtractorAgent",
                product_id=gap.get("product_id", ""),
                product_name=gap.get("product_name", ""),
                schema_key=gap.get("schema_key", ""),
                reason=gap.get("reason", ""),
                required_actions={
                    "schema_key": gap.get("schema_key", ""),
                    "product_id": gap.get("product_id", ""),
                    "suggested_queries": gap.get("suggested_queries", [])[:4],
                    "required_source_types": gap.get("required_source_types", []),
                    "gap_type": gap_type,
                },
                affected_objects=[gap.get("gap_id", "")],
                status="pending",
                retry_count=0,
                max_retry=2,
                metrics_before=dict(metrics_before),
                metrics_after={},
                new_evidence_ids=[],
                new_fact_ids=[],
                new_facts=[],
                new_claim_ids=[],
                new_claims=[],
                created_at=created_at,
                completed_at="",
                error_message="",
            )
            tasks.append(task)

        return tasks

    def create_rework_tasks_from_reviewer_requests(
        self,
        rework_requests: list[dict[str, Any]],
        run_id: str,
        metrics_before: dict[str, Any],
        claim_drafts: list[dict[str, Any]],
        signed_claims: list[dict[str, Any]],
    ) -> list[ReworkTask]:
        """
        Convert ReviewerAgent rework_requests into ReworkTask objects.
        """
        tasks = []
        created_at = datetime.now(timezone.utc).isoformat()

        for req in rework_requests:
            # Determine source_id from available fields
            source_id = (
                req.get("rework_request_id")
                or req.get("review_id")
                or req.get("claim_id")
                or f"req_{uuid.uuid4().hex[:8]}"
            )

            # Target node and agent from request, default to extract_facts
            target_node = req.get("target_node", "extract_facts")
            target_agent = req.get("target_agent", "ExtractorAgent")

            # If claim_id is available, target analyze_dimensions for claims
            claim_id = req.get("claim_id")
            if claim_id:
                # Check if claim exists
                claim_exists = any(c.get("claim_id") == claim_id for c in claim_drafts)
                if claim_exists:
                    target_node = "analyze_dimensions"
                    target_agent = "AnalystAgent"

            rework_id = f"rework_{uuid.uuid4().hex[:12]}"
            task = ReworkTask(
                rework_id=rework_id,
                run_id=run_id,
                source_type="reviewer",
                source_id=source_id,
                target_node=target_node,
                target_agent=target_agent,
                product_id=req.get("product_id", ""),
                product_name=req.get("product_name", ""),
                schema_key=req.get("schema_key", ""),
                reason=req.get("reason", "") or req.get("message", ""),
                required_actions={
                    "rework_request_id": source_id,
                    "claim_id": claim_id,
                    "suggested_queries": req.get("suggested_queries", [])[:4],
                },
                affected_objects=[claim_id] if claim_id else [],
                status="pending",
                retry_count=0,
                max_retry=2,
                metrics_before=dict(metrics_before),
                metrics_after={},
                new_evidence_ids=[],
                new_fact_ids=[],
                new_facts=[],
                new_claim_ids=[],
                new_claims=[],
                created_at=created_at,
                completed_at="",
                error_message="",
            )
            tasks.append(task)

        return tasks

    def execute_rework_tasks(
        self,
        sources: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        claim_drafts: list[dict[str, Any]],
        signed_claims: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Execute pending rework tasks.

        Attempts local supplementation by re-running fact extraction for relevant
        product/schema combinations and comparing before/after state.

        Args:
            sources: Existing sources from the workflow
            evidence_items: Existing evidence items
            facts: Existing facts
            claim_drafts: Current claim drafts
            signed_claims: Current signed claims

        Returns:
            Tuple of (updated_task_dicts, after_metrics)
        """
        if not self._tasks:
            return [], {}

        now = datetime.now(timezone.utc).isoformat()
        completed_tasks = []
        total_tasks = len(self._tasks)
        succeeded_count = 0
        failed_count = 0
        skipped_count = 0

        for task in self._tasks:
            if task.status != "pending":
                continue

            task.status = "running"

            # Snapshot metrics_before at task start
            metrics_before_task = _snapshot_rework_metrics(
                [],  # No gaps in task context
                claim_drafts,
                signed_claims,
                evidence_items,
                facts,
                sources,
            )

            try:
                result = self._attempt_local_supplementation(
                    task, sources, evidence_items, facts, claim_drafts, signed_claims
                )

                # Handle skipped tasks (data collection issues, not failures)
                if result.get("skipped"):
                    task.status = "skipped"
                    task.error_message = result.get("error", "Skipped due to data collection issue")
                    task.metrics_after = result.get("after_metrics", {})
                    skipped_count += 1
                    completed_tasks.append(task.to_dict())
                    continue

                if result["success"]:
                    # vNext-P0: Check if evidence/facts/claims actually improved
                    metrics_before_task = result.get("metrics_before", {})
                    metrics_after_task = result.get("after_metrics", {})
                    
                    evidence_improved = (
                        metrics_after_task.get("evidence_count", 0) > metrics_before_task.get("evidence_count", 0)
                    )
                    facts_improved = (
                        metrics_after_task.get("facts_count", 0) > metrics_before_task.get("facts_count", 0)
                    )
                    claims_improved = (
                        metrics_after_task.get("claim_count", 0) > metrics_before_task.get("claim_count", 0)
                    )
                    
                    # Read new items from result dict (task.new_* not yet populated)
                    new_evidence = len(result.get("new_evidence_ids", []))
                    new_facts = len(result.get("new_fact_ids", []))
                    new_claims = len(result.get("new_claim_ids", []))
                    
                    if new_evidence > 0 or new_facts > 0 or new_claims > 0:
                        task.status = "succeeded"
                        succeeded_count += 1
                    elif evidence_improved or facts_improved or claims_improved:
                        task.status = "succeeded"
                        succeeded_count += 1
                    else:
                        # Sources were supplemented but no new evidence/facts/claims
                        task.status = "evidence_extraction_failed"
                        task.error_message = task.error_message or (
                            f"Sources supplemented but no evidence/facts/claims improved. "
                            f"Evidence: {metrics_before_task.get('evidence_count', 0)} -> {metrics_after_task.get('evidence_count', 0)}, "
                            f"Facts: {metrics_before_task.get('facts_count', 0)} -> {metrics_after_task.get('facts_count', 0)}, "
                            f"Claims: {metrics_before_task.get('claim_count', 0)} -> {metrics_after_task.get('claim_count', 0)}"
                        )
                        failed_count += 1
                    task.metrics_after = metrics_after_task
                    task.new_evidence_ids = result.get("new_evidence_ids", [])
                    task.new_fact_ids = result.get("new_fact_ids", [])
                    task.new_facts = result.get("new_facts", [])
                    task.new_claim_ids = result.get("new_claim_ids", [])
                    task.new_claims = result.get("new_claims", [])
                else:
                    task.status = "failed"
                    task.error_message = result.get("error", "Unknown error")
                    task.metrics_after = result.get("after_metrics", {})
                    task.new_fact_ids = result.get("new_fact_ids", [])
                    task.new_facts = result.get("new_facts", [])
                    task.new_claim_ids = result.get("new_claim_ids", [])
                    task.new_claims = result.get("new_claims", [])
                    task.new_evidence_ids = result.get("new_evidence_ids", [])
                    failed_count += 1

            except Exception as exc:
                task.status = "failed"
                task.error_message = str(exc)
                failed_count += 1

            task.completed_at = now
            completed_tasks.append(task.to_dict())

        # Build after metrics
        after_metrics = {
            "rework_total_tasks": total_tasks,
            "rework_succeeded": succeeded_count,
            "rework_failed": failed_count,
            "rework_skipped": skipped_count,
            "rework_success_rate": succeeded_count / total_tasks if total_tasks > 0 else 0.0,
        }

        return completed_tasks, after_metrics

    def _attempt_local_supplementation(
        self,
        task: ReworkTask,
        sources: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        claim_drafts: list[dict[str, Any]],
        signed_claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Attempt to supplement facts/claims locally.

        For target_node == extract_facts:
        - Attempts to re-extract facts from existing evidence
        - Compares before/after fact_ids for the same product/schema_key
        - Succeeds only if new facts are produced

        For target_node == analyze_dimensions:
        - Uses template fallback to generate claims
        - Compares before/after for new claim_ids
        - Succeeds only if new claims are produced

        Returns:
            Dict with success status, after_metrics, and new item IDs
        """
        product_id = task.product_id
        schema_key = task.schema_key
        target_node = task.target_node

        # Build product/slug filter
        product_slug = product_id.lower().replace(" ", "-").replace("_", "-")

        # Count existing facts for this product/schema before rework
        existing_fact_ids_before = set()
        for f in facts:
            f_product = f.get("product_id", "") or f.get("product_slug", "")
            f_slug = f_product.lower().replace(" ", "-").replace("_", "-")
            f_key = f.get("schema_key", "")
            if (f_slug == product_slug or f_product == product_id) and f_key == schema_key:
                existing_fact_ids_before.add(f.get("fact_id", ""))

        # Find relevant evidence for this product/schema
        relevant_evidence = []
        for e in evidence_items:
            e_product = e.get("product_id", "") or e.get("product_slug", "")
            e_slug = e_product.lower().replace(" ", "-").replace("_", "-")
            e_key = e.get("schema_key", "")
            if (e_slug == product_slug or e_product == product_id) and e_key == schema_key:
                relevant_evidence.append(e)

        after_metrics = {
            "rework_id": task.rework_id,
            "schema_key": schema_key,
            "target_node": target_node,
            "existing_evidence_count": len(relevant_evidence),
            "existing_facts_count_before": len(existing_fact_ids_before),
            "facts_added": 0,
            "claims_added": 0,
            "evidence_count": len(evidence_items),
            "facts_count": len(facts),
            "claim_count": len(claim_drafts),
        }

        # Snapshot before state for comparison
        metrics_before = {
            "evidence_count": len(evidence_items),
            "facts_count": len(facts),
            "claim_count": len(claim_drafts),
            "existing_evidence_count": len(relevant_evidence),
            "existing_facts_count": len(existing_fact_ids_before),
        }

        if target_node == "extract_facts":
            # Try to re-extract facts from relevant evidence
            if not relevant_evidence:
                # Return special marker to indicate task was skipped
                # This is a data collection issue, not a rework failure
                logger.info(
                    "rework: no evidence for product_id=%s schema_key=%s - skipping (initial collection issue)",
                    product_id, schema_key,
                )
                return {
                    "skipped": True,
                    "reason": "No evidence for initial data collection",
                    "error": f"No evidence found for product_id={product_id} schema_key={schema_key}. "
                             f"This is an initial data collection issue, not a rework failure.",
                    "metrics_before": metrics_before,
                    "after_metrics": after_metrics,
                }

            # Attempt local fact extraction
            new_facts, new_fact_ids = self._try_fact_extraction(
                relevant_evidence, product_id, schema_key, existing_fact_ids_before, task.run_id
            )

            if new_fact_ids:
                after_metrics["facts_added"] = len(new_fact_ids)
                return {
                    "success": True,
                    "metrics_before": metrics_before,
                    "after_metrics": after_metrics,
                    "new_fact_ids": new_fact_ids,
                    "new_facts": new_facts,
                    "new_evidence_ids": [],
                    "new_claim_ids": [],
                    "new_claims": [],
                }
            else:
                # Return special marker to indicate task was skipped (not failed)
                # Fact extraction ran but produced no new facts - data quality issue
                return {
                    "skipped": True,
                    "reason": "No new facts from fact extraction",
                    "error": f"No new facts extracted for product_id={product_id} schema_key={schema_key}. "
                             f"Evidence exists but fact extraction did not produce new facts.",
                    "metrics_before": metrics_before,
                    "after_metrics": after_metrics,
                }

        elif target_node == "analyze_dimensions":
            # Try to generate new claims via template fallback
            new_claims, new_claim_ids = self._try_claim_generation(
                relevant_evidence, product_id, schema_key, claim_drafts, task.run_id
            )

            if new_claim_ids:
                after_metrics["claims_added"] = len(new_claim_ids)
                return {
                    "success": True,
                    "metrics_before": metrics_before,
                    "after_metrics": after_metrics,
                    "new_fact_ids": [],
                    "new_facts": [],
                    "new_evidence_ids": [],
                    "new_claim_ids": new_claim_ids,
                    "new_claims": new_claims,
                }
            else:
                return {
                    "success": False,
                    "error": f"No new claims generated for product_id={product_id} schema_key={schema_key}. "
                             f"Cannot produce new claims without relevant evidence.",
                    "metrics_before": metrics_before,
                    "after_metrics": after_metrics,
                    "new_fact_ids": [],
                    "new_facts": [],
                    "new_evidence_ids": [],
                    "new_claim_ids": [],
                    "new_claims": [],
                }

        else:
            return {
                "success": False,
                "error": f"Unsupported target_node: {target_node}. Only extract_facts and analyze_dimensions are supported.",
                "metrics_before": metrics_before,
                "after_metrics": after_metrics,
                "new_fact_ids": [],
                "new_facts": [],
                "new_evidence_ids": [],
                "new_claim_ids": [],
                "new_claims": [],
            }

    def _try_fact_extraction(
        self,
        evidence_items: list[dict[str, Any]],
        product_id: str,
        schema_key: str,
        existing_fact_ids: set[str],
        run_id: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Attempt to extract new facts from evidence items.

        Uses a simple rule-based extraction when LLM is not available.
        Returns tuple of (new_fact_dicts, new_fact_ids).
        """
        # Try LLM-based extraction first
        try:
            from backend.app.agents.collector.fact_extractor import FactExtractor
            extractor = FactExtractor()
            extracted_facts = extractor.extract_facts(evidence_items, run_id=run_id)

            product_slug = product_id.lower().replace(" ", "-").replace("_", "-")
            new_facts = []
            new_fact_ids = []
            for fact in extracted_facts:
                f_product = fact.get("product_id", "") or fact.get("product_slug", "")
                f_slug = f_product.lower().replace(" ", "-").replace("_", "-")
                f_key = fact.get("schema_key", "")

                if (f_slug == product_slug or f_product == product_id) and f_key == schema_key:
                    fact_id = fact.get("fact_id", "")
                    if fact_id and fact_id not in existing_fact_ids:
                        # Ensure run_id is set
                        fact["run_id"] = run_id
                        new_facts.append(fact)
                        new_fact_ids.append(fact_id)

            # If LLM produced results, return them; otherwise fall through to rule-based
            if new_fact_ids:
                return new_facts, new_fact_ids

        except Exception:
            pass  # Fall through to rule-based fallback

        # Rule-based extraction without LLM
        return self._rule_based_fact_extraction(evidence_items, product_id, schema_key, existing_fact_ids, run_id)

    def _rule_based_fact_extraction(
        self,
        evidence_items: list[dict[str, Any]],
        product_id: str,
        schema_key: str,
        existing_fact_ids: set[str],
        run_id: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Rule-based fact extraction without LLM.

        Generates minimal facts based on evidence snippets matching the schema key.
        Returns tuple of (new_fact_dicts, new_fact_ids).
        This always generates new synthetic facts - deduplication happens upstream.
        """
        new_facts = []
        new_fact_ids = []
        product_slug = product_id.lower().replace(" ", "-").replace("_", "-")
        created_at = datetime.now(timezone.utc).isoformat()

        for idx, ev in enumerate(evidence_items[:3]):  # Limit to 3 pieces of evidence
            snippet = ev.get("snippet", "") or ev.get("text", "")[:500]
            if not snippet:
                continue

            # Generate a synthetic fact_id (always new, deduplication happens upstream)
            fact_id = f"fact_{product_slug}_{schema_key}_{idx}"

            # Build a complete synthetic fact dict with proper run_id
            fact = {
                "fact_id": fact_id,
                "run_id": run_id,
                "product_id": product_id,
                "product_slug": product_slug,
                "schema_key": schema_key,
                "value_json": {"summary": snippet[:200]},
                "confidence": 0.65,
                "evidence_ids": [ev.get("evidence_id", "")],
                "created_at": created_at,
            }
            new_facts.append(fact)
            new_fact_ids.append(fact_id)

        return new_facts, new_fact_ids

    def _try_claim_generation(
        self,
        evidence_items: list[dict[str, Any]],
        product_id: str,
        schema_key: str,
        claim_drafts: list[dict[str, Any]],
        run_id: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Attempt to generate new claims from evidence.

        Uses template-based claim generation when LLM is not available.
        Returns tuple of (new_claim_dicts, new_claim_ids).
        """
        from backend.app.orchestrator.nodes import _template_claims_from_evidence

        # Filter evidence for this product/schema
        product_slug = product_id.lower().replace(" ", "-").replace("_", "-")
        filtered_evidence = []
        for ev in evidence_items:
            e_product = ev.get("product_id", "") or ev.get("product_slug", "")
            e_slug = e_product.lower().replace(" ", "-").replace("_", "-")
            e_key = ev.get("schema_key", "")
            if (e_slug == product_slug or e_product == product_id) and e_key == schema_key:
                filtered_evidence.append(ev)

        if not filtered_evidence:
            return [], []

        # Generate template claims with proper run_id
        try:
            new_claims = _template_claims_from_evidence(filtered_evidence, run_id=run_id)

            # Get existing claim IDs
            existing_claim_ids = {c.get("claim_id", "") for c in claim_drafts}

            # Filter to new claims and ensure run_id is set
            new_claims_filtered = []
            new_claim_ids = []
            for claim in new_claims:
                claim_id = claim.get("claim_id", "")
                if claim_id and claim_id not in existing_claim_ids:
                    # Ensure run_id is set on the claim
                    claim["run_id"] = run_id
                    new_claims_filtered.append(claim)
                    new_claim_ids.append(claim_id)

            return new_claims_filtered, new_claim_ids

        except Exception:
            return [], []

    def _schema_key_matches(self, source_key: str, target_key: str) -> bool:
        """Check if a source schema key matches the target schema key."""
        if not source_key or not target_key:
            return False

        source_lower = source_key.lower()
        target_lower = target_key.lower()

        if source_lower == target_lower:
            return True
        if target_lower in source_lower or source_lower in target_lower:
            return True

        source_parts = set(source_lower.replace("_", " ").split())
        target_parts = set(target_lower.replace("_", " ").split())
        return bool(source_parts & target_parts)

    def get_tasks_summary(self) -> dict[str, Any]:
        """Get summary of all rework tasks."""
        if not self._tasks:
            return {
                "total_tasks": 0,
                "pending": 0,
                "running": 0,
                "succeeded": 0,
                "failed": 0,
                "skipped": 0,
                "evidence_extraction_failed": 0,
            }

        return {
            "total_tasks": len(self._tasks),
            "pending": sum(1 for t in self._tasks if t.status == "pending"),
            "running": sum(1 for t in self._tasks if t.status == "running"),
            "succeeded": sum(1 for t in self._tasks if t.status == "succeeded"),
            "failed": sum(1 for t in self._tasks if t.status == "failed"),
            "skipped": sum(1 for t in self._tasks if t.status == "skipped"),
            "evidence_extraction_failed": sum(1 for t in self._tasks if t.status == "evidence_extraction_failed"),
        }

    def update_task_status(
        self,
        rework_id: str,
        status: str,
        error_message: str = "",
        after_metrics: dict[str, Any] | None = None,
    ) -> bool:
        """Update a specific task's status by rework_id."""
        for task in self._tasks:
            if task.rework_id == rework_id:
                task.status = status
                task.completed_at = datetime.now(timezone.utc).isoformat()
                if error_message:
                    task.error_message = error_message
                if after_metrics:
                    task.metrics_after = after_metrics
                return True
        return False


def create_rework_tasks(
    schema_gaps: list[dict[str, Any]],
    rework_requests: list[dict[str, Any]],
    claim_drafts: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]],
    run_id: str,
    sources: list[dict[str, Any]] | None = None,
    evidence_items: list[dict[str, Any]] | None = None,
    facts: list[dict[str, Any]] | None = None,
    max_tasks: int = 5,
) -> list[dict[str, Any]]:
    """
    Unified entry point to create rework tasks from both schema_gaps and rework_requests.

    Args:
        schema_gaps: High-priority schema gaps
        rework_requests: Reviewer rework requests
        claim_drafts: Current claim drafts (for metrics)
        signed_claims: Current signed claims (for metrics)
        run_id: Current run ID
        sources: Existing sources (for metrics)
        evidence_items: Existing evidence items (for metrics)
        facts: Existing facts (for metrics)
        max_tasks: Maximum total tasks to create

    Returns:
        List of rework task dicts
    """
    sources = sources or []
    evidence_items = evidence_items or []
    facts = facts or []

    # Snapshot metrics before creating tasks
    metrics_before = _snapshot_rework_metrics(
        schema_gaps,
        claim_drafts,
        signed_claims,
        evidence_items,
        facts,
        sources,
    )

    service = ReworkService()

    # Create tasks from schema gaps
    gap_tasks = service.create_rework_tasks_from_schema_gaps(
        schema_gaps=schema_gaps,
        run_id=run_id,
        metrics_before=metrics_before,
    )

    # Create tasks from reviewer requests
    reviewer_tasks = service.create_rework_tasks_from_reviewer_requests(
        rework_requests=rework_requests,
        run_id=run_id,
        metrics_before=metrics_before,
        claim_drafts=claim_drafts,
        signed_claims=signed_claims,
    )

    # Merge, respecting max_tasks limit
    all_tasks = gap_tasks + reviewer_tasks
    selected_tasks = all_tasks[:max_tasks]

    return [t.to_dict() for t in selected_tasks]


def create_and_execute_rework(
    schema_gaps: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    run_id: str,
    before_metrics: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """
    Legacy convenience function for backward compatibility.

    Creates and executes rework tasks. Prefer using create_rework_tasks + ReworkService.

    Args:
        schema_gaps: High-priority schema gaps
        sources: Existing sources
        evidence_items: Existing evidence items
        facts: Existing facts
        run_id: Current run ID
        before_metrics: Metrics before rework (for comparison)

    Returns:
        Tuple of (tasks, task_summary, after_metrics)
    """
    tasks = create_rework_tasks(
        schema_gaps=schema_gaps,
        rework_requests=[],
        claim_drafts=[],
        signed_claims=[],
        run_id=run_id,
        sources=sources,
        evidence_items=evidence_items,
        facts=facts,
    )

    service = ReworkService()
    service._tasks = []
    from backend.app.services.rework_service import ReworkTask
    for task_dict in tasks:
        task = ReworkTask(**{k: v for k, v in task_dict.items() if k in ReworkTask.__dataclass_fields__})
        service._tasks.append(task)

    completed_tasks, after_metrics = service.execute_rework_tasks(
        sources=sources,
        evidence_items=evidence_items,
        facts=facts,
        claim_drafts=[],
        signed_claims=[],
    )

    summary = service.get_tasks_summary()
    return completed_tasks, summary, after_metrics
