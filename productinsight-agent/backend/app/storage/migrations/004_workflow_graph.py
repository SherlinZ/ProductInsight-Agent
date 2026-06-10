"""
Workflow graph tables for tracking node execution status and DAG edges.
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
        # --- workflow_nodes table ---
        if not _table_exists(conn, "workflow_nodes"):
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
            conn.execute("CREATE INDEX idx_workflow_nodes_run_id ON workflow_nodes(run_id);")
            conn.execute("CREATE INDEX idx_workflow_nodes_status ON workflow_nodes(status);")
            print("[migration] Created table: workflow_nodes")

        # --- workflow_edges table ---
        if not _table_exists(conn, "workflow_edges"):
            conn.execute("""
                CREATE TABLE workflow_edges (
                    edge_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    from_node TEXT NOT NULL,
                    to_node TEXT NOT NULL,
                    edge_type TEXT NOT NULL DEFAULT 'sequence'
                        CHECK (edge_type IN ('sequence', 'conditional', 'rework', 'manual_gate', 'invalidate')),
                    condition_json TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX idx_workflow_edges_run_id ON workflow_edges(run_id);")
            print("[migration] Created table: workflow_edges")

        conn.commit()
        print(f"[migration] Completed successfully on {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    migrate(db_path)
