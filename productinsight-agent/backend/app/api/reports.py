from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from backend.app.storage.repositories import ReportRepository
from backend.app.services.deep_report import _collapse_table_blank_lines, _markdown_to_html


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
    """Serve the v2 Markdown report rendered as HTML.

    P0-7 Fix: Browsers (including iframes inside Streamlit) do NOT render
    markdown natively. Previously this endpoint returned
    `text/markdown; charset=utf-8` and the browser displayed raw pipe
    characters, broken tables, and pipe-escaped cell text. It now:

      1. Runs the markdown through _collapse_table_blank_lines so any
         intra-table blanks (a known generator bug) cannot break a table.
      2. Runs it through _markdown_to_html to produce real <table> elements.
      3. Wraps the HTML in a minimal stylesheet so the iframe renders
         legibly without depending on the host page.
    """
    # P0: Check v2 file system first (authoritative source)
    md_path = _find_v2_md_path(run_id)
    if md_path is not None:
        md_text = md_path.read_text(encoding="utf-8")
        # Step 1: collapse any in-table blank lines so the markdown is GFM-clean.
        md_text = _collapse_table_blank_lines(md_text)
        # Step 2: render to HTML.
        body_html = _markdown_to_html(md_text)
        # Step 3: wrap with a minimal inline stylesheet for iframe display.
        html_doc = _wrap_markdown_html(body_html, title=f"Report {run_id}")
        return HTMLResponse(content=html_doc)

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


_HTML_DOC_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<style>
  body {{
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI",
                 Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1a202c; line-height: 1.65; padding: 16px 24px; max-width: 1200px;
    margin: 0 auto; background: #ffffff;
  }}
  h1, h2, h3, h4 {{ color: #1a202c; margin-top: 1.4em; margin-bottom: 0.6em; }}
  h1 {{ font-size: 1.7em; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
  h2 {{ font-size: 1.35em; border-bottom: 1px solid #edf2f7; padding-bottom: 4px; }}
  h3 {{ font-size: 1.15em; }}
  p  {{ margin: 0.6em 0; }}
  ul, ol {{ padding-left: 1.6em; }}
  li {{ margin: 0.25em 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0;
           font-size: 0.95em; }}
  th, td {{ border: 1px solid #cbd5e0; padding: 8px 12px; text-align: left;
            vertical-align: top; }}
  th {{ background: #f7fafc; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #f9fafb; }}
  blockquote {{ border-left: 4px solid #cbd5e0; padding: 4px 12px;
                color: #4a5568; background: #f7fafc; margin: 12px 0; }}
  code {{ background: #f1f5f9; padding: 1px 4px; border-radius: 3px;
          font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
          font-size: 0.92em; }}
  pre  {{ background: #1a202c; color: #f7fafc; padding: 12px;
          border-radius: 6px; overflow-x: auto; }}
  strong {{ color: #2d3748; }}
  .table-container {{ overflow-x: auto; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _wrap_markdown_html(body_html: str, title: str = "Report") -> str:
    return _HTML_DOC_TEMPLATE.format(title=title, body=body_html)
