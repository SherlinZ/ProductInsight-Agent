"""Projects page for ProductInsight Agent.

Extracted from app.py (lines 4248-4327).
"""

from __future__ import annotations

from typing import Optional

import streamlit as st
import requests

from frontend.common.api import get_json
from frontend.common.config import API_BASE

from frontend.common.navigation import goto_page
from frontend.common.actions import start_run_async_and_go_to_running


def _status_icon(status: str) -> str:
    return {"completed": "✅", "running": "🟡", "failed": "🔴", "pending": "⏳", "paused": "🟠", "cancelled": "⚫"}.get(status, "⚪")


def render_projects_page(run_id: Optional[str] = None):
    """Render the Projects page."""
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

    # Load all runs for run count / status per project
    try:
        runs_resp = requests.get(f"{API_BASE}/api/runs?limit=200", timeout=10)
        runs_resp.raise_for_status()
        all_runs_raw = runs_resp.json()
        if isinstance(all_runs_raw, dict):
            all_runs = all_runs_raw.get("runs", all_runs_raw.get("data", []))
        elif isinstance(all_runs_raw, list):
            all_runs = all_runs_raw
        else:
            all_runs = []
    except requests.exceptions.RequestException:
        all_runs = []

    # Index runs by project_id; fall back to task_brief.project_id for orphan runs
    runs_by_project: dict[str, list] = {}
    for r in all_runs:
        pid = r.get("project_id")
        if not pid:
            pid = (r.get("task_brief") or {}).get("project_id")
        if pid:
            runs_by_project.setdefault(pid, []).append(r)

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
            goto_page("New Analysis")
    else:
        for proj in filtered:
            pid = proj.get("project_id", "unknown")
            pname = proj.get("project_name", "Untitled")
            ptype = proj.get("task_type", "")
            region = proj.get("target_region", "global")
            status = proj.get("status", "active")
            dims = proj.get("analysis_dimensions", [])
            created = (proj.get("created_at", "") or "")[:10]

            proj_runs = runs_by_project.get(pid, [])
            # Fallback: include runs whose task_brief references this project
            if not proj_runs:
                for r in all_runs:
                    tb_pid = (r.get("task_brief") or {}).get("project_id")
                    if tb_pid == pid:
                        proj_runs = runs_by_project.setdefault(pid, [])
                        proj_runs.append(r)
            run_count = len(proj_runs)
            status_counts = {}
            for r in proj_runs:
                s = r.get("status", "unknown")
                status_counts[s] = status_counts.get(s, 0) + 1

            with st.container():
                cc = st.columns([3, 1, 1, 2, 1])
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
                    st.markdown(f"**Runs**")
                    if run_count == 0:
                        st.text("—")
                    else:
                        parts = [f"{v} {_status_icon(k)}" for k, v in sorted(status_counts.items())]
                        st.markdown(f"**{run_count}** runs  {' '.join(parts)}")
                with cc[4]:
                    st.markdown(f"**Created**")
                    st.text(created)

                # Action buttons
                ba, bb, bc, bd, be = st.columns(5)
                with ba:
                    if st.button("View Details", key=f"view_{pid}"):
                        st.session_state["selected_project_id"] = pid
                        goto_page("Project Workspace")
                with bb:
                    if st.button("Start Run", key=f"start_{pid}"):
                        ok = start_run_async_and_go_to_running(pid)
                        if not ok:
                            st.stop()
                with bc:
                    if st.button("Sources", key=f"src_{pid}"):
                        st.session_state["selected_project_id"] = pid
                        goto_page("Sources")
                with bd:
                    if st.button("Evidence", key=f"ev_{pid}"):
                        st.session_state["selected_project_id"] = pid
                        goto_page("Evidence Hub")
                with be:
                    st.caption("")
                    if st.button("Runs", key=f"runs_{pid}", use_container_width=True):
                        st.session_state["selected_project_id"] = pid
                        st.query_params["project_id"] = pid
                        goto_page("Runs")
                st.divider()
