"""Runs page for ProductInsight Agent.

Shows all runs from DB (API), enriched with local v2.md report info.
Sorted by created_at descending — newest first.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from frontend.common.api import get_json
from frontend.common.config import API_BASE

_DAG_APP_URL = os.environ.get(
    "DAG_APP_URL", "http://localhost:3001"
)
_REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "reports"
)

_MIN_MD_SIZE = 10 * 1024
_REAL_MD_SIZE = 30 * 1024
_WARNING_MARKERS = ("⚠", "证据不足", "预评估阶段")


def _status_label(status: str) -> str:
    color_map = {
        "completed": "green",
        "running": "blue",
        "pending": "gray",
        "failed": "red",
        "cancelled": "orange",
        "paused": "yellow",
        "demo": "purple",
    }
    return f":{color_map.get(status, 'gray')}[{status}]"


def _format_ts(ts) -> str:
    """Convert unix timestamp or ISO string to readable date."""
    if not ts:
        return "—"
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return str(ts)[:16]


def _check_report(run_id: str):
    """Check if report_{run_id}_v2.md exists and get its size + quality."""
    md_path = os.path.join(_REPORTS_DIR, f"report_{run_id}_v2.md")
    if not os.path.exists(md_path):
        return None
    size = os.path.getsize(md_path)
    if size < _MIN_MD_SIZE:
        return {"size": size, "is_real": False, "has_warning": False}
    try:
        head = open(md_path, encoding="utf-8", errors="ignore").read(10 * 1024)
    except Exception:
        head = ""
    has_warning = any(m in head for m in _WARNING_MARKERS)
    is_real = size >= _REAL_MD_SIZE and not has_warning
    return {"size": size, "is_real": is_real, "has_warning": has_warning}


def render_runs_page(run_id: str = None) -> None:
    st.header("Runs")
    st.caption("All runs from DB — sorted by creation time, newest first.")

    # ── Fetch runs from API ────────────────────────────────────────────────────
    all_runs_data = get_json("/api/runs?limit=200")
    # Handle both paginated {"runs": [...], "total": N} and list responses
    if isinstance(all_runs_data, dict):
        raw_runs = all_runs_data.get("runs", all_runs_data.get("data", []))
    elif isinstance(all_runs_data, list):
        raw_runs = all_runs_data
    else:
        raw_runs = []
    # Each item may be a dict or a bare run_id string
    all_runs = [
        r if isinstance(r, dict) else {"run_id": str(r)}
        for r in raw_runs
    ]
    # ── Enrich each run with local report info ────────────────────────────────
    enriched = []
    for r in all_runs:
        rid = r.get("run_id", "")
        report_info = _check_report(rid)
        enriched.append({
            **r,
            "_report": report_info,
            "_md_kb": (report_info["size"] // 1024) if report_info else 0,
            "_is_real": report_info["is_real"] if report_info else False,
        })

    # Sort newest first by created_at
    enriched.sort(key=lambda r: r.get("created_at") or 0, reverse=True)

    # Collect all distinct project_ids from runs (DB column + task_brief fallback)
    project_ids: set = set()
    for r in all_runs:
        pid = r.get("project_id")
        if not pid:
            pid = (r.get("task_brief") or {}).get("project_id")
        if pid:
            project_ids.add(pid)
    project_ids = sorted(project_ids)
    project_options = ["all"] + project_ids
    default_project_idx = 0
    if project_ids:
        # Respect query param first, then session state
        init_project = st.query_params.get("project_id", st.session_state.get("_runs_project_filter", "all"))
        if init_project in project_options:
            default_project_idx = project_options.index(init_project)

    selected_project = st.selectbox(
        "Project",
        options=project_options,
        index=default_project_idx,
    )
    if selected_project != "all":
        st.query_params["project_id"] = selected_project
    else:
        st.query_params.pop("project_id", None)
    st.session_state["_runs_project_filter"] = selected_project

    # ── Filters ───────────────────────────────────────────────────────────────
    status_filter = st.selectbox(
        "Status",
        ["all", "completed", "running", "pending", "failed", "cancelled"],
    )
    quality_filter = st.radio(
        "Quality",
        ["all", "has v2 report", "real only"],
        horizontal=True,
    )

    filtered = enriched
    if status_filter != "all":
        filtered = [r for r in filtered if r.get("status") == status_filter]
    if quality_filter == "has v2 report":
        filtered = [r for r in filtered if r.get("_report") is not None]
    elif quality_filter == "real only":
        filtered = [r for r in filtered if r.get("_is_real")]
    if selected_project != "all":
        filtered = [
            r for r in filtered
            if (r.get("project_id") or (r.get("task_brief") or {}).get("project_id")) == selected_project
        ]

    real_count = sum(1 for r in filtered if r.get("_is_real"))
    report_count = sum(1 for r in filtered if r.get("_report") is not None)
    st.info(
        f"{len(filtered)} runs — "
        f"{real_count} real (>=30KB, no ⚠), "
        f"{report_count} with v2 report, "
        f"{len(filtered) - report_count} no v2 report"
    )

    if not filtered:
        st.warning("No runs match.")
        return

    # ── Table header ─────────────────────────────────────────────────────────
    cols = st.columns([2, 2, 2, 1, 1, 1, 1, 1, 2])
    for c, h in zip(cols, ["Run ID", "Mode", "Project", "Status", "Created", "MD Size", "Quality", "DAG", "Report"]):
        c.markdown(f"**{h}**")
    st.markdown("")

    # ── Rows ──────────────────────────────────────────────────────────────────
    for r in filtered:
        rid = r.get("run_id", "")
        mode = r.get("mode", "—")
        status = r.get("status", "—")
        created = _format_ts(r.get("created_at"))
        md_kb = r.get("_md_kb", 0)
        is_real = r.get("_is_real", False)
        has_report = r.get("_report") is not None
        proj_id = r.get("project_id") or (r.get("task_brief") or {}).get("project_id", "—")

        with st.container():
            row_cols = st.columns([2, 2, 2, 1, 1, 1, 1, 1, 2])

            row_cols[0].code(rid[:18] + "..." if len(rid) > 18 else rid)
            row_cols[1].text(mode)

            # Project column: clickable link that navigates to Project Workspace for this run's project
            if proj_id:
                proj_short = proj_id[:16] + "..."
                row_cols[2].markdown(
                    f'{proj_short}  <a href="?project_id={proj_id}" style="font-size:11px;color:#0083f8;">[open]</a>',
                    unsafe_allow_html=True,
                )
            else:
                row_cols[2].text("—")

            row_cols[3].markdown(_status_label(status))
            row_cols[4].text(created)
            if has_report:
                row_cols[5].markdown(
                    f":green[**{md_kb}KB**]" if is_real else f":orange[{md_kb}KB]"
                )
            else:
                row_cols[5].text("—")
            badge = "🏆" if is_real else ("⚠" if has_report else "—")
            row_cols[6].text(badge)

            dag_url = f"{_DAG_APP_URL}/?run_id={rid}"
            row_cols[7].markdown(
                f'<a href="{dag_url}" target="_blank" style="display:inline-block;padding:3px 7px;background:#0083f8;color:white;border-radius:4px;font-size:12px;text-decoration:none;">DAG</a>',
                unsafe_allow_html=True,
            )
            row_cols[8].markdown(
                f'<a href="?viewer={rid}" style="display:inline-block;padding:3px 7px;background:#00a060;color:white;border-radius:4px;font-size:12px;text-decoration:none;">Report</a>',
                unsafe_allow_html=True,
            )
        st.divider()
