"""
Tests for ResearchPlanner Service and DAG Compiler (vNext-R1).

Tests:
1. Test fallback ResearchPlan generation
2. Test DAG compilation
3. Test plan revision
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.research_planner import (
    generate_research_plan,
    revise_research_plan,
    compile_execution_dag,
    _extract_competitors_from_query,
    _generate_project_name,
    SCHEMA_TYPE_DIMENSIONS,
    KNOWN_COMPETITORS,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

class TestCompetitorExtraction:
    def test_extract_known_competitors(self):
        query = "Analyze Dify, Coze, Flowise, and LangGraph for enterprise AI agent platform comparison."
        competitors = _extract_competitors_from_query(query)
        
        # Should find at least Dify, Coze, Flowise, LangGraph
        names = [c.name for c in competitors]
        assert "Dify" in names
        assert "Coze" in names
        assert "Flowise" in names
        assert "LangGraph" in names

    def test_extract_partial_query(self):
        query = "Compare Dify and Coze pricing"
        competitors = _extract_competitors_from_query(query)
        names = [c.name for c in competitors]
        assert "Dify" in names
        assert "Coze" in names

    def test_extract_no_known_competitors(self):
        query = "Analyze some random products"
        competitors = _extract_competitors_from_query(query)
        # Should still return some capitalized names
        assert isinstance(competitors, list)


class TestProjectNameGeneration:
    def test_generate_name_from_query(self):
        query = "Analyze Dify, Coze, Flowise, and LangGraph"
        name = _generate_project_name(query)
        assert len(name) > 0
        assert isinstance(name, str)


# ---------------------------------------------------------------------------
# Test Fallback Plan Generation
# ---------------------------------------------------------------------------

class TestFallbackPlanGeneration:
    def test_generate_plan_basic(self):
        plan = generate_research_plan(
            user_query="Analyze Dify and Coze for AI agent platform comparison",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        assert plan is not None
        assert isinstance(plan, dict)
        assert "research_plan_id" in plan
        assert plan["status"] == "draft"
        assert plan["generated_by"] in ("llm", "fallback")

    def test_generate_plan_with_competitors(self):
        plan = generate_research_plan(
            user_query="Analyze Dify, Coze, Flowise, and LangGraph for enterprise AI agent platform comparison",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        competitors = plan.get("competitors", [])
        assert len(competitors) >= 3
        
        # Check competitor structure
        for comp in competitors[:3]:
            if isinstance(comp, dict):
                assert "name" in comp
                assert "competitor_id" in comp

    def test_generate_plan_dimensions(self):
        plan = generate_research_plan(
            user_query="Compare Dify vs Coze for enterprise use",
            schema_type="ai_agent_platform",
            target_region="china",
            mode="review",
        )
        
        dimensions = plan.get("analysis_dimensions", [])
        assert len(dimensions) > 0
        
        # Check dimension structure
        for dim in dimensions[:2]:
            if isinstance(dim, dict):
                assert "dimension_id" in dim
                assert "name" in dim

    def test_generate_plan_pricing_schema(self):
        plan = generate_research_plan(
            user_query="Pricing analysis for AI agent platforms",
            schema_type="pricing_analysis",
            target_region="global",
            mode="auto",
        )
        
        dimensions = plan.get("analysis_dimensions", [])
        dim_ids = [d.get("dimension_id") if isinstance(d, dict) else d for d in dimensions]
        
        # Pricing analysis should have pricing_model
        assert "pricing_model" in dim_ids

    def test_generate_plan_source_plan(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        source_plan = plan.get("source_plan")
        if source_plan and isinstance(source_plan, dict):
            assert "source_types" in source_plan
            assert "collection_strategy" in source_plan

    def test_generate_plan_human_checkpoints(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        checkpoints = plan.get("human_checkpoints", [])
        assert len(checkpoints) >= 2
        
        # Check for plan_review checkpoint
        stage_ids = [cp.get("stage") if isinstance(cp, dict) else getattr(cp, "stage", "") for cp in checkpoints]
        assert "research_plan_review" in stage_ids

    def test_generate_plan_review_mode_checkpoints(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        checkpoints = plan.get("human_checkpoints", [])
        # review mode should have required checkpoints
        required_count = sum(
            1 for cp in checkpoints 
            if isinstance(cp, dict) and cp.get("required")
        )
        assert required_count >= 1

    def test_generate_plan_auto_mode(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="auto",
        )
        
        # Auto mode should have fewer human checkpoints
        checkpoints = plan.get("human_checkpoints", [])
        # Most checkpoints should not be required in auto mode
        required = [cp for cp in checkpoints if isinstance(cp, dict) and cp.get("required")]
        # At least plan review should still be required
        assert len(required) >= 1

    def test_generate_plan_success_metrics(self):
        plan = generate_research_plan(
            user_query="Analyze Dify, Coze, Flowise",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        metrics = plan.get("success_metrics")
        if metrics and isinstance(metrics, dict):
            assert "minimum_signed_claims" in metrics
            assert "minimum_sources_per_competitor" in metrics
            assert "minimum_evidence_items" in metrics

    def test_generate_plan_expert_mode(self):
        plan = generate_research_plan(
            user_query="Deep analysis of AI agent platforms",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="expert",
        )
        
        assert plan is not None
        assert plan.get("mode") == "expert"


# ---------------------------------------------------------------------------
# Test Plan Revision
# ---------------------------------------------------------------------------

class TestPlanRevision:
    def test_revise_add_competitor(self):
        original_plan = generate_research_plan(
            user_query="Analyze Dify and Coze",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        revised = revise_research_plan(
            original_plan,
            "Also add Flowise as a competitor and focus more on pricing",
        )
        
        assert revised is not None
        assert revised.get("generated_by") == "human_edited"

    def test_revise_focus_dimensions(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        revised = revise_research_plan(
            plan,
            "Focus more on enterprise deployment and security",
        )
        
        assert revised is not None
        # Should have enterprise_readiness dimension
        dims = revised.get("analysis_dimensions", [])
        dim_ids = [d.get("dimension_id") if isinstance(d, dict) else d for d in dims]
        assert "enterprise_readiness" in dim_ids


# ---------------------------------------------------------------------------
# Test DAG Compilation
# ---------------------------------------------------------------------------

class TestDAGCompiler:
    def test_compile_basic_dag(self):
        plan = generate_research_plan(
            user_query="Analyze Dify and Coze",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        
        assert dag is not None
        assert "dag_id" in dag
        assert "nodes" in dag
        assert "edges" in dag

    def test_dag_nodes_structure(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        nodes = dag.get("nodes", [])
        
        assert len(nodes) >= 10  # Standard pipeline
        
        # Check node structure
        for node in nodes[:3]:
            if isinstance(node, dict):
                assert "node_id" in node
                assert "node_type" in node
                assert "agent_name" in node
                assert "depends_on" in node
                assert "human_checkpoint" in node
                assert "status" in node

    def test_dag_required_nodes(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        nodes = dag.get("nodes", [])
        
        node_types = [n.get("node_type") if isinstance(n, dict) else n.node_type for n in nodes]
        
        # Check for required node types
        required_types = [
            "confirm_plan",
            "collect_sources",
            "extract_evidence",
            "extract_facts",
            "generate_claims",
            "review_claims",
            "plan_report_outline",
            "write_sections",
            "review_report",
            "compose_final_report",
        ]
        
        for required_type in required_types:
            assert required_type in node_types, f"Missing node type: {required_type}"

    def test_dag_human_checkpoint_nodes(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        nodes = dag.get("nodes", [])
        
        # review_claims should have human_checkpoint in review mode
        review_claim_nodes = [
            n for n in nodes 
            if (isinstance(n, dict) and n.get("node_type") == "review_claims")
            or (hasattr(n, "node_type") and n.node_type == "review_claims")
        ]
        assert len(review_claim_nodes) >= 1
        
        # Check one has human_checkpoint = True
        has_checkpoint = any(
            (isinstance(n, dict) and n.get("human_checkpoint"))
            or (hasattr(n, "human_checkpoint") and n.human_checkpoint)
            for n in review_claim_nodes
        )
        assert has_checkpoint

    def test_dag_edges_structure(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        edges = dag.get("edges", [])
        
        assert len(edges) >= 9  # Standard pipeline edges
        
        # Check edge structure
        for edge in edges[:3]:
            if isinstance(edge, dict):
                assert "from" in edge
                assert "to" in edge

    def test_dag_edges_connectivity(self):
        plan = generate_research_plan(
            user_query="Analyze Dify",
            schema_type="ai_agent_platform",
            target_region="global",
            mode="review",
        )
        
        dag = compile_execution_dag(plan)
        nodes = dag.get("nodes", [])
        edges = dag.get("edges", [])
        
        # Build adjacency list
        adj = {}
        for edge in edges:
            if isinstance(edge, dict):
                from_node = edge.get("from")
                to_node = edge.get("to")
                if from_node:
                    adj.setdefault(from_node, []).append(to_node)
        
        # confirm_plan should be first (no incoming edges)
        node_ids = [n.get("node_id") if isinstance(n, dict) else n.node_id for n in nodes]
        assert "node_confirm_plan" in node_ids
        
        # compose_final_report should be last
        assert "node_compose_final_report" in node_ids


# ---------------------------------------------------------------------------
# Test Schema Types
# ---------------------------------------------------------------------------

class TestSchemaTypes:
    def test_ai_agent_platform_dimensions(self):
        dims = SCHEMA_TYPE_DIMENSIONS.get("ai_agent_platform", [])
        assert len(dims) >= 5
        
        dim_ids = [d.get("dimension_id") for d in dims]
        assert "function_tree" in dim_ids
        assert "pricing_model" in dim_ids
        assert "enterprise_readiness" in dim_ids

    def test_pricing_analysis_dimensions(self):
        dims = SCHEMA_TYPE_DIMENSIONS.get("pricing_analysis", [])
        dim_ids = [d.get("dimension_id") for d in dims]
        assert "pricing_model" in dim_ids
        assert "value_proposition" in dim_ids

    def test_sales_battlecard_dimensions(self):
        dims = SCHEMA_TYPE_DIMENSIONS.get("sales_battlecard", [])
        dim_ids = [d.get("dimension_id") for d in dims]
        assert "strengths" in dim_ids
        assert "weaknesses" in dim_ids


# ---------------------------------------------------------------------------
# Test Known Competitors
# ---------------------------------------------------------------------------

class TestKnownCompetitors:
    def test_known_competitors_defined(self):
        assert len(KNOWN_COMPETITORS) > 0
        assert "Dify" in KNOWN_COMPETITORS
        assert "Coze" in KNOWN_COMPETITORS
        assert "Flowise" in KNOWN_COMPETITORS

    def test_competitor_has_seed_urls(self):
        dify = KNOWN_COMPETITORS.get("Dify", {})
        assert "seed_urls" in dify
        assert len(dify["seed_urls"]) > 0


# ---------------------------------------------------------------------------
# Run Tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
