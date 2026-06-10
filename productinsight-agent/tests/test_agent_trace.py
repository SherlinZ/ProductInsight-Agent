"""Tests for Agent Trace system (run_with_trace, TraceRepository, API endpoints)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_traces.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Shared timestamp for test runs
NOW = datetime.now(timezone.utc).isoformat()


class TestTraceRepository(unittest.TestCase):
    """Test TraceRepository methods."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        # Create a dummy run so trace FK constraint passes
        from backend.app.storage.repositories import RunRepository
        for rid, tid, title in [
            ("run_001", "task_001", "Test Run"),
            ("run_filter_test", "task_filter", "Filter Test"),
            ("run_summary_test", "task_summary", "Summary Test"),
            ("run_latest_test", "task_latest", "Latest Test"),
            ("run_io_test", "task_io", "IO Test"),
        ]:
            RunRepository().create_run({
                "run_id": rid,
                "task_id": tid,
                "task_title": title,
                "task_brief": {},
                "mode": "real_time",
                "status": "pending",
                "created_at": NOW,
                "updated_at": NOW,
            })

    def test_add_and_get_trace(self):
        """Test adding a trace and retrieving it."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        trace_id = "trace_test_001"
        trace = {
            "trace_id": trace_id,
            "run_id": "run_001",
            "node_name": "collect_sources",
            "agent_name": "CollectorAgent",
            "status": "success",
            "started_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "model_name": "gpt-4o",
            "latency_ms": 1500,
            "token_input": 100,
            "token_output": 200,
        }
        repo.add_trace(trace)

        result = repo.get_trace(trace_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["trace_id"], trace_id)
        self.assertEqual(result["run_id"], "run_001")
        self.assertEqual(result["node_name"], "collect_sources")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["latency_ms"], 1500)

    def test_list_traces_with_filters(self):
        """Test listing traces with node_name, agent_name, and status filters."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()

        # Add multiple traces
        for i in range(5):
            repo.add_trace({
                "trace_id": f"trace_{i}",
                "run_id": "run_filter_test",
                "node_name": "collect_sources" if i % 2 == 0 else "extract_facts",
                "agent_name": "CollectorAgent" if i % 2 == 0 else "ExtractorAgent",
                "status": "success" if i % 3 == 0 else "failed",
                "started_at": f"2026-01-01T00:00:{i:02d}Z",
                "created_at": f"2026-01-01T00:00:{i:02d}Z",
            })

        # Filter by run_id only
        all_traces = repo.list_traces("run_filter_test")
        self.assertGreaterEqual(len(all_traces), 5)

        # Filter by node_name
        source_traces = repo.list_traces("run_filter_test", node_name="collect_sources")
        for t in source_traces:
            self.assertEqual(t["node_name"], "collect_sources")

        # Filter by agent_name
        collector_traces = repo.list_traces("run_filter_test", agent_name="CollectorAgent")
        for t in collector_traces:
            self.assertEqual(t["agent_name"], "CollectorAgent")

        # Filter by status
        failed_traces = repo.list_traces("run_filter_test", status="failed")
        for t in failed_traces:
            self.assertEqual(t["status"], "failed")

    def test_summarize_traces(self):
        """Test trace summary computes total_traces, failures, tokens, latency."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        run_id = "run_summary_test"

        # Add traces with known token/latency values
        repo.add_trace({
            "trace_id": "sum_trace_1",
            "run_id": run_id,
            "node_name": "collect_sources",
            "agent_name": "CollectorAgent",
            "status": "success",
            "started_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "model_name": "gpt-4o",
            "token_input": 100,
            "token_output": 200,
            "latency_ms": 1000,
        })
        repo.add_trace({
            "trace_id": "sum_trace_2",
            "run_id": run_id,
            "node_name": "extract_facts",
            "agent_name": "ExtractorAgent",
            "status": "failed",
            "started_at": "2026-01-01T00:01:00Z",
            "created_at": "2026-01-01T00:01:00Z",
            "model_name": "gpt-4o-mini",
            "token_input": 50,
            "token_output": 100,
            "latency_ms": 500,
        })
        repo.add_trace({
            "trace_id": "sum_trace_3",
            "run_id": run_id,
            "node_name": "plan_schema",
            "agent_name": "SchemaPlanner",
            "status": "success",
            "started_at": "2026-01-01T00:02:00Z",
            "created_at": "2026-01-01T00:02:00Z",
            "model_name": "non_llm",
            "token_input": 0,
            "token_output": 0,
            "latency_ms": 10,
        })

        summary = repo.summarize_traces(run_id)
        self.assertEqual(summary["total_traces"], 3)
        self.assertEqual(summary["failed_traces"], 1)
        self.assertEqual(summary["total_tokens"], 450)  # (100+200) + (50+100) + (0+0)
        self.assertEqual(summary["llm_calls"], 2)  # gpt-4o and gpt-4o-mini
        self.assertEqual(summary["non_llm_calls"], 1)

    def test_get_latest_traces(self):
        """Test getting the most recent traces."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        run_id = "run_latest_test"

        for i in range(10):
            repo.add_trace({
                "trace_id": f"latest_{i}",
                "run_id": run_id,
                "node_name": f"node_{i}",
                "agent_name": "TestAgent",
                "status": "success",
                "started_at": f"2026-01-01T00:00:{i:02d}Z",
                "created_at": f"2026-01-01T00:00:{i:02d}Z",
            })

        latest = repo.get_latest_traces(run_id, limit=3)
        self.assertEqual(len(latest), 3)
        # Should be ordered by started_at DESC (most recent first)
        self.assertEqual(latest[0]["trace_id"], "latest_9")
        self.assertEqual(latest[1]["trace_id"], "latest_8")
        self.assertEqual(latest[2]["trace_id"], "latest_7")

    def test_get_node_io_summary(self):
        """Test getting per-node input/output/artifact summary."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        run_id = "run_io_test"

        repo.add_trace({
            "trace_id": "io_trace_1",
            "run_id": run_id,
            "node_name": "collect_sources",
            "agent_name": "CollectorAgent",
            "status": "success",
            "started_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "model_name": "gpt-4o",
            "input_payload": {"products": ["dify", "coze"]},
            "output_payload": {"sources": 10},
            "artifact_refs": [{"type": "sources", "ids": ["s1", "s2"]}],
        })

        io_summary = repo.get_node_io_summary(run_id)
        self.assertGreaterEqual(len(io_summary), 1)
        self.assertEqual(io_summary[0]["node_name"], "collect_sources")
        self.assertEqual(io_summary[0]["input_payload"], {"products": ["dify", "coze"]})
        self.assertEqual(io_summary[0]["output_payload"], {"sources": 10})
        self.assertEqual(io_summary[0]["artifact_refs"], [{"type": "sources", "ids": ["s1", "s2"]}])

    def test_get_nonexistent_trace(self):
        """Test get_trace returns None for nonexistent trace_id."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        result = repo.get_trace("nonexistent_trace_id")
        self.assertIsNone(result)

    def test_empty_summary(self):
        """Test summarize_traces returns zeros for run with no traces."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()
        summary = repo.summarize_traces("run_with_no_traces")
        self.assertEqual(summary["total_traces"], 0)
        self.assertEqual(summary["failed_traces"], 0)
        self.assertEqual(summary["total_tokens"], 0)


class TestRunWithTrace(unittest.TestCase):
    """Test run_with_trace wrapper."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        # Create dummy runs
        from backend.app.storage.repositories import RunRepository
        for rid in ("trace_run_success", "trace_run_fail", "trace_non_llm",
                    "trace_helpers_test", "trace_fail_test"):
            RunRepository().create_run({
                "run_id": rid,
                "task_id": f"task_{rid}",
                "task_title": f"Test {rid}",
                "task_brief": {},
                "mode": "real_time",
                "status": "pending",
                "created_at": NOW,
                "updated_at": NOW,
            })

    def test_run_with_trace_success(self):
        """Test run_with_trace records success trace."""
        from backend.app.tracing.agent_trace import run_with_trace
        from backend.app.storage.repositories import TraceRepository

        def dummy_func(x: int, y: int) -> dict:
            return {"sum": x + y, "token_input": 50, "token_output": 100}

        result = run_with_trace(
            run_id="trace_run_success",
            node_name="test_node",
            agent_name="TestAgent",
            func=dummy_func,
            x=10,
            y=20,
        )

        self.assertEqual(result["sum"], 30)

        # Verify trace was recorded
        repo = TraceRepository()
        traces = repo.list_traces("trace_run_success")
        self.assertGreaterEqual(len(traces), 1)
        latest = traces[-1]
        self.assertEqual(latest["node_name"], "test_node")
        self.assertEqual(latest["status"], "success")
        self.assertEqual(latest["token_input"], 50)
        self.assertEqual(latest["token_output"], 100)
        self.assertIsNotNone(latest["latency_ms"])
        self.assertIsNotNone(latest["completed_at"])

    def test_run_with_trace_failure(self):
        """Test run_with_trace records failed trace then re-raises."""
        from backend.app.tracing.agent_trace import run_with_trace
        from backend.app.storage.repositories import TraceRepository

        def failing_func() -> None:
            raise ValueError("Deliberate test failure")

        with self.assertRaises(ValueError) as ctx:
            run_with_trace(
                run_id="trace_run_fail",
                node_name="fail_node",
                agent_name="FailingAgent",
                func=failing_func,
            )

        self.assertIn("Deliberate test failure", str(ctx.exception))

        # Verify failure trace was recorded
        repo = TraceRepository()
        traces = repo.list_traces("trace_run_fail")
        self.assertGreaterEqual(len(traces), 1)
        latest = traces[-1]
        self.assertEqual(latest["node_name"], "fail_node")
        self.assertEqual(latest["status"], "failed")
        self.assertIn("ValueError", latest["error_message"])
        self.assertIn("Deliberate test failure", latest["error_message"])

    def test_run_with_trace_non_llm_node(self):
        """Test run_with_trace sets model_name to non_llm for non-LLM nodes."""
        from backend.app.tracing.agent_trace import run_with_trace
        from backend.app.storage.repositories import TraceRepository

        def simple_math(a: int, b: int) -> int:
            return a * b

        result = run_with_trace(
            run_id="trace_non_llm",
            node_name="plan_schema",
            agent_name="SchemaPlanner",
            func=simple_math,
            a=5,
            b=6,
            model_name="non_llm",
        )

        self.assertEqual(result, 30)

        repo = TraceRepository()
        traces = repo.list_traces("trace_non_llm")
        latest = traces[-1]
        self.assertEqual(latest["model_name"], "non_llm")
        self.assertEqual(latest["token_input"], 0)
        self.assertEqual(latest["token_output"], 0)


class TestTraceServiceHelpers(unittest.TestCase):
    """Test create_trace_start, complete_trace, fail_trace helpers."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()

    def test_create_and_complete_trace(self):
        """Test creating a trace start, then completing it."""
        from backend.app.tracing.agent_trace import create_trace_start, complete_trace
        from backend.app.storage.repositories import TraceRepository

        trace = create_trace_start(
            run_id="trace_helpers_test",
            node_name="analyze_dimensions",
            agent_name="AnalystAgent",
            model_name="gpt-4o",
            input_payload={"dimensions": ["pricing", "features"]},
        )

        self.assertEqual(trace["status"], "running")
        self.assertIsNotNone(trace["trace_id"])
        self.assertIsNotNone(trace["started_at"])

        # Complete the trace
        complete_trace(
            trace,
            result={"claims_generated": 15},
            token_input=200,
            token_output=500,
        )

        # Verify completion
        repo = TraceRepository()
        traces = repo.list_traces("trace_helpers_test")
        completed = [t for t in traces if t["trace_id"] == trace["trace_id"]][-1]
        self.assertEqual(completed["status"], "success")
        self.assertIsNotNone(completed["completed_at"])
        self.assertIsNotNone(completed["latency_ms"])

    def test_fail_trace(self):
        """Test failing a trace."""
        from backend.app.tracing.agent_trace import create_trace_start, fail_trace
        from backend.app.storage.repositories import TraceRepository

        trace = create_trace_start(
            run_id="trace_fail_test",
            node_name="write_report",
            agent_name="WriterAgent",
        )

        error = RuntimeError("Report generation failed")
        fail_trace(trace, error)

        repo = TraceRepository()
        traces = repo.list_traces("trace_fail_test")
        failed = [t for t in traces if t["trace_id"] == trace["trace_id"]][-1]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("RuntimeError", failed["error_message"])
        self.assertIn("Report generation failed", failed["error_message"])


class TestLLMNonLLMClassification(unittest.TestCase):
    """Test that model_name correctly classifies LLM vs non-LLM calls."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "llm_test_run",
            "task_id": "llm_test_task",
            "task_title": "LLM Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_model_name_na_not_llm(self):
        """model_name='n/a' should NOT be counted as llm_calls."""
        from backend.app.storage.repositories import TraceRepository, RunRepository

        # Use a different run_id to avoid leftover traces
        RunRepository().create_run({
            "run_id": "llm_test_run_4",
            "task_id": "llm_test_task_4",
            "task_title": "LLM Test 4",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

        repo = TraceRepository()
        repo.add_trace({
            "trace_id": "trace_na",
            "run_id": "llm_test_run_4",
            "node_name": "collect_sources",
            "agent_name": "CollectorAgent",
            "status": "success",
            "started_at": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "model_name": "n/a",
        })

        summary = repo.summarize_traces("llm_test_run_4")
        self.assertEqual(summary["llm_calls"], 0)
        self.assertEqual(summary["non_llm_calls"], 1)

    def test_non_llm_models_not_llm(self):
        """Various non-LLM model names should not be counted as llm_calls."""
        from backend.app.storage.repositories import TraceRepository, RunRepository

        # Use a different run_id to avoid leftover traces
        RunRepository().create_run({
            "run_id": "llm_test_run_3",
            "task_id": "llm_test_task_3",
            "task_title": "LLM Test 3",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

        repo = TraceRepository()
        non_llm_names = ["non_llm", "none", "", "rule_based", "N/A", "Non_LLM"]

        for name in non_llm_names:
            repo.add_trace({
                "trace_id": f"trace_{name}",
                "run_id": "llm_test_run_3",
                "node_name": "test_node",
                "agent_name": "TestAgent",
                "status": "success",
                "started_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "model_name": name,
            })

        summary = repo.summarize_traces("llm_test_run_3")
        self.assertEqual(summary["llm_calls"], 0)
        self.assertEqual(summary["non_llm_calls"], len(non_llm_names))

    def test_real_llm_models_are_llm(self):
        """Real LLM model names should be counted as llm_calls."""
        from backend.app.storage.repositories import TraceRepository, RunRepository

        # Use a different run_id to avoid leftover traces
        RunRepository().create_run({
            "run_id": "llm_test_run_2",
            "task_id": "llm_test_task_2",
            "task_title": "LLM Test 2",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

        repo = TraceRepository()
        llm_names = ["gpt-4o", "gpt-4o-mini", "claude-3-opus", "gemini-pro"]

        for name in llm_names:
            repo.add_trace({
                "trace_id": f"trace_{name}",
                "run_id": "llm_test_run_2",
                "node_name": "test_node",
                "agent_name": "TestAgent",
                "status": "success",
                "started_at": "2026-01-01T00:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
                "model_name": name,
            })

        summary = repo.summarize_traces("llm_test_run_2")
        self.assertEqual(summary["llm_calls"], len(llm_names))
        self.assertEqual(summary["non_llm_calls"], 0)


class TestRouteAfterFinalReview(unittest.TestCase):
    """Test route_after_final_review routing logic."""

    def test_rework_required_routes_to_write_report(self):
        """status='rework_required' should route to write_report."""
        from backend.app.orchestrator.graph import route_after_final_review

        state = {
            "final_review_result": {"status": "rework_required"},
            "report_draft": {"report_status": "draft"},
        }
        result = route_after_final_review(state)
        self.assertEqual(result, "write_report")

    def test_blocked_without_rework_required_routes_to_export_report(self):
        """report_status='blocked' without rework_required should NOT loop to write_report."""
        from backend.app.orchestrator.graph import route_after_final_review

        state = {
            "final_review_result": {"status": "approved"},
            "report_draft": {"report_status": "blocked"},
        }
        result = route_after_final_review(state)
        self.assertEqual(result, "export_report")

    def test_no_final_review_result_routes_to_export_report(self):
        """Missing final_review_result should route to export_report."""
        from backend.app.orchestrator.graph import route_after_final_review

        state = {
            "report_draft": {"report_status": "draft"},
        }
        result = route_after_final_review(state)
        self.assertEqual(result, "export_report")


if __name__ == "__main__":
    unittest.main()


class TestAnalystAgentDynamicProductsDimensions(unittest.TestCase):
    """Test AnalystAgent dynamic product/dimension filtering (vNext-R2-C)."""

    def test_extract_valid_products_from_task_brief(self):
        """Test that _extract_valid_products_and_dimensions parses products correctly."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        # Test with pricing_analysis products (Slack, Teams, Zoom, Google Meet)
        task_brief = {
            "products": [
                {"product_id": "Slack"},
                {"product_id": "Microsoft Teams"},
                {"product_id": "Zoom"},
                {"product_id": "Google Meet"},
            ],
            "task_type": "pricing_analysis",
            "analysis_dimensions": [
                {"dimension_id": "pricing_model"},
                {"dimension_id": "value_proposition"},
                {"dimension_id": "ai_feature_pricing"},
            ],
        }
        
        valid_products, valid_dimensions = agent._extract_valid_products_and_dimensions(task_brief)
        
        # Should include normalized versions
        self.assertIn("slack", valid_products)
        self.assertIn("microsoft_teams", valid_products)
        self.assertIn("zoom", valid_products)
        self.assertIn("google_meet", valid_products)
        
        # Should include pricing_analysis dimensions
        self.assertIn("pricing_model", valid_dimensions)
        self.assertIn("value_proposition", valid_dimensions)
        self.assertIn("ai_feature_pricing", valid_dimensions)

    def test_extract_valid_products_fallback_to_default(self):
        """Test fallback to default PRODUCTS when no products in task_brief."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        # Empty task_brief
        task_brief = {}
        
        valid_products, valid_dimensions = agent._extract_valid_products_and_dimensions(task_brief)
        
        # Should fall back to default PRODUCTS
        self.assertIn("dify", valid_products)
        self.assertIn("coze", valid_products)
        self.assertIn("fastgpt", valid_products)
        self.assertIn("flowise", valid_products)
        
        # Should fall back to ALL_DIMENSIONS
        self.assertIn("function_tree", valid_dimensions)
        self.assertIn("pricing_model", valid_dimensions)

    def test_extract_valid_dimensions_pricing_analysis(self):
        """Test pricing_analysis dimensions are extracted correctly."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        task_brief = {
            "task_type": "pricing_analysis",
            "analysis_dimensions": [
                "pricing_model",
                "value_proposition",
                "ai_feature_pricing",
                "admin_security_cost",
                "migration_adoption",
                "competitive_positioning",
            ],
        }
        
        _, valid_dimensions = agent._extract_valid_products_and_dimensions(task_brief)
        
        # Should include all pricing_analysis dimensions
        self.assertIn("pricing_model", valid_dimensions)
        self.assertIn("value_proposition", valid_dimensions)
        self.assertIn("ai_feature_pricing", valid_dimensions)
        self.assertIn("admin_security_cost", valid_dimensions)
        self.assertIn("migration_adoption", valid_dimensions)
        self.assertIn("competitive_positioning", valid_dimensions)

    def test_normalize_product_id_slack_variants(self):
        """Test _normalize_product_id handles Slack variants correctly."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        valid_products = {"slack", "slack_lower"}
        
        # Direct match
        self.assertEqual(agent._normalize_product_id("Slack", valid_products), "slack")
        self.assertEqual(agent._normalize_product_id("slack", valid_products), "slack")
        
        # Case insensitive
        self.assertEqual(agent._normalize_product_id("SLACK", valid_products), "slack")
        
        # Not found
        self.assertIsNone(agent._normalize_product_id("Unknown", valid_products))

    def test_normalize_product_id_run_scoped(self):
        """Test _normalize_product_id handles run-scoped product IDs."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        valid_products = {"slack", "teams"}
        
        # Run-scoped product ID should still match
        self.assertEqual(agent._normalize_product_id("run_123_slack", valid_products), "slack")
        self.assertEqual(agent._normalize_product_id("run_456_teams", valid_products), "teams")

    def test_normalize_dimension_pricing_analysis(self):
        """Test _normalize_dimension handles pricing_analysis dimensions."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        valid_dimensions = {
            "pricing_model",
            "value_proposition",
            "ai_feature_pricing",
            "admin_security_cost",
            "migration_adoption",
            "competitive_positioning",
        }
        
        # Direct match
        self.assertEqual(agent._normalize_dimension("pricing_model", valid_dimensions), "pricing_model")
        self.assertEqual(agent._normalize_dimension("value_proposition", valid_dimensions), "value_proposition")
        
        # Case insensitive
        self.assertEqual(agent._normalize_dimension("VALUE_PROPOSITION", valid_dimensions), "value_proposition")
        
        # Not found
        self.assertIsNone(agent._normalize_dimension("unknown_dimension", valid_dimensions))

    def test_parse_and_enrich_claims_accepts_slack_teams(self):
        """Test _parse_and_enrich_claims accepts Slack/Teams claims."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        valid_products = {"slack", "microsoft_teams", "zoom", "google_meet"}
        valid_dimensions = {"pricing_model", "value_proposition", "ai_feature_pricing"}
        
        evidence_items = [
            {"evidence_id": "ev_1", "product_id": "slack", "schema_key": "pricing_model"},
            {"evidence_id": "ev_2", "product_id": "teams", "schema_key": "pricing_model"},
        ]
        
        response = {
            "claims": [
                {
                    "product_id": "Slack",
                    "dimension": "pricing_model",
                    "claim_text": "Slack offers a free tier for small teams.",
                    "evidence_ids": ["ev_1"],
                    "confidence": 0.9,
                    "risk_level": "low",
                    "claim_type": "factual_summary",
                },
                {
                    "product_id": "Microsoft Teams",
                    "dimension": "value_proposition",
                    "claim_text": "Teams provides strong value for enterprise customers.",
                    "evidence_ids": [],
                    "confidence": 0.8,
                    "risk_level": "medium",
                    "claim_type": "comparative_insight",
                },
            ]
        }
        
        claims = agent._parse_and_enrich_claims(
            response, evidence_items, "test_run",
            valid_products=valid_products,
            valid_dimensions=valid_dimensions,
        )
        
        # Slack claim should be accepted
        self.assertGreaterEqual(len(claims), 1)
        
        # Check that at least the Slack claim is there (has evidence_ids)
        slack_claims = [c for c in claims if "slack" in c.get("product_id", "")]
        self.assertGreaterEqual(len(slack_claims), 1)

    def test_parse_and_enrich_claims_accepts_pricing_dimensions(self):
        """Test _parse_and_enrich_claims accepts pricing_analysis dimensions."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        valid_products = {"slack"}
        valid_dimensions = {"pricing_model", "value_proposition", "ai_feature_pricing"}
        
        evidence_items = [
            {"evidence_id": "ev_1"},
        ]
        
        response = {
            "claims": [
                {
                    "product_id": "slack",
                    "dimension": "value_proposition",
                    "claim_text": "Slack offers good ROI for remote teams.",
                    "evidence_ids": ["ev_1"],
                    "confidence": 0.85,
                    "risk_level": "medium",
                    "claim_type": "factual_summary",
                },
                {
                    "product_id": "slack",
                    "dimension": "ai_feature_pricing",
                    "claim_text": "Slack AI features cost extra.",
                    "evidence_ids": ["ev_1"],
                    "confidence": 0.8,
                    "risk_level": "low",
                    "claim_type": "factual_summary",
                },
            ]
        }
        
        claims = agent._parse_and_enrich_claims(
            response, evidence_items, "test_run",
            valid_products=valid_products,
            valid_dimensions=valid_dimensions,
        )
        
        # Both pricing_analysis dimension claims should be accepted
        self.assertEqual(len(claims), 2)
        
        # Verify dimensions
        dimensions = {c["dimension"] for c in claims}
        self.assertIn("value_proposition", dimensions)
        self.assertIn("ai_feature_pricing", dimensions)

    def test_parse_and_enrich_claims_fallback_to_default_products(self):
        """Test _parse_and_enrich_claims falls back to default PRODUCTS when not provided."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        evidence_items = [
            {"evidence_id": "ev_1"},
        ]
        
        response = {
            "claims": [
                {
                    "product_id": "dify",
                    "dimension": "function_tree",
                    "claim_text": "Dify supports workflow builder.",
                    "evidence_ids": ["ev_1"],
                    "confidence": 0.9,
                    "risk_level": "low",
                    "claim_type": "factual_summary",
                },
            ]
        }
        
        # Not passing valid_products/valid_dimensions - should fallback
        claims = agent._parse_and_enrich_claims(
            response, evidence_items, "test_run",
        )
        
        # Should accept default product
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["product_id"], "dify")

    def test_build_system_prompt_dynamic(self):
        """Test _build_system_prompt is dynamic based on task_type."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        
        # pricing_analysis task
        prompt = agent._build_system_prompt({"task_type": "pricing_analysis"})
        self.assertIn("pricing models", prompt)
        self.assertIn("value proposition", prompt)
        
        # knowledge_management task
        prompt = agent._build_system_prompt({"task_type": "knowledge_management"})
        self.assertIn("knowledge structure", prompt)
        self.assertIn("collaboration", prompt)
        
        # No task_type (generic)
        prompt = agent._build_system_prompt({})
        self.assertNotIn("Dify", prompt)
        self.assertNotIn("Coze", prompt)
        self.assertIn("products and dimensions", prompt)

    def test_extract_valid_products_product_name_field(self):
        """Test extraction from product_name field (vNext-R2-C)."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        task_brief = {
            "products": [{"product_name": "Slack"}, {"product_name": "Microsoft Teams"}],
            "task_type": "pricing_analysis",
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        
        # Should extract normalized variants
        self.assertIn("slack", valid_products)
        self.assertIn("microsoft_teams", valid_products)
        # Original casing should also be present
        self.assertIn("Slack", valid_products)
        self.assertIn("Microsoft Teams", valid_products)

    def test_extract_valid_products_name_field(self):
        """Test extraction from name field (vNext-R2-C)."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        task_brief = {
            "products": [{"name": "Zoom"}, {"name": "Google Meet"}],
            "task_type": "pricing_analysis",
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        
        self.assertIn("zoom", valid_products)
        self.assertIn("google_meet", valid_products)
        self.assertIn("Zoom", valid_products)
        self.assertIn("Google Meet", valid_products)

    def test_extract_valid_products_mixed_fields(self):
        """Test extraction with mixed field names (vNext-R2-C)."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        task_brief = {
            "products": [
                {"product_id": "slack"},
                {"product_name": "Microsoft Teams"},
                {"name": "Zoom"},
                {"product_slug": "google-meet"},
            ],
            "task_type": "pricing_analysis",
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        
        self.assertIn("slack", valid_products)
        self.assertIn("microsoft_teams", valid_products)
        self.assertIn("zoom", valid_products)
        self.assertIn("google_meet", valid_products)
        self.assertIn("google-meet", valid_products)

    def test_extract_valid_products_competitors_name_field(self):
        """Test extraction from competitors.name field (vNext-R2-C)."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        task_brief = {
            "competitors": [{"name": "Zoom"}, {"name": "Google Meet"}],
            "task_type": "pricing_analysis",
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        
        self.assertIn("zoom", valid_products)
        self.assertIn("google_meet", valid_products)

    def test_extract_valid_products_empty_dict_fallback(self):
        """Test fallback when products is non-empty list but all entries have no valid fields (vNext-R2-C)."""
        from backend.app.agents.analyst.analyst import AnalystAgent
        
        agent = AnalystAgent()
        task_brief = {
            "products": [{}, {"foo": "bar"}],  # No product_id, product_name, name, product_slug
            "task_type": "pricing_analysis",
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        
        # Should fallback to default PRODUCTS
        self.assertIn("dify", valid_products)
        self.assertIn("coze", valid_products)
