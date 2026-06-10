"""Workflow status component for ProductInsight Agent frontend."""

import requests
import streamlit as st

from frontend.common.api import get_json
from frontend.common.config import API_BASE
from frontend.common.formatters import _workflow_node_icon, _workflow_node_color


def render_workflow_status(run_id: str, compact: bool = False):
    """Render workflow status for a given run."""
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
    
    st.dataframe(node_rows, width="stretch", hide_index=True, height=300)
    
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
    """Return blocked status info for a run."""
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
    """Return (count, source) for pending reviews."""
    if not run_id:
        return 0, "human_intervention"
    pending_count = 0
    try:
        resp = requests.get(f"{API_BASE}/api/runs/{run_id}/human-interventions?status=pending", timeout=10)
        if resp.status_code == 200:
            pending_count = len(resp.json())
    except Exception:
        pass
    blocked_info = get_run_blocked_info(run_id)
    if blocked_info["is_blocked"] and pending_count == 0:
        return 1, "blocked_report_fallback"
    return pending_count, "human_intervention"
