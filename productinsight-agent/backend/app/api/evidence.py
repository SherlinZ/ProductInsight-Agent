from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.app.storage.repositories import SourceRepository, EvidenceRepository


router = APIRouter(tags=["evidence"])


@router.get("/api/runs/{run_id}/sources")
def list_sources(run_id: str) -> list[dict]:
    return SourceRepository().list_sources(run_id)


@router.get("/api/runs/{run_id}/evidence")
def list_evidence(run_id: str, product_id: Optional[str] = None) -> list[dict]:
    return EvidenceRepository().list_evidence(run_id, product_id)


@router.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str) -> dict:
    evidence = EvidenceRepository().get_evidence(evidence_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return evidence
