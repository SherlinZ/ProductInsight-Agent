from __future__ import annotations

from fastapi import APIRouter

from backend.app.storage.repositories import ClaimRepository, ReviewRepository


router = APIRouter(tags=["reviews"])


@router.get("/api/runs/{run_id}/review-items")
def list_review_items(run_id: str) -> dict:
    return {
        "claims": ClaimRepository().list_claims(run_id),
        "reviews": ReviewRepository().list_reviews(run_id),
        "rework_requests": ReviewRepository().list_rework_requests(run_id),
    }
