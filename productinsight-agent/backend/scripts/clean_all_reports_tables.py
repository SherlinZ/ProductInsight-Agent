"""Apply _collapse_table_blank_lines to all v2 markdown reports on disk.

This is a one-shot cleanup so that *every* existing v2 report (not just
the one(s) we hand-patched earlier) has continuous tables. After this
runs, the reports can be downloaded as .md files, exported, or rendered
by any GFM-compliant viewer without the "header + N broken fragments"
symptom that the user reported.

Run once; safe to re-run (idempotent).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path("/home/shijialin/paperworking/workflow_new/productinsight-agent")
sys.path.insert(0, str(REPO))

from backend.app.services.deep_report import _collapse_table_blank_lines  # noqa: E402

REPORT_DIR = REPO / "data" / "reports"


def main() -> None:
    md_files = sorted(REPORT_DIR.glob("report_run_*_v2.md"))
    print(f"Found {len(md_files)} v2 markdown reports in {REPORT_DIR}")
    total_fixes = 0
    for md_path in md_files:
        original = md_path.read_text(encoding="utf-8")
        cleaned = _collapse_table_blank_lines(original)
        if cleaned != original:
            # Save backup only on first change.
            bak = md_path.with_suffix(".pre-tablefix.md")
            if not bak.exists():
                bak.write_text(original, encoding="utf-8")
            md_path.write_text(cleaned, encoding="utf-8")
            total_fixes += 1
            print(f"  ✓ fixed {md_path.name}  ({len(original)} → {len(cleaned)} chars)")
        else:
            print(f"  - unchanged {md_path.name}")
    print(f"\nDone: fixed {total_fixes} / {len(md_files)} reports.")


if __name__ == "__main__":
    main()
