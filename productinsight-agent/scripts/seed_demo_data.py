from __future__ import annotations

import json
import os
import random
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.storage.db import get_connection, init_db
from backend.app.storage.repositories import (
    ClaimRepository,
    EvidenceRepository,
    EvalRepository,
    ProductRepository,
    ReportRepository,
    ReviewRepository,
    RunRepository,
    SourceRepository,
    TraceRepository,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_fact(fact: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO facts (
                fact_id, run_id, product_id, schema_key, value_json, value_type,
                unit, confidence, evidence_ids_json, extraction_result_id,
                review_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fact["fact_id"],
                fact["run_id"],
                fact["product_id"],
                fact["schema_key"],
                fact["value_json"],
                fact["value_type"],
                fact.get("unit"),
                fact["confidence"],
                fact["evidence_ids_json"],
                fact.get("extraction_result_id"),
                fact["review_status"],
                fact["created_at"],
                fact["updated_at"],
            ),
        )
        conn.commit()


def main() -> None:
    db_path = Path("data/productinsight.db")
    if db_path.exists():
        db_path.unlink()
    init_db()

    now = utc_now()
    run_id = "run_demo_ai_agent_001"

    # -------------------------------------------------------------------------
    # 1. Run record
    # -------------------------------------------------------------------------
    task_brief = {
        "title": "AI Agent Product Competitive Analysis: Dify vs Coze vs FastGPT vs Flowise",
        "description": "Analyze four leading AI Agent / enterprise AI platforms across function_tree, pricing_model, user_persona, customer_voice, swot, enterprise_readiness.",
        "target_region": "global",
        "products": [
            {"product_id": "dify", "product_name": "Dify", "seed_urls": ["https://dify.ai", "https://docs.dify.ai"]},
            {"product_id": "coze", "product_name": "Coze", "seed_urls": ["https://www.coze.com"]},
            {"product_id": "fastgpt", "product_name": "FastGPT", "seed_urls": ["https://fastgpt.cn"]},
            {"product_id": "flowise", "product_name": "Flowise", "seed_urls": ["https://flowiseai.com"]},
        ],
        "analysis_dimensions": ["function_tree", "pricing_model", "user_persona", "customer_voice", "swot", "enterprise_readiness"],
    }

    RunRepository().create_run({
        "run_id": run_id,
        "task_id": "task_demo_ai_agent_001",
        "task_title": "AI Agent Product Competitive Analysis: Dify vs Coze vs FastGPT vs Flowise",
        "task_brief": task_brief,
        "mode": "replay",
        "status": "completed",
        "current_node": "compute_metrics",
        "created_at": now,
        "started_at": now,
        "completed_at": now,
        "updated_at": now,
    })

    # -------------------------------------------------------------------------
    # 2. Products
    # -------------------------------------------------------------------------
    product_definitions = [
        {
            "product_id": "dify",
            "product_name": "Dify",
            "company_name": "Dify Technology Co., Ltd.",
            "official_website": "https://dify.ai",
            "region": "global",
            "product_type": "ai_agent_platform",
            "seed_urls": ["https://dify.ai", "https://docs.dify.ai"],
        },
        {
            "product_id": "coze",
            "product_name": "Coze",
            "company_name": "ByteDance Ltd.",
            "official_website": "https://www.coze.com",
            "region": "global",
            "product_type": "ai_agent_platform",
            "seed_urls": ["https://www.coze.com"],
        },
        {
            "product_id": "fastgpt",
            "product_name": "FastGPT",
            "company_name": "FastGPT Team",
            "official_website": "https://fastgpt.cn",
            "region": "global",
            "product_type": "ai_agent_platform",
            "seed_urls": ["https://fastgpt.cn"],
        },
        {
            "product_id": "flowise",
            "product_name": "Flowise",
            "company_name": "Flowise, Inc.",
            "official_website": "https://flowiseai.com",
            "region": "global",
            "product_type": "ai_agent_platform",
            "seed_urls": ["https://flowiseai.com"],
        },
    ]

    for p in product_definitions:
        ProductRepository().add_product({
            "product_id": p["product_id"],
            "run_id": run_id,
            "product_name": p["product_name"],
            "company_name": p["company_name"],
            "official_website": p["official_website"],
            "region": p["region"],
            "product_type": p["product_type"],
            "seed_urls": p["seed_urls"],
            "created_at": now,
            "updated_at": now,
        })

    # -------------------------------------------------------------------------
    # 3. Sources (3 per product = 12 total) + Snapshots (1 per source = 12)
    # -------------------------------------------------------------------------
    source_definitions = [
        # Dify
        {
            "product_id": "dify",
            "source_id": "src_dify_official",
            "source_type": "official_site",
            "title": "Dify - AI Agent Application Development Platform",
            "url": "https://dify.ai",
            "domain": "dify.ai",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official product landing page.",
            "trust_tier": "official",
        },
        {
            "product_id": "dify",
            "source_id": "src_dify_docs",
            "source_type": "documentation",
            "title": "Dify Documentation - Getting Started",
            "url": "https://docs.dify.ai",
            "domain": "docs.dify.ai",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official documentation with API reference.",
            "trust_tier": "official",
        },
        {
            "product_id": "dify",
            "source_id": "src_dify_pricing",
            "source_type": "pricing_page",
            "title": "Dify Pricing - Subscription Plans",
            "url": "https://dify.ai/pricing",
            "domain": "dify.ai",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Public pricing page listing subscription tiers.",
            "trust_tier": "official",
        },
        # Coze
        {
            "product_id": "coze",
            "source_id": "src_coze_official",
            "source_type": "official_site",
            "title": "Coze - Create Bots Without Coding",
            "url": "https://www.coze.com",
            "domain": "coze.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official Coze product homepage.",
            "trust_tier": "official",
        },
        {
            "product_id": "coze",
            "source_id": "src_coze_docs",
            "source_type": "documentation",
            "title": "Coze Documentation - Platform Overview",
            "url": "https://www.coze.com/docs",
            "domain": "coze.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official documentation for bot creation workflow.",
            "trust_tier": "official",
        },
        {
            "product_id": "coze",
            "source_id": "src_coze_enterprise",
            "source_type": "community_review",
            "title": "Coze Enterprise Features Overview",
            "url": "https://www.coze.com/enterprise",
            "domain": "coze.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Enterprise offering and team collaboration features.",
            "trust_tier": "official",
        },
        # FastGPT
        {
            "product_id": "fastgpt",
            "source_id": "src_fastgpt_official",
            "source_type": "official_site",
            "title": "FastGPT - Open Source Knowledge Base QA Platform",
            "url": "https://fastgpt.cn",
            "domain": "fastgpt.cn",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official FastGPT homepage.",
            "trust_tier": "official",
        },
        {
            "product_id": "fastgpt",
            "source_id": "src_fastgpt_docs",
            "source_type": "documentation",
            "title": "FastGPT Documentation - Deployment Guide",
            "url": "https://doc.fastgpt.cn",
            "domain": "doc.fastgpt.cn",
            "collection_method": "manual",
            "robots_status": "unknown",
            "terms_note": "GitHub-hosted deployment and configuration docs.",
            "trust_tier": "official",
        },
        {
            "product_id": "fastgpt",
            "source_id": "src_fastgpt_github",
            "source_type": "community_review",
            "title": "FastGPT GitHub Repository - README",
            "url": "https://github.com/labring/fastgpt",
            "domain": "github.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Open-source repository with feature descriptions.",
            "trust_tier": "community",
        },
        # Flowise
        {
            "product_id": "flowise",
            "source_id": "src_flowise_official",
            "source_type": "official_site",
            "title": "Flowise - Drag & Drop UI for LLM Flows",
            "url": "https://flowiseai.com",
            "domain": "flowiseai.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Official Flowise product page.",
            "trust_tier": "official",
        },
        {
            "product_id": "flowise",
            "source_id": "src_flowise_docs",
            "source_type": "documentation",
            "title": "Flowise Documentation - Getting Started",
            "url": "https://docs.flowiseai.com",
            "domain": "docs.flowiseai.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Documentation covering installation and flow building.",
            "trust_tier": "official",
        },
        {
            "product_id": "flowise",
            "source_id": "src_flowise_github",
            "source_type": "community_review",
            "title": "Flowise GitHub - MIT Licensed LLM Flow Builder",
            "url": "https://github.com/FlowiseAI/Flowise",
            "domain": "github.com",
            "collection_method": "manual",
            "robots_status": "allowed",
            "terms_note": "Repository listing MIT license and feature set.",
            "trust_tier": "community",
        },
    ]

    token_counter = random.Random(42)
    for src in source_definitions:
        snapshot_id = f"snap_{src['product_id']}_{src['source_type']}"
        content_hash = f"hash_{src['product_id']}_{src['source_type']}"

        SourceRepository().add_source({
            "source_id": src["source_id"],
            "run_id": run_id,
            "product_id": src["product_id"],
            "source_type": src["source_type"],
            "title": src["title"],
            "url": src["url"],
            "domain": src["domain"],
            "collection_method": src["collection_method"],
            "robots_status": src["robots_status"],
            "terms_note": src["terms_note"],
            "trust_tier": src["trust_tier"],
            "fetched_at": now,
            "content_hash": content_hash,
            "status": "collected",
            "created_at": now,
            "updated_at": now,
        })

        EvidenceRepository().add_snapshot({
            "snapshot_id": snapshot_id,
            "source_id": src["source_id"],
            "run_id": run_id,
            "raw_text_path": f"data/runs/{run_id}/snapshots/{snapshot_id}/raw.txt",
            "content_hash": content_hash,
            "metadata": {"demo": True},
            "token_count": token_counter.randint(100, 500),
            "created_at": now,
        })

    # -------------------------------------------------------------------------
    # 4. Evidence items (vary schema_keys significantly)
    # -------------------------------------------------------------------------
    # Each tuple: (evidence_id, source_id, snapshot_id, product_id, schema_key, snippet, confidence)
    evidence_definitions = [
        # Dify evidence (6 items, 6 distinct schema_keys)
        (
            "ev_dify_product_name",
            "src_dify_official",
            "snap_dify_official_site",
            "dify",
            "product_profile.product_name",
            "Dify is an open-source AI Agent application development platform that enables teams to build and deploy LLM-powered applications.",
            0.92,
        ),
        (
            "ev_dify_company_name",
            "src_dify_official",
            "snap_dify_official_site",
            "dify",
            "product_profile.company_name",
            "Dify is developed and maintained by Dify Technology Co., Ltd., a company focused on developer tooling for AI applications.",
            0.90,
        ),
        (
            "ev_dify_deployment_self_hosted",
            "src_dify_docs",
            "snap_dify_documentation",
            "dify",
            "agent_product_capabilities.deployment_options.self_hosted",
            "Dify provides Docker-based self-hosted deployment options, allowing enterprise AI application teams to run the platform on their own infrastructure with full data control and privacy compliance.",
            0.88,
        ),
        (
            "ev_dify_workflow",
            "src_dify_docs",
            "snap_dify_documentation",
            "dify",
            "agent_product_capabilities.workflow_orchestration",
            "Dify supports visual workflow orchestration with conditional branching, loop nodes, HTTP request nodes, and code execution nodes, enabling complex AI application logic without writing backend code.",
            0.85,
        ),
        (
            "ev_dify_target_users",
            "src_dify_official",
            "snap_dify_official_site",
            "dify",
            "user_persona.target_users",
            "Dify targets AI application developers and enterprise AI teams that need self-hosted, privacy-compliant solutions for building and deploying LLM-based agents and workflows.",
            0.87,
        ),
        (
            "ev_dify_pricing_info",
            "src_dify_pricing",
            "snap_dify_pricing_page",
            "dify",
            "pricing_model.has_free_plan",
            "Dify offers a free community tier with limited usage, as well as paid subscription plans targeting professional teams and enterprise deployments.",
            0.84,
        ),
        # Coze evidence (5 items)
        (
            "ev_coze_nocode",
            "src_coze_docs",
            "snap_coze_documentation",
            "coze",
            "agent_product_capabilities.agent_builder_mode",
            "Coze provides a no-code bot building platform with a visual editor that allows non-technical users to create AI bots by dragging and dropping components, configuring prompts, and connecting knowledge bases.",
            0.82,
        ),
        (
            "ev_coze_enterprise",
            "src_coze_enterprise",
            "snap_coze_community_review",
            "coze",
            "agent_product_capabilities.enterprise_readiness",
            "Coze offers enterprise features including team collaboration spaces, role-based access control, bot version management, and centralized bot governance dashboards for large organizations.",
            0.78,
        ),
        (
            "ev_coze_product_name",
            "src_coze_official",
            "snap_coze_official_site",
            "coze",
            "product_profile.product_name",
            "Coze is a bot creation platform developed by ByteDance that enables users to build and deploy AI-powered bots across multiple channels without writing code.",
            0.91,
        ),
        (
            "ev_coze_workflow",
            "src_coze_docs",
            "snap_coze_documentation",
            "coze",
            "agent_product_capabilities.workflow_orchestration",
            "Coze supports workflow-based bot building with conditional logic, API integrations, and multi-step conversation flows that can be triggered by user messages or scheduled events.",
            0.80,
        ),
        (
            "ev_coze_target_users",
            "src_coze_official",
            "snap_coze_official_site",
            "coze",
            "user_persona.target_users",
            "Coze primarily targets non-technical users, content creators, and small business owners who want to create AI-powered chatbots and automation workflows without programming knowledge.",
            0.83,
        ),
        # FastGPT evidence (5 items)
        (
            "ev_fastgpt_rag",
            "src_fastgpt_github",
            "snap_fastgpt_community_review",
            "fastgpt",
            "function_tree.core_capabilities",
            "FastGPT focuses on RAG-based knowledge base Q&A, allowing users to upload documents and build customized question-answering applications with customizable workflow pipelines.",
            0.80,
        ),
        (
            "ev_fastgpt_deployment",
            "src_fastgpt_docs",
            "snap_fastgpt_documentation",
            "fastgpt",
            "agent_product_capabilities.deployment_options.self_hosted",
            "FastGPT supports Docker-based self-hosted deployment, providing a docker-compose setup that privacy-conscious teams can run entirely on their own infrastructure.",
            0.83,
        ),
        (
            "ev_fastgpt_product_name",
            "src_fastgpt_official",
            "snap_fastgpt_official_site",
            "fastgpt",
            "product_profile.product_name",
            "FastGPT is an open-source knowledge base QA platform built on LLMs, specifically optimized for retrieval-augmented generation workflows.",
            0.89,
        ),
        (
            "ev_fastgpt_target_users",
            "src_fastgpt_official",
            "snap_fastgpt_official_site",
            "fastgpt",
            "user_persona.target_users",
            "FastGPT is designed for development teams and organizations that need to build private knowledge base Q&A systems with full data control and customizable AI workflows.",
            0.81,
        ),
        (
            "ev_fastgpt_pricing",
            "src_fastgpt_official",
            "snap_fastgpt_official_site",
            "fastgpt",
            "pricing_model.has_free_plan",
            "FastGPT is open-source and free to self-host, with optional paid managed cloud services available for teams that prefer not to manage their own infrastructure.",
            0.82,
        ),
        # Flowise evidence (5 items)
        (
            "ev_flowise_nocode",
            "src_flowise_docs",
            "snap_flowise_documentation",
            "flowise",
            "agent_product_capabilities.agent_builder_mode",
            "Flowise provides a visual drag-and-drop interface for building LLM flows without coding, allowing users to chain LLM models, vector databases, and tools by connecting nodes on a canvas.",
            0.81,
        ),
        (
            "ev_flowise_opensource",
            "src_flowise_github",
            "snap_flowise_community_review",
            "flowise",
            "agent_product_capabilities.deployment_options.self_hosted",
            "Flowise is fully open-source under the MIT license, enabling anyone to self-host, customize, and extend the platform for commercial and non-commercial use cases.",
            0.87,
        ),
        (
            "ev_flowise_product_name",
            "src_flowise_official",
            "snap_flowise_official_site",
            "flowise",
            "product_profile.product_name",
            "Flowise is a visual drag-and-drop LLM flow builder that simplifies the creation of AI-powered applications through an intuitive node-based interface.",
            0.90,
        ),
        (
            "ev_flowise_workflow",
            "src_flowise_docs",
            "snap_flowise_documentation",
            "flowise",
            "agent_product_capabilities.workflow_orchestration",
            "Flowise supports workflow orchestration by allowing users to connect LLM nodes with tool nodes, memory nodes, and API integrations, creating multi-step AI pipelines.",
            0.79,
        ),
        (
            "ev_flowise_target_users",
            "src_flowise_official",
            "snap_flowise_official_site",
            "flowise",
            "user_persona.target_users",
            "Flowise targets developers and technical teams who want a low-code approach to building complex LLM applications without writing extensive code from scratch.",
            0.84,
        ),
    ]

    for ev in evidence_definitions:
        ev_id, src_id, snap_id, prod_id, schema_key, snippet, confidence = ev
        EvidenceRepository().add_evidence({
            "evidence_id": ev_id,
            "run_id": run_id,
            "source_id": src_id,
            "snapshot_id": snap_id,
            "product_id": prod_id,
            "schema_key": schema_key,
            "snippet": snippet,
            "confidence": confidence,
            "pii_masked": True,
            "evidence_type": "text",
            "created_at": now,
        })

    # -------------------------------------------------------------------------
    # 5. Facts (at least 8 per product = 32 total)
    # -------------------------------------------------------------------------
    fact_counter = 1
    all_facts = []

    # Dify facts (9)
    dify_facts = [
        ("product_profile.product_name", '"Dify"', "string", 0.92, ["ev_dify_product_name"]),
        ("product_profile.company_name", '"Dify Technology Co., Ltd."', "string", 0.90, ["ev_dify_company_name"]),
        ("agent_product_capabilities.deployment_options.self_hosted", "true", "boolean", 0.88, ["ev_dify_deployment_self_hosted"]),
        ("agent_product_capabilities.workflow_orchestration", "true", "boolean", 0.85, ["ev_dify_workflow"]),
        ("user_persona.target_users", '["AI application developers","enterprise AI teams","DevOps engineers"]', "list", 0.87, ["ev_dify_target_users"]),
        ("pricing_model.has_free_plan", "true", "boolean", 0.84, ["ev_dify_pricing_info"]),
        ("function_tree.core_capabilities", '["LLM orchestration","RAG pipeline","API exposure","multi-agent support"]', "list", 0.83, ["ev_dify_workflow"]),
        ("swot.strengths", '["Open-source flexibility","Visual workflow builder","Self-hosted deployment"]', "list", 0.82, ["ev_dify_deployment_self_hosted", "ev_dify_workflow"]),
        ("customer_voice.positive_feedback", '"Developers praise Dify for its open-source model and self-hosting capability."', "string", 0.80, ["ev_dify_deployment_self_hosted"]),
    ]
    for schema_key, value_json, value_type, confidence, ev_ids in dify_facts:
        all_facts.append(("dify", schema_key, value_json, value_type, confidence, ev_ids))

    # Coze facts (8)
    coze_facts = [
        ("product_profile.product_name", '"Coze"', "string", 0.91, ["ev_coze_product_name"]),
        ("agent_product_capabilities.agent_builder_mode", '"no-code"', "string", 0.82, ["ev_coze_nocode"]),
        ("agent_product_capabilities.enterprise_readiness", "true", "boolean", 0.78, ["ev_coze_enterprise"]),
        ("agent_product_capabilities.workflow_orchestration", "true", "boolean", 0.80, ["ev_coze_workflow"]),
        ("user_persona.target_users", '["Non-technical users","Content creators","Small business owners"]', "list", 0.83, ["ev_coze_target_users"]),
        ("function_tree.core_capabilities", '["Bot creation","Knowledge base integration","Multi-channel deployment"]', "list", 0.79, ["ev_coze_nocode"]),
        ("swot.strengths", '["No-code interface","Fast bot deployment","Multi-platform publishing"]', "list", 0.81, ["ev_coze_nocode"]),
        ("swot.weaknesses", '["Limited self-hosting options","Enterprise features require paid plan"]', "list", 0.77, ["ev_coze_enterprise"]),
    ]
    for schema_key, value_json, value_type, confidence, ev_ids in coze_facts:
        all_facts.append(("coze", schema_key, value_json, value_type, confidence, ev_ids))

    # FastGPT facts (8)
    fastgpt_facts = [
        ("product_profile.product_name", '"FastGPT"', "string", 0.89, ["ev_fastgpt_product_name"]),
        ("function_tree.core_capabilities", '["RAG-based Q&A","Knowledge base management","Customizable workflows"]', "list", 0.80, ["ev_fastgpt_rag"]),
        ("agent_product_capabilities.deployment_options.self_hosted", "true", "boolean", 0.83, ["ev_fastgpt_deployment"]),
        ("user_persona.target_users", '["Development teams","Privacy-conscious organizations","Internal knowledge base teams"]', "list", 0.81, ["ev_fastgpt_target_users"]),
        ("pricing_model.has_free_plan", "true", "boolean", 0.82, ["ev_fastgpt_pricing"]),
        ("agent_product_capabilities.agent_builder_mode", '"low-code"', "string", 0.78, ["ev_fastgpt_rag"]),
        ("swot.strengths", '["RAG specialization","Docker deployment","Open-source"]', "list", 0.82, ["ev_fastgpt_rag", "ev_fastgpt_deployment"]),
        ("customer_voice.positive_feedback", '"FastGPT is praised for its focused RAG capabilities and easy Docker setup."', "string", 0.79, ["ev_fastgpt_deployment"]),
    ]
    for schema_key, value_json, value_type, confidence, ev_ids in fastgpt_facts:
        all_facts.append(("fastgpt", schema_key, value_json, value_type, confidence, ev_ids))

    # Flowise facts (8)
    flowise_facts = [
        ("product_profile.product_name", '"Flowise"', "string", 0.90, ["ev_flowise_product_name"]),
        ("agent_product_capabilities.agent_builder_mode", '"no-code"', "string", 0.81, ["ev_flowise_nocode"]),
        ("agent_product_capabilities.deployment_options.self_hosted", "true", "boolean", 0.87, ["ev_flowise_opensource"]),
        ("agent_product_capabilities.workflow_orchestration", "true", "boolean", 0.79, ["ev_flowise_workflow"]),
        ("user_persona.target_users", '["Developers","Technical teams","AI builders"]', "list", 0.84, ["ev_flowise_target_users"]),
        ("function_tree.core_capabilities", '["LLM chaining","Vector DB integration","Tool use","Memory management"]', "list", 0.80, ["ev_flowise_workflow"]),
        ("swot.strengths", '["Visual UI","MIT license","Active open-source community"]', "list", 0.83, ["ev_flowise_opensource", "ev_flowise_nocode"]),
        ("swot.weaknesses", '["Smaller enterprise feature set","Less documentation than larger platforms"]', "list", 0.76, ["ev_flowise_target_users"]),
    ]
    for schema_key, value_json, value_type, confidence, ev_ids in flowise_facts:
        all_facts.append(("flowise", schema_key, value_json, value_type, confidence, ev_ids))

    review_statuses = ["signed"] * 25 + ["pending"] * 8
    random.shuffle(review_statuses)
    status_iter = iter(review_statuses)

    for prod_id, schema_key, value_json, value_type, confidence, ev_ids in all_facts:
        fact_id = f"fact_{prod_id}_{fact_counter:03d}"
        fact_counter += 1
        review_status = next(status_iter)
        add_fact({
            "fact_id": fact_id,
            "run_id": run_id,
            "product_id": prod_id,
            "schema_key": schema_key,
            "value_json": value_json,
            "value_type": value_type,
            "confidence": confidence,
            "evidence_ids_json": json.dumps(ev_ids),
            "review_status": review_status,
            "created_at": now,
            "updated_at": now,
        })

    # -------------------------------------------------------------------------
    # 6. Claims (9 total)
    # -------------------------------------------------------------------------

    # Dify claim: deployment_options (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_deployment_0",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "deployment_options",
        "claim_text": "Dify provides open-source self-hosted deployment options for enterprise AI application teams.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_deployment_self_hosted"],
        "confidence": 0.88,
        "risk_level": "medium",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_deployment_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: function_tree (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_workflow_0",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "function_tree",
        "claim_text": "Dify supports visual workflow orchestration with conditional branching and loop nodes.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_workflow"],
        "confidence": 0.85,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_workflow_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Coze claim: function_tree (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_coze_bot_0",
        "run_id": run_id,
        "product_id": "coze",
        "dimension": "function_tree",
        "claim_text": "Coze provides a no-code bot building platform targeting non-technical users.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_coze_nocode"],
        "confidence": 0.82,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_coze_bot_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Coze claim: enterprise_readiness (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_coze_enterprise_0",
        "run_id": run_id,
        "product_id": "coze",
        "dimension": "enterprise_readiness",
        "claim_text": "Coze offers enterprise features including team collaboration and bot management.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_coze_enterprise"],
        "confidence": 0.78,
        "risk_level": "medium",
        "support_level": "moderate",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_coze_enterprise_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # FastGPT claim: function_tree (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_fastgpt_rag_0",
        "run_id": run_id,
        "product_id": "fastgpt",
        "dimension": "function_tree",
        "claim_text": "FastGPT focuses on RAG-based knowledge base Q&A with customizable workflows.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_fastgpt_rag"],
        "confidence": 0.80,
        "risk_level": "medium",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_fastgpt_rag_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # FastGPT claim: deployment_options (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_fastgpt_deployment_0",
        "run_id": run_id,
        "product_id": "fastgpt",
        "dimension": "deployment_options",
        "claim_text": "FastGPT supports Docker-based self-hosted deployment for privacy-conscious teams.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_fastgpt_deployment"],
        "confidence": 0.83,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_fastgpt_deployment_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Flowise claim: function_tree (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_flowise_nocode_0",
        "run_id": run_id,
        "product_id": "flowise",
        "dimension": "function_tree",
        "claim_text": "Flowise provides a visual drag-and-drop interface for building LLM flows without coding.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_flowise_nocode"],
        "confidence": 0.81,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_flowise_nocode_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Flowise claim: deployment_options (with evidence)
    ClaimRepository().add_claim({
        "claim_id": "claim_flowise_open_source_0",
        "run_id": run_id,
        "product_id": "flowise",
        "dimension": "deployment_options",
        "claim_text": "Flowise is fully open-source under the MIT license.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_flowise_opensource"],
        "confidence": 0.87,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_flowise_open_source_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: pricing_model (with evidence - signed)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_pricing_missing",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "pricing_model",
        "claim_text": "Dify offers a free community tier with limited usage, as well as paid subscription plans targeting professional teams and enterprise deployments.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_pricing_info"],
        "confidence": 0.84,
        "risk_level": "medium",
        "support_level": "partial",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_pricing_missing",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: SWOT (signed after rework)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_schema_incomplete",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "swot",
        "claim_text": "Dify's strengths include its open-source model, visual workflow builder, and broad LLM support. Weaknesses include relatively complex initial setup for non-technical users. Opportunities include growing enterprise adoption of AI agents. Threats include competition from well-funded platforms like Coze.",
        "claim_type": "swot_strength",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_deployment_self_hosted", "ev_dify_workflow"],
        "confidence": 0.79,
        "risk_level": "medium",
        "support_level": "partial",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_schema_incomplete",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: user_persona
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_persona_0",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "user_persona",
        "claim_text": "Dify primarily serves AI application developers and enterprise DevOps teams requiring self-hosted, privacy-compliant deployments.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_target_users"],
        "confidence": 0.87,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_persona_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: customer_voice (swot positive)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_voice_0",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "customer_voice",
        "claim_text": "Developers praise Dify for its open-source model and self-hosting capability as key strengths in user feedback.",
        "claim_type": "factual_summary",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_deployment_self_hosted"],
        "confidence": 0.80,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_voice_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # Dify claim: swot (strengths)
    ClaimRepository().add_claim({
        "claim_id": "claim_dify_swot_0",
        "run_id": run_id,
        "product_id": "dify",
        "dimension": "swot",
        "claim_text": "Dify's strengths include open-source flexibility, visual workflow builder, and self-hosted deployment options.",
        "claim_type": "swot_strength",
        "fact_ids": [],
        "evidence_ids": ["ev_dify_deployment_self_hosted", "ev_dify_workflow"],
        "confidence": 0.82,
        "risk_level": "low",
        "support_level": "strong",
        "review_status": "signed",
        "signed_claim_id": "signed_claim_dify_swot_0",
        "created_by_agent": "analyst_agent",
        "created_at": now,
        "updated_at": now,
    })

    # -------------------------------------------------------------------------
    # 7. Claim-evidence links
    # -------------------------------------------------------------------------
    link_counter = 1
    claims_with_evidence = [
        ("claim_dify_deployment_0", "ev_dify_deployment_self_hosted", 0.88),
        ("claim_dify_workflow_0", "ev_dify_workflow", 0.85),
        ("claim_dify_pricing_missing", "ev_dify_pricing_info", 0.84),
        ("claim_dify_schema_incomplete", "ev_dify_deployment_self_hosted", 0.79),
        ("claim_dify_persona_0", "ev_dify_target_users", 0.87),
        ("claim_dify_voice_0", "ev_dify_deployment_self_hosted", 0.80),
        ("claim_dify_swot_0", "ev_dify_deployment_self_hosted", 0.82),
        ("claim_coze_bot_0", "ev_coze_nocode", 0.82),
        ("claim_coze_enterprise_0", "ev_coze_enterprise", 0.78),
        ("claim_fastgpt_rag_0", "ev_fastgpt_rag", 0.80),
        ("claim_fastgpt_deployment_0", "ev_fastgpt_deployment", 0.83),
        ("claim_flowise_nocode_0", "ev_flowise_nocode", 0.81),
        ("claim_flowise_open_source_0", "ev_flowise_opensource", 0.87),
    ]

    for claim_id, evidence_id, score in claims_with_evidence:
        ClaimRepository().add_claim_evidence_link({
            "link_id": f"link_{link_counter:03d}",
            "run_id": run_id,
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "support_type": "supports",
            "support_score": score,
            "created_at": now,
        })
        link_counter += 1

    # -------------------------------------------------------------------------
    # 8. Reviews (all signed claims)
    # -------------------------------------------------------------------------
    signed_claims = [
        ("claim_dify_deployment_0", "signed_claim_dify_deployment_0"),
        ("claim_dify_workflow_0", "signed_claim_dify_workflow_0"),
        ("claim_dify_pricing_missing", "signed_claim_dify_pricing_missing"),
        ("claim_dify_schema_incomplete", "signed_claim_dify_schema_incomplete"),
        ("claim_dify_persona_0", "signed_claim_dify_persona_0"),
        ("claim_dify_voice_0", "signed_claim_dify_voice_0"),
        ("claim_dify_swot_0", "signed_claim_dify_swot_0"),
        ("claim_coze_bot_0", "signed_claim_coze_bot_0"),
        ("claim_coze_enterprise_0", "signed_claim_coze_enterprise_0"),
        ("claim_fastgpt_rag_0", "signed_claim_fastgpt_rag_0"),
        ("claim_fastgpt_deployment_0", "signed_claim_fastgpt_deployment_0"),
        ("claim_flowise_nocode_0", "signed_claim_flowise_nocode_0"),
        ("claim_flowise_open_source_0", "signed_claim_flowise_open_source_0"),
    ]

    review_counter = 1
    for claim_id, signed_claim_id in signed_claims:
        ReviewRepository().add_review({
            "review_id": f"review_{review_counter:03d}",
            "run_id": run_id,
            "review_target_type": "claim",
            "review_target_id": claim_id,
            "reviewer_agent": "reviewer_agent",
            "status": "pass",
            "checks": [
                {"check_name": "evidence_required", "status": "pass", "details": "Claim has valid supporting evidence."},
                {"check_name": "schema_compliance", "status": "pass", "details": "Dimension exists in schema."},
                {"check_name": "confidence_threshold", "status": "pass", "details": "Confidence 0.75 meets minimum threshold."},
            ],
            "reason_codes": [],
            "comments": "Claim signed after passing all review checks.",
            "signed_claim_id": signed_claim_id,
            "reviewed_at": now,
            "created_at": now,
        })
        review_counter += 1

    # The pricing claim is the 3rd review in the loop
    rework_review_id = "review_003"

    # -------------------------------------------------------------------------
    # 9. Rework Requests (1 succeeded rework case - for demo history)
    # -------------------------------------------------------------------------
    ReviewRepository().add_rework_request({
        "rework_id": "rw_demo_missing_evidence_001",
        "run_id": run_id,
        "review_id": rework_review_id,
        "target_agent": "collector_agent",
        "target_node": "collect_sources",
        "affected_objects": [{"object_type": "claim", "object_id": "claim_dify_pricing_missing"}],
        "reason_codes": ["MISSING_EVIDENCE"],
        "required_actions": [{
            "action_type": "collect_more_sources",
            "product_id": "dify",
            "schema_keys": ["pricing_model.has_free_plan", "pricing_model.paid_plans"],
            "required_source_types": ["pricing_page", "official_site"],
            "min_new_evidence_count": 1,
        }],
        "success_criteria": {
            "evidence_coverage_rate_min": 0.95,
            "unsupported_claim_count_max": 0,
        },
        "status": "succeeded",
        "retry_count": 1,
        "max_retry": 2,
        "metrics_before": {"evidence_coverage_rate": 0.75, "unsupported_claim_count": 2},
        "metrics_after": {"evidence_coverage_rate": 0.95, "unsupported_claim_count": 0},
        "created_at": now,
        "completed_at": now,
    })

    review_counter += 1
    schema_rework_review_id = f"review_{review_counter:03d}"

    ReviewRepository().add_review({
        "review_id": schema_rework_review_id,
        "run_id": run_id,
        "review_target_type": "claim",
        "review_target_id": "claim_dify_schema_incomplete",
        "reviewer_agent": "reviewer_agent",
        "status": "pass",
        "checks": [
            {"check_name": "schema_completeness", "status": "pass", "details": "SWOT analysis complete after rework."},
            {"check_name": "schema_compliance", "status": "pass", "details": "Dimension exists in schema."},
            {"check_name": "confidence_threshold", "status": "pass", "details": "Confidence meets threshold."},
        ],
        "reason_codes": [],
        "comments": "Claim signed after rework completed.",
        "signed_claim_id": "signed_claim_dify_schema_incomplete",
        "reviewed_at": now,
        "created_at": now,
    })

    ReviewRepository().add_rework_request({
        "rework_id": "rw_demo_schema_missing_001",
        "run_id": run_id,
        "review_id": schema_rework_review_id,
        "target_agent": "extractor_agent",
        "target_node": "extract_facts",
        "affected_objects": [{"object_type": "claim", "object_id": "claim_dify_schema_incomplete"}],
        "reason_codes": ["SCHEMA_FIELD_MISSING"],
        "required_actions": [{
            "action_type": "re_extract_with_schema",
            "target_schema_keys": ["swot.opportunities", "swot.threats"],
            "min_new_facts_count": 2,
        }],
        "success_criteria": {
            "schema_completion_rate_min": 0.90,
        },
        "status": "succeeded",
        "retry_count": 0,
        "max_retry": 2,
        "metrics_before": {"schema_completion_rate": 0.70},
        "metrics_after": {"schema_completion_rate": 0.91},
        "created_at": now,
        "completed_at": now,
    })

    # -------------------------------------------------------------------------
    # 10. Trace logs (12 nodes)
    # -------------------------------------------------------------------------
    dag_nodes = [
        ("trace_001", "build_task_brief", 118),
        ("trace_002", "plan_schema", 142),
        ("trace_003", "plan_sources", 156),
        ("trace_004", "collect_sources", 248),
        ("trace_005", "pii_scrub", 97),
        ("trace_006", "extract_facts", 203),
        ("trace_007", "analyze_dimensions", 177),
        ("trace_008", "review_claims", 189),
        ("trace_009", "write_report", 225),
        ("trace_010", "final_review", 134),
        ("trace_011", "export_report", 112),
        ("trace_012", "compute_metrics", 88),
    ]

    for trace_id, node_name, latency in dag_nodes:
        TraceRepository().add_trace({
            "trace_id": trace_id,
            "run_id": run_id,
            "node_name": node_name,
            "agent_name": f"{node_name}_agent",
            "prompt_version": "v0.1",
            "model_name": "doubao-seed-2.0-lite",
            "input_path": f"data/runs/{run_id}/traces/{trace_id}_input.json",
            "output_path": f"data/runs/{run_id}/traces/{trace_id}_output.json",
            "decision": "success",
            "token_input": random.randint(400, 1200),
            "token_output": random.randint(200, 800),
            "latency_ms": latency,
            "status": "success",
            "started_at": now,
            "completed_at": now,
            "created_at": now,
        })

    # -------------------------------------------------------------------------
    # 11. Report (1 report, 9 sections)
    # -------------------------------------------------------------------------
    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "report_demo_ai_agent_001.md"
    report_file.write_text(
        "# AI Agent Product Competitive Analysis\n\n"
        "This report compares four leading AI Agent platforms: Dify, Coze, FastGPT, and Flowise, "
        "across six key dimensions: function_tree, pricing_model, user_persona, customer_voice, swot, and enterprise_readiness.\n",
        encoding="utf-8",
    )

    ReportRepository().add_report({
        "report_id": "report_demo_ai_agent_001",
        "run_id": run_id,
        "title": "AI Agent Product Competitive Analysis",
        "report_status": "reviewed",
        "content_markdown_path": str(report_file),
        "quality_summary": {
            "claim_count": 13,
            "signed_claims": 13,
            "rework_required": 0,
            "evidence_coverage_rate": 0.95,
            "unsupported_claim_count": 0,
        },
        "created_by_agent": "writer_agent",
        "created_at": now,
        "updated_at": now,
    })

    # -------------------------------------------------------------------------
    # 12. Report spans (9)
    # -------------------------------------------------------------------------
    report_spans = [
        (
            "span_001",
            "report_demo_ai_agent_001",
            "section_01_executive_summary",
            "Executive Summary",
            "paragraph",
            "This competitive analysis examines four leading AI Agent platforms: Dify, Coze, FastGPT, and Flowise. "
            "The analysis reveals distinct positioning: Dify and FastGPT target enterprise teams with self-hosted deployment needs, "
            "Coze focuses on no-code bot building for non-technical users, and Flowise provides a developer-friendly visual LLM flow builder. "
            "All four platforms demonstrate strong open-source or free-tier offerings, with varying degrees of enterprise readiness.",
            ["claim_dify_deployment_0", "claim_coze_bot_0", "claim_fastgpt_deployment_0", "claim_flowise_open_source_0"],
            ["ev_dify_deployment_self_hosted", "ev_coze_nocode", "ev_fastgpt_deployment", "ev_flowise_opensource"],
            False,
        ),
        (
            "span_002",
            "report_demo_ai_agent_001",
            "section_02_product_overview",
            "Product Overview",
            "paragraph",
            "Dify is developed by Dify Technology Co., Ltd. and positions itself as an open-source AI Agent application platform with strong self-hosted capabilities. "
            "Coze, backed by ByteDance, offers a no-code bot building experience targeting non-technical creators and small businesses. "
            "FastGPT specializes in RAG-based knowledge base Q&A with Docker deployment support. "
            "Flowise provides a visual drag-and-drop interface for building LLM flows, operating under the MIT open-source license.",
            ["claim_dify_workflow_0", "claim_coze_bot_0"],
            ["ev_dify_workflow", "ev_coze_nocode", "ev_flowise_nocode"],
            False,
        ),
        (
            "span_003",
            "report_demo_ai_agent_001",
            "section_03_feature_comparison",
            "Feature Comparison",
            "paragraph",
            "In terms of workflow orchestration, Dify stands out with visual conditional branching, loop nodes, and API integration capabilities. "
            "Coze provides workflow-based bot building with multi-channel deployment options. "
            "FastGPT is optimized for RAG pipeline customization with knowledge base management. "
            "Flowise enables LLM chaining through its node-based interface with tool integrations and memory management. "
            "All platforms offer some form of knowledge base integration, though depth varies significantly.",
            ["claim_dify_workflow_0", "claim_coze_bot_0", "claim_fastgpt_rag_0", "claim_flowise_nocode_0"],
            ["ev_dify_workflow", "ev_coze_nocode", "ev_fastgpt_rag", "ev_flowise_nocode"],
            False,
        ),
        (
            "span_004",
            "report_demo_ai_agent_001",
            "section_04_pricing_analysis",
            "Pricing Analysis",
            "paragraph",
            "Dify offers a free community tier with paid subscription plans for professional and enterprise teams. "
            "FastGPT is open-source and free to self-host, with optional managed cloud services. "
            "Dify offers a free community tier with self-hosted deployment options. "
            "Coze provides a free tier for basic bot creation, with enterprise features requiring paid plans. "
            "Flowise is entirely free under the MIT license for self-hosted deployments. "
            "FastGPT offers a free tier optimized for RAG-based knowledge base use cases.",
            ["claim_dify_deployment_0", "claim_coze_bot_0", "claim_flowise_open_source_0", "claim_fastgpt_deployment_0"],
            ["ev_dify_deployment_self_hosted", "ev_coze_nocode", "ev_flowise_opensource", "ev_fastgpt_deployment"],
            False,
        ),
        (
            "span_005",
            "report_demo_ai_agent_001",
            "section_05_user_persona",
            "User Persona",
            "paragraph",
            "Dify primarily serves AI application developers and enterprise DevOps teams requiring self-hosted, privacy-compliant deployments. "
            "Coze targets non-technical users, content creators, and small business owners seeking quick bot creation without programming knowledge. "
            "FastGPT is designed for development teams and organizations building private knowledge base Q&A systems. "
            "Flowise appeals to developers and technical teams wanting low-code LLM application building capabilities.",
            ["claim_dify_persona_0"],
            ["ev_dify_target_users", "ev_coze_target_users", "ev_fastgpt_target_users", "ev_flowise_target_users"],
            False,
        ),
        (
            "span_006",
            "report_demo_ai_agent_001",
            "section_06_customer_voice",
            "Customer Voice",
            "paragraph",
            "User feedback across platforms highlights Dify's open-source flexibility and self-hosting capability as key strengths. "
            "FastGPT receives praise for its focused RAG capabilities and straightforward Docker deployment setup. "
            "Coze users appreciate the rapid bot creation workflow and multi-channel publishing options. "
            "Flowise's MIT license and visual interface are frequently mentioned positively by the developer community.",
            ["claim_dify_voice_0"],
            ["ev_dify_deployment_self_hosted", "ev_fastgpt_deployment", "ev_coze_nocode", "ev_flowise_opensource"],
            False,
        ),
        (
            "span_007",
            "report_demo_ai_agent_001",
            "section_07_swot_analysis",
            "SWOT Analysis",
            "paragraph",
            "Dify's strengths include open-source flexibility, visual workflow builder, and self-hosted deployment options; weaknesses include relatively complex initial setup. "
            "Coze excels in no-code accessibility and multi-platform publishing; limitations include restricted self-hosting options. "
            "FastGPT's RAG specialization and Docker support are notable strengths; opportunities include expanding enterprise feature set. "
            "Flowise benefits from an active open-source community and MIT license; threats include a smaller feature set compared to larger platforms.",
            ["claim_dify_swot_0"],
            ["ev_dify_deployment_self_hosted", "ev_coze_enterprise", "ev_fastgpt_rag", "ev_flowise_opensource"],
            False,
        ),
        (
            "span_008",
            "report_demo_ai_agent_001",
            "section_08_enterprise_readiness",
            "Enterprise Readiness",
            "paragraph",
            "Coze provides enterprise features including team collaboration spaces, role-based access control, and centralized bot governance. "
            "Dify supports enterprise deployments through its self-hosted architecture, enabling full data control for regulated industries. "
            "FastGPT's Docker-based deployment allows enterprise teams to maintain data sovereignty while using an open-source platform. "
            "Flowise, while fully open-source, currently has a smaller enterprise feature set compared to Dify and Coze, "
            "making it more suitable for technical teams with strong in-house DevOps capabilities.",
            ["claim_coze_enterprise_0", "claim_dify_deployment_0"],
            ["ev_coze_enterprise", "ev_dify_deployment_self_hosted", "ev_fastgpt_deployment", "ev_flowise_target_users"],
            False,
        ),
        (
            "span_009",
            "report_demo_ai_agent_001",
            "section_09_key_findings",
            "Key Findings",
            "summary",
            "Four key findings emerge from this competitive analysis. First, all four platforms offer free or open-source tiers, democratizing AI Agent development. "
            "Second, deployment strategy varies significantly: Dify and FastGPT emphasize self-hosted deployments, while Coze and Flowise offer more accessible cloud-based options. "
            "Third, visual workflow orchestration is a shared priority, though implementations differ in depth and complexity. "
            "Fourth, enterprise readiness is strongest in Dify and Coze, with FastGPT and Flowise better suited for technical teams.",
            ["claim_dify_deployment_0", "claim_coze_enterprise_0", "claim_flowise_open_source_0"],
            ["ev_dify_deployment_self_hosted", "ev_coze_enterprise", "ev_flowise_opensource", "ev_dify_pricing_info"],
            False,
        ),
    ]

    for (span_id, report_id, section_id, section_title, span_type, text, claim_ids, evidence_ids, unsupported_flag) in report_spans:
        ReportRepository().add_report_span({
            "span_id": span_id,
            "report_id": report_id,
            "run_id": run_id,
            "section_id": section_id,
            "section_title": section_title,
            "span_type": span_type,
            "text": text,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "unsupported_flag": unsupported_flag,
            "created_at": now,
        })

    # -------------------------------------------------------------------------
    # 13. Eval log
    # -------------------------------------------------------------------------
    source_type_count = len({s["source_type"] for s in source_definitions})
    eval_metrics = {
        "schema_completion_rate": 1.0,
        "evidence_coverage_rate": 0.95,
        "unsupported_claim_rate": 0.0,
        "review_pass_rate": 1.0,
        "rework_success_rate": 1.0,
        "replay_success_rate": 1.0,
        "manual_correction_rate": 0.05,
        "source_coverage_count": source_type_count,
        "conflict_count": 0,
        "analysis_time_minutes": 4.5,
    }

    EvalRepository().add_eval_log({
        "eval_id": "eval_demo_ai_agent_001",
        "run_id": run_id,
        "schema_completion_rate": 1.0,
        "evidence_coverage_rate": 0.95,
        "unsupported_claim_rate": 0.0,
        "review_pass_rate": 1.0,
        "rework_success_rate": 1.0,
        "replay_success_rate": 1.0,
        "manual_correction_rate": 0.05,
        "source_coverage_count": source_type_count,
        "conflict_count": 0,
        "analysis_time_minutes": 4.5,
        "metrics": eval_metrics,
        "created_at": now,
    })

    import sqlite3
    db_path = os.getenv("DATABASE_URL", "sqlite:///./data/productinsight.db").replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)

    print(f"Seeded demo run: {run_id}")
    print(f"  Products: {conn.execute('SELECT COUNT(*) FROM products WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Sources: {conn.execute('SELECT COUNT(*) FROM sources WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Snapshots: {conn.execute('SELECT COUNT(*) FROM snapshots WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Evidence items: {conn.execute('SELECT COUNT(*) FROM evidence_items WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Facts: {conn.execute('SELECT COUNT(*) FROM facts WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    claims_total = conn.execute('SELECT COUNT(*) FROM claims WHERE run_id = ?', (run_id,)).fetchone()[0]
    claims_signed = conn.execute('SELECT COUNT(*) FROM claims WHERE run_id = ? AND review_status = ?', (run_id, "signed")).fetchone()[0]
    claims_rework = conn.execute('SELECT COUNT(*) FROM claims WHERE run_id = ? AND review_status = ?', (run_id, "rework_required")).fetchone()[0]
    print(f"  Claims: {claims_total} (signed={claims_signed}, rework={claims_rework})")
    print(f"  Reviews: {conn.execute('SELECT COUNT(*) FROM reviews WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Rework requests: {conn.execute('SELECT COUNT(*) FROM rework_requests WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Trace logs: {conn.execute('SELECT COUNT(*) FROM trace_logs WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Report spans: {conn.execute('SELECT COUNT(*) FROM report_spans WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    print(f"  Eval log: {conn.execute('SELECT COUNT(*) FROM eval_logs WHERE run_id = ?', (run_id,)).fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
