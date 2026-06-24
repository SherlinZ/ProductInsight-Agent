"""Patch a single report's '本报告底气有多足' section by calling the fixed
_generate_evidence_strength_matrix directly on its signed_claims.

This is the fast verification path: skip the full LLM workflow, just regenerate
the one section that contained the 0%-coverage bug.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path("/home/shijialin/paperworking/workflow_new/productinsight-agent")
sys.path.insert(0, str(REPO))

from backend.app.services.deep_report import _generate_evidence_strength_matrix  # noqa: E402

REPORT_DIR = REPO / "data" / "reports"


def patch_report(run_id: str) -> None:
    md_path = REPORT_DIR / f"report_{run_id}_v2.md"
    json_path = REPORT_DIR / f"report_{run_id}_v2.json"
    html_path = REPORT_DIR / f"report_{run_id}_v2.html"

    if not md_path.exists():
        print(f"!! {md_path} not found")
        return

    md = md_path.read_text()
    lines = md.splitlines(keepends=True)

    # Find the start of "## 15. 本报告底气有多足" (or "## N. 本报告底气有多足")
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## ") and "本报告底气有多足" in s:
            start = i
            break
    if start is None:
        print(f"!! '本报告底气有多足' section not found in {md_path}")
        return

    # Find the next "## " section after start
    end = None
    for i in range(start + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("## "):
            end = i
            break
    if end is None:
        end = len(lines)

    print(f"  Old section: lines {start + 1}..{end}  ({end - start} lines)")
    print(f"  --- First line: {lines[start].rstrip()}")
    print(f"  --- Last line:  {lines[end - 1].rstrip()}")

    # Load the data and run the patched function
    data = json.load(open(json_path))
    sc = data.get("signed_claims", [])
    products = data.get("products", [])
    if not sc or not products:
        print(f"  !! No signed_claims / products in json — skipping")
        return

    render_ctx = {
        "products": products,
        "signed_claims": sc,
        "coverage_by_product": data.get("coverage_by_product", {}) or {p: 0.5 for p in products},
        "evidence_items": data.get("evidence_registry", []),
    }
    new_section = _generate_evidence_strength_matrix(
        f"report_{run_id}_v2", run_id, render_ctx
    )
    # The function returns content WITHOUT the "## N. 本报告底气有多足" header.
    # We must add it back (matching the existing numbering if any).
    header = lines[start]  # e.g. "## 15. 本报告底气有多足\n"
    new_block = header + ("\n" + new_section if not new_section.startswith("\n") else new_section)
    if not new_block.endswith("\n"):
        new_block += "\n"

    # Build the patched file
    patched = "".join(lines[:start]) + new_block + "".join(lines[end:])

    # Backup original
    bak = md_path.with_suffix(".pre-fix-v2.md")
    if not bak.exists():
        shutil.copy(md_path, bak)
    md_path.write_text(patched)
    print(f"  ✓ Patched {md_path}  (old {len(md)} → new {len(patched)} chars)")
    print(f"  ✓ Backup at {bak}")

    # Patch the JSON too: store the new section under the same key
    # (don't touch the rest of the JSON)
    try:
        d = json.load(open(json_path))
        # Section content lives under d.get("sections", []) with title="本报告底气有多足"
        if "sections" in d:
            for sec in d["sections"]:
                if "本报告底气有多足" in sec.get("title", ""):
                    sec["content_markdown"] = new_section
                    sec["content_html"] = None  # HTML is regenerated on view
                    break
            json_path.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str))
            print(f"  ✓ Patched {json_path} (section content updated)")
    except Exception as e:
        print(f"  !! JSON patch failed: {e}")

    # Don't patch HTML — Streamlit may rebuild it on demand from the JSON.
    # But we can regenerate it now for the user's local open.
    try:
        from backend.app.services.deep_report import _markdown_to_html  # type: ignore
        new_html = _markdown_to_html(patched)
        html_path.write_text(new_html, encoding="utf-8")
        print(f"  ✓ Patched {html_path}")
    except Exception as e:
        print(f"  !! HTML patch failed: {e}")


def main():
    for run_id in (
        "run_9059278798c64c17",  # 4 products, exactly matches user's screenshot
        "run_f9b7f31c8db04cfc",   # also 4 products
    ):
        path = REPORT_DIR / f"report_{run_id}_v2.md"
        if not path.exists():
            continue
        print(f"\n== Patching {run_id} ==")
        patch_report(run_id)


if __name__ == "__main__":
    main()
