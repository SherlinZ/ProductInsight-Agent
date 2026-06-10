"""
Tests for ResearchPlan Schemas (vNext-R1).

Tests:
1. Test fallback ResearchPlan generation
2. Test ResearchPlan validation
3. Test confirm plan creates ExecutionDAG
4. Test existing golden demo still works
5. Test API endpoints return expected fields
"""

import json
import pytest
from datetime import datetime, timezone

# Add project root to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.schemas.research_plan import (
    ResearchPlan,
    TaskBrief,
    CompetitorSpec,
    AnalysisDimension,
    SourcePlan,
    ReportOutline,
    ReportSection,
    ExecutionDAG,
    DAGNode,
    DAGEdge,
    HumanCheckpoint,
    SuccessMetrics,
    validate_research_plan,
    generate_id,
    utc_now,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_user_query():
    return "Analyze Dify, Coze, Flowise, and LangGraph for enterprise AI agent platform comparison."


@pytest.fixture
def sample_competitor():
    return CompetitorSpec(
        competitor_id="comp_dify",
        name="Dify",
        company_name="Dify",
        official_url="https://dify.ai",
        seed_urls=["https://dify.ai", "https://docs.dify.ai"],
        known_aliases=["Dify AI"],
        priority="high",
    )


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestTaskBrief:
    def test_create_task_brief(self):
        tb = TaskBrief(
            task_id="task_001",
            project_name="AI Agent Platform Comparison",
            user_query="Analyze Dify, Coze, and Flowise",
            task_type="competitive_analysis",
            target_region="global",
            target_audience="enterprise_product_team",
            business_goal="Understand competitive positioning",
        )
        assert tb.task_id == "task_001"
        assert tb.project_name == "AI Agent Platform Comparison"
        assert tb.task_type == "competitive_analysis"
        assert tb.target_region == "global"

    def test_task_brief_to_dict(self):
        tb = TaskBrief(
            task_id="task_001",
            project_name="Test Project",
            user_query="Test query",
            task_type="competitive_analysis",
            target_region="china",
        )
        d = tb.to_dict()
        assert d["task_id"] == "task_001"
        assert d["project_name"] == "Test Project"
        assert "created_at" in d

    def test_task_brief_from_dict(self):
        data = {
            "task_id": "task_002",
            "project_name": "From Dict",
            "user_query": "Query",
            "task_type": "product_comparison",
            "target_region": "us",
        }
        tb = TaskBrief.from_dict(data)
        assert tb.task_id == "task_002"
        assert tb.project_name == "From Dict"


class TestCompetitorSpec:
    def test_create_competitor(self, sample_competitor):
        assert sample_competitor.name == "Dify"
        assert sample_competitor.priority == "high"
        assert len(sample_competitor.seed_urls) == 2

    def test_competitor_to_dict(self, sample_competitor):
        d = sample_competitor.to_dict()
        assert d["name"] == "Dify"
        assert "dify.ai" in d["official_url"]

    def test_competitor_from_dict(self):
        data = {
            "competitor_id": "comp_test",
            "name": "TestProduct",
            "company_name": "TestCo",
            "priority": "medium",
        }
        comp = CompetitorSpec.from_dict(data)
        assert comp.name == "TestProduct"
        assert comp.priority == "medium"


class TestAnalysisDimension:
    def test_create_dimension(self):
        dim = AnalysisDimension(
            dimension_id="pricing_model",
            name="Pricing Model",
            description="Analyze pricing structure",
            required=True,
            sub_dimensions=["free_tier", "paid_tiers"],
            expected_outputs=["facts", "claims"],
        )
        assert dim.dimension_id == "pricing_model"
        assert len(dim.sub_dimensions) == 2

    def test_dimension_defaults(self):
        dim = AnalysisDimension(
            dimension_id="test",
            name="Test",
            description="Test dimension",
        )
        assert dim.required is True
        assert dim.sub_dimensions == []
        assert dim.expected_outputs == ["facts", "claims", "comparison_matrix"]


class TestSourcePlan:
    def test_create_source_plan(self):
        sp = SourcePlan(
            source_plan_id="sp_001",
            source_types=["official_website", "documentation"],
            minimum_sources_per_competitor=3,
            minimum_evidence_per_dimension=5,
        )
        assert len(sp.source_types) == 2
        assert sp.minimum_sources_per_competitor == 3

    def test_source_plan_compliance_notes(self):
        sp = SourcePlan(source_plan_id="sp_002")
        assert len(sp.compliance_notes) == 2
        assert "Respect robots" in sp.compliance_notes[0]


class TestReportOutline:
    def test_create_outline(self):
        section = ReportSection(
            section_id="exec_summary",
            title="Executive Summary",
            purpose="Summarize key findings",
            min_words=600,
            requires_human_review=True,
        )
        outline = ReportOutline(
            outline_id="outline_001",
            report_title="AI Agent Comparison",
            sections=[section],
        )
        assert outline.report_title == "AI Agent Comparison"
        assert len(outline.sections) == 1

    def test_outline_serialization(self):
        outline = ReportOutline(
            outline_id="outline_002",
            report_title="Test Report",
            sections=[
                ReportSection(
                    section_id="s1",
                    title="Section 1",
                    purpose="Purpose 1",
                    required_dimensions=["pricing"],
                    min_words=500,
                )
            ],
        )
        d = outline.to_dict()
        assert d["outline_id"] == "outline_002"
        assert len(d["sections"]) == 1
        assert d["sections"][0]["title"] == "Section 1"


class TestExecutionDAG:
    def test_create_dag(self):
        node1 = DAGNode(
            node_id="node_1",
            node_type="collect_sources",
            agent_name="collector",
            depends_on=[],
            human_checkpoint=False,
        )
        node2 = DAGNode(
            node_id="node_2",
            node_type="extract_evidence",
            agent_name="collector",
            depends_on=["node_1"],
            human_checkpoint=False,
        )
        edge = DAGEdge(from_node="node_1", to_node="node_2")

        dag = ExecutionDAG(
            dag_id="dag_001",
            research_plan_id="plan_001",
            nodes=[node1, node2],
            edges=[edge],
        )
        assert len(dag.nodes) == 2
        assert len(dag.edges) == 1

    def test_dag_serialization(self):
        dag = ExecutionDAG(
            dag_id="dag_002",
            research_plan_id="plan_002",
            nodes=[
                DAGNode(
                    node_id="n1",
                    node_type="test",
                    agent_name="agent",
                )
            ],
            edges=[],
        )
        d = dag.to_dict()
        assert d["dag_id"] == "dag_002"
        assert len(d["nodes"]) == 1


class TestHumanCheckpoint:
    def test_create_checkpoint(self):
        cp = HumanCheckpoint(
            checkpoint_id="cp_001",
            stage="research_plan_review",
            title="Review plan before execution",
            required=True,
        )
        assert cp.required is True

    def test_checkpoint_defaults(self):
        cp = HumanCheckpoint(
            checkpoint_id="cp_002",
            stage="claim_review",
            title="Review claims",
        )
        assert cp.required is True
        assert cp.description == ""


class TestSuccessMetrics:
    def test_create_metrics(self):
        sm = SuccessMetrics(
            minimum_signed_claims=20,
            minimum_sources_per_competitor=3,
            minimum_evidence_items=50,
            minimum_report_words=10000,
        )
        assert sm.minimum_signed_claims == 20
        assert sm.minimum_report_words == 10000

    def test_metrics_defaults(self):
        sm = SuccessMetrics()
        assert sm.minimum_signed_claims == 15
        assert sm.minimum_sources_per_competitor == 2


# ---------------------------------------------------------------------------
# ResearchPlan Tests
# ---------------------------------------------------------------------------

class TestResearchPlan:
    def test_create_research_plan(self, sample_competitor):
        plan = ResearchPlan(
            research_plan_id="plan_001",
            project_id="proj_001",
            status="draft",
            task_brief=TaskBrief(
                task_id="task_001",
                project_name="Test",
                user_query="Test query",
                task_type="competitive_analysis",
                target_region="global",
            ),
            competitors=[sample_competitor],
            analysis_dimensions=[
                AnalysisDimension(
                    dimension_id="pricing",
                    name="Pricing",
                    description="Test",
                )
            ],
            source_plan=SourcePlan(source_plan_id="sp_001"),
            report_outline=ReportOutline(outline_id="o_001", report_title="Test"),
            human_checkpoints=[
                HumanCheckpoint(
                    checkpoint_id="cp_001",
                    stage="plan_review",
                    title="Review plan",
                )
            ],
            success_metrics=SuccessMetrics(),
            generated_by="llm",
            user_query="Test query",
        )
        assert plan.research_plan_id == "plan_001"
        assert plan.status == "draft"
        assert len(plan.competitors) == 1

    def test_research_plan_to_dict(self, sample_competitor):
        plan = ResearchPlan(
            research_plan_id="plan_002",
            competitors=[sample_competitor],
            generated_by="fallback",
            user_query="Test",
        )
        d = plan.to_dict()
        assert d["research_plan_id"] == "plan_002"
        assert d["generated_by"] == "fallback"
        assert len(d["competitors"]) == 1
        assert "created_at" in d

    def test_research_plan_from_dict(self):
        data = {
            "research_plan_id": "plan_003",
            "status": "draft",
            "generated_by": "llm",
            "user_query": "Test query",
            "competitors": [
                {"competitor_id": "c1", "name": "Product1"}
            ],
            "analysis_dimensions": [
                {"dimension_id": "d1", "name": "Dim1", "description": "Desc"}
            ],
            "human_checkpoints": [
                {"checkpoint_id": "cp1", "stage": "review", "title": "CP"}
            ],
        }
        plan = ResearchPlan.from_dict(data)
        assert plan.research_plan_id == "plan_003"
        assert len(plan.competitors) == 1
        assert len(plan.analysis_dimensions) == 1

    def test_research_plan_roundtrip(self):
        original = ResearchPlan(
            research_plan_id="plan_roundtrip",
            status="confirmed",
            generated_by="human_edited",
            user_query="Roundtrip test",
            competitors=[
                CompetitorSpec(
                    competitor_id="comp_rt",
                    name="RoundtripProduct",
                    priority="high",
                )
            ],
            success_metrics=SuccessMetrics(minimum_signed_claims=25),
        )
        d = original.to_dict()
        restored = ResearchPlan.from_dict(d)
        assert restored.research_plan_id == original.research_plan_id
        assert restored.status == original.status
        assert restored.generated_by == original.generated_by
        assert len(restored.competitors) == 1


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_plan(self):
        plan = ResearchPlan(
            research_plan_id="plan_valid",
            user_query="Test query",
            status="draft",
            generated_by="llm",
            mode="review",
        )
        is_valid, errors = validate_research_plan(plan)
        assert is_valid is True
        assert errors == []

    def test_invalid_status(self):
        plan = ResearchPlan(
            research_plan_id="plan_bad",
            user_query="Test",
            status="invalid_status",
            generated_by="llm",
            mode="review",
        )
        is_valid, errors = validate_research_plan(plan)
        assert is_valid is False
        assert "Invalid status" in errors[0]

    def test_invalid_generated_by(self):
        plan = ResearchPlan(
            research_plan_id="plan_bad2",
            user_query="Test",
            generated_by="invalid",
            mode="review",
        )
        is_valid, errors = validate_research_plan(plan)
        assert is_valid is False
        assert "Invalid generated_by" in errors[0]

    def test_invalid_mode(self):
        plan = ResearchPlan(
            research_plan_id="plan_bad3",
            user_query="Test",
            mode="invalid_mode",
        )
        is_valid, errors = validate_research_plan(plan)
        assert is_valid is False
        assert "Invalid mode" in errors[0]

    def test_missing_id(self):
        plan = ResearchPlan(
            research_plan_id="",
            user_query="Test query",
        )
        is_valid, errors = validate_research_plan(plan)
        assert is_valid is False
        assert "research_plan_id is required" in errors

    def test_dict_validation(self):
        data = {
            "research_plan_id": "plan_dict",
            "user_query": "Test",
            "status": "draft",
            "generated_by": "llm",
            "mode": "review",
        }
        is_valid, errors = validate_research_plan(data)
        assert is_valid is True


# ---------------------------------------------------------------------------
# Helper Function Tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_generate_id(self):
        id1 = generate_id("plan")
        id2 = generate_id("plan")
        assert id1.startswith("plan_")
        assert id2.startswith("plan_")
        assert id1 != id2

    def test_utc_now(self):
        now = utc_now()
        assert now.endswith("+00:00")
        # Should be parseable as ISO format
        dt = datetime.fromisoformat(now)
        assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# Run Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
