from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.app.storage.repositories import EvalRepository


router = APIRouter(tags=["metrics"])


@router.get("/api/runs/{run_id}/metrics")
def get_metrics(run_id: str) -> dict:
    metrics = EvalRepository().get_latest_eval(run_id)
    if not metrics:
        raise HTTPException(status_code=404, detail="Metrics not found")
    return metrics
