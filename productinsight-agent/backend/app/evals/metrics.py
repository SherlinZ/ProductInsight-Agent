from __future__ import annotations

import json


def compute_evidence_coverage_rate(claims: list[dict]) -> float:
    if not claims:
        return 0.0
    covered = 0
    for claim in claims:
        evidence_ids = json.loads(claim.get("evidence_ids_json") or "[]")
        if evidence_ids:
            covered += 1
    return covered / len(claims)


def compute_unsupported_claim_rate(claims: list[dict]) -> float:
    if not claims:
        return 0.0
    unsupported = 0
    for claim in claims:
        evidence_ids = json.loads(claim.get("evidence_ids_json") or "[]")
        if not evidence_ids:
            unsupported += 1
    return unsupported / len(claims)


def compute_review_pass_rate(claims: list[dict]) -> float:
    if not claims:
        return 0.0
    signed = sum(1 for claim in claims if claim.get("review_status") == "signed")
    return signed / len(claims)
