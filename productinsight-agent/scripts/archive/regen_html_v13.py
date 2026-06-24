#!/usr/bin/env python3
"""Regenerate HTML for a given run_id from existing JSON report data."""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.app.services.deep_report import generate_html_report

RUN_ID = os.environ.get("RUN_ID", "run_9059278798c64c17")
JSON_PATH = os.path.join(PROJECT_ROOT, f"data/reports/report_{RUN_ID}_v2.json")
HTML_PATH = JSON_PATH.replace(".json", ".html")
MD_PATH = JSON_PATH.replace(".json", ".md")

if not os.path.exists(JSON_PATH):
    print(f"ERROR: JSON not found: {JSON_PATH}")
    sys.exit(1)

with open(JSON_PATH) as f:
    data = json.load(f)

html = generate_html_report(data)

with open(HTML_PATH, "w", encoding="utf-8") as f:
    f.write(html)

links = re.findall(r'class="ev-citation"', html)
raw_links = re.findall(r'href="#ev-E(\d+)"', html)
print(f"Generated HTML: {HTML_PATH} ({len(html):,} chars)")
print(f"Interactive [E:N] links: {len(links)}")
print(f"Unique cited evidence IDs: {len(set(raw_links))}")

# Verify the 选型建议速查 table specifically
scorecard_match = re.search(
    r'<h3>选型建议速查</h3>.*?(?=<h[34]|<div class="table-container|<h2)',
    html,
    re.DOTALL,
)
if scorecard_match:
    snippet = scorecard_match.group(0)
    n_tables = len(re.findall(r'<table>', snippet))
    n_strong = len(re.findall(r'<strong>[^<]+</strong>', snippet))
    n_remaining_stars = len(re.findall(r'\*\*[^*]+\*\*', snippet))
    print(f"\n=== 选型建议速查 表格验证 ===")
    print(f"  tables in section: {n_tables}")
    print(f"  <strong> cells: {n_strong}")
    print(f"  literal **残留: {n_remaining_stars}")
    if n_tables == 1 and n_strong >= 5 and n_remaining_stars == 0:
        print(f"  ✓ 表格渲染正确")
    else:
        print(f"  ⚠ 表格可能有问题")
else:
    print("\n⚠ 选型建议速查 表格未在 HTML 中找到")

# Sync file timestamps comment
print(f"\n  md  文件: {MD_PATH}")
print(f"  html 文件: {HTML_PATH}")
