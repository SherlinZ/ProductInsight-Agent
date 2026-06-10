"""
Workflow Graph and Human Intervention API routes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from backend.app.storage.repositories import (
    WorkflowRepository,
    HumanInterventionRepository,
    ReworkTaskRepository,
)


router = APIRouter(prefix="/api", tags=["workflow"])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ApproveRequest(BaseModel):
    comment: str = ""
    resolved_by: str = "human_user"


class RejectRequest(BaseModel):
    comment: str = ""
    resolved_by: str = "human_user"


class EditRequest(BaseModel):
    after_json: dict[str, Any] = Field(default_factory=dict)
    comment: str = ""
    resolved_by: str = "human_user"


class RespondRequest(BaseModel):
    after_json: dict[str, Any] = Field(default_factory=dict)
    comment: str = ""
    resolved_by: str = "human_user"


# ---------------------------------------------------------------------------
# Workflow Graph APIs
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/workflow/nodes")
def get_workflow_nodes(run_id: str) -> list[dict[str, Any]]:
    """Get all workflow nodes for a run, sorted by backbone order."""
    repo = WorkflowRepository()
    try:
        nodes = repo.list_workflow_nodes(run_id)
        return nodes
    except Exception:
        return []


@router.get("/runs/{run_id}/workflow/edges")
def get_workflow_edges(run_id: str) -> list[dict[str, Any]]:
    """Get all workflow edges for a run."""
    repo = WorkflowRepository()
    try:
        edges = repo.list_workflow_edges(run_id)
        return edges
    except Exception:
        return []


@router.get("/runs/{run_id}/workflow")
def get_workflow(run_id: str) -> dict[str, Any]:
    """Get combined workflow graph with nodes, edges, and summary."""
    repo = WorkflowRepository()
    hi_repo = HumanInterventionRepository()

    try:
        nodes = repo.list_workflow_nodes(run_id)
    except Exception:
        nodes = []

    try:
        edges = repo.list_workflow_edges(run_id)
    except Exception:
        edges = []

    # Compute summary from nodes
    total = len(nodes)
    completed = sum(1 for n in nodes if n.get("status") == "completed")
    running = sum(1 for n in nodes if n.get("status") == "running")
    paused = sum(1 for n in nodes if n.get("status") == "paused")
    failed = sum(1 for n in nodes if n.get("status") == "failed")
    pending = sum(1 for n in nodes if n.get("status") == "pending")

    # Check for pending human interventions
    has_pending_interventions = False
    try:
        pending_interventions = hi_repo.list_interventions(run_id, status="pending")
        has_pending_interventions = len(pending_interventions) > 0
    except Exception:
        pass

    # has_human_review if there are paused nodes or pending interventions
    has_human_review = paused > 0 or has_pending_interventions

    return {
        "run_id": run_id,
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "total_nodes": total,
            "completed": completed,
            "running": running,
            "paused": paused,
            "failed": failed,
            "pending": pending,
            "has_human_review": has_human_review,
        },
    }


@router.get("/runs/{run_id}/dag")
def get_dag_data(run_id: str) -> dict[str, Any]:
    """Return DAG graph data (nodes + edges) for the ReactFlow frontend component.

    This is a dedicated endpoint for the DAG visualization. It returns the same
    workflow nodes and edges as /workflow, allowing the ReactFlow app to render
    the live DAG with pre-computed layout positions handled client-side.
    """
    repo = WorkflowRepository()
    try:
        nodes = repo.list_workflow_nodes(run_id)
    except Exception:
        nodes = []

    try:
        edges = repo.list_workflow_edges(run_id)
    except Exception:
        edges = []

    # Enrich collect_sources node with collection_stats and sources
    try:
        from backend.app.storage.repositories import SourceRepository
        all_sources = SourceRepository().list_sources(run_id)
        if all_sources and nodes:
            total_urls = len(all_sources)
            collected = sum(1 for s in all_sources if s.get("status") == "collected")
            failed = sum(1 for s in all_sources if s.get("status") == "failed")
            skipped = total_urls - collected - failed
            total_chars = sum(s.get("char_count", 0) or 0 for s in all_sources)

            collection_stats = {
                "total_urls": total_urls,
                "collected": collected,
                "failed": failed,
                "skipped": skipped,
                "elapsed_s": 0,
                "total_timeout_s": 900,
                "total_chars": total_chars,
            }

            workflow_sources = [
                {
                    "source_id": s.get("source_id"),
                    "product_id": s.get("product_id"),
                    "url": s.get("url"),
                    "fetch_level": s.get("fetch_level") or 0,
                    "fetch_strategy": s.get("fetch_strategy") or "",
                    "status": s.get("status"),
                    "error_message": s.get("error_message"),
                    "char_count": s.get("char_count") or 0,
                }
                for s in all_sources
            ]

            for node in nodes:
                if node.get("node_name") == "collect_sources":
                    node["collection_stats"] = collection_stats
                    node["sources"] = workflow_sources
                    break
    except Exception:
        pass  # Enrichment is best-effort

    return {
        "run_id": run_id,
        "nodes": nodes,
        "edges": edges,
    }


# ---------------------------------------------------------------------------
# Human Intervention APIs
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/human-interventions")
def get_run_interventions(
    run_id: str,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get human interventions for a run, optionally filtered by status."""
    repo = HumanInterventionRepository()
    try:
        return repo.list_interventions(run_id, status=status)
    except Exception:
        return []


@router.get("/human-interventions/{intervention_id}")
def get_intervention(intervention_id: str) -> dict[str, Any]:
    """Get a specific human intervention by ID."""
    repo = HumanInterventionRepository()
    intervention = repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")
    return intervention


@router.post("/human-interventions/{intervention_id}/approve")
def approve_intervention(
    intervention_id: str,
    request: ApproveRequest,
) -> dict[str, Any]:
    """Approve a human intervention and resume workflow if all interventions are resolved."""
    repo = HumanInterventionRepository()
    intervention = repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")

    try:
        resolved = repo.resolve_intervention(
            intervention_id=intervention_id,
            action="approve",
            comment=request.comment,
            resolved_by=request.resolved_by,
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="Intervention not found")

        # Check if all interventions for this run are resolved
        run_id = intervention["run_id"]
        all_intervs = repo.list_interventions(run_id)
        pending_intervs = [i for i in all_intervs if i.get("status") == "pending"]
        if not pending_intervs:
            # All interventions resolved - trigger replay to resume workflow
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"All interventions resolved for run {run_id}, triggering replay")
            try:
                from backend.app.api.runs import replay_run
                replay_run(run_id)
            except Exception as e:
                _logger.error(f"Failed to trigger replay: {e}")

        return resolved
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/human-interventions/{intervention_id}/reject")
def reject_intervention(
    intervention_id: str,
    request: RejectRequest,
) -> dict[str, Any]:
    """Reject a human intervention and resume workflow if all interventions are resolved."""
    repo = HumanInterventionRepository()
    intervention = repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")

    try:
        resolved = repo.resolve_intervention(
            intervention_id=intervention_id,
            action="reject",
            comment=request.comment,
            resolved_by=request.resolved_by,
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="Intervention not found")

        # Check if all interventions for this run are resolved
        run_id = intervention["run_id"]
        all_intervs = repo.list_interventions(run_id)
        pending_intervs = [i for i in all_intervs if i.get("status") == "pending"]
        if not pending_intervs:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"All interventions resolved for run {run_id}, triggering replay")
            try:
                from backend.app.api.runs import replay_run
                replay_run(run_id)
            except Exception as e:
                _logger.error(f"Failed to trigger replay: {e}")

        return resolved
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/human-interventions/{intervention_id}/edit")
def edit_intervention(
    intervention_id: str,
    request: EditRequest,
) -> dict[str, Any]:
    """Edit and resolve a human intervention, then resume workflow if all interventions are resolved."""
    repo = HumanInterventionRepository()
    intervention = repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")

    try:
        resolved = repo.resolve_intervention(
            intervention_id=intervention_id,
            action="edit",
            after_json=request.after_json,
            comment=request.comment,
            resolved_by=request.resolved_by,
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="Intervention not found")

        # Check if all interventions for this run are resolved
        run_id = intervention["run_id"]
        all_intervs = repo.list_interventions(run_id)
        pending_intervs = [i for i in all_intervs if i.get("status") == "pending"]
        if not pending_intervs:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"All interventions resolved for run {run_id}, triggering replay")
            try:
                from backend.app.api.runs import replay_run
                replay_run(run_id)
            except Exception as e:
                _logger.error(f"Failed to trigger replay: {e}")

        return resolved
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/human-interventions/{intervention_id}/respond")
def respond_intervention(
    intervention_id: str,
    request: RespondRequest,
) -> dict[str, Any]:
    """Respond to a human intervention and resume workflow if all interventions are resolved."""
    repo = HumanInterventionRepository()
    intervention = repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")

    try:
        resolved = repo.resolve_intervention(
            intervention_id=intervention_id,
            action="respond",
            after_json=request.after_json,
            comment=request.comment,
            resolved_by=request.resolved_by,
        )
        if not resolved:
            raise HTTPException(status_code=404, detail="Intervention not found")

        # Check if all interventions for this run are resolved
        run_id = intervention["run_id"]
        all_intervs = repo.list_interventions(run_id)
        pending_intervs = [i for i in all_intervs if i.get("status") == "pending"]
        if not pending_intervs:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"All interventions resolved for run {run_id}, triggering replay")
            try:
                from backend.app.api.runs import replay_run
                replay_run(run_id)
            except Exception as e:
                _logger.error(f"Failed to trigger replay: {e}")

        return resolved
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Rework Task APIs
# ---------------------------------------------------------------------------


class RequestReworkRequest(BaseModel):
    comment: str = "Request rework from Review Center"
    requested_by: str = "frontend_user"


def _parse_reason_codes_from_intervention(intervention: dict[str, Any]) -> list[str]:
    """Extract reason codes from intervention comment and before_json."""
    codes = []
    import re

    comment = intervention.get("comment") or ""
    for m in re.finditer(r"\[(\w+)\]", comment):
        codes.append(m.group(1))

    before = intervention.get("before_json") or {}
    if isinstance(before, dict):
        bj_codes = before.get("reason_codes", [])
        if isinstance(bj_codes, list):
            for c in bj_codes:
                if c not in codes:
                    codes.append(c)

    return codes if codes else ["MISSING_EVIDENCE"]


def _build_rework_plan(reason_codes: list[str], intervention: dict[str, Any]) -> dict[str, Any]:
    """Build a rework_plan_json from reason codes."""
    action_map = {
        "MISSING_EVIDENCE": ("collect_missing_evidence", "Find or attach evidence for unsupported claims and report spans."),
        "UNSUPPORTED_REPORT_SPAN": ("relink_report_spans", "Link report paragraphs to signed claims and supporting evidence."),
        "BLOCKED_NO_SIGNED_CLAIMS": ("sign_pending_claims", "Review and sign pending claims so the report can proceed."),
        "PII_NOT_MASKED": ("mask_pii", "Identify and mask personally identifiable information in collected evidence."),
    }
    steps = []
    for i, code in enumerate(reason_codes):
        action, desc = action_map.get(code, ("general_rework", "Address the identified quality gate issue."))
        steps.append({
            "step": i + 1,
            "action": action,
            "reason": code,
            "description": desc,
        })
    steps.append({
        "step": len(steps) + 1,
        "action": "rerun_review",
        "reason": "QUALITY_GATE",
        "description": "Re-run claim and report review after repair.",
    })
    return {
        "goal": "Repair blocked report by addressing quality gate findings.",
        "reason_codes": reason_codes,
        "steps": steps,
        "auto_execution": False,
        "requires_human_confirmation": True,
    }


@router.post("/human-interventions/{intervention_id}/request-rework")
def request_rework(
    intervention_id: str,
    request: RequestReworkRequest,
) -> dict[str, Any]:
    """Create a rework task from a pending human intervention."""
    hi_repo = HumanInterventionRepository()
    intervention = hi_repo.get_intervention(intervention_id)
    if not intervention:
        raise HTTPException(status_code=404, detail="Intervention not found")

    if intervention.get("status") != "pending":
        raise HTTPException(status_code=400, detail="Only pending interventions can request rework")

    existing_after = intervention.get("after_json") or {}
    if isinstance(existing_after, str):
        try:
            import json
            existing_after = json.loads(existing_after)
        except Exception:
            existing_after = {}

    # Prevent duplicate: if rework was already requested, return existing task info
    if existing_after.get("rework_requested") and existing_after.get("rework_id"):
        existing_rework_id = existing_after["rework_id"]
        rt_repo = ReworkTaskRepository()
        existing_task = rt_repo.get_rework_task(existing_rework_id)
        return {
            "rework_id": existing_rework_id,
            "intervention_id": intervention_id,
            "status": existing_task.get("status", "pending") if existing_task else "pending",
            "reason_codes": existing_task.get("reason_codes", []) if existing_task else [],
            "rework_plan_json": existing_task.get("rework_plan_json") if existing_task else None,
            "created_at": existing_task.get("created_at", "") if existing_task else "",
        }

    # Also check rework_tasks table directly as a fallback
    rt_repo = ReworkTaskRepository()
    existing_tasks = rt_repo.list_rework_tasks(intervention.get("run_id", ""))
    for t in existing_tasks:
        if t.get("intervention_id") == intervention_id:
            return {
                "rework_id": t["rework_id"],
                "intervention_id": intervention_id,
                "status": t.get("status", "pending"),
                "reason_codes": t.get("reason_codes", []),
                "rework_plan_json": t.get("rework_plan_json"),
                "created_at": t.get("created_at", ""),
            }

    import uuid as _uuid
    rework_id = f"rework_{intervention_id}_{_uuid.uuid4().hex[:8]}"

    reason_codes = _parse_reason_codes_from_intervention(intervention)
    rework_plan = _build_rework_plan(reason_codes, intervention)

    task = {
        "rework_id": rework_id,
        "intervention_id": intervention_id,
        "run_id": intervention.get("run_id", ""),
        "project_id": intervention.get("project_id"),
        "source_node": intervention.get("node_name"),
        "target_artifact_type": intervention.get("artifact_type"),
        "target_artifact_id": intervention.get("artifact_id"),
        "reason_codes": reason_codes,
        "status": "pending",
        "rework_plan_json": rework_plan,
        "before_json": intervention.get("before_json"),
        "created_at": utc_now(),
        "created_by": request.requested_by,
    }

    rt_repo.create_rework_task(task)

    now_str = utc_now()
    updated_after = dict(existing_after)
    updated_after.update({
        "rework_requested": True,
        "rework_id": rework_id,
        "requested_by": request.requested_by,
        "requested_at": now_str,
        "note": "Rework task created. Automatic re-run will be implemented in next phase.",
    })
    hi_repo._update_after_json(intervention_id, updated_after)

    return {
        "rework_id": rework_id,
        "intervention_id": intervention_id,
        "status": "pending",
        "reason_codes": reason_codes,
        "rework_plan_json": rework_plan,
        "created_at": now_str,
    }


@router.get("/runs/{run_id}/rework-tasks")
def get_run_rework_tasks(run_id: str) -> list[dict[str, Any]]:
    """Get all rework tasks for a run."""
    repo = ReworkTaskRepository()
    try:
        return repo.list_rework_tasks(run_id)
    except Exception:
        return []


@router.get("/rework-tasks/{rework_id}")
def get_rework_task(rework_id: str) -> dict[str, Any]:
    """Get a single rework task by rework_id."""
    repo = ReworkTaskRepository()
    task = repo.get_rework_task(rework_id)
    if not task:
        raise HTTPException(status_code=404, detail="Rework task not found")
    return task


@router.post("/rework-tasks/{rework_id}/simulate-fix")
def simulate_rework_fix(rework_id: str) -> dict[str, Any]:
    """Simulate completing a rework task: mark status=completed and write after_json."""
    repo = ReworkTaskRepository()
    task = repo.get_rework_task(rework_id)
    if not task:
        raise HTTPException(status_code=404, detail="Rework task not found")

    if task.get("status") in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot apply fix to a task with status '{task['status']}'")

    reason_codes = task.get("reason_codes", [])
    changes = []
    if "UNSUPPORTED_REPORT_SPAN" in reason_codes:
        changes.append("Marked unsupported report spans for relinking")
    if "MISSING_EVIDENCE" in reason_codes:
        changes.append("Prepared missing evidence collection plan")
    if "BLOCKED_NO_SIGNED_CLAIMS" in reason_codes:
        changes.append("Identified pending claims for signing")
    if "PII_NOT_MASKED" in reason_codes:
        changes.append("Identified PII regions for masking")
    if not changes:
        changes.append("Reviewed and validated artifact content")
    changes.append("Queued review rerun")

    after_json = {
        "simulated_fix": True,
        "fixed_reason_codes": reason_codes,
        "changes": changes,
        "metrics_after": {
            "rework_completed": True,
        },
    }

    repo.update_rework_task(rework_id, status="completed", after_json=after_json)
    updated = repo.get_rework_task(rework_id)
    return updated


_REASON_CODE_BEFORE_AFTER = {
    "UNSUPPORTED_REPORT_SPAN": {
        "before": "Report spans were not sufficiently supported by signed claims and evidence.",
        "after": "Unsupported report spans were marked for relinking to signed claims and evidence.",
    },
    "MISSING_EVIDENCE": {
        "before": "Evidence was missing for some claims or report spans.",
        "after": "Missing evidence collection was prepared for affected claims.",
    },
    "BLOCKED_NO_SIGNED_CLAIMS": {
        "before": "No signed claims were available; report could not proceed.",
        "after": "Pending claims were identified for signing to unblock the report.",
    },
    "PII_NOT_MASKED": {
        "before": "Personally identifiable information was detected in collected evidence.",
        "after": "PII regions were identified and prepared for masking.",
    },
}


@router.post("/rework-tasks/{rework_id}/simulate-review-rerun")
def simulate_review_rerun(rework_id: str) -> dict[str, Any]:
    """Simulate a review rerun after a completed rework task."""
    repo = ReworkTaskRepository()
    task = repo.get_rework_task(rework_id)
    if not task:
        raise HTTPException(status_code=404, detail="Rework task not found")

    if task.get("status") != "completed":
        raise HTTPException(
            status_code=400,
            detail="Rework task must be completed before review rerun.",
        )

    reason_codes = task.get("reason_codes", [])
    existing_after = task.get("after_json") or {}

    before_after_summary = []
    for rc in reason_codes:
        entry = _REASON_CODE_BEFORE_AFTER.get(rc, {
            "before": f"Quality gate issue: [{rc}]",
            "after": "Issue addressed by rework plan.",
        })
        before_after_summary.append(entry)

    appended = {
        "review_rerun_simulated": True,
        "review_rerun_at": utc_now(),
        "quality_gate_before": {
            "status": "blocked",
            "reason_codes": reason_codes,
        },
        "quality_gate_after": {
            "status": "ready_for_review",
            "remaining_issues": [],
            "message": "Simulated review indicates the rework plan addresses the known quality gate findings.",
        },
        "before_after_summary": before_after_summary,
        "recommended_next_action": "Regenerate report or request human approval after evidence relinking.",
    }

    # Merge into existing after_json (don't clobber simulated_fix or fixed_reason_codes)
    merged = dict(existing_after)
    merged.update(appended)

    repo.update_rework_task(rework_id, after_json=merged)
    return repo.get_rework_task(rework_id)


# ---------------------------------------------------------------------------
# Coverage Gap Rework APIs
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/coverage-gaps")
def create_coverage_gap_tasks(run_id: str) -> dict[str, Any]:
    """Generate coverage gap rework tasks from a run's quality_summary.

    Reads the latest report's quality_summary.product_coverage_summary and creates
    rework tasks for products with coverage_status == "insufficient" or "partial".
    """
    import uuid as _uuid

    from backend.app.storage.repositories import ReportRepository, RunRepository

    # Get run info
    run_repo = RunRepository()
    run = run_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    project_id = run.get("project_id")

    # Get latest report
    report_repo = ReportRepository()
    report = report_repo.get_latest_report(run_id)
    if not report:
        raise HTTPException(status_code=404, detail="No report found for this run")

    quality_summary = report.get("quality_summary") or {}
    pcs = quality_summary.get("product_coverage_summary") or {}

    if not pcs:
        return {
            "run_id": run_id,
            "created_tasks": [],
            "message": "No product_coverage_summary found in report.",
        }

    # Build metrics snapshot for all products
    metrics_before = {
        "product_coverage_summary": pcs,
        "insufficient_products": quality_summary.get("insufficient_products", 0),
        "partial_products": quality_summary.get("partial_products", 0),
        "sufficient_products": quality_summary.get("sufficient_products", 0),
        "evidence_coverage_rate": quality_summary.get("evidence_coverage_rate", 0.0),
        "claim_count": quality_summary.get("claim_count", 0),
        "signed_claims": quality_summary.get("signed_claims", 0),
        "report_status": report.get("report_status", "unknown"),
    }

    created_tasks = []
    existing_tasks = []
    skipped_tasks = []
    rt_repo = ReworkTaskRepository()

    # Check for existing coverage gap tasks to ensure idempotency
    existing = rt_repo.list_rework_tasks(run_id)
    existing_by_product = {}
    for t in existing:
        pid = t.get("product_id", "")
        if pid and t.get("product_id"):
            existing_by_product[pid.lower()] = t

    for slug, cov in pcs.items():
        status = cov.get("coverage_status", "sufficient")
        if status == "sufficient":
            continue

        product_id = cov.get("product_id", slug)
        product_name = cov.get("product_name", slug.title())

        if status == "insufficient":
            reason_code = "INSUFFICIENT_PRODUCT_COVERAGE"
            target_node = "collect_sources"
            required_action = "add_seed_urls_and_collect_evidence"
        else:  # partial
            reason_code = "PARTIAL_PRODUCT_COVERAGE"
            target_node = "extract_facts"
            required_action = "collect_additional_evidence_for_missing_dimensions"

        # Idempotency: skip if a planned/pending/running task already exists for this product
        key = product_id.lower()
        if key in existing_by_product:
            existing_task = existing_by_product[key]
            if existing_task.get("status") in ("planned", "pending", "running"):
                logger.info(
                    "create_coverage_gap_tasks: skipping %s (existing task %s status=%s)",
                    product_id, existing_task["rework_id"], existing_task["status"],
                )
                skipped_tasks.append({
                    "rework_id": existing_task["rework_id"],
                    "product_id": product_id,
                    "product_name": product_name,
                    "status": existing_task["status"],
                    "note": "Already exists",
                })
                continue
            # If status is completed/failed/cancelled, allow creating a new one

        rework_id = f"rework_cov_{product_id}_{_uuid.uuid4().hex[:8]}"

        # Build before_json with product-specific metrics
        product_metrics_before = {
            "product_id": product_id,
            "product_name": product_name,
            "coverage_status": status,
            "evidence": cov.get("evidence", 0),
            "facts": cov.get("facts", 0),
            "signed_claims": cov.get("signed_claims", 0),
            "sources": cov.get("sources", 0),
            "missing_dimensions": cov.get("missing_dimensions", []),
            "insufficient_products": quality_summary.get("insufficient_products", 0),
            "evidence_coverage_rate": quality_summary.get("evidence_coverage_rate", 0.0),
            "report_status": report.get("report_status", "unknown"),
        }

        rework_plan = {
            "goal": f"Address {status} coverage for {product_name}",
            "reason_codes": [reason_code],
            "steps": [
                {
                    "step": 1,
                    "action": "add_seed_urls",
                    "reason": reason_code,
                    "description": f"Add seed URLs for {product_name} if none exist or coverage is insufficient.",
                },
                {
                    "step": 2,
                    "action": "collect_evidence",
                    "reason": reason_code,
                    "description": f"Collect evidence items for {product_name} using the provided seed URLs.",
                },
                {
                    "step": 3,
                    "action": "extract_facts",
                    "reason": reason_code,
                    "description": f"Extract facts from new evidence for {product_name}.",
                },
                {
                    "step": 4,
                    "action": "generate_claims",
                    "reason": reason_code,
                    "description": f"Generate or update claims for {product_name}.",
                },
                {
                    "step": 5,
                    "action": "review_claims",
                    "reason": reason_code,
                    "description": "Review and sign new claims.",
                },
            ],
            "auto_execution": False,
            "requires_human_confirmation": True,
        }

        task = {
            "rework_id": rework_id,
            "intervention_id": None,
            "run_id": run_id,
            "project_id": project_id,
            "source_node": "export_report",
            "target_artifact_type": "report",
            "target_artifact_id": report.get("report_id"),
            "reason_codes": [reason_code],
            "status": "planned",
            "rework_plan_json": rework_plan,
            "before_json": product_metrics_before,
            "after_json": None,
            "created_at": utc_now(),
            "created_by": "system",
            "product_id": product_id,
            "product_name": product_name,
            "target_node": target_node,
            "required_action": required_action,
            "seed_urls": [],
            "metrics_before": metrics_before,
            "metrics_after": None,
        }

        rt_repo.create_rework_task(task)
        created_tasks.append({
            "rework_id": rework_id,
            "product_id": product_id,
            "product_name": product_name,
            "coverage_status": status,
            "reason_code": reason_code,
            "target_node": target_node,
            "status": "planned",
        })

    return {
        "run_id": run_id,
        "created_tasks": created_tasks,
        "skipped_tasks": skipped_tasks,
        "total_created": len(created_tasks),
        "total_skipped": len(skipped_tasks),
        "message": f"Created {len(created_tasks)} coverage gap rework tasks, skipped {len(skipped_tasks)} existing.",
    }


class ExecuteReworkRequest(BaseModel):
    seed_urls: list[str] = Field(default_factory=list, description="Supplemental seed URLs to add for the product")
    mode: str = Field(default="real_time", description="Execution mode: real_time or async")


@router.post("/rework-tasks/{rework_id}/execute")
def execute_rework(rework_id: str, request: ExecuteReworkRequest) -> dict[str, Any]:
    """Execute a real coverage gap rework task.

    This performs actual evidence collection for a product with insufficient coverage:
    1. Merges seed_urls into the product's source plan
    2. Collects sources / snapshots / raw_documents
    3. Extracts evidence_items
    4. Extracts facts
    5. Generates claims
    6. Reviews and signs claims
    7. Updates the report
    8. Records before/after metrics
    """
    from backend.app.storage.repositories import (
        ReportRepository, SourceRepository, EvidenceRepository,
        ClaimRepository, ReviewRepository, RunRepository, ProductRepository,
    )
    from backend.app.storage.fact_repository import FactRepository
    from backend.app.orchestrator.nodes import (
        collect_sources, extract_evidence, extract_facts,
        generate_claims, review_claims, export_report,
    )

    rt_repo = ReworkTaskRepository()
    task = rt_repo.get_rework_task(rework_id)
    if not task:
        raise HTTPException(status_code=404, detail="Rework task not found")

    if task.get("status") in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot execute a task with status '{task['status']}'",
        )

    run_id = task["run_id"]
    product_id = task.get("product_id", "")
    product_name = task.get("product_name", product_id)
    target_node = task.get("target_node", "collect_sources")

    # Merge provided seed_urls with existing seed_urls
    existing_urls = task.get("seed_urls", [])
    all_seed_urls = list({url.strip() for url in existing_urls + request.seed_urls if url.strip()})

    # Capture before state
    before_json = task.get("before_json") or {}
    metrics_before = task.get("metrics_before") or {}

    # Snapshot current DB state for this product
    report_repo = ReportRepository()
    source_repo = SourceRepository()
    evidence_repo = EvidenceRepository()
    claim_repo = ClaimRepository()
    review_repo = ReviewRepository()
    fact_repo = FactRepository()

    def snapshot_product_metrics() -> dict[str, Any]:
        """Snapshot current metrics for this product using flexible product matching."""
        evidence_items = evidence_repo.list_evidence(run_id)
        facts = fact_repo.list_facts(run_id)
        claims = claim_repo.list_claims(run_id)
        signed_claims = [c for c in claims if c.get("review_status") == "signed"]
        sources = source_repo.list_sources(run_id)

        def _matches(row: dict) -> bool:
            rp = str(row.get("product_id", ""))
            rs = str(row.get("product_slug", "")).lower()
            rn = str(row.get("product_name", "")).lower()
            tp = product_id.lower()
            return (rp == product_id or rp.endswith("_" + tp) or
                    rs == tp or rn == tp)

        product_evidence = [e for e in evidence_items if _matches(e)]
        product_facts = [f for f in facts if _matches(f)]
        product_claims = [c for c in claims if _matches(c)]
        product_signed = [s for s in signed_claims if _matches(s)]
        product_sources = [s for s in sources if _matches(s)]

        report = report_repo.get_latest_report(run_id)
        qs = (report.get("quality_summary") or {}) if report else {}

        return {
            "product_id": product_id,
            "product_name": product_name,
            "evidence_count": len(product_evidence),
            "facts_count": len(product_facts),
            "signed_claims": len(product_signed),
            "total_claims": len(product_claims),
            "sources_count": len(product_sources),
            "report_status": (report.get("report_status") if report else "unknown"),
            "insufficient_products": qs.get("insufficient_products", 0),
            "partial_products": qs.get("partial_products", 0),
            "evidence_coverage_rate": qs.get("evidence_coverage_rate", 0.0),
        }

    before_snapshot = snapshot_product_metrics()

    # Mark task as running
    rt_repo.update_rework_task(rework_id, status="running", seed_urls=all_seed_urls)

    try:
        # Build execution state by loading existing run data from DB
        from backend.app.storage.repositories import RunRepository, ProductRepository

        run_repo = RunRepository()
        prod_repo = ProductRepository()
        run = run_repo.get_run(run_id)

        if not run:
            raise ValueError(f"Run not found: {run_id}")

        # Build initial state from run and task_brief
        # Safely parse task_brief_json which may be a string or dict
        raw_tb = run.get("task_brief_json") or {}
        if isinstance(raw_tb, str):
            import json as _json
            task_brief = _json.loads(raw_tb) if raw_tb else {}
        else:
            task_brief = raw_tb or {}
        products = task_brief.get("products", [])

        # Product matching helper: match by product_id, product_slug, or name
        def _product_matches(row: dict, target_pid: str, target_slug: str) -> bool:
            rp = row.get("product_id", "")
            rs = row.get("product_slug", "")
            rn = row.get("product_name", "").lower()
            tp = target_pid.lower()
            ts = target_slug.lower()
            return (rp == target_pid or rp.endswith("_" + ts) or
                    rs == target_slug or rs == ts or
                    rn == tp or rn == ts)

        # Find the target product in task_brief.products and merge seed_urls
        target_products_list = []
        found = False
        for p in products:
            pid = p.get("product_id", "")
            pslug = p.get("product_slug", "") or (pid.split("_")[-1] if pid else "")
            if _product_matches(p, product_id, product_id):
                p = dict(p)
                existing_urls = p.get("seed_urls") or []
                if isinstance(existing_urls, str):
                    existing_urls = [existing_urls]
                p["seed_urls"] = sorted(set(list(existing_urls) + all_seed_urls))
                if not p.get("official_website") and p["seed_urls"]:
                    p["official_website"] = p["seed_urls"][0]
                target_products_list.append(p)
                found = True
                logger.info("execute_rework: merged seed_urls into existing product %s", pid)
                break

        # If product not found in task_brief, create a placeholder
        if not found:
            product_slug = product_id.lower().replace(" ", "-").replace("_", "-")
            target_products_list.append({
                "product_id": product_id,
                "product_slug": product_slug,
                "product_name": product_name,
                "official_website": all_seed_urls[0] if all_seed_urls else "",
                "seed_urls": all_seed_urls,
            })
            logger.info("execute_rework: created placeholder product %s with seed_urls", product_id)

        # Update task_brief with scoped products
        task_brief = dict(task_brief)
        task_brief["products"] = target_products_list

        state: dict[str, Any] = {
            "run_id": run_id,
            "project_id": run.get("project_id"),
            "task_brief": task_brief,
            "products": target_products_list,
            "mode": request.mode,
            "seed_urls": {product_id: all_seed_urls},
            "target_products": [product_id],
            "current_rework_product": product_id,
            "sources": [],
            "evidence_items": [],
            "facts": [],
            "claim_drafts": [],
            "signed_claims": [],
            "errors": [],
        }

        # Load existing sources/evidence/facts/claims from DB for context
        existing_sources = source_repo.list_sources(run_id)
        existing_evidence = evidence_repo.list_evidence(run_id)
        existing_facts = fact_repo.list_facts(run_id)
        existing_claims = claim_repo.list_claims(run_id)
        existing_signed = [c for c in existing_claims if c.get("review_status") == "signed"]

        state["sources"] = existing_sources
        state["evidence_items"] = existing_evidence
        state["facts"] = existing_facts
        state["claim_drafts"] = existing_claims
        state["signed_claims"] = existing_signed

        logger.info(
            "execute_rework: run_id=%s product_id=%s seed_urls=%d "
            "existing: sources=%d evidence=%d facts=%d claims=%d",
            run_id, product_id, len(all_seed_urls),
            len(existing_sources), len(existing_evidence),
            len(existing_facts), len(existing_signed),
        )

        # Step 1: Collect sources (with merged seed URLs in real_time mode to actually collect new evidence)
        logger.info("execute_rework: BEFORE STEP1 target_node=%r all_seed_urls=%s", target_node, all_seed_urls)
        if target_node in ("collect_sources", "collect_evidence"):
            logger.info("execute_rework: about to call collect_sources, mode=%s product_id=%s", state.get("mode"), product_id)
            logger.info("execute_rework: collect_sources for %s (real_time, %d seed_urls)", product_id, len(all_seed_urls))
            state["mode"] = "real_time"
            state = collect_sources(state)
            logger.info(
                "execute_rework: collect_sources done, sources=%d snapshots=%d evidence=%d",
                len(state.get("sources", [])), len(state.get("snapshots", [])),
                len(state.get("evidence_items", [])),
            )

        # Step 2: Extract evidence is handled by collect_sources in real_time mode above.

        # Step 3: Extract facts
        if target_node in ("collect_sources", "collect_evidence", "extract_evidence", "extract_facts"):
            logger.info("execute_rework: extract_facts for %s", product_id)
            state = extract_facts(state)
            logger.info(
                "execute_rework: extract_facts done, facts=%d",
                len(state.get("facts", [])),
            )

        # Step 4: Generate claims
        if target_node in ("collect_sources", "collect_evidence", "extract_evidence", "extract_facts", "generate_claims"):
            logger.info("execute_rework: generate_claims for %s", product_id)
            state = generate_claims(state)
            logger.info(
                "execute_rework: generate_claims done, claims=%d",
                len(state.get("claim_drafts", [])),
            )

        # Step 5: Review claims
        if target_node in ("collect_sources", "collect_evidence", "extract_evidence", "extract_facts", "generate_claims", "review_claims"):
            logger.info("execute_rework: review_claims for %s", product_id)
            state = review_claims(state)
            logger.info(
                "execute_rework: review_claims done, signed_claims=%d",
                len(state.get("signed_claims", [])),
            )

        # Step 6: Update report (re-export with new data)
        logger.info("execute_rework: re-export report for %s", product_id)
        # Load existing report into state so export_report can update it
        existing_report = report_repo.get_latest_report(run_id)
        if existing_report:
            state["report_draft"] = existing_report
            logger.info("execute_rework: loaded existing report_id=%s", existing_report.get("report_id"))
        else:
            logger.warning("execute_rework: no existing report found for run_id=%s", run_id)
        state = export_report(state)
        logger.info(
            "execute_rework: export_report done, report_status=%s",
            state.get("report_status"),
        )

        # Switch to cached mode AFTER export so the report actually gets persisted
        state["mode"] = "cached"

        # Capture after state
        after_snapshot = snapshot_product_metrics()

        # Build after_json
        after_json = {
            "executed": True,
            "executed_at": utc_now(),
            "seed_urls_used": all_seed_urls,
            "target_node": target_node,
            "execution_summary": {
                "sources_added": after_snapshot["sources_count"] - before_snapshot["sources_count"],
                "evidence_added": after_snapshot["evidence_count"] - before_snapshot["evidence_count"],
                "facts_added": after_snapshot["facts_count"] - before_snapshot["facts_count"],
                "claims_added": after_snapshot["total_claims"] - before_snapshot["total_claims"],
                "signed_claims_added": after_snapshot["signed_claims"] - before_snapshot["signed_claims"],
            },
        }

        # Update metrics_after
        metrics_after = dict(metrics_before)
        metrics_after["product_after"] = after_snapshot

        # Mark task completed
        now_str = utc_now()
        rt_repo.update_rework_task(
            rework_id,
            status="completed",
            after_json=after_json,
            metrics_after=metrics_after,
            completed_at=now_str,
        )

        logger.info(
            "execute_rework: SUCCESS run_id=%s product_id=%s "
            "evidence %d->%d facts %d->%d signed %d->%d",
            run_id, product_id,
            before_snapshot["evidence_count"], after_snapshot["evidence_count"],
            before_snapshot["facts_count"], after_snapshot["facts_count"],
            before_snapshot["signed_claims"], after_snapshot["signed_claims"],
        )

        return {
            "rework_id": rework_id,
            "status": "completed",
            "before": before_snapshot,
            "after": after_snapshot,
            "after_json": after_json,
            "message": f"Successfully collected evidence for {product_name}. "
                       f"Added {after_json['execution_summary']['evidence_added']} evidence items, "
                       f"{after_json['execution_summary']['facts_added']} facts, "
                       f"{after_json['execution_summary']['signed_claims_added']} signed claims.",
        }

    except Exception as exc:
        error_json = {
            "error_node": target_node,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "failed_at": utc_now(),
        }

        rt_repo.update_rework_task(
            rework_id,
            status="failed",
            error_json=error_json,
            completed_at=utc_now(),
        )

        logger.error(
            "execute_rework: FAILED run_id=%s product_id=%s error=%s",
            run_id, product_id, exc,
            exc_info=True,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Rework execution failed: {exc}",
        )

