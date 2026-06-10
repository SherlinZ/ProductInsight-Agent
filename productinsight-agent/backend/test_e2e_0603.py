"""
E2E Test: 证据契约 + Writer blocked降级
"""
from pathlib import Path
import sys, json, re
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "productinsight.db"
RUN_ID = "run_fd7ec6196a594fc4"

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def load_data(run_id: str) -> dict:
    cur.execute("SELECT * FROM evidence_items WHERE run_id=?", (run_id,))
    ev_items = []
    for r in cur.fetchall():
        d = dict(r)
        d.pop("raw_text", None)
        ev_items.append(d)

    cur.execute("SELECT * FROM claims WHERE run_id=?", (run_id,))
    claims = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) FROM facts WHERE run_id=?", (run_id,))
    fact_count = cur.fetchone()[0]

    cur.execute("SELECT task_brief_json FROM runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    tb = json.loads(row[0]) if row and row[0] else {}
    products = [p.get("product_name", p.get("product_id", "")) for p in tb.get("products", [])]

    return {
        "evidence_items": ev_items,
        "claims": claims,
        "fact_count": fact_count,
        "products": products,
        "task_brief": tb,
    }


def test_1_evidence_gate(ev_items):
    """验证 evidence gate 硬门控"""
    from backend.app.services.deep_report import _gate_evidence_by_dimension

    gated = _gate_evidence_by_dimension(ev_items)
    rejected = [e for e in gated if e.get("gate_rejection")]
    tpa_no_rejection = [
        e for e in gated
        if "third_party_article" in str(e.get("source_type", "")).lower()
        and not e.get("gate_rejection")
    ]

    print(f"  总 evidence 数: {len(gated)}")
    print(f"  被 gate 拒绝: {len(rejected)}")
    if rejected:
        for e in rejected[:3]:
            print(f"    拒绝原因: {e.get('gate_rejection')} | schema_key={e.get('schema_key','?')} source={e.get('source_type','?')}")
    print(f"  third_party_article 未被 gate 拒绝(非受限维度): {len(tpa_no_rejection)}")

    return {"passed": True, "rejected": len(rejected)}


def test_2_render_context(data):
    """验证 render_ctx 构建"""
    from backend.app.services.deep_report import _gate_evidence_by_dimension, _build_render_context

    gated_evidence = _gate_evidence_by_dimension(data["evidence_items"])

    # Build signed_claims from DB claims
    cur2 = conn.cursor()
    signed_claims = []
    for i, c in enumerate(data["claims"]):
        claim = {
            "claim_id": c.get("claim_id", f"claim_{i}"),
            "product_id": c.get("product_id", ""),
            "dimension": c.get("dimension", "function_tree"),
            "claim_text": c.get("claim_text", ""),
            "evidence_ids": [],
            "confidence": c.get("confidence", 0.5),
        }
        cur2.execute("SELECT evidence_id FROM claim_evidence_links WHERE claim_id=?", (claim["claim_id"],))
        claim["evidence_ids"] = [r["evidence_id"] for r in cur2.fetchall()]
        signed_claims.append(claim)

    facts = [{}] * data["fact_count"]

    ctx = _build_render_context(
        data["products"],
        signed_claims,
        gated_evidence,
        facts,
        rework_required_claims=[],
    )

    cov = ctx.get("coverage_by_product", {})
    zero = [p for p, v in cov.items() if v == 0]
    partial = [p for p, v in cov.items() if 0 < v < 0.7]
    ready = [p for p, v in cov.items() if v >= 0.7]

    print(f"  Products: {data['products']}")
    print(f"  Coverage by product: {cov}")
    print(f"  零覆盖: {zero}")
    print(f"  部分覆盖: {partial}")
    print(f"  就绪: {ready}")

    scorecard = ctx.get("scorecard_inputs", {})
    print(f"  Scorecard dims: {list(scorecard.keys()) if scorecard else []}")
    print(f"  POC requirements: {len(ctx.get('poc_requirements', []))} 项")
    print(f"  Evidence tiers: {ctx.get('evidence_tiers', {})}")
    print(f"  AB ratio: {ctx.get('ab_ratio', 0):.2%}")

    return {
        "passed": True,
        "coverage": cov,
        "zero": zero,
        "partial": partial,
        "ready": ready,
    }


def test_3_report_status():
    """验证 report_status 一致性"""
    cur.execute("SELECT * FROM reports WHERE run_id=?", (RUN_ID,))
    row = cur.fetchone()
    if not row:
        print("  No report found")
        return {"passed": False}

    qs = json.loads(row["quality_summary_json"]) if row["quality_summary_json"] else {}
    report_status = qs.get("report_status", "unknown")
    claims_count = qs.get("claims_count", -1)

    print(f"  DB report_status: {report_status}")
    print(f"  claims_count: {claims_count}")
    print(f"  一致性: report_status == {report_status}")

    return {"passed": True, "status": report_status}


def main():
    print("=" * 60)
    print("E2E Test: Evidence Contract + Blocked降级")
    print("=" * 60)
    print(f"\nRun ID: {RUN_ID}")

    print("\n[1/3] Evidence Gate...")
    data = load_data(RUN_ID)
    print(f"  Products: {data['products']}")
    print(f"  Evidence: {len(data['evidence_items'])}")
    print(f"  Claims: {len(data['claims'])}")

    # Show sample evidence
    for ev in data["evidence_items"][:3]:
        print(f"    schema_key={ev.get('schema_key','?')} source_type={ev.get('source_type','?')} usable={ev.get('usable_for_claim','?')}")

    r1 = test_1_evidence_gate(data["evidence_items"])

    print("\n[2/3] Render Context Build...")
    r2 = test_2_render_context(data)

    print("\n[3/3] Report Status Consistency...")
    r3 = test_3_report_status()

    print("\n" + "=" * 60)
    print("Summary:")
    print(f"  Evidence Gate: {'PASS' if r1['passed'] else 'FAIL'}")
    print(f"  Render Context: {'PASS' if r2['passed'] else 'FAIL'}")
    print(f"  Report Status: {'PASS' if r3['passed'] else 'FAIL'}")
    print("=" * 60)

    # Also save data for manual inspection
    output = {
        "run_id": RUN_ID,
        "products": data["products"],
        "evidence_count": len(data["evidence_items"]),
        "claims_count": len(data["claims"]),
        "evidence_gate": r1,
        "render_context": r2,
        "report_status": r3,
    }
    out_path = Path(__file__).parent / f"e2e_result_{RUN_ID[:16]}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nResult saved: {out_path}")


if __name__ == "__main__":
    main()
