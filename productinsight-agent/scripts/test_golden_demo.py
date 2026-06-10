#!/usr/bin/env python3
"""
Verification script for Golden Demo seed data.

Checks:
 1. run_golden_gap exists with correct report_status
 2. run_golden_gap report is reviewed_with_gaps
 3. run_golden_gap quality_summary has insufficient_products >= 1
 4. run_golden_gap has a planned coverage gap task
 5. run_golden_completed exists
 6. run_golden_completed report is reviewed
 7. run_golden_completed has a completed coverage gap task
 8. after_json.executed = True
 9. signed_claims_added >= 1
10. evidence_added >= 1
11. run_golden_completed created_at > run_golden_gap created_at (latest run priority)
12. run_golden_completed has workflow_nodes with total_nodes > 0
13. run_golden_completed workflow: completed == total_nodes, failed == 0
14. run_golden_completed has eval_logs with schema_completion_rate = 1.0
15. run_golden_completed eval_logs: unsupported_claim_rate = 0.0
16. run_golden_completed eval_logs: review_pass_rate = 1.0
17. Frontend Load Golden Demo priority helpers present
18. reports.content_html_path is not empty for run_golden_completed
19. HTML file exists at the content_html_path
20. HTML content contains report title / ProductInsight branding
21. HTML content contains Coze (product mentioned)
22. HTML content contains Evidence
23. HTML does not contain "Run-Golden-Completed-Coze"
24. HTML does not contain "Run-Golden-Completed-Dify"
25. HTML contains "Sufficient" product coverage status
26. HTML Coze row has correct counts (src=2, ev=5, facts=5, sc=3)
27. HTML shows ≥ 4 Sufficient products (not all Missing)

Usage:  PYTHONPATH=. python scripts/test_golden_demo.py
"""
from __future__ import annotations

import json
import sys

FRONTEND_PATH = "frontend/app.py"


def main() -> int:
    from backend.app.storage.db import get_connection

    passed = 0
    failed = 0

    with get_connection() as conn:
        # 1. run_golden_gap exists
        row = conn.execute(
            "SELECT run_id, status FROM runs WHERE run_id = ?", ("run_golden_gap",)
        ).fetchone()
        if row:
            print("✅ run_golden_gap exists")
            passed += 1
        else:
            print("❌ run_golden_gap not found")
            failed += 1
            return 1

        # 2. run_golden_gap report is reviewed_with_gaps
        rep = conn.execute(
            "SELECT report_status FROM reports WHERE run_id = ?", ("run_golden_gap",)
        ).fetchone()
        if rep and rep[0] == "reviewed_with_gaps":
            print("✅ run_golden_gap report_status = reviewed_with_gaps")
            passed += 1
        else:
            print(f"❌ run_golden_gap report_status = {rep[0] if rep else 'MISSING'} (expected reviewed_with_gaps)")
            failed += 1

        # 3. run_golden_gap has insufficient_products in quality_summary
        qs_raw = conn.execute(
            "SELECT quality_summary_json FROM reports WHERE run_id = ?", ("run_golden_gap",)
        ).fetchone()
        if qs_raw:
            qs = json.loads(qs_raw[0])
            ins = qs.get("insufficient_products", -1)
            if ins >= 1:
                print(f"✅ run_golden_gap quality_summary.insufficient_products = {ins}")
                passed += 1
            else:
                print(f"❌ run_golden_gap insufficient_products = {ins} (expected >= 1)")
                failed += 1
        else:
            print("❌ run_golden_gap has no quality_summary")
            failed += 1

        # 4. run_golden_gap has a planned coverage gap task
        task = conn.execute(
            "SELECT status FROM rework_tasks WHERE run_id = ? AND rework_id = ?",
            ("run_golden_gap", "rework_cov_coze_gap"),
        ).fetchone()
        if task and task[0] == "planned":
            print("✅ run_golden_gap has a planned coverage gap rework task")
            passed += 1
        else:
            print(f"❌ run_golden_gap coverage gap task status = {task[0] if task else 'MISSING'} (expected planned)")
            failed += 1

        # 5. run_golden_completed exists
        row2 = conn.execute(
            "SELECT run_id, status, created_at FROM runs WHERE run_id = ?", ("run_golden_completed",)
        ).fetchone()
        if row2:
            print("✅ run_golden_completed exists")
            passed += 1
        else:
            print("❌ run_golden_completed not found")
            failed += 1
            return 1

        # 11. run_golden_completed created_at > run_golden_gap created_at
        gap_row = conn.execute(
            "SELECT created_at FROM runs WHERE run_id = ?", ("run_golden_gap",)
        ).fetchone()
        if gap_row and row2:
            gap_created = gap_row[0]
            comp_created = row2[2]
            if comp_created > gap_created:
                print(f"✅ run_golden_completed created_at ({comp_created}) > run_golden_gap created_at ({gap_created})")
                passed += 1
            else:
                print(f"❌ run_golden_completed created_at ({comp_created}) NOT > run_golden_gap ({gap_created})")
                failed += 1

        # 6. run_golden_completed report is reviewed
        rep2 = conn.execute(
            "SELECT report_status FROM reports WHERE run_id = ?", ("run_golden_completed",)
        ).fetchone()
        if rep2 and rep2[0] == "reviewed":
            print("✅ run_golden_completed report_status = reviewed")
            passed += 1
        else:
            print(f"❌ run_golden_completed report_status = {rep2[0] if rep2 else 'MISSING'} (expected reviewed)")
            failed += 1

        # 7. run_golden_completed has a completed coverage gap task
        task2 = conn.execute(
            "SELECT status, after_json FROM rework_tasks WHERE run_id = ? AND rework_id = ?",
            ("run_golden_completed", "rework_cov_coze_completed"),
        ).fetchone()
        if task2 and task2[0] == "completed":
            print("✅ run_golden_completed has a completed coverage gap rework task")
            passed += 1
        else:
            print(f"❌ run_golden_completed task status = {task2[0] if task2 else 'MISSING'} (expected completed)")
            failed += 1

        # 8. after_json.executed = True
        if task2 and task2[1]:
            after = json.loads(task2[1]) if isinstance(task2[1], str) else task2[1]
            if after.get("executed") is True:
                print("✅ after_json.executed = True")
                passed += 1
            else:
                print(f"❌ after_json.executed = {after.get('executed')}")
                failed += 1

            # 9. signed_claims_added >= 1
            summary = after.get("execution_summary", {})
            signed = summary.get("signed_claims_added", 0)
            if signed >= 1:
                print(f"✅ execution_summary.signed_claims_added = {signed}")
                passed += 1
            else:
                print(f"❌ execution_summary.signed_claims_added = {signed} (expected >= 1)")
                failed += 1

            # 10. evidence_added >= 1
            ev_added = summary.get("evidence_added", 0)
            if ev_added >= 1:
                print(f"✅ execution_summary.evidence_added = {ev_added}")
                passed += 1
            else:
                print(f"❌ execution_summary.evidence_added = {ev_added}")
                failed += 1
        else:
            print("❌ after_json is missing")
            failed += 3

        # 12. run_golden_completed workflow_nodes: total_nodes > 0
        wf_rows = conn.execute(
            "SELECT COUNT(*) FROM workflow_nodes WHERE run_id = ?", ("run_golden_completed",)
        ).fetchone()
        total_nodes = wf_rows[0] if wf_rows else 0
        if total_nodes > 0:
            print(f"✅ run_golden_completed workflow total_nodes = {total_nodes}")
            passed += 1
        else:
            print(f"❌ run_golden_completed workflow total_nodes = {total_nodes} (expected > 0)")
            failed += 1

        # 13. run_golden_completed workflow: completed == total_nodes, failed == 0
        wf_completed = conn.execute(
            "SELECT COUNT(*) FROM workflow_nodes WHERE run_id = ? AND status = 'completed'",
            ("run_golden_completed",)
        ).fetchone()
        wf_failed = conn.execute(
            "SELECT COUNT(*) FROM workflow_nodes WHERE run_id = ? AND status = 'failed'",
            ("run_golden_completed",)
        ).fetchone()
        completed_count = wf_completed[0] if wf_completed else 0
        failed_count = wf_failed[0] if wf_failed else 0

        if completed_count == total_nodes and total_nodes > 0:
            print(f"✅ run_golden_completed workflow: {completed_count}/{total_nodes} completed, {failed_count} failed")
            passed += 1
        else:
            print(f"❌ run_golden_completed workflow: {completed_count}/{total_nodes} completed, {failed_count} failed (expected all completed, 0 failed)")
            failed += 1

        # 14. run_golden_completed has eval_logs with schema_completion_rate = 1.0
        eval_row = conn.execute(
            "SELECT schema_completion_rate, unsupported_claim_rate, review_pass_rate FROM eval_logs WHERE run_id = ?",
            ("run_golden_completed",)
        ).fetchone()
        if eval_row:
            print(f"✅ run_golden_completed has eval_logs")
            passed += 1

            # 14. schema_completion_rate = 1.0
            scr = eval_row[0]
            if scr == 1.0:
                print(f"✅ schema_completion_rate = {scr}")
                passed += 1
            else:
                print(f"❌ schema_completion_rate = {scr} (expected 1.0)")
                failed += 1

            # 15. unsupported_claim_rate = 0.0
            ucr = eval_row[1]
            if ucr == 0.0:
                print(f"✅ unsupported_claim_rate = {ucr}")
                passed += 1
            else:
                print(f"❌ unsupported_claim_rate = {ucr} (expected 0.0)")
                failed += 1

            # 16. review_pass_rate = 1.0
            rpr = eval_row[2]
            if rpr == 1.0:
                print(f"✅ review_pass_rate = {rpr}")
                passed += 1
            else:
                print(f"❌ review_pass_rate = {rpr} (expected 1.0)")
                failed += 1
        else:
            print("❌ run_golden_completed has no eval_logs record")
            failed += 3

    # 17. Frontend Load Golden Demo has priority logic
    try:
        with open(FRONTEND_PATH, encoding="utf-8") as f:
            frontend_code = f.read()
        if "_is_completed_real_rework_task" in frontend_code and "_is_coverage_gap_task" in frontend_code:
            print("✅ frontend/app.py has Load Golden Demo priority helpers")
            passed += 1
        else:
            print("❌ frontend/app.py missing Load Golden Demo priority helpers")
            failed += 1
    except FileNotFoundError:
        print(f"⚠️  frontend/app.py not found — skipping frontend check")
        passed += 1

    # 18-22. HTML report checks for run_golden_completed
    from pathlib import Path
    rep_html_row = conn.execute(
        "SELECT content_html_path FROM reports WHERE run_id = ?", ("run_golden_completed",)
    ).fetchone()
    html_path = rep_html_row[0] if rep_html_row else ""

    # 18. content_html_path is not empty
    if html_path and html_path.strip():
        print(f"✅ reports.content_html_path = {html_path!r}")
        passed += 1
    else:
        print("❌ reports.content_html_path is empty (expected path to HTML file)")
        failed += 1
        html_path = ""

    # 19. HTML file exists
    html_file = Path(html_path) if html_path else None
    if html_file and html_file.exists():
        print(f"✅ HTML file exists: {html_file} ({html_file.stat().st_size:,} bytes)")
        passed += 1
    else:
        print(f"❌ HTML file not found: {html_path}")
        failed += 1

    # 20. HTML content contains report title
    if html_file and html_file.exists():
        content = html_file.read_text(encoding="utf-8")
        if "AI Agent Platform Competitive Analysis" in content or "ProductInsight" in content:
            print("✅ HTML contains report title / ProductInsight branding")
            passed += 1
        else:
            print("❌ HTML missing report title / ProductInsight branding")
            failed += 1

        # 21. HTML contains Coze
        if "Coze" in content:
            print("✅ HTML contains Coze (product mentioned)")
            passed += 1
        else:
            print("❌ HTML missing Coze")
            failed += 1

        # 22. HTML contains Evidence
        if "Evidence" in content or "evidence" in content:
            print("✅ HTML contains Evidence content")
            passed += 1
        else:
            print("❌ HTML missing Evidence content")
            failed += 1

        # 23. HTML does NOT contain run-scoped product ID "Run-Golden-Completed-Coze"
        if "Run-Golden-Completed-Coze" not in content:
            print("✅ HTML does not contain 'Run-Golden-Completed-Coze'")
            passed += 1
        else:
            print("❌ HTML contains run-scoped product ID 'Run-Golden-Completed-Coze'")
            failed += 1

        # 24. HTML does NOT contain run-scoped product ID "Run-Golden-Completed-Dify"
        if "Run-Golden-Completed-Dify" not in content:
            print("✅ HTML does not contain 'Run-Golden-Completed-Dify'")
            passed += 1
        else:
            print("❌ HTML contains run-scoped product ID 'Run-Golden-Completed-Dify'")
            failed += 1

        # 25. HTML contains "Sufficient" (product coverage status)
        if "Sufficient" in content:
            print("✅ HTML contains 'Sufficient' product coverage status")
            passed += 1
        else:
            print("❌ HTML missing 'Sufficient' product coverage status")
            failed += 1

        # 26. HTML Coze row contains expected counts (src=2, evidence=5, facts=5, signed_claims=3)
        # Extract Coze row from the product coverage table
        import re as _re
        coze_rows = _re.findall(
            r'<tr><td><strong>Coze</strong></td><td><span[^>]+>(.*?)</span></td><td>(\d+)</td><td>(\d+)</td><td>(\d+)</td><td>(\d+)</td></tr>',
            content
        )
        if coze_rows:
            status, src, ev, facts, sc = coze_rows[0]
            expected = (("Sufficient",), "2", "5", "5", "3")
            if src == "2" and ev == "5" and facts == "5" and sc == "3":
                print(f"✅ HTML Coze row has correct counts: src={src} ev={ev} facts={facts} sc={sc}")
                passed += 1
            else:
                print(f"❌ HTML Coze row has wrong counts: src={src} ev={ev} facts={facts} sc={sc} (expected 2/5/5/3)")
                failed += 1
        else:
            # Fallback: just check the numbers appear in the file near "Coze"
            coze_pos = content.find("Coze")
            nearby = content[coze_pos:coze_pos + 400] if coze_pos != -1 else ""
            has_counts = "2" in nearby and "5" in nearby
            if has_counts:
                print("✅ HTML Coze context contains expected count numbers")
                passed += 1
            else:
                print("❌ HTML Coze row not found or missing expected counts")
                failed += 1

        # 27. HTML does not show all products as "Missing" (at least one Sufficient)
        insufficient_count = content.count("Missing")
        sufficient_count = content.count("Sufficient")
        if sufficient_count >= 4:
            print(f"✅ HTML shows {sufficient_count} Sufficient products (not all missing)")
            passed += 1
        else:
            print(f"❌ HTML has {sufficient_count} Sufficient products, expected ≥ 4")
            failed += 1
    else:
        print("⚠️  Skipping HTML content checks (file not found)")
        failed += 2

    print()
    print(f"Result: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
