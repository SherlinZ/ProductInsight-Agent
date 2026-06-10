from __future__ import annotations

from backend.app.storage.repositories import EvidenceRepository


class EvidenceService:
    def __init__(self) -> None:
        self.repo = EvidenceRepository()

    def list_evidence(self, run_id: str, product_id: str | None = None) -> list[dict]:
        return self.repo.list_evidence(run_id, product_id)

    def get_evidence(self, evidence_id: str) -> dict | None:
        return self.repo.get_evidence(evidence_id)
