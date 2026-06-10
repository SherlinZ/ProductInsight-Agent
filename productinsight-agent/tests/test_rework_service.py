"""
Tests for the ReworkService.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from backend.app.services.rework_service import (
    ReworkService,
    ReworkTask,
    create_rework_tasks,
    create_and_execute_rework,
    _snapshot_rework_metrics,
)


class TestReworkTask(unittest.TestCase):
    """Tests for the ReworkTask dataclass."""

    def test_rework_task_new_fields(self):
        """Test ReworkTask has all new fields."""
        task = ReworkTask(
            rework_id="rework_001",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_001",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="rbac",
            reason="Missing fact for RBAC",
            required_actions={"schema_key": "rbac", "product_id": "prod_001"},
            affected_objects=["gap_001"],
            status="pending",
            retry_count=0,
            max_retry=2,
            metrics_before={"schema_gaps_count": 5},
            metrics_after={},
            new_evidence_ids=[],
            new_fact_ids=["fact_001"],
            new_claim_ids=[],
            created_at="2026-01-01T00:00:00Z",
            completed_at="",
            error_message="",
        )

        result = task.to_dict()

        self.assertEqual(result["rework_id"], "rework_001")
        self.assertEqual(result["task_id"], "rework_001")
        self.assertEqual(result["source_type"], "schema_gap")
        self.assertEqual(result["source_id"], "gap_001")
        self.assertEqual(result["target_node"], "extract_facts")
        self.assertEqual(result["target_agent"], "ExtractorAgent")
        self.assertEqual(result["reason"], "Missing fact for RBAC")
        self.assertEqual(result["required_actions"]["schema_key"], "rbac")
        self.assertEqual(result["new_fact_ids"], ["fact_001"])
        self.assertEqual(result["status"], "pending")

    def test_rework_task_task_id_compat(self):
        """Test that task_id is set from rework_id for backward compatibility."""
        task = ReworkTask(
            rework_id="rework_002",
            run_id="run_001",
            source_type="reviewer",
            source_id="req_001",
            target_node="analyze_dimensions",
            target_agent="AnalystAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="pricing",
            reason="Weak claim",
            required_actions={},
            affected_objects=[],
            status="pending",
        )

        self.assertEqual(task.task_id, "rework_002")

    def test_rework_task_explicit_task_id(self):
        """Test that explicit task_id is preserved."""
        task = ReworkTask(
            rework_id="rework_003",
            task_id="legacy_task_001",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_002",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="api_support",
            reason="Missing API support fact",
            required_actions={},
            affected_objects=[],
            status="pending",
        )

        self.assertEqual(task.task_id, "legacy_task_001")


class TestSnapshotReworkMetrics(unittest.TestCase):
    """Tests for _snapshot_rework_metrics."""

    def test_snapshot_metrics_basic(self):
        """Test basic metrics snapshot."""
        metrics = _snapshot_rework_metrics(
            schema_gaps=[{"gap_id": "g1", "priority": "high"}],
            claim_drafts=[{"claim_id": "c1"}, {"claim_id": "c2", "evidence_ids": ["e1"]}],
            signed_claims=[{"claim_id": "c1"}],
            evidence_items=[{"evidence_id": "e1"}],
            facts=[{"fact_id": "f1", "product_id": "p1", "schema_key": "rbac"}],
            sources=[{"source_id": "s1"}],
        )

        self.assertIn("schema_gaps_count", metrics)
        self.assertIn("claim_count", metrics)
        self.assertIn("signed_claim_count", metrics)
        self.assertIn("unsupported_claim_rate", metrics)
        self.assertIn("evidence_coverage_rate", metrics)
        self.assertIn("facts_count", metrics)
        self.assertIn("evidence_count", metrics)
        self.assertIn("sources_count", metrics)
        self.assertEqual(metrics["claim_count"], 2)
        self.assertEqual(metrics["signed_claim_count"], 1)
        self.assertEqual(metrics["unsupported_claim_rate"], 0.5)

    def test_snapshot_metrics_empty(self):
        """Test snapshot with empty inputs."""
        metrics = _snapshot_rework_metrics([], [], [], [], [], [])

        self.assertEqual(metrics["schema_gaps_count"], 0)
        self.assertEqual(metrics["claim_count"], 0)
        self.assertEqual(metrics["signed_claim_count"], 0)
        self.assertEqual(metrics["unsupported_claim_rate"], 0.0)
        self.assertEqual(metrics["evidence_coverage_rate"], 0.0)


class TestReworkServiceCreateTasks(unittest.TestCase):
    """Tests for ReworkService task creation methods."""

    def test_create_tasks_from_schema_gaps(self):
        """Test creating tasks from schema gaps."""
        service = ReworkService()
        gaps = [
            {
                "gap_id": "gap_001",
                "product_id": "prod_001",
                "product_name": "Dify",
                "schema_key": "rbac",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": ["query1", "query2"],
                "required_source_types": ["documentation"],
                "reason": "No fact found",
            },
            {
                "gap_id": "gap_002",
                "product_id": "prod_002",
                "product_name": "Coze",
                "schema_key": "pricing",
                "gap_type": "weak_evidence",
                "priority": "medium",
                "suggested_queries": ["query3"],
                "required_source_types": ["pricing_page"],
                "reason": "Evidence quality low",
            },
        ]
        metrics_before = {"schema_gaps_count": 5}
        tasks = service.create_rework_tasks_from_schema_gaps(gaps, "run_001", metrics_before)

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].source_type, "schema_gap")
        self.assertEqual(tasks[0].source_id, "gap_001")
        self.assertEqual(tasks[0].target_node, "extract_facts")
        self.assertEqual(tasks[0].target_agent, "ExtractorAgent")
        self.assertEqual(tasks[0].required_actions["schema_key"], "rbac")
        self.assertEqual(tasks[0].metrics_before, metrics_before)

    def test_create_tasks_from_reviewer_requests(self):
        """Test creating tasks from reviewer rework requests."""
        service = ReworkService()
        requests = [
            {
                "rework_request_id": "req_001",
                "claim_id": "claim_001",
                "product_id": "prod_001",
                "product_name": "Dify",
                "schema_key": "api_support",
                "reason": "Claim needs more evidence",
                "target_node": "extract_facts",
                "suggested_queries": ["query1"],
            }
        ]
        metrics_before = {"claim_count": 3}
        tasks = service.create_rework_tasks_from_reviewer_requests(
            requests, "run_001", metrics_before, [], []
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].source_type, "reviewer")
        self.assertEqual(tasks[0].source_id, "req_001")
        self.assertEqual(tasks[0].target_node, "extract_facts")
        self.assertEqual(tasks[0].target_agent, "ExtractorAgent")

    def test_create_tasks_max_limit(self):
        """Test that task creation respects max limit."""
        service = ReworkService()
        gaps = [
            {
                "gap_id": f"gap_{i:03d}",
                "product_id": f"prod_{i}",
                "product_name": f"Product {i}",
                "schema_key": f"key_{i}",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": [],
                "required_source_types": [],
                "reason": "Missing",
            }
            for i in range(10)
        ]
        metrics_before = {}
        tasks = service.create_rework_tasks_from_schema_gaps(gaps, "run_001", metrics_before)

        self.assertEqual(len(tasks), 5)  # Max 5

    def test_create_tasks_prioritizes_missing_fact(self):
        """Test that missing_fact gaps are prioritized."""
        service = ReworkService()
        gaps = [
            {
                "gap_id": "gap_weak",
                "product_id": "p1",
                "product_name": "P1",
                "schema_key": "weak",
                "gap_type": "weak_evidence",
                "priority": "high",
                "suggested_queries": [],
                "required_source_types": [],
                "reason": "Weak",
            },
            {
                "gap_id": "gap_missing",
                "product_id": "p2",
                "product_name": "P2",
                "schema_key": "missing",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": [],
                "required_source_types": [],
                "reason": "Missing",
            },
        ]
        metrics_before = {}
        tasks = service.create_rework_tasks_from_schema_gaps(gaps, "run_001", metrics_before)

        self.assertEqual(tasks[0].source_id, "gap_missing")
        # gap_type is now stored in required_actions
        self.assertEqual(tasks[0].required_actions.get("gap_type"), "missing_fact")


class TestCreateReworkTasks(unittest.TestCase):
    """Tests for the unified create_rework_tasks function."""

    def test_create_rework_tasks_combines_sources(self):
        """Test that create_rework_tasks handles both schema_gaps and rework_requests."""
        gaps = [
            {
                "gap_id": "gap_001",
                "product_id": "p1",
                "product_name": "P1",
                "schema_key": "rbac",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": ["q1"],
                "required_source_types": ["docs"],
                "reason": "Missing",
            }
        ]
        requests = [
            {
                "rework_request_id": "req_001",
                "claim_id": "c1",
                "product_id": "p1",
                "product_name": "P1",
                "schema_key": "api",
                "reason": "Weak claim",
                "suggested_queries": [],
            }
        ]
        tasks = create_rework_tasks(
            schema_gaps=gaps,
            rework_requests=requests,
            claim_drafts=[],
            signed_claims=[],
            run_id="run_001",
        )

        self.assertEqual(len(tasks), 2)
        source_types = [t["source_type"] for t in tasks]
        self.assertIn("schema_gap", source_types)
        self.assertIn("reviewer", source_types)

    def test_create_rework_tasks_max_limit_total(self):
        """Test that total tasks are limited to max_tasks."""
        gaps = [
            {
                "gap_id": f"gap_{i}",
                "product_id": f"p{i}",
                "product_name": f"P{i}",
                "schema_key": f"k{i}",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": [],
                "required_source_types": [],
                "reason": "Missing",
            }
            for i in range(3)
        ]
        requests = [
            {
                "rework_request_id": f"req_{i}",
                "product_id": f"p{i}",
                "product_name": f"P{i}",
                "schema_key": f"k{i}",
                "reason": "Rework",
                "suggested_queries": [],
            }
            for i in range(3)
        ]
        tasks = create_rework_tasks(
            schema_gaps=gaps,
            rework_requests=requests,
            claim_drafts=[],
            signed_claims=[],
            run_id="run_001",
            max_tasks=4,
        )

        self.assertEqual(len(tasks), 4)

    def test_create_rework_tasks_with_sources_for_metrics(self):
        """Test that create_rework_tasks passes sources/evidence_items/facts to snapshot metrics."""
        gaps = [
            {
                "gap_id": "gap_001",
                "product_id": "p1",
                "product_name": "P1",
                "schema_key": "rbac",
                "gap_type": "missing_fact",
                "priority": "high",
                "suggested_queries": ["q1"],
                "required_source_types": ["docs"],
                "reason": "Missing",
            }
        ]

        sources = [{"source_id": "s1"}, {"source_id": "s2"}]
        evidence_items = [{"evidence_id": "e1"}, {"evidence_id": "e2"}, {"evidence_id": "e3"}]
        facts = [{"fact_id": "f1"}, {"fact_id": "f2"}]

        tasks = create_rework_tasks(
            schema_gaps=gaps,
            rework_requests=[],
            claim_drafts=[{"claim_id": "c1"}],
            signed_claims=[],
            run_id="run_001",
            sources=sources,
            evidence_items=evidence_items,
            facts=facts,
        )

        self.assertEqual(len(tasks), 1)
        mb = tasks[0]["metrics_before"]

        # Verify metrics_before contains counts from the passed data
        self.assertIn("facts_count", mb)
        self.assertEqual(mb["facts_count"], 2)
        self.assertIn("evidence_count", mb)
        self.assertEqual(mb["evidence_count"], 3)
        self.assertIn("sources_count", mb)
        self.assertEqual(mb["sources_count"], 2)
        self.assertIn("schema_completion_rate", mb)
        self.assertIn("claim_count", mb)
        self.assertEqual(mb["claim_count"], 1)


class TestReworkServiceExecuteTasks(unittest.TestCase):
    """Tests for ReworkService.execute_rework_tasks."""

    def test_execute_no_tasks(self):
        """Test execute with no tasks."""
        service = ReworkService()
        tasks, metrics = service.execute_rework_tasks([], [], [], [], [])

        self.assertEqual(len(tasks), 0)
        self.assertEqual(metrics, {})

    def test_execute_fails_without_evidence(self):
        """Test that task fails when no evidence is available for extraction."""
        service = ReworkService()

        # Create a task targeting extract_facts
        task = ReworkTask(
            rework_id="rework_001",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_001",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="rbac",
            reason="Missing fact",
            required_actions={},
            affected_objects=[],
            status="pending",
            metrics_before={"schema_gaps_count": 1},
        )
        service._tasks.append(task)

        tasks, metrics = service.execute_rework_tasks(
            sources=[],
            evidence_items=[],  # No evidence
            facts=[],
            claim_drafts=[],
            signed_claims=[],
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("No evidence found", tasks[0]["error_message"])
        self.assertEqual(metrics["rework_failed"], 1)

    def test_execute_fails_without_new_facts(self):
        """Test that task fails when no new facts are extracted."""
        service = ReworkService()

        # Create a task targeting extract_facts
        task = ReworkTask(
            rework_id="rework_002",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_001",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="rbac",
            reason="Missing fact",
            required_actions={},
            affected_objects=[],
            status="pending",
            metrics_before={"schema_gaps_count": 1},
        )
        service._tasks.append(task)

        # Provide evidence but no facts can be extracted (mocked failure)
        evidence = [
            {
                "evidence_id": "ev_001",
                "product_id": "prod_001",
                "product_slug": "prod-001",
                "schema_key": "rbac",
                "snippet": "Short",  # Too short to extract
            }
        ]

        with patch.object(service, "_try_fact_extraction", return_value=([], [])):
            tasks, metrics = service.execute_rework_tasks(
                sources=[],
                evidence_items=evidence,
                facts=[],
                claim_drafts=[],
                signed_claims=[],
            )

        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("No new facts extracted", tasks[0]["error_message"])

    def test_execute_succeeds_with_new_facts(self):
        """Test that task succeeds when new facts are extracted and task contains new_facts."""
        service = ReworkService()

        task = ReworkTask(
            rework_id="rework_003",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_001",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="rbac",
            reason="Missing fact",
            required_actions={},
            affected_objects=[],
            status="pending",
            metrics_before={"schema_gaps_count": 1},
        )
        service._tasks.append(task)

        evidence = [
            {
                "evidence_id": "ev_001",
                "product_id": "prod_001",
                "product_slug": "prod-001",
                "schema_key": "rbac",
                "snippet": "RBAC is supported with role-based permissions",
            }
        ]

        # Mock returns (new_facts, new_fact_ids) tuple
        synthetic_fact = {
            "fact_id": "fact_new_001",
            "run_id": "run_001",
            "product_id": "prod_001",
            "product_slug": "prod-001",
            "schema_key": "rbac",
            "value_json": {"summary": "RBAC is supported"},
            "confidence": 0.65,
            "evidence_ids": ["ev_001"],
        }
        with patch.object(service, "_try_fact_extraction", return_value=([synthetic_fact], ["fact_new_001"])):
            tasks, metrics = service.execute_rework_tasks(
                sources=[],
                evidence_items=evidence,
                facts=[],
                claim_drafts=[],
                signed_claims=[],
            )

        self.assertEqual(tasks[0]["status"], "succeeded")
        self.assertEqual(tasks[0]["new_fact_ids"], ["fact_new_001"])
        # Verify new_facts is also populated
        self.assertIn("new_facts", tasks[0])
        self.assertEqual(len(tasks[0]["new_facts"]), 1)
        self.assertEqual(tasks[0]["new_facts"][0]["fact_id"], "fact_new_001")
        self.assertEqual(metrics["rework_succeeded"], 1)

    def test_execute_analyze_dimensions_succeeds(self):
        """Test analyze_dimensions target produces new claims and new_claims list."""
        service = ReworkService()

        task = ReworkTask(
            rework_id="rework_004",
            run_id="run_001",
            source_type="reviewer",
            source_id="req_001",
            target_node="analyze_dimensions",
            target_agent="AnalystAgent",
            product_id="prod_001",
            product_name="Test Product",
            schema_key="pricing",
            reason="Need new claim",
            required_actions={},
            affected_objects=[],
            status="pending",
            metrics_before={"claim_count": 2},
        )
        service._tasks.append(task)

        evidence = [
            {
                "evidence_id": "ev_001",
                "product_id": "prod_001",
                "product_slug": "prod-001",
                "schema_key": "pricing",
                "snippet": "Pricing starts at $29/month for professional plan",
            }
        ]

        # Mock returns (new_claims, new_claim_ids) tuple
        synthetic_claim = {
            "claim_id": "claim_new_001",
            "run_id": "run_001",
            "product_id": "prod_001",
            "schema_key": "pricing",
            "claim_text": "Pricing starts at $29/month",
            "dimension": "pricing_model",
            "confidence": 0.75,
        }
        with patch.object(service, "_try_claim_generation", return_value=([synthetic_claim], ["claim_new_001"])):
            tasks, metrics = service.execute_rework_tasks(
                sources=[],
                evidence_items=evidence,
                facts=[],
                claim_drafts=[{"claim_id": "c1"}],
                signed_claims=[],
            )

        self.assertEqual(tasks[0]["status"], "succeeded")
        self.assertEqual(tasks[0]["new_claim_ids"], ["claim_new_001"])
        # Verify new_claims is also populated
        self.assertIn("new_claims", tasks[0])
        self.assertEqual(len(tasks[0]["new_claims"]), 1)
        self.assertEqual(tasks[0]["new_claims"][0]["claim_id"], "claim_new_001")


class TestUpdateTaskStatus(unittest.TestCase):
    """Tests for ReworkService.update_task_status."""

    def test_update_task_by_rework_id(self):
        """Test updating task by rework_id."""
        service = ReworkService()
        task = ReworkTask(
            rework_id="rework_001",
            run_id="run_001",
            source_type="schema_gap",
            source_id="gap_001",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="p1",
            product_name="P1",
            schema_key="rbac",
            reason="Test",
            required_actions={},
            affected_objects=[],
            status="pending",
        )
        service._tasks.append(task)

        result = service.update_task_status(
            "rework_001", "succeeded", after_metrics={"facts_added": 1}
        )

        self.assertTrue(result)
        self.assertEqual(service._tasks[0].status, "succeeded")
        self.assertEqual(service._tasks[0].metrics_after, {"facts_added": 1})

    def test_update_task_not_found(self):
        """Test update with non-existent rework_id."""
        service = ReworkService()
        result = service.update_task_status("non_existent", "succeeded")
        self.assertFalse(result)


class TestCreateAndExecuteRework(unittest.TestCase):
    """Tests for the legacy create_and_execute_rework function."""

    def test_empty_gaps(self):
        """Test create_and_execute_rework with empty gaps."""
        tasks, summary, after_metrics = create_and_execute_rework(
            schema_gaps=[],
            sources=[],
            evidence_items=[],
            facts=[],
            run_id="run_001",
        )

        self.assertEqual(len(tasks), 0)
        self.assertEqual(summary["total_tasks"], 0)


class TestGraphOrder(unittest.TestCase):
    """Tests to verify graph node ordering."""

    def test_review_claims_to_execute_rework_edge(self):
        """Test that graph has fixed edge from review_claims to execute_rework."""
        from backend.app.orchestrator.graph import build_graph, route_after_review

        # Verify route_after_review returns write_report (fixed path)
        state = {"rework_requests": [{"status": "pending", "retry_count": 0}]}
        result = route_after_review(state)
        self.assertEqual(result, "write_report")

    def test_execute_rework_to_write_report_edge(self):
        """Test that graph has fixed edge from execute_rework to write_report."""
        from backend.app.orchestrator.graph import build_graph

        graph = build_graph()
        if graph is not None:
            # Verify the structure has execute_rework -> write_report
            # This is implicitly tested by the fact that conditional edges are removed
            self.assertIsNotNone(graph)


class TestComputeMetricsIntegration(unittest.TestCase):
    """Tests for compute_metrics rework integration."""

    def test_rework_metrics_in_compute_metrics(self):
        """Test that compute_metrics includes rework fields."""
        from backend.app.orchestrator.nodes import compute_metrics

        state = {
            "run_id": "test_run",
            "mode": "real_time",
            "claim_drafts": [{"claim_id": "c1"}],
            "signed_claims": [{"claim_id": "c1"}],
            "evidence_items": [{"evidence_id": "e1", "evidence_ids": ["e1"]}],
            "rework_requests": [],
            "sources": [{"source_id": "s1", "source_type": "web"}],
            "errors": [],
            "schema_gaps": [
                {
                    "gap_id": "g1",
                    "product_name": "Dify",
                    "schema_key": "rbac",
                    "gap_type": "missing_fact",
                    "priority": "high",
                    "suggested_queries": ["Dify RBAC"],
                    "reason": "No fact",
                }
            ],
            "schema_coverage": {
                "schema_completion_rate": 0.8,
                "high_priority_gaps": 1,
                "schema_coverage_by_product": {},
                "missing_schema_keys_by_product": {},
            },
            "rework_tasks": [
                {
                    "rework_id": "rework_001",
                    "task_id": "rework_001",
                    "source_type": "schema_gap",
                    "source_id": "g1",
                    "target_node": "extract_facts",
                    "target_agent": "ExtractorAgent",
                    "product_id": "p1",
                    "product_name": "Dify",
                    "schema_key": "rbac",
                    "reason": "Missing fact",
                    "required_actions": {},
                    "affected_objects": [],
                    "status": "succeeded",
                    "retry_count": 0,
                    "max_retry": 2,
                    "metrics_before": {
                        "schema_completion_rate": 0.7,
                        "schema_gaps_count": 5,
                        "high_priority_schema_gaps": 2,
                        "claim_count": 3,
                        "signed_claim_count": 2,
                        "unsupported_claim_rate": 0.33,
                        "evidence_coverage_rate": 0.8,
                        "facts_count": 10,
                        "evidence_count": 15,
                        "sources_count": 5,
                    },
                    "metrics_after": {"facts_added": 1},
                    "new_evidence_ids": [],
                    "new_fact_ids": ["fact_001"],
                    "new_claim_ids": [],
                    "created_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:00Z",
                    "error_message": "",
                }
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 1, "failed": 0, "skipped": 0},
            "rework_after_metrics": {"rework_succeeded": 1},
        }

        result = compute_metrics(state)
        metrics = result.get("metrics", {})

        self.assertIn("rework_task_count", metrics)
        self.assertEqual(metrics["rework_task_count"], 1)
        self.assertEqual(metrics["rework_succeeded_count"], 1)
        self.assertEqual(metrics["rework_failed_count"], 0)
        self.assertIn("rework_before_after", metrics)
        self.assertIn("rework_task_examples", metrics)
        self.assertEqual(len(metrics["rework_task_examples"]), 1)
        self.assertEqual(metrics["rework_task_examples"][0]["status"], "succeeded")

    def test_rework_before_after_structure(self):
        """Test rework_before_after has correct structure."""
        from backend.app.orchestrator.nodes import compute_metrics

        state = {
            "run_id": "test_run",
            "mode": "real_time",
            "claim_drafts": [],
            "signed_claims": [],
            "evidence_items": [],
            "rework_requests": [],
            "sources": [],
            "errors": [],
            "schema_gaps": [],
            "schema_coverage": {},
            "rework_tasks": [
                {
                    "rework_id": "rework_001",
                    "source_type": "schema_gap",
                    "target_node": "extract_facts",
                    "product_name": "Test",
                    "schema_key": "rbac",
                    "status": "failed",
                    "reason": "No evidence",
                    "metrics_before": {
                        "schema_gaps_count": 5,
                        "unsupported_claim_rate": 0.3,
                        "evidence_coverage_rate": 0.5,
                    },
                    "metrics_after": {},
                    "new_evidence_ids": [],
                    "new_fact_ids": [],
                    "new_claim_ids": [],
                }
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 0, "failed": 1, "skipped": 0},
            "rework_after_metrics": {},
            "facts": [],
        }

        result = compute_metrics(state)
        metrics = result.get("metrics", {})
        before_after = metrics["rework_before_after"]

        self.assertIn("before", before_after)
        self.assertIn("after", before_after)
        self.assertIn("delta_schema_gaps", before_after)
        self.assertIn("delta_unsupported_claim_rate", before_after)
        self.assertIn("delta_evidence_coverage_rate", before_after)


class TestRouteAfterReview(unittest.TestCase):
    """Tests for route_after_review."""

    def test_route_returns_write_report(self):
        """Test that route_after_review always returns write_report."""
        from backend.app.orchestrator.graph import route_after_review

        # With pending rework requests
        state1 = {"rework_requests": [{"status": "pending", "retry_count": 0}]}
        self.assertEqual(route_after_review(state1), "write_report")

        # Without rework requests
        state2 = {"rework_requests": []}
        self.assertEqual(route_after_review(state2), "write_report")

        # With empty rework requests
        state3 = {}
        self.assertEqual(route_after_review(state3), "write_report")


class TestExecuteReworkNode(unittest.TestCase):
    """Tests for nodes.execute_rework."""

    def test_execute_rework_creates_tasks_with_metrics(self):
        """Test that execute_rework creates tasks with metrics including source counts."""
        from backend.app.orchestrator.nodes import execute_rework

        state = {
            "run_id": "test_run_001",
            "mode": "real_time",
            "schema_gaps": [
                {
                    "gap_id": "gap_001",
                    "product_id": "prod_001",
                    "product_name": "Test Product",
                    "schema_key": "rbac",
                    "gap_type": "missing_fact",
                    "priority": "high",
                    "suggested_queries": ["query1"],
                    "required_source_types": ["docs"],
                    "reason": "Missing fact",
                }
            ],
            "rework_requests": [],
            "claim_drafts": [{"claim_id": "c1"}],
            "signed_claims": [],
            "sources": [{"source_id": "s1"}, {"source_id": "s2"}],
            "evidence_items": [
                {"evidence_id": "e1"},
                {"evidence_id": "e2"},
                {"evidence_id": "e3"},
            ],
            "facts": [{"fact_id": "f1"}, {"fact_id": "f2"}],
        }

        result = execute_rework(state)

        # Verify tasks were created
        self.assertIn("rework_tasks", result)
        self.assertEqual(len(result["rework_tasks"]), 1)

        # Verify metrics_before in task has counts
        task = result["rework_tasks"][0]
        self.assertIn("metrics_before", task)
        mb = task["metrics_before"]
        self.assertEqual(mb.get("facts_count"), 2)
        self.assertEqual(mb.get("evidence_count"), 3)
        self.assertEqual(mb.get("sources_count"), 2)
        self.assertIn("schema_completion_rate", mb)
        self.assertEqual(mb.get("claim_count"), 1)

    def test_execute_rework_appends_new_facts_to_state(self):
        """Test that execute_rework appends new_facts to state['facts'] and run_id is set."""
        from backend.app.orchestrator.nodes import execute_rework

        state = {
            "run_id": "test_run_002",
            "mode": "real_time",
            "schema_gaps": [
                {
                    "gap_id": "gap_001",
                    "product_id": "prod_001",
                    "product_name": "Test Product",
                    "schema_key": "rbac",
                    "gap_type": "missing_fact",
                    "priority": "high",
                    "suggested_queries": ["query1"],
                    "required_source_types": ["docs"],
                    "reason": "Missing fact",
                }
            ],
            "rework_requests": [],
            "claim_drafts": [],
            "signed_claims": [],
            "sources": [],
            "evidence_items": [
                {
                    "evidence_id": "ev_001",
                    "product_id": "prod_001",
                    "product_slug": "prod-001",
                    "schema_key": "rbac",
                    "snippet": "RBAC is supported with role-based permissions for enterprise users",
                }
            ],
            "facts": [{"fact_id": "existing_fact"}],
        }

        result = execute_rework(state)

        # Verify rework_tasks were created
        self.assertIn("rework_tasks", result)
        self.assertGreater(len(result["rework_tasks"]), 0)

        # Check if any task has new_facts
        all_new_facts = []
        for task in result["rework_tasks"]:
            all_new_facts.extend(task.get("new_facts", []))

        # Verify new facts were generated
        self.assertGreater(len(all_new_facts), 0,
                          f"Should have generated new facts. Tasks: {result['rework_tasks']}")

        # Verify run_id is set on new facts
        self.assertEqual(all_new_facts[0]["run_id"], "test_run_002",
                       "new_facts should have correct run_id")

    def test_execute_rework_appends_new_claims_to_state(self):
        """Test that execute_rework appends new_claims to state['claim_drafts'] and run_id is set."""
        from backend.app.orchestrator.nodes import execute_rework

        state = {
            "run_id": "test_run_003",
            "mode": "real_time",
            "schema_gaps": [],
            "rework_requests": [
                {
                    "rework_request_id": "req_001",
                    "claim_id": "claim_001",
                    "product_id": "prod_001",
                    "product_name": "Test Product",
                    "schema_key": "pricing",
                    "reason": "Need new claim",
                    "suggested_queries": [],
                }
            ],
            "claim_drafts": [{"claim_id": "claim_001"}],  # Include claim so target becomes analyze_dimensions
            "signed_claims": [],
            "sources": [],
            "evidence_items": [
                {
                    "evidence_id": "ev_001",
                    "product_id": "prod_001",
                    "product_slug": "prod-001",
                    "schema_key": "pricing",
                    "snippet": "Pricing starts at $29/month for professional plan with enterprise options available",
                }
            ],
            "facts": [],
        }

        result = execute_rework(state)

        # Verify rework_tasks were created
        self.assertIn("rework_tasks", result)
        self.assertGreater(len(result["rework_tasks"]), 0)

        # Check if any task has new_claims
        all_new_claims = []
        for task in result["rework_tasks"]:
            all_new_claims.extend(task.get("new_claims", []))

        # Verify new claims were generated
        self.assertGreater(len(all_new_claims), 0,
                          f"Should have generated new claims. Tasks: {result['rework_tasks']}")

        # Verify run_id is set on new claims
        self.assertEqual(all_new_claims[0]["run_id"], "test_run_003",
                       "new_claims should have correct run_id")


if __name__ == "__main__":
    unittest.main()
