"""
ResearchPlan Schemas Module.

Defines all data models for the Research Plan & Execution DAG Foundation (vNext-R1).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import uuid
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# TaskBrief
# ---------------------------------------------------------------------------

@dataclass
class TaskBrief:
    task_id: str
    project_name: str
    user_query: str
    task_type: str
    target_region: str
    target_audience: str = ""
    business_goal: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskBrief:
        return cls(
            task_id=data.get("task_id", ""),
            project_name=data.get("project_name", ""),
            user_query=data.get("user_query", ""),
            task_type=data.get("task_type", "competitive_analysis"),
            target_region=data.get("target_region", "global"),
            target_audience=data.get("target_audience", ""),
            business_goal=data.get("business_goal", ""),
            created_at=data.get("created_at", utc_now()),
        )


# ---------------------------------------------------------------------------
# CompetitorSpec
# ---------------------------------------------------------------------------

@dataclass
class CompetitorSpec:
    competitor_id: str
    name: str
    company_name: str = ""
    official_url: str = ""
    seed_urls: list[str] = field(default_factory=list)
    known_aliases: list[str] = field(default_factory=list)
    priority: str = "high"  # high, medium, low

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompetitorSpec:
        return cls(
            competitor_id=data.get("competitor_id", ""),
            name=data.get("name", ""),
            company_name=data.get("company_name", ""),
            official_url=data.get("official_url", ""),
            seed_urls=data.get("seed_urls", []),
            known_aliases=data.get("known_aliases", []),
            priority=data.get("priority", "high"),
        )


# ---------------------------------------------------------------------------
# AnalysisDimension
# ---------------------------------------------------------------------------

@dataclass
class AnalysisDimension:
    dimension_id: str
    name: str
    description: str
    required: bool = True
    sub_dimensions: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=lambda: ["facts", "claims", "comparison_matrix"])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnalysisDimension:
        return cls(
            dimension_id=data.get("dimension_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            required=data.get("required", True),
            sub_dimensions=data.get("sub_dimensions", []),
            expected_outputs=data.get("expected_outputs", ["facts", "claims", "comparison_matrix"]),
        )


# ---------------------------------------------------------------------------
# SourcePlan
# ---------------------------------------------------------------------------

@dataclass
class SourcePlan:
    source_plan_id: str
    source_types: list[str] = field(default_factory=lambda: [
        "official_website",
        "documentation",
        "github",
        "pricing_page",
        "community_feedback",
        "questionnaire_or_interview"
    ])
    collection_strategy: str = "Collect official and public sources first, then supplement missing dimensions."
    minimum_sources_per_competitor: int = 2
    minimum_evidence_per_dimension: int = 3
    compliance_notes: list[str] = field(default_factory=lambda: [
        "Respect robots and terms where applicable.",
        "Mask personal information from interviews or questionnaires."
    ])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourcePlan:
        return cls(
            source_plan_id=data.get("source_plan_id", ""),
            source_types=data.get("source_types", []),
            collection_strategy=data.get("collection_strategy", ""),
            minimum_sources_per_competitor=data.get("minimum_sources_per_competitor", 2),
            minimum_evidence_per_dimension=data.get("minimum_evidence_per_dimension", 3),
            compliance_notes=data.get("compliance_notes", []),
        )


# ---------------------------------------------------------------------------
# SourceDiscovery (vNext-R1.5)
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryQuery:
    """Search queries for source discovery."""
    competitor: str
    queries: list[str]
    status: str = "pending"  # pending, searching, completed, failed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryQuery:
        return cls(
            competitor=data.get("competitor", ""),
            queries=data.get("queries", []),
            status=data.get("status", "pending"),
        )


@dataclass
class SourceDiscovery:
    """Source discovery configuration for competitors without URLs."""
    source_discovery_required: bool = False
    auto_discovery_enabled: bool = True
    discovery_queries: list[DiscoveryQuery] = field(default_factory=list)
    source_readiness: str = "ready"  # ready, ready_with_discovery, blocked_before_run

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_discovery_required": self.source_discovery_required,
            "auto_discovery_enabled": self.auto_discovery_enabled,
            "discovery_queries": [q.to_dict() if hasattr(q, 'to_dict') else q for q in self.discovery_queries],
            "source_readiness": self.source_readiness,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceDiscovery:
        queries = [
            DiscoveryQuery.from_dict(q) if isinstance(q, dict) else q
            for q in data.get("discovery_queries", [])
        ]
        return cls(
            source_discovery_required=data.get("source_discovery_required", False),
            auto_discovery_enabled=data.get("auto_discovery_enabled", True),
            discovery_queries=queries,
            source_readiness=data.get("source_readiness", "ready"),
        )

    @staticmethod
    def _generate_queries(name: str, schema_type: str) -> list[str]:
        """Generate schema-specific search queries for source discovery."""
        queries = []

        if schema_type == "knowledge_management":
            queries = [
                f"{name} official website enterprise",
                f"{name} pricing plans team enterprise",
                f"{name} permissions admin security documentation",
                f"{name} AI features Copilot Q&A documentation",
                f"{name} integrations API Slack Google Drive Jira",
                f"{name} templates wiki knowledge base",
                f"{name} G2 reviews enterprise teams",
                f"{name} Confluence Notion migration guide",
            ]
        elif schema_type == "ai_coding_assistant":
            queries = [
                f"{name} official website enterprise",
                f"{name} pricing plans subscription enterprise",
                f"{name} documentation features IDE integration",
                f"{name} vs Cursor Copilot comparison review",
                f"{name} security privacy enterprise documentation",
                f"{name} G2 reviews developers enterprise",
            ]
        elif schema_type == "ai_agent_platform":
            queries = [
                f"{name} official website",
                f"{name} documentation pricing enterprise",
                f"{name} RAG workflow features",
                f"{name} multi-agent orchestration",
                f"{name} GitHub stars enterprise customers",
            ]
        elif schema_type == "pricing_analysis":
            queries = [
                f"{name} pricing plans per user per month enterprise",
                f"{name} free tier limitations features",
                f"{name} enterprise pricing quote contact sales",
                f"{name} AI Copilot add-on pricing per user",
                f"{name} SSO SCIM admin security pricing",
                f"{name} audit compliance HIPAA SOC2 pricing",
                f"{name} premium support pricing SLA",
                f"{name} migration from competitor training cost",
                f"{name} total cost of ownership TCO review",
                f"{name} G2 Capterra pricing value review",
            ]
        else:
            queries = [
                f"{name} official website",
                f"{name} documentation pricing enterprise",
                f"{name} features comparison",
                f"{name} G2 reviews",
            ]

        return queries

    @classmethod
    def from_competitors(cls, competitors: list[dict | Any], schema_type: str = "ai_agent_platform") -> SourceDiscovery:
        """Create SourceDiscovery from competitor list with schema-specific queries."""
        discovery_queries = []
        competitors_needing_discovery = []

        for comp in competitors:
            if hasattr(comp, 'to_dict'):
                comp = comp.to_dict()
            if not isinstance(comp, dict):
                continue

            name = comp.get("name", "")
            official_url = comp.get("official_url", "")
            seed_urls = comp.get("seed_urls", [])

            # Check if this competitor needs source discovery
            if not official_url and not seed_urls:
                competitors_needing_discovery.append(name)
                # Generate schema-specific search queries
                queries = cls._generate_queries(name, schema_type)
                discovery_queries.append(DiscoveryQuery(
                    competitor=name,
                    queries=queries,
                    status="pending",
                ))

        # Determine readiness status
        if competitors_needing_discovery:
            source_readiness = "ready_with_discovery" if discovery_queries else "blocked_before_run"
            source_discovery_required = True
        else:
            source_readiness = "ready"
            source_discovery_required = False

        return cls(
            source_discovery_required=source_discovery_required,
            auto_discovery_enabled=bool(discovery_queries),
            discovery_queries=discovery_queries,
            source_readiness=source_readiness,
        )


# ---------------------------------------------------------------------------
# ReportOutline
# ---------------------------------------------------------------------------

@dataclass
class ReportSection:
    section_id: str
    title: str
    purpose: str
    required_dimensions: list[str] = field(default_factory=list)
    min_words: int = 600
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportSection:
        return cls(
            section_id=data.get("section_id", ""),
            title=data.get("title", ""),
            purpose=data.get("purpose", ""),
            required_dimensions=data.get("required_dimensions", []),
            min_words=data.get("min_words", 600),
            requires_human_review=data.get("requires_human_review", True),
        )


@dataclass
class ReportOutline:
    outline_id: str
    report_title: str
    sections: list[ReportSection] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline_id": self.outline_id,
            "report_title": self.report_title,
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportOutline:
        sections = [
            ReportSection.from_dict(s) if isinstance(s, dict) else s
            for s in data.get("sections", [])
        ]
        return cls(
            outline_id=data.get("outline_id", ""),
            report_title=data.get("report_title", ""),
            sections=sections,
        )


# ---------------------------------------------------------------------------
# ExecutionDAG
# ---------------------------------------------------------------------------

@dataclass
class DAGNode:
    node_id: str
    node_type: str
    agent_name: str
    depends_on: list[str] = field(default_factory=list)
    input_refs: list[str] = field(default_factory=list)
    output_refs: list[str] = field(default_factory=list)
    human_checkpoint: bool = False
    status: str = "pending"  # pending, running, completed, failed, skipped

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGNode:
        return cls(
            node_id=data.get("node_id", ""),
            node_type=data.get("node_type", ""),
            agent_name=data.get("agent_name", ""),
            depends_on=data.get("depends_on", []),
            input_refs=data.get("input_refs", []),
            output_refs=data.get("output_refs", []),
            human_checkpoint=data.get("human_checkpoint", False),
            status=data.get("status", "pending"),
        )


@dataclass
class DAGEdge:
    from_node: str
    to_node: str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_node, "to": self.to_node}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DAGEdge:
        return cls(
            from_node=data.get("from", ""),
            to_node=data.get("to", ""),
        )


@dataclass
class ExecutionDAG:
    dag_id: str
    research_plan_id: str = ""
    nodes: list[DAGNode] = field(default_factory=list)
    edges: list[DAGEdge] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "research_plan_id": self.research_plan_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionDAG:
        nodes = [
            DAGNode.from_dict(n) if isinstance(n, dict) else n
            for n in data.get("nodes", [])
        ]
        edges = [
            DAGEdge.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("edges", [])
        ]
        return cls(
            dag_id=data.get("dag_id", ""),
            research_plan_id=data.get("research_plan_id", ""),
            nodes=nodes,
            edges=edges,
        )


# ---------------------------------------------------------------------------
# HumanCheckpoint
# ---------------------------------------------------------------------------

@dataclass
class HumanCheckpoint:
    checkpoint_id: str
    stage: str
    title: str
    required: bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanCheckpoint:
        return cls(
            checkpoint_id=data.get("checkpoint_id", ""),
            stage=data.get("stage", ""),
            title=data.get("title", ""),
            required=data.get("required", True),
            description=data.get("description", ""),
        )


# ---------------------------------------------------------------------------
# SuccessMetrics
# ---------------------------------------------------------------------------

@dataclass
class SuccessMetrics:
    minimum_signed_claims: int = 15
    minimum_sources_per_competitor: int = 2
    minimum_evidence_items: int = 30
    minimum_report_words: int = 7000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuccessMetrics:
        return cls(
            minimum_signed_claims=data.get("minimum_signed_claims", 15),
            minimum_sources_per_competitor=data.get("minimum_sources_per_competitor", 2),
            minimum_evidence_items=data.get("minimum_evidence_items", 30),
            minimum_report_words=data.get("minimum_report_words", 7000),
        )


# ---------------------------------------------------------------------------
# ResearchPlan (Main Aggregate)
# ---------------------------------------------------------------------------

@dataclass
class ResearchPlan:
    research_plan_id: str
    project_id: str = ""
    status: str = "draft"  # draft, confirmed, in_progress, completed, cancelled
    task_brief: Optional[TaskBrief] = None
    competitors: list[CompetitorSpec] = field(default_factory=list)
    analysis_dimensions: list[AnalysisDimension] = field(default_factory=list)
    source_plan: Optional[SourcePlan] = None
    source_discovery: Optional[SourceDiscovery] = None  # vNext-R1.5
    report_outline: Optional[ReportOutline] = None
    execution_dag: Optional[ExecutionDAG] = None
    human_checkpoints: list[HumanCheckpoint] = field(default_factory=list)
    success_metrics: Optional[SuccessMetrics] = None
    research_questions: list[str] = field(default_factory=list)  # vNext-R1.6
    generated_by: str = "fallback"  # llm, fallback, human_edited
    user_query: str = ""
    schema_type: str = "ai_agent_platform"
    target_region: str = "global"
    mode: str = "review"  # auto, review, expert
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    confirmed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_plan_id": self.research_plan_id,
            "project_id": self.project_id,
            "status": self.status,
            "task_brief": self.task_brief.to_dict() if self.task_brief else None,
            "competitors": [c.to_dict() for c in self.competitors],
            "analysis_dimensions": [d.to_dict() for d in self.analysis_dimensions],
            "source_plan": self.source_plan.to_dict() if self.source_plan else None,
            "source_discovery": self.source_discovery.to_dict() if self.source_discovery else None,
            "report_outline": self.report_outline.to_dict() if self.report_outline else None,
            "execution_dag": self.execution_dag.to_dict() if self.execution_dag else None,
            "human_checkpoints": [h.to_dict() for h in self.human_checkpoints],
            "success_metrics": self.success_metrics.to_dict() if self.success_metrics else None,
            "research_questions": self.research_questions,
            "generated_by": self.generated_by,
            "user_query": self.user_query,
            "schema_type": self.schema_type,
            "target_region": self.target_region,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchPlan:
        task_brief = None
        if data.get("task_brief"):
            tb = data["task_brief"]
            task_brief = TaskBrief.from_dict(tb) if isinstance(tb, dict) else tb

        source_plan = None
        if data.get("source_plan"):
            sp = data["source_plan"]
            source_plan = SourcePlan.from_dict(sp) if isinstance(sp, dict) else sp

        source_discovery = None
        if data.get("source_discovery"):
            sd = data["source_discovery"]
            source_discovery = SourceDiscovery.from_dict(sd) if isinstance(sd, dict) else sd

        report_outline = None
        if data.get("report_outline"):
            ro = data["report_outline"]
            report_outline = ReportOutline.from_dict(ro) if isinstance(ro, dict) else ro

        execution_dag = None
        if data.get("execution_dag"):
            ed = data["execution_dag"]
            execution_dag = ExecutionDAG.from_dict(ed) if isinstance(ed, dict) else ed

        success_metrics = None
        if data.get("success_metrics"):
            sm = data["success_metrics"]
            success_metrics = SuccessMetrics.from_dict(sm) if isinstance(sm, dict) else sm

        competitors = [
            CompetitorSpec.from_dict(c) if isinstance(c, dict) else c
            for c in data.get("competitors", [])
        ]

        analysis_dimensions = [
            AnalysisDimension.from_dict(d) if isinstance(d, dict) else d
            for d in data.get("analysis_dimensions", [])
        ]

        human_checkpoints = [
            HumanCheckpoint.from_dict(h) if isinstance(h, dict) else h
            for h in data.get("human_checkpoints", [])
        ]

        return cls(
            research_plan_id=data.get("research_plan_id", ""),
            project_id=data.get("project_id", ""),
            status=data.get("status", "draft"),
            task_brief=task_brief,
            competitors=competitors,
            analysis_dimensions=analysis_dimensions,
            source_plan=source_plan,
            source_discovery=source_discovery,
            report_outline=report_outline,
            execution_dag=execution_dag,
            human_checkpoints=human_checkpoints,
            success_metrics=success_metrics,
            research_questions=data.get("research_questions", []),
            generated_by=data.get("generated_by", "fallback"),
            user_query=data.get("user_query", ""),
            schema_type=data.get("schema_type", "ai_agent_platform"),
            target_region=data.get("target_region", "global"),
            mode=data.get("mode", "review"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            confirmed_at=data.get("confirmed_at"),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_research_plan(plan: ResearchPlan | dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate a ResearchPlan instance or dict.
    Returns (is_valid, error_messages).
    """
    errors = []

    if isinstance(plan, dict):
        plan = ResearchPlan.from_dict(plan)

    if not plan.research_plan_id:
        errors.append("research_plan_id is required")

    if not plan.user_query:
        errors.append("user_query is required")

    if plan.status not in ("draft", "confirmed", "in_progress", "completed", "cancelled"):
        errors.append(f"Invalid status: {plan.status}")

    if plan.generated_by not in ("llm", "llm_augmented", "llm_outline_generator", "fallback", "human_edited"):
        errors.append(f"Invalid generated_by: {plan.generated_by}")

    if plan.mode not in ("auto", "review", "expert", "standard"):
        errors.append(f"Invalid mode: {plan.mode}")

    # At minimum, task_brief should exist or be creatable
    if not plan.task_brief and not plan.user_query:
        errors.append("Either task_brief or user_query must be provided")

    return len(errors) == 0, errors
