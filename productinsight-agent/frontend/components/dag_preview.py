"""DAG preview component for ProductInsight Agent frontend."""

from __future__ import annotations

from typing import Optional

import requests
import streamlit as st
import pandas as pd

from frontend.common.config import API_BASE


def load_dag(plan_id: str) -> bool:
    """Load DAG data from API into session state."""
    try:
        resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}/dag", timeout=30)
        if resp.status_code >= 400:
            return False
        st.session_state["rp_dag_data"] = resp.json()
        return True
    except Exception:
        return False


def render_research_plan_dag_preview(plan_id: str, plan: Optional[dict] = None):
    """Render DAG preview for Research Plan page.
    
    - Prioritizes st.session_state["rp_dag_data"]
    - Falls back to load_dag() API call
    - Falls back to plan.get("execution_dag")
    - Handles both edge formats: {"from": ..., "to": ...} and {"from_node": ..., "to_node": ...}
    """
    import requests
    
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
