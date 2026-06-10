"""Migration 013: Add trust_tier and source_type to evidence_items.

This migration adds two new columns to the evidence_items table:
- trust_tier: The trust tier of the evidence (high/medium/low)
- source_type: The type of source (official_site/docs/github/blog/media/social/etc)

These columns are used to categorize evidence by quality for the report.
"""

UP_MIGRATION = """
-- Add trust_tier column to evidence_items
ALTER TABLE evidence_items ADD COLUMN trust_tier TEXT DEFAULT 'medium';

-- Add source_type column to evidence_items
ALTER TABLE evidence_items ADD COLUMN source_type TEXT DEFAULT 'web_page';

-- Create index for faster trust_tier queries
CREATE INDEX IF NOT EXISTS idx_evidence_items_trust_tier ON evidence_items(trust_tier);

-- Create index for faster source_type queries
CREATE INDEX IF NOT EXISTS idx_evidence_items_source_type ON evidence_items(source_type);
"""

DOWN_MIGRATION = """
-- Note: SQLite does not support DROP COLUMN easily
-- This is a one-way migration for adding columns
-- If rollback is needed, data would need to be migrated manually
PRAGMA foreign_keys=OFF;
"""


def migrate(db_path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if columns already exist
    cursor.execute("PRAGMA table_info(evidence_items)")
    columns = {row[1] for row in cursor.fetchall()}
    
    if "trust_tier" not in columns:
        print("[migration 013] Adding trust_tier column to evidence_items")
        cursor.execute("ALTER TABLE evidence_items ADD COLUMN trust_tier TEXT DEFAULT 'medium'")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_items_trust_tier ON evidence_items(trust_tier)")
        print("[migration 013] Added trust_tier column and index")
    else:
        print("[migration 013] trust_tier column already exists - skipping")
    
    if "source_type" not in columns:
        print("[migration 013] Adding source_type column to evidence_items")
        cursor.execute("ALTER TABLE evidence_items ADD COLUMN source_type TEXT DEFAULT 'web_page'")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_items_source_type ON evidence_items(source_type)")
        print("[migration 013] Added source_type column and index")
    else:
        print("[migration 013] source_type column already exists - skipping")
    
    conn.commit()
    conn.close()
    print("[migration 013] Completed successfully")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python 013_evidence_trust_tier.py <db_path>")
        sys.exit(1)
    migrate(sys.argv[1])
