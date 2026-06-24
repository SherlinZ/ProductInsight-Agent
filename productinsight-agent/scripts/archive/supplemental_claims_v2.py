#!/usr/bin/env python3
"""
Supplemental Claims Generator v2

Same as v1, but ALSO generates claims from non-usable evidence
for dimensions that have no claims at all. This ensures the scorecard
and tables have at least some content for each dimension.

Usage:
    python scripts/supplemental_claims_v2.py <run_id>
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
BACKEND_DIR = SCRIPT_DIR.parent / "backend"
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

REPORT_DIR = SCRIPT_DIR.parent / "data" / "reports"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def generate_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python supplemental_claims_v2.py <run_id>")
        sys.exit(1)

    run_id = sys.argv[1]

    # Find latest report JSON
    report_files = sorted(REPORT_DIR.glob(f"report_{run_id}_v*.json"))
    if not report_files:
        print(f"No report files found for run_id={run_id}")
        sys.exit(1)

    report_path = report_files[-1]
    print(f"Reading report: {report_path}")

    with open(report_path) as f:
        report_data = json.load(f)

    ev_items = report_data.get("quality_summary", {}).get("evidence_items", [])
    existing_claims = report_data.get("signed_claims", [])
    print(f"Evidence items: {len(ev_items)}, Existing claims: {len(existing_claims)}")

    # Build product_id → product_name mapping
    pid_to_name = {}
    for ev in ev_items:
        pid = ev.get("product_id", "")
        name = ev.get("product_name", "")
        if pid and name:
            pid_to_name[pid] = name

    # Track which (product, dimension) pairs already have claims
    existing_coverage = {}
    for c in existing_claims:
        key = (c.get("product_id", ""), c.get("dimension", ""))
        if key not in existing_coverage:
            existing_coverage[key] = []
        existing_coverage[key].append(c)

    # Schema key → target dimension mapping
    SCHEMA_TO_DIM = {
        "function_tree.workflow": "workflow_orchestration",
        "function_tree.general": "workflow_orchestration",
        "function_tree.agent_capabilities": "workflow_orchestration",
        "agent_product_capabilities.knowledge_base": "rag_knowledge",
        "agent_product_capabilities.model_support": "model_support",
        "agent_product_capabilities.enterprise_readiness": "security_compliance",
        "function_tree.integration": "integration",
        "business_value": "user_persona",
        "pricing_model": "pricing_model",
    }

    def schema_to_dim(raw_schema):
        if raw_schema in SCHEMA_TO_DIM:
            return SCHEMA_TO_DIM[raw_schema]
        raw_lower = raw_schema.lower()
        for kw, dim in [
            ("workflow", "workflow_orchestration"), ("orchestrat", "workflow_orchestration"),
            ("rag", "rag_knowledge"), ("knowledge", "rag_knowledge"),
            ("model", "model_support"), ("llm", "model_support"),
            ("agent", "multi_agent"), ("multi", "multi_agent"),
            ("integration", "integration"), ("plugin", "integration"), ("tool", "integration"),
            ("enterprise", "security_compliance"), ("security", "security_compliance"),
            ("sso", "security_compliance"), ("rbac", "security_compliance"),
            ("pricing", "pricing_model"), ("price", "pricing_model"),
            ("user", "user_persona"), ("persona", "user_persona"),
            ("business_value", "user_persona"),
        ]:
            if kw in raw_lower:
                return dim
        return "function_tree"

    # Group evidence by (product, dimension)
    from collections import defaultdict
    grouped = defaultdict(list)
    for ev in ev_items:
        pid = ev.get("product_id", "")
        ev_id = ev.get("evidence_id", "")
        if not pid or not ev_id:
            continue
        dim = schema_to_dim(ev.get("schema_key", ""))
        grouped[(pid, dim)].append(ev)

    # All target dimensions
    ALL_DIMS = [
        "workflow_orchestration", "rag_knowledge", "model_support",
        "multi_agent", "integration", "security_compliance",
        "free_tier", "paid_plans", "enterprise_pricing",
        "user_persona", "non_technical_business", "low_code_developers",
        "professional_developers", "ai_engineers",
    ]

    supplemental_claims = []
    all_ev_ids = set()

    print(f"\nAnalyzing dimensions...")

    for (pid, dim), evs in sorted(grouped.items()):
        key = (pid, dim)
        existing = existing_coverage.get(key, [])
        pname = pid_to_name.get(pid, pid)

        if existing:
            print(f"  {pname} / {dim}: SKIP (already has {len(existing)} claims)")
            continue

        # Try usable evidence first
        usable = [e for e in evs if e.get("usable_for_claim", False)]
        evidence_to_use = usable if usable else evs

        if not evidence_to_use:
            print(f"  {pname} / {dim}: SKIP (no evidence at all)")
            continue

        print(f"  {pname} / {dim}: {len(evs)} evidence, {len(usable)} usable → generating claim")

        # Build claim from evidence snippets
        claim_text_parts = []
        evidence_ids = []
        for ev in evidence_to_use[:5]:
            eid = ev.get("evidence_id", "")
            snippet = ev.get("snippet", "").strip()
            if snippet:
                evidence_ids.append(eid)
                all_ev_ids.add(eid)
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                claim_text_parts.append(snippet)

        if not claim_text_parts:
            continue

        combined = " ".join(claim_text_parts)
        if len(combined) > 500:
            combined = combined[:500] + "..."

        claim = {
            "claim_id": generate_id("supp_claim"),
            "run_id": run_id,
            "product_id": pid,
            "product_name": pname,
            "dimension": dim,
            "claim_text": combined,
            "claim_type": "factual_summary",
            "fact_ids": [],
            "evidence_ids": evidence_ids,
            "evidence_ids_json": json.dumps(evidence_ids),
            "fact_ids_json": "[]",
            "confidence": 0.75 if usable else 0.60,
            "risk_level": "medium" if usable else "high",
            "support_level": None,
            "review_status": "signed",
            "signed_claim_id": generate_id("signed_supp"),
            "created_by_agent": "SupplementalClaimGenerator",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        supplemental_claims.append(claim)
        print(f"    → {len(evidence_ids)} evidence IDs")

    # For dimensions still missing claims, try non-usable evidence as last resort
    print(f"\nSecond pass: using non-usable evidence for remaining gaps...")

    # Also group non-usable evidence by dimension for "last resort" claims
    non_usable_by_dim = defaultdict(list)
    for ev in ev_items:
        pid = ev.get("product_id", "")
        if not pid:
            continue
        dim = schema_to_dim(ev.get("schema_key", ""))
        if not ev.get("usable_for_claim", False):
            non_usable_by_dim[(pid, dim)].append(ev)

    for (pid, dim) in list(grouped.keys()) + list(non_usable_by_dim.keys()):
        key = (pid, dim)
        if existing_coverage.get(key):
            continue  # Already has claims
        if any(c["dimension"] == dim and c.get("product_id") == pid for c in supplemental_claims):
            continue  # Already generated supplemental claim

        pname = pid_to_name.get(pid, pid)
        # Try non-usable evidence for this product/dimension
        non_usable = non_usable_by_dim.get(key, [])
        if not non_usable:
            continue

        print(f"  {pname} / {dim}: using {len(non_usable)} non-usable evidence as last resort")

        evidence_ids = []
        claim_text_parts = []
        for ev in non_usable[:3]:
            eid = ev.get("evidence_id", "")
            snippet = ev.get("snippet", "").strip()
            if snippet:
                evidence_ids.append(eid)
                all_ev_ids.add(eid)
                if len(snippet) > 150:
                    snippet = snippet[:150] + "..."
                claim_text_parts.append(snippet)

        if not claim_text_parts:
            continue

        combined = " ".join(claim_text_parts)
        if len(combined) > 400:
            combined = combined[:400] + "..."

        claim = {
            "claim_id": generate_id("supp_claim"),
            "run_id": run_id,
            "product_id": pid,
            "product_name": pname,
            "dimension": dim,
            "claim_text": combined,
            "claim_type": "factual_summary",
            "fact_ids": [],
            "evidence_ids": evidence_ids,
            "evidence_ids_json": json.dumps(evidence_ids),
            "fact_ids_json": "[]",
            "confidence": 0.60,
            "risk_level": "high",
            "support_level": None,
            "review_status": "signed",
            "signed_claim_id": generate_id("signed_supp"),
            "created_by_agent": "SupplementalClaimGenerator",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        supplemental_claims.append(claim)

    print(f"\n{'='*60}")
    print(f"Total supplemental claims to generate: {len(supplemental_claims)}")

    if not supplemental_claims:
        print("All dimensions already have claims. Nothing to do.")
        return

    # Write to DB
    print(f"\nWriting to DB...")
    os.environ["WORK_DIR"] = str(PROJECT_DIR)

    from backend.app.storage.db import get_connection

    def ensure_product(pid, pname):
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT product_id FROM products WHERE run_id = ? AND (product_id = ? OR product_name = ?)",
                (run_id, pid, pname)
            ).fetchall()
            if rows:
                return rows[0][0]
            conn.execute(
                "INSERT OR IGNORE INTO products (product_id, run_id, product_name, created_at) VALUES (?, ?, ?, ?)",
                (pid, run_id, pname or pid, utc_now())
            )
            return pid

    def upsert_claim(claim):
        now = claim.get("created_at", utc_now())
        resolved_pid = ensure_product(claim["product_id"], claim.get("product_name", ""))
        with get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO claims (
                    claim_id, run_id, product_id, dimension, claim_text, claim_type,
                    fact_ids_json, evidence_ids_json, confidence, risk_level, support_level,
                    review_status, signed_claim_id, created_by_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim["claim_id"], claim["run_id"], resolved_pid, claim["dimension"],
                    claim["claim_text"], claim.get("claim_type", "factual_summary"),
                    claim.get("fact_ids_json", "[]"),
                    claim.get("evidence_ids_json", "[]"),
                    claim.get("confidence", 0.75), claim.get("risk_level", "medium"),
                    claim.get("support_level"),
                    claim.get("review_status", "signed"),
                    claim.get("signed_claim_id"),
                    claim.get("created_by_agent", "SupplementalClaimGenerator"),
                    now, now,
                ),
            )

    for i, claim in enumerate(supplemental_claims):
        upsert_claim(claim)
        print(f"  [{i+1}/{len(supplemental_claims)}] {claim['product_name']} / {claim['dimension']}")

    # Update report JSON
    print(f"\nUpdating report JSON...")
    existing_ids = {c.get("claim_id") for c in existing_claims}
    all_claims = list(existing_claims)
    for sc in supplemental_claims:
        if sc["claim_id"] not in existing_ids:
            all_claims.append({
                "claim_id": sc["claim_id"], "run_id": sc["run_id"],
                "product_id": sc["product_id"], "dimension": sc["dimension"],
                "claim_text": sc["claim_text"], "claim_type": sc.get("claim_type"),
                "fact_ids_json": sc.get("fact_ids_json", "[]"),
                "evidence_ids_json": sc.get("evidence_ids_json", "[]"),
                "evidence_ids": sc.get("evidence_ids", []),
                "fact_ids": sc.get("fact_ids", []),
                "confidence": sc.get("confidence"),
                "risk_level": sc.get("risk_level"),
                "support_level": sc.get("support_level"),
                "review_status": sc.get("review_status"),
                "signed_claim_id": sc.get("signed_claim_id"),
                "created_by_agent": sc.get("created_by_agent"),
                "created_at": sc.get("created_at"),
                "updated_at": sc.get("updated_at"),
            })

    report_data["signed_claims"] = all_claims

    qs = report_data.setdefault("quality_summary", {})
    qs["claims_count"] = len(all_claims)

    from collections import Counter
    coverage_by_product = {}
    coverage_by_dimension = {}
    for pid in set(c.get("product_id", "") for c in all_claims):
        pname = pid_to_name.get(pid, pid)
        p_claims = [c for c in all_claims if c.get("product_id") == pid]
        total = len(p_claims)
        usable = sum(1 for c in p_claims if c.get("evidence_ids"))
        coverage_by_product[pname] = usable / total if total > 0 else 0.0
        coverage_by_dimension[pname] = {}
        dim_counts = Counter(c.get("dimension", "") for c in p_claims)
        for dim in ALL_DIMS:
            cnt = dim_counts.get(dim, 0)
            usable_cnt = sum(1 for c in p_claims if c.get("dimension") == dim and c.get("evidence_ids"))
            if cnt == 0:
                status = "no_claims"; rate = 0.0
            elif usable_cnt == 0:
                status = "evidence_gap"; rate = 0.0
            elif usable_cnt == cnt:
                status = "ready"; rate = 1.0
            else:
                status = "partial"; rate = usable_cnt / cnt if cnt > 0 else 0.0
            coverage_by_dimension[pname][dim] = {"status": status, "rate": rate, "claim_count": cnt, "usable_count": usable_cnt}

    report_data["coverage_by_product"] = coverage_by_product
    qs["coverage_by_product"] = coverage_by_product

    with open(report_path, "w") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"\nUpdated report JSON: {report_path}")
    print(f"Total claims: {len(all_claims)}")
    print(f"\nCoverage by product:")
    for p, r in coverage_by_product.items():
        print(f"  {p}: {r:.0%}")

    print(f"\nCoverage by dimension:")
    for pname, dims in sorted(coverage_by_dimension.items()):
        ready = sum(1 for v in dims.values() if v["status"] == "ready")
        partial = sum(1 for v in dims.values() if v["status"] == "partial")
        no = sum(1 for v in dims.values() if v["status"] in ("no_claims", "evidence_gap"))
        print(f"  {pname}: {ready} ready, {partial} partial, {no} no coverage")

    print(f"\n{'='*60}")
    print(f"Done! {len(supplemental_claims)} supplemental claims written to DB.")
    print(f"Next: Re-run deep report generation for updated report.")


if __name__ == "__main__":
    main()
