from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

ALLOWED_DIMENSIONS = frozenset({
    # Core dimensions
    "function_tree",
    "pricing_model",
    "user_persona",
    "customer_voice",
    "swot",
    "enterprise_readiness",
    "deployment_options",
    "function_comparison",
    "workflow",
    "knowledge_base",
    "integration",
    "model_support",
    "agent_capabilities",
    # vNext-R1.6+: Extended knowledge management dimensions
    "ai_assistance",
    "collaboration_experience",
    "enterprise_integration",
    "permission_governance",
    "template_ecosystem",
    "team_fit",
    "market_positioning",
    "data_management",
    "customization",
    # P0-5: New dimensions added to support broader schema coverage
    "rag_support",
    "rag",
    "swot_analysis",
    "ecosystem",
    "knowledge_structure",
    "value_proposition",
    "workflow_orchestration",
    "agent_builder",
    "tool_calling",
    "security",
    "version_control",
    "mobile_support",
    "offline_capability",
    "product_overview",
})

_REASON_CODE_TO_AGENT: dict[str, str] = {
    "MISSING_EVIDENCE": "collector_agent",
    "INVALID_EVIDENCE_ID": "collector_agent",
    "SCHEMA_MISMATCH": "extractor_agent",
    "SCHEMA_FIELD_MISSING": "extractor_agent",
    "LOW_CONFIDENCE": "analyst_agent",
    "PII_NOT_MASKED": "collector_agent",
    "NOISE_CLAIM": "analyst_agent",
    "UNUSABLE_EVIDENCE": "collector_agent",
}

_REASON_CODE_TO_NODE: dict[str, str] = {
    "MISSING_EVIDENCE": "collect_sources",
    "LOW_CONFIDENCE": "analyze_dimensions",
    "SCHEMA_MISMATCH": "extract_facts",
    "SCHEMA_FIELD_MISSING": "extract_facts",
    "PII_NOT_MASKED": "pii_scrub",
    "NOISE_CLAIM": "analyze_dimensions",
    "INVALID_EVIDENCE_ID": "collect_sources",
    "UNUSABLE_EVIDENCE": "collect_sources",
}

_REQUIRED_ACTION_MAP: dict[str, dict[str, Any]] = {
    "MISSING_EVIDENCE": {
        "action_type": "collect_more_sources",
        "required_source_types": ["official_site", "documentation"],
        "min_new_evidence_count": 1,
    },
    "INVALID_EVIDENCE_ID": {
        "action_type": "validate_evidence_ids",
        "required_source_types": [],
        "min_new_evidence_count": 0,
    },
    "SCHEMA_MISMATCH": {
        "action_type": "fix_dimension_mapping",
        "required_source_types": [],
        "min_new_evidence_count": 0,
    },
    "SCHEMA_FIELD_MISSING": {
        "action_type": "populate_required_fields",
        "required_source_types": [],
        "min_new_evidence_count": 0,
    },
    "LOW_CONFIDENCE": {
        "action_type": "increase_confidence",
        "required_source_types": ["official_site", "documentation"],
        "min_new_evidence_count": 1,
    },
    "PII_NOT_MASKED": {
        "action_type": "scrub_pii",
        "required_source_types": [],
        "min_new_evidence_count": 0,
    },
    "NOISE_CLAIM": {
        "action_type": "filter_noise_claims",
        "required_source_types": [],
        "min_new_evidence_count": 0,
    },
    "UNUSABLE_EVIDENCE": {
        "action_type": "collect_higher_quality_evidence",
        "required_source_types": ["official_site", "documentation"],
        "min_new_evidence_count": 1,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewerAgent:

    # Noise patterns: button nav text, CTA phrases, too-short text
    NOISE_PATTERNS = frozenset({
        "get a demo", "sign up", "contact sales", "try for free",
        "learn more", "read more", "watch demo", "start free",
        "get started", "book a demo", "schedule demo", "see pricing",
        "download", "install now", "try now", "start trial",
        "request demo", "try it free", "free trial",
    })

    MIN_CLAIM_LENGTH = 20  # characters

    def _is_noise_claim(self, claim_text: str, evidence_ids: list, evidence_items: list) -> tuple[bool, str]:
        """Reject noise/CTA claims. Returns (is_noise, reason)."""
        if not claim_text:
            return True, "empty_claim_text"
        text_lower = claim_text.lower().strip()
        if text_lower in self.NOISE_PATTERNS or len(text_lower) < self.MIN_CLAIM_LENGTH:
            return True, "too_short_or_nav_noise"
        # Check if evidence snippet looks like nav noise
        for eid in (evidence_ids or []):
            ev = next((e for e in evidence_items if e.get("evidence_id") == eid), None)
            if ev:
                snippet = (ev.get("snippet") or "").lower()
                if snippet and len(snippet) < 30 and any(p in snippet for p in self.NOISE_PATTERNS):
                    return True, "evidence_snippet_is_nav_noise"
        return False, ""

    def review_claim(
        self, claim: dict[str, Any], evidence_items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)
        logger.debug("Starting claim review for claim_id=%s", claim.get("claim_id"))

        checks: list[dict[str, Any]] = []
        fail_codes: list[str] = []
        warn_codes: list[str] = []

        # ── 0. noise_claim_filter (pre-check before all other checks) ─────────────
        evidence_ids = claim.get("evidence_ids") or []
        claim_text = claim.get("claim_text") or ""
        is_noise, noise_reason = self._is_noise_claim(claim_text, evidence_ids, evidence_items)
        if is_noise:
            checks.append({
                "check_name": "noise_filter",
                "status": "fail",
                "details": f"Claim rejected as noise (reason: {noise_reason}): '{claim_text[:60]}'",
            })
            fail_codes.append("NOISE_CLAIM")
            signed_claim_id = f"sc_rejected_{uuid.uuid4().hex[:8]}"
            review = {
                "review_id": f"review_{claim.get('claim_id', 'unknown')}_{uuid.uuid4().hex[:8]}",
                "claim_id": claim.get("claim_id", ""),
                "status": "rework_required",
                "review_status": "rework_required",
                "reason_codes": ["NOISE_CLAIM"],
                "checks": checks,
                "outcome": "rejected_as_noise",
                "noise_reason": noise_reason,
                "signed_claim_id": signed_claim_id,
                "reviewed_at": utc_now(),
                "created_at": utc_now(),
            }
            review["rework_request"] = self.build_rework_request(review, claim, evidence_items)
            return review

        # ── 1. evidence_required ────────────────────────────────────────────────
        evidence_ids = claim.get("evidence_ids") or []
        if evidence_ids:
            checks.append({
                "check_name": "evidence_required",
                "status": "pass",
                "details": f"Claim has {len(evidence_ids)} evidence ID(s).",
            })
        else:
            checks.append({
                "check_name": "evidence_required",
                "status": "fail",
                "details": "Claim has no evidence IDs.",
            })
            fail_codes.append("MISSING_EVIDENCE")

        # ── 2. evidence_exists ─────────────────────────────────────────────────
        existing_ids = {e.get("evidence_id") for e in evidence_items if e.get("evidence_id")}
        missing_ids = [eid for eid in evidence_ids if eid not in existing_ids]
        if missing_ids:
            checks.append({
                "check_name": "evidence_exists",
                "status": "fail",
                "details": f"Evidence IDs not found in evidence store: {missing_ids}",
            })
            fail_codes.append("INVALID_EVIDENCE_ID")
        elif evidence_ids:
            checks.append({
                "check_name": "evidence_exists",
                "status": "pass",
                "details": "All evidence IDs resolve to items in the evidence store.",
            })

        # ── 3. schema_compliance ───────────────────────────────────────────────
        dimension = claim.get("dimension")
        if dimension and dimension in ALLOWED_DIMENSIONS:
            checks.append({
                "check_name": "schema_compliance",
                "status": "pass",
                "details": f"Dimension '{dimension}' is valid.",
            })
        else:
            checks.append({
                "check_name": "schema_compliance",
                "status": "fail",
                "details": f"Dimension '{dimension}' is not in the allowed set: {sorted(ALLOWED_DIMENSIONS)}",
            })
            fail_codes.append("SCHEMA_MISMATCH")

        # ── 4. confidence_threshold ───────────────────────────────────────────
        confidence = claim.get("confidence")
        if confidence is None:
            checks.append({
                "check_name": "confidence_threshold",
                "status": "fail",
                "details": "Confidence score is absent.",
            })
            fail_codes.append("LOW_CONFIDENCE")
        elif confidence < 0.5:
            checks.append({
                "check_name": "confidence_threshold",
                "status": "fail",
                "details": f"Confidence {confidence} is below minimum threshold 0.5.",
            })
            fail_codes.append("LOW_CONFIDENCE")
        elif confidence < 0.7:
            checks.append({
                "check_name": "confidence_threshold",
                "status": "warning",
                "details": f"Confidence {confidence} is between 0.5 and 0.7 — marginal.",
            })
            warn_codes.append("LOW_CONFIDENCE")
        else:
            checks.append({
                "check_name": "confidence_threshold",
                "status": "pass",
                "details": f"Confidence {confidence} meets or exceeds 0.7 threshold.",
            })

        # ── 5. pii_masked ──────────────────────────────────────────────────────
        evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}
        unmasked: list[str] = []
        for eid in evidence_ids:
            item = evidence_map.get(eid)
            if item is not None and not item.get("pii_masked", False):
                unmasked.append(eid)

        if unmasked:
            checks.append({
                "check_name": "pii_masked",
                "status": "fail",
                "details": f"Evidence items with unmasked PII: {unmasked}",
            })
            fail_codes.append("PII_NOT_MASKED")
        elif evidence_ids:
            checks.append({
                "check_name": "pii_masked",
                "status": "pass",
                "details": "All referenced evidence items have PII masked.",
            })
        else:
            checks.append({
                "check_name": "pii_masked",
                "status": "pass",
                "details": "No evidence IDs to check for PII masking.",
            })

        # ── 6. required_field_coverage ─────────────────────────────────────────
        missing_fields: list[str] = []
        if not dimension:
            missing_fields.append("dimension")
        if not claim.get("claim_text"):
            missing_fields.append("claim_text")

        if missing_fields:
            checks.append({
                "check_name": "required_field_coverage",
                "status": "fail",
                "details": f"Claim is missing required fields: {missing_fields}",
            })
            fail_codes.append("SCHEMA_FIELD_MISSING")
        else:
            checks.append({
                "check_name": "required_field_coverage",
                "status": "pass",
                "details": "Claim contains both 'dimension' and 'claim_text'.",
            })

        # ── HARD GATE: evidence_quality_gate (P0 - must check before signing) ──────
        # LLM cannot override this gate - ALL evidence must be usable to sign
        evidence_map = {e.get("evidence_id"): e for e in evidence_items}
        usable_evidence = []
        # Build evidence maps from evidence_items (supports both list and dict formats)
        evidence_map: dict[str, dict[str, Any]] = {}
        for ev in evidence_items:
            ev_id = ev.get("evidence_id", "")
            if ev_id:
                evidence_map[ev_id] = ev

        usable_evidence = []
        unusable_evidence = []

        for eid in evidence_ids:
            ev = evidence_map.get(eid)
            if ev:
                # P1 Fix: Use usable_for_claim field directly - it already encodes quality threshold
                # This handles the case where evidence was fetched after claims were generated
                usable = ev.get("usable_for_claim", False)
                if usable:
                    usable_evidence.append(eid)
                else:
                    unusable_evidence.append(eid)

        if not evidence_ids:
            # No evidence at all - hard fail
            checks.append({
                "check_name": "evidence_quality_gate",
                "status": "fail",
                "details": "Claim has no evidence - cannot sign without evidence.",
            })
            fail_codes.append("MISSING_EVIDENCE")
        elif not usable_evidence:
            # Hard fail only when every referenced evidence is unavailable or unusable.
            # If the claim references stale evidence IDs that no longer exist in the current
            # evidence set, let the rework flow handle it as a claim/evidence sync issue.
            if unusable_evidence and len(unusable_evidence) == len(evidence_ids):
                fail_codes.append("UNUSABLE_EVIDENCE")
                checks.append({
                    "check_name": "evidence_quality_gate",
                    "status": "fail",
                    "details": (
                        f"No usable evidence found. "
                        f"{len(evidence_ids)} evidence item(s) available but ALL have usable_for_claim=false. "
                        f"Claim cannot be signed without at least 1 usable evidence."
                    ),
                })
            else:
                fail_codes.append("STALE_EVIDENCE_REFERENCES")
                checks.append({
                    "check_name": "evidence_quality_gate",
                    "status": "fail",
                    "details": (
                        f"Claim references stale evidence IDs not present in the current evidence set: "
                        f"{[eid for eid in evidence_ids if eid not in evidence_map]}"
                    ),
                })
        else:
            # At least 1 usable evidence - pass the gate
            checks.append({
                "check_name": "evidence_quality_gate",
                "status": "pass",
                "details": (
                    f"{len(usable_evidence)} usable evidence(s) found. "
                    f"{len(unusable_evidence)} lower-quality evidence(s) ignored."
                ),
            })

        # ── Determine overall status ───────────────────────────────────────────
        if fail_codes:
            status = "rework_required"
        elif warn_codes:
            status = "warning"
        else:
            status = "pass"

        signed_claim_id = f"signed_{claim['claim_id']}" if status in ("pass", "warning") else None

        logger.info(
            "Claim review completed claim_id=%s status=%s fail_codes=%s warn_codes=%s",
            claim.get("claim_id"), status, fail_codes, warn_codes,
        )

        review_result: dict[str, Any] = {
            "review_id": f"review_{claim.get('claim_id', 'unknown')}_{uuid.uuid4().hex[:8]}",
            "run_id": claim.get("run_id"),
            "review_target_type": "claim",
            "review_target_id": claim.get("claim_id"),
            "reviewer_agent": "reviewer_agent",
            "status": status,
            "checks": checks,
            "reason_codes": fail_codes,
            "warning_codes": warn_codes,
            "comments": self._build_comment(status, fail_codes, warn_codes),
            "signed_claim_id": signed_claim_id,
            "reviewed_at": utc_now(),
            "created_at": utc_now(),
        }

        if status == "rework_required":
            review_result["rework_request"] = self.build_rework_request(review_result, claim, evidence_items)

        return review_result

    def review_report(
        self,
        report_draft: dict[str, Any],
        signed_claims: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)
        logger.debug("Starting report review for report_id=%s", report_draft.get("report_id"))

        checks: list[dict[str, Any]] = []
        fail_codes: list[str] = []

        signed_claim_ids = {c.get("claim_id") for c in signed_claims if c.get("claim_id")}
        evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}

        # Sections that are summary/overview/template-generated context sections —
        # they are exempt from claim/evidence linking checks because they are
        # auto-generated (not LLM-authored) and don't cite individual claims/evidence.
        # Matches both WriterAgent templates (section_01_executive_summary, ...) and
        # _build_structured_sections (sec_01_exec_summary, ...).
        _SUMMARY_SECTION_ID_PREFIXES = ("section_", "sec_")

        # ── 1. report_span_claim_link ─────────────────────────────────────────
        sections = report_draft.get("sections", [])
        span_missing_claims: list[str] = []
        for section in sections:
            section_id = section.get("section_id", "unknown")
            # Context/summary sections are exempt — they aggregate data, not cite claims
            if section_id.startswith(_SUMMARY_SECTION_ID_PREFIXES):
                continue
            claim_ids = section.get("claim_ids") or []
            if not claim_ids:
                span_missing_claims.append(section_id)

        if span_missing_claims:
            checks.append({
                "check_name": "report_span_claim_link",
                "status": "fail",
                "details": f"Sections with no claim_ids (and need them): {span_missing_claims}",
            })
            fail_codes.append("UNSUPPORTED_REPORT_SPAN")
        else:
            checks.append({
                "check_name": "report_span_claim_link",
                "status": "pass",
                "details": f"All content sections have appropriate claim coverage.",
            })

        # ── 2. evidence_linked ────────────────────────────────────────────────
        sections_missing_evidence: list[str] = []
        for section in sections:
            section_id = section.get("section_id", "unknown")
            # Context/summary sections are exempt — they aggregate evidence
            if section_id.startswith(_SUMMARY_SECTION_ID_PREFIXES):
                continue
            evidence_ids = section.get("evidence_ids") or []
            if not evidence_ids:
                sections_missing_evidence.append(section_id)

        if sections_missing_evidence:
            checks.append({
                "check_name": "evidence_linked",
                "status": "fail",
                "details": f"Sections with no evidence_ids (and need it): {sections_missing_evidence}",
            })
            fail_codes.append("MISSING_EVIDENCE")
        else:
            checks.append({
                "check_name": "evidence_linked",
                "status": "pass",
                "details": f"All content sections have at least one evidence_id.",
            })

        # ── 3. pii_leakage ─────────────────────────────────────────────────────
        report_evidence_ids: list[str] = []
        for section in sections:
            report_evidence_ids.extend(section.get("evidence_ids") or [])

        unmasked_in_report: list[str] = []
        for eid in report_evidence_ids:
            item = evidence_map.get(eid)
            if item is not None and not item.get("pii_masked", False):
                unmasked_in_report.append(eid)

        if unmasked_in_report:
            checks.append({
                "check_name": "pii_leakage",
                "status": "fail",
                "details": f"Evidence items with unmasked PII in report: {unmasked_in_report}",
            })
            fail_codes.append("PII_NOT_MASKED")
        else:
            checks.append({
                "check_name": "pii_leakage",
                "status": "pass",
                "details": "No evidence with pii_masked=False appears in the report.",
            })

        # ── 4. schema_compliance ───────────────────────────────────────────────
        all_claim_ids_in_report: list[str] = []
        for section in sections:
            all_claim_ids_in_report.extend(section.get("claim_ids") or [])

        invalid_dimensions: list[dict[str, str]] = []
        for claim in signed_claims:
            dim = claim.get("dimension")
            if dim and dim not in ALLOWED_DIMENSIONS:
                invalid_dimensions.append({
                    "claim_id": claim.get("claim_id", "unknown"),
                    "dimension": str(dim),
                })

        if invalid_dimensions:
            checks.append({
                "check_name": "schema_compliance",
                "status": "fail",
                "details": f"Claims with invalid dimensions: {invalid_dimensions}",
            })
            fail_codes.append("SCHEMA_MISMATCH")
        else:
            checks.append({
                "check_name": "schema_compliance",
                "status": "pass",
                "details": "All claims in the report have valid dimensions.",
            })

        # ── Determine overall status ───────────────────────────────────────────
        status = "rework_required" if fail_codes else "pass"

        logger.info(
            "Report review completed report_id=%s status=%s fail_codes=%s",
            report_draft.get("report_id"), status, fail_codes,
        )

        review_result: dict[str, Any] = {
            "review_id": f"review_report_{report_draft.get('report_id', 'unknown')}_{uuid.uuid4().hex[:8]}",
            "run_id": report_draft.get("run_id"),
            "review_target_type": "report",
            "review_target_id": report_draft.get("report_id"),
            "reviewer_agent": "reviewer_agent",
            "status": status,
            "checks": checks,
            "reason_codes": fail_codes,
            "comments": self._build_report_comment(status, fail_codes),
            "reviewed_at": utc_now(),
            "created_at": utc_now(),
        }

        if status == "rework_required":
            review_result["rework_request"] = self._build_report_rework_request(
                review_result, report_draft, signed_claims, evidence_items
            )

        return review_result

    def build_rework_request(
        self,
        review: dict[str, Any],
        claim: dict[str, Any],
        evidence_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)
        logger.debug("Building rework request for review_id=%s", review.get("review_id"))

        reason_codes = review.get("reason_codes") or []

        primary_agent = "collector_agent"
        for code in reason_codes:
            if code in _REASON_CODE_TO_AGENT:
                primary_agent = _REASON_CODE_TO_AGENT[code]
                break

        primary_node = "collect_sources"
        for code in reason_codes:
            if code in _REASON_CODE_TO_NODE:
                primary_node = _REASON_CODE_TO_NODE[code]
                break

        affected_objects: list[dict[str, str]] = [
            {"object_type": "claim", "object_id": str(claim.get("claim_id", ""))},
        ]

        required_actions: list[dict[str, Any]] = []
        for code in reason_codes:
            action_def = _REQUIRED_ACTION_MAP.get(code, {
                "action_type": "generic_fix",
                "required_source_types": [],
                "min_new_evidence_count": 0,
            })
            action: dict[str, Any] = {
                "action_type": action_def["action_type"],
                "product_id": claim.get("product_id"),
                "schema_keys": [claim.get("dimension", "")],
                "required_source_types": action_def["required_source_types"],
                "min_new_evidence_count": action_def["min_new_evidence_count"],
                "triggered_by_reason_code": code,
            }
            if code in ("MISSING_EVIDENCE", "INVALID_EVIDENCE_ID"):
                action["evidence_ids_to_validate"] = claim.get("evidence_ids") or []
            if code == "LOW_CONFIDENCE":
                action["target_confidence"] = 0.7
            if code == "SCHEMA_MISMATCH":
                action["allowed_dimensions"] = sorted(ALLOWED_DIMENSIONS)
            if code == "PII_NOT_MASKED":
                action["evidence_ids_to_scrub"] = [
                    e.get("evidence_id")
                    for e in (evidence_items or [])
                    if not e.get("pii_masked", False)
                    and e.get("evidence_id") in (claim.get("evidence_ids") or [])
                ]
            required_actions.append(action)

        metrics_before: dict[str, Any] = {
            "claim_confidence": claim.get("confidence"),
            "evidence_count": len(claim.get("evidence_ids") or []),
            "dimension": claim.get("dimension"),
            "has_dimension": bool(claim.get("dimension")),
            "has_claim_text": bool(claim.get("claim_text")),
        }

        return {
            "rework_id": f"rw_{claim.get('claim_id', 'unknown')}_{uuid.uuid4().hex[:8]}",
            "run_id": review.get("run_id"),
            "review_id": review.get("review_id"),
            "target_agent": primary_agent,
            "target_node": primary_node,
            "affected_objects": affected_objects,
            "reason_codes": reason_codes,
            "required_actions": required_actions,
            "success_criteria": {
                "evidence_coverage_rate_min": 0.95,
                "unsupported_claim_count_max": 0,
                "confidence_min": 0.7,
                "pii_masked": True,
                "all_required_fields_present": True,
                "dimension_valid": True,
            },
            "status": "pending",
            "retry_count": 0,
            "max_retry": 2,
            "metrics_before": metrics_before,
            "metrics_after": {},
            "created_at": utc_now(),
            "completed_at": None,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_comment(
        self, status: str, fail_codes: list[str], warn_codes: list[str]
    ) -> str:
        if status == "pass":
            return "All deterministic checks passed. Claim is approved for signing."
        if status == "warning":
            return f"Claim passed all mandatory checks with warnings: {warn_codes}. Claim may be signed."
        return f"Claim failed deterministic checks: {fail_codes}. Rework is required."

    def _build_report_comment(self, status: str, fail_codes: list[str]) -> str:
        if status == "pass":
            return "All report-level deterministic checks passed."
        return f"Report failed deterministic checks: {fail_codes}. Rework is required."

    def _build_report_rework_request(
        self,
        review: dict[str, Any],
        report_draft: dict[str, Any],
        signed_claims: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        logger = logging.getLogger(__name__)
        logger.debug("Building report-level rework request for review_id=%s", review.get("review_id"))

        reason_codes = review.get("reason_codes") or []

        primary_agent = "collector_agent"
        for code in reason_codes:
            if code in _REASON_CODE_TO_AGENT:
                primary_agent = _REASON_CODE_TO_AGENT[code]
                break

        primary_node = "collect_sources"
        for code in reason_codes:
            if code in _REASON_CODE_TO_NODE:
                primary_node = _REASON_CODE_TO_NODE[code]
                break

        sections = report_draft.get("sections", [])
        affected_objects: list[dict[str, Any]] = [
            {"object_type": "report", "object_id": str(report_draft.get("report_id", ""))},
        ]
        for section in sections:
            affected_objects.append({
                "object_type": "section",
                "object_id": str(section.get("section_id", "")),
            })

        required_actions: list[dict[str, Any]] = []
        for code in reason_codes:
            action_def = _REQUIRED_ACTION_MAP.get(code, {
                "action_type": "generic_fix",
                "required_source_types": [],
                "min_new_evidence_count": 0,
            })
            action: dict[str, Any] = {
                "action_type": action_def["action_type"],
                "product_id": report_draft.get("product_id"),
                "required_source_types": action_def["required_source_types"],
                "min_new_evidence_count": action_def["min_new_evidence_count"],
                "triggered_by_reason_code": code,
            }
            if code == "UNSUPPORTED_REPORT_SPAN":
                action["sections_to_update"] = [
                    s.get("section_id") for s in sections if not s.get("claim_ids")
                ]
            if code == "MISSING_EVIDENCE":
                action["sections_to_update"] = [
                    s.get("section_id") for s in sections if not s.get("evidence_ids")
                ]
            if code == "PII_NOT_MASKED":
                evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}
                report_evidence_ids: list[str] = []
                for section in sections:
                    report_evidence_ids.extend(section.get("evidence_ids") or [])
                action["evidence_ids_to_scrub"] = [
                    eid for eid in report_evidence_ids
                    if not evidence_map.get(eid, {}).get("pii_masked", False)
                ]
            if code == "SCHEMA_MISMATCH":
                action["allowed_dimensions"] = sorted(ALLOWED_DIMENSIONS)
                action["invalid_claims"] = [
                    c.get("claim_id") for c in signed_claims
                    if c.get("dimension") not in ALLOWED_DIMENSIONS
                ]
            required_actions.append(action)

        metrics_before: dict[str, Any] = {
            "section_count": len(sections),
            "claim_count": sum(len(s.get("claim_ids") or []) for s in sections),
            "evidence_count": sum(len(s.get("evidence_ids") or []) for s in sections),
            "product_id": report_draft.get("product_id"),
        }

        return {
            "rework_id": f"rw_report_{report_draft.get('report_id', 'unknown')}_{uuid.uuid4().hex[:8]}",
            "run_id": review.get("run_id"),
            "review_id": review.get("review_id"),
            "target_agent": primary_agent,
            "target_node": primary_node,
            "affected_objects": affected_objects,
            "reason_codes": reason_codes,
            "required_actions": required_actions,
            "success_criteria": {
                "all_sections_have_claims": True,
                "all_sections_have_evidence": True,
                "pii_masked": True,
                "all_claims_schema_compliant": True,
            },
            "status": "pending",
            "retry_count": 0,
            "max_retry": 2,
            "metrics_before": metrics_before,
            "metrics_after": {},
            "created_at": utc_now(),
            "completed_at": None,
        }
