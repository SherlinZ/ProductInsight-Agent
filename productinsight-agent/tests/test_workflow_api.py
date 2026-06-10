"""
Tests for workflow and human intervention API endpoints.
"""
from __future__ import annotations

import os
import tempfile
import unittest

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_workflow_api.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"


class TestWorkflowNodesAPI(unittest.TestCase):
    """Test GET /api/runs/{run_id}/workflow/nodes endpoint."""

    def setUp(self):
        from backend.app.storage.db import init_db
        from backend.app.storage.repositories import WorkflowRepository
        init_db()
        self.repo = WorkflowRepository()
        self.run_id = "test_run_nodes_api"

    def test_get_nodes_returns_backbone_in_order(self):
        """Test that nodes are returned in BACKBONE_NODES order."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow_nodes
        nodes = get_workflow_nodes(self.run_id)

        self.assertEqual(len(nodes), 16)

        # First node should be build_task_brief
        self.assertEqual(nodes[0]["node_name"], "build_task_brief")

        # Should include prepare_human_intervention
        node_names = [n["node_name"] for n in nodes]
        self.assertIn("prepare_human_intervention", node_names)

        # Last node should be compute_metrics
        self.assertEqual(nodes[-1]["node_name"], "compute_metrics")

    def test_get_nodes_empty_for_unknown_run(self):
        """Test that empty list is returned for unknown run_id."""
        from backend.app.api.workflow import get_workflow_nodes
        nodes = get_workflow_nodes("nonexistent_run_12345")
        self.assertEqual(nodes, [])

    def test_get_nodes_includes_all_required_fields(self):
        """Test that node objects include all required fields."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow_nodes
        nodes = get_workflow_nodes(self.run_id)

        required_fields = [
            "node_id", "run_id", "node_name", "node_type", "status",
            "input_summary", "output_summary", "started_at", "completed_at",
            "latency_ms", "error_message", "created_at", "updated_at"
        ]
        for node in nodes:
            for field in required_fields:
                self.assertIn(field, node)


class TestWorkflowEdgesAPI(unittest.TestCase):
    """Test GET /api/runs/{run_id}/workflow/edges endpoint."""

    def setUp(self):
        from backend.app.storage.db import init_db
        from backend.app.storage.repositories import WorkflowRepository
        init_db()
        self.repo = WorkflowRepository()
        self.run_id = "test_run_edges_api"

    def test_get_edges_returns_backbone_edges(self):
        """Test that edges are returned for backbone workflow."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow_edges
        edges = get_workflow_edges(self.run_id)

        self.assertEqual(len(edges), 16)

        # Should contain execute_rework -> prepare_human_intervention
        edge_tuples = [(e["from_node"], e["to_node"]) for e in edges]
        self.assertIn(("execute_rework", "prepare_human_intervention"), edge_tuples)

        # Should contain prepare_human_intervention -> write_report_v2_v2
        self.assertIn(("prepare_human_intervention", "write_report_v2"), edge_tuples)

    def test_get_edges_empty_for_unknown_run(self):
        """Test that empty list is returned for unknown run_id."""
        from backend.app.api.workflow import get_workflow_edges
        edges = get_workflow_edges("nonexistent_run_12345")
        self.assertEqual(edges, [])

    def test_get_edges_includes_all_required_fields(self):
        """Test that edge objects include all required fields."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow_edges
        edges = get_workflow_edges(self.run_id)

        required_fields = ["edge_id", "run_id", "from_node", "to_node", "edge_type", "condition", "created_at"]
        for edge in edges:
            for field in required_fields:
                self.assertIn(field, edge)


class TestWorkflowCombinedAPI(unittest.TestCase):
    """Test GET /api/runs/{run_id}/workflow endpoint."""

    def setUp(self):
        from backend.app.storage.db import init_db
        from backend.app.storage.repositories import WorkflowRepository
        init_db()
        self.repo = WorkflowRepository()
        self.run_id = "test_run_combined_api"

    def test_get_workflow_returns_nodes_edges_summary(self):
        """Test that combined endpoint returns nodes, edges, and summary."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow
        result = get_workflow(self.run_id)

        self.assertIn("run_id", result)
        self.assertIn("nodes", result)
        self.assertIn("edges", result)
        self.assertIn("summary", result)

        self.assertEqual(result["run_id"], self.run_id)
        self.assertEqual(len(result["nodes"]), 16)
        self.assertEqual(len(result["edges"]), 16)

    def test_get_workflow_summary_counts_correct(self):
        """Test that summary counts match actual node counts."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_workflow
        result = get_workflow(self.run_id)

        summary = result["summary"]
        self.assertEqual(summary["total_nodes"], 16)
        self.assertEqual(summary["pending"], 16)
        self.assertEqual(summary["completed"], 0)
        self.assertEqual(summary["running"], 0)
        self.assertEqual(summary["paused"], 0)
        self.assertEqual(summary["failed"], 0)
        self.assertFalse(summary["has_human_review"])

    def test_get_workflow_summary_with_paused_node(self):
        """Test that summary reflects paused node correctly."""
        self.repo.init_workflow_graph(self.run_id)
        self.repo.pause_node(self.run_id, "build_task_brief", reason="Test pause")

        from backend.app.api.workflow import get_workflow
        result = get_workflow(self.run_id)

        summary = result["summary"]
        self.assertEqual(summary["paused"], 1)
        self.assertEqual(summary["pending"], 15)
        self.assertTrue(summary["has_human_review"])

    def test_get_workflow_summary_with_pending_intervention(self):
        """Test that summary reflects pending intervention correctly."""
        self.repo.init_workflow_graph(self.run_id)

        from backend.app.api.workflow import get_run_interventions
        from backend.app.storage.repositories import HumanInterventionRepository

        hi_repo = HumanInterventionRepository()
        hi_repo.create_intervention({
            "intervention_id": "interv_api_test",
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "artifact_id": "artifact_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        from backend.app.api.workflow import get_workflow
        result = get_workflow(self.run_id)

        summary = result["summary"]
        self.assertTrue(summary["has_human_review"])


class TestHumanInterventionsAPI(unittest.TestCase):
    """Test human intervention API endpoints."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        import uuid
        self.run_id = "test_run_hi_api"
        self.run_id = f"test_run_hi_api_{uuid.uuid4().hex[:8]}"
        self.repo = None

    def _get_repo(self):
        if self.repo is None:
            from backend.app.storage.repositories import HumanInterventionRepository
            self.repo = HumanInterventionRepository()
        return self.repo

    def test_get_interventions_empty_for_new_run(self):
        """Test that empty list is returned for run with no interventions."""
        from backend.app.api.workflow import get_run_interventions
        interventions = get_run_interventions(self.run_id)
        self.assertEqual(interventions, [])

    def test_get_interventions_returns_created_interventions(self):
        """Test that created interventions are returned."""
        import uuid
        repo = self._get_repo()
        interv_id = f"interv_get_test_{uuid.uuid4().hex[:8]}"
        repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        from backend.app.api.workflow import get_run_interventions
        interventions = get_run_interventions(self.run_id)

        self.assertEqual(len(interventions), 1)
        self.assertEqual(interventions[0]["intervention_id"], interv_id)

    def test_get_interventions_filter_by_status(self):
        """Test that status filter works correctly."""
        import uuid
        repo = self._get_repo()

        # Create pending intervention
        pending_id = f"interv_pending_{uuid.uuid4().hex[:8]}"
        repo.create_intervention({
            "intervention_id": pending_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "artifact_id": "artifact_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        # Create resolved intervention
        resolved_id = f"interv_resolved_{uuid.uuid4().hex[:8]}"
        repo.create_intervention({
            "intervention_id": resolved_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "artifact_id": "artifact_002",
            "action": "approve",
            "status": "resolved",
            "created_at": "2026-01-01T00:00:00Z",
            "resolved_at": "2026-01-01T00:01:00Z",
            "resolved_by": "test_user",
        })

        from backend.app.api.workflow import get_run_interventions

        all_interventions = get_run_interventions(self.run_id)
        self.assertEqual(len(all_interventions), 2)

        pending_only = get_run_interventions(self.run_id, status="pending")
        self.assertEqual(len(pending_only), 1)
        self.assertEqual(pending_only[0]["intervention_id"], pending_id)

        resolved_only = get_run_interventions(self.run_id, status="resolved")
        self.assertEqual(len(resolved_only), 1)
        self.assertEqual(resolved_only[0]["intervention_id"], resolved_id)

    def test_get_single_intervention_success(self):
        """Test GET /api/human-interventions/{id} returns intervention."""
        import uuid
        repo = self._get_repo()
        interv_id = f"interv_single_test_{uuid.uuid4().hex[:8]}"
        repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

        from backend.app.api.workflow import get_intervention
        intervention = get_intervention(interv_id)

        self.assertEqual(intervention["intervention_id"], interv_id)

    def test_get_single_intervention_not_found(self):
        """Test GET /api/human-interventions/{id} returns 404 for missing."""
        from fastapi import HTTPException
        from backend.app.api.workflow import get_intervention

        with self.assertRaises(HTTPException) as ctx:
            get_intervention("nonexistent_intervention_id")

        self.assertEqual(ctx.exception.status_code, 404)


class TestApproveInterventionAPI(unittest.TestCase):
    """Test POST /api/human-interventions/{id}/approve endpoint."""

    def setUp(self):
        import uuid
        from backend.app.storage.db import init_db
        init_db()
        self.run_id = f"test_run_approve_{uuid.uuid4().hex[:8]}"
        from backend.app.storage.repositories import HumanInterventionRepository
        self.hi_repo = HumanInterventionRepository()
        interv_id = f"interv_approve_test_{uuid.uuid4().hex[:8]}"
        self.hi_repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })
        self.interv_id = interv_id

    def test_approve_sets_status_resolved(self):
        """Test that approve sets status to resolved and action to approve."""
        from backend.app.api.workflow import approve_intervention, ApproveRequest

        request = ApproveRequest(comment="Approved by user", resolved_by="test_user")
        result = approve_intervention(self.interv_id, request)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["comment"], "Approved by user")
        self.assertEqual(result["resolved_by"], "test_user")
        self.assertIsNotNone(result["resolved_at"])

    def test_approve_nonexistent_returns_404(self):
        """Test that approving nonexistent intervention returns 404."""
        from fastapi import HTTPException
        from backend.app.api.workflow import approve_intervention, ApproveRequest

        request = ApproveRequest()
        with self.assertRaises(HTTPException) as ctx:
            approve_intervention("nonexistent_id", request)

        self.assertEqual(ctx.exception.status_code, 404)


class TestRejectInterventionAPI(unittest.TestCase):
    """Test POST /api/human-interventions/{id}/reject endpoint."""

    def setUp(self):
        import uuid
        from backend.app.storage.db import init_db
        init_db()
        self.run_id = f"test_run_reject_{uuid.uuid4().hex[:8]}"
        from backend.app.storage.repositories import HumanInterventionRepository
        self.hi_repo = HumanInterventionRepository()
        interv_id = f"interv_reject_test_{uuid.uuid4().hex[:8]}"
        self.hi_repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_002",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })
        self.interv_id = interv_id

    def test_reject_sets_action_reject(self):
        """Test that reject sets action to reject."""
        from backend.app.api.workflow import reject_intervention, RejectRequest

        request = RejectRequest(comment="Rejected due to insufficient evidence", resolved_by="test_user")
        result = reject_intervention(self.interv_id, request)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "reject")
        self.assertEqual(result["comment"], "Rejected due to insufficient evidence")


class TestEditInterventionAPI(unittest.TestCase):
    """Test POST /api/human-interventions/{id}/edit endpoint."""

    def setUp(self):
        import uuid
        from backend.app.storage.db import init_db
        init_db()
        self.run_id = f"test_run_edit_{uuid.uuid4().hex[:8]}"
        from backend.app.storage.repositories import HumanInterventionRepository
        self.hi_repo = HumanInterventionRepository()
        interv_id = f"interv_edit_test_{uuid.uuid4().hex[:8]}"
        self.hi_repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_003",
            "action": "pending",
            "status": "pending",
            "before_json": {"original_value": "old"},
            "created_at": "2026-01-01T00:00:00Z",
        })
        self.interv_id = interv_id

    def test_edit_saves_after_json(self):
        """Test that edit saves after_json and returns it."""
        from backend.app.api.workflow import edit_intervention, EditRequest

        after_data = {"edited_value": "new", "changes": ["Updated claim"]}
        request = EditRequest(
            after_json=after_data,
            comment="Edited claim value",
            resolved_by="test_user"
        )
        result = edit_intervention(self.interv_id, request)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "edit")
        self.assertEqual(result["after_json"], after_data)
        self.assertEqual(result["comment"], "Edited claim value")


class TestRespondInterventionAPI(unittest.TestCase):
    """Test POST /api/human-interventions/{id}/respond endpoint."""

    def setUp(self):
        import uuid
        from backend.app.storage.db import init_db
        init_db()
        self.run_id = f"test_run_respond_{uuid.uuid4().hex[:8]}"
        from backend.app.storage.repositories import HumanInterventionRepository
        self.hi_repo = HumanInterventionRepository()
        interv_id = f"interv_respond_test_{uuid.uuid4().hex[:8]}"
        self.hi_repo.create_intervention({
            "intervention_id": interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "general",
            "artifact_id": "artifact_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })
        self.interv_id = interv_id

    def test_respond_sets_action_respond(self):
        """Test that respond sets action to respond."""
        from backend.app.api.workflow import respond_intervention, RespondRequest

        response_data = {"user_input": "This is my response", "attachments": []}
        request = RespondRequest(
            after_json=response_data,
            comment="User response",
            resolved_by="test_user"
        )
        result = respond_intervention(self.interv_id, request)

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["action"], "respond")
        self.assertEqual(result["after_json"], response_data)


class TestWorkflowAPIRoutesWithClient(unittest.TestCase):
    """Smoke tests using FastAPI TestClient to verify real HTTP routing."""

    def setUp(self):
        import uuid
        from backend.app.storage.db import init_db
        from backend.app.storage.repositories import WorkflowRepository, HumanInterventionRepository
        init_db()
        self.run_id = f"test_client_run_{uuid.uuid4().hex[:8]}"
        self.repo = WorkflowRepository()
        self.hi_repo = HumanInterventionRepository()
        # Initialize workflow graph
        self.repo.init_workflow_graph(self.run_id)
        # Create a pending intervention
        self.interv_id = f"interv_client_{uuid.uuid4().hex[:8]}"
        self.hi_repo.create_intervention({
            "intervention_id": self.interv_id,
            "run_id": self.run_id,
            "node_name": "test_node",
            "artifact_type": "claim",
            "artifact_id": "claim_001",
            "action": "pending",
            "status": "pending",
            "created_at": "2026-01-01T00:00:00Z",
        })

    def test_get_workflow_nodes_via_client(self):
        """Test GET /api/runs/{run_id}/workflow/nodes returns 200."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)

        response = client.get(f"/api/runs/{self.run_id}/workflow/nodes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 16)

    def test_get_workflow_edges_via_client(self):
        """Test GET /api/runs/{run_id}/workflow/edges returns 200."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)

        response = client.get(f"/api/runs/{self.run_id}/workflow/edges")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 16)

    def test_get_workflow_combined_via_client(self):
        """Test GET /api/runs/{run_id}/workflow returns 200 with nodes/edges/summary."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)

        response = client.get(f"/api/runs/{self.run_id}/workflow")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("run_id", data)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertIn("summary", data)
        self.assertEqual(len(data["nodes"]), 16)
        self.assertEqual(len(data["edges"]), 16)

    def test_get_human_interventions_with_status_filter_via_client(self):
        """Test GET /api/runs/{run_id}/human-interventions?status=pending returns 200."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)

        response = client.get(f"/api/runs/{self.run_id}/human-interventions?status=pending")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["intervention_id"], self.interv_id)

    def test_get_intervention_not_found_via_client(self):
        """Test GET /api/human-interventions/nonexistent returns 404."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        client = TestClient(app)

        response = client.get("/api/human-interventions/nonexistent_id_12345")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
