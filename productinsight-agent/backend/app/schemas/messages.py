from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


MessageType = Literal[
    "TASK_BRIEF",
    "SCHEMA_PLAN",
    "SOURCE_PLAN",
    "SOURCE_RESULT",
    "EXTRACTION_RESULT",
    "ANALYSIS_RESULT",
    "REVIEW_RESULT",
    "REWORK_REQUEST",
    "SIGNED_CLAIM_RESULT",
    "REPORT_DRAFT",
    "FINAL_REVIEW_RESULT",
    "ERROR_EVENT",
    "HUMAN_FEEDBACK",
]


class MessageMetadata(BaseModel):
    priority: Literal["low", "normal", "high"] = "normal"
    retry_count: int = 0
    trace_id: str
    parent_message_id: str | None = None


class AgentMessage(BaseModel):
    message_id: str
    run_id: str
    task_id: str
    sender: str
    receiver: str
    message_type: MessageType
    schema_version: str = "1.0.0"
    created_at: str
    payload: dict[str, Any]
    metadata: MessageMetadata


class TaskProduct(BaseModel):
    product_id: str
    product_name: str
    seed_urls: list[str] = Field(default_factory=list)


class RequiredOutput(BaseModel):
    report_format: Literal["html", "markdown", "pdf"] = "html"
    include_evidence_links: bool = True
    include_swot: bool = True
    include_comparison_table: bool = True


class TaskConstraints(BaseModel):
    max_sources_per_product: int = 8
    allow_web_search: bool = True
    allow_uploaded_docs: bool = True
    allow_survey_data: bool = True
    allow_interview_data: bool = True


class TaskBriefPayload(BaseModel):
    task_goal: str
    target_region: Literal["global", "china", "custom"] = "global"
    products: list[TaskProduct]
    analysis_dimensions: list[str]
    required_output: RequiredOutput = Field(default_factory=RequiredOutput)
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)


class SourceQuery(BaseModel):
    query_id: str
    query_text: str
    expected_schema_keys: list[str] = Field(default_factory=list)


class CompliancePolicy(BaseModel):
    check_robots: bool = True
    public_sources_only: bool = True
    pii_scrub_required: bool = True


class SourcePlanPayload(BaseModel):
    source_plan_id: str
    product_id: str
    target_source_types: list[str]
    queries: list[SourceQuery]
    seed_urls: list[str] = Field(default_factory=list)
    compliance_policy: CompliancePolicy = Field(default_factory=CompliancePolicy)


class ReviewCheck(BaseModel):
    check_name: str
    status: Literal["pass", "fail", "warning"]
    details: str


class ReviewResultPayload(BaseModel):
    review_result_id: str
    review_target_type: Literal["claim", "report", "schema", "source"]
    review_target_id: str
    status: Literal["pass", "rework_required", "rejected", "warning"]
    checks: list[ReviewCheck]
    reason_codes: list[str] = Field(default_factory=list)
    comments: str | None = None
    signed_claim_id: str | None = None


class AffectedObject(BaseModel):
    object_type: str
    object_id: str


class RequiredAction(BaseModel):
    action_type: str
    product_id: str | None = None
    schema_keys: list[str] = Field(default_factory=list)
    required_source_types: list[str] = Field(default_factory=list)
    min_new_evidence_count: int = 1


class ReworkRequestPayload(BaseModel):
    rework_id: str
    target_agent: str
    target_node: str
    affected_objects: list[AffectedObject]
    reason_codes: list[str]
    required_actions: list[RequiredAction]
    success_criteria: dict[str, Any]
    max_retry: int = 2


class ErrorEventPayload(BaseModel):
    error_id: str
    node_name: str
    agent_name: str
    error_type: Literal["timeout", "schema_validation_error", "model_error", "compliance_blocked", "unknown"]
    error_message: str
    retryable: bool = True
    fallback_action: str | None = None
    created_at: str
