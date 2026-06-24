"""Run banner component for ProductInsight Agent frontend."""

import streamlit as st

from frontend.common.api import get_json
from frontend.common.formatters import _badge


def render_run_banner(rid: str):
    """Render a banner showing key run information."""
    run = get_json(f"/api/runs/{rid}", {})
    if not run:
        st.warning(f"未找到 Run `{rid}`，请到「任务设置」页面创建。")
        return
    
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown("**Run ID**")
        st.code(rid, language=None)
    with c2:
        title = run.get("task_title", "—")
        st.markdown("**任务**")
        st.text(title[:25] + ("…" if len(title) > 25 else ""), help=title)
    with c3:
        st.markdown("**模式**")
        st.write(_badge(run.get("mode", "unknown")))
    with c4:
        st.markdown("**状态**")
        st.write(_badge(run.get("status", "unknown")))
    with c5:
        ts = run.get("created_at", "—")
        ts = ts[:16].replace("T", " ") if ts and ts != "—" else "—"
        st.markdown("**创建时间**")
        st.text(ts)

    # Show contextually complementary navigation button
    current = st.session_state.get("current_page_zh", "")
    if current == "Review Center":
        if st.button("⚙ Running Center", key=f"banner_rc_{rid}", help="返回运行中心查看执行进度"):
            st.session_state["af_stage"] = "running"
            st.session_state["current_page_zh"] = "Analysis Flow"
            st.rerun()
    else:
        pass  # Review Center tab removed
