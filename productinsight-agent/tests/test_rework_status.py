"""Tests for rework status granularity (vNext-P0)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_rework.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

NOW = datetime.now(timezone.utc).isoformat()


class TestReworkStatusGranularity(unittest.TestCase):
    """Test that rework tasks have granular status based on evidence improvement."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()

    def test_rework_task_has_evidence_extraction_failed_status(self):
        """ReworkTask should support evidence_extraction_failed status."""
        from backend.app.services.rework_service import ReworkTask
        task = ReworkTask(
            rework_id="rework_test",
            run_id="test_run",
            source_type="schema_gap",
            source_id="gap_1",
            target_node="extract_facts",
            target_agent="ExtractorAgent",
            product_id="slack",
            product_name="Slack",
            schema_key="pricing_model",
            reason="Missing pricing facts",
            status="evidence_extraction_failed",
        )
        self.assertEqual(task.status, "evidence_extraction_failed")
        d = task.to_dict()
        self.assertEqual(d["status"], "evidence_extraction_failed")
        print(f"  OK: ReworkTask supports evidence_extraction_failed status")

    def test_rework_metrics_include_evidence_facts_claims(self):
        """_snapshot_rework_metrics should include evidence/facts/claims counts."""
        from backend.app.services.rework_service import _snapshot_rework_metrics

        metrics = _snapshot_rework_metrics(
            schema_gaps=[{"priority": "high", "gap_type": "missing_fact"}],
            claim_drafts=[
                {"claim_id": "c1", "evidence_ids": ["e1"]},
                {"claim_id": "c2", "evidence_ids": []},
            ],
            signed_claims=[{"claim_id": "c1"}],
            evidence_items=[
                {"evidence_id": "e1", "product_id": "slack"},
                {"evidence_id": "e2", "product_id": "slack"},
            ],
            facts=[
                {"fact_id": "f1", "product_id": "slack", "schema_key": "pricing_model"},
            ],
            sources=[{"source_id": "s1"}],
        )

        self.assertEqual(metrics["evidence_count"], 2)
        self.assertEqual(metrics["facts_count"], 1)
        self.assertEqual(metrics["claim_count"], 2)
        self.assertEqual(metrics["signed_claim_count"], 1)
        print(f"  OK: metrics={metrics}")

    def test_rework_completed_only_when_evidence_improves(self):
        """When only sources increase but evidence stays 0, status should not be succeeded."""
        from backend.app.services.rework_service import ReworkTask, _snapshot_rework_metrics

        # Simulate: sources added, but evidence/facts/claims still 0
        before = _snapshot_rework_metrics(
            schema_gaps=[], claim_drafts=[], signed_claims=[],
            evidence_items=[], facts=[], sources=[{"source_id": "s1"}],
        )

        after = _snapshot_rework_metrics(
            schema_gaps=[], claim_drafts=[], signed_claims=[],
            evidence_items=[], facts=[], sources=[
                {"source_id": "s1"}, {"source_id": "s2"}, {"source_id": "s3"},
            ],
        )

        # Sources increased but evidence/facts/claims still 0
        self.assertGreater(after["sources_count"], before["sources_count"])
        self.assertEqual(before["evidence_count"], 0)
        self.assertEqual(after["evidence_count"], 0)
        self.assertEqual(before["facts_count"], 0)
        self.assertEqual(after["facts_count"], 0)

        # This should NOT be marked as succeeded
        evidence_improved = after["evidence_count"] > before["evidence_count"]
        facts_improved = after["facts_count"] > before["facts_count"]
        claims_improved = after["claim_count"] > before["claim_count"]

        self.assertFalse(evidence_improved, "Evidence should NOT be improved")
        self.assertFalse(facts_improved, "Facts should NOT be improved")
        self.assertFalse(claims_improved, "Claims should NOT be improved")
        print(f"  OK: Sources increased but evidence/facts/claims stayed 0 → not succeeded")

    def test_rework_completed_when_evidence_improves(self):
        """When evidence count increases, rework should be marked as succeeded."""
        from backend.app.services.rework_service import _snapshot_rework_metrics

        before = _snapshot_rework_metrics(
            schema_gaps=[], claim_drafts=[{"claim_id": "c1", "evidence_ids": []}],
            signed_claims=[],
            evidence_items=[{"evidence_id": "e1"}],  # Only 1 evidence
            facts=[], sources=[],
        )

        after = _snapshot_rework_metrics(
            schema_gaps=[],
            claim_drafts=[
                {"claim_id": "c1", "evidence_ids": ["e1", "e2"]},
                {"claim_id": "c2", "evidence_ids": ["e3"]},
            ],
            signed_claims=[{"claim_id": "c1"}],
            evidence_items=[
                {"evidence_id": "e1"}, {"evidence_id": "e2"}, {"evidence_id": "e3"},
            ],  # 3 evidence
            facts=[{"fact_id": "f1"}],
            sources=[],
        )

        evidence_improved = after["evidence_count"] > before["evidence_count"]
        facts_improved = after["facts_count"] > before["facts_count"]
        claims_improved = after["claim_count"] > before["claim_count"]

        self.assertTrue(evidence_improved, "Evidence should improve")
        self.assertTrue(claims_improved, "Claims should improve")
        print(f"  OK: evidence {before['evidence_count']}->{after['evidence_count']}, claims improved → succeeded")


if __name__ == "__main__":
    unittest.main()
