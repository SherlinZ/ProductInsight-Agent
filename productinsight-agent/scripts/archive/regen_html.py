#!/usr/bin/env python3
"""Regenerate HTML from existing JSON report data (after enrich fix)."""
import json, sys, os, re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.app.services.deep_report import generate_html_report

JSON_PATH = os.path.join(PROJECT_ROOT, "data/reports/report_run_b5f3b4c56ea34391_v2.json")
HTML_PATH = JSON_PATH.replace(".json", ".html")

with open(JSON_PATH) as f:
    data = json.load(f)

html = generate_html_report(data)

with open(HTML_PATH, "w", encoding="utf-8") as f:
    f.write(html)

links = re.findall(r'class="ev-citation"', html)
raw_links = re.findall(r'href="#ev-E(\d+)"', html)
print(f"Generated HTML: {HTML_PATH} ({len(html)} chars)")
print(f"Interactive [E:N] links: {len(links)}")
print(f"Unique cited evidence IDs: {sorted(set(raw_links))}")
