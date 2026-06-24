"""Migration 016: Backfill input_summary_json for legacy workflow_nodes rows.

Problem: the /replay code path (_run_pending_nodes_sync in runs.py) was calling
WorkflowRepository().start_node(run_id, node_name, {}) with an empty dict instead
of the real _summarize_state(state). This left input_summary_json = NULL for all
nodes replayed via the /replay endpoint.

Additionally, the initial run code path (_wrap_node in graph.py) did call
start_node with the real input_summary, but only for nodes that actually ran
through _wrap_node. Nodes that existed in the DB but were never started (e.g.
the first node of a run that failed before the first _wrap_node call) also have
NULL input_summary_json.

Fix: for rows where input_summary_json IS NULL but output_summary_json IS NOT NULL,
copy the output_summary_json into input_summary_json. This is correct because:
  - For the first node (build_task_brief): input == output == initial state
  - For all subsequent nodes: output of node N == input of node N+1
  - Rows where both are NULL are either pending (never ran) or failed before
    completing, and should remain NULL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print(f"[migration 016] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        # Verify workflow_nodes table exists
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_nodes'"
        ).fetchone()
        if not table_exists:
            print("[migration 016] workflow_nodes table not found — skipping")
            return

        # Verify the columns we need exist
        cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_nodes)")}
        if "input_summary_json" not in cols or "output_summary_json" not in cols:
            print("[migration 016] input_summary_json or output_summary_json column missing — skipping")
            return

        # Find rows needing backfill: input_summary_json IS NULL but output_summary_json IS NOT NULL
        cursor = conn.execute(
            """
            SELECT node_id, node_name, run_id
            FROM workflow_nodes
            WHERE input_summary_json IS NULL
              AND output_summary_json IS NOT NULL
            """
        )
        rows = cursor.fetchall()

        if not rows:
            print("[migration 016] No rows need backfill — input_summary_json is already populated")
            return

        print(f"[migration 016] Found {len(rows)} rows with NULL input_summary_json to backfill:")
        for node_id, node_name, run_id in rows:
            print(f"  - {node_id} ({node_name}) in run {run_id}")

        # Backfill: copy output_summary_json → input_summary_json for each row
        conn.execute(
            """
            UPDATE workflow_nodes
            SET input_summary_json = output_summary_json,
                updated_at = datetime('now')
            WHERE input_summary_json IS NULL
              AND output_summary_json IS NOT NULL
            """
        )

        # Verify
        remaining = conn.execute(
            """
            SELECT COUNT(*) FROM workflow_nodes
            WHERE input_summary_json IS NULL
              AND output_summary_json IS NOT NULL
            """
        ).fetchone()[0]

        conn.commit()
        print(f"[migration 016] Backfill complete. Remaining NULL input_summary_json with valid output: {remaining}")

    finally:
        conn.close()


if __name__ == "__main__":
    import os, sys
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    migrate(db_path)
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <db_path>")
        print(f"Detected DB path: {db_path}")
