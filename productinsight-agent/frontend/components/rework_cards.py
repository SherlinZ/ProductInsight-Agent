"""Rework cards component for ProductInsight Agent frontend."""

import streamlit as st

from frontend.common.api import post_json
from frontend.common.config import API_BASE


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

    with st.expander(
        f"🎯 {product_name} — {product_id} | {status}",
        expanded=(status in ("planned", "pending", "running", "failed") or executed or simulated),
    ):
        summary = after_json.get("execution_summary", {})
        exec_sum = after_json.get("execution_summary", {})

        before_src = before_json.get("sources", 0)
        before_ev = before_json.get("evidence", before_json.get("evidence_count", 0))
        before_facts = before_json.get("facts", before_json.get("facts_count", 0))
        before_signed = before_json.get("signed_claims", 0)

        src_added = exec_sum.get("sources_added", 0)
        ev_added = exec_sum.get("evidence_added", 0)
        facts_added = exec_sum.get("facts_added", 0)
        signed_added = exec_sum.get("signed_claims_added", 0)

        after_src_total = before_src + src_added
        after_ev_total = before_ev + ev_added
        after_facts_total = before_facts + facts_added
        after_signed_total = before_signed + signed_added

        def delta_str(val, executed_flag):
            if not executed_flag:
                return None
            return f"+{val}" if val else "0"

        bc1, bc2, bc3, bc4 = st.columns(4)
        bc1.metric("Sources (before)", before_src, delta=delta_str(src_added, executed or simulated))
        bc2.metric("Evidence (before)", before_ev, delta=delta_str(ev_added, executed or simulated))
        bc3.metric("Facts (before)", before_facts, delta=delta_str(facts_added, executed or simulated))
        bc4.metric("Signed Claims (before)", before_signed, delta=delta_str(signed_added, executed or simulated))

        if executed:
            st.success("✅ 真实返工已完成")
            ac1, ac2, ac3, ac4 = st.columns(4)
            ac1.metric("Sources (after)", after_src_total)
            ac2.metric("Evidence (after)", after_ev_total)
            ac3.metric("Facts (after)", after_facts_total)
            ac4.metric("Signed Claims (after)", after_signed_total)
            st.caption(f"本次返工：+{src_added} source(s)，+{ev_added} evidence，+{facts_added} facts，+{signed_added} signed claim(s)")
        elif simulated:
            st.info("🔁 模拟修复已完成，不代表真实补证")
            st.caption(f"模拟：+{ev_added} evidence，+{facts_added} facts，+{signed_added} signed claim(s)（仅为演示）")

        col_s, col_r = st.columns([1, 2])
        with col_s:
            st.caption(f"Status: **{status}**")
        if reason_codes:
            with col_r:
                st.caption("Reason: " + ", ".join(reason_codes))

        missing_dimensions = before_json.get("missing_dimensions") or []
        if missing_dimensions:
            st.caption("缺失维度: " + ", ".join(missing_dimensions))

        existing_urls = task.get("seed_urls") or []
        seed_text = st.text_area(
            "补充抓取 URL（每行一个）",
            value="\n".join(existing_urls),
            key=f"coverage_seed_urls_{rework_id}",
            height=90,
            disabled=is_completed,
        )

        if not is_completed:
            col_exec, col_sim = st.columns(2)
            with col_exec:
                urls = [u.strip() for u in seed_text.splitlines() if u.strip()]
                if st.button("🔍 Execute real rework", key=f"execute_real_rework_{rework_id}", type="primary", use_container_width=True):
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
                if st.button("🎭 Simulate fix（备用演示，不新增证据）", key=f"simulate_rework_{rework_id}", use_container_width=True):
                    try:
                        resp = _requests.post(f"{API_BASE}/api/rework-tasks/{rework_id}/simulate-fix", timeout=60)
                        if resp.status_code >= 400:
                            st.error(f"Simulation failed: HTTP {resp.status_code} - {resp.text}")
                        else:
                            st.success("Simulation completed.")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Simulation failed: {exc}")
        else:
            st.caption("⏹ 已完成，无需重复执行。")

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
                    st.markdown(f":orange[{rc}]")

        st.divider()

        if steps:
            st.markdown("**Rework Plan**")
            for s in steps:
                icon = ":white_check_mark:" if status == "completed" else ":arrow_right:"
                st.markdown(f"{icon} **Step {s.get('step')}:** `{s.get('action')}` (`{s.get('reason')}`) — {s.get('description', '')}")

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
