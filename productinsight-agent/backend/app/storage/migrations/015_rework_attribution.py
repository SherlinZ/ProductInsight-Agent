"""Migration 015: Add rework attribution to evidence_items and facts.

This migration adds columns that record whether an evidence/fact was added
during a rework round (a true re-collect triggered by the reviewer's feedback
loop). This lets us measure the actual feedback loop impact:

- rework_iteration: which rework round this evidence belongs to (0 = initial
  collection, 1+ = added by execute_rework → re-collect)
- rework_reason: short tag explaining why rework was triggered
  (e.g. "MISSING_DIMENSION:pricing_model")

Used by the new "true feedback loop" graph routing (P1-Redesign 2026-06-18)
where execute_rework can jump back to collect_sources to fetch fresh evidence.
"""

UP_MIGRATION = """
-- Track which rework round produced each evidence row
ALTER TABLE evidence_items ADD COLUMN rework_iteration INTEGER DEFAULT 0;

-- Track which rework reason triggered the new evidence
ALTER TABLE evidence_items ADD COLUMN rework_reason TEXT DEFAULT '';

-- Same attribution for facts
ALTER TABLE facts ADD COLUMN rework_iteration INTEGER DEFAULT 0;

-- Index for fast "show me all evidence added by rework" queries
CREATE INDEX IF NOT EXISTS idx_evidence_items_rework_iter
  ON evidence_items(rework_iteration);
CREATE INDEX IF NOT EXISTS idx_facts_rework_iter
  ON facts(rework_iteration);
"""

DOWN_MIGRATION = """
-- SQLite does not support DROP COLUMN.
-- Rollback requires a table rebuild (not implemented for this migration).
PRAGMA foreign_keys=OFF;
"""


def migrate(db_path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # evidence_items columns
    cursor.execute("PRAGMA table_info(evidence_items)")
    ev_cols = {row[1] for row in cursor.fetchall()}

    if "rework_iteration" not in ev_cols:
        print("[migration 015] Adding rework_iteration column to evidence_items")
        cursor.execute(
            "ALTER TABLE evidence_items ADD COLUMN rework_iteration INTEGER DEFAULT 0"
        )
    else:
        print("[migration 015] evidence_items.rework_iteration already exists - skipping")

    if "rework_reason" not in ev_cols:
        print("[migration 015] Adding rework_reason column to evidence_items")
        cursor.execute(
            "ALTER TABLE evidence_items ADD COLUMN rework_reason TEXT DEFAULT ''"
        )
    else:
        print("[migration 015] evidence_items.rework_reason already exists - skipping")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_evidence_items_rework_iter "
        "ON evidence_items(rework_iteration)"
    )

    # facts columns
    cursor.execute("PRAGMA table_info(facts)")
    fact_cols = {row[1] for row in cursor.fetchall()}

    if "rework_iteration" not in fact_cols:
        print("[migration 015] Adding rework_iteration column to facts")
        cursor.execute(
            "ALTER TABLE facts ADD COLUMN rework_iteration INTEGER DEFAULT 0"
        )
    else:
        print("[migration 015] facts.rework_iteration already exists - skipping")

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_rework_iter "
        "ON facts(rework_iteration)"
    )

    conn.commit()
    conn.close()
    print("[migration 015] Completed successfully")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python 015_rework_attribution.py <db_path>")
        sys.exit(1)
    migrate(sys.argv[1])