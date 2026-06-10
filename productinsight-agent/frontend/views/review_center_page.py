"""Report Quality Assessment page — former Review Center.

P1-Redesign (2026-06-05): The Review Center has been repositioned from a
workflow gate to a post-generation quality assessment page. The workflow no
longer blocks waiting for human intervention; instead this page provides
a read-only quality overview to help users understand report reliability
before delivering the output.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st
import requests

from frontend.common.api import get_json
from frontend.common.config import API_BASE
from frontend.components.run_banner import render_run_banner


def _render_action_row(run_id: str, key_prefix: str):
    """Quick-links to related views."""
    cols = st.columns(3)
    with cols[0]:
        if st.button("查看 Claims", key=f"{key_prefix}_claims_{run_id}", use_container_width=True):
            from frontend.common.navigation import goto_page
            goto_page("Knowledge Table")
        st.caption("浏览所有采集的声明")
    with cols[1]:
        if st.button("查看证据", key=f"{key_prefix}_evidence_{run_id}", use_container_width=True):
            from frontend.common.navigation import goto_page
            goto_page("Evidence Hub")
        st.caption("浏览证据附录")
    with cols[2]:
        if st.button("查看报告", key=f"{key_prefix}_report_{run_id}", use_container_width=True):
            st.session_state["af_stage"] = "deliverables"
            from frontend.common.navigation import goto_page
            goto_page("Analysis Flow")
        st.caption("查看生成的报告内容")


def _readiness_badge(readiness: str) -> tuple[str, str]:
    """Return (icon, label, color) for a readiness level."""
    return {
        "ready":     ("✅", "可交付", "green"),
        "partial":   ("⚠️", "部分可交付", "yellow"),
        "needs_work": ("🔴", "需改进", "red"),
    }.get(readiness, ("❓", "未知", "gray"))


def _render_readiness_section(qs: dict):
    """Render the main deliverability assessment."""
    reviewer = qs.get("_reviewer_signed_count", 0)
    analyst = qs.get("_analyst_signed_count", 0)
    signed = qs.get("claims_count", 0)
    rework = qs.get("rework_required_claims_count", 0)
    rev_sections = qs.get("_revision_requested_sections", [])

    # Determine readiness
    if reviewer > 0 and rework == 0 and not rev_sections:
        readiness = "ready"
    elif signed > 0 and rework == 0:
        readiness = "partial"
    else:
        readiness = "needs_work"

    icon, label, color = _readiness_badge(readiness)

    st.markdown(f"### {icon} 报告可交付性：**{label}**")

    # Colored banner
    if readiness == "ready":
        st.success(
            "本报告已通过 LLM 质量门验证，包含 Reviewer 正式签署声明，可作为正式交付物使用。"
        )
    elif readiness == "partial":
        st.warning(
            f"本报告包含 {analyst} 条 Analyst 预签声明（待 Reviewer 复核）。"
            "建议 Reviewer 完成复核后再作为正式交付物。"
        )
    else:
        st.error(
            "本报告存在返工需求或章节审查未通过。建议在改进后再使用。"
        )


def _render_claims_metrics(qs: dict):
    """Render the claims signing breakdown."""
    reviewer = qs.get("_reviewer_signed_count", 0)
    analyst = qs.get("_analyst_signed_count", 0)
    signed = qs.get("claims_count", 0)
    rework = qs.get("rework_required_claims_count", 0)
    total = signed + rework

    st.markdown("### 📋 Claims 签署情况")

    cols = st.columns(4)
    with cols[0]:
        st.metric("候选总数", total)
    with cols[1]:
        st.metric("已签署", signed)
    with cols[2]:
        st.metric("  其中 Reviewer 签署", reviewer)
    with cols[3]:
        st.metric("  其中 Analyst 预签", analyst) if analyst > 0 else st.metric("  其中 Analyst 预签", "—")

    if rework > 0:
        st.metric("需返工", rework, delta_color="inverse")
    else:
        st.metric("需返工", "—")

    if analyst > 0:
        st.caption(
            "⚠️ Analyst 预签声明尚未经过 Reviewer 正式复核。"
            "如需提升报告可信度，请在 Knowledge Table 中完成 Reviewer 复核。"
        )


def _render_coverage_section(qs: dict):
    """Render per-product coverage breakdown."""
    coverage = qs.get("coverage_by_product", {})
    insufficient = qs.get("insufficient_products", 0)
    partial = qs.get("partial_products", 0)
    insufficient_prods = [p for p, v in coverage.items() if v == 0]

    st.markdown("### 🗺️ 产品覆盖情况")

    if coverage:
        rows = []
        for product, count in sorted(coverage.items(), key=lambda x: x[1]):
            if count == 0:
                status_icon, status_text = "🔴", "无声明"
                status_color = "error"
            elif count < 3:
                status_icon, status_text = "⚠️", "不足"
                status_color = "warning"
            else:
                status_icon, status_text = "✅", "充足"
                status_color = "off"
            rows.append({
                "产品": product,
                "已签署 Claims": count,
                "状态": f"{status_icon} {status_text}",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无产品覆盖数据。")

    if insufficient_prods:
        st.warning(f"以下产品尚未签署任何声明：{', '.join(insufficient_prods)}")


def _render_section_status(qs: dict):
    """Render revision-requested sections if any."""
    rev_sections = qs.get("_revision_requested_sections", [])
    report_status = qs.get("report_status", "unknown")

    st.markdown("### 📑 章节审查状态")

    status_map = {
        "reviewed":        ("✅", "已通过", "complete"),
        "reviewed_partial": ("⚠️", "部分通过", "warning"),
        "reviewed_with_gaps": ("⚠️", "有缺口", "warning"),
        "blocked_consistency": ("🔴", "一致性问题", "error"),
        "blocked":         ("🔴", "已阻断", "error"),
        "draft":          ("📝", "草稿中", "off"),
    }
    icon, label, _ = status_map.get(report_status, ("❓", report_status, "off"))
    st.markdown(f"**报告整体状态：** {icon} {label}")

    if rev_sections:
        st.warning(f"以下章节仍有审查意见待处理：")
        for slug in rev_sections:
            st.markdown(f"  - `{slug}`")
    elif report_status == "reviewed":
        st.success("所有章节均已通过审查，无需返工。")


def _render_workflow_summary(qs: dict, report_status: str):
    """Render additional quality signals."""
    st.markdown("### 🔍 质量信号摘要")

    col_info, col_count = st.columns(2)

    with col_info:
        st.markdown("**基本信息**")
        st.write(f"- 报告状态：`{report_status}`")
        st.write(f"- 总字数：{qs.get('total_word_count', 'N/A'):,}" if isinstance(qs.get('total_word_count'), (int, float)) else f"- 总字数：{qs.get('total_word_count', 'N/A')}")
        st.write(f"- 章节数：{qs.get('section_count', 'N/A')}")
        st.write(f"- 对比矩阵：{qs.get('table_count', 0)} 张")
        st.write(f"- 图表：{qs.get('figure_count', 0)} 个")

    with col_count:
        st.markdown("**数据规模**")
        st.write(f"- 引用证据：{qs.get('evidence_count', 0)} 条")
        st.write(f"- 已签署 Claims：{qs.get('claims_count', 0)} 条")
        st.write(f"- 证据覆盖率：{qs.get('evidence_coverage_rate', 0):.0%}" if isinstance(qs.get('evidence_coverage_rate'), (int, float)) else f"- 证据覆盖率：{qs.get('evidence_coverage_rate', 'N/A')}")
        st.write(f"- 平均深度得分：{qs.get('average_depth_score', 0):.0%}" if isinstance(qs.get('average_depth_score'), (int, float)) else f"- 平均深度得分：{qs.get('average_depth_score', 'N/A')}")
        st.write(f"- 分析产品数：{qs.get('products_analyzed', 0)} 个")


def _render_improvement_suggestions(qs: dict):
    """Render actionable improvement suggestions based on quality signals."""
    suggestions = []

    analyst = qs.get("_analyst_signed_count", 0)
    reviewer = qs.get("_reviewer_signed_count", 0)
    rework = qs.get("rework_required_claims_count", 0)
    rev_sections = qs.get("_revision_requested_sections", [])
    coverage = qs.get("coverage_by_product", {})
    insufficient_prods = [p for p, v in coverage.items() if v == 0]
    gate_failures = qs.get("_gate_failures", [])

    if analyst > 0 and reviewer == 0:
        suggestions.append(
            "所有已签署声明均为 Analyst 预签，尚未经过 Reviewer 正式复核。"
            "请在 **Knowledge Table** 中完成 Reviewer 复核以提升报告可信度。"
        )
    if rework > 0:
        suggestions.append(
            f"存在 {rework} 条需返工的声明。建议在 **Knowledge Table** 中逐条审查，"
            "确认后将其改为 Signed 或 Rework Required 状态。"
        )
    if rev_sections:
        suggestions.append(
            f"存在 {len(rev_sections)} 个章节仍有审查意见。系统将在下次报告重生成时自动处理。"
        )
    if insufficient_prods:
        suggestions.append(
            f"产品 {', '.join(insufficient_prods)} 尚未签署任何声明。"
            "建议在 Review Center 触发补证流程，或手动在 **Evidence Hub** 中添加证据。"
        )
    if gate_failures:
        suggestions.append(
            f"存在 {len(gate_failures)} 项一致性问题。建议在报告生成前解决这些问题。"
        )

    if suggestions:
        st.markdown("### 💡 改进建议")
        for s in suggestions:
            st.info(s)
    else:
        st.success("✅ 报告质量良好，暂无需特别改进的地方。")


def render_review_center_page(run_id: Optional[str] = None):
    """Render the Report Quality Assessment page (formerly Review Center)."""
    st.header("📋 报告质量评估")

    # Guard
    if not run_id or run_id == "run_demo_ai_agent_001":
        st.warning("请先选择一个运行（Run）来查看报告质量评估。")
        return

    # Fetch run status
    run_status = None
    run_error = ""
    try:
        resp = requests.get(f"{API_BASE}/api/runs/{run_id}", timeout=10)
        if resp.status_code == 200:
            run_data = resp.json()
            run_status = run_data.get("status")
            run_error = run_data.get("error_message") or ""
    except Exception:
        pass

    # Fetch report / quality summary
    report_data = get_json(f"/api/runs/{run_id}/report", {}) or {}
    if isinstance(report_data, dict):
        qs = report_data.get("quality_summary", {}) or {}
    else:
        qs = {}

    report_status = qs.get("report_status", "unknown")
    is_blocked_run = run_status == "failed" and "block" in run_error.lower()

    # ── Run banner ──────────────────────────────────────────────
    render_run_banner(run_id)

    # ── Blocked run warning ─────────────────────────────────────
    if is_blocked_run and not qs:
        st.error("报告生成失败。请查看工作流日志了解具体错误原因。")
        st.divider()
        _render_action_row(run_id, "rc")
        return

    # ── Report status pill ──────────────────────────────────────
    status_pills = {
        "reviewed":               ("已通过", "green"),
        "reviewed_partial":        ("部分通过", "yellow"),
        "reviewed_with_gaps":      ("有缺口", "yellow"),
        "blocked_consistency":     ("一致性问题", "red"),
        "blocked":                 ("已阻断", "red"),
        "draft":                  ("草稿中", "gray"),
    }
    pill_label, pill_color = status_pills.get(
        report_status, (report_status, "gray")
    )
    st.markdown(
        f"<span style='background-color: {pill_color}; color: white; "
        f"padding: 4px 12px; border-radius: 12px; font-size: 0.85em;'>"
        f"报告状态：{pill_label}</span>",
        unsafe_allow_html=True,
    )

    st.divider()

    # ── Readiness ────────────────────────────────────────────────
    _render_readiness_section(qs)

    st.divider()

    # ── Claims metrics ───────────────────────────────────────────
    _render_claims_metrics(qs)

    st.divider()

    # ── Coverage ─────────────────────────────────────────────────
    _render_coverage_section(qs)

    st.divider()

    # ── Section status ────────────────────────────────────────────
    _render_section_status(qs)

    st.divider()

    # ── Quality signals ──────────────────────────────────────────
    _render_workflow_summary(qs, report_status)

    st.divider()

    # ── Improvement suggestions ──────────────────────────────────
    _render_improvement_suggestions(qs)

    st.divider()

    # ── Actions ──────────────────────────────────────────────────
    st.markdown("### 🚀 后续操作")
    _render_action_row(run_id, "rc")
