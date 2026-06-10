"""
End-to-end test for vNext-P0-Real-Frontend-Integration:
Full链路: ResearchPlan → Project → Run → report_outline propagation.

Covers:
1. POST /api/research-plans/generate → plan with report_outline
2. POST /api/research-plans/{id}/confirm → dag_id
3. POST /api/projects with full research_plan / report_outline payload
4. GET /api/projects/{project_id} returns research_plan / report_outline
5. POST /api/projects/{project_id}/runs → run with task_brief.report_outline
6. GET /api/runs/{run_id}/report-draft returns report_outline
7. GET /api/system/status returns build_tag
"""
import json
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ensure_metadata_json_column():
    """Add metadata_json column to projects table if not present."""
    import sqlite3, os
    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db")
    db_path = db_url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
        if "metadata_json" not in cols:
            conn.execute('ALTER TABLE projects ADD COLUMN metadata_json TEXT DEFAULT "{}"')
            conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def ensure_db_schema():
    """Ensure metadata_json column exists before any tests run."""
    _ensure_metadata_json_column()
    yield


@pytest.fixture(scope="module")
def client(ensure_db_schema):
    """Create a test client against the running FastAPI app."""
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: System status has build_tag (Task 7)
# ---------------------------------------------------------------------------

def test_system_status_has_build_tag(client):
    """GET /api/system/status must return build_tag and loaded_modules."""
    resp = client.get("/api/system/status")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "build_tag" in data, f"build_tag missing from system status. Keys: {list(data.keys())}"
    assert data["build_tag"] == "vNext-P0-real-frontend-integration"
    assert "loaded_modules" in data
    assert data["loaded_modules"].get("nodes_has_plan_schema_llm") is True
    assert data["loaded_modules"].get("projects_accepts_research_plan") is True


# ---------------------------------------------------------------------------
# Test 2: Create research plan with report_outline (Task 6 seed)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 2: Create research plan with report_outline
# ---------------------------------------------------------------------------

def test_generate_research_plan_returns_report_outline(client):
    """POST /api/research-plans/generate must return plan with report_outline."""
    resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare Dify vs Flowise for AI agent workflow platforms, focusing on pricing and enterprise readiness.",
            "schema_type": "",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert resp.status_code == 200, f"Plan generation failed: {resp.status_code} - {resp.text}"
    result = resp.json()
    assert "research_plan" in result, f"Missing research_plan in response. Keys: {list(result.keys())}"
    research_plan = result["research_plan"]
    assert "report_outline" in research_plan, f"Missing report_outline in research_plan. Keys: {list(research_plan.keys())}"
    report_outline = research_plan["report_outline"]
    assert isinstance(report_outline, dict), f"report_outline should be dict, got {type(report_outline)}"
    assert "sections" in report_outline, f"report_outline missing sections. Keys: {list(report_outline.keys())}"
    sections = report_outline["sections"]
    assert isinstance(sections, list), f"sections should be list, got {type(sections)}"
    assert len(sections) > 0, "report_outline should have at least one section"
    # Verify section structure
    first = sections[0]
    assert "section_id" in first or "title" in first, f"Section missing section_id/title: {first}"


# ---------------------------------------------------------------------------
# Test 3: Confirm research plan
# ---------------------------------------------------------------------------

def test_confirm_research_plan_returns_dag_id(client):
    """POST /api/research-plans/{id}/confirm must return dag_id."""
    # First generate a plan
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Analyze Notion, Confluence, and Coda for team knowledge management.",
            "schema_type": "knowledge_management",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_id = gen_resp.json().get("research_plan_id")
    assert plan_id, "No research_plan_id returned"

    # Confirm it
    confirm_resp = client.post(f"/api/research-plans/{plan_id}/confirm", json={}, timeout=30)
    assert confirm_resp.status_code == 200, f"Confirm failed: {confirm_resp.status_code} - {confirm_resp.text}"
    confirm_data = confirm_resp.json()
    dag_id = confirm_data.get("dag_id")
    assert dag_id, f"Missing dag_id in confirm response. Keys: {list(confirm_data.keys())}"


# ---------------------------------------------------------------------------
# Test 4: Create project with full research_plan payload
# ---------------------------------------------------------------------------

def test_create_project_accepts_research_plan_fields(client):
    """POST /api/projects must accept research_plan, report_outline, research_plan_id, execution_dag_id."""
    # Generate a plan first
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare Slack, Teams, and Zoom for enterprise communication.",
            "schema_type": "",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_data = gen_resp.json().get("research_plan", {})
    plan_id = gen_resp.json().get("research_plan_id")

    # Confirm to get dag_id
    confirm_resp = client.post(f"/api/research-plans/{plan_id}/confirm", json={}, timeout=30)
    assert confirm_resp.status_code == 200
    dag_id = confirm_resp.json().get("dag_id")

    # Create project with full payload
    create_resp = client.post(
        "/api/projects",
        json={
            "project_name": "E2E Test Project - Communication Tools",
            "task_type": "competitor_landscape",
            "target_region": "global",
            "description": "End-to-end test project",
            "products": [
                {
                    "product_name": "Slack",
                    "company_name": "Salesforce",
                    "official_website": "https://slack.com",
                    "seed_urls": ["https://slack.com/features"],
                },
                {
                    "product_name": "Microsoft Teams",
                    "company_name": "Microsoft",
                    "official_website": "https://teams.microsoft.com",
                    "seed_urls": [],
                },
            ],
            "analysis_dimensions": ["function_tree", "pricing_model", "enterprise_readiness"],
            "research_plan_id": plan_id,
            "execution_dag_id": dag_id,
            "research_plan": plan_data,
            "report_outline": plan_data.get("report_outline", {}),
            "source_discovery": plan_data.get("source_discovery", {}),
        },
        timeout=15,
    )
    assert create_resp.status_code == 200, f"Project creation failed: {create_resp.status_code} - {create_resp.text}"
    project_result = create_resp.json()
    project_id = project_result.get("project_id")
    assert project_id, f"Missing project_id in response. Keys: {list(project_result.keys())}"


# ---------------------------------------------------------------------------
# Test 5: GET project returns research_plan / report_outline
# ---------------------------------------------------------------------------

def test_get_project_returns_research_plan(client, fresh_db):
    """GET /api/projects/{project_id} must return metadata.research_plan and metadata.report_outline."""
    # Create project first
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare Notion, Coda, and Slite for knowledge management.",
            "schema_type": "knowledge_management",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_data = gen_resp.json().get("research_plan", {})
    plan_id = gen_resp.json().get("research_plan_id")

    create_resp = client.post(
        "/api/projects",
        json={
            "project_name": "E2E Knowledge Mgmt Test",
            "task_type": "competitor_landscape",
            "target_region": "global",
            "description": "E2E test",
            "products": [
                {"product_name": "Notion", "company_name": "Notion Labs", "official_website": "https://notion.so"},
            ],
            "analysis_dimensions": [],
            "research_plan_id": plan_id,
            "execution_dag_id": None,
            "research_plan": plan_data,
            "report_outline": plan_data.get("report_outline", {}),
            "source_discovery": {},
        },
        timeout=15,
    )
    assert create_resp.status_code == 200
    project_id = create_resp.json().get("project_id")

    # Get project
    get_resp = client.get(f"/api/projects/{project_id}")
    assert get_resp.status_code == 200, f"GET project failed: {get_resp.status_code} - {get_resp.text}"
    proj = get_resp.json()

    # project must expose research_plan from metadata
    metadata = proj.get("metadata") or {}
    research_plan = metadata.get("research_plan") or proj.get("research_plan") or {}
    report_outline = metadata.get("report_outline") or proj.get("report_outline") or {}

    assert research_plan, (
        f"research_plan not found in project. metadata={metadata}. "
        f"project keys: {list(proj.keys())}"
    )
    assert report_outline or research_plan.get("report_outline"), (
        f"report_outline not found. research_plan={research_plan}"
    )


# ---------------------------------------------------------------------------
# Test 6: Start run propagates report_outline into task_brief (Task 5)
# ---------------------------------------------------------------------------

def test_start_run_propagates_report_outline(client, fresh_db):
    """POST /api/projects/{project_id}/runs must include report_outline in task_brief."""
    # Create project with research_plan
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Analyze Dify vs Coze for AI agent platform capabilities.",
            "schema_type": "ai_agent_platform",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_data = gen_resp.json().get("research_plan", {})
    plan_id = gen_resp.json().get("research_plan_id")
    report_outline_from_plan = plan_data.get("report_outline", {})

    create_resp = client.post(
        "/api/projects",
        json={
            "project_name": "E2E Run Propagation Test",
            "task_type": "competitor_landscape",
            "target_region": "global",
            "description": "E2E run propagation test",
            "products": [
                {"product_name": "Dify", "company_name": "Dify", "official_website": "https://dify.ai"},
            ],
            "analysis_dimensions": [],
            "research_plan_id": plan_id,
            "execution_dag_id": None,
            "research_plan": plan_data,
            "report_outline": report_outline_from_plan,
            "source_discovery": {},
        },
        timeout=15,
    )
    assert create_resp.status_code == 200
    project_id = create_resp.json().get("project_id")

    # Start run
    run_resp = client.post(f"/api/projects/{project_id}/runs", json={}, timeout=30)
    assert run_resp.status_code == 200, f"Start run failed: {run_resp.status_code} - {run_resp.text}"
    run_result = run_resp.json()
    run_id = run_result.get("run_id")
    assert run_id, f"Missing run_id in response. Keys: {list(run_result.keys())}"

    # Get the run
    get_run_resp = client.get(f"/api/runs/{run_id}")
    assert get_run_resp.status_code == 200
    run = get_run_resp.json()

    # Check task_brief has report_outline
    task_brief = run.get("task_brief") or {}
    run_report_outline = task_brief.get("report_outline") or {}

    assert run_report_outline or task_brief.get("report_outline"), (
        f"task_brief missing report_outline. task_brief keys: {list(task_brief.keys())}"
    )
    assert task_brief.get("research_plan"), "task_brief missing research_plan"
    assert task_brief.get("source_discovery") or task_brief.get("schema_type"), (
        "task_brief missing source_discovery or schema_type"
    )


# ---------------------------------------------------------------------------
# Test 7: /api/runs/{run_id}/report-draft endpoint (Task 6)
# ---------------------------------------------------------------------------

def test_report_draft_endpoint_returns_outline(client, fresh_db):
    """GET /api/runs/{run_id}/report-draft must return report_outline and section_statuses."""
    # Create a project and run
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare FastGPT, Coze, and Dify for AI workflow platforms.",
            "schema_type": "ai_agent_platform",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_data = gen_resp.json().get("research_plan", {})
    plan_id = gen_resp.json().get("research_plan_id")

    create_resp = client.post(
        "/api/projects",
        json={
            "project_name": "E2E Report Draft Test",
            "task_type": "competitor_landscape",
            "target_region": "global",
            "description": "E2E report draft test",
            "products": [
                {"product_name": "FastGPT", "company_name": "FastGPT Team", "official_website": "https://fastgpt.io"},
            ],
            "analysis_dimensions": [],
            "research_plan_id": plan_id,
            "execution_dag_id": None,
            "research_plan": plan_data,
            "report_outline": plan_data.get("report_outline", {}),
            "source_discovery": {},
        },
        timeout=15,
    )
    assert create_resp.status_code == 200
    project_id = create_resp.json().get("project_id")

    run_resp = client.post(f"/api/projects/{project_id}/runs", json={}, timeout=30)
    assert run_resp.status_code == 200
    run_id = run_resp.json().get("run_id")

    # Call report-draft endpoint
    draft_resp = client.get(f"/api/runs/{run_id}/report-draft")
    assert draft_resp.status_code == 200, f"report-draft failed: {draft_resp.status_code} - {draft_resp.text}"
    draft = draft_resp.json()

    # Must have report_outline from propagated task_brief
    assert "report_outline" in draft, f"report_outline missing from draft. Keys: {list(draft.keys())}"
    assert "section_statuses" in draft, f"section_statuses missing from draft. Keys: {list(draft.keys())}"
    assert "sections" in draft, f"sections missing from draft. Keys: {list(draft.keys())}"
    assert "report_status" in draft
    assert "report_id" in draft
    report_outline = draft.get("report_outline") or {}
    sections_from_outline = report_outline.get("sections", [])
    assert len(sections_from_outline) > 0, (
        f"report_outline should have sections. Got outline: {report_outline}"
    )


# ---------------------------------------------------------------------------
# Test 8: schema_type=null is accepted by /api/research-plans/generate (prevents regression)
# ---------------------------------------------------------------------------

def test_schema_type_null_accepted(client):
    """schema_type=null (JSON null) must be accepted by generate endpoint."""
    resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare Airtable and Notion for database capabilities.",
            "schema_type": None,  # Explicit null
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert resp.status_code == 200, f"schema_type=null rejected: {resp.status_code} - {resp.text}"
    result = resp.json()
    assert "research_plan" in result


# ---------------------------------------------------------------------------
# Test 9: schema_type="" is accepted (auto-infer)
# ---------------------------------------------------------------------------

def test_schema_type_empty_string_accepted(client):
    """schema_type='' (empty string) must be accepted and auto-inferred."""
    resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Analyze GitHub Copilot vs Cursor for AI coding assistants.",
            "schema_type": "",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert resp.status_code == 200, f"schema_type='' rejected: {resp.status_code} - {resp.text}"
    result = resp.json()
    assert "research_plan" in result
    assert "report_outline" in result["research_plan"]


# ---------------------------------------------------------------------------
# Test 10: End-to-end pipeline (Tasks 1-6 integration)
# ---------------------------------------------------------------------------

def test_full_pipeline_research_plan_to_report_draft(client, fresh_db):
    """Complete pipeline: generate plan → confirm → create project → start run → check report-draft."""
    # Step 1: Generate plan
    gen_resp = client.post(
        "/api/research-plans/generate",
        json={
            "user_query": "Compare Linear, Jira, and Asana for project management tools.",
            "schema_type": "",
            "target_region": "global",
            "mode": "review",
        },
        timeout=60,
    )
    assert gen_resp.status_code == 200
    plan_data = gen_resp.json()["research_plan"]
    plan_id = gen_resp.json()["research_plan_id"]
    assert plan_data.get("report_outline"), "Generated plan must have report_outline"

    # Step 2: Confirm plan
    confirm_resp = client.post(f"/api/research-plans/{plan_id}/confirm", json={}, timeout=30)
    assert confirm_resp.status_code == 200
    dag_id = confirm_resp.json().get("dag_id")
    assert dag_id, "Confirm must return dag_id"

    # Step 3: Create project with full payload
    create_resp = client.post(
        "/api/projects",
        json={
            "project_name": "E2E Full Pipeline Test",
            "task_type": plan_data.get("schema_type", "competitor_landscape"),
            "target_region": "global",
            "description": "Full pipeline E2E test",
            "products": [
                {"product_name": "Linear", "company_name": "Linear", "official_website": "https://linear.app"},
                {"product_name": "Jira", "company_name": "Atlassian", "official_website": "https://jira.com"},
            ],
            "analysis_dimensions": ["function_tree", "pricing_model"],
            "research_plan_id": plan_id,
            "execution_dag_id": dag_id,
            "research_plan": plan_data,
            "report_outline": plan_data.get("report_outline", {}),
            "source_discovery": plan_data.get("source_discovery", {}),
        },
        timeout=15,
    )
    assert create_resp.status_code == 200
    project_id = create_resp.json()["project_id"]

    # Step 4: Get project — must have research_plan in metadata
    get_proj_resp = client.get(f"/api/projects/{project_id}")
    assert get_proj_resp.status_code == 200
    proj = get_proj_resp.json()
    metadata = proj.get("metadata") or {}
    assert metadata.get("research_plan"), "Project metadata must have research_plan"
    assert metadata.get("report_outline"), "Project metadata must have report_outline"

    # Step 5: Start run
    run_resp = client.post(f"/api/projects/{project_id}/runs", json={}, timeout=30)
    assert run_resp.status_code == 200
    run_id = run_resp.json()["run_id"]

    # Step 6: Get run task_brief — must have report_outline
    get_run_resp = client.get(f"/api/runs/{run_id}")
    assert get_run_resp.status_code == 200
    run = get_run_resp.json()
    task_brief = run.get("task_brief") or {}
    assert task_brief.get("report_outline") or task_brief.get("research_plan"), (
        f"Run task_brief must have report_outline or research_plan. "
        f"task_brief keys: {list(task_brief.keys())}"
    )

    # Step 7: Check report-draft endpoint
    draft_resp = client.get(f"/api/runs/{run_id}/report-draft")
    assert draft_resp.status_code == 200
    draft = draft_resp.json()
    assert draft.get("report_outline"), f"report-draft must have report_outline. draft keys: {list(draft.keys())}"
    assert "sections" in draft
    assert "section_statuses" in draft
    assert draft["report_status"] in ("draft", "review", "approved", "blocked", "")
