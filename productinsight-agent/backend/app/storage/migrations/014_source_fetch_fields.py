"""
Migration 014: Add fetch_level, fetch_strategy, char_count to sources table.

Tracks which fetch strategy was used for each URL (L1=requests, L2=playwright, L3=search_api)
and how many characters were extracted.
"""

import sqlite3

DB_PATH = "/home/shijialin/paperworking/workflow_new/productinsight-agent/data/productinsight.db"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return result is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    result = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in result)


def upgrade():
    conn = sqlite3.connect(DB_PATH)
    try:
        if not _table_exists(conn, "sources"):
            print("[migration 014] 'sources' table not found, skipping.")
            return

        new_columns = [
            ("fetch_level", "INTEGER DEFAULT 0"),
            ("fetch_strategy", "TEXT DEFAULT ''"),
            ("char_count", "INTEGER DEFAULT 0"),
        ]

        for col_name, col_def in new_columns:
            if not _column_exists(conn, "sources", col_name):
                conn.execute(f"ALTER TABLE sources ADD COLUMN {col_name} {col_def}")
                print(f"[migration 014] Added column: sources.{col_name}")
            else:
                print(f"[migration 014] Column sources.{col_name} already exists, skipping.")

        conn.commit()
        print("[migration 014] Complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    upgrade()
