from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    run_id: str
    task_id: str
    task_brief: Dict[str, Any]
    schema_plan: Dict[str, Any]
    source_plan: Dict[str, Any]
    sources: List[Dict[str, Any]]
    snapshots: List[Dict[str, Any]]
    evidence_items: List[Dict[str, Any]]
    facts: List[Dict[str, Any]]
    claim_drafts: List[Dict[str, Any]]
    review_results: List[Dict[str, Any]]
    rework_requests: List[Dict[str, Any]]
    signed_claims: List[Dict[str, Any]]
    report_draft: Optional[Dict[str, Any]]
    metrics: Dict[str, Any]
    errors: List[Dict[str, Any]]
    retry_count: Dict[str, int]
    mode: str
