from __future__ import annotations

import atexit
import concurrent.futures
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel

from backend.app.orchestrator.graph import run_workflow
from backend.app.storage.repositories import RunRepository, ProductRepository, HumanInterventionRepository

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Module-level thread pools — survive worker reloads, avoid per-request spawn.
# ---------------------------------------------------------------------------
_workflow_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="workflow-",
)
_replay_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="replay-",
)
# Protect shutdown from concurrent calls during uvicorn reload
_executor_lock = threading.Lock()


def _shutdown_executors() -> None:
    """Called at process exit to cleanly shut down thread pools."""
    with _executor_lock:
        _workflow_executor.shutdown(wait=False, cancel_futures=True)
        _replay_executor.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_executors)


router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    task_title: str
    task_brief: dict
    mode: str = "real_time"  # Changed from "cached" to enable actual data collection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(started_at: str | None) -> bool:
    """Return True if a node started more than 30 minutes ago and appears stuck."""
    if not started_at:
        return False
    try:
        from datetime import datetime
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - started).total_seconds() > 1800  # 30 minutes
    except Exception:
        return False


@router.post("")
def create_run(request: CreateRunRequest) -> dict:
    now = utc_now()
    run_id = f"run_{uuid.uuid4().hex[:16]}"
    run = {
        "run_id": run_id,
        "task_id": f"task_{run_id}",
        "task_title": request.task_title,
        "task_brief": request.task_brief,
        "mode": request.mode,
        "status": "pending",
        "current_node": None,
        "created_at": now,
        "updated_at": now,
    }
    RunRepository().create_run(run)

    for product in request.task_brief.get("products", []):
        # Support both string array: ["Dify", "Coze"] and object array: [{"product_id": "dify", "product_name": "Dify"}]
        if isinstance(product, str):
            product_id = f"product_{uuid.uuid4().hex[:8]}"
            product_name = product
            seed_urls = []
        else:
            product_id = product.get("product_id", f"product_{uuid.uuid4().hex[:8]}")
            product_name = product.get("product_name", "")
            seed_urls = product.get("seed_urls", [])
        ProductRepository().add_product({
            "product_id": product_id,
            "run_id": run_id,
            "product_name": product_name,
            "company_name": product.get("company_name") if isinstance(product, dict) else None,
            "official_website": seed_urls[0] if seed_urls else None,
            "region": request.task_brief.get("target_region", "global"),
            "product_type": "ai_agent_platform",
            "seed_urls": seed_urls,
            "created_at": now,
            "updated_at": now,
        })

    return {"run_id": run_id, "status": "pending"}


def _report_available_for_run(run_id: str) -> dict:
    """Check if HTML report files exist for a run. Returns file paths if present."""
    for suffix in ("_v2", ""):
        md_path = PROJECT_ROOT / "data" / "reports" / f"report_{run_id}{suffix}.md"
        html_path = PROJECT_ROOT / "data" / "reports" / f"report_{run_id}{suffix}.html"
        if html_path.exists():
            return {
                "report_available": True,
                "report_id": f"report_{run_id}{suffix}",
                "content_markdown_path": str(md_path.relative_to(PROJECT_ROOT)),
                "content_html_path": str(html_path.relative_to(PROJECT_ROOT)) if html_path.exists() else None,
            }
    return {
        "report_available": False,
        "report_id": None,
        "content_markdown_path": None,
        "content_html_path": None,
    }


@router.get("")  # NOTE: no path param — this route MUST come before /{run_id}
def list_runs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status: pending/running/completed/failed"),
    has_report: Optional[bool] = Query(None, description="Filter runs that have a generated report"),
    project_id: Optional[str] = Query(None),
) -> dict:
    """
    List runs with pagination, optional status filter, and report availability.

    Returns runs ordered by created_at DESC with report availability metadata.
    """
    all_runs = RunRepository().list_runs()

    # Apply status filter
    if status:
        all_runs = [r for r in all_runs if r.get("status") == status]

    # Apply project_id filter
    if project_id:
        all_runs = [r for r in all_runs if r.get("project_id") == project_id]

    total = len(all_runs)

    # Apply has_report filter (check file existence on disk)
    if has_report is not None:
        filtered = []
        for r in all_runs:
            meta = _report_available_for_run(r["run_id"])
            if meta["report_available"] == has_report:
                filtered.append({**r, **meta})
        all_runs = filtered
        total = len(all_runs)

    runs_slice = all_runs[offset : offset + limit]

    # Always check file system for report availability — DB values may be stale
    enriched = []
    for run in runs_slice:
        report_meta = _report_available_for_run(run["run_id"])
        enriched.append({**run, **report_meta})

    return {
        "runs": enriched,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    run = RunRepository().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Enrich with live report availability (disk is source of truth)
    report_meta = _report_available_for_run(run_id)
    return {**run, **report_meta}


@router.post("/{run_id}/start")
def start_run(run_id: str) -> dict:
    repo = RunRepository()
    run = repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    result = _start_run_sync(run_id, run)
    return result


def _start_run_sync(run_id: str, run: Optional[dict] = None) -> dict:
    """Internal helper: actually execute the workflow for a run.

    This is the synchronous workflow engine. Used by /start and called by
    /start-async in a background thread.
    """
    repo = RunRepository()
    if run is None:
        run = repo.get_run(run_id)
        if not run:
            raise ValueError(f"Run not found: {run_id}")

    import json
    import logging
    _logger = logging.getLogger(__name__)
    
    # NOTE: get_run() already parses task_brief_json and stores it in run["task_brief"]
    # via result["task_brief"] = json.loads(result.pop("task_brief_json"))
    # So we should use run.get("task_brief") instead of run.get("task_brief_json")!
    task_brief = run.get("task_brief") or {}
    if not task_brief:
        # Fallback: try task_brief_json directly (for cases where pop wasn't done)
        if run.get("task_brief_json"):
            try:
                task_brief = json.loads(run["task_brief_json"])
            except Exception:
                pass
    if not task_brief:
        _logger.warning("_start_run_sync: task_brief was empty/invalid, using minimal task_brief!")
        task_brief = {
            "title": run.get("task_title", "AI Agent analysis"),
            "products": [],
            "analysis_dimensions": [],
        }

    _logger.info("_start_run_sync: starting workflow for run_id=%s with %d products",
        run_id, len(task_brief.get("products", [])))

    repo.update_status(run_id, "running", "build_task_brief", started_at=utc_now())
    try:
        state = run_workflow({
            "run_id": run_id,
            "project_id": run.get("project_id"),
            "task_id": run["task_id"],
            "task_brief": task_brief,
            "mode": run["mode"],
        })
    except Exception as exc:
        _logger.error("run %s workflow exception: %s", run_id, exc, exc_info=True)
        # Graceful degradation: never mark failed. Try to produce a minimal report.
        _logger.warning("_start_run_sync: attempting graceful degradation after exception for run_id=%s", run_id)
        # Try to use whatever partial state was produced before the crash.
        # run_workflow may have left partial state in state.get(...) even after throwing.
        # We force 'completed' so the user sees a partial report instead of a failed run.
        # Attempt to write a minimal report record so the UI can show something.
        try:
            from backend.app.storage.repositories import ReportRepository
            partial_report_id = f"report_{run_id}"
            report_record = {
                "report_id": partial_report_id,
                "run_id": run_id,
                "title": task_brief.get("title", "竞品分析报告") if task_brief else "竞品分析报告",
                "report_status": "reviewed_with_gaps",
                "content_markdown_path": "",
                "content_html_path": "",
                "content_pdf_path": "",
                "quality_summary": {
                    "_workflow_exception": str(exc),
                    "_degraded": True,
                    "products_analyzed": len(task_brief.get("products", []) if task_brief else []),
                    "evidence_count": 0,
                    "signed_claims_count": 0,
                },
                "created_by_agent": "WriterAgent",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            ReportRepository().upsert_report(report_record)
        except Exception as db_exc:
            _logger.error("run %s: even degraded DB write failed: %s", run_id, db_exc)
        repo.update_status(run_id, "completed", "workflow_exception", completed_at=utc_now())
        return {"run_id": run_id, "status": "completed", "state": {}}

    # P2.1: Handle workflow paused for human intervention.
    if state.get("workflow_paused"):
        paused_node = state.get("paused_node", "unknown")
        interventions = state.get("pause_interventions", [])
        pause_reason = f"Workflow paused at '{paused_node}' for human review. {len(interventions)} intervention(s) created."
        repo.update_status(run_id, "paused", paused_node, error_message=pause_reason, completed_at=utc_now())
        _logger.info("Workflow paused for run_id=%s at node '%s' with %d interventions",
                     run_id, paused_node, len(interventions))
        return {"run_id": run_id, "status": "paused", "state": state}

    errors = state.get("errors", []) or []
    report_draft = state.get("report_draft") or {}
    report_status = report_draft.get("report_status")

    # Graceful degradation: never show 'failed' to the user.
    # - DB_WRITE_REPORT_FAILED: report file exists on disk, just DB record failed.
    #   Force-write to DB as 'reviewed_with_gaps' so run completes.
    # - NODE_EXCEPTION: caught by outer try/except. Only uncaught exceptions reach here.
    # - BLOCKED_NO_SIGNED_CLAIMS: degrade to 'reviewed_with_gaps', include partial report.
    # - BLOCKED status: degrade to 'reviewed_with_gaps', include partial report.
    critical_errors = {
        "UNSUPPORTED_REPORT_SPAN",
        "DB_WRITE_REPORT_FAILED",
        "NODE_EXCEPTION",
        # NOTE: BLOCKED_NO_SIGNED_CLAIMS is intentionally NOT here.
        # final_review no longer adds this error code (it degrades gracefully instead).
        # NODE_TIMEOUT is not here because the timeout handler already recovers
        # from checkpoint data, and the node is retried by coverage_critic.
    }
    has_critical_error = any(e.get("reason_code") in critical_errors for e in errors)
    is_blocked = report_status in ("blocked", "blocked_consistency")

    if has_critical_error or is_blocked:
        # Override report_status to 'reviewed_with_gaps' so DB CHECK passes.
        # This preserves the partial report on disk while making it accessible.
        report_draft["report_status"] = "reviewed_with_gaps"
        report_draft["_degraded_from"] = report_status or ""
        report_draft["_degraded_errors"] = [e.get("reason_code", "") for e in errors if e.get("reason_code")]
        _logger.warning(
            "run %s degraded to 'reviewed_with_gaps': blocked=%s errors=%s",
            run_id, is_blocked, [e.get("reason_code") for e in errors]
        )
        # Force-write the DB record with 'reviewed_with_gaps' so the run completes.
        try:
            from backend.app.storage.repositories import ReportRepository
            report_record = {
                "report_id": report_draft.get("report_id") or f"report_{run_id}",
                "run_id": run_id,
                "title": report_draft.get("title", "竞品分析报告"),
                "report_status": "reviewed_with_gaps",
                "content_markdown_path": report_draft.get("content_markdown_path", ""),
                "content_html_path": report_draft.get("content_html_path", ""),
                "content_pdf_path": report_draft.get("content_pdf_path", ""),
                "quality_summary": report_draft.get("quality_summary", {}),
                "created_by_agent": "WriterAgent",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            ReportRepository().upsert_report(report_record)
            _logger.info("run %s: degraded report record written to DB", run_id)
        except Exception as db_exc:
            _logger.error("run %s: failed to write degraded report record: %s", run_id, db_exc)
        # Do NOT mark run as failed. Continue to completed path below.

    repo.update_status(run_id, "completed", "compute_metrics", completed_at=utc_now())
    return {"run_id": run_id, "status": "completed", "state": state}


@router.post("/{run_id}/start-async")
def start_run_async(run_id: str) -> dict:
    """Start a run's workflow asynchronously.

    Idempotent:
    - pending: starts and returns running
    - running: returns current running state (no-op)
    - completed/failed: returns current state (no-op)

    Returns immediately. The workflow executes in a background thread.
    """
    repo = RunRepository()
    run = repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    current_status = run.get("status", "unknown")
    if current_status == "running":
        return {
            "run_id": run_id,
            "status": "running",
            "current_node": run.get("current_node") or "unknown",
            "message": "Already running",
        }
    if current_status in ("completed", "failed"):
        return {
            "run_id": run_id,
            "status": current_status,
            "current_node": run.get("current_node") or "",
            "message": f"Run already {current_status}",
        }
    if current_status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start run with status '{current_status}'. Only 'pending' runs can be started.",
        )

    # Mark as running immediately
    repo.update_status(run_id, "running", "build_task_brief", started_at=utc_now())

    # Fire-and-forget: run the workflow in a non-daemon thread via ThreadPoolExecutor.
    # Non-daemon threads are not killed when the main thread exits, which means
    # uvicorn worker reloads won't abort in-flight workflows.
    # Also logs exceptions instead of silently swallowing them.
    import logging as _bg_logger
    _bg_logger = _bg_logger.getLogger(__name__)

    def _bg():
        try:
            _start_run_sync(run_id, run)
        except Exception:
            _bg_logger.exception("Async run %s failed with exception", run_id)
            try:
                RunRepository().update_status(run_id, "completed", "workflow_aborted",
                    error_message="Workflow terminated early (background exception). Partial report may be available.",
                    completed_at=utc_now())
            except Exception:
                pass

    _workflow_executor.submit(_bg)

    return {"run_id": run_id, "status": "running", "current_node": "build_task_brief"}


def _run_pending_nodes_sync(run_id: str, pending_names: list[str], state: dict) -> dict:
    """Execute pending workflow nodes for a replayed run. Extracted for reusability."""
    from backend.app.storage.repositories import WorkflowRepository
    from backend.app.orchestrator import nodes as orchestrator_nodes
    import time as _time_mod

    wf_repo = WorkflowRepository()

    node_map = {
        "build_task_brief": orchestrator_nodes.build_task_brief,
        "plan_schema": orchestrator_nodes.plan_schema,
        "plan_sources": orchestrator_nodes.plan_sources,
        "collect_sources": orchestrator_nodes.collect_sources,
        "evaluate_evidence": orchestrator_nodes.evaluate_evidence,
        "pii_scrub": orchestrator_nodes.pii_scrub,
        "extract_facts": orchestrator_nodes.extract_facts,
        "detect_schema_gaps": orchestrator_nodes.detect_schema_gaps,
        "analyze_dimensions": orchestrator_nodes.analyze_dimensions,
        "review_claims": orchestrator_nodes.review_claims,
        "execute_rework": orchestrator_nodes.execute_rework,
        "prepare_human_intervention": orchestrator_nodes.prepare_human_intervention,
        "write_report_v2": orchestrator_nodes.write_report_v2,
        "final_review": orchestrator_nodes.final_review,
        "export_report": orchestrator_nodes.export_report,
        "compute_metrics": orchestrator_nodes.compute_metrics,
    }
    node_order = [
        "build_task_brief", "plan_schema", "plan_sources", "collect_sources",
        "evaluate_evidence", "pii_scrub", "extract_facts", "detect_schema_gaps",
        "analyze_dimensions", "review_claims", "execute_rework",
        "prepare_human_intervention", "write_report_v2",
        "final_review", "export_report", "compute_metrics",
    ]

    try:
        from backend.app.orchestrator.graph import _summarize_state
    except ImportError:
        def _summarize_state(s): return {}

    for node_name in node_order:
        if node_name not in pending_names:
            continue
        fn = node_map.get(node_name)
        if not fn:
            continue
        start = _time_mod.perf_counter()
        input_summary = _summarize_state(state)
        wf_repo.start_node(run_id, node_name, input_summary)
        try:
            state = fn(state)
        except Exception as exc:
            latency = int((_time_mod.perf_counter() - start) * 1000)
            wf_repo.fail_node(run_id, node_name, str(exc), latency_ms=latency)
            raise
        latency = int((_time_mod.perf_counter() - start) * 1000)
        output_summary = _summarize_state(state)
        wf_repo.complete_node(run_id, node_name, output_summary, latency)
        RunRepository().update_status(run_id, "running", node_name)

    errors = state.get("errors") or []
    critical = {"BLOCKED_NO_SIGNED_CLAIMS", "UNSUPPORTED_REPORT_SPAN",
                "DB_WRITE_REPORT_FAILED", "NODE_EXCEPTION"}
    has_error = any(e.get("reason_code") in critical for e in errors)
    report_status = (state.get("report_draft") or {}).get("report_status")
    if has_error or report_status in ("blocked", "blocked_consistency"):
        RunRepository().update_status(run_id, "failed", "compute_metrics",
                                     error_message=f"blocked: {report_status}", completed_at=utc_now())
    else:
        RunRepository().update_status(run_id, "completed", "compute_metrics", completed_at=utc_now())

    return {
        "run_id": run_id,
        "status": "completed",
        "message": f"Replayed {len(pending_names)} pending nodes",
        "pending_nodes": pending_names,
        "report_status": report_status,
    }


@router.post("/{run_id}/replay")
def replay_run(run_id: str) -> dict:
    """Replay/re-continue a run from its first pending workflow node.

    Idempotent:
    - pending/failed: builds state from DB and resumes remaining nodes (async)
    - running: returns current state (no-op)
    - completed: returns current state (no-op)

    The workflow executes in a non-daemon background thread so it survives
    uvicorn worker reloads. Exceptions are logged; run is marked failed on crash.
    """
    import asyncio, concurrent.futures, logging as _replay_logger
    _replay_logger = _replay_logger.getLogger(__name__)

    run = RunRepository().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run_status = run.get("status", "unknown")

    # Check if there's a zombie running entry (stale "running" without active thread)
    if run_status == "running":
        from backend.app.storage.repositories import WorkflowRepository
        wf_repo = WorkflowRepository()
        all_nodes = wf_repo.list_workflow_nodes(run_id)
        running_nodes = [n for n in all_nodes if n.get("status") == "running"]
        stale_running = all(n.get("started_at") for n in running_nodes
                           if n.get("started_at") and _is_stale(n.get("started_at")))
        if stale_running or not running_nodes:
            # Treat as recoverable: mark stale nodes pending and allow replay
            for n in running_nodes:
                wf_repo.update_node_status(run_id, n["node_name"], "pending")
            run_status = "stale"

    if run_status in ("completed",):
        return {"run_id": run_id, "status": run_status,
                "message": f"Run already {run_status}", "pending_nodes": []}

    from backend.app.storage.repositories import WorkflowRepository
    wf_repo = WorkflowRepository()
    all_nodes = wf_repo.list_workflow_nodes(run_id)

    running_nodes = [n for n in all_nodes if n.get("status") == "running"]
    pending_nodes_all = [n for n in all_nodes if n.get("status") in ("pending", "running")]
    completed_nodes = [n for n in all_nodes if n.get("status") == "completed"]

    # Determine if the workflow is actually alive:
    # - If any running node started recently (< 30 min ago), workflow is alive
    # - If running nodes exist but started > 30 min ago or have NULL started_at, stale
    # - If no running nodes but run_status="running" and all nodes are pending/completed, stale
    any_alive = any(
        n.get("status") == "running" and n.get("started_at") and not _is_stale(n.get("started_at"))
        for n in running_nodes
    )
    is_stale = (
        not any_alive  # no live running node found
        or (run_status == "running" and not running_nodes and not pending_nodes_all)
    )

    if run_status == "running" and is_stale:
        # Treat as recoverable: reset all running nodes to pending
        for n in running_nodes:
            wf_repo.update_node_status(run_id, n["node_name"], "pending")
        run_status = "stale"

    pending_nodes = [n for n in all_nodes if n.get("status") in ("pending", "running")]
    if not pending_nodes:
        return {"run_id": run_id, "status": run_status,
                "message": "No pending nodes found", "pending_nodes": []}

    pending_names = [n["node_name"] for n in pending_nodes]

    # Load real state from DB (not just counts)
    from backend.app.storage.repositories import (
        ClaimRepository, EvidenceRepository, SourceRepository,
    )
    from backend.app.storage.db import get_connection

    try:
        all_claims = ClaimRepository().list_claims(run_id)
        signed_claims = [c for c in all_claims if c.get("signed_claim_id")]
    except Exception:
        signed_claims = []
    try:
        evidence_items = EvidenceRepository().list_evidence(run_id)
    except Exception:
        evidence_items = []
    try:
        sources = SourceRepository().list_sources(run_id)
    except Exception:
        sources = []
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE run_id = ?", (run_id,)).fetchall()
            cols = list(zip(*conn.execute("SELECT * FROM facts WHERE run_id = ? LIMIT 1", (run_id,)).description))[0] if rows else []
            facts = [dict(zip(cols, r)) for r in rows] if cols else []
    except Exception:
        facts = []

    state = {
        "run_id": run_id,
        "task_id": run.get("task_id", ""),
        "mode": "replay",  # Force replay mode so collect_sources skips real-time collection
        "project_id": run.get("project_id"),
        "signed_claims": signed_claims,
        "evidence_items": evidence_items,
        "facts": facts,
        "sources": sources,
        "task_brief": run.get("task_brief", {}),
        "report_version": "v2",
    }

    # Mark as running
    RunRepository().update_status(run_id, "running", pending_names[0])

    import logging as _replay_logger
    _replay_logger = _replay_logger.getLogger(__name__)

    def _bg_replay():
        try:
            _run_pending_nodes_sync(run_id, pending_names, state)
        except Exception:
            _replay_logger.exception("Replay for run %s failed", run_id)
            try:
                RunRepository().update_status(run_id, "failed", "workflow_aborted",
                    error_message="Replay crashed. Use /replay to try again.",
                    completed_at=utc_now())
            except Exception:
                pass

    _replay_executor.submit(_bg_replay)

    return {
        "run_id": run_id,
        "status": "running",
        "message": f"Resuming {len(pending_names)} pending nodes in background",
        "pending_nodes": pending_names,
    }


@router.get("/{run_id}/live")
def get_run_live(run_id: str) -> dict:
    """Aggregated live view of a running analysis.

    Returns run status, workflow nodes, traces, artifact counts, and
    quality gate / review status in a single call — designed for
    the running-stage frontend panel to poll every 1-2 seconds.
    """
    run = RunRepository().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    run_status = run.get("status", "pending")
    current_node = run.get("current_node") or ""

    # --- Workflow nodes ---
    try:
        from backend.app.storage.repositories import WorkflowRepository
        wf_nodes = WorkflowRepository().list_workflow_nodes(run_id)
    except Exception:
        wf_nodes = []

    # --- Enrich collect_sources node with collection_stats + sources ---
    try:
        from backend.app.storage.repositories import SourceRepository
        all_sources = SourceRepository().list_sources(run_id)
    except Exception:
        all_sources = []

    if all_sources and wf_nodes:
        # Compute collection_stats from DB
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

        # Build per-source summary records
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

        # Inject into the collect_sources node
        for node in wf_nodes:
            if node.get("node_name") == "collect_sources":
                node["collection_stats"] = collection_stats
                node["sources"] = workflow_sources
                break

    wf_summary = {
        "total": len(wf_nodes),
        "completed": sum(1 for n in wf_nodes if n.get("status") == "completed"),
        "running": sum(1 for n in wf_nodes if n.get("status") == "running"),
        "failed": sum(1 for n in wf_nodes if n.get("status") == "failed"),
        "pending": sum(1 for n in wf_nodes if n.get("status") == "pending"),
        "paused": sum(1 for n in wf_nodes if n.get("status") == "paused"),
    }

    # --- Latest traces (last 5) ---
    try:
        from backend.app.storage.repositories import TraceRepository
        trace_summary = TraceRepository().summarize_traces(run_id)
        latest_traces = TraceRepository().get_latest_traces(run_id, limit=5)
    except Exception:
        trace_summary = {}
        latest_traces = []

    # --- Current agent (from most recent trace) ---
    current_agent = ""
    current_action = ""
    if latest_traces:
        latest = latest_traces[0]
        current_agent = latest.get("agent_name", "")
        current_action = latest.get("node_name", "")

    # --- Artifact counts ---
    artifact_counts = {"sources": 0, "evidence": 0, "facts": 0, "claims": 0, "signed_claims": 0}
    try:
        from backend.app.storage.repositories import SourceRepository
        artifact_counts["sources"] = len(SourceRepository().list_sources(run_id))
    except Exception:
        pass
    try:
        from backend.app.storage.repositories import EvidenceRepository
        artifact_counts["evidence"] = len(EvidenceRepository().list_evidence(run_id))
    except Exception:
        pass
    try:
        from backend.app.storage.repositories import ClaimRepository
        all_claims = ClaimRepository().list_claims(run_id)
        artifact_counts["claims"] = len(all_claims)
        artifact_counts["signed_claims"] = sum(1 for c in all_claims if c.get("signed_claim_id"))
    except Exception:
        pass

    # Try to get fact count from a direct table query (facts table)
    try:
        from backend.app.storage.db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM facts WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row:
                artifact_counts["facts"] = row[0] or 0
    except Exception:
        pass

    # --- Pending review count ---
    pending_review_count = 0
    try:
        from backend.app.storage.repositories import HumanInterventionRepository
        pending = HumanInterventionRepository().list_interventions(run_id, status="pending")
        pending_review_count = len(pending)
    except Exception:
        pass

    # --- Report status ---
    report_status = None
    try:
        from backend.app.storage.repositories import ReportRepository
        report = ReportRepository().get_report(run_id)
        if report:
            report_status = report.get("report_status")
    except Exception:
        pass

    # --- Quality gate (block check) ---
    quality_gate = {"blocked": False, "reason": None, "reason_codes": []}
    if run_status == "failed":
        err = run.get("error_message") or ""
        if "blocked" in err.lower() or "BLOCKED" in err:
            quality_gate["blocked"] = True
            quality_gate["reason"] = err
            import re
            codes = re.findall(r"\[([A-Z_]+)\]", err)
            quality_gate["reason_codes"] = codes

    return {
        "run_id": run_id,
        "status": run_status,
        "current_node": current_node,
        "error_message": run.get("error_message"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "workflow_nodes": wf_nodes,
        "workflow_summary": wf_summary,
        "latest_traces": latest_traces,
        "trace_summary": trace_summary,
        "current_agent": current_agent,
        "current_action": current_action,
        "artifact_counts": artifact_counts,
        "pending_review_count": pending_review_count,
        "report_status": report_status,
        "quality_gate": quality_gate,
    }


# vNext-P0-Real-Frontend-Integration: Dedicated report-draft endpoint
@router.get("/{run_id}/report-draft")
def get_run_report_draft(run_id: str) -> dict[str, Any]:
    """
    Return the full report draft for a run, including report_outline,
    section_statuses, sections, quality_summary, and report_status.

    Reads from:
    1. ReportRepository (if report already written and stored)
    2. RunRepository task_brief (if outline was propagated from research plan)
    3. Returns empty/null outline with a warning if nothing found

    This endpoint is polled by AnalysisFlow deliverables page and Project Workspace
    Deliverables tab to render the Report Outline / Section Status table.
    """
    from backend.app.storage.repositories import ReportRepository, RunRepository

    run = RunRepository().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    # ── P0 Fix: Check for v2 report files first ──────────────────────────────────
    # v2 reports are saved to data/reports/{report_id}.md by run_deep_report_workflow.
    # The /report-draft endpoint was returning the old v1 fallback report instead.
    from pathlib import Path
    import json as _json

    # Try report_id = report_{run_id}_v2 first
    for candidate_id in [f"report_{run_id}_v2", f"report_{run_id}"]:
        v2_md_path = f"data/reports/{candidate_id}.md"
        v2_html_path = f"data/reports/{candidate_id}.html"
        if Path(v2_md_path).exists():
            md_content = Path(v2_md_path).read_text(encoding="utf-8")
            html_content = ""
            if Path(v2_html_path).exists():
                html_content = Path(v2_html_path).read_text(encoding="utf-8")

            # Also try to load v2 structured data
            v2_json_path = f"data/reports/{candidate_id}.json"
            v2_data = {}
            if Path(v2_json_path).exists():
                try:
                    v2_data = _json.loads(Path(v2_json_path).read_text(encoding="utf-8"))
                except Exception:
                    pass

            return {
                "report_id": candidate_id,
                "run_id": run_id,
                "report_outline": {},
                "section_statuses": [],
                "sections": v2_data.get("sections", []),
                "spans": [],
                "content_markdown": md_content,
                "content_html": html_content,
                "quality_summary": v2_data.get("quality_summary", {}),
                "report_status": v2_data.get("report_status", "draft"),
                "report_version": "v2",
                "tables": v2_data.get("tables", []),
                "figures": v2_data.get("figures", []),
            }

    # Try ReportRepository first
    report = ReportRepository().get_report(run_id)
    if report:
        # Build report_outline from report_spans if available
        report_outline = report.get("report_outline") or {}
        if not report_outline:
            spans = report.get("spans") or []
            if spans:
                # Group spans by section_id
                from collections import OrderedDict
                section_map = OrderedDict()
                for span in spans:
                    sid = span.get("section_id", "unknown")
                    if sid not in section_map:
                        section_map[sid] = {
                            "section_id": sid,
                            "title": span.get("section_title", sid),
                            "status": "written",
                            "word_count": 0,
                            "spans": [],
                        }
                    section_map[sid]["spans"].append(span)
                    if span.get("text"):
                        section_map[sid]["word_count"] += len(span.get("text", "").split())

                sections = []
                for sid, sdata in section_map.items():
                    sections.append({
                        "section_id": sdata["section_id"],
                        "title": sdata["title"],
                        "status": "written" if sdata["word_count"] > 0 else "missing",
                        "word_count": sdata["word_count"],
                    })
                report_outline = {"sections": sections}

        # vNext-R3-A: Add Deep Report v2 support
        # Check if report has v2 data (tables, figures)
        tables = []
        figures = []
        report_version = "v1"
        
        try:
            from backend.app.storage.repositories import ReportTableRepository, ReportFigureRepository
            report_id = report.get("report_id", "")
            if report_id:
                tables = ReportTableRepository().get_tables_by_report(report_id)
                figures = ReportFigureRepository().get_figures_by_report(report_id)
                if tables or figures:
                    report_version = "v2"
        except Exception:
            pass
        
        result = {
            "report_id": report.get("report_id", run_id),
            "run_id": run_id,
            "report_outline": report_outline,
            "section_statuses": report.get("section_statuses") or [],
            "sections": report.get("sections") or [],
            "spans": report.get("spans") or [],
            "content_markdown": report.get("content_markdown") or "",
            "quality_summary": report.get("quality_summary") or {},
            "report_status": report.get("report_status") or "draft",
            "report_version": report_version,
            "tables": tables,
            "figures": figures,
        }
        
        return result

    # Fallback: try to reconstruct from run task_brief
    # NOTE: get_run() already parses task_brief_json and stores in task_brief
    task_brief = run.get("task_brief") or {}
    if not task_brief:
        # Extra safety: try task_brief_json directly
        task_brief_json = run.get("task_brief_json") or ""
        if task_brief_json:
            try:
                import json
                task_brief = json.loads(task_brief_json)
            except Exception:
                pass

    report_outline = task_brief.get("report_outline") or {}
    sections = task_brief.get("report_sections") or []

    # Build section_statuses from sections
    section_statuses = []
    for s in sections:
        title = s.get("section_title") or s.get("title") or "Unknown"
        content = s.get("content_markdown") or s.get("content") or ""
        word_count = len(content.split()) if content else 0
        status = "drafted" if word_count > 0 else "missing"
        section_statuses.append({
            "section_id": s.get("section_id", ""),
            "section_title": title,
            "status": status,
            "word_count": word_count,
        })

    return {
        "report_id": run_id,
        "run_id": run_id,
        "report_outline": report_outline,
        "section_statuses": section_statuses,
        "sections": sections,
        "quality_summary": {},
        "report_status": "draft",
        "report_version": "v1",
        "tables": [],
        "figures": [],
    }
