from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from backend.app.storage.repositories import ReportRepository


router = APIRouter(tags=["reports"])

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _find_latest_md_path(run_id: str):
    """Find the latest Markdown file for a run_id.
    Returns the absolute path of the highest version, else None.
    Finds report_{run_id}_v{N}.md where N is highest.
    """
    reports_dir = _PROJECT_ROOT / "data" / "reports"
    if not reports_dir.exists():
        return None

    candidates = []
    for f in reports_dir.iterdir():
        if f.is_file() and f.name.startswith(f"report_{run_id}_v") and f.suffix == ".md":
            # Extract version number
            name = f.name[len(f"report_{run_id}_v"):-3]
            try:
                ver = int(name)
                candidates.append((ver, f))
            except ValueError:
                pass

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _find_v2_md_path(run_id: str):
    """Find the latest Markdown file for a run_id.
    Returns the absolute path of the highest version.
    Falls back to v2 if no versioned files found.
    """
    md_path = _PROJECT_ROOT / "data" / "reports" / f"report_{run_id}_v2.md"
    if md_path.exists():
        # Override: always serve the latest version, not v2
        latest = _find_latest_md_path(run_id)
        return latest if latest else md_path
    return _find_latest_md_path(run_id)


@router.get("/api/runs/{run_id}/report")
def get_report(run_id: str) -> dict:
    report = ReportRepository().get_report(run_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/api/runs/{run_id}/report/md")
def get_report_md(run_id: str):
    """Serve the v2 Markdown report directly.
    Returns 404 if no report_{run_id}_v2.md exists.
    The browser renders this as Markdown natively.
    """
    md_path = _find_v2_md_path(run_id)
    if md_path is None:
        raise HTTPException(status_code=404, detail="v2 Markdown report not found")
    return PlainTextResponse(
        md_path.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/api/runs/{run_id}/report/json")
def get_report_json(run_id: str):
    """Serve the v2 JSON sidecar directly.
    Returns 404 if no report_{run_id}_v2.json exists.
    """
    json_path = _PROJECT_ROOT / "data" / "reports" / f"report_{run_id}_v2.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="JSON report not found")
    return FileResponse(json_path, media_type="application/json")


@router.get("/api/runs/{run_id}/report/html")
def get_report_html(run_id: str):
    # P0: Check v2 file system first (authoritative source)
    md_path = _find_v2_md_path(run_id)
    if md_path is not None:
        return PlainTextResponse(
            md_path.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
        )

    # Fallback: ReportRepository (v1 reports stored only in DB)
    report = ReportRepository().get_report(run_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    html_path_str = report.get("content_html_path", "")
    if not html_path_str:
        raise HTTPException(status_code=404, detail="HTML report not found")
    full_path = (_PROJECT_ROOT / html_path_str).resolve()
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"HTML file not found: {html_path_str}")
    return FileResponse(full_path, media_type="text/html; charset=utf-8")
