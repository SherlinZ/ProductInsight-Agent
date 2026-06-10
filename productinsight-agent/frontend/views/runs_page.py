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
    "DAG_APP_URL", "http://172.18.40.105:3001"
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
    cols = st.columns([2, 2, 1, 1, 1, 1, 2])
    for c, h in zip(cols, ["Run ID", "Mode", "Status", "Created", "MD Size", "Quality", "Actions"]):
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

        with st.container():
            row_cols = st.columns([2, 2, 1, 1, 1, 1, 2])

            row_cols[0].code(rid[:18] + "..." if len(rid) > 18 else rid)
            row_cols[1].text(mode)
            row_cols[2].markdown(_status_label(status))
            row_cols[3].text(created)
            if has_report:
                row_cols[4].markdown(
                    f":green[**{md_kb}KB**]" if is_real else f":orange[{md_kb}KB]"
                )
            else:
                row_cols[4].text("—")
            badge = "🏆" if is_real else ("⚠" if has_report else "—")
            row_cols[5].text(badge)

            dag_url = f"{_DAG_APP_URL}/?run_id={rid}"
            row_cols[6].markdown(
                f'<a href="{dag_url}" target="_blank" style="display:inline-block;padding:3px 7px;background:#0083f8;color:white;border-radius:4px;font-size:12px;text-decoration:none;margin-right:4px;">DAG</a>'
                f'<a href="?viewer={rid}" style="display:inline-block;padding:3px 7px;background:#00a060;color:white;border-radius:4px;font-size:12px;text-decoration:none;">Report</a>',
                unsafe_allow_html=True,
            )
        st.divider()
