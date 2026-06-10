PRAGMA foreign_keys = ON;

-- vNext-R1: Research Plans and Execution DAGs tables
CREATE TABLE IF NOT EXISTS research_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'confirmed', 'in_progress', 'completed', 'cancelled')),
    user_query TEXT NOT NULL,
    schema_type TEXT DEFAULT 'ai_agent_platform',
    target_region TEXT DEFAULT 'global',
    mode TEXT DEFAULT 'review' CHECK (mode IN ('auto', 'review', 'expert')),
    generated_by TEXT DEFAULT 'fallback' CHECK (generated_by IN ('llm', 'fallback', 'human_edited')),
    payload_json TEXT NOT NULL,
    dag_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS execution_dags (
    id TEXT PRIMARY KEY,
    research_plan_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'planned', 'running', 'completed', 'failed', 'cancelled')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (research_plan_id) REFERENCES research_plans(id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_research_plans_project_id ON research_plans(project_id);
CREATE INDEX IF NOT EXISTS idx_research_plans_status ON research_plans(status);
CREATE INDEX IF NOT EXISTS idx_execution_dags_research_plan_id ON execution_dags(research_plan_id);
