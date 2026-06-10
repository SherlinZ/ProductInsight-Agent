"""
Human interventions table for manual review, approval, and editing.
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
        # --- human_interventions table ---
        if not _table_exists(conn, "human_interventions"):
            conn.execute("""
                CREATE TABLE human_interventions (
                    intervention_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    node_name TEXT,
                    artifact_type TEXT
                        CHECK (artifact_type IN ('source', 'evidence', 'fact', 'claim', 'report', 'rework', 'workflow', 'general')),
                    artifact_id TEXT,
                    action TEXT NOT NULL DEFAULT 'pending'
                        CHECK (action IN ('approve', 'reject', 'edit', 'respond', 'pending')),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'resolved', 'cancelled')),
                    before_json TEXT,
                    after_json TEXT,
                    comment TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    created_by TEXT,
                    resolved_by TEXT
                )
            """)
            conn.execute("CREATE INDEX idx_human_interventions_run_id ON human_interventions(run_id);")
            conn.execute("CREATE INDEX idx_human_interventions_status ON human_interventions(status);")
            conn.execute("CREATE INDEX idx_human_interventions_artifact ON human_interventions(artifact_type, artifact_id);")
            print("[migration] Created table: human_interventions")

        # --- Add paused status to workflow_nodes ---
        # SQLite doesn't support ALTER TABLE to modify CHECK constraints easily,
        # so we recreate the table with the new CHECK if needed
        if _table_exists(conn, "workflow_nodes"):
            # Check if current CHECK allows 'paused'
            rows = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_nodes'"
            ).fetchall()
            if rows:
                current_sql = rows[0][0] or ""
                # If 'paused' not in the CHECK, we need to recreate
                if "'paused'" not in current_sql:
                    # Get all data first
                    data = conn.execute("SELECT * FROM workflow_nodes").fetchall()
                    columns = [desc[0] for desc in conn.execute("SELECT * FROM workflow_nodes LIMIT 0").description]

                    # Drop and recreate with new CHECK
                    conn.execute("DROP TABLE workflow_nodes")
                    conn.execute("""
                        CREATE TABLE workflow_nodes (
                            node_id TEXT PRIMARY KEY,
                            run_id TEXT NOT NULL,
                            node_name TEXT NOT NULL,
                            node_type TEXT,
                            status TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'invalidated', 'paused')),
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
                    # Restore data using executemany if there is data
                    if data:
                        placeholders = ",".join(["?" for _ in columns])
                        conn.executemany(
                            f"INSERT INTO workflow_nodes ({','.join(columns)}) VALUES ({placeholders})",
                            data
                        )
                    conn.execute("CREATE INDEX idx_workflow_nodes_run_id ON workflow_nodes(run_id);")
                    conn.execute("CREATE INDEX idx_workflow_nodes_status ON workflow_nodes(status);")
                    print("[migration] Updated workflow_nodes: added 'paused' status")

        conn.commit()
        print(f"[migration] Completed successfully on {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    migrate(db_path)
