"""
Research Plan API Endpoints (vNext-R1).

Provides endpoints for:
- POST /api/research-plans/generate
- GET /api/research-plans/{research_plan_id}
- PUT /api/research-plans/{research_plan_id}
- POST /api/research-plans/{research_plan_id}/revise
- POST /api/research-plans/{research_plan_id}/confirm
- GET /api/research-plans/{research_plan_id}/dag
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.storage.repositories import (
    ResearchPlanRepository,
    ExecutionDAGRepository,
)
from backend.app.services.research_planner import (
    generate_research_plan,
    revise_research_plan,
    compile_execution_dag,
    utc_now,
    generate_id,
    detect_language,
    get_language_config,
    analyze_query,
)
from backend.app.schemas.research_plan import validate_research_plan


router = APIRouter(prefix="/api/research-plans", tags=["research-plans"])


# ---------------------------------------------------------------------------
# Request/Response Schemas
# ---------------------------------------------------------------------------

class GeneratePlanRequest(BaseModel):
    user_query: str = Field(..., min_length=1, description="Natural language research query")
    schema_type: Optional[str] = Field(default="", description="Analysis schema type (empty/null = auto-infer from query)")
    target_region: str = Field(default="global", description="Target market region")
    mode: str = Field(default="review", description="Execution mode: auto, review, or expert")
    # Optional: pre-analyzed data from /analyze endpoint
    analyzed_data: Optional[dict[str, Any]] = Field(
        default=None,
        description="Pre-analyzed data from /analyze endpoint (competitors, schema_type, dimensions, etc.)",
    )


class AnalyzeQueryRequest(BaseModel):
    user_query: str = Field(..., min_length=1, description="Natural language research query")
    target_region: str = Field(default="global", description="Target market region")


class RevisePlanRequest(BaseModel):
    human_instruction: str = Field(..., min_length=1, description="Human instruction for revision")


class UpdatePlanRequest(BaseModel):
    payload_json: Optional[str] = Field(None, description="Updated plan JSON string")


class PlanResponse(BaseModel):
    research_plan_id: str
    status: str
    research_plan: dict[str, Any]
    generated_by: str
    created_at: str
    detected_language: Optional[str] = None
    language_config: Optional[dict[str, Any]] = None


class ConfirmResponse(BaseModel):
    research_plan_id: str
    status: str
    dag_id: str
    project_id: Optional[str] = None
    message: str


# ---------------------------------------------------------------------------
# POST /api/research-plans/analyze
# ---------------------------------------------------------------------------

@router.post("/analyze")
def analyze_research_query(req: AnalyzeQueryRequest) -> dict[str, Any]:
    """Analyze user query using LLM to extract competitors, intent, schema type, and dimensions.

    This is the new recommended first step:
    1. User submits query → /analyze returns preview (competitors, dimensions, schema)
    2. User reviews and edits the preview
    3. User confirms → /generate creates the full plan (no LLM analysis needed, uses confirmed data)

    Falls back to rule-based extraction if LLM is unavailable.
    """
    detected_language = detect_language(req.user_query)

    result = analyze_query(
        user_query=req.user_query,
        target_region=req.target_region,
        detected_language=detected_language,
    )

    return {
        "analysis": result,
        "detected_language": detected_language,
        "input_language": "zh" if detected_language == "zh" else "en",
    }


# ---------------------------------------------------------------------------
# POST /api/research-plans/generate
# ---------------------------------------------------------------------------

@router.post("/generate", response_model=PlanResponse)
def create_research_plan(req: GeneratePlanRequest) -> dict[str, Any]:
    """Generate a new ResearchPlan.

    Two paths:
    1. With analyzed_data: use pre-analyzed competitors/schema (no LLM re-analysis)
       → User already reviewed the analysis preview, so skip redundant LLM calls
    2. Without analyzed_data: run full LLM analysis + generation (legacy behavior)
    """
    # Normalize: empty/null schema_type means auto-infer
    schema = req.schema_type or ""

    # Detect user language from query
    detected_language = detect_language(req.user_query)
    lang_config = get_language_config(detected_language)

    # Extract from analyzed data if provided
    explicit_competitors = None
    explicit_schema_type = None
    skip_outline = False
    if req.analyzed_data:
        explicit_competitors = req.analyzed_data.get("competitors")
        explicit_schema_type = req.analyzed_data.get("schema_type") or schema
        skip_outline = True  # User reviewed outline-free preview, don't generate outline now

    # Generate the plan — if we have explicit data, pass it to avoid redundant LLM analysis
    plan = generate_research_plan(
        user_query=req.user_query,
        schema_type=explicit_schema_type or schema,
        target_region=req.target_region,
        mode=req.mode,
        detected_language=detected_language,
        language_config=lang_config,
        explicit_competitors=explicit_competitors,
        skip_outline_generation=skip_outline,
    )

    # Validate
    is_valid, errors = validate_research_plan(plan)
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"Generated plan validation failed: {errors}"
        )

    # Store in DB
    plan_id = plan.get("research_plan_id") or generate_id("plan")
    plan["research_plan_id"] = plan_id

    # Prepare for storage
    store_plan = {
        "research_plan_id": plan_id,
        "status": "draft",
        "user_query": req.user_query,
        "schema_type": req.schema_type,
        "target_region": req.target_region,
        "mode": req.mode,
        "generated_by": plan.get("generated_by", "fallback"),
        "payload_json": json.dumps(plan, ensure_ascii=False),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }

    repo = ResearchPlanRepository()
    repo.create_research_plan(store_plan)

    return {
        "research_plan_id": plan_id,
        "status": "draft",
        "research_plan": plan,
        "generated_by": plan.get("generated_by", "fallback"),
        "created_at": store_plan["created_at"],
        "detected_language": detected_language,  # Return detected language for frontend
        "language_config": lang_config,  # Return language config for frontend
    }


# ---------------------------------------------------------------------------
# GET /api/research-plans/{research_plan_id}
# ---------------------------------------------------------------------------

@router.get("/{research_plan_id}", response_model=PlanResponse)
def get_research_plan(research_plan_id: str) -> dict[str, Any]:
    """Get a research plan by ID."""
    repo = ResearchPlanRepository()
    row = repo.get_research_plan(research_plan_id)

    if not row:
        raise HTTPException(status_code=404, detail="Research plan not found")

    # _parse_plan returns full plan directly (payload_json already parsed)
    plan = row

    return {
        "research_plan_id": plan.get("research_plan_id", research_plan_id),
        "status": plan.get("status", "draft"),
        "research_plan": plan,
        "generated_by": plan.get("generated_by", "fallback"),
        "created_at": plan.get("created_at", ""),
    }


# ---------------------------------------------------------------------------
# PUT /api/research-plans/{research_plan_id}
# ---------------------------------------------------------------------------

@router.put("/{research_plan_id}", response_model=PlanResponse)
def update_research_plan(
    research_plan_id: str,
    req: UpdatePlanRequest,
) -> dict[str, Any]:
    """Update a research plan with new JSON payload."""
    repo = ResearchPlanRepository()
    existing = repo.get_research_plan(research_plan_id)

    if not existing:
        raise HTTPException(status_code=404, detail="Research plan not found")

    if existing.get("status") == "confirmed":
        raise HTTPException(
            status_code=400,
            detail="Cannot modify a confirmed research plan"
        )

    # Parse new payload
    if req.payload_json:
        try:
            new_plan = json.loads(req.payload_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")
    else:
        new_plan = existing  # _parse_plan returns full plan directly

    # Validate
    is_valid, errors = validate_research_plan(new_plan)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Validation failed: {errors}")

    # Update generated_by
    new_plan["generated_by"] = "human_edited"

    # Persist
    repo.update_research_plan(
        research_plan_id,
        payload_json=json.dumps(new_plan, ensure_ascii=False),
    )

    return {
        "research_plan_id": research_plan_id,
        "status": new_plan.get("status", "draft"),
        "research_plan": new_plan,
        "generated_by": "human_edited",
        "created_at": existing.get("created_at", utc_now()),
    }


# ---------------------------------------------------------------------------
# POST /api/research-plans/{research_plan_id}/revise
# ---------------------------------------------------------------------------

@router.post("/{research_plan_id}/revise", response_model=PlanResponse)
def revise_plan(
    research_plan_id: str,
    req: RevisePlanRequest,
) -> dict[str, Any]:
    """Revise a research plan based on human instruction."""
    repo = ResearchPlanRepository()
    existing = repo.get_research_plan(research_plan_id)

    if not existing:
        raise HTTPException(status_code=404, detail="Research plan not found")

    if existing.get("status") == "confirmed":
        raise HTTPException(
            status_code=400,
            detail="Cannot revise a confirmed research plan"
        )

    # _parse_plan returns full plan directly
    plan = existing

    # Revise based on instruction
    revised_plan = revise_research_plan(plan, req.human_instruction)

    # Validate
    is_valid, errors = validate_research_plan(revised_plan)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Revised plan validation failed: {errors}")

    # Persist
    repo.update_research_plan(
        research_plan_id,
        payload_json=json.dumps(revised_plan, ensure_ascii=False),
    )

    return {
        "research_plan_id": research_plan_id,
        "status": revised_plan.get("status", "draft"),
        "research_plan": revised_plan,
        "generated_by": "human_edited",
        "created_at": existing.get("created_at", utc_now()),
    }


# ---------------------------------------------------------------------------
# POST /api/research-plans/{research_plan_id}/competitors
# P2 Fix: Allow adding new competitors to confirmed plans (hot-replan)
# ---------------------------------------------------------------------------

class AddCompetitorRequest(BaseModel):
    """Add one or more new competitors to an existing research plan."""
    competitors: list[dict[str, Any]] = Field(
        description="List of competitor dicts. Each requires 'name'. "
                    "Optional: 'product_url', 'official_website', 'notes'.",
        min_length=1,
    )


@router.post("/{research_plan_id}/competitors", response_model=PlanResponse)
def add_competitors_to_plan(
    research_plan_id: str,
    req: AddCompetitorRequest,
) -> dict[str, Any]:
    """
    Add new competitors to an existing research plan, even if the plan is confirmed.

    Unlike PUT /{id} which blocks modifications to confirmed plans, this endpoint
    intentionally allows the addition of new competitors to confirmed plans so that
    users can expand the competitive landscape after initial analysis.

    This does NOT modify or invalidate existing confirmed competitors.
    """
    repo = ResearchPlanRepository()
    existing = repo.get_research_plan(research_plan_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Research plan not found")

    plan = existing
    competitors: list[dict] = plan.get("competitors", [])
    existing_names = {c.get("name", "").lower() for c in competitors}

    added = []
    for raw_c in req.competitors:
        name = (raw_c.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in existing_names:
            logger.info("add_competitors_to_plan: skipping duplicate '%s' in plan %s", name, research_plan_id)
            continue
        slug = name.lower().replace(" ", "-").replace("_", "-")
        competitor = {
            "name": name,
            "slug": raw_c.get("slug") or slug,
            "product_url": raw_c.get("product_url") or raw_c.get("official_website") or "",
            "notes": raw_c.get("notes", ""),
            "added_via": "hot_replan",
        }
        competitors.append(competitor)
        added.append(competitor)
        existing_names.add(name.lower())

    if not added:
        return {
            "research_plan_id": research_plan_id,
            "status": plan.get("status", "draft"),
            "research_plan": plan,
            "generated_by": plan.get("generated_by", "fallback"),
            "created_at": plan.get("created_at", utc_now()),
        }

    plan = dict(plan)
    plan["competitors"] = competitors
    plan["generated_by"] = "hot_replan"

    repo.update_research_plan(
        research_plan_id,
        payload_json=json.dumps(plan, ensure_ascii=False),
    )

    logger.info("add_competitors_to_plan: plan=%s added=%s", research_plan_id, [c["name"] for c in added])

    return {
        "research_plan_id": research_plan_id,
        "status": plan.get("status", "draft"),
        "research_plan": plan,
        "generated_by": "hot_replan",
        "created_at": plan.get("created_at", utc_now()),
    }


# ---------------------------------------------------------------------------
# POST /api/research-plans/{research_plan_id}/generate-outline
# ---------------------------------------------------------------------------

class GenerateOutlineRequest(BaseModel):
    competitors: list[dict[str, Any]] = Field(default_factory=list, description="List of competitors")
    dimensions: list[dict[str, Any]] = Field(default_factory=list, description="List of analysis dimensions")
    language: str = Field(default="zh", description="Output language: 'zh' or 'en'")


class GenerateOutlineResponse(BaseModel):
    outline: dict[str, Any]
    generated_by: str


@router.post("/{research_plan_id}/generate-outline", response_model=GenerateOutlineResponse)
def generate_outline(research_plan_id: str, req: GenerateOutlineRequest) -> dict[str, Any]:
    """Generate report outline using LLM based on competitors and dimensions.
    
    This is a separate, dedicated endpoint for outline generation.
    """
    from backend.app.services.outline_generator import generate_report_outline
    
    # Get existing plan
    repo = ResearchPlanRepository()
    existing = repo.get_research_plan(research_plan_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Research plan not found")
    
    # Generate outline using LLM
    outline = generate_report_outline(
        competitors=req.competitors,
        dimensions=req.dimensions,
        language=req.language,
    )
    
    return {
        "outline": outline,
        "generated_by": "llm_outline_generator",
    }


# ---------------------------------------------------------------------------
# POST /api/research-plans/{research_plan_id}/confirm
# ---------------------------------------------------------------------------

@router.post("/{research_plan_id}/confirm", response_model=ConfirmResponse)
def confirm_plan(research_plan_id: str) -> dict[str, Any]:
    """Confirm a research plan and create an Execution DAG."""
    plan_repo = ResearchPlanRepository()
    dag_repo = ExecutionDAGRepository()

    existing = plan_repo.get_research_plan(research_plan_id)

    if not existing:
        raise HTTPException(status_code=404, detail="Research plan not found")

    if existing.get("status") == "confirmed":
        raise HTTPException(
            status_code=400,
            detail="Research plan is already confirmed"
        )

    # _parse_plan returns full plan directly
    plan = existing

    # Compile DAG
    dag_data = compile_execution_dag(plan)
    dag_id = dag_data.get("dag_id")

    # Store DAG
    dag_store = {
        "dag_id": dag_id,
        "research_plan_id": research_plan_id,
        "status": "planned",
        "payload_json": json.dumps(dag_data, ensure_ascii=False),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    dag_repo.create_dag(dag_store)

    # Update plan with DAG reference and confirmed status
    plan["execution_dag"] = dag_data
    plan["status"] = "confirmed"

    plan_repo.update_research_plan(
        research_plan_id,
        status="confirmed",
        payload_json=json.dumps(plan, ensure_ascii=False),
        dag_id=dag_id,
        confirmed_at=utc_now(),
    )

    return {
        "research_plan_id": research_plan_id,
        "status": "confirmed",
        "dag_id": dag_id,
        "project_id": plan.get("project_id"),
        "message": "Research plan confirmed. Execution DAG created.",
    }


# ---------------------------------------------------------------------------
# GET /api/research-plans/{research_plan_id}/dag
# ---------------------------------------------------------------------------

@router.get("/{research_plan_id}/dag")
def get_plan_dag(research_plan_id: str) -> dict[str, Any]:
    """Get the Execution DAG for a research plan."""
    plan_repo = ResearchPlanRepository()
    dag_repo = ExecutionDAGRepository()

    # First check if plan exists
    plan_row = plan_repo.get_research_plan(research_plan_id)
    if not plan_row:
        raise HTTPException(status_code=404, detail="Research plan not found")

    # Get DAG from plan or find by research_plan_id
    # _parse_plan returns full plan directly
    plan = plan_row
    dag_id = plan.get("execution_dag", {}).get("dag_id")

    if not dag_id:
        # Try to find DAG by research_plan_id
        dag = dag_repo.get_dag_by_research_plan(research_plan_id)
        if dag:
            dag_id = dag.get("dag_id")

    if not dag_id:
        # DAG not yet compiled
        if plan.get("status") != "confirmed":
            raise HTTPException(
                status_code=400,
                detail="Plan is not yet confirmed. DAG will be created upon confirmation."
            )
        raise HTTPException(status_code=404, detail="DAG not found")

    dag = dag_repo.get_dag(dag_id)
    if not dag:
        raise HTTPException(status_code=404, detail="DAG not found")

    # _parse_dag returns full DAG with payload already merged
    dag_data = dag

    return {
        "dag_id": dag_id,
        "research_plan_id": research_plan_id,
        "status": dag_data.get("status", "pending"),
        "nodes": dag_data.get("nodes", []),
        "edges": dag_data.get("edges", []),
    }


# ---------------------------------------------------------------------------
# GET /api/research-plans (list all)
# ---------------------------------------------------------------------------

@router.get("")
def list_research_plans(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List all research plans, optionally filtered."""
    repo = ResearchPlanRepository()
    plans = repo.list_research_plans(project_id=project_id, status=status)

    return [
        {
            "research_plan_id": p.get("research_plan_id") or p.get("id", ""),
            "status": p.get("status", "draft"),
            "generated_by": p.get("generated_by", "fallback"),
            "created_at": p.get("created_at", ""),
            "dag_id": p.get("dag_id"),
            "project_name": (
                p.get("task_brief", {}).get("project_name")
                or p.get("report_outline", {}).get("report_title")
                or ""
            ),
            "task_type": p.get("task_brief", {}).get("task_type", ""),
            "target_region": p.get("task_brief", {}).get("target_region", ""),
        }
        for p in plans
    ]
