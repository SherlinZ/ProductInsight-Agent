"""Migration 017: Add workflow_state_json column to runs table.

Purpose: Enable workflow pause/resume for human intervention gates.
When the workflow pauses (e.g., at prepare_human_intervention), we serialize the current
WorkflowState into this column so it can be resumed later. The column is also used
to persist workflow state for replay/retry scenarios.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print(f"[migration 017] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        # Check if runs table exists
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchone()
        if not table_exists:
            print("[migration 017] runs table not found — skipping")
            return

        # Check if workflow_state_json column already exists
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "workflow_state_json" in cols:
            print("[migration 017] workflow_state_json column already exists — skipping")
            return

        # Add the column
        conn.execute("ALTER TABLE runs ADD COLUMN workflow_state_json TEXT;")
        conn.commit()
        print("[migration 017] Added workflow_state_json column to runs table")

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
