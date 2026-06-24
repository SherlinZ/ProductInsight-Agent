"""
Workflow Orchestrator — DAG graph definition and runtime engine.

ARCHITECTURE OVERVIEW
====================
This module implements a multi-agent orchestration system with structured
message passing, quality-gated feedback loops, and iterative rework closure.

Key design principles:

1. AGENT COMMUNICATION VIA SHARED WorkflowState (TypedDict)
   ─────────────────────────────────────────────────────
   Agents do not communicate peer-to-peer. Instead, they communicate through
   a shared WorkflowState dictionary — a strongly-typed, versioned payload
   that carries all context between nodes. Each agent reads from state,
   performs its task, and writes results back to state. The orchestrator
   passes the mutated state to the next agent.

   Message contract example (reviewer → rework):
     state["rework_requests"].append({
       "dimension": "pricing_model",
       "claim_id": "claim_001",
       "reason": "MISSING_EVIDENCE",
       "evidence_ids": [],
       "priority": "high",
       "instructions": "Find evidence from official_pricing_page",
     })

   This is analogous to structured function-call tool_result payloads in
   LLM-based tool-calling frameworks, but expressed at the Python level for
   type safety and debuggability.

2. QUALITY GATING — ReviewerAgent as structured quality inspector
   ─────────────────────────────────────────────────────────────
   ReviewerAgent reviews claims at three levels:
     a. Evidence quality (EvidenceEvaluator): 5-dimension weighted scoring
        with usable_for_claim binary gate (score >= 0.32 AND relevance >= 0.20)
     b. Claim contract: evidence source type whitelist, PII check, confidence
     c. Report contract: claim-evidence linkage, span support

   A failed gate produces a structured rework_request payload in state,
   which the orchestrator routes back to execute_rework.

3. ITERATIVE FEEDBACK LOOPS — Three independent rework loops
   ─────────────────────────────────────────────────────────
   Each loop has MAX_ITERATIONS guards to prevent infinite execution:
     Loop A — Coverage:   coverage_critic → execute_rework → evaluate_evidence → analyze_dimensions
     Loop B — Claims:    review_claims → reflect_on_review → execute_rework → evaluate_evidence
     Loop C — Report:    final_review → write_report_v2 → final_review

   The sequential fallback runner implements the same loops by jumping the
   program counter back to earlier nodes, ensuring replay mode mirrors the
   same iteration semantics.

4. HUMAN INTERVENTION — Soft gate with async resolution
   ─────────────────────────────────────────────────────
   When automated loops cannot resolve quality issues, prepare_human_intervention
   aggregates signals and exposes them to the frontend. API endpoints exist for
   human approval/rejection; after resolution, replay_run() resumes execution.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Per-node timeout limits (seconds). None = no timeout.
# collect_sources: web scraping + Playwright retry + evidence extraction (LLM calls).
#   With 4 products × 5 URLs each, full scraping + evidence extraction takes 10-15 min.
#   P0-C retry pass (Playwright) adds ~5 min per failed URL.
#   Set generously to 2 hours to avoid premature timeout on slow networks.
# write_report_v2: LLM-heavy report generation for 4 products, 9000+ words.
#   Previous runs showed 600s insufficient; 20 min allows full chapter generation.
# coverage_critic: LLM call for coverage assessment per product/dimension.
#   5 min allows for multiple LLM invocations across products.
NODE_TIMEOUTS: dict[str, int | None] = {
    "build_task_brief": 60,
    "plan_schema": 120,
    "plan_sources": 60,
    "collect_sources": 1800,  # P1-Hotfix: raised from 900 to 1800. The node does discovery (health
                              # check + API calls ~20-60s) PLUS parallel URL collection (up to 4 batches
                              # × 225s if sites are slow). Previous 900s was too tight and caused
                              # the outer thread to be killed mid-run, even when inner collection
                              # was completing successfully in ~75s. 30min gives enough headroom.
    "evidence_extraction": 600,  # 10 min — parallel CPU text analysis
    "evaluate_evidence": 600,
    "pii_scrub": 60,
    "extract_facts": 600,
    "detect_schema_gaps": 300,
    "coverage_critic": 3600,   # 60 min — LLM coverage assessment + rework loops
    "execute_rework": 900,
    "analyze_dimensions": 900,
    "review_claims": 900,
    "reflect_on_review": 600,
    "prepare_human_intervention": 60,
    "write_report_v2": 5400,  # 90 min — LLM-heavy report generation + section revision loops
    "final_review": 600,
    "export_report": 300,
    "compute_metrics": 120,
}


class NodeTimeoutError(Exception):
    """Raised when a workflow node exceeds its timeout limit."""
    pass


class WorkflowPaused(Exception):
    """Raised when a workflow node triggers human-in-the-loop review.

    The workflow pauses at this node and waits for human resolution
    before it can be replayed to continue.
    """

    def __init__(
        self,
        message: str,
        paused_node: str,
        interventions: list[dict[str, Any]] | None = None,
        resume_state: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.paused_node = paused_node
        self.interventions = interventions or []
        self.resume_state = resume_state


try:
    from langgraph.graph import StateGraph, END
except Exception:  # pragma: no cover
    StateGraph = None
    END = "__end__"

from backend.app.orchestrator.state import WorkflowState
from backend.app.orchestrator import nodes


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Iteration guards – prevent infinite loops in conditional routing
# ------------------------------------------------------------------
# These are state keys used by route functions. Each route function
# reads and increments these counters; the corresponding graph edge is
# only followed when the counter is below the threshold.
MAX_CLAIMS_REWORK_ITERATIONS = 3  # review_claims → execute_rework → analyze_dimensions
MAX_CLAIMS_REFLECT_ITERATIONS = 2  # reflect cycles before exiting claims loop → prepare_human_intervention
MAX_REPORT_REWRITE_ITERATIONS = 3  # final_review → write_report_v2
MAX_COVERAGE_REWORK_ITERATIONS = 3  # coverage_critic → execute_rework (multi-round search)
# P1-Redesign (2026-06-18): rework may trigger a real re-collect round, so cap
# how many times we re-enter collect_sources. Beyond this, we fall back to the
# evaluate_evidence-only path (original behavior).
MAX_REWORK_COLLECT_ITERATIONS = 2  # reflect_on_review → execute_rework → collect_sources (true re-fetch)


def _summarize_state(state: WorkflowState) -> dict[str, Any]:
    """Small, serializable execution summary for trace/message payloads."""
    summary = {
        "run_id": state.get("run_id"),
        "mode": state.get("mode"),
        "sources": len(state.get("sources", []) or []),
        "evidence_items": len(state.get("evidence_items", []) or []),
        "facts": len(state.get("facts", []) or []),
        "claim_drafts": len(state.get("claim_drafts", []) or []),
        "signed_claims": len(state.get("signed_claims", []) or []),
        "rework_requests": len(state.get("rework_requests", []) or []),
        "errors": len(state.get("errors", []) or []),
    }
    interventions = state.get("human_interventions", [])
    if interventions is not None:
        summary["human_interventions"] = len(interventions)
        summary["requires_human_review"] = state.get("requires_human_review", False)
    ev_eval = state.get("evidence_evaluation")
    if ev_eval:
        summary["evidence_eval_total"] = ev_eval.get("total_evidence", 0)
        summary["evidence_eval_usable"] = ev_eval.get("usable_evidence", 0)
        summary["evidence_eval_avg_score"] = ev_eval.get("avg_final_score", 0.0)
        summary["evidence_eval_low_quality"] = ev_eval.get("low_quality_count", 0)
    schema_gaps = state.get("schema_gaps")
    schema_coverage = state.get("schema_coverage")
    if schema_gaps is not None:
        summary["schema_gaps"] = len(schema_gaps)
    if schema_coverage:
        summary["schema_coverage_rate"] = schema_coverage.get("schema_completion_rate", 0.0)
        summary["high_priority_schema_gaps"] = schema_coverage.get("high_priority_gaps", 0)
    rework_summary = state.get("rework_summary", {})
    if rework_summary:
        summary["rework_tasks"] = rework_summary.get("total_tasks", 0)
        summary["rework_succeeded"] = rework_summary.get("succeeded", 0)
        summary["rework_failed"] = rework_summary.get("failed", 0)

    # ── Schema key frequency ───────────────────────────────────────────
    schema_key_counter: Counter[str] = Counter()
    for ev in state.get("evidence_items", []) or []:
        sk = ev.get("schema_key", "unknown")
        schema_key_counter[sk] += 1
    if schema_key_counter:
        summary["top_schema_keys"] = [k for k, _ in schema_key_counter.most_common(5)]

    # ── Sample source domains ─────────────────────────────────────────
    seen_domains: set[str] = set()
    sample_domains: list[str] = []
    for src in state.get("sources", []) or []:
        url = src.get("url") or ""
        domain = url.split("/")[2] if "//" in url else url
        if domain and domain not in seen_domains and len(sample_domains) < 3:
            seen_domains.add(domain)
            sample_domains.append(domain)
    if sample_domains:
        summary["sample_domains"] = sample_domains

    # ── Product names ─────────────────────────────────────────────────
    # Products live in state["task_brief"]["products"], not state["products"]
    tb_products = state.get("task_brief", {}).get("products", [])
    if tb_products:
        names = [p.get("product_name") if isinstance(p, dict) else (p if isinstance(p, str) else "") for p in tb_products]
        names = [n for n in names if n]
        if names:
            summary["top_products"] = names[:3]
            summary["product_count"] = len(names)

    # ── Top schema gap dimensions ──────────────────────────────────────
    gaps = state.get("schema_gaps", [])
    if gaps:
        summary["top_gap_dims"] = [
            (g.get("dimension") or g.get("field") or "unknown") for g in gaps[:5]
        ]

    # ── Claim draft titles ────────────────────────────────────────────
    claims = state.get("claim_drafts", [])
    if claims:
        titles = [
            (c.get("title") or c.get("claim_text", "")[:60]) for c in claims
        ]
        summary["top_claim_titles"] = [t for t in titles if t][:3]

    # ── Rework reasons ────────────────────────────────────────────────
    rework_reqs = state.get("rework_requests", [])
    if rework_reqs:
        summary["top_rework_reasons"] = [
            ((r.get("dimension") or r.get("reason", "") or "")[:50]) for r in rework_reqs[:5]
        ]

    # ── Source type breakdown ─────────────────────────────────────────
    src_type_counter: Counter[str] = Counter()
    for src in state.get("sources", []) or []:
        st = src.get("source_type", "unknown") or "unknown"
        src_type_counter[st] += 1
    if src_type_counter:
        summary["source_types"] = dict(src_type_counter)

    return summary


def _agent_for_node(node_name: str) -> str:
    return {
        "build_task_brief": "Orchestrator",
        "plan_schema": "SchemaPlanner",
        "plan_sources": "SourcePlanner",
        "collect_sources": "CollectorAgent",
        "evaluate_evidence": "Evaluator",
        "pii_scrub": "ComplianceAgent",
        "extract_facts": "ExtractorAgent",
        "detect_schema_gaps": "SchemaGapPlanner",
        "coverage_critic": "CoverageCritic",
        "execute_rework": "ReworkAgent",
        "analyze_dimensions": "AnalystAgent",
        "review_claims": "ReviewerAgent",
        "prepare_human_intervention": "HumanReviewAgent",
        "write_report": "WriterAgent",
        "write_report_v2": "DeepReportAgent",
        "final_review": "ReviewerAgent",
        "export_report": "ReportExporter",
        "compute_metrics": "Evaluator",
    }.get(node_name, "Orchestrator")


# ------------------------------------------------------------------
# _wrap_node – framework-neutral node wrapper
#
# Key design for LangGraph compatibility:
# - Catches ALL exceptions and attaches error info to state, then
#   returns state (does NOT re-raise). This ensures LangGraph's
#   compiled.invoke() always receives the mutated state dict and can
#   pass it to the next node/route function.
# - Records pause/pause-info via state flags set by prepare_human_intervention.
# ------------------------------------------------------------------
def _wrap_node(node_name: str, fn: Callable[[WorkflowState], WorkflowState]) -> Callable[[WorkflowState], WorkflowState]:
    def _wrapped(state: WorkflowState) -> WorkflowState:
        trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        started = _utc_now()
        start_time = time.perf_counter()
        run_id = state.get("run_id", "unknown")
        task_id = state.get("task_id", "")
        agent_name = _agent_for_node(node_name)
        input_summary = _summarize_state(state)
        status = "success"
        error_message: str | None = None
        output_state: WorkflowState = state
        timed_out = False

        # ── Start node in DB ─────────────────────────────────────────
        try:
            from backend.app.storage.repositories import WorkflowRepository, RunRepository
            WorkflowRepository().start_node(run_id, node_name, input_summary)
            RunRepository().update_status(run_id, "running", node_name)
        except Exception as exc:
            logger.warning("_wrap_node: failed to start node %s: %s", node_name, exc)

        # ── Run node (with optional timeout) ────────────────────────
        timeout_secs = NODE_TIMEOUTS.get(node_name)

        def _run_node() -> None:
            nonlocal output_state
            try:
                output_state = fn(state)
            except Exception as raw_exc:
                output_state = state
                output_state.setdefault("_node_exception", raw_exc)

        if timeout_secs is not None:
            print(f"[_wrap_node] {node_name}: starting thread (timeout={timeout_secs}s)", flush=True)
            t = threading.Thread(target=_run_node, daemon=True)
            t.start()
            t.join(timeout=timeout_secs)
            elapsed = time.perf_counter() - start_time
            if t.is_alive():
                # Timeout: thread is still running, treat as failed
                timed_out = True
                status = "failed"
                error_message = f"Node '{node_name}' exceeded timeout of {timeout_secs}s"
                print(f"[_wrap_node] {node_name}: TIMEOUT after {elapsed:.1f}s", flush=True)
                logger.error("Node timeout: %s", error_message)
                output_state.setdefault("errors", []).append({
                    "reason_code": "NODE_TIMEOUT",
                    "message": error_message,
                    "node": node_name,
                    "timeout_secs": timeout_secs,
                })
                output_state["_failed_node"] = node_name
                output_state["_failed_node_error"] = error_message
                output_state["_timed_out"] = True

                # P0-Fix: collect_sources writes incremental checkpoints to disk.
                # When the thread is killed by timeout, the node's return value is lost.
                # Recover partial data from the checkpoint so downstream nodes have sources.
                # IMPORTANT: Write recovered data back to `state` in-place (not just output_state).
                # evidence_extraction reads from `state["sources"]`, not `output_state["sources"]`.
                if node_name == "collect_sources":
                    from pathlib import Path
                    import json as _json
                    from datetime import datetime, timezone
                    ckpt_path = Path(f"/tmp/collector_ckpt_{run_id}.json")
                    if ckpt_path.exists():
                        try:
                            ckpt_data = _json.loads(ckpt_path.read_text())
                            ckpt_results = ckpt_data.get("results", [])
                            now_str = datetime.now(timezone.utc).isoformat()
                            recovered_sources: list = []
                            recovered_snapshots: list = []
                            for res in ckpt_results:
                                task = res.get("_task", {})
                                url = task.get("url", "")
                                product_id = task.get("product_id", "")
                                source_id = task.get("source_id", "")
                                snapshot_id = task.get("snapshot_id", "")
                                error_msg = res.get("error_message")
                                status_code = res.get("status_code", 0)
                                raw_text = res.get("raw_text", "") or ""
                                raw_html = res.get("raw_html", "") or ""
                                title = res.get("title", "") or task.get("product_name", "")
                                domain = res.get("domain", "")
                                content_hash = res.get("content_hash", "")
                                fetched_at = res.get("fetched_at", now_str)
                                source_type = task.get("source_type", "official_site")
                                source_record = {
                                    "run_id": run_id,
                                    "source_id": source_id,
                                    "product_id": product_id,
                                    "url": url,
                                    "source_type": source_type,
                                    "fetch_level": task.get("fetch_level", 1),
                                    "fetch_strategy": task.get("fetch_strategy", "requests"),
                                    "collection_method": task.get("collection_method", "seed_url"),
                                    "status": "collected" if not error_msg else "failed",
                                    "char_count": len(raw_text),
                                    "content": raw_text,
                                    "raw_html": raw_html,
                                    "content_hash": content_hash,
                                    "title": title,
                                    "domain": domain,
                                    "error_message": error_msg,
                                    "status_code": status_code,
                                    "fetched_at": fetched_at,
                                    "created_at": now_str,
                                }
                                recovered_sources.append(source_record)
                                snapshot_record = {
                                    "run_id": run_id,
                                    "snapshot_id": snapshot_id,
                                    "source_id": source_id,
                                    "product_id": product_id,
                                    "content": raw_text,
                                    "char_count": len(raw_text),
                                    "created_at": now_str,
                                }
                                recovered_snapshots.append(snapshot_record)
                                try:
                                    from backend.app.storage.repositories import SourceRepository, EvidenceRepository
                                    SourceRepository().add_source(source_record)
                                    EvidenceRepository().add_snapshot(snapshot_record)
                                except Exception as db_exc:
                                    logger.warning("_wrap_node: checkpoint recovery DB write failed: %s", db_exc)
                            logger.warning(
                                "_wrap_node: collect_sources TIMEOUT — recovered %d sources and %d snapshots from checkpoint",
                                len(recovered_sources), len(recovered_snapshots),
                            )
                            # Write to both output_state AND state (in-place sync).
                            # evidence_extraction reads from state["sources"], not output_state.
                            output_state["sources"] = recovered_sources
                            output_state["snapshots"] = recovered_snapshots
                            output_state["raw_documents"] = []
                            output_state["evidence_items"] = []
                            output_state["_collect_sources_from_checkpoint"] = True
                            # In-place sync to state so subsequent nodes see the recovered data
                            state["sources"] = recovered_sources
                            state["snapshots"] = recovered_snapshots
                            state["raw_documents"] = []
                            state["evidence_items"] = []
                            state["_collect_sources_from_checkpoint"] = True
                            ckpt_path.unlink(missing_ok=True)
                        except Exception as exc:
                            logger.warning("_wrap_node: checkpoint recovery failed for collect_sources: %s", exc)
            elif output_state.get("_node_exception"):
                raw_exc = output_state.pop("_node_exception")
                status = "failed"
                error_message = str(raw_exc)
                print(f"[_wrap_node] {node_name}: EXCEPTION in thread: {raw_exc}", flush=True)
                logger.warning("_wrap_node: %s raised exception in thread: %s", node_name, raw_exc)
                output_state.setdefault("errors", []).append({
                    "reason_code": "NODE_EXCEPTION",
                    "message": f"{node_name}: {raw_exc}",
                    "node": node_name,
                })
                output_state["_failed_node"] = node_name
                output_state["_failed_node_error"] = error_message
            else:
                print(f"[_wrap_node] {node_name}: completed in thread ({elapsed:.1f}s)", flush=True)
        else:
            # No timeout config for this node OR sequential fallback: run synchronously
            # Wrap in a thread so we can apply timeout uniformly regardless of path.
            _sequential_result: dict[str, WorkflowState] = {}
            _exc_info: list[Any] = []

            def _run_sequential() -> None:
                try:
                    _sequential_result["state"] = fn(state)
                except Exception as raw_exc:
                    _exc_info.append(raw_exc)

            t = threading.Thread(target=_run_sequential, daemon=True)
            t.start()
            timeout_val = NODE_TIMEOUTS.get(node_name) or 300
            t.join(timeout=timeout_val)
            if t.is_alive():
                timed_out = True
                status = "failed"
                error_message = f"Node '{node_name}' exceeded timeout of {timeout_val}s"
                logger.error("Node timeout (sequential): %s", error_message)
                output_state.setdefault("errors", []).append({
                    "reason_code": "NODE_TIMEOUT",
                    "message": error_message,
                    "node": node_name,
                    "timeout_secs": timeout_val,
                })
                output_state["_failed_node"] = node_name
                output_state["_failed_node_error"] = error_message
                output_state["_timed_out"] = True
            elif _exc_info:
                raw_exc = _exc_info[0]
                status = "failed"
                error_message = str(raw_exc)
                output_state.setdefault("errors", []).append({
                    "reason_code": "NODE_EXCEPTION",
                    "message": f"{node_name}: {raw_exc}",
                    "node": node_name,
                })
                output_state["_failed_node"] = node_name
                output_state["_failed_node_error"] = error_message
            elif _sequential_result:
                output_state = _sequential_result["state"]

        # ── Sequential path: checkpoint recovery for collect_sources timeout ─
        if timed_out and node_name == "collect_sources":
            from pathlib import Path
            import json as _ckpt_json
            from datetime import datetime, timezone
            ckpt_path = Path(f"/tmp/collector_ckpt_{run_id}.json")
            if ckpt_path.exists():
                try:
                    ckpt_data = _ckpt_json.loads(ckpt_path.read_text())
                    ckpt_results = ckpt_data.get("results", [])
                    now_str = datetime.now(timezone.utc).isoformat()
                    recovered_sources: list = []
                    recovered_snapshots: list = []
                    for res in ckpt_results:
                        task = res.get("_task", {})
                        url = task.get("url", "")
                        product_id = task.get("product_id", "")
                        source_id = task.get("source_id", "")
                        snapshot_id = task.get("snapshot_id", "")
                        error_msg = res.get("error_message")
                        raw_text = res.get("raw_text", "") or ""
                        raw_html = res.get("raw_html", "") or ""
                        title = res.get("title", "") or task.get("product_name", "")
                        domain = res.get("domain", "")
                        content_hash = res.get("content_hash", "")
                        fetched_at = res.get("fetched_at", now_str)
                        source_type = task.get("source_type", "official_site")
                        source_record = {
                            "run_id": run_id, "source_id": source_id, "product_id": product_id,
                            "url": url, "source_type": source_type,
                            "fetch_level": task.get("fetch_level", 1),
                            "fetch_strategy": task.get("fetch_strategy", "requests"),
                            "collection_method": task.get("collection_method", "seed_url"),
                            "status": "collected" if not error_msg else "failed",
                            "char_count": len(raw_text), "content": raw_text,
                            "raw_html": raw_html, "content_hash": content_hash,
                            "title": title, "domain": domain,
                            "error_message": error_msg, "fetched_at": fetched_at,
                            "created_at": now_str,
                        }
                        recovered_sources.append(source_record)
                        recovered_snapshots.append({
                            "run_id": run_id, "snapshot_id": snapshot_id,
                            "source_id": source_id, "product_id": product_id,
                            "content": raw_text, "char_count": len(raw_text),
                            "created_at": now_str,
                        })
                        try:
                            from backend.app.storage.repositories import SourceRepository, EvidenceRepository
                            SourceRepository().add_source(source_record)
                            EvidenceRepository().add_snapshot(recovered_snapshots[-1])
                        except Exception as db_exc:
                            logger.warning("_wrap_node (seq): checkpoint DB write failed: %s", db_exc)
                    logger.warning(
                        "_wrap_node (seq): collect_sources TIMEOUT — recovered %d sources and %d snapshots",
                        len(recovered_sources), len(recovered_snapshots),
                    )
                    output_state["sources"] = recovered_sources
                    output_state["snapshots"] = recovered_snapshots
                    output_state["raw_documents"] = []
                    output_state["evidence_items"] = []
                    output_state["_collect_sources_from_checkpoint"] = True
                    state["sources"] = recovered_sources
                    state["snapshots"] = recovered_snapshots
                    state["raw_documents"] = []
                    state["evidence_items"] = []
                    state["_collect_sources_from_checkpoint"] = True
                    ckpt_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("_wrap_node (seq): checkpoint recovery failed: %s", exc)

        # ── Detect human-in-the-loop pause ─────────────────────────
        # Pause if ANY node sets workflow_pause_node to signal that it needs human confirmation.
        should_pause = bool(output_state.get("workflow_pause_node") and output_state.get("requires_human_review"))
        if should_pause:
            status = "paused"
            error_message = output_state.get("workflow_pause_reason") or "Human review required"
            pause_summary = output_state.get("workflow_pause_output_summary") or ""
            interventions = output_state.get("human_interventions", [])
            output_state["_workflow_paused_at"] = node_name
            output_state["_workflow_paused_interventions"] = interventions

        # ── Compute summary & latency ────────────────────────────────
        completed = _utc_now()
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        output_summary = _summarize_state(output_state)

        # ── Backfill enriched fields into input_summary ────────────────
        # input_summary was captured before fn(state) ran, so it lacks rich fields
        # that fn populates (e.g. top_products from task_brief enrichment).
        # Patch input_summary with any enriched fields now present in output_summary.
        _RICH_FIELDS = (
            "top_schema_keys", "sample_domains", "source_types",
            "top_claim_titles", "top_products", "product_count",
            "schema_gaps", "schema_coverage_rate", "high_priority_schema_gaps",
            "top_gap_dims", "rework_tasks", "rework_succeeded", "rework_failed",
            "top_rework_reasons", "evidence_eval_total", "evidence_eval_usable",
            "evidence_eval_avg_score", "evidence_eval_low_quality",
        )
        _enriched_fields = {}
        for key in _RICH_FIELDS:
            if key in output_summary and key not in input_summary:
                input_summary[key] = output_summary[key]
                _enriched_fields[key] = output_summary[key]

        # ── Update DB (with suppressed errors) ──────────────────────
        try:
            from backend.app.storage.repositories import TraceRepository, MessageRepository, WorkflowRepository, RunRepository

            try:
                if should_pause:
                    RunRepository().update_status(
                        run_id, "paused", node_name,
                        error_message=error_message,
                        completed_at=output_state.get("workflow_pause_completed_at")
                    )
                    # Save full workflow state so it can be restored on resume
                    try:
                        RunRepository().save_workflow_state(run_id, output_state)
                    except Exception as exc:
                        logger.warning("_wrap_node: failed to save workflow_state for pause: %s", exc)
                else:
                    RunRepository().update_status(run_id, "running", node_name)
            except Exception as exc:
                logger.warning("_wrap_node: failed to update runs table for node %s: %s", node_name, exc)

            try:
                if should_pause:
                    WorkflowRepository().pause_node(run_id, node_name, pause_summary, error_message)
                elif status == "success":
                    WorkflowRepository().patch_node_input_summary(run_id, node_name, _enriched_fields)
                    WorkflowRepository().complete_node(run_id, node_name, output_summary, latency_ms)
                else:
                    WorkflowRepository().patch_node_input_summary(run_id, node_name, _enriched_fields)
                    WorkflowRepository().fail_node(run_id, node_name, error_message or "", output_summary, latency_ms)
            except Exception as exc:
                logger.warning("_wrap_node: failed to update workflow node %s: %s", node_name, exc)

            TraceRepository().add_trace({
                "trace_id": trace_id,
                "run_id": run_id,
                "project_id": state.get("project_id"),
                "node_name": node_name,
                "agent_name": agent_name,
                "agent_role": node_name,
                "event_type": "node_execution",
                "prompt_version": "rule_or_agent_v1",
                "model_name": "non_llm",
                "input_path": "state",
                "output_path": "state",
                "decision": f"input={json.dumps(input_summary)[:500]}; output={json.dumps(output_summary)[:500]}",
                "token_input": 0,
                "token_output": 0,
                "latency_ms": latency_ms,
                "status": status,
                "error_message": error_message,
                "started_at": started,
                "completed_at": completed,
                "created_at": completed,
                "input_payload": input_summary,
                "output_payload": output_summary,
                "decision_summary": f"{node_name} {status} in {latency_ms}ms",
                "retry_count": 0,
                "artifact_refs": [
                    {"type": "sources", "count": input_summary.get("sources", 0)},
                    {"type": "evidence_items", "count": input_summary.get("evidence_items", 0)},
                    {"type": "facts", "count": input_summary.get("facts", 0)},
                    {"type": "claim_drafts", "count": input_summary.get("claim_drafts", 0)},
                ],
            })
            MessageRepository().add_message({
                "message_id": f"msg_{uuid.uuid4().hex[:16]}",
                "run_id": run_id,
                "task_id": task_id,
                "sender": "orchestrator",
                "receiver": agent_name,
                "message_type": "node_execution",
                "schema_version": "1.0.0",
                "payload": {
                    "node_name": node_name,
                    "input_summary": input_summary,
                    "output_summary": output_summary,
                    "status": status,
                },
                "metadata": {"latency_ms": latency_ms},
                "trace_id": trace_id,
                "created_at": completed,
            })
        except Exception:
            # Trace must never break the actual workflow.
            pass

        return output_state

    return _wrapped


# ------------------------------------------------------------------
# Route functions – control conditional edges in the graph
# ------------------------------------------------------------------

def route_after_coverage_critic(state: WorkflowState) -> str:
    """
    Route after coverage_critic.

    Reads _coverage_rework_count from state (incremented by this function)
    and enforces MAX_COVERAGE_REWORK_ITERATIONS.

    Edges:
      - insufficient + not exhausted → execute_rework
      - insufficient + exhausted → analyze_dimensions (force-proceed)
      - sufficient → analyze_dimensions
    """
    result = state.get("coverage_critic_result", {})
    status = result.get("status", "unknown")

    if status in ("critical", "weak"):
        count = state.get("_coverage_rework_count", 0)
        state["_coverage_rework_count"] = count + 1
        if count < MAX_COVERAGE_REWORK_ITERATIONS:
            logger.info(
                "route_after_coverage_critic: coverage=%s, rework_count=%d/%d → execute_rework",
                status, count + 1, MAX_COVERAGE_REWORK_ITERATIONS,
            )
            return "execute_rework"
        else:
            logger.warning(
                "route_after_coverage_critic: coverage=%s, rework_count=%d/%d EXHAUSTED → analyze_dimensions",
                status, count + 1, MAX_COVERAGE_REWORK_ITERATIONS,
            )
    return "analyze_dimensions"


def route_after_review_claims(state: WorkflowState) -> str:
    """
    Route after review_claims.

    P1-Redesign (2026-06-05): Human intervention is fully automated via LLM self-check.
    This function routes based on self_check result:

    - PASS: → write_report_v2 (report is ready)
    - RETRY with suggestion: → execute_rework (remediation needed)
    - fallback (no clear signal): → prepare_human_intervention
      (soft node: aggregates quality signals, then always → write_report_v2)

    State keys:
      - _claims_rework_count: total execute_rework cycles
      - _claims_reflect_count: total reflect_on_review cycles
    """
    rework_requests = state.get("rework_requests", []) or []
    reflect_count = state.get("_claims_reflect_count", 0)

    has_rework = bool(rework_requests)

    # LLM self-check takes priority
    self_check_detail = state.get("rework_self_check_detail", {})
    self_check_result = state.get("rework_self_check_result", "")

    if self_check_result == "PASS":
        logger.info("route_after_review_claims: self_check PASS → write_report_v2")
        return "write_report_v2"

    if self_check_result == "RETRY":
        suggestion = self_check_detail.get("suggestion")
        if suggestion and len(rework_requests) < 10:
            new_request = {
                "dimension": "self_check_remediation",
                "reason": f"自检建议: {suggestion}",
                "priority": "high",
                "specific_instruction": suggestion,
                "source": "llm_self_check",
            }
            state["rework_requests"] = rework_requests + [new_request]
            logger.info(
                f"route_after_review_claims: self_check RETRY with suggestion → execute_rework "
                f"(added rework request: {suggestion[:50]}...)"
            )
            return "execute_rework"

    # Fallback: enter rework reflect loop if there are rework requests
    if has_rework and reflect_count < MAX_CLAIMS_REFLECT_ITERATIONS:
        state["_claims_reflect_count"] = reflect_count + 1
        logger.info(
            "route_after_review_claims: %d rework items, reflect_count=%d/%d → reflect_on_review",
            len(rework_requests), reflect_count + 1, MAX_CLAIMS_REFLECT_ITERATIONS,
        )
        return "reflect_on_review"

    # Fallback path: no rework or loops exhausted → soft node, then write_report_v2
    if has_rework:
        logger.warning(
            "route_after_review_claims: %d rework items, reflect_count=%d/%d EXHAUSTED",
            len(rework_requests), reflect_count, MAX_CLAIMS_REFLECT_ITERATIONS,
        )
    else:
        logger.info("route_after_review_claims: no rework requests")

    # P1-Redesign: prepare_human_intervention is now a soft node — it just
    # collects quality info and immediately returns, then graph edges to write_report_v2.
    logger.info("route_after_review_claims: fallback → prepare_human_intervention (soft) → write_report_v2")
    return "prepare_human_intervention"


def route_after_reflect(state: WorkflowState) -> str:
    """
    Route after reflect_on_review.

    P1-Redesign (2026-06-05): Human intervention is fully automated.
    Routes:
      - PASS: → write_report_v2
      - RETRY with suggestion, under limit: → execute_rework
      - RETRY without suggestion OR count exhausted: forced PASS → write_report_v2
      - no rework: → write_report_v2
    """
    rework_requests = state.get("rework_requests", []) or []
    count = state.get("_claims_rework_count", 0)

    has_rework = bool(rework_requests)

    # 先检查 LLM 自检结果
    self_check_detail = state.get("rework_self_check_detail", {})
    self_check_result = state.get("rework_self_check_result", "")
    
    # 如果自检通过，直接进入报告生成
    if self_check_result == "PASS":
        logger.info("route_after_reflect: self_check PASS → write_report_v2")
        return "write_report_v2"

    # 自检 RETRY 且有建议：在未达上限时继续 execute_rework
    # 注意：不在这里重复计数，graph continue 已递增 count
    if self_check_result == "RETRY":
        suggestion = self_check_detail.get("suggestion")
        if suggestion and count < MAX_CLAIMS_REWORK_ITERATIONS:
            logger.info(
                "route_after_reflect: RETRY (suggestion=%s) → execute_rework "
                "(count=%d/%d, count incremented by graph continue)",
                suggestion[:50] if suggestion else "null",
                count, MAX_CLAIMS_REWORK_ITERATIONS,
            )
            return "execute_rework"

    # 正常迭代检查：rework_requests 且未达 claims rework 上限
    # P1-Redesign (2026-06-18): 当返回 execute_rework 时，实际路由由 graph
    # 的 run_workflow 决定是 true-recollect (→ collect_sources) 还是 re-score
    # (→ evaluate_evidence)，取决于 _rework_collect_count 是否达到上限。
    if has_rework and count < MAX_CLAIMS_REWORK_ITERATIONS:
        logger.info(
            "route_after_reflect: %d rework items, count=%d/%d → execute_rework "
            "(graph will check _rework_collect_count to decide collect_sources vs evaluate_evidence)",
            len(rework_requests), count + 1, MAX_CLAIMS_REWORK_ITERATIONS,
        )
        return "execute_rework"

    # FIX: RETRY 无建议 或 已达上限 → 强制 PASS 生成 blocked 报告（防止无限循环）
    if self_check_result == "RETRY" or (has_rework and count >= MAX_CLAIMS_REWORK_ITERATIONS):
        state["rework_self_check_result"] = "PASS"
        state["rework_self_check_detail"] = {
            "decision": "PASS",
            "reason": (
                f"Rework 循环已达上限（{count}次）或 RETRY 无改进建议，"
                f"强制通过生成报告（blocked 或 partial 状态）"
            ),
            "missing": [],
            "suggestion": None,
        }
        logger.info(
            "route_after_reflect: forced PASS (count=%d/%d, self_check=%s, rework=%d) → write_report_v2",
            count, MAX_CLAIMS_REWORK_ITERATIONS, self_check_result, len(rework_requests),
        )
        return "write_report_v2"

    # 无 rework_requests：直接生成报告
    logger.info("route_after_reflect: no rework requests → write_report_v2")
    return "write_report_v2"


def route_after_final_review(state: WorkflowState) -> str:
    """
    Route after final_review.

    Reads _report_rewrite_count from state (incremented by this function)
    and enforces MAX_REPORT_REWRITE_ITERATIONS.

    Only "rework_required" triggers a rewrite. "blocked" (no claims)
    proceeds directly to export_report where the quality gate handles it.
    """
    fr_result = state.get("final_review_result", {})
    status = fr_result.get("status", "")
    count = state.get("_report_rewrite_count", 0)
    state["_report_rewrite_count"] = count + 1

    if status == "rework_required" and count < MAX_REPORT_REWRITE_ITERATIONS:
        logger.info(
            "route_after_final_review: status=%s, count=%d/%d → write_report_v2",
            status, count + 1, MAX_REPORT_REWRITE_ITERATIONS,
        )
        return "write_report_v2"

    if status == "rework_required":
        logger.warning(
            "route_after_final_review: rework_required, count=%d/%d EXHAUSTED → export_report",
            count + 1, MAX_REPORT_REWRITE_ITERATIONS,
        )

    return "export_report"


def route_write_report(state: WorkflowState) -> str:
    """Always route to write_report_v2 (v1 is fully deprecated)."""
    return "write_report_v2"


# ------------------------------------------------------------------
# Graph builder
# ------------------------------------------------------------------
def build_graph() -> Any:
    if StateGraph is None:
        return None

    graph = StateGraph(WorkflowState)

    # ── Nodes ──────────────────────────────────────────────────────
    graph.add_node("build_task_brief", _wrap_node("build_task_brief", nodes.build_task_brief))
    graph.add_node("plan_schema", _wrap_node("plan_schema", nodes.plan_schema))
    graph.add_node("plan_sources", _wrap_node("plan_sources", nodes.plan_sources))
    graph.add_node("collect_sources", _wrap_node("collect_sources", nodes.collect_sources))
    graph.add_node("evidence_extraction", _wrap_node("evidence_extraction", nodes.evidence_extraction))
    graph.add_node("evaluate_evidence", _wrap_node("evaluate_evidence", nodes.evaluate_evidence))
    graph.add_node("pii_scrub", _wrap_node("pii_scrub", nodes.pii_scrub))
    graph.add_node("extract_facts", _wrap_node("extract_facts", nodes.extract_facts))
    graph.add_node("detect_schema_gaps", _wrap_node("detect_schema_gaps", nodes.detect_schema_gaps))
    graph.add_node("coverage_critic", _wrap_node("coverage_critic", nodes.coverage_critic))
    graph.add_node("execute_rework", _wrap_node("execute_rework", nodes.execute_rework))
    graph.add_node("analyze_dimensions", _wrap_node("analyze_dimensions", nodes.analyze_dimensions))
    graph.add_node("review_claims", _wrap_node("review_claims", nodes.review_claims))
    graph.add_node("reflect_on_review", _wrap_node("reflect_on_review", nodes.reflect_on_review))
    graph.add_node("prepare_human_intervention", _wrap_node("prepare_human_intervention", nodes.prepare_human_intervention))
    graph.add_node("write_report_v2", _wrap_node("write_report_v2", nodes.write_report_v2))
    graph.add_node("final_review", _wrap_node("final_review", nodes.final_review))
    graph.add_node("export_report", _wrap_node("export_report", nodes.export_report))
    graph.add_node("compute_metrics", _wrap_node("compute_metrics", nodes.compute_metrics))

    # ── Entry ─────────────────────────────────────────────────────
    graph.set_entry_point("build_task_brief")

    # ── Fixed edges ────────────────────────────────────────────────
    graph.add_edge("build_task_brief", "plan_schema")
    graph.add_edge("plan_schema", "plan_sources")
    graph.add_edge("plan_sources", "collect_sources")
    graph.add_edge("collect_sources", "evidence_extraction")  # P0-7: split evidence extraction
    graph.add_edge("evidence_extraction", "evaluate_evidence")
    graph.add_edge("evaluate_evidence", "pii_scrub")
    graph.add_edge("pii_scrub", "extract_facts")
    graph.add_edge("extract_facts", "detect_schema_gaps")
    graph.add_edge("detect_schema_gaps", "coverage_critic")

    # execute_rework always continues to analyze_dimensions (supplemental work is done in-place)
    graph.add_edge("execute_rework", "analyze_dimensions")

    # review_claims → reflect_on_review (if rework) or prepare_human_intervention (if no rework)
    graph.add_conditional_edges(
        "review_claims",
        route_after_review_claims,
        {
            "reflect_on_review": "reflect_on_review",
            "prepare_human_intervention": "prepare_human_intervention",
        },
    )

    # reflect_on_review → execute_rework (with enriched instructions) or prepare_human_intervention
    graph.add_conditional_edges(
        "reflect_on_review",
        route_after_reflect,
        {
            "execute_rework": "execute_rework",
            "prepare_human_intervention": "prepare_human_intervention",
        },
    )

    # After research is complete, proceed directly to writing the report (outline confirmed implicitly)
    graph.add_edge("prepare_human_intervention", "write_report_v2")

    # write_report_v2 → final_review (always, no skipping)
    graph.add_edge("write_report_v2", "final_review")

    # final_review → conditional: rework → write_report_v2 (with iteration guard),
    #                      or pass/block → export_report
    graph.add_conditional_edges(
        "final_review",
        route_after_final_review,
        {
            "write_report_v2": "write_report_v2",
            "export_report": "export_report",
        },
    )

    graph.add_edge("export_report", "compute_metrics")
    graph.add_edge("compute_metrics", END)

    # ── Conditional edges (coverage gate) ──────────────────────────
    graph.add_conditional_edges(
        "coverage_critic",
        route_after_coverage_critic,
        {
            "execute_rework": "execute_rework",
            "analyze_dimensions": "analyze_dimensions",
        },
    )

    return graph.compile()


# ------------------------------------------------------------------
# run_workflow – tries LangGraph first, falls back to sequential Python
# ------------------------------------------------------------------
def run_workflow(initial_state: WorkflowState) -> WorkflowState:
    run_id = initial_state.get("run_id", "unknown")

    # Initialize workflow graph for this run (idempotent)
    if run_id and run_id != "unknown":
        try:
            from backend.app.storage.repositories import WorkflowRepository
            WorkflowRepository().init_workflow_graph(run_id)
        except Exception as exc:
            logger.warning("run_workflow: failed to init workflow graph: %s", exc)

    # Reset iteration counters so each run starts fresh
    initial_state.setdefault("_coverage_rework_count", 0)
    initial_state.setdefault("_claims_rework_count", 0)
    initial_state.setdefault("_claims_reflect_count", 0)
    initial_state.setdefault("_report_rewrite_count", 0)
    initial_state.setdefault("_rework_collect_count", 0)  # P1-Redesign (2026-06-18)
    initial_state.pop("_failed_node", None)
    initial_state.pop("_failed_node_error", None)

    # Try LangGraph first
    compiled = build_graph()

    # TEMP: Force sequential path — LangGraph has a pre-existing bug where review_claims
    # state mutations don't propagate correctly through the graph. Sequential is stable.
    _debug_force_sequential = True

    if compiled is not None and not _debug_force_sequential:
        try:
            return compiled.invoke(initial_state)
        except Exception as exc:
            logger.error(
                "run_workflow (LangGraph): unhandled exception: %s. "
                "Returning partial state for inspection.",
                exc,
            )
            initial_state.setdefault("errors", []).append({
                "reason_code": "LANGGRAPH_UNHANDLED",
                "message": str(exc),
                "node": initial_state.get("_failed_node", "unknown"),
            })
            return initial_state

    # ── Fallback: sequential Python execution ──────────────────────
    # Mirrors the same node order and conditional routing as LangGraph.
    # Conditional routes are implemented as while-loops with the same
    # iteration guards used by the route functions above.
    # NOTE: LangGraph has a pre-existing state propagation bug in this codebase
    # where state mutations from wrapped nodes don't propagate correctly.
    # Sequential path is verified to work correctly (signed_claims are populated).
    _debug_force_sequential = True
    logger.warning("run_workflow: USING SEQUENTIAL FALLBACK (not LangGraph)!")
    def _get_write_report_node(_state: WorkflowState):
        return "write_report_v2"

    state = dict(initial_state)  # shallow copy so we don't mutate the caller's dict
    state.setdefault("_coverage_rework_count", 0)
    state.setdefault("_claims_rework_count", 0)
    state.setdefault("_claims_reflect_count", 0)
    state.setdefault("_report_rewrite_count", 0)
    state.setdefault("_rework_collect_count", 0)  # P1-Redesign (2026-06-18)

    node_sequence = [
        ("build_task_brief", nodes.build_task_brief),
        ("plan_schema", nodes.plan_schema),
        ("plan_sources", nodes.plan_sources),
        ("collect_sources", nodes.collect_sources),
        ("evidence_extraction", nodes.evidence_extraction),  # P0-7: split from collect_sources
        ("evaluate_evidence", nodes.evaluate_evidence),
        ("pii_scrub", nodes.pii_scrub),
        ("extract_facts", nodes.extract_facts),
        ("detect_schema_gaps", nodes.detect_schema_gaps),
        ("coverage_critic", nodes.coverage_critic),
        ("execute_rework", nodes.execute_rework),
        ("analyze_dimensions", nodes.analyze_dimensions),
        ("review_claims", nodes.review_claims),
        ("reflect_on_review", nodes.reflect_on_review),
        ("prepare_human_intervention", nodes.prepare_human_intervention),
        ("write_report_v2", nodes.write_report_v2),
        ("final_review", nodes.final_review),
        ("export_report", nodes.export_report),
        ("compute_metrics", nodes.compute_metrics),
    ]

    # Node name → index in sequence (for conditional routing)
    node_index = {name: i for i, (name, _) in enumerate(node_sequence)}

    # Simple program counter approach: walk forward through node_sequence,
    # but route_* functions can jump the counter backward (coverage rework loop)
    # or forward (skip to export on final_review pass).
    pc = 0
    while pc < len(node_sequence):
        node_name, fn = node_sequence[pc]

        state = _wrap_node(node_name, fn)(state)

        # Pause for human intervention
        if state.get("_workflow_paused_at"):
            logger.info(
                "run_workflow (fallback): paused at '%s' for human intervention.",
                state.get("_workflow_paused_at"),
            )
            state["workflow_paused"] = True
            state["paused_node"] = state.get("_workflow_paused_at")
            state["pause_interventions"] = state.get("_workflow_paused_interventions", [])
            return state

        # Handle failed node (from _wrap_node catching exception)
        if state.get("_failed_node") == node_name:
            logger.error(
                "run_workflow (fallback): node '%s' failed: %s. Continuing for cleanup.",
                node_name, state.get("_failed_node_error"),
            )
            state.pop("_failed_node", None)
            state.pop("_failed_node_error", None)
            # Continue to next node so traces/DB get updated

        # ── Conditional routing ─────────────────────────────────────
        logger.info("run_workflow (fallback): executed node=%s", node_name)
        if node_name == "coverage_critic":
            next_node = route_after_coverage_critic(state)
            if next_node == "execute_rework":
                # CRITICAL: Re-evaluate evidence quality before fetching more sources.
                # New evidence added by execute_rework needs quality scoring (usable_for_claim)
                # before analyze_dimensions can generate claims that review_claims can approve.
                pc = node_index["execute_rework"]
                state["_coverage_rework_count"] = state.get("_coverage_rework_count", 0) + 1
                # P0-Fix: Prevent infinite coverage_rework loops.
                # execute_rework reads from DB state which is reset each run,
                # so it always sees empty evidence and loops forever.
                # Detect: if _coverage_rework_count keeps incrementing without progress,
                # force exit after MAX_COVERAGE_REWORK_ITERATIONS.
                cc_count = state.get("_coverage_rework_count", 0)
                if cc_count >= MAX_COVERAGE_REWORK_ITERATIONS:
                    logger.warning(
                        "run_workflow (fallback): coverage_rework loop exhausted (%d iters) "
                        "→ force-proceeding to analyze_dimensions. "
                        "Evidence collection may need improvement.",
                        cc_count,
                    )
                    pc = node_index["analyze_dimensions"]
                continue
            # P1-Fix: When coverage is sufficient, route_after_coverage_critic
            # returns "analyze_dimensions". We MUST jump the pc there explicitly;
            # otherwise pc+=1 falls through to execute_rework (next in
            # node_sequence) which re-runs without a rework request and
            # contaminates the workflow trace.
            pc = node_index["analyze_dimensions"]
            continue

        elif node_name == "review_claims":
            next_node = route_after_review_claims(state)
            if next_node == "reflect_on_review":
                pc = node_index["reflect_on_review"]
                continue
            # no-rework → fall through to prepare_human_intervention

        elif node_name == "reflect_on_review":
            next_node = route_after_reflect(state)
            if next_node == "execute_rework":
                # P1-Redesign (2026-06-18): Real feedback loop. When the claims
                # reviewer requests rework, prefer a TRUE re-collect round
                # (jump back to collect_sources) over the legacy LLM-only
                # re-evaluation path. This actually fetches more evidence
                # for the missing dimensions instead of merely re-scoring
                # the existing evidence.
                rc = state.get("_rework_collect_count", 0)
                if rc < MAX_REWORK_COLLECT_ITERATIONS:
                    state["_rework_collect_count"] = rc + 1
                    logger.info(
                        "run_workflow (fallback): rework → true re-collect "
                        "(iteration %d/%d) → collect_sources",
                        rc + 1, MAX_REWORK_COLLECT_ITERATIONS,
                    )
                    # Jump to collect_sources to actually fetch new evidence
                    # for the dimensions the reviewer flagged.
                    pc = node_index["collect_sources"]
                else:
                    logger.warning(
                        "run_workflow (fallback): rework re-collect cap reached "
                        "(%d/%d), falling back to evaluate_evidence re-score",
                        rc, MAX_REWORK_COLLECT_ITERATIONS,
                    )
                    # Legacy path: re-evaluate existing evidence only
                    pc = node_index["evaluate_evidence"]
                state["_claims_rework_count"] = state.get("_claims_rework_count", 0) + 1
                continue
            # PASS → write_report_v2 directly (skip the soft prepare_human_intervention node)
            # exhausted → prepare_human_intervention (then write_report_v2)
            if next_node == "write_report_v2":
                logger.info("run_workflow (fallback): reflect_on_review PASS → write_report_v2")
                pc = node_index["write_report_v2"]
            else:
                pc = node_index["prepare_human_intervention"]
            continue

        elif node_name == "prepare_human_intervention":
            # P2 FIX: Check for auto-replay signal
            if state.get("workflow_auto_replay"):
                logger.info("Fallback runner: workflow_auto_replay detected, triggering replay")
                state.pop("workflow_auto_replay", None)
                try:
                    from backend.app.api.runs import replay_run
                    replay_run(state["run_id"])
                except Exception as exc:
                    logger.warning("Failed to trigger replay: %s", exc)
            # After prepare_human_intervention, proceed directly to write_report_v2
            pc = node_index["write_report_v2"]
            continue

        elif node_name == "final_review":
            next_node = route_after_final_review(state)
            if next_node == "write_report_v2":
                pc = node_index["write_report_v2"]
                continue
            # pass/block → fall through to export_report

        pc += 1

    return state
