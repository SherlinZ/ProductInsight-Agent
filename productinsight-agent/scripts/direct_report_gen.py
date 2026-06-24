#!/usr/bin/env python3
"""Direct report generation test for run_4e21a613f1884090."""
import sys, os, json, sqlite3, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services.deep_report import run_deep_report_workflow, generate_markdown_report

RUN_ID = "run_4e21a613f1884090"
REPORT_ID = f"report_{RUN_ID}_v2"


def main():
    print(f"\n{'='*60}")
    print(f"DIRECT REPORT GENERATION TEST")
    print(f"Run ID: {RUN_ID}")
    print(f"{'='*60}")

    conn = sqlite3.connect("data/productinsight.db")
    conn.row_factory = sqlite3.Row

    # Products
    products = [r["product_name"] for r in conn.execute(
        "SELECT product_name FROM products WHERE run_id=?", (RUN_ID,)
    ).fetchall()]
    print(f"\nProducts ({len(products)}): {products}")

    # Evidence items - use * to avoid column name issues
    evidence_items = []
    for r in conn.execute(
        "SELECT * FROM evidence_items WHERE run_id=?", (RUN_ID,)
    ).fetchall():
        d = dict(r)
        evidence_items.append({
            "evidence_id": d.get("evidence_id"),
            "product_id": d.get("product_id"),
            "schema_key": d.get("schema_key"),
            "snippet": d.get("snippet"),
            "source_url": d.get("source_id"),
            "source_type": d.get("source_type"),
            "trust_tier": d.get("trust_tier"),
            "quality_score": d.get("quality_score"),
            "usable_for_claim": bool(d.get("usable_for_claim")),
        })
    print(f"Evidence items: {len(evidence_items)}")

    # Claims
    all_claims = []
    for r in conn.execute(
        "SELECT * FROM claims WHERE run_id=?", (RUN_ID,)
    ).fetchall():
        d = dict(r)
        ev_ids = d.get("evidence_ids_json") or d.get("evidence_ids") or "[]"
        if isinstance(ev_ids, str):
            ev_ids = json.loads(ev_ids)
        all_claims.append({
            "claim_id": d.get("claim_id"),
            "product_id": d.get("product_id"),
            "schema_key": d.get("schema_key") or d.get("dimension"),
            "claim_text": d.get("claim_text"),
            "evidence_ids": ev_ids,
            "review_status": d.get("review_status"),
        })

    signed_claims = [c for c in all_claims if c["review_status"] == "signed"]
    print(f"Total claims: {len(all_claims)}, Signed: {len(signed_claims)}")

    conn.close()

    if not evidence_items:
        print("WARNING: No evidence - testing LLM-knowledge fallback")
    if not signed_claims:
        print("WARNING: No signed claims - low evidence density expected")

    # Call run_deep_report_workflow
    print(f"\nCalling run_deep_report_workflow...")
    t0 = time.time()

    try:
        result = run_deep_report_workflow(
            run_id=RUN_ID,
            report_id=REPORT_ID,
            signed_claims=all_claims,
            facts=[],
            evidence_items=evidence_items,
            products=products,
            research_plan=None,
            schema_type="competitor_landscape",
            domain_schema=None,
            query_understanding={"report_type": "product_selection"},
            rework_required_claims=[],
            analyst_signed_claims=[],
            product_id_to_name={p: p for p in products},
        )
        print(f"\nCompleted in {time.time()-t0:.1f}s")

        qs = result.get("quality_summary", {})
        print(f"\nQuality Summary:")
        print(f"  Words: {qs.get('total_word_count', '?')}")
        print(f"  Sections: {qs.get('section_count', '?')}")
        print(f"  Tables: {qs.get('table_count', '?')}")
        print(f"  Figures: {qs.get('figure_count', '?')}")
        print(f"  Evidence: {qs.get('evidence_count', '?')}")
        print(f"  Claims: {qs.get('claims_count', '?')}")
        print(f"  Status: {qs.get('report_status', '?')}")

        sections = result.get("sections", [])
        print(f"\nSections ({len(sections)}):")
        for s in sections:
            print(f"  {s['section_slug']} ({s['section_title']}) - {s.get('word_count', 0)}w")

        # Save markdown
        md = generate_markdown_report(result)
        md_path = f"data/reports/report_{RUN_ID}_v2.md"
        os.makedirs("data/reports", exist_ok=True)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\nMarkdown: {md_path} ({len(md):,} chars)")

        # Quality checks
        print(f"\n{'='*60}")
        print("QUALITY CHECKS")
        print(f"{'='*60}")
        checks = [
            ("竞品选择逻辑", "竞品选择逻辑章节"),
            ("市场定位图", "市场定位图章节"),
            ("竞品画像", "竞品画像章节"),
            ("SWOT", "SWOT分析"),
            ("产品概览卡片", "产品概览卡片"),
            ("证据附录", "证据附录"),
            ("暂无公开可验证信息", "待补证提示（应无）"),
        ]
        all_ok = True
        for pattern, label in checks:
            found = pattern in md
            ok = found if "应无" not in label else not found
            label = label.replace("（应无）", "")
            print(f"  {'✅' if ok else '❌'} {label}")
            if not ok:
                all_ok = False

        print(f"\n{'✅ ALL PASSED' if all_ok else '❌ SOME FAILED'}")
        return 0 if all_ok else 1

    except Exception as e:
        import traceback
        print(f"\n❌ ERROR: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
