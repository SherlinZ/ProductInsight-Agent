from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class AnalysisClaim(BaseModel):
    claim_id: str
    run_id: str
    product_id: str | None = None
    dimension: str
    claim_text: str
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["low", "medium", "high"]
    claim_type: str
    review_status: Literal["pending", "signed", "rework_required", "rejected"] = "pending"


class SignedClaim(BaseModel):
    signed_claim_id: str
    claim_id: str
    run_id: str
    signed_by: str
    signed_at: str
    evidence_ids: list[str]
    confidence: float
