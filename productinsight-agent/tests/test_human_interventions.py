"""
Tests for human interventions functionality.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_human_interventions.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"


class TestHumanInterventionsMigration(unittest.TestCase):
    """Test migration creates human_interventions table."""

    def setUp(self):
        from backend.app.storage.db import init_db, get_connection
        init_db()

    def test_table_exists(self):
        """Test human_interventions table is created."""
        from backend.app.storage.db import get_connection

        with get_connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = [t[0] for t in tables]

        self.assertIn("human_interventions", table_names)

    def test_workflow_nodes_allows_paused(self):
        """Test workflow_nodes table allows 'paused' status."""
        from backend.app.storage.db import get_connection

        with get_connection() as conn:
            # Try to insert a node with paused status
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_nodes
                    (node_id, run_id, node_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                ("test_paused_node", "test_run", "test_node", "paused"),
            )
            # If we get here without CHECK constraint error, test passes

        # Verify the node was inserted
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_nodes WHERE node_id = ?",
                ("test_paused_node",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["status"], "paused")

    def test_migration_preserves_existing_data(self):
        """Test migration preserves existing workflow_nodes data when updating CHECK constraint."""
        import os
        import tempfile
        from backend.app.storage.db import get_connection

        # Create a temporary database with old schema (without paused in CHECK)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db_path = f.name

        try:
            conn = __import__("sqlite3").connect(temp_db_path)
            conn.execute("PRAGMA foreign_keys = OFF;")

            # Create old workflow_nodes table WITHOUT paused in CHECK
            conn.execute("""
                CREATE TABLE workflow_nodes (
                    node_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    node_type TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'invalidated')),
                    input_summary_json TEXT,
                    output_summary_json TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    latency_ms INTEGER,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Insert 2 rows of existing data
            conn.execute(
                """
                INSERT INTO workflow_nodes
                    (node_id, run_id, node_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                ("old_run_node1", "old_run_001", "build_task_brief", "completed"),
            )
            conn.execute(
                """
                INSERT INTO workflow_nodes
                    (node_id, run_id, node_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                ("old_run_node2", "old_run_001", "plan_schema", "running"),
            )
            conn.commit()
            conn.close()

            # Run migration on the temp database
            import importlib.util
            # Use relative path from test file to migration file
            test_file = Path(__file__).resolve()
            migration_path = test_file.parents[1] / "backend" / "app" / "storage" / "migrations" / "005_human_interventions.py"
            spec = importlib.util.spec_from_file_location("migrate_005", str(migration_path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.migrate(temp_db_path)

            # Verify both rows are preserved
            conn2 = __import__("sqlite3").connect(temp_db_path)
            rows = conn2.execute(
                "SELECT node_id, node_name, status FROM workflow_nodes ORDER BY node_id"
            ).fetchall()
            conn2.close()

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0], ("old_run_node1", "build_task_brief", "completed"))
            self.assertEqual(rows[1], ("old_run_node2", "plan_schema", "running"))

            # Verify paused can be inserted after migration
            conn3 = __import__("sqlite3").connect(temp_db_path)
            conn3.execute(
                """
                INSERT INTO workflow_nodes
                    (node_id, run_id, node_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                ("new_paused_node", "old_run_001", "new_node", "paused"),
            )
            conn3.commit()
            conn3.close()

            # Verify the paused node was inserted
            conn4 = __import__("sqlite3").connect(temp_db_path)
            conn4.row_factory = __import__("sqlite3").Row
            row = conn4.execute(
                "SELECT status FROM workflow_nodes WHERE node_id = ?",
                ("new_paused_node",),
            ).fetchone()
            conn4.close()

            self.assertEqual(row["status"], "paused")
        finally:
            # Cleanup
            if os.path.exists(temp_db_path):
                os.unlink(temp_db_path)


class TestHumanInterventionRepository(unittest.TestCase):
    """Test HumanInterventionRepository methods."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        self.repo = None

    def _get_repo(self):
        if self.repo is None:
            from backend.app.storage.repositories import HumanInterventionRepository
            self.repo = HumanInterventionRepository()
        return self.repo

    def test_create_intervention(self):
        """Test creating a human intervention."""
        repo = self._get_repo()
        run_id = "test_run_001"

        intervention = {
            "intervention_id": "interv_001",
            "run_id": run_id,
            "node_name": "execute_rework",
            "artifact_type": "rework",
            "artifact_id": "rework_001",
            "action": "pending",
            "status": "pending",
            "before_json": {"task_status": "failed", "reason": "Network error"},
            "comment": "Review required",
            "created_at": "2026-01-01T00:00:00Z",
            "created_by": "system",
        }

        repo.create_intervention(intervention)

        # Verify it can be retrieved
        retrieved = repo.get_intervention("interv_001")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["run_id"], run_id)
        self.assertEqual(retrieved["artifact_type"], "rework")
        self.assertEqual(retrieved["status"], "pending")

    def test_list_interventions(self):
        """Test listing interventions for a run."""
        repo = self._get_repo()
        run_id = "test_run_002"

        # Create multiple interventions
        for i in range(3):
            repo.create_intervention({
                "intervention_id": f"interv_list_{i}",
                "run_id": run_id,
                "node_name": "test_node",
                "artifact_type": "general",
                "action": "pending",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00Z",
            })

        # List all
        interventions = repo.list_interventions(run_id)
        self.assertEqual(len(interventions), 3)

        # List by status
        pending = repo.list_interventions(run_id, status="pending")
        self.assertEqual(len(pending), 3)

    def test_resolve_intervention_approve(self):
        """Test resolving an intervention with approve action."""
        repo = self._get_repo()
        run_id = "test_run_003"

        repo.create_intervention({
            "intervention_id": "interv_approve",
            "run_id": run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        result = repo.resolve_intervention(
            "interv_approve",
            action="approve",
            comment="Approved by reviewer",
            resolved_by="human_user",
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["resolved_by"], "human_user")
        self.assertIsNotNone(result["resolved_at"])

    def test_resolve_intervention_edit(self):
        """Test resolving an intervention with edit action saves after_json."""
        repo = self._get_repo()
        run_id = "test_run_004"

        repo.create_intervention({
            "intervention_id": "interv_edit",
            "run_id": run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        after_json = {
            "edited_claim": "Updated claim text",
            "changes": ["Fixed pricing info"],
        }

        result = repo.resolve_intervention(
            "interv_edit",
            action="edit",
            after_json=after_json,
            comment="Edited claim based on review",
            resolved_by="human_user",
        )

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "edit")
        self.assertEqual(result["after_json"], after_json)
        self.assertIsNotNone(result["resolved_at"])

    def test_cancel_intervention(self):
        """Test cancelling an intervention."""
        repo = self._get_repo()
        run_id = "test_run_005"

        repo.create_intervention({
            "intervention_id": "interv_cancel",
            "run_id": run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        result = repo.cancel_intervention(
            "interv_cancel",
            comment="Cancelled - no longer needed",
        )

        self.assertEqual(result["status"], "cancelled")
        self.assertIn("no longer needed", result["comment"])
        self.assertIsNotNone(result["resolved_at"])

    def test_create_review_interventions_from_rework(self):
        """Test creating interventions for failed rework tasks."""
        repo = self._get_repo()
        run_id = "test_run_006"

        failed_tasks = [
            {
                "rework_id": "rework_failed_001",
                "task_id": "rework_failed_001",
                "status": "failed",
                "error_message": "Network timeout",
                "target_node": "extract_facts",
                "metrics_before": {"schema_gaps_count": 5},
            },
            {
                "rework_id": "rework_failed_002",
                "status": "failed",
                "error_message": "No evidence found",
                "target_node": "analyze_dimensions",
            },
        ]

        interventions = repo.create_review_interventions_from_rework(
            run_id=run_id,
            rework_tasks=failed_tasks,
        )

        self.assertEqual(len(interventions), 2)

        # Verify interventions were created in DB
        all_interventions = repo.list_interventions(run_id)
        self.assertEqual(len(all_interventions), 2)

        # Verify all are pending
        for interv in all_interventions:
            self.assertEqual(interv["status"], "pending")
            self.assertEqual(interv["artifact_type"], "rework")

    def test_create_review_interventions_from_review_issues_no_duplicate(self):
        """Test that each review issue creates exactly one intervention (no duplicates)."""
        repo = self._get_repo()
        run_id = "test_run_007"

        # Create two high-priority review issues
        review_issues = [
            {
                "issue_id": "issue_001",
                "artifact_type": "claim",
                "artifact_id": "claim_001",
                "priority": "high",
                "requires_human_review": True,
                "message": "First high priority issue",
                "node_name": "review_claims",
            },
            {
                "issue_id": "issue_002",
                "artifact_type": "claim",
                "artifact_id": "claim_002",
                "priority": "high",
                "requires_human_review": True,
                "message": "Second high priority issue",
                "node_name": "review_claims",
            },
        ]

        interventions = repo.create_review_interventions_from_rework(
            run_id=run_id,
            rework_tasks=[],
            review_issues=review_issues,
        )

        # Assert returned interventions length is 2
        self.assertEqual(len(interventions), 2)

        # Assert DB has exactly 2 interventions
        db_interventions = repo.list_interventions(run_id)
        self.assertEqual(len(db_interventions), 2)

        # Assert intervention_ids are unique (no duplicates)
        intervention_ids = [i["intervention_id"] for i in interventions]
        self.assertEqual(len(intervention_ids), len(set(intervention_ids)))
        self.assertEqual(len(intervention_ids), 2)


class TestWorkflowRepositoryPauseNode(unittest.TestCase):
    """Test WorkflowRepository.pause_node method."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        self.repo = None

    def _get_repo(self):
        if self.repo is None:
            from backend.app.storage.repositories import WorkflowRepository
            self.repo = WorkflowRepository()
        return self.repo

    def test_pause_node_sets_status_to_paused(self):
        """Test pause_node sets node status to paused."""
        repo = self._get_repo()
        run_id = "test_pause_001"

        repo.init_workflow_graph(run_id)
        repo.pause_node(
            run_id,
            "build_task_brief",
            output_summary={"reason": "Manual pause"},
            reason="Requires human review",
        )

        node = repo.get_node_status(run_id, "build_task_brief")
        self.assertEqual(node["status"], "paused")
        self.assertIsNotNone(node["completed_at"])
        self.assertEqual(node["error_message"], "Requires human review")

    def test_pause_node_preserves_output_summary(self):
        """Test pause_node preserves output summary."""
        repo = self._get_repo()
        run_id = "test_pause_002"

        repo.init_workflow_graph(run_id)
        output_summary = {
            "interventions_created": 3,
            "failed_tasks": 2,
        }

        repo.pause_node(
            run_id,
            "prepare_human_intervention",
            output_summary=output_summary,
            reason="Human intervention needed",
        )

        node = repo.get_node_status(run_id, "prepare_human_intervention")
        self.assertEqual(node["status"], "paused")
        self.assertEqual(node["output_summary"], output_summary)


class TestPrepareHumanInterventionNode(unittest.TestCase):
    """Test prepare_human_intervention node function."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()

    def test_prepare_human_intervention_with_failed_tasks(self):
        """Test prepare_human_intervention creates interventions for failed rework tasks."""
        from backend.app.orchestrator.nodes import prepare_human_intervention
        from backend.app.storage.repositories import HumanInterventionRepository

        run_id = "test_phi_001"
        state = {
            "run_id": run_id,
            "task_id": "t1",
            "mode": "real_time",
            "rework_tasks": [
                {
                    "rework_id": "rework_failed_001",
                    "task_id": "rework_failed_001",
                    "status": "failed",
                    "error_message": "Network timeout",
                    "target_node": "extract_facts",
                },
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 0, "failed": 1},
            "rework_requests": [],
        }

        result = prepare_human_intervention(state)

        # Should require human review
        self.assertTrue(result["requires_human_review"])
        self.assertGreater(len(result["human_interventions"]), 0)

        # Verify intervention was created
        hi_repo = HumanInterventionRepository()
        interventions = hi_repo.list_interventions(run_id)
        self.assertEqual(len(interventions), 1)
        self.assertEqual(interventions[0]["artifact_type"], "rework")

    def test_prepare_human_intervention_without_issues(self):
        """Test prepare_human_intervention does not create interventions when no issues."""
        from backend.app.orchestrator.nodes import prepare_human_intervention

        run_id = "test_phi_002"
        state = {
            "run_id": run_id,
            "task_id": "t1",
            "mode": "real_time",
            "rework_tasks": [
                {
                    "rework_id": "rework_ok_001",
                    "status": "succeeded",
                },
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 1, "failed": 0},
            "rework_requests": [],
        }

        result = prepare_human_intervention(state)

        # Should not require human review
        self.assertFalse(result["requires_human_review"])
        self.assertEqual(len(result["human_interventions"]), 0)

    def test_prepare_human_intervention_with_high_priority_requests(self):
        """Test prepare_human_intervention handles high-priority rework requests."""
        from backend.app.orchestrator.nodes import prepare_human_intervention

        run_id = "test_phi_003"
        state = {
            "run_id": run_id,
            "task_id": "t1",
            "mode": "real_time",
            "rework_tasks": [],
            "rework_summary": {"total_tasks": 0, "succeeded": 0, "failed": 0},
            "rework_requests": [
                {
                    "rework_request_id": "req_001",
                    "priority": "high",
                    "requires_human_review": True,
                    "message": "Critical issue needs attention",
                },
            ],
        }

        result = prepare_human_intervention(state)

        # Should require human review due to high priority
        self.assertTrue(result["requires_human_review"])
        self.assertGreater(len(result["human_interventions"]), 0)


class TestWrapNodePausedIntegration(unittest.TestCase):
    """Test _wrap_node properly handles paused status for human intervention."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()

    def test_wrap_node_preserves_paused_status(self):
        """Test wrapped prepare_human_intervention results in paused node status."""
        from backend.app.orchestrator.graph import _wrap_node
        from backend.app.orchestrator.nodes import prepare_human_intervention
        from backend.app.storage.repositories import WorkflowRepository, HumanInterventionRepository

        repo = WorkflowRepository()
        run_id = "test_wrap_paused_001"

        # Init graph first
        repo.init_workflow_graph(run_id)

        # Call prepare_human_intervention wrapped
        wrapped = _wrap_node("prepare_human_intervention", prepare_human_intervention)
        state = {
            "run_id": run_id,
            "task_id": "t1",
            "mode": "real_time",
            "rework_tasks": [
                {
                    "rework_id": "rework_failed_001",
                    "task_id": "rework_failed_001",
                    "status": "failed",
                    "error_message": "Network timeout",
                    "target_node": "extract_facts",
                },
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 0, "failed": 1},
            "rework_requests": [],
        }

        # P2.1: _wrap_node now sets _workflow_paused_at instead of raising WorkflowPaused
        # (Python swallows raise in finally, so we use state flags instead)
        result = wrapped(state)

        # Verify _workflow_paused_at is set in state
        self.assertEqual(result.get("_workflow_paused_at"), "prepare_human_intervention")
        self.assertGreater(len(result.get("_workflow_paused_interventions", [])), 0)

        # Verify node is paused, not completed
        node = repo.get_node_status(run_id, "prepare_human_intervention")
        self.assertEqual(node["status"], "paused", "Node should be paused, not completed")

        # Verify requires_human_review is set
        self.assertTrue(result.get("requires_human_review"))

    def test_wrap_node_no_pause_without_issues(self):
        """Test wrapped prepare_human_intervention completes normally without issues."""
        from backend.app.orchestrator.graph import _wrap_node
        from backend.app.orchestrator.nodes import prepare_human_intervention
        from backend.app.storage.repositories import WorkflowRepository

        repo = WorkflowRepository()
        run_id = "test_wrap_no_pause_001"

        # Init graph first
        repo.init_workflow_graph(run_id)

        # Call prepare_human_intervention wrapped without failed tasks
        wrapped = _wrap_node("prepare_human_intervention", prepare_human_intervention)
        state = {
            "run_id": run_id,
            "task_id": "t1",
            "mode": "real_time",
            "rework_tasks": [
                {
                    "rework_id": "rework_ok_001",
                    "status": "succeeded",
                },
            ],
            "rework_summary": {"total_tasks": 1, "succeeded": 1, "failed": 0},
            "rework_requests": [],
        }

        result = wrapped(state)

        # Verify node is completed (not paused)
        node = repo.get_node_status(run_id, "prepare_human_intervention")
        self.assertEqual(node["status"], "completed")

        # Verify no human review needed
        self.assertFalse(result["requires_human_review"])


class TestGraphIntegration(unittest.TestCase):
    """Test graph.py integration for human interventions."""

    def test_summarize_state_includes_interventions(self):
        """Test _summarize_state includes human_interventions fields."""
        from backend.app.orchestrator.graph import _summarize_state

        state = {
            "run_id": "test_summarize",
            "mode": "real_time",
            "human_interventions": [{"id": "1"}, {"id": "2"}],
            "requires_human_review": True,
            "sources": [],
            "evidence_items": [],
            "facts": [],
            "claim_drafts": [],
            "signed_claims": [],
            "rework_requests": [],
            "errors": [],
        }

        summary = _summarize_state(state)

        self.assertIn("human_interventions", summary)
        self.assertIn("requires_human_review", summary)
        self.assertEqual(summary["human_interventions"], 2)
        self.assertTrue(summary["requires_human_review"])

    def test_agent_for_node_includes_prepare_human_intervention(self):
        """Test _agent_for_node includes prepare_human_intervention."""
        from backend.app.orchestrator.graph import _agent_for_node

        agent = _agent_for_node("prepare_human_intervention")
        self.assertEqual(agent, "HumanReviewAgent")

    def test_backbone_nodes_include_prepare_human_intervention(self):
        """Test BACKBONE_NODES includes prepare_human_intervention."""
        from backend.app.storage.repositories import WorkflowRepository

        self.assertIn("prepare_human_intervention", WorkflowRepository.BACKBONE_NODES)

    def test_backbone_edges_include_prepare_human_intervention(self):
        """Test BACKBONE_EDGES includes prepare_human_intervention."""
        from backend.app.storage.repositories import WorkflowRepository

        # Check the edge sequence
        edges = WorkflowRepository.BACKBONE_EDGES
        edge_tuples = [(e[0], e[1]) for e in edges]

        self.assertIn(("execute_rework", "prepare_human_intervention"), edge_tuples)
        self.assertIn(("prepare_human_intervention", "write_report_v2"), edge_tuples)  # vNext-R3-A


if __name__ == "__main__":
    unittest.main()
