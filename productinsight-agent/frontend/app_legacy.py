from __future__ import annotations

import json
import time
import requests
import streamlit as st
import pandas as pd

from frontend.components.dag_preview import render_research_plan_dag_preview

# ---------------------------------------------------------------------------
# Helper: Coverage Gap Rework Task Card
# ---------------------------------------------------------------------------

def _render_coverage_gap_task_card(task: dict, run_id: str):
    """Render a coverage gap rework task card with before/after comparison."""
    import requests as _requests

    rework_id = task.get("rework_id") or task.get("task_id") or ""
    product_name = task.get("product_name") or task.get("product_id") or "Unknown product"
    product_id = task.get("product_id") or ""
    status = task.get("status") or "unknown"
    reason_codes = task.get("reason_codes") or []
    before_json = task.get("before_json") or {}
    after_json = task.get("after_json") or {}
    error_json = task.get("error_json") or {}
    executed = after_json.get("executed", False)
    simulated = after_json.get("simulated_fix", False)

    is_completed = status == "completed"

    # Expand completed tasks so the before/after comparison is immediately visible
    with st.expander(
        f"🎯 {product_name} — {product_id} | {status}",
        expanded=(status in ("planned", "pending", "running", "failed") or executed or simulated),
    ):
        # ── Before / After comparison ──────────────────────────────────────────
        summary = after_json.get("execution_summary", {})
        exec_sum = after_json.get("execution_summary", {})

        # Read before values from before_json
        before_src = before_json.get("sources", 0)
        before_ev = before_json.get("evidence", before_json.get("evidence_count", 0))
        before_facts = before_json.get("facts", before_json.get("facts_count", 0))
        before_signed = before_json.get("signed_claims", 0)

        # Read deltas from execution_summary
        src_added = exec_sum.get("sources_added", 0)
        ev_added = exec_sum.get("evidence_added", 0)
        facts_added = exec_sum.get("facts_added", 0)
        signed_added = exec_sum.get("signed_claims_added", 0)

        # After totals
        after_src_total = before_src + src_added
        after_ev_total = before_ev + ev_added
        after_facts_total = before_facts + facts_added
        after_signed_total = before_signed + signed_added

        # Build delta strings
        def delta_str(val, executed_flag):
            if not executed_flag:
                return None
            return f"+{val}" if val else "0"

        # Always show before row with delta badges
        bc1, bc2, bc3, bc4 = st.columns(4)
        bc1.metric(
            "Sources (before)",
            before_src,
            delta=delta_str(src_added, executed or simulated),
        )
        bc2.metric(
            "Evidence (before)",
            before_ev,
            delta=delta_str(ev_added, executed or simulated),
        )
        bc3.metric(
            "Facts (before)",
            before_facts,
            delta=delta_str(facts_added, executed or simulated),
        )
        bc4.metric(
            "Signed Claims (before)",
            before_signed,
            delta=delta_str(signed_added, executed or simulated),
        )

        if executed:
            st.success("✅ 真实返工已完成")
            ac1, ac2, ac3, ac4 = st.columns(4)
            ac1.metric("Sources (after)", after_src_total)
            ac2.metric("Evidence (after)", after_ev_total)
            ac3.metric("Facts (after)", after_facts_total)
            ac4.metric("Signed Claims (after)", after_signed_total)
            # Detailed summary
            st.caption(
                f"本次返工：+{src_added} source(s)，+{ev_added} evidence，"
                f"+{facts_added} facts，+{signed_added} signed claim(s)"
            )
        elif simulated:
            st.info("🔁 模拟修复已完成，不代表真实补证")
            st.caption(
                f"模拟：+{ev_added} evidence，+{facts_added} facts，"
                f"+{signed_added} signed claim(s)（仅为演示）"
            )

        # ── Status / reason / missing dims ───────────────────────────────────
        col_s, col_r = st.columns([1, 2])
        with col_s:
            st.caption(f"Status: **{status}**")
        if reason_codes:
            with col_r:
                st.caption("Reason: " + ", ".join(reason_codes))

        missing_dimensions = before_json.get("missing_dimensions") or []
        if missing_dimensions:
            st.caption("缺失维度: " + ", ".join(missing_dimensions))

        # ── Seed URLs ─────────────────────────────────────────────────────────
        existing_urls = task.get("seed_urls") or []
        seed_text = st.text_area(
            "补充抓取 URL（每行一个）",
            value="\n".join(existing_urls),
            key=f"coverage_seed_urls_{rework_id}",
            height=90,
            disabled=is_completed,
        )

        # ── Action buttons ────────────────────────────────────────────────────
        if not is_completed:
            col_exec, col_sim = st.columns(2)
            with col_exec:
                urls = [u.strip() for u in seed_text.splitlines() if u.strip()]
                if st.button(
                    "🔍 Execute real rework",
                    key=f"execute_real_rework_{rework_id}",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        resp = _requests.post(
                            f"{API_BASE}/api/rework-tasks/{rework_id}/execute",
                            json={"seed_urls": urls, "mode": "real_time"},
                            timeout=240,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Rework execution failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            result = resp.json()
                            st.success(result.get("message", "Rework completed."))
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Rework execution failed: {exc}")
            with col_sim:
                if st.button(
                    "🎭 Simulate fix（备用演示，不新增证据）",
                    key=f"simulate_rework_{rework_id}",
                    use_container_width=True,
                ):
                    try:
                        resp = _requests.post(
                            f"{API_BASE}/api/rework-tasks/{rework_id}/simulate-fix",
                            timeout=60,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Simulation failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Simulation completed.")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Simulation failed: {exc}")
        else:
            st.caption("⏹ 已完成，无需重复执行。")

        # ── Debug JSON ───────────────────────────────────────────────────────
        if before_json:
            with st.expander("Before JSON", expanded=False):
                st.json(before_json)
        if after_json:
            with st.expander("After JSON", expanded=False):
                st.json(after_json)
        if error_json:
            st.error(error_json.get("error_message", str(error_json)))
            with st.expander("Error JSON", expanded=False):
                st.json(error_json)


# ---------------------------------------------------------------------------
# Helper: Intervention Rework Task Card
# ---------------------------------------------------------------------------

def _render_intervention_rework_task_card(task: dict, run_id: str):
    """Render an intervention-based rework task card."""
    rework_id = task.get("rework_id", "unknown")
    status = task.get("status", "unknown")
    reason_codes = task.get("reason_codes", [])
    plan = task.get("rework_plan_json") or {}
    steps = plan.get("steps", []) if isinstance(plan, dict) else []
    after_json = task.get("after_json") or {}
    rerun_done = isinstance(after_json, dict) and after_json.get("review_rerun_simulated")

    status_color = {
        "pending": "orange", "planned": "blue", "running": "blue",
        "completed": "green", "failed": "red", "cancelled": "gray",
    }.get(status, "gray")

    expander_title = f"Rework: `{rework_id}` — Status: :{status_color}[{status}]"

    with st.expander(expander_title):
        c_meta1, c_meta2, c_meta3 = st.columns(3)
        with c_meta1:
            st.text(f"Intervention: `{task.get('intervention_id', 'N/A')}`")
        with c_meta2:
            st.text(f"Source: {task.get('source_node', 'N/A')} / {task.get('target_artifact_type', 'N/A')}")
        with c_meta3:
            st.text(f"Created: {str(task.get('created_at', 'N/A'))[:19].replace('T', ' ')}")

        st.divider()

        if reason_codes:
            st.markdown("**Reason Codes**")
            rc_cols = st.columns(min(len(reason_codes), 4))
            for i, rc in enumerate(reason_codes):
                with rc_cols[i % len(rc_cols)]:
                    st.markdown(f":orange[{format_reason_code(rc)}]")

        st.divider()

        if steps:
            st.markdown("**Rework Plan**")
            for s in steps:
                icon = ":white_check_mark:" if status == "completed" else ":arrow_right:"
                st.markdown(
                    f"{icon} **Step {s.get('step')}:** `{s.get('action')}` "
                    f"(`{s.get('reason')}`) — {s.get('description', '')}"
                )

        st.divider()

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if status in ("pending", "planned"):
                if st.button("Simulate fix (fallback)", key=f"simulate_{rework_id}", use_container_width=True):
                    with st.spinner("Simulating..."):
                        result = post_json(f"/api/rework-tasks/{rework_id}/simulate-fix", {})
                    if result and result.get("status") == "completed":
                        st.success("Done.")
                        st.rerun()
                    else:
                        st.error(f"Failed: {result}")

        with btn_col2:
            if status == "completed" and not rerun_done:
                if st.button("Simulate Review Rerun", key=f"rerun_{rework_id}", use_container_width=True):
                    with st.spinner("Simulating..."):
                        result = post_json(f"/api/rework-tasks/{rework_id}/simulate-review-rerun", {})
                    if result and result.get("after_json", {}).get("review_rerun_simulated"):
                        st.success("Review rerun simulated.")
                        st.rerun()
                    else:
                        st.error(f"Failed: {result}")

        if isinstance(after_json, dict) and after_json.get("review_rerun_simulated"):
            st.divider()
            st.success("**Review Rerun Result — Ready for review**")
            qg_before = after_json.get("quality_gate_before", {})
            qg_after = after_json.get("quality_gate_after", {})

            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Before", qg_before.get("status", "blocked"))
            with mc2:
                st.metric("After", qg_after.get("status", "ready_for_review"))
            with mc3:
                st.metric("Remaining issues", len(qg_after.get("remaining_issues", [])))

            st.markdown(f"**Next:** {after_json.get('recommended_next_action', '')}")

            summary = after_json.get("before_after_summary", [])
            if summary:
                st.markdown("**Before / After Comparison**")
                for entry in summary:
                    ba_row = st.columns(2)
                    with ba_row[0]:
                        st.markdown(":red[Before]")
                        st.text((entry.get("before") or "")[:140])
                    with ba_row[1]:
                        st.markdown(":green[After]")
                        st.text((entry.get("after") or "")[:140])
                st.divider()

        if isinstance(after_json, dict) and after_json:
            with st.expander("After JSON", expanded=False):
                st.json(after_json)
API_BASE = "http://localhost:8000"

st.set_page_config(page_title="ProductInsight Agent", page_icon="🎛️", layout="wide")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "demo_loaded" not in st.session_state:
    st.session_state["demo_loaded"] = False

if "selected_run_id" not in st.session_state:
    st.session_state["selected_run_id"] = None

if "selected_project_id" not in st.session_state:
    st.session_state["selected_project_id"] = None

# Intake Draft Session State
if "intake_user_request" not in st.session_state:
    st.session_state["intake_user_request"] = ""

if "intake_project_draft" not in st.session_state:
    st.session_state["intake_project_draft"] = None

if "intake_products_df" not in st.session_state:
    st.session_state["intake_products_df"] = None

if "intake_selected_dimensions" not in st.session_state:
    st.session_state["intake_selected_dimensions"] = None

if "intake_generated" not in st.session_state:
    st.session_state["intake_generated"] = False

# AnalysisFlow Session State
if "af_stage" not in st.session_state:
    st.session_state["af_stage"] = "intake"  # intake | plan_review | running | deliverables

if "af_intake_draft" not in st.session_state:
    st.session_state["af_intake_draft"] = None

if "af_intake_products_df" not in st.session_state:
    st.session_state["af_intake_products_df"] = None

if "af_intake_dims" not in st.session_state:
    st.session_state["af_intake_dims"] = None

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .stMainBlockContainer { padding-top: 1rem; }
    section[data-testid="stSidebar"] > div { padding-top: 1rem; }
    div[data-testid="stExpander"] { border: 1px solid #e0e0e0; border-radius: 8px; }
    .stMetric { background: #f8f9fa; border-radius: 8px; padding: 10px; }
</style>
""", unsafe_allow_html=True)

st.title("ProductInsight Agent")
st.caption("证据优先的多 Agent 竞品分析工作台")

NAV_ZH = {
    # --- Production nav (primary) ---
    "Analysis Flow": "AnalysisFlow",
    "Running Center": "AnalysisFlow",   # Same page, but triggers running sub-stage
    "Research Plan": "ResearchPlan",
    "Projects": "Projects",
    "Runs": "Runs",
    "Project Workspace": "ProjectDetail",
    "Review Center": "HumanReview",
    # --- Debug / Audit nav (preserved) ---
    "Audit / Debug": "TraceAudit",
    # --- Legacy nav (preserved for direct jump / legacy buttons) ---
    "DAG 执行": "DAG",
    "Human Review": "HumanReview",
    "Trace & Audit": "TraceAudit",
    "New Analysis": "NewAnalysis",
    "Project Detail": "ProjectDetail",
    "Sources": "Sources",
    "Evidence Hub": "EvidenceHub",
    "Knowledge Table": "KnowledgeTable",
    "分析报告": "Report",
    "证据池": "Evidence",
    "质检与打回": "Review",
    "合规与隐私": "Compliance",
    "Agent 团队": "Agents",
    "离线回放": "Replay",
    "质量指标": "Metrics",
    "执行追踪": "Trace",
}

NAV_DISPLAY = [
    "Analysis Flow",
    "Running Center",
    "Research Plan",
    "Projects",
    "Runs",
    "Project Workspace",
    "Review Center",
    "Audit / Debug",
]

if "current_page_zh" not in st.session_state:
    st.session_state["current_page_zh"] = "Analysis Flow"

# Compute which sidebar item to highlight: if current_page_zh is in NAV_DISPLAY,
# highlight it; otherwise default to "Audit / Debug" (hidden legacy page case).
if st.session_state["current_page_zh"] in NAV_DISPLAY:
    sidebar_selected = st.session_state["current_page_zh"]
else:
    sidebar_selected = "Audit / Debug"

selected_nav = st.sidebar.radio(
    "导航",
    NAV_DISPLAY,
    index=NAV_DISPLAY.index(sidebar_selected),
)

# Only update current_page_zh when the user actually clicked a different sidebar item.
# If selected_nav == sidebar_selected, either the user clicked the already-selected
# item (no-op) or we are on a hidden legacy page and the radio is just reflecting
# the "Audit / Debug" fallback (also no-op).
if selected_nav != sidebar_selected:
    # Special case: "Running Center" always goes to Analysis Flow + running stage
    if selected_nav == "Running Center":
        st.session_state["current_page_zh"] = "Analysis Flow"
        st.session_state["af_stage"] = "running"
        # Ensure a run is selected; if not, don't navigate
        if not st.session_state.get("selected_run_id"):
            st.warning("No active run. Please start a run first.")
            st.stop()
    else:
        st.session_state["current_page_zh"] = selected_nav
    st.rerun()

page = NAV_ZH.get(st.session_state["current_page_zh"], "AnalysisFlow")


def goto_page(display_name: str):
    """Navigate to any page (including hidden legacy pages) by its display name."""
    if display_name in NAV_ZH:
        st.session_state["current_page_zh"] = display_name
        st.rerun()
    else:
        st.error(f"Invalid page: {display_name}")


def start_run_async_and_go_to_running(project_id: str, run_id: str | None = None) -> bool:
    """Unified helper: create run + start-async + navigate to Running Center.

    Returns True on success (will st.rerun), False on error (caller should st.stop).
    """
    # 1. Create run if run_id not provided
    if run_id is None:
        try:
            run_resp = requests.post(
                f"{API_BASE}/api/projects/{project_id}/runs",
                json={"mode": "real_time", "auto_start": False},
                timeout=15,
            )
            run_resp.raise_for_status()
            run_data = run_resp.json()
            run_id = run_data.get("run_id")
        except requests.exceptions.RequestException as e:
            st.session_state["last_start_error"] = f"创建 Run 失败: {e}"
            return False

    if not run_id:
        st.session_state["last_start_error"] = "创建 Run 成功但未返回 run_id"
        return False

    # 2. Start async
    try:
        ar = requests.post(
            f"{API_BASE}/api/runs/{run_id}/start-async",
            timeout=10,
        )
        if ar.status_code not in (200, 201, 202):
            try:
                err_detail = ar.json().get("detail", ar.text)
            except Exception:
                err_detail = str(ar.status_code)
            st.session_state["last_start_error"] = f"启动分析失败: {err_detail}"
            return False
    except requests.exceptions.RequestException as e:
        st.session_state["last_start_error"] = f"启动分析失败: {e}"
        return False

    # 3. Navigate to Running Center
    st.session_state["selected_project_id"] = project_id
    st.session_state["selected_run_id"] = run_id
    st.session_state["af_stage"] = "running"
    st.session_state["current_page_zh"] = "Analysis Flow"
    # Clear any previous error
    st.session_state["last_start_error"] = ""
    st.rerun()


# Use session state to persist selected run_id across re-renders
# Priority: session_state > latest completed real_time run > latest completed any > demo fallback
def _get_default_run_id():
    # 1. session state
    if st.session_state.get("selected_run_id"):
        return st.session_state["selected_run_id"]
    # 2. latest completed real_time run
    try:
        resp = requests.get(f"{API_BASE}/api/runs?limit=200", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            runs = data if isinstance(data, list) else data.get("runs", [])
            completed_real = [
                r for r in runs
                if r.get("status") == "completed" and r.get("mode") == "real_time"
            ]
            if completed_real:
                latest = sorted(completed_real, key=lambda r: r.get("created_at", ""), reverse=True)[0]
                return latest.get("run_id")
            # 3. latest completed any
            completed_any = [r for r in runs if r.get("status") == "completed"]
            if completed_any:
                latest = sorted(completed_any, key=lambda r: r.get("created_at", ""), reverse=True)[0]
                return latest.get("run_id")
    except Exception:
        pass
    # 4. demo fallback only
    return "run_demo_ai_agent_001"

default_run = _get_default_run_id()
run_id = st.sidebar.text_input("Run ID", value=default_run)

# Keep session state in sync when user manually types a run_id
if run_id and run_id != st.session_state.get("selected_run_id"):
    st.session_state["selected_run_id"] = run_id

# ---------------------------------------------------------------------------
# Helpers (must be defined before Load Golden Demo button)
# ---------------------------------------------------------------------------

def _is_completed_real_rework_task(task: dict) -> bool:
    after_json = task.get("after_json") or {}
    summary = after_json.get("execution_summary", {})
    return (
        task.get("status") == "completed"
        and after_json.get("executed") is True
        and summary.get("evidence_added", 0) > 0
        and summary.get("facts_added", 0) > 0
        and summary.get("signed_claims_added", 0) >= 1
    )


def _is_coverage_gap_task(task: dict) -> bool:
    reason_codes = task.get("reason_codes") or []
    return (
        "INSUFFICIENT_PRODUCT_COVERAGE" in reason_codes
        or "PARTIAL_PRODUCT_COVERAGE" in reason_codes
        or str(task.get("rework_id", "")).startswith("rework_cov_")
    )


def _status_icon(status: str) -> str:
    m = {
        "success": "🟢", "signed": "🟢",
        "failed": "🔴", "failed_permanently": "🔴",
        "rework_required": "🔴",
        "pending": "🟡", "retry": "🔵",
        "skipped": "⚪", "running": "🔵",
    }
    return m.get(status.lower().strip(), "⚪")


def _status_color(status: str) -> str:
    m = {
        "success": "green", "signed": "green",
        "failed": "red", "failed_permanently": "red",
        "rework_required": "red",
        "pending": "yellow", "retry": "orange",
        "skipped": "gray", "running": "blue",
    }
    return m.get(status.lower().strip(), "gray")


def _badge(status: str) -> str:
    return f":{_status_color(status)}[{_status_icon(status)} {status}]"


def _workflow_node_icon(status: str) -> str:
    """Get icon for workflow node status."""
    m = {
        "completed": "✅",
        "running": "🔄",
        "paused": "⏸️",
        "failed": "❌",
        "pending": "⚪",
        "skipped": "⚠️",
        "invalidated": "⚠️",
    }
    return m.get(status.lower().strip(), "⚪")


def _workflow_node_color(status: str) -> str:
    """Get color for workflow node status."""
    m = {
        "completed": "green",
        "running": "blue",
        "paused": "orange",
        "failed": "red",
        "pending": "gray",
        "skipped": "gray",
        "invalidated": "gray",
    }
    return m.get(status.lower().strip(), "gray")


def get_json(path: str, default=None):
    try:
        resp = requests.get(f"{API_BASE}{path}", timeout=10)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to backend API ({API_BASE}). Make sure the backend is running.")
        return default
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return default


def post_json(path: str, data: dict = None, default=None):
    """POST JSON to API and handle errors."""
    try:
        resp = requests.post(f"{API_BASE}{path}", json=data or {}, timeout=15)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to backend API ({API_BASE}). Make sure the backend is running.")
        return default
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return default


# --- Load Golden Demo button ---
st.sidebar.divider()
if st.sidebar.button("Load Golden Demo", use_container_width=True):
    data = get_json("/api/runs?limit=200")
    runs = data if isinstance(data, list) else (data.get("runs", []) if data else [])
    golden_run_id = None
    golden_proj_id = None

    # Priority 1: completed real coverage rework task
    for r in runs:
        rid = r.get("run_id", "")
        if not rid:
            continue
        tasks = get_json(f"/api/runs/{rid}/rework-tasks", []) or []
        for task in tasks:
            if _is_completed_real_rework_task(task):
                golden_run_id = rid
                golden_proj_id = r.get("project_id")
                break
        if golden_run_id:
            break

    # Priority 2: planned/pending/running coverage gap task
    if not golden_run_id:
        for r in runs:
            rid = r.get("run_id", "")
            if not rid:
                continue
            tasks = get_json(f"/api/runs/{rid}/rework-tasks", []) or []
            for task in tasks:
                if _is_coverage_gap_task(task) and task.get("status") in ("planned", "pending", "running"):
                    golden_run_id = rid
                    golden_proj_id = r.get("project_id")
                    break
            if golden_run_id:
                break

    # Priority 3: reviewed_with_gaps / reviewed_partial with insufficient or partial products
    if not golden_run_id:
        for r in runs:
            rid = r.get("run_id", "")
            if not rid:
                continue
            report = get_json(f"/api/runs/{rid}/report", None) or {}
            if isinstance(report, dict):
                rs = report.get("report_status", "")
                qs = report.get("quality_summary") or {}
                if rs in ("reviewed_with_gaps", "reviewed_partial"):
                    if qs.get("insufficient_products", 0) > 0 or qs.get("partial_products", 0) > 0:
                        golden_run_id = rid
                        golden_proj_id = r.get("project_id")
                        break

    # Priority 4: failed run with any rework tasks (old fallback)
    if not golden_run_id:
        for r in runs:
            rid = r.get("run_id", "")
            if r.get("status") != "failed":
                continue
            tasks = get_json(f"/api/runs/{rid}/rework-tasks", []) or []
            if tasks:
                golden_run_id = rid
                golden_proj_id = r.get("project_id")
                break

    # Priority 5: failed run with pending interventions (old fallback)
    if not golden_run_id:
        for r in runs:
            rid = r.get("run_id", "")
            if r.get("status") != "failed":
                continue
            interventions = get_json(f"/api/runs/{rid}/human-interventions?status=pending", []) or []
            if interventions:
                golden_run_id = rid
                golden_proj_id = r.get("project_id")
                break

    if golden_run_id:
        st.session_state["selected_run_id"] = golden_run_id
        st.session_state["selected_project_id"] = golden_proj_id
        st.session_state["current_page_zh"] = "Project Workspace"
        st.rerun()
    else:
        st.sidebar.warning("No golden demo run found. Please run a new analysis or seed a demo run first.")
st.sidebar.caption("Jump to Project Workspace → Deliverables with the best golden demo run")

def _fmt_rate(v):
    if v is None:
        return "N/A"
    if isinstance(v, float) and 0 <= v <= 1:
        return f"{v:.1%}"
    return str(v)


# ---------------------------------------------------------------------------
# Conversational Intake Helpers
# ---------------------------------------------------------------------------

# Built-in competitor catalog
COMPETITOR_CATALOG = {
    "Dify": {
        "product_id": "Dify",
        "product_name": "Dify",
        "company_name": "Dify Technology Co., Ltd.",
        "official_website": "https://dify.ai",
        "seed_urls": ["https://dify.ai", "https://docs.dify.ai"],
    },
    "Flowise": {
        "product_id": "Flowise",
        "product_name": "Flowise",
        "company_name": "Flowise Inc.",
        "official_website": "https://flowiseai.com",
        "seed_urls": ["https://flowiseai.com"],
    },
    "Coze": {
        "product_id": "Coze",
        "product_name": "Coze",
        "company_name": "ByteDance",
        "official_website": "https://www.coze.com",
        "seed_urls": ["https://www.coze.com"],
    },
    "LangGraph": {
        "product_id": "LangGraph",
        "product_name": "LangGraph",
        "company_name": "LangChain",
        "official_website": "https://www.langchain.com/langgraph",
        "seed_urls": [
            "https://www.langchain.com/langgraph",
            "https://langchain-ai.github.io/langgraph/",
        ],
    },
    "CrewAI": {
        "product_id": "CrewAI",
        "product_name": "CrewAI",
        "company_name": "CrewAI",
        "official_website": "https://www.crewai.com",
        "seed_urls": ["https://www.crewai.com"],
    },
    "AutoGen": {
        "product_id": "AutoGen",
        "product_name": "AutoGen",
        "company_name": "Microsoft",
        "official_website": "https://microsoft.github.io/autogen/",
        "seed_urls": ["https://microsoft.github.io/autogen/"],
    },
    "FastGPT": {
        "product_id": "FastGPT",
        "product_name": "FastGPT",
        "company_name": "FastGPT",
        "official_website": "https://fastgpt.in",
        "seed_urls": ["https://fastgpt.in"],
    },
    "LangSmith": {
        "product_id": "LangSmith",
        "product_name": "LangSmith",
        "company_name": "LangChain",
        "official_website": "https://docs.langchain.com/langsmith",
        "seed_urls": ["https://docs.langchain.com/langsmith"],
    },
}

# Default recommended competitors for AI agent platforms
DEFAULT_AI_AGENT_COMPETITORS = ["Dify", "Flowise", "Coze", "LangGraph"]


def parse_intake_to_project_draft(user_request: str) -> dict:
    """
    Parse a natural language user request into a project draft.
    This is a rule-based parser, no LLM involved.

    Args:
        user_request: Natural language request from user.

    Returns:
        dict with project draft including project_name, task_type, target_region,
        description, products, analysis_dimensions, and research_plan.
    """
    req_lower = user_request.lower()

    # Determine region
    target_region = "global"
    if "china" in req_lower or "中国" in user_request:
        target_region = "china"
    elif " us " in req_lower or "united states" in req_lower or "美国" in user_request:
        target_region = "us"
    elif "europe" in req_lower or "欧洲" in user_request:
        target_region = "europe"
    elif "global" in req_lower or "全球" in user_request:
        target_region = "global"

    # Determine task_type (priority: compare > pricing > battlecard > customer_voice > default)
    task_type = "competitor_landscape"
    if any(kw in req_lower for kw in ["compare", "comparison", "vs ", "v/s ", "对比", "比较"]):
        task_type = "product_comparison"
    elif "pricing" in req_lower or "价格" in user_request or "定价" in user_request:
        task_type = "pricing_analysis"
    elif "battlecard" in req_lower:
        task_type = "sales_battlecard"
    elif "customer voice" in req_lower or "用户口碑" in user_request:
        task_type = "customer_voice"

    # Determine analysis dimensions
    default_dims = [
        "function_tree",
        "pricing_model",
        "user_persona",
        "customer_voice",
        "swot",
        "enterprise_readiness",
    ]
    selected_dims = list(default_dims)

    if "pricing" in req_lower or "价格" in user_request or "定价" in user_request:
        if "pricing_model" not in selected_dims:
            selected_dims.append("pricing_model")
    if "enterprise" in req_lower or "企业" in user_request or "私有化" in user_request or "sso" in req_lower or "rbac" in req_lower:
        if "enterprise_readiness" not in selected_dims:
            selected_dims.append("enterprise_readiness")
    if "user" in req_lower or "用户" in user_request or "persona" in req_lower:
        if "user_persona" not in selected_dims:
            selected_dims.append("user_persona")
    if "integration" in req_lower or "api" in req_lower or "plugin" in req_lower:
        if "integration_capabilities" not in selected_dims:
            selected_dims.append("integration_capabilities")

    # Keywords that suggest user wants competitor landscape analysis
    competitor_keywords = [
        "competitors", "competitor", "alternatives", "landscape",
        "竞品", "对手", "同类", "ai agent", "agent platform",
        "工作流平台", "llm", "genai", "生成式",
    ]
    # Keywords that suggest user only wants single product analysis (no auto-fill)
    single_product_keywords = [
        "only ", "only ", "just ", "just ", "只分析", "只看", "只要",
        "only dify", "only flowise", "只分析", "only analyze",
    ]

    # Extract products
    mentioned_products = []
    for product_key in COMPETITOR_CATALOG:
        if product_key.lower() in req_lower:
            mentioned_products.append(product_key)

    # Check if user explicitly wants single product only
    wants_single = any(kw in req_lower for kw in single_product_keywords)

    # If no explicit products mentioned
    if not mentioned_products:
        # Only auto-fill if request mentions competitor keywords
        if any(kw in req_lower for kw in competitor_keywords):
            mentioned_products = list(DEFAULT_AI_AGENT_COMPETITORS)
        else:
            # No products detected and no competitor keywords - needs clarification
            return {
                "project_name": "",
                "task_type": task_type,
                "target_region": target_region,
                "description": user_request,
                "products": [],
                "analysis_dimensions": selected_dims,
                "needs_clarification": True,
                "clarification_message": "未识别出明确竞品分析需求，请补充产品名称、行业或分析目标。",
            }
    elif len(mentioned_products) == 1 and not wants_single:
        # Single product mentioned - auto-fill if request suggests competitor analysis
        if any(kw in req_lower for kw in competitor_keywords):
            # Add default competitors, keeping user's product
            for competitor in DEFAULT_AI_AGENT_COMPETITORS:
                if competitor not in mentioned_products:
                    mentioned_products.append(competitor)

    # Build products list
    products = []
    for prod_key in mentioned_products:
        if prod_key in COMPETITOR_CATALOG:
            products.append(COMPETITOR_CATALOG[prod_key].copy())

    # Generate project name
    if len(mentioned_products) == 1:
        proj_name = f"{mentioned_products[0]} Competitive Analysis"
    elif len(mentioned_products) <= 3:
        proj_name = " vs ".join(mentioned_products) + " Comparison"
    else:
        proj_name = f"{mentioned_products[0]} & {len(mentioned_products) - 1} Others Comparison"

    draft = {
        "project_name": proj_name,
        "task_type": task_type,
        "target_region": target_region,
        "description": user_request,
        "products": products,
        "analysis_dimensions": selected_dims,
    }

    return draft


def generate_research_plan(draft: dict) -> dict:
    """
    Generate a research plan based on the project draft.
    This is a rule-based generator, no LLM involved.

    Args:
        draft: Project draft dictionary from parse_intake_to_project_draft.

    Returns:
        dict with research_plan containing objective, competitor_selection_rationale,
        source_plan, schema_plan, workflow_plan, and risk_notes.
    """
    products = draft.get("products", [])
    dims = draft.get("analysis_dimensions", [])
    region = draft.get("target_region", "global")
    task_type = draft.get("task_type", "competitor_landscape")

    product_names = [p.get("product_name", "") for p in products]
    num_products = len(products)

    # Objective
    task_labels = {
        "competitor_landscape": "competitor landscape analysis",
        "product_comparison": "product comparison",
        "pricing_analysis": "pricing analysis",
        "sales_battlecard": "sales battlecard generation",
        "customer_voice": "customer voice analysis",
    }
    objective = f"Conduct a comprehensive {task_labels.get(task_type, 'analysis')} for {num_products} product(s): {', '.join(product_names)}. Focus on {region} market."

    # Competitor selection rationale
    competitor_selection_rationale = (
        f"Selected {num_products} products based on user request: {', '.join(product_names)}. "
        f"These represent key players in the AI agent workflow platform space, "
        f"covering different positioning (open-source, enterprise, startup) and geographic focus."
    )

    # Source plan
    source_plan = [
        {
            "step": 1,
            "phase": "Discovery",
            "action": "Scrape official websites",
            "targets": [p.get("official_website", "") for p in products if p.get("official_website")],
            "purpose": "Collect product overviews, feature lists, pricing pages",
        },
        {
            "step": 2,
            "phase": "Discovery",
            "action": "Seed URL crawling",
            "targets": [url for p in products for url in p.get("seed_urls", [])],
            "purpose": "Deep dive into documentation and blog posts",
        },
        {
            "step": 3,
            "phase": "Market Intelligence",
            "action": "Search for reviews and comparisons",
            "targets": ["G2", "Capterra", "Product Hunt", "GitHub"],
            "purpose": "Customer voice, ratings, and community feedback",
        },
        {
            "step": 4,
            "phase": "Market Intelligence",
            "action": "Search for news and press coverage",
            "targets": ["TechCrunch", "VentureBeat", "LinkedIn", "X (Twitter)"],
            "purpose": "Market positioning, funding, and strategic updates",
        },
    ]

    # Schema plan based on dimensions
    schema_plan = []
    dim_labels = {
        "function_tree": ("Function & Feature Comparison", ["core_features", "workflow_capabilities", "integration_support"]),
        "pricing_model": ("Pricing Analysis", ["pricing_tiers", "free_tier", "enterprise_pricing", "cost_per_token"]),
        "user_persona": ("User Persona Analysis", ["target_users", "use_cases", "skill_requirements"]),
        "customer_voice": ("Customer Voice", ["review_summary", "pros_cons", "rating_summary"]),
        "swot": ("SWOT Analysis", ["strengths", "weaknesses", "opportunities", "threats"]),
        "enterprise_readiness": ("Enterprise Readiness", ["sso_support", "rbac", "private_deployment", "compliance", "sla"]),
        "market_positioning": ("Market Positioning", ["target_segment", "competitive_advantage", "market_share"]),
        "integration_capabilities": ("Integration Capabilities", ["api_coverage", "plugin_ecosystem", "webhook_support"]),
    }

    for dim in dims:
        if dim in dim_labels:
            label, keys = dim_labels[dim]
            schema_plan.append({
                "dimension": dim,
                "label": label,
                "schema_keys": keys,
            })

    # Workflow plan
    workflow_plan = [
        {
            "phase": "Plan",
            "nodes": ["RequirementParser", "CompetitorSelector", "SourcePlanner"],
            "description": "Parse requirements and plan the research approach",
        },
        {
            "phase": "Source Collection",
            "nodes": ["WebScraper", "DocumentParser", "SeedCrawler"],
            "description": "Collect raw data from various sources",
        },
        {
            "phase": "Extraction",
            "nodes": ["EvidenceExtractor", "SchemaClassifier", "PIICleaner"],
            "description": "Extract structured evidence and classify by schema",
        },
        {
            "phase": "Review",
            "nodes": ["QualityChecker", "HumanReviewer"],
            "description": "Quality assurance and human review for key findings",
        },
        {
            "phase": "Synthesis",
            "nodes": ["FactAggregator", "ClaimGenerator", "ReportRenderer"],
            "description": "Synthesize facts into claims and generate report",
        },
    ]

    # Risk notes
    risk_notes = [
        "Some websites may block scraping. Fall back to cached sources or manual entry.",
        "Pricing information may be outdated. Mark evidence confidence accordingly.",
        "Enterprise features may require direct outreach to verify claims.",
        "Multi-language content (e.g., Chinese product docs) may need translation.",
        f"Region focus ({region}) may limit available sources if products have regional concentration.",
    ]

    # Add dimension-specific risks
    if "pricing_model" in dims:
        risk_notes.append("Pricing data from public sources may not reflect negotiated enterprise deals.")
    if "enterprise_readiness" in dims:
        risk_notes.append("Enterprise features often require sales contact to verify detailed capabilities.")
    if "customer_voice" in dims and region == "china":
        risk_notes.append("Chinese platform reviews on global sites (G2, Capterra) may be limited.")

    research_plan = {
        "objective": objective,
        "competitor_selection_rationale": competitor_selection_rationale,
        "source_plan": source_plan,
        "schema_plan": schema_plan,
        "workflow_plan": workflow_plan,
        "risk_notes": risk_notes,
    }

    return research_plan


# ---------------------------------------------------------------------------
# Reusable Workflow & Human Intervention Renderers
# ---------------------------------------------------------------------------

def render_workflow_status(run_id: str, compact: bool = False):
    """
    Render workflow status for a given run.
    
    Args:
        run_id: The run ID to display workflow status for.
        compact: If True, show only summary + short node table.
                 If False, show summary + nodes table + expandable node details.
    """
    workflow_data = get_json(f"/api/runs/{run_id}/workflow", None)
    
    if not workflow_data:
        st.info("Workflow data not available for this run.")
        return None
    
    summary = workflow_data.get("summary", {})
    
    # Summary metrics
    sc1, sc2, sc3, sc4, sc5, sc6, sc7 = st.columns(7)
    with sc1:
        st.metric("Total", summary.get("total_nodes", 0))
    with sc2:
        st.metric("Completed", summary.get("completed", 0))
    with sc3:
        st.metric("Running", summary.get("running", 0))
    with sc4:
        st.metric("Paused", summary.get("paused", 0))
    with sc5:
        st.metric("Failed", summary.get("failed", 0))
    with sc6:
        st.metric("Pending", summary.get("pending", 0))
    with sc7:
        has_review = summary.get("has_human_review", False)
        st.metric("Human Review", "Required" if has_review else "None")
    
    if summary.get("has_human_review", False):
        st.warning("Human review is required for this run. Please check the Human Interventions section below.")
    
    nodes = workflow_data.get("nodes", [])
    
    if not nodes:
        return summary
    
    st.subheader("Workflow Nodes")
    
    # Build node rows with clean status display (no Streamlit markdown in dataframe)
    node_rows = []
    for n in nodes:
        status = n.get("status", "pending")
        icon = _workflow_node_icon(status)
        started = (n.get("started_at") or "")[:16].replace("T", " ") if n.get("started_at") else "—"
        completed = (n.get("completed_at") or "")[:16].replace("T", " ") if n.get("completed_at") else "—"
        latency = n.get("latency_ms")
        latency_str = f"{latency}ms" if latency else "—"
        error = n.get("error_message", "")
        error_short = (error[:40] + "...") if error and len(error) > 40 else error
        
        node_rows.append({
            "Node": n.get("node_name", ""),
            "Status Icon": icon,
            "Status Label": status,
            "Latency": latency_str,
            "Started": started,
            "Completed": completed,
            "Error": error_short or "—",
        })
    
    # Use data_editor for better display
    st.dataframe(node_rows, width="stretch", hide_index=True, height=300)
    
    # Only show expandable node details in non-compact mode
    if not compact:
        with st.expander("Node Details (expandable)"):
            for n in nodes:
                status = n.get("status", "pending")
                color = _workflow_node_color(status)
                with st.expander(f"`{n.get('node_name', '')}` - {status}"):
                    st.text(f"Node ID: {n.get('node_id', 'N/A')}")
                    st.text(f"Node Type: {n.get('node_type', 'N/A')}")
                    st.text(f"Status: {status}")
                    started = (n.get("started_at") or "—")[:19].replace("T", " ")
                    st.text(f"Started: {started}")
                    completed = (n.get("completed_at") or "—")[:19].replace("T", " ")
                    st.text(f"Completed: {completed}")
                    latency = n.get("latency_ms")
                    st.text(f"Latency: {latency}ms" if latency else "Latency: —")
                    if n.get("error_message"):
                        st.error(f"Error: {n.get('error_message')}")
                    if n.get("input_summary"):
                        with st.expander("Input Summary"):
                            st.json(n.get("input_summary"))
                    if n.get("output_summary"):
                        with st.expander("Output Summary"):
                            st.json(n.get("output_summary"))
    
    return summary


def get_run_blocked_info(run_id: str) -> dict:
    """Return blocked status info for a run. Returns a dict with is_blocked, error_message, status, current_node."""
    if not run_id:
        return {"is_blocked": False, "error_message": "", "status": "unknown", "current_node": ""}
    try:
        resp = requests.get(f"{API_BASE}/api/runs/{run_id}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "unknown")
            error_msg = data.get("error_message") or ""
            return {
                "is_blocked": status == "failed" and "block" in error_msg.lower(),
                "error_message": error_msg,
                "status": status,
                "current_node": data.get("current_node", ""),
            }
    except Exception:
        pass
    return {"is_blocked": False, "error_message": "", "status": "unknown", "current_node": ""}


def get_pending_review_count(run_id: str) -> tuple[int, str]:
    """
    Return (count, source) where source is one of:
      - "blocked_report_fallback": run failed with blocked report, no DB intervention
      - "human_intervention": count from DB interventions
    """
    if not run_id:
        return 0, "human_intervention"
    # Fetch pending interventions from DB
    pending_count = 0
    try:
        resp = requests.get(f"{API_BASE}/api/runs/{run_id}/human-interventions?status=pending", timeout=10)
        if resp.status_code == 200:
            pending_count = len(resp.json())
    except Exception:
        pass
    # Check if this is a blocked report with no intervention
    blocked_info = get_run_blocked_info(run_id)
    if blocked_info["is_blocked"] and pending_count == 0:
        return 1, "blocked_report_fallback"
    return pending_count, "human_intervention"


_REASON_CODE_MAP = {
    "MISSING_EVIDENCE": "Missing evidence — some claims or report spans do not have sufficient evidence support.",
    "UNSUPPORTED_REPORT_SPAN": "Unsupported report span — some report paragraphs are not backed by signed claims.",
    "BLOCKED_NO_SIGNED_CLAIMS": "No signed claims — the report cannot be published without reviewed claims.",
    "PII_NOT_MASKED": "PII risk — sensitive information must be masked before publishing.",
}


def format_reason_code(reason_code: str) -> str:
    """Convert a raw reason_code into a human-readable explanation."""
    return _REASON_CODE_MAP.get(reason_code, reason_code)


def render_review_center_action_row(run_id: str, key_prefix: str):
    """Action row for Review Center — links to related views."""
    st.markdown("##### Actions")
    action_cols = st.columns(3)
    with action_cols[0]:
        if st.button("View Claims", key=f"{key_prefix}_view_claims_{run_id}", use_container_width=True):
            goto_page("Knowledge Table")
        st.caption("Browse all collected claims")
    with action_cols[1]:
        if st.button("View Evidence", key=f"{key_prefix}_view_evidence_{run_id}", use_container_width=True):
            goto_page("Evidence Hub")
        st.caption("Browse evidence appendix")
    with action_cols[2]:
        if st.button("Generate Partial Deliverable", key=f"{key_prefix}_partial_{run_id}", use_container_width=True):
            st.session_state["af_stage"] = "deliverables"
            goto_page("Analysis Flow")
        st.caption("View available outputs despite blockage")


def render_human_interventions(run_id: str, compact: bool = False):
    """
    Render human interventions for a given run.
    
    Args:
        run_id: The run ID to display interventions for.
        compact: If True, show only pending intervention summary + action buttons.
                 If False, show pending interventions with full details + all interventions.
    """
    st.subheader("Human Interventions")
    
    interventions = get_json(f"/api/runs/{run_id}/human-interventions?status=pending", [])
    
    if not interventions:
        st.success(f"No pending human interventions for run `{run_id[:16]}...`. All interventions have been resolved.")
        if compact:
            st.caption(
                "Pending interventions are scoped to the selected run. "
                "If you expected an item here, switch Active Run or open the standalone Human Review page with that run ID."
            )
    else:
        st.warning(f"Found {len(interventions)} pending intervention(s). Action required.")
        
        for interv in interventions:
            _render_intervention_card(interv, compact)
            st.divider()
    
    # Always show expander in compact mode to view all interventions
    if compact:
        with st.expander("View all interventions for this run"):
            all_interventions = get_json(f"/api/runs/{run_id}/human-interventions", [])
            if not all_interventions:
                st.info("No interventions found for this run.")
            else:
                st.text(f"Total: {len(all_interventions)} intervention(s)")
                
                # Summary by status
                status_counts = {}
                for i in all_interventions:
                    s = i.get("status", "unknown")
                    status_counts[s] = status_counts.get(s, 0) + 1
                st.text(f"Status: {', '.join(f'{k}={v}' for k, v in status_counts.items())}")
                
                for interv in all_interventions:
                    status = interv.get("status", "unknown")
                    status_icon = "⚠️" if status == "pending" else ("✅" if status == "resolved" else "⚪")
                    with st.expander(
                        f"{status_icon} `{interv.get('intervention_id', '')}` - "
                        f"action: {interv.get('action', 'pending')} - "
                        f"status: {status}"
                    ):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.text(f"Node: {interv.get('node_name', 'N/A')}")
                            st.text(f"Artifact: {interv.get('artifact_type', 'N/A')} / {interv.get('artifact_id', 'N/A')}")
                            st.text(f"Action: {interv.get('action', 'N/A')}")
                            st.text(f"Status: {status}")
                        with col2:
                            st.text(f"Created: {interv.get('created_at', 'N/A')}")
                            if interv.get("resolved_at"):
                                st.text(f"Resolved: {interv.get('resolved_at')}")
                            if interv.get("resolved_by"):
                                st.text(f"Resolved By: {interv.get('resolved_by')}")
                        if interv.get("comment"):
                            st.text(f"Comment: {interv.get('comment')}")
                        if interv.get("before_json"):
                            with st.expander("Before JSON"):
                                st.json(interv.get("before_json"))
                        if interv.get("after_json"):
                            with st.expander("After JSON"):
                                st.json(interv.get("after_json"))
    else:
        # Non-compact mode: show all interventions in expander
        with st.expander("View All Interventions (including resolved/cancelled)"):
            all_interventions = get_json(f"/api/runs/{run_id}/human-interventions", [])
            if not all_interventions:
                st.info("No interventions found for this run.")
            else:
                st.text(f"Total: {len(all_interventions)} intervention(s)")
                
                # Summary by status
                status_counts = {}
                for i in all_interventions:
                    s = i.get("status", "unknown")
                    status_counts[s] = status_counts.get(s, 0) + 1
                st.text(f"Status: {', '.join(f'{k}={v}' for k, v in status_counts.items())}")
                
                for interv in all_interventions:
                    status = interv.get("status", "unknown")
                    status_icon = "⚠️" if status == "pending" else ("✅" if status == "resolved" else "⚪")
                    with st.expander(
                        f"{status_icon} `{interv.get('intervention_id', '')}` - "
                        f"action: {interv.get('action', 'pending')} - "
                        f"status: {status}"
                    ):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.text(f"Node: {interv.get('node_name', 'N/A')}")
                            st.text(f"Artifact: {interv.get('artifact_type', 'N/A')} / {interv.get('artifact_id', 'N/A')}")
                            st.text(f"Action: {interv.get('action', 'N/A')}")
                            st.text(f"Status: {status}")
                        with col2:
                            st.text(f"Created: {interv.get('created_at', 'N/A')}")
                            if interv.get("resolved_at"):
                                st.text(f"Resolved: {interv.get('resolved_at')}")
                            if interv.get("resolved_by"):
                                st.text(f"Resolved By: {interv.get('resolved_by')}")
                        if interv.get("comment"):
                            st.text(f"Comment: {interv.get('comment')}")
                        if interv.get("before_json"):
                            with st.expander("Before JSON"):
                                st.json(interv.get("before_json"))
                        if interv.get("after_json"):
                            with st.expander("After JSON"):
                                st.json(interv.get("after_json"))


def _render_intervention_card(interv: dict, compact: bool = False):
    """
    Render a single intervention card with action buttons.
    
    Args:
        interv: Intervention data dictionary.
        compact: If True, use a more compact layout.
    """
    interv_id = interv.get("intervention_id", "")
    node_name = interv.get("node_name", "unknown")
    artifact_type = interv.get("artifact_type", "general")
    artifact_id = interv.get("artifact_id", "")
    comment = interv.get("comment", "")
    before_json = interv.get("before_json", {})
    created_at = (interv.get("created_at") or "")[:19].replace("T", " ")
    
    with st.container():
        if compact:
            # Compact layout for Project Workspace
            bc1, bc2, bc3 = st.columns([3, 1, 1])
            with bc1:
                st.markdown(f"**`{interv_id}`** at `{node_name}`")
                st.text(f"Artifact: {artifact_type} / {artifact_id}")
                if comment:
                    st.text(f"Comment: {comment[:80]}{'...' if len(comment) > 80 else ''}")
            with bc2:
                if before_json:
                    with st.expander("Before"):
                        st.json(before_json)
            with bc3:
                st.markdown("**Actions**")
                action_cols = st.columns(2)
                with action_cols[0]:
                    if st.button("Approve", key=f"compact_approve_{interv_id}", type="primary"):
                        with st.spinner("Approving..."):
                            result = post_json(
                                f"/api/human-interventions/{interv_id}/approve",
                                {"comment": "Approved by user", "resolved_by": "frontend_user"}
                            )
                            if result and result.get("status") == "resolved":
                                st.success(f"Approved {interv_id}.")
                                st.rerun()
                            else:
                                st.error("Failed to approve.")
                with action_cols[1]:
                    if st.button("Reject", key=f"compact_reject_{interv_id}"):
                        with st.spinner("Rejecting..."):
                            result = post_json(
                                f"/api/human-interventions/{interv_id}/reject",
                                {"comment": "Rejected by user", "resolved_by": "frontend_user"}
                            )
                            if result and result.get("status") == "resolved":
                                st.success(f"Rejected {interv_id}.")
                                st.rerun()
                            else:
                                st.error("Failed to reject.")
                
                # Respond/Edit in expander for compact mode
                with st.expander("Respond / Edit"):
                    st.session_state.setdefault(f"compact_respond_{interv_id}", False)
                    st.session_state.setdefault(f"compact_edit_{interv_id}", False)
                    
                    r_col, e_col = st.columns(2)
                    with r_col:
                        if st.button("Respond", key=f"compact_respond_btn_{interv_id}", use_container_width=True):
                            st.session_state[f"compact_respond_{interv_id}"] = not st.session_state[f"compact_respond_{interv_id}"]
                        if st.session_state.get(f"compact_respond_{interv_id}", False):
                            respond_comment = st.text_area(
                                "Response", key=f"compact_respond_text_{interv_id}", height=60
                            )
                            if st.button("Submit", key=f"compact_submit_respond_{interv_id}"):
                                if not respond_comment:
                                    st.warning("Enter a comment.")
                                else:
                                    result = post_json(
                                        f"/api/human-interventions/{interv_id}/respond",
                                        {"comment": respond_comment, "resolved_by": "frontend_user", "after_json": {}}
                                    )
                                    if result and result.get("status") == "resolved":
                                        st.success("Response submitted.")
                                        st.session_state[f"compact_respond_{interv_id}"] = False
                                        st.rerun()
                    with e_col:
                        if st.button("Edit", key=f"compact_edit_btn_{interv_id}", use_container_width=True):
                            st.session_state[f"compact_edit_{interv_id}"] = not st.session_state[f"compact_edit_{interv_id}"]
                        if st.session_state.get(f"compact_edit_{interv_id}", False):
                            edit_json_str = st.text_area(
                                "JSON", key=f"compact_edit_json_{interv_id}", height=60
                            )
                            edit_comment = st.text_area(
                                "Comment", key=f"compact_edit_comment_{interv_id}", height=60
                            )
                            if st.button("Submit", key=f"compact_submit_edit_{interv_id}"):
                                if not edit_json_str:
                                    st.warning("Enter JSON.")
                                else:
                                    try:
                                        after_json = json.loads(edit_json_str)
                                        result = post_json(
                                            f"/api/human-interventions/{interv_id}/edit",
                                            {"after_json": after_json, "comment": edit_comment, "resolved_by": "frontend_user"}
                                        )
                                        if result and result.get("status") == "resolved":
                                            st.success("Edit submitted.")
                                            st.session_state[f"compact_edit_{interv_id}"] = False
                                            st.rerun()
                                    except json.JSONDecodeError:
                                        st.error("Invalid JSON.")
        else:
            # Full layout for DAG and Human Review pages
            bc1, bc2, bc3, bc4 = st.columns([2, 1, 1, 1])
            with bc1:
                st.markdown(f"**Intervention: `{interv_id}`**")
                st.text(f"Node: {node_name} | Type: {artifact_type} | Artifact: {artifact_id}")
                st.text(f"Created: {created_at}")
                # Parse and display human-readable reason codes from comment/before_json
                reason_codes_in_comment = []
                if comment:
                    import re
                    for m in re.finditer(r"\[(\w+)\]", comment):
                        reason_codes_in_comment.append(m.group(1))
                reason_codes_in_bj = before_json.get("reason_codes", []) if before_json else []
                all_reason_codes = reason_codes_in_comment or reason_codes_in_bj
                if all_reason_codes:
                    for rc in all_reason_codes:
                        st.markdown(f":orange[**{format_reason_code(rc)}**]")
                if comment:
                    st.text(f"Comment: {comment[:120]}{'...' if len(comment) > 120 else ''}")
                # Show rework info from after_json
                after_json = interv.get("after_json") or {}
                if isinstance(after_json, str):
                    try:
                        after_json = json.loads(after_json)
                    except Exception:
                        after_json = {}
                if after_json.get("rework_requested"):
                    rw_id = after_json.get("rework_id", "")
                    rw_at = after_json.get("requested_at", "")
                    rw_note = after_json.get("note", "")
                    st.markdown(f":orange[**Rework requested:** `{rw_id}`]")
                    if rw_at:
                        st.text(f"  Requested at: {rw_at[:19].replace('T', ' ')}")
                    if rw_note:
                        st.text(f"  {rw_note}")
            with bc2:
                st.markdown("**Before JSON**")
                if before_json:
                    with st.expander("View details"):
                        st.json(before_json)
                else:
                    st.text("—")
            with bc3:
                st.markdown("**Actions**")
                action_col1, action_col2 = st.columns(2)
                with action_col1:
                    if st.button("Approve", key=f"full_approve_{interv_id}", type="primary"):
                        with st.spinner("Approving..."):
                            result = post_json(
                                f"/api/human-interventions/{interv_id}/approve",
                                {"comment": "Approved by user", "resolved_by": "frontend_user"}
                            )
                            if result and result.get("status") == "resolved":
                                st.success(f"Intervention {interv_id} approved.")
                                st.rerun()
                            else:
                                st.error("Failed to approve intervention.")
                with action_col2:
                    if st.button("Reject", key=f"full_reject_{interv_id}"):
                        with st.spinner("Rejecting..."):
                            result = post_json(
                                f"/api/human-interventions/{interv_id}/reject",
                                {"comment": "Rejected by user", "resolved_by": "frontend_user"}
                            )
                            if result and result.get("status") == "resolved":
                                st.success(f"Intervention {interv_id} rejected.")
                                st.rerun()
                            else:
                                st.error("Failed to reject intervention.")
                action_col3, action_col4 = st.columns(2)
                with action_col3:
                    if st.button("Respond", key=f"full_respond_{interv_id}"):
                        st.session_state[f"show_respond_{interv_id}"] = True
                with action_col4:
                    if st.button("Edit", key=f"full_edit_{interv_id}"):
                        st.session_state[f"show_edit_{interv_id}"] = True

                # Show rework info in after_json if rework was already requested
                if before_json and isinstance(before_json, dict):
                    rework_info = before_json.get("rework_requested") or {}
                    if isinstance(rework_info, dict) and rework_info.get("rework_id"):
                        st.success(f"Rework requested: `{rework_info['rework_id']}`")
                elif isinstance(before_json, dict) and before_json.get("rework_id"):
                    st.success(f"Rework requested: `{before_json['rework_id']}`")

                # Request Rework button — only for pending interventions
                interv_status = interv.get("status", "")
                if interv_status == "pending":
                    if st.button("Request Rework", key=f"full_rework_{interv_id}", use_container_width=True):
                        with st.spinner("Creating rework task..."):
                            result = post_json(
                                f"/api/human-interventions/{interv_id}/request-rework",
                                {"comment": "Request rework from Review Center", "requested_by": "frontend_user"}
                            )
                            if result and result.get("rework_id"):
                                st.success(f"Rework task created: `{result['rework_id']}`")
                                st.rerun()
                            else:
                                st.error(f"Failed: {result}")
                else:
                    st.button("Request Rework", key=f"full_rework_{interv_id}", use_container_width=True, disabled=True)
                    st.caption(f"Status: {interv_status}")
            
            with bc4:
                st.markdown("**Response/Edit**")
                # Respond form
                if st.session_state.get(f"show_respond_{interv_id}", False):
                    respond_comment = st.text_area(
                        "Response comment",
                        key=f"respond_comment_{interv_id}",
                        placeholder="Enter your response...",
                        height=80
                    )
                    respond_col1, respond_col2 = st.columns(2)
                    with respond_col1:
                        if st.button("Submit Response", key=f"submit_respond_{interv_id}"):
                            if not respond_comment:
                                st.warning("Please enter a comment.")
                            else:
                                with st.spinner("Submitting response..."):
                                    result = post_json(
                                        f"/api/human-interventions/{interv_id}/respond",
                                        {"comment": respond_comment, "resolved_by": "frontend_user", "after_json": {}}
                                    )
                                    if result and result.get("status") == "resolved":
                                        st.success(f"Response submitted for {interv_id}.")
                                        st.session_state[f"show_respond_{interv_id}"] = False
                                        st.rerun()
                                    else:
                                        st.error("Failed to submit response.")
                    with respond_col2:
                        if st.button("Cancel", key=f"cancel_respond_{interv_id}"):
                            st.session_state[f"show_respond_{interv_id}"] = False
                            st.rerun()
                
                # Edit form
                if st.session_state.get(f"show_edit_{interv_id}", False):
                    edit_json_str = st.text_area(
                        "Edit JSON (after_json)",
                        key=f"edit_json_{interv_id}",
                        placeholder='{"field": "new_value"}',
                        height=80
                    )
                    edit_comment = st.text_area(
                        "Edit comment",
                        key=f"edit_comment_{interv_id}",
                        placeholder="Describe your edit...",
                        height=80
                    )
                    edit_col1, edit_col2 = st.columns(2)
                    with edit_col1:
                        if st.button("Submit Edit", key=f"submit_edit_{interv_id}"):
                            if not edit_json_str:
                                st.warning("Please enter JSON data.")
                            else:
                                try:
                                    after_json = json.loads(edit_json_str)
                                    with st.spinner("Submitting edit..."):
                                        result = post_json(
                                            f"/api/human-interventions/{interv_id}/edit",
                                            {"after_json": after_json, "comment": edit_comment, "resolved_by": "frontend_user"}
                                        )
                                        if result and result.get("status") == "resolved":
                                            st.success(f"Edit submitted for {interv_id}.")
                                            st.session_state[f"show_edit_{interv_id}"] = False
                                            st.rerun()
                                        else:
                                            st.error("Failed to submit edit.")
                                except json.JSONDecodeError:
                                    st.error("Invalid JSON format. Please check your input.")
                    with edit_col2:
                        if st.button("Cancel", key=f"cancel_edit_{interv_id}"):
                            st.session_state[f"show_edit_{interv_id}"] = False
                            st.rerun()
        
        st.caption("Note: This action records the human decision. Resume/re-run is not implemented in this version.")


# ---------------------------------------------------------------------------
# Run Info Banner
# ---------------------------------------------------------------------------

def render_run_banner(rid: str):
    run = get_json(f"/api/runs/{rid}", {})
    if not run:
        st.warning(f"未找到 Run `{rid}`，请到「任务设置」页面创建。")
        return
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown("**Run ID**")
        st.code(rid, language=None)
    with c2:
        title = run.get("task_title", "—")
        st.markdown("**任务**")
        st.text(title[:25] + ("…" if len(title) > 25 else ""), help=title)
    with c3:
        st.markdown("**模式**")
        st.write(_badge(run.get("mode", "unknown")))
    with c4:
        st.markdown("**状态**")
        st.write(_badge(run.get("status", "unknown")))
    with c5:
        ts = run.get("created_at", "—")
        ts = ts[:16].replace("T", " ") if ts and ts != "—" else "—"
        st.markdown("**创建时间**")
        st.text(ts)

    # Show contextually complementary navigation button in the banner.
    # - On Review Center: show "⚙ Running Center" to go back to running view.
    # - Everywhere else: show "🏛 Review Center".
    current = st.session_state.get("current_page_zh", "")
    if current == "Review Center":
        if st.button("⚙ Running Center", key=f"banner_rc_{rid}", help="返回运行中心查看执行进度"):
            st.session_state["af_stage"] = "running"
            st.session_state["current_page_zh"] = "Analysis Flow"
            st.rerun()
    else:
        if st.button("🏛 Review Center", key=f"banner_rc_{rid}", help="跳转人工审核与打回"):
            st.session_state["current_page_zh"] = "Review Center"
            st.rerun()


# ---------------------------------------------------------------------------
# Page: AnalysisFlow - Main business flow (default entry)
# ---------------------------------------------------------------------------

if page == "AnalysisFlow":
    st.markdown("🟢 **DEBUG: app.py AnalysisFlow loaded - 2026-05-28 14:47**")
    st.header("新建竞品分析")

    def af_reset():
        """Reset all AnalysisFlow session state for a new task."""
        # Core flow state
        st.session_state["af_stage"] = "intake"

        # Intake state
        st.session_state["af_intake_draft"] = None
        st.session_state["af_intake_products_df"] = None
        st.session_state["af_intake_dims"] = None
        st.session_state["intake_user_request"] = ""
        st.session_state["intake_project_draft"] = None
        st.session_state["intake_products_df"] = None
        st.session_state["intake_selected_dimensions"] = None
        st.session_state["intake_generated"] = False

        # Edit state
        st.session_state["edit_proj_name"] = ""
        st.session_state["edit_task_type"] = "competitor_landscape"
        st.session_state["edit_region"] = "global"
        st.session_state["edit_description"] = ""

        # Research Plan state
        st.session_state["rp_plan_id"] = None
        st.session_state["rp_plan_data"] = None
        st.session_state["rp_dag_data"] = None
        st.session_state["rp_confirmed_dag_id"] = None
        st.session_state["af_plan_confirmed"] = False
        st.session_state["rp_edit_mode"] = False

        # Project selection state
        st.session_state["selected_project_id"] = None
        st.session_state["selected_run_id"] = None

        # Clear editor keys
        if "products_editor" in st.session_state:
            del st.session_state["products_editor"]
        for dim in ["function_tree", "pricing_model", "user_persona",
                    "customer_voice", "swot", "enterprise_readiness",
                    "market_positioning", "integration_capabilities"]:
            key = f"intake_dim_{dim}"
            if key in st.session_state:
                del st.session_state[key]

    # New Task button in top right
    col_title, col_new_task = st.columns([4, 1])
    with col_title:
        st.caption("基于自然语言输入的智能竞品调研流程")
    with col_new_task:
        if st.button("+ New Task", use_container_width=True):
            af_reset()
            st.rerun()

    stage = st.session_state.get("af_stage", "intake")

    # -------------------------------------------------------------------------
    # Stage 1: Intake — Conversational intake form
    # -------------------------------------------------------------------------
    if stage == "intake":
        st.subheader("描述你的竞品分析任务")

        intake_col1, intake_col2 = st.columns([3, 1])
        with intake_col1:
            user_request = st.text_area(
                "你的需求",
                placeholder="例如：Compare Dify and Flowise for AI agent workflow platforms, focusing on pricing and enterprise readiness.\n\n或者：帮我分析几个 AI Agent 平台的竞品情况",
                height=100,
                key="intake_request_input",
                value=st.session_state.get("intake_user_request", ""),
            )
        with intake_col2:
            st.markdown("")
            st.markdown("")
            col_gen, col_clear = st.columns(2)
            with col_gen:
                generate_clicked = st.button("生成调研方案", type="primary", use_container_width=True)
            with col_clear:
                clear_clicked = st.button("清空", use_container_width=True)

        if clear_clicked:
            af_reset()
            st.rerun()

        if generate_clicked and user_request.strip():
            st.session_state["intake_user_request"] = user_request.strip()
            
            # Call backend API for plan generation (vNext-R1)
            try:
                resp = requests.post(
                    f"{API_BASE}/api/research-plans/generate",
                    json={
                        "user_query": user_request.strip(),
                        "schema_type": "ai_agent_platform",
                        "target_region": "global",
                        "mode": "review",
                    },
                    timeout=60,
                )
                if resp.status_code >= 400:
                    st.error(f"Plan generation failed: HTTP {resp.status_code} - {resp.text}")
                    st.stop()
                result = resp.json()
                research_plan = result.get("research_plan", {})
                generated_by = result.get("generated_by", "unknown")
                
                # Store plan info for potential edit
                st.session_state["rp_plan_id"] = result.get("research_plan_id")
                st.session_state["rp_plan_data"] = research_plan
                
            except Exception as exc:
                st.error(f"Plan generation failed: {exc}")
                st.stop()
            
            # Create draft from plan
            draft = {
                "project_name": research_plan.get("task_brief", {}).get("project_name", ""),
                "task_type": research_plan.get("schema_type", "competitor_landscape"),
                "target_region": research_plan.get("target_region", "global"),
                "description": research_plan.get("task_brief", {}).get("business_goal", ""),
                "user_query": user_request.strip(),
                "research_plan": research_plan,
                "plan_generated_by": generated_by,
            }
            
            # Extract competitors from plan
            competitors = research_plan.get("competitors", [])
            if competitors:
                import pandas as pd
                products_for_df = []
                for p in competitors:
                    if isinstance(p, dict):
                        seed_urls = p.get("seed_urls", [])
                        seed_urls_str = "\n".join(seed_urls) if isinstance(seed_urls, list) else str(seed_urls or "")
                        products_for_df.append({
                            "product_name": p.get("name", ""),
                            "company_name": p.get("company_name", ""),
                            "official_website": p.get("official_url", ""),
                            "seed_urls": seed_urls_str,
                        })
                st.session_state["intake_products_df"] = pd.DataFrame(products_for_df)
            else:
                st.session_state["intake_products_df"] = None
            
            # Extract dimensions from plan
            dims = research_plan.get("analysis_dimensions", [])
            if dims:
                selected_dims = [d.get("dimension_id") for d in dims if isinstance(d, dict)]
            else:
                selected_dims = ["function_tree", "pricing_model", "enterprise_readiness"]
            st.session_state["intake_selected_dimensions"] = selected_dims
            
            draft["products"] = competitors
            draft["analysis_dimensions"] = selected_dims
            
            st.session_state["intake_project_draft"] = draft
            st.session_state["intake_generated"] = True
            st.session_state["af_stage"] = "plan_review"
            st.rerun()

    # -------------------------------------------------------------------------
    # Stage 2: Plan Review — Preview, edit, and confirm
    # -------------------------------------------------------------------------
    elif stage == "plan_review":
        if not st.session_state.get("intake_project_draft"):
            st.warning("未找到调研方案，请重新描述需求。")
            if st.button("重新开始"):
                af_reset()
                st.rerun()
        else:
            draft = st.session_state["intake_project_draft"]
            
            # Block if draft needs clarification
            if draft.get("needs_clarification"):
                st.warning(draft.get("clarification_message", "请补充更多需求信息。"))
                if st.button("重新描述需求"):
                    af_reset()
                    st.rerun()
                st.stop()
            
            research_plan = draft.get("research_plan", {})

            st.divider()
            st.subheader("调研方案预览")

            # --- Section 1: Project Basics ---
            with st.expander("1. 项目基本信息", expanded=True):
                proj_name_edit = st.text_input(
                    "项目名称",
                    value=draft.get("project_name", ""),
                    key="edit_proj_name",
                )

                task_type_edit = st.selectbox(
                    "任务类型",
                    options=[
                        "competitor_landscape",
                        "product_comparison",
                        "pricing_analysis",
                        "sales_battlecard",
                        "customer_voice",
                    ],
                    index=[
                        "competitor_landscape",
                        "product_comparison",
                        "pricing_analysis",
                        "sales_battlecard",
                        "customer_voice",
                    ].index(draft.get("task_type", "competitor_landscape")) if draft.get("task_type", "competitor_landscape") in [
                        "competitor_landscape",
                        "product_comparison",
                        "pricing_analysis",
                        "sales_battlecard",
                        "customer_voice",
                    ] else 0,
                    key="edit_task_type",
                )

                region_edit = st.selectbox(
                    "目标区域",
                    options=["global", "china", "us", "europe", "southeast_asia", "custom"],
                    index=["global", "china", "us", "europe", "southeast_asia", "custom"].index(
                        draft.get("target_region", "global")
                    ),
                    key="edit_region",
                )

                desc_edit = st.text_area(
                    "描述",
                    value=draft.get("description", ""),
                    height=80,
                    key="edit_description",
                )

            # --- Section 2: Recommended Competitors ---
            with st.expander("2. 竞品列表", expanded=True):
                st.info("添加竞品名称即可。用户若有额外要求，请在备注列填写。系统将自动搜索所有竞品信息，无需填写 URL。")
                st.caption("竞品数量和备注均可编辑。")

                products_df = st.session_state.get("intake_products_df")
                if products_df is not None and not products_df.empty:
                    edited_df = st.data_editor(
                        products_df,
                        num_rows="dynamic",
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "product_name": st.column_config.TextColumn("产品名称 *", required=True),
                            "company_name": st.column_config.TextColumn("公司名称（可选）"),
                            "priority": st.column_config.SelectboxColumn(
                                "优先级",
                                options=["high", "medium", "low"],
                                default="medium",
                            ),
                            "user_notes": st.column_config.TextColumn(
                                "用户要求/备注（可选）",
                                help="例如：重点关注企业版定价和私有化部署能力",
                            ),
                        },
                        key="products_editor",
                    )
                    st.session_state["intake_products_df"] = edited_df
                else:
                    st.info("未检测到竞品，请在下方手动添加。")

            # --- Section 3: Analysis Dimensions ---
            with st.expander("3. 分析维度", expanded=True):
                # Dynamic dimensions from research_plan
                plan_dims = research_plan.get("analysis_dimensions") or []

                if plan_dims:
                    # Use dimensions from research_plan
                    st.caption(f"✅ 方案已定义 {len(plan_dims)} 个分析维度")
                    for dim in plan_dims:
                        if isinstance(dim, dict):
                            dim_id = dim.get("dimension_id", "")
                            dim_name = dim.get("name", dim_id)
                            dim_desc = dim.get("description", "")
                            dim_required = dim.get("required", False)
                            st.markdown(f"**{dim_name}** {'(必选)' if dim_required else '(可选)'} — `{dim_id}`")
                            if dim_desc:
                                st.caption(f"_{dim_desc[:100]}..._")
                else:
                    # Bilingual predefined dimensions
                    st.caption("⚠️ 方案未定义维度，使用默认维度（可自行勾选）")
                    all_dims = [
                        ("function_tree",            "功能树 / 核心能力",        "Core Capabilities"),
                        ("workflow_builder",         "工作流编排",                "Workflow Builder"),
                        ("rag",                      "RAG / 知识库",              "RAG / Knowledge Base"),
                        ("tool_calling",            "工具调用",                  "Tool Calling"),
                        ("multi_agent",              "多Agent 编排",              "Multi-Agent Orchestration"),
                        ("enterprise_readiness",      "企业就绪度",                "Enterprise Readiness"),
                        ("private_deployment",       "私有化部署",                "Private Deployment"),
                        ("security",                 "安全与合规",                "Security & Compliance"),
                        ("pricing_model",            "定价模式",                  "Pricing Model"),
                        ("pricing_strategy",         "定价策略",                  "Pricing Strategy"),
                        ("user_persona",             "用户画像",                  "User Persona"),
                        ("customer_voice",           "用户声音",                  "Customer Voice"),
                        ("learning_curve",           "学习曲线",                  "Learning Curve"),
                        ("ecosystem",                "生态系统",                  "Ecosystem"),
                        ("community",                "社区活跃度",                 "Community Activity"),
                    ]
                    current_dims = st.session_state.get("intake_selected_dimensions", draft.get("analysis_dimensions", []))
                    new_dims = []
                    # Display 3 columns of checkboxes
                    cols = st.columns(3)
                    for idx, (dim_key, dim_zh, dim_en) in enumerate(all_dims):
                        with cols[idx % 3]:
                            label = f"{dim_zh} / {dim_en}"
                            if st.checkbox(label, value=(dim_key in current_dims), key=f"intake_dim_{dim_key}"):
                                new_dims.append(dim_key)
                    st.session_state["intake_selected_dimensions"] = new_dims

            # --- Section 4: Research Plan Details (vNext-R1) ---
            with st.expander("4. 调研方案详情 (vNext-R1)", expanded=True):
                generated_by = draft.get("plan_generated_by", "unknown")
                st.caption(f"生成方式: **{generated_by}** | Schema: **{research_plan.get('schema_type', 'N/A')}** | Mode: **{research_plan.get('mode', 'N/A')}**")
                
                # Task Brief
                task_brief = research_plan.get("task_brief") or {}
                if task_brief:
                    st.markdown("**Task Brief**")
                    col_tb1, col_tb2 = st.columns(2)
                    with col_tb1:
                        st.markdown(f"- Project: {task_brief.get('project_name', 'N/A')}")
                        st.markdown(f"- Task Type: {task_brief.get('task_type', 'N/A')}")
                    with col_tb2:
                        st.markdown(f"- Target Region: {task_brief.get('target_region', 'N/A')}")
                        st.markdown(f"- Business Goal: {task_brief.get('business_goal', 'N/A')[:100]}...")
                else:
                    # Fallback to old format
                    st.markdown("**目标**")
                    st.info(research_plan.get("objective", "N/A"))

                # Competitors from new format
                competitors = research_plan.get("competitors") or []
                if competitors:
                    st.markdown("**竞品列表**")
                    import pandas as pd
                    comp_rows = []
                    for comp in competitors:
                        if isinstance(comp, dict):
                            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(comp.get("priority", "medium"), "")
                            comp_rows.append({
                                "竞品": comp.get("name", ""),
                                "公司": comp.get("company_name", ""),
                                "优先级": f"{priority_emoji} {comp.get('priority', '')}",
                                "用户要求": (comp.get("notes", "") or "")[:60],
                            })
                    if comp_rows:
                        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, use_container_width=True)
                    else:
                        st.markdown("**竞品选择理由**")
                        st.write(research_plan.get("competitor_selection_rationale", "无"))
                else:
                    st.markdown("**竞品选择理由**")
                    st.write(research_plan.get("competitor_selection_rationale", "无"))

                # Source Plan
                source_plan = research_plan.get("source_plan") or {}
                if source_plan and isinstance(source_plan, dict):
                    st.markdown("**Source Plan**")
                    st.markdown(f"- Strategy: {source_plan.get('collection_strategy', 'N/A')[:100]}...")
                    st.markdown(f"- Min sources/competitor: {source_plan.get('minimum_sources_per_competitor', 'N/A')}")
                else:
                    source_plan_list = research_plan.get("source_plan") or []
                    if source_plan_list:
                        for step in source_plan_list:
                            with st.expander(f"步骤 {step.get('step', '?')}: {step.get('phase', 'Unknown')} - {step.get('action', 'Unknown')}"):
                                st.markdown(f"**阶段:** {step.get('phase', 'N/A')}")
                                st.markdown(f"**动作:** {step.get('action', 'N/A')}")
                                st.markdown(f"**目标:** {', '.join(step.get('targets', []))}")
                                st.markdown(f"**目的:** {step.get('purpose', 'N/A')}")

                # Analysis Dimensions (bilingual preview)
                dims = research_plan.get("analysis_dimensions") or []
                if dims:
                    st.markdown("**分析维度**")
                    dim_rows = []
                    for dim in dims:
                        if isinstance(dim, dict):
                            dim_rows.append({
                                "维度 / Dimension": dim.get("name", dim.get("dimension_id", "")),
                                "ID": dim.get("dimension_id", ""),
                                "必选 / Required": "🔴 是" if dim.get("required") else "否",
                            })
                    if dim_rows:
                        st.dataframe(pd.DataFrame(dim_rows), hide_index=True, use_container_width=True)

                # Report Outline — hierarchical view + separate LLM generation
                report_outline = research_plan.get("report_outline") or {}
                outline_sections = report_outline.get("sections") or []

                st.markdown("**📋 报告大纲**")
                st.caption("大纲由 AI 根据竞品和维度单独生成，可在确认方案后在 Research Plan 页面调整。")

                if not outline_sections:
                    st.info("暂无大纲。可在确认方案后进入 Research Plan 页面生成详细大纲。")
                else:
                    # Build chapter hierarchy
                    chapters = []
                    current_ch = None
                    for sec in outline_sections:
                        if sec.get("type") == "chapter" or sec.get("type") is None:
                            current_ch = {"chapter": sec, "subs": []}
                            chapters.append(current_ch)
                        elif current_ch:
                            current_ch["subs"].append(sec)
                        else:
                            current_ch = {"chapter": sec, "subs": []}
                            chapters.append(current_ch)

                    for ci, ch in enumerate(chapters, 1):
                        sec = ch["chapter"]
                        subs = ch["subs"]
                        with st.container():
                            col_h, col_w, col_r = st.columns([4, 1, 1])
                            with col_h:
                                st.markdown(f"**📌 第{ci}章：{sec.get('title', '')}**")
                            with col_w:
                                st.caption(f"≥{sec.get('min_words', 800)}字")
                            with col_r:
                                if sec.get("requires_human_review"):
                                    st.caption("🔴 需审核")
                            for sub in subs:
                                st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ **{sub.get('title', '')}**  _({sub.get('min_words', 400)}字)_")
                            st.divider()

                # Human Checkpoints
                checkpoints = research_plan.get("human_checkpoints") or []
                if checkpoints:
                    st.markdown("**Human Checkpoints**")
                    for cp in checkpoints:
                        if isinstance(cp, dict):
                            st.markdown(f"- [{cp.get('stage', 'N/A')}] {cp.get('title', 'N/A')} {'(Required)' if cp.get('required') else '(Optional)'}")

                # Success Metrics
                metrics = research_plan.get("success_metrics") or {}
                if metrics and isinstance(metrics, dict):
                    st.markdown("**Success Metrics**")
                    col_m1, col_m2, col_m3 = st.columns(3)
                    with col_m1:
                        st.metric("Min Claims", metrics.get("minimum_signed_claims", "N/A"))
                    with col_m2:
                        st.metric("Min Evidence", metrics.get("minimum_evidence_items", "N/A"))
                    with col_m3:
                        st.metric("Min Words", metrics.get("minimum_report_words", "N/A"))

                # Source Discovery (vNext-R1.5)
                source_discovery = research_plan.get("source_discovery") or {}
                if source_discovery:
                    st.markdown("### 来源发现计划")
                    source_readiness = source_discovery.get("source_readiness", "unknown")
                    readiness_colors = {
                        "ready": "green",
                        "ready_with_discovery": "blue",
                        "blocked_before_run": "red",
                    }
                    readiness_labels = {
                        "ready": "✅ 就绪",
                        "ready_with_discovery": "🔍 需要自动搜索",
                        "blocked_before_run": "⚠️ 被阻塞",
                    }
                    color = readiness_colors.get(source_readiness, "gray")
                    label = readiness_labels.get(source_readiness, source_readiness)
                    st.markdown(f"**来源就绪状态:** :{color}[{label}]")

                    # Show discovery queries
                    discovery_queries = source_discovery.get("discovery_queries") or []
                    if discovery_queries:
                        competitors_needing_discovery = [q.get("competitor", "") for q in discovery_queries if isinstance(q, dict)]
                        if competitors_needing_discovery:
                            st.info(f"以下竞品将进入自动来源发现: {', '.join(competitors_needing_discovery)}")

                        with st.expander("查看搜索查询", expanded=False):
                            for q in discovery_queries:
                                if isinstance(q, dict):
                                    comp = q.get("competitor", "")
                                    queries = q.get("queries", [])
                                    st.markdown(f"**{comp}:**")
                                    for query in queries:
                                        st.markdown(f"- {query}")
                    else:
                        st.success("所有竞品已有 URL，无需自动搜索。")

                    if source_readiness == "ready_with_discovery":
                        st.info("💡 未填写 URL 的竞品将在分析开始后进入自动来源发现阶段。")
                    elif source_readiness == "blocked_before_run":
                        st.error("⚠️ 无法启动分析：既没有 URL 也没有自动搜索计划。请补充 URL。")

                # Research Questions (vNext-R1.6)
                research_questions = research_plan.get("research_questions") or []
                if research_questions:
                    st.markdown("### 研究问题")
                    for i, q in enumerate(research_questions, 1):
                        st.markdown(f"{i}. {q}")

                # DAG Preview section - show real DAG after confirmation
                plan_id = st.session_state.get("rp_plan_id")
                if plan_id:
                    st.divider()
                    st.markdown("### DAG 预览")
                    st.markdown(f"**Plan ID:** `{plan_id}`")

                    # Check if plan is confirmed
                    plan_data = st.session_state.get("rp_plan_data") or {}
                    is_confirmed = plan_data.get("status") == "confirmed"
                    dag_id = st.session_state.get("rp_confirmed_dag_id") or plan_data.get("dag_id")

                    if is_confirmed and dag_id:
                        render_research_plan_dag_preview(plan_id, plan_data)
                    else:
                        st.info("方案确认后将显示真实 DAG 结构。")

                    st.info("💡 可跳转到 **Research Plan** 页面进行更详细的编辑和 DAG 预览")

            st.divider()

            confirm_col1, confirm_col2, confirm_col3 = st.columns([1, 1, 1])
            with confirm_col1:
                back_clicked = st.button("修改需求", use_container_width=True)
            with confirm_col2:
                # First confirm the plan to create DAG
                confirm_plan_clicked = st.button("确认方案", type="primary", use_container_width=True)
            with confirm_col3:
                # After confirmation, start analysis
                start_analysis_clicked = st.button("开始分析", type="primary", use_container_width=True)

            if back_clicked:
                st.session_state["af_stage"] = "intake"
                st.rerun()

            # Handle: Confirm Plan (creates DAG)
            if confirm_plan_clicked:
                plan_id = st.session_state.get("rp_plan_id")
                if not plan_id:
                    st.error("无调研方案 ID，无法确认。请重新生成方案。")
                else:
                    with st.spinner("正在确认方案并创建 DAG..."):
                        try:
                            resp = requests.post(f"{API_BASE}/api/research-plans/{plan_id}/confirm", json={}, timeout=60)
                            if resp.status_code >= 400:
                                st.error(f"确认方案失败: {resp.text}")
                            else:
                                result = resp.json()
                                dag_id = result.get("dag_id")
                                st.session_state["rp_confirmed_dag_id"] = dag_id
                                st.session_state["af_plan_confirmed"] = True
                                st.success(f"✅ 调研方案已确认！DAG 已创建: `{dag_id}`")
                                # Reload plan to get confirmed status
                                load_resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}", timeout=30)
                                if load_resp.status_code == 200:
                                    plan_data = load_resp.json().get("research_plan", {})
                                    st.session_state["rp_plan_data"] = plan_data
                        except Exception as exc:
                            st.error(f"确认方案失败: {exc}")

            # Handle: Start Analysis (only after plan is confirmed)
            if start_analysis_clicked:
                plan_id = st.session_state.get("rp_plan_id")
                dag_id = st.session_state.get("rp_confirmed_dag_id")
                plan_data = st.session_state.get("rp_plan_data") or {}

                if not dag_id:
                    st.error("请先确认方案。")
                else:
                    # Source Readiness Gate (vNext-R1.5)
                    source_discovery = plan_data.get("source_discovery") or {}
                    source_readiness = source_discovery.get("source_readiness", "ready")

                    if source_readiness == "blocked_before_run":
                        st.error("⚠️ 当前没有可用来源，也没有自动搜索计划。请补充 URL 或重新生成调研方案。")
                    else:
                        # Check if user has products without URLs
                        competitors = plan_data.get("competitors", [])
                        needs_discovery = []
                        for comp in competitors:
                            if isinstance(comp, dict):
                                if not comp.get("official_url") and not comp.get("seed_urls"):
                                    needs_discovery.append(comp.get("name", "Unknown"))

                        if needs_discovery:
                            st.warning(f"📌 未填写 URL 的竞品将先进入自动来源发现阶段: {', '.join(needs_discovery)}")

                        final_proj_name = st.session_state.get("edit_proj_name", draft.get("project_name", ""))
                        final_task_type = st.session_state.get("edit_task_type", draft.get("task_type", "competitor_landscape"))
                        final_region = st.session_state.get("edit_region", draft.get("target_region", "global"))
                        final_description = st.session_state.get("edit_description", draft.get("description", ""))

                        # Use research_plan dimensions if available, otherwise fall back to session state
                        plan_dims = research_plan.get("analysis_dimensions") or []
                        if plan_dims:
                            final_dims = [d.get("dimension_id") for d in plan_dims if isinstance(d, dict) and d.get("dimension_id")]
                        else:
                            final_dims = st.session_state.get("intake_selected_dimensions", draft.get("analysis_dimensions", []))

                        final_products = []
                        edited_df = st.session_state.get("intake_products_df")
                        if edited_df is not None and not edited_df.empty:
                            for _, row in edited_df.iterrows():
                                seed_urls_str = row.get("seed_urls", "")
                                seed_urls = [u.strip() for u in seed_urls_str.split("\n") if u.strip()] if isinstance(seed_urls_str, str) else (seed_urls_str if isinstance(seed_urls_str, list) else [])
                                final_products.append({
                                    "product_name": row.get("product_name", ""),
                                    "company_name": row.get("company_name", ""),
                                    "official_website": row.get("official_website", ""),
                                    "seed_urls": seed_urls,
                                })

                        if not final_proj_name:
                            st.error("项目名称不能为空。")
                        elif not final_products:
                            st.error("至少需要一个竞品。")
                        else:
                            try:
                                # Create project with DAG reference and source discovery
                                resp = requests.post(
                                    f"{API_BASE}/api/projects",
                                    json={
                                        "project_name": final_proj_name,
                                        "task_type": final_task_type,
                                        "target_region": final_region,
                                        "description": final_description,
                                        "products": final_products,
                                        "analysis_dimensions": final_dims,
                                        "research_plan_id": plan_id,
                                        "execution_dag_id": dag_id,
                                        "source_discovery": source_discovery,
                                    },
                                    timeout=15,
                                )
                                resp.raise_for_status()
                                project_result = resp.json()
                                new_proj_id = project_result.get("project_id")

                                # Start async run
                                ok = start_run_async_and_go_to_running(new_proj_id)
                                if not ok:
                                    st.stop()

                            except requests.exceptions.RequestException as e:
                                st.session_state["last_start_error"] = f"创建项目失败: {e}"

            # Show confirmation status
            if st.session_state.get("af_plan_confirmed"):
                dag_id = st.session_state.get("rp_confirmed_dag_id")
                st.success(f"✅ 调研方案已确认 | DAG ID: `{dag_id}` | 请点击「开始分析」启动执行。")

    # -------------------------------------------------------------------------
    # ---------------------------------------------------------------------------
    elif stage == "running":
            st.subheader("运行中心")
            proj_id = st.session_state.get("selected_project_id")
            run_id = st.session_state.get("selected_run_id")

            if not proj_id:
                    st.warning("No project selected.")
                    af_reset()
                    st.rerun()

            proj_name = "Unknown Project"
            try:
                    pr = requests.get(f"{API_BASE}/api/projects/{proj_id}", timeout=10)
                    if pr.status_code == 200:
                            proj_name = pr.json().get("project_name", "Unknown Project")
            except Exception:
                    pass

            effective_run_id = run_id
            if not effective_run_id and proj_id:
                    try:
                            pr = requests.get(f"{API_BASE}/api/projects/{proj_id}", timeout=10)
                            if pr.status_code == 200:
                                    lr = pr.json().get("latest_run") or {}
                                    effective_run_id = lr.get("run_id") or ""
                    except Exception:
                            pass

            live_data = None
            if effective_run_id:
                    try:
                            lr = requests.get(f"{API_BASE}/api/runs/{effective_run_id}/live", timeout=10)
                            if lr.status_code == 200:
                                    live_data = lr.json()
                    except Exception:
                            pass

            run_status = live_data.get("status", "pending") if live_data else "pending"
            current_node = live_data.get("current_node", "") if live_data else ""
            wf_nodes = live_data.get("workflow_nodes", []) if live_data else []
            wf_summary = live_data.get("workflow_summary", {}) if live_data else {}
            latest_traces = live_data.get("latest_traces", []) if live_data else []
            ac = live_data.get("artifact_counts", {}) if live_data else {}
            pending_review = live_data.get("pending_review_count", 0) if live_data else 0
            report_status = live_data.get("report_status") if live_data else None
            qg = live_data.get("quality_gate", {}) if live_data else {}
            started_at = live_data.get("started_at", "") if live_data else ""

            src_count = ac.get("sources", 0)
            ev_count = ac.get("evidence", 0)
            fact_count = ac.get("facts", 0)
            claim_count = ac.get("claims", 0)
            signed_count = ac.get("signed_claims", 0)

            def _elapsed(start_iso: str) -> str:
                    if not start_iso:
                            return "—"
                    try:
                            from datetime import datetime, timezone
                            start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
                            diff = datetime.now(timezone.utc) - start
                            total_s = int(diff.total_seconds())
                            if total_s < 60:
                                    return f"{total_s}s"
                            m, s = divmod(total_s, 60)
                            if m < 60:
                                    return f"{m}m {s}s"
                            h, rem = divmod(total_s, 3600)
                            return f"{h}h {m % 60}m"
                    except Exception:
                            return "—"

            elapsed_str = _elapsed(started_at)

            NODE_LABELS = {
                    "build_task_brief": ("构建任务简报", "编排器"),
                    "plan_schema": ("规划分析结构", "Schema规划Agent"),
                    "plan_sources": ("规划信息来源", "Source规划Agent"),
                    "collect_sources": ("收集信息源", "采集Agent"),
                    "evaluate_evidence": ("评估证据质量", "评估Agent"),
                    "pii_scrub": ("隐私合规处理", "合规Agent"),
                    "extract_facts": ("抽取结构化事实", "抽取Agent"),
                    "detect_schema_gaps": ("检测结构空白", "Schema差距规划Agent"),
                    "execute_rework": ("补证返工", "修复Agent"),
                    "analyze_dimensions": ("多维分析", "分析Agent"),
                    "review_claims": ("结论质检", "审查Agent"),
                    "prepare_human_intervention": ("人工介入", "人工审查Agent"),
                    "write_report": ("撰写报告", "撰写Agent"),
                    "final_review": ("最终质量门", "审查Agent"),
                    "export_report": ("导出报告", "导出Agent"),
                    "compute_metrics": ("计算质量指标", "评估Agent"),
            }

            def _label(nk):
                    info = NODE_LABELS.get(nk)
                    return info[0] if info else nk

            def _agent(nk):
                    info = NODE_LABELS.get(nk)
                    return info[1] if info else "—"

            current_label = _label(current_node)
            current_agent = _agent(current_node)

            # === HEADER ===
            status_display = {"pending": "等待中", "running": "运行中", "completed": "已完成", "failed": "失败"}
            status_color_map = {"pending": "gray", "running": "blue", "completed": "green", "failed": "red"}
            status_zh = status_display.get(run_status, run_status)
            sc = status_color_map.get(run_status, "gray")

            col_h1, col_h2, col_h3, col_h4 = st.columns([2, 1, 1, 1])
            with col_h1:
                    st.subheader(f"项目: {proj_name}")
                    st.caption(f"Run ID: `{effective_run_id or '—'}`")
            with col_h2:
                    st.markdown("**状态**")
                    st.markdown(f":{sc}[{status_zh}]")
            with col_h3:
                    st.markdown("**当前步骤**")
                    st.markdown(f":blue[{current_label}]")
            with col_h4:
                    st.markdown("**运行时间**")
                    st.markdown(f":blue[{elapsed_str}]")

            if run_status == "running" and current_agent != "—":
                    st.caption(f"当前 Agent: {current_agent}")

            st.divider()

            # === THREE-COLUMN LAYOUT ===
            col_phases, col_timeline, col_counts = st.columns([1, 1, 1])

            # ---------- LEFT: 中文 Workflow 阶段进度 ----------
            with col_phases:
                    st.markdown("##### 🔄 工作流阶段")
                    st.caption("Phase progress (node-level detail)")

                    PHASES_ZH = [
                            ("构建简报", ["build_task_brief"]),
                            ("结构规划", ["plan_schema", "plan_sources"]),
                            ("信息采集", ["collect_sources"]),
                            ("证据评估", ["evaluate_evidence", "pii_scrub"]),
                            ("事实抽取", ["extract_facts", "detect_schema_gaps"]),
                            ("多维分析", ["analyze_dimensions"]),
                            ("结论质检", ["review_claims"]),
                            ("补证返工", ["execute_rework"]),
                            ("人工介入", ["prepare_human_intervention"]),
                            ("报告撰写", ["write_report"]),
                            ("最终质量门", ["final_review"]),
                            ("报告导出", ["export_report"]),
                            ("质量指标", ["compute_metrics"]),
                    ]

                    node_by_name = {n.get("node_name", ""): n for n in wf_nodes}
                    node_status_map = {}
                    for phase_name, keywords in PHASES_ZH:
                            statuses = []
                            for kw in keywords:
                                    n = node_by_name.get(kw, {})
                                    statuses.append(n.get("status", "pending"))
                            if statuses:
                                    if any(s in ("failed", "failed_permanently") for s in statuses):
                                            node_status_map[phase_name] = "failed"
                                    elif "running" in statuses:
                                            node_status_map[phase_name] = "running"
                                    elif all(s == "completed" for s in statuses):
                                            node_status_map[phase_name] = "completed"
                                    else:
                                            node_status_map[phase_name] = "pending"

                    phase_icon = {"completed": "✅", "running": "🔄", "failed": "❌", "pending": "⚪"}
                    phase_color_css = {
                            "completed": "#22c55e", "running": "#3b82f6",
                            "failed": "#ef4444", "pending": "#d1d5db",
                    }

                    for phase_name, _ in PHASES_ZH:
                            stype = node_status_map.get(phase_name, "pending")
                            icon = phase_icon.get(stype, "⚪")
                            color = phase_color_css.get(stype, "#d1d5db")
                            is_active = stype == "running"
                            extra = " ◀ 执行中" if is_active else ""
                            bg = "#f0f7ff" if is_active else "transparent"
                            st.markdown(
                                    f"<div style='padding:5px 8px;border-left:3px solid {color};margin-bottom:3px;"
                                    f"background:{bg};border-radius:4px;'>"
                                    f"{icon} {phase_name}{extra}</div>",
                                    unsafe_allow_html=True,
                            )

                    completed_n = wf_summary.get("completed", 0)
                    total_n = wf_summary.get("total", len(wf_nodes))
                    st.caption(f"Node progress: {completed_n}/{total_n} completed")

            # ---------- MIDDLE: 执行时间线 ----------
            with col_timeline:
                    st.markdown("##### 📋 执行时间线")
                    st.caption("Recent node completions")

                    if latest_traces:
                            for trace in reversed(latest_traces):
                                    nk = trace.get("node_name", "")
                                    lat_ms = trace.get("latency_ms", 0) or 0
                                    status_t = trace.get("status", "")
                                    ticon = _workflow_node_icon(status_t)
                                    err_t = trace.get("error_message", "")
                                    comp_at = trace.get("completed_at", "")[:19] if trace.get("completed_at") else "—"

                                    lat_str = f"{lat_ms / 1000:.1f}s" if lat_ms >= 1000 else f"{lat_ms}ms"

                                    if err_t:
                                            st.markdown(
                                                    f"{ticon} **`{nk}`** ({lat_str}) — {comp_at}  " f"    ❌ {err_t[:80]}",
                                            )
                                    else:
                                            st.markdown(
                                                    f"{ticon} **`{nk}`** — {_label(nk)} ({lat_str})  " f"    完成于 {comp_at}",
                                            )
                    else:
                            st.info("时间线数据加载中...")

                    running_nodes = [n for n in wf_nodes if n.get("status") == "running"]
                    if running_nodes:
                            st.markdown("**正在执行:**")
                            for n in running_nodes:
                                    nk = n.get("node_name", "")
                                    st.markdown(f"🔄 `{nk}` — {_label(nk)} — {_agent(nk)}")

            # ---------- RIGHT: 实时产物计数 ----------
            with col_counts:
                    st.markdown("##### 📊 实时产物")
                    st.caption("Live artifact counts")

                    def _metric_row(label, count, icon, active=False):
                            color = "blue" if active else "gray"
                            st.markdown(f":{color}[**{icon} {label}**]")
                            st.markdown(f":{color}[**{count}**]")
                            st.divider()

                    _metric_row("🌐 来源", src_count, "🌐", active=(run_status == "running"))
                    _metric_row("💎 证据", ev_count, "💎", active=(run_status == "running"))
                    _metric_row("🧩 结构化事实", fact_count, "🧩")
                    _metric_row("🧠 结论", claim_count, "🧠")
                    _metric_row("✅ 已签发结论", signed_count, "✅")
                    _metric_row("⚠️ 待审查", pending_review, "⚠️", active=(pending_review > 0))

                    report_status_map = {
                            "draft": ("草稿", "orange"),
                            "review": ("审查中", "blue"),
                            "approved": ("已批准", "green"),
                            "blocked": ("被阻止", "red"),
                            "published": ("已发布", "green"),
                    }
                    if report_status:
                            rs_zh, rs_color = report_status_map.get(report_status, (report_status, "gray"))
                            st.markdown(f":{rs_color}[**📄 报告状态**: {rs_zh}]")
                    else:
                            st.markdown("**📄 报告状态**: 尚未生成")

                    if qg.get("blocked"):
                            codes = qg.get("reason_codes", [])
                            st.error(f"🚫 质量门已阻止 — {codes}")

            st.divider()

            # === STATUS-SPECIFIC BANNER ===
            if run_status == "completed":
                    st.success("分析完成，可以查看交付物。")
            elif run_status == "failed":
                    qg_reason = qg.get("reason") or ""
                    qg_codes = qg.get("reason_codes", [])
                    if qg_codes:
                            st.error(f"🚫 质量门阻断 — 原因代码: {qg_codes}")
                            if qg_reason:
                                    st.caption(f"详情: {qg_reason[:120]}")
                    else:
                            st.error(f"❌ 分析失败 — {qg_reason[:120] if qg_reason else '未知原因，请前往审查中心查看'}")

            # === BOTTOM action buttons ===
            col_del, col_ws, col_hr = st.columns([1, 1, 1])
            view_del_clicked = st.button(
                    "查看交付物",
                    key=f"af_running_vd_{effective_run_id or 'none'}",
                    type="primary",
                    use_container_width=True,
            )
            with col_ws:
                    if st.button("项目工作台", key=f"af_running_ws_{effective_run_id or 'none'}", use_container_width=True):
                            goto_page("Project Workspace")
            with col_hr:
                    if st.button("审查中心", key=f"af_running_hr_{effective_run_id or 'none'}", use_container_width=True):
                            goto_page("Review Center")

            if view_del_clicked:
                    st.session_state["af_stage"] = "deliverables"
                    st.rerun()

            # === AUTO-REFRESH (at the end, after all UI is rendered) ===
            # Only auto-refresh when pending/running; completed/failed stay on this page.
            if run_status in ("pending", "running"):
                    auto_key = f"af_ar_{effective_run_id or 'none'}"
                    col_ref, col_auto = st.columns([1, 1])
                    with col_ref:
                            st.button("刷新", key=f"af_refresh_{effective_run_id or 'none'}", use_container_width=True)
                    with col_auto:
                            auto_refresh = st.checkbox("自动刷新 (每 2 秒)", value=True, key=auto_key)
                    if auto_refresh:
                            time.sleep(2)
                            st.rerun()

    # -------------------------------------------------------------------------
    # Stage 4: Deliverables
    # -------------------------------------------------------------------------
    elif stage == "deliverables":
            proj_id = st.session_state.get("selected_project_id")
            run_id = st.session_state.get("selected_run_id")

            effective_run_id = run_id
            if not effective_run_id and proj_id:
                    try:
                            proj_resp = requests.get(f"{API_BASE}/api/projects/{proj_id}", timeout=10)
                            if proj_resp.status_code == 200:
                                    proj_data = proj_resp.json()
                                    lr = proj_data.get("latest_run") or {}
                                    effective_run_id = lr.get("run_id") or ""
                    except Exception:
                            pass

            proj_name = "Analysis"
            if proj_id:
                    try:
                            pr = requests.get(f"{API_BASE}/api/projects/{proj_id}", timeout=10)
                            if pr.status_code == 200:
                                    proj_name = pr.json().get("project_name", "Analysis")
                    except Exception:
                            pass

            col_title, col_btn = st.columns([3, 1])
            with col_title:
                    st.subheader(f"Deliverables: {proj_name}")
                    st.caption(f"Run ID: `{effective_run_id or 'N/A'}`")
            with col_btn:
                    if st.button("New Analysis", key=f"af_deliv_newanalysis_{effective_run_id or 'none'}", type="primary"):
                            af_reset()
                            st.rerun()

            # Check if run was blocked/failed
            deliv_run_status = "unknown"
            deliv_run_err = ""
            is_blocked_run = False
            if effective_run_id:
                    try:
                            dr = requests.get(f"{API_BASE}/api/runs/{effective_run_id}", timeout=10)
                            if dr.status_code == 200:
                                    drj = dr.json()
                                    deliv_run_status = drj.get("status", "unknown")
                                    deliv_run_err = drj.get("error_message", "") or ""
                                    is_blocked_run = deliv_run_status == "failed" and "block" in deliv_run_err.lower()
                    except Exception:
                            pass

            # Fetch all available data (must precede any checks that reference these vars)
            report_data = {}
            evidence_data = []
            sources_data = []
            claims_data = {}
            metrics_data = {}
            facts_data = []

            if effective_run_id:
                    report_data = get_json(f"/api/runs/{effective_run_id}/report", {}) or {}
                    evidence_data = get_json(f"/api/runs/{effective_run_id}/evidence", []) or []
                    sources_data = get_json(f"/api/runs/{effective_run_id}/sources", []) or []
                    claims_data = get_json(f"/api/runs/{effective_run_id}/review-items", {}) or {}
                    metrics_data = get_json(f"/api/runs/{effective_run_id}/metrics", {}) or {}
                    facts_data = get_json(f"/api/runs/{effective_run_id}/facts", []) or []

            qs = report_data.get("quality_summary", {}) if isinstance(report_data, dict) else {}
            if isinstance(qs, str):
                    try:
                            qs = json.loads(qs)
                    except Exception:
                            qs = {}
            for k in ("schema_completion_rate", "evidence_coverage_rate",
                                "unsupported_claim_rate", "review_pass_rate"):
                    if k not in qs and k in metrics_data:
                            qs[k] = metrics_data[k]

            claims = claims_data.get("claims", []) if isinstance(claims_data, dict) else []
            signed_claims = [c for c in claims if isinstance(c, dict) and c.get("review_status", "").lower() == "signed"]
            all_spans = report_data.get("spans", []) or report_data.get("sections", []) if isinstance(report_data, dict) else []

            if deliv_run_status == "failed" or is_blocked_run:
                    if is_blocked_run:
                            # Rich partial deliverable for blocked runs
                            st.warning("**Partial Deliverable — Final report is blocked**\n\nThe workflow completed but the quality gate blocked the final report. Available partial outputs are shown below.")
                            st.divider()

                            # Parse reason codes
                            reason_codes = []
                            for part in (deliv_run_err or "").split(";"):
                                    part = part.strip()
                                    m = __import__("re").match(r"\[(\w+)\]", part) if part else None
                                    if m:
                                            reason_codes.append(m.group(1))

                            # --- Missing Evidence / Unsupported Span Issues ---
                            if reason_codes:
                                    with st.expander("Quality Gate Findings", expanded=True):
                                            for rc in reason_codes:
                                                    st.markdown(f"- **{format_reason_code(rc)}**")
                                            st.markdown("")
                                            st.caption(f"Raw: {deliv_run_err}")

                            # --- Recommended Rework Plan ---
                            rework_steps = []
                            if "MISSING_EVIDENCE" in reason_codes:
                                    rework_steps.append("- **Collect more evidence:** Browse the Evidence Appendix below and identify claims with low evidence support.")
                            if "UNSUPPORTED_REPORT_SPAN" in reason_codes:
                                    rework_steps.append("- **Sign more claims:** Go to Review Center, review the pending claims, and sign them to back the report spans.")
                            if "BLOCKED_NO_SIGNED_CLAIMS" in reason_codes:
                                    rework_steps.append("- **No signed claims yet:** Review and sign at least one claim before re-running the analysis.")
                            if "PII_NOT_MASKED" in reason_codes:
                                    rework_steps.append("- **Mask PII:** Find and mask any personally identifiable information in the collected evidence.")
                            if rework_steps:
                                    with st.expander("Recommended Rework Plan", expanded=True):
                                            for step in rework_steps:
                                                    st.markdown(step)

                            st.divider()
                    else:
                            st.error(f"**Partial Deliverable — Run failed**: {deliv_run_err}")
            elif deliv_run_status == "completed" and not report_data:
                    st.info("Run completed but no report data found. Deliverables are partial.")

            st.divider()

            # --- Quality Summary ---
            with st.expander("Quality Summary", expanded=True):
                    if qs:
                            q_fields = [
                                    ("evidence_coverage_rate", "Evidence Coverage Rate"),
                                    ("claim_count", "Claims"),
                                    ("evidence_count", "Evidence"),
                                    ("total_products", "Total Products"),
                                    ("signed_claims", "Signed Claims"),
                                    ("sufficient_products", "✅ Sufficient"),
                                    ("partial_products", "⚠ Partial"),
                                    ("insufficient_products", "❌ Insufficient"),
                            ]
                            qc = st.columns(min(len(q_fields), 4))
                            for i, (k, label) in enumerate(q_fields):
                                    val = qs.get(k) if isinstance(qs, dict) else None
                                    with qc[i % 4]:
                                            if val is not None:
                                                    if isinstance(val, float) and 0 <= val <= 1:
                                                            display_val = f"{val:.0%}"
                                                    else:
                                                            display_val = str(val)
                                                    st.metric(label, display_val)
                                            else:
                                                    st.metric(label, "N/A")

                            # Product Coverage Table from canonical summary
                            pcs = qs.get("product_coverage_summary", {}) if isinstance(qs, dict) else {}
                            if pcs:
                                    st.divider()
                                    st.markdown("**Product Coverage Detail**")
                                    rows_data = []
                                    for slug, cov in sorted(pcs.items(), key=lambda x: x[0]):
                                            status = cov.get("coverage_status", "unknown")
                                            status_icon = {"sufficient": "✅", "partial": "⚠", "insufficient": "❌"}.get(status, "?")
                                            rows_data.append({
                                                    "Product": cov.get("product_name", slug.title()),
                                                    "Status": f"{status_icon} {status.title()}",
                                                    "Sources": cov.get("sources", 0),
                                                    "Evidence": cov.get("evidence", 0),
                                                    "Facts": cov.get("facts", 0),
                                                    "Claims": cov.get("signed_claims", 0),
                                            })
                                    if rows_data:
                                            st.dataframe(rows_data, use_container_width=True, hide_index=True)
                    else:
                            st.info("Quality metrics not yet available. Run may still be in progress.")

            # --- Executive Summary ---
            with st.expander("Executive Summary", expanded=True):
                    summary_text = ""
                    if isinstance(report_data, dict):
                            summary_text = report_data.get("executive_summary") or report_data.get("summary") or ""

                    if summary_text:
                            st.markdown(summary_text)
                    elif all_spans:
                            # Fallback: generate executive summary from spans
                            st.markdown(
                                    f"**Generated from {len(all_spans)} report section(s):** "
                                    "Executive summary is being generated by the analysis pipeline."
                            )
                            for span in all_spans[:3]:
                                    title = span.get("section_title", "Section")
                                    content = span.get("content_markdown") or span.get("text") or ""
                                    if content:
                                            st.markdown(f"**{title}**")
                                            st.markdown(content[:500] + ("..." if len(content) > 500 else ""))
                    elif signed_claims:
                            # Fallback: generate from claims
                            st.markdown("**Executive Summary (auto-generated from claims):**")
                            products_in_summary = set()
                            for c in signed_claims[:5]:
                                    products_in_summary.add(c.get("product_id", "unknown"))
                            st.markdown(
                                    f"This analysis covers {len(products_in_summary)} product(s) with "
                                    f"{len(signed_claims)} signed claims. "
                                    "The full executive summary will appear once the report is fully generated."
                            )
                    else:
                            st.info("Executive summary not yet available. Results will appear as analysis completes.")

            # --- Comparison Matrix ---
            with st.expander("Comparison Matrix", expanded=False):
                    comparison = None
                    if isinstance(report_data, dict):
                            comparison = (
                                    report_data.get("comparison_matrix")
                                    or report_data.get("comparison")
                                    or report_data.get("matrix")
                            )

                    if comparison:
                            st.json(comparison, expanded=False)
                    else:
                            matrix_spans = [s for s in all_spans
                                                          if "comparison" in (s.get("section_title") or "").lower()
                                                          or "matrix" in (s.get("section_title") or "").lower()] if all_spans else []
                            if matrix_spans:
                                    for span in matrix_spans:
                                            st.markdown(f"**{span.get('section_title', 'Section')}**")
                                            st.markdown(span.get("content_markdown") or span.get("text") or "")
                            elif signed_claims:
                                    # Fallback: build comparison from claims
                                    st.markdown("**Comparison Matrix (auto-generated from evidence):**")
                                    products_set = sorted(set(c.get("product_id", "") for c in claims if c.get("product_id")))
                                    dims_set = sorted(set(c.get("dimension", "") for c in claims if c.get("dimension")))
                                    matrix_rows = []
                                    for prod in products_set:
                                            row = {"Product": prod}
                                            for dim in dims_set:
                                                    related_claims = [c for c in signed_claims
                                                                                      if c.get("product_id") == prod and c.get("dimension") == dim]
                                                    if related_claims:
                                                            conf = max(c.get("confidence", 0) for c in related_claims)
                                                            row[dim] = f"Conf: {conf:.0%}"
                                                    else:
                                                            row[dim] = "N/A"
                                            matrix_rows.append(row)
                                    if matrix_rows and dims_set:
                                            st.dataframe(matrix_rows, hide_index=True, use_container_width=True)
                                    else:
                                            st.info("Not enough data to generate comparison matrix.")
                            else:
                                    st.info(
                                            "Comparison matrix not yet available. "
                                            "This section will be populated as evidence is collected."
                                    )

            # --- Evidence-backed Claims ---
            show_all_claims = is_blocked_run
            with st.expander("Evidence-backed Claims", expanded=False):
                    if signed_claims:
                            st.markdown(f"**{len(signed_claims)} signed claims** (out of {len(claims)} total)")
                            for c in signed_claims[:15]:
                                    conf = c.get("confidence", 0)
                                    risk = c.get("risk_level", "N/A")
                                    claim_text = c.get("claim_text", "N/A")
                                    ev_ids = c.get("evidence_ids", [])
                                    st.markdown(f"- {claim_text}")
                                    st.caption(
                                            f"  Product: `{c.get('product_id', 'N/A')}` | "
                                            f"Dimension: `{c.get('dimension', 'N/A')}` | "
                                            f"Confidence: {conf:.0%} | Risk: {risk} | "
                                            f"Evidence: {len(ev_ids)} item(s)"
                                    )
                    else:
                            st.info(
                                    "No signed claims yet. Claims will be signed after review passes. "
                                    "Check the **Review Center** for pending interventions."
                            )
                    # Show unsigned/pending claims for blocked runs
                    if show_all_claims and claims:
                            unsigned = [c for c in claims if isinstance(c, dict) and c.get("review_status", "").lower() != "signed"]
                            if unsigned:
                                    st.markdown(f"**{len(unsigned)} unsigned / pending claims**")
                                    for c in unsigned[:15]:
                                            conf = c.get("confidence", 0)
                                            status_label = c.get("review_status", "unsigned")
                                            claim_text = c.get("claim_text", "N/A")
                                            st.markdown(f"- {claim_text}")
                                            st.caption(
                                                    f"  Status: `{status_label}` | "
                                                    f"Product: `{c.get('product_id', 'N/A')}` | "
                                                    f"Dimension: `{c.get('dimension', 'N/A')}` | "
                                                    f"Confidence: {conf:.0%}"
                                            )

            # --- Risk & Opportunity ---
            with st.expander("Risk & Opportunity", expanded=False):
                    risk_spans = [
                            s for s in all_spans
                            if any(kw in (s.get("section_title") or "").lower()
                                          for kw in ["risk", "opportunity", "swot"])
                    ] if all_spans else []

                    if risk_spans:
                            for span in risk_spans:
                                    st.markdown(f"**{span.get('section_title', 'Section')}**")
                                    st.markdown(span.get("content_markdown") or span.get("text") or "")
                    elif signed_claims:
                            # Fallback: extract SWOT from claims
                            swot_dims = {"swot": [], "enterprise_readiness": []}
                            for dim_key in swot_dims:
                                    swot_claims = [c for c in signed_claims if c.get("dimension") == dim_key]
                                    swot_dims[dim_key] = swot_claims

                            if swot_dims.get("swot"):
                                    st.markdown("**SWOT Analysis (auto-generated from claims):**")
                                    for c in swot_dims["swot"][:5]:
                                            st.markdown(f"- {c.get('claim_text', 'N/A')}")
                            elif evidence_data:
                                    st.markdown("**Risk & Opportunity (auto-generated from evidence):**")
                                    # Extract from high-risk evidence
                                    high_risk_ev = [e for e in evidence_data if e.get("risk_level") in ("high", "medium")]
                                    low_conf_claims = [c for c in claims if isinstance(c, dict) and c.get("confidence", 1) < 0.6]
                                    if high_risk_ev:
                                            st.markdown("**Potential Risks:**")
                                            for e in high_risk_ev[:5]:
                                                    st.markdown(f"- {e.get('snippet', 'N/A')[:200]}")
                                    if low_conf_claims:
                                            st.markdown("**Areas Needing More Evidence:**")
                                            for c in low_conf_claims[:5]:
                                                    st.markdown(f"- {c.get('claim_text', 'N/A')[:200]}")
                                    if not high_risk_ev and not low_conf_claims:
                                            st.info("Analysis is in progress. Risk & Opportunity section will be populated soon.")
                            else:
                                    st.info("Risk & Opportunity section will be populated as analysis progresses.")
                    else:
                            st.info("Risk & Opportunity section will be populated once SWOT claims are generated.")

            # --- Sales Battlecard ---
            with st.expander("Sales Battlecard", expanded=False):
                    battlecard = None
                    if isinstance(report_data, dict):
                            battlecard = report_data.get("sales_battlecard") or report_data.get("battlecard")

                    if battlecard:
                            st.json(battlecard, expanded=False)
                    else:
                            battlecard_spans = [
                                    s for s in all_spans
                                    if "battlecard" in (s.get("section_title") or "").lower()
                                    or "battlecard" in (s.get("content_markdown") or "").lower()
                            ] if all_spans else []

                            if battlecard_spans:
                                    for span in battlecard_spans:
                                            st.markdown(f"**{span.get('section_title', 'Section')}**")
                                            st.markdown(span.get("content_markdown") or span.get("text") or "")
                            elif evidence_data:
                                    # Fallback: build battlecard from high-confidence claims
                                    st.markdown("**Sales Battlecard (auto-generated from evidence):**")

                                    products_in_run = sorted(set(e.get("product_id", "") for e in evidence_data if e.get("product_id")))
                                    for prod in products_in_run:
                                            prod_claims = [c for c in signed_claims if c.get("product_id") == prod]
                                            prod_ev = [e for e in evidence_data if e.get("product_id") == prod]
                                            if prod_claims:
                                                    strengths = [c.get("claim_text", "") for c in prod_claims if c.get("confidence", 0) >= 0.8][:3]
                                                    st.markdown(f"**{prod}**")
                                                    if strengths:
                                                            st.markdown("Key strengths:")
                                                            for s in strengths:
                                                                    st.markdown(f"  - {s}")
                                                    st.markdown(f"Evidence count: {len(prod_ev)}")
                                                    st.markdown("")
                            else:
                                    st.info("Sales Battlecard not yet available. This will be populated once evidence is collected.")

            # --- Evidence Appendix ---
            with st.expander("Evidence Appendix", expanded=False):
                    if evidence_data:
                            if is_blocked_run:
                                    st.markdown(":orange[This run was blocked at final quality gate — see **Recommended Rework Plan** above for next steps.]")
                                    st.divider()
                            st.markdown(
                                    f"**{len(evidence_data)} evidence items** collected from {len(sources_data)} sources"
                            )
                            # Summary by schema key
                            schema_keys = {}
                            for e in evidence_data:
                                    sk = (e.get("schema_key") or "unknown").split(".")[-1]
                                    schema_keys[sk] = schema_keys.get(sk, 0) + 1
                            st.markdown("**Evidence by Schema Key:**")
                            for k, v in sorted(schema_keys.items()):
                                    st.markdown(f"- `{k}`: {v} items")

                            # Summary by product
                            product_ev = {}
                            for e in evidence_data:
                                    p = e.get("product_id", "unknown")
                                    product_ev[p] = product_ev.get(p, 0) + 1
                            st.markdown("**Evidence by Product:**")
                            for k, v in sorted(product_ev.items()):
                                    st.markdown(f"- `{k}`: {v} items")

                            st.markdown("")
                            st.caption("See **Evidence Hub** for full evidence details.")
                    elif sources_data:
                            st.markdown(f"**{len(sources_data)} sources** collected, evidence extraction in progress.")
                            src_types = {}
                            for s in sources_data:
                                    t = s.get("source_type", "unknown")
                                    src_types[t] = src_types.get(t, 0) + 1
                            st.markdown("**Sources by Type:**")
                            for k, v in sorted(src_types.items()):
                                    st.markdown(f"- `{k}`: {v}")
                            st.info("Evidence extraction is in progress. Check back shortly.")
                    else:
                            st.info(
                                    "No evidence collected yet. Evidence collection starts after the analysis run begins."
                            )

            st.divider()

            st.caption(
                    ":warning: Human decisions are recorded. Resume/re-run will be implemented in backend next."
            )

            col_rep, col_ev, col_ws = st.columns(3)
            with col_rep:
                    if st.button("Full Report", key=f"af_deliv_report_{effective_run_id or 'none'}", use_container_width=True):
                            goto_page("分析报告")
            with col_ev:
                    if st.button("Evidence Hub", key=f"af_deliv_evidence_{effective_run_id or 'none'}", use_container_width=True):
                            goto_page("Evidence Hub")
            with col_ws:
                    if st.button("Project Workspace", key=f"af_deliv_workspace_{effective_run_id or 'none'}", use_container_width=True):
                            goto_page("Project Workspace")


# ---------------------------------------------------------------------------
# Page: Legacy Task (old "任务设置" page - kept for debug compatibility only)
# NOT accessible from main NAV; use New Analysis (Project Wizard) instead.
# ---------------------------------------------------------------------------


if page == "LegacyTask":
    st.header("任务设置")

    task_brief = {
            "task_goal": "分析 AI Agent 产品竞品情况",
            "target_region": "全球",
            "products": [
                    {"product_id": "dify",     "product_name": "Dify",     "seed_urls": ["https://dify.ai"]},
                    {"product_id": "coze",     "product_name": "Coze",     "seed_urls": ["https://www.coze.com"]},
                    {"product_id": "fastgpt",  "product_name": "FastGPT",  "seed_urls": ["https://fastgpt.cn"]},
                    {"product_id": "flowise",  "product_name": "Flowise",  "seed_urls": ["https://flowiseai.com"]},
            ],
            "analysis_dimensions": ["function_tree", "pricing_model", "user_persona",
                                                            "customer_voice", "swot", "enterprise_readiness"],
            "required_output": {
                    "report_format": "html",
                    "include_evidence_links": True,
                    "include_swot": True,
                    "include_comparison_table": True,
            },
            "constraints": {
                    "max_sources_per_product": 8,
                    "allow_web_search": True,
                    "allow_uploaded_docs": True,
                    "allow_survey_data": True,
                    "allow_interview_data": True,
            },
    }

    col1, col2 = st.columns([2, 1])
    with col1:
            with st.expander("任务配置（JSON）", expanded=True):
                    st.json(task_brief)
    with col2:
            st.markdown("### 概览")
            st.metric("产品数", len(task_brief["products"]))
            st.metric("分析维度", len(task_brief["analysis_dimensions"]))
            st.metric("单产品最大来源数", task_brief["constraints"]["max_sources_per_product"])

    st.divider()
    ca, cb = st.columns([1, 1])
    with ca:
            st.subheader("发起新 Run")
            title_in = st.text_input("任务标题", value="AI Agent 产品竞品分析")
            mode_opts = {"回放模式（演示）": "replay", "缓存模式": "cached", "实时采集": "real_time"}
            mode_lbl = st.selectbox("执行模式", list(mode_opts.keys()), index=0)
            mode = mode_opts[mode_lbl]
            if mode == "real_time":
                    st.caption(
                            "实时采集：系统根据 official_website / seed_urls 抓取公开网页，"
                            "生成 Source → Snapshot → Evidence → Fact，再进入 Analyst → "
                            "Reviewer → Writer 流程。LLM 超时会自动使用模板 Fallback，不中断流程。"
                    )
            if st.button("创建并启动分析任务", type="primary", width="stretch"):
                    # Step 1: Create run
                    try:
                            resp = requests.post(
                                    f"{API_BASE}/api/runs",
                                    json={"task_title": title_in, "task_brief": task_brief, "mode": mode},
                                    timeout=15,
                            )
                            resp.raise_for_status()
                            result = resp.json()
                            new_run_id = result.get("run_id") or result.get("id")
                            new_proj_id = result.get("project_id")
                    except requests.exceptions.RequestException as e:
                            st.session_state["last_start_error"] = f"创建 Run 失败: {e}"
                            new_run_id = None
                            new_proj_id = None

                    # Step 2: Start async — returns immediately
                    if new_run_id and new_proj_id:
                            ok = start_run_async_and_go_to_running(new_proj_id, new_run_id)
                            if not ok:
                                    st.stop()


    with cb:
            st.subheader("历史 Run")
            try:
                    resp = requests.get(f"{API_BASE}/api/runs", timeout=10)
                    if resp.status_code == 200:
                            runs = resp.json()
                            if runs:
                                    st.info(f"共 {len(runs)} 条记录")
                                    for r in runs[-5:]:
                                            rid = r.get("run_id") or r.get("id", "unknown")
                                            st.text(f"  {rid}  [{r.get('status', '')}]")
                            else:
                                    st.info("暂无历史记录")
            except Exception:
                    st.info("历史记录不可用")

# ---------------------------------------------------------------------------
# Page: Agent 团队
# ---------------------------------------------------------------------------


    # -------------------------------------------------------------------------


elif page == "Agents":
    st.header("Agent 团队")

    agents = [
            {
                    "name": "Orchestrator", "icon": "🎛️",
                    "desc": "编排 Agent 是整个系统的中央控制器。它读取任务简报、协调子 Agent 调用、维护工作流状态，并在节点失败时决定重试或回放策略。",
                    "responsibilities": [
                            "初始化多 Agent 工作流",
                            "在 Agent 之间路由任务",
                            "管理重试逻辑和反压机制",
                            "失败时触发回放模式",
                    ],
                    "forbidden": [
                            "跳过证据校验",
                            "绕过质检门禁",
                            "无审计地修改来源数据",
                    ],
                    "inputs": ["TaskBrief JSON", "agent_config YAML"],
                    "outputs": ["路由决策", "工作流状态"],
            },
            {
                    "name": "Collector Agent", "icon": "🔍",
                    "desc": "采集 Agent 负责所有外部数据收集。它发现相关来源、抓取内容，并将结构化证据项草稿准备好供分析 Agent 使用。",
                    "responsibilities": [
                            "为每个产品发现和收集来源 URL",
                            "对关键网页进行快照",
                            "生成带引用的结构化证据草稿",
                            "跟踪各产品的采集进度",
                    ],
                    "forbidden": [
                            "不遵守 robots.txt 抓取网站",
                            "采集超出目标产品范围的数据",
                    ],
                    "inputs": ["产品列表", "种子 URL", "单产品最大来源数"],
                    "outputs": ["证据项", "来源快照", "采集报告"],
            },
            {
                    "name": "Analyst Agent", "icon": "🧠",
                    "desc": "分析 Agent 将原始证据转化为结构化竞品洞察。它生成带置信度和风险等级的维度标签 Claims，准备好供质检 Agent 校验。",
                    "responsibilities": [
                            "运行多维度分析（SWOT、定价、用户画像等）",
                            "按维度起草结构化 Claims",
                            "为 Claims 分配置信度和风险等级",
                            "将每个 Claim 映射到对应证据 ID",
                    ],
                    "forbidden": [
                            "无证据支撑推论 Claim",
                            "无正当理由虚高置信度",
                    ],
                    "inputs": ["证据项", "分析 Schema", "维度列表"],
                    "outputs": ["Claim 草稿", "置信度评分", "风险评估"],
            },
            {
                    "name": "Reviewer Agent", "icon": "✅",
                    "desc": "质检 Agent 扮演质量门禁角色。它对每个 Claim 运行证据和 Schema 校验，通过则签发，否则打回给对应 Agent 重做。",
                    "responsibilities": [
                            "对照证据校验每个 Claim",
                            "检查 Schema 完整性和一致性",
                            "对低质量 Claim 发出 Rework 指令",
                            "批准通过质检的 Claims（签发）",
                    ],
                    "forbidden": [
                            "签发有未解决矛盾的 Claims",
                            "签发 evidence_coverage < 0.6 的 Claims",
                    ],
                    "inputs": ["Claim 草稿", "证据项", "质检 Schema"],
                    "outputs": ["已签发 Claims", "Rework 指令", "质检报告"],
            },
            {
                    "name": "Writer Agent", "icon": "✍️",
                    "desc": "写作 Agent 仅消费已签发 Claims，渲染最终竞品分析报告。它是报告导出前的最后一个活跃节点。",
                    "responsibilities": [
                            "将已签发 Claims 组装成报告章节",
                            "从结构化章节数据渲染 Markdown/HTML",
                            "每节包含证据引用和来源链接",
                            "为报告附加质量摘要元数据",
                    ],
                    "forbidden": [
                            "省略报告章节中的证据引用",
                            "包含未签发或待打回的 Claims",
                    ],
                    "inputs": ["已签发 Claims", "报告模板", "证据映射"],
                    "outputs": ["报告章节", "最终报告 HTML", "质量摘要"],
            },
    ]

    cols = st.columns(2)
    for idx, a in enumerate(agents):
            with cols[idx % 2]:
                    with st.expander(f"{a['icon']} {a['name']}", expanded=(idx == 0)):
                            st.markdown(f"**{a['desc']}**")
                            c1, c2 = st.columns(2)
                            with c1:
                                    st.markdown("**职责**")
                                    for r in a["responsibilities"]:
                                            st.write(f"- {r}")
                            with c2:
                                    st.markdown("**禁止行为**")
                                    for f in a["forbidden"]:
                                            st.write(f"- :red[{f}]")
                            st.markdown("**输入：** " + "、".join(a["inputs"]))
                            st.markdown("**输出：** " + "、".join(a["outputs"]))

# ---------------------------------------------------------------------------
# Page: DAG 执行
# ---------------------------------------------------------------------------

elif page == "DAG":
    st.header("DAG 执行")
    st.info("本页展示系统由多个 Agent 通过 DAG 节点协作完成的工作流。")
    render_run_banner(run_id)

    # ---- New: Workflow Status Section (using helper) ----
    st.divider()
    render_workflow_status(run_id, compact=False)

    # ---- Human Interventions (using helper) ----
    st.divider()
    render_human_interventions(run_id, compact=False)

    # ---- Legacy DAG View (collapsed by default) ----
    st.divider()
    st.info("**Legacy DAG View**: This is the legacy static DAG view. The authoritative workflow state is shown above.")

    with st.expander("Legacy DAG View (old static demo)", expanded=False):
            # 每个节点：key=后端返回的node_name, name=中文展示名
            dag_nodes = [
                    {"key": "build_task_brief",  "name": "构建任务简报",  "agent": "Orchestrator",
                      "desc": "解析任务配置为结构化 TaskBrief。校验产品、维度和约束条件，生成初始工作流状态。"},
                    {"key": "plan_schema",        "name": "规划分析 Schema",  "agent": "Orchestrator",
                      "desc": "基于请求的维度（SWOT、定价、用户画像等）设计分析 Schema。定义每个 Claim 必须包含的字段。"},
                    {"key": "plan_sources",      "name": "规划来源采集",  "agent": "Collector",
                      "desc": "为每个产品生成来源采集计划。确定目标 URL 类别、搜索词和优先级排序。"},
                    {"key": "collect_sources",   "name": "采集来源",      "agent": "Collector",
                      "desc": "执行来源采集计划。抓取页面、做快照、提取结构化数据，将原始证据项存入证据库。"},
                    {"key": "pii_scrub",         "name": "PII 数据脱敏",  "agent": "Orchestrator",
                      "desc": "扫描所有采集内容中的个人身份信息。在证据传给分析 Agent 前完成脱敏处理。"},
                    {"key": "extract_facts",     "name": "抽取结构化事实", "agent": "Collector",
                      "desc": "将原始来源内容解析为结构化事实单元。按产品和主题分组，附上来源归属标签。"},
                    {"key": "analyze_dimensions","name": "多维度分析",    "agent": "Analyst",
                      "desc": "对抽取的事实应用每个分析维度。起草带置信度、风险等级和证据映射的 Claims。"},
                    {"key": "review_claims",      "name": "质检 Claims",   "agent": "Reviewer",
                      "desc": "对照证据和 Schema 校验所有起草的 Claims。通过的签发，有问题的生成 Rework 指令打回到前置节点。"},
                    {"key": "write_report",      "name": "撰写报告",      "agent": "Writer",
                      "desc": "将已签发 Claims 组装为结构化报告章节。渲染带引用和证据链接的 Markdown/HTML，计算质量摘要。"},
                    {"key": "final_review",      "name": "最终质检",      "agent": "Reviewer",
                      "desc": "对完整报告进行最终质量检查。确认所有章节达标后再导出。"},
                    {"key": "export_report",     "name": "导出报告",      "agent": "Writer",
                      "desc": "按请求格式（HTML）导出最终报告。持久化报告并更新 Run 记录。"},
                    {"key": "compute_metrics",   "name": "计算质量指标",  "agent": "Orchestrator",
                      "desc": "聚合所有 Run 数据计算指标：Schema 完整率、证据覆盖率、无支撑 Claim 率、审核通过率等。"},
            ]

            status_data = get_json(f"/api/runs/{run_id}/dag-status")
            status_map = {}
            if status_data:
                    for node in status_data:
                            status_map[node.get("node_name", "")] = node.get("status", "pending")

            node_cols = st.columns(3)
            for i, node in enumerate(dag_nodes):
                    node_status = status_map.get(node["key"], "pending")
                    with node_cols[i % 3]:
                            with st.container():
                                    st.markdown(f"**{i + 1}. {node['name']}**")
                                    st.markdown(f"`{node['key']}`  |  _Agent: {node['agent']}_")
                                    st.write(_badge(node_status))
                                    with st.expander("详情"):
                                            st.write(node["desc"])
                                            st.text(f"状态：{node_status}")

            st.divider()
            if status_data:
                    completed = sum(1 for s in status_map.values() if s == "success")
                    total = len(dag_nodes)
                    st.progress(completed / total, text=f"{completed}/{total} 个节点已完成")

            st.info(
                    "**质检打回机制：** Reviewer 节点在发现问题时可循环回到「采集来源」「抽取事实」或「多维度分析」节点，"
                    "形成反馈回路，直到所有 Claims 通过质量门禁。"
            )

# ---------------------------------------------------------------------------
# Page: 证据池
# ---------------------------------------------------------------------------

elif page == "Evidence":
    st.header("证据池")
    st.info("本页展示所有 Claim 的证据来源，每条证据都绑定 source、snapshot、schema_key 和 snippet。")
    render_run_banner(run_id)

    all_evidence = get_json(f"/api/runs/{run_id}/evidence", [])
    if not all_evidence:
            st.warning("本 Run 暂无证据，后端可能尚未完成数据采集。")
    else:
            all_sources = get_json(f"/api/runs/{run_id}/sources", [])
            src_map = {s.get("source_id"): s for s in all_sources}
            for ev in all_evidence:
                    src = src_map.get(ev.get("source_id"), {})
                    ev["_src_title"] = src.get("title", "N/A")
                    ev["_src_url"] = src.get("url", "")
                    ev["_src_type"] = src.get("source_type", "N/A")

            # 筛选器
            st.subheader("筛选条件")
            f1, f2, f3, f4 = st.columns(4)
            pids = ["全部"] + sorted(set(e.get("product_id", "") for e in all_evidence))
            stypes = ["全部"] + sorted(set(e.get("_src_type", "") for e in all_evidence if e.get("_src_type")))
            skeys = ["全部"] + sorted(set(e.get("schema_key", "") for e in all_evidence))
            with f1:
                    sel_pid = st.selectbox("产品", pids)
            with f2:
                    sel_stype = st.selectbox("来源类型", stypes)
            with f3:
                    sel_skey = st.selectbox("Schema Key", skeys)
            with f4:
                    sel_pii = st.selectbox("PII 状态", ["全部", "已脱敏", "未脱敏"])

            filtered = all_evidence
            if sel_pid != "全部":
                    filtered = [e for e in filtered if e.get("product_id") == sel_pid]
            if sel_stype != "全部":
                    filtered = [e for e in filtered if e.get("_src_type") == sel_stype]
            if sel_skey != "全部":
                    filtered = [e for e in filtered if e.get("schema_key") == sel_skey]
            if sel_pii != "全部":
                    masked = (sel_pii == "已脱敏")
                    filtered = [e for e in filtered if bool(e.get("pii_masked")) == masked]

            st.markdown(f"**{len(filtered)} 条证据**（共 {len(all_evidence)} 条）")

            # 表格 + 详情并排
            tbl_col, det_col = st.columns([1, 1])
            with tbl_col:
                    st.subheader("证据列表")
                    rows = []
                    for e in filtered:
                            snippet = (e.get("snippet") or "")[:55] + ("…" if len(e.get("snippet") or "") > 55 else "")
                            pii = "✅" if e.get("pii_masked") in (1, True) else "❌"
                            rows.append({
                                    "evidence_id": e.get("evidence_id", ""),
                                    "产品": e.get("product_id", ""),
                                    "Schema字段": (e.get("schema_key") or "").split(".")[-1],
                                    "摘要": snippet,
                                    "置信度": f"{e.get('confidence', '')}",
                                    "PII": pii,
                            })
                    st.dataframe(rows, width="stretch", hide_index=True)

            with det_col:
                    st.subheader("证据详情")
                    opts = [f"{e.get('evidence_id')} — {e.get('product_id')} — {(e.get('schema_key') or '').split('.')[-1]}" for e in filtered]
                    opts.insert(0, "请选择一条证据…")
                    sel = st.selectbox("选择证据查看详情", opts, key="ev_sel")
                    if sel != "请选择一条证据…":
                            eid = sel.split(" — ")[0]
                            ev = next((e for e in filtered if e.get("evidence_id") == eid), None)
                            if ev:
                                    src = src_map.get(ev.get("source_id"), {})
                                    pii_str = "✅ 已脱敏" if ev.get("pii_masked") in (1, True) else "❌ 未脱敏"
                                    st.markdown("**Snippet（内容摘要）**")
                                    st.info(ev.get("snippet", "N/A"))
                                    if src.get("url"):
                                            st.markdown("**Source URL（来源链接）**")
                                            st.markdown(f"[{src.get('url')}]({src.get('url')})")
                                    meta = [
                                            ("Evidence ID（证据ID）", ev.get("evidence_id", "N/A")),
                                            ("Source Title（来源标题）", src.get("title", "N/A")),
                                            ("Source Type（来源类型）", src.get("source_type", "N/A")),
                                            ("Schema Key（Schema 键）", ev.get("schema_key", "N/A")),
                                            ("Product（产品）", ev.get("product_id", "N/A")),
                                            ("Confidence（置信度）", str(ev.get("confidence", "N/A"))),
                                            ("PII Status（PII状态）", pii_str),
                                    ]
                                    for k, v in meta:
                                            st.text(f"{k}：{v}")

            # 全部展开
            st.divider()
            st.subheader("全部证据（可展开）")
            for idx, ev in enumerate(filtered):
                    src = src_map.get(ev.get("source_id"), {})
                    pii_str = "✅ 已脱敏" if ev.get("pii_masked") in (1, True) else "❌ 未脱敏"
                    with st.expander(
                            f"**{idx + 1}. `{ev.get('evidence_id', '')}`** | "
                            f"{ev.get('product_id', '')} | "
                            f"schema: `{(ev.get('schema_key') or '').split('.')[-1]}` | "
                            f"置信度: {ev.get('confidence', 'N/A')} | {pii_str}",
                            expanded=False,
                    ):
                            lc, rc = st.columns([1, 1])
                            with lc:
                                    st.markdown("**Snippet（内容）**")
                                    st.text_area("snip", ev.get("snippet", "N/A"), height=100, disabled=True, label_visibility="collapsed", key=f"snip{idx}")
                            with rc:
                                    st.markdown("**Source（来源）**")
                                    st.text(f"标题：{src.get('title', 'N/A')}")
                                    if src.get("url"):
                                            st.markdown(f"[URL]({src.get('url')})")
                                    st.text(f"类型：{src.get('source_type', 'N/A')}")
                                    ts = (src.get("fetched_at") or "N/A")[:16].replace("T", " ")
                                    st.text(f"抓取时间：{ts}")

# ---------------------------------------------------------------------------
# Page: 质检与打回
# ---------------------------------------------------------------------------

elif page == "Review":
    st.header("质检与打回")
    st.info("本页展示质检 Agent 如何检查 Claim，发现缺证或 Schema 缺失后生成 ReworkRequest，并打回对应 Agent 重做。")
    render_run_banner(run_id)

    data = get_json(f"/api/runs/{run_id}/review-items", {})
    if not data:
            st.warning("本 Run 暂无质检数据。")
    else:
            claims = data.get("claims", [])
            rework_requests = data.get("rework_requests", [])
            reviews = data.get("reviews", [])

            signed = [c for c in claims if c.get("review_status", "").lower() == "signed"]
            succeeded_rw = [r for r in rework_requests if r.get("status") == "succeeded"]

            st.subheader("质检概览")
            r_cols = st.columns(5)
            with r_cols[0]:
                    st.metric("总 Claim 数", len(claims))
            with r_cols[1]:
                    st.metric("已签发", len(signed))
            with r_cols[2]:
                    st.metric("打回请求", len(rework_requests))
            with r_cols[3]:
                    rate = len(signed) / len(claims) if claims else 0
                    st.metric("审核通过率", f"{rate:.0%}")
            with r_cols[4]:
                    rw_rate = len(succeeded_rw) / len(rework_requests) if rework_requests else 0
                    st.metric("打回成功率", f"{rw_rate:.0%}")

            st.divider()

            # Claims 列表
            if claims:
                    st.subheader(f"Claim 列表（共 {len(claims)} 条）")
                    rows = []
                    for c in claims:
                            txt = (c.get("claim_text", "")[:65] + "…" if len(c.get("claim_text", "")) > 65 else c.get("claim_text", ""))
                            rows.append({
                                    "claim_id": c.get("claim_id", ""),
                                    "产品": c.get("product_id", ""),
                                    "维度": c.get("dimension", ""),
                                    "Claim 内容": txt,
                                    "置信度": f"{c.get('confidence', '')}",
                                    "风险": c.get("risk_level", ""),
                                    "状态": _badge(c.get("review_status", "")),
                            })
                    st.dataframe(rows, width="stretch", hide_index=True)

            st.divider()

            # Rework 时间线
            if rework_requests:
                    st.subheader(f"Rework 时间线（共 {len(rework_requests)} 条打回指令）")
                    for r in rework_requests:
                            # 后端已归一化：metrics_before / metrics_after（dict）
                            mb = r.get("metrics_before", {})
                            ma = r.get("metrics_after", {})

                            rw_status = r.get("status", "unknown")
                            rw_color = {"succeeded": "green", "failed": "red", "failed_permanently": "red"}.get(rw_status, "orange")
                            reason_zh = {
                                    "MISSING_EVIDENCE": "证据缺失",
                                    "SCHEMA_FIELD_MISSING": "Schema 字段缺失",
                                    "LOW_CONFIDENCE": "置信度过低",
                                    "CONTRADICTION": "内容矛盾",
                            }
                            reason_codes = [reason_zh.get(rc, rc) for rc in r.get("reason_codes", [])]
                            target_zh = {
                                    "collector_agent": "采集 Agent",
                                    "extractor_agent": "抽取 Agent",
                                    "analyst_agent": "分析 Agent",
                            }

                            with st.expander(
                                    f"**Rework `{r.get('rework_id', '')}`** | "
                                    f"→ {target_zh.get(r.get('target_agent', ''), r.get('target_agent', ''))} @ "
                                    f"`{r.get('target_node', '')}` | "
                                    f"原因：{', '.join(reason_codes) or 'N/A'} | "
                                    f"重试：{r.get('retry_count', 0)} 次 | "
                                    f":{rw_color}[{rw_status}]",
                                    expanded=False,
                            ):
                                    # 基本信息
                                    ic1, ic2 = st.columns(2)
                                    with ic1:
                                            st.markdown("**基本信息**")
                                            st.text(f"打回 ID：{r.get('rework_id', 'N/A')}")
                                            st.text(f"目标 Agent：{target_zh.get(r.get('target_agent', ''), r.get('target_agent', ''))}")
                                            st.text(f"目标节点：{r.get('target_node', 'N/A')}")
                                            st.text(f"状态：{rw_status}")
                                            st.text(f"重试次数：{r.get('retry_count', 0)} / {r.get('max_retry', 'N/A')}")
                                    with ic2:
                                            st.markdown("**打回原因**")
                                            for rc in reason_codes:
                                                    st.text(f"  - {rc}")

                                    st.markdown("**受影响对象**")
                                    for obj in r.get("affected_objects", []):
                                            st.text(f"  - {obj.get('object_type', '')}：{obj.get('object_id', '')}")

                                    st.markdown("**需要补救的动作**")
                                    for act in r.get("required_actions", []):
                                            st.text(f"  - {json.dumps(act, ensure_ascii=False)}")

                                    st.markdown("**成功条件**")
                                    for k, v in r.get("success_criteria", {}).items():
                                            st.text(f"  {k}：{v}")

                                    # 打回前/后指标对比表
                                    st.markdown("**打回前 / 打回后指标对比**")
                                    overlap = set(mb.keys()) & set(ma.keys())
                                    if overlap:
                                            comp_rows = []
                                            for k in sorted(overlap):
                                                    b, a = mb.get(k), ma.get(k)
                                                    if isinstance(b, float) and isinstance(a, float):
                                                            delta = a - b
                                                            if 0 <= abs(b) <= 1:
                                                                    comp_rows.append({
                                                                            "指标": k,
                                                                            "打回前": f"{b:.1%}",
                                                                            "打回后": f"{a:.1%}",
                                                                            "变化": f"{delta:+.1%}",
                                                                    })
                                                            else:
                                                                    comp_rows.append({
                                                                            "指标": k,
                                                                            "打回前": f"{b:.2f}",
                                                                            "打回后": f"{a:.2f}",
                                                                            "变化": f"{delta:+.2f}",
                                                                    })
                                                    else:
                                                            comp_rows.append({"指标": k, "打回前": b, "打回后": a, "变化": "—"})
                                            st.dataframe(comp_rows, width="stretch", hide_index=True)
                                    else:
                                            st.text(f"打回前：{mb}")
                                            st.text(f"打回后：{ma}")

                                    if rw_status == "succeeded":
                                            st.success(f"✅ 打回成功：{r.get('rework_id')} 已完成补救，指标达到成功条件。")
                                    elif rw_status == "failed":
                                            st.error(f"🔴 打回失败：{r.get('rework_id')} 未达到成功条件。")

                                    with st.expander("原始 JSON"):
                                            st.json(r, expanded=False)
            else:
                    st.success("✅ 暂无打回请求，所有 Claims 一次审核通过。")

            st.divider()

            # Reviews 明细
            if reviews:
                    st.subheader(f"审核记录（共 {len(reviews)} 条）")
                    for idx, rev in enumerate(reviews):
                            verdict_zh = {"pass": "通过", "fail": "不通过"}.get(rev.get("status", ""), rev.get("status", "N/A"))
                            with st.expander(
                                    f"审核：`{rev.get('review_target_id', 'unknown')}` | "
                                    f"审核 Agent：{rev.get('reviewer_agent', 'unknown')} | "
                                    f"结论：{verdict_zh}",
                                    expanded=False,
                            ):
                            # 后端已归一化：checks（list）
                                    checks = rev.get("checks", [])

                                    for ch in checks:
                                            icon = "✅" if ch.get("status") == "pass" else "❌"
                                            st.text(f"  {icon} {ch.get('check_name', '')}：{ch.get('details', '')}")
                                    if rev.get("comments"):
                                            st.markdown(f"**备注：** {rev.get('comments')}")
                                    with st.expander("原始 JSON"):
                                            st.json(rev, expanded=False)

# ---------------------------------------------------------------------------
# Page: 分析报告
# ---------------------------------------------------------------------------

elif page == "Report":
    st.header("分析报告")
    st.info("本页展示由 Signed Claims 渲染的结构化报告，每个段落都能回链 evidence。")
    render_run_banner(run_id)

    # Determine if this is a demo/replay run
    run_info = get_json(f"/api/runs/{run_id}", {})
    is_demo = (run_id == "run_demo_ai_agent_001") or (run_info.get("mode") in ("replay", "cached"))
    if is_demo:
            st.warning("当前显示的是 demo/replay run，不代表最新 real_time 结果。")
    else:
            st.success("当前显示的是最新 real_time run。")

    report = get_json(f"/api/runs/{run_id}/report", {})
    if not report:
            st.warning("本 Run 暂无报告。")
    else:
            spans = report.get("spans", []) or report.get("sections", [])

            qs = report.get("quality_summary", {})
            if isinstance(qs, str):
                    try:
                            qs = json.loads(qs)
                    except Exception:
                            qs = {}
            metrics_data = get_json(f"/api/runs/{run_id}/metrics", {})
            for k in ("schema_completion_rate", "unsupported_claim_rate", "review_pass_rate"):
                    if k not in qs and k in (metrics_data or {}):
                            qs[k] = metrics_data.get(k)
            if "evidence_coverage_rate" not in qs:
                    qs["evidence_coverage_rate"] = metrics_data.get("evidence_coverage_rate")

            claim_count = qs.get("claim_count", len(spans))
            coverage = qs.get("evidence_coverage_rate", 0.0)
            unsupported = qs.get("unsupported_claim_count", 0)
            report_status = report.get("report_status", "draft")

            # ---- Beautiful HTML Report Preview ----
            st.subheader("HTML 报告预览")
            col_meta, col_btn = st.columns([3, 1])
            with col_meta:
                    status_icon = {"reviewed": "✅", "exported": "📤", "draft": "📝", "blocked": "🚫"}.get(report_status, "📄")
                    st.markdown(
                            f"**{status_icon} 状态：** `{report_status}` &nbsp;&nbsp; "
                            f"**📊 Claims：** `{claim_count}` &nbsp;&nbsp; "
                            f"**🔗 证据覆盖率：** `{int(coverage * 100)}%` &nbsp;&nbsp; "
                            f"**📑 章节：** `{len(spans)}`"
                    )
            with col_btn:
                    html_path = report.get("content_html_path", "")
                    if html_path:
                            btn_label = "📄 打开 HTML 报告"
                    else:
                            btn_label = "📄 暂无 HTML（请重新生成）"

            # Embed HTML report in an iframe, with markdown fallback if file is missing
            if html_path:
                    html_url = f"{API_BASE}/api/runs/{run_id}/report/html?ts={int(time.time())}"
                    # Probe: check if the HTML file actually exists on disk
                    try:
                        probe_resp = requests.head(html_url.split("?")[0], timeout=5, allow_redirects=True)
                        html_available = probe_resp.status_code == 200
                    except Exception:
                        html_available = False

                    if html_available:
                        st.markdown(
                                f'<iframe src="{html_url}" width="100%" height="680" '
                                f'style="border:1px solid #e2e8f0;border-radius:12px;'
                                f'margin-top:8px;" title="HTML Report Preview"></iframe>',
                                unsafe_allow_html=True,
                        )
                    else:
                        # Fallback: show markdown content directly
                        md_content = report.get("content_markdown", "") or ""
                        if md_content:
                            st.markdown("### 📄 报告内容 (Markdown)")
                            st.markdown(md_content)
                            st.caption(
                                f"HTML 报告文件未找到 ({html_path})。"
                                f"尝试运行 `python scripts/generate_html_report.py --run-id {run_id}` 重新生成。"
                            )
                        else:
                            st.warning(
                                f"报告内容为空。HTML 文件未找到 (`{html_path}`)。"
                                f"请运行 `python scripts/generate_html_report.py --run-id {run_id}` 生成。"
                            )

            st.divider()

            # Quality Summary
            st.subheader("报告质量摘要")
            q_cols = st.columns(4)
            fields = [
                    ("schema_completion_rate", "Schema 完整率"),
                    ("evidence_coverage_rate", "证据覆盖率"),
                    ("unsupported_claim_rate", "无支撑结论率"),
                    ("review_pass_rate", "审核通过率"),
            ]
            for i, (k, label) in enumerate(fields):
                    val = qs.get(k)
                    with q_cols[i % 4]:
                            if val is not None:
                                    if isinstance(val, float) and k == "unsupported_claim_rate":
                                            if val == 0:
                                                    st.metric(label, "✅ 0%")
                                            else:
                                                    st.metric(label, f"{val:.1%}")
                                    else:
                                            st.metric(label, _fmt_rate(val))
                            else:
                                    st.metric(label, "N/A")

            st.divider()

            # 报告章节
            st.subheader(f"报告章节（共 {len(spans)} 节）")
            for si, span in enumerate(spans):
                    title = span.get("section_title", span.get("title", f"第 {si + 1} 节"))
                    text = span.get("content_markdown") or span.get("text") or span.get("content") or ""

                    claim_ids = span.get("claim_ids", [])
                    evidence_ids = span.get("evidence_ids", [])

                    unsupported = span.get("unsupported_flag", 0)
                    flag_label = "⚠️ 无支撑" if unsupported in (1, True) else "✅ 有支撑"
                    flag_color = "red" if unsupported in (1, True) else "green"

                    with st.expander(f"**{si + 1}. {title}** [{flag_label}]", expanded=True):
                            if text:
                                    st.markdown(text)
                            else:
                                    st.info("本节暂无内容。")

                            cc1, cc2 = st.columns([1, 1])
                            with cc1:
                                    if claim_ids:
                                            st.markdown(f"**Claim IDs（{len(claim_ids)} 条）：**")
                                            st.markdown(" ".join(f"`{cid}`" for cid in claim_ids))
                            with cc2:
                                    if evidence_ids:
                                            st.markdown(f"**Evidence IDs（{len(evidence_ids)} 条）：**")
                                            for eid in evidence_ids:
                                                    ev = get_json(f"/api/evidence/{eid}")
                                                    if ev:
                                                            snip = (ev.get("snippet") or "")[:45] + "…"
                                                    else:
                                                            snip = "N/A"
                                                    st.markdown(f"  - `{eid}` — {snip}")

                            st.markdown(f"**支撑状态：** :{flag_color}[{'无支撑' if unsupported in (1, True) else '有支撑'}]")

                            # Evidence 详情
                            if evidence_ids:
                                    st.markdown("**Evidence 详情（可展开）**")
                                    for eid in evidence_ids:
                                            ev = get_json(f"/api/evidence/{eid}")
                                            src_map_local = {}
                                            if not ev:
                                                    all_src = get_json(f"/api/runs/{run_id}/sources", [])
                                                    src_map_local = {s.get("source_id"): s for s in all_src}
                                            else:
                                                    all_src = get_json(f"/api/runs/{run_id}/sources", [])
                                                    src_map_local = {s.get("source_id"): s for s in all_src}
                                            src = src_map_local.get(ev.get("source_id"), {}) if ev else {}

                                            if ev:
                                                    pii_str = "✅ 已脱敏" if ev.get("pii_masked") in (1, True) else "❌ 未脱敏"
                                                    with st.expander(f"证据：`{eid}`", expanded=False):
                                                            lc, rc = st.columns([1, 1])
                                                            with lc:
                                                                    st.markdown("**Snippet**")
                                                                    st.info(ev.get("snippet", "N/A"))
                                                                    if src.get("url"):
                                                                            st.markdown(f"**Source URL：** [{src.get('url')}]({src.get('url')})")
                                                            with rc:
                                                                    meta = [
                                                                            ("Evidence ID", ev.get("evidence_id", "N/A")),
                                                                            ("Source Title", src.get("title", "N/A")),
                                                                            ("Source Type", src.get("source_type", "N/A")),
                                                                            ("Schema Key", ev.get("schema_key", "N/A")),
                                                                            ("Product", ev.get("product_id", "N/A")),
                                                                            ("Confidence", str(ev.get("confidence", "N/A"))),
                                                                            ("PII Status", pii_str),
                                                                    ]
                                                                    for k, v in meta:
                                                                            st.text(f"{k}：{v}")
                                            else:
                                                    st.text(f"证据 `{eid}` 未找到。")

                            with st.expander("原始 JSON"):
                                    st.json(span, expanded=False)

            with st.expander("完整报告 JSON"):
                    st.json(report, expanded=False)

# ---------------------------------------------------------------------------
# Page: 执行追踪
# ---------------------------------------------------------------------------

elif page == "Trace":
    st.header("执行追踪")
    st.info("本页展示每个 Agent 节点的输入、输出、Token、耗时和决策过程。")
    render_run_banner(run_id)

    data = get_json(f"/api/runs/{run_id}/traces", [])
    if not data:
            st.warning("本 Run 暂无 Trace 数据。")
    else:
            if isinstance(data, dict) and "traces" in data:
                    data = data["traces"]

            st.metric("Trace 事件总数", len(data))

            st.subheader("Trace 时间线（按执行顺序）")
            status_opts = st.multiselect("按状态筛选", ["success", "failed", "retry", "pending"],
                                                                        default=["success", "failed", "retry", "pending"])
            filtered = [t for t in data if t.get("status", "") in status_opts]

            for ti, t in enumerate(filtered):
                    ts = t.get("status", "unknown")
                    tc = {"success": "green", "failed": "red", "retry": "orange", "pending": "gray"}.get(ts, "gray")
                    tkin = t.get("token_input", 0) or 0
                    tkout = t.get("token_output", 0) or 0
                    with st.expander(
                            f"**{ti + 1}. `{t.get('node_name', '')}`** "
                            f"| Agent: {t.get('agent_name', 'N/A')} "
                            f"| :{tc}[{ts}] "
                            f"| ⏱ {t.get('latency_ms', '?')}ms "
                            f"| 📥 {tkin} tok "
                            f"| 📤 {tkout} tok "
                            f"| model={t.get('model_name', '?')} "
                            f"| prompt={t.get('prompt_version', '?')}",
                            expanded=False,
                    ):
                            ic, oc, ec = st.columns(3)
                            with ic:
                                    st.markdown("**输入**")
                                    st.text(f"节点：{t.get('node_name', 'N/A')}")
                                    st.text(f"Agent：{t.get('agent_name', 'N/A')}")
                                    st.text(f"Prompt 版本：{t.get('prompt_version', 'N/A')}")
                                    st.text(f"输入路径：{t.get('input_path', 'N/A')}")
                                    st.text(f"Token 输入：{tkin}")
                            with oc:
                                    st.markdown("**输出**")
                                    st.text(f"输出路径：{t.get('output_path', 'N/A')}")
                                    st.text(f"决策：{t.get('decision', 'N/A')}")
                                    st.text(f"Token 输出：{tkout}")
                                    st.text(f"总 Token：{tkin + tkout}")
                                    st.text(f"耗时：{t.get('latency_ms', 'N/A')}ms")
                            with ec:
                                    st.markdown("**状态**")
                                    st.text(f"状态：{ts}")
                                    st.text(f"模型：{t.get('model_name', 'N/A')}")
                                    ts_start = (t.get("started_at") or "N/A")[:19].replace("T", " ")
                                    ts_end = (t.get("completed_at") or "N/A")[:19].replace("T", " ")
                                    st.text(f"开始：{ts_start}")
                                    st.text(f"完成：{ts_end}")
                                    if t.get("error_message"):
                                            st.error(f"错误：{t.get('error_message')}")

                            with st.expander("原始 JSON"):
                                    st.json(t, expanded=False)

            st.divider()
            st.subheader("Trace 汇总表")
            rows = [{
                    "trace_id": t.get("trace_id", ""),
                    "节点": t.get("node_name", ""),
                    "Agent": t.get("agent_name", ""),
                    "状态": _badge(t.get("status", "")),
                    "耗时ms": t.get("latency_ms", ""),
                    "Token入": t.get("token_input", ""),
                    "Token出": t.get("token_output", ""),
                    "模型": t.get("model_name", ""),
                    "Prompt版本": t.get("prompt_version", ""),
                    "决策": t.get("decision", ""),
            } for t in data]
            st.dataframe(rows, width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# Page: 质量指标
# ---------------------------------------------------------------------------

elif page == "Metrics":
    st.header("质量指标")
    st.info("本页用指标量化 Schema 完整率、证据覆盖率、无支撑 Claim 率和打回成功率。")
    render_run_banner(run_id)

    data = get_json(f"/api/runs/{run_id}/metrics", {})
    if not data:
            st.warning("本 Run 暂无质量指标。")
    else:
            for k in ("metrics_json",):
                    if isinstance(data.get(k), str):
                            try:
                                    data[k] = json.loads(data[k])
                            except Exception:
                                    pass

            st.subheader("核心指标（8 项）")
            r1 = st.columns(4)
            val = data.get("schema_completion_rate")
            st.caption("schema_completion_rate")
            with r1[0]:
                    st.metric("Schema 完整率", _fmt_rate(val))
            val = data.get("evidence_coverage_rate")
            st.caption("evidence_coverage_rate")
            with r1[1]:
                    st.metric("证据覆盖率", _fmt_rate(val))
            val = data.get("unsupported_claim_rate")
            st.caption("unsupported_claim_rate")
            with r1[2]:
                    if isinstance(val, float) and val == 0:
                            st.metric("无支撑结论率", "✅ 0%")
                    else:
                            st.metric("无支撑结论率", _fmt_rate(val))
            val = data.get("review_pass_rate")
            st.caption("review_pass_rate")
            with r1[3]:
                    st.metric("审核通过率", _fmt_rate(val))

            r2 = st.columns(4)
            val = data.get("rework_success_rate")
            st.caption("rework_success_rate")
            with r2[0]:
                    st.metric("打回成功率", _fmt_rate(val))
            val = data.get("replay_success_rate")
            st.caption("replay_success_rate")
            with r2[1]:
                    st.metric("回放成功率", _fmt_rate(val))
            src_cnt = data.get("source_coverage_count", 0)
            st.caption("source_coverage_count")
            with r2[2]:
                    st.metric("来源类型覆盖", f"{src_cnt} 种来源类型")
            time_val = data.get("analysis_time_minutes")
            st.caption("analysis_time_minutes")
            with r2[3]:
                    st.metric("分析耗时", f"{time_val} 分钟" if time_val else "N/A")

            st.divider()

            # 进度条
            st.subheader("关键指标进度")
            pb_cols = st.columns(3)
            rate_keys = [
                    ("schema_completion_rate", "Schema 完整率"),
                    ("evidence_coverage_rate", "证据覆盖率"),
                    ("review_pass_rate", "审核通过率"),
                    ("rework_success_rate", "打回成功率"),
                    ("replay_success_rate", "回放成功率"),
            ]
            for i, (k, label) in enumerate(rate_keys):
                    val = data.get(k)
                    if val is not None and isinstance(val, float) and 0 <= val <= 1:
                            with pb_cols[i % 3]:
                                    st.progress(val, text=f"{label}：{val:.1%}")

            st.divider()

            # 计数指标
            st.subheader("计数指标")
            cnt_cols = st.columns(4)
            cnt_keys = [
                    ("source_coverage_count", "来源覆盖数"),
                    ("evidence_count", "证据总数"),
                    ("claim_count", "Claim 总数"),
                    ("conflict_count", "冲突数"),
            ]
            for i, (k, label) in enumerate(cnt_keys):
                    val = data.get(k)
                    with cnt_cols[i % 4]:
                            st.metric(label, val if val is not None else "N/A")

            # 打回前后对比
            mb = data.get("metrics_before", {})
            ma = data.get("metrics_after", {})

            if mb or ma:
                    st.divider()
                    st.subheader("打回前 / 打回后对比")
                    bc, ac = st.columns(2)
                    with bc:
                            st.markdown("**打回前**")
                            for k, v in mb.items():
                                    st.text(f"  {k}：{v}")
                    with ac:
                            st.markdown("**打回后**")
                            for k, v in ma.items():
                                    st.text(f"  {k}：{v}")

                    overlap = set(mb.keys()) & set(ma.keys())
                    if overlap:
                            diff_rows = []
                            for k in sorted(overlap):
                                    b, a = mb.get(k), ma.get(k)
                                    if isinstance(b, float) and isinstance(a, float):
                                            d = a - b
                                            if 0 <= abs(b) <= 1:
                                                    diff_rows.append({"指标": k, "打回前": f"{b:.1%}", "打回后": f"{a:.1%}", "变化": f"{d:+.1%}"})
                                            else:
                                                    diff_rows.append({"指标": k, "打回前": f"{b:.2f}", "打回后": f"{a:.2f}", "变化": f"{d:+.2f}"})
                                    else:
                                            diff_rows.append({"指标": k, "打回前": b, "打回后": a, "变化": "—"})
                            st.dataframe(diff_rows, width="stretch", hide_index=True)

            with st.expander("完整指标 JSON（调试用）"):
                    st.json(data, expanded=False)

# ---------------------------------------------------------------------------
# Page: 离线回放
# ---------------------------------------------------------------------------

elif page == "Replay":
    st.header("离线回放")
    st.info("本页证明系统可以使用已保存的快照、证据、报告和 Trace 离线回放，不依赖现场网络。")
    render_run_banner(run_id)

    st.success(
            "**回放模式使用已保存的网页快照、证据池、Claim、报告和 Trace，不依赖现场网络。**\n\n"
            "适合答辩现场稳定演示。"
    )

    ev_data = get_json(f"/api/runs/{run_id}/evidence", [])
    claims_data = get_json(f"/api/runs/{run_id}/review-items", {})
    report_data = get_json(f"/api/runs/{run_id}/report", {})
    traces_data = get_json(f"/api/runs/{run_id}/traces", [])
    metrics_data = get_json(f"/api/runs/{run_id}/metrics", {})

    total_claims = len(claims_data.get("claims", [])) if claims_data else 0
    report_status = (report_data.get("report_status", "unknown") if report_data else "unknown")
    replay_rate = metrics_data.get("replay_success_rate") if metrics_data else None
    replay_display = _fmt_rate(replay_rate) if replay_rate is not None else "N/A"

    st.subheader("快照数据概览")
    sc = st.columns(5)
    with sc[0]:
            st.metric("证据数量", len(ev_data))
    with sc[1]:
            st.metric("Claim 数量", total_claims)
    with sc[2]:
            st.metric("报告状态", report_status)
    with sc[3]:
            st.metric("Trace 数量", len(traces_data))
    with sc[4]:
            st.metric("回放成功率", replay_display)

    st.divider()

    st.subheader("导航至快照数据")
    nav_targets = [
            ("📄 打开报告", "分析报告"),
            ("🔍 打开证据池", "证据池"),
            ("🔎 打开执行追踪", "执行追踪"),
            ("📊 打开质量指标", "质量指标"),
    ]
    nav_cols = st.columns(len(nav_targets))
    for i, (lbl, _) in enumerate(nav_targets):
            with nav_cols[i]:
                    st.info(f"{lbl} → 请使用**侧边栏**切换到对应页面查看")

    st.divider()

    ca, cb = st.columns([1, 1])
    with ca:
            st.subheader("加载演示 Run")
            st.markdown(f"演示 Run（`{run_id}`）包含预采集的证据、Claim、审核记录和最终报告，全部本地缓存。")
            if st.button("加载演示 Run", type="primary", width="stretch"):
                    st.session_state["demo_loaded"] = True
                    st.success("✅ 演示数据加载成功，请通过侧边栏浏览各页面。")
            if st.session_state.get("demo_loaded"):
                    st.markdown("✅ 演示 Run 已加载，请使用侧边栏浏览各页面。")

    with cb:
            st.subheader("回放已保存的 Run")
            st.write(f"当前 Run ID：`{run_id}`")
            if st.button("执行回放", width="stretch"):
                    try:
                            resp = requests.post(f"{API_BASE}/api/runs/{run_id}/replay", timeout=15)
                            resp.raise_for_status()
                            st.success("✅ 回放完成。")
                            st.json(resp.json(), expanded=False)
                    except requests.exceptions.RequestException:
                            st.warning("回放端点不可用或 Run 未找到。")

    st.markdown(
            "**回放包含：** 已保存的证据快照、Trace 事件、Claim 草稿、审核输出、Rework 指令和最终报告 —— "
            "全部从缓存状态回放，无需任何网络请求。"
    )

# ---------------------------------------------------------------------------
# Page: 合规与隐私
# ---------------------------------------------------------------------------

elif page == "Compliance":
    st.header("合规与隐私")
    st.info("本页展示来源合规元数据和 PII 脱敏状态。")
    render_run_banner(run_id)

    sources = get_json(f"/api/runs/{run_id}/sources", [])
    if not sources:
            st.warning("本 Run 暂无来源合规数据。")
    else:
            ev_data = get_json(f"/api/runs/{run_id}/evidence", [])
            masked_cnt = sum(1 for e in ev_data if e.get("pii_masked") in (1, True))
            unmasked_cnt = len(ev_data) - masked_cnt

            st.subheader("PII 脱敏状态")
            pc = st.columns(3)
            with pc[0]:
                    st.metric("证据总数", len(ev_data))
            with pc[1]:
                    st.metric("已脱敏", masked_cnt)
            with pc[2]:
                    st.metric("未脱敏", unmasked_cnt)

            if unmasked_cnt == 0 and ev_data:
                    st.success("✅ 演示证据中无未脱敏 PII。")
            elif unmasked_cnt > 0:
                    st.warning(f"⚠️ {unmasked_cnt} 条证据存在未脱敏 PII。")

            st.divider()

            st.subheader(f"来源合规详情（共 {len(sources)} 条）")
            for si, src in enumerate(sources):
                    sid = src.get("source_id", f"src-{si}")
                    surl = src.get("url", "")
                    stitle = src.get("title", "N/A")
                    stype = src.get("source_type", "N/A")
                    sdomain = src.get("domain", "N/A")
                    scollect = src.get("collection_method", "N/A")
                    srobots = src.get("robots_status", "N/A")
                    terms = src.get("terms_note", "N/A")
                    trust = src.get("trust_tier", "N/A")
                    pii_stat = src.get("pii_status", "N/A")
                    fetched = (src.get("fetched_at") or "N/A")[:16].replace("T", " ")

                    trust_icon_map = {"high": "🟢", "medium": "🟡", "low": "🔴", "official": "🟢", "community": "🔵", "trusted": "🟢"}
                    trust_color_map = {"high": "green", "medium": "yellow", "low": "red", "official": "green", "community": "blue", "trusted": "green"}
                    pii_icon_map = {"clean": "🟢 无PII", "scrubbed": "🟡 已脱敏", "flagged": "🔴 有PII"}
                    trust_icon = trust_icon_map.get(trust, "⚪")
                    pii_icon = pii_icon_map.get(pii_stat, "⚪ 未知")

                    with st.expander(
                            f"{trust_icon} {stitle or surl} | "
                            f"可信度：{trust.upper() if trust else '未知'} | "
                            f"采集：{scollect} | "
                            f"{pii_icon}",
                            expanded=False,
                    ):
                            c1, c2, c3 = st.columns(3)
                            with c1:
                                    st.markdown("**来源信息**")
                                    st.text(f"来源 ID：`{sid}`")
                                    if surl:
                                            st.markdown(f"URL：[{surl[:60]}]({surl})")
                                    st.text(f"标题：{stitle}")
                                    st.text(f"类型：{stype}")
                                    st.text(f"域名：{sdomain}")
                            with c2:
                                    st.markdown("**合规属性**")
                                    st.text(f"采集方式：{scollect}")
                                    st.text(f"Robots.txt：{srobots}")
                                    st.text(f"条款说明：{terms}")
                                    tc = trust_color_map.get(trust, "gray")
                                    st.markdown(f"可信等级：:{tc}[{trust.upper() if trust else '未知'}]")
                            with c3:
                                    st.markdown("**PII / 抓取**")
                                    st.text(f"PII 状态：{pii_icon}")
                                    st.text(f"抓取时间：{fetched}")
                                    st.text(f"状态：{src.get('status', 'N/A')}")

                            with st.expander("原始 JSON"):
                                    st.json(src, expanded=False)

            st.divider()
            st.subheader("合规汇总表")
            rows = [{
                    "来源ID": s.get("source_id", ""),
                    "URL": (s.get("url") or "")[:50],
                    "采集方式": s.get("collection_method", ""),
                    "robots状态": s.get("robots_status", ""),
                    "可信等级": s.get("trust_tier", ""),
                    "PII状态": s.get("pii_status", ""),
            } for s in sources]
            st.dataframe(rows, width="stretch", hide_index=True)


# =============================================================================
# NEW PAGES: Production-first navigation
# =============================================================================

# ---------------------------------------------------------------------------
# Page: New Analysis (Project Wizard)
# ---------------------------------------------------------------------------

elif page == "NewAnalysis":
    st.session_state["current_page_zh"] = "Analysis Flow"
    st.rerun()



elif page == "Runs":
    from frontend.views.runs_page import render_runs_page
    render_runs_page()


elif page == "Projects":
    st.header("Projects")
    st.caption("All competitive analysis projects. Select one to view details or start a new run.")

    # Load projects
    try:
            resp = requests.get(f"{API_BASE}/api/projects", timeout=10)
            resp.raise_for_status()
            projects = resp.json()
    except requests.exceptions.RequestException as e:
            st.error(f"Failed to load projects: {e}")
            projects = []

    # Filter bar
    fcol1, fcol2 = st.columns([1, 4])
    with fcol1:
            status_filter = st.selectbox("Status", ["all", "active", "archived", "completed"])
    filtered = projects
    if status_filter != "all":
            filtered = [p for p in projects if p.get("status") == status_filter]

    st.info(f"Showing {len(filtered)} of {len(projects)} projects")

    if not filtered:
            st.warning("No projects found. Go to **New Analysis** to create one.")
            if st.button("Create New Analysis", type="primary"):
                    st.session_state["current_page_zh"] = "New Analysis"
                    st.rerun()
    else:
            for proj in filtered:
                    pid = proj.get("project_id", "unknown")
                    pname = proj.get("project_name", "Untitled")
                    ptype = proj.get("task_type", "")
                    region = proj.get("target_region", "global")
                    status = proj.get("status", "active")
                    dims = proj.get("analysis_dimensions", [])
                    created = (proj.get("created_at", "") or "")[:10]

                    with st.container():
                            cc = st.columns([3, 1, 1, 1, 1])
                            with cc[0]:
                                    st.markdown(f"**{pname}**")
                                    st.caption(f"ID: `{pid}` | Type: {ptype} | Region: {region}")
                            with cc[1]:
                                    st.markdown(f"**Status**")
                                    status_color = {"active": "green", "completed": "blue", "archived": "gray"}.get(status, "gray")
                                    st.markdown(f":{status_color}[{status}]")
                            with cc[2]:
                                    st.markdown(f"**Dims**")
                                    st.text(str(len(dims)))
                            with cc[3]:
                                    st.markdown(f"**Products**")
                                    st.text("—")
                            with cc[4]:
                                    st.markdown(f"**Created**")
                                    st.text(created)

                            # Action buttons
                            ba, bb, bc, bd = st.columns(4)
                            with ba:
                                    if st.button("View Details", key=f"view_{pid}"):
                                            st.session_state["selected_project_id"] = pid
                                            st.session_state["current_page_zh"] = "Project Workspace"
                                            st.rerun()
                            with bb:
                                    if st.button("Start Run", key=f"start_{pid}"):
                                            ok = start_run_async_and_go_to_running(pid)
                                            if not ok:
                                                    st.stop()
                            with bc:
                                    if st.button("Sources", key=f"src_{pid}"):
                                            st.session_state["selected_project_id"] = pid
                                            st.session_state["current_page_zh"] = "Sources"
                                            st.rerun()
                            with bd:
                                    if st.button("Evidence", key=f"ev_{pid}"):
                                            st.session_state["selected_project_id"] = pid
                                            goto_page("Evidence Hub")
                            st.divider()


# ---------------------------------------------------------------------------
# Page: Sources
# ---------------------------------------------------------------------------

elif page == "Sources":
    st.header("Sources")

    proj_id = st.session_state.get("selected_project_id")
    if proj_id:
            st.caption(f"Showing sources for project: `{proj_id}`")
            try:
                    resp = requests.get(f"{API_BASE}/api/projects/{proj_id}/sources", timeout=10)
                    resp.raise_for_status()
                    sources = resp.json()
            except requests.exceptions.RequestException:
                    sources = []
    else:
            # Fall back to run_id
            sources = get_json(f"/api/runs/{run_id}/sources", [])

    if not sources:
            st.warning("No sources found. Start an analysis run first.")
    else:
            st.metric("Total Sources", len(sources))
            src_types = sorted(set(s.get("source_type", "") for s in sources))
            st.text(f"Source types: {', '.join(src_types)}")

            rows = []
            for s in sources:
                    rows.append({
                            "source_id": s.get("source_id", ""),
                            "Product": s.get("product_slug", s.get("product_id", "")),
                            "Type": s.get("source_type", ""),
                            "Title": (s.get("title") or "")[:50],
                            "Domain": s.get("domain", ""),
                            "Trust": s.get("trust_tier", ""),
                            "Status": s.get("status", ""),
                    })
            st.dataframe(rows, width="stretch", hide_index=True)

            st.subheader("Source Details")
            for si, s in enumerate(sources):
                    with st.expander(f"{s.get('title', s.get('source_id', f'Source {si}'))}", expanded=False):
                            st.text(f"ID: {s.get('source_id')}")
                            st.text(f"Product: {s.get('product_slug', s.get('product_id', 'N/A'))}")
                            st.text(f"Type: {s.get('source_type')}")
                            url = s.get("url", "")
                            if url:
                                    st.markdown(f"URL: [{url}]({url})")
                            st.text(f"Domain: {s.get('domain')}")
                            st.text(f"Trust Tier: {s.get('trust_tier')}")
                            st.text(f"Collection: {s.get('collection_method')}")
                            st.text(f"Status: {s.get('status')}")


# ---------------------------------------------------------------------------
# Page: Evidence Hub
# ---------------------------------------------------------------------------

elif page == "EvidenceHub":
    st.header("Evidence Hub")
    st.info("All evidence items collected for a project, with full PII masking and source tracing.")

    proj_id = st.session_state.get("selected_project_id")
    if proj_id:
            st.caption(f"Evidence for project: `{proj_id}`")
            try:
                    resp = requests.get(f"{API_BASE}/api/projects/{proj_id}/evidence", timeout=10)
                    resp.raise_for_status()
                    evidence = resp.json()
            except requests.exceptions.RequestException:
                    evidence = []
    else:
            evidence = get_json(f"/api/runs/{run_id}/evidence", [])

    if not evidence:
            st.warning("No evidence found. Start an analysis run first.")
    else:
            st.metric("Total Evidence Items", len(evidence))

            # Filters
            f1, f2, f3 = st.columns(3)
            pids = ["all"] + sorted(set(e.get("product_slug", e.get("product_id", "")) for e in evidence))
            skeys = ["all"] + sorted(set(e.get("schema_key", "") for e in evidence if e.get("schema_key")))
            with f1:
                    sel_pid = st.selectbox("Product", pids)
            with f2:
                    sel_skey = st.selectbox("Schema Key", skeys)
            with f3:
                    sel_pii = st.selectbox("PII Status", ["all", "masked", "clean"])

            filtered = evidence
            if sel_pid != "all":
                    filtered = [e for e in filtered if e.get("product_slug", e.get("product_id")) == sel_pid]
            if sel_skey != "all":
                    filtered = [e for e in filtered if e.get("schema_key", "").startswith(sel_skey)]
            if sel_pii != "all":
                    masked = (sel_pii == "masked")
                    filtered = [e for e in filtered if bool(e.get("pii_masked")) == masked]

            st.text(f"Showing {len(filtered)} of {len(evidence)} items")

            # Summary table
            rows = []
            for e in filtered:
                    snippet = (e.get("snippet") or "")[:60] + ("..." if len(e.get("snippet") or "") > 60 else "")
                    rows.append({
                            "evidence_id": e.get("evidence_id", ""),
                            "Product": e.get("product_slug", e.get("product_id", "")),
                            "Schema Key": (e.get("schema_key") or "").split(".")[-1],
                            "Snippet": snippet,
                            "Confidence": f"{e.get('confidence', 0):.2f}",
                            "PII": "Masked" if e.get("pii_masked") in (1, True) else "Clean",
                    })
            st.dataframe(rows, width="stretch", hide_index=True, height=300)

            # Detail expanders
            st.subheader("Evidence Details")
            for ei, e in enumerate(filtered):
                    eid = e.get("evidence_id", f"ev_{ei}")
                    product = e.get("product_slug", e.get("product_id", "unknown"))
                    skey = e.get("schema_key", "")
                    pii = "Masked" if e.get("pii_masked") in (1, True) else "Clean"
                    conf = f"{e.get('confidence', 0):.2f}"

                    with st.expander(f"`{eid}` | {product} | schema: `{(skey or '').split('.')[-1]}` | conf: {conf} | PII: {pii}"):
                            st.markdown(f"**Snippet:**")
                            st.info(e.get("snippet", "N/A"))
                            mc1, mc2 = st.columns(2)
                            with mc1:
                                    st.text(f"Evidence ID: {eid}")
                                    st.text(f"Product: {product}")
                                    st.text(f"Schema Key: {skey or 'N/A'}")
                            with mc2:
                                    st.text(f"Confidence: {conf}")
                                    st.text(f"PII Status: {pii}")
                                    st.text(f"Type: {e.get('evidence_type', 'N/A')}")


# ---------------------------------------------------------------------------
# Page: Knowledge Table
# ---------------------------------------------------------------------------

elif page == "KnowledgeTable":
    st.header("Knowledge Table")
    st.info("Structured competitive intelligence facts extracted from evidence. This is the single source of truth for report generation.")

    proj_id = st.session_state.get("selected_project_id")
    if proj_id:
            st.caption(f"Knowledge base for project: `{proj_id}`")
            try:
                    resp = requests.get(f"{API_BASE}/api/projects/{proj_id}/knowledge", timeout=10)
                    resp.raise_for_status()
                    facts = resp.json()
            except requests.exceptions.RequestException:
                    facts = []
    else:
            # Fallback: try to get from run
            try:
                    resp = requests.get(f"{API_BASE}/api/runs/{run_id}/evidence", timeout=10)
                    facts = []
                    if resp.status_code == 200:
                            evidence = resp.json()
                            # Group evidence into pseudo-facts
                            for e in evidence:
                                    facts.append({
                                            "fact_id": e.get("evidence_id", ""),
                                            "product_slug": e.get("product_slug", e.get("product_id", "")),
                                            "schema_key": e.get("schema_key", ""),
                                            "value_summary": (e.get("snippet") or "")[:150],
                                            "confidence": e.get("confidence", 0),
                                            "evidence_count": 1,
                                            "review_status": "signed",
                                    })
            except:
                    facts = []

    if not facts:
            st.warning("No knowledge entries found. Run the analysis pipeline first.")
    else:
            st.metric("Total Facts", len(facts))

            # Filters
            f1, f2, f3 = st.columns(3)
            products = ["all"] + sorted(set(f.get("product_slug", f.get("product_id", "")) for f in facts))
            skeys = ["all"] + sorted(set(f.get("schema_key", "") for f in facts if f.get("schema_key")))
            statuses = ["all", "signed", "pending", "rejected"]

            with f1:
                    sel_prod = st.selectbox("Product", products)
            with f2:
                    sel_key = st.selectbox("Schema Key", skeys)
            with f3:
                    sel_status = st.selectbox("Review Status", statuses)

            filtered = facts
            if sel_prod != "all":
                    filtered = [f for f in filtered if f.get("product_slug", f.get("product_id")) == sel_prod]
            if sel_key != "all":
                    filtered = [f for f in filtered if f.get("schema_key", "").startswith(sel_key)]
            if sel_status != "all":
                    filtered = [f for f in filtered if f.get("review_status") == sel_status]

            st.text(f"Showing {len(filtered)} of {len(facts)} entries")

            # Stats
            signed = sum(1 for f in filtered if f.get("review_status") == "signed")
            avg_conf = sum(f.get("confidence", 0) for f in filtered) / max(len(filtered), 1)
            sc1, sc2, sc3 = st.columns(3)
            with sc1:
                    st.metric("Signed Facts", signed)
            with sc2:
                    st.metric("Avg Confidence", f"{avg_conf:.2f}")
            with sc3:
                    st.metric("Coverage", f"{signed}/{len(filtered)}")

            # Table
            rows = []
            for f in filtered:
                    val = f.get("value_summary") or f.get("value_json") or ""
                    if len(val) > 80:
                            val = val[:80] + "..."
                    rows.append({
                            "Product": f.get("product_slug", f.get("product_id", "")),
                            "Schema Key": (f.get("schema_key") or "").split(".")[-1],
                            "Value / Summary": val,
                            "Confidence": f"{f.get('confidence', 0):.2f}",
                            "Evidence #": f.get("evidence_count", 0),
                            "Review": f.get("review_status", "unknown"),
                            "Updated": (f.get("updated_at") or f.get("created_at") or "")[:10],
                    })
            st.dataframe(rows, width="stretch", hide_index=True, height=400)

            # Detail
            st.subheader("Fact Details")
            for fi, fact in enumerate(filtered[:20]):
                    fid = fact.get("fact_id", f"fact_{fi}")
                    prod = fact.get("product_slug", fact.get("product_id", "unknown"))
                    skey = fact.get("schema_key", "")
                    val = fact.get("value_summary") or fact.get("value_json") or "N/A"
                    conf = fact.get("confidence", 0)
                    status = fact.get("review_status", "unknown")
                    ev_count = fact.get("evidence_count", 0)

                    with st.expander(f"`{fid}` | {prod} | `{skey}` | conf: {conf:.2f} | {status}"):
                            st.markdown(f"**Schema Key:** `{skey}`")
                            st.markdown(f"**Value:**")
                            st.text(val)
                            mc1, mc2, mc3, mc4 = st.columns(4)
                            with mc1:
                                    st.text(f"Product: {prod}")
                            with mc2:
                                    st.text(f"Confidence: {conf:.2f}")
                            with mc3:
                                    st.text(f"Evidence #: {ev_count}")
                            with mc4:
                                    st.text(f"Status: {status}")


# ---------------------------------------------------------------------------
# Page: Project Detail (replaces old Task page with project context)
# ---------------------------------------------------------------------------

elif page == "ProjectDetail":
    proj_id = st.session_state.get("selected_project_id")

    if not proj_id:
            st.warning("No project selected. Go to **Projects** to select one.")
            if st.button("Go to Projects"):
                    goto_page("Projects")
    else:
            try:
                    resp = requests.get(f"{API_BASE}/api/projects/{proj_id}", timeout=10)
                    resp.raise_for_status()
                    proj = resp.json()
            except requests.exceptions.RequestException:
                    st.error("Failed to load project.")
                    proj = {}

            pname = proj.get("project_name", "Unknown Project")
            st.header(f"Project Workspace: {pname}")
            st.caption(f"Project ID: `{proj_id}`")

            # Get latest run for all tabs
            latest_run = proj.get("latest_run")
            agg = proj.get("aggregates", {})

            # -------------------------------------------------------------------------
            # Build project_runs list with deduplication and sorting
            # -------------------------------------------------------------------------
            runs_raw = proj.get("runs", [])
            project_runs = {}
            if latest_run:
                    rid = latest_run.get("run_id")
                    if rid:
                            project_runs[rid] = latest_run
            for r in runs_raw:
                    rid = r.get("run_id")
                    if rid and rid not in project_runs:
                            project_runs[rid] = r

            # Sort by started_at / created_at descending
            def run_sort_key(r):
                    ts = r.get("started_at") or r.get("created_at") or ""
                    return ts

            sorted_runs = sorted(project_runs.values(), key=run_sort_key, reverse=True)

            # Default to session state run if it belongs to this project, else latest_run
            saved_run_id = st.session_state.get("selected_run_id")
            if saved_run_id and saved_run_id in project_runs:
                    default_run_id = saved_run_id
            elif latest_run and latest_run.get("run_id"):
                    default_run_id = latest_run.get("run_id")
            elif sorted_runs:
                    default_run_id = sorted_runs[0].get("run_id", "")
            else:
                    default_run_id = ""

            # -------------------------------------------------------------------------
            # Active Run Selector
            # -------------------------------------------------------------------------
            st.divider()

            run_options = []
            run_label_map = {}
            for r in sorted_runs:
                    rid = r.get("run_id", "")
                    status = r.get("status", "unknown")
                    mode = r.get("mode", "N/A")
                    ts = (r.get("started_at") or r.get("created_at") or "")[:16].replace("T", " ")
                    label = f"{rid[:16]}...{rid[-6:]} | {status} | {mode} | {ts}"
                    run_options.append(rid)
                    run_label_map[rid] = label

            active_run_id = ""
            if run_options:
                    active_run_id = st.selectbox(
                            "Active Run",
                            options=run_options,
                            format_func=lambda rid: run_label_map.get(rid, rid[:16] + "..." + rid[-6:] if len(rid) > 22 else rid),
                            index=run_options.index(default_run_id) if default_run_id in run_options else 0,
                    )

                    # Show full active run ID
                    st.markdown("**Active Run ID**")
                    st.code(active_run_id, language=None)

                    # Show latest run ID if different
                    latest_run_id = latest_run.get("run_id") if latest_run else ""
                    if latest_run_id and latest_run_id != active_run_id:
                            st.caption(f"Latest Run ID: `{latest_run_id}`")

                    # Show active run info
                    if active_run_id and active_run_id in project_runs:
                            ar = project_runs[active_run_id]
                            a_status = ar.get("status", "unknown")
                            a_mode = ar.get("mode", "N/A")
                            a_node = ar.get("current_node", "—") or "—"
                            ac = {"completed": "green", "running": "blue", "failed": "red", "pending": "gray"}.get(a_status, "gray")
                            st.caption(f"Status: :{ac}[{a_status}] | Mode: {a_mode} | Node: {a_node}")
                            if active_run_id != latest_run_id:
                                    st.info("You are viewing a selected run, not the latest run.")

                            # Pending run retry button
                            if a_status == "pending":
                                    st.warning("该 Run 已创建但尚未启动。")
                                    if st.button("继续启动并进入运行中心", key=f"retry_pending_{active_run_id}"):
                                            ok = start_run_async_and_go_to_running(proj_id, active_run_id)
                                            if not ok:
                                                    st.stop()
                    else:
                            active_run_id = ""
                            st.info("No runs yet. Start an analysis run first.")

            # Store active_run_id in session state for other pages
            st.session_state["selected_run_id"] = active_run_id

            # Create tabs
            tab_overview, tab_workflow, tab_human_review, tab_deliverables, tab_audit = st.tabs([
                    "Overview", "Workflow", "Review Center", "Deliverables", "Audit"
            ])

            with tab_overview:
                    # Fetch live data for overview
                    live_src_count = 0
                    live_ev_count = 0
                    live_fact_count = 0
                    live_claim_count = 0
                    live_pending_interv = 0
                    live_pending_source = ""
                    live_run_status = "unknown"

                    if active_run_id:
                            try:
                                    sr = requests.get(f"{API_BASE}/api/runs/{active_run_id}/sources", timeout=10)
                                    if sr.status_code == 200:
                                            live_src_count = len(sr.json())
                            except Exception:
                                    pass
                            try:
                                    er = requests.get(f"{API_BASE}/api/runs/{active_run_id}/evidence", timeout=10)
                                    if er.status_code == 200:
                                            live_ev_count = len(er.json())
                            except Exception:
                                    pass
                            try:
                                    cr = requests.get(f"{API_BASE}/api/runs/{active_run_id}/review-items", timeout=10)
                                    if cr.status_code == 200:
                                            claims_json = cr.json()
                                            live_claim_count = len(claims_json.get("claims", []))
                            except Exception:
                                    pass
                            try:
                                    live_pending_interv, live_pending_source = get_pending_review_count(active_run_id)
                            except Exception:
                                    live_pending_interv = 0
                                    live_pending_source = ""
                            try:
                                    rr = requests.get(f"{API_BASE}/api/runs/{active_run_id}", timeout=10)
                                    if rr.status_code == 200:
                                            live_run_status = rr.json().get("status", "unknown")
                            except Exception:
                                    pass

                    # --- Health & Progress Cards (first section) ---
                    st.markdown("#### Project Health")

                    # Status row
                    proj_status = proj.get("status", "active")
                    status_color = {"active": "green", "completed": "blue", "archived": "gray"}.get(proj_status, "gray")
                    health_cols = st.columns(4)
                    with health_cols[0]:
                            run_status_disp = live_run_status
                            run_status_color = {"completed": "green", "running": "blue", "failed": "red", "pending": "gray"}.get(run_status_disp, "gray")
                            st.markdown(f"**Run Status**")
                            st.markdown(f":{run_status_color}[{run_status_disp}]")
                    with health_cols[1]:
                            st.metric("Sources", live_src_count or agg.get("source_count", 0))
                    with health_cols[2]:
                            st.metric("Evidence", live_ev_count or agg.get("evidence_count", 0))
                    with health_cols[3]:
                            pending_label = "Pending Reviews"
                            if live_pending_source == "blocked_report_fallback":
                                    pending_color = "orange"
                                    st.markdown(f"**{pending_label}**")
                                    st.markdown(f":{pending_color}[1]")
                                    st.caption("Final quality gate blocked the report")
                            else:
                                    pending_color = "red" if live_pending_interv > 0 else "green"
                                    st.markdown(f"**{pending_label}**")
                                    st.markdown(f":{pending_color}[{live_pending_interv}]")

                    # Progress row
                    prog_cols = st.columns(4)
                    with prog_cols[0]:
                            st.metric("Facts", live_fact_count or agg.get("fact_count", 0))
                    with prog_cols[1]:
                            st.metric("Claims", live_claim_count or agg.get("claim_count", 0))
                    with prog_cols[2]:
                            st.metric("Reports", agg.get("report_count", 0))
                    with prog_cols[3]:
                            runs_total = len(proj.get("runs", []))
                            st.metric("Analysis Runs", runs_total)

                    # --- Active Run Status ---
                    if active_run_id and active_run_id in project_runs:
                            ar = project_runs[active_run_id]
                            ar_status = ar.get("status", "unknown")
                            ar_mode = ar.get("mode", "N/A")
                            ar_node = ar.get("current_node", "—") or "—"
                            ar_ts = (ar.get("started_at") or "")[:16].replace("T", " ")

                            st.divider()
                            st.markdown("#### Active Run")
                            ar_col1, ar_col2, ar_col3, ar_col4 = st.columns(4)
                            with ar_col1:
                                    st.markdown("**Status**")
                                    ar_sc = {"completed": "green", "running": "blue", "failed": "red", "pending": "gray"}.get(ar_status, "gray")
                                    st.markdown(f":{ar_sc}[{ar_status}]")
                            with ar_col2:
                                    st.markdown("**Mode**")
                                    st.text(ar_mode)
                            with ar_col3:
                                    st.markdown("**Current Node**")
                                    st.text(ar_node)
                            with ar_col4:
                                    st.markdown("**Started**")
                                    st.text(ar_ts or "—")

                            if ar_status == "failed" and ar.get("error_message"):
                                    ar_err = ar.get("error_message", "")
                                    if "block" in (ar_err or "").lower():
                                            with st.expander("⚠ Quality gate finding", expanded=False):
                                                    st.text(ar_err)
                                    else:
                                            st.error(f"Run error: {ar_err}")

                    # --- Workflow Progress ---
                    if active_run_id:
                            workflow_data = get_json(f"/api/runs/{active_run_id}/workflow", None)
                            if workflow_data:
                                    summary = workflow_data.get("summary", {})
                                    total_nodes = summary.get("total_nodes", 0)
                                    completed_nodes = summary.get("completed", 0)
                                    running_nodes = summary.get("running", 0)
                                    failed_nodes = summary.get("failed", 0)
                                    has_hr = summary.get("has_human_review", False)

                                    st.divider()
                                    st.markdown("#### Workflow Progress")

                                    # Run Outcome (separate from workflow node status)
                                    outcome_cols = st.columns([1, 1])
                                    with outcome_cols[0]:
                                            st.markdown("**Workflow Execution**")
                                            wf_exec_color = "green" if failed_nodes == 0 and completed_nodes == total_nodes else "red" if failed_nodes > 0 else "blue"
                                            wf_exec_label = f"{completed_nodes}/{total_nodes} completed"
                                            st.markdown(f":{wf_exec_color}[{wf_exec_label}]")
                                    with outcome_cols[1]:
                                            st.markdown("**Run Outcome**")
                                            if ar_status == "failed":
                                                    ar_err = ar.get("error_message", "")
                                                    if "block" in (ar_err or "").lower():
                                                            st.error("Quality Gate Blocked")
                                                    else:
                                                            st.error(f"failed: {ar_err or 'unknown error'}")
                                            elif ar_status == "completed":
                                                    st.success("completed successfully")
                                            elif ar_status == "running":
                                                    st.info("currently running")
                                            else:
                                                    st.text(ar_status)

                                    # Node metrics
                                    wp_cols = st.columns(5)
                                    with wp_cols[0]:
                                            st.metric("Total Nodes", total_nodes)
                                    with wp_cols[1]:
                                            st.metric("Completed", completed_nodes)
                                    with wp_cols[2]:
                                            st.metric("Running", running_nodes)
                                    with wp_cols[3]:
                                            st.metric("Failed", failed_nodes)
                                    with wp_cols[4]:
                                            st.metric("Human Review", "Required" if has_hr else "None")

                                    if completed_nodes > 0 and total_nodes > 0:
                                            progress_pct = completed_nodes / total_nodes
                                            if ar_status == "failed":
                                                    st.warning(f"Progress: {completed_nodes}/{total_nodes} nodes completed — Run Outcome: failed")
                                            else:
                                                    st.progress(progress_pct, text=f"Progress: {completed_nodes}/{total_nodes} nodes completed")

                    # --- Competitors (collapsed by default) ---
                    products = proj.get("products", [])
                    if products:
                            st.divider()
                            with st.expander(f"Competitors ({len(products)})", expanded=False):
                                    for p in products:
                                            url = p.get("official_website", "")
                                            link_md = f" [{url}]({url})" if url else ""
                                            st.markdown(f"- **{p.get('product_name', 'N/A')}** — {p.get('company_name', '')}{link_md}")

                    # --- Quick Actions (secondary/weak) ---
                    st.divider()
                    st.markdown("**Quick Actions**")
                    qa_col1, qa_col2, qa_col3, qa_col4 = st.columns(4)
                    with qa_col1:
                            if st.button("Evidence Hub", key=f"ws_overview_evidence_{active_run_id}", use_container_width=True):
                                    goto_page("Evidence Hub")
                    with qa_col2:
                            if st.button("Sources", key=f"ws_overview_sources_{active_run_id}", use_container_width=True):
                                    goto_page("Sources")
                    with qa_col3:
                            if st.button("Knowledge", key=f"ws_overview_knowledge_{active_run_id}", use_container_width=True):
                                    goto_page("Knowledge Table")
                    with qa_col4:
                            if st.button("Full Report", key=f"ws_overview_report_{active_run_id}", use_container_width=True):
                                    goto_page("分析报告")

            with tab_workflow:
                    st.subheader("Workflow Status")
                    if active_run_id:
                            render_workflow_status(active_run_id, compact=True)
                    else:
                            st.info("No run yet. Start an analysis run first from the Overview tab.")

            with tab_human_review:
                    st.subheader("Human Review")
                    if active_run_id:
                            st.caption(f"Viewing interventions for active run: `{active_run_id}`")
                            render_human_interventions(active_run_id, compact=True)
                    else:
                            st.info("No run yet.")

            with tab_deliverables:
                    st.subheader("Deliverables")
                    if active_run_id:
                            run_id = active_run_id
                            run_status = project_runs.get(run_id, {}).get("status", "unknown") if run_id in project_runs else "unknown"

                            # Fetch report data for this run
                            report_data = get_json(f"/api/runs/{run_id}/report", {}) or {}

                            # Show report status
                            c1, c2 = st.columns(2)
                            with c1:
                                    st.markdown("**Report Status**")
                                    if run_status == "completed":
                                            qs_data = report_data.get("quality_summary", {}) if isinstance(report_data, dict) else {}
                                            insufficient = qs_data.get("insufficient_products", 0) if isinstance(qs_data, dict) else 0
                                            partial = qs_data.get("partial_products", 0) if isinstance(qs_data, dict) else 0
                                            if insufficient > 0:
                                                    st.warning(f"⚠ 分析流程已完成，但 {insufficient} 个产品存在证据覆盖不足。报告结果应谨慎使用。")
                                                    st.caption("→ 请前往 Review Center 查看详情")
                                            elif partial > 0:
                                                    st.info(f"分析流程已完成，但 {partial} 个产品证据覆盖不完整。结果仅供参考。")
                                            else:
                                                    st.success("✅ 分析流程已完成，报告已就绪。")
                                            if st.button("View Report", key=f"ws_deliv_viewreport_{run_id}", type="primary"):
                                                    goto_page("分析报告")
                                    elif run_status == "failed":
                                            st.error(f"Run failed. Current outcome: failed.")
                                    else:
                                            st.info(f"Report will be available after run completes. Current status: {run_status}")

                            with c2:
                                    st.markdown("**Evidence & Knowledge**")
                                    st.metric("Evidence Items", agg.get("evidence_count", 0))
                                    st.metric("Facts", agg.get("fact_count", 0))

                            # Quick links
                            st.divider()
                            st.markdown("**Quick Links**")
                            q1, q2, q3 = st.columns(3)
                            with q1:
                                    if st.button("Evidence Hub", key=f"ws_deliv_evidence_{run_id}", use_container_width=True):
                                            goto_page("Evidence Hub")
                            with q2:
                                    if st.button("Knowledge Table", key=f"ws_deliv_knowledge_{run_id}", use_container_width=True):
                                            goto_page("Knowledge Table")
                            with q3:
                                    if st.button("Sources", key=f"ws_deliv_sources_{run_id}", use_container_width=True):
                                            goto_page("Sources")
                    else:
                            st.info("No run yet. Start an analysis run first from the Overview tab.")

            with tab_audit:
                    st.subheader("Audit & Trace")
                    if active_run_id:
                            st.markdown(f"**Run ID:** `{active_run_id}`")

                            # Quick audit links
                            st.divider()
                            st.markdown("**Audit Views**")
                            q1, q2, q3 = st.columns(3)
                            with q1:
                                    if st.button("DAG Execution", key=f"ws_audit_dag_{active_run_id}", use_container_width=True):
                                            goto_page("DAG 执行")
                            with q2:
                                    if st.button("Trace & Audit", key=f"ws_audit_trace_{active_run_id}", use_container_width=True):
                                            goto_page("Trace & Audit")
                            with q3:
                                    if st.button("Review Center", key=f"ws_audit_humanreview_{active_run_id}", use_container_width=True):
                                            goto_page("Review Center")
                    else:
                            st.info("No run yet.")

            # Quick actions (secondary/weak) and Run button
            st.divider()
            qa_col1, qa_col2 = st.columns([1, 1])
            with qa_col1:
                    if st.button("Start New Analysis Run", type="primary", use_container_width=True):
                            ok = start_run_async_and_go_to_running(proj_id)
                            if not ok:
                                    st.stop()
            with qa_col2:
                    st.caption(":warning: Resume/re-run will be implemented in backend next.")


# ---------------------------------------------------------------------------
# Page: Trace & Audit (replaces old Trace + Metrics + Replay pages)
# ---------------------------------------------------------------------------

elif page == "TraceAudit":
    proj_id = st.session_state.get("selected_project_id")

    if proj_id:
            st.header("Trace & Audit")
            st.caption(f"Execution traces and audit logs for project: `{proj_id}`")

            try:
                    resp = requests.get(f"{API_BASE}/api/projects/{proj_id}/audit", timeout=10)
                    resp.raise_for_status()
                    audit = resp.json()
            except:
                    audit = {}

            # Summary
            sc1, sc2, sc3, sc4, sc5 = st.columns(5)
            with sc1:
                    st.metric("Runs", audit.get("run_count", 0))
            with sc2:
                    st.metric("Traces", audit.get("trace_count", 0))
            with sc3:
                    st.metric("Messages", audit.get("message_count", 0))
            with sc4:
                    st.metric("PII Logs", audit.get("pii_log_count", 0))
            with sc5:
                    st.metric("Eval Logs", audit.get("eval_log_count", 0))

            # Trace by node
            node_counts = audit.get("trace_by_node", {})
            if node_counts:
                    st.subheader("Traces by Node")
                    rows = [{"Node": k, "Count": v} for k, v in sorted(node_counts.items())]
                    st.dataframe(rows, width="stretch", hide_index=True)
    else:
            st.header("Trace & Audit")
            render_run_banner(run_id)
            data = get_json(f"/api/runs/{run_id}/traces", [])
            if not data:
                    st.warning("No trace data found for this run.")
            else:
                    st.metric("Total Trace Events", len(data))
                    rows = [{
                            "node_name": t.get("node_name", ""),
                            "agent_name": t.get("agent_name", ""),
                            "status": _badge(t.get("status", "")),
                            "latency_ms": t.get("latency_ms", ""),
                    } for t in data]
                    st.dataframe(rows, width="stretch", hide_index=True)

                    st.divider()
                    for ti, t in enumerate(data):
                            ts = t.get("status", "unknown")
                            tc = {"success": "green", "failed": "red"}.get(ts, "gray")
                            with st.expander(
                                    f"**{ti + 1}. `{t.get('node_name', '')}`** | "
                                    f"Agent: {t.get('agent_name', 'N/A')} | :{tc}[{ts}] | "
                                    f"Latency: {t.get('latency_ms', '?')}ms",
                                    expanded=False,
                            ):
                                    st.text(f"Node: {t.get('node_name')}")
                                    st.text(f"Agent: {t.get('agent_name')}")
                                    st.text(f"Status: {t.get('status')}")
                                    st.text(f"Latency: {t.get('latency_ms')}ms")
                                    st.text(f"Model: {t.get('model_name', 'N/A')}")
                                    st.text(f"Decision: {t.get('decision', 'N/A')}")
                                    with st.expander("Full JSON"):
                                            st.json(t)


# ---------------------------------------------------------------------------
# Page: Human Review (dedicated page for human interventions)
# ---------------------------------------------------------------------------

elif page == "HumanReview":
    st.header("Review Center")
    st.info("Review evidence issues, rework tasks, and human interventions.")

    # Fetch run status
    run_resp = None
    try:
        run_resp = requests.get(f"{API_BASE}/api/runs/{run_id}", timeout=10)
    except Exception:
        pass

    run_status = None
    run_error = None
    if run_resp and run_resp.status_code == 200:
        run_data = run_resp.json()
        run_status = run_data.get("status")
        run_error = run_data.get("error_message") or ""

    is_blocked = run_status == "failed" and "block" in run_error.lower()
    interventions = get_json(f"/api/runs/{run_id}/human-interventions?status=pending", [])
    rework_tasks = get_json(f"/api/runs/{run_id}/rework-tasks", []) or []
    has_pending_interventions = bool(interventions)
    has_pending_rework = any(t.get("status") in ("pending", "planned", "running") for t in rework_tasks)
    show_rework_demo_story = is_blocked or has_pending_interventions or has_pending_rework

    # Fetch report for quality summary
    report_data = get_json(f"/api/runs/{run_id}/report", {}) or {}
    quality_summary = report_data.get("quality_summary", {}) if isinstance(report_data, dict) else {}
    insufficient_products = quality_summary.get("insufficient_products", 0) if isinstance(quality_summary, dict) else 0
    partial_products = quality_summary.get("partial_products", 0) if isinstance(quality_summary, dict) else 0

    if show_rework_demo_story:
        with st.expander("Rework Loop Demo Story"):
            st.markdown("""
**ProductInsight Agent — Rework Loop Demo Story**

1. **Workflow execution completed** — the pipeline ran and collected sources, evidence, facts, and claims.
2. **A quality gate blocked the report** because evidence support was insufficient (e.g. unsupported report spans, missing evidence for claims).
3. **A pending human intervention was created** by the system, surfacing the quality gate failure.
4. **The user requests rework** from the intervention card — clicking **Request Rework**.
5. **The system creates a rework task and plan** — with reason codes and step-by-step instructions.
6. **Apply Rework / Simulate Fix** records repair actions and marks the task `completed`.
7. **Simulate Review Rerun** shows the before/after improvement, indicating the report is ready for review.
""")
    else:
        with st.expander("Rework Loop Demo Story"):
            if insufficient_products > 0:
                st.warning(f"暂无人工介入，但存在 {insufficient_products} 个产品的证据覆盖缺口。")
                st.caption("建议补充相关产品的证据收集。")
            elif partial_products > 0:
                st.info(f"暂无人工介入，但 {partial_products} 个产品的证据覆盖不完整。")
            else:
                st.success(f"✅ 该运行已完成并通过质量门，暂无待处理人工审核。")
            st.caption(f"Run `{run_id[:16]}...` status: {run_status}. "
                       "No pending interventions or rework tasks.")

    render_run_banner(run_id)

    if not run_id or run_id == "run_demo_ai_agent_001":
            st.warning("Please create or select a run first.")
    else:
            # Check if this run has a blocked report that should be surfaced in Review Center
            # Frontend fallback: if run failed due to blocked report but no intervention exists yet,
            # show a blocked report card.
            if is_blocked and not interventions:
                    st.error("Report blocked at final quality gate — action required.")
                    blocked_col1, blocked_col2 = st.columns([2, 1])
                    with blocked_col1:
                            st.markdown("##### Blocked Report Issue")
                            # Parse reason codes from error message and show human-readable explanations
                            reason_codes = []
                            for part in (run_error or "").split(";"):
                                    part = part.strip()
                                    if not part:
                                            continue
                                    # Extract [CODE] prefix if present
                                    import re
                                    m = re.match(r"\[(\w+)\]", part)
                                    if m:
                                            reason_codes.append(m.group(1))
                                            st.markdown(f"**{format_reason_code(m.group(1))}**")
                                            suffix = part[len(m.group(0)):].strip()
                                            if suffix:
                                                    st.caption(f"Detail: {suffix}")
                                    else:
                                            st.markdown(part)
                            if not reason_codes:
                                    st.markdown(f"**Raw:** {run_error}")
                            st.markdown("The full report was blocked because the final quality gate failed.")
                    with blocked_col2:
                            st.markdown("##### Current Counts")
                            proj_id = st.session_state.get("selected_project_id")
                            proj = get_json(f"/api/projects/{proj_id}", {}) if proj_id else {}
                            agg = proj.get("aggregates", {})
                            st.metric("Sources", agg.get("source_count", "N/A"))
                            st.metric("Evidence", agg.get("evidence_count", "N/A"))
                            st.metric("Facts", agg.get("fact_count", "N/A"))
                            st.metric("Claims", agg.get("claim_count", "N/A"))
                            st.metric("Signed Claims", agg.get("signed_claim_count", "N/A"))
                            st.metric("Reports", agg.get("report_count", "N/A"))

                    st.divider()
                    render_review_center_action_row(run_id, "rc_fallback")

                    st.markdown("---")
                    st.caption(
                            "No database intervention record found — showing fallback issue. "
                            "After backend restart and a new run, a proper intervention card will appear above."
                    )
            elif interventions:
                    # Normal flow: render human interventions
                    pass  # fall through to render_human_interventions below
            else:
                    # Not blocked and no interventions — check for coverage gaps
                    report_resp = get_json(f"/api/runs/{run_id}/report", None)
                    qs = {}
                    if report_resp and isinstance(report_resp, dict):
                            qs = report_resp.get("quality_summary", {}) or {}
                    insufficient = qs.get("insufficient_products", 0) if isinstance(qs, dict) else 0
                    partial = qs.get("partial_products", 0) if isinstance(qs, dict) else 0
                    if insufficient > 0 or partial > 0:
                            if insufficient > 0:
                                    st.warning(f"⚠ 该运行已完成，但 {insufficient} 个产品存在证据覆盖不足。结果仅供参考，不建议用于采购决策。")
                            else:
                                    st.info(f"ℹ 该运行已完成，但 {partial} 个产品证据覆盖不完整。请谨慎解读。")
                            # Show coverage table
                            pcs = qs.get("product_coverage_summary", {}) if isinstance(qs, dict) else {}
                            if pcs:
                                    rows = []
                                    for slug, cov in sorted(pcs.items(), key=lambda x: x[0]):
                                            status = cov.get("coverage_status", "unknown")
                                            icon = {"sufficient": "✅", "partial": "⚠", "insufficient": "❌"}.get(status, "?")
                                            rows.append({"Product": cov.get("product_name", slug.title()),
                                                         "Status": f"{icon} {status.title()}",
                                                         "Evidence": cov.get("evidence", 0),
                                                         "Facts": cov.get("facts", 0),
                                                         "Claims": cov.get("signed_claims", 0)})
                                    st.dataframe(rows, use_container_width=True, hide_index=True)
                            # ── Create Coverage Gap Rework Task button ──────────────────
                            if not rework_tasks:
                                    if st.button(
                                            "🔧 为所有缺口产品创建补证返工任务",
                                            key=f"rc_create_coverage_gap_{run_id}",
                                            type="primary",
                                    ):
                                            try:
                                                    resp = requests.post(
                                                            f"{API_BASE}/api/runs/{run_id}/coverage-gaps",
                                                            timeout=30,
                                                    )
                                                    if resp.status_code >= 400:
                                                            st.error(f"创建失败: HTTP {resp.status_code} — {resp.text}")
                                                    else:
                                                            result = resp.json()
                                                            created = len(result.get("created_tasks", []))
                                                            skipped = len(result.get("skipped_tasks", []))
                                                            st.success(f"已创建 {created} 个补证返工任务，跳过 {skipped} 个已有任务。")
                                                            st.rerun()
                                            except Exception as exc:
                                                    st.error(f"创建补证返工任务失败: {exc}")
                            elif insufficient > 0 or partial > 0:
                                    st.caption("💡 如需补充缺口，请展开上方 Rework Tasks 中的卡片，输入 URL 后 Execute real rework。")
                    else:
                            st.success("✅ 该运行已完成并通过质量门，暂无待处理人工审核。")

            st.divider()
            # Fetch workflow status
            workflow_data = get_json(f"/api/runs/{run_id}/workflow", None)
            if workflow_data:
                    summary = workflow_data.get("summary", {})
                    if summary.get("has_human_review", False):
                            st.warning("Human review is required for this run.")

            st.divider()
            # Only render human interventions when there are actual interventions
            if interventions:
                    render_review_center_action_row(run_id, "rc_real")
                    render_human_interventions(run_id, compact=False)

            # --- Rework Tasks ---
            st.divider()
            st.markdown("### Rework Tasks")
            if rework_tasks:
                    for task in rework_tasks:
                            reason_codes = task.get("reason_codes") or []
                            is_coverage_gap = (
                                    bool(reason_codes)
                                    or str(task.get("rework_id", "")).startswith("rework_cov_")
                                    or task.get("target_node") == "collect_sources"
                            )
                            if is_coverage_gap:
                                    _render_coverage_gap_task_card(task, run_id)
                            else:
                                    _render_intervention_rework_task_card(task, run_id)


# ---------------------------------------------------------------------------
# Page: ResearchPlan - View, edit, and confirm research plans (vNext-R1)
# ---------------------------------------------------------------------------
elif page == "ResearchPlan":
    st.header("Research Plan")
    st.caption("vNext-R1: 查看、编辑和确认调研方案")

    # Session state for ResearchPlan
    if "rp_plan_id" not in st.session_state:
        st.session_state["rp_plan_id"] = None
    if "rp_plan_data" not in st.session_state:
        st.session_state["rp_plan_data"] = None
    if "rp_dag_data" not in st.session_state:
        st.session_state["rp_dag_data"] = None
    if "rp_edit_mode" not in st.session_state:
        st.session_state["rp_edit_mode"] = False

    # Helper functions
    def load_plan(plan_id: str):
        try:
            resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}", timeout=30)
            if resp.status_code >= 400:
                return False
            data = resp.json()
            st.session_state["rp_plan_id"] = plan_id
            st.session_state["rp_plan_data"] = data.get("research_plan")
            return True
        except Exception:
            return False

    def load_dag(plan_id: str):
        try:
            resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}/dag", timeout=30)
            if resp.status_code >= 400:
                return False
            st.session_state["rp_dag_data"] = resp.json()
            return True
        except Exception:
            return False

    def render_research_plan_dag_preview(plan_id: str, plan: dict | None = None):
        """
        Render DAG preview for Research Plan page.
        - Prioritizes st.session_state["rp_dag_data"]
        - Falls back to load_dag() API call
        - Falls back to plan.get("execution_dag")
        - Handles both edge formats: {"from": ..., "to": ...} and {"from_node": ..., "to_node": ...}
        """
        # Priority 1: session state
        dag_data = st.session_state.get("rp_dag_data")
        source = "session"

        if not dag_data:
            # Priority 2: try to load from API
            if load_dag(plan_id):
                dag_data = st.session_state.get("rp_dag_data")
                source = "api"
            else:
                source = "none"

        if not dag_data and plan:
            # Priority 3: fallback to plan.execution_dag
            exec_dag = plan.get("execution_dag") or {}
            if exec_dag:
                dag_data = exec_dag
                source = "plan"

        if not dag_data:
            st.warning("DAG 数据不可用。")
            return

        # Extract nodes and edges
        nodes = dag_data.get("nodes") or []
        edges = dag_data.get("edges") or []

        # Get DAG ID
        dag_id = dag_data.get("dag_id") or dag_data.get("execution_dag_id") or "N/A"
        dag_status = dag_data.get("status") or "N/A"

        st.markdown(f"**DAG ID:** `{dag_id}` | **状态:** {dag_status} | **来源:** {source}")

        # Render nodes
        if nodes:
            st.markdown("### 节点")
            rows = []
            for n in nodes:
                if isinstance(n, dict):
                    rows.append({
                        "节点 ID": n.get("node_id", ""),
                        "类型": n.get("node_type", ""),
                        "Agent": n.get("agent_name", ""),
                        "人工审核点": "是" if n.get("human_checkpoint") else "否",
                        "状态": n.get("status", "pending"),
                    })
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            with st.expander("完整节点 (Raw JSON)"):
                st.json(nodes)

        # Render edges
        if edges:
            st.markdown("### 边 (执行顺序)")
            edge_rows = []
            for e in edges:
                if isinstance(e, dict):
                    # Handle both edge formats
                    from_node = e.get("from") or e.get("from_node", "")
                    to_node = e.get("to") or e.get("to_node", "")
                    edge_rows.append({"从": from_node, "到": to_node})
            if edge_rows:
                st.dataframe(pd.DataFrame(edge_rows), hide_index=True, use_container_width=True)
            with st.expander("完整边 (Raw JSON)"):
                st.json(edges)

    def render_status_badge(status: str):
        colors = {"draft": "gray", "confirmed": "green", "in_progress": "blue", "completed": "green", "cancelled": "red"}
        color = colors.get(status, "gray")
        st.markdown(f":{color}[**{status.upper()}**]")

    # Plan ID input
    col1, col2 = st.columns([2, 1])
    with col1:
        plan_id_input = st.text_input(
            "Research Plan ID",
            placeholder="Enter plan_xxx or generate a new plan",
            help="输入调研方案 ID 或在下方生成新方案",
        )
    with col2:
        st.markdown("")
        st.markdown("")
        if st.button("加载方案", use_container_width=True) and plan_id_input:
            if load_plan(plan_id_input):
                st.success(f"已加载: {plan_id_input}")
            else:
                st.error("加载失败，请检查 ID。")

    st.divider()

    plan_data = st.session_state.get("rp_plan_data")
    plan_id = st.session_state.get("rp_plan_id")

    if not plan_data:
        st.info("暂无调研方案。请生成新方案或加载已有方案。")

        # Generate new plan section
        with st.expander("生成新调研方案", expanded=True):
            user_query = st.text_area(
                "研究需求",
                placeholder="例如：分析 Dify, Coze, Flowise 和 LangGraph 的企业 AI Agent 平台对比，关注功能、定价、部署和企业就绪度。",
                height=100,
                key="rp_new_query",
            )

            col_schema, col_region, col_mode = st.columns(3)
            with col_schema:
                schema_type = st.selectbox(
                    "Schema 类型",
                    options=["ai_agent_platform", "competitor_landscape", "product_comparison", "pricing_analysis", "sales_battlecard"],
                    index=0,
                )
            with col_region:
                target_region = st.selectbox(
                    "目标区域",
                    options=["global", "china", "us", "europe", "southeast_asia"],
                    index=0,
                )
            with col_mode:
                mode = st.selectbox(
                    "模式",
                    options=["review", "auto", "expert"],
                    index=0,
                )

            if st.button("生成调研方案", type="primary", use_container_width=True):
                if not user_query.strip():
                    st.error("请输入研究需求。")
                else:
                    with st.spinner("正在生成..."):
                        try:
                            resp = requests.post(
                                f"{API_BASE}/api/research-plans/generate",
                                json={"user_query": user_query, "schema_type": schema_type, "target_region": target_region, "mode": mode},
                                timeout=60,
                            )
                            if resp.status_code >= 400:
                                st.error(f"生成失败: HTTP {resp.status_code}")
                            else:
                                result = resp.json()
                                plan_data = result.get("research_plan")
                                plan_id = result.get("research_plan_id")
                                st.session_state["rp_plan_id"] = plan_id
                                st.session_state["rp_plan_data"] = plan_data
                                st.success(f"方案已生成！来源: {result.get('generated_by', 'unknown')}")
                                st.rerun()
                        except Exception as exc:
                            st.error(f"生成失败: {exc}")

    else:
        plan = plan_data
        status = plan.get("status", "draft")
        generated_by = plan.get("generated_by", "unknown")

        col_header1, col_header2, col_header3 = st.columns([2, 1, 1])
        with col_header1:
            st.subheader(f"调研方案: `{plan_id}`")
        with col_header2:
            render_status_badge(status)
        with col_header3:
            st.caption(f"生成方式: **{generated_by}**")

        st.divider()

        # Edit mode
        edit_mode = st.toggle("编辑 JSON", value=st.session_state.get("rp_edit_mode", False))
        st.session_state["rp_edit_mode"] = edit_mode

        if edit_mode:
            st.markdown("### 编辑方案 JSON")
            edited_json = st.text_area(
                "Plan JSON（编辑后保存）",
                value=json.dumps(plan, indent=2, ensure_ascii=False),
                height=400,
                key="rp_json_editor",
            )

            col_save, col_reset = st.columns(2)
            with col_save:
                if st.button("保存修改", type="primary", use_container_width=True):
                    try:
                        new_plan = json.loads(edited_json)
                        resp = requests.put(
                            f"{API_BASE}/api/research-plans/{plan_id}",
                            json={"payload_json": edited_json},
                            timeout=60,
                        )
                        if resp.status_code >= 400:
                            st.error(f"保存失败: {resp.text}")
                        else:
                            result = resp.json()
                            st.session_state["rp_plan_data"] = result.get("research_plan")
                            st.success("已保存！")
                            st.rerun()
                    except json.JSONDecodeError as exc:
                        st.error(f"JSON 格式错误: {exc}")

            with col_reset:
                if st.button("重置", use_container_width=True):
                    st.rerun()

            st.divider()

        # Tabs
        tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
            "任务简报", "竞品", "分析维度", "来源规划", "报告大纲", "人工审核点", "成功指标", "DAG 预览",
        ])

        with tab1:
            task_brief = plan.get("task_brief") or {}
            if task_brief:
                col_tb1, col_tb2 = st.columns(2)
                with col_tb1:
                    st.markdown(f"**项目名称:** {task_brief.get('project_name', 'N/A')}")
                    st.markdown(f"**任务类型:** {task_brief.get('task_type', 'N/A')}")
                    st.markdown(f"**目标区域:** {task_brief.get('target_region', 'N/A')}")
                with col_tb2:
                    st.markdown(f"**目标受众:** {task_brief.get('target_audience', 'N/A')}")
                    st.markdown(f"**商业目标:** {task_brief.get('business_goal', 'N/A')}")
                st.markdown("**用户需求:**")
                st.info(task_brief.get("user_query", "N/A"))
            else:
                st.info("无任务简报。")

        with tab2:
            competitors = plan.get("competitors") or []
            if competitors:
                rows = [{"名称": c.get("name", ""), "公司": c.get("company_name", ""), "优先级": c.get("priority", ""), "URL": c.get("official_url", "")} for c in competitors if isinstance(c, dict)]
                if rows:
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                with st.expander("完整竞品数据"):
                    st.json(competitors)
            else:
                st.info("无竞品。")

        with tab3:
            dimensions = plan.get("analysis_dimensions") or []
            if dimensions:
                rows = [
                    {
                        "ID": d.get("dimension_id", ""),
                        "名称": d.get("name", ""),
                        "必需": "是" if d.get("required") else "否",
                    }
                    for d in dimensions if isinstance(d, dict)
                ]
                if rows:
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                # Show descriptions
                for d in dimensions:
                    if isinstance(d, dict):
                        dim_id = d.get("dimension_id", "")
                        dim_name = d.get("name", dim_id)
                        dim_desc = d.get("description", "")
                        st.markdown(f"**{dim_name}** (`{dim_id}`)")
                        if dim_desc:
                            st.caption(f"_{dim_desc[:120]}..._")
                with st.expander("完整维度数据"):
                    st.json(dimensions)
            else:
                st.info("无分析维度。")

        with tab4:
            source_plan = plan.get("source_plan") or {}
            if source_plan:
                st.markdown(f"**采集策略:** {source_plan.get('collection_strategy', 'N/A')}")
                st.markdown(f"**最低竞品来源数:** {source_plan.get('minimum_sources_per_competitor', 'N/A')}")
                st.markdown(f"**最低维度证据数:** {source_plan.get('minimum_evidence_per_dimension', 'N/A')}")
                with st.expander("完整来源规划"):
                    st.json(source_plan)
            else:
                st.info("无来源规划。")

        with tab5:
            report_outline = plan.get("report_outline") or {}
            if report_outline:
                st.markdown(f"**报告标题:** {report_outline.get('report_title', 'N/A')}")
                sections = report_outline.get("sections") or []
                if sections:
                    rows = [{"ID": s.get("section_id", ""), "标题": s.get("title", ""), "最低字数": s.get("min_words", 0), "人工审核": "是" if s.get("requires_human_review") else "否"} for s in sections if isinstance(s, dict)]
                    if rows:
                        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                with st.expander("完整报告大纲"):
                    st.json(report_outline)
            else:
                st.info("无报告大纲。")

        with tab6:
            checkpoints = plan.get("human_checkpoints") or []
            if checkpoints:
                for cp in checkpoints:
                    if isinstance(cp, dict):
                        with st.expander(f"审核点: {cp.get('title', 'N/A')}"):
                            st.markdown(f"**阶段:** {cp.get('stage', 'N/A')}")
                            st.markdown(f"**必需:** {'是' if cp.get('required') else '否'}")
                            st.markdown(f"**描述:** {cp.get('description', 'N/A')}")
            else:
                st.info("无人工审核点。")

        with tab7:
            metrics = plan.get("success_metrics") or {}
            if metrics:
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                with col_m1:
                    st.metric("最低签约声明数", metrics.get("minimum_signed_claims", "N/A"))
                with col_m2:
                    st.metric("每竞品最低来源数", metrics.get("minimum_sources_per_competitor", "N/A"))
                with col_m3:
                    st.metric("最低证据数", metrics.get("minimum_evidence_items", "N/A"))
                with col_m4:
                    st.metric("最低报告字数", metrics.get("minimum_report_words", "N/A"))
                with st.expander("完整指标"):
                    st.json(metrics)
            else:
                st.info("无成功指标。")

        with tab8:
            if status == "confirmed":
                render_research_plan_dag_preview(plan_id, plan)
            else:
                st.info("方案确认后将生成 DAG。")

        st.divider()

        # Actions
        if status == "draft":
            st.markdown("### 修改方案")
            revise_instruction = st.text_area(
                "修改指令",
                placeholder="例如：重点关注企业部署、安全和定价。添加 AutoGen 作为竞品。",
                height=80,
                key="rp_revise_input",
            )

            col_revise, col_refresh = st.columns([1, 1])
            with col_revise:
                if st.button("根据指令修改", type="primary", use_container_width=True):
                    if not revise_instruction.strip():
                        st.error("请输入修改指令。")
                    else:
                        with st.spinner("正在修改..."):
                            try:
                                resp = requests.post(
                                    f"{API_BASE}/api/research-plans/{plan_id}/revise",
                                    json={"human_instruction": revise_instruction},
                                    timeout=60,
                                )
                                if resp.status_code >= 400:
                                    st.error(f"修改失败: {resp.text}")
                                else:
                                    result = resp.json()
                                    st.session_state["rp_plan_data"] = result.get("research_plan")
                                    st.success("方案已修改！")
                                    st.rerun()
                            except Exception as exc:
                                st.error(f"修改失败: {exc}")

            with col_refresh:
                if st.button("重新加载", use_container_width=True):
                    load_plan(plan_id)
                    st.rerun()

            st.divider()

            st.markdown("### 确认方案")
            st.warning("确认后方案将无法修改，并将创建执行 DAG。")

            if st.button("确认方案并创建 DAG", type="primary", use_container_width=True):
                with st.spinner("确认方案并创建 DAG..."):
                    try:
                        resp = requests.post(f"{API_BASE}/api/research-plans/{plan_id}/confirm", json={}, timeout=60)
                        if resp.status_code >= 400:
                            st.error(f"确认失败: {resp.text}")
                        else:
                            result = resp.json()
                            dag_id = result.get("dag_id")
                            st.success(f"方案已确认！DAG 已创建: `{dag_id}`")
                            load_dag(plan_id)
                            st.session_state["rp_plan_data"] = None  # Clear to reload
                            st.rerun()
                    except Exception as exc:
                        st.error(f"确认失败: {exc}")

        elif status == "confirmed":
            st.success("此方案已确认。执行 DAG 已创建。")
            dag_id = st.session_state.get("rp_dag_data", {}).get("dag_id") if st.session_state.get("rp_dag_data") else None
            if dag_id:
                st.markdown(f"**DAG ID:** `{dag_id}`")

            if st.button("重新加载验证 DAG", use_container_width=True):
                load_dag(plan_id)
                st.rerun()

        # Raw JSON
        st.divider()
        with st.expander("查看完整 JSON", expanded=False):
            st.json(plan)



# ---------------------------------------------------------------------------
# Advanced Manual Form — visible on AnalysisFlow page only
# ---------------------------------------------------------------------------
if page == "AnalysisFlow":
    # Advanced Manual Form — manual project config without conversational intake
    # ---------------------------------------------------------------------------
    with st.expander("高级手动配置（不依赖 AI 解析）", expanded=False):
        st.caption("使用此表单手动配置所有项目设置，无需通过自然语言输入。")

        with st.form("advanced_project_form", clear_on_submit=False):
            adv_pname = st.text_input(
                "项目名称",
                placeholder="例如：AI Agent 平台竞品分析 2026",
                help="该分析项目的描述性名称",
            )

            adv_col_a, adv_col_b = st.columns(2)
            with adv_col_a:
                adv_task_type = st.selectbox(
                    "任务类型",
                    options=[
                        ("competitor_landscape", "竞品全景"),
                        ("product_comparison", "产品对比"),
                        ("pricing_analysis", "定价分析"),
                        ("sales_battlecard", "销售战卡"),
                        ("customer_voice", "客户声音"),
                    ],
                    format_func=lambda x: x[1],
                    index=0,
                )[0]

            with adv_col_b:
                adv_region = st.selectbox(
                    "目标区域",
                    options=["global", "china", "us", "europe", "southeast_asia", "custom"],
                    index=0,
                )

            adv_desc = st.text_area(
                "描述",
                placeholder="简要描述此分析旨在发现的内容...",
                height=80,
            )

            st.divider()
            st.markdown("**竞品**")

            adv_competitor_configs = []
            adv_num_competitors = st.number_input("竞品数量", min_value=1, max_value=20, value=4, step=1)

            for i in range(int(adv_num_competitors)):
                with st.expander(f"竞品 {i + 1}", expanded=(i < 2)):
                    adv_cn = st.text_input(
                        "产品名称",
                        placeholder="例如：Dify",
                        key=f"adv_prod_name_{i}",
                    )
                    adv_cco = st.text_input(
                        "公司名称",
                        placeholder="例如：Dify Technology Co., Ltd.",
                        key=f"adv_prod_company_{i}",
                    )
                    adv_cow = st.text_input(
                        "官网",
                        placeholder="https://dify.ai",
                        key=f"adv_prod_url_{i}",
                    )
                    adv_raw_urls = st.text_area(
                        "Seed URLs（每行一个）",
                        placeholder="https://dify.ai\\nhttps://docs.dify.ai",
                        height=60,
                        key=f"adv_prod_seeds_{i}",
                    )
                    adv_seed_urls = [u.strip() for u in adv_raw_urls.split("\\n") if u.strip()]
                    if adv_cn:
                        adv_competitor_configs.append({
                            "product_name": adv_cn,
                            "company_name": adv_cco,
                            "official_website": adv_cow,
                            "seed_urls": adv_seed_urls,
                        })

            st.divider()
            st.markdown("**分析维度**")

            adv_dims = [
                ("function_tree", "功能对比"),
                ("pricing_model", "定价模式"),
                ("user_persona", "用户画像"),
                ("customer_voice", "客户声音"),
                ("swot", "SWOT 分析"),
                ("enterprise_readiness", "企业级能力"),
                ("market_positioning", "市场定位"),
                ("integration_capabilities", "集成能力"),
            ]

            adv_selected_dims = []
            adv_cols = st.columns(4)
            for idx, (dim_key, dim_label) in enumerate(adv_dims):
                with adv_cols[idx % 4]:
                    if st.checkbox(dim_label, value=True, key=f"adv_dim_{dim_key}"):
                        adv_selected_dims.append(dim_key)

            st.divider()
            adv_submitted = st.form_submit_button("创建并开始分析", type="primary", use_container_width=True)

            if adv_submitted:
                if not adv_pname:
                    st.error("项目名称不能为空。")
                elif not adv_competitor_configs:
                    st.error("至少需要一个竞品。")
                else:
                    try:
                        resp = requests.post(
                            f"{API_BASE}/api/projects",
                            json={
                                "project_name": adv_pname,
                                "task_type": adv_task_type,
                                "target_region": adv_region,
                                "description": adv_desc,
                                "products": adv_competitor_configs,
                                "analysis_dimensions": adv_selected_dims,
                            },
                            timeout=15,
                        )
                        resp.raise_for_status()
                        result = resp.json()
                        new_proj_id = result.get("project_id")
                        ok = start_run_async_and_go_to_running(new_proj_id)
                        if not ok:
                            st.stop()
                    except requests.exceptions.RequestException as e:
                        st.session_state["last_start_error"] = f"创建项目失败: {e}"


