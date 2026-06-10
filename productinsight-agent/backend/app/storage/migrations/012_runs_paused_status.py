"""
Migration 012: Add 'paused' status to runs table

The runs table CHECK constraint only allows:
('pending', 'running', 'completed', 'failed', 'cancelled')

But the code tries to set status='paused' when workflow pauses for HITL.
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
        print(f"[migration 012] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        if not _table_exists(conn, "runs"):
            print("[migration 012] runs table does not exist yet - skipping")
            return

        # Check current CHECK constraint
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchall()

        if rows:
            current_sql = rows[0][0] or ""
            if "'paused'" in current_sql:
                print("[migration 012] 'paused' already in CHECK constraint - skipping")
                return

        # Backup data
        data = conn.execute("SELECT * FROM runs").fetchall()
        columns = [desc[0] for desc in conn.execute("SELECT * FROM runs LIMIT 0").description]
        row_count = len(data)
        print(f"[migration 012] Backing up {row_count} rows from runs table")

        # Drop old table
        conn.execute("DROP TABLE runs")

        # Create new table with paused status
        conn.execute("""
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_title TEXT NOT NULL,
                task_brief_json TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('real_time', 'cached', 'replay')),
                status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'paused')),
                current_node TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                project_id TEXT REFERENCES projects(project_id)
            )
        """)

        # Recreate indexes
        conn.execute("CREATE INDEX idx_runs_status ON runs(status)")
        conn.execute("CREATE INDEX idx_runs_project_id ON runs(project_id)")

        # Restore data
        if data:
            placeholders = ",".join(["?" for _ in columns])
            conn.executemany(
                f"INSERT INTO runs ({','.join(columns)}) VALUES ({placeholders})",
                data
            )

        print("[migration 012] Added 'paused' status to runs table")
        conn.commit()

    except Exception as e:
        print(f"[migration 012] Error: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()
