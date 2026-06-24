"""Regression tests for _collapse_table_blank_lines and the /report/html endpoint.

Why these tests exist:
- Multiple section generators (e.g. _generate_selection_scorecard in
  deep_report.py) historically inserted blank lines between table rows,
  producing markdown like::

      | team_type | recommended | reason | verify |
                                              ← BLANK
      |---|---|---|---|---|
      | row1      | ...         | ...    | ...    |
                                              ← BLANK
      | row2      | ...         | ...    | ...    |

  GFM parsers (Chromium iframe, GitHub, pandoc) terminate the table on
  the first blank line, which causes the header row to leak through as
  raw pipe-separated text and the data rows to be rendered as separate
  one-row fragments.

- /report/html historically returned `text/markdown; charset=utf-8`,
  which browsers display verbatim. The endpoint now applies
  _collapse_table_blank_lines and renders via _markdown_to_html before
  returning `text/html`.

These tests cover both the function and the endpoint behavior.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Make `backend.app.*` importable when running from any cwd.
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.services.deep_report import (  # noqa: E402
    _collapse_table_blank_lines,
)


class CollapseTableBlankLinesTests(unittest.TestCase):
    def test_collapses_blanks_inside_table(self):
        """Header / separator / rows with blanks between → all rows contiguous."""
        md = (
            "| team_type | recommended | reason | verify |\n"
            "\n"
            "|---|---|---|---|\n"
            "| **non_tech** | ⚠️ FastGPT | x | y |\n"
            "\n"
            "| **technical** | ✅ Dify | a | b |\n"
            "\n"
            "| **enterprise** | 🔄 Coze | c | d |\n"
        )
        out = _collapse_table_blank_lines(md)
        # Split by '|' boundaries and check each row is contiguous.
        lines = out.split("\n")
        # Find table lines (start with '|') — header + separator + 3 data rows = 5.
        table_line_indices = [i for i, l in enumerate(lines) if l.lstrip().startswith("|")]
        self.assertEqual(len(table_line_indices), 5)
        # They should form a single contiguous run.
        self.assertEqual(
            table_line_indices,
            list(range(table_line_indices[0], table_line_indices[0] + 5)),
            f"Table lines not contiguous: indices={table_line_indices}",
        )

    def test_idempotent(self):
        """Running the cleanup twice should yield the same output as running once."""
        md = (
            "| h1 | h2 |\n"
            "\n"
            "|---|---|\n"
            "| a  | b  |\n"
            "\n"
            "| c  | d  |\n"
        )
        once = _collapse_table_blank_lines(md)
        twice = _collapse_table_blank_lines(once)
        self.assertEqual(once, twice)

    def test_preserves_blanks_outside_table(self):
        """Blank lines between paragraphs (non-table) must NOT be removed."""
        md = (
            "Some intro paragraph.\n"
            "\n"
            "Another paragraph.\n"
            "\n"
            "| h1 | h2 |\n"
            "\n"
            "|---|---|\n"
            "| a  | b  |\n"
        )
        out = _collapse_table_blank_lines(md)
        # Both intro blanks must still be present.
        self.assertIn("Some intro paragraph.\n\nAnother paragraph.\n\n", out)

    def test_no_table_returns_unchanged(self):
        """Pure prose input should be returned verbatim."""
        md = "Hello world.\n\nNo tables here.\n"
        self.assertEqual(_collapse_table_blank_lines(md), md)

    def test_renders_to_one_table_in_html(self):
        """After cleanup, _markdown_to_html should produce exactly ONE <table>
        element, not three separate broken ones. This is the symptom the user
        reported: header + 5 data rows rendered as 5+ raw-text fragments."""
        from backend.app.services.deep_report import _markdown_to_html

        md = (
            "| 团队类型 | 推荐产品 | 核心原因 | 采购前必验证 |\n"
            "\n"
            "|---|---|---|---|\n"
            "| **非技术业务团队** | ⚠️ FastGPT、Dify, Flowise | r1 | v1 |\n"
            "\n"
            "| **技术研发团队** | ✅ Dify、Flowise, FastGPT | r2 | v2 |\n"
            "\n"
            "| **金融 / 政务企业** | 🔄 FastGPT、Dify, Flowise | r3 | v3 |\n"
            "\n"
            "| **知识库问答场景** | ✅ Dify、Flowise, FastGPT | r4 | v4 |\n"
            "\n"
            "| **初创 / 小团队** | ⚠️ FastGPT、Dify, Flowise | r5 | v5 |\n"
        )
        out = _collapse_table_blank_lines(md)
        html = _markdown_to_html(out)
        # Exactly one <table> element should appear.
        table_count = html.count("<table>")
        self.assertEqual(
            table_count, 1,
            f"Expected 1 <table>, got {table_count}. HTML was:\n{html[:2000]}",
        )
        # It should contain the header AND all 5 data rows.
        for needle in (
            "团队类型", "推荐产品", "核心原因", "采购前必验证",
            "非技术业务团队", "技术研发团队", "金融", "知识库问答场景", "初创",
        ):
            self.assertIn(needle, html, f"Missing '{needle}' in rendered HTML")

    def test_does_not_break_table_in_larger_document(self):
        """Verify the function only touches intra-table blanks in a full report."""
        md = (
            "## Section heading\n"
            "\n"
            "Intro paragraph.\n"
            "\n"
            "| col A | col B |\n"
            "\n"
            "|---|---|\n"
            "| 1 | 2 |\n"
            "\n"
            "| 3 | 4 |\n"
            "\n"
            "After-table paragraph.\n"
            "\n"
            "### Another heading\n"
            "\n"
            "More text.\n"
        )
        out = _collapse_table_blank_lines(md)
        # The post-table paragraph and following section should remain intact.
        self.assertIn("After-table paragraph.", out)
        self.assertIn("### Another heading", out)
        # Table should be contiguous now (4 lines: header + sep + 2 rows).
        lines = out.split("\n")
        table_line_indices = [
            i for i, l in enumerate(lines) if l.lstrip().startswith("|")
        ]
        self.assertEqual(len(table_line_indices), 4)
        self.assertEqual(
            table_line_indices,
            list(range(table_line_indices[0], table_line_indices[0] + 4)),
        )


class ReportHtmlEndpointTests(unittest.TestCase):
    """Smoke-test the FastAPI endpoint by invoking the route function directly."""

    def test_endpoint_returns_html_with_table_for_real_report(self):
        """For an existing v2 report, the endpoint must return text/html
        containing a single <table> for the broken '选型建议速查' section."""
        try:
            from backend.app.api.reports import get_report_html
        except ImportError as e:
            self.skipTest(f"Cannot import reports router (missing deps?): {e}")

        # Pick any v2 report that actually exists on disk.
        reports_dir = _PROJECT_ROOT / "data" / "reports"
        candidates = sorted(reports_dir.glob("report_run_*_v2.md"))
        if not candidates:
            self.skipTest(f"No v2 reports found under {reports_dir}")
        md_path = candidates[0]
        run_id = re.search(r"report_(run_[a-f0-9]+)_v2\.md", md_path.name).group(1)

        response = get_report_html(run_id)
        # Response should be an HTMLResponse with the right media type.
        media_type = getattr(response, "media_type", "") or getattr(
            response, "headers", {}
        ).get("content-type", "")
        self.assertIn("html", media_type.lower(),
                      f"Expected HTML media type, got: {media_type!r}")
        body = response.body.decode("utf-8") if isinstance(response.body, bytes) else response.body
        # Must wrap the content in <html>...</html> (we wrap markdown with a doc shell).
        self.assertIn("<html", body)
        # The selection scorecard should render as exactly one <table>
        # (was previously 5+ broken fragments).
        table_count = body.count("<table>")
        self.assertGreaterEqual(
            table_count, 1,
            f"Expected at least 1 <table>, got {table_count}",
        )


if __name__ == "__main__":
    unittest.main()
