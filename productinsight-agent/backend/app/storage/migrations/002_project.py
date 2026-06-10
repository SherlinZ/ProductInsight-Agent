"""
Project-centric data model migration.
Handles both fresh installs and existing databases with ALTER TABLE for additive changes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return result is not None


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        # --- projects table ---
        if not _table_exists(conn, "projects"):
            conn.execute("""
                CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT NOT NULL,
                    task_type TEXT NOT NULL DEFAULT 'competitor_landscape',
                    target_region TEXT NOT NULL DEFAULT 'global',
                    description TEXT,
                    analysis_dimensions_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'archived', 'completed')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX idx_projects_status ON projects(status);")
            conn.execute("CREATE INDEX idx_projects_created ON projects(created_at DESC);")
            print("[migration] Created table: projects")

        # --- project_products table ---
        if not _table_exists(conn, "project_products"):
            conn.execute("""
                CREATE TABLE project_products (
                    project_product_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    product_slug TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    company_name TEXT,
                    official_website TEXT,
                    seed_urls_json TEXT NOT NULL DEFAULT '[]',
                    product_type TEXT,
                    region TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                )
            """)
            conn.execute("CREATE INDEX idx_project_products_project_id ON project_products(project_id);")
            print("[migration] Created table: project_products")

        # --- runs.project_id ---
        if not _col_exists(conn, "runs", "project_id"):
            conn.execute("ALTER TABLE runs ADD COLUMN project_id TEXT REFERENCES projects(project_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id);")
            print("[migration] Added column: runs.project_id")

        # --- products.product_slug ---
        if not _col_exists(conn, "products", "product_slug"):
            conn.execute("ALTER TABLE products ADD COLUMN product_slug TEXT;")
            print("[migration] Added column: products.product_slug")

        # --- sources.product_slug ---
        if not _col_exists(conn, "sources", "product_slug"):
            conn.execute("ALTER TABLE sources ADD COLUMN product_slug TEXT;")
            print("[migration] Added column: sources.product_slug")

        # --- evidence_items.product_slug ---
        if not _col_exists(conn, "evidence_items", "product_slug"):
            conn.execute("ALTER TABLE evidence_items ADD COLUMN product_slug TEXT;")
            print("[migration] Added column: evidence_items.product_slug")

        # --- facts.product_slug ---
        if not _col_exists(conn, "facts", "product_slug"):
            conn.execute("ALTER TABLE facts ADD COLUMN product_slug TEXT;")
            print("[migration] Added column: facts.product_slug")

        # --- evidence_items.quality_score ---
        if not _col_exists(conn, "evidence_items", "quality_score"):
            conn.execute("ALTER TABLE evidence_items ADD COLUMN quality_score REAL DEFAULT 0.0;")
            print("[migration] Added column: evidence_items.quality_score")

        # --- facts.raw_schema_key ---
        if not _col_exists(conn, "facts", "raw_schema_key"):
            conn.execute("ALTER TABLE facts ADD COLUMN raw_schema_key TEXT;")
            print("[migration] Added column: facts.raw_schema_key")

        # vNext-P0-Real-Frontend-Integration: project metadata_json
        if not _col_exists(conn, "projects", "metadata_json"):
            conn.execute("ALTER TABLE projects ADD COLUMN metadata_json TEXT DEFAULT '{}';")
            print("[migration] Added column: projects.metadata_json")

        conn.commit()
        print(f"[migration] Completed successfully on {db_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    migrate(db_path)
