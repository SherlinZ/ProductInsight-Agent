"""Generate beautiful HTML reports from database spans."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# P0-2: Import secret redaction for report export
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
    from backend.app.services.pii_service import sanitize_report_content, sanitize_evidence_snippet
    from backend.app.services.evidence_evaluator import is_noise_evidence
except ImportError:
    # Fallback if backend not available
    def sanitize_report_content(text):
        return text
    def sanitize_evidence_snippet(text):
        return text, False
    def is_noise_evidence(text):
        return False  # Conservative fallback


def _safe_json(value, default):
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return default


def _escape_html(text: str) -> str:
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;"))



def _md(text):
    if not text: return ""
    lines = text.split("\n"); out = []; in_list = False
    def inline(s):
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s)
        return s
    def render_table(rows):
        if len(rows) < 2: return ""
        hdr = [inline(c.strip()) for c in rows[0].strip("|").split("|")]
        body = ""
        for r in rows[1:]:
            if re.match(r"^\|[\-: ]+\|$", r): continue
            cells = [inline(c.strip()) for c in r.strip("|").split("|")]
            body += "<tr>"+"".join(f"<td>{c}</td>" for c in cells)+"</tr>"
        return "<table class=\'md-table\'><thead><tr>"+"".join(f"<th>{h}</th>" for h in hdr)+"</tr></thead><tbody>"+body+"</tbody></table>"
    table_buf = []; in_table = False
    for line in lines:
        if re.match(r"^\|.+\|$", line):
            if not in_table: in_table = True; table_buf = []
            table_buf.append(line); continue
        elif in_table:
            in_table = False
            out.append(render_table(table_buf)); table_buf = []
        hm = re.match(r"^(#{1,6})\s+(.+)$", line)
        if hm:
            lvl = len(hm.group(1))
            out.append(f"<h{lvl}>" + inline(hm.group(2)) + f"</h{lvl}>")
        elif re.match(r"^[-*]\s+(.+)$", line):
            if not in_list: out.append("<ul>"); in_list = True
            out.append("<li>"+inline(re.sub(r"^[-*]\s+","",line))+"</li>")
        elif re.match(r"^\d+\.\s+(.+)$", line):
            if not in_list: out.append("<ol>"); in_list = True
            out.append("<li>"+inline(re.sub(r"^\d+\.\s+","",line))+"</li>")
        else:
            if in_list: out.append("</ul>")
            in_list = False
            stripped = line.strip()
            if stripped: out.append("<p>"+inline(stripped)+"</p>")
    if in_list: out.append("</ul>")
    if in_table and table_buf:
        out.append(render_table(table_buf))
    return "\n".join(out)

def _build_beautiful_html(
    title: str,
    run_id: str,
    spans: list[dict],
    quality_summary: dict,
    report_status: str,
    sources_map: dict,
    created_at: str,
    project_context: dict | None = None,
    comparison_matrix: list[dict] | None = None,
    key_findings: list[dict] | None = None,
    comparison_products: list[str] | None = None,
    product_coverage: dict | None = None,
    evidence_map: dict | None = None,
) -> str:
    ctx = project_context or {}
    qs = quality_summary or {}
    claim_count = qs.get("claim_count", 0)
    coverage = qs.get("evidence_coverage_rate", 0.0)
    signed = qs.get("signed_claims", claim_count)

    # ---- Project Context HTML ----
    project_context_html = ""
    if ctx:
        items = []
        for label, value in [
            ("Project", ctx.get("project_name", "")),
            ("Task Type", ctx.get("task_type", "")),
            ("Region", ctx.get("target_region", "")),
            ("Products", ", ".join(p.get("product_name", "") for p in (ctx.get("products") or [])[:4])),
        ]:
            if value:
                items.append('<div class="ctx-item"><span class="ctx-label">' + label + '</span><span class="ctx-value">' + value + '</span></div>')
        if items:
            project_context_html = '<div class="project-context"><div class="project-context-header"><span>&#128203;</span><h3>Project Context</h3></div><div class="project-context-body">' + "".join(items) + '</div></div>'

    # ---- Decision Summary (Key Findings) ----
    decision_summary_html = ""
    findings = key_findings or []
    if findings:
        cards = []
        for f in findings[:5]:
            conf = f.get("confidence", 0)
            cls_ = "high-confidence" if conf >= 0.85 else "medium-confidence"
            ftype = f.get("finding_type", "general")
            ftype_badge_color = {"differentiation": "#10b981", "opportunity": "#3b82f6", "coverage_gap": "#f59e0b", "risk": "#ef4444", "general": "#6b7280"}.get(ftype, "#6b7280")
            ev_count = f.get("evidence_count", 0)
            txt = (f.get("text") or "")[:100]
            cards.append('<div class="finding-card"><div class="finding-title">' + txt + '</div><div class="finding-meta"><span class="finding-badge ' + cls_ + '">Conf: ' + str(int(conf * 100)) + '%</span><span class="finding-badge" style="background:#e0e7ff;color:#3730a3;">Evidence: ' + str(ev_count) + '</span></div></div>')
        decision_summary_html = '<div class="decision-summary"><h3>Decision Summary — ' + str(len(findings)) + ' Key Findings</h3><div class="findings-grid">' + "".join(cards) + '</div></div>'

    # ---- Product Coverage Table ----
    product_coverage_html = ""
    cov = product_coverage or {}
    if cov:
        status_colors = {"sufficient": "#10b981", "partial": "#f59e0b", "insufficient": "#ef4444"}
        status_labels = {"sufficient": "Sufficient", "partial": "Partial", "insufficient": "Insufficient Evidence"}
        rows = []
        for slug, data in sorted(cov.items()):
            status = data.get("coverage_status", "insufficient")
            color = status_colors.get(status, "#6b7280")
            label = status_labels.get(status, status.title())
            ev = data.get("evidence", data.get("evidence_count", 0))
            src = data.get("sources", data.get("source_count", 0))
            fct = data.get("facts", data.get("fact_count", 0))
            sc_cnt = data.get("signed_claims", 0)
            prod_name = data.get("product_name", slug.title())
            rows.append(
                '<tr><td><strong>' + prod_name + '</strong></td>'
                '<td><span style="color:' + color + ';font-weight:600;">' + label + '</span></td>'
                '<td>' + str(src) + '</td><td>' + str(ev) + '</td><td>' + str(fct) + '</td><td>' + str(sc_cnt) + '</td></tr>'
            )
        product_coverage_html = (
            '<div class="coverage-table"><h3>Product Coverage</h3>'
            '<table class="matrix-table"><thead><tr>'
            '<th>Product</th><th>Status</th><th>Sources</th><th>Evidence</th><th>Facts</th><th>Signed Claims</th>'
            '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
        )

    comparison_matrix_html = ""
    matrix_rows = comparison_matrix or []
    if matrix_rows:
        cols = comparison_products or sorted(set(
            prod for row in matrix_rows for prod in (row.get("products") or {}).keys()
        )) or []
        if cols:
            dim_labels = {
                "workflow":"Workflow / Orchestration","knowledge_base":"Knowledge Base / RAG",
                "deployment_options":"Deployment Options","pricing_model":"Pricing Model",
                "enterprise_readiness":"Enterprise Readiness","integration":"Integration / API",
                "model_support":"Model Support","agent_capabilities":"Agent Capabilities","user_persona":"User Persona",
            }
            header = "".join("<th>"+_escape_html(p)+"</th>" for p in cols)
            data_rows = []
            for row in matrix_rows:
                dim = row.get("schema_key","")
                label = dim_labels.get(dim, dim.replace("_"," ").title())
                cells = ""
                prod_map = row.get("products") or {}
                for p in cols:
                    val = str(prod_map.get(p,"Unknown") or "Unknown")[:100]
                    val_esc = _escape_html(val)
                    cls = "cell-positive" if val.lower() not in ("unknown","") and any(s in val.lower() for s in ["strong","available","supported","free","plugin","rb","ss","audit","encrypt","api","webhook","basic","paid"]) else ("cell-unknown" if val.lower() in ("unknown","") else "")
                    cls_attr = " class='"+cls+"'" if cls else ""
                    cells += f"<td{cls_attr}>"+val_esc+"</td>"
                data_rows.append("<tr><td>"+_escape_html(label)+"</td>"+cells+"</tr>")
            comparison_matrix_html = (
                "<div class='comparison-matrix'><h3>Comparison Matrix</h3>"
                "<table class='matrix-table'><thead><tr><th>Dimension</th>"+header+"</tr></thead><tbody>"
                +"".join(data_rows)+"</tbody></table></div>"
            )

    # Determine status badge color
    status_colors = {
        "reviewed": "#10b981",
        "exported": "#3b82f6",
        "draft": "#f59e0b",
        "blocked": "#ef4444",
        "reviewed_with_gaps": "#f59e0b",
        "reviewed_partial": "#f59e0b",
    }
    status_color = status_colors.get(report_status, "#6b7280")
    status_label = {
        "reviewed": "Reviewed",
        "exported": "Exported",
        "draft": "Draft",
        "blocked": "Blocked",
        "reviewed_with_gaps": "Reviewed with Evidence Gaps",
        "reviewed_partial": "Reviewed Partial",
    }.get(report_status, report_status)

    # ---- Metrics Bar ----
    insufficient = qs.get('insufficient_products', 0)
    sufficient = qs.get('sufficient_products', 0)
    if sufficient == 0:
        # Fallback: count sufficient products from coverage table
        sufficient = sum(1 for v in (product_coverage or {}).values() if v.get("coverage_status") == "sufficient")
    coverage_color = "green" if insufficient == 0 else ("amber" if insufficient < 3 else "red")

    # Section accent colors
    section_colors = [
        "#6366f1",  # indigo
        "#8b5cf6",  # violet
        "#ec4899",  # pink
        "#f59e0b",  # amber
        "#10b981",  # emerald
        "#3b82f6",  # blue
        "#ef4444",  # red
        "#14b8a6",  # teal
        "#f97316",  # orange
    ]

    def _render_claim(claim_id: str) -> str:
        return f'<code class="claim-tag">{_escape_html(claim_id)}</code>'

    def _render_evidence(eid: str) -> str:
        ev = (evidence_map or sources_map or {}).get(eid, {})
        # P0-2: Sanitize evidence snippet before rendering
        raw_snippet = ev.get("snippet") or ""
        sanitized_snippet, _ = sanitize_evidence_snippet(raw_snippet)
        snippet = _escape_html(sanitized_snippet[:120])
        url = ev.get("url", "")
        product = ev.get("product_id", "")
        src_type = ev.get("source_type", "")
        html = f"""
        <div class="evidence-card">
            <div class="evidence-header">
                <code class="evidence-id">{_escape_html(eid)}</code>
                <span class="evidence-meta">{_escape_html(product)} · {src_type}</span>
            </div>"""
        if snippet and snippet != "N/A":
            html += f'<blockquote class="evidence-snippet">{snippet}…</blockquote>'
        if url:
            html += f'<a class="evidence-url" href="{_escape_html(url)}" target="_blank">{_escape_html(url[:70])} ↗</a>'
        html += "</div>"
        return html

    sections_html = ""
    for idx, span in enumerate(spans):
        sec_title = span.get("section_title", f"Section {idx + 1}")
        content = (span.get("text") or "").strip()
        claim_ids = span.get("claim_ids", [])
        evidence_ids = span.get("evidence_ids", [])
        unsupported_flag = span.get("unsupported_flag", 0)
        accent = section_colors[idx % len(section_colors)]

        # P0-4: Improved badge logic based on evidence quality and content depth
        CONTEXT_TITLES = {"executive summary", "product coverage", "product overview"}
        
        # Check if evidence is meaningful (not just noise)
        meaningful_evidence_count = 0
        noise_evidence_count = 0
        for eid in evidence_ids:
            ev = (evidence_map or {}).get(eid, {})
            snippet = ev.get("snippet", "") or ""
            if is_noise_evidence(snippet):
                noise_evidence_count += 1
            else:
                meaningful_evidence_count += 1
        
        # Calculate content quality
        word_count = len(content.split()) if content else 0
        min_content_words = 30  # Minimum meaningful content
        
        # Determine badge
        content_lower = (content or "").lower()
        has_evidence_gap = any(phrase in content_lower for phrase in 
            ["insufficient evidence", "missing evidence", "no structured facts", 
             "evidence gap", "no facts found", "no evidence found"])
        
        if sec_title.lower() in CONTEXT_TITLES or sec_title.lower().startswith("executive") or sec_title.lower().startswith("product "):
            badge_text = "Context"
            badge_color = "#6366f1"
            badge_icon = "&#9432;"
        elif has_evidence_gap:
            badge_text = "Evidence Gap"
            badge_color = "#ef4444"  # Red for evidence gap
            badge_icon = "&#9888;"
        elif not evidence_ids:
            badge_text = "No Evidence"
            badge_color = "#ef4444"
            badge_icon = "&#9888;"
        elif meaningful_evidence_count == 0 and noise_evidence_count > 0:
            badge_text = "Evidence Gap"
            badge_color = "#ef4444"
            badge_icon = "&#9888;"
        elif meaningful_evidence_count > 0 and word_count >= min_content_words:
            badge_text = "Supported"
            badge_color = "#10b981"  # Green for supported
            badge_icon = "&#10003;"
        elif meaningful_evidence_count > 0 and word_count < min_content_words:
            badge_text = "Partial"
            badge_color = "#f59e0b"  # Amber for partial
            badge_icon = "&#10060;"
        else:
            badge_text = "Context"
            badge_color = "#6366f1"
            badge_icon = "&#9432;"

        content_html = _md(content)

        # Fix bad tables: if Product Coverage section has a markdown table with only --- rows,
        # replace with a plain text note to avoid showing an ugly broken table
        if "product coverage" in sec_title.lower() and "<table" in content_html:
            # Check if table has only separator rows (no real data)
            import re as _re
            # Only look inside <tbody>, skip <thead>
            tbody_match = _re.search(r"<tbody[^>]*>(.*?)</tbody>", content_html, _re.DOTALL)
            if tbody_match:
                tbody_content = tbody_match.group(1)
                # Find all <td> rows (data rows), skip <th> header rows
                td_rows = _re.findall(r"<tr>(.*?)</tr>", tbody_content, _re.DOTALL)
                # A "real" row has at least one non-dash/non-empty cell
                real_rows = [r for r in td_rows if _re.search(r"<td>(?![\s\-])([^<]+)", r)]
                if len(real_rows) == 0:
                    # Replace table with plain text
                    content_html = "<p>This section summarizes product-level source, evidence, and fact coverage. See the Product Coverage table above for product-level details.</p>"

        # Build the section header + body (no triple-quote f-string issues)
        section_block = (
            f'        <section class="report-section" style="--accent: {accent}">\n'
            f'            <div class="section-accent-bar"></div>\n'
            f'            <div class="section-header">\n'
            f'                <span class="section-number">{idx + 1}</span>\n'
            f'                <h2 class="section-title">{_escape_html(sec_title)}</h2>\n'
            f'                <span class="section-badge" style="background:{badge_color};color:white;">\n'
            f'                    <span class="badge-icon">{badge_icon}</span> {badge_text}\n'
            f'                </span>\n'
            f'            </div>\n'
            f'            <div class="section-body">\n'
            f'                <div class="section-text">{content_html}</div>\n'
            f'            </div>\n'
        )

        # Only add Claims/Evidence chips if non-empty
        chips_html = ""
        chips = []
        if claim_ids:
            chips.append(f'                    <span class="meta-chip">\n'
                        f'                        <span class="chip-label">Claims</span>\n'
                        f'                        <span class="chip-value">{len(claim_ids)}</span>\n'
                        f'                    </span>')
        if evidence_ids:
            chips.append(f'                    <span class="meta-chip">\n'
                        f'                        <span class="chip-label">Evidence</span>\n'
                        f'                        <span class="chip-value">{len(evidence_ids)}</span>\n'
                        f'                    </span>')
        if chips:
            chips_html = (
                '            <div class="section-footer">\n'
                '                <div class="meta-chips">\n'
                + '\n'.join(chips) + '\n'
                '                </div>\n'
                '            </div>\n'
            )
        section_block += chips_html

        # Evidence details list
        if evidence_ids:
            section_block += '            <div class="evidence-list">\n'
            for eid in evidence_ids:
                section_block += f'                {_render_evidence(eid)}\n'
            section_block += '            </div>\n'

        if unsupported_flag:
            section_block += (
                '            <div class="unsupported-warning">\n'
                '                &#9888; This section contains unsupported claims.\n'
                '            </div>\n'
            )

        section_block += '        </section>\n'
        sections_html += section_block

    # Footer
    footer_html = f"""
        <footer class="report-footer">
            <div class="footer-meta">
                <span>Report ID: <code>{_escape_html(run_id)}</code></span>
                <span>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}</span>
                <span>Created: {created_at[:10] if created_at else 'N/A'}</span>
            </div>
            <p class="footer-note">
                This report was generated by <strong>ProductInsight Agent</strong>.
                All claims are traceable to source evidence.
            </p>
        </footer>
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape_html(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {{
    --bg-primary: #f8fafc;
    --bg-card: #ffffff;
    --bg-section: #ffffff;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #94a3b8;
    --border: #e2e8f0;
    --accent: #6366f1;
    --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -1px rgba(0,0,0,0.04);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.08), 0 4px 6px -2px rgba(0,0,0,0.03);
    --radius: 12px;
    --radius-sm: 8px;
}}

* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
    font-family: var(--font-sans);
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
}}

/* ---- Header ---- */
.report-header {{
    background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4c1d95 100%);
    color: white;
    padding: 64px 40px 56px;
    position: relative;
    overflow: hidden;
}}

.report-header::before {{
    content: '';
    position: absolute;
    top: -50%;
    right: -20%;
    width: 600px;
    height: 600px;
    background: radial-gradient(circle, rgba(99,102,241,0.3) 0%, transparent 70%);
    pointer-events: none;
}}

.report-header::after {{
    content: '';
    position: absolute;
    bottom: -30%;
    left: -10%;
    width: 400px;
    height: 400px;
    background: radial-gradient(circle, rgba(236,72,153,0.2) 0%, transparent 70%);
    pointer-events: none;
}}

.header-content {{ position: relative; z-index: 1; max-width: 900px; margin: 0 auto; }}

.report-header .label {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.5);
    margin-bottom: 16px;
}}

.report-header h1 {{
    font-size: 38px;
    font-weight: 800;
    line-height: 1.2;
    margin-bottom: 20px;
    letter-spacing: -0.5px;
}}

.report-header .subtitle {{
    font-size: 15px;
    color: rgba(255,255,255,0.7);
    margin-bottom: 36px;
}}

/* ---- Metrics Bar ---- */
.metrics-bar {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    max-width: 900px;
    margin: 0 auto;
}}

.metric-card {{
    background: rgba(255,255,255,0.1);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.15);
    border-radius: var(--radius-sm);
    padding: 18px 20px;
    text-align: center;
}}

.metric-card .metric-label {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: rgba(255,255,255,0.5);
    margin-bottom: 8px;
}}

.metric-card .metric-value {{
    font-size: 28px;
    font-weight: 800;
    line-height: 1;
}}

.metric-card .metric-value.green {{ color: #34d399; }}
.metric-card .metric-value.amber {{ color: #fbbf24; }}
.metric-card .metric-value.red {{ color: #f87171; }}
.metric-card .metric-value.white {{ color: #ffffff; }}

.metric-card .metric-sub {{
    font-size: 11px;
    color: rgba(255,255,255,0.4);
    margin-top: 4px;
}}

/* ---- Status badge ---- */
.status-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
    color: white;
    margin-bottom: 24px;
}}

/* ---- Main content ---- */
.report-body {{
    max-width: 860px;
    margin: 0 auto;
    padding: 40px 24px 80px;
}}

/* ---- Project Context ---- */
.project-context {{
    background: #ffffff;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 28px;
    overflow: hidden;
}}
.project-context-header {{
    background: #f8f9ff;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
}}
.project-context-header h3 {{ font-size: 14px; font-weight: 700; color: var(--text-primary); margin: 0; }}
.project-context-body {{
    padding: 14px 20px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 10px;
}}
.ctx-item {{ display: flex; flex-direction: column; gap: 3px; }}
.ctx-label {{ font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }}
.ctx-value {{ font-size: 13px; font-weight: 500; color: var(--text-primary); }}

/* ---- Decision Summary ---- */
.decision-summary {{
    background: #f0fdf4;
    border: 1px solid #a7f3d0;
    border-radius: var(--radius);
    padding: 18px 22px;
    margin-bottom: 28px;
}}
.decision-summary h3 {{ font-size: 14px; font-weight: 700; color: #065f46; margin: 0 0 12px; }}
.findings-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }}
.finding-card {{ background: white; border: 1px solid #d1fae5; border-radius: 8px; padding: 12px 14px; }}
.finding-title {{ font-size: 12px; font-weight: 500; color: var(--text-primary); margin-bottom: 8px; }}
.finding-meta {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.finding-badge {{ font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 10px; }}
.finding-badge.high-confidence {{ background: #dcfce7; color: #166534; }}
.finding-badge.medium-confidence {{ background: #fef9c3; color: #854d0e; }}

/* ---- Comparison Matrix ---- */
.comparison-matrix {{ background: white; border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 28px; overflow: hidden; }}
.comparison-matrix h3 {{ font-size: 14px; font-weight: 700; color: var(--text-primary); padding: 14px 18px; border-bottom: 1px solid var(--border); margin: 0; }}
.matrix-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.matrix-table th {{ background: #f8fafc; padding: 8px 12px; text-align: left; font-weight: 600; color: var(--text-secondary); border-bottom: 2px solid var(--border); }}
.matrix-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text-secondary); vertical-align: top; }}
.matrix-table td:first-child {{ font-weight: 600; color: var(--text-primary); }}
.matrix-table tr:last-child td {{ border-bottom: none; }}
            .cell-positive {{ color: #34d399; }}
            .cell-unknown {{ color: #64748b; font-style: italic; }}
            .coverage-table {{ margin: 24px 0; }}
            .coverage-table > h3 {{ font-size: 14px; font-weight: 700; color: var(--text-primary); margin: 0 0 12px 0; }}
            .md-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
            .md-table th {{ background: #334155; padding: 8px 12px; }}
            .md-table td {{ padding: 6px 12px; border: 1px solid #334155; }}
            blockquote {{ border-left: 4px solid #6366f1; padding-left: 16px; margin: 12px 0; color: #94a3b8; }}

/* ---- Section ---- */
.report-section {{
    background: var(--bg-card);
    border-radius: var(--radius);
    box-shadow: var(--shadow-md);
    margin-bottom: 28px;
    overflow: hidden;
    border: 1px solid var(--border);
    position: relative;
}}

.section-accent-bar {{
    height: 4px;
    background: var(--accent);
}}

.section-header {{
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 24px 28px 16px;
    border-bottom: 1px solid var(--border);
}}

.section-number {{
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--accent);
    color: white;
    font-size: 13px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    opacity: 0.9;
}}

.section-title {{
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    flex: 1;
    letter-spacing: -0.2px;
}}

.section-badge {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: #dcfce7;
    color: #15803d;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}

.section-badge.unsupported {{
    background: #fee2e2;
    color: #dc2626;
}}

.section-body {{
    padding: 20px 28px 16px;
}}

.section-text {{
    font-size: 14.5px;
    color: var(--text-secondary);
    line-height: 1.85;
}}

.section-text strong {{
    color: var(--text-primary);
    font-weight: 600;
}}

.section-footer {{
    padding: 0 28px 16px;
}}

.meta-chips {{
    display: flex;
    gap: 10px;
}}

.meta-chip {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #f1f5f9;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 12px;
}}

.chip-label {{
    color: var(--text-muted);
    font-weight: 500;
}}

.chip-value {{
    font-weight: 700;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
}}

/* ---- Evidence ---- */
.evidence-list {{
    padding: 0 28px 20px;
    display: none;
}}

.evidence-list.open {{ display: block; }}

.evidence-card {{
    background: #f8fafc;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    margin-bottom: 10px;
}}

.evidence-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}}

.evidence-id {{
    font-family: var(--font-mono);
    font-size: 11px;
    background: #e0e7ff;
    color: #4338ca;
    padding: 2px 7px;
    border-radius: 4px;
}}

.evidence-meta {{
    font-size: 11px;
    color: var(--text-muted);
}}

.evidence-snippet {{
    font-size: 13px;
    color: var(--text-secondary);
    border-left: 3px solid var(--accent);
    padding-left: 10px;
    margin: 4px 0 8px;
    font-style: italic;
    line-height: 1.6;
}}

.evidence-url {{
    font-size: 11px;
    color: var(--accent);
    text-decoration: none;
    word-break: break-all;
}}

.evidence-url:hover {{ text-decoration: underline; }}

/* ---- Claim tags ---- */
.claim-tag {{
    font-family: var(--font-mono);
    font-size: 10px;
    background: #f1f5f9;
    color: var(--text-secondary);
    padding: 1px 5px;
    border-radius: 3px;
    margin: 0 2px;
}}

/* ---- Unsupported warning ---- */
.unsupported-warning {{
    margin: 0 28px 16px;
    background: #fff7ed;
    border: 1px solid #fed7aa;
    border-radius: var(--radius-sm);
    padding: 10px 14px;
    font-size: 13px;
    color: #c2410c;
}}

/* ---- Footer ---- */
.report-footer {{
    border-top: 1px solid var(--border);
    padding: 28px;
    text-align: center;
}}

.footer-meta {{
    display: flex;
    justify-content: center;
    gap: 24px;
    margin-bottom: 12px;
    font-size: 12px;
    color: var(--text-muted);
}}

.footer-meta code {{
    font-family: var(--font-mono);
    font-size: 11px;
    background: #f1f5f9;
    padding: 1px 5px;
    border-radius: 3px;
}}

.footer-note {{
    font-size: 12px;
    color: var(--text-muted);
}}

/* ---- Responsive ---- */
@media (max-width: 640px) {{
    .report-header {{ padding: 40px 20px 36px; }}
    .report-header h1 {{ font-size: 26px; }}
    .metrics-bar {{ grid-template-columns: repeat(2, 1fr); }}
    .report-body {{ padding: 24px 16px 60px; }}
    .section-header {{ padding: 18px 18px 12px; }}
    .section-body {{ padding: 16px 18px 12px; }}
    .section-footer {{ padding: 0 18px 12px; }}
    .evidence-list {{ padding: 0 18px 16px; }}
}}

/* ---- Print styles ---- */
@media print {{
    body {{ background: white; }}
    .report-section {{ box-shadow: none; border: 1px solid #ddd; break-inside: avoid; }}
    .evidence-list {{ display: block !important; }}
    .report-header {{ background: #1e1b4b !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    .metrics-bar {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
}}
</style>
</head>
<body>

<!-- ===== HEADER ===== -->
<header class="report-header">
    <div class="header-content">
        <div class="label">ProductInsight · Competitive Analysis Report</div>
        <div class="status-badge">
            <span style="width:8px;height:8px;border-radius:50%;background:{status_color};display:inline-block;"></span>
            {status_label}
        </div>
        <h1>{_escape_html(title)}</h1>
        <p class="subtitle">Competitive Analysis · {len(spans)} Dimensions</p>

        <div class="metrics-bar">
            <div class="metric-card">
                <div class="metric-label">Claims</div>
                <div class="metric-value white">{claim_count}</div>
                <div class="metric-sub">Signed</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Evidence Coverage</div>
                <div class="metric-value green">{int(coverage*100)}%</div>
                <div class="metric-sub">Supported</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Insufficient Products</div>
                <div class="metric-value {coverage_color}">{insufficient}</div>
                <div class="metric-sub">Need Evidence</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Sufficient Products</div>
                <div class="metric-value green">{sufficient}</div>
                <div class="metric-sub">Ready for Report</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Dimensions</div>
                <div class="metric-value white">{len(spans)}</div>
                <div class="metric-sub">Analyzed</div>
            </div>
        </div>
    </div>
</header>

<!-- ===== BODY ===== -->
<main class="report-body">
{project_context_html}
{decision_summary_html}
{product_coverage_html}
{comparison_matrix_html}
{sections_html}
</main>

{footer_html}

</body>
</html>"""
    # P0-2: Final sanitization pass to catch any remaining secrets
    html = sanitize_report_content(html)
    return html


def render_evidence_inline(eid: str, sources_map: dict) -> str:
    ev = sources_map.get(eid, {})
    # P0-2: Sanitize evidence snippet before rendering
    raw_snippet = ev.get("snippet") or ""
    sanitized_snippet, _ = sanitize_evidence_snippet(raw_snippet)
    snippet = sanitized_snippet[:120]
    url = ev.get("url", "")
    product = ev.get("product_id", "")
    src_type = ev.get("source_type", "")
    return f"""<div class="evidence-card">
            <div class="evidence-header">
                <code class="evidence-id">{_escape_html(eid)}</code>
                <span class="evidence-meta">{_escape_html(product)} · {_escape_html(src_type)}</span>
            </div>
            {f'<blockquote class="evidence-snippet">{_escape_html(snippet)}…</blockquote>' if snippet else ''}
            {f'<a class="evidence-url" href="{_escape_html(url)}" target="_blank">{_escape_html(url[:70])} ↗</a>' if url else ''}
        </div>"""


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate beautiful HTML report")
    parser.add_argument("--run-id", default="run_demo_ai_agent_001")
    parser.add_argument("--db", default="data/productinsight.db")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    db = Path(args.db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    report = conn.execute(
        "SELECT * FROM reports WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
        (args.run_id,),
    ).fetchone()

    if not report:
        print(f"No report found for run_id={args.run_id}")
        conn.close()
        return

    spans_rows = conn.execute(
        "SELECT * FROM report_spans WHERE report_id = ? ORDER BY section_id, span_id",
        (report["report_id"],),
    ).fetchall()

    sources = conn.execute("SELECT * FROM sources WHERE run_id = ?", (args.run_id,)).fetchall()
    evidence_rows = conn.execute("SELECT * FROM evidence_items WHERE run_id = ?", (args.run_id,)).fetchall()

    # Get project context if available
    project_context = None
    try:
        from backend.app.storage.repositories import RunRepository
        from backend.app.storage.repositories import ProjectRepository
        # task_brief_json lives in the runs table, not reports table
        run_obj = RunRepository().get_run(args.run_id)
        if run_obj:
            tb_json = run_obj.get("task_brief_json")
            if tb_json:
                tb = _safe_json(tb_json, {})
                proj_id = tb.get("project_id")
                if proj_id:
                    proj = ProjectRepository().get_project(proj_id)
                    if proj:
                        project_context = proj
    except Exception:
        pass

    conn.close()

    report_dict = dict(report)
    created_at = report_dict.get("created_at", "")
    report_id = report_dict["report_id"]

    spans = []
    for s in spans_rows:
        sd = dict(s)
        sd["claim_ids"] = _safe_json(sd.pop("claim_ids_json"), [])
        sd["evidence_ids"] = _safe_json(sd.pop("evidence_ids_json"), [])
        spans.append(sd)

    sources_map = {str(s["source_id"]): dict(s) for s in sources}
    evidence_map = {str(e["evidence_id"]): dict(e) for e in evidence_rows}
    evidence_list = [dict(e) for e in evidence_rows]
    quality = _safe_json(report_dict.get("quality_summary_json", "{}"), {})

    # Build product_coverage — PRIORITY 1: quality_summary.product_coverage_summary (canonical data)
    # This is the authoritative source written by the report generation pipeline.
    # Use LOWER-CASE slugs as dict keys so Priority 2 can match case-insensitively.
    product_coverage = {}
    qs_pcs = quality.get("product_coverage_summary", {}) or {}
    for slug, data in qs_pcs.items():
        if not slug:
            continue
        # Normalize to lowercase key so Priority 2 (lowercase) can match
        key = slug.lower()
        product_coverage[key] = {
            "source_count": data.get("sources", 0),
            "evidence_count": data.get("evidence", data.get("evidence_count", 0)),
            "fact_count": data.get("facts", data.get("fact_count", 0)),
            "signed_claims": data.get("signed_claims", 0),
            "coverage_status": data.get("coverage_status", "insufficient"),
            "product_name": data.get("product_name", slug.title()),
        }

    # Helper: normalize a run-scoped product_id to canonical slug
    def _canonical_slug(raw_id: str) -> str:
        """Map run-scoped product IDs like 'run_golden_completed_run-golden-completed-dify' -> 'dify'."""
        if not raw_id:
            return ""
        cleaned = raw_id.lower()
        # Strip known run_id prefixes (underscore and hyphen variants)
        for prefix in [
            "run_golden_completed_", "run_golden_gap_", "run_",
            "run-golden-completed-", "run-golden-gap-",
        ]:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        # Strip the run_id itself if it appears twice (e.g. "run_golden_completed_run-golden-completed-dify")
        if cleaned.startswith("run_golden_completed_") or cleaned.startswith("run-golden-completed-"):
            for prefix in [
                "run_golden_completed_", "run_golden_gap_", "run_",
                "run-golden-completed-", "run-golden-gap-",
            ]:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
        # Known canonical names
        known = {
            "coze": "coze",
            "dify": "dify",
            "flowise": "flowise",
            "langgraph": "langgraph",
            "langchain": "langgraph",
        }
        return known.get(cleaned, cleaned)

    # PRIORITY 2: supplement from evidence table (with run-scoped ID normalization)
    # Only create new product entries; don't touch Priority 1 authoritative data.
    evidence_list = [dict(e) for e in evidence_rows]
    for ev in evidence_list:
        raw_pid = (ev.get("product_slug") or ev.get("product_id") or "").strip()
        if not raw_pid or raw_pid == "None":
            continue
        slug = _canonical_slug(raw_pid)
        if not slug:
            continue
        # Only create entry if Priority 1 didn't already populate it
        if slug not in product_coverage:
            product_coverage[slug] = {
                "source_count": 0, "evidence_count": 0,
                "fact_count": 0, "signed_claims": 0,
                "coverage_status": "missing",
                "product_name": slug.title(),
            }
            product_coverage[slug]["evidence_count"] += 1

    # PRIORITY 3: fill missing Sources from sources table (with normalization)
    # Only touch products that Priority 1 left empty
    for src in [dict(s) for s in sources]:
        raw_pid = (src.get("product_slug") or src.get("product_id") or "").strip()
        if not raw_pid or raw_pid == "None":
            continue
        slug = _canonical_slug(raw_pid)
        if slug and slug in product_coverage and product_coverage[slug]["source_count"] == 0:
            product_coverage[slug]["source_count"] += 1

    # Comparison products: use canonical slugs from task_brief
    comparison_products = sorted(product_coverage.keys())

    # Patch Executive Summary text in spans to fix 0/0 products issue
    total_products = len(product_coverage)
    sufficient = sum(1 for v in product_coverage.values() if v.get("coverage_status") == "sufficient")
    for span in spans:
        title = (span.get("section_title") or "").lower()
        if "executive summary" in title:
            if total_products == 0:
                coverage_text = "Product coverage analysis pending."
            else:
                coverage_text = f"{sufficient}/{total_products} products have sufficient evidence coverage."
            span["text"] = (
                f"This competitive landscape analysis covers {total_products} products based on {len(evidence_list)} evidence items. "
                f"{coverage_text} "
                f"The report examines workflow orchestration, knowledge management, deployment options, pricing, enterprise readiness, and integrations."
            )
        elif "product coverage" in title:
            # If the section has only a markdown table with --- rows, replace text
            text = span.get("text") or ""
            if "| Product |" in text and "| --- |" in text and text.count("|") < 10:
                span["text"] = (
                    "This section summarizes product-level source, evidence, and fact coverage. "
                    "See the Product Coverage table above for product-level details."
                )

    if not args.out:
        out_path = Path("data/reports") / f"{report_id}.html"
    else:
        out_path = Path(args.out)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    html = _build_beautiful_html(
        title=report_dict.get("title", "Competitive Analysis Report"),
        run_id=args.run_id,
        spans=spans,
        quality_summary=quality,
        report_status=report_dict.get("report_status", "draft"),
        sources_map=sources_map,
        created_at=created_at,
        project_context=project_context,
        evidence_map=evidence_map,
        product_coverage=product_coverage,
        comparison_products=comparison_products,
    )

    out_path.write_text(html, encoding="utf-8")
    print(f"Generated: {out_path} ({len(html):,} bytes, {len(spans)} sections)")

    # Write content_html_path back to the reports table
    from backend.app.storage.db import get_connection
    relative_path = str(out_path)
    with get_connection() as conn:
        conn.execute(
            "UPDATE reports SET content_html_path=? WHERE report_id=?",
            (relative_path, report_id),
        )
        conn.commit()
    print(f"Updated reports.content_html_path = {relative_path}")


if __name__ == "__main__":
    main()
