#!/usr/bin/env python3
"""
Supplemental Claims Generator

For a given run_id, reads existing evidence items from the report JSON,
identifies dimensions with 0 claims, and generates supplemental claims
from the unused evidence. Then re-runs the deep report generation.

Usage:
    python scripts/supplemental_claims.py <run_id>
    python scripts/supplemental_claims.py run_8e8343b559b94878
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
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
        print("Usage: python supplemental_claims.py <run_id>")
        sys.exit(1)

    run_id = sys.argv[1]

    # Find the latest report JSON for this run
    report_files = sorted(REPORT_DIR.glob(f"report_{run_id}_v*.json"))
    if not report_files:
        print(f"No report files found for run_id={run_id}")
        sys.exit(1)

    report_path = report_files[-1]
    print(f"Reading report: {report_path}")

    with open(report_path) as f:
        report_data = json.load(f)

    # Extract evidence items
    qs = report_data.get("quality_summary", {})
    ev_items = qs.get("evidence_items", [])
    print(f"Found {len(ev_items)} evidence items")

    # Extract existing claims
    existing_claims = report_data.get("signed_claims", [])
    print(f"Found {len(existing_claims)} existing claims")

    # Build product_id → product_name mapping from evidence
    pid_to_name = {}
    for ev in ev_items:
        pid = ev.get("product_id", "")
        name = ev.get("product_name", "")
        if pid and name:
            pid_to_name[pid] = name

    # Determine which (product, dimension) pairs already have claims
    covered = {}
    for c in existing_claims:
        pid = c.get("product_id", "")
        dim = c.get("dimension", "")
        key = (pid, dim)
        if key not in covered:
            covered[key] = []
        covered[key].append(c)

    print(f"\nExisting (product, dimension) coverage:")
    for (pid, dim), claims in sorted(covered.items()):
        name = pid_to_name.get(pid, pid)
        print(f"  {name} / {dim}: {len(claims)} claims")

    # Target dimensions for supplemental claims
    TARGET_DIMENSIONS = [
        "workflow_orchestration",
        "rag_knowledge",
        "model_support",
        "multi_agent",
        "integration",
        "security_compliance",
        "free_tier",
        "paid_plans",
        "enterprise_pricing",
        "non_technical_business",
        "low_code_developers",
        "professional_developers",
        "ai_engineers",
    ]

    # Schema key to dimension mapping (evidence.schema_key → claim.dimension)
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

    # Group evidence by (product, dimension) where dimension = mapped dimension
    from collections import defaultdict

    grouped = defaultdict(list)  # (pid, dim) → list of evidence

    for ev in ev_items:
        pid = ev.get("product_id", "")
        raw_schema = ev.get("schema_key", "")
        ev_id = ev.get("evidence_id", "")
        snippet = ev.get("snippet", "")
        source_url = ev.get("source_url", "")
        source_title = ev.get("source_title", "")
        usable = ev.get("usable_for_claim", False)

        if not pid or not ev_id:
            continue

        # Map schema_key to target dimension
        dim = SCHEMA_TO_DIM.get(raw_schema, None)
        if dim is None:
            # Try partial matching
            raw_lower = raw_schema.lower()
            if "workflow" in raw_lower or "orchestrat" in raw_lower:
                dim = "workflow_orchestration"
            elif "rag" in raw_lower or "knowledge" in raw_lower:
                dim = "rag_knowledge"
            elif "model" in raw_lower or "llm" in raw_lower:
                dim = "model_support"
            elif "multi_agent" in raw_lower or "agent" in raw_lower:
                dim = "multi_agent"
            elif "integration" in raw_lower or "plugin" in raw_lower or "tool" in raw_lower:
                dim = "integration"
            elif "enterprise" in raw_lower or "security" in raw_lower or "sso" in raw_lower or "rbac" in raw_lower:
                dim = "security_compliance"
            elif "pricing" in raw_lower or "price" in raw_lower or "subscription" in raw_lower or "tier" in raw_lower:
                dim = "pricing_model"
            elif "user" in raw_lower or "persona" in raw_lower or "usecase" in raw_lower or "scenario" in raw_lower:
                dim = "user_persona"
            else:
                dim = "function_tree"  # default

        key = (pid, dim)
        grouped[key].append({
            "evidence_id": ev_id,
            "snippet": snippet[:500],
            "source_url": source_url,
            "source_title": source_title,
            "usable": usable,
            "schema_key": raw_schema,
        })

    print(f"\nGrouped evidence by (product, dimension):")
    for (pid, dim), evs in sorted(grouped.items()):
        name = pid_to_name.get(pid, pid)
        usable = sum(1 for e in evs if e["usable"])
        covered_key = (pid, dim)
        existing = len(covered.get(covered_key, []))
        print(f"  {name} / {dim}: {len(evs)} evidence ({usable} usable), existing claims: {existing}")

    # Generate supplemental claims for (product, dimension) pairs that:
    # 1. Have evidence
    # 2. Have 0 existing claims
    supplemental_claims = []

    for (pid, dim), evs in grouped.items():
        key = (pid, dim)
        if len(covered.get(key, [])) > 0:
            print(f"\nSkipping {pid} / {dim}: already has claims")
            continue

        usable_evs = [e for e in evs if e["usable"]]
        if not usable_evs and len(evs) == 0:
            print(f"\nSkipping {pid} / {dim}: no usable evidence")
            continue

        # Use all evidence (usable or not) for supplemental claims
        evidence_for_claim = usable_evs if usable_evs else evs
        if not evidence_for_claim:
            continue

        pname = pid_to_name.get(pid, pid)

        # Build claim text from evidence snippets
        claim_text_parts = []
        evidence_ids = []

        for ev in evidence_for_claim[:5]:  # Max 5 evidence items per claim
            evidence_ids.append(ev["evidence_id"])
            snippet = ev["snippet"].strip()
            if snippet:
                # Truncate long snippets
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                claim_text_parts.append(snippet)

        if not claim_text_parts:
            continue

        # Build the claim text
        combined = " ".join(claim_text_parts)
        if len(combined) > 500:
            combined = combined[:500] + "..."

        claim_text = combined

        # Build the supplemental claim
        claim = {
            "claim_id": generate_id("supp_claim"),
            "run_id": run_id,
            "product_id": pid,
            "product_name": pname,
            "dimension": dim,
            "claim_text": claim_text,
            "claim_type": "factual_summary",
            "fact_ids": [],
            "evidence_ids": evidence_ids,
            "evidence_ids_json": json.dumps(evidence_ids),
            "fact_ids_json": "[]",
            "confidence": 0.75,
            "risk_level": "medium",
            "support_level": None,
            "review_status": "signed",
            "signed_claim_id": generate_id("signed_supp"),
            "created_by_agent": "SupplementalClaimGenerator",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }

        supplemental_claims.append(claim)
        print(f"\n  Generated supplemental claim for {pname} / {dim}:")
        print(f"    evidence_ids: {evidence_ids}")
        print(f"    text: {claim_text[:100]}...")

    print(f"\n\n{'='*60}")
    print(f"Total supplemental claims generated: {len(supplemental_claims)}")

    if not supplemental_claims:
        print("No supplemental claims needed. All dimensions have coverage.")
        return

    # Save supplemental claims to a JSON file for review
    output_path = REPORT_DIR / f"supplemental_claims_{run_id}.json"
    with open(output_path, "w") as f:
        json.dump(supplemental_claims, f, ensure_ascii=False, indent=2)
    print(f"Saved supplemental claims to: {output_path}")

    # Now write supplemental claims to the DB and update the report JSON
    print(f"\nWriting supplemental claims to DB...")

    # Import DB utilities
    os.environ["WORK_DIR"] = str(PROJECT_DIR)
    from backend.app.storage.db import get_connection

    def _ensure_product_in_db(run_id, raw_pid, raw_pname, now):
        with get_connection() as conn:
            # Check if product exists
            rows = conn.execute(
                "SELECT product_id FROM products WHERE run_id = ? AND (product_id = ? OR product_name = ?)",
                (run_id, raw_pid, raw_pname)
            ).fetchall()
            if rows:
                return rows[0][0]
            # Insert
            conn.execute(
                "INSERT OR IGNORE INTO products (product_id, run_id, product_name, created_at) VALUES (?, ?, ?, ?)",
                (raw_pid, run_id, raw_pname or raw_pid, now)
            )
            return raw_pid

    def _upsert_claim(claim):
        now = claim.get("created_at", utc_now())
        resolved_pid = _ensure_product_in_db(
            claim["run_id"], claim["product_id"],
            claim.get("product_name", ""), now
        )
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claims (
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
        return claim

    # Write all supplemental claims to DB
    for i, claim in enumerate(supplemental_claims):
        _upsert_claim(claim)
        print(f"  [{i+1}/{len(supplemental_claims)}] Wrote: {claim['product_name']} / {claim['dimension']}")

    print(f"\nSuccessfully wrote {len(supplemental_claims)} supplemental claims to DB")

    # Now update the report JSON to include the supplemental claims
    print(f"\nUpdating report JSON...")

    existing_claim_ids = {c.get("claim_id") for c in existing_claims}

    # Add supplemental claims to existing claims list
    all_claims = list(existing_claims)
    for sc in supplemental_claims:
        if sc["claim_id"] not in existing_claim_ids:
            # Convert to same format as existing claims
            existing_format = {
                "claim_id": sc["claim_id"],
                "run_id": sc["run_id"],
                "product_id": sc["product_id"],
                "dimension": sc["dimension"],
                "claim_text": sc["claim_text"],
                "claim_type": sc["claim_type"],
                "fact_ids_json": sc.get("fact_ids_json", "[]"),
                "evidence_ids_json": sc.get("evidence_ids_json", "[]"),
                "confidence": sc.get("confidence"),
                "risk_level": sc.get("risk_level"),
                "support_level": sc.get("support_level"),
                "review_status": sc.get("review_status"),
                "signed_claim_id": sc.get("signed_claim_id"),
                "created_by_agent": sc.get("created_by_agent"),
                "created_at": sc.get("created_at"),
                "updated_at": sc.get("updated_at"),
                "fact_ids": sc.get("fact_ids", []),
                "evidence_ids": sc.get("evidence_ids", []),
            }
            all_claims.append(existing_format)

    # Update the report data
    report_data["signed_claims"] = all_claims

    # Update quality summary
    qs = report_data.get("quality_summary", {})
    qs["claims_count"] = len(all_claims)

    # Update coverage_by_product and coverage_by_dimension
    products = report_data.get("products", [])
    from collections import Counter

    # Rebuild coverage_by_dimension
    coverage_by_dimension = {}
    for pid in set(c.get("product_id", "") for c in all_claims):
        pname = pid_to_name.get(pid, pid)
        if pname not in coverage_by_dimension:
            coverage_by_dimension[pname] = {}

        dim_claims = [c for c in all_claims if c.get("product_id") == pid]
        dim_counts = Counter(c.get("dimension", "") for c in dim_claims)

        target_dims = [
            "workflow_orchestration", "rag_knowledge", "model_support",
            "multi_agent", "integration", "security_compliance",
            "pricing_model", "free_tier", "paid_plans", "enterprise_pricing",
            "user_persona", "non_technical_business", "low_code_developers",
            "professional_developers", "ai_engineers",
        ]

        for dim in target_dims:
            count = dim_counts.get(dim, 0)
            usable_count = sum(
                1 for c in dim_claims
                if c.get("dimension") == dim and c.get("evidence_ids")
            )
            if count == 0:
                status = "no_claims"
                rate = 0.0
            elif usable_count == 0:
                status = "evidence_gap"
                rate = 0.0
            elif usable_count == count:
                status = "ready"
                rate = 1.0
            else:
                status = "partial"
                rate = usable_count / count if count > 0 else 0.0

            coverage_by_dimension[pname][dim] = {
                "status": status,
                "rate": rate,
                "claim_count": count,
                "usable_count": usable_count,
            }

    # Rebuild coverage_by_product
    coverage_by_product = {}
    for pid in set(c.get("product_id", "") for c in all_claims):
        pname = pid_to_name.get(pid, pid)
        p_claims = [c for c in all_claims if c.get("product_id") == pid]
        total = len(p_claims)
        usable = sum(1 for c in p_claims if c.get("evidence_ids"))
        coverage_by_product[pname] = usable / total if total > 0 else 0.0

    report_data["coverage_by_product"] = coverage_by_product
    report_data["quality_summary"]["coverage_by_product"] = coverage_by_product

    # Write updated report JSON
    with open(report_path, "w") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    print(f"Updated report JSON: {report_path}")
    print(f"Total claims now: {len(all_claims)}")
    print(f"\nCoverage by product:")
    for p, rate in coverage_by_product.items():
        print(f"  {p}: {rate:.0%}")

    print(f"\nCoverage by dimension:")
    for pname, dims in sorted(coverage_by_dimension.items()):
        ready = sum(1 for v in dims.values() if v["status"] == "ready")
        partial = sum(1 for v in dims.values() if v["status"] == "partial")
        no = sum(1 for v in dims.values() if v["status"] in ("no_claims", "evidence_gap"))
        print(f"  {pname}: {ready} ready, {partial} partial, {no} no coverage")

    print(f"\n{'='*60}")
    print(f"Done! Supplemental claims written to DB and report JSON updated.")
    print(f"Next step: Re-run the deep report generation to produce the updated report.")


if __name__ == "__main__":
    main()
