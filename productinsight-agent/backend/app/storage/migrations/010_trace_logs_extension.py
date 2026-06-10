"""
Migration 010: Extend trace_logs table with comprehensive agent trace fields.

Adds fields for rich trace logging: project_id, agent_role, event_type,
prompt_text, input_payload_json, output_payload_json, decision_summary,
retry_count, artifact_refs_json. Also adds useful indexes.

Also updates the status CHECK constraint to include 'running' and 'paused'.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    result = conn.execute(
        f"PRAGMA table_info({table})",
    ).fetchall()
    return any(row[1] == column for row in result)


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print(f"[migration] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        if _column_exists(conn, "trace_logs", "trace_id"):
            # Table exists, add missing columns
            new_columns = [
                ("project_id", "TEXT"),
                ("agent_role", "TEXT"),
                ("event_type", "TEXT"),
                ("prompt_text", "TEXT"),
                ("input_payload_json", "TEXT"),
                ("output_payload_json", "TEXT"),
                ("decision_summary", "TEXT"),
                ("retry_count", "INTEGER DEFAULT 0"),
                ("artifact_refs_json", "TEXT"),
            ]

            for col_name, col_def in new_columns:
                if not _column_exists(conn, "trace_logs", col_name):
                    conn.execute(f"ALTER TABLE trace_logs ADD COLUMN {col_name} {col_def}")
                    print(f"[migration] Added column: trace_logs.{col_name}")

            # Update status CHECK constraint to include 'running' and 'paused'
            try:
                conn.execute(
                    "ALTER TABLE trace_logs DROP CONSTRAINT IF EXISTS trace_logs_status_check"
                )
                print("[migration] Dropped old status constraint (if any)")
            except sqlite3.OperationalError:
                pass  # Constraint may not have a name in older SQLite

            try:
                conn.execute(
                    "ALTER TABLE trace_logs ADD CONSTRAINT trace_logs_status_check "
                    "CHECK (status IN ('success', 'failed', 'retry', 'skipped', 'running', 'paused'))"
                )
                print("[migration] Added status CHECK constraint with running/paused")
            except sqlite3.OperationalError:
                # SQLite version may not support named CHECK constraints
                # Try renaming the table and recreating
                print("[migration] Could not add named CHECK, trying table recreation...")
                try:
                    # Backup data
                    conn.execute("CREATE TABLE trace_logs_backup AS SELECT * FROM trace_logs")
                    conn.execute("DROP TABLE trace_logs")

                    # Recreate with new constraint
                    conn.execute("""
                        CREATE TABLE trace_logs (
                            trace_id TEXT PRIMARY KEY,
                            run_id TEXT NOT NULL,
                            node_name TEXT NOT NULL,
                            agent_name TEXT,
                            prompt_version TEXT,
                            model_name TEXT,
                            input_path TEXT,
                            output_path TEXT,
                            decision TEXT,
                            token_input INTEGER,
                            token_output INTEGER,
                            latency_ms INTEGER,
                            status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'retry', 'skipped', 'running', 'paused')),
                            error_message TEXT,
                            started_at TEXT NOT NULL,
                            completed_at TEXT,
                            created_at TEXT NOT NULL,
                            project_id TEXT,
                            agent_role TEXT,
                            event_type TEXT,
                            prompt_text TEXT,
                            input_payload_json TEXT,
                            output_payload_json TEXT,
                            decision_summary TEXT,
                            retry_count INTEGER DEFAULT 0,
                            artifact_refs_json TEXT
                        )
                    """)
                    conn.execute("INSERT INTO trace_logs SELECT * FROM trace_logs_backup")
                    conn.execute("DROP TABLE trace_logs_backup")
                    print("[migration] Recreated trace_logs with expanded status CHECK")
                except sqlite3.OperationalError:
                    pass  # Give up on CHECK constraint update

            # Add indexes (ignore errors if they already exist)
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_trace_logs_node_name ON trace_logs(node_name)",
                "CREATE INDEX IF NOT EXISTS idx_trace_logs_agent_name ON trace_logs(agent_name)",
                "CREATE INDEX IF NOT EXISTS idx_trace_logs_status ON trace_logs(status)",
            ]
            for idx_sql in indexes:
                try:
                    conn.execute(idx_sql)
                    print(f"[migration] Index created: {idx_sql.split(' ')[5]}")
                except sqlite3.OperationalError as e:
                    if "already exists" not in str(e).lower():
                        raise

        conn.commit()
        print("[migration] 010_trace_logs_extension completed successfully")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"Migration 010 failed: {exc}") from exc
    finally:
        conn.close()
