"""
Add quality_json column to evidence_items for evidence quality scoring.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print(f"[migration] Database does not exist yet: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        # --- evidence_items.quality_json ---
        if not _col_exists(conn, "evidence_items", "quality_json"):
            conn.execute("""
                ALTER TABLE evidence_items ADD COLUMN quality_json TEXT;
            """)
            print("[migration] Added column: evidence_items.quality_json")

        # --- evidence_items.usable_for_claim ---
        if not _col_exists(conn, "evidence_items", "usable_for_claim"):
            conn.execute("""
                ALTER TABLE evidence_items ADD COLUMN usable_for_claim INTEGER DEFAULT 0;
            """)
            print("[migration] Added column: evidence_items.usable_for_claim")

        conn.commit()
        print(f"[migration] Completed successfully on {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    migrate(db_path)
