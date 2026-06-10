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
                st.divider()
