"""Project Workspace page for ProductInsight Agent.

Extracted from app.py (lines 4589-5008).
"""

from __future__ import annotations

from typing import Optional

import streamlit as st
import streamlit.components.v1 as components
import requests

from frontend.common.api import get_json
from frontend.common.config import API_BASE
from frontend.common.navigation import goto_page
from frontend.common.actions import start_run_async_and_go_to_running
from frontend.components.workflow_status import render_workflow_status, get_pending_review_count
from frontend.components.human_interventions import render_human_interventions


def _render_section_status_badge(status: str) -> str:
    """Return emoji badge for section status."""
    badges = {
        "drafted": "🟢",
        "missing": "🟡",
        "blocked": "🔴",
    }
    return badges.get(status, "⚪")


import os as _os

_REPORTS_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "data", "reports"
)
_MIN_MD_SIZE = 10 * 1024
_REAL_MD_SIZE = 30 * 1024
_WARNING_MARKERS = ("⚠", "证据不足", "预评估阶段")


def _check_report(run_id: str):
    """Check if report_{run_id}_v2.md exists and get its size + quality."""
    md_path = _os.path.join(_REPORTS_DIR, f"report_{run_id}_v2.md")
    if not _os.path.exists(md_path):
        return None
    size = _os.path.getsize(md_path)
    if size < _MIN_MD_SIZE:
        return {"size": size, "is_real": False, "has_warning": False}
    try:
        head = open(md_path, encoding="utf-8", errors="ignore").read(10 * 1024)
    except Exception:
        head = ""
    has_warning = any(m in head for m in _WARNING_MARKERS)
    is_real = size >= _REAL_MD_SIZE and not has_warning
    return {"size": size, "is_real": is_real, "has_warning": has_warning}


# ── Planned → Runtime node mapping (planned DAG vs actual LangGraph backbone) ──
PLANNED_TO_RUNTIME_MAPPING = [
    ("node_confirm_plan", "build_task_brief"),
    ("node_collect_sources", "collect_sources"),
    ("node_extract_evidence", "evaluate_evidence"),
    ("node_extract_facts", "extract_facts"),
    ("node_generate_claims", "detect_schema_gaps"),
    ("node_review_claims", "review_claims"),
    ("node_plan_report_outline", "write_report"),
    ("node_write_sections", "write_report"),
    ("node_review_report", "final_review"),
    ("node_compose_final_report", "export_report"),
]

# Actual runtime nodes (16-node LangGraph backbone)
RUNTIME_NODES = [
    "build_task_brief", "plan_schema", "plan_sources", "collect_sources",
    "evidence_extraction", "evaluate_evidence", "pii_scrub", "extract_facts",
    "detect_schema_gaps", "coverage_critic", "execute_rework",
    "analyze_dimensions", "review_claims", "reflect_on_review",
    "prepare_human_intervention", "write_report_v2", "final_review",
    "export_report", "compute_metrics",
]

# Planned DAG nodes (from compile_execution_dag in research_planner.py)
PLANNED_DAG_NODES = [
    "node_confirm_plan", "node_collect_sources", "node_extract_evidence",
    "node_extract_facts", "node_generate_claims", "node_review_claims",
    "node_plan_report_outline", "node_write_sections", "node_review_report",
    "node_compose_final_report",
]


def render_project_workspace_page(run_id: Optional[str] = None):
    """Render the Project Workspace page."""
    proj_id = st.session_state.get("selected_project_id")

    if not proj_id:
        st.warning("No project selected. Go to **Projects** to select one.")
        if st.button("Go to Projects"):
            goto_page("Projects")
        return

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

    # Get latest run
    latest_run = proj.get("latest_run")
    agg = proj.get("aggregates", {})

    # Build project_runs list
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

    # Default to session state run
    saved_run_id = st.session_state.get("selected_run_id")
    if saved_run_id and saved_run_id in project_runs:
        default_run_id = saved_run_id
    elif latest_run and latest_run.get("run_id"):
        default_run_id = latest_run.get("run_id")
    elif sorted_runs:
        default_run_id = sorted_runs[0].get("run_id", "")
    else:
        default_run_id = ""

    st.divider()

    # Active Run Selector
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

        st.markdown("**Active Run ID**")
        st.code(active_run_id, language=None)

        latest_run_id = latest_run.get("run_id") if latest_run else ""
        if latest_run_id and latest_run_id != active_run_id:
            st.caption(f"Latest Run ID: `{latest_run_id}`")

        if active_run_id and active_run_id in project_runs:
            ar = project_runs[active_run_id]
            a_status = ar.get("status", "unknown")
            a_mode = ar.get("mode", "N/A")
            a_node = ar.get("current_node", "—") or "—"
            ac = {"completed": "green", "running": "blue", "failed": "red", "pending": "gray"}.get(a_status, "gray")
            st.caption(f"Status: :{ac}[{a_status}] | Mode: {a_mode} | Node: {a_node}")
            if active_run_id != latest_run_id:
                st.info("You are viewing a selected run, not the latest run.")

            if a_status == "pending":
                st.warning("该 Run 已创建但尚未启动。")
                if st.button("继续启动并进入运行中心", key=f"retry_pending_{active_run_id}"):
                    ok = start_run_async_and_go_to_running(proj_id, active_run_id)
                    if not ok:
                        st.stop()
        else:
            active_run_id = ""
            st.info("No runs yet. Start an analysis run first.")

    st.session_state["selected_run_id"] = active_run_id

    # Tabs
    # vNext-P0: Support navigation from AnalysisFlow deliverables → Deliverables tab
    default_tab = st.session_state.pop("pw_active_tab", "Overview")
    TAB_INDEX_MAP = {
        "Overview": 0, "Workflow": 1,
        "Deliverables": 2, "Audit": 3,
    }
    tab_idx = TAB_INDEX_MAP.get(default_tab, 0)
    tab_overview, tab_workflow, tab_deliverables, tab_audit = st.tabs([
        "Overview", "Workflow", "Deliverables", "Audit"
    ])

    with tab_overview:
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

        st.markdown("#### Project Health")

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

        # Active Run Status
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

        # Workflow Progress
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

        # Competitors
        products = proj.get("products", [])
        if products:
            st.divider()
            with st.expander(f"Competitors ({len(products)})", expanded=False):
                for p in products:
                    url = p.get("official_website", "")
                    link_md = f" [{url}]({url})" if url else ""
                    st.markdown(f"- **{p.get('product_name', 'N/A')}** — {p.get('company_name', '')}{link_md}")

        # Quick Actions
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
                st.session_state["pw_active_tab"] = "Deliverables"
                goto_page("Project Workspace")

    with tab_workflow:
        st.subheader("Workflow Status")
        if active_run_id:
            render_workflow_status(active_run_id, compact=True)

            # ── Execution Model: Planned DAG vs Actual Runtime Graph ───────────────
            st.divider()
            st.markdown("### 🔀 Execution Model")

            # Gather planned DAG metadata from project
            planned_dag_nodes = PLANNED_DAG_NODES
            planned_dag_edges = []
            proj_metadata = proj.get("metadata", {}) or {}
            research_plan = proj_metadata.get("research_plan", {}) or {}
            execution_dag = research_plan.get("execution_dag", {}) or {}
            if isinstance(execution_dag, dict):
                planned_dag_nodes_meta = execution_dag.get("nodes", planned_dag_nodes)
                planned_dag_edges_meta = execution_dag.get("edges", [])
            else:
                planned_dag_nodes_meta = planned_dag_nodes
                planned_dag_edges_meta = []

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**Planned DAG** (from ResearchPlan)")
                st.caption("Defines the planned analysis flow before execution")
                st.metric("Planned Nodes", len(planned_dag_nodes_meta) if planned_dag_nodes_meta else "N/A")
                st.metric("Planned Edges", len(planned_dag_edges_meta) if planned_dag_edges_meta else "N/A")
                with st.expander("Planned Nodes", expanded=False):
                    for n in planned_dag_nodes_meta:
                        st.markdown(f"- `{n}`")

            with col2:
                st.markdown("**Actual Runtime Graph** (LangGraph)")
                st.caption("The 16-node backbone that executes in real_time mode")
                runtime_workflow = get_json(f"/api/runs/{active_run_id}/workflow", {})
                completed_count = 0
                if runtime_workflow:
                    completed_count = runtime_workflow.get("summary", {}).get("completed", 0)
                st.metric("Runtime Nodes", len(RUNTIME_NODES))
                st.metric("Completed Nodes", completed_count if completed_count else "—")
                with st.expander("Runtime Nodes", expanded=False):
                    for n in RUNTIME_NODES:
                        st.markdown(f"- `{n}`")

            st.markdown("**Planned → Runtime Node Mapping**")
            mapping_data = {
                "Planned Node": [m[0] for m in PLANNED_TO_RUNTIME_MAPPING],
                "Runtime Node": [m[1] for m in PLANNED_TO_RUNTIME_MAPPING],
            }
            st.table(mapping_data)
        else:
            st.info("No run yet. Start an analysis run first from the Overview tab.")

    with tab_deliverables:
        st.subheader("Deliverables")
        if active_run_id:
            run_status = project_runs.get(active_run_id, {}).get("status", "unknown") if active_run_id in project_runs else "unknown"
            report_data = get_json(f"/api/runs/{active_run_id}/report", {}) or {}

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Report Status**")
                report_has_content = False
                
                # Check if report has content (from API that now reads file content)
                report_content = report_data.get("content_markdown", "") or report_data.get("report_content", "") or report_data.get("content", "")
                html_path = report_data.get("content_html_path", "")
                report_has_content = bool(report_content or html_path)
                report_status = report_data.get("report_status", "unknown") if isinstance(report_data, dict) else "unknown"
                
                if run_status == "completed" or report_has_content:
                    # Show report status message
                    if run_status == "failed":
                        st.error(f"Run failed (blocked by quality gate). Report content may be partial.")
                    elif report_status == "blocked":
                        st.warning(f"Report blocked by quality gate. Partial content available.")
                    else:
                        qs_data = report_data.get("quality_summary", {}) if isinstance(report_data, dict) else {}
                        insufficient = qs_data.get("insufficient_products", 0) if isinstance(qs_data, dict) else 0
                        partial = qs_data.get("partial_products", 0) if isinstance(qs_data, dict) else 0
                        if insufficient > 0:
                            st.warning(f"⚠ 分析流程已完成，但 {insufficient} 个产品存在证据覆盖不足。报告结果应谨慎使用。")
                        elif partial > 0:
                            st.info(f"分析流程已完成，但 {partial} 个产品证据覆盖不完整。结果仅供参考。")
                        else:
                            st.success("✅ 分析流程已完成，报告已就绪。")
                    
                    if st.button("View Report", key=f"ws_deliv_viewreport_{active_run_id}", type="primary"):
                        goto_page("分析报告")
                elif run_status == "failed":
                    st.error(f"Run failed. No report content available.")
                else:
                    st.info(f"Report will be available after run completes. Current status: {run_status}")

            with c2:
                st.markdown("**Evidence & Knowledge**")
                st.metric("Evidence Items", agg.get("evidence_count", 0))
                st.metric("Facts", agg.get("fact_count", 0))

            st.divider()

            # ── Report Outline / Section Status ─────────────────────────────────
            st.markdown("**📋 Report Outline / Section Status**")
            # Fetch report draft data for section-level detail
            report_draft = get_json(f"/api/runs/{active_run_id}/report-draft", {}) or {}
            outline_titles_map = {}
            outline_raw = report_draft.get("report_outline", {}) or {}
            if isinstance(outline_raw, dict):
                outline_sections = outline_raw.get("sections", [])
            elif isinstance(outline_raw, list):
                outline_sections = outline_raw
            else:
                outline_sections = []
            if outline_sections:
                for s in outline_sections:
                    t = (s.get("title") or "").lower().strip()
                    if t:
                        outline_titles_map[t] = s

            sections = report_draft.get("sections", [])
            report_outline_sections = outline_sections

            # vNext-R3-A: Deep Report v2 Support - Tables and Figures
            report_version = report_draft.get("report_version", "v1")
            if report_version == "v2":
                tables = report_draft.get("tables", [])
                figures = report_draft.get("figures", [])
                
                if tables:
                    with st.expander("📊 Comparison Matrices", expanded=False):
                        for table in tables:
                            st.markdown(f"**{table.get('table_title', '对比矩阵')}**")
                            # Render simple table
                            headers = table.get("headers", [])
                            rows = table.get("rows", [])
                            cells = table.get("cells", {})
                            
                            if headers and rows:
                                table_data = []
                                for row in rows:
                                    row_data = [row]
                                    for h in headers[1:]:
                                        cell_key = f"{row}_{h}"
                                        cell_data = cells.get(cell_key, {})
                                        if isinstance(cell_data, dict):
                                            row_data.append(cell_data.get("text", "—"))
                                        else:
                                            row_data.append(cell_data)
                                    table_data.append(row_data)
                                
                                import pandas as pd
                                df = pd.DataFrame(table_data, columns=headers)
                                st.dataframe(df, use_container_width=True, hide_index=True)
                            st.divider()
                
                if figures:
                    with st.expander("📈 Charts & SWOT Cards", expanded=False):
                        for figure in figures:
                            st.markdown(f"**{figure.get('figure_title', '图表')}**")
                            ft = figure.get("figure_type", "")
                            if ft == "swot_card":
                                chart_data = figure.get("chart_data", {})
                                swot_cols = st.columns(4)
                                swot_items = [
                                    ("strengths", "💪 优势", swot_cols[0]),
                                    ("weaknesses", "⚠️ 劣势", swot_cols[1]),
                                    ("opportunities", "🚀 机会", swot_cols[2]),
                                    ("threats", "⚡ 威胁", swot_cols[3]),
                                ]
                                for key, label, col in swot_items:
                                    with col:
                                        st.markdown(f"**{label}**")
                                        items = chart_data.get(key, [])
                                        for item in items[:3]:
                                            st.markdown(f"- {item}")
                                        if not items:
                                            st.caption("待补充")
                            else:
                                st.caption(f"图表类型: {ft}")
                            st.divider()

            if report_outline_sections or sections:
                with st.expander("🔍 View Section Details", expanded=False):
                    if report_outline_sections:
                        st.markdown("**Outline Sections**")
                        outline_data = []
                        for s in report_outline_sections:
                            outline_data.append({
                                "Section ID": s.get("section_id", "—"),
                                "Title": s.get("title", "—"),
                                "Purpose": s.get("purpose", "")[:80] + ("..." if len(s.get("purpose", "")) > 80 else ""),
                            })
                        st.dataframe(outline_data, use_container_width=True)

                    if sections:
                        st.markdown("**Drafted Sections**")
                        drafted_data = []
                        for section in sections:
                            title = section.get("section_title", "Unknown")
                            section_id = section.get("section_id", "")
                            # v2 has word_count field directly; v1 computes from content
                            word_count = section.get("word_count", 0) or 0
                            if word_count == 0:
                                content = section.get("content_markdown", "")
                                word_count = len(content.split()) if content else 0
                            # v2: cited_claims_count is a number; v1: cited_claims is a list
                            cited_count = section.get("cited_claims_count", 0) or 0
                            if cited_count == 0:
                                cited_claims = section.get("cited_claims", [])
                                claim_ids = section.get("claim_ids", [])
                                cited_count = len(cited_claims) if cited_claims is not None else 0
                                if cited_count == 0 and claim_ids:
                                    cited_count = len(claim_ids)

                            # Use authoritative status from section data (set by review_section)
                            # v2 status values: "completed", "pending", "failed", etc.
                            authoritative_status = section.get("status", "")
                            if authoritative_status == "draft_complete":
                                badge = "🟢 drafted"
                            elif authoritative_status == "revision_requested":
                                badge = "🔄 revision_requested"
                            elif authoritative_status in ("pending", "research_pack_ready"):
                                badge = "⏳ in_progress"
                            elif authoritative_status in ("review_complete", "approved"):
                                badge = "✅ reviewed"
                            elif authoritative_status in ("failed", "error"):
                                badge = "🔴 failed"
                            elif authoritative_status == "completed":
                                badge = "🟢 drafted"  # v2 uses "completed" for done sections
                            elif authoritative_status == "drafted":
                                badge = "🟢 drafted"
                            else:
                                if word_count > 0:
                                    badge = "🟢 drafted"
                                else:
                                    badge = "🟡 missing"

                            drafted_data.append({
                                "Section": title,
                                "Section ID": section_id,
                                "Status": badge,
                                "Words": word_count,
                                "Cited Claims": cited_count,
                            })

                        if drafted_data:
                            st.dataframe(drafted_data, use_container_width=True)
            else:
                st.info("No report outline defined for this run yet.")

            st.divider()
            st.markdown("**Quick Links**")
            q1, q2, q3 = st.columns(3)
            with q1:
                if st.button("Evidence Hub", key=f"ws_deliv_evidence_{active_run_id}", use_container_width=True):
                    goto_page("Evidence Hub")
            with q2:
                if st.button("Knowledge Table", key=f"ws_deliv_knowledge_{active_run_id}", use_container_width=True):
                    goto_page("Knowledge Table")
            with q3:
                if st.button("Sources", key=f"ws_deliv_sources_{active_run_id}", use_container_width=True):
                    goto_page("Sources")
        else:
            st.info("No run yet. Start an analysis run first from the Overview tab.")

    with tab_audit:
        st.subheader("🔍 Audit & Trace")
        if active_run_id:
            st.markdown(f"**Run ID:** `{active_run_id}`")
            
            # Fetch trace data
            trace_summary = get_json(f"/api/runs/{active_run_id}/trace-summary", default={})
            latest_traces = get_json(f"/api/runs/{active_run_id}/trace-latest?limit=10", default=[])
            
            # Trace Summary
            if trace_summary and trace_summary.get("total_traces", 0) > 0:
                st.markdown("**📊 Trace Summary**")
                col1, col2, col3, col4, col5, col6 = st.columns(6)
                with col1:
                    st.metric("Total", trace_summary.get("total_traces", 0))
                with col2:
                    st.metric("Failed", trace_summary.get("failed_traces", 0))
                with col3:
                    st.metric("LLM", trace_summary.get("llm_calls", 0))
                with col4:
                    st.metric("Non-LLM", trace_summary.get("non_llm_calls", 0))
                with col5:
                    tokens = trace_summary.get("total_tokens", 0)
                    st.metric("Tokens", f"{tokens:,}" if tokens else "-")
                with col6:
                    lat = trace_summary.get("total_latency_ms", 0)
                    st.metric("Latency", f"{lat/1000:.1f}s" if lat else "-")
                
                # vNext-R2-C: Detailed LLM call breakdown
                successful_llm = trace_summary.get('successful_llm_calls', 0)
                failed_llm = trace_summary.get('failed_llm_calls', 0)
                fallback_llm = trace_summary.get('fallback_llm_calls', 0)
                
                if successful_llm > 0 or failed_llm > 0 or fallback_llm > 0:
                    st.divider()
                    llm_col1, llm_col2, llm_col3, llm_col4 = st.columns(4)
                    with llm_col1:
                        st.metric("LLM Attempts", trace_summary.get("llm_calls", 0))
                    with llm_col2:
                        st.metric("✅ Successful", successful_llm if successful_llm > 0 else "-")
                    with llm_col3:
                        st.metric("❌ Failed", failed_llm if failed_llm > 0 else "-")
                    with llm_col4:
                        st.metric("⚡ Fallback", fallback_llm if fallback_llm > 0 else "-")

            # Prominent shortcut to full trace view
            if st.button("🔍 查看完整 Trace 记录（Prompt / Token / 决策）", key=f"ws_audit_full_trace_{active_run_id}", use_container_width=True):
                goto_page("Trace & Audit")
            st.caption("包含每次 LLM 调用的完整 Prompt、输入、输出、Token 消耗及决策摘要")
            
            st.divider()
            
            # Latest Traces
            if latest_traces:
                st.markdown("**📋 Latest Traces**")

                status_icon = {
                    "success": "🟢",
                    "failed": "🔴",
                    "running": "🟡",
                    "paused": "🟠",
                }

                for trace in latest_traces:
                    status = trace.get("status", "")
                    icon = status_icon.get(status, "⚪")
                    nk = trace.get("node_name", "-")
                    agent = trace.get("agent_name", "-")
                    event_type = trace.get("event_type", "")
                    lat = trace.get("latency_ms", 0) or 0
                    lat_str = f"{lat/1000:.1f}s" if lat >= 1000 else f"{lat}ms"
                    tok_in = trace.get("token_input", 0) or 0
                    tok_out = trace.get("token_output", 0) or 0
                    err = trace.get("error_message", "")

                    # vNext-R2-D: Highlight search_call traces
                    if event_type == "search_call":
                        # Special styling for search calls
                        result_count = ""
                        input_payload = trace.get("input_payload", {}) or {}
                        if isinstance(input_payload, dict):
                            query = input_payload.get("query", "")
                            if query:
                                query_short = query[:40] + "..." if len(query) > 40 else query
                                result_count = f" 🔍 \"{query_short}\""
                        if err:
                            st.markdown(f"{icon} `{nk}` — {agent} — {lat_str} — 🔍 **search_call** ❌ {err[:60]}")
                        else:
                            output_payload = trace.get("output_payload", {}) or {}
                            if isinstance(output_payload, dict):
                                count = output_payload.get("result_count", 0)
                                result_count = f" ({count} results)"
                            st.markdown(f"{icon} `{nk}` — {agent} — {lat_str} — 🔍 **search_call**{result_count}")
                    elif err:
                        st.markdown(f"{icon} `{nk}` — {agent} — {lat_str} — ❌ {err[:60]}")
                    else:
                        st.markdown(f"{icon} `{nk}` — {agent} — {lat_str} — tokens:[{tok_in}+{tok_out}]")
            else:
                st.info("No traces available.")
            
            st.divider()
            
            # Navigation buttons
            st.markdown("**🔗 Open Full Pages**")
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                if st.button("🔍 Full TraceAudit", key=f"ws_audit_trace_{active_run_id}", use_container_width=True):
                    goto_page("Trace & Audit")
            with col_btn2:
                if st.button("🔄 DAG Execution", key=f"ws_audit_dag_{active_run_id}", use_container_width=True):
                    goto_page("DAG 执行")
        else:
            st.info("No active run. Start an analysis to see traces.")

    # Quick actions
    st.divider()
    qa_col1, qa_col2 = st.columns([1, 1])
    with qa_col1:
        if st.button("Start New Analysis Run", type="primary", use_container_width=True):
            ok = start_run_async_and_go_to_running(proj_id)
            if not ok:
                st.stop()
    with qa_col2:
        st.caption(":warning: Resume/re-run will be implemented in backend next.")
