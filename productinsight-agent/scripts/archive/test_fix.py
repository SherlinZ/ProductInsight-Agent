import sys, json, os

base = '/home/shijialin/paperworking/workflow_new/productinsight-agent'
# Actual filename has "report_" prefix
json_path = f'{base}/data/reports/report_run_3205f530fe2547a8_v2.json'

print("Loading:", json_path, "| exists:", os.path.exists(json_path))

sys.path.insert(0, f'{base}/backend')
from app.services.deep_report import generate_markdown_report

with open(json_path) as f:
    data = json.load(f)

print("Generating markdown...")
md = generate_markdown_report(data)

out_path = f'{base}/data/reports/report_run_3205f530fe2547a8_v2_fixed.md'
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(md)

print(f"Done! {len(md)} chars -> {out_path}")
print("\n=== First 120 lines (checking table headers) ===")
for i, line in enumerate(md.split('\n')[:120]):
    print(f'{i+1:3}: {line}')
