"""Human interventions component for ProductInsight Agent frontend."""

import streamlit as st

from frontend.common.api import get_json


def _render_intervention_card(interv: dict, compact: bool = False, run_id: str = ""):
    """Render a single intervention card with action buttons."""
    interv_id = interv.get("intervention_id", "")
    action = interv.get("action", "")
    status = interv.get("status", "unknown")
    node_name = interv.get("node_name", "N/A")
    artifact_type = interv.get("artifact_type", "N/A")
    artifact_id = interv.get("artifact_id", "N/A")
    reason = interv.get("reason", "")
    instructions = interv.get("instructions", "")
    created = interv.get("created_at", "N/A")

    status_icon = "⚠️" if status == "pending" else ("✅" if status == "resolved" else "⚪")
    safe_label = f"{interv_id[:16]}..." if isinstance(interv_id, str) and interv_id else "N/A"

    with st.expander(
        f"{status_icon} `{safe_label}` | action: {action} | status: {status}",
        expanded=(status == "pending"),
    ):
        col1, col2 = st.columns(2)
        with col1:
            st.text(f"Node: {node_name}")
            st.text(f"Artifact: {artifact_type} / {artifact_id}")
            st.text(f"Action: {action}")
        with col2:
            safe_created = created[:19].replace("T", " ") if isinstance(created, str) and created else "N/A"
            st.text(f"Created: {safe_created}")
            resolved_at = interv.get("resolved_at", "")
            if resolved_at:
                safe_resolved = resolved_at[:19].replace("T", " ") if isinstance(resolved_at, str) else "N/A"
                st.text(f"Resolved: {safe_resolved}")
            resolved_by = interv.get("resolved_by", "")
            if resolved_by:
                st.text(f"By: {resolved_by}")

        if reason:
            st.markdown(f"**Reason:** {reason}")

        # vNext-R2-D: Special handling for search provider not configured
        before_json = interv.get("before_json", {}) or {}
        if isinstance(before_json, dict) and before_json.get("reason_code") == "SEARCH_PROVIDER_NOT_CONFIGURED":
            st.error("⚠️ **搜索提供者未配置** - 自动来源发现需要配置搜索 API")
            products = before_json.get("products_needing_discovery", [])
            if products:
                st.markdown(f"**需要来源发现的竞品:** {', '.join(products)}")
            st.info("💡 请配置环境变量 `TAVILY_API_KEY`、`SERPAPI_API_KEY` 或 `SEARCH_API_ENDPOINT`")
        if instructions:
            st.markdown(f"**Instructions:** {instructions}")

        if status == "pending":
            act_cols = st.columns(4)
            with act_cols[0]:
                if st.button("✅ Approve", key=f"interv_approve_{interv_id}", use_container_width=True):
                    try:
                        import requests as _requests
                        from frontend.common.config import API_BASE
                        resp = _requests.post(
                            f"{API_BASE}/api/human-interventions/{interv_id}/approve",
                            json={"notes": ""},
                            timeout=15,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Approved")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            with act_cols[1]:
                if st.button("❌ Reject", key=f"interv_reject_{interv_id}", use_container_width=True):
                    try:
                        import requests as _requests
                        from frontend.common.config import API_BASE
                        resp = _requests.post(
                            f"{API_BASE}/api/human-interventions/{interv_id}/reject",
                            json={"notes": ""},
                            timeout=15,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Rejected")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            with act_cols[2]:
                if st.button("⏭️ Skip", key=f"interv_skip_{interv_id}", use_container_width=True):
                    try:
                        import requests as _requests
                        from frontend.common.config import API_BASE
                        resp = _requests.post(
                            f"{API_BASE}/api/human-interventions/{interv_id}/respond",
                            json={"response": "skip", "notes": ""},
                            timeout=15,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Skipped")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            with act_cols[3]:
                if st.button("🔧 Request Rework", key=f"interv_rework_{interv_id}", use_container_width=True):
                    try:
                        import requests as _requests
                        from frontend.common.config import API_BASE
                        resp = _requests.post(
                            f"{API_BASE}/api/human-interventions/{interv_id}/request-rework",
                            json={"notes": "", "requested_by": "human_reviewer"},
                            timeout=15,
                        )
                        if resp.status_code >= 400:
                            st.error(f"Request rework failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Rework requested")
                            st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

            with st.expander("Edit response (coming in Review Center v2)"):
                st.info("The edit / custom response form will be available in a future update.")


def render_human_interventions(run_id: str, compact: bool = False):
    """Render human interventions for a given run."""
    if not run_id:
        st.info("No run selected. Please select a run first.")
        return

    st.subheader("Human Interventions")

    interventions = get_json(f"/api/runs/{run_id}/human-interventions?status=pending", [])

    safe_run_label = f"{run_id[:16]}..." if isinstance(run_id, str) and run_id else "N/A"

    if not interventions:
        st.success(f"No pending human interventions for run `{safe_run_label}`. All interventions have been resolved.")
        if compact:
            st.caption(
                "Pending interventions are scoped to the selected run. "
                "If you expected an item here, switch Active Run or open the standalone Human Review page with that run ID."
            )
    else:
        st.warning(f"Found {len(interventions)} pending intervention(s). Action required.")

        for interv in interventions:
            _render_intervention_card(interv, compact, run_id=run_id)
            st.divider()

    # Show all interventions in compact mode
    if compact:
        with st.expander("View all interventions for this run"):
            all_interventions = get_json(f"/api/runs/{run_id}/human-interventions", [])
            if not all_interventions:
                st.info("No interventions found for this run.")
            else:
                st.text(f"Total: {len(all_interventions)} intervention(s)")
                status_counts = {}
                for i in all_interventions:
                    s = i.get("status", "unknown")
                    status_counts[s] = status_counts.get(s, 0) + 1
                st.text(f"Status: {', '.join(f'{k}={v}' for k, v in status_counts.items())}")

                for interv in all_interventions:
                    status = interv.get("status", "unknown")
                    status_icon = "⚠️" if status == "pending" else ("✅" if status == "resolved" else "⚪")
                    safe_interv_id = interv.get("intervention_id", "")
                    safe_label = f"{safe_interv_id[:16]}..." if isinstance(safe_interv_id, str) and safe_interv_id else "N/A"
                    with st.expander(
                        f"{status_icon} `{safe_label}` - "
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
                            resolved_at = interv.get("resolved_at", "")
                            if resolved_at:
                                st.text(f"Resolved: {resolved_at}")
                            resolved_by = interv.get("resolved_by", "")
                            if resolved_by:
                                st.text(f"By: {resolved_by}")
