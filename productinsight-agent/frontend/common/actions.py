"""Action utilities for ProductInsight Agent frontend."""

from __future__ import annotations

from typing import Optional

import requests
import streamlit as st

from frontend.common.config import API_BASE


def start_run_async_and_go_to_running(project_id: str, run_id: Optional[str] = None) -> bool:
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
    st.session_state["last_start_error"] = ""
    st.rerun()
