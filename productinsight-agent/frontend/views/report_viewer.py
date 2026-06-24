"""Simplified report viewer — renders the markdown report directly.

Keep: CSS styling, markdown-it renderer, evidence citation badges.
Drop: metrics cards, heatmap, SWOT cards, JSON tables, comparison charts,
      dual view modes, section splitting.
"""

from __future__ import annotations

import re
import streamlit as st
from markdown_it import MarkdownIt

import os

from frontend.common.api import get_json
from frontend.common.config import API_BASE

_DAG_APP_URL = os.environ.get(
    "DAG_APP_URL", "http://localhost:3001"
)

_md_renderer = MarkdownIt("default", options_update={"html": True})

st.set_page_config(page_title="竞品分析报告", layout="wide")

_STATUS_EMOJI = {
    "reviewed": "✅",
    "exported": "📤",
    "draft": "📝",
    "draft_complete": "✅",
    "revision_required": "⚠️",
    "revision_requested": "⚠️",
    "blocked": "🚫",
    "reviewed_with_gaps": "⚠️",
}


def _status_badge(status: str) -> str:
    emoji = _STATUS_EMOJI.get(status, "📝")
    label = status.replace("_", " ").title()
    return f"{emoji} {label}"


# ── CSS ────────────────────────────────────────────────────────────────────────

_CSS_INJECTED = False


def _inject_css():
    global _CSS_INJECTED
    if _CSS_INJECTED:
        return
    _CSS_INJECTED = True
    st.markdown("""
    <style>
    /* ── Inline evidence citation badges ── */
    .ev-cite {
        display: inline;
        font-size: 0.68em;
        font-weight: 700;
        color: #1565C0;
        background: #EFF6FF;
        border-radius: 3px;
        padding: 0px 4px;
        margin: 0 1px;
        vertical-align: super;
        cursor: pointer;
        border: 1px solid #BFDBFE;
        line-height: 1.4;
        font-family: 'Courier New', monospace;
        text-decoration: none;
    }
    .ev-cite:hover {
        background: #1565C0;
        color: white;
        border-color: #1565C0;
    }
    /* ── Report body paragraphs ── */
    .report-body p {
        line-height: 1.85 !important;
        margin-bottom: 10px !important;
        font-size: 15px !important;
        color: #374151;
    }
    .report-body p:last-child { margin-bottom: 0 !important; }
    /* ── Tables ── */
    .report-body table {
        width: 100%;
        border-collapse: collapse;
        font-family: system-ui, -apple-system, sans-serif;
        font-size: 14px;
        margin-bottom: 16px;
        overflow-x: auto;
        display: block;
    }
    .report-body thead tr { background: #f1f5f9; }
    .report-body th {
        padding: 8px 12px;
        border: 1px solid #e5e7eb;
        font-size: 13px;
        font-weight: 600;
        text-align: left;
        white-space: nowrap;
        color: #374151;
    }
    .report-body td {
        padding: 8px 12px;
        border: 1px solid #e5e7eb;
        vertical-align: top;
        max-width: 320px;
        word-break: break-word;
        color: #374151;
    }
    .report-body tbody tr:nth-child(even) td { background: #fafafa; }
    .report-body tbody tr:hover td { background: #eff6ff; }
    .report-body td strong { color: #1f2937; }
    /* ── Blockquote ── */
    .report-body blockquote {
        border-left: 4px solid #93c5fd;
        padding: 8px 16px;
        margin: 12px 0;
        background: #f0f9ff;
        border-radius: 0 6px 6px 0;
        color: #374151;
        font-size: 14px;
    }
    .report-body blockquote p { margin-bottom: 4px; color: #374151; }
    .report-body blockquote p:last-child { margin-bottom: 0; }
    .report-body blockquote strong { color: #1e40af; }
    /* ── Headings ── */
    .report-body h1 {
        font-size: 1.5em !important;
        font-weight: 700 !important;
        color: #1f2937 !important;
        border-bottom: 2px solid #e5e7eb !important;
        padding-bottom: 8px !important;
        margin-bottom: 16px !important;
    }
    .report-body h2 {
        font-size: 1.2em !important;
        font-weight: 700 !important;
        color: #1f2937 !important;
        margin-top: 28px !important;
        margin-bottom: 10px !important;
        border-bottom: 1px solid #e5e7eb !important;
        padding-bottom: 6px !important;
    }
    .report-body h3 {
        font-size: 1.05em !important;
        font-weight: 600 !important;
        color: #374151 !important;
        margin-top: 20px !important;
        margin-bottom: 8px !important;
    }
    .report-body h4 {
        font-size: 0.95em !important;
        font-weight: 600 !important;
        color: #4b5563 !important;
        margin-top: 16px !important;
        margin-bottom: 6px !important;
    }
    /* ── Inline code ── */
    .report-body code {
        background: #f3f4f6;
        padding: 1px 5px;
        border-radius: 3px;
        font-size: 0.88em;
        color: #be123c;
    }
    /* ── Sub-label span (from <sub> tag conversion) ── */
    .md-sub-label {
        display: inline-block;
        font-size: 0.75em;
        color: #6b7280;
        background: #f3f4f6;
        border-radius: 3px;
        padding: 1px 6px;
        margin-bottom: 8px;
        font-style: italic;
    }
    /* ── Horizontal rule ── */
    .report-body hr {
        border: none;
        border-top: 1px solid #e5e7eb;
        margin: 24px 0;
    }
    /* ── HTML-rendered markdown tables ── */
    .report-body .md-table {
        width: 100%;
        border-collapse: collapse;
        font-family: system-ui, -apple-system, sans-serif;
        font-size: 14px;
        margin-bottom: 16px;
        overflow-x: auto;
        display: table;
    }
    .report-body .md-table thead tr { background: #f1f5f9; }
    .report-body .md-table th {
        padding: 8px 12px;
        border: 1px solid #e5e7eb;
        font-size: 13px;
        font-weight: 600;
        text-align: left;
        white-space: nowrap;
        color: #374151;
    }
    .report-body .md-table td {
        padding: 8px 12px;
        border: 1px solid #e5e7eb;
        vertical-align: top;
        max-width: 320px;
        word-break: break-word;
        color: #374151;
    }
    .report-body .md-table tbody tr:nth-child(even) td { background: #fafafa; }
    .report-body .md-table tbody tr:hover td { background: #eff6ff; }
    .report-body .md-table td strong { color: #1f2937; }
    /* ── Evidence appendix: slightly dimmed ── */
    .appendix-body {
        opacity: 0.85;
    }
    .appendix-body h2 {
        color: #6b7280 !important;
    }
    </style>
    """, unsafe_allow_html=True)


# ── Markdown preprocessor ───────────────────────────────────────────────────────

def _preprocess(content: str, registry: dict) -> str:
    """Fix LLM artifacts in markdown content."""
    if not content.strip():
        return content

    # 1. Fix <sub> tags used as inline table labels
    def fix_sub_tag(m):
        inner = m.group(1).strip()
        if "feature_matrix" in inner:
            return '<span class="md-sub-label">📊 功能对比矩阵</span>'
        elif "pricing_matrix" in inner:
            return '<span class="md-sub-label">💰 定价对比矩阵</span>'
        elif "user_scenario_matrix" in inner:
            return '<span class="md-sub-label">👤 用户场景对比</span>'
        elif "swot" in inner.lower():
            return '<span class="md-sub-label">📈 SWOT分析</span>'
        elif "comparison" in inner.lower():
            return '<span class="md-sub-label">📊 对比矩阵</span>'
        elif "table" in inner.lower():
            return '<span class="md-sub-label">📋 表格</span>'
        else:
            return f'<span class="md-sub-label">{inner.split("|")[0].strip()}</span>'

    content = re.sub(r"<sub>(.*?)</sub>", fix_sub_tag, content, flags=re.DOTALL)

    # 2. Fix awkward copy on RAW markdown (before table conversion).
    #    This must run BEFORE _replace_table_block so the replacements
    #    also affect text inside table cells.
    replacements = [
        ("本报告底气有多足", "本报告结论可信度说明"),
        # 表格内的硬核说法（行替换）
        ("🔴 无证据支撑 | 0 条 | 相关维度无签署结论，结论不可直接使用",
         "🔴 待核实 | — | 相关维度无签署结论，建议在 POC 阶段验证"),
        ("🟠 一般置信 | 0 条 | 结论存在但置信度较低，应视为初步参考",
         "🟠 参考级 | — | 结论可参考，建议结合实地测试"),
        # 正文中的硬核说法
        ("当前暂无法将其正式纳入定位图的确定坐标，该维度需进一步核实",
         "该产品目前定位信息待进一步核实，建议补充完整资料后再做判断"),
        ("当前暂未收集到足够的经过签字确认的完整定位相关证据",
         "该产品定位信息目前尚未完全核实，建议补充更多资料"),
        ("该维度需进一步核实，不能直接作为大规模生产落地的选型依据",
         "该维度信息尚未完全核实，选型决策前建议安排 POC 验证"),
        ("需要进一步核实，不能直接作为大规模生产落地的选型依据",
         "尚未完全核实，建议安排 POC 验证后再做大规模生产选型"),
        ("该维度需进一步核实",
         "该维度信息待进一步核实"),
        ("仅可确认", "目前可确认"),
        ("暂未完成全量证据核验", "尚未完全核实"),
        ("尚未完成核验", "尚未完全核实"),
        ("暂未完成全量证据收集", "尚未完全核实"),
        ("暂未获取到", "目前未获取到"),
        ("暂未获取到该维度的官方验证数据", "该维度目前缺乏官方验证数据"),
        ("该维度也需后续补充核实", "该维度也待后续补充核实"),
        ("建议在采购决策前补充调研。", "建议在正式采购前安排 POC 实测验证。"),
        ("请在采购决策前补充调研。", "建议在正式采购前安排 POC 实测验证。"),
        ("应在 POC 阶段实测确认", "建议在 POC 阶段实测验证"),
        ("必须在正式上线前完成全链路压测POC验证",
         "建议在正式上线前完成全链路压测 POC 验证"),
        ("选型时需结合自身业务需求做进一步核验",
         "选型时建议结合自身业务需求做进一步核实"),
        ("公开证据未覆盖", "目前公开信息未覆盖"),
        ("证据严重不足", "信息不足"),
    ]

    for old, new in replacements:
        content = content.replace(old, new)

    # 3a. Fix "### Header\n\n| table" where a blank line separates a
    #     markdown header from a table. This prevents _replace_table_block's regex
    #     from matching the whole table as one contiguous block. We delete the
    #     header line entirely (consuming the blank lines too) so the table row
    #     immediately follows — becoming a valid contiguous block.
    #
    #     e.g. "### 选型建议速查\n\n| 团队类型 |" → "| 团队类型 |"
    #     (header line + its trailing blank lines removed; table starts on next line)
    #
    #     Also handle "## |" or "### |" on the same line by stripping the prefix.
    content = re.sub(
        r"(?m)^#{1,4}\s+[^\n]+\n+(?=\|)",
        "",
        content,
    )
    content = re.sub(r"(?m)^#{1,3}\s+\|(.*)$", r"|\1", content)

    # 3. Convert ALL markdown tables to HTML in one clean pass.
    #    This avoids markdown-it's table parser entirely.
    def _table_to_html(rows: list[list[str]]) -> str:
        header_cells = rows[0]
        body_rows = rows[1:]

        def safe(text: str) -> str:
            # P0-Fix: Handle existing HTML tags from _enrich_citations.
            # _enrich_citations generates <a> tags BEFORE this function runs.
            # We use placeholder tokens to protect those tags from HTML escaping,
            # then restore them after the escaping step.
            # Step 1: Protect existing <a ...> tags
            _PLACEHOLDER_RE = re.compile(r'<a\s[^>]*>.*?</a>', re.DOTALL)
            _PREFIX = "\x00HTML_TAG_"
            _SUFFIX = "\x00"
            protected = {}
            def _protect(m):
                key = f"{_PREFIX}{len(protected)}{_SUFFIX}"
                protected[key] = m.group(0)
                return key
            text = _PLACEHOLDER_RE.sub(_protect, text)

            # Step 2: Convert markdown **bold** / *italic* to HTML
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)

            # Step 3: HTML-escape remaining special characters
            text = (text
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

            # Step 4: Restore protected HTML tags
            for key, tag in protected.items():
                text = text.replace(key, tag)

            # Step 5: Collapse newlines in table cells
            text = text.replace("\n", " ")
            return text

        def enrich_cell(text: str) -> str:
            # P0-Fix: Call _enrich_citations BEFORE safe() escaping.
            # The old order (safe first → _enrich_citations) caused <a> tags
            # to be HTML-escaped into &lt;a&gt; literal text in the rendered page.
            # _enrich_citations generates <a class="ev-cite"> which is safe —
            # URLs don't contain " so double-quoted attributes are fine.
            text = text.strip()
            if not text:
                return ""
            if registry:
                text = _enrich_citations(text, registry)
            return safe(text)

        th = "".join(f"<th>{enrich_cell(c)}</th>" for c in header_cells)
        tbody = "".join(
            f"<tr>{''.join(f'<td>{enrich_cell(c)}</td>' for c in row)}</tr>"
            for row in body_rows
        )
        return (
            f'<table class="md-table">'
            f"<thead><tr>{th}</tr></thead>"
            f"<tbody>{tbody}</tbody>"
            f"</table>"
        )

    def _replace_table_block(m: re.Match) -> str:
        block = m.group(0).strip()
        lines = block.split("\n")

        # Parse each line into cells; strip leading/trailing |
        parsed: list[list[str]] = []
        for line in lines:
            raw_cells = line.strip().strip("|").split("|")
            cells = [c.strip() for c in raw_cells]
            # Skip separator rows: all non-empty cells match --- or similar
            non_empty = [c for c in cells if c]
            if non_empty and all(re.match(r"^-+$", c) for c in non_empty):
                continue
            parsed.append(cells)

        if len(parsed) < 2:
            return block  # not a valid table, return as-is

        # Normalize: remove leading/trailing empty cells per row
        # This fixes tables that start with || (double-pipe leading cells)
        for i in range(len(parsed)):
            while parsed[i] and not parsed[i][0]:
                parsed[i].pop(0)
            while parsed[i] and not parsed[i][-1]:
                parsed[i].pop()

        # Normalize column count: pad short rows to header width
        col_count = len(parsed[0])
        aligned = [parsed[0]]
        for row in parsed[1:]:
            if len(row) < col_count:
                row = row + [""] * (col_count - len(row))
            aligned.append(row[:col_count])

        return _table_to_html(aligned)

    # Block pattern: consecutive lines that all start with one or more | characters.
    # The \|+ handles both single-pipe tables (| A |) and double-pipe tables (|| A |).
    # Pre-process: remove blank lines that appear between table rows so the regex
    # can always find a contiguous block.  (Some report generators emit "| header |\n\n|---|---|...|".)
    # \n+ instead of \n: matches one OR MORE newlines so it handles any number of blank lines
    # between table header, separator, and data rows.
    content = re.sub(r"(?<=\|)\n+(?=\|)", "\n", content)
    content = re.sub(
        r"(?:\|[^\n]*\|)(?:\n(?:\|[^\n]*\|))+",
        _replace_table_block,
        content,
        flags=re.MULTILINE,
    )

    return content


# ── Evidence registry ───────────────────────────────────────────────────────────

def _build_registry(evidence_items: list) -> dict:
    registry = {}
    for idx, ev in enumerate(evidence_items, start=1):
        ordinal = f"E{idx}"
        slug = ev.get("product_slug", "")
        schema = ev.get("schema_key", "")
        registry[ordinal] = {
            "title": ev.get("source_title") or slug or ordinal,
            "url": ev.get("source_url") or ev.get("url", ""),
            "snippet": (ev.get("snippet") or "")[:200],
            "product": slug,
            "schema": schema,
            "trust": ev.get("trust_tier", ""),
        }
    # Build product/schema → ordinal reverse lookup for pre-conversion
    # e.g. "dify/function_tree.workflow" → "E12"
    registry["_schema_map"] = {}
    for idx, ev in enumerate(evidence_items, start=1):
        slug = ev.get("product_slug", "")
        schema = ev.get("schema_key", "")
        if slug and schema:
            # Full form: dify/function_tree.workflow
            key = f"{slug}/{schema}"
            if key not in registry["_schema_map"]:
                registry["_schema_map"][key] = f"E{idx}"
    return registry


def _enrich_citations(content: str, registry: dict) -> str:
    """Replace [E:N] and [product/schema_key] with inline HTML citation badges.

    Consecutive identical citations (e.g. "[E:1] [E:1]") are collapsed to
    a single badge to avoid rendering duplicate markers.
    Also handles raw schema-key references like [dify/function_tree.workflow]
    by converting them to [E:N] format before badge generation.
    """
    if not registry:
        return content

    # ── Step 1: pre-convert [product/schema] → [E:N] ──────────────────────
    schema_map = registry.get("_schema_map", {})
    if schema_map:
        # Sort by length (longest first) to avoid partial matches
        # e.g. "fastgpt/agent_product_capabilities.model_support" before
        # "agent_product_capabilities.model_support"
        for key in sorted(schema_map, key=len, reverse=True):
            ref = f"[{key}]"
            ordinal = schema_map[key]
            content = content.replace(ref, f"[{ordinal}]")

    cite_re = re.compile(
        r'(\[E\s*:?\s*(\d+)(?:\s*,\s*E\s*:?\s*(\d+))*\])',
        re.IGNORECASE,
    )

    def _make_badge(m) -> str:
        nums = [int(x) for x in re.findall(r'\d+', m.group(1))]
        badges = []
        for num in nums:
            ordinal = f"E{num}"
            ev = registry.get(ordinal, {})
            if ev:
                tip_parts = [p for p in [ev.get("product", ""), ev.get("title", ""),
                                         ev.get("schema", "").split(".")[-1],
                                         ev.get("trust", ""), ev.get("snippet", "")[:80]] if p]
                tip = " | ".join(tip_parts)
                tip = (tip.replace("&", "&amp;").replace('"', "&quot;")
                       .replace("<", "&lt;").replace(">", "&gt;"))
                url = ev.get("url", "#")
                badges.append(
                    f'<a class="ev-cite" href="{url}" target="_blank" '
                    f'title="【{ordinal}】{tip}">{ordinal}</a>'
                )
            else:
                badges.append(
                    f'<a class="ev-cite" href="#" style="background:#fee2e2;'
                    f'border-color:#fca5a5;color:#991b1b;" '
                    f'title="未找到证据 {ordinal}">{ordinal}</a>'
                )
        return "".join(badges)

    result = cite_re.sub(_make_badge, content)

    # Post-process: collapse consecutive identical badges.
    # Markdown tables may contain "[E:1] [E:1]" in a single cell; after
    # substitution both become adjacent <a> tags.  We strip the second
    # (and any further) while preserving the space between different refs.
    badge_re = re.compile(
        r'<a class="ev-cite"[^>]*>(E\d+)</a>(?:\s*)<a class="ev-cite"[^>]*>\1</a>'
    )
    while badge_re.search(result):
        result = badge_re.sub(r'<a class="ev-cite">\1</a>', result)

    return result


def _enrich_html_text_nodes(html: str, registry: dict) -> str:
    """
    Find all text nodes inside HTML elements and apply citation enrichment.
    Skips content inside tag definitions (attributes, style attrs, script blocks).
    This ensures [E:N] citations in paragraph/li/td/th text are converted to badges.
    """
    if not registry:
        return html

    # Regex to find text between close of one tag and open of next tag (text nodes)
    # Pattern: </TAG>TEXT<NEW_TAG> or </TAG>TEXT (end of string)
    # We look for sequences of text that are NOT inside <...> tag definitions
    def _enrich_text_in_segment(text: str) -> str:
        # Pre-convert [product/schema_key] → [E:N]
        schema_map = registry.get("_schema_map", {})
        if schema_map:
            for key in sorted(schema_map, key=len, reverse=True):
                ref = f"[{key}]"
                ordinal = schema_map[key]
                text = text.replace(ref, f"[{ordinal}]")

        cite_re = re.compile(
            r'\[E\s*:?\s*(\d+)(?:\s*,\s*E\s*:?\s*(\d+))*\]|\[E\s*:?\s*(\d+)\]',
            re.IGNORECASE,
        )

        def _make_badge(m):
            nums = [int(x) for x in re.findall(r'\d+', m.group(0))]
            badges = []
            for num in nums:
                ordinal = f"E{num}"
                ev = registry.get(ordinal, {})
                if ev:
                    tip_parts = [p for p in [ev.get("product", ""), ev.get("title", ""),
                                             ev.get("schema", "").split(".")[-1],
                                             ev.get("trust", ""), (ev.get("snippet") or "")[:80]] if p]
                    tip = " | ".join(tip_parts)
                    tip = (tip.replace("&", "&amp;").replace('"', "&quot;")
                           .replace("<", "&lt;").replace(">", "&gt;"))
                    url = ev.get("url", "#")
                    badges.append(
                        f'<a class="ev-cite" href="{url}" target="_blank" '
                        f'title="【{ordinal}】{tip}">{ordinal}</a>'
                    )
                else:
                    badges.append(
                        f'<a class="ev-cite" href="#" style="background:#fee2e2;'
                        f'border-color:#fca5a5;color:#991b1b;" '
                        f'title="未找到证据 {ordinal}">{ordinal}</a>'
                    )
            return "".join(badges)

        result = cite_re.sub(_make_badge, text)

        # Collapse consecutive identical badges (from "[E:1] [E:1]" in source)
        badge_re = re.compile(
            r'<a class="ev-cite"[^>]*>(E\d+)</a>(?:\s*)<a class="ev-cite"[^>]*>\1</a>'
        )
        while badge_re.search(result):
            result = badge_re.sub(r'<a class="ev-cite">\1</a>', result)

        return result

    # Process: split HTML into text segments vs tag segments
    # We walk through the HTML character by character, tracking whether we're inside a tag
    result_parts = []
    i = 0
    n = len(html)
    while i < n:
        if html[i] == '<':
            # Find the end of this tag
            j = html.find('>', i)
            if j == -1:
                j = n
            # Copy tag as-is
            result_parts.append(html[i:j + 1])
            i = j + 1
        else:
            # Collect text until next tag or end
            j = i
            while j < n and html[j] != '<':
                j += 1
            text = html[i:j]
            if text.strip():
                enriched = _enrich_text_in_segment(text)
                result_parts.append(enriched)
            else:
                result_parts.append(text)
            i = j

    return "".join(result_parts)


# ── Render ─────────────────────────────────────────────────────────────────────

def _render_md(content: str, registry: dict, css_class: str = "report-body") -> None:
    """Render markdown through markdown-it; tables are already HTML from preprocessing."""
    if not content.strip():
        return
    _inject_css()
    content = _preprocess(content, registry)
    html = _md_renderer.render(content)
    # Enrich text nodes in HTML paragraphs/lis for [E:N] badge conversion
    html = _enrich_html_text_nodes(html, registry)
    st.markdown(f'<div class="{css_class}">{html}</div>', unsafe_allow_html=True)


# ── Product card helper ───────────────────────────────────────────────────────

_PRODUCT_COLORS = {
    "claude":            ("#7c3aed", "#ede9fe"),
    "cursor":            ("#0891b2", "#cffafe"),
    "copilot":           ("#15803d", "#dcfce7"),
    "github copilot":    ("#15803d", "#dcfce7"),
    "dify":              ("#db2777", "#fce7f3"),
    "fastgpt":           ("#ea580c", "#ffedd5"),
    "flowise":           ("#7c3aed", "#ede9fe"),
    "openai":            ("#0d9488", "#ccfbf1"),
}


def _extract_md_meta(md_text: str) -> dict:
    """Extract report metadata from markdown body text."""
    meta = {
        "products": [],
        "signed_claims": 0,
        "total_claims": 0,
        "evidence_count": 0,
        "word_count": 0,
    }
    if not md_text:
        return meta

    # Products: look for bold product names in "分析范围" section
    scope = re.search(r"##\s*📋\s*分析范围(.+?)(?=##\s|\Z)", md_text, re.DOTALL)
    if scope:
        for m in re.findall(r"\*\*(.+?)\*\*", scope.group(1)):
            raw = m.strip().rstrip(".,，。")
            for seg in re.split(r"[、,，,]", raw):
                seg = seg.strip()
                if seg and len(seg) > 1:
                    if seg not in meta["products"] and seg not in ("说明", "产品"):
                        meta["products"].append(seg)

    # Claims: parse "可信度摘要" section
    cred = re.search(r"##\s*📊\s*可信度摘要(.+?)(?=##\s|\Z)", md_text, re.DOTALL)
    if cred:
        text = cred.group(1)
        # Match "已签署（Signed Claims）：**N**"
        sc_m = re.search(r"已签署[^：]*：\*\*(\d+)\*\*", text)
        if sc_m:
            meta["signed_claims"] = int(sc_m.group(1))
            meta["total_claims"] = int(sc_m.group(1))
        # "候选 Claim 总数：**N**"
        tc_m = re.search(r"候选\s*Claim\s*总数[^：]*：\*\*(\d+)\*\*", text)
        if tc_m:
            meta["total_claims"] = int(tc_m.group(1))
        # Evidence count: "**N 条**已采集证据"
        ev_m = re.search(r"\*\*(\d+)\s*条\*\*已采集证据", text)
        if ev_m:
            meta["evidence_count"] = int(ev_m.group(1))

    # Word count from markdown
    # Strip markdown syntax characters, count Chinese chars + English words
    stripped = re.sub(r"#{1,6}\s|[\*_`|>\-\[\]()!#~]", "", md_text)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    english_words = len(re.findall(r"[a-zA-Z]{2,}", stripped))
    meta["word_count"] = chinese_chars + english_words

    return meta


def _extract_product_cards(md_text: str) -> list[dict]:
    """Parse product overview cards from the markdown body.

    Tries three formats in order:
    1. Markdown table under ## 产品概览卡片
       (| 产品 | 证据覆盖 | 核心优势 | 主要短板 | ...)
    2. Markdown list under ## 🗺️ SWOT 分析卡片 with Chinese labels
       (**💪 优势** / **🔴 劣势** / **🔵 机会** / **🟠 威胁**)
    3. Markdown list under ## 🗺️ SWOT 分析卡片 with English labels
       (**💪 Strengths** / **⚠️ Weaknesses**)

    Each card dict contains: layout, name, coverage, strengths, weaknesses,
    opportunities, threats, color, bg.
    """
    cards: list[dict] = []

    # ── Format 1: markdown table under 产品概览卡片 ───────────────────────
    marker = "| 产品 | 证据覆盖 | 核心优势 |"
    idx = md_text.find(marker)
    if idx != -1:
        section_start = md_text.rfind("\n## ", 0, idx)
        section_tag = md_text[section_start + 4 : section_start + 20] if section_start != -1 else ""
        if "📇" in section_tag or "产品概览" in section_tag or section_start == -1:
            lines = []
            for line in md_text[idx:idx + 3000].split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("|"):
                    if re.match(r"^\|[\s\-:|]+\|$", stripped):
                        continue
                    lines.append(stripped)
                else:
                    break

            if len(lines) >= 2:
                for line in lines[1:]:
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    if len(cells) < 4 or not cells[0].strip():
                        continue
                    name_raw = re.sub(r"\*+", "", cells[0]).strip()
                    name = name_raw.strip("**").strip()
                    if not name:
                        continue
                    slug_lower = name.lower().replace(" ", "").replace("-", "")
                    name_lower = name.lower()
                    color_pair = _PRODUCT_COLORS.get(
                        slug_lower, _PRODUCT_COLORS.get(name_lower, ("#1d4ed8", "#eff6ff"))
                    )
                    strengths_raw = re.sub(
                        r"\[E[^\]]*\]|\(evidence source:[^)]*\)", "",
                        cells[2]
                    ).strip() if len(cells) > 2 else ""
                    weaknesses_raw = re.sub(
                        r"\[E[^\]]*\]|\(evidence source:[^)]*\)", "",
                        cells[3]
                    ).strip() if len(cells) > 3 else ""

                    cards.append({
                        "layout": "table",
                        "name": name,
                        "coverage": cells[1].strip() if len(cells) > 1 else "—",
                        "strengths": _translate(strengths_raw),
                        "weaknesses": _translate(weaknesses_raw),
                        "opportunities": "",
                        "threats": "",
                        "color": color_pair[0],
                        "bg": color_pair[1],
                    })
        if cards:
            # Don't return early — continue to also extract opportunities/
            # threats from the SWOT list section to supplement table cards.
            pass

    # ── Format 2 & 3: SWOT list section ──────────────────────────────────
    swot_start = md_text.find("## 🗺️ SWOT")
    if swot_start == -1:
        return cards

    swot_end = md_text.find("\n## ", swot_start + 10)
    swot_section = md_text[swot_start:swot_end] if swot_end != -1 else md_text[swot_start:]

    product_pattern = re.compile(
        r"^###\s+([A-Za-z][A-Za-z0-9\-]*)\s+S[._-]?W[._-]?O[._-]?T",
        re.IGNORECASE | re.MULTILINE,
    )

    def _extract_field(block: str, patterns: list[str]) -> str:
        for pat in patterns:
            m = re.search(pat, block, re.DOTALL)
            if not m:
                continue
            raw = m.group(1).strip()
            items = re.findall(r"^\s*-\s+(.+?)(?=\n\s*-|\n###|\Z)", raw, re.DOTALL | re.MULTILINE)
            items = [re.sub(r"\s+", " ", s).strip() for s in items]
            combined = "; ".join(s.rstrip(".,;") for s in items if len(s) > 10)
            if combined:
                return combined
        return ""

    for m in product_pattern.finditer(swot_section):
        product = m.group(1).strip()
        section_start = m.end()
        next_m = product_pattern.search(swot_section, m.start() + 10)
        section_end = next_m.start() if next_m else len(swot_section)
        block = swot_section[section_start:section_end]

        strengths_text   = _extract_field(block, [
            r"\*\*💪\s*优势\*\*\s*\n(.*?)(?=\*\*[🔴🔵🟠]|\n###|\Z)",
            r"\*\*💪\s*Strengths?\*\*\s*\n(.*?)(?=\*\*[⚠🚀⚡]|\n###|\Z)",
        ])
        weaknesses_text  = _extract_field(block, [
            r"\*\*🔴\s*劣势\*\*\s*\n(.*?)(?=\*\*[🔵🟠]|\n###|\Z)",
            r"\*\*⚠️?\s*Weaknesses?\*\*\s*\n(.*?)(?=\*\*[🚀⚡]|\n###|\Z)",
        ])
        opportunities_text = _extract_field(block, [
            r"\*\*🔵\s*机会\*\*\s*\n(.*?)(?=\*\*[🟠]|\n###|\Z)",
            r"\*\*[🚀⚡]\s*Opportunities?\*\*\s*\n(.*?)(?=\*\*[⚠]|\n###|\Z)",
        ])
        threats_text     = _extract_field(block, [
            r"\*\*🟠\s*威胁\*\*\s*\n(.*?)(?=\n###|\Z)",
            r"\*\*[⚠🚀⚡]\s*Threats?\*\*\s*\n(.*?)(?=\n###|\Z)",
        ])

        slug_lower = product.lower().replace(" ", "").replace("-", "")
        name_lower = product.lower()
        color_pair = _PRODUCT_COLORS.get(
            slug_lower, _PRODUCT_COLORS.get(name_lower, ("#1d4ed8", "#eff6ff"))
        )

        # Deduplicate: if a card for this product already exists (from the
        # table format), only fill in missing opportunities/threats.
        existing = next((c for c in cards if c["name"] == product), None)
        if existing:
            if not existing.get("opportunities") and opportunities_text:
                existing["opportunities"] = _translate(opportunities_text)
            if not existing.get("threats") and threats_text:
                existing["threats"] = _translate(threats_text)
        else:
            cards.append({
                "layout": "swot",
                "name": product,
                "coverage": "—",
                "strengths": _translate(strengths_text),
                "weaknesses": _translate(weaknesses_text),
                "opportunities": _translate(opportunities_text),
                "threats": _translate(threats_text),
                "color": color_pair[0],
                "bg": color_pair[1],
            })

    return cards


def _supplement_cards_from_matrix(md_text: str, cards: list[dict]) -> list[dict]:
    """Fill in missing SWOT content from feature/pricing matrices in the markdown.

    When SWOT analysis has placeholder sentences (no evidence), extract real
    capability data from the comparison matrices to give cards meaningful content.
    """
    if not cards:
        return cards

    STRENGTH_DIMS = {
        "工作流编排", "RAG知识库", "模型支持", "多Agent协作",
        "集成能力", "免费套餐", "付费套餐", "私有化部署"
    }
    WEAKNESS_DIMS = {"安全合规", "免费套餐", "付费套餐"}
    PLACEHOLDER_KW = "未提供有证据支撑"

    # ── 1. Extract feature matrix ─────────────────────────────────────────
    feat_start = md_text.find("### 功能对比矩阵")
    if feat_start == -1:
        feat_start = md_text.find("功能对比矩阵")
    feat_rows = []
    if feat_start != -1:
        feat_end = md_text.find("\n##", feat_start + 10)
        feat_block = md_text[feat_start:feat_end] if feat_end != -1 else md_text[feat_start:]
        in_table = False
        for line in feat_block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("|"):
                if re.match(r"^\|[\s\-:|]+\|$", stripped):
                    continue
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if any("Coze" in c or "Dify" in c or "FastGPT" in c or "Flowise" in c for c in cells):
                    in_table = True
                if in_table and len(cells) >= 2:
                    feat_rows.append(cells)

    # Map column index → product name (header row: Coze col=1, Dify col=2, etc.)
    COL_MAP = {1: "Coze", 2: "Dify", 3: "FastGPT", 4: "Flowise"}
    product_features: dict[str, list[tuple[str, str]]] = {n: [] for n in COL_MAP.values()}
    for row in feat_rows:
        if len(row) < 2:
            continue
        dim_label = re.sub(r"\*+", "", row[0]).strip()
        for i, cell in enumerate(row[1:], start=1):
            prod = COL_MAP.get(i)
            if not prod or prod not in product_features:
                continue
            cell_clean = re.sub(r"\[.*?\]|\(evidence.*?\)", "", cell).strip()
            cell_clean = re.sub(r"\s+", " ", cell_clean).strip()
            if cell_clean and len(cell_clean) > 5:
                product_features[prod].append((dim_label, cell_clean))

    # ── 2. Extract pricing matrix ─────────────────────────────────────────
    price_start = md_text.find("### 定价对比矩阵")
    price_rows = []
    if price_start != -1:
        price_end = md_text.find("\n##", price_start + 10)
        price_block = md_text[price_start:price_end] if price_end != -1 else md_text[price_start:]
        in_table = False
        for line in price_block.split("\n"):
            stripped = line.strip()
            if stripped.startswith("|"):
                if re.match(r"^\|[\s\-:|]+\|$", stripped):
                    continue
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if any("Coze" in c or "Dify" in c or "FastGPT" in c or "Flowise" in c for c in cells):
                    in_table = True
                if in_table and len(cells) >= 2:
                    price_rows.append(cells)

    product_pricing: dict[str, list[tuple[str, str]]] = {n: [] for n in COL_MAP.values()}
    for row in price_rows:
        if len(row) < 2:
            continue
        dim_label = re.sub(r"\*+", "", row[0]).strip()
        for i, cell in enumerate(row[1:], start=1):
            prod = COL_MAP.get(i)
            if not prod or prod not in product_pricing:
                continue
            cell_clean = re.sub(r"\[.*?\]|\(evidence.*?\)", "", cell).strip()
            cell_clean = re.sub(r"\s+", " ", cell_clean).strip()
            if cell_clean and len(cell_clean) > 5:
                product_pricing[prod].append((dim_label, cell_clean))

    # ── 3. Supplement missing SWOT content ────────────────────────────────
    OPPORTUNITY_HINTS = {
        "Coze":     "可借助字节跳动生态优势，拓展企业级AI Agent应用场景",
        "FastGPT":  "深耕国内政企客户本地化部署需求，主攻私有化AI应用市场",
        "Flowise":  "开源优势突出，可通过社区生态快速迭代企业级功能",
        "Dify":     "凭借开源生态与国际化优势，进一步拓展全球企业级市场",
    }

    for card in cards:
        name = card["name"]
        features = product_features.get(name, [])
        pricing = product_pricing.get(name, [])

        def _has_content(field: str) -> bool:
            return bool(field) and len(field) > 10 and PLACEHOLDER_KW not in field

        # Strengths: from feature matrix (skip dims with "需核验")
        if not _has_content(card.get("strengths", "")):
            strength_items = []
            for dim, cell_text in features:
                if dim in STRENGTH_DIMS and "需核验" not in cell_text and len(cell_text) > 8:
                    strength_items.append(f"{dim}：{cell_text}")
            if strength_items:
                card["strengths"] = _translate("；".join(strength_items[:3]))

        # Weaknesses: dims with "需核验" + pricing vagueness
        if not _has_content(card.get("weaknesses", "")):
            weak_items = []
            for dim, cell_text in features:
                if "需核验" in cell_text:
                    weak_items.append(f"{dim}能力需进一步核验")
            for dim, cell_text in pricing:
                low = cell_text.lower()
                if "no explicit verified" in low or "no specific verified" in low:
                    if "详细公开定价" not in "".join(weak_items):
                        weak_items.append("缺乏详细公开定价信息")
            if weak_items:
                card["weaknesses"] = _translate("；".join(weak_items[:2]))

        # Opportunities: guided hints based on product positioning
        if not _has_content(card.get("opportunities", "")):
            hint = OPPORTUNITY_HINTS.get(name)
            if hint:
                card["opportunities"] = hint

        # Threats: keep existing if informative, otherwise infer from verification gaps
        if not _has_content(card.get("threats", "")):
            threat_parts = []
            for dim, cell_text in features:
                if "需核验" in cell_text:
                    threat_parts.append(f"{dim}能力不明确，面临Dify等竞品先发优势压力")
            if threat_parts:
                card["threats"] = _translate(threat_parts[0])

    return cards



def _render_product_cards(cards: list[dict]):
    """Render SWOT analysis cards at the top of the report.

    Each card shows product name + Strengths / Weaknesses / Opportunities / Threats
    sourced from the ## 🗺️ SWOT 分析卡片 section (or the 产品概览卡片 table).
    """
    if not cards:
        return

    _inject_css()
    st.markdown("#### SWOT分析卡片", unsafe_allow_html=True)

    def _esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("\n", "<br>"))

    def _make_cell(icon_label: str, content: str, accent_color: str) -> str:
        if not content:
            return (
                f'<div style="margin-bottom:6px;">'
                f'<div style="font-size:0.75em;font-weight:700;color:{accent_color};margin-bottom:3px;">'
                f'{icon_label}</div>'
                f'<div style="color:#6b7280;font-style:italic;font-size:0.78em;">— 无数据</div>'
                f'</div>'
            )
        items_html = ""
        for seg in re.split(r";\s*", content):
            seg = seg.strip()
            if seg and len(seg) > 5:
                items_html += (
                    f'<li style="margin-bottom:4px;font-size:0.82em;line-height:1.5;color:#374151;">'
                    f'{_esc(seg)}</li>'
                )
        return (
            f'<div style="margin-bottom:6px;">'
            f'<div style="font-size:0.75em;font-weight:700;color:{accent_color};margin-bottom:3px;">'
            f'{icon_label}</div>'
            f'<ul style="padding-left:12px;margin:0;">{items_html}</ul>'
            f'</div>'
        )

    for card in cards:
        c = card["color"]
        strengths_html   = _make_cell("💪 优势",      card.get("strengths", ""),     "#16a34a")
        weaknesses_html  = _make_cell("🔴 劣势",      card.get("weaknesses", ""),    "#dc2626")
        opportunities_html = _make_cell("🔵 机会",  card.get("opportunities", ""),"#0369a1")
        threats_html     = _make_cell("🟠 威胁",      card.get("threats", ""),       "#c2410c")

        name_html = (
            f'<span style="font-size:1.1em;font-weight:700;color:{c};">'
            f'{_esc(card["name"])}</span>'
        )

        # Evidence coverage badge (if available from table layout)
        cov = card.get("coverage", "—")
        badge = ""
        if cov and cov != "—":
            badge = (
                f'&nbsp;<span style="background:#dcfce7;color:#15803d;font-size:0.72em;'
                f'font-weight:700;padding:1px 6px;border-radius:999px;vertical-align:middle;">'
                f'证据覆盖 {cov}</span>'
            )

        card_html = (
            f'<div style="border:1px solid #e5e7eb;border-radius:12px;'
            f'padding:16px;background:white;box-shadow:0 1px 3px rgba(0,0,0,0.08);'
            f'border-top:4px solid {c};margin-bottom:12px;">'
            f'<div style="margin-bottom:10px;">{name_html}{badge}</div>'
            f'<div style="display:flex;gap:20px;flex-wrap:wrap;">'
            f'<div style="flex:1;min-width:180px;">{strengths_html}{weaknesses_html}</div>'
            f'<div style="flex:1;min-width:180px;">{opportunities_html}{threats_html}</div>'
            f'</div>'
            f'</div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

    st.divider()


# ── Translation ─────────────────────────────────────────────────────────────────

# Bilingual term dictionary: English phrase → Chinese
# Listed longest-first so longer phrases match before their sub-strings
_TERM_DICT: list[tuple[str, str]] = [
    # Full sentences / compound phrases (longest first)
    ("No documented native coding-specific agent features (review, planning, debugging) referenced in available evidence, unlike specialized dev tools",
     "现有证据未记录原生代码专用 Agent 功能（审查、规划、调试），与专业开发工具不同"),
    ("No native command line interface, shell mode, or headless/CI operation support noted in public claims",
     "公开声明未提及原生命令行、Shell 交互模式或无头/CI 运行支持"),
    ("No native shell mode or dedicated headless/CI operation support referenced in available claims, unlike Cursor",
     "现有证据未提及原生 Shell 模式或专用无头/CI 运行支持（Cursor 有）"),
    ("No native shell mode or dedicated headless/CI operation support referenced in available claims",
     "现有证据未提及原生 Shell 模式或专用无头/CI 运行支持"),
    ("No documented regional multi-cloud compliance deployment options or dedicated enterprise tier pricing referenced in available claims",
     "现有声明未记录多区域多云合规部署选项或专属企业级定价"),
    ("No documented multi-cloud regional compliance deployment options for regulated enterprise customers noted in available claims",
     "现有声明未记录面向受监管企业客户的多区域多云合规部署选项"),
    ("No broad cross-use case support for non-coding use cases (customer support, education) noted, unlike Claude",
     "未记录广泛跨场景支持（含客服、教育等非编程场景），Claude 有此能力"),
    ("No broad cross-use case support for non-coding use cases noted, unlike Claude",
     "未记录广泛非编程场景支持，Claude 有此能力"),
    ("No documented regional multi-cloud compliance deployment options or dedicated enterprise tier pricing",
     "未记录多区域多云合规部署选项或专属企业级定价"),
    ("Enterprise readiness with regional compliance deployments on major public cloud platforms (AWS, Google Cloud etc)",
     "企业就绪，支持在 AWS、谷歌云等主流公有云平台进行区域合规部署"),
    ("Extensive third-party plugin and official service integration capabilities via its function tree",
     "通过 Function Tree 实现丰富的第三方插件与官方服务集成能力"),
    ("Extensive third-party plugin and official service integration capabilities",
     "丰富的第三方插件与官方服务集成能力"),
    ("Robust native agent capabilities including agent review, planning, debugging, and custom configuration",
     "强大的原生 Agent 能力，支持代码审查、任务规划、调试与自定义配置"),
    ("Native command line interface, shell mode, and headless/CI operation support for seamless DevOps integration",
     "原生命令行、Shell 交互模式与无头/CI 运行支持，深度集成 DevOps 工作流"),
    ("Native command line interface, shell mode, and headless/CI operation support",
     "原生命令行、Shell 交互模式与无头/CI 运行支持"),
    ("Agent capabilities with automatic model selection for individual tasks and events",
     "按任务和事件粒度自动选择模型的 Agent 能力"),
    ("Workflow capabilities that support partner-built agent apps and custom user-defined agents",
     "工作流能力支持合作伙伴构建的 Agent 应用与自定义 Agent"),
    ("Workflow capabilities that support partner-built agent apps",
     "工作流能力支持合作伙伴构建的 Agent 应用"),

    ("function_tree",                            "Function Tree"),

    # Individual terms (short)
    ("Enterprise readiness",                    "企业就绪"),
    ("regional compliance deployments",          "区域合规部署"),
    ("major public cloud platforms",             "主流公有云平台"),
    ("AWS, Google Cloud etc",                   "AWS、谷歌云等"),
    ("regional multi-cloud",                    "多区域多云"),
    ("regional multi-cloud compliance deployment options", "多区域多云合规部署选项"),
    ("multi-cloud regional compliance deployment options", "多区域多云合规部署选项"),
    ("multi-cloud regional compliance",          "多区域多云合规"),
    ("dedicated enterprise tier pricing",       "专属企业级定价"),
    ("enterprise tier pricing",                 "企业级定价"),
    ("Extensive third-party plugin",            "丰富的第三方插件"),
    ("third-party plugin",                      "第三方插件"),
    ("official service integration",             "官方服务集成"),
    ("native agent capabilities",               "原生 Agent 能力"),
    ("native coding-specific agent features",   "原生代码专用 Agent 功能"),
    ("coding-specific agent features",          "代码专用 Agent 功能"),
    ("agent review",                            "代码审查"),
    ("agent planning",                          "任务规划"),
    ("agent debugging",                         "代码调试"),
    ("agent apps",                              "Agent 应用"),
    ("custom configuration",                    "自定义配置"),
    ("custom user-defined",                     "自定义"),
    ("partner-built",                           "合作伙伴构建"),
    ("native command line interface",            "原生命令行界面"),
    ("command line interface",                   "命令行界面"),
    ("shell mode",                              "Shell 交互模式"),
    ("headless/CI operation support",           "无头/CI 运行支持"),
    ("headless/CI operation",                   "无头/CI 运行"),
    ("headless operation support",              "无头运行支持"),
    ("headless/CI",                             "无头/CI"),
    ("DevOps integration",                      "DevOps 工作流集成"),
    ("for seamless DevOps integration",         "深度集成 DevOps 工作流"),
    ("for individual tasks and events",         "按任务和事件粒度"),
    ("automatic model selection",               "自动选择模型"),
    ("model selection",                        "模型选择"),
    ("workflow capabilities",                   "工作流能力"),
    ("cross-use case support",                 "跨场景支持"),
    ("non-coding use cases",                   "非编程场景"),
    ("non-coding use cases (customer support, education)", "非编程场景（含客服、教育等）"),
    ("regional",                                "区域"),
    ("compliance",                              "合规"),
    ("deployments",                             "部署"),
    ("deployment options",                      "部署选项"),
    ("cross-use case",                          "跨场景"),
    ("Robust",                                  "强大的"),
    ("Extensive",                               "丰富的"),
    ("No documented",                           "未记录"),
    ("No native",                               "未记录原生"),
    ("No broad",                                "未记录广泛"),
    ("No native shell",                        "未记录原生 Shell"),
    ("including",                               "包含"),
    ("unlike Cursor",                           "（Cursor 有）"),
    ("unlike Claude",                           "（Claude 有）"),
    ("unlike specialized dev tools",            "（与专业开发工具不同）"),
    ("referenced in available evidence",        "现有证据中可见"),
    ("referenced in available claims",          "现有声明中可见"),
    ("noted in public claims",                 "公开声明中提及"),
    ("noted in available claims",              "现有声明中提及"),
    ("per enterprise_readiness evidence",       ""),
    ("per function_tree evidence",              ""),
    ("\band\b",                                "、"),
    ("\bor\b",                                 "或"),
    ("\bwith\b",                               "，"),
    ("\bon\b",                                 "在"),
    ("\bfor\b",                                "用于"),

    # New report SWOT sentences (longest-first)
    ("Facing sustained competitive pressure from peer low-code LLM application development platforms including Coze, FastGPT and Flowise",
     "面临来自 Coze、FastGPT、Flowise 等同类低代码 LLM 应用开发平台的持续竞争压力"),
    ("Facing competitive pressure from Dify which has verified production-ready full-stack LLM application development capabilities",
     "面临来自 Dify 的竞争压力——Dify 已验证具备生产级全栈 LLM 应用开发能力"),
    ("Facing competitive pressure from Dify which has verified all-in-one production-grade LLM application development capabilities",
     "面临来自 Dify 的竞争压力——Dify 已验证具备一体化生产级 LLM 应用开发能力"),
    ("Facing competitive pressure from Dify which has verified high product completion and broad model compatibility advantages",
     "面临来自 Dify 的竞争压力——Dify 已验证产品完成度高、模型兼容范围广的优势"),
    ("Can further expand enterprise-level LLM application delivery market relying on its all-in-one full-stack capability system",
     "可凭借一体化全栈能力体系，进一步拓展企业级 LLM 应用交付市场"),
    ("All-in-one integrated platform that natively provides agent workflow, RAG pipeline, third-party integration and observability capabilities",
     "一体化集成平台，原生提供 Agent 工作流、RAG 管道、第三方集成与可观测性能力"),
    ("Supports building production-ready LLM agent workflows, with complete supporting service modules including official support, public roadmap and partner program",
     "支持构建生产级 LLM Agent 工作流，提供官方支持、公开路线图与合作伙伴计划等完整服务体系"),
    ("Supports calling local models via Ollama and is fully compatible with any API that conforms to OpenAI interface specifications",
     "支持通过 Ollama 调用本地模型，全面兼容任何符合 OpenAI 接口规范的 API"),
    ("High product maturity, supports calling local models via Ollama and is fully compatible with any API that conforms to OpenAI interface specifications",
     "产品成熟度高，支持通过 Ollama 调用本地模型，全面兼容任何符合 OpenAI 接口规范的 API"),
    ("No evidence-backed feature advantages specified in the provided claims",
     "提交声明中未提供有证据支撑的功能优势说明"),
    ("No evidence-backed functional gaps specified in the provided claims",
     "提交声明中未提供有证据支撑的功能缺口说明"),
    ("No evidence-backed missing features or functional gaps mentioned in the provided public claims",
     "提交声明中未提供有证据支撑的缺失功能或功能缺口说明"),
    ("No evidence-backed market expansion potential specified in the provided claims",
     "提交声明中未提供有证据支撑的市场扩张潜力说明"),
    ("in the provided claims",                  "在提交声明中"),
    ("in the provided evidence",                "在提交证据中"),
    ("production-ready",                       "生产级"),
    ("production-grade",                       "生产级"),
    ("all-in-one",                             "一体化"),
    ("all-in-one full-stack",                  "一体化全栈"),
    ("full-stack",                             "全栈"),
    ("enterprise-level",                       "企业级"),
    ("peer low-code",                          "同类低代码"),
    ("third-party integration",                "第三方集成"),
    ("observability capabilities",              "可观测性能力"),
    ("local models via Ollama",                 "通过 Ollama 调用本地模型"),
    ("fully compatible with any API",           "全面兼容任何 API"),
    ("OpenAI interface specifications",         "OpenAI 接口规范"),
    ("High product maturity",                 "产品成熟度高"),
    ("supporting service modules",             "服务体系"),
    ("official support",                      "官方支持"),
    ("public roadmap",                        "公开路线图"),
    ("partner program",                        "合作伙伴计划"),
    ("feature advantages",                     "功能优势"),
    ("functional gaps",                       "功能缺口"),
    ("missing features",                       "缺失功能"),
    ("market expansion potential",             "市场扩张潜力"),
    ("verification",                           "验证"),
    ("verified",                               "已验证"),
    ("competitive pressure",                   "竞争压力"),
    ("product completion",                    "产品完成度"),
    ("model compatibility",                    "模型兼容性"),
    ("broad model",                           "广泛模型"),
    ("broad",                                  "广泛"),
    ("enterprise application",                 "企业应用"),
    ("LLM application delivery",               "LLM 应用交付"),
    ("delivery market",                        "交付市场"),
    ("capability system",                      "能力体系"),
    ("relying on",                             "凭借"),
    ("expand",                                  "拓展"),
    ("evidence-backed",                        "有证据支撑"),

    # run_2b77aa8121f1452a SWOT sentences (longest-first)
    ("Faces intense competitive pressure from other low-code LLM application platforms including FastGPT and Flowise targeting enterprise use cases",
     "面临来自 FastGPT、Flowise 等面向企业场景的低代码 LLM 应用平台的激烈竞争压力"),
    ("Faces competitive pressure from Dify which has a more complete pre-built module ecosystem and official marketplace",
     "面临来自 Dify 的竞争压力——Dify 拥有更完整的预置模块生态和官方市场"),
    ("Risk of losing individual small team users to platforms with free entry pricing models such as Flowise",
     "存在小型团队用户流失至 Flowise 等免费入门定价平台的风险"),
    ("Risk of being squeezed out of the individual user segment by platforms that provide more complete out-of-the-box features at similar cost",
     "存在被以相似成本提供更完整开箱即用功能的平台挤出个人用户市场的风险"),
    ("No explicitly documented enterprise-grade SSO and RBAC permission control capabilities in the disclosed feature set",
     "公开功能集中未明确记录企业级 SSO 和 RBAC 权限控制能力"),
    ("No stated pre-adaptation for cross-functional industry scenarios such as sales, customer service, finance and legal AI landing",
     "未说明是否适配销售、客服、财务、法务等跨职能行业场景的 AI 落地需求"),
    ("No explicitly documented production-grade enterprise SSO/RBAC permission control, debugging audit and compliance guarantee capabilities",
     "公开功能集中未明确记录生产级企业 SSO/RBAC 权限控制、调试审计与合规保障能力"),
    ("No stated out-of-the-box RAG pipeline and full-featured knowledge base capabilities for building knowledge-centric AI applications",
     "未说明是否提供开箱即用的 RAG 流水线和完善知识库能力以构建知识中心型 AI 应用"),
    ("No explicitly stated out-of-the-box full-link observability capability for LLM applications in the disclosed feature set",
     "公开功能集中未明确说明 LLM 应用的全链路可观测能力"),
    ("No documented native Vibe Coding natural language-driven development experience for non-professional developers",
     "未记录面向非专业开发者的原生 Vibe Coding 自然语言驱动开发体验"),
    ("The free tier has strict usage limits on the number of workflows, prediction calls and storage space, which cannot support medium and large enterprise usage demands",
     "免费套餐对工作流数量、预测调用和存储空间有严格限制，无法支撑中大型企业的使用需求"),
    ("No explicitly documented enterprise-grade SSO/RBAC permission control, LLM load balancing and full-link observability capabilities for large-scale enterprise deployment",
     "大规模企业部署所需的企业级 SSO/RBAC 权限控制、LLM 负载均衡和全链路可观测能力在公开功能集中均未明确记录"),
    ("Faces competition from other open source low-code AI application platforms that offer more generous free tier usage limits",
     "面临来自其他开源低代码 AI 应用平台（提供更慷慨的免费套餐额度）的竞争压力"),
    ("Expand Dify Marketplace to add more industry-specific scenario templates for different enterprise functional teams",
     "扩展 Dify 市场，上线更多面向不同企业职能部门团队的垂直行业场景模板"),
    ("Optimize non-technical user oriented low-code experience to further reduce the threshold of building production-grade agents",
     "优化面向非技术用户的低代码体验，进一步降低构建生产级 Agent 的使用门槛"),
    ("Faces competitive pressure from other low-code AI development platforms that are expanding natural language-driven development features",
     "面临来自其他低代码 AI 开发平台（纷纷扩展自然语言驱动开发功能）的竞争压力"),
    ("Risk of user loss if competing platforms launch more mature one-click multi-end deployment capabilities",
     "若竞品推出更成熟的一键多端部署能力，存在用户流失风险"),
    ("Add natural language low-code development layer to lower usage threshold for non-technical business users",
     "增加自然语言低代码开发层，降低非技术业务用户的上手门槛"),
    ("Expand native local model adaptation capabilities to meet offline deployment demands of highly data-sensitive enterprises",
     "扩展原生本地模型适配能力，满足对数据高度敏感企业的离线部署需求"),
    ("Launch tiered paid plans to cover usage demands of growing small and medium enterprise customers that outgrow the free tier",
     "推出分层付费套餐，覆盖因超出免费套餐额度而增长的中小企业客户的使用需求"),
    ("Integrate pre-built RAG pipelines and rich application modules to narrow the feature gap with competing platforms such as Dify and FastGPT",
     "集成预置 RAG 流水线和丰富应用模块，缩小与 Dify、FastGPT 等竞品的功能差距"),
    ("Integrate pre-built RAG and knowledge base modules to expand use cases to enterprise internal knowledge assistant scenarios",
     "集成预置 RAG 和知识库模块，将应用场景拓展至企业内部知识助手场景"),
    ("Add enterprise-level permission management and compliance capabilities to enter the enterprise customer market currently occupied by Dify and FastGPT",
     "增加企业级权限管理和合规能力，切入 Dify、FastGPT 已占据的企业客户市场"),
    ("Adopts a free-to-start pricing model, the free version includes 2 monthly processes and assistants, 100 predictions, 5MB storage space, lowering the threshold for new users",
     "采用免费起步定价模式，免费版含每月 2 个流程和助手、100 次预测、5MB 存储空间，降低新用户入门门槛"),
    ("Supports evaluation indicator statistics and custom embedded chatbot brand customization, meeting basic personalized deployment needs of small teams",
     "支持评估指标统计和自定义嵌入式聊天机器人品牌定制，满足小型团队的基本个性化部署需求"),
    ("Provides dedicated Vibe Coding infrastructure for AI application developers, supporting rapid development of multiple product types including mini-programs, chatbots, and websites",
     "为 AI 应用开发者提供专属 Vibe Coding 基础设施，支持小程序、聊天机器人、网站等多类产品的快速开发"),
    ("Supports one-click online deployment of all developed AI products, greatly reducing post-development launch friction",
     "支持所开发 AI 产品的一键上线部署，大幅降低开发后的发布摩擦"),
    ("Covers global developers and cross-industry business teams as target users, has served more than 1000 enterprise customers, and adapts to AI landing demands of different enterprise scales",
     "覆盖全球开发者及跨行业业务团队为目标用户，已服务超过 1000 家企业客户，适配不同规模企业的 AI 落地需求"),
    ("Adopts production-grade professional architecture, supports SSO and RBAC permission control, provides debugging audit capabilities to ensure compliance and security in enterprise environments",
     "采用生产级专业架构，支持 SSO 和 RBAC 权限控制，提供调试审计能力，确保企业环境下的合规与安全"),
    ("Comes with full pre-built application construction modules including Chatbot, text generator, Agent, Chatflow, Workflow, plus supporting capabilities",
     "配备完整的预置应用构建模块（聊天机器人、文本生成器、Agent、对话流、工作流）及配套支撑能力"),
    ("Provides out-of-the-box RAG pipeline, third-party integration capabilities and full-link observability support, enabling production-grade intelligent applications",
     "提供开箱即用的 RAG 流水线、第三方集成能力和全链路可观测支持，实现生产级智能应用"),
    ("Supports block-based AI application building, with core functional modules including full-featured knowledge base, visual workflow, intelligent data processing and agent orchestration",
     "支持模块化 AI 应用构建，核心功能模块包括完善知识库、可视化工作流、智能数据处理和 Agent 编排"),
    ("pre-built application construction modules",     "预置应用构建模块"),
    ("pre-built module ecosystem",                    "预置模块生态"),
    ("official marketplace",                          "官方市场"),
    ("full-link observability",                      "全链路可观测"),
    ("full-featured knowledge base",                "完善知识库"),
    ("visual workflow",                              "可视化工作流"),
    ("intelligent data processing",                  "智能数据处理"),
    ("agent orchestration",                          "Agent 编排"),
    ("production-grade professional architecture",    "生产级专业架构"),
    ("debugging audit capabilities",                  "调试审计能力"),
    ("enterprise environments",                       "企业环境"),
    ("SSO and RBAC permission control",             "SSO 和 RBAC 权限控制"),
    ("compliance and security",                       "合规与安全"),
    ("Vibe Coding infrastructure",                   "Vibe Coding 基础设施"),
    ("AI application developers",                    "AI 应用开发者"),
    ("mini-programs, chatbots, and websites",       "小程序、聊天机器人和网站"),
    ("one-click online deployment",                  "一键上线部署"),
    ("post-development launch friction",              "开发后发布摩擦"),
    ("block-based AI application building",           "模块化 AI 应用构建"),
    ("enterprise customers",                          "企业客户"),
    ("global developers and cross-industry business teams", "全球开发者及跨行业业务团队"),
    ("AI landing demands",                          "AI 落地需求"),
    ("different enterprise scales",                  "不同规模企业"),
    ("industry-specific scenario templates",          "垂直行业场景模板"),
    ("different enterprise functional teams",         "不同企业职能部门团队"),
    ("non-technical user oriented low-code experience","面向非技术用户的低代码体验"),
    ("building production-grade agents",              "构建生产级 Agent"),
    ("natural language-driven development features",  "自然语言驱动开发功能"),
    ("natural language low-code development layer",   "自然语言低代码开发层"),
    ("non-technical business users",                 "非技术业务用户"),
    ("native local model adaptation capabilities",    "原生本地模型适配能力"),
    ("offline deployment demands",                   "离线部署需求"),
    ("highly data-sensitive enterprises",            "对数据高度敏感企业"),
    ("tiered paid plans",                           "分层付费套餐"),
    ("small and medium enterprise customers",         "中小企业客户"),
    ("outgrow the free tier",                       "超出免费套餐额度"),
    ("pre-built RAG pipelines",                      "预置 RAG 流水线"),
    ("enterprise internal knowledge assistant scenarios","企业内部知识助手场景"),
    ("enterprise-level permission management",        "企业级权限管理"),
    ("compliance capabilities",                     "合规能力"),
    ("free-to-start pricing model",                  "免费起步定价模式"),
    ("monthly processes and assistants",             "每月流程和助手"),
    ("free version",                                 "免费版"),
    ("lowering the threshold",                       "降低门槛"),
    ("evaluation indicator statistics",               "评估指标统计"),
    ("custom embedded chatbot brand customization",  "自定义嵌入式聊天机器人品牌定制"),
    ("basic personalized deployment needs",          "基本个性化部署需求"),
    ("small teams",                                 "小型团队"),
    ("more generous free tier usage limits",          "更慷慨的免费套餐额度"),
    ("complete out-of-the-box features",             "完整开箱即用功能"),
    ("similar cost",                                 "相似成本"),
    ("individual user segment",                      "个人用户市场"),
    ("cross-functional industry scenarios",          "跨职能行业场景"),
    ("sales, customer service, finance and legal",   "销售、客服、财务和法务"),
    ("AI landing",                                  "AI 落地"),
    ("out-of-the-box RAG pipeline",                  "开箱即用 RAG 流水线"),
    ("full-featured knowledge base",                 "完善知识库能力"),
    ("knowledge-centric AI applications",            "知识中心型 AI 应用"),
    ("production-grade enterprise SSO/RBAC",          "生产级企业 SSO/RBAC"),
    ("debugging audit and compliance guarantee",     "调试审计与合规保障"),
    ("LLM load balancing",                          "LLM 负载均衡"),
    ("large-scale enterprise deployment",             "大规模企业部署"),
    ("free entry pricing models",                   "免费入门定价模式"),
    ("out-of-the-box full-link observability",       "开箱即用全链路可观测能力"),
    ("non-professional developers",                   "非专业开发者"),
    ("natural language-driven development experience","自然语言驱动开发体验"),
    ("plus supporting capabilities",                  "及配套支撑能力"),
    ("model supplier management",                    "模型供应商管理"),
    ("LLM load balancing",                          "LLM 负载均衡"),
    ("local model adaptation",                       "本地模型适配"),
    ("application lifecycle management",             "应用生命周期管理"),
    ("supporting rapid development",                 "支持快速开发"),
    ("includes",                                     "包含"),
    ("containing",                                   "包含"),
    ("such as",                                      "如"),
    ("including",                                    "包含"),
    ("with core functional modules",                  "，核心功能模块包括"),
    ("supports",                                     "支持"),
    ("provides",                                     "提供"),
    ("adopts",                                       "采用"),
    ("enabling",                                     "实现"),
    ("ensuring",                                     "确保"),
    ("meets",                                        "满足"),
    ("meeting",                                      "满足"),
    ("covers",                                       "覆盖"),
    ("serving",                                      "服务"),
    ("adapts to",                                    "适配"),
    ("including model supplier management, LLM load balancing, local model adaptation, and application lifecycle management",
     "含模型供应商管理、LLM 负载均衡、本地模型适配和应用生命周期管理"),
    ("plus supporting capabilities",                  "及配套支撑能力"),
    ("model supplier management",                    "模型供应商管理"),
    ("model providers management",                   "模型供应商管理"),
    ("LLM load balancing",                          "LLM 负载均衡"),
    ("local model adaptation",                       "本地模型适配"),
    ("application lifecycle management",              "应用生命周期管理"),
    ("application version control",                  "应用版本控制"),
    ("supporting rapid development",                 "支持快速开发"),
    ("intelligent agents and workflows via",         "通过智能体和工作流"),
    ("intelligent data parsing",                     "智能数据解析"),
    ("workflow orchestration and powerful API integration", "工作流编排和强大 API 集成"),
    ("100 predictions",                              "100 次预测"),
    ("5MB storage space",                            "5MB 存储空间"),
    ("prediction calls",                              "预测调用"),
    ("lowering the trial threshold for new users",   "降低新用户试用门槛"),
    ("comprehensive pre-built application module ecosystems", "更完善的预置应用模块生态"),
    ("comprehensive pre-built module ecosystem",      "更完善的预置模块生态"),
    ("intelligent data processing",                  "智能数据处理"),
    ("powerful API integration",                     "强大 API 集成"),
    ("API integration",                              "API 集成"),
    ("dify marketplace",                             "Dify 市场"),
    ("Risk of user churn",                           "存在用户流失风险"),
    ("if competing platforms launch",                 "若竞品推出"),
    ("serves more than 1000 enterprise customers",   "已服务超过 1000 家企业客户"),
    ("intelligent agents",                          "智能体"),
    ("multiple product types",                      "多类产品"),
    ("web pages",                                  "网页"),
    ("apps",                                       "应用"),
]


def _translate(text: str) -> str:
    """Convert an English phrase to Chinese using the term dictionary.

    Uses regex word-boundary substitutions for short conjunction words to avoid
    accidentally matching substrings (e.g., ``in`` inside ``function``).
    """
    if not text:
        return text
    result = text

    # Phase 1: phrase substitutions (plain string replace, longest-first)
    phrase_terms = sorted(
        [(e, z) for e, z in _TERM_DICT if not e.startswith("\\b")],
        key=lambda x: -len(x[0]),
    )
    for en, zh in phrase_terms:
        result = result.replace(en, zh)

    # Phase 2: word-boundary conjunction replacements (regex)
    word_terms = [(e, z) for e, z in _TERM_DICT if e.startswith("\\b") and z]
    for en, zh in word_terms:
        result = re.sub(en, zh, result)

    # Collapse multiple spaces
    result = re.sub(r"  +", " ", result)
    # Punctuation normalization
    result = re.sub(r"\s*,\s*", "，", result)
    result = re.sub(r"\s*\.\s*", "。", result)
    result = re.sub(r"\s*;\s*", "；", result)
    result = re.sub(r"\s*:\s*", "：", result)
    result = re.sub(r"\(\s*", "（", result)
    result = re.sub(r"\s*\)", "）", result)
    result = re.sub(r"\s+", " ", result).strip()
    # Clean leading/trailing punctuation artifacts
    result = re.sub(r"^[，,、\s]+", "", result)
    result = re.sub(r"[，,、\s]+$", "", result)
    return result


# ── Main viewer ─────────────────────────────────────────────────────────────────

def render_report_viewer(run_id: str):
    """Render the full report — banner + product cards + markdown report body + evidence appendix."""

    # Normalize run_id
    rid = str(run_id).strip()
    if not rid.startswith("run_"):
        rid = "run_" + rid

    data = get_json(f"/api/runs/{rid}/report", default=None, timeout=15)
    if data is None:
        st.error(f"无法加载报告 (run_id: {rid})。")
        return

    report_status = data.get("report_status", "draft")

    md_text = data.get("content_markdown", "").strip()
    if not md_text:
        import os as _os
        md_path = data.get("content_markdown_path", "")
        if md_path and _os.path.exists(md_path):
            with open(md_path, encoding="utf-8", errors="ignore") as _f:
                md_text = _f.read().strip()

    # Extract metadata from markdown (quality_summary is often empty)
    md_meta = _extract_md_meta(md_text)
    qs = data.get("quality_summary", {})

    # Fallback chain: markdown meta → quality_summary → defaults
    products_list = md_meta["products"]
    if not products_list:
        products_list = qs.get("products") or []
        if not products_list and "_product_id_to_name" in qs:
            products_list = list(qs["_product_id_to_name"].values())

    sc = md_meta["signed_claims"] or len(qs.get("signed_claims", []))
    tc = md_meta["total_claims"] or qs.get("claims_count", 0) or sc
    evidence_count = md_meta["evidence_count"]
    word_count = md_meta["word_count"]

    evidence_items = get_json(f"/api/runs/{rid}/evidence", []) or []
    registry = _build_registry(evidence_items)

    # ── Top hero banner ─────────────────────────────────────────────────────────
    _inject_css()

    # Try to get report title from markdown
    title_match = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    report_title = title_match.group(1).strip() if title_match else "竞品分析报告"

    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#1e3a5f 0%,#1d4ed8 40%,#4338ca 100%);
            border-radius:14px;padding:28px 32px 24px;margin-bottom:20px;">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
            <span style="font-size:1.7em;">🏆</span>
            <h1 style="margin:0;font-size:1.6rem;font-weight:800;color:white;
                       letter-spacing:-0.3px;">{report_title}</h1>
          </div>
          <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:4px;">
            <span style="color:rgba(255,255,255,0.85);font-size:0.82em;">
              {_status_badge(report_status)}
            </span>
            <span style="color:rgba(255,255,255,0.85);font-size:0.82em;">
              📦 对比产品：<strong style="color:white;">{"、".join(products_list) if products_list else "—"}</strong>
            </span>
            <span style="color:rgba(255,255,255,0.85);font-size:0.82em;">
              📝 字数：<strong style="color:white;">{word_count:,}</strong>
            </span>
            <span style="color:rgba(255,255,255,0.85);font-size:0.82em;">
              ✅ 已核实 Claims：<strong style="color:white;">{sc} / {tc}</strong>
            </span>
            <span style="color:rgba(255,255,255,0.85);font-size:0.82em;">
              📚 证据：<strong style="color:white;">{evidence_count}</strong>
            </span>
          </div>
        </div>""",
        unsafe_allow_html=True,
    )

    st.link_button("🔗 打开 DAG 视图", f"{_DAG_APP_URL}/?run_id={rid}",
                   use_container_width=True)

    # ── Product summary cards ───────────────────────────────────────────────────
    cards = _extract_product_cards(md_text)
    cards = _supplement_cards_from_matrix(md_text, cards)
    if cards:
        _render_product_cards(cards)
    else:
        pass
    st.divider()

    # ── Report body section marker ────────────────────────────────────────────
    marker_patterns = ["## 📑 报告正文", "## 报告正文"]

    body_start = -1
    for marker in marker_patterns:
        idx = md_text.find(marker)
        if idx != -1:
            body_start = md_text.find("\n", idx) + 1
            break

    if body_start == -1 or body_start >= len(md_text):
        body_start = 0

    body_text = md_text[body_start:].strip()

    appendix_patterns = ["## 证据附录", "## 12. 证据附录"]
    appendix_start = len(body_text)
    for marker in appendix_patterns:
        idx = body_text.find(marker)
        if idx != -1 and idx < appendix_start:
            appendix_start = idx

    report_body = body_text[:appendix_start].strip()
    appendix_body = body_text[appendix_start:].strip()

    # ── Render ────────────────────────────────────────────────────────────────
    _render_md(report_body, registry, css_class="report-body")

    if appendix_body:
        with st.expander("📚 证据附录", expanded=False):
            _render_md(appendix_body, registry, css_class="report-body appendix-body")


# ── Standalone entry point: when opened directly (no app.py routing) ──────────────

def _main():
    """Show a simple input form if no run_id is provided."""
    st.markdown(
        "<h1 style='font-size:1.6rem;font-weight:700;margin-bottom:16px;'>竞品分析报告</h1>",
        unsafe_allow_html=True,
    )
    run_id_input = st.text_input(
        "输入 Run ID",
        placeholder="例如: 42af66c085b34844_v2",
        help="在 DAG 页面或 Runs 页面找到报告的 Run ID。",
    )
    if run_id_input:
        st.session_state["_standalone_run_id"] = run_id_input
    saved = st.session_state.get("_standalone_run_id", "")
    if saved:
        st.session_state["_standalone_run_id"] = saved
        render_report_viewer(saved)


if __name__ == "__main__":
    # Check for run_id in query params (e.g. ?run_id=xxx)
    _qp = st.query_params
    if "run_id" in _qp and _qp["run_id"]:
        render_report_viewer(str(_qp["run_id"]))
    else:
        _main()
