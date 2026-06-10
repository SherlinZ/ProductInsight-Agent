-- Project-centric data model (additive only - safe to re-run on existing DB)
-- Uses CREATE TABLE IF NOT EXISTS so it is idempotent.
-- Schema changes (ALTER TABLE) are handled by 002_project.py migration instead.

-- projects: primary container for competitive analysis work
CREATE TABLE IF NOT EXISTS projects (
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
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);

-- project_products: products scoped to a project
CREATE TABLE IF NOT EXISTS project_products (
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
);

CREATE INDEX IF NOT EXISTS idx_project_products_project_id ON project_products(project_id);
