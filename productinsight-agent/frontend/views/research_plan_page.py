"""Research Plan page for ProductInsight Agent.

Extracted from app.py (lines 5284-5728).
"""

import json
import streamlit as st
import pandas as pd
import requests

from frontend.common.api import get_json, post_json
from frontend.common.config import API_BASE
from frontend.common.actions import start_run_async_and_go_to_running
from frontend.components.dag_preview import render_research_plan_dag_preview


def _start_analysis_from_plan(plan, plan_id, dag_id):
    """Create a project from the confirmed plan and start analysis run."""
    plan_data = plan

    task_brief = plan_data.get("task_brief") or {}
    source_discovery = plan_data.get("source_discovery") or {}

    # Build project name
    proj_name = task_brief.get("project_name", "")
    if not proj_name:
        proj_name = plan_data.get("report_outline", {}).get("report_title", f"Research Plan {plan_id[:8]}")

    final_products = []
    competitors = plan_data.get("competitors") or []
    for comp in competitors:
        if isinstance(comp, dict):
            final_products.append({
                "product_name": comp.get("name", ""),
                "company_name": comp.get("company_name", ""),
                "official_website": comp.get("official_url", ""),
                "seed_urls": comp.get("seed_urls") or [],
            })

    plan_dims = plan_data.get("analysis_dimensions") or []
    final_dims = [d.get("dimension_id") for d in plan_dims if isinstance(d, dict) and d.get("dimension_id")]
    plan_report_outline = plan_data.get("report_outline", {})

    try:
        resp = requests.post(
            f"{API_BASE}/api/projects",
            json={
                "project_name": proj_name,
                "task_type": task_brief.get("task_type", "competitor_landscape"),
                "target_region": task_brief.get("target_region", "global"),
                "description": task_brief.get("business_goal", ""),
                "products": final_products,
                "analysis_dimensions": final_dims,
                "research_plan_id": plan_id,
                "execution_dag_id": dag_id,
                "research_plan": plan_data,
                "report_outline": plan_report_outline,
                "source_discovery": source_discovery,
            },
            timeout=15,
        )
        resp.raise_for_status()
        project_result = resp.json()
        new_proj_id = project_result.get("project_id")

        ok = start_run_async_and_go_to_running(new_proj_id)
        if not ok:
            st.error(st.session_state.get("last_start_error", "启动失败"))
    except requests.exceptions.RequestException as e:
        st.error(f"创建项目失败: {e}")


def load_plan(plan_id: str) -> bool:
    """Load a research plan from the API."""
    try:
        resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}", timeout=30)
        if resp.status_code >= 400:
            return False
        data = resp.json()
        st.session_state["rp_plan_id"] = plan_id
        st.session_state["rp_plan_data"] = data.get("research_plan")
        return True
    except Exception:
        return False


def load_dag(plan_id: str) -> bool:
    """Load DAG data from the API."""
    try:
        resp = requests.get(f"{API_BASE}/api/research-plans/{plan_id}/dag", timeout=30)
        if resp.status_code >= 400:
            return False
        st.session_state["rp_dag_data"] = resp.json()
        return True
    except Exception:
        return False


def render_status_badge(status: str):
    """Render a status badge with color."""
    colors = {"draft": "gray", "confirmed": "green", "in_progress": "blue", "completed": "green", "cancelled": "red"}
    color = colors.get(status, "gray")
    st.markdown(f":{color}[**{status.upper()}**]")


def render_research_plan_page(run_id: str = None):
    """Render the Research Plan page."""
    import requests

    # Language: default Chinese, override from loaded plan
    is_chinese = True
    if st.session_state.get("rp_plan_data"):
        lang_meta = st.session_state["rp_plan_data"].get("language_metadata", {})
        is_chinese = lang_meta.get("output_language", "中文") == "中文"

    # ── Session state init ────────────────────────────────────────────────
    for key, default in [
        ("rp_plan_id", None),
        ("rp_plan_data", None),
        ("rp_dag_data", None),
        ("rp_edit_mode", False),
        # competitors
        ("rp_comps_edited", False),
        # dimensions
        ("rp_dims_edited", False),
        # outline
        ("rp_outline_edited", False),
        ("rp_outline_sections", []),
        ("rp_outline_title", ""),
        ("rp_outline_generating", False),
        # New: analysis preview state
        ("rp_analysis_data", None),       # holds the /analyze result
        ("rp_analysis_done", False),       # True after user clicks "confirm preview"
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # ── Header ──────────────────────────────────────────────────────────
    st.header("📋 调研方案" if is_chinese else "📋 Research Plan")
    st.caption("vNext-R1: 查看、编辑和确认调研方案")

    # ── Existing Plans Selector ──────────────────────────────────────────
    plan_data = st.session_state.get("rp_plan_data")
    plan_id  = st.session_state.get("rp_plan_id")

    try:
        resp = requests.get(f"{API_BASE}/api/research-plans?limit=50", timeout=15)
        all_plans = []
        if resp.status_code == 200:
            raw = resp.json()
            all_plans = raw if isinstance(raw, list) else raw.get("research_plans", [])
    except Exception:
        all_plans = []

    # Build display options (fetch title for each plan)
    plan_options = []
    plan_id_to_summary = {}
    if all_plans:
        status_labels = {"draft": "📝 草稿", "confirmed": "✅ 已确认", "in_progress": "🔄 进行中", "completed": "🏁 完成"}
        for p in all_plans:
            pid = p.get("research_plan_id") or p.get("plan_id")
            if not pid:
                continue
            status = p.get("status", "draft")
            status_lbl = status_labels.get(status, status)
            # Try to get title from cached session data
            title = ""
            for cached_pid, cached_data in [
                (st.session_state.get("rp_plan_id"), st.session_state.get("rp_plan_data")),
            ]:
                pass
            # Label: [status] plan_id — title or date
            label = f"{status_lbl}  {pid}"
            plan_options.append((label, pid))
            plan_id_to_summary[pid] = {"status": status, "title": title}

    with st.expander("📂 选择已有方案" if is_chinese else "📂 Load Existing Plan", expanded=(not plan_data)):
        col_sel, col_load = st.columns([3, 1])
        with col_sel:
            sel_label = st.selectbox(
                "已有方案" if is_chinese else "Existing Plans",
                options=[""] + [pid for _, pid in plan_options],
                format_func=lambda pid: next((lbl for lbl, p in plan_options if p == pid), pid) if pid else "— 选择一个方案 —",
                key="rp_sel_pid",
            )
        with col_load:
            st.markdown("")
            st.markdown("")
            if st.button("加载" if is_chinese else "Load", use_container_width=True) and sel_label:
                if load_plan(sel_label):
                    loaded_plan = st.session_state["rp_plan_data"]
                    st.session_state["rp_comps_edited"] = False
                    st.session_state["rp_dims_edited"] = False
                    st.session_state["rp_outline_edited"] = False
                    st.session_state["rp_analysis_data"] = None
                    st.session_state["rp_analysis_done"] = False
                    st.session_state["rp_analysis_loading"] = False
                    # Sync competitors: convert old schema to new in-memory format
                    _comps = []
                    for c in (loaded_plan.get("competitors") or []):
                        new_c = dict(c)
                        if "url" not in new_c and "seed_urls" in new_c:
                            new_c["url"] = ""
                        if "notes" not in new_c:
                            new_c["notes"] = ""
                        if "company_name" not in new_c:
                            new_c["company_name"] = c.get("company") or ""
                        _comps.append(new_c)
                    st.session_state["rp_comps_list"] = _comps
                    st.session_state["rp_outline_sections"] = (loaded_plan.get("report_outline") or {}).get("sections") or []
                    st.session_state["rp_outline_title"] = (loaded_plan.get("report_outline") or {}).get("report_title") or ""
                    st.rerun()
                else:
                    st.error("加载失败" if is_chinese else "Load failed")
        st.caption(f"共 {len(all_plans)} 个方案" if all_plans else "暂无已有方案" if is_chinese else f"{len(all_plans)} plans" if all_plans else "No existing plans")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # NO PLAN LOADED — New Plan Flow (2 steps: Analyze → Confirm & Generate)
    # ════════════════════════════════════════════════════════════════════════
    if not plan_data:
        with st.expander("🆕 生成新调研方案" if is_chinese else "🆕 Generate New Research Plan", expanded=True):
            user_query = st.text_area(
                "研究需求" if is_chinese else "Research Query",
                placeholder=(
                    "例如：分析 Dify, Coze, Flowise 和 LangGraph 的企业 AI Agent 平台对比，"
                    "关注功能、定价、部署和企业就绪度。"
                    if is_chinese else
                    "e.g. Compare Dify, Coze, Flowise and LangGraph for enterprise AI Agent platforms, "
                    "focusing on features, pricing, deployment, and enterprise readiness."
                ),
                height=100,
                key="rp_new_query",
            )
            col_schema, col_region, col_mode = st.columns(3)
            schema_opts   = ["ai_agent_platform", "competitor_landscape", "product_comparison", "pricing_analysis", "sales_battlecard"]
            region_opts   = ["global", "china", "us", "europe", "southeast_asia"]
            mode_opts     = ["review", "auto", "expert"]
            with col_schema:
                schema_type = st.selectbox(
                    "Schema 类型" if is_chinese else "Schema Type",
                    options=schema_opts, index=0,
                )
            with col_region:
                target_region = st.selectbox(
                    "目标区域" if is_chinese else "Target Region",
                    options=region_opts, index=0,
                )
            with col_mode:
                mode = st.selectbox(
                    "模式" if is_chinese else "Mode",
                    options=mode_opts, index=0,
                )

            # ── Step 1: Analyze ───────────────────────────────────────────────
            if not st.session_state.get("rp_analysis_done"):
                if st.button(
                    "🔍 " + ("智能分析需求" if is_chinese else "Analyze Query (AI)"),
                    type="primary", use_container_width=True,
                ):
                    if not user_query.strip():
                        st.error("请输入研究需求。" if is_chinese else "Enter a research query.")
                    else:
                        st.session_state["rp_analysis_loading"] = True
                        st.rerun()

                if st.session_state.get("rp_analysis_loading"):
                    with st.spinner(
                        "🔍 " + ("正在智能分析，请稍候..." if is_chinese else "Analyzing query with AI...")
                    ):
                        try:
                            resp = requests.post(
                                f"{API_BASE}/api/research-plans/analyze",
                                json={"user_query": user_query, "target_region": target_region},
                                timeout=120,
                            )
                            if resp.status_code >= 400:
                                st.error(f"分析失败: {resp.text}")
                                st.session_state["rp_analysis_loading"] = False
                            else:
                                result = resp.json()
                                analysis = result.get("analysis", {})
                                st.session_state["rp_analysis_data"] = analysis
                                st.session_state["rp_analysis_loading"] = False
                                st.session_state["rp_analysis_query"] = user_query
                                st.session_state["rp_analysis_schema"] = schema_type
                                st.session_state["rp_analysis_region"] = target_region
                                st.session_state["rp_analysis_mode"] = mode
                                # Pre-fill competitors from analysis
                                analysis_comps = analysis.get("competitors", [])
                                comp_list = []
                                for c in analysis_comps:
                                    comp_list.append({
                                        "name": c.get("name", ""),
                                        "company_name": c.get("company_name", ""),
                                        "official_url": c.get("official_url", ""),
                                        "url": c.get("official_url", ""),
                                        "notes": c.get("note", ""),
                                        "priority": c.get("priority", "medium"),
                                    })
                                st.session_state["rp_comps_list"] = comp_list
                                st.session_state["rp_comps_edited"] = True  # mark as edited so user can adjust
                                # Pre-fill schema from analysis
                                if analysis.get("schema_type"):
                                    st.session_state["rp_analysis_schema"] = analysis["schema_type"]
                                st.rerun()
                        except Exception as exc:
                            st.error(f"分析失败: {exc}")
                            st.session_state["rp_analysis_loading"] = False

                # ── Step 2: Confirm & Generate ──────────────────────────────────
                elif st.session_state.get("rp_analysis_data") is not None:
                    analysis = st.session_state["rp_analysis_data"]
                    st.markdown("---")
                    st.markdown("#### 📊 " + ("分析预览" if is_chinese else "Analysis Preview"))
                    detected_lang = st.session_state.get("rp_analysis_data", {}).get("output_language", "zh")
                    confidence = analysis.get("confidence_score", 0.0)
                    if confidence > 0:
                        conf_pct = int(confidence * 100)
                        color = "green" if conf_pct >= 70 else "orange" if conf_pct >= 40 else "red"
                        st.markdown(
                            f"AI 置信度: :{color}[{conf_pct}%]"
                            if is_chinese else
                            f"AI Confidence: :{color}[{conf_pct}%]"
                        )

                    # Show inferred schema
                    inferred_schema = analysis.get("schema_type", schema_type)
                    schema_label_map = {
                        "ai_agent_platform": "AI Agent 平台",
                        "competitor_landscape": "竞品全景",
                        "product_comparison": "产品对比",
                        "pricing_analysis": "定价分析",
                        "sales_battlecard": "销售战卡",
                        "knowledge_management": "知识管理",
                    }
                    st.markdown(
                        f"**推断 Schema 类型:** {schema_label_map.get(inferred_schema, inferred_schema)}"
                        if is_chinese else
                        f"**Inferred Schema Type:** {inferred_schema}"
                    )

                    # Show competitors
                    comps = analysis.get("competitors", [])
                    st.markdown(
                        f"**识别竞品 ({len(comps)} 个):**"
                        if is_chinese else
                        f"**Identified Competitors ({len(comps)}):**"
                    )
                    if comps:
                        for i, c in enumerate(comps):
                            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(c.get("priority", "medium"), "⚪")
                            conf = c.get("confidence", 0)
                            note = c.get("note", "")
                            url = c.get("official_url", "")
                            display = f"{priority_emoji} **{c.get('name', '')}**"
                            if c.get("company_name"):
                                display += f" ({c['company_name']})"
                            if url:
                                display += f" — {url}"
                            if note:
                                display += f"\n   └ {note}"
                            if conf > 0:
                                display += f" (conf: {int(conf*100)}%)"
                            st.markdown(f"{i+1}. {display}")
                    else:
                        st.info("未识别到竞品，请手动添加。" if is_chinese else "No competitors identified. Please add manually.")

                    # Warnings
                    warnings = analysis.get("warnings", [])
                    for w in warnings:
                        st.warning(w)

                    st.markdown("---")
                    st.info(
                        "请确认上述分析结果无误后，点击「确认并生成方案」。"
                        if is_chinese else
                        "Review the analysis above, then click 'Confirm & Generate Plan'."
                    )
                    col_confirm, col_reanalyze = st.columns(2)
                    with col_confirm:
                        if st.button(
                            "✅ " + ("确认并生成方案" if is_chinese else "Confirm & Generate Plan"),
                            type="primary", use_container_width=True,
                        ):
                            with st.spinner(
                                "正在生成方案..." if is_chinese else "Generating plan..."
                            ):
                                try:
                                    resp = requests.post(
                                        f"{API_BASE}/api/research-plans/generate",
                                        json={
                                            "user_query": st.session_state.get("rp_analysis_query", user_query),
                                            "schema_type": st.session_state.get("rp_analysis_schema", schema_type),
                                            "target_region": st.session_state.get("rp_analysis_region", target_region),
                                            "mode": st.session_state.get("rp_analysis_mode", mode),
                                            "analyzed_data": {
                                                "competitors": st.session_state.get("rp_comps_list", []),
                                                "schema_type": st.session_state.get("rp_analysis_schema", schema_type),
                                            },
                                        },
                                        timeout=60,
                                    )
                                    if resp.status_code >= 400:
                                        st.error(f"生成失败: {resp.text}")
                                    else:
                                        result = resp.json()
                                        st.session_state["rp_plan_id"] = result.get("research_plan_id")
                                        st.session_state["rp_plan_data"] = result.get("research_plan")
                                        st.session_state["rp_comps_edited"] = False
                                        st.session_state["rp_dims_edited"] = False
                                        st.session_state["rp_outline_edited"] = False
                                        # Clear analysis state
                                        st.session_state["rp_analysis_data"] = None
                                        st.session_state["rp_analysis_done"] = True
                                        st.session_state["rp_outline_sections"] = []
                                        st.session_state["rp_outline_title"] = ""
                                        st.success(
                                            f"方案已生成！来源: {result.get('generated_by', 'unknown')}"
                                        )
                                        st.rerun()
                                except Exception as exc:
                                    st.error(f"生成失败: {exc}")
                    with col_reanalyze:
                        if st.button(
                            "🔄 " + ("重新分析" if is_chinese else "Re-analyze"),
                            use_container_width=True,
                        ):
                            st.session_state["rp_analysis_data"] = None
                            st.session_state["rp_analysis_loading"] = False
                            st.rerun()
        return  # stop here if no plan

    # ════════════════════════════════════════════════════════════════════════
    # PLAN LOADED — Show tabs
    # ════════════════════════════════════════════════════════════════════════
    plan     = plan_data
    status   = plan.get("status", "draft")
    gen_by   = plan.get("generated_by", "unknown")

    col_h1, col_h2, col_h3 = st.columns([2, 1, 1])
    with col_h1:
        st.subheader(f"调研方案: `{plan_id}`")
    with col_h2:
        render_status_badge(status)
    with col_h3:
        st.caption(f"生成方式: **{gen_by}**")

    st.divider()

    # Edit JSON toggle
    edit_mode = st.toggle("编辑 JSON" if is_chinese else "Edit JSON",
                          value=st.session_state.get("rp_edit_mode", False))
    st.session_state["rp_edit_mode"] = edit_mode

    if edit_mode:
        st.markdown("### " + ("编辑方案 JSON" if is_chinese else "Edit Plan JSON"))
        edited_json = st.text_area(
            "Plan JSON" if is_chinese else "Plan JSON",
            value=json.dumps(plan, indent=2, ensure_ascii=False),
            height=400, key="rp_json_editor",
        )
        col_save, col_reset = st.columns(2)
        with col_save:
            if st.button("保存修改" if is_chinese else "Save",
                         type="primary", use_container_width=True):
                try:
                    new_plan = json.loads(edited_json)
                    resp = requests.put(
                        f"{API_BASE}/api/research-plans/{plan_id}",
                        json={"payload_json": edited_json}, timeout=30,
                    )
                    if resp.status_code >= 400:
                        st.error(f"保存失败: {resp.text}")
                    else:
                        st.session_state["rp_plan_data"] = resp.json().get("research_plan")
                        st.success("已保存！" if is_chinese else "Saved!")
                        st.rerun()
                except json.JSONDecodeError as exc:
                    st.error(f"JSON 格式错误: {exc}")
        with col_reset:
            if st.button("重置" if is_chinese else "Reset", use_container_width=True):
                st.rerun()
        st.divider()

    # ── Tabs ─────────────────────────────────────────────────────────────
    tab_labels = [
        "任务简报" if is_chinese else "Task Brief",
        "竞品" if is_chinese else "Competitors",
        "分析维度" if is_chinese else "Dimensions",
        "来源规划" if is_chinese else "Sources",
        "人工审核点" if is_chinese else "Review Points",
        "成功指标" if is_chinese else "Metrics",
        "DAG 预览" if is_chinese else "DAG",
    ]
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(tab_labels)

    # ── Tab 1: Task Brief ────────────────────────────────────────────────
    with tab1:
        task_brief = plan.get("task_brief") or {}
        if task_brief:
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{'项目名称' if is_chinese else 'Project Name'}:** {task_brief.get('project_name', 'N/A')}")
                st.markdown(f"**{'任务类型' if is_chinese else 'Task Type'}:** {task_brief.get('task_type', 'N/A')}")
                st.markdown(f"**{'目标区域' if is_chinese else 'Target Region'}:** {task_brief.get('target_region', 'N/A')}")
            with col_b:
                st.markdown(f"**{'目标受众' if is_chinese else 'Target Audience'}:** {task_brief.get('target_audience', 'N/A')}")
                st.markdown(f"**{'商业目标' if is_chinese else 'Business Goal'}:** {task_brief.get('business_goal', 'N/A')}")
            st.markdown(f"**{'用户需求' if is_chinese else 'User Query'}:**")
            st.info(task_brief.get("user_query", "N/A"))
        else:
            st.info("无任务简报。" if is_chinese else "No task brief.")

    # ── Tab 2: Competitors ───────────────────────────────────────────────
    with tab2:
        _render_competitors_tab(plan, plan_id, is_chinese)

    # ── Tab 3: Dimensions ────────────────────────────────────────────────
    with tab3:
        _render_dimensions_tab(plan, plan_id, is_chinese)

    # ── Tab 4: Source Plan ───────────────────────────────────────────────
    with tab4:
        source_plan = plan.get("source_plan") or {}
        if source_plan:
            st.markdown(f"**{'采集策略' if is_chinese else 'Collection Strategy'}:** {source_plan.get('collection_strategy', 'N/A')}")
            st.markdown(f"**{'最低竞品来源数' if is_chinese else 'Min Sources per Competitor'}:** {source_plan.get('minimum_sources_per_competitor', 'N/A')}")
            st.markdown(f"**{'最低维度证据数' if is_chinese else 'Min Evidence per Dimension'}:** {source_plan.get('minimum_evidence_per_dimension', 'N/A')}")
            with st.expander(("完整来源规划" if is_chinese else "Full Source Plan")):
                st.json(source_plan)
        else:
            st.info("无来源规划。" if is_chinese else "No source plan.")

    # ── Tab 5: Human Checkpoints ────────────────────────────────────────
    with tab5:
        checkpoints = plan.get("human_checkpoints") or []
        if checkpoints:
            for cp in checkpoints:
                if isinstance(cp, dict):
                    with st.expander(f"{'审核点' if is_chinese else 'Checkpoint'}: {cp.get('title', 'N/A')}"):
                        st.markdown(f"**{'阶段' if is_chinese else 'Stage'}:** {cp.get('stage', 'N/A')}")
                        st.markdown(f"**{'必需' if is_chinese else 'Required'}:** {'是' if cp.get('required') else '否'}")
                        st.markdown(f"**{'描述' if is_chinese else 'Description'}:** {cp.get('description', 'N/A')}")
        else:
            st.info("无人工审核点。" if is_chinese else "No checkpoints.")

    # ── Tab 6: Success Metrics ───────────────────────────────────────────
    with tab6:
        metrics = plan.get("success_metrics") or {}
        if metrics:
            col_m = st.columns(4)
            for i, (key, label) in enumerate([
                ("minimum_signed_claims",   "最低签约声明数" if is_chinese else "Min Signed Claims"),
                ("minimum_sources_per_competitor", "每竞品最低来源数" if is_chinese else "Min Sources/Comp"),
                ("minimum_evidence_items",  "最低证据数" if is_chinese else "Min Evidence"),
                ("minimum_report_words",    "最低报告字数" if is_chinese else "Min Report Words"),
            ]):
                with col_m[i]:
                    st.metric(label, metrics.get(key, "N/A"))
            with st.expander(("完整指标" if is_chinese else "Full Metrics")):
                st.json(metrics)
        else:
            st.info("无成功指标。" if is_chinese else "No metrics.")

    # ── Tab 7: DAG Preview ───────────────────────────────────────────────
    with tab7:
        if status == "confirmed":
            render_research_plan_dag_preview(plan_id, plan)
        else:
            st.info("方案确认后将生成 DAG。" if is_chinese else "DAG will be generated after plan confirmation.")

    st.divider()

    # ════════════════════════════════════════════════════════════════════════════
    # STANDALONE OUTLINE BLOCK — outside tabs, between tabs and bottom actions
    # ════════════════════════════════════════════════════════════════════════════
    outline_from_plan = plan.get("report_outline") or {}

    # Always ensure session state reflects the current plan's outline on every render.
    # This prevents stale session state from showing wrong data after plan load.
    st.session_state["rp_outline_sections"] = outline_from_plan.get("sections") or []
    st.session_state["rp_outline_title"] = outline_from_plan.get("report_title") or (
        ("竞品分析报告" if is_chinese else "Competitive Analysis Report"))
    # Reset edit flag so any subsequent edits are tracked correctly
    st.session_state["rp_outline_edited"] = False

    sections = st.session_state["rp_outline_sections"]
    title    = st.session_state["rp_outline_title"]

    # ── Section header + title ──────────────────────────────────────
    st.markdown("### 📋 " + ("报告大纲" if is_chinese else "Report Outline"))
    title_col, btn_col = st.columns([4, 1])
    with title_col:
        new_title = st.text_input(
            ("报告标题" if is_chinese else "Report Title"),
            value=title,
            label_visibility="collapsed",
            placeholder=("报告标题，例如：Codex / Trae / Cursor 竞品分析报告" if is_chinese
                        else "Report title, e.g. Codex / Trae / Cursor Competitive Analysis"),
            key="rp_outline_title_input",
        )
        if new_title != title:
            st.session_state["rp_outline_title"] = new_title
    with btn_col:
        st.markdown("")
        lang_code = "zh" if is_chinese else "en"
        if st.button(
                "✨ " + ("AI 生成大纲" if is_chinese else "Generate Outline"),
                type="primary", use_container_width=True,
                disabled=st.session_state.get("rp_outline_generating", False),
        ):
            st.session_state["rp_outline_generating"] = True
            st.rerun()

    # ── LLM generation spinner ───────────────────────────────────────
    if st.session_state.get("rp_outline_generating"):
        with st.spinner(("正在调用 LLM 生成大纲，请稍候..." if is_chinese else
                         "Generating outline with LLM, please wait...")):
            try:
                resp = requests.post(
                    f"{API_BASE}/api/research-plans/{plan_id}/generate-outline",
                    json={
                        "competitors": st.session_state.get("rp_comps_list", plan.get("competitors", [])),
                        "dimensions":  st.session_state.get("rp_dims_list",  plan.get("analysis_dimensions", [])),
                        "language":    lang_code,
                    },
                    timeout=180,
                )
                if resp.status_code >= 400:
                    st.error(f"生成失败: {resp.text}")
                else:
                    result      = resp.json()
                    new_outline = result.get("outline", {})
                    st.session_state["rp_outline_sections"] = new_outline.get("sections", [])
                    st.session_state["rp_outline_title"]    = new_outline.get(
                        "report_title", st.session_state["rp_outline_title"])
                    try:
                        updated_plan = dict(st.session_state.get("rp_plan_data", {}))
                        updated_plan["report_outline"] = new_outline
                        requests.put(
                            f"{API_BASE}/api/research-plans/{plan_id}",
                            json={"payload_json": json.dumps(updated_plan)},
                            timeout=30,
                        )
                        st.session_state["rp_plan_data"] = updated_plan
                        st.success(("大纲已生成并自动保存！" if is_chinese else "Outline generated and auto-saved!"))
                    except Exception as save_exc:
                        st.warning(
                            ("大纲已生成但自动保存失败: " if is_chinese else "Outline generated but auto-save failed: ") + str(save_exc)
                        )
            except Exception as exc:
                st.error(f"生成失败: {exc}")
            finally:
                st.session_state["rp_outline_generating"] = False
                st.rerun()

    # ── Compact outline table ───────────────────────────────────────
    st.markdown("")  # small gap
    non_trivial = [s for s in sections if s.get("type") not in ("cover", "appendix")]
    product_count = len(plan.get("competitors", []))
    min_sections = product_count * 3 if product_count > 0 else 6
    if not sections:
        st.info(
            ("暂无大纲。点击上方「AI 生成大纲」按钮创建。" if is_chinese else
             "No outline yet. Click 'Generate Outline' above to create one."))
    else:
        # ── Compact table: one row per section ────────────────────
        rows = []
        for i, s in enumerate(sections, 1):
            rows.append({
                "#": i,
                ("章节" if is_chinese else "Title"): s.get("title", ""),
                ("类型" if is_chinese else "Type"): (
                    "章节" if s.get("type") == "chapter" or not s.get("type") else "小节"
                ),
                ("最低字数" if is_chinese else "Min Words"): s.get("min_words", 0),
                ("审核" if is_chinese else "Review"): "🔴" if s.get("requires_human_review") else "",
            })
        st.table(pd.DataFrame(rows))
        st.caption(
            f"{len(sections)} 个章节 | "
            + (f"{len(non_trivial)} 个正文章节" if is_chinese else f"{len(non_trivial)} substantive sections")
            + (f" | {product_count} 个竞品" if product_count and is_chinese else "")
            + (f" | {product_count} competitors" if product_count and not is_chinese else "")
        )

        # ── Inline quick edit ───────────────────────────────────────
        with st.expander(("✏️  编辑大纲章节" if is_chinese else "Edit Sections"), expanded=False):
            ch_to_del = []
            for i, s in enumerate(list(sections)):
                sid = s.get("section_id") or f"sec_{i}"
                col_t, col_w, col_d = st.columns([4, 1, 1])
                with col_t:
                    new_t = st.text_input(
                        ("标题" if is_chinese else "Title"),
                        value=s.get("title", ""), key=f"oe_t_{sid}",
                    )
                    if new_t != s.get("title"):
                        s["title"] = new_t
                with col_w:
                    new_w = st.number_input(
                        ("字数" if is_chinese else "Words"),
                        min_value=0, max_value=50000,
                        value=int(s.get("min_words", 800) or 800),
                        step=100, key=f"oe_w_{sid}",
                    )
                    if new_w != s.get("min_words"):
                        s["min_words"] = new_w
                with col_d:
                    st.markdown("")
                    if st.button("🗑️", key=f"oe_d_{sid}", use_container_width=True):
                        ch_to_del.append(sid)
            if ch_to_del:
                sections[:] = [s for s in sections if s.get("section_id", "").replace("sec_", "") not in ch_to_del]
                st.session_state["rp_outline_sections"] = sections
                st.rerun()

            col_add, _ = st.columns([1, 4])
            with col_add:
                if st.button("➕ " + ("添加章节" if is_chinese else "Add Section"), use_container_width=True):
                    sections.append({
                        "section_id": f"sec_{len(sections)+1:02d}",
                        "title": ("新章节" if is_chinese else "New Section"),
                        "type": "chapter",
                        "min_words": 800,
                        "requires_human_review": False,
                        "purpose": "",
                        "slug": ("xin-zhangjie" if is_chinese else "new-section"),
                    })
                    st.session_state["rp_outline_sections"] = sections
                    st.rerun()

            if st.button("💾 " + ("保存大纲" if is_chinese else "Save Outline"), type="primary", use_container_width=True):
                _save_outline(plan_id, plan, is_chinese)

    # ── Start Analysis button ────────────────────────────────────────
    st.divider()
    st.markdown("### " + ("🚀 开始分析" if is_chinese else "🚀 Start Analysis"))
    # dag_id is only available after plan is confirmed; the confirmed block below
    # renders its own button. Nothing to do here for the draft state.
    if status == "draft":
        st.info(
            ("请先「确认方案」生成执行计划后，再开始分析。" if is_chinese else
             "Please confirm the plan first, then start analysis.")
        )

    # ── Bottom actions ───────────────────────────────────────────────────
    if status == "draft":
        st.markdown("### " + ("确认方案" if is_chinese else "Confirm Plan"))
        st.info("确认后方案将无法修改，并将创建执行 DAG。" if is_chinese
                   else "After confirmation the plan cannot be modified and a DAG will be created.")
        col_confirm, col_reload = st.columns(2)
        with col_confirm:
            if st.button(("确认方案并创建 DAG" if is_chinese else "Confirm & Create DAG"),
                         type="primary", use_container_width=True):
                with st.spinner("确认方案并创建 DAG..." if is_chinese else "Confirming..."):
                    try:
                        resp = requests.post(f"{API_BASE}/api/research-plans/{plan_id}/confirm",
                                            json={}, timeout=60)
                        if resp.status_code >= 400:
                            st.error(f"确认失败: {resp.text}")
                        else:
                            result = resp.json()
                            st.success(f"方案已确认！DAG: `{result.get('dag_id')}`")
                            load_plan(plan_id)
                            load_dag(plan_id)
                            st.rerun()
                    except Exception as exc:
                        st.error(f"确认失败: {exc}")
        with col_reload:
            if st.button("重新加载" if is_chinese else "Reload", use_container_width=True):
                load_plan(plan_id)
                st.rerun()

    elif status == "confirmed":
        st.success("此方案已确认。执行 DAG 已创建。" if is_chinese
                   else "This plan is confirmed. Execution DAG has been created.")
        dag_id = st.session_state.get("rp_dag_data", {}).get("dag_id")
        if dag_id:
            st.markdown(f"**DAG ID:** `{dag_id}`")
        if st.button("重新加载验证 DAG" if is_chinese else "Reload DAG", use_container_width=True):
            load_dag(plan_id)
            st.rerun()
        render_research_plan_dag_preview(plan_id, plan)

        st.divider()
        st.markdown("### " + ("🚀 开始分析" if is_chinese else "🚀 Start Analysis"))
        # Change 5: warn if outline is too thin
        outline_sections = st.session_state.get("rp_outline_sections", [])
        if outline_sections:
            non_trivial = [s for s in outline_sections if s.get("type") not in ("cover", "appendix")]
            product_count = len(plan.get("competitors", []))
            min_sections = product_count * 3 if product_count > 0 else 6
            if len(non_trivial) < min_sections:
                st.warning(
                    f"大纲目前仅 {len(non_trivial)} 个正文章节，建议至少 {min_sections} 个以确保报告完整性。"
                    if is_chinese else
                    f"Outline has only {len(non_trivial)} substantive sections, minimum {min_sections} recommended."
                )
        st.warning("将以此方案创建项目并启动竞品分析执行。" if is_chinese
                   else "This will create a project from this plan and start execution.")
        col_start, _ = st.columns([1, 2])
        with col_start:
            if st.button("🚀 开始分析" if is_chinese else "🚀 Start Analysis",
                         type="primary", use_container_width=True):
                # Change 6: validate outline has at least 3 sections before starting
                if len(outline_sections) < 3:
                    st.error(
                        "大纲章节数过少（至少需要 3 个章节），请先生成或添加章节。"
                        if is_chinese else
                        "Outline has too few sections (minimum 3 required). Please generate or add sections first."
                    )
                else:
                    _start_analysis_from_plan(plan, plan_id, dag_id)

    # Raw JSON
    with st.expander(("查看完整 JSON" if is_chinese else "View Full JSON"), expanded=False):
        st.json(plan)


# ════════════════════════════════════════════════════════════════════════════
# Tab renderers (broken out for readability)
# ════════════════════════════════════════════════════════════════════════════

def _render_competitors_tab(plan, plan_id, is_chinese: bool):
    """竞品管理：支持旧 schema (url/seed_urls) + 新 schema (notes)，统一展示和增删改。"""
    competitors = plan.get("competitors") or []

    # ── Derive working copy from plan (or session) ──────────────────────
    # Guard: only init from plan if plan_id is set (prevents wiping on page load)
    if not st.session_state["rp_comps_edited"] and plan_id:
        # Convert old schema to new schema in memory
        _comps = []
        for c in competitors:
            new_c = dict(c)
            # Normalise: if old schema has url/seed_urls, keep but hide from UI
            if "url" not in new_c and "seed_urls" in new_c:
                new_c["url"] = ""
            if "notes" not in new_c:
                new_c["notes"] = ""
            if "company_name" not in new_c:
                new_c["company_name"] = c.get("company") or ""
            _comps.append(new_c)
        st.session_state["rp_comps_list"] = _comps
        st.session_state["rp_comps_edited"] = True
    else:
        _comps = st.session_state.get("rp_comps_list", [])

    comps = _comps

    st.subheader("🏢 " + ("竞品管理" if is_chinese else "Competitor Management"))
    st.caption(
        ("新增、编辑或删除竞品。用户要求会自动传给搜索器，无需填写 URL。" if is_chinese else
         "Add, edit or delete competitors. User notes are passed to the searcher automatically. No URL needed."))

    if not comps:
        st.info("无竞品，请添加。" if is_chinese else "No competitors. Add one below.")

    # ── Add new competitor ───────────────────────────────────────────────
    with st.expander("➕ " + ("添加新竞品" if is_chinese else "Add Competitor"), expanded=False):
        col_n, col_co = st.columns(2)
        with col_n:
            new_name = st.text_input(
                ("竞品名称 *" if is_chinese else "Competitor Name *"),
                placeholder="Dify", key="comp_new_name",
            )
        with col_co:
            new_company = st.text_input(
                ("公司名称（可选）" if is_chinese else "Company (optional)"),
                placeholder="Dify.ai", key="comp_new_company",
            )
        col_pr, col_re = st.columns(2)
        with col_pr:
            new_priority = st.selectbox(
                ("优先级" if is_chinese else "Priority"),
                options=["high", "medium", "low"], index=1, key="comp_new_priority",
            )
        with col_re:
            new_region = st.selectbox(
                ("目标市场" if is_chinese else "Target Market"),
                options=["global", "china", "us", "europe", "southeast_asia"],
                index=0, key="comp_new_region",
            )
        new_notes = st.text_area(
            ("用户要求/备注（可选）" if is_chinese else "User Notes / Requirements (optional)"),
            placeholder=("例如：重点关注其企业版定价和私有化部署能力" if is_chinese else
                        "e.g. Focus on enterprise pricing and private deployment"),
            height=80, key="comp_new_notes",
        )
        if st.button(
                "✅ " + ("添加竞品" if is_chinese else "Add"),
                type="primary", use_container_width=True,
        ):
            if not new_name.strip():
                st.error("请输入竞品名称。" if is_chinese else "Enter a competitor name.")
            else:
                comps.append({
                    "name": new_name.strip(),
                    "company_name": new_company.strip(),
                    "priority": new_priority,
                    "region": new_region,
                    "notes": new_notes.strip(),
                })
                st.session_state["rp_comps_list"] = comps
                st.session_state["rp_comps_edited"] = True
                st.success(f"已添加: {new_name}")
                st.rerun()

    st.divider()

    # ── Edit existing competitors ────────────────────────────────────────
    if comps:
        for idx, comp in enumerate(comps):
            with st.container():
                col_a, col_b, col_c = st.columns([4, 2, 1])
                with col_a:
                    edited_name = st.text_input(
                        ("名称 *" if is_chinese else "Name *"),
                        value=comp.get("name", ""),
                        key=f"comp_name_{idx}",
                    )
                with col_b:
                    edited_priority = st.selectbox(
                        ("优先级" if is_chinese else "Priority"),
                        options=["high", "medium", "low"],
                        index=["high", "medium", "low"].index(comp.get("priority", "medium")),
                        key=f"comp_pr_{idx}",
                    )
                with col_c:
                    st.markdown("")
                    st.markdown("")
                    if st.button("🗑️", key=f"comp_del_{idx}", use_container_width=True):
                        comps.pop(idx)
                        st.session_state["rp_comps_list"] = comps
                        st.success("已删除" if is_chinese else "Deleted")
                        st.rerun()

                col_co, col_re = st.columns(2)
                with col_co:
                    edited_company = st.text_input(
                        ("公司名称" if is_chinese else "Company"),
                        value=comp.get("company_name", ""),
                        key=f"comp_co_{idx}",
                    )
                with col_re:
                    region_opts = ["global", "china", "us", "europe", "southeast_asia"]
                    cur_region = comp.get("region", "global")
                    edited_region = st.selectbox(
                        ("目标市场" if is_chinese else "Target Market"),
                        options=region_opts,
                        index=region_opts.index(cur_region) if cur_region in region_opts else 0,
                        key=f"comp_re_{idx}",
                    )

                edited_notes = st.text_area(
                    ("用户要求/备注" if is_chinese else "User Notes"),
                    value=comp.get("notes", ""),
                    height=60, key=f"comp_note_{idx}",
                )

                # Live-update the comps list (streamlit re-runs on every widget change)
                comp["name"]         = edited_name
                comp["company_name"] = edited_company
                comp["priority"]     = edited_priority
                comp["region"]       = edited_region
                comp["notes"]        = edited_notes
                st.session_state["rp_comps_list"] = comps

                st.divider()

        # ── Summary table ──────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📊 " + ("竞品总览" if is_chinese else "Competitor Overview"))
        summary = []
        for c in comps:
            emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(c.get("priority", "medium"), "")
            summary.append({
                ("竞品" if is_chinese else "Competitor"):   c.get("name", ""),
                ("公司" if is_chinese else "Company"):       c.get("company_name", ""),
                ("优先级" if is_chinese else "Priority"):    f"{emoji} {c.get('priority', '')}",
                ("市场" if is_chinese else "Market"):        c.get("region", "global"),
                ("用户要求" if is_chinese else "Notes"):      c.get("notes", "")[:40],
            })
        st.table(pd.DataFrame(summary))

        # ── Save button ────────────────────────────────────────────────────
        st.divider()
        if st.button("💾 " + ("保存竞品修改" if is_chinese else "Save Changes"),
                     type="primary", use_container_width=True):
            _save_competitors(plan_id, comps, plan, is_chinese)


def _save_competitors(plan_id, comps, plan, is_chinese):
    """PUT updated competitors back to plan JSON."""
    updated_plan = dict(plan)
    updated_plan["competitors"] = comps
    try:
        resp = requests.put(
            f"{API_BASE}/api/research-plans/{plan_id}",
            json={"payload_json": json.dumps(updated_plan, ensure_ascii=False)},
            timeout=30,
        )
        if resp.status_code >= 400:
            st.error(f"保存失败: {resp.text}")
        else:
            st.session_state["rp_plan_data"] = resp.json().get("research_plan")
            st.session_state["rp_comps_edited"] = False
            st.success("已保存！" if is_chinese else "Saved!")
            st.rerun()
    except Exception as exc:
        st.error(f"保存失败: {exc}")


def _render_dimensions_tab(plan, plan_id, is_chinese: bool):
    """分析维度：预设维度选择（跟随语言）+ 自定义 + 已选列表。"""
    plan_dims = plan.get("analysis_dimensions") or []

    # ── Derive working copy ─────────────────────────────────────────────
    if not st.session_state["rp_dims_edited"]:
        st.session_state["rp_dims_list"] = list(plan_dims)
        st.session_state["rp_dims_edited"] = True
    dims = st.session_state.get("rp_dims_list", [])

    # ── Predefined bilingual dimensions ─────────────────────────────────
    PREDEFINED = {
        ("产品功能" if is_chinese else "Product Features"): [
            {"dim_id": "core_capabilities",   "name": "功能树 / 核心能力",           "name_en": "Core Capabilities",
             "desc": "工作流构建、RAG、工具调用、多Agent编排等", "desc_en": "Workflow, RAG, tool calling, multi-agent", "req": True},
            {"dim_id": "workflow_builder",    "name": "工作流编排",                  "name_en": "Workflow Builder",
             "desc": "可视化构建、节点类型、触发条件",         "desc_en": "Visual builder, node types, triggers", "req": False},
            {"dim_id": "rag",                "name": "RAG / 知识库",                "name_en": "RAG / Knowledge Base",
             "desc": "知识库管理、向量检索、文档处理",         "desc_en": "KB mgmt, vector search, document processing", "req": False},
            {"dim_id": "tool_calling",       "name": "工具调用",                    "name_en": "Tool Calling",
             "desc": "第三方工具集成、API扩展能力",           "desc_en": "3rd-party integrations, API extensibility", "req": False},
            {"dim_id": "multi_agent",         "name": "多Agent编排",                  "name_en": "Multi-Agent",
             "desc": "多Agent协作、状态管理、复杂任务",       "desc_en": "Multi-agent collaboration, state management", "req": False},
        ],
        ("企业能力" if is_chinese else "Enterprise Capabilities"): [
            {"dim_id": "enterprise_readiness", "name": "企业就绪度",                  "name_en": "Enterprise Readiness",
             "desc": "私有化部署、权限控制、审计日志",         "desc_en": "Private deployment, access control, audit logs", "req": False},
            {"dim_id": "private_deployment",  "name": "私有化部署",                  "name_en": "Private Deployment",
             "desc": "本地部署、Docker、K8s支持",             "desc_en": "On-premise, Docker, K8s support", "req": False},
            {"dim_id": "security",            "name": "安全与合规",                  "name_en": "Security & Compliance",
             "desc": "SSO、数据加密、审计日志、合规认证",     "desc_en": "SSO, encryption, audit logs, compliance", "req": False},
        ],
        ("商业化" if is_chinese else "Commercialization"): [
            {"dim_id": "pricing_model",       "name": "定价模式",                    "name_en": "Pricing Model",
             "desc": "免费版、订阅制、用量计费",              "desc_en": "Free tier, subscription, usage-based pricing", "req": False},
            {"dim_id": "pricing_strategy",     "name": "定价策略",                    "name_en": "Pricing Strategy",
             "desc": "价格竞争力、TCO、商业模式",            "desc_en": "Price competitiveness, TCO, business model", "req": False},
        ],
        ("用户体验" if is_chinese else "User Experience"): [
            {"dim_id": "user_persona",        "name": "用户画像",                    "name_en": "User Persona",
             "desc": "目标用户类型、使用场景、易用性",        "desc_en": "Target users, use cases, ease of use", "req": False},
            {"dim_id": "customer_voice",      "name": "用户声音",                    "name_en": "Customer Voice",
             "desc": "社区反馈、评价、典型案例",             "desc_en": "Community feedback, reviews, case studies", "req": False},
            {"dim_id": "learning_curve",      "name": "学习曲线",                    "name_en": "Learning Curve",
             "desc": "上手难度、文档质量",                  "desc_en": "Ease of onboarding, documentation quality", "req": False},
        ],
        ("生态与支持" if is_chinese else "Ecosystem & Support"): [
            {"dim_id": "ecosystem",           "name": "生态系统",                    "name_en": "Ecosystem",
             "desc": "插件市场、模板中心、第三方集成",        "desc_en": "Plugin marketplace, templates, 3rd-party integrations", "req": False},
            {"dim_id": "community",           "name": "社区活跃度",                  "name_en": "Community Activity",
             "desc": "GitHub星标、贡献者、版本发布频率",     "desc_en": "GitHub stars, contributors, release frequency", "req": False},
            {"dim_id": "enterprise_support",  "name": "企业支持",                    "name_en": "Enterprise Support",
             "desc": "技术支持、SLA、定制服务",              "desc_en": "Technical support, SLA, professional services", "req": False},
        ],
    }

    st.subheader("📊 " + ("分析维度管理" if is_chinese else "Dimension Management"))

    # ── Predefined dimension picker ─────────────────────────────────────
    st.markdown("### " + ("➕ 从预设维度中选择" if is_chinese else "➕ Select from Predefined Dimensions"))
    st.caption(("勾选要纳入分析的维度，点击切换选中状态" if is_chinese
                else "Toggle dimensions to include in analysis"))

    for category, dim_list in PREDEFINED.items():
        selected_in_cat = [
            d for d in dim_list
            if any(dim.get("dimension_id") == d["dim_id"] for dim in dims)
        ]
        with st.expander(
                f"📁 {category}  ({len(selected_in_cat)}/{len(dim_list)})",
                expanded=False,
        ):
            for d in dim_list:
                dim_id      = d["dim_id"]
                is_selected = any(dim.get("dimension_id") == dim_id for dim in dims)
                display_name = d["name"] if is_chinese else d["name_en"]
                display_desc = d["desc"] if is_chinese else d["desc_en"]

                col_left, col_btn = st.columns([5, 1])
                with col_left:
                    emoji = "🔴" if d["req"] else ""
                    st.markdown(f"**{emoji}{display_name}**")
                    st.caption(f"_{display_desc[:60]}_")
                with col_btn:
                    if is_selected:
                        if st.button(
                                "✓ " + ("移除" if is_chinese else "Remove"),
                                key=f"dim_toggle_{dim_id}",
                                use_container_width=True,
                        ):
                            dims[:] = [dim for dim in dims if dim.get("dimension_id") != dim_id]
                            st.session_state["rp_dims_list"] = dims
                            st.rerun()
                    else:
                        if st.button(
                                "➕ " + ("选中" if is_chinese else "Add"),
                                key=f"dim_toggle_{dim_id}",
                                use_container_width=True,
                        ):
                            dims.append({
                                "dimension_id": dim_id,
                                "name": d["name"],
                                "name_en": d["name_en"],
                                "description": d["desc"],
                                "description_en": d["desc_en"],
                                "required": d["req"],
                            })
                            st.session_state["rp_dims_list"] = dims
                            st.rerun()

    st.divider()

    # ── Currently selected ──────────────────────────────────────────────
    st.markdown("### " + ("📋 当前已选维度" if is_chinese else "📋 Currently Selected Dimensions"))
    if not dims:
        st.info(("请从上方预设维度中选择" if is_chinese else "Select dimensions from above"))
    else:
        st.markdown(f"**{len(dims)} " + ("个维度" if is_chinese else "dimensions selected") + "**")
        for idx, dim in enumerate(dims):
            col_name, col_id, col_req, col_del = st.columns([3, 1, 1, 1])
            display_name = dim.get("name") if is_chinese else dim.get("name_en", dim.get("name", ""))
            with col_name:
                emoji = "🔴" if dim.get("required") else "⚪"
                st.markdown(f"{emoji} **{display_name}**")
            with col_id:
                st.caption(f"`{dim.get('dimension_id', '')}`")
            with col_req:
                if dim.get("required"):
                    st.caption("必需")
            with col_del:
                if st.button("🗑️", key=f"dim_rm_{idx}", use_container_width=True):
                    dims.pop(idx)
                    st.session_state["rp_dims_list"] = dims
                    st.rerun()
        st.divider()

    # ── Custom dimension ─────────────────────────────────────────────────
    with st.expander("✨ " + ("添加自定义维度" if is_chinese else "Add Custom Dimension"), expanded=False):
        col_n, col_i = st.columns(2)
        with col_n:
            cust_name = st.text_input(
                ("维度名称 *" if is_chinese else "Dimension Name *"),
                placeholder="API稳定性", key="cust_dim_name",
            )
        with col_i:
            cust_id = st.text_input(
                ("维度ID *" if is_chinese else "Dimension ID *"),
                placeholder="api_stability", key="cust_dim_id",
            )
        cust_desc = st.text_area(
            ("描述" if is_chinese else "Description"),
            height=60, key="cust_dim_desc",
        )
        cust_req = st.checkbox(
            ("设为必需维度" if is_chinese else "Mark as required"),
            value=False, key="cust_dim_req",
        )
        if st.button(
                "➕ " + ("添加自定义维度" if is_chinese else "Add Dimension"),
                type="primary", use_container_width=True,
        ):
            if not cust_name.strip() or not cust_id.strip():
                st.error("请输入维度名称和ID。" if is_chinese else "Enter name and ID.")
            else:
                dims.append({
                    "dimension_id": cust_id.strip().lower().replace(" ", "_"),
                    "name": cust_name.strip(),
                    "name_en": "",
                    "description": cust_desc.strip(),
                    "description_en": "",
                    "required": cust_req,
                })
                st.session_state["rp_dims_list"] = dims
                st.success(f"已添加: {cust_name}")
                st.rerun()

    # ── Save ────────────────────────────────────────────────────────────
    st.divider()
    if st.button("💾 " + ("保存维度修改" if is_chinese else "Save Changes"),
                 type="primary", use_container_width=True):
        _save_dimensions(plan_id, dims, plan, is_chinese)


def _save_dimensions(plan_id, dims, plan, is_chinese):
    """PUT updated dimensions back to plan JSON."""
    updated_plan = dict(plan)
    updated_plan["analysis_dimensions"] = dims
    try:
        resp = requests.put(
            f"{API_BASE}/api/research-plans/{plan_id}",
            json={"payload_json": json.dumps(updated_plan, ensure_ascii=False)},
            timeout=30,
        )
        if resp.status_code >= 400:
            st.error(f"保存失败: {resp.text}")
        else:
            st.session_state["rp_plan_data"]  = resp.json().get("research_plan")
            st.session_state["rp_dims_edited"] = False
            st.success("已保存！" if is_chinese else "Saved!")
            st.rerun()
    except Exception as exc:
        st.error(f"保存失败: {exc}")


def _render_outline_tab(plan, plan_id, is_chinese: bool):
    """报告大纲：层级结构（大章节+小节）+ 跟随语言 + 独立 LLM 生成。"""
    # ── Language-aware initial state ────────────────────────────────────
    outline_from_plan = plan.get("report_outline") or {}

    # Always sync from plan on every render
    st.session_state["rp_outline_sections"] = outline_from_plan.get("sections") or []
    st.session_state["rp_outline_title"] = outline_from_plan.get("report_title") or (
        ("竞品分析报告" if is_chinese else "Competitive Analysis Report"))
    st.session_state["rp_outline_edited"] = False

    sections = st.session_state["rp_outline_sections"]
    title    = st.session_state["rp_outline_title"]

    # ── Report title ─────────────────────────────────────────────────────
    st.subheader("📋 " + ("报告大纲" if is_chinese else "Report Outline"))
    st.caption(
        ("大纲由 AI 根据竞品和维度单独生成，可随意增删调整。" if is_chinese else
         "Outline is generated by AI based on competitors & dimensions. Edit freely."))

    new_title = st.text_input(
        ("📝 报告标题" if is_chinese else "📝 Report Title"),
        value=title,
        key="rp_outline_title_input",
    )
    if new_title != title:
        st.session_state["rp_outline_title"] = new_title

    st.divider()

    # ── AI Generate button ──────────────────────────────────────────────
    st.markdown("### 🤖 " + ("AI 生成大纲" if is_chinese else "AI Generate Outline"))
    st.caption(
        ("根据竞品、分析维度和语言设置，生成包含大章节和小节的完整报告结构。" if is_chinese else
         "Generate a complete outline with chapters and subsections based on competitors, dimensions and language."))

    lang_code = "zh" if is_chinese else "en"

    col_gen, col_desc = st.columns([1, 4])
    with col_gen:
        if st.button(
                "✨ " + ("生成大纲" if is_chinese else "Generate Outline"),
                type="primary", use_container_width=True,
                disabled=st.session_state.get("rp_outline_generating", False),
        ):
            st.session_state["rp_outline_generating"] = True
            st.session_state["rp_outline_sections"]  = []
            st.rerun()

    if st.session_state.get("rp_outline_generating"):
        with st.spinner(("正在调用 LLM 生成大纲，请稍候..." if is_chinese else
                         "Generating outline with LLM, please wait...")):
            try:
                resp = requests.post(
                    f"{API_BASE}/api/research-plans/{plan_id}/generate-outline",
                    json={
                        "competitors": st.session_state.get("rp_comps_list", plan.get("competitors", [])),
                        "dimensions":  st.session_state.get("rp_dims_list",  plan.get("analysis_dimensions", [])),
                        "language":     lang_code,
                    },
                    timeout=180,
                )
                if resp.status_code >= 400:
                    st.error(f"生成失败: {resp.text}")
                else:
                    result       = resp.json()
                    new_outline  = result.get("outline", {})
                    st.session_state["rp_outline_sections"] = new_outline.get("sections", [])
                    st.session_state["rp_outline_title"]    = new_outline.get(
                        "report_title", st.session_state["rp_outline_title"])
                    # Change 6: auto-save outline to plan after LLM generation
                    try:
                        updated_plan = dict(st.session_state.get("rp_plan_data", {}))
                        updated_plan["report_outline"] = new_outline
                        requests.put(
                            f"{API_BASE}/api/research-plans/{plan_id}",
                            json={"payload_json": json.dumps(updated_plan)},
                            timeout=30,
                        )
                        st.session_state["rp_plan_data"] = updated_plan
                        st.success(("大纲已生成并自动保存！" if is_chinese else "Outline generated and auto-saved!"))
                    except Exception as save_exc:
                        st.warning(
                            ("大纲已生成但自动保存失败，请手动保存。错误: " if is_chinese else
                             "Outline generated but auto-save failed. Please save manually. Error: ") + str(save_exc)
                        )
            except Exception as exc:
                st.error(f"生成失败: {exc}")
            finally:
                st.session_state["rp_outline_generating"] = False
                st.rerun()

    st.divider()

    # ── Outline hierarchy display ───────────────────────────────────────
    if not sections:
        st.info(
            ("暂无大纲。点击上方「生成大纲」按钮由 AI 创建，或手动添加章节。" if is_chinese else
             "No outline yet. Click 'Generate Outline' above or add sections manually."))
    else:
        heading_sections = ("章节结构" if is_chinese else "Chapter Structure")
        count_label     = ("个章节" if is_chinese else "sections")
        st.markdown("### 📑 " + heading_sections + "  (" + str(len(sections)) + " " + count_label + ")")

        chapter_counter = 0
        # group into chapters / subsections
        chapters = []
        current_chapter = None
        for sec in sections:
            if sec.get("type") == "chapter" or sec.get("type") is None:
                chapter_counter += 1
                current_chapter = {"chapter": sec, "chapter_idx": chapter_counter, "subsections": []}
                chapters.append(current_chapter)
            else:
                if current_chapter:
                    current_chapter["subsections"].append(sec)
                else:
                    # orphan subsection → treat as chapter
                    chapter_counter += 1
                    current_chapter = {"chapter": sec, "chapter_idx": chapter_counter, "subsections": []}
                    chapters.append(current_chapter)

        # ── Render chapters ─────────────────────────────────────────────
        chapters_to_delete = set()
        for ch in chapters:
            sec   = ch["chapter"]
            cidx  = ch["chapter_idx"]
            subs  = ch["subsections"]
            sid   = sec.get("section_id", f"ch_{cidx}")

            with st.container():
                col_title, col_words, col_review, col_del = st.columns([4, 1, 1, 1])
                with col_title:
                    heading = "第" + str(cidx) + "章：" + sec.get("title", "") if is_chinese else "Chapter " + str(cidx) + ": " + sec.get("title", "")
                st.markdown("#### 📌 " + heading)
                with col_words:
                    st.caption(f"≥{sec.get('min_words', 800)}字")
                with col_review:
                    if sec.get("requires_human_review"):
                        st.caption("🔴 " + ("需审核" if is_chinese else "Review"))
                with col_del:
                    if st.button("🗑️", key=f"del_ch_{sid}", use_container_width=True):
                        chapters_to_delete.add(sid)

                with st.expander(("章节详情" if is_chinese else "Chapter Details"), expanded=False):
                    col_t, col_w = st.columns(2)
                    with col_t:
                        new_ch_title = st.text_input(
                            ("章节标题" if is_chinese else "Chapter Title"),
                            value=sec.get("title", ""), key=f"ch_t_{sid}",
                        )
                    with col_w:
                        new_words = st.number_input(
                            ("最低字数" if is_chinese else "Min Words"),
                            min_value=0, max_value=50000, value=sec.get("min_words", 800),
                            step=100, key=f"ch_w_{sid}",
                        )
                    new_review = st.checkbox(
                        ("需要人工审核" if is_chinese else "Requires Human Review"),
                        value=bool(sec.get("requires_human_review")), key=f"ch_r_{sid}",
                    )
                    new_purpose = st.text_area(
                        ("章节目的/简介" if is_chinese else "Chapter Purpose"),
                        value=sec.get("purpose", ""), height=60, key=f"ch_p_{sid}",
                    )
                    # live-update
                    sec["title"]                = new_ch_title
                    sec["min_words"]            = new_words
                    sec["requires_human_review"] = new_review
                    sec["purpose"]              = new_purpose

            # ── Subsections ───────────────────────────────────────────────
            for sub in subs:
                sub_id = sub.get("section_id", f"sub_{sid}")
                with st.container():
                    col_s1, col_s2, col_s3 = st.columns([4, 1, 1])
                    with col_s1:
                        st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ **{sub.get('title', '')}**")
                    with col_s2:
                        st.caption(f"≥{sub.get('min_words', 400)}字")
                    with col_s3:
                        if st.button("🗑️", key=f"del_sub_{sub_id}", use_container_width=True):
                            subs.remove(sub)

                    with st.expander(("小节详情" if is_chinese else "Subsection Details"), expanded=False):
                        col_st, col_sw = st.columns(2)
                        with col_st:
                            new_sub_title = st.text_input(
                                ("小节标题" if is_chinese else "Subsection Title"),
                                value=sub.get("title", ""), key=f"sub_t_{sub_id}",
                            )
                        with col_sw:
                            new_sub_words = st.number_input(
                                ("最低字数" if is_chinese else "Min Words"),
                                min_value=0, max_value=50000, value=sub.get("min_words", 400),
                                step=100, key=f"sub_w_{sub_id}",
                            )
                        new_sub_purpose = st.text_area(
                            ("小节目的" if is_chinese else "Subsection Purpose"),
                            value=sub.get("purpose", ""), height=60, key=f"sub_p_{sub_id}",
                        )
                        sub["title"]   = new_sub_title
                        sub["min_words"] = new_sub_words
                        sub["purpose"]  = new_sub_purpose

            st.divider()

        # Apply deletions
        if chapters_to_delete:
            sections[:] = [s for s in sections if s.get("section_id") not in chapters_to_delete]
            st.session_state["rp_outline_sections"] = sections
            st.rerun()

        # ── Move up / down ──────────────────────────────────────────────
        if len(sections) > 1:
            col_up, col_down = st.columns(2)
            with col_up:
                if st.button("⬆ " + ("整体上移" if is_chinese else "Move Up"), use_container_width=True):
                    sections.insert(0, sections.pop())
                    st.session_state["rp_outline_sections"] = sections
                    st.rerun()
            with col_down:
                if st.button("⬇ " + ("整体下移" if is_chinese else "Move Down"), use_container_width=True):
                    sections.append(sections.pop(0))
                    st.session_state["rp_outline_sections"] = sections
                    st.rerun()

        # ── Preview table ─────────────────────────────────────────────────
        st.divider()
        st.markdown("### 📄 " + ("大纲预览" if is_chinese else "Outline Preview"))
        rows = []
        for i, s in enumerate(sections, 1):
            rows.append({
                ("序号" if is_chinese else "#"):     i,
                ("类型" if is_chinese else "Type"):  ("章节" if s.get("type") == "chapter" else "小节") if s.get("type") else "章节",
                ("章节" if is_chinese else "Title"): s.get("title", ""),
                ("最低字数" if is_chinese else "Min Words"): s.get("min_words", 0),
                ("审核" if is_chinese else "Review"): "🔴" if s.get("requires_human_review") else "",
            })
        st.table(pd.DataFrame(rows))

    # ── Add new chapter / subsection ───────────────────────────────────
    st.divider()
    with st.expander("➕ " + ("添加新章节" if is_chinese else "Add Section"), expanded=False):
        col_type, col_title_in = st.columns([1, 3])
        with col_type:
            new_type = st.selectbox(
                ("类型" if is_chinese else "Type"),
                options=["chapter", "subsection"],
                index=0, key="new_sec_type_v2",
            )
        with col_title_in:
            new_title_in = st.text_input(
                ("章节标题" if is_chinese else "Section Title"),
                key="new_sec_title_v2",
            )
        col_w, col_rev = st.columns(2)
        with col_w:
            new_words_in = st.number_input(
                ("最低字数" if is_chinese else "Min Words"),
                min_value=0, max_value=50000, value=800, step=100,
                key="new_sec_words_v2",
            )
        with col_rev:
            new_review_in = st.checkbox(
                ("需要人工审核" if is_chinese else "Requires Review"),
                value=False, key="new_sec_rev_v2",
            )
        if st.button(
                ("添加章节" if is_chinese else "Add Section"),
                type="primary", use_container_width=True,
        ):
            if not new_title_in.strip():
                st.error("请输入章节标题。" if is_chinese else "Enter a section title.")
            else:
                sections.append({
                    "section_id":            f"sec_{len(sections)+1:02d}",
                    "title":                 new_title_in.strip(),
                    "type":                  new_type,
                    "min_words":            new_words_in,
                    "requires_human_review": new_review_in,
                    "purpose":              "",
                    "slug":                 new_title_in.lower().replace(" ", "_")[:20],
                })
                st.session_state["rp_outline_sections"] = sections
                st.rerun()

    # ── Save ────────────────────────────────────────────────────────────
    st.divider()
    col_save, col_reset = st.columns(2)
    with col_save:
        if st.button(
                "💾 " + ("保存大纲" if is_chinese else "Save Outline"),
                type="primary", use_container_width=True,
        ):
            _save_outline(plan_id, plan, is_chinese)
    with col_reset:
        if st.button("🔄 " + ("重置" if is_chinese else "Reset"), use_container_width=True):
            st.session_state["rp_outline_sections"] = outline_from_plan.get("sections") or []
            st.session_state["rp_outline_title"]     = outline_from_plan.get("report_title") or ""
            st.session_state["rp_outline_edited"]   = False
            st.rerun()

    # ── Raw JSON ────────────────────────────────────────────────────────
    with st.expander("🔍 " + ("查看大纲 JSON" if is_chinese else "View Outline JSON")):
        st.json({"report_title": st.session_state["rp_outline_title"],
                 "sections":    st.session_state["rp_outline_sections"]})


def _save_outline(plan_id, plan, is_chinese):
    """PUT updated outline back to plan JSON."""
    updated_plan = dict(plan)
    updated_plan["report_outline"] = {
        "report_title": st.session_state["rp_outline_title"],
        "sections":     st.session_state["rp_outline_sections"],
    }
    try:
        resp = requests.put(
            f"{API_BASE}/api/research-plans/{plan_id}",
            json={"payload_json": json.dumps(updated_plan, ensure_ascii=False)},
            timeout=30,
        )
        if resp.status_code >= 400:
            st.error(f"保存失败: {resp.text}")
        else:
            st.session_state["rp_plan_data"]    = resp.json().get("research_plan")
            st.session_state["rp_outline_edited"] = False
            st.success("已保存！" if is_chinese else "Saved!")
            st.rerun()
    except Exception as exc:
        st.error(f"保存失败: {exc}")
