"""Navigation utilities for ProductInsight Agent frontend."""

import requests
import streamlit as st

from frontend.common.api import get_json
from frontend.common.config import API_BASE


NAV_ZH = {
    "Analysis Flow": "AnalysisFlow",
    "Running Center": "AnalysisFlow",
    "Research Plan": "ResearchPlan",
    "Projects": "Projects",
    "Runs": "Runs",
    "Report": "Report",
    "Project Workspace": "ProjectDetail",
    "Review Center": "HumanReview",
    "Audit / Debug": "TraceAudit",
    # Legacy nav (preserved for direct jump / legacy buttons)
    "DAG 执行": "DAG",
    "Human Review": "HumanReview",
    "Trace & Audit": "TraceAudit",
    "New Analysis": "NewAnalysis",
    "Project Detail": "ProjectDetail",
    "Sources": "Sources",
    "Evidence Hub": "EvidenceHub",
    "Knowledge Table": "KnowledgeTable",
    "分析报告": "Report",
    "Report": "Report",
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
    "Report",
    "Project Workspace",
    "Audit / Debug",
]


def goto_page(display_name: str):
    """Navigate to any page (including hidden legacy pages) by its display name."""
    if display_name in NAV_ZH:
        st.session_state["current_page_zh"] = display_name
        st.rerun()
    else:
        st.error(f"Invalid page: {display_name}")


def render_sidebar():
    """Render the sidebar with navigation and run ID input.
    
    Returns:
        tuple: (page: str, run_id: str)
    """
    # Initialize current_page_zh if not set
    if "current_page_zh" not in st.session_state:
        st.session_state["current_page_zh"] = "Analysis Flow"

    # Compute which sidebar item to highlight
    if st.session_state["current_page_zh"] in NAV_DISPLAY:
        sidebar_selected = st.session_state["current_page_zh"]
    else:
        sidebar_selected = "Audit / Debug"

    selected_nav = st.sidebar.radio(
        "导航",
        NAV_DISPLAY,
        index=NAV_DISPLAY.index(sidebar_selected),
    )

    # Handle navigation change
    # P0-Fix: Use _nav_lock flag to prevent auto-refresh st.rerun() in
    # Analysis Flow (Running Center stage) from accidentally triggering the
    # sidebar radio handler.  When the lock is set, skip this handler and
    # clear the flag — the rerun that set the lock was intentional.
    if st.session_state.pop("_nav_lock", False):
        # Skipping navigation update from a programmatic st.rerun().
        page = NAV_ZH.get(st.session_state["current_page_zh"], "AnalysisFlow")
        # Run ID input
        default_run = _get_default_run_id()
        run_id = st.session_state.get("selected_run_id") or default_run
        return page, run_id

    if selected_nav != sidebar_selected:
        if selected_nav == "Running Center":
            st.session_state["current_page_zh"] = "Analysis Flow"
            st.session_state["af_stage"] = "running"
            if not st.session_state.get("selected_run_id"):
                st.warning("No active run. Please start a run first.")
                st.stop()
        else:
            st.session_state["current_page_zh"] = selected_nav
        st.rerun()

    page = NAV_ZH.get(st.session_state["current_page_zh"], "AnalysisFlow")

    # Run ID input
    def _get_default_run_id():
        if st.session_state.get("selected_run_id"):
            return st.session_state["selected_run_id"]
        try:
            resp = requests.get(f"{API_BASE}/api/runs?limit=100", timeout=10)
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
                completed_any = [r for r in runs if r.get("status") == "completed"]
                if completed_any:
                    latest = sorted(completed_any, key=lambda r: r.get("created_at", ""), reverse=True)[0]
                    return latest.get("run_id")
        except Exception:
            pass
        return "run_demo_ai_agent_001"

    default_run = _get_default_run_id()
    run_id = st.sidebar.text_input("Run ID", value=default_run)

    if run_id and run_id != st.session_state.get("selected_run_id"):
        st.session_state["selected_run_id"] = run_id

    return page, run_id
