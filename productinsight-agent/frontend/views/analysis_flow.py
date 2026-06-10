"""AnalysisFlow page for ProductInsight Agent.

Extracted from app.py (lines 1708-3072).
"""

from __future__ import annotations

from typing import Optional

import json
import sys
import time
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import requests

from frontend.common.api import get_json, post_json
from frontend.common.config import API_BASE
from frontend.common.navigation import goto_page
from frontend.common.state import reset_analysis_flow_state
from frontend.common.actions import start_run_async_and_go_to_running
from frontend.components.dag_preview import render_research_plan_dag_preview
from frontend.common.formatters import _workflow_node_icon


# DAG App URL (ReactFlow visualization running on port 3001)
import os as _os
_DAG_APP_URL = _os.environ.get("DAG_APP_URL", "http://172.18.40.105:3001")


def render_dag_iframe(run_id: str, height: int = 580):
    """Embed the ReactFlow DAG visualization via iframe.

    Falls back gracefully if the DAG app is not running.
    """
    dag_url = f"{_DAG_APP_URL}/?run_id={run_id}"
    try:
        # Lightweight connectivity check — if the DAG app responds, show the iframe
        check = requests.get(f"{_DAG_APP_URL}/", timeout=1.5)
        if check.status_code < 500:
            components.html(
                f'<iframe src="{dag_url}" width="100%" height="{height}" '
                f'style="border:none;border-radius:8px;"></iframe>',
                height=height + 10,
                scrolling=False,
            )
            return True
    except requests.exceptions.RequestException:
        pass

    # Fallback: show connection error hint
    st.caption(
        f":orange[DAG 可视化暂不可用（需要启动 DAG App: `npm run dev` in `frontend/dag-app/`）]"
    )
    return False


def render_analysis_flow(run_id: Optional[str] = None):
    """Render the Analysis Flow page."""
    st.header("新建竞品分析")

    # vNext-P0-Real-Frontend-Integration: Show build_tag from backend for version confirmation
    try:
        sys_resp = requests.get(f"{API_BASE}/api/system/status", timeout=5)
        if sys_resp.status_code == 200:
            sys_data = sys_resp.json()
            build_tag = sys_data.get("build_tag", "")
            if build_tag:
                st.caption(f":grey[Build: **{build_tag}**]")
    except Exception:
        pass

    # New Task button in top right
    col_title, col_new_task = st.columns([4, 1])
    with col_title:
        st.caption("基于自然语言输入的智能竞品调研流程")
    with col_new_task:
        if st.button("+ New Task", use_container_width=True):
            reset_analysis_flow_state()
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
            reset_analysis_flow_state()
            st.rerun()

        if generate_clicked and user_request.strip():
            st.session_state["intake_user_request"] = user_request.strip()

            with st.spinner("正在生成调研方案..."):
                try:
                    resp = requests.post(
                        f"{API_BASE}/api/research-plans/generate",
                        json={
                            "user_query": user_request.strip(),
                            "schema_type": "",
                            "target_region": "global",
                            "mode": "review",
                        },
                        timeout=120,
                    )
                    if resp.status_code >= 400:
                        st.error(f"Plan generation failed: HTTP {resp.status_code} - {resp.text}")
                        st.stop()
                    result = resp.json()
                    research_plan = result.get("research_plan", {})

                    st.session_state["rp_plan_id"] = result.get("research_plan_id")
                    st.session_state["rp_plan_data"] = research_plan
                    st.session_state["rp_user_query"] = user_request.strip()

                    # Jump to Research Plan page for review
                    st.session_state["current_page_zh"] = "Research Plan"
                    st.rerun()

                except Exception as exc:
                    st.error(f"Plan generation failed: {exc}")
                    st.stop()

    # -------------------------------------------------------------------------
    # Stage 2: Running — handled by Research Plan page confirmation,
    # but the running/reporting logic stays here for active runs
    # -------------------------------------------------------------------------
    elif stage == "running":
        st.subheader("运行中心")
        proj_id = st.session_state.get("selected_project_id")
        run_id = st.session_state.get("selected_run_id")

        # Auto-discover latest running run if none selected
        if not proj_id or not run_id:
            try:
                resp = requests.get(f"{API_BASE}/api/runs?status=running&limit=5", timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    runs = data if isinstance(data, list) else data.get("runs", [])
                    # Find most recent running run
                    running_runs = [r for r in runs if r.get("status") == "running"]
                    if running_runs:
                        latest = sorted(running_runs, key=lambda r: r.get("created_at", ""), reverse=True)[0]
                        run_id = latest.get("run_id")
                        proj_id = latest.get("project_id")
                        st.session_state["selected_run_id"] = run_id
                        st.session_state["selected_project_id"] = proj_id
                        st.session_state["af_stage"] = "running"
            except Exception:
                pass

        if not proj_id:
            st.warning("没有正在运行的 Run。请从 Analysis Flow 页面启动分析任务。")
            reset_analysis_flow_state()
            st.rerun()
            return

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
        current_agent = live_data.get("current_agent", "") if live_data else ""
        current_action = live_data.get("current_action", "") if live_data else ""
        wf_nodes = live_data.get("workflow_nodes", []) if live_data else []
        wf_summary = live_data.get("workflow_summary", {}) if live_data else {}
        latest_traces = live_data.get("latest_traces", []) if live_data else []
        trace_summary = live_data.get("trace_summary", {}) if live_data else {}
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

        if run_status == "running" and (current_agent or current_action):
            if current_agent:
                st.caption(f"当前 Agent: {current_agent}")
            if current_action:
                st.caption(f"当前 Action: {current_action}")

        # Show trace summary if available
        if trace_summary and trace_summary.get("total_traces", 0) > 0:
            col_ts1, col_ts2, col_ts3, col_ts4, col_ts5 = st.columns(5)
            with col_ts1:
                st.metric("Traces", trace_summary.get("total_traces", 0))
            with col_ts2:
                st.metric("LLM Calls", trace_summary.get("llm_calls", 0))
            with col_ts3:
                st.metric("Non-LLM", trace_summary.get("non_llm_calls", 0))
            with col_ts4:
                failed = trace_summary.get("failed_traces", 0)
                st.metric("Failed", failed)
            with col_ts5:
                tokens = trace_summary.get("total_tokens", 0)
                st.metric("Tokens", f"{tokens:,}" if tokens else "0")
            
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
            
            st.divider()

        # DAG visualization tab — takes full width above the 3-column layout
        if wf_nodes:
            dag_tab, phases_tab = st.tabs(["DAG 图", "阶段列表"])
            with dag_tab:
                dag_available = render_dag_iframe(effective_run_id)
                if not dag_available:
                    st.info("请启动 DAG App 以查看动态 DAG 图：\n"
                            "`cd frontend/dag-app && npm run dev`")
            with phases_tab:
                col_phases, col_timeline, col_counts = st.columns([1, 1, 1])

                with col_phases:
                    st.markdown("##### 🔄 工作流阶段")

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

                with col_timeline:
                    st.markdown("##### 📋 执行时间线")

                    if latest_traces:
                        for trace in reversed(latest_traces):
                            nk = trace.get("node_name", "")
                            lat_ms = trace.get("latency_ms", 0) or 0
                            status_t = trace.get("status", "")
                            ticon = _workflow_node_icon(status_t)
                            err_t = trace.get("error_message", "")
                            comp_at = trace.get("completed_at", "")[:19] if trace.get("completed_at") else "—"
                            tok_in = trace.get("token_input", 0) or 0
                            tok_out = trace.get("token_output", 0) or 0

                            lat_str = f"{lat_ms / 1000:.1f}s" if lat_ms >= 1000 else f"{lat_ms}ms"
                            token_str = f"[{tok_in}+{tok_out}]" if (tok_in or tok_out) else ""

                            if err_t:
                                st.markdown(f"{ticon} **`{nk}`** {token_str} ({lat_str}) — {comp_at}  ❌ {err_t[:80]}")
                            else:
                                st.markdown(f"{ticon} **`{nk}`** {token_str} — {_label(nk)} ({lat_str})  完成于 {comp_at}")

                    running_nodes = [n for n in wf_nodes if n.get("status") == "running"]
                    if running_nodes:
                        st.markdown("**正在执行:**")
                        for n in running_nodes:
                            nk = n.get("node_name", "")
                            st.markdown(f"🔄 `{nk}` — {_label(nk)} — {_agent(nk)}")

                with col_counts:
                    st.markdown("##### 📊 实时产物")

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

        col_del, col_ws, col_hr = st.columns([1, 1, 1])
        view_del_clicked = st.button("查看交付物", key=f"af_running_vd_{effective_run_id or 'none'}", type="primary", use_container_width=True)
        with col_ws:
            if st.button("项目工作台", key=f"af_running_ws_{effective_run_id or 'none'}", use_container_width=True):
                goto_page("Project Workspace")
        with col_hr:
            if st.button("审查中心", key=f"af_running_hr_{effective_run_id or 'none'}", use_container_width=True):
                goto_page("Review Center")

        if view_del_clicked:
            st.session_state["af_stage"] = "deliverables"
            st.rerun()

        if run_status in ("pending", "running"):
            col_ref, col_auto = st.columns([1, 1])
            with col_ref:
                st.button("刷新", key=f"af_refresh_{effective_run_id or 'none'}", use_container_width=True)
            with col_auto:
                auto_refresh = st.checkbox("自动刷新 (每 2 秒)", value=True, key=f"af_ar_{effective_run_id or 'none'}")
            if auto_refresh:
                time.sleep(2)
                st.rerun()

    # -------------------------------------------------------------------------
    # Stage 4: Deliverables (simplified)
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
                reset_analysis_flow_state()
                st.rerun()

        # Fetch data
        report_data = get_json(f"/api/runs/{effective_run_id}/report", {}) or {} if effective_run_id else {}
        report_draft = get_json(f"/api/runs/{effective_run_id}/report-draft", {}) or {} if effective_run_id else {}
        evidence_data = get_json(f"/api/runs/{effective_run_id}/evidence", []) or [] if effective_run_id else []
        sources_data = get_json(f"/api/runs/{effective_run_id}/sources", []) or [] if effective_run_id else []
        claims_data = get_json(f"/api/runs/{effective_run_id}/review-items", {}) or {} if effective_run_id else {}
        metrics_data = get_json(f"/api/runs/{effective_run_id}/metrics", {}) or {} if effective_run_id else {}

        # ── Report Outline / Section Status ──────────────────────────────────
        # vNext-P0-Real-Frontend-Integration: Fetch from /report-draft endpoint
        outline_from_draft = report_draft.get("report_outline", {}) or {}
        sections_from_draft = report_draft.get("sections", []) or []
        section_statuses_from_draft = report_draft.get("section_statuses", []) or []

        # Build outline sections list from report_outline
        outline_sections = outline_from_draft.get("sections", []) if isinstance(outline_from_draft, dict) else []
        outline_titles_map = {}
        if outline_sections:
            for s in outline_sections:
                t = (s.get("title") or "").lower().strip()
                if t:
                    outline_titles_map[t] = s

        # Determine which sections are drafted based on word count
        drafted_data = []
        section_ids_seen = set()
        for section in sections_from_draft:
            title = section.get("section_title") or section.get("title") or "Unknown"
            section_id = section.get("section_id", "")
            section_ids_seen.add(section_id)
            content = section.get("content_markdown") or section.get("content") or ""
            word_count = len(content.split()) if content else 0
            cited_claims = section.get("cited_claims") or section.get("claim_ids") or []
            cited_count = len(cited_claims) if isinstance(cited_claims, list) else 0

            if word_count > 0:
                status = "drafted"
                badge = "🟢 drafted"
            elif title.lower() in ["executive summary", "key findings"]:
                status = "blocked"
                badge = "🔴 blocked"
            else:
                status = "missing"
                badge = "🟡 missing"

            drafted_data.append({
                "Section": title,
                "Section ID": section_id,
                "Status": badge,
                "Words": word_count,
                "Cited Claims": cited_count,
            })

        # Fill in outline sections not yet drafted
        for s in outline_sections:
            sid = s.get("section_id", "")
            title = s.get("title", "Unknown")
            if sid not in section_ids_seen:
                drafted_data.append({
                    "Section": title,
                    "Section ID": sid,
                    "Status": "🟡 missing",
                    "Words": 0,
                    "Cited Claims": 0,
                })

        # Show Report Outline section
        has_report_outline = bool(outline_sections or drafted_data)
        report_outline_expander_label = (
            f"📋 Report Outline / Section Status ({len(drafted_data)} sections)"
            if drafted_data else "📋 Report Outline / Section Status"
        )
        with st.expander(report_outline_expander_label, expanded=True):
            if has_report_outline:
                if outline_sections:
                    st.markdown("**Outline Definition**")
                    outline_data = []
                    for s in outline_sections:
                        outline_data.append({
                            "Section ID": s.get("section_id", "—"),
                            "Title": s.get("title", "—"),
                            "Min Words": s.get("min_words", 0),
                            "Human Review": "Yes" if s.get("requires_human_review") else "No",
                        })
                    if outline_data:
                        st.dataframe(pd.DataFrame(outline_data), use_container_width=True, hide_index=True)

                if drafted_data:
                    st.markdown("**Section Status**")
                    st.dataframe(pd.DataFrame(drafted_data), use_container_width=True, hide_index=True)
            else:
                # vNext-P0: Clear warning when report_outline is missing
                st.warning(
                    "⚠️ This run has no report_outline in report_draft. "
                    "Check ResearchPlan → Project → Run propagation. "
                    "Ensure the project was created with a research_plan that includes report_outline."
                )

        qs = report_data.get("quality_summary", {}) if isinstance(report_data, dict) else {}
        claims = claims_data.get("claims", []) if isinstance(claims_data, dict) else []
        signed_claims = [c for c in claims if isinstance(c, dict) and c.get("review_status", "").lower() == "signed"]

        # ── Quality Summary ────────────────────────────────────────────────────
        with st.expander("Quality Summary", expanded=True):
            if qs:
                st.json(qs)
            else:
                st.info("Quality metrics not yet available.")

        # ── Evidence Appendix ──────────────────────────────────────────────────
        with st.expander("Evidence Appendix", expanded=False):
            if evidence_data:
                st.markdown(f"**{len(evidence_data)} evidence items** collected from {len(sources_data)} sources")
            else:
                st.info("No evidence collected yet.")

        # ── Evidence-backed Claims ─────────────────────────────────────────────
        with st.expander("Evidence-backed Claims", expanded=False):
            if signed_claims:
                st.markdown(f"**{len(signed_claims)} signed claims**")
                for c in signed_claims[:10]:
                    st.markdown(f"- {c.get('claim_text', 'N/A')}")
            else:
                st.info("No signed claims yet.")

        st.divider()

        col_rep, col_ev, col_ws = st.columns(3)
        with col_rep:
            # vNext-P0-Real-Frontend-Integration: "分析报告" page doesn't exist;
            # redirect to Project Workspace Deliverables tab instead
            if st.button("Full Report", key=f"af_deliv_report_{effective_run_id or 'none'}", use_container_width=True):
                st.session_state["pw_active_tab"] = "Deliverables"
                goto_page("Project Workspace")
        with col_ev:
            if st.button("Evidence Hub", key=f"af_deliv_evidence_{effective_run_id or 'none'}", use_container_width=True):
                st.session_state["evidence_run_id"] = effective_run_id
                goto_page("Evidence Hub")
        with col_ws:
            if st.button("Project Workspace", key=f"af_deliv_workspace_{effective_run_id or 'none'}", use_container_width=True):
                goto_page("Project Workspace")
