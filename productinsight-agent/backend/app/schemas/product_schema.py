from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


MaturityLevel = Literal["unknown", "basic", "standard", "advanced"]
TriState = bool | Literal["unknown"]


class EvidenceBoundItem(BaseModel):
    evidence_ids: list[str] = Field(default_factory=list)


class ProductProfile(BaseModel):
    product_id: str
    product_name: str
    company_name: str | None = None
    official_website: str | None = None
    region: str | None = None
    product_type: str | None = None
    target_market: str | None = None
    release_status: str | None = None
    short_description: str | None = None


class Capability(EvidenceBoundItem):
    capability_name: str
    description: str | None = None
    maturity_level: MaturityLevel = "unknown"


class PricingPlan(EvidenceBoundItem):
    plan_name: str
    price: str | None = None
    billing_unit: str | None = None
    target_user: str | None = None
    main_limits: list[str] = Field(default_factory=list)


class PricingModel(BaseModel):
    has_free_plan: TriState = "unknown"
    free_plan_description: str | None = None
    paid_plans: list[PricingPlan] = Field(default_factory=list)
    enterprise_pricing: str | None = None
    trial_policy: str | None = None
    pricing_page_url: str | None = None
    pricing_evidence_ids: list[str] = Field(default_factory=list)


class UserPersona(EvidenceBoundItem):
    persona_name: str
    description: str | None = None
    main_jobs_to_be_done: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)


class CustomerVoice(BaseModel):
    positive_feedback: list[str] = Field(default_factory=list)
    negative_feedback: list[str] = Field(default_factory=list)
    common_requests: list[str] = Field(default_factory=list)
    survey_summary: list[str] = Field(default_factory=list)
    interview_summary: list[str] = Field(default_factory=list)
    source_distribution: dict[str, int] = Field(default_factory=dict)


class Swot(BaseModel):
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    threats: list[str] = Field(default_factory=list)


class BaseCompetitiveSchema(BaseModel):
    schema_name: str = "BaseCompetitiveSchema"
    schema_version: str = "1.0.0"
    product_profile: ProductProfile
    function_tree: dict[str, list[Capability]] = Field(default_factory=dict)
    pricing_model: PricingModel = Field(default_factory=PricingModel)
    user_persona: dict[str, list[UserPersona]] = Field(default_factory=dict)
    customer_voice: CustomerVoice = Field(default_factory=CustomerVoice)
    swot: Swot = Field(default_factory=Swot)


class AgentBuilderMode(EvidenceBoundItem):
    supports_no_code_builder: TriState = "unknown"
    supports_low_code_builder: TriState = "unknown"
    supports_code_first_development: TriState = "unknown"
    description: str | None = None


class WorkflowOrchestration(EvidenceBoundItem):
    supports_dag_or_workflow: TriState = "unknown"
    supports_conditional_branch: TriState = "unknown"
    supports_parallel_execution: TriState = "unknown"
    supports_human_in_the_loop: TriState = "unknown"


class ToolCalling(EvidenceBoundItem):
    supports_external_tools: TriState = "unknown"
    supports_function_calling: TriState = "unknown"
    supports_api_integration: TriState = "unknown"
    tool_ecosystem_description: str | None = None


class KnowledgeBase(EvidenceBoundItem):
    supports_rag: TriState = "unknown"
    supported_data_types: list[str] = Field(default_factory=list)
    retrieval_features: list[str] = Field(default_factory=list)


class MemoryAndContext(EvidenceBoundItem):
    supports_short_term_memory: TriState = "unknown"
    supports_long_term_memory: TriState = "unknown"
    context_management_features: list[str] = Field(default_factory=list)


class MultiAgentSupport(EvidenceBoundItem):
    supports_multi_agent: TriState = "unknown"
    coordination_mode: str | None = None
    role_management: str | None = None


class DeploymentOptions(EvidenceBoundItem):
    cloud_hosted: TriState = "unknown"
    self_hosted: TriState = "unknown"
    private_deployment: TriState = "unknown"
    open_source: TriState = "unknown"
    license: str | None = None


class EnterpriseReadiness(EvidenceBoundItem):
    permission_control: str | None = None
    audit_log: str | None = None
    sso_support: str | None = None
    data_security: str | None = None
    compliance_claims: list[str] = Field(default_factory=list)


class Observability(EvidenceBoundItem):
    trace_support: str | None = None
    debugging_tools: str | None = None
    evaluation_tools: str | None = None


class Ecosystem(EvidenceBoundItem):
    template_marketplace: str | None = None
    plugin_marketplace: str | None = None
    community_activity: str | None = None
    developer_resources: list[str] = Field(default_factory=list)


class AgentProductCapabilities(BaseModel):
    agent_builder_mode: AgentBuilderMode = Field(default_factory=AgentBuilderMode)
    workflow_orchestration: WorkflowOrchestration = Field(default_factory=WorkflowOrchestration)
    tool_calling: ToolCalling = Field(default_factory=ToolCalling)
    knowledge_base: KnowledgeBase = Field(default_factory=KnowledgeBase)
    memory_and_context: MemoryAndContext = Field(default_factory=MemoryAndContext)
    multi_agent_support: MultiAgentSupport = Field(default_factory=MultiAgentSupport)
    deployment_options: DeploymentOptions = Field(default_factory=DeploymentOptions)
    enterprise_readiness: EnterpriseReadiness = Field(default_factory=EnterpriseReadiness)
    observability: Observability = Field(default_factory=Observability)
    ecosystem: Ecosystem = Field(default_factory=Ecosystem)


class AIAgentProductSchema(BaseCompetitiveSchema):
    schema_name: str = "AIAgentProductSchema"
    extends: str = "BaseCompetitiveSchema"
    agent_product_capabilities: AgentProductCapabilities = Field(default_factory=AgentProductCapabilities)


class RuntimeObject(BaseModel):
    run_id: str
    product_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
