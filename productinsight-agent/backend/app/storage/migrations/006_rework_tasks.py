"""
Rework tasks table for human-initiated rework requests from Review Center.
Stores rework tasks created when a user clicks "Request Rework" on a pending intervention.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return result is not None


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print(f"[migration] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        if not _table_exists(conn, "rework_tasks"):
            conn.execute("""
                CREATE TABLE rework_tasks (
                    rework_id TEXT PRIMARY KEY,
                    intervention_id TEXT,
                    run_id TEXT NOT NULL,
                    project_id TEXT,
                    source_node TEXT,
                    target_artifact_type TEXT,
                    target_artifact_id TEXT,
                    reason_codes_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'planned', 'running', 'completed', 'failed', 'cancelled')),
                    rework_plan_json TEXT,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    created_by TEXT DEFAULT 'frontend_user',
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)
            conn.execute("CREATE INDEX idx_rework_tasks_run_id ON rework_tasks(run_id);")
            conn.execute("CREATE INDEX idx_rework_tasks_status ON rework_tasks(status);")
            conn.execute("CREATE INDEX idx_rework_tasks_intervention_id ON rework_tasks(intervention_id);")
            print("[migration] Created table: rework_tasks")

    finally:
        conn.close()


