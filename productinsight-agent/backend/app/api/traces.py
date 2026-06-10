"""Trace API endpoints for ProductInsight Agent."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.app.storage.repositories import TraceRepository


router = APIRouter(tags=["traces"])


@router.get("/api/runs/{run_id}/traces")
def list_traces(
    run_id: str,
    node_name: Optional[str] = Query(None, description="Filter by node name"),
    agent_name: Optional[str] = Query(None, description="Filter by agent name"),
    status: Optional[str] = Query(None, description="Filter by status (success/failed/running)"),
) -> list[dict]:
    """List all traces for a run, with optional filters."""
    return TraceRepository().list_traces(
        run_id=run_id,
        node_name=node_name,
        agent_name=agent_name,
        status=status,
    )


@router.get("/api/runs/{run_id}/trace-summary")
def get_trace_summary(run_id: str) -> dict:
    """Return summary statistics for all traces of a run."""
    return TraceRepository().summarize_traces(run_id)


@router.get("/api/traces/{trace_id}")
def get_trace(trace_id: str) -> dict:
    """Return a single trace by ID."""
    trace = TraceRepository().get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@router.get("/api/runs/{run_id}/trace-latest")
def get_latest_traces(
    run_id: str,
    limit: int = Query(5, ge=1, le=20, description="Number of recent traces to return"),
) -> list[dict]:
    """Return the most recent traces for a run."""
    return TraceRepository().get_latest_traces(run_id, limit=limit)


@router.get("/api/runs/{run_id}/node-io")
def get_node_io(run_id: str) -> list[dict]:
    """Return per-node input/output/artifact summary for a run."""
    return TraceRepository().get_node_io_summary(run_id)


@router.get("/api/runs/{run_id}/dag-status")
def get_dag_status(run_id: str) -> list[dict]:
    """Return DAG node status derived from trace data."""
    traces = TraceRepository().list_traces(run_id)
    trace_map = {t.get("node_name"): t for t in traces}
    node_order = [
        "build_task_brief", "plan_schema", "plan_sources", "collect_sources",
        "pii_scrub", "extract_facts", "analyze_dimensions", "review_claims",
        "write_report", "final_review", "export_report", "compute_metrics",
    ]
    result = []
    for node_name in node_order:
        t = trace_map.get(node_name, {})
        result.append({
            "node_name": node_name,
            "status": t.get("status", "pending"),
            "latency_ms": t.get("latency_ms"),
            "model_name": t.get("model_name"),
            "token_input": t.get("token_input"),
            "token_output": t.get("token_output"),
        })
    return result
