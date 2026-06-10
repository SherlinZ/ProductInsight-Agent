#!/usr/bin/env python3
"""
cleanup_runs.py — Prune stale/failed runs from the database.

Usage:
    python scripts/cleanup_runs.py [--dry-run] [--status STATUS] [--keep-count N]

Policies applied (in order):
  1. Any run with error_message "Cancelled by cleanup" that has NO report file
     on disk is marked as "cancelled".
  2. Any run that is "failed" with current_node in the early-stage set
     (build_task_brief, plan_schema, plan_sources) AND has no report file
     is marked as "cancelled".
  3. Completed runs are NEVER touched.
  4. Runs with a report file (v1 or v2) are NEVER touched.

For runs with status "failed" but rich data (partial reports, sources, evidence),
a --dry-run report is shown but no changes are made.

Add --status cancelled to actually update the DB; without it the script runs in
read-only mode and prints what it would do.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "productinsight.db"
REPORTS_DIR = PROJECT_ROOT / "data" / "reports"

EARLY_NODE_BLACKLIST = {
    "build_task_brief",
    "plan_schema",
    "plan_sources",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report_exists(run_id: str) -> bool:
    for suffix in ("_v2", ""):
        if (REPORTS_DIR / f"report_{run_id}{suffix}.md").exists():
            return True
    return False


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def scan_runs() -> list[sqlite3.Row]:
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT run_id, task_title, status, current_node, error_message,
                   created_at, completed_at, project_id
            FROM runs
            ORDER BY created_at DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return rows


def apply_policy(rows: list[sqlite3.Row]) -> tuple[list[dict], list[dict]]:
    """
    Returns (to_cancel, to_keep) where each is a list of run descriptors.
    """
    to_cancel: list[dict] = []
    to_keep: list[dict] = []

    for row in rows:
        run_id = row["run_id"]
        status = row["status"]
        current_node = row["current_node"] or ""
        error_msg = row["error_message"] or ""

        # Never touch completed runs
        if status == "completed":
            to_keep.append(dict(row))
            continue

        has_report = _report_exists(run_id)

        # Never touch runs that have a report on disk
        if has_report:
            to_keep.append(dict(row))
            continue

        # Cancelled-by-cleanup AND no report → cancel
        if error_msg == "Cancelled by cleanup":
            to_cancel.append({
                "run_id": run_id,
                "reason": "Cancelled by cleanup with no report",
                "current_node": current_node,
                "status": status,
            })
            continue

        # Failed early-stage AND no report → cancel
        if status == "failed" and current_node in EARLY_NODE_BLACKLIST:
            to_cancel.append({
                "run_id": run_id,
                "reason": f"Failed at early node '{current_node}' with no report",
                "current_node": current_node,
                "status": status,
            })
            continue

        # Failed late-stage (write_report, export, etc.) but no report → flag as warning
        # but don't auto-cancel; user may want to replay
        to_keep.append(dict(row))

    return to_cancel, to_keep


def mark_cancelled(run_ids: list[str]) -> int:
    """Update runs to cancelled status. Returns number of rows affected."""
    if not run_ids:
        return 0
    conn = _get_connection()
    try:
        placeholders = ",".join("?" * len(run_ids))
        now = _utc_now()
        cursor = conn.execute(
            f"""
            UPDATE runs
            SET status = 'cancelled',
                updated_at = ?,
                completed_at = COALESCE(completed_at, ?),
                error_message = COALESCE(error_message, 'Cancelled by cleanup policy')
            WHERE run_id IN ({placeholders})
            """,
            [now, now] + list(run_ids),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def print_report(to_cancel: list[dict], to_keep: list[dict]) -> None:
    print(f"\n{'='*60}")
    print(f"  Run Cleanup Report  ({_utc_now()})")
    print(f"{'='*60}")
    print(f"\nTotal runs scanned : {len(to_cancel) + len(to_keep)}")
    print(f"  → Would cancel  : {len(to_cancel)}")
    print(f"  → Keeping       : {len(to_keep)}")

    if to_cancel:
        print(f"\n{'─'*60}")
        print("  Runs to CANCEL:")
        print(f"{'─'*60}")
        for r in to_cancel:
            print(f"  {r['run_id']}")
            print(f"    reason : {r['reason']}")
            print(f"    status : {r['status']}  node: {r['current_node']}")
            print()

    if to_keep:
        by_status: dict[str, list] = {}
        for r in to_keep:
            by_status.setdefault(r["status"], []).append(r)

        print(f"{'─'*60}")
        print("  Runs being KEPT (sample):")
        print(f"{'─'*60}")
        for status, runs in sorted(by_status.items()):
            print(f"\n  status={status} ({len(runs)} runs):")
            for r in runs[:3]:
                print(f"    {r['run_id']}  node={r.get('current_node','')}")
            if len(runs) > 3:
                print(f"    ... and {len(runs) - 3} more")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up stale/failed runs in the productinsight database."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Read-only mode (default). Pass --status cancelled to actually write.",
    )
    parser.add_argument(
        "--status",
        choices=["cancelled"],
        help="Set status for runs flagged by the cleanup policy. "
             "Requires explicit value 'cancelled'. Implies --no-dry-run.",
    )
    parser.add_argument(
        "--keep-count",
        type=int,
        default=3,
        help="How many runs to show per status group in the keep list (default 3).",
    )
    args = parser.parse_args()

    dry_run = not bool(args.status)

    print("Scanning runs...")
    rows = scan_runs()
    to_cancel, to_keep = apply_policy(rows)
    print_report(to_cancel, to_keep)

    if dry_run:
        print(
            "\n[DRY RUN] No changes made. "
            "Run with --status cancelled to apply cleanup."
        )
        return

    if not to_cancel:
        print("\nNothing to clean up.")
        return

    confirm = input(f"\nCancel {len(to_cancel)} runs? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted.")
        sys.exit(0)

    run_ids = [r["run_id"] for r in to_cancel]
    n = mark_cancelled(run_ids)
    print(f"\nDone. {n} run(s) marked as 'cancelled'.")


if __name__ == "__main__":
    main()
