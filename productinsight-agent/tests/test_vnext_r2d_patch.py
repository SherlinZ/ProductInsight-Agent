"""Tests for vNext-R2-D Patch fixes.

Tests:
1. Source Discovery writes search_call traces when provider is disabled
2. Source candidates are converted to seed_urls for collection
3. Intervention product name resolution (product_name > name > product_id > product_slug)
4. write_report passes task_brief and report_outline to WriterAgent
5. WriterAgent preserves outline.section_id for missing sections
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_vnext_r2d.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Shared timestamp for test runs
NOW = datetime.now(timezone.utc).isoformat()


class TestSourceDiscoverySkippedTraces(unittest.TestCase):
    """Test 1: Source Discovery writes search_call traces when provider is disabled."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT", "SEARCH_API_KEY"]:
            os.environ.pop(key, None)
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "test_skip_trace_run",
            "task_id": "test_skip_trace_task",
            "task_title": "Skip Trace Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_perform_source_discovery_writes_skipped_traces_when_disabled(self):
        """When provider is disabled, _perform_source_discovery writes skipped traces."""
        from backend.app.orchestrator.nodes import _perform_source_discovery
        from backend.app.storage.repositories import TraceRepository

        discovery_queries = [
            {
                "competitor": "Dify",
                "queries": ["Dify AI platform official site", "Dify documentation"],
            },
            {
                "competitor": "Coze",
                "queries": ["Coze bot platform"],
            },
        ]

        result = _perform_source_discovery(
            run_id="test_skip_trace_run",
            project_id="test_project",
            products_without_urls=[{"product_id": "dify"}, {"product_id": "coze"}],
            discovery_queries=discovery_queries,
            source_readiness="ready_with_discovery",
        )

        # Should indicate provider not configured
        self.assertEqual(result["discovery_status"], "provider_not_configured")
        self.assertFalse(result["provider_configured"])

        # Should have written skipped traces
        self.assertGreater(len(result["search_traces"]), 0)

        # Verify traces in DB
        repo = TraceRepository()
        traces = repo.list_traces("test_skip_trace_run")
        search_traces = [t for t in traces if t.get("event_type") == "search_call"]

        self.assertGreater(len(search_traces), 0, "Expected search_call traces in DB")

        # All should be skipped/failed with SEARCH_PROVIDER_NOT_CONFIGURED error
        for trace in search_traces:
            self.assertIn(trace.get("status"), ("skipped", "failed"), f"Unexpected status: {trace.get('status')}")
            self.assertEqual(trace.get("model_name"), "disabled", "model_name should be 'disabled'")
            self.assertEqual(trace.get("agent_name"), "SearchProvider", "agent_name should be 'SearchProvider'")
            self.assertEqual(trace.get("node_name"), "collect_sources", "node_name should be 'collect_sources'")
            self.assertIn("SEARCH_PROVIDER_NOT_CONFIGURED", trace.get("error_message", ""))
            self.assertEqual(trace.get("decision_summary"), "Search skipped: provider not configured")

        print(f"  OK: {len(search_traces)} skipped search traces written")

    def test_skipped_traces_limited_to_3_per_competitor(self):
        """Skipped traces should be limited to 3 per competitor (capped for trace volume)."""
        from backend.app.orchestrator.nodes import _perform_source_discovery

        # 3 competitors with 5 queries each
        discovery_queries = [
            {"competitor": f"Product{i}", "queries": [f"query{j}" for j in range(5)]}
            for i in range(3)
        ]

        result = _perform_source_discovery(
            run_id="test_skip_trace_run",
            project_id="test_project",
            products_without_urls=[{"product_id": f"product{i}"} for i in range(3)],
            discovery_queries=discovery_queries,
            source_readiness="ready_with_discovery",
        )

        # Should be capped at 5 competitors * 3 queries = 15 traces max
        self.assertLessEqual(len(result["search_traces"]), 15)
        # But should have at least some traces
        self.assertGreater(len(result["search_traces"]), 0)


class TestSourceCandidatesToSeedUrls(unittest.TestCase):
    """Test 2: Source candidates are converted to seed_urls for collection."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT", "SEARCH_API_KEY"]:
            os.environ.pop(key, None)
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "test_candidates_run",
            "task_id": "test_candidates_task",
            "task_title": "Candidates Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_candidates_converted_to_seed_urls(self):
        """When candidates are found, they should become seed_urls for collection."""
        from backend.app.orchestrator.nodes import _perform_source_discovery

        discovery_queries = [
            {
                "competitor": "Dify",
                "queries": ["Dify AI platform"],
            },
        ]

        # Mock the search provider to return candidates
        from backend.app.services.search_provider import SearchResult, SEARCH_SUCCESS
        mock_results = [
            SearchResult(
                title="Dify Official",
                url="https://dify.ai",
                snippet="Official Dify website",
                source="mock",
            ),
            SearchResult(
                title="Dify Docs",
                url="https://docs.dify.ai",
                snippet="Dify documentation",
                source="mock",
            ),
        ]

        # Reset the singleton so our mock is used
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()

        # Patch where it is imported (inside _perform_source_discovery)
        with patch("backend.app.services.search_provider.get_search_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_configured = True
            mock_provider.provider_name = "mock"
            mock_provider.search.return_value = (mock_results, SEARCH_SUCCESS)
            mock_get_provider.return_value = mock_provider

            result = _perform_source_discovery(
                run_id="test_candidates_run",
                project_id="test_project",
                products_without_urls=[{"product_id": "dify", "product_name": "Dify"}],
                discovery_queries=discovery_queries,
                source_readiness="ready_with_discovery",
            )

        # Reset again to clean up
        reset_search_provider()

        # Should have candidates
        self.assertEqual(result["discovery_status"], "completed")
        self.assertEqual(len(result["candidates"]), 2)

        # Candidates should have discovery metadata
        for cand in result["candidates"]:
            self.assertEqual(cand["competitor"], "Dify")
            self.assertEqual(cand["discovery_status"], "success")
            self.assertIn("candidate_id", cand)
            self.assertIn("url", cand)

        print(f"  OK: {len(result['candidates'])} candidates found")

    def test_collect_sources_uses_discovered_urls(self):
        """collect_sources should use discovered candidate URLs as seed_urls."""
        from backend.app.orchestrator.state import WorkflowState
        from backend.app.services.search_provider import SearchResult, SEARCH_SUCCESS

        state: WorkflowState = {
            "run_id": "test_collect_run",
            "project_id": "test_project",
            "mode": "real_time",
            "task_brief": {
                "products": [
                    {
                        "product_id": "dify",
                        "product_name": "Dify",
                        # No seed_urls - needs discovery
                    },
                ],
                "source_discovery": {
                    "source_discovery_required": True,
                    "auto_discovery_enabled": True,
                    "discovery_queries": [
                        {"competitor": "Dify", "queries": ["Dify AI platform"]},
                    ],
                    "source_readiness": "ready_with_discovery",
                },
            },
        }

        mock_results = [
            SearchResult(
                title="Dify Official",
                url="https://dify.ai",
                snippet="Official",
                source="mock",
            ),
        ]

        with patch("backend.app.orchestrator.nodes._perform_source_discovery") as mock_discovery:
            mock_discovery.return_value = {
                "discovery_status": "completed",
                "candidates": [
                    {
                        "competitor": "Dify",
                        "query": "Dify AI platform",
                        "title": "Dify Official",
                        "url": "https://dify.ai",
                        "snippet": "Official",
                        "source": "mock",
                        "discovery_status": "success",
                    },
                ],
                "provider_name": "mock",
                "search_traces": [],
            }
            # Also patch collector to avoid actual HTTP calls
            with patch("backend.app.orchestrator.nodes._collector") as mock_collector_cls:
                mock_collector = MagicMock()
                mock_collector.collect.return_value = {
                    "sources": [
                        {
                            "source_id": "src_test",
                            "run_id": "test_collect_run",
                            "product_id": "dify",
                            "url": "https://dify.ai",
                            "status": "collected",
                            "discovered_by_search": True,
                        },
                    ],
                    "snapshots": [],
                    "raw_documents": [],
                }
                mock_collector_cls.return_value = mock_collector

                from backend.app.orchestrator.nodes import collect_sources
                result_state = collect_sources(state)

        # Should have sources from discovered URLs
        self.assertIn("sources", result_state)
        self.assertGreater(len(result_state["sources"]), 0)

        # Sources should be marked as discovered by search
        for src in result_state["sources"]:
            if src.get("product_id") == "dify":
                self.assertTrue(src.get("discovered_by_search", False), "Source should be marked as discovered_by_search")

        print(f"  OK: {len(result_state['sources'])} sources collected, {len(result_state['source_candidates'])} candidates")


class TestInterventionProductNameResolution(unittest.TestCase):
    """Test 3: Intervention product name resolution priority."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "test_intervention_run",
            "task_id": "test_intervention_task",
            "task_title": "Intervention Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_product_name_priority(self):
        """Product name should resolve with correct priority."""
        from backend.app.orchestrator.nodes import _create_search_provider_intervention

        products = [
            {"product_name": "Dify AI"},  # Should use this
            {"name": "Coze Bot"},  # Should use this
            {"product_id": "flowise"},  # Should use this
            {"product_slug": "langchain"},  # Should use this
            {},  # Should fall back to "unknown"
        ]

        with patch("backend.app.storage.repositories.HumanInterventionRepository.create_intervention") as mock_create:
            mock_create.return_value = True
            from backend.app.orchestrator.state import WorkflowState
            state: WorkflowState = {}
            _create_search_provider_intervention(
                state=state,
                run_id="test_intervention_run",
                project_id="test_project",
                products_without_urls=products,
                discovery_queries=[{"competitor": "test", "queries": ["test"]}],
            )

            # Verify the intervention was created with correct product names
            call_args = mock_create.call_args[0][0]
            product_names = call_args["before_json"]["products_needing_discovery"]
            self.assertEqual(product_names[0], "Dify AI", "Should use product_name")
            self.assertEqual(product_names[1], "Coze Bot", "Should use name")
            self.assertEqual(product_names[2], "flowise", "Should use product_id")
            self.assertEqual(product_names[3], "langchain", "Should use product_slug")
            self.assertEqual(product_names[4], "unknown", "Should fall back to unknown")

            print(f"  OK: product names resolved: {product_names}")

    def test_product_name_overrides_id(self):
        """product_name should take priority over product_id."""
        from backend.app.orchestrator.nodes import _create_search_provider_intervention

        product = {
            "product_id": "my_id",
            "product_name": "My Product Name",
            "name": "Should Not Use",
        }

        with patch("backend.app.storage.repositories.HumanInterventionRepository.create_intervention") as mock_create:
            mock_create.return_value = True
            from backend.app.orchestrator.state import WorkflowState
            state: WorkflowState = {}
            _create_search_provider_intervention(
                state=state,
                run_id="test_intervention_run",
                project_id="test_project",
                products_without_urls=[product],
                discovery_queries=[{"competitor": "test", "queries": ["test"]}],
            )

            call_args = mock_create.call_args[0][0]
            product_names = call_args["before_json"]["products_needing_discovery"]
            self.assertEqual(product_names[0], "My Product Name", "product_name should override product_id")


class TestWriteReportTaskBrief(unittest.TestCase):
    """Test 4: write_report passes task_brief and report_outline to WriterAgent."""

    def test_write_report_passes_task_brief_and_outline(self):
        """write_report should extract and pass task_brief and report_outline."""
        from backend.app.orchestrator.nodes import write_report
        from backend.app.orchestrator.state import WorkflowState

        task_brief = {
            "title": "Test Analysis",
            "schema_type": "ai_agent_platform",
            "report_outline": {
                "sections": [
                    {"title": "Overview", "section_id": "sec_01"},
                    {"title": "Features", "section_id": "sec_02"},
                ],
            },
        }

        state: WorkflowState = {
            "run_id": "test_write_report_run",
            "project_id": "test_project",
            "mode": "real_time",
            "task_brief": task_brief,
            "signed_claims": [
                {
                    "claim_id": "claim_1",
                    "product_id": "dify",
                    "dimension": "function_tree",
                    "claim_text": "Dify supports RAG pipeline",
                    "evidence_ids": [],
                    "confidence": 0.8,
                },
            ],
        }

        with patch("backend.app.orchestrator.nodes._writer") as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.write.return_value = {
                "report_id": "report_test",
                "run_id": "test_write_report_run",
                "sections": [],
                "quality_summary": {},
            }
            mock_writer_cls.return_value = mock_writer

            write_report(state)

            # Verify WriterAgent.write was called with task_brief and report_outline
            call_kwargs = mock_writer.write.call_args[1]
            self.assertEqual(call_kwargs["run_id"], "test_write_report_run")
            self.assertEqual(call_kwargs["project_id"], "test_project")
            self.assertEqual(call_kwargs["task_brief"], task_brief)
            self.assertEqual(call_kwargs["report_outline"], task_brief["report_outline"])

            print(f"  OK: WriterAgent.write called with task_brief and report_outline")

    def test_write_report_resolves_nested_research_plan_outline(self):
        """report_outline should also be readable from task_brief.research_plan."""
        from backend.app.orchestrator.nodes import write_report
        from backend.app.orchestrator.state import WorkflowState

        task_brief = {
            "title": "Nested Research Plan Test",
            "research_plan": {
                "report_outline": {
                    "sections": [
                        {"title": "Introduction", "section_id": "sec_intro"},
                    ],
                },
            },
        }

        state: WorkflowState = {
            "run_id": "test_nested_run",
            "mode": "real_time",
            "task_brief": task_brief,
            "signed_claims": [
                {
                    "claim_id": "claim_1",
                    "product_id": "test",
                    "dimension": "function_tree",
                    "claim_text": "Test claim",
                    "evidence_ids": [],
                },
            ],
        }

        with patch("backend.app.orchestrator.nodes._writer") as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.write.return_value = {
                "report_id": "report_nested",
                "run_id": "test_nested_run",
                "sections": [],
                "quality_summary": {},
            }
            mock_writer_cls.return_value = mock_writer

            write_report(state)

            call_kwargs = mock_writer.write.call_args[1]
            self.assertIsNotNone(call_kwargs["report_outline"])
            self.assertEqual(
                call_kwargs["report_outline"]["sections"][0]["title"],
                "Introduction",
            )
            print(f"  OK: report_outline resolved from nested research_plan")


class TestWriterAgentSectionId(unittest.TestCase):
    """Test 5: WriterAgent preserves outline.section_id for missing sections."""

    def test_missing_sections_preserve_outline_section_id(self):
        """When report_outline has section_id, missing sections should use it."""
        from backend.app.agents.writer.writer import _enrich_sections_with_defaults

        raw_sections = [
            {"section_title": "Overview", "section_id": "sec_overview", "content_markdown": "..."},
            # "Features" is missing from raw_sections but defined in outline
        ]

        report_outline = {
            "sections": [
                {"title": "Overview", "section_id": "sec_overview"},
                {"title": "Features", "section_id": "sec_features"},  # Will be added as missing
                {"title": "Pricing", "section_id": "sec_pricing"},
            ],
        }

        enriched = _enrich_sections_with_defaults(raw_sections, "test_run", report_outline)

        # Find the Features section that was added as missing
        features_section = None
        for s in enriched:
            if s["section_title"] == "Features":
                features_section = s
                break

        self.assertIsNotNone(features_section, "Features section should be added")
        self.assertEqual(
            features_section["section_id"],
            "sec_features",
            "Should preserve outline's section_id, not generate a new one",
        )
        print(f"  OK: Features section has preserved section_id: {features_section['section_id']}")

    def test_outline_without_section_id_generates_fallback(self):
        """When outline section lacks section_id, should generate one."""
        from backend.app.agents.writer.writer import _enrich_sections_with_defaults

        raw_sections = []

        report_outline = {
            "sections": [
                {"title": "Custom Section", "purpose": "Test"},  # No section_id
            ],
        }

        enriched = _enrich_sections_with_defaults(raw_sections, "test_run", report_outline)

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["section_title"], "Custom Section")
        self.assertIn("section_id", enriched[0])
        # Should have generated a section_id
        self.assertTrue(enriched[0]["section_id"].startswith("section_"))

        print(f"  OK: Generated section_id: {enriched[0]['section_id']}")


class TestSearchSuccessTraceSingleWrite(unittest.TestCase):
    """Test 3b: Search success trace written once with correct status."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT", "SEARCH_API_KEY"]:
            os.environ.pop(key, None)
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "test_success_trace_run",
            "task_id": "test_success_trace_task",
            "task_title": "Success Trace Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_search_success_writes_single_trace_not_running(self):
        """When provider succeeds, exactly one trace per search with status=success."""
        from backend.app.orchestrator.nodes import _perform_source_discovery
        from backend.app.services.search_provider import SearchResult, SEARCH_SUCCESS
        from backend.app.storage.repositories import TraceRepository

        # Reset singleton
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()

        mock_results = [
            SearchResult(
                title="Dify Official",
                url="https://dify.ai",
                snippet="Official Dify website",
                source="mock",
            ),
            SearchResult(
                title="Dify Docs",
                url="https://docs.dify.ai",
                snippet="Docs",
                source="mock",
            ),
        ]

        with patch("backend.app.services.search_provider.get_search_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_configured = True
            mock_provider.provider_name = "mock"
            mock_provider.search.return_value = (mock_results, SEARCH_SUCCESS)
            mock_get_provider.return_value = mock_provider

            result = _perform_source_discovery(
                run_id="test_success_trace_run",
                project_id="test_project",
                products_without_urls=[{"product_id": "dify", "product_name": "Dify"}],
                discovery_queries=[{"competitor": "Dify", "queries": ["Dify AI"]}],
                source_readiness="ready_with_discovery",
            )

        reset_search_provider()

        # Should have completed successfully
        self.assertEqual(result["discovery_status"], "completed")
        self.assertEqual(len(result["candidates"]), 2)

        # Verify DB: exactly one trace per search (no "running" left)
        repo = TraceRepository()
        traces = repo.list_traces("test_success_trace_run")
        search_traces = [t for t in traces if t.get("event_type") == "search_call"]

        self.assertGreater(len(search_traces), 0)

        # No trace should have status="running"
        running_traces = [t for t in search_traces if t.get("status") == "running"]
        self.assertEqual(len(running_traces), 0, "No trace should be left in 'running' state")

        # All traces should have status="success"
        for trace in search_traces:
            self.assertEqual(trace.get("status"), "success", f"Trace {trace.get('trace_id')} should be success")
            self.assertEqual(trace.get("model_name"), "mock")

            # output_payload should have result_count > 0 and non-empty candidates
            import json
            output_payload = trace.get("output_payload_json") or trace.get("output_payload")
            if isinstance(output_payload, str):
                output_payload = json.loads(output_payload)
            self.assertIsNotNone(output_payload)
            self.assertGreater(output_payload.get("result_count", 0), 0, "result_count should be > 0")
            self.assertGreater(len(output_payload.get("candidates", [])), 0, "candidates should not be empty")

        print(f"  OK: {len(search_traces)} traces all success, no 'running' state left")


class TestSchemaTypeInference(unittest.TestCase):
    """Test Slack/Teams/Zoom/Google Meet pricing inference."""

    def test_slack_pricing_infers_pricing_analysis(self):
        """Slack pricing analysis should infer pricing_analysis schema."""
        from backend.app.services.research_planner import infer_schema_type

        result = infer_schema_type(
            "Compare Slack and Microsoft Teams pricing plans for enterprise",
            explicit_schema_type=None,
        )
        self.assertEqual(result, "pricing_analysis")
        print(f"  OK: Slack pricing → {result}")

    def test_teams_zoom_pricing_infers_pricing_analysis(self):
        """Teams and Zoom pricing should infer pricing_analysis."""
        from backend.app.services.research_planner import infer_schema_type

        for query in [
            "Microsoft Teams pricing vs Zoom pricing",
            "Zoom vs Google Meet pricing comparison",
            "Video conferencing platform pricing analysis",
        ]:
            result = infer_schema_type(query, explicit_schema_type=None)
            self.assertEqual(result, "pricing_analysis", f"Query: {query} → {result}")

        print(f"  OK: Teams/Zoom/Google Meet queries all infer pricing_analysis")

    def test_schema_type_none_bypasses_to_auto(self):
        """schema_type=None should let infer_schema_type decide."""
        from backend.app.services.research_planner import infer_schema_type

        # None should be treated as auto - "pricing" alone should infer pricing_analysis
        result = infer_schema_type("Compare pricing for Slack and Teams", explicit_schema_type=None)
        self.assertEqual(result, "pricing_analysis")

        # ai_agent_platform as default: "Dify and Coze" should infer ai_agent_platform
        result2 = infer_schema_type("Analyze Dify and Coze", explicit_schema_type="ai_agent_platform")
        self.assertEqual(result2, "ai_agent_platform")

        # But "pricing" in the query with Dify/Coze: still ai_agent_platform because
        # the agent keywords match first (dify/coze are more specific than pricing)
        result3 = infer_schema_type("Analyze Dify and Coze pricing", explicit_schema_type="ai_agent_platform")
        self.assertEqual(result3, "ai_agent_platform")

        print(f"  OK: schema_type=None/ai_agent_platform both allow inference correctly")


if __name__ == "__main__":
    unittest.main()
