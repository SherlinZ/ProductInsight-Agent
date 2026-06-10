from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.storage.repositories import (
    ProjectRepository,
    RunRepository,
    SourceRepository,
    EvidenceRepository,
    ClaimRepository,
    ReportRepository,
    MessageRepository,
    TraceRepository,
    EvalRepository,
)
from backend.app.storage.repositories import _safe_parse_json

router = APIRouter(prefix="/api/projects", tags=["projects"])


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    """Create a URL-safe slug from a product name."""
    return name.lower().replace(" ", "-").replace("_", "-")


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ProductInput(BaseModel):
    product_name: str = Field(..., min_length=1)
    company_name: str = ""
    official_website: str = ""
    seed_urls: list[str] = Field(default_factory=list)


class CreateProjectRequest(BaseModel):
    project_name: str = Field(..., min_length=1)
    task_type: str = Field(default="competitor_landscape")
    target_region: str = Field(default="global")
    description: str = ""
    products: list[ProductInput] = Field(default_factory=list)
    analysis_dimensions: list[str] = Field(default_factory=list)
    # vNext-P0-Real-Frontend-Integration: research plan propagation fields
    research_plan_id: Optional[str] = None
    execution_dag_id: Optional[str] = None
    source_discovery: dict[str, Any] = Field(default_factory=dict)
    research_plan: dict[str, Any] = Field(default_factory=dict)
    report_outline: dict[str, Any] = Field(default_factory=dict)


class StartRunRequest(BaseModel):
    mode: str = Field(default="real_time")
    task_title: str = ""
    auto_start: bool = Field(default=False)


# ---------------------------------------------------------------------------
# POST /api/projects — create project
# ---------------------------------------------------------------------------

@router.post("")
def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    """Create a new project with optional products.

    vNext-P0-Real-Frontend-Integration:
    - Saves research_plan_id, execution_dag_id, research_plan, report_outline,
      source_discovery to project metadata_json.
    - If req.research_plan is empty but req.research_plan_id is set, loads
      the full plan from ResearchPlanRepository.
    """
    now = utc_now()
    project_id = f"proj_{uuid.uuid4().hex[:12]}"
    repo = ProjectRepository()

    analysis_dims = req.analysis_dimensions or [
        "function_tree",
        "pricing_model",
        "user_persona",
        "customer_voice",
        "swot",
        "enterprise_readiness",
    ]

    # vNext-P0: Resolve research_plan if only research_plan_id was passed
    research_plan = dict(req.research_plan) if req.research_plan else {}
    if not research_plan and req.research_plan_id:
        try:
            from backend.app.storage.repositories import ResearchPlanRepository
            rp = ResearchPlanRepository().get_research_plan(req.research_plan_id)
            if rp:
                # get_research_plan returns the full parsed plan directly
                research_plan = rp
        except Exception:
            pass  # Keep empty dict

    report_outline = dict(req.report_outline) if req.report_outline else {}
    if not report_outline and research_plan:
        report_outline = research_plan.get("report_outline") or {}

    # vNext-P0: Build metadata JSON
    metadata = {
        "research_plan_id": req.research_plan_id,
        "execution_dag_id": req.execution_dag_id,
        "source_discovery": dict(req.source_discovery) if req.source_discovery else {},
        "research_plan": research_plan,
        "report_outline": report_outline,
    }

    project = {
        "project_id": project_id,
        "project_name": req.project_name,
        "task_type": req.task_type,
        "target_region": req.target_region,
        "description": req.description,
        "analysis_dimensions": analysis_dims,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        # vNext-P0: research plan metadata stored alongside project record
        "metadata": metadata,
        "metadata_json": json.dumps(metadata, ensure_ascii=False) if metadata else "{}",
    }
    repo.create_project(project)

    # Add products
    for i, prod_in in enumerate(req.products):
        slug = _slugify(prod_in.product_name)
        project_product_id = f"pp_{uuid.uuid4().hex[:12]}"
        repo.add_project_product({
            "project_product_id": project_product_id,
            "project_id": project_id,
            "product_slug": slug,
            "product_name": prod_in.product_name,
            "company_name": prod_in.company_name,
            "official_website": prod_in.official_website,
            "seed_urls": prod_in.seed_urls,
            "product_type": "ai_agent_platform",
            "region": req.target_region,
            "created_at": now,
            "updated_at": now,
        })

    return {"project_id": project_id, "status": "created"}


# ---------------------------------------------------------------------------
# GET /api/projects — list projects
# ---------------------------------------------------------------------------

@router.get("")
def list_projects(status: Optional[str] = None) -> list[dict[str, Any]]:
    """List all projects, optionally filtered by status."""
    return ProjectRepository().list_projects(status=status)


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id} — project detail
# ---------------------------------------------------------------------------

@router.get("/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    """Get project with products, runs, and aggregates."""
    result = ProjectRepository().get_project_with_products(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


# ---------------------------------------------------------------------------
# POST /api/projects/{project_id}/runs — start analysis run
# ---------------------------------------------------------------------------

@router.post("/{project_id}/runs")
def start_project_run(project_id: str, req: StartRunRequest) -> dict[str, Any]:
    """Create and optionally start a run for the given project.

    vNext-P0-Real-Frontend-Integration: Resolves research_plan and report_outline
    from multiple sources: project top-level, project.metadata, or ResearchPlanRepository.
    Ensures task_brief always contains report_outline, research_plan, source_discovery,
    and schema_type when available.
    """
    import logging as _proj_logger
    _proj_logger.critical("!!! START_PROJECT_RUN !!! project_id=%s", project_id)
    
    project = ProjectRepository().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    products = ProjectRepository().list_project_products(project_id)
    _proj_logger.critical("!!! START_PROJECT_RUN: products from list_project_products: %d", len(products))
    if not products:
        raise HTTPException(status_code=400, detail="Project has no products; add products first")

    now = utc_now()
    run_id = f"run_{uuid.uuid4().hex[:16]}"
    task_title = req.task_title or f"{project['project_name']} - Analysis"

    task_brief = {
        "title": task_title,
        "description": project.get("description", ""),
        "target_region": project["target_region"],
        "products": [
            {
                "product_id": p["product_slug"],
                "product_name": p["product_name"],
                "company_name": p.get("company_name", ""),
                "official_website": p.get("official_website", ""),
                "seed_urls": p.get("seed_urls", []),
                "region": p.get("region", project["target_region"]),
            }
            for p in products
        ],
        "analysis_dimensions": project.get("analysis_dimensions", []),
        "project_id": project_id,
        "task_type": project.get("task_type", "competitor_landscape"),
    }
    _proj_logger.critical("!!! START_PROJECT_RUN: task_brief.products=%d", len(task_brief["products"]))

    # vNext-P0-Real-Frontend-Integration: Multi-source research_plan / report_outline resolution
    project_meta = project
    research_plan = project_meta.get("research_plan") or {}
    report_outline = project_meta.get("report_outline") or {}

    # Fallback: check metadata_json
    if not research_plan:
        metadata = project_meta.get("metadata") or {}
        research_plan = metadata.get("research_plan") or {}
        report_outline = metadata.get("report_outline") or {}

    # Fallback: check top-level research_plan field
    if not research_plan:
        research_plan = project_meta.get("research_plan") or {}

    # Fallback: if only research_plan_id is available, load from ResearchPlanRepository
    if not research_plan:
        research_plan_id = project_meta.get("research_plan_id") or (
            project_meta.get("metadata") or {}
        ).get("research_plan_id")
        if research_plan_id:
            try:
                from backend.app.storage.repositories import ResearchPlanRepository
                rp = ResearchPlanRepository().get_research_plan(research_plan_id)
                if rp:
                    # get_research_plan returns the full parsed plan directly
                    research_plan = rp
                    report_outline = research_plan.get("report_outline") or {}
            except Exception:
                pass

    # Fallback: source_discovery from project metadata
    source_discovery = project_meta.get("source_discovery") or (
        project_meta.get("metadata") or {}
    ).get("source_discovery") or {}

    if research_plan and not report_outline:
        report_outline = research_plan.get("report_outline") or {}

    if research_plan:
        task_brief["research_plan"] = research_plan
    if report_outline:
        task_brief["report_outline"] = report_outline
    if source_discovery:
        task_brief["source_discovery"] = source_discovery
    if research_plan.get("schema_type"):
        task_brief["schema_type"] = research_plan["schema_type"]
    elif research_plan.get("task_brief", {}).get("task_type"):
        task_brief["schema_type"] = research_plan["task_brief"]["task_type"]

    run = {
        "run_id": run_id,
        "project_id": project_id,
        "task_id": f"task_{run_id}",
        "task_title": task_title,
        "task_brief": task_brief,
        "mode": req.mode,
        "status": "pending",
        "current_node": None,
        "created_at": now,
        "updated_at": now,
    }
    _proj_logger.critical("!!! CREATE_RUN: task_brief.products=%d", len(task_brief.get("products", [])))
    RunRepository().create_run(run)

    if req.auto_start:
        from backend.app.storage.repositories import start_run_execution
        result = start_run_execution(run_id)
        result["project_id"] = project_id
        return result

    return {"run_id": run_id, "project_id": project_id, "status": "pending",
            "current_node": None, "error_message": None}


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/sources — sources for project
# ---------------------------------------------------------------------------

@router.get("/{project_id}/sources")
def get_project_sources(project_id: str) -> list[dict[str, Any]]:
    """Get all source records for a project via its runs."""
    run_ids = _get_project_run_ids(project_id)
    results: list[dict[str, Any]] = []
    for rid in run_ids:
        results.extend(SourceRepository().list_sources(rid))
    return results


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/evidence — evidence for project
# ---------------------------------------------------------------------------

@router.get("/{project_id}/evidence")
def get_project_evidence(
    project_id: str,
    product_slug: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get all evidence items for a project, optionally filtered by product."""
    run_ids = _get_project_run_ids(project_id)
    results: list[dict[str, Any]] = []
    for rid in run_ids:
        evidence = EvidenceRepository().list_evidence(rid)
        if product_slug:
            evidence = [e for e in evidence if e.get("product_slug") == product_slug]
        results.extend(evidence)
    return results


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/knowledge — facts as knowledge table
# ---------------------------------------------------------------------------

@router.get("/{project_id}/knowledge")
def get_project_knowledge(
    project_id: str,
    product_slug: Optional[str] = None,
    schema_key: Optional[str] = None,
    review_status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get facts as structured knowledge entries for the project."""
    from backend.app.storage.db import get_connection
    run_ids = _get_project_run_ids(project_id)
    if not run_ids:
        return []

    placeholders = ",".join(["?"] * len(run_ids))
    query = f"""
        SELECT f.*,
               COUNT(e.evidence_id) AS evidence_count
        FROM facts f
        LEFT JOIN evidence_items e ON e.evidence_id IN (
            SELECT value FROM json_each(f.evidence_ids_json)
        ) AND e.run_id = f.run_id
        WHERE f.run_id IN ({placeholders})
    """
    params: list[Any] = list(run_ids)

    if product_slug:
        query += " AND f.product_slug = ?"
        params.append(product_slug)
    if schema_key:
        query += " AND f.schema_key LIKE ?"
        params.append(f"{schema_key}%")
    if review_status:
        query += " AND f.review_status = ?"
        params.append(review_status)

    query += " GROUP BY f.fact_id ORDER BY f.updated_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        r["evidence_ids"] = _safe_parse_json(r.pop("evidence_ids_json", None), [])
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/claims — claims for project
# ---------------------------------------------------------------------------

@router.get("/{project_id}/claims")
def get_project_claims(
    project_id: str,
    review_status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get all claims (including signed claims) for the project."""
    run_ids = _get_project_run_ids(project_id)
    results: list[dict[str, Any]] = []
    for rid in run_ids:
        claims = ClaimRepository().list_claims(rid, status=review_status)
        results.extend(claims)
    return results


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/deliverables — reports and exports
# ---------------------------------------------------------------------------

@router.get("/{project_id}/deliverables")
def get_project_deliverables(project_id: str) -> dict[str, Any]:
    """Get all deliverables (reports, exports) for the project."""
    run_ids = _get_project_run_ids(project_id)
    reports: list[dict[str, Any]] = []
    for rid in run_ids:
        r = ReportRepository().get_report(rid)
        if r:
            reports.append(r)

    return {
        "project_id": project_id,
        "reports": reports,
        "total_reports": len(reports),
    }


# ---------------------------------------------------------------------------
# GET /api/projects/{project_id}/audit — trace and audit summary
# ---------------------------------------------------------------------------

@router.get("/{project_id}/audit")
def get_project_audit(project_id: str) -> dict[str, Any]:
    """Get trace logs, messages, pii logs, and eval logs summary."""
    run_ids = _get_project_run_ids(project_id)

    trace_count = 0
    message_count = 0
    pii_count = 0
    eval_count = 0
    traces_by_node: dict[str, int] = {}

    for rid in run_ids:
        traces = TraceRepository().list_traces(rid)
        trace_count += len(traces)
        for t in traces:
            node = t.get("node_name", "unknown")
            traces_by_node[node] = traces_by_node.get(node, 0) + 1

        msgs = MessageRepository().list_messages(rid)
        message_count += len(msgs)

        from backend.app.storage.repositories import PiiLogRepository
        pii_logs = PiiLogRepository().list_pii_logs(rid)
        pii_count += len(pii_logs)

        ev = EvalRepository().get_latest_eval(rid)
        if ev:
            eval_count += 1

    return {
        "project_id": project_id,
        "run_count": len(run_ids),
        "run_ids": run_ids,
        "trace_count": trace_count,
        "trace_by_node": traces_by_node,
        "message_count": message_count,
        "pii_log_count": pii_count,
        "eval_log_count": eval_count,
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_project_run_ids(project_id: str) -> list[str]:
    """Return all run_ids associated with a project."""
    from backend.app.storage.db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return [r["run_id"] for r in rows]
