"""
完整跑一遍报告生成流程。
直接调用 run_deep_report_workflow，验收所有关键环节。
"""
import sys, os, json, time, logging
from datetime import datetime, timezone
import sqlite3

# CRITICAL: Set DATABASE_URL BEFORE any backend imports.
# deep_report.py uses "../backend/" in its path, making PROJECT_ROOT resolve to the wrong
# location (backend/data/ instead of project/data/). DATABASE_URL overrides this.
_E2E_ROOT = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_E2E_ROOT)  # backend/ -> project/
_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "productinsight.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["WORK_DIR"] = _PROJECT_ROOT

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging as app_logging
app_logging.basicConfig(
    level=app_logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)
logger = logging.getLogger("e2e_full")

import os as _os
_PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
DB_PATH = _os.path.join(_PROJECT_ROOT, "data", "productinsight.db")
SRC_RUN = "run_fd7ec6196a594fc4"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_task_brief(run_id: str) -> dict:
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT task_brief_json FROM runs WHERE run_id=?", (run_id,))
    row = cur.fetchone()
    conn.close()
    return json.loads(row[0]) if row and row[0] else {}


def load_data(run_id: str) -> dict:
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM evidence_items WHERE run_id=?", (run_id,))
    ev_items = []
    for r in cur.fetchall():
        d = dict(r)
        d.pop("raw_text", None)
        ev_items.append(d)

    cur.execute("SELECT * FROM claims WHERE run_id=?", (run_id,))
    claims = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM facts WHERE run_id=?", (run_id,))
    facts = [dict(r) for r in cur.fetchall()]

    # Load evidence links
    claim_ev_links = {}
    for c in claims:
        cur.execute("SELECT evidence_id FROM claim_evidence_links WHERE claim_id=?", (c["claim_id"],))
        claim_ev_links[c["claim_id"]] = [r["evidence_id"] for r in cur.fetchall()]

    conn.close()
    return {
        "evidence_items": ev_items,
        "claims": claims,
        "facts": facts,
        "claim_ev_links": claim_ev_links,
    }


def main():
    # Ensure consistent DB path for all imports. deep_report.py's PROJECT_ROOT resolves
    # to backend/.. due to the "../backend/" import path, which would cause it to use
    # backend/data/productinsight.db (missing tables) instead of the real DB.
    import os as _os
    _os.environ["WORK_DIR"] = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

    # Ensure all migrations have run (including report_v2 tables) so that
    # deep_report.py's repository calls find the report_sections table.
    from backend.app.storage.db import init_db
    init_db()

    print("=" * 70)
    print("E2E Full Report Generation - Direct Function Call")
    print("=" * 70)

    # Step 0: Re-extract Coze evidence with improved pipeline
    print(f"\n[Step 0] Re-extracting Coze evidence with improved pipeline...")
    import importlib
    import backend.app.agents.collector.evidence_extractor as ee_mod
    importlib.reload(ee_mod)
    from backend.app.agents.collector.evidence_extractor import EvidenceExtractor

    run_id = SRC_RUN
    conn_re = sqlite3.connect(DB_PATH)
    conn_re.row_factory = sqlite3.Row
    cur_re = conn_re.cursor()

    # Load sources and snapshots for Coze
    cur_re.execute("""
        SELECT s.*, snap.raw_text_path
        FROM sources s
        LEFT JOIN snapshots snap ON s.source_id = snap.source_id
        WHERE s.run_id=? AND s.product_slug='coze'
    """, (run_id,))
    coze_sources = []
    for r in cur_re.fetchall():
        coze_sources.append(dict(r))
    conn_re.close()

    print(f"  Found {len(coze_sources)} Coze sources")

    # Build raw_documents from snapshots
    raw_docs = []
    for src in coze_sources:
        path = src.get("raw_text_path", "")
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_text = f.read()
            if raw_text and len(raw_text) > 100:
                raw_docs.append({
                    "source_id": src["source_id"],
                    "product_id": src["product_id"],
                    "product_slug": "coze",
                    "snapshot_id": "",
                    "raw_text": raw_text,
                    "url": src.get("url", ""),
                    "title": src.get("title", ""),
                    "source_type": src.get("source_type"),
                    "trust_tier": src.get("trust_tier"),
                })
                print(f"  Loaded: {src.get('url', '')[:50]} ({len(raw_text)} chars)")

    if raw_docs:
        # Re-extract with improved pipeline
        importlib.reload(ee_mod)
        from backend.app.agents.collector.evidence_extractor import EvidenceExtractor
        extractor2 = EvidenceExtractor()
        new_items, _ = extractor2.extract_evidence(raw_docs, run_id)
        print(f"  New evidence extracted: {len(new_items)}")

        # Update DB: replace coze evidence with new items (direct SQL to avoid DB lock)
        conn_upd = sqlite3.connect(DB_PATH)
        cur_upd = conn_upd.cursor()
        # Delete old coze evidence
        cur_upd.execute("DELETE FROM evidence_items WHERE run_id=? AND product_slug='coze'", (run_id,))
        print(f"  Deleted old Coze evidence")

        # Clean up stale claim evidence links for deleted evidence
        cur_upd.execute("""
            DELETE FROM claim_evidence_links
            WHERE claim_id IN (SELECT claim_id FROM claims WHERE run_id=?)
              AND evidence_id NOT IN (SELECT evidence_id FROM evidence_items WHERE run_id=?)
        """, (run_id, run_id))
        deleted_links = cur_upd.rowcount
        print(f"  Cleaned up {deleted_links} stale claim evidence links")

        # Get resolved product_id for coze
        coze_product_id = raw_docs[0].get("product_id", run_id + "_coze") if raw_docs else run_id + "_coze"

        # Insert new evidence directly (bypass repository to avoid DB lock)
        now_str = datetime.now(timezone.utc).isoformat()
        for item in new_items:
            try:
                cur_upd.execute("""
                    INSERT INTO evidence_items (
                        evidence_id, run_id, source_id, snapshot_id, product_id, product_slug,
                        schema_key, snippet, start_offset, end_offset, section_title,
                        confidence, quality_score, usable_for_claim,
                        pii_masked, evidence_type, created_at,
                        trust_tier, source_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item["evidence_id"], run_id,
                    item.get("source_id", ""), item.get("snapshot_id", ""),
                    coze_product_id, "coze",
                    item.get("schema_key"), item["snippet"],
                    item.get("start_offset", 0), item.get("end_offset", len(item["snippet"])),
                    item.get("section_title", ""),
                    item.get("confidence", 0.5),
                    item.get("quality_score", 0.0),
                    item.get("usable_for_claim", False),
                    item.get("pii_masked", False),
                    item.get("evidence_type", "text"),
                    now_str,
                    item.get("trust_tier"),
                    item.get("source_type"),
                ))
            except Exception as exc:
                print(f"    Failed to insert {item.get('evidence_id', '?')}: {exc}")
        conn_upd.commit()

        # Re-evaluate new evidence with improved evaluator
        print(f"\n  Re-evaluating new evidence...")
        import backend.app.services.evidence_evaluator as ev_mod
        importlib.reload(ev_mod)
        from backend.app.services.evidence_evaluator import evaluate_evidence_items

        # Get source info for new items
        for item in new_items:
            cur_upd.execute("SELECT url, source_type, trust_tier FROM sources WHERE source_id=?", (item.get("source_id", ""),))
            src_row = cur_upd.fetchone()
            if src_row:
                item["url"] = src_row[0]
                item["source_type"] = src_row[1] or item.get("source_type")
                item["trust_tier"] = src_row[2] or item.get("trust_tier")

        re_evaled, _ = evaluate_evidence_items(new_items, run_id=run_id)
        for ev in re_evaled:
            ev_id = ev.get("evidence_id")
            q = ev.get("quality", {})
            usable = q.get("usable_for_claim", False)
            score = q.get("final_score", ev.get("quality_score", 0))
            cur_upd.execute(
                "UPDATE evidence_items SET usable_for_claim=?, quality_score=? WHERE evidence_id=?",
                (usable, score, ev_id)
            )
            print(f"    Updated: {ev_id[:16]} [{score:.3f}] usable={usable} schema={ev.get('schema_key','')[:30]}")

        conn_upd.commit()
        conn_upd.close()
        print(f"  Inserted {len(new_items)} new Coze evidence items")
        inserted_count = len(new_items)

    # Reload data after update
    print(f"\n  Reloading data after evidence re-extraction...")
    data = load_data(SRC_RUN)
    print(f"  Evidence items: {len(data['evidence_items'])} (was 39)")

    # Step 1b: Generate Coze claims with analyst (since new evidence was added)
    # NOTE: analyst needs Coze-only evidence + Coze-only products in task brief.
    print(f"\n[Step 1b] Generating Coze claims with AnalystAgent...")
    import importlib as il
    il.reload(ee_mod)  # ensure pipeline is reloaded
    from backend.app.agents.analyst.analyst import AnalystAgent as AA

    # Reload all evidence from DB (coze is now updated in Step 0)
    conn_reload = sqlite3.connect(DB_PATH)
    conn_reload.row_factory = sqlite3.Row
    cur_reload = conn_reload.cursor()
    cur_reload.execute("SELECT * FROM evidence_items WHERE run_id=?", (SRC_RUN,))
    all_evidence = []
    for r in cur_reload.fetchall():
        d = dict(r)
        d.pop("raw_text", None)
        all_evidence.append(d)
    conn_reload.close()

    # Get coze evidence (usable only)
    coze_evidence = [e for e in all_evidence if e.get("product_slug") == "coze" and e.get("usable_for_claim", False)]
    print(f"  Total evidence: {len(all_evidence)}")
    print(f"  Coze usable evidence: {len(coze_evidence)}")

    coze_claims = []
    if coze_evidence:
        print(f"  Calling AnalystAgent for Coze claims...")
        try:
            # Load task_brief here (Step 1b runs before Step 1 where task_brief is normally loaded)
            task_brief_coze = load_task_brief(SRC_RUN)
            analyst = AA()
            # Filter to Coze products only
            coze_task_brief = {
                **task_brief_coze,
                "products": [p for p in task_brief_coze.get("products", []) if "coze" in p.get("product_name", "").lower()],
            }
            if not coze_task_brief.get("products"):
                # Fallback: create a minimal Coze product
                coze_task_brief = {
                    **task_brief_coze,
                    "products": [{"product_name": "Coze", "product_id": "run_fd7ec6196a594fc4_coze"}],
                }

            coze_claims = analyst.analyze(
                evidence_items=coze_evidence,  # Only Coze evidence
                facts=[],                     # No facts needed for claim generation
                task_brief=coze_task_brief,   # Only Coze products
                run_id=SRC_RUN,
            )
            print(f"  Analyst generated {len(coze_claims)} claims for Coze")
            for c in coze_claims:
                print(f"    [{c.get('dimension','?')}] {c.get('claim_text','')[:60]}...")
        except Exception as exc:
            import traceback
            print(f"  Analyst call failed: {exc}")
            traceback.print_exc()
            coze_claims = []
    else:
        print(f"  No usable Coze evidence, skipping analyst")

    # Step 1: Load data
    print(f"\n[Step 1] Loading data from {SRC_RUN}...")
    task_brief = load_task_brief(SRC_RUN)
    data = load_data(SRC_RUN)

    products = [p.get("product_name", p.get("product_id", "")) for p in task_brief.get("products", [])]
    print(f"  Products: {products}")
    print(f"  Evidence items: {len(data['evidence_items'])}")
    print(f"  Claims: {len(data['claims'])}")
    print(f"  Facts: {len(data['facts'])}")

    # Step 2: Build signed_claims and rework_required_claims from DB data
    print(f"\n[Step 2] Building signed and rework_required claims...")
    signed_claims = []
    rework_required_claims = []
    for i, c in enumerate(data["claims"]):
        review_status = c.get("review_status", "")
        claim = {
            "claim_id": c.get("claim_id", f"claim_{i}"),
            "run_id": SRC_RUN,
            "product_id": c.get("product_id", ""),
            "dimension": c.get("dimension", "function_tree"),
            "claim_text": c.get("claim_text", ""),
            "evidence_ids": data["claim_ev_links"].get(c["claim_id"], []),
            "confidence": c.get("confidence", 0.5),
            "risk_level": c.get("risk_level", ""),
            "support_level": c.get("support_level", ""),
            "review_status": review_status,
        }
        if review_status in ("signed", "warning"):
            signed_claims.append(claim)
        elif review_status == "rework_required":
            rework_required_claims.append(claim)
        else:
            # Default: put unsigned in rework_required
            rework_required_claims.append(claim)

    # ── P0-1 Fix: Coze analyst claims must pass evidence gate and be tracked separately ───
    # OLD: analyst Coze claims were injected directly as `review_status="signed"`,
    # bypassing the evidence gate and conflating analyst-signed with reviewer-signed claims.
    # NEW: mark them as analyst_generated=True, pass through evidence gate filter,
    # count separately as analyst_signed (not mixed into reviewer-signed).
    analyst_signed_claims = []
    if coze_claims:
        coze_pid = None
        for c in data["claims"]:
            pid = c.get("product_id", "")
            if "coze" in pid.lower():
                coze_pid = pid
                break
        if not coze_pid:
            coze_pid = SRC_RUN + "_coze"

        for c in coze_claims:
            ev_ids = c.get("evidence_ids", [])
            if not ev_ids:
                continue
            claim = {
                "claim_id": c.get("claim_id", f"coze_analyst_{len(analyst_signed_claims)}"),
                "run_id": SRC_RUN,
                "product_id": coze_pid,
                "dimension": c.get("dimension", "function_tree"),
                "claim_text": c.get("claim_text", ""),
                "evidence_ids": ev_ids,
                "confidence": c.get("confidence", 0.6),
                "risk_level": c.get("risk_level", "low"),
                "support_level": c.get("support_level", ""),
                "review_status": "pending",
                "_analyst_generated": True,
            }
            analyst_signed_claims.append(claim)
        print(f"  {len(analyst_signed_claims)} analyst-generated Coze claims (marked analyst_signed, not reviewer-signed)")

    print(f"  Signed claims: {len(signed_claims)}")
    print(f"  Rework required claims: {len(rework_required_claims)}")

    # Step 3: Apply evidence gate
    print(f"\n[Step 3] Applying Evidence Contract gate...")
    from backend.app.services.deep_report import _gate_evidence_by_dimension
    gated_evidence = _gate_evidence_by_dimension(data["evidence_items"])
    rejected = [e for e in gated_evidence if e.get("gate_rejection")]
    gated_ev_ids = {e.get("evidence_id") for e in gated_evidence if e.get("evidence_id")}
    print(f"  Total evidence: {len(gated_evidence)}")
    print(f"  Rejected by gate: {len(rejected)}")
    for e in rejected[:3]:
        print(f"    - {e.get('gate_rejection')}: schema_key={e.get('schema_key','?')}, source={e.get('source_type','?')}")

    # Step 3b: Enrich evidence with URL-inferred source_type/trust_tier
    # The evidence_items table has source_type=None for old records (written before the fix).
    # We join with sources table to get the URL, then infer source_type and trust_tier.
    print(f"\n[Step 3b] Enriching evidence with URL-inferred source_type/trust_tier...")
    from backend.app.agents.collector.evidence_extractor import _infer_source_type, _infer_trust_tier
    evidence_ids = [e.get("evidence_id") for e in gated_evidence if e.get("evidence_id")]
    source_type_map = {}  # evidence_id -> source_type
    source_url_map = {}   # evidence_id -> url
    if evidence_ids:
        try:
            conn_src = sqlite3.connect(DB_PATH)
            conn_src.row_factory = sqlite3.Row
            cur_src = conn_src.cursor()
            placeholders = ",".join(["?"] * len(evidence_ids))
            cur_src.execute(
                f"""SELECT e.evidence_id, s.url, s.source_type as src_source_type
                    FROM evidence_items e
                    LEFT JOIN sources s ON e.source_id = s.source_id
                    WHERE e.evidence_id IN ({placeholders})""",
                evidence_ids,
            )
            for row in cur_src.fetchall():
                source_type_map[row["evidence_id"]] = row["src_source_type"]
                source_url_map[row["evidence_id"]] = row["url"]
            conn_src.close()
        except Exception as ex:
            print(f"  Warning: could not join sources table: {ex}")

    for e in gated_evidence:
        eid = e.get("evidence_id", "")
        url = source_url_map.get(eid, e.get("url", ""))
        # Prefer DB source_type if available and non-None; else infer from URL
        db_st = source_type_map.get(eid)
        if db_st:
            e["source_type"] = db_st
        else:
            e["source_type"] = _infer_source_type(url) if url else "web_page"
        e["trust_tier"] = _infer_trust_tier(url, e.get("product_id", ""))

    st_fixed = sum(1 for e in gated_evidence if e.get("source_type") not in (None, "web_page"))
    print(f"  Evidence with non-web_page source_type: {st_fixed}/{len(gated_evidence)}")

    # Step 3c: Re-evaluate evidence quality with new rules
    print(f"\n[Step 3c] Re-evaluating evidence quality with new rules...")
    from backend.app.services.evidence_evaluator import evaluate_evidence_items
    usable_candidates = [e for e in gated_evidence if not e.get("gate_rejection")]
    re_evaluated, _ = evaluate_evidence_items(usable_candidates, run_id=SRC_RUN)
    # Build a map of re-evaluated quality
    re_eval_map = {e.get("evidence_id"): e for e in re_evaluated}
    # Merge new quality into gated_evidence (keep gate_rejection, add/update quality)
    for e in gated_evidence:
        eid = e.get("evidence_id")
        if eid in re_eval_map:
            e["quality"] = re_eval_map[eid]["quality"]
            e["usable_for_claim"] = re_eval_map[eid]["quality"]["usable_for_claim"]
            e["quality_score"] = re_eval_map[eid]["quality"]["final_score"]
    new_usable = sum(1 for e in gated_evidence if e.get("usable_for_claim", False))
    old_usable = sum(1 for e in data["evidence_items"] if e.get("usable_for_claim", False))
    print(f"  Usable evidence BEFORE new rules: {old_usable}/{len(data['evidence_items'])}")
    print(f"  Usable evidence AFTER new rules:  {new_usable}/{len(gated_evidence)}")
    print(f"  Delta: +{new_usable - old_usable} usable evidence items")

    # Step 3d: Filter + remap claims by gated evidence
    # The analyst's claim.evidence_ids reference the OLD full evidence set (143 items from prior runs).
    # The gated_evidence set has only 39 items from THIS run.
    # We need to keep only claims whose evidence_ids overlap with the current gated set.
    # This simulates what review_claims does: it filters stale evidence_ids from claims.
    def _claim_has_passed_gate(claim: dict, gated_ids: set) -> bool:
        ev_ids = claim.get("evidence_ids") or []
        return any(eid in gated_ids for eid in ev_ids)

    # Re-map claim evidence_ids to the current gated_evidence set.
    # For each claim, keep only evidence_ids that exist in gated_ev_ids.
    def _remap_claim_evidence_ids(claim: dict, gated_ids: set) -> dict:
        claim = dict(claim)
        old_ids = claim.get("evidence_ids") or []
        new_ids = [eid for eid in old_ids if eid in gated_ids]
        claim["evidence_ids"] = new_ids
        return claim

    # ── P0-1 Fix: Filter analyst claims through evidence gate ─────────────────────────
    # Replicate the core review_claims evidence filter:
    # 1. Strip stale evidence IDs (not in evidence_index)
    # 2. Keep only usable evidence IDs (usable_for_claim=True)
    # 3. Downgrade to rework_required if no usable evidence remains
    usable_ev_ids = {e["evidence_id"] for e in gated_evidence if e.get("usable_for_claim", False)}

    def _filter_analyst_claims(claims: list[dict]) -> tuple[list[dict], list[dict]]:
        """Filter analyst claims through evidence gate. Returns (signed, rework)."""
        signed = []
        rework = []
        for claim in claims:
            claim = dict(claim)
            raw_ids = claim.get("evidence_ids") or []
            stale_ids = [eid for eid in raw_ids if eid not in gated_ev_ids]
            usable_ids = [eid for eid in raw_ids if eid in usable_ev_ids]
            claim["_stale_evidence_ids"] = stale_ids
            claim["evidence_ids"] = usable_ids

            if not usable_ids:
                # Hard gate: downgrade to rework if no usable evidence remains
                claim["review_status"] = "rework_required"
                claim["_hard_gate_downgrade"] = True
                rework.append(claim)
            else:
                claim["review_status"] = "analyst_signed"
                signed.append(claim)
        return signed, rework

    analyst_signed_filtered, analyst_rework_filtered = _filter_analyst_claims(analyst_signed_claims)
    print(f"\n[Step 3d-Analyst] Analyst Coze claims through evidence gate:")
    print(f"  Passed (analyst_signed): {len(analyst_signed_filtered)} / {len(analyst_signed_claims)}")
    print(f"  Downgraded (rework): {len(analyst_rework_filtered)} / {len(analyst_signed_claims)}")
    for c in analyst_signed_filtered:
        print(f"    [analyst_signed] {c.get('dimension','?')} | {c.get('claim_text','')[:50]}...")

    filtered_signed = [_remap_claim_evidence_ids(c, gated_ev_ids) for c in signed_claims if _claim_has_passed_gate(c, gated_ev_ids)]
    filtered_rework = [_remap_claim_evidence_ids(c, gated_ev_ids) for c in rework_required_claims if _claim_has_passed_gate(c, gated_ev_ids)]

    # P0-1 Fix: Track analyst_signed separately from reviewer-signed
    total_analyst_signed = len(analyst_signed_filtered)
    total_reviewer_signed = len(filtered_signed)

    print(f"\n[Step 3d] Filtering + remapping claims by gated evidence...")
    print(f"  Reviewer-signed claims (mapped): {total_reviewer_signed} / {len(signed_claims)}")
    print(f"  Analyst-signed claims (gated): {total_analyst_signed}")
    print(f"  Total signed (reviewer + analyst): {total_reviewer_signed + total_analyst_signed}")
    print(f"  Rework claims (mapped): {len(filtered_rework)} / {len(rework_required_claims)}")

    signed_claims = filtered_signed
    rework_required_claims = filtered_rework

    # P0-1 Fix: Merge analyst-signed claims into signed_claims for the report.
    # They passed the evidence gate and are tracked as _analyst_generated.
    # The report quality_summary will show them separately.
    merged_signed_claims = list(signed_claims) + list(analyst_signed_filtered)

    # Step 4: Run deep report workflow
    print(f"\n[Step 4] Running run_deep_report_workflow...")
    from backend.app.services.deep_report import run_deep_report_workflow
    from backend.app.storage.db import get_db_path
    dbg_path = get_db_path()
    dbg_conn = sqlite3.connect(dbg_path)
    dbg_tables = [r[0] for r in dbg_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    dbg_conn.close()
    print(f"  DEBUG: DB path = {dbg_path}")
    print(f"  DEBUG: report_sections exists = {'report_sections' in dbg_tables}")

    report_id = f"report_{SRC_RUN}_e2e_{int(time.time())}"
    t0 = time.time()

    try:
        result = run_deep_report_workflow(
            run_id=SRC_RUN,
            report_id=report_id,
            signed_claims=merged_signed_claims,
            rework_required_claims=rework_required_claims,
            facts=data["facts"],
            evidence_items=gated_evidence,
            products=products,
            analyst_signed_claims=analyst_signed_filtered,
        )
    except Exception as exc:
        print(f"  ERROR: {exc}")
        import traceback
        traceback.print_exc()
        return

    # P0-1 Fix: Annotate result with analyst_signed breakdown
    result["_analyst_signed_count"] = total_analyst_signed
    result["_reviewer_signed_count"] = total_reviewer_signed
    result["_analyst_signed_claims"] = analyst_signed_filtered

    elapsed = time.time() - t0
    print(f"\n  Workflow completed in {elapsed:.1f}s")

    # Step 5: Verify results
    print(f"\n[Step 5] Verifying results...")

    qs = result.get("quality_summary", {})
    sections = result.get("sections", [])

    print(f"\n  Report Status: {qs.get('report_status', '?')}")
    print(f"  Claims: {qs.get('claims_count', '?')}")
    print(f"  Coverage rate: {qs.get('evidence_coverage_rate', qs.get('coverage_rate', '?'))}")
    print(f"  Sections: {len(sections)}")

    # P0-1 Fix: Show analyst vs reviewer-signed claim breakdown
    print(f"\n  [P0-1] Claim Signing Breakdown:")
    print(f"    Reviewer-signed claims: {total_reviewer_signed}")
    print(f"    Analyst-signed claims:   {total_analyst_signed}")
    print(f"    Total signed claims:    {total_reviewer_signed + total_analyst_signed}")
    print(f"    Rework required:        {len(filtered_rework) + len(analyst_rework_filtered)}")
    if analyst_signed_filtered:
        print(f"    Analyst-signed details:")
        for c in analyst_signed_filtered:
            print(f"      [{c.get('dimension','?')}] {c.get('claim_text','')[:60]}")
    if analyst_rework_filtered:
        print(f"    Analyst claims downgraded to rework:")
        for c in analyst_rework_filtered:
            print(f"      [{c.get('dimension','?')}] {c.get('claim_text','')[:60]}")

    # Coverage breakdown — read from quality_summary (not render_ctx, which is None in E2E)
    cov = qs.get("coverage_by_product", {})
    zero = [p for p, v in cov.items() if v == 0]
    partial = [p for p, v in cov.items() if 0 < v < 0.7]
    ready = [p for p, v in cov.items() if v >= 0.7]
    print(f"\n  [P0-2] Coverage Breakdown:")
    print(f"    Overall by product: {cov}")
    print(f"    Zero coverage: {zero}")
    print(f"    Partial: {partial}")
    print(f"    Ready: {ready}")
    print(f"    Products without signed claims: {qs.get('_products_without_signed_claims', [])}")

    # P0-2 Fix: Show dimension-level coverage for each product
    dim_cov = qs.get("coverage_by_dimension", {})
    if dim_cov:
        print(f"\n  [P0-2] Dimension-Level Coverage:")
        for product, dims in dim_cov.items():
            print(f"    {product}:")
            for dim, info in dims.items():
                if isinstance(info, dict):
                    status = info.get("status", "?")
                    rate = info.get("rate", 0)
                    print(f"      {dim}: {status} (rate={rate:.0%})")
                else:
                    print(f"      {dim}: {info}")
    else:
        # Build dimension coverage from analyst-signed claims
        all_claims = merged_signed_claims
        from collections import defaultdict
        dim_by_product = defaultdict(lambda: defaultdict(list))
        for c in all_claims:
            pid = c.get("product_id", "unknown")
            dim = c.get("dimension", "function_tree")
            dim_by_product[pid][dim].append(c)
        print(f"\n  [P0-2] Dimension-Level Coverage (from claims):")
        for product, dims in sorted(dim_by_product.items()):
            print(f"    {product}:")
            for dim, claims in sorted(dims.items()):
                print(f"      {dim}: {len(claims)} signed claim(s)")

    # Sections
    print(f"\n  Sections ({len(sections)} total):")
    for s in sections:
        title = s.get("section_title", s.get("section_slug", "?"))
        content = s.get("content_markdown", "")
        words = len(content)
        print(f"    - [{s.get('section_slug','?')}] {title}: {words} chars")

    # Check for strong recommendations in all sections (blocked report)
    print(f"\n  Checking for strong recommendation patterns...")
    strong_patterns = [
        r"top\s*pick", r"optimal\s*choice", r"optimal\s*pick",
        r"most\s*mature", r"strongly\s*recommend",
        r"最佳", r"最优", r"最优选", r"最优秀", r"最佳选择", r"最佳方案",
        r"most\s*cost-effective", r"best\s*suited", r"best\s*option", r"best\s*choice",
        r"winner", r"market\s*leader",
        r"优先选择", r"最值得推荐", r"最推荐", r"明确推荐", r"首推",
        r"\🥇", r"\🥈", r"\🥉",
    ]
    import re
    found_strong = []
    for s in sections:
        content = s.get("content_markdown", "")
        for pattern in strong_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                slug = s.get("section_slug", "?")
                found_strong.append(f"  [{slug}] {pattern}: {matches[:2]}")
    if found_strong:
        print(f"  WARNING: Strong patterns found ({len(found_strong)}):")
        for f in found_strong[:10]:
            print(f)
    else:
        print(f"  OK: No strong recommendation patterns found")

    # Check for literal \n in markdown
    md = result.get("content_markdown", "")
    if "\\n" in md:
        pos = md.find("\\n")
        print(f"  WARNING: literal \\n found in markdown at pos {pos}: {repr(md[pos-20:pos+40])}")
    else:
        print(f"  OK: No literal \\n in markdown")

    # ── Step 7: Enhanced diagnostic output ───────────────────────────────────
    print(f"\n[Step 7] Evidence Diagnostic...")

    # 7a: Evidence quality table
    print(f"\n  Evidence Quality Table ({len(gated_evidence)} gated items):")
    print(f"  {'evidence_id':<30} {'product':<10} {'schema_key':<40} {'lang':<6} {'score':<6} {'usable':<7} {'gate_rej':<30}")
    print(f"  {'-'*130}")
    chinese_chars = set("的一是在不了有和人这中大为上个国我以要他时来")
    for e in gated_evidence:
        snippet = e.get("snippet", "")[:60]
        lang = "ZH" if any(c in snippet for c in chinese_chars) else "EN"
        quality = e.get("quality", {})
        score = quality.get("final_score", e.get("quality_score", 0))
        usable = quality.get("usable_for_claim", e.get("usable_for_claim", False))
        gate_rej = e.get("gate_rejection", "")
        schema = e.get("schema_key", "?")[:38]
        eid = e.get("evidence_id", "?")[:28]
        pid = e.get("product_id", "?")[:8]
        print(f"  {eid:<30} {pid:<10} {schema:<40} {lang:<6} {score:<6.3f} {str(usable):<7} {gate_rej:<30}")

    # 7b: Claim-evidence binding table
    print(f"\n  Claim-Evidence Binding Table:")
    all_claims = [(c, "signed") for c in signed_claims] + [(c, "rework") for c in rework_required_claims]
    print(f"  {'claim_id':<45} {'dimension':<35} {'#evids(pre)':<12} {'#evids(post)':<12} {'status'}")
    print(f"  {'-'*115}")
    for c, stype in all_claims:
        cid = c.get("claim_id", "?")[:43]
        dim = c.get("dimension", "?")[:33]
        pre = len(c.get("evidence_ids", []))
        # For filtered claims, evidence_ids already filtered; show pre-filter would be unknown
        # So we show current count and note it
        post = pre  # already filtered in this test
        print(f"  {cid:<45} {dim:<35} {pre:<12} {post:<12} {stype}")

    # 7c: Usable evidence by product and schema (use product_slug, not product_id)
    print(f"\n  Usable Evidence by Product x Schema:")
    usable_by_ps = {}
    for e in gated_evidence:
        if e.get("usable_for_claim", False) or e.get("quality", {}).get("usable_for_claim", False):
            # product_slug contains clean name e.g. "dify", "coze", "fastgpt"
            pid = e.get("product_slug", e.get("product_id", "?"))[:20]
            sk = e.get("schema_key", "?")[:30]
            bucket = usable_by_ps.setdefault(pid, {})
            bucket[sk] = bucket.get(sk, 0) + 1
    for pid, schemas in sorted(usable_by_ps.items()):
        print(f"  {pid}:")
        for sk, cnt in sorted(schemas.items()):
            print(f"    {sk}: {cnt}")

    print(f"\n  Signed Claims by Product:")
    for c in signed_claims:
        pid = c.get("product_id", "?")[:20]
        dim = c.get("dimension", "?")[:30]
        ev_cnt = len(c.get("evidence_ids", []))
        print(f"    {pid} | {dim} | {ev_cnt} evidence_ids")

    # ── End diagnostics ─────────────────────────────────────────────────────
    out_path = f"e2e_full_result_{report_id[:30]}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  Full result saved: {out_path}")
    print(f"  File size: {os.path.getsize(out_path) / 1024:.1f} KB")

    print("\n" + "=" * 70)
    print("E2E Complete")
    print("=" * 70)


if __name__ == "__main__":
    main()
