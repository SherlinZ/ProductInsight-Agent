"""Tests for traced_llm_call and LLM trace functionality (vNext-R2-C)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_llm_traces.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Shared timestamp for test runs
NOW = datetime.now(timezone.utc).isoformat()


class TestTracedLLMCall(unittest.TestCase):
    """Test traced_llm_call wrapper."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository

        # Create test runs
        for rid in (
            "llm_trace_success",
            "llm_trace_failure",
            "llm_trace_token_est",
            "llm_fallback_trace",
        ):
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

    def test_traced_llm_call_success(self):
        """Test traced_llm_call records llm_call trace on success."""
        from backend.app.tracing.llm_trace import traced_llm_call
        from backend.app.storage.repositories import TraceRepository

        def mock_llm_call():
            return "This is the LLM response text."

        result = traced_llm_call(
            run_id="llm_trace_success",
            node_name="research_plan",
            agent_name="ResearchPlanner",
            agent_role="research_planner",
            prompt_version="v1.0",
            prompt_text="Generate a research plan",
            input_payload={"test": "data"},
            call_fn=mock_llm_call,
            input_length_hint=1000,
            decision_summary="Generated research plan outline",
        )

        self.assertEqual(result["output_text"], "This is the LLM response text.")
        self.assertIn("token_input", result)
        self.assertIn("token_output", result)

        # Verify trace was recorded
        repo = TraceRepository()
        traces = repo.list_traces("llm_trace_success")
        self.assertGreaterEqual(len(traces), 1)

        # Find the llm_call trace
        llm_traces = [t for t in traces if t.get("event_type") == "llm_call"]
        self.assertGreaterEqual(len(llm_traces), 1)

        llm_trace = llm_traces[-1]
        self.assertEqual(llm_trace["node_name"], "research_plan")
        self.assertEqual(llm_trace["agent_name"], "ResearchPlanner")
        self.assertEqual(llm_trace["status"], "success")
        self.assertIn("prompt_text", llm_trace)
        self.assertIn("latency_ms", llm_trace)

    def test_traced_llm_call_failure_records_trace_then_raises(self):
        """Test traced_llm_call records failed trace then re-raises exception."""
        from backend.app.tracing.llm_trace import traced_llm_call
        from backend.app.storage.repositories import TraceRepository

        def failing_llm_call():
            raise RuntimeError("LLM service unavailable")

        with self.assertRaises(RuntimeError) as ctx:
            traced_llm_call(
                run_id="llm_trace_failure",
                node_name="analyze_dimensions",
                agent_name="AnalystAgent",
                agent_role="analyst",
                prompt_version="v1.0",
                prompt_text="Analyze evidence and generate claims",
                call_fn=failing_llm_call,
            )

        self.assertIn("LLM service unavailable", str(ctx.exception))

        # Verify failure trace was recorded
        repo = TraceRepository()
        traces = repo.list_traces("llm_trace_failure")
        self.assertGreaterEqual(len(traces), 1)

        # Find the failed llm_call trace
        failed_traces = [t for t in traces if t.get("status") == "failed"]
        self.assertGreaterEqual(len(failed_traces), 1)

        failed_trace = failed_traces[-1]
        self.assertEqual(failed_trace["event_type"], "llm_call")
        self.assertEqual(failed_trace["node_name"], "analyze_dimensions")
        self.assertEqual(failed_trace["status"], "failed")
        self.assertIn("RuntimeError", failed_trace.get("error_message", ""))

    def test_token_extraction_from_response(self):
        """Test token usage is extracted from LLM response when available."""
        from backend.app.tracing.llm_trace import traced_llm_call
        from backend.app.storage.repositories import TraceRepository

        def mock_llm_with_usage():
            return {
                "content": "Analysis complete with structured claims.",
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 200,
                    "total_tokens": 700,
                },
                "model": "test-model-v1",
            }

        result = traced_llm_call(
            run_id="llm_trace_success",
            node_name="analyze_dimensions",
            agent_name="AnalystAgent",
            agent_role="analyst",
            prompt_version="v1.0",
            prompt_text="Analyze evidence",
            call_fn=mock_llm_with_usage,
            input_length_hint=500,
        )

        # Check tokens were extracted
        self.assertEqual(result["token_output"], 200)
        self.assertTrue(result["token_estimated"] or result["token_input"] > 0)

    def test_token_estimation_when_usage_missing(self):
        """Test token estimation when usage is not in response."""
        from backend.app.tracing.llm_trace import traced_llm_call

        def mock_llm_no_usage():
            return "Short response without usage info."

        result = traced_llm_call(
            run_id="llm_trace_token_est",
            node_name="write_report",
            agent_name="WriterAgent",
            agent_role="writer",
            prompt_version="v1.0",
            prompt_text="Write report based on claims",
            call_fn=mock_llm_no_usage,
            input_length_hint=2000,
        )

        self.assertTrue(result["token_estimated"])
        self.assertGreater(result["token_output"], 0)

    def test_parse_fn_called(self):
        """Test that parse_fn is called and result is returned."""
        from backend.app.tracing.llm_trace import traced_llm_call

        def mock_llm_json():
            return '{"claims": [{"id": "c1", "text": "test claim"}]}'

        def parse_response(response):
            import json
            data = json.loads(response) if isinstance(response, str) else response
            return {"parsed": data.get("claims", [])}

        result = traced_llm_call(
            run_id="llm_trace_success",
            node_name="analyze_dimensions",
            agent_name="AnalystAgent",
            agent_role="analyst",
            prompt_version="v1.0",
            prompt_text="Analyze",
            call_fn=mock_llm_json,
            parse_fn=parse_response,
        )

        self.assertIn("parsed_output", result)
        self.assertEqual(len(result["parsed_output"]["parsed"]), 1)


class TestCreateLLMFallbackTrace(unittest.TestCase):
    """Test create_llm_fallback_trace function."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "fallback_test_run",
            "task_id": "fallback_task",
            "task_title": "Fallback Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_create_llm_fallback_trace(self):
        """Test that fallback traces are recorded with correct status."""
        from backend.app.tracing.llm_trace import create_llm_fallback_trace
        from backend.app.storage.repositories import TraceRepository

        create_llm_fallback_trace(
            run_id="fallback_test_run",
            project_id=None,
            node_name="research_plan",
            agent_name="ResearchPlanner",
            agent_role="research_planner",
            prompt_version="v1.0",
            prompt_text="Generate plan",
            input_payload={"schema_type": "ai_agent_platform"},
            reason="LLM_UNAVAILABLE_OR_INVALID_JSON: LLMError: connection timeout",
            decision_summary="Fallback to template plan",
        )

        repo = TraceRepository()
        traces = repo.list_traces("fallback_test_run")
        self.assertGreaterEqual(len(traces), 1)

        fallback_traces = [t for t in traces if t.get("model_name") == "fallback"]
        self.assertGreaterEqual(len(fallback_traces), 1)

        fallback = fallback_traces[-1]
        self.assertEqual(fallback["event_type"], "llm_call")
        self.assertEqual(fallback["status"], "failed")
        self.assertIn("LLM_UNAVAILABLE_OR_INVALID_JSON", fallback["error_message"])
        self.assertEqual(fallback["decision_summary"], "Fallback to template plan")


class TestSummarizeTracesLLMClassification(unittest.TestCase):
    """Test that summarize_traces correctly classifies llm_call vs node_execution."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository

        # Use unique run_id with timestamp to avoid data collision
        import time
        ts = int(time.time() * 1000)
        self.run_id = f"llm_classify_run_{ts}"
        
        RunRepository().create_run({
            "run_id": self.run_id,
            "task_id": f"llm_classify_task_{ts}",
            "task_title": "LLM Classification Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_llm_call_event_type_counts(self):
        """Test event_type=llm_call is counted correctly."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()

        # Add llm_call traces
        for i in range(3):
            repo.add_trace({
                "trace_id": f"llm_trace_{self.run_id}_{i}",
                "run_id": self.run_id,
                "node_name": f"node_{i}",
                "agent_name": "TestAgent",
                "event_type": "llm_call",
                "model_name": "test-model",
                "status": "success",
                "started_at": NOW,
                "created_at": NOW,
                "token_input": 100,
                "token_output": 50,
            })

        # Add node_execution traces
        for i in range(2):
            repo.add_trace({
                "trace_id": f"node_trace_{self.run_id}_{i}",
                "run_id": self.run_id,
                "node_name": f"node_exec_{i}",
                "agent_name": "TestAgent",
                "event_type": "node_execution",
                "model_name": "non_llm",
                "status": "success",
                "started_at": NOW,
                "created_at": NOW,
            })

        summary = repo.summarize_traces(self.run_id)
        self.assertEqual(summary["llm_calls"], 3)
        self.assertEqual(summary["non_llm_calls"], 2)
        self.assertEqual(summary["total_tokens"], 3 * 150)  # 3 llm_calls * 150 tokens each

    def test_fallback_model_not_llm(self):
        """Test model_name=fallback with llm_call event_type is counted as llm."""
        from backend.app.storage.repositories import TraceRepository

        repo = TraceRepository()

        repo.add_trace({
            "trace_id": f"fallback_trace_{self.run_id}",
            "run_id": self.run_id,
            "node_name": "research_plan",
            "agent_name": "ResearchPlanner",
            "event_type": "llm_call",
            "model_name": "fallback",
            "status": "failed",
            "started_at": NOW,
            "created_at": NOW,
            "error_message": "LLM_UNAVAILABLE: connection refused",
        })

        summary = repo.summarize_traces(self.run_id)
        # "fallback" is NOT in NON_LLM_MODEL_NAMES, so it counts as llm_calls
        # This is intentional - fallback traces are still "llm_call" events
        self.assertGreaterEqual(summary["llm_calls"], 1)


class TestNonLLMModelNames(unittest.TestCase):
    """Test NON_LLM_MODEL_NAMES constant."""

    def test_non_llm_names(self):
        """Test that all standard non-LLM names are excluded."""
        from backend.app.tracing.llm_trace import NON_LLM_MODEL_NAMES

        expected = {"", "n/a", "none", "non_llm", "rule_based", "template"}
        self.assertEqual(NON_LLM_MODEL_NAMES, expected)


class TestWriterAgentDynamicReportOutline(unittest.TestCase):
    """Test WriterAgent dynamic report_outline support (vNext-R2-C)."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        import time
        ts = int(time.time() * 1000)
        self.run_id = f"writer_outline_test_{ts}"
        RunRepository().create_run({
            "run_id": self.run_id,
            "task_id": f"writer_task_{ts}",
            "task_title": "Writer Outline Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_enrich_sections_with_report_outline(self):
        """Test _enrich_sections_with_defaults uses report_outline sections."""
        from backend.app.agents.writer.writer import _enrich_sections_with_defaults

        report_outline = {
            "sections": [
                {"title": "Executive Summary", "section_id": "sec_01"},
                {"title": "Pricing Analysis", "section_id": "sec_02"},
                {"title": "Key Takeaways", "section_id": "sec_03"},
            ]
        }

        raw_sections = [
            {"section_title": "Executive Summary", "section_id": "sec_01", "content_markdown": "Overview"},
            {"section_title": "Pricing Analysis", "section_id": "sec_02", "content_markdown": "Pricing details"},
        ]

        enriched = _enrich_sections_with_defaults(raw_sections, self.run_id, report_outline)
        enriched_titles = {s["section_title"] for s in enriched}

        # Should include report_outline sections
        self.assertIn("Executive Summary", enriched_titles)
        self.assertIn("Pricing Analysis", enriched_titles)
        self.assertIn("Key Takeaways", enriched_titles)

    def test_enrich_sections_with_report_outline_no_default_padding(self):
        """Test that report_outline=12 sections does NOT get padded with old 9 default sections."""
        from backend.app.agents.writer.writer import _enrich_sections_with_defaults

        # Simulate a pricing_analysis outline with 12 sections
        report_outline = {
            "sections": [
                {"title": f"Section {i}", "section_id": f"sec_{i:02d}"}
                for i in range(1, 13)
            ]
        }

        raw_sections = [
            {"section_title": f"Section {i}", "section_id": f"sec_{i:02d}", "content_markdown": f"Content {i}"}
            for i in range(1, 13)
        ]

        enriched = _enrich_sections_with_defaults(raw_sections, self.run_id, report_outline)

        # Should have exactly 12 sections, NOT 12 + 9 = 21
        self.assertEqual(len(enriched), 12)

        # Should NOT contain any of the default 9 section titles
        default_titles = {
            "Executive Summary", "Product Overview", "Feature Comparison",
            "Pricing Analysis", "User Persona", "Customer Voice",
            "SWOT Analysis", "Enterprise Readiness", "Key Findings",
        }
        enriched_titles = {s["section_title"] for s in enriched}
        overlap = enriched_titles & default_titles
        self.assertEqual(len(overlap), 0, f"Should not include default titles but found: {overlap}")

    def test_enrich_sections_without_report_outline_falls_back_to_defaults(self):
        """Test that without report_outline, defaults are used."""
        from backend.app.agents.writer.writer import _enrich_sections_with_defaults

        raw_sections = [
            {"section_title": "Executive Summary", "content_markdown": "Overview"},
        ]

        enriched = _enrich_sections_with_defaults(raw_sections, self.run_id, report_outline=None)

        # Should fall back to all 9 default sections
        self.assertEqual(len(enriched), 9)

    def test_template_sections_no_ai_agent_platform(self):
        """Test fallback template does not contain 'AI Agent platform' hardcoding."""
        from backend.app.agents.writer.writer import _template_sections_from_claims

        signed_claims = [
            {
                "claim_id": "c1",
                "product_id": "slack",
                "dimension": "pricing_model",
                "claim_text": "Slack offers free tier.",
                "evidence_ids": ["ev_1"],
                "confidence": 0.9,
                "risk_level": "low",
                "claim_type": "factual_summary",
            }
        ]

        sections = _template_sections_from_claims(signed_claims, self.run_id)
        all_content = " ".join(s.get("content_markdown", "") for s in sections)

        self.assertNotIn("AI Agent platform", all_content)
        self.assertNotIn("AI Agent platforms", all_content)

    def test_assemble_final_report_with_report_outline(self):
        """Test _assemble_final_report passes report_outline to _enrich_sections_with_defaults."""
        from backend.app.agents.writer.writer import _assemble_final_report

        report_outline = {
            "sections": [
                {"title": "Section A", "section_id": "sec_a"},
                {"title": "Section B", "section_id": "sec_b"},
            ]
        }

        raw_sections = [
            {"section_title": "Section A", "section_id": "sec_a", "content_markdown": "Content A"},
        ]

        signed_claims = [
            {
                "claim_id": "c1",
                "product_id": "slack",
                "dimension": "pricing_model",
                "claim_text": "Test claim.",
                "evidence_ids": ["ev_1"],
                "confidence": 0.9,
                "risk_level": "low",
                "claim_type": "factual_summary",
            }
        ]

        report = _assemble_final_report(raw_sections, signed_claims, self.run_id, report_outline)

        # Should include report_outline sections (Section A, Section B) not padded with defaults
        self.assertEqual(len(report["sections"]), 2)
        titles = {s["section_title"] for s in report["sections"]}
        self.assertIn("Section A", titles)
        self.assertIn("Section B", titles)

    def test_analyst_extracts_from_product_name_and_name(self):
        """Test AnalystAgent extracts Slack/Teams from product_name and name fields."""
        from backend.app.agents.analyst.analyst import AnalystAgent

        agent = AnalystAgent()

        # Test product_name field
        task_brief = {
            "products": [{"product_name": "Slack"}, {"product_name": "Microsoft Teams"}],
        }
        valid_products, _ = agent._extract_valid_products_and_dimensions(task_brief)
        self.assertIn("slack", valid_products)
        self.assertIn("microsoft_teams", valid_products)

        # Test name field (competitors)
        task_brief2 = {
            "competitors": [{"name": "Zoom"}, {"name": "Google Meet"}],
        }
        valid_products2, _ = agent._extract_valid_products_and_dimensions(task_brief2)
        self.assertIn("zoom", valid_products2)
        self.assertIn("google_meet", valid_products2)


if __name__ == "__main__":
    unittest.main()
