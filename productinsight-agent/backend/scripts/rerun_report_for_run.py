"""Re-run Deep Report v2 assembly for an existing run.

Uses the existing evidence_items + claims + claim_evidence_links as the
"signed_claims" input to run_deep_report_workflow, skipping the
collection/claim-generation stages. This is the fastest way to verify
the _generate_evidence_strength_matrix fix in a real report context.

This writes the output to data/reports/report_run_<id>_v2.{md,html,json}
alongside the original.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path("/home/shijialin/paperworking/workflow_new/productinsight-agent")
sys.path.insert(0, str(REPO))

# CRITICAL: set DATABASE_URL & WORK_DIR before any backend imports
os.environ.setdefault("DATABASE_URL", f"sqlite:///{REPO / 'data' / 'productinsight.db'}")
os.environ.setdefault("WORK_DIR", str(REPO))

from backend.app.services.deep_report import run_deep_report_workflow  # noqa: E402

DB_PATH = REPO / "data" / "productinsight.db"
REPORT_DIR = REPO / "data" / "reports"

RUN_ID = "run_f9b7f31c8db04cfc"


def load_data(run_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Products
    cur.execute("SELECT * FROM products WHERE run_id=?", (run_id,))
    products = [dict(r) for r in cur.fetchall()]

    # Evidence items
    cur.execute("SELECT * FROM evidence_items WHERE run_id=?", (run_id,))
    ev_rows = [dict(r) for r in cur.fetchall()]
    for ev in ev_rows:
        ev.pop("raw_text", None)
        # Normalize product_name from product_id
        pid = ev.get("product_id", "")
        for p in products:
            if p.get("product_id") == pid:
                ev["product_name"] = p.get("product_name", "")
                ev["product_slug"] = p.get("product_name", "").lower()
                break

    # Claims (used as signed_claims input)
    cur.execute("SELECT * FROM claims WHERE run_id=?", (run_id,))
    claim_rows = [dict(r) for r in cur.fetchall()]

    # Claim-evidence links
    cur.execute("SELECT * FROM claim_evidence_links WHERE run_id=?", (run_id,))
    link_rows = [dict(r) for r in cur.fetchall()]
    # Build evidence_ids per claim
    links_by_claim: dict[str, list[str]] = {}
    for ln in link_rows:
        cid = ln.get("claim_id", "")
        eid = ln.get("evidence_id", "")
        links_by_claim.setdefault(cid, []).append(eid)

    signed_claims = []
    for c in claim_rows:
        cid = c.get("claim_id", "")
        eids = links_by_claim.get(cid, [])
        # Resolve product_name
        pid = c.get("product_id", "")
        pname = ""
        for p in products:
            if p.get("product_id") == pid:
                pname = p.get("product_name", "")
                break
        signed_claims.append({
            "claim_id": cid,
            "run_id": run_id,
            "product_id": pid,
            "product_name": pname,
            "dimension": c.get("dimension", "function_tree"),
            "claim_text": c.get("claim_text", ""),
            "fact_ids": c.get("fact_ids", []) or [],
            "evidence_ids": eids,
            "confidence": c.get("confidence", 0.5),
            "risk_level": c.get("risk_level", "low"),
            "claim_type": c.get("claim_type", "factual_summary"),
            "review_status": "signed",  # mark as signed
        })

    # Run metadata
    cur.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    run = dict(row) if row else {}
    conn.close()

    return {
        "products": [p.get("product_name", "") for p in products if p.get("product_name")],
        "evidence_items": ev_rows,
        "signed_claims": signed_claims,
        "run": run,
    }


def main():
    print(f"== Loading {RUN_ID} from DB ==")
    data = load_data(RUN_ID)
    print(f"  products: {data['products']}")
    print(f"  evidence_items: {len(data['evidence_items'])}")
    print(f"  signed_claims: {len(data['signed_claims'])}")

    # Build product_id_to_name
    product_id_to_name = {}
    for ev in data["evidence_items"]:
        pid = ev.get("product_id", "")
        pname = ev.get("product_name", "")
        if pid and pname:
            product_id_to_name[pid] = pname
    print(f"  product_id_to_name: {product_id_to_name}")

    # Parse task_brief if available
    task_brief = {}
    if data["run"].get("task_brief_json"):
        try:
            task_brief = json.loads(data["run"]["task_brief_json"])
        except Exception:
            pass
    elif data["run"].get("task_brief"):
        task_brief = data["run"]["task_brief"]
    print(f"  task_brief keys: {list(task_brief.keys()) if task_brief else 'NONE'}")

    # Backup old reports (so we can compare)
    for suffix in ("_v2", ""):
        for ext in ("md", "html", "json"):
            p = REPORT_DIR / f"report_{RUN_ID}{suffix}.{ext}"
            if p.exists():
                bak = p.with_suffix(f".pre-fix.{ext}")
                shutil.copy(p, bak)
                print(f"  backed up: {p.name} → {bak.name}")

    # Run workflow
    print(f"\n== Running Deep Report v2 workflow ==")
    report_id = f"report_{RUN_ID}_v2"
    result = run_deep_report_workflow(
        run_id=RUN_ID,
        report_id=report_id,
        signed_claims=data["signed_claims"],
        facts=[],
        evidence_items=data["evidence_items"],
        products=data["products"],
        research_plan=task_brief or None,
        product_id_to_name=product_id_to_name,
    )
    print(f"\n== Result ==")
    print(json.dumps({k: v for k, v in result.items() if k not in ("signed_claims", "evidence_registry")}, ensure_ascii=False, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
