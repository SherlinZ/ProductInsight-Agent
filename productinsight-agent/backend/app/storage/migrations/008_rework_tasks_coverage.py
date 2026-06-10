"""
Extend rework_tasks table for product coverage gap rework tasks.

Adds fields for product-specific rework (from insufficient/partial product coverage)
in addition to the existing intervention-based rework.
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
        if _table_exists(conn, "rework_tasks"):
            new_columns = [
                ("product_id", "TEXT"),
                ("product_name", "TEXT"),
                ("target_node", "TEXT"),
                ("required_action", "TEXT"),
                ("seed_urls_json", "TEXT DEFAULT '[]'"),
                ("error_json", "TEXT"),
                ("completed_at", "TEXT"),
                ("metrics_before_json", "TEXT"),
                ("metrics_after_json", "TEXT"),
            ]

            for col_name, col_def in new_columns:
                if not _column_exists(conn, "rework_tasks", col_name):
                    conn.execute(f"ALTER TABLE rework_tasks ADD COLUMN {col_name} {col_def}")
                    print(f"[migration] Added column: rework_tasks.{col_name}")

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rework_tasks_product_id ON rework_tasks(product_id);")
                print("[migration] Added index: idx_rework_tasks_product_id")
            except sqlite3.OperationalError:
                pass

        conn.commit()
        print("[migration] 008_rework_tasks_coverage completed successfully")
    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"Migration 008 failed: {exc}") from exc
    finally:
        conn.close()
