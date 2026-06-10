"""Tests for report_outline integration (vNext-P0).

Tests that report_outline from ResearchPlan flows into the workflow state,
through write_report, and into report_draft.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_outline.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

NOW = datetime.now(timezone.utc).isoformat()


class TestReportOutlineFlow(unittest.TestCase):
    """Test that report_outline flows from ResearchPlan to write_report output."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "test_outline_run",
            "task_id": "test_outline_task",
            "task_title": "Outline Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_write_report_receives_report_outline(self):
        """write_report node should read report_outline from state."""
        from backend.app.orchestrator.nodes import write_report
        from backend.app.orchestrator.state import WorkflowState

        report_outline = {
            "sections": [
                {"title": "Executive Summary", "section_id": "sec_exec", "min_words": 100},
                {"title": "Pricing Analysis", "section_id": "sec_pricing", "min_words": 200},
                {"title": "Conclusion", "section_id": "sec_conclusion", "min_words": 50},
            ],
        }

        state: WorkflowState = {
            "run_id": "test_outline_run",
            "project_id": "test_project",
            "mode": "real_time",
            "task_brief": {
                "title": "Pricing Analysis",
                "report_outline": report_outline,
                "schema_type": "pricing_analysis",
            },
            "report_outline": report_outline,  # Also at state level
            "signed_claims": [
                {
                    "claim_id": "c1",
                    "product_id": "slack",
                    "dimension": "pricing_model",
                    "claim_text": "Slack has a free tier",
                    "evidence_ids": ["e1"],
                    "confidence": 0.9,
                },
            ],
        }

        with mock.patch("backend.app.orchestrator.nodes._writer") as mock_writer_cls:
            mock_writer = mock.MagicMock()
            mock_writer.write.return_value = {
                "report_id": "report_test",
                "run_id": "test_outline_run",
                "sections": [
                    {"section_id": "sec_exec", "section_title": "Executive Summary", "content_markdown": "..."},
                ],
                "quality_summary": {"claim_count": 1, "evidence_coverage_rate": 1.0},
                "report_status": "draft",
            }
            mock_writer_cls.return_value = mock_writer

            write_report(state)

            # Verify WriterAgent.write was called with report_outline
            call_kwargs = mock_writer.write.call_args[1]
            self.assertEqual(call_kwargs["report_outline"], report_outline)
            self.assertEqual(call_kwargs["task_brief"]["report_outline"], report_outline)
            print(f"  OK: WriterAgent.write received report_outline with {len(report_outline['sections'])} sections")

    def test_report_outline_in_research_plan_flows_to_task_brief(self):
        """report_outline in nested research_plan should be readable from task_brief."""
        from backend.app.orchestrator.nodes import write_report
        from backend.app.orchestrator.state import WorkflowState

        research_plan = {
            "report_outline": {
                "sections": [
                    {"title": "Overview", "section_id": "sec_overview"},
                ],
            },
        }

        state: WorkflowState = {
            "run_id": "test_outline_run",
            "mode": "real_time",
            "task_brief": {
                "title": "Test",
                "research_plan": research_plan,
            },
            "signed_claims": [
                {
                    "claim_id": "c1",
                    "product_id": "test",
                    "dimension": "function_tree",
                    "claim_text": "Test claim",
                    "evidence_ids": [],
                },
            ],
        }

        with mock.patch("backend.app.orchestrator.nodes._writer") as mock_writer_cls:
            mock_writer = mock.MagicMock()
            mock_writer.write.return_value = {
                "report_id": "report_test2",
                "run_id": "test_outline_run",
                "sections": [],
                "quality_summary": {},
                "report_status": "draft",
            }
            mock_writer_cls.return_value = mock_writer

            write_report(state)

            call_kwargs = mock_writer.write.call_args[1]
            self.assertIsNotNone(call_kwargs["report_outline"])
            self.assertEqual(
                call_kwargs["report_outline"]["sections"][0]["title"],
                "Overview",
            )
            print(f"  OK: report_outline resolved from nested research_plan")


class TestReportOutlineInWriterAgent(unittest.TestCase):
    """Test WriterAgent properly uses report_outline."""

    def test_assemble_final_report_preserves_outline(self):
        """_assemble_final_report should include report_outline in output."""
        from backend.app.agents.writer.writer import _assemble_final_report

        report_outline = {
            "sections": [
                {"title": "Executive Summary", "section_id": "sec_exec"},
                {"title": "Feature Comparison", "section_id": "sec_features"},
            ],
        }

        signed_claims = [
            {"claim_id": "c1", "product_id": "test", "dimension": "function_tree",
             "claim_text": "Test claim", "evidence_ids": ["e1"], "confidence": 0.9},
        ]

        report = _assemble_final_report(
            raw_sections=[
                {"section_title": "Executive Summary", "section_id": "sec_exec",
                 "content_markdown": "Overview...", "claim_ids": ["c1"], "evidence_ids": ["e1"]},
            ],
            signed_claims=signed_claims,
            run_id="test_run",
            report_outline=report_outline,
        )

        self.assertEqual(report.get("report_status"), "draft")
        self.assertGreater(len(report.get("sections", [])), 0)
        print(f"  OK: _assemble_final_report produced report with {len(report['sections'])} sections")


if __name__ == "__main__":
    unittest.main()
