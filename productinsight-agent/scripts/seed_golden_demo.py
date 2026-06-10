"""
Seed Golden Demo — creates two stable runs for demonstration.

A. run_golden_gap
   - completed, report_status = reviewed_with_gaps
   - Dify/Flowise/LangGraph: sufficient (evidence, facts, signed claims)
   - Coze: insufficient (0 evidence, 0 facts, 0 signed claims)
   - One planned coverage gap rework task

B. run_golden_completed
   - completed, report_status = reviewed
   - Coze after rework: 5 evidence, 5 facts, 3 signed claims
   - One completed coverage gap rework task with after_json deltas

Usage:  PYTHONPATH=. python scripts/seed_golden_demo.py
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.storage.db import get_connection, init_db
from backend.app.storage.repositories import (
    ClaimRepository,
    EvidenceRepository,
    EvalRepository,
    ProductRepository,
    ProjectRepository,
    ReportRepository,
    ReworkTaskRepository,
    RunRepository,
    SourceRepository,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(v):
    return json.dumps(v, ensure_ascii=False)


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace("_", "-")


def _offset_seconds(iso: str, seconds: int) -> str:
    """Return an ISO timestamp offset by `seconds` from the given ISO string."""
    from datetime import timedelta
    dt = datetime.fromisoformat(iso)
    return (dt + timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

def _cleanup_golden_runs() -> None:
    golden_ids = ["run_golden_gap", "run_golden_completed"]
    golden_proj_ids = ["proj_golden_demo"]
    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF;")
        for table in [
            "trace_logs", "eval_logs", "workflow_nodes",
            "report_spans", "reports",
            "reviews", "claims",
            "facts", "evidence_items",
            "snapshots", "sources",
            "rework_tasks", "human_interventions",
            "products", "runs",
            "project_products", "projects",
        ]:
            try:
                if table == "projects":
                    placeholders = ",".join("?" * len(golden_proj_ids))
                    conn.execute(f"DELETE FROM {table} WHERE project_id IN ({placeholders})", golden_proj_ids)
                else:
                    placeholders = ",".join("?" * len(golden_ids))
                    conn.execute(f"DELETE FROM {table} WHERE run_id IN ({placeholders})", golden_ids)
            except sqlite3.OperationalError:
                pass
        conn.commit()


def _insert_product(run_id: str, raw_pid: str, pname: str, company: str, website: str, now: str) -> str:
    """
    Insert a product via ProductRepository (handles run-scoped ID).
    Returns the actual run-scoped product_id stored in DB.
    """
    ProductRepository().add_product({
        "run_id": run_id,
        "product_id": raw_pid,
        "product_name": pname,
        "company_name": company,
        "official_website": website,
        "region": "global",
        "product_type": "ai_agent_platform",
        "seed_urls": [],
        "created_at": now,
        "updated_at": now,
    })
    # Query back the actual run-scoped product_id
    slug = _slugify(pname)
    scoped_id = f"{run_id}_{slug}"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT product_id FROM products WHERE run_id=? AND product_slug=?",
            (run_id, slug),
        ).fetchone()
        if row:
            return row[0]
    return scoped_id


def _insert_fact(conn, fact_id: str, run_id: str, scoped_pid: str, schema_key: str,
                  value: dict, ev_ids: list, now: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO facts (
            fact_id, run_id, product_id, schema_key, value_json, value_type,
            unit, confidence, evidence_ids_json, extraction_result_id,
            review_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fact_id, run_id, scoped_pid, schema_key,
            _json(value), "structured",
            None, 0.85,
            _json(ev_ids), f"ext_{fact_id}",
            "pending", now, now,
        ),
    )


def _insert_source(run_id: str, scoped_pid: str, source_id: str, title: str,
                   url: str, source_type: str, domain: str, trust_tier: str,
                   now: str, snapshot: bool = False) -> str:
    SourceRepository().add_source({
        "source_id": source_id,
        "run_id": run_id,
        "product_id": scoped_pid,
        "source_type": source_type,
        "title": title,
        "url": url,
        "domain": domain,
        "collection_method": "manual",
        "robots_status": "allowed",
        "terms_note": "",
        "trust_tier": trust_tier,
        "fetched_at": now,
        "content_hash": f"hash_{source_id}",
        "status": "collected",
        "created_at": now,
        "updated_at": now,
    })
    if snapshot:
        snapshot_id = f"snap_{source_id}"
        EvidenceRepository().add_snapshot({
            "snapshot_id": snapshot_id,
            "source_id": source_id,
            "run_id": run_id,
            "raw_text_path": f"data/runs/{run_id}/snapshots/{snapshot_id}/raw.txt",
            "content_hash": f"hash_{source_id}",
            "metadata": {"demo": True},
            "token_count": random.randint(100, 500),
            "created_at": now,
        })
    return source_id


def _seed_workflow_nodes(run_id: str, started_at: str, completed_at: str) -> None:
    """Insert completed workflow_nodes for a run into the database.

    Uses direct INSERT so we control exact started_at/completed_at/latency_ms values.
    All nodes are status=completed to show a perfect DAG execution.
    """
    nodes = [
        ("build_task_brief",       1200),
        ("plan_schema",            800),
        ("plan_sources",           600),
        ("collect_sources",        15000),
        ("evaluate_evidence",      3000),
        ("pii_scrub",              1000),
        ("extract_facts",          4000),
        ("detect_schema_gaps",     1500),
        ("analyze_dimensions",     3500),
        ("review_claims",          2000),
        ("execute_rework",         18000),
        ("prepare_human_intervention", 500),
        ("write_report",           5000),
        ("final_review",           1500),
        ("export_report",          3000),
        ("compute_metrics",        1000),
    ]

    with get_connection() as conn:
        for node_name, latency_ms in nodes:
            node_id = f"{run_id}_{node_name}"
            conn.execute(
                """
                INSERT OR IGNORE INTO workflow_nodes (
                    node_id, run_id, node_name, node_type, status,
                    started_at, completed_at, latency_ms,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id, run_id, node_name, "backbone", "completed",
                    started_at, completed_at, latency_ms,
                    started_at, completed_at,
                ),
            )
        conn.commit()


def _seed_eval_log(run_id: str, created_at: str) -> None:
    """Insert a complete eval_logs record for a run's quality metrics."""
    EvalRepository().add_eval_log({
        "eval_id": f"eval_{run_id}",
        "run_id": run_id,
        "schema_completion_rate": 1.0,
        "evidence_coverage_rate": 1.0,
        "unsupported_claim_rate": 0.0,
        "review_pass_rate": 1.0,
        "rework_success_rate": 1.0,
        "replay_success_rate": 1.0,
        "manual_correction_rate": None,
        "source_coverage_count": 3,
        "conflict_count": 0,
        "analysis_time_minutes": 3.2,
        "metrics_json": _json({
            "schema_keys_covered": 5,
            "total_sources": 9,
            "total_evidence": 11,
            "total_claims": 6,
            "signed_claims": 6,
            "rework_tasks_completed": 1,
            "products_sufficient": 4,
        }),
        "created_at": created_at,
    })


# ---------------------------------------------------------------------------
# seed A: run_golden_gap
# ---------------------------------------------------------------------------

def seed_gap_run(now: str) -> None:
    run_id = "run_golden_gap"
    random.seed(42)

    task_brief = {
        "title": "AI Agent Platform Competitive Analysis",
        "description": "Compare Dify, Flowise, LangGraph, Coze across function_tree, pricing_model, user_persona, swot, enterprise_readiness.",
        "target_region": "global",
        "products": [
            {"product_id": "dify", "product_name": "Dify", "seed_urls": []},
            {"product_id": "flowise", "product_name": "Flowise", "seed_urls": []},
            {"product_id": "langgraph", "product_name": "LangGraph", "seed_urls": []},
            {"product_id": "coze", "product_name": "Coze", "seed_urls": []},
        ],
        "analysis_dimensions": ["function_tree", "pricing_model", "user_persona", "swot", "enterprise_readiness"],
    }

    RunRepository().create_run({
        "run_id": run_id,
        "task_id": "task_golden_gap",
        "task_title": "AI Agent Platform Competitive Analysis",
        "task_brief": task_brief,
        "mode": "replay",
        "status": "completed",
        "current_node": "compute_metrics",
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
    })

    # Products — collect scoped IDs
    dify_pid = _insert_product(run_id, "dify", "Dify", "Dify Technology", "https://dify.ai", now)
    flowise_pid = _insert_product(run_id, "flowise", "Flowise", "Flowise Inc.", "https://flowiseai.com", now)
    langgraph_pid = _insert_product(run_id, "langgraph", "LangGraph", "LangChain", "https://langchain.com/langgraph", now)
    coze_pid = _insert_product(run_id, "coze", "Coze", "ByteDance", "https://www.coze.cn", now)

    # Sources + Snapshots for sufficient products
    dify_src0 = _insert_source(run_id, dify_pid, "src_gap_dify_0", "Dify Official", "https://dify.ai", "official_site", "dify.ai", "official", now, True)
    dify_src1 = _insert_source(run_id, dify_pid, "src_gap_dify_1", "Dify Docs", "https://docs.dify.ai", "documentation", "docs.dify.ai", "official", now, True)
    dify_src2 = _insert_source(run_id, dify_pid, "src_gap_dify_2", "Dify Pricing", "https://dify.ai/pricing", "pricing_page", "dify.ai", "official", now, True)

    flowise_src0 = _insert_source(run_id, flowise_pid, "src_gap_flowise_0", "Flowise Official", "https://flowiseai.com", "official_site", "flowiseai.com", "official", now, True)
    flowise_src1 = _insert_source(run_id, flowise_pid, "src_gap_flowise_1", "Flowise GitHub", "https://github.com/FlowiseAI/Flowise", "community_review", "github.com", "community", now, True)

    langgraph_src0 = _insert_source(run_id, langgraph_pid, "src_gap_langgraph_0", "LangGraph Official", "https://langchain.com/langgraph", "official_site", "langchain.com", "official", now, True)
    langgraph_src1 = _insert_source(run_id, langgraph_pid, "src_gap_langgraph_1", "LangGraph Docs", "https://langchain.github.io/langgraph/", "documentation", "langchain.github.io", "official", now, True)

    # Evidence + Facts + Claims for sufficient products (each op is self-contained)
    for pid, src0, ev_defs in [
        (dify_pid, dify_src0, [
            ("ev_gap_dify_0", "function_tree", "Dify provides a visual workflow builder for AI application development.", 0.92),
            ("ev_gap_dify_1", "pricing_model", "Dify offers SaaS and self-hosted deployment options with subscription tiers.", 0.88),
            ("ev_gap_dify_2", "enterprise_readiness", "Dify supports SSO, RBAC, and audit logs for enterprise use.", 0.90),
        ]),
        (flowise_pid, flowise_src0, [
            ("ev_gap_flowise_0", "function_tree", "Flowise is an open-source drag-and-drop LLM flow builder.", 0.93),
            ("ev_gap_flowise_1", "function_tree", "Flowise integrates with LangChain for flexible LLM routing.", 0.89),
        ]),
        (langgraph_pid, langgraph_src0, [
            ("ev_gap_langgraph_0", "function_tree", "LangGraph enables stateful multi-agent orchestration with checkpointing.", 0.91),
            ("ev_gap_langgraph_1", "enterprise_readiness", "LangGraph provides fault tolerance and human-in-the-loop checkpoints.", 0.87),
        ]),
    ]:
        eids = []
        for eid, schema, snippet, conf in ev_defs:
            EvidenceRepository().add_evidence({
                "run_id": run_id,
                "product_id": pid,
                "source_id": src0,
                "snapshot_id": f"snap_{src0}",
                "evidence_id": eid,
                "schema_key": schema,
                "snippet": snippet,
                "confidence": conf,
                "extraction_result_id": f"ext_{eid}",
                "created_at": now,
                "updated_at": now,
            })
            eids.append(eid)

        with get_connection() as conn:
            for i in range(2):
                fid = f"fact_gap_{pid[-20:]}_{i}"
                _insert_fact(conn, fid, run_id, pid, ev_defs[0][1],
                             {"name": f"Feature {i}", "description": ev_defs[0][2][:80]},
                             eids[:2], now)
            conn.commit()

        ClaimRepository().add_claim({
            "run_id": run_id,
            "product_id": pid,
            "claim_id": f"claim_gap_{pid[-20:]}_0",
            "dimension": ev_defs[0][1],
            "claim_text": f"Product provides AI agent platform capabilities across {ev_defs[0][1]}.",
            "claim_type": "structured",
            "fact_ids": [f"fact_gap_{pid[-20:]}_0", f"fact_gap_{pid[-20:]}_1"],
            "evidence_ids": eids,
            "risk_level": "low",
            "support_level": "supported",
            "confidence": 0.85,
            "review_status": "signed",
            "signed_by": "GoldenDemoSeed",
            "signed_at": now,
            "created_by_agent": "GoldenDemoSeed",
            "created_at": now,
            "updated_at": now,
        })

    # Report
    product_coverage = {
        "dify": {"product_slug": "dify", "product_name": "Dify", "sources": 3,
                 "evidence": 3, "evidence_ids": ["ev_gap_dify_0", "ev_gap_dify_1", "ev_gap_dify_2"],
                 "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "flowise": {"product_slug": "flowise", "product_name": "Flowise", "sources": 2,
                    "evidence": 2, "evidence_ids": ["ev_gap_flowise_0", "ev_gap_flowise_1"],
                    "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "langgraph": {"product_slug": "langgraph", "product_name": "LangGraph", "sources": 2,
                      "evidence": 2, "evidence_ids": ["ev_gap_langgraph_0", "ev_gap_langgraph_1"],
                      "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "coze": {"product_slug": "coze", "product_name": "Coze", "sources": 0,
                 "evidence": 0, "evidence_ids": [],
                 "facts": 0, "signed_claims": 0, "coverage_status": "insufficient",
                 "missing_dimensions": ["function_tree", "pricing_model", "enterprise_readiness", "user_persona", "swot"]},
    }
    ReportRepository().add_report({
        "run_id": run_id,
        "report_id": f"report_{run_id}",
        "title": "AI Agent Platform Competitive Analysis",
        "report_status": "reviewed_with_gaps",
        "quality_summary": {
            "claim_count": 3, "signed_claims": 3,
            "evidence_coverage_rate": 0.75, "unsupported_claim_count": 0,
            "section_count": 5, "evidence_count": 7,
            "total_products": 4, "sufficient_products": 3,
            "partial_products": 0, "insufficient_products": 1,
            "product_coverage_summary": product_coverage,
        },
        "created_by_agent": "GoldenDemoSeed",
        "created_at": now,
        "updated_at": now,
    })

    # Planned coverage gap rework task
    ReworkTaskRepository().create_rework_task({
        "run_id": run_id,
        "rework_id": "rework_cov_coze_gap",
        "status": "planned",
        "product_id": coze_pid,
        "product_name": "Coze",
        "target_node": "collect_sources",
        "required_action": "补充 Coze 产品的证据收集",
        "reason_codes": ["INSUFFICIENT_PRODUCT_COVERAGE"],
        "rework_plan_json": {
            "steps": [
                {"node": "collect_sources", "instruction": "访问 coze.cn 收集产品信息"},
                {"node": "extract_evidence", "instruction": "抽取 Coze 产品特性证据"},
                {"node": "extract_facts", "instruction": "生成结构化事实"},
                {"node": "generate_claims", "instruction": "生成 claim"},
                {"node": "review_claims", "instruction": "签发 signed claim"},
            ]
        },
        "before_json": {"executed": False, "sources": 0, "evidence": 0, "facts": 0, "signed_claims": 0},
        "seed_urls": [],
        "created_by": "GoldenDemoSeed",
        "created_at": now,
        "updated_at": now,
    })


# ---------------------------------------------------------------------------
# seed B: run_golden_completed
# ---------------------------------------------------------------------------

def seed_completed_run(now: str) -> None:
    run_id = "run_golden_completed"
    random.seed(99)

    task_brief = {
        "title": "AI Agent Platform Competitive Analysis (Reworked)",
        "description": "Compare Dify, Flowise, LangGraph, Coze — Coze has been reworked.",
        "target_region": "global",
        "products": [
            {"product_id": "dify", "product_name": "Dify", "seed_urls": []},
            {"product_id": "flowise", "product_name": "Flowise", "seed_urls": []},
            {"product_id": "langgraph", "product_name": "LangGraph", "seed_urls": []},
            {"product_id": "coze", "product_name": "Coze", "seed_urls": []},
        ],
        "analysis_dimensions": ["function_tree", "pricing_model", "user_persona", "swot", "enterprise_readiness"],
    }

    # Use a timestamp 2 seconds after run_golden_gap so this run sorts first
    # (latest_run = ORDER BY created_at DESC LIMIT 1)
    completed_now = _offset_seconds(now, 2)
    started_at = _offset_seconds(now, 1)

    RunRepository().create_run({
        "run_id": run_id,
        "task_id": "task_golden_completed",
        "task_title": "AI Agent Platform Competitive Analysis (Reworked)",
        "task_brief": task_brief,
        "mode": "replay",
        "status": "completed",
        "current_node": "compute_metrics",
        "created_at": completed_now,
        "started_at": started_at,
        "completed_at": completed_now,
        "updated_at": completed_now,
    })

    dify_pid = _insert_product(run_id, "dify", "Dify", "Dify Technology", "https://dify.ai", now)
    flowise_pid = _insert_product(run_id, "flowise", "Flowise", "Flowise Inc.", "https://flowiseai.com", now)
    langgraph_pid = _insert_product(run_id, "langgraph", "LangGraph", "LangChain", "https://langchain.com/langgraph", now)
    coze_pid = _insert_product(run_id, "coze", "Coze", "ByteDance", "https://www.coze.cn", now)

    # Sources for sufficient products
    for pid, prefix in [(dify_pid, "dify"), (flowise_pid, "flowise"), (langgraph_pid, "langgraph")]:
        for i in range(3 if prefix == "dify" else 2):
            _insert_source(run_id, pid, f"src_comp_{prefix}_{i}",
                           f"{prefix.title()} Feature {i}",
                           f"https://{prefix}.ai/feature_{i}",
                           "official_site", f"{prefix}.ai", "official", now, True)

    # Evidence + Facts + Claims for sufficient products
    for pid, prefix in [(dify_pid, "dify"), (flowise_pid, "flowise"), (langgraph_pid, "langgraph")]:
        eids = []
        for i in range(2):
            eid = f"ev_comp_{prefix}_{i}"
            EvidenceRepository().add_evidence({
                "run_id": run_id, "product_id": pid,
                "source_id": f"src_comp_{prefix}_0",
                "snapshot_id": f"snap_src_comp_{prefix}_0",
                "evidence_id": eid, "schema_key": "function_tree",
                "snippet": f"{prefix.title()} provides AI agent platform capability {i}.",
                "confidence": 0.9, "extraction_result_id": f"ext_{eid}",
                "created_at": now, "updated_at": now,
            })
            eids.append(eid)

        fids = []
        with get_connection() as conn:
            for i in range(2):
                fid = f"fact_comp_{prefix}_{i}"
                _insert_fact(conn, fid, run_id, pid, "function_tree",
                             {"name": f"{prefix.title()} Capability {i}"},
                             eids[:2], now)
                fids.append(fid)
            conn.commit()

        ClaimRepository().add_claim({
            "run_id": run_id, "product_id": pid,
            "claim_id": f"claim_comp_{prefix}_0",
            "dimension": "function_tree",
            "claim_text": f"{prefix.title()} provides AI agent platform capabilities.",
            "claim_type": "structured",
            "fact_ids": fids[:2],
            "evidence_ids": eids[:2],
            "risk_level": "low",
            "support_level": "supported", "confidence": 0.85,
            "review_status": "signed", "signed_by": "GoldenDemoSeed", "signed_at": now,
            "created_by_agent": "GoldenDemoSeed",
            "created_at": now, "updated_at": now,
        })

    # Coze sources (added during rework)
    for i in range(2):
        _insert_source(run_id, coze_pid, f"src_comp_coze_{i}",
                       f"Coze Official Page {i}",
                       f"https://www.coze.cn/page_{i}",
                       "official_site", "coze.cn", "official", now, True)

    # Coze evidence AFTER rework
    coze_ev_defs = [
        ("ev_comp_coze_0", "function_tree", "Coze provides a bot creation platform with visual workflow builder.", 0.9),
        ("ev_comp_coze_1", "function_tree", "Coze supports multi-channel deployment including Discord, Slack, and WeChat.", 0.9),
        ("ev_comp_coze_2", "enterprise_readiness", "Coze Enterprise offers SSO, team collaboration, and admin controls.", 0.9),
        ("ev_comp_coze_3", "pricing_model", "Coze provides free tier with limited bots, paid plans for team and enterprise.", 0.9),
        ("ev_comp_coze_4", "function_tree", "Coze supports AI agents with memory, knowledge base, and skills marketplace.", 0.9),
    ]
    coze_eids = []
    for eid, schema, snippet, conf in coze_ev_defs:
        EvidenceRepository().add_evidence({
            "run_id": run_id, "product_id": coze_pid,
            "source_id": "src_comp_coze_0",
            "snapshot_id": "snap_src_comp_coze_0",
            "evidence_id": eid, "schema_key": schema,
            "snippet": snippet, "confidence": conf,
            "extraction_result_id": f"ext_{eid}",
            "created_at": now, "updated_at": now,
        })
        coze_eids.append(eid)

    # Coze facts
    coze_fids = []
    with get_connection() as conn:
        for i in range(5):
            fid = f"fact_comp_coze_{i}"
            _insert_fact(conn, fid, run_id, coze_pid, coze_ev_defs[i][1],
                         {"name": f"Coze Feature {i}", "description": coze_ev_defs[i][2][:60]},
                         coze_eids[:2], now)
            coze_fids.append(fid)
        conn.commit()

    # Coze signed claims (3)
    for i in range(3):
        ClaimRepository().add_claim({
            "run_id": run_id, "product_id": coze_pid,
            "claim_id": f"claim_comp_coze_{i}",
            "dimension": coze_ev_defs[i][1],
            "claim_text": f"Coze enables enterprise bot creation and multi-channel deployment for team collaboration (claim {i}).",
            "claim_type": "structured",
            "fact_ids": coze_fids[:3],
            "evidence_ids": coze_eids[:3],
            "risk_level": "low",
            "support_level": "supported", "confidence": 0.85,
            "review_status": "signed", "signed_by": "GoldenDemoSeed", "signed_at": now,
            "created_by_agent": "GoldenDemoSeed",
            "created_at": now, "updated_at": now,
        })

    # Report: reviewed, Coze sufficient
    product_coverage = {
        "dify": {"product_slug": "dify", "product_name": "Dify", "sources": 3,
                 "evidence": 2, "evidence_ids": ["ev_comp_dify_0", "ev_comp_dify_1"],
                 "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "flowise": {"product_slug": "flowise", "product_name": "Flowise", "sources": 2,
                    "evidence": 2, "evidence_ids": ["ev_comp_flowise_0", "ev_comp_flowise_1"],
                    "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "langgraph": {"product_slug": "langgraph", "product_name": "LangGraph", "sources": 2,
                      "evidence": 2, "evidence_ids": ["ev_comp_langgraph_0", "ev_comp_langgraph_1"],
                      "facts": 2, "signed_claims": 1, "coverage_status": "sufficient", "missing_dimensions": []},
        "coze": {"product_slug": "coze", "product_name": "Coze", "sources": 2,
                 "evidence": 5, "evidence_ids": coze_eids,
                 "facts": 5, "signed_claims": 3, "coverage_status": "sufficient", "missing_dimensions": []},
    }
    ReportRepository().add_report({
        "run_id": run_id,
        "report_id": f"report_{run_id}",
        "title": "AI Agent Platform Competitive Analysis (Reworked)",
        "report_status": "reviewed",
        "quality_summary": {
            "claim_count": 6, "signed_claims": 6,
            "evidence_coverage_rate": 1.0, "unsupported_claim_count": 0,
            "section_count": 5, "evidence_count": 11,
            "total_products": 4, "sufficient_products": 4,
            "partial_products": 0, "insufficient_products": 0,
            "product_coverage_summary": product_coverage,
        },
        "created_by_agent": "GoldenDemoSeed",
        "created_at": now,
        "updated_at": now,
    })

    # Report spans (sections) for run_golden_completed
    spans_defs = [
        ("span_exec_summary", "section_01", "执行摘要",
         "本报告对比分析了 Dify、Flowise、LangGraph、Coze 四个主流 AI Agent 平台。报告基于官方网站、文档、GitHub 等来源的证据生成，所有结论均经过 Reviewer Agent 签发 Signed Claims 后输出，可追溯、有支撑、杜绝幻觉。"),
        ("span_overview", "section_02", "产品概览",
         "Dify 定位为开源 AI Agent 应用平台，支持可视化编排和私有化部署；Flowise 是基于 LangChain 的开源拖拽式 LLM 流程构建工具；LangGraph 提供有状态的多 Agent 编排能力，支持容错和人工介入；Coze 是字节跳动推出的无代码 Bot 创建平台，支持多渠道发布。"),
        ("span_function", "section_03", "功能对比",
         "在功能维度，Dify 和 Flowise 均提供可视化流程编排，但 Dify 更强调企业级特性和多模型支持，Flowise 则以 LangChain 原生集成见长。LangGraph 通过代码方式提供最灵活的多 Agent 状态管理能力，适合复杂的企业级场景。Coze 提供无代码 Bot 创建和多渠道发布，但在私有化部署方面能力有限。"),
        ("span_pricing", "section_04", "定价分析",
         "Dify 提供社区版免费使用，专业版和企业版按订阅收费，支持自托管满足合规需求。Flowise 完全开源免费，可自行部署，托管服务另有收费计划。LangGraph 开源免费，商业支持通过 LangChain Enterprise 提供。Coze 提供免费基础版，团队版和企业版按席位收费。"),
        ("span_swot", "section_05", "SWOT 分析",
         "Dify 优势在于开源灵活性和企业级特性，劣势为初始配置复杂度较高；Flowise 优势为上手简单和 LangChain 生态，劣势为功能深度有限；LangGraph 优势为最灵活的编排能力，劣势为需要代码能力；Coze 优势为无代码体验和多渠道发布，劣势为私有化受限。"),
    ]
    for span_id, sec_id, title, text in spans_defs:
        ReportRepository().add_report_span({
            "span_id": span_id,
            "report_id": f"report_{run_id}",
            "run_id": run_id,
            "section_id": sec_id,
            "section_title": title,
            "span_type": "paragraph",
            "text": text,
            "claim_ids": [],
            "evidence_ids": [],
            "unsupported_flag": False,
            "created_at": now,
        })

    # Completed coverage gap rework task
    ReworkTaskRepository().create_rework_task({
        "run_id": run_id,
        "rework_id": "rework_cov_coze_completed",
        "status": "completed",
        "product_id": coze_pid,
        "product_name": "Coze",
        "target_node": "collect_sources",
        "required_action": "补充 Coze 产品证据",
        "reason_codes": ["INSUFFICIENT_PRODUCT_COVERAGE"],
        "rework_plan_json": {
            "steps": [
                {"node": "collect_sources", "instruction": "访问 coze.cn 收集产品信息"},
                {"node": "extract_evidence", "instruction": "抽取 Coze 功能证据"},
                {"node": "extract_facts", "instruction": "生成结构化事实"},
                {"node": "generate_claims", "instruction": "生成 claim"},
                {"node": "review_claims", "instruction": "签发 signed claim"},
            ]
        },
        "before_json": {
            "executed": False,
            "execution_summary": {"sources_added": 0, "evidence_added": 0,
                                 "facts_added": 0, "claims_added": 0, "signed_claims_added": 0},
        },
        "after_json": {
            "executed": True, "executed_at": now,
            "execution_summary": {
                "sources_added": 2, "evidence_added": 5,
                "facts_added": 5, "claims_added": 3, "signed_claims_added": 3,
            },
        },
        "seed_urls": ["https://www.coze.cn"],
        "metrics_before": {"sources": 0, "evidence": 0, "facts": 0, "signed_claims": 0},
        "metrics_after": {"sources": 2, "evidence": 5, "facts": 5, "signed_claims": 3},
        "completed_at": completed_now,
        "created_by": "GoldenDemoSeed",
        "created_at": completed_now,
        "updated_at": completed_now,
    })

    # Workflow nodes — 16 backbone nodes, all completed
    _seed_workflow_nodes(run_id, started_at, completed_now)

    # Quality metrics via eval_logs
    _seed_eval_log(run_id, completed_now)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()

    print("Cleaning existing golden demo runs and project...")
    _cleanup_golden_runs()

    now = utc_now()
    print("Seeding run_golden_gap...")
    seed_gap_run(now)
    print("Seeding run_golden_completed...")
    seed_completed_run(now)

    # Create golden demo project and link both runs
    proj_id = "proj_golden_demo"
    print(f"Creating golden demo project {proj_id}...")
    ProjectRepository().create_project({
        "project_id": proj_id,
        "project_name": "AI Agent Platform 竞品分析（Golden Demo）",
        "task_type": "competitor_landscape",
        "target_region": "global",
        "description": "Golden Demo — Dify/Flowise/LangGraph/Coze 竞品分析",
        "analysis_dimensions": ["function_tree", "pricing_model", "user_persona", "swot", "enterprise_readiness"],
        "status": "active",
        "created_at": now,
        "updated_at": now,
    })
    # Add products to project
    for raw_pid, pname, company, website in [
        ("dify", "Dify", "Dify Technology", "https://dify.ai"),
        ("flowise", "Flowise", "Flowise Inc.", "https://flowiseai.com"),
        ("langgraph", "LangGraph", "LangChain", "https://langchain.com/langgraph"),
        ("coze", "Coze", "ByteDance", "https://www.coze.cn"),
    ]:
        slug = _slugify(pname)
        scoped_id = f"run_golden_completed_{slug}"
        ProjectRepository().add_project_product({
            "project_product_id": f"{proj_id}_{raw_pid}",
            "project_id": proj_id,
            "product_slug": raw_pid,
            "product_name": pname,
            "company_name": company,
            "official_website": website,
            "seed_urls": [website],
            "product_type": "ai_agent_platform",
            "region": "global",
            "created_at": now,
            "updated_at": now,
        })

    # Link runs to project
    with get_connection() as conn:
        conn.execute("UPDATE runs SET project_id=? WHERE run_id IN (?, ?)",
                     (proj_id, "run_golden_gap", "run_golden_completed"))
        conn.commit()

    print()
    print("Golden demo seeded.")
    print(f"  Project:  {proj_id}")
    print(f"  Gap run:        run_golden_gap")
    print(f"  Completed run:  run_golden_completed")
    print()

    # Generate HTML report for run_golden_completed
    print("Generating HTML report for run_golden_completed...")
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "scripts/generate_html_report.py", "--run-id", "run_golden_completed"],
            capture_output=True, text=True, cwd=Path(__file__).parent.parent,
            env={**__import__("os").environ, "PYTHONPATH": "."},
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f"⚠️  HTML generation failed: {result.stderr.strip()}")
    except Exception as exc:
        print(f"⚠️  HTML generation skipped: {exc}")

    print()
    print("Open frontend, go to Projects → 'AI Agent Platform 竞品分析（Golden Demo）' → Deliverables tab.")
    print("Or click Load Golden Demo to jump in.")


if __name__ == "__main__":
    main()
