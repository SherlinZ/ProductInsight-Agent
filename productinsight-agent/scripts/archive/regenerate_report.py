#!/usr/bin/env python3
"""
Regenerate the deep report for a run_id using existing evidence/claims.
Reads from the latest report JSON and calls run_deep_report_workflow directly.

Usage:
    python scripts/regenerate_report.py <run_id>
"""

import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

os.environ["WORK_DIR"] = str(PROJECT_DIR)

REPORT_DIR = PROJECT_DIR / "data" / "reports"


def main():
    if len(sys.argv) < 2:
        print("Usage: python regenerate_report.py <run_id>")
        sys.exit(1)

    run_id = sys.argv[1]

    # Find latest report JSON
    report_files = sorted(REPORT_DIR.glob(f"report_{run_id}_v*.json"))
    # Fallback: if no files found, try with "run_" prefix stripped
    if not report_files and run_id.startswith("run_"):
        alt_id = run_id[4:]  # strip "run_"
        report_files = sorted(REPORT_DIR.glob(f"report_run_{alt_id}_v*.json"))
    if not report_files:
        print(f"No report files found for run_id={run_id}")
        sys.exit(1)

    report_path = report_files[-1]
    print(f"Reading: {report_path}")

    with open(report_path) as f:
        report_data = json.load(f)

    signed_claims = report_data.get("signed_claims", [])
    print(f"Claims: {len(signed_claims)}")

    qs = report_data.get("quality_summary", {})
    evidence_items = qs.get("evidence_items", [])
    print(f"Evidence: {len(evidence_items)}")

    products = report_data.get("products", [])
    print(f"Products: {products}")

    # Build product_id_to_name mapping from evidence
    product_id_to_name = {}
    for ev in evidence_items:
        pid = ev.get("product_id", "")
        name = ev.get("product_name", "")
        if pid and name and pid not in product_id_to_name:
            product_id_to_name[pid] = name

    # Get product_id_to_name from metadata too
    product_id_to_name.update(report_data.get("_product_id_to_name", {}))
    print(f"Product ID mapping: {product_id_to_name}")

    # Also load from DB
    try:
        from backend.app.storage.repositories import EvidenceRepository, ClaimRepository, FactRepository
        ev_repo = EvidenceRepository()
        claim_repo = ClaimRepository()

        # Load evidence from DB
        db_evidence = ev_repo.list_evidence(run_id=run_id)
        print(f"Evidence from DB: {len(db_evidence)}")
        if db_evidence and not evidence_items:
            evidence_items = db_evidence

        # Load claims from DB
        db_claims = claim_repo.list_claims(run_id=run_id)
        print(f"Claims from DB: {len(db_claims)}")
        if db_claims and not signed_claims:
            signed_claims = [c for c in db_claims if c.get("review_status") == "signed"]
            print(f"Signed claims: {len(signed_claims)}")

    except Exception as e:
        print(f"DB load error (will use JSON data): {e}")

    # Load facts
    facts = []
    try:
        from backend.app.storage.fact_repository import FactRepository
        fact_repo = FactRepository()
        facts = fact_repo.list_facts(run_id=run_id) or []
        print(f"Facts from DB: {len(facts)}")
    except Exception as e:
        print(f"Facts load error: {e}")

    if not products:
        print("ERROR: No products found!")
        sys.exit(1)

    if not signed_claims:
        print("ERROR: No signed claims found!")
        sys.exit(1)

    # Run the deep report workflow
    print(f"\n{'='*60}")
    print(f"Starting report generation...")
    print(f"  run_id: {run_id}")
    print(f"  products: {products}")
    print(f"  claims: {len(signed_claims)}")
    print(f"  evidence: {len(evidence_items)}")
    print(f"  facts: {len(facts)}")
    print(f"{'='*60}\n")

    from backend.app.services.deep_report import run_deep_report_workflow

    try:
        result = run_deep_report_workflow(
            run_id=run_id,
            report_id=f"report_{run_id}_v2",
            products=products,
            signed_claims=signed_claims,
            facts=facts,
            evidence_items=evidence_items,
            product_id_to_name=product_id_to_name,
        )

        print(f"\n{'='*60}")
        print(f"Report generation complete!")
        print(f"  Report ID: {result.get('report_id', 'N/A')}")
        print(f"  Status: {result.get('status', 'N/A')}")
        md_path = result.get("content_markdown_path", "")
        html_path = result.get("content_html_path", "")
        print(f"  Markdown: {md_path}")
        print(f"  HTML: {html_path}")
        print(f"  Sections: {len(result.get('sections', []))}")
        print(f"  Tables: {len(result.get('tables', []))}")
        print(f"  Figures: {len(result.get('figures', []))}")
        print(f"  Quality summary: {json.dumps(result.get('quality_summary', {}), ensure_ascii=False, indent=2)}")
        print(f"{'='*60}")

    except Exception as e:
        print(f"\nERROR during report generation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
