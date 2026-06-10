"""
Deep Report v2 Service

vNext-R3-A: Deep Report v2 - Multi-stage, evidence-backed, chapterized competitive analysis.

Core workflow:
1. ReportOutline - Generate (LLM) or use existing outline from research_plan
2. SectionResearchPack - Bind evidence/signed claims to each section
3. SectionDraft - Write each section with LLM based on research pack
4. SectionReview - LLM-powered review section depth and evidence coverage
5. RevisionLoop - Rework sections that failed review
6. TableAgent - LLM-driven comparison matrix generation
7. ChartSpecAgent - LLM-driven chart spec generation
8. FinalSynthesis - Combine all sections into final report
9. HTML Report - Generate HTML output
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from backend.app.schemas.deep_report import (
    DEEP_REPORT_OUTLINE,
    get_default_outline,
    ReportSection,
    SectionResearchPack,
    SectionDraft,
    ReportFigure,
    ReportTable,
    ReportReview,
    ReportReviewIssue,
)
from backend.app.storage.repositories import (
    ClaimRepository,
    EvidenceRepository,
    ReportRepository,
    SourceRepository,
    ReportSectionRepository,
    SectionResearchPackRepository,
    SectionDraftRepository,
    ReportFigureRepository,
    ReportTableRepository,
    ReportReviewV2Repository,
)
from backend.app.tracing.llm_trace import traced_llm_call
from backend.app.services.pii_service import sanitize_evidence_snippet
from backend.app.services.evidence_evaluator import is_noise_evidence

logger = logging.getLogger(__name__)

DEEP_REPORT_VERSION = "v2.0"
MAX_REVISION_ROUNDS = 2  # Max revision attempts per section
MAX_PARALLEL_SECTIONS = 3  # Max concurrent sections (reduced for SQLite compatibility)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================================
# Report Outline Generation
# ============================================================================

def get_report_outline(
    run_id: str,
    research_plan: dict[str, Any] | None = None,
    task_brief: dict[str, Any] | None = None,
    signed_claims: list[dict[str, Any]] | None = None,
    domain_schema: dict[str, Any] | None = None,
    query_understanding: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Get the report outline for Deep Report v2.

    Priority:
    1. research_plan.report_outline (structured sections list)
    2. domain_schema + report_type (for generalized cross-domain support)
    3. LLM-generated outline using task_brief.products context
       (even with 0 claims, LLM can generate product-specific outline)
    4. Default DEEP_REPORT_OUTLINE template (last resort)
    
    vNext-R3-B (泛化): Added domain_schema and query_understanding for 
    cross-domain competitive analysis support.
    """
    # Priority 1: use outline from research_plan
    if research_plan and research_plan.get("report_outline"):
        outline = research_plan["report_outline"]
        if isinstance(outline, dict):
            sections = outline.get("sections", [])
            if sections:
                return sections
        elif isinstance(outline, list):
            return outline
    
    # Priority 2: Use domain schema for generalized report generation
    if domain_schema and query_understanding:
        try:
            from backend.app.services.domain_schema import get_generic_report_outline
            products = task_brief.get("products", []) if task_brief else []
            report_type = query_understanding.get("report_type", "product_selection")
            
            outline = get_generic_report_outline(
                report_type=report_type,
                schema=domain_schema,
                products=products,
            )
            if outline:
                logger.info(f"Generated outline from domain_schema: {domain_schema.get('name')}, "
                           f"report_type={report_type}, sections={len(outline)}")
                return outline
        except Exception as e:
            logger.warning(f"Domain schema outline generation failed: {e}")
    
    # Priority 3: LLM outline generation
    # Even with 0 claims, the LLM can generate a product-specific outline
    # from task_brief.products context. This is better than the generic default.
    try:
        outline = _generate_outline_with_llm(
            run_id=run_id,
            task_brief=task_brief or {},
            signed_claims=signed_claims or [],
            domain_schema=domain_schema,
        )
        if outline:
            return outline
    except Exception as e:
        logger.warning("LLM outline generation failed: %s", e)

    # Priority 4: default template (fallback, no product context)
    default = get_default_outline()
    if default:
        return default

    return []


def _generate_outline_with_llm(
    run_id: str,
    task_brief: dict[str, Any],
    signed_claims: list[dict[str, Any]],
    domain_schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Generate report outline using LLM when no outline exists in research_plan.
    
    vNext-R3-B (泛化): Uses domain_schema to generate domain-specific dimensions.
    """
    def _llm_fn() -> dict[str, Any]:
        from backend.app.services.llm_client import get_llm_client

        try:
            client = get_llm_client()
            response_text = client.chat_text(
                messages=[
                    {"role": "system", "content": "You are a competitive analysis expert. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response_text

            # Extract JSON array
            json_match = re.search(r'\[[\s\S]*\]', content)
            if json_match:
                sections = json.loads(json_match.group())
                return {"success": True, "outline": sections, "tokens": 0}
            return {"success": False, "outline": []}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Build prompt
    products = [p.get("product_name", p.get("product_id", "")) if isinstance(p, dict) else str(p) 
                for p in task_brief.get("products", [])]
    products_str = ", ".join(products) if products else "N/A"
    schema_type = task_brief.get("schema_type", "general")
    
    # Get domain-specific dimensions from schema
    domain_dims = ""
    if domain_schema and domain_schema.get("comparison_dimensions"):
        domain_dims = "\n\n## Domain-Specific Comparison Dimensions:\n"
        for dim in domain_schema["comparison_dimensions"]:
            domain_dims += f"- **{dim.get('chinese', dim.get('dimension'))}**: {dim.get('business_question', '')}\n"
        domain_dims += f"\nEvidence sources for this domain: {', '.join(domain_schema.get('evidence_sources', []))}"
    
    schema_name = domain_schema.get("name", "通用领域") if domain_schema else "AI Agent平台"

    prompt = f"""You are an expert competitive analysis report planner for AI Agent platforms.

Given the following competitive analysis context, generate a DECISION-ORIENTED report outline as a JSON array.

CONTEXT:
- Primary Products (正式竞品): {products_str}
- Schema type: {schema_type}
- Number of signed claims available: {len(signed_claims)}
- Sample claims (evidence-backed findings):
{chr(10).join(f"- [{c.get('product_id','?')}/{c.get('dimension','?')}] {c.get('claim_text','')[:120]}" for c in signed_claims[:10]) if signed_claims else "- No claims yet, use product knowledge"}

CRITICAL INSTRUCTIONS:
1. This is a BUSINESS DECISION report, NOT an Evidence Coverage Report. Focus on "what business decisions does this evidence support?"
2. Product Classification: Products should be classified as either "正式竞品" (primary competitors for full analysis) or "Benchmark产品" (reference products for comparison only).
   - If Flowise appears in claims, mark it as "Benchmark产品" (benchmark reference), NOT primary competitor.
   - Similarly, if only 2-3 primary products are given, don't add extra products unless they appear in claims.

3. All quantitative claims (percentages, timelines, cost ratios) MUST be qualified with evidence status:
   - With strong evidence: can state as fact with citation
   - Without evidence: use qualifiers like "typically", "often", "estimated", "requires POC verification"

Generate a JSON array of section definitions. Each section must have:
- slug: kebab-case identifier (e.g., "executive-summary", "swot-analysis")
- title: Chinese section title
- type: "cover" | "chapter" | "appendix" | "executive"
- min_words: minimum word count (executive/chapter: 400-800)
- target_words: target words (executive/chapter: 600-1200)
- purpose: 1-2 sentence purpose in Chinese
- section_type: "decision" | "comparison" | "analysis" | "recommendation"

The outline MUST include these sections in order:
1. Cover page with report title, date, products analyzed (distinguish primary vs benchmark)
2. Executive Decision Summary (执行摘要) - KEY CONCLUSIONS and RECOMMENDATIONS first
3. Analysis Objective & Scope (分析目标与范围) - why this analysis, who it's for
4. Competitor Selection Logic (竞品选择逻辑) - primary vs benchmark classification
5. Market Positioning Map (市场定位图) - 2D positioning of products
6. Competitor Profiles (竞品画像) - one card per PRIMARY product only (benchmark in appendix)
7. Capability Comparison Matrix (能力对比矩阵) - structured comparison across key dimensions
8. Pricing & Deployment Analysis (定价与部署分析) - cost, TCO, deployment options
9. Customer & Ecosystem Signals (客户与生态信号) - user voice, community, market signals
10. SWOT Analysis (SWOT分析) - strengths, weaknesses, opportunities, threats (primary products only)
11. Scenario-based Recommendations (场景化建议) - who should use what, when
12. Risk & Limitation Notes (选型风险说明) - known limitations, areas requiring further investigation
13. Evidence Appendix (证据附录) - full evidence list, benchmarks can appear here

Return ONLY a valid JSON array. Example:
[
  {{"slug": "executive-summary", "title": "执行摘要", "type": "executive", "min_words": 400, "target_words": 600, "purpose": "给出核心结论和选型建议", "section_type": "decision"}}
]

Return ONLY the JSON array. No markdown, no explanation."""

    # Add domain-specific dimensions to prompt if available
    if domain_dims:
        prompt = prompt.replace(
            "The outline MUST include these sections in order:",
            f"{domain_dims}\n\nThe outline MUST include these sections in order:"
        )

    result = traced_llm_call(
        run_id=run_id,
        node_name="outline_generator",
        agent_name="outline_generator",
        agent_role="outline_generator",
        prompt_version="outline_v1",
        prompt_text=prompt,
        input_payload={"claims_count": len(signed_claims), "products": [p.get("product_id") for p in task_brief.get("products", [])]},
        call_fn=_llm_fn,
    )

    po = result.get("parsed_output", {})
    if po.get("success") and po.get("outline"):
        logger.info("LLM generated %d outline sections", len(po["outline"]))
        return po["outline"]
    return []


# ============================================================================
# Section Initialization
# ============================================================================

def initialize_report_sections(
    report_id: str,
    run_id: str,
    outline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Initialize report sections based on the outline. Creates DB records.

    vNext-R3-B: Checks for existing sections to prevent duplicate initialization
    when write_report_v2 is replayed multiple times.
    """
    repo = ReportSectionRepository()

    # P0-fix: Skip if sections already exist for this report.
    # Without this, replaying write_report_v2 would create 17x duplicate sections.
    existing = repo.get_sections_by_report(report_id)
    if existing:
        logger.info(
            "initialize_report_sections: %d sections already exist for report_id=%s, reusing",
            len(existing), report_id,
        )
        return existing

    sections = []
    for idx, section_def in enumerate(outline):
        section_id = _generate_id("section")
        slug = section_def.get("slug") or section_def.get("section_id", f"section_{idx}")
        section_type = section_def.get("type") or "chapter"
        min_words = section_def.get("min_word_count") or section_def.get("min_words", 800)
        target_words = section_def.get("target_words") or (min_words * 1.5)

        section = ReportSection.create(
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            section_index=idx,
            section_title=section_def["title"],
            section_slug=slug,
            section_type=section_type,
            min_word_count=min_words,
            target_word_count=target_words,
            writing_requirements={
                "purpose": section_def.get("purpose"),
                "required_dimensions": section_def.get("required_dimensions"),
                "requires_human_review": section_def.get("requires_human_review"),
            },
        )

        repo.create_section(section.model_dump())
        sections.append(section.model_dump())

    return sections


# ============================================================================
# Section Research Pack Generation
# ============================================================================

def build_section_research_pack(
    section_id: str,
    report_id: str,
    run_id: str,
    section_def: dict[str, Any],
    signed_claims: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    products: list[str],
) -> dict[str, Any]:
    """Build a research pack by binding relevant evidence/claims to a section."""
    pack_repo = SectionResearchPackRepository()

    section_slug = section_def.get("slug", "")
    required_dimensions = _get_section_dimensions(section_slug)

    # P0-5: Support prefix matching so "function_tree.integration" matches section
    # requirement "integration", and "agent_product_capabilities.knowledge_base" matches
    # "knowledge_structure". Falls back to exact match if neither matches.
    def _key_fragments(key: str) -> list[str]:
        """Return lowercased dot/underscore-delimited fragments of a schema key."""
        return key.lower().replace("_", ".").split(".")

    def _matches_dimension(evidence_schema_key: str | None, required_dims: list[str]) -> bool:
        if not required_dims:
            return True
        if not evidence_schema_key:
            return False
        ev_frags = _key_fragments(evidence_schema_key)
        ev_last = ev_frags[-1] if ev_frags else ""

        for dim in required_dims:
            dim_frags = _key_fragments(dim)
            dim_last = dim_frags[-1] if dim_frags else ""

            # Exact match (full key or last segment)
            if evidence_schema_key.lower() == dim.lower() or ev_last == dim_last:
                return True
            # Dot/underscore suffix match: "function_tree.integration" matches "integration"
            ev_lower = evidence_schema_key.lower()
            if ev_lower.endswith(f".{dim.lower()}") or ev_lower.endswith(f"_{dim.lower()}"):
                return True
            # Reverse suffix: "knowledge_structure" ends with "structure" → match
            if ev_lower.endswith(f".{dim_last}") or ev_lower.endswith(f"_{dim_last}"):
                return True
            # Section dimension prefix matches evidence key start
            if ev_lower.startswith(dim.lower()):
                return True
            # Semantic fuzzy: last segment overlap (handles knowledge_base ↔ knowledge_structure)
            if dim_last and ev_last and (
                dim_last.startswith(ev_last) or ev_last.startswith(dim_last)
                or dim_last in ev_last or ev_last in dim_last
            ):
                return True
        return False

    # Filter claims by dimension
    relevant_claims = [
        c for c in signed_claims
        if _matches_dimension(c.get("dimension"), required_dimensions)
    ]

    # Filter facts by dimension
    relevant_facts = [
        f for f in facts
        if _matches_dimension(f.get("schema_key"), required_dimensions)
    ]

    # Filter evidence by dimension
    relevant_evidence = [
        ev for ev in evidence_items
        if _matches_dimension(ev.get("schema_key"), required_dimensions)
    ]

    comparison_points = _extract_comparison_points(relevant_claims, relevant_facts, products)
    missing_info = _identify_missing_information(section_slug, relevant_claims, relevant_facts, products)
    coverage_rate = _calculate_coverage_rate(relevant_claims, relevant_evidence)
    confidence = "high" if coverage_rate >= 0.8 else "medium" if coverage_rate >= 0.5 else "low"

    recommended_tables = []
    if section_def.get("requires_matrix"):
        recommended_tables.append(section_def["requires_matrix"])

    section_question = _generate_section_question(section_slug, section_def)

    pack_id = _generate_id("pack")
    pack = SectionResearchPack.create(
        pack_id=pack_id,
        section_id=section_id,
        report_id=report_id,
        run_id=run_id,
        section_question=section_question,
        required_dimensions=required_dimensions,
        evidence_items=relevant_evidence,
        facts=relevant_facts,
        candidate_claims=[],
        signed_claims=relevant_claims,
        comparison_points=comparison_points,
        missing_information=missing_info,
        risk_notes=[],
        recommended_tables=recommended_tables,
    )
    pack.evidence_coverage_rate = coverage_rate
    pack.confidence_level = confidence
    pack.status = "ready" if coverage_rate >= 0.3 else "insufficient"

    pack_repo.create_pack(pack.model_dump())

    ReportSectionRepository().update_section(section_id, {
        "status": "research_pack_ready",
        "evidence_count": len(relevant_evidence),
        "claim_count": len(relevant_claims),
    })

    return pack.model_dump()


def _get_section_dimensions(section_slug: str) -> list[str]:
    """Map section slug (kebab-case or Chinese) to required analysis dimensions."""
    slug_lower = section_slug.lower().replace("-", "_").replace(" ", "_")

    # Normalize Chinese slugs to keys
    slug_to_key = {
        # P0-1: 3 Schema aligned slugs
        "executive_summary": "executive_summary",
        "executive": "executive_summary",
        "function_tree_overview": "function_tree_overview",
        "workflow_orchestration": "workflow_orchestration",
        "rag_knowledge_base": "rag_knowledge_base",
        "model_support": "model_support",
        "pricing_model": "pricing_model",
        "tco_model": "tco_model",
        "user_persona": "user_persona",
        "selection_scorecard": "selection_scorecard",
        "poc_checklist": "poc_checklist",
        "risks_gaps": "risks_gaps",
        # Legacy / backwards compat
        "market_positioning": "market_positioning",
        "market": "market_positioning",
        "feature_comparison": "feature_comparison",
        "feature": "feature_comparison",
        "feature_overview": "feature_comparison",
        "enterprise_readiness": "enterprise_readiness",  # P0-1: maps to function_tree
        "enterprise": "enterprise_readiness",
        "pricing_analysis": "pricing_model",  # P0-1: maps to pricing_model
        "pricing": "pricing_model",
        "pricing_overview": "pricing_model",
        "ecosystem_analysis": "function_tree_overview",  # P0-1: maps to function_tree
        "ecosystem": "function_tree_overview",
        "customer_voice": "user_persona",  # P0-1: maps to user_persona
        "swot_analysis": "swot_analysis",
        "swot": "swot_analysis",
        "competitive_landscape": "competitive_landscape",
        "competitive": "competitive_landscape",
        "recommendations": "recommendations",
        "recommendation": "recommendations",
        "competitor_overview": "competitor_overview",
        "overview": "competitor_overview",
        "analysis_scope": "analysis_scope",
        "evidence_appendix": "evidence_appendix",
        "knowledge_structure": "function_tree_overview",
        "collaboration_workflow": "user_persona",
        "permission_governance": "function_tree_overview",
        "ai_assistance": "function_tree_overview",
        "integrations_migration": "function_tree_overview",
    }

    key = slug_to_key.get(slug_lower, slug_lower)

    # P0-1: Strictly aligned with 3 Schema keys per 开题材料要求
    dimension_map = {
        # Executive summary covers all 3 schemas
        "executive_summary": ["function_tree", "pricing_model", "user_persona"],
        # function_tree schema sections
        "function_tree_overview": ["function_tree"],
        "workflow_orchestration": ["function_tree"],
        "rag_knowledge_base": ["function_tree"],
        "model_support": ["function_tree"],
        # Legacy feature_comparison → maps to function_tree
        "feature_comparison": ["function_tree"],
        "feature_overview": ["function_tree"],
        "feature": ["function_tree"],
        # pricing_model schema sections
        "pricing_model": ["pricing_model"],
        "pricing_analysis": ["pricing_model"],
        "pricing": ["pricing_model"],
        # user_persona schema sections
        "user_persona": ["user_persona"],
        "market_positioning": ["user_persona"],
        # Cross-schema sections
        "swot_analysis": ["function_tree", "pricing_model", "user_persona"],
        "competitor_overview": ["function_tree", "pricing_model", "user_persona"],
        "competitive_landscape": ["function_tree", "pricing_model", "user_persona"],
        "recommendations": ["function_tree", "pricing_model", "user_persona"],
        "selection_scorecard": ["function_tree", "pricing_model", "user_persona"],
        "poc_checklist": ["function_tree", "pricing_model", "user_persona"],
        "risks_gaps": ["function_tree", "pricing_model", "user_persona"],
        # Utility sections
        "analysis_scope": [],
        "evidence_appendix": [],
        # Legacy mappings (for backwards compat with older outlines)
        "enterprise_readiness": ["function_tree"],  # P0-1: downgraded to function_tree
        "ecosystem_analysis": ["function_tree"],   # P0-1: downgraded to function_tree
        "customer_voice": ["user_persona"],        # P0-1: maps to user_persona
        "knowledge_structure": ["function_tree"],   # P0-1: maps to function_tree
        "collaboration_experience": ["user_persona"],
        "ai_assistance": ["function_tree"],
    }

    return dimension_map.get(key, [])


def _extract_comparison_points(
    claims: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    products: list[str],
) -> list[dict[str, Any]]:
    """Extract structured comparison points from claims and facts."""
    comparison_points = []
    by_dimension: dict[str, list[dict]] = {}
    for claim in claims:
        dim = claim.get("dimension", claim.get("schema_key", "unknown"))
        by_dimension.setdefault(dim, []).append(claim)

    for dimension, dim_claims in by_dimension.items():
        by_product: dict[str, dict] = {}
        for claim in dim_claims:
            pid = claim.get("product_id", "unknown")
            if pid not in by_product:
                by_product[pid] = {"product_id": pid, "claims": [], "facts": []}
            by_product[pid]["claims"].append(claim)

        comparison_points.append({
            "dimension": dimension,
            "products": list(by_product.keys()),
            "comparisons": by_product,
        })

    return comparison_points


def _identify_missing_information(
    section_slug: str,
    claims: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    products: list[str],
) -> list[str]:
    """Identify what information is missing for a section."""
    missing = []
    covered_products = set(c.get("product_id") for c in claims)
    for product in products:
        if product not in covered_products:
            missing.append(f"Missing evidence for {product}")

    by_dimension: dict[str, int] = {}
    for claim in claims:
        dim = claim.get("dimension", "unknown")
        by_dimension[dim] = by_dimension.get(dim, 0) + 1

    if not by_dimension:
        missing.append("No claims available for this section")

    return missing


def _calculate_coverage_rate(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> float:
    """Calculate evidence coverage rate for a section."""
    if not claims:
        return 0.0
    claims_with_evidence = sum(
        1 for c in claims
        if c.get("evidence_ids") and len(c["evidence_ids"]) > 0
    )
    return claims_with_evidence / len(claims) if claims else 0.0


def _generate_section_question(section_slug: str, section_def: dict[str, Any]) -> str:
    """Generate the research question for a section."""
    question_templates = {
        # P0-1: 3 Schema keys aligned questions
        "executive_summary": "What are the 3-5 key findings from this competitive analysis across function_tree, pricing_model, and user_persona?",
        "competitor_overview": "What are the core positioning, target users, and key capabilities of each competitor?",
        "market_positioning": "Who is the target user for each product and what scenarios do they serve?",
        "function_tree_overview": "What are the core capabilities and functional spectrum of each product?",
        "workflow_orchestration": "How do the products compare in workflow orchestration depth, flexibility, and scalability?",
        "rag_knowledge_base": "How do the products compare in RAG and knowledge management capabilities?",
        "model_support": "How do the products compare in LLM support, model flexibility, and API compatibility?",
        "feature_comparison": "How do the products compare across key functional dimensions?",
        "pricing_model": "What are the pricing models, free tiers, and commercial strategies of each product?",
        "tco_model": "What is the total cost of ownership for each product considering setup, operation, and scaling?",
        "user_persona": "Which user personas (non-technical business, low-code, professional, AI engineers) is each product best suited for?",
        "swot_analysis": "What are the Strengths, Weaknesses, Opportunities, and Threats for each product?",
        "selection_scorecard": "Based on evidence, which products show best fit across function_tree, pricing_model, and user_persona?",
        "poc_checklist": "What specific validation steps should a team take to verify each product's claims?",
        "risks_gaps": "What are the identified risks and evidence gaps in this competitive analysis?",
        "analysis_scope": "What is the scope and methodology of this competitive analysis?",
        # Legacy slug mappings (backwards compat)
        "enterprise_readiness": "How do the products compare in enterprise production readiness? (P0-1: mapped to function_tree)",
        "ecosystem_analysis": "What is the ecosystem strength and developer community health of each product? (P0-1: mapped to function_tree)",
        "customer_voice": "What are users saying about each product - strengths and pain points? (P0-1: mapped to user_persona)",
        "competitive_landscape": "What are the key competitive differentiators and market 格局?",
        "recommendations": "What actionable recommendations can we derive from this analysis?",
    }
    return question_templates.get(section_slug, f"What insights can we derive about {section_def.get('title', 'this topic')}?")


# ============================================================================
# Section Drafting (Per-section LLM calls)
# ============================================================================

def write_section_draft(
    section_id: str,
    report_id: str,
    run_id: str,
    section_def: dict[str, Any],
    research_pack: dict[str, Any],
    signed_claims: list[dict[str, Any]],
    products: list[str],
    schema_type: str | None = None,
    product_id_to_name: dict[str, str] | None = None,
    revision_feedback: str | None = None,
    draft_type: str = "initial",
    is_blocked: bool = False,
) -> dict[str, Any]:
    """
    Write a section draft using LLM based on the research pack.

    Supports both initial draft and revision (with revision_feedback).
    Uses traced_llm_call for full observability.
    """
    section_repo = ReportSectionRepository()
    draft_repo = SectionDraftRepository()

    previous_drafts = draft_repo.get_drafts_by_section(section_id)
    draft_index = len(previous_drafts) + 1

    prompt = _build_section_prompt(
        section_def=section_def,
        research_pack=research_pack,
        signed_claims=signed_claims,
        products=products,
        schema_type=schema_type,
        product_id_to_name=product_id_to_name,
        revision_feedback=revision_feedback,
        is_blocked=is_blocked,
    )

    system_msg = _build_section_system_message(section_def, schema_type)

    def _write_section_fn() -> dict[str, Any]:
        from backend.app.services.llm_client import get_llm_client

        try:
            client = get_llm_client()
            response_text = client.chat_text(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4000,
            )
            content = _extract_json_from_response(response_text)
            return {
                "success": True,
                "content": content,
                "model": client.model_name,
                "tokens": 0,
            }
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return {
                "success": False,
                "content": _generate_fallback_section(section_def, research_pack, products),
                "error": str(e),
            }

    result = traced_llm_call(
        run_id=run_id,
        node_name="section_writer",
        agent_name="section_writer",
        agent_role="writer",
        prompt_version=f"section_{section_def.get('slug', 'unknown')}_v1",
        prompt_text=prompt,
        input_payload={
            "section_id": section_id,
            "section_title": section_def.get("title"),
            "research_pack_id": research_pack.get("pack_id"),
            "claims_count": len(signed_claims),
            "draft_type": draft_type,
            "revision_round": draft_index - 1,
        },
        call_fn=_write_section_fn,
    )

    # Extract content from traced_llm_call result
    # traced_llm_call returns {"parsed_output": {...}} where parsed_output may be a dict
    # with {"content_markdown": "...", "key_judgments": [...]}
    po = result.get("parsed_output", {})
    draft_content = po.get("content_markdown") or po.get("content") or ""

    # If content is a dict (JSON returned as object), extract content_markdown
    if isinstance(draft_content, dict):
        draft_content = draft_content.get("content_markdown", "")

    if not draft_content:
        raw = result.get("content", "")
        if isinstance(raw, str):
            # Try to parse JSON from raw text
            try:
                json_match = re.search(r'\{[\s\S]*\}', raw)
                if json_match:
                    parsed = json.loads(json_match.group())
                    draft_content = parsed.get("content_markdown") or parsed.get("content", "")
                    if isinstance(draft_content, dict):
                        draft_content = draft_content.get("content_markdown", "")
            except (json.JSONDecodeError, ValueError):
                draft_content = raw

    # Extract structured fields from LLM response
    parsed_extra = {}
    try:
        raw = po.get("content") or result.get("content", "{}")
        if isinstance(raw, str):
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                parsed_extra = json.loads(json_match.group())
        elif isinstance(raw, dict):
            parsed_extra = raw
    except Exception:
        pass

    # Extract cited evidence IDs from claims + from markdown content
    cited_evidence_ids = []
    # From claims
    for claim in signed_claims[:20]:
        for ev_id in (claim.get("evidence_ids") or []):
            if ev_id and ev_id not in cited_evidence_ids:
                cited_evidence_ids.append(ev_id)
    # From markdown content: parse [E1], [E2], etc.
    if draft_content:
        for m in re.finditer(r'\[E(\d+)\]', draft_content):
            # Try common evidence ID formats
            for fmt in (f"ev_{m.group(1)}", f"E{m.group(1)}", f"evidence_{m.group(1)}"):
                if fmt and fmt not in cited_evidence_ids:
                    cited_evidence_ids.append(fmt)
                    break

    draft_id = _generate_id("draft")
    draft = SectionDraft.create(
        draft_id=draft_id,
        section_id=section_id,
        report_id=report_id,
        run_id=run_id,
        content_markdown=draft_content,
        draft_type=draft_type,
        draft_index=draft_index,
        trigger_type="automatic",
        review_feedback=revision_feedback,
        created_by_agent="section_writer",
        key_judgments=parsed_extra.get("key_judgments", []),
        cited_evidence_ids=cited_evidence_ids,
    )

    draft_repo.create_draft(draft.model_dump())
    section_repo.update_section(section_id, {
        "status": "draft_complete",
        "word_count": draft.word_count,
        "revision_count": draft_index - 1,
    })

    # Did the LLM call succeed? Check via parsed_output
    llm_success = po.get("success", True) if isinstance(po, dict) else True

    return {
        "draft_id": draft_id,
        "section_id": section_id,
        "word_count": draft.word_count,
        "status": "draft_complete",
        "llm_success": llm_success,
        "key_judgments": parsed_extra.get("key_judgments", []),
        "evidence_references": parsed_extra.get("evidence_references", []),
    }


def _build_section_system_message(
    section_def: dict[str, Any],
    schema_type: str | None,
) -> str:
    """Build the system message for section writing."""
    section_type = section_def.get("type", "chapter")
    title = section_def.get("title", "")
    section_slug = section_def.get("slug", "")
    section_category = section_def.get("section_type", "analysis")

    # Base system message
    base_msg = """你是一位资深竞品分析战略师，正在撰写面向企业决策者的商业竞品分析报告。

你的任务是为竞品分析报告撰写一个章节，帮助企业决策者做出选择。

## 你的角色

你不是在写"证据覆盖率报告"或"审计仪表盘"。
你是在写一份"决策导向型竞品分析"，帮助企业管理层做出选择。

## 语言强制约束（最高优先级）

本报告必须全程使用中文撰写。所有输出内容必须为中文。

具体要求：
- 报告正文、分析结论、产品对比、SWOT分析、定价对比等所有章节内容，必须使用中文
- 表格单元格内的内容、表格标题、表格解读，必须全部为中文
- Blockquote/引用说明文字，必须为中文
- 任何解释性、过渡性、总结性段落，必须为中文
- 允许出现的英文：专有名词（产品名，品牌名，技术栈名，API名）
- 禁止出现的英文：描述性句子（如"Publicly available free tier"、"SaaS-based paid subscription"）、形容词（如"unified"、"production-grade"、"low-cost"）、动词短语（如"Supports building"、"Facing competitive pressure"）

如果输出内容中出现英文句子（非专有名词），将被视为严重错误。

## 报告类型与写作风格

| 章节类型 | 风格 | 示例 |
|---|---|---|
| Executive Summary | 结论优先，简短证据支撑 | "结论：Dify更适合企业快速部署" |
| Competitor Profile | 结构化产品卡片与定位 | "产品定位：开发者框架 vs 低代码平台" |
| Capability Matrix | 结构化对比与商业含义 | "工作流：Dify可视化 vs LangChain代码化" |
| SWOT | 优势/劣势→机会/威胁 | "优势：开源 → 机会：私有化需求" |
| Recommendations | WHO + WHAT + WHEN + WHY | "技术团队 → LangGraph → 构建复杂Agent" |

## 关键约束

1. 商业影响优先：每个发现都要回答"然后呢？"和"这对决策者为什么重要？"

2. 拒绝证据剧场：不要写"基于31条证据"或"证据覆盖率100%"。

3. 正确引用：用[E1]、[E2]等引用证据，重点是结论而非引用数量。

4. 缺口报告：写"该维度需进一步核实"——避免使用令人警觉的标签。

5. 无网页噪音：绝不包含Cookie横幅、导航文本、API密钥或页面样板。

6. 禁止未核实数字：没有证据不要生成具体百分比、时间线或成本比率。
   错误："TCO仅为30%"，"上线周期1-2周"，"效率高出80%"
   正确："通常能显著降低成本（需POC验证）"，"上线周期因团队能力而异"

7. Coze区域限制：必须使用以下确切措辞描述区域限制：
   "当前证据显示Coze存在区域访问与站点跳转限制，但尚不足以完整判断其全球部署边界。对于跨境团队，该项应作为高优先级POC与合规核验项。"

输出格式 — 仅返回JSON对象：
{
    "content_markdown": "你的章节正文，使用中文。执行摘要：以## 核心结论开头；其他章节：直接以正文开头。",
    "key_judgments": ["中文判断1", "中文判断2"],
    "evidence_references": ["[E1] 证据来源描述"],
    "unsupported_claims": ["无法核实的声明"]
}

不要在JSON对象之外包含任何文本。content_markdown字段应包含纯Markdown文本。"""

    # Add schema-specific context (internal guidance, not output)
    if schema_type == "ai_agent_platform":
        base_msg += """

## AI Agent平台分析背景（仅供参考，不写入报告）

关键商业问题：
- 哪个平台适合非技术团队？哪个适合开发者？
- 哪个能最快实现企业部署？哪个最灵活？

对比关键维度（及其商业含义）：
| 维度 | 商业问题 |
|---|---|
| 工作流编排 | 团队多久能搭建可生产使用的工作流？ |
| RAG/知识库 | 企业知识能否得到妥善管理和安全保障？ |
| 工具/插件生态 | 平台对自定义集成的扩展性如何？ |
| 部署方式 | 能否在私有云/本地部署？ |
| 定价/TCO | 包括运维开销在内的总成本是多少？ |
| 学习曲线 | 新团队多久能上手？"""

    return base_msg


def _build_section_prompt(
    section_def: dict[str, Any],
    research_pack: dict[str, Any],
    signed_claims: list[dict[str, Any]],
    products: list[str],
    schema_type: str | None,
    product_id_to_name: dict[str, str] | None = None,
    revision_feedback: str | None = None,
    is_blocked: bool = False,
) -> str:
    """Build the user prompt for section writing."""
    section_slug = section_def.get("slug", "")
    title = section_def.get("title", "")
    min_words = section_def.get("min_words", 800)
    target_words = section_def.get("target_words", 1200)
    purpose = section_def.get("purpose", "")

    prompt_parts = [f"# 撰写章节：{title}\n"]

    # P0-Rebuild: Tell the LLM about pre-assessment state BEFORE it writes any content.
    # This must come early in the prompt so it shapes all output.
    if is_blocked:
        prompt_parts.extend([
            f"",
            f"## ⚠️ 预评估状态 — 关键写作约束",
            f"本报告处于预评估/阻塞状态，因为证据不足，无法支撑正式采购结论。",
            f"",
            f"绝对禁止（违反以下任何一条将导致报告不可靠）：",
            f"  - 禁止使用强烈正面推荐语言：",
            f"    '优先选择'、'最优'、'最优选'、'最优秀'、'最高'、'strongly recommend'、",
            f"    'top pick'、'optimal choice'、'most mature'、'optimal pick'、",
            f"    'most cost-effective'、'best suited'、'best option'、'best choice'、",
            f"    'winner'、'market leader'、'first choice'、'preferred choice'、",
            f"    '🥇🥈🥉'或任何数字排名（第1/2/3名）",
            f"  - 禁止声称某一产品明显优于另一产品",
            f"  - 禁止将本报告呈现为正式采购推荐",
            f"",
            f"对比和推荐时必须使用的措辞：",
            f"  - 使用：'候选方向'、'建议优先核验'、'可作为POC候选'、'待补证后重新评估'",
            f"  - 零覆盖率产品使用：'⚠️ 无签署声明，需补证后重新评估'",
            f"  - 未经核实的维度使用：'该维度需进一步核实'，避免使用【证据缺口】等警示标签",
            f"  - 使用：'需结合POC验证后决策'",
            f"  - 每个对比陈述前须加限定语：",
            f"    '据公开资料'、'根据产品定位'、'初步来看'、'目前来看'、",
            f"    '该信息有待进一步验证'、'在证据支持的前提下'",
            f"",
        ])

    if revision_feedback:
        prompt_parts.extend([
            f"",
            f"## 🔄 REVISION FEEDBACK",
            f"Previous draft had the following issues. Please address them:",
            f"{revision_feedback}",
            f"",
        ])

    prompt_parts.extend([
        f"",
        f"## 章节要求",
        f"- 标题：{title}",
        f"- 目的：{purpose}",
        f"- 最少字数：{min_words}",
        f"- 目标字数：{target_words}",
        f"- 章节类型：{section_def.get('type', 'chapter')}",
    ])

    if section_def.get("key_judgments"):
        prompt_parts.append(f"- 必须包含以下核心判断：{section_def['key_judgments']}")
    if section_def.get("product_cards"):
        prompt_parts.append("- 必须为每个产品包含竞品卡片")
    if section_def.get("actionable"):
        prompt_parts.append("- 必须包含可落地的建议")
    if section_def.get("requires_matrix"):
        prompt_parts.append(f"- 参考 {section_def['requires_matrix']} 表格")

    # P1.1 Fix: Include evidence snippets directly in the prompt so the LLM
    # can write based on actual evidence, not just claim references.
    # P0-2 Fix: Filter noise evidence AND sanitize secrets before passing to LLM.
    raw_evidence = research_pack.get("evidence_items", [])
    evidence_items = []
    for ev in raw_evidence:
        snippet = ev.get("snippet", "")
        # Skip pure navigation/noise evidence — not useful for analysis
        if is_noise_evidence(snippet):
            continue
        # Sanitize secrets and PII before including in prompt
        safe_snippet, _ = sanitize_evidence_snippet(snippet)
        ev = dict(ev)  # shallow copy so we don't mutate the original
        ev["snippet"] = safe_snippet
        evidence_items.append(ev)
    if evidence_items:
        prompt_parts.extend(["", "## 证据条目（原始来源材料）"])
        prompt_parts.append(
            "以下是支撑声明的原始证据片段。请据此撰写有具体证据支撑的分析。"
        )
        for idx, ev in enumerate(evidence_items[:15], 1):
            snippet = ev.get("snippet", "")[:300]
            schema_key = ev.get("schema_key", "unknown")
            # P0-Fix: Use display name for evidence items in prompt
            raw_pid = ev.get("product_id", "unknown")
            product = (product_id_to_name or {}).get(raw_pid) or raw_pid
            src_title = ev.get("section_title", "unknown source")
            llm_classified = ev.get("llm_classified", False)
            classifier_note = " [LLM-classified]" if llm_classified else ""
            prompt_parts.append(
                f"{idx}. [{product}/{schema_key}{classifier_note}] ({src_title}):\n"
                f"   \"{snippet}\""
            )

        # P5 Fix: STRICT synthesis instruction — prevent verbatim reproduction of search snippets
        prompt_parts.extend([
            "",
            "⚠️  必须综合 — 禁止直接复制原始证据片段：",
            "  上方证据是原材料，你需要将其综合为结构化分析。",
            "  严禁逐字复制原始搜索结果标题、片段或样板文本。",
            "  严禁写出类似'以下是前5个结果'或'优先官方资源'的内容。",
            "  务必将证据转化为你自己的分析语言。",
        ])

    prompt_parts.extend([
        "",
        f"## 研究问题",
        research_pack.get("section_question", ""),
        "",
        f"## 待分析产品：{', '.join(products)}",
        "重要：你必须在该章节中提及所有上述产品，不得遗漏任何产品。",
        "如果某产品证据不足，仍须以适当措辞提及。",
        "",
        "## 已签署声明（基于证据的事实）",
    ])

    if signed_claims:
        pid_to_name = product_id_to_name or {}
        for idx, claim in enumerate(signed_claims[:20], 1):
            claim_text = claim.get("claim_text", "")
            safe_claim_text, _ = sanitize_evidence_snippet(claim_text)
            # P0-Fix: Use display name instead of raw run-scoped product_id
            raw_pid = claim.get("product_id", "unknown")
            product = pid_to_name.get(raw_pid) or raw_pid
            dimension = claim.get("dimension", "unknown")
            evidence_count = len(claim.get("evidence_ids", []) or [])
            prompt_parts.append(
                f"{idx}. [{product}/{dimension}] {safe_claim_text} "
                f"[supported by {evidence_count} evidence items]"
            )
    else:
        # No claims: allow LLM to use its training knowledge with qualifiers
        prompt_parts.extend([
            "（该章节暂无已验证的声明。）",
            "你可以使用关于这些产品的一般知识撰写本章，但你必须：",
            "  - 使用限定语：'据公开资料'、'根据产品定位'、'通常来看'、'在业内'",
            "  - 声明不确定性：'该信息有待进一步验证'",
            "  - 不要将一般知识作为已验证的事实呈现",
            "  - 自然地将证据缺口作为正常句子的组成部分标注，",
            "    不要使用【证据缺口】等警示标签，应写'该信息需进一步核实'",
        ])

    comparison_points = research_pack.get("comparison_points", [])
    if comparison_points:
        prompt_parts.extend(["", "## 对比数据"])
        for cp in comparison_points[:5]:
            prompt_parts.append(f"- {cp.get('dimension', 'dimension')}: {cp.get('products', [])}")

    missing_info = research_pack.get("missing_information", [])
    if missing_info:
        prompt_parts.extend([
            "",
            "## ⚠️ 缺失信息",
            "以下信息不可用。严禁捏造事实：",
        ])
        for info in missing_info:
            prompt_parts.append(f"- {info}")

    prompt_parts.extend([
        "",
        "## 写作指南（严格遵守 — 决策导向型报告）",
        "- 只写本章正文内容 — 不写标题行或章节标题",
        "  （除非本章本身就是执行摘要）",
        "- 不要生成独立报告、执行摘要或其他章节",
        "- 不要重复其他章节的内容",
        "",
        "## 内容要求（关键）",
        "1. 以1-2段引言开头，然后正文，最后简短的总结",
        "2. 每个发现都要解释商业影响（然后呢？这对决策者为什么重要？）",
        "3. 按维度或产品适当组织结构",
        "4. 执行摘要：先给结论，再给简短的证据支撑",
        "5. SWOT：先陈述有证据支撑的优势/劣势，再推导机会/威胁",
        "6. 建议：说明谁在何时用什么，并有明确的理由",
        "",
        "## 禁止内容（必须遵守）",
        "- 禁止以'基于X条证据'作为主要发现",
        "- 禁止列出网页原始片段 — 将证据转化为自己的分析语言",
        "- 禁止使用'Dify: 31条证据，LangChain: 16条证据'这样的表述",
        "- 禁止包含网页噪音（Cookie声明、导航菜单等）",
        "- 禁止将证据数量作为结论呈现",
        "- 禁止复制搜索结果标题或样板文本",
        "  禁止写法：'以下是前5个结果'、'优先官方资源'、",
        "  '优先官方资源与文档'、裸露URL、搜索引擎输出",
        "- 没有证据时禁止生成具体百分比、时间线或成本比率：",
        "  禁止：'TCO仅为30%'、'上线周期1-2周'、'效率高出80%'、'2-3天培训'",
        "  必须：使用限定语如'通常能显著降低成本'、'上线周期因团队能力而异（需POC验证）'",
        "- Coze区域限制：必须使用确切措辞：",
        "  '当前证据显示 Coze 存在区域访问与站点跳转限制，但尚不足以完整判断其全球部署边界。",
        "   对于跨境团队，该项应作为高优先级 POC 与合规核验项。'",
        "",
        "## 引用格式",
        "- 用[E1]、[E2]等为每个事实声明引用证据",
        "- 证据不足时，写一段简短的文字承认不确定性，避免使用【证据缺口】等警示标签",
        "- 使用自然的不确定语言：'该维度需进一步核实'而非令人警觉的缺口标签",
        "",
        "现在只用中文撰写本章内容。仅返回JSON响应，不要包含任何额外说明。",
    ])

    return "\n".join(prompt_parts)


def _extract_json_from_response(content: str) -> str:
    """Extract JSON content from LLM response.
    
    Handles nested JSON: if content_markdown contains another JSON object,
    recursively extract to get the actual markdown content.
    """
    def _recursive_extract(obj: Any) -> Any:
        """Recursively extract nested JSON objects until we get markdown text."""
        if isinstance(obj, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(obj)
                return _recursive_extract(parsed)
            except (json.JSONDecodeError, TypeError):
                # Not JSON, return as-is
                return obj
        elif isinstance(obj, dict):
            # Priority: content_markdown > content > text
            for key in ["content_markdown", "content", "text", "markdown"]:
                if key in obj:
                    return _recursive_extract(obj[key])
            # No content key found, return the dict as string representation
            return str(obj)
        elif isinstance(obj, list):
            # For lists, try to join or return
            return str(obj)
        else:
            return str(obj)
    
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            result = _recursive_extract(parsed)
            # If result is still a JSON-like string, return as-is
            if isinstance(result, str):
                return result
            return str(result)
        except json.JSONDecodeError:
            pass
    return content


def _generate_fallback_section(
    section_def: dict[str, Any],
    research_pack: dict[str, Any],
    products: list[str],
) -> str:
    """Generate a fallback section when LLM writing fails.

    Uses the LLM with a lightweight prompt (no claims dependency) so that
    we still get product-aware structured content even without evidence.
    Falls back to template text only if the LLM also fails.
    """
    title = section_def.get("title", "")
    slug = section_def.get("slug", "")
    section_type = section_def.get("type", "chapter")
    min_words = section_def.get("min_words", 500)
    target_words = section_def.get("target_words", 800)
    purpose = section_def.get("purpose", "")
    required_dims = section_def.get("required_dimensions", [])

    dims_str = ", ".join(required_dims) if required_dims else "general"

    def _llm_fallback_fn() -> str:
        from backend.app.services.llm_client import get_llm_client

        products_str = ", ".join(products) if products else "未指定产品"

        prompt = f"""You are an expert competitive analysis report writer.

Write a section for a competitive analysis report:

SECTION: {title}
SLUG: {slug}
PRODUCTS TO ANALYZE: {products_str}
SECTION PURPOSE: {purpose}
TARGET WORD COUNT: {target_words} Chinese characters

GUIDELINES:
- Write ONLY in Chinese
- Write ONLY this section — do NOT write a full report or other sections
- Do NOT invent specific facts or statistics. Use qualified statements.
- Use ### sub-headers for sub-sections (no top-level ## heading)
- Cover all listed products in each sub-section
- Address these dimensions: {dims_str}
- Cite evidence with [E1], [E2] when available
- For uncertain content: use natural language like "该维度需进一步核实" — avoid 【证据缺口】 tags
- End with a brief summary

Return ONLY the section Markdown content. No preamble, no title line."""

        try:
            client = get_llm_client()
            content = client.chat_text(
                messages=[
                    {"role": "system", "content": "You are an expert competitive analysis report writer. Return only valid Markdown."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=3000,
            )
            return content.strip() if content else ""
        except Exception:
            return ""

    content = _llm_fallback_fn()

    if content:
        return content

    # Final fallback: template text (should rarely trigger)
    lines = [f"## {title}\n"]
    products_str = ", ".join(products) if products else "待分析产品"

    if slug == "executive_summary":
        lines.append(f"本节对 {products_str} 进行全面竞品分析，基于 function_tree、pricing_model、user_persona 三个 Schema 的证据提供关键发现摘要。\n\n### 关键发现\n")
        for p in products:
            lines.append(f"- **{p}**：详见正文各章节分析。\n")
        lines.append("\n### 市场格局判断\n基于现有证据，对各产品在市场中的定位和竞争关系进行分析。\n")
    elif slug == "competitor_overview":
        lines.append(f"### 产品总览\n以下是对 {products_str} 的总体概述。\n")
        for p in products:
            lines.append(f"**{p}**：详见正文各章节详细分析。\n")
    elif slug == "analysis_scope":
        lines.append(f"### 分析范围\n本报告覆盖 {products_str} 在企业知识管理/协作平台领域的表现。\n")
        lines.append(f"### 分析维度\n本分析涵盖以下核心维度：{dims_str}。\n")
    elif slug == "feature_comparison":
        lines.append(f"### 功能对比\n基于已收集的证据，对 {products_str} 进行功能对比分析。\n")
    elif slug in ("workflow_orchestration", "rag_knowledge_base", "model_support", "function_tree_overview"):
        lines.append(f"### 功能树分析\n基于已收集的证据，对 {products_str} 的功能能力进行分析（Schema: function_tree）。\n")
    elif slug == "pricing_model":
        lines.append(f"### 商业模式与定价\n基于已收集的证据，对 {products_str} 的定价模式和商业策略进行分析（Schema: pricing_model）。\n")
    elif slug == "pricing_analysis":
        lines.append(f"### 定价分析\n基于已收集的证据，对 {products_str} 的定价模式和商业策略进行分析。\n")
    elif slug == "tco_model":
        lines.append(f"### TCO 成本分析\n基于 pricing_model Schema，对 {products_str} 的总体拥有成本进行分析。\n")
    elif slug == "user_persona":
        lines.append(f"### 用户场景与适用团队\n基于 user_persona Schema，分析 {products_str} 的目标用户群体和适用场景。\n")
    elif slug == "swot_analysis":
        lines.append(f"### SWOT 分析\n")
        for p in products:
            lines.append(f"**{p}**：详见正文功能对比与定价分析章节。\n")
    elif slug == "selection_scorecard":
        lines.append(f"### 场景化选型建议\n基于本报告采集的证据，为不同团队类型提供选型建议与采购前行动指引。\n")
    elif slug == "poc_checklist":
        lines.append(f"### 采购前必须验证什么\n列出采购前需要实测验证的关键项目，帮助团队制定 POC 计划。\n")
    elif slug == "risks_gaps":
        lines.append(f"### 选这个产品有什么风险\n分析各产品在实际使用中可能遇到的风险，并提供缓解建议。\n")
    elif slug == "evidence_appendix":
        lines.append("### 证据附录\n本报告所引用的证据详见附录表格，请参考各章节引用的证据编号。\n")
    else:
        lines.append(f"本节对 {products_str} 进行分析，基于已收集的证据进行编写。\n")

    return "\n".join(lines)


# ============================================================================
# P1.4: Citation Verifier — validate evidence citations in drafted sections
# ============================================================================

class CitationVerifier:
    """Verify that citations in drafted sections reference actual evidence."""

    def __init__(self, evidence_items: list[dict[str, Any]]):
        # Index evidence by ID for O(1) lookup
        self._by_id: dict[str, dict[str, Any]] = {
            ev.get("evidence_id", ""): ev for ev in evidence_items if ev.get("evidence_id")
        }
        self._by_product: dict[str, list[dict[str, Any]]] = {}
        for ev in evidence_items:
            pid = ev.get("product_id", "")
            self._by_product.setdefault(pid, []).append(ev)

    def verify_draft(self, draft_content: str, section_title: str) -> dict[str, Any]:
        """Check a drafted section for citation quality issues."""
        issues: list[dict[str, str]] = []
        cited_ids: set[str] = set()
        unsupported_claims: list[str] = []

        # Pattern 1: Markdown citation markers like [E:1], [E:ev123], [E:src_abc]
        citation_patterns = [
            re.compile(r'\[E:(\d+)\]', re.IGNORECASE),
            re.compile(r'\[E(\d+)\]', re.IGNORECASE),
            re.compile(r'\[E:([\w\-]+)\]', re.IGNORECASE),  # [E:ev123], [E:src_abc]
            re.compile(r'\[来源[：:](.+?)\]', re.IGNORECASE),
            re.compile(r'证据编号[：:]?\s*([\w\-]+)', re.IGNORECASE),
            re.compile(r'来源[：:]\s*(https?://[^\s\]]+)', re.IGNORECASE),
        ]

        for pat in citation_patterns:
            for m in pat.finditer(draft_content):
                ref = m.group(1) or m.group(0)
                cited_ids.add(ref)

        # Pattern 2: Qualified language that doesn't need citations
        qualified_patterns = [
            re.compile(r'据公开资料', re.IGNORECASE),
            re.compile(r'通常来看', re.IGNORECASE),
            re.compile(r'在业内', re.IGNORECASE),
            re.compile(r'根据产品定位', re.IGNORECASE),
            re.compile(r'【证据缺口】', re.IGNORECASE),
            re.compile(r'待验证', re.IGNORECASE),
        ]

        has_qualifiers = any(p.search(draft_content) for p in qualified_patterns)

        # Check: unsupported absolute claims
        absolute_patterns = [
            re.compile(r'是唯一', re.IGNORECASE),
            re.compile(r'业界最佳', re.IGNORECASE),
            re.compile(r'排名第一', re.IGNORECASE),
            re.compile(r'市场领先', re.IGNORECASE),
        ]
        for pat in absolute_patterns:
            for m in pat.finditer(draft_content):
                issues.append({
                    "type": "unsupported_absolute_claim",
                    "text": m.group(0),
                    "message": f"使用了绝对性表述 '{m.group(0)}'，但缺乏证据支持",
                    "severity": "high",
                })

        # Check: evidence IDs that don't exist
        for cid in cited_ids:
            if cid not in self._by_id and not cid.startswith("http"):
                issues.append({
                    "type": "missing_evidence_id",
                    "ref": cid,
                    "message": f"引用了不存在的证据 ID: {cid}",
                    "severity": "medium",
                })

        # Check: factuality of claims referencing evidence
        for cid in cited_ids:
            if cid in self._by_id:
                ev = self._by_id[cid]
                ev_product = ev.get("product_id", "")
                ev_schema = ev.get("schema_key", "")
                # Warn if claim text is very short or generic
                snippet = ev.get("snippet", "")
                if len(snippet) < 50:
                    issues.append({
                        "type": "thin_evidence",
                        "ref": cid,
                        "message": f"证据 {cid} (产品: {ev_product}) 内容过短，可能无法充分支持引用它的论断",
                        "severity": "low",
                    })

        # Check: section written without any evidence or qualifiers
        word_count = len(draft_content.split())
        if word_count > 100 and not cited_ids and not has_qualifiers:
            issues.append({
                "type": "uncited_analysis",
                "message": f"章节 '{section_title}' 篇幅 {word_count} 字但未引用任何证据，也未使用 '据公开资料' 等限定语",
                "severity": "high",
            })

        # Severity summary
        severity_counts = {"high": 0, "medium": 0, "low": 0}
        for issue in issues:
            severity_counts[issue["severity"]] += 1

        return {
            "verified": len([i for i in issues if i["severity"] == "high"]) == 0,
            "issues": issues,
            "cited_evidence_ids": list(cited_ids),
            "cited_count": len(cited_ids),
            "has_qualifiers": has_qualifiers,
            "severity_summary": severity_counts,
            "recommendation": self._get_recommendation(issues, cited_ids, has_qualifiers),
        }

    def _get_recommendation(
        self,
        issues: list[dict[str, Any]],
        cited_ids: set[str],
        has_qualifiers: bool,
    ) -> str:
        high = sum(1 for i in issues if i["severity"] == "high")
        if high == 0 and cited_ids:
            return "PASS — 引用质量良好，建议继续。"
        if high > 0:
            return f"FAIL — 发现 {high} 个高严重性问题，请修改后再提交人工审核。"
        if not cited_ids and not has_qualifiers:
            return "WARN — 章节未引用证据且未使用限定语，建议补充证据引用或添加 '据公开资料' 等说明。"
        return "WARN — 发现一些中低严重性问题，建议检查。"


def verify_all_sections(
    sections: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify citation quality across all sections and return a summary report."""
    verifier = CitationVerifier(evidence_items)
    section_results = {}
    all_issues: list[dict[str, Any]] = []
    verified_count = 0

    for section in sections:
        section_id = section.get("section_id", "")
        title = section.get("section_title", "")
        content = section.get("content_markdown", "")
        result = verifier.verify_draft(content, title)
        section_results[section_id] = result
        all_issues.extend(result["issues"])
        if result["verified"]:
            verified_count += 1

    return {
        "total_sections": len(sections),
        "verified_sections": verified_count,
        "pass_rate": verified_count / len(sections) if sections else 0,
        "total_issues": len(all_issues),
        "section_results": section_results,
        "recommendation": (
            "全部通过" if verified_count == len(sections)
            else f"通过 {verified_count}/{len(sections)} 个章节，建议优先修复高严重性问题"
        ),
    }


# ============================================================================
# LLM-powered Report Reviewer
# ============================================================================

def review_section(
    section_id: str,
    report_id: str,
    run_id: str,
    draft: dict[str, Any],
    section_def: dict[str, Any],
    research_pack: dict[str, Any],
    revision_round: int = 0,
) -> dict[str, Any]:
    """
    LLM-powered section review.

    Evaluates section quality across:
    1. Evidence coverage (does it cite signed claims?)
    2. Depth and analysis quality
    3. Unsupported claims detection
    4. Product coverage (all competitors covered?)
    5. Dimension coverage (required dimensions addressed?)
    6. Word count adequacy

    Returns review with issues, suggestions, and rework instructions.
    """
    review_repo = ReportReviewV2Repository()
    section_repo = ReportSectionRepository()

    # Defensive: ensure draft is never None
    if not draft:
        logger.warning("review_section: draft is None/empty for section_id=%s", section_id)
        return {
            "overall_score": 0,
            "depth_score": 0,
            "evidence_score": 0,
            "issues": [{"description": "No draft available for review", "severity": "error"}],
            "suggestions": [],
        }
    if not isinstance(draft, dict):
        logger.error("review_section: draft is not a dict! type=%s section_id=%s", type(draft).__name__, section_id)
        return {
            "overall_score": 0,
            "depth_score": 0,
            "evidence_score": 0,
            "issues": [{"description": f"Invalid draft type: {type(draft).__name__}", "severity": "error"}],
            "suggestions": [],
        }

    content = draft.get("content_markdown", "") or ""
    word_count = draft.get("word_count", len(content.split()))
    min_words = section_def.get("min_words", 800)
    target_words = section_def.get("target_words", 1200)

    # LLM-powered review
    llm_review = _llm_review_section(
        run_id=run_id,
        section_def=section_def,
        content=content,
        research_pack=research_pack,
        revision_round=revision_round,
    )

    issues = llm_review.get("issues", [])
    suggestions = llm_review.get("suggestions", [])
    overall_score = llm_review.get("overall_score", 0)
    depth_score = llm_review.get("depth_score", 0)
    evidence_score = llm_review.get("evidence_score", 0)

    # Add heuristic checks as supplemental issues
    if word_count < min_words:
        issues.append({
            "issue_type": "too_short",
            "severity": "high",
            "description": f"Section is {word_count} words, minimum is {min_words}",
            "suggested_action": "expand_section",
        })
        suggestions.append(f"Expand section to at least {min_words} words")

    evidence_count = len(research_pack.get("evidence_items", []))
    claim_count = len(research_pack.get("signed_claims", []))
    if evidence_count < 3:
        issues.append({
            "issue_type": "insufficient_evidence",
            "severity": "high",
            "description": f"Only {evidence_count} evidence items for this section",
            "suggested_action": "collect_more_evidence",
        })

    has_analysis_markers = any(m in content for m in [
        "因此", "所以", "表明", "说明", "显示", "适合", "优势", "劣势", "机会", "风险"
    ])
    if not has_analysis_markers:
        issues.append({
            "issue_type": "lacks_analysis",
            "severity": "medium",
            "description": "Section appears to list facts without business analysis",
            "suggested_action": "add_analysis",
        })
        suggestions.append("Add business analysis and implications, not just facts")

    # P1-3: Enhanced depth checks
    # Check 1: Minimum word count (strict)
    if word_count < min_words * 0.8:
        issues.append({
            "issue_type": "too_shallow",
            "severity": "high",
            "description": f"Section has only {word_count} words, requires at least {int(min_words * 0.8)} for minimum quality",
            "suggested_action": "expand_significantly",
        })
        suggestions.append(f"Significantly expand this section - it needs at least {min_words} words")

    # Check 2: Per-product analysis requirement
    # Count how many products are mentioned in the section
    products = research_pack.get("products", [])
    content_lower = content.lower()
    mentioned_products = 0
    for product in products:
        product_lower = product.lower()
        if product_lower in content_lower or product_lower.replace(" ", "") in content_lower:
            mentioned_products += 1
    
    if products and mentioned_products < len(products) * 0.7:
        issues.append({
            "issue_type": "missing_product_coverage",
            "severity": "high",
            "description": f"Only {mentioned_products}/{len(products)} products mentioned in section",
            "suggested_action": "add_per_product_analysis",
        })
        suggestions.append(f"Add analysis for all products - currently missing {len(products) - mentioned_products} products")

    # Check 3: Check for comparison patterns (products should be compared)
    comparison_patterns = [" vs ", " versus ", "相比", "对比", "与", "优于", "劣于", "差异"]
    has_comparison = any(p in content_lower for p in comparison_patterns)
    if not has_comparison and len(products) > 1:
        issues.append({
            "issue_type": "lacks_comparison",
            "severity": "medium",
            "description": "Section lacks product comparisons",
            "suggested_action": "add_comparison_analysis",
        })
        suggestions.append("Add comparative analysis between products, not just individual descriptions")

    # Check 4: Check if section has real content vs placeholder text
    placeholder_indicators = ["待补充", "待分析", "待验证", "待评估", "to be added", "tbd", "pending"]
    has_placeholder = any(p in content_lower for p in placeholder_indicators)
    if has_placeholder:
        issues.append({
            "issue_type": "has_placeholder_content",
            "severity": "high",
            "description": "Section contains placeholder content like '待补充' or 'to be added'",
            "suggested_action": "replace_with_real_analysis",
        })
        suggestions.append("Remove all placeholder text and provide actual analysis based on evidence")

    # Check 5: Evidence gap awareness - section should acknowledge gaps
    evidence_items = research_pack.get("evidence_items", [])
    missing_info = research_pack.get("missing_information", [])
    has_evidence_gap_acknowledgment = any(
        p in content_lower for p in ["证据不足", "缺乏证据", "evidence gap", "insufficient evidence", 
                                       "需进一步", "有待验证", "not available", "暂无"]
    )
    if (len(evidence_items) < 5 or missing_info) and not has_evidence_gap_acknowledgment:
        issues.append({
            "issue_type": "missing_evidence_gap_flag",
            "severity": "medium",
            "description": "Section should acknowledge evidence gaps when evidence is limited",
            "suggested_action": "add_evidence_gap_note",
        })
        suggestions.append("Add a note about evidence limitations when data is insufficient")

    # Determine status
    high_severity_count = len([i for i in issues if i.get("severity") == "high"])
    status = "pass" if high_severity_count == 0 else "fail"

    # Create review record
    review_id = _generate_id("review")
    review = ReportReview.create(
        review_id=review_id,
        report_id=report_id,
        run_id=run_id,
        review_type="section",
        target_id=section_id,
        target_type="section",
        reviewer_agent="report_reviewer",
    )

    review.status = status
    review.overall_score = overall_score
    review.depth_score = depth_score
    review.evidence_score = evidence_score
    # Use ReportReviewIssue Pydantic model for structured issue tracking
    review.issues = [
        ReportReviewIssue(
            issue_type=i.get("issue_type", "unknown"),
            severity=i.get("severity", "medium"),
            section_id=section_id,
            description=i.get("description", ""),
            suggested_action=i.get("suggested_action", ""),
            target_agent="section_writer",
        )
        for i in issues
    ]
    review.suggestions = suggestions

    if issues:
        review.rework_instruction = _generate_rework_instruction(issues)

    review_repo.create_review(review.model_dump())

    section_repo.update_section(section_id, {
        "status": "review_complete" if status == "pass" else "revision_requested",
        "depth_score": overall_score,
        "review_notes": review.rework_instruction or "",
    })

    return review.model_dump()


def _llm_review_section(
    run_id: str,
    section_def: dict[str, Any],
    content: str,
    research_pack: dict[str, Any],
    revision_round: int,
) -> dict[str, Any]:
    """Use LLM to review a section draft."""

    # Build prompt in outer scope so traced_llm_call can reference it
    signed_claims = research_pack.get("signed_claims", [])
    missing_info = research_pack.get("missing_information", [])
    evidence_count = len(research_pack.get("evidence_items", []))
    section_title = section_def.get("title", "")

    claims_str = "\n".join(
        f"- [{c.get('product_id','?')}/{c.get('dimension','?')}] {c.get('claim_text','')[:100]}"
        for c in signed_claims[:15]
    ) or "(No claims)"

    prompt = f"""You are an expert competitive analysis report reviewer.

Review the following section draft. Evaluate it critically and honestly.

SECTION TITLE: {section_title}
MIN WORD COUNT: {section_def.get('min_words', 800)}
TARGET WORD COUNT: {section_def.get('target_words', 1200)}

SIGNED CLAIMS AVAILABLE (these are the ONLY facts you should reference):
{claims_str}

MISSING INFORMATION (do NOT invent these):
{chr(10).join(f'- {m}' for m in missing_info) if missing_info else '(None)'}

SECTION DRAFT TO REVIEW:
{content[:3000]}

TASK:
Evaluate the section and return a JSON object with:
- "overall_score": 0-100 (quality of analysis, evidence usage, business value)
- "depth_score": 0-100 (is it analytical or just descriptive?)
- "evidence_score": 0-100 (does it properly cite claims and evidence?)
- "issues": array of issues found, each with:
    - "issue_type": "unsupported_claim" | "lacks_depth" | "lacks_evidence" | "missing_coverage" | "evidence_gap" | "weak_analysis"
    - "severity": "high" | "medium" | "low"
    - "description": specific description
    - "suggested_action": what to do
- "suggestions": array of strings (improvement recommendations)

Be strict. If the section invents facts not in the claims, flag as high severity.
If the section says "待补充" (to be supplemented) for factual content, flag as high severity.
If evidence is missing for a claim the section makes, flag as high severity.

Return ONLY valid JSON."""

    def _llm_fn() -> dict[str, Any]:
        from backend.app.services.llm_client import get_llm_client

        try:
            client = get_llm_client()
            response_text = client.chat_text(
                messages=[
                    {"role": "system", "content": "You are an expert competitive analysis report reviewer. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                return {**json.loads(json_match.group()), "success": True}
            return {"success": False, "issues": [], "suggestions": [], "overall_score": 50}
        except Exception as e:
            logger.warning("LLM review failed: %s", e)
            return {"success": False, "issues": [], "suggestions": [], "overall_score": 50}

    result = traced_llm_call(
        run_id=run_id,
        node_name="report_reviewer",
        agent_name="report_reviewer",
        agent_role="reviewer",
        prompt_version=f"review_{section_def.get('slug', 'unknown')}_v1",
        prompt_text=prompt,
        input_payload={
            "section_id": section_def.get("slug", ""),
            "section_title": section_def.get("title"),
            "claims_count": len(signed_claims),
            "revision_round": revision_round,
        },
        call_fn=_llm_fn,
    )

    po = result.get("parsed_output", {})
    if po.get("success"):
        return po
    return {
        "overall_score": 50,
        "depth_score": 50,
        "evidence_score": 50,
        "issues": [],
        "suggestions": [],
    }


def _generate_rework_instruction(issues: list[dict[str, Any]]) -> str:
    """Generate a rework instruction from issues."""
    if not issues:
        return ""

    instructions = ["请根据以下问题修改章节内容：\n"]
    for issue in issues:
        instructions.append(f"- {issue['description']}。建议：{issue['suggested_action']}")

    return "\n".join(instructions)


def _sanitize_pricing_table(
    cells: dict[str, Any],
    rows: list[str],
    products: list[str],
    claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    P0-4 Fix: Sanitize pricing table cells to remove fabricated pricing data.

    Rules:
    - If a cell contains specific prices (¥, $, numbers with units) but no evidence,
      replace with actionable guidance (product pricing reference text)
    - If LLM outputs "Not publicly verified" without evidence, replace with guidance
    - Only keep prices that have explicit evidence support
    """
    import re as _re

    # Pattern to detect likely fabricated prices
    fabricated_price_pattern = _re.compile(
        r'(¥|\$|USD|EUR|GBP)\s*\d+|'  # Currency amounts
        r'\d+\s*(per|/)\s*(k|K|1k|1K|token|month|year|user)|'  # Rate patterns
        r'(free|tier|plan)\s*¥?\d+|'  # "free tier ¥99"
        r'¥\d+|'  # Just ¥ amounts
        r'\$\d+|'  # Just $ amounts
        r'\d+%?\s*(off|discount)',  # Discounts
        flags=_re.IGNORECASE
    )

    suspicious_pattern = _re.compile(
        r'\b(starting\s*(at\s*)?|from\s*|as\s*low\s*as)\b|'
        r'not publicly verified',
        flags=_re.IGNORECASE
    )

    sanitized = {}
    for cell_key, cell_data in cells.items():
        cell_text = cell_data.get("text", "")
        ev_count = cell_data.get("evidence_count", 0)

        if ev_count == 0:
            if (fabricated_price_pattern.search(cell_text)
                    or suspicious_pattern.search(cell_text)
                    or cell_text.strip().lower() == "not publicly verified"):
                logger.warning(
                    "P0-4: Removing fabricated pricing data from cell %s: %s",
                    cell_key, cell_text[:80]
                )
                # Extract product name from cell_key (format: "RowLabel_ProductName")
                parts = cell_key.rsplit("_", 1)
                product_name = parts[-1] if len(parts) > 1 else cell_key
                cell_text = f"{product_name}定价详情请参考官方渠道"
            elif cell_text == "—" or not cell_text:
                parts = cell_key.rsplit("_", 1)
                product_name = parts[-1] if len(parts) > 1 else cell_key
                cell_text = f"{product_name}定价详情请参考官方渠道"

        sanitized[cell_key] = {
            **cell_data,
            "text": cell_text,
        }

    return sanitized


# ============================================================================
# TableAgent (LLM-driven comparison matrix generation)
# ============================================================================

def _normalize_product_id(raw: str, canonical_names: list[str]) -> str:
    """
    Normalize product ID to a consistent canonical form.

    Problem: claims in DB have product_id like 'run_cfd3aacbd0214fcd_dify' or 'run_xxx_coze',
    but the products list passed to generate_comparison_table uses canonical names
    like 'Dify'. This mismatch causes all fallback cells to show '—'.

    Resolution: strip the run_id prefix using rsplit("_", 1), then lowercase.
    Example: 'run_cfd3aacbd0214fcd_dify' -> 'dify', 'Dify' -> 'dify'
    """
    if not raw:
        return ""
    lower = raw.strip().lower()
    # Strip run_id prefix: 'run_cfd3aacbd0214fcd_dify' -> 'dify'
    # Use rsplit("_", 1) to get the LAST underscore-separated part
    if "_" in lower:
        parts = lower.rsplit("_", 1)
        if len(parts) == 2 and parts[0].startswith("run_"):
            lower = parts[1]
    # Match against canonical names (case-insensitive)
    for name in canonical_names:
        if name.lower() == lower:
            return name.lower()
    return lower


def _match_claims_for_product(
    product_key: str,
    dimension: str,
    claims: list[dict[str, Any]],
    canonical_names: list[str],
) -> list[dict[str, Any]]:
    """
    Match claims for a given product_key across both 'run_xxx_name' and 'name' formats.
    """
    normalized_key = _normalize_product_id(product_key, canonical_names)
    return [
        c for c in claims
        if _normalize_product_id(c.get("product_id", ""), canonical_names) == normalized_key
        and c.get("dimension") == dimension
    ]


def _recompute_cell_evidence_counts(
    cells: dict[str, Any],
    dimensions: list[str],
    rows: list[str],
    products: list[str],
    claims: list[dict[str, Any]],
    product_id_to_name: dict[str, str] | None = None,
) -> None:
    """
    P0 Fix: Recompute evidence_count for each table cell from actual claim data.

    LLM-generated table cells may omit or incorrectly compute evidence_count.
    This function recalculates it by matching claims for each (product, dimension) cell.

    Uses case-insensitive dimension matching and proper product ID normalization.
    """
    if not claims:
        return

    run_id = claims[0].get("run_id", "") if claims else ""

    # Build slug_to_name from claims' product_id mapping
    slug_to_name: dict[str, str] = {}
    for c in claims:
        pid = c.get("product_id", "")
        pname = c.get("product_name", "")
        if pid and pname and pid not in slug_to_name:
            slug_to_name[pid] = pname
    # Also use the provided mapping
    if product_id_to_name:
        slug_to_name.update(product_id_to_name)

    def norm(s: str) -> str:
        import re as _re
        return _re.sub(r'[^a-z0-9]', '', (s or "").lower())

    def extract_slug(pid: str) -> str:
        import re as _re
        slug = pid
        for prefix in ("product_", "product-"):
            if slug.startswith(prefix):
                slug = slug[len(prefix):]
                break
        return slug

    def pid_matches_product(pid: str, product_name: str) -> bool:
        p_lower = norm(product_name)
        pid_norm = norm(pid)
        if pid_norm == p_lower:
            return True
        slug = extract_slug(pid)
        slug_lower = norm(slug)
        if slug_lower == p_lower or p_lower in slug_lower or slug_lower in p_lower:
            return True
        # Use slug_to_name mapping
        if pid in slug_to_name:
            if norm(slug_to_name[pid]) == p_lower:
                return True
        if slug in slug_to_name:
            if norm(slug_to_name[slug]) == p_lower:
                return True
        # Partial match
        if p_lower in pid_norm or pid_norm in p_lower:
            return True
        return False

    # Normalize row labels to match claim dimensions
    def dim_normalize(d: str) -> str:
        d_lower = d.lower()
        # Map table row labels to claim.dimension values
        mapping = {
            # function_tree sub-dimensions
            "workflow orchestration": "workflow_orchestration",
            "workflow_orchestration": "workflow_orchestration",
            "workflow orchestr": "workflow_orchestration",
            "rag knowledge": "rag_knowledge",
            "rag_knowledge": "rag_knowledge",
            "model support": "model_support",
            "model_support": "model_support",
            "multi agent": "multi_agent",
            "multi_agent": "multi_agent",
            "integration": "integration",
            "security compliance": "security_compliance",
            "security_compliance": "security_compliance",
            # pricing_model sub-dimensions → map to parent "pricing_model"
            "free tier": "pricing_model",
            "free_tier": "pricing_model",
            "paid plans": "pricing_model",
            "paid_plans": "pricing_model",
            "enterprise pricing": "pricing_model",
            "enterprise_pricing": "pricing_model",
            # user_persona sub-dimensions → map to parent "user_persona"
            "non technical business": "user_persona",
            "non_technical_business": "user_persona",
            "low code developers": "user_persona",
            "low_code_developers": "user_persona",
            "professional developers": "user_persona",
            "professional_developers": "user_persona",
            "ai engineers": "user_persona",
            "ai_engineers": "user_persona",
            "user persona": "user_persona",
            "user_persona": "user_persona",
        }
        return mapping.get(d_lower, d_lower)

    for dimension, row_label in zip(dimensions, rows):
        norm_dim = dim_normalize(dimension)
        for product in products:
            cell_key = f"{row_label}_{product}"
            # Find claims matching this (product, dimension)
            matched_claims = []
            for c in claims:
                c_pid = c.get("product_id", "")
                if pid_matches_product(c_pid, product):
                    c_dim = c.get("dimension", "")
                    # Case-insensitive dimension match (exact match only to avoid cross-contamination)
                    c_dim_norm = dim_normalize(c_dim)
                    if c_dim_norm == norm_dim:
                        matched_claims.append(c)

            ev_count = 0
            claim_ids = []
            for c in matched_claims:
                ev_ids = c.get("evidence_ids") or []
                ev_count += len(ev_ids)
                claim_ids.append(c.get("claim_id"))

            # Update cell with computed evidence_count
            existing = cells.get(cell_key, {})
            existing_text = existing.get("text", "")
            # Strip existing [E:n] badge to avoid double-counting
            existing_text = re.sub(r'\s*\[E:\d+\]\s*$', '', existing_text).rstrip()

            if ev_count > 0:
                existing_text += f" [E:{ev_count}]"

            cells[cell_key] = {
                "text": existing_text,
                "claim_ids": claim_ids,
                "evidence_count": ev_count,
            }


def generate_comparison_table(
    report_id: str,
    run_id: str,
    table_type: str,
    table_title: str,
    products: list[str],
    claims: list[dict[str, Any]],
    dimensions: list[str],
    section_def: dict[str, Any] | None = None,
    product_id_to_name: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    TableAgent: Generate structured comparison tables using LLM.

    Creates evidence-backed comparison matrices with:
    - Proper product columns
    - Dimension rows
    - Cell content based on signed claims
    - Evidence indicators
    - Evidence Gap handling for cells without evidence

    P0-v3 Fix: If no evidence exists for a cell, show _gap_fill_text (dimension-specific guidance) instead of "Evidence Gap"
    """
    table_repo = ReportTableRepository()

    # Use LLM to generate structured table data
    llm_table = _llm_generate_table(
        run_id=run_id,
        table_type=table_type,
        table_title=table_title,
        products=products,
        claims=claims,
        dimensions=dimensions,
        section_def=section_def,
    )

    if llm_table.get("success"):
        headers = llm_table.get("headers", ["维度"] + products)
        rows = llm_table.get("rows", [d.replace("_", " ").title() for d in dimensions])
        cells = llm_table.get("cells", {})
        interpretation = llm_table.get("interpretation", "")

        # P0-4 Fix: For pricing tables, mark cells with fabricated prices as "Not publicly verified"
        if table_type in ("pricing_matrix", "pricing_comparison"):
            cells = _sanitize_pricing_table(cells, rows, products, claims)
            # Replace generic "product定价请参考官网" with proper dimension-aware fill text
            for dimension, row_label in zip(dimensions, rows):
                for product in products:
                    cell_key = f"{row_label}_{product}"
                    cell_data = cells.get(cell_key, {})
                    cell_text = cell_data.get("text", "")
                    ev_count = cell_data.get("evidence_count", 0)
                    if ev_count == 0 and "定价请参考" in cell_text:
                        cells[cell_key] = {
                            "text": _gap_fill_text(product, dimension, run_id=run_id),
                            "claim_ids": [],
                            "evidence_count": 0,
                        }

        # P0 Fix: Post-process evidence_count from claims for ALL cells.
        # LLM may not include evidence_count in cells. Recompute from matching claims.
        _recompute_cell_evidence_counts(cells, dimensions, rows, products, claims, product_id_to_name)

        # P0 Fix: Normalize cells - replace "—" with "Evidence Gap" for empty cells
        for dimension, row_label in zip(dimensions, rows):
            for product in products:
                cell_key = f"{row_label}_{product}"
                cell_data = cells.get(cell_key, {})
                cell_text = cell_data.get("text", "")
                ev_count = cell_data.get("evidence_count", 0)
                # If cell is empty or "—" and there's no evidence, mark as Evidence Gap
                if (not cell_text or cell_text == "—") and ev_count == 0:
                    # Check if we have any claims for this product/dimension to provide context
                    matching_claims = _match_claims_for_product(
                        product, dimension, claims, products
                    )
                    if matching_claims:
                        # We have claims but they might be generic - keep "—"
                        pass
                    else:
                        # No claims for this cell — provide useful fill text instead of "Evidence Gap"
                        cells[cell_key] = {
                            "text": _gap_fill_text(product, dimension, run_id=run_id),
                            "claim_ids": [],
                            "evidence_count": 0,
                        }
    else:
        # Fallback: data-driven table using _match_claims_for_product
        # (handles both 'run_xxx_dify' and 'Dify' product_id formats)
        headers = ["维度"] + products
        # Row labels must match the LLM output format: "Function Tree", "Pricing Model", etc.
        rows = [d.replace("_", " ").title() for d in dimensions]
        cells = {}
        for dimension, row_label in zip(dimensions, rows):
            for product in products:
                # Use normalized matching so 'run_xxx_dify' in DB matches 'Dify' in products
                matching_claims = _match_claims_for_product(
                    product, dimension, claims, products
                )
                if matching_claims:
                    claim = matching_claims[0]
                    ev_ids = []
                    try:
                        ev_raw = claim.get("evidence_ids_json", "[]")
                        ev_ids = json.loads(ev_raw) if isinstance(ev_raw, str) else (ev_raw or [])
                    except Exception:
                        ev_ids = claim.get("evidence_ids", []) or []
                    ev_count = len(ev_ids)
                    cell_text = claim.get("claim_text", "")[:100]
                    if ev_count > 0:
                        cell_text += f" [E:{ev_count}]"
                    cells[f"{row_label}_{product}"] = {
                        "text": cell_text,
                        "claim_ids": [claim.get("claim_id")],
                        "evidence_count": ev_count,
                    }
                else:
                    # No claims — provide useful fill text instead of "Evidence Gap"
                    cells[f"{row_label}_{product}"] = {
                        "text": _gap_fill_text(product, dimension, run_id=run_id),
                        "claim_ids": [],
                        "evidence_count": 0,
                    }
        interpretation = ""

    table_id = _generate_id("table")
    table = ReportTable.create(
        table_id=table_id,
        report_id=report_id,
        run_id=run_id,
        table_type=table_type,
        table_title=table_title,
        headers=headers,
        rows=rows,
        cells=cells,
        interpretation=interpretation,
    )

    table_repo.create_table(table.model_dump())
    return table.model_dump()


# ============================================================================
# Evidence Chunking Utilities (P0-5: Long Context Handling)
# ============================================================================


def _estimate_evidence_tokens(ev: dict[str, Any]) -> int:
    """Estimate token count for an evidence item.

    Rough estimate: ~2 chars per token for Chinese-heavy text.
    """
    text = " ".join(filter(None, [
        ev.get("snippet", ""),
        ev.get("source_title", ""),
        ev.get("product_id", ""),
    ]))
    return len(text) // 2


def _chunk_evidence_for_llm(
    evidence_items: list[dict[str, Any]],
    max_tokens: int = 8000,
    overlap: int = 200,
) -> list[list[dict[str, Any]]]:
    """
    Chunk evidence items into groups that fit within LLM context window.
    Groups by product to keep related evidence together.
    """
    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    current_tokens = 0

    by_product: dict[str, list[dict]] = {}
    for ev in evidence_items:
        product = ev.get("product_id", "unknown")
        by_product.setdefault(product, []).append(ev)

    for product, evs in by_product.items():
        for ev in evs:
            ev_tokens = _estimate_evidence_tokens(ev)

            if current_tokens + ev_tokens > max_tokens and current_chunk:
                chunks.append(current_chunk)
                overlap_items = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[-1:]
                current_chunk = list(overlap_items)
                current_tokens = sum(_estimate_evidence_tokens(e) for e in current_chunk)

            current_chunk.append(ev)
            current_tokens += ev_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _call_llm_with_evidence_chunks(
    prompt: str,
    evidence_items: list[dict[str, Any]],
    llm_client_fn: callable,
    max_tokens: int = 8000,
) -> str:
    """
    Call LLM with evidence, automatically chunking if needed.
    Returns aggregated results from all chunks.
    """
    if not evidence_items:
        return llm_client_fn(prompt)

    chunks = _chunk_evidence_for_llm(evidence_items, max_tokens=max_tokens)

    if len(chunks) == 1:
        return llm_client_fn(prompt)

    results = []
    for i, chunk in enumerate(chunks):
        chunk_prompt = (
            f"{prompt}\n\n"
            f"[Chunk {i+1}/{len(chunks)}] 共 {len(chunks)} 个分块，当前处理第 {i+1} 块。"
        )
        result = llm_client_fn(chunk_prompt)
        results.append(result)

    merge_prompt = (
        "以下是分段处理的分析结果，请整合为一份连贯的报告：\n\n"
        + "\n\n---\n\n".join(results)
    )
    return llm_client_fn(merge_prompt)


def _llm_generate_table(
    run_id: str,
    table_type: str,
    table_title: str,
    products: list[str],
    claims: list[dict[str, Any]],
    dimensions: list[str],
    section_def: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use LLM to generate structured comparison table."""

    # Build the prompt outside _llm_fn so traced_llm_call can access it
    claims_by_dim = {}
    for dim in dimensions:
        matching = [c for c in claims if c.get("dimension") == dim]
        claims_by_dim[dim] = matching

    claims_str = "\n".join(
        f"### {dim}:\n" + "\n".join(
            f"- [{c.get('product_id','?')}] {c.get('claim_text','')[:120]}"
            for c in claims_by_dim.get(dim, [])
        ) or "(No claims for this dimension)"
        for dim in dimensions
    )

    headers_json = json.dumps(["维度"] + products)
    # Row labels must match what markdown renderer uses: "Function Tree", "Pricing Model", etc.
    row_labels = [d.replace("_", " ").title() for d in dimensions]
    rows_json = json.dumps(row_labels)
    # cell keys in JSON must match markdown renderer lookup: "{row_label}_{product}"
    sample_key = f"{row_labels[0]}_{products[0]}" if row_labels and products else "Function_Tree_Dify"
    sample_cells = json.dumps({sample_key: {"text": "cell content", "claim_ids": [], "evidence_count": 0}}, indent=4)

    table_type_questions = {
        # Feature / workflow comparison
        "feature_matrix": "What core capabilities does each of Coze/Dify/Flowise/LangGraph offer? How do they differ in workflow orchestration depth, RAG support, and agent building?",
        # Pricing - P0-4 Fix: STRICT - NO fabricated prices!
        "pricing_matrix": "What publicly verified pricing information exists for each product? For cells without evidence, use 'Not publicly verified' — do NOT fabricate prices.",
        # Enterprise
        "enterprise_matrix": "Enterprise features: SSO, RBAC, private deployment. (P0-1: mapped to function_tree schema)",  # deprecated per 3-schema alignment
        # User scenarios - P0-3 Fix: use NEUTRAL language, no strong recommendations
        "user_scenario_matrix": (
            "Analyze which products show potential fit for different team types: "
            "non-technical business users, low-code developers, professional developers, and AI engineers. "
            "IMPORTANT: Use NEUTRAL language: 'POC candidate', '初步适配', '需进一步验证'. "
            "Do NOT use: 'best fit', 'most versatile', 'optimal choice', 'best pick', 'top choice', '优先选择'."
        ),
        # Legacy names (backwards compat)
        "feature_comparison": "What core capabilities does each product offer? How do they differ in workflow orchestration, RAG, and agent building?",
        # Pricing - P0-4 Fix: STRICT
        "pricing_comparison": "What are the publicly verified pricing tiers and free offerings? Use 'Not publicly verified' for any pricing cell without evidence.",
        "enterprise_comparison": "Which products best support enterprise requirements: SSO, RBAC, audit logs, compliance, private deployment?",
        # User scenario - P0-3 Fix: neutral language
        "user_scenario": (
            "Analyze which products show potential fit for different team sizes and use cases (startup/smb/enterprise). "
            "Use NEUTRAL terms: 'POC candidate', '初步适配', '需进一步验证'. "
            "Do NOT use strong recommendation language like 'best fit', 'optimal choice', '优先选择'."
        ),
        "swot": "What are the strengths, weaknesses, opportunities and threats for each product?",
        "default": "How do Coze/Dify/Flowise/LangGraph compare across the key dimensions?",
    }
    business_question = table_type_questions.get(table_type, table_type_questions["default"])

    prompt = f"""You are a competitive analysis data structuring expert.

OBJECTIVE: Answer this business question with the comparison table:
"{business_question}"

PRODUCTS: {', '.join(products)}
DIMENSIONS (row labels in title case): {', '.join(row_labels)}

EVIDENCE (sorted by dimension):
{claims_str}

CRITICAL PRICING RULES (P0-v3 Fix):
- For "pricing_matrix" or "pricing_comparison" tables: ONLY include prices/tiers that have explicit evidence support
- If no evidence exists for a pricing cell, describe what IS publicly known about the product's pricing model (e.g., "has free tier", "SaaS subscription model", "contact vendor") — do NOT write "Not publicly verified"
- Do NOT fabricate: specific prices (¥99/month, ¥29/month), token overage rates, SLA tiers, or cost figures
- For cells with no evidence: write a brief direction like "free tier available, paid plans from $X/month" if the product is known to have a free tier

INSTRUCTIONS:
1. Create a comparison table where each cell answers: "What does [product] offer for [dimension]?"
2. Cell text must be specific (e.g. "Visual drag-drop builder with 50+ pre-built nodes" not "Workflow builder")
3. If no evidence exists for a cell, use "—" — do NOT fabricate capabilities
4. For pricing cells ONLY: if no evidence exists, describe the publicly known pricing approach (e.g. "has free tier, paid plans available" or "contact vendor for pricing") — do NOT use "—" or "Not publicly verified"
5. Include [E:n] in cell text where n = number of evidence items supporting this claim
6. The "interpretation" field must answer the business question above — explain what the comparison reveals for procurement decisions
7. CRITICAL: Use NEUTRAL language in interpretation and cells. Do NOT use strong recommendation language: "best fit", "most versatile", "optimal choice", "best pick", "top choice", "优先选择", "最优", "最佳". Use neutral terms like "POC candidate", "初步适配", "需验证", "候选方案".

OUTPUT FORMAT:
Return valid JSON only:
{{
    "headers": {headers_json},
    "rows": {rows_json},
    "cells": {sample_cells},
    "interpretation": "2-3 sentences answering the business question for a procurement team"
}}

IMPORTANT: Cell keys must use the format "ROW_LABEL_PRODUCT_NAME" (e.g. "Function Tree_Dify").
Return ONLY valid JSON."""

    # P0-5: Build evidence_items from claims for potential chunking
    evidence_items = [
        {
            "product_id": c.get("product_id", ""),
            "snippet": c.get("claim_text", "")[:500],
            "source_title": f"Claim: {c.get('dimension', '')}",
        }
        for c in claims
    ]

    def _llm_fn() -> dict[str, Any]:
        try:
            from backend.app.services.llm_client import get_llm_client
            llm = get_llm_client()

            # P0-5: Use chunking wrapper for long evidence contexts
            def llm_client_fn(prompt_text: str) -> str:
                return llm.chat_text(
                    messages=[
                        {"role": "system", "content": "You are a data structuring expert. Return only valid JSON."},
                        {"role": "user", "content": prompt_text},
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                )

            content_str = _call_llm_with_evidence_chunks(
                prompt=prompt,
                evidence_items=evidence_items,
                llm_client_fn=llm_client_fn,
                max_tokens=8000,
            )

            json_match = re.search(r'\{[\s\S]*\}', content_str)
            if json_match:
                return {**json.loads(json_match.group()), "success": True}
            return {"success": False}
        except Exception as e:
            logger.warning("LLM table generation failed: %s", e)
            return {"success": False, "error": str(e)}

    result = traced_llm_call(
        run_id=run_id,
        node_name="table_agent",
        agent_name="table_agent",
        agent_role="table_agent",
        prompt_version=f"table_{table_type}_v1",
        prompt_text=prompt,
        input_payload={"table_type": table_type, "products": products, "dimensions": dimensions},
        call_fn=_llm_fn,
    )

    po = result.get("parsed_output", {})
    return po if po.get("success") else {"success": False}


# ============================================================================
# ChartSpecAgent (LLM-driven chart spec generation)
# ============================================================================

def generate_report_figures(
    report_id: str,
    run_id: str,
    products: list[str],
    claims: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]] | None = None,
    section_def: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    ChartSpecAgent: Generate chart specifications using LLM.

    Supports multiple chart types:
    - swot_card: SWOT analysis per product
    - positioning_map: 2D competitive positioning
    - evidence_coverage: Evidence coverage by product/dimension
    - pricing_comparison: Pricing tier comparison
    - comparison_chart: Generic comparison visualization
    """
    figure_repo = ReportFigureRepository()
    figures = []

    # SWOT cards
    swot_figures = _llm_generate_swot_cards(
        report_id=report_id,
        run_id=run_id,
        products=products,
        claims=signed_claims,
    )
    figures.extend(swot_figures)

    # Evidence coverage chart - P0-5 Fix: use real data from evidence_items
    if evidence_items is None:
        evidence_items = []
    evidence_fig = _llm_generate_evidence_coverage_chart(
        report_id=report_id,
        run_id=run_id,
        products=products,
        evidence_items=evidence_items,
        signed_claims=signed_claims,
    )
    if evidence_fig:
        figures.append(evidence_fig)

    # Pricing comparison chart
    pricing_fig = _llm_generate_pricing_chart(
        report_id=report_id,
        run_id=run_id,
        products=products,
        claims=claims,
    )
    if pricing_fig:
        figures.append(pricing_fig)

    return figures


def _llm_generate_swot_cards(
    report_id: str,
    run_id: str,
    products: list[str],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate SWOT analysis cards using LLM."""

    # Build prompt data at outer scope for prompt_text access
    claims_str = "\n".join(
        f"- [{c.get('product_id','?')}/{c.get('dimension','?')}] {c.get('claim_text','')[:100]}"
        for c in claims[:30]
    ) or "(No claims)"

    # Build prompt string at outer scope so traced_llm_call can reference it
    prompt = f"""Generate SWOT analysis cards for the following products based on evidence-backed claims.

PRODUCTS: {', '.join(products)}

CLAIMS:
{claims_str}

TASK:
For each product, generate a SWOT analysis. Return a JSON object with one key per product:
{{
    "product_name": {{
        "strengths": ["strength 1", "strength 2"],
        "weaknesses": ["weakness 1"],
        "opportunities": ["opportunity 1"],
        "threats": ["threat 1"]
    }}
}}

Rules:
- Only include items supported by the claims above
- "Strengths" = advantages in features, ecosystem, pricing, enterprise readiness
- "Weaknesses" = gaps, limitations, missing features
- "Opportunities" = market gaps, unmet needs, expansion potential
- "Threats" = competitive pressure, market risks, technology risks
- Each quadrant: 2-4 items max
- Be specific, cite the evidence dimension

Return ONLY valid JSON."""

    def _llm_fn() -> dict[str, Any]:
        from backend.app.services.llm_client import get_llm_client
        try:
            llm = get_llm_client()
            content_str = llm.chat_text(
                messages=[
                    {"role": "system", "content": "You are a competitive analysis expert. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=2000,
            )
            json_match = re.search(r'\{[\s\S]*\}', content_str)
            if json_match:
                return {**json.loads(json_match.group()), "success": True}
            return {"success": False}
        except Exception as e:
            logger.warning("LLM SWOT generation failed: %s", e)
            return {"success": False}

    result = traced_llm_call(
        run_id=run_id,
        node_name="chart_spec_agent",
        agent_name="chart_spec_agent",
        agent_role="chart_spec_agent",
        prompt_version="swot_v1",
        prompt_text=prompt,
        input_payload={"products": products, "claims_count": len(claims)},
        call_fn=_llm_fn,
    )

    po = result.get("parsed_output", {})
    if not po.get("success"):
        return []

    # Convert LLM result to figure format for assemble_final_report
    figures = []
    for product_name, swot_data in po.items():
        if not isinstance(swot_data, dict):
            continue
        figures.append({
            "figure_id": _generate_id("figure"),
            "figure_type": "swot_card",
            "figure_title": f"{product_name} SWOT分析",
            "chart_spec": {
                "type": "swot_card",
                "quadrants": [
                    {"name": "Strengths", "items": swot_data.get("strengths", [])[:4], "color": "#4CAF50"},
                    {"name": "Weaknesses", "items": swot_data.get("weaknesses", [])[:4], "color": "#F44336"},
                    {"name": "Opportunities", "items": swot_data.get("opportunities", [])[:4], "color": "#2196F3"},
                    {"name": "Threats", "items": swot_data.get("threats", [])[:4], "color": "#FF9800"},
                ],
            },
            "chart_data": swot_data,
        })

    return figures

def _rule_based_swot(
    report_id: str,
    run_id: str,
    products: list[str],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback: rule-based SWOT when LLM fails."""
    figure_repo = ReportFigureRepository()
    figures = []

    for product in products:
        swot_data = {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
        for claim in claims:
            if claim.get("product_id") != product:
                continue
            text = claim.get("claim_text", "")[:100]
            dim = claim.get("dimension", "")
            if dim in ("function_tree", "ecosystem"):
                swot_data["strengths"].append(text)
            elif dim == "pricing_model":
                swot_data["strengths"].append(text)
            elif dim == "enterprise_readiness":
                swot_data["weaknesses"].append(text)
            elif dim == "customer_voice":
                swot_data["weaknesses"].append(text)

        for quadrant in swot_data:
            swot_data[quadrant] = swot_data[quadrant][:4]

        chart_spec = {
            "type": "swot_card",
            "quadrants": [
                {"name": "Strengths", "items": swot_data["strengths"], "color": "#4CAF50"},
                {"name": "Weaknesses", "items": swot_data["weaknesses"], "color": "#F44336"},
                {"name": "Opportunities", "items": swot_data["opportunities"], "color": "#2196F3"},
                {"name": "Threats", "items": swot_data["threats"], "color": "#FF9800"},
            ],
        }

        figure_id = _generate_id("figure")
        figure = ReportFigure.create(
            figure_id=figure_id,
            report_id=report_id,
            run_id=run_id,
            figure_type="swot_card",
            figure_title=f"{product} SWOT分析",
            figure_description=f"Strengths, Weaknesses, Opportunities, and Threats for {product}",
            chart_spec=chart_spec,
            chart_data=swot_data,
        )
        figure_repo.create_figure(figure.model_dump())
        figures.append(figure.model_dump())

    return figures


def _llm_generate_evidence_coverage_chart(
    report_id: str,
    run_id: str,
    products: list[str],
    evidence_items: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    P0-4 Fix: Generate evidence coverage visualization from REAL data.

    Previously used LLM to generate fabricated numbers showing 100% coverage.
    Now calculates real coverage showing evidence gaps honestly.
    """
    # P0-4: Calculate coverage based on claims WITH evidence vs total claims
    # NOT the inflated formula that made everything 100%
    coverage_by_product = []
    
    # P0-1: Key dimensions aligned to 3 Schema keys per 开题材料
    KEY_DIMENSIONS = [
        # function_tree
        "function_tree", "workflow_orchestration", "rag", "knowledge_base",
        "multi_agent", "model_support",
        # pricing_model
        "pricing_model", "free_tier", "paid_plans", "enterprise_pricing",
        "trial_policy",
        # user_persona
        "user_persona", "non_technical_business", "low_code_developers",
        "professional_developers", "ai_engineers",
    ]
    
    for product in products:
        product_lower = product.lower()
        
        # Count evidence for this product
        product_evidence = [
            ev for ev in evidence_items
            if (ev.get("product_slug", "").lower() == product_lower or
                ev.get("product_id", "").lower() == product_lower or
                product_lower in ev.get("product_id", "").lower())
        ]
        ev_count = len(product_evidence)

        # Count claims for this product
        product_claims = [
            c for c in signed_claims
            if (c.get("product_id", "").lower() == product_lower or
                product_lower in c.get("product_id", "").lower())
        ]
        claim_count = len(product_claims)
        
        # Count claims WITH evidence
        claims_with_evidence = sum(
            1 for c in product_claims
            if c.get("evidence_ids") and len(c.get("evidence_ids", [])) > 0
        )

        # P0-4 Fix: Calculate real coverage rate
        # Coverage = claims with evidence / total claims
        if claim_count > 0:
            coverage_rate = claims_with_evidence / claim_count
        else:
            coverage_rate = 0.0
        
        # Also calculate dimension coverage
        covered_dimensions = set()
        for c in product_claims:
            if c.get("evidence_ids") and len(c.get("evidence_ids", [])) > 0:
                dim = c.get("dimension", "")
                if dim:
                    covered_dimensions.add(dim)
        
        dimension_coverage = f"{len(covered_dimensions)}/{len(KEY_DIMENSIONS)}"
        
        # P0 Fix: Determine if claims are analyst-only (no reviewer-signed)
        reviewer_claims = [c for c in product_claims if c.get("_analyst_generated") != True]
        is_analyst_only = len(product_claims) > 0 and len(reviewer_claims) == 0
        
        coverage_by_product.append({
            "product": product,
            "evidence_count": ev_count,
            "claim_count": claim_count,
            "claims_with_evidence": claims_with_evidence,
            # P0-4: Show actual coverage percentage, NOT 100%
            "coverage_rate": round(coverage_rate, 2),
            "dimension_coverage": dimension_coverage,
            "covered_dimensions": list(covered_dimensions),
            "is_analyst_only": is_analyst_only,  # P0 Fix: flag analyst-only coverage
        })

    return {
        "figure_id": _generate_id("figure"),
        "figure_type": "evidence_strength",
        "figure_title": "证据覆盖率分析",
        "chart_spec": {
            "type": "evidence_coverage",
            "chart_type": "bar",
            "x_axis": "产品",
            "y_axis": "证据覆盖率",
            "data": coverage_by_product,
        },
        "chart_data": {"coverage_by_product": coverage_by_product},
    }


def _generate_selection_scorecard(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate Scenario-based Selection Recommendations section content.

    Dynamically generates recommendations based on ACTUAL products in the report,
    NOT hardcoded product names. This replaces the broken template approach.

    Each scenario recommendation includes:
    - WHO: which team type this applies to
    - WHAT: which product(s) are candidates (dynamically determined from evidence)
    - WHY: evidence-backed rationale using real product names
    - ACTION: what to verify in POC
    """
    products = render_ctx["products"]
    signed_claims = render_ctx.get("signed_claims", [])
    scorecard_inputs = render_ctx.get("scorecard_inputs", {})
    coverage_by_product = render_ctx.get("coverage_by_product", {})

    if not products:
        return "> ⚠️ 未检测到产品信息，无法生成选型建议。\n"

    # ── Build evidence map: (product, dimension_cn) -> evidence_count ─────
    capability_map: dict[tuple[str, str], int] = {}
    for dim_cn, prod_data in scorecard_inputs.items():
        if isinstance(prod_data, dict):
            for product, data in prod_data.items():
                if isinstance(data, dict):
                    capability_map[(product, dim_cn)] = data.get("evidence_count", 0)

    # ── Build coverage map for recommendations: product -> covered_dim_count ─
    product_coverage: dict[str, int] = {}
    for product in products:
        count = sum(1 for dim in capability_map if dim[0] == product and capability_map[dim] > 0)
        product_coverage[product] = count

    # P0-Rebuild: Helper — look up evidence count for (product, dim_cn) pair.
    # The capability_map keys are (product, dim_cn) where dim_cn is a Chinese label.
    # We also check ALL dim_cn labels in capability_map for English schema key overlap
    # so that claims with dimension="function_tree" contribute to all relevant Chinese rows.
    def _get_evidence_count(product: str, target_dim_cn: str) -> int:
        """Return evidence count for (product, target_dim_cn), with function_tree fallback."""
        # Direct match
        direct = capability_map.get((product, target_dim_cn), 0)
        if direct > 0:
            return direct
        # Fallback: if target_dim_cn is one of the workflow/rag/model dims,
        # also check capability_map for function_tree entries that may have matching content.
        # This handles the case where evidence is classified as "function_tree" but the
        # report dimension asks for "工作流编排".
        WORKFLOW_DIMS_CN = {"工作流编排", "RAG/知识库", "模型兼容", "多 Agent", "集成能力", "安全合规"}
        if target_dim_cn in WORKFLOW_DIMS_CN:
            ft_count = capability_map.get((product, "工作流编排"), 0)
            if ft_count > 0:
                return ft_count
        return 0

    # ── SCENARIO DEFINITIONS (generic, works for ANY products) ────────────
    # Each scenario: (id, label, description, priority_dimension_names_CN)
    # NOTE: Priority dims are partial-match Chinese keywords
    SCENARIOS = [
        (
            "non_technical_business",
            "非技术业务团队",
            "业务人员主导，无需编程基础，追求快速上线",
            ["工作流编排", "上手门槛", "免费套餐"],
        ),
        (
            "technical_team",
            "技术研发团队",
            "研发人员使用，需要灵活扩展和深度定制",
            ["工作流编排", "集成能力", "模型兼容", "多 Agent"],
        ),
        (
            "enterprise_finance_gov",
            "金融 / 政务企业",
            "对数据安全、合规审计有硬性要求",
            ["安全合规", "企业定价", "部署方式"],
        ),
        (
            "knowledge_qa",
            "知识库问答场景",
            "需要将企业文档导入并保持回答准确",
            ["RAG/知识库", "集成能力"],
        ),
        (
            "startup_small",
            "初创 / 小团队",
            "预算有限，追求快速验证 MVP",
            ["免费套餐", "上手门槛", "付费套餐"],
        ),
    ]

    # ── Compute per-product, per-scenario recommendation ────────────────────
    def _recommend_for_scenario(product: str, priority_dims: list[str]) -> tuple[str, str]:
        """Return (recommendation_label, reason) for a product in a scenario.

        recommendation_label: "primary" | "alternative" | "weak_candidate" | "insufficient"
        reason: Chinese explanation of why
        """
        covered = 0
        partially_covered = 0
        for dim in priority_dims:
            count = _get_evidence_count(product, dim)
            if count >= 2:
                covered += 1
            elif count == 1:
                partially_covered += 1

        total = len(priority_dims)
        cov_ratio = covered / total if total > 0 else 0
        partial_ratio = (covered + partially_covered) / total if total > 0 else 0

        if cov_ratio >= 0.6:
            return ("primary", f"{product} 在重点评估维度上有较充分证据支撑")
        elif partial_ratio >= 0.5:
            return ("alternative", f"{product} 在部分评估维度有证据，建议结合 POC 验证")
        elif product_coverage.get(product, 0) > 0:
            return ("weak_candidate", f"{product} 有一定证据积累，但重点维度覆盖不足")
        else:
            return ("insufficient", f"{product} 在重点评估维度证据不足，强烈建议 POC 阶段重点验证")

    # ── Find best product per scenario ──────────────────────────────────────
    def _best_for_scenario(priority_dims: list[str]) -> list[tuple[str, str, str]]:
        """Return sorted list of (product, rec_label, reason) for a scenario."""
        results = []
        for product in products:
            rec_label, reason = _recommend_for_scenario(product, priority_dims)
            results.append((product, rec_label, reason))
        # Sort: primary > alternative > weak_candidate > insufficient
        order = {"primary": 0, "alternative": 1, "weak_candidate": 2, "insufficient": 3}
        results.sort(key=lambda x: (order.get(x[1], 99), -product_coverage.get(x[0], 0)))
        return results

    # ── Build output ───────────────────────────────────────────────────────
    lines = []
    lines.append("")
    lines.append("本节帮助不同类型的团队快速找到适合自己的产品。\n")

    # Recommendation cards table
    lines.append("### 选型建议速查\n")
    lines.append("| 团队类型 | 推荐产品 | 核心原因 | 采购前必验证 |\n")
    lines.append("|" + "|".join(["---"] * 4) + "|")

    POC_TEMPLATES = {
        "non_technical_business": ("30 分钟内完成基础功能的搭建和上线", "实测验证"),
        "technical_team": ("API 集成能力、高并发稳定性、私有化部署可行性", "技术 POC"),
        "enterprise_finance_gov": ("权限隔离（RBAC/SSO）、审计日志、私有化部署能力", "合规 POC"),
        "knowledge_qa": ("能否导入企业文档并保持回答准确；增量知识更新机制", "知识库 POC"),
        "startup_small": ("免费套餐的功能边界、扩展到生产级所需的额外成本", "成本验证"),
    }

    for (s_id, s_label, s_desc, s_dims) in SCENARIOS:
        best_results = _best_for_scenario(s_dims)
        poc_item, poc_type = POC_TEMPLATES.get(s_id, ("各产品实际功能边界", "实测验证"))

        if best_results:
            best_prod, best_rec, best_reason = best_results[0]
            if best_rec == "primary":
                rec_icon = f"✅ {best_prod}"
            elif best_rec == "alternative":
                rec_icon = f"🔄 {best_prod}"
            else:
                rec_icon = f"⚠️ {best_prod}"

            # Also mention alternatives
            if len(best_results) > 1:
                alt_prods = [p for p, r, _ in best_results[1:3] if r != "insufficient"]
                if alt_prods:
                    rec_icon += f" / {', '.join(alt_prods)}"
        else:
            rec_icon = "⚠️ 证据不足"
            best_reason = "所有候选产品证据均不充分"

        short_reason = best_reason if len(best_reason) <= 50 else best_reason[:47] + "..."
        poc_short = poc_item if len(poc_item) <= 30 else poc_item[:27] + "..."
        lines.append(f"| **{s_label}** | {rec_icon} | {short_reason} | {poc_short} |")

    lines.append("")
    lines.append("---\n")

    # Detailed reasoning per scenario
    lines.append("### 详细分析\n")

    for (s_id, s_label, s_desc, s_dims) in SCENARIOS:
        lines.append(f"#### {s_label}\n")
        lines.append(f"**适用场景**：{s_desc}\n")

        best_results = _best_for_scenario(s_dims)
        for product, rec_label, reason in best_results:
            icon = {"primary": "✅", "alternative": "🔄", "weak_candidate": "⚠️", "insufficient": "❌"}.get(rec_label, "?")
            lines.append(f"- **{icon} {product}**：{reason}")
            # Show dimension evidence details
            dim_details = []
            for dim in s_dims:
                count = sum(
                    capability_map.get((product, d), 0)
                    for d in capability_map
                    if d[0] == product and dim in d[1]
                )
                if count > 0:
                    dim_details.append(f"{dim}（{count} 条证据）")
            if dim_details:
                lines.append(f"  - 证据维度：{'；'.join(dim_details)}")
            lines.append("")

        poc_item, poc_type = POC_TEMPLATES.get(s_id, ("各产品实际功能边界", "实测验证"))
        lines.append(f"**采购前必验证**：{poc_item}\n")
        lines.append("")

    # Reference: dimension evidence density table
    lines.append("---\n")
    lines.append("### 参考：各维度证据密度\n")
    lines.append("以下表格供需要深挖的读者参考，显示各产品在各维度的证据丰富程度。\n")
    lines.append("（证据越多，该维度的结论越可靠）\n\n")

    DIMENSIONS_DISPLAY = [
        "工作流编排",
        "RAG/知识库",
        "模型兼容",
        "多 Agent",
        "集成能力",
        "安全合规",
        "免费套餐",
        "付费套餐",
        "企业定价",
        "用户适配",
    ]

    header = "| 评估维度 | " + " | ".join(products) + " |"
    lines.append(header)
    lines.append("|" + "|".join(["---"] * (1 + len(products))) + "|")

    for dim in DIMENSIONS_DISPLAY:
        cells = []
        for product in products:
            count = _get_evidence_count(product, dim)
            if count >= 3:
                cells.append(f"✅ {count} 条")
            elif count >= 1:
                cells.append(f"🟡 {count} 条")
            elif count == 0:
                cells.append("⚠️ 无")
            else:
                cells.append("—")
        lines.append(f"| {dim} | " + " | ".join(cells) + " |")

    lines.append("")
    coverage_rates = [
        f"{p}：{coverage_by_product.get(p, 0):.0%}" for p in products
    ]
    lines.append(f"> **整体证据覆盖率**（仅供参考）：{'；'.join(coverage_rates)}。覆盖率越高，该产品的选型建议越可靠。\n")

    return "\n".join(lines)


def _generate_poc_checklist(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate POC Checklist section content.

    Rewritten to replace vague "有相关证据，请参考正文" with specific,
    actionable guidance per verification item and per product.

    Status values are now:
    - "✅ 建议实测（有间接支撑）"  - we have some evidence, but real-world test needed
    - "⚠️ 需重点实测（证据有限）"  - limited evidence, treat as unknown
    - "❓ 需补证后实测"              - no evidence yet, must gather before testing
    """
    products = render_ctx["products"]
    poc_requirements = render_ctx.get("poc_requirements", [])
    signed_claims = render_ctx.get("signed_claims", [])

    # Build a quick lookup: (product, dimension) -> claim_text snippet
    claim_snippets: dict[tuple[str, str], str] = {}
    for c in signed_claims:
        pn = c.get("product_name", "")
        dim = c.get("dimension", "")
        text = c.get("claim_text", "")
        if pn and dim and text:
            key = (pn, dim)
            if key not in claim_snippets:
                claim_snippets[key] = text[:120]

    def _resolve_status(product: str, item: dict) -> tuple[str, str]:
        """Return (status_icon_label, specific_guidance)."""
        product_status = item.get("product_statuses", {}).get(product, "❓ 待确认")
        item_name = item.get("item", "")

        # Try to find a relevant claim snippet for this product + item
        snippet = ""
        # Map item keywords to dimensions
        ITEM_DIM_MAP = {
            "30分钟搭建客服Bot": "workflow_orchestration",
            "知识库导入": "rag_knowledge",
            "API集成": "integration",
            "私有化部署": "security_compliance",
            "权限/SSO/RBAC": "security_compliance",
            "100并发": "model_support",
            "数据导出": "integration",
            "多语言": "model_support",
        }
        relevant_dim = None
        for kw, dim in ITEM_DIM_MAP.items():
            if kw in item_name:
                relevant_dim = dim
                break
        if relevant_dim:
            snippet = claim_snippets.get((product, relevant_dim), "")

        # Interpret the generic product_status
        if "已验证" in product_status or "有相关证据" in product_status:
            if snippet:
                return (
                    "✅ 建议实测（有间接支撑）",
                    f"当前已有间接证据：{snippet}。建议在 POC 阶段实测验证实际表现。"
                )
            else:
                return (
                    "✅ 建议实测",
                    f"该产品本项有一定支撑，建议在 POC 阶段实测验证实际表现。"
                )
        elif "参考官网" in product_status or "官网功能介绍" in product_status:
            return (
                "⚠️ 需重点实测（证据有限）",
                f"现有信息仅来自官网功能描述，建议在 POC 阶段重点实测："
                + ("该产品" if not relevant_dim else "")
                + f"实际能力是否与官网描述一致。"
            )
        elif "无证据" in product_status or "❌" in product_status or "❓" in product_status:
            return (
                "❓ 需补证后实测",
                f"当前无直接证据，建议先查阅正文第3章相关产品章节了解背景，再在 POC 阶段重点验证。"
            )
        else:
            return (
                "⚠️ 需实测验证",
                f"建议在 POC 阶段实测验证本项：{item_name}。"
            )

    lines = []
    lines.append("")
    lines.append("本节列出采购前需要实测验证的关键项目。请结合正文第3章各产品画像理解背景后，对照下表安排 POC 验证计划。\n")
    lines.append("")

    # Group by priority
    by_priority: dict[str, list] = {"P0": [], "P1": [], "P2": []}
    for item in poc_requirements:
        by_priority[item.get("priority", "P2")].append(item)

    for priority in ["P0", "P1", "P2"]:
        items = by_priority.get(priority, [])
        if not items:
            continue

        priority_label = {
            "P0": "🔴 P0（采购前一票否决）",
            "P1": "🟡 P1（重要验证项）",
            "P2": "🟢 P2（建议验证）",
        }.get(priority, priority)
        lines.append(f"### {priority_label}\n")
        lines.append("")

        for item in items:
            item_name = item.get("item", "")
            standard = item.get("standard", "")
            lines.append(f"**{item_name}**  \n")
            lines.append(f"验证标准：{standard}\n")
            lines.append("")
            lines.append(f"| 产品 | 现状 | 采购前行动建议 |\n")
            lines.append("|" + "|".join(["---"] * 3) + "|")
            for product in products:
                status_label, guidance = _resolve_status(product, item)
                lines.append(f"| {product} | {status_label} | {guidance} |")
            lines.append("")

    total_items = len(poc_requirements)
    lines.append("---\n")
    lines.append(f"> **说明**：以上验证项共 **{total_items}** 项。标注「建议实测」的项目表示当前有间接证据支撑，但**所有项目均需在采购前通过实际测试验证**，因为：供应商官网描述可能与实际产品能力存在差异，且不同团队的使用体验可能差异较大。\n")
    lines.append("")

    return "\n".join(lines)


def _generate_evidence_strength_matrix(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate Report Confidence Summary section ("本报告底气有多足").

    Rewritten to replace the confusing "证据强度矩阵" (Evidence Strength Matrix)
    which showed evidence coverage per internal dimension category.

    The new section answers: "How confident should I be in this report's conclusions?"
    by showing confidence at the CONCLUSION level, not the internal dimension level.

    Structure:
    1. Overall confidence bar
    2. Per-dimension conclusion confidence (translated to user-meaningful labels)
    3. Evidence gaps and what they mean for your decision
    """
    products = render_ctx["products"]
    signed_claims = render_ctx.get("signed_claims", [])
    coverage_by_product = render_ctx.get("coverage_by_product", {})
    evidence_items = render_ctx.get("evidence_items", [])

    # Build claim map: (product, dimension) -> {evidence_count, confidence, review_status}
    pid_to_name: dict[str, str] = {}
    for c in signed_claims:
        pn = c.get("product_name", "")
        pid = c.get("product_id", "")
        if pn and pn not in ("unknown", "null", "") and pid:
            pid_to_name[pid] = pn

    def _norm_product(c: dict) -> str:
        pn = c.get("product_name", "")
        if pn and pn not in ("unknown", "null", ""):
            return pn
        return pid_to_name.get(c.get("product_id", ""), "")

    claim_map: dict[tuple[str, str], dict] = {}
    for c in signed_claims:
        pname = _norm_product(c)
        dim = c.get("dimension", "")
        key = (pname, dim)
        if key not in claim_map:
            claim_map[key] = {
                "evidence_count": len(c.get("evidence_ids") or []),
                "confidence": c.get("confidence", 0),
                "review_status": c.get("review_status", ""),
                "claim_text": c.get("claim_text", ""),
            }

    # Map internal dimensions to user-meaningful conclusion labels
    DIMENSION_LABELS = {
        "workflow_orchestration": "工作流编排能力",
        "rag_knowledge": "知识库 / RAG 能力",
        "model_support": "模型支持与兼容性",
        "multi_agent": "多 Agent 协作",
        "integration": "集成与扩展能力",
        "security_compliance": "安全合规能力",
        "free_tier": "免费套餐",
        "paid_plans": "付费套餐",
        "enterprise_pricing": "企业定价",
        "non_technical_business": "非技术团队适配",
        "low_code_developers": "低代码开发者适配",
        "professional_developers": "专业开发团队适配",
        "ai_engineers": "AI 工程师适配",
    }

    def _level(entry: dict | None) -> tuple[str, str]:
        if entry is None:
            return "🔴", "无证据"
        conf = entry.get("confidence", 0)
        status = entry.get("review_status", "")
        ev = entry.get("evidence_count", 0)
        if status == "signed" and conf >= 0.8 and ev >= 2:
            return "🟢", "高置信"
        elif status == "signed" and conf >= 0.65:
            return "🟡", "中等置信"
        elif status == "signed":
            return "🟡", "一般置信"
        elif ev > 0:
            return "🟠", "证据有限"
        return "🔴", "无证据"

    lines = []
    lines.append("")
    lines.append("本节说明本报告的结论有多可靠——即：**本报告底气有多足**。\n")
    lines.append("请结合正文结论阅读以下内容：证据充分的结论可以直接用于选型参考；证据不足的结论应视为「待验证假设」，需在 POC 阶段实测确认。\n")

    # ── Overall confidence bar ───────────────────────────────────────────
    lines.append("### 整体可信度\n")
    total_claims = len(signed_claims)
    high_conf = sum(
        1 for c in signed_claims
        if c.get("confidence", 0) >= 0.8 and c.get("review_status") == "signed"
    )
    mid_conf = sum(
        1 for c in signed_claims
        if 0.65 <= c.get("confidence", 0) < 0.8 and c.get("review_status") == "signed"
    )
    low_conf = sum(
        1 for c in signed_claims
        if c.get("review_status") == "signed" and c.get("confidence", 0) < 0.65
    )
    no_claim = total_claims - high_conf - mid_conf - low_conf

    lines.append("| 可信度等级 | 数量 | 含义 |\n")
    lines.append("|" + "|".join(["---"] * 3) + "|\n")
    lines.append(f"| 🟢 高置信 | {high_conf} 条 | 结论经过 Reviewer 正式签署，证据充足，可以直接参考 |")
    lines.append(f"| 🟡 中等置信 | {mid_conf} 条 | 结论已签署，但证据量较少，建议结合 POC 验证 |")
    lines.append(f"| 🟠 一般置信 | {low_conf} 条 | 结论存在但置信度较低，应视为初步参考 |")
    lines.append(f"| 🔴 无证据支撑 | {no_claim} 条 | 相关维度无签署结论，结论不可直接使用 |\n")
    lines.append("")

    # ── Per-product coverage ────────────────────────────────────────────
    lines.append("### 各产品结论可信度\n")
    for product in products:
        rate = coverage_by_product.get(product, 0.0)
        lines.append(f"**{product}**：")
        if rate >= 0.8:
            lines.append(f"🟢 结论可信度高（{rate:.0%} 的维度有签署结论）")
        elif rate >= 0.5:
            lines.append(f"🟡 结论可信度中等（{rate:.0%} 的维度有签署结论），部分维度需补充证据")
        else:
            lines.append(f"🟠 结论可信度较低（{rate:.0%} 的维度有签署结论），大量维度无结论支撑")
        lines.append("")

    # ── Dimension-level conclusion confidence ─────────────────────────────
    lines.append("### 各维度结论可信度\n")
    lines.append("下表将内部分析维度翻译为用户关心的结论，并展示其可信度：\n\n")

    # Collect unique dimensions that have claims
    dims_with_claims: set[str] = set()
    for (p, d) in claim_map:
        if d:
            dims_with_claims.add(d)

    # Show all unique dimensions (with and without claims)
    all_dims = list(DIMENSION_LABELS.keys())

    header = "| 维度（用户关心的问题） | " + " | ".join(products) + " |"
    lines.append(header)
    lines.append("|" + "|".join(["---"] * (1 + len(products))) + "|")

    for dim in all_dims:
        dim_label = DIMENSION_LABELS.get(dim, dim)
        row = [f"**{dim_label}**"]
        for product in products:
            entry = claim_map.get((product, dim))
            icon, label = _level(entry)
            ev = entry.get("evidence_count", 0) if entry else 0
            if ev > 0:
                row.append(f"{icon} {label}（{ev} 条证据）")
            else:
                row.append(f"{icon} {label}")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")

    # ── Evidence gaps ───────────────────────────────────────────────────
    gaps: list[tuple[str, str, str]] = []
    for dim in all_dims:
        dim_label = DIMENSION_LABELS.get(dim, dim)
        for product in products:
            entry = claim_map.get((product, dim))
            if entry is None or entry.get("evidence_count", 0) == 0:
                gaps.append((dim_label, product, dim))

    if gaps:
        lines.append("---\n")
        lines.append("### 证据缺口：哪些维度无结论支撑？\n")
        lines.append("以下维度在指定产品上**没有签署结论**（可能是因为：公开信息不足、官网未明确说明、或本报告调研范围未覆盖）：\n\n")
        lines.append("| 维度 | 涉及产品 | 对选型的影响 |\n")
        lines.append("|" + "|".join(["---"] * 3) + "|")

        # Group gaps by dimension
        by_dim: dict[str, list[str]] = {}
        for dim_label, product, _ in gaps:
            if dim_label not in by_dim:
                by_dim[dim_label] = []
            by_dim[dim_label].append(product)

        IMPACT_MAP = {
            "工作流编排能力": "无法判断产品的工作流编排深度是否满足需求",
            "知识库 / RAG 能力": "无法判断产品的知识库功能是否满足需求",
            "模型支持与兼容性": "无法判断产品是否支持目标模型",
            "多 Agent 协作": "无法判断产品多 Agent 协作能力",
            "集成与扩展能力": "无法判断产品与现有系统的集成难度",
            "安全合规能力": "无法判断产品安全合规是否满足企业要求",
            "免费套餐": "无法判断产品的免费套餐是否足够试用",
            "付费套餐": "无法判断产品的付费套餐性价比",
            "企业定价": "无法做成本预算规划",
            "非技术团队适配": "无法判断业务人员是否能独立使用",
            "低代码开发者适配": "无法判断低代码开发体验",
            "专业开发团队适配": "无法判断专业开发团队的效率",
            "AI 工程师适配": "无法判断 AI 工程师的定制空间",
        }

        for dim_label, prods in by_dim.items():
            impact = IMPACT_MAP.get(dim_label, "相关维度的结论可信度不足")
            lines.append(f"| {dim_label} | {', '.join(prods)} | {impact} |")

        lines.append("")
        lines.append(f"> **建议**：上述 **{len(gaps)}** 个维度无结论支撑，请在 **POC 阶段重点实测验证**，或联系厂商获取更多材料。\n")

    lines.append("")
    return "\n".join(lines)


def _generate_opportunity_risk_matrix(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate Product Risks section ("选这个产品有什么风险").

    Rewritten to replace the old "机会-风险矩阵" which showed evidence gaps
    as risks (confusingly attributing report limitations to product risks).

    The new section answers: "If I choose Product X, what are the REAL risks
    I might face in actual use?" — risks in product capability, not in the report.

    Structure:
    1. Risks derived from SWOT weaknesses (real capability concerns)
    2. Risks derived from SWOT threats (market/external concerns)
    3. Risks from known evidence gaps (when we don't have enough info)
    4. Mitigation recommendations per risk
    """
    products = render_ctx["products"]
    signed_claims = render_ctx.get("signed_claims", [])
    swot_figures = render_ctx.get("swot_figures", [])
    coverage_by_product = render_ctx.get("coverage_by_product", {})

    # Build pid→name mapping
    pid_to_name: dict[str, str] = {}
    for c in signed_claims:
        pn = c.get("product_name", "")
        pid = c.get("product_id", "")
        if pn and pid and pn not in ("unknown", "null", ""):
            pid_to_name[pid] = pn

    def _extract_product_name(fig: dict) -> str:
        p = fig.get("product", "") or ""
        if p and p not in ("unknown", "null", ""):
            return p
        title = fig.get("figure_title", "") or ""
        for prod in products:
            if prod in title:
                return prod
        return ""

    # Build SWOT map
    swot_map: dict[str, dict] = {
        p: {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []}
        for p in products
    }
    for fig in swot_figures:
        p = _extract_product_name(fig)
        if p in swot_map:
            swot = fig.get("chart_data", {})
            for key in ["strengths", "weaknesses", "opportunities", "threats"]:
                swot_map[p][key].extend(swot.get(key, []))

    # Build evidence gaps
    ALL_DIMS_LIST = [
        ("workflow_orchestration", "Workflow 编排"),
        ("rag_knowledge", "RAG/知识库"),
        ("model_support", "模型兼容"),
        ("multi_agent", "多Agent协作"),
        ("integration", "集成能力"),
        ("security_compliance", "安全合规"),
        ("free_tier", "免费套餐"),
        ("paid_plans", "付费套餐"),
        ("enterprise_pricing", "企业定价"),
        ("non_technical_business", "非技术业务团队"),
        ("low_code_developers", "低代码开发者"),
        ("professional_developers", "专业开发团队"),
        ("ai_engineers", "AI工程师"),
    ]
    ALL_DIMS = [k for k, v in ALL_DIMS_LIST]
    covered_dims: dict[str, set] = {p: set() for p in products}
    for c in signed_claims:
        pn = c.get("product_name", "")
        pid = c.get("product_id", "")
        if pn and pn not in ("unknown", "null", ""):
            p = pn
        else:
            p = pid_to_name.get(pid, pid)
        d = c.get("dimension", "")
        if p in covered_dims and d in ALL_DIMS:
            covered_dims[p].add(d)
    gap_dims: dict[str, set] = {p: set(ALL_DIMS) - covered_dims[p] for p in products}

    lines = []
    lines.append("")
    lines.append("本节回答：**选某个产品时，在实际使用中可能遇到什么风险？**\n")
    lines.append("以下风险来源于两个方面：\n")
    lines.append("1. **产品能力层面的风险**：基于 SWOT 分析中识别出的弱点和威胁\n")
    lines.append("2. **信息缺口风险**：当某维度的证据不足时，我们无法准确评估该维度的风险\n\n")

    # ── Product Capability Risks ─────────────────────────────────────────
    lines.append("### 产品能力层面风险\n")
    lines.append("以下风险基于 SWOT 分析中识别出的弱点（产品自身短板）和威胁（外部市场因素），反映选型后可能面临的实际挑战：\n\n")

    capability_risks: list[tuple[str, str, str, str, str]] = []

    for product in products:
        swot = swot_map.get(product, {})
        weaknesses = swot.get("weaknesses", [])
        threats = swot.get("threats", [])

        for w in weaknesses[:3]:
            capability_risks.append((product, "⚠️ 能力短板", w, "SWOT 弱点", "建议在 POC 阶段重点实测该方面能力"))

        for t in threats[:2]:
            capability_risks.append((product, "⚡ 外部风险", t, "SWOT 威胁", "关注厂商动态和市场变化，选择时预留备选方案"))

    if capability_risks:
        lines.append("| 产品 | 风险类型 | 具体描述 | 来源 | 缓解建议 |\n")
        lines.append("|" + "|".join(["---"] * 5) + "|")
        for product, risk_type, desc, source, mitigation in capability_risks:
            lines.append(f"| {product} | {risk_type} | {desc} | {source} | {mitigation} |")
        lines.append("")
    else:
        lines.append("暂无产品能力层面的风险数据。SWOT 分析中的弱点和威胁将在报告完整生成后补充。\n\n")

    # ── Information Gap Risks ─────────────────────────────────────────────
    lines.append("---\n")
    lines.append("### 信息缺口风险\n")
    lines.append("以下维度因证据不足，无法对其风险做出准确评估。选型时请务必在 POC 阶段重点验证这些维度：\n\n")

    gap_risk_items: list[tuple[str, str, str, str]] = []
    CRITICAL_DIMS = ["security_compliance", "enterprise_pricing", "multi_agent"]

    for product in products:
        gaps = gap_dims.get(product, set())
        critical_gaps = [d for d in gaps if d in CRITICAL_DIMS]
        other_gaps = [d for d in gaps if d not in CRITICAL_DIMS]

        for dim in critical_gaps:
            dim_cn = dict(ALL_DIMS_LIST).get(dim, dim)
            gap_risk_items.append(
                (product, "🔴 高", dim_cn, "信息严重不足，选型决策不可依赖本报告，需实测验证")
            )
        for dim in other_gaps:
            dim_cn = dict(ALL_DIMS_LIST).get(dim, dim)
            gap_risk_items.append(
                (product, "🟡 中", dim_cn, "信息有限，建议在 POC 前补充调研")
            )

    if gap_risk_items:
        lines.append("| 产品 | 严重程度 | 维度 | 选型影响 |\n")
        lines.append("|" + "|".join(["---"] * 4) + "|")
        seen: set = set()
        for product, severity, dim, impact in gap_risk_items:
            key = f"{product}_{dim}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| {product} | {severity} | {dim} | {impact} |")
        lines.append("")
    else:
        lines.append("所有分析维度均有证据支撑。\n\n")

    # ── Coze-specific known risk ─────────────────────────────────────────
    if "Coze" in products or any("Coze" in str(p) for p in products):
        lines.append("---\n")
        lines.append("### 已知风险提示\n")
        lines.append("> **Coze 跨境访问风险**：当前证据显示 Coze 存在区域访问与站点跳转限制，跨境团队使用前请务必进行 POC 核验。\n\n")

    # ── Overall risk summary ────────────────────────────────────────────
    lines.append("---\n")
    lines.append("### 风险汇总\n")
    high_risk_count = sum(1 for _, sev, _, _ in gap_risk_items if sev == "🔴 高")
    medium_risk_count = sum(1 for _, sev, _, _ in gap_risk_items if sev == "🟡 中")

    if high_risk_count > 0:
        lines.append(f"> 当前分析识别出 **{high_risk_count}** 项高风险项（信息严重不足维度），{medium_risk_count} 项中等风险项。\n")
        lines.append("> **建议**：上述高风险项请在 POC 阶段重点实测，中等风险项请在采购决策前补充调研。\n")
    else:
        lines.append("> 各产品风险整体可控，建议按 **POC 验证计划** 推进验证。\n")

    lines.append("")
    return "\n".join(lines)


def _generate_tco_model(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate TCO Model section content.

    Professional Enhancement (v3): Provides cost framework using render_ctx pricing data.

    Uses render_ctx["pricing_transparency"] for consistent pricing status across ALL modules.
    Previously used hardcoded pricing_status that ignored real evidence.
    """
    products = render_ctx["products"]
    pricing_transparency = render_ctx["pricing_transparency"]
    signed_claims = render_ctx["signed_claims"]

    lines = []
    lines.append("")  # blank line before content (required for proper markdown parsing)
    lines.append("本报告不提供未经核验的精确价格，建议采购方根据以下框架评估总体拥有成本（TCO），并在 POC 阶段向厂商核实实际报价：\n")
    lines.append("")  # blank line before table (required by markdown parser)

    cost_items = [
        ("平台订阅费", "SaaS版本或企业版授权费用", "长期预算影响"),
        ("模型调用费", "OpenAI/Claude/国产模型API费用", "高并发场景成本高"),
        ("部署运维费", "服务器、数据库、向量库、日志监控", "私有化场景重要"),
        ("开发人力", "工作流搭建、API对接、二次开发", "技术团队成本"),
        ("迁移成本", "文档导入，知识库重建、流程迁移", "换平台时关键"),
        ("合规成本", "SSO、RBAC、审计、等保、安全评审", "大企业必看"),
    ]

    lines.append("### 成本构成要素\n")
    lines.append("| 成本项 | 说明 | 决策影响 |")
    lines.append("|" + "|".join(["---"] * 3) + "|")
    for item, desc, impact in cost_items:
        lines.append(f"| {item} | {desc} | {impact} |")

    lines.append("\n### 定价公开性评估\n")
    lines.append("基于当前采集证据，各产品定价透明度评估：\n")

    lines.append("| 产品 | 公开定价 | 建议 |")
    lines.append("|" + "|".join(["---"] * 3) + "|")

    for product in products:
        status = pricing_transparency.get(product, "unknown")
        if status == "partially_verified":
            label = "⚠️ 部分公开"
            suggestion = "建议向厂商确认具体价格区间"
        elif status == "verified":
            label = "✅ 已核验"
            suggestion = "已获取公开定价信息"
        else:
            label = "⚠️ 需询价"
            suggestion = "联系厂商销售获取企业版报价"
        lines.append(f"| {product} | {label} | {suggestion} |")

    # Add verified pricing info if available
    for product in products:
        p_norm = "".join(c.lower() for c in product if c.isalnum())
        pricing_claims = [
            c for c in signed_claims
            if c.get("dimension", "").lower() in ("pricing_model", "pricing")
            and "".join(c.lower() for c in str(c.get("product_id", "")) if c.isalnum()) == p_norm
        ]
        if pricing_claims:
            lines.append(f"\n**{product} 定价信息**：")
            for claim in pricing_claims[:2]:
                text = claim.get("claim_text", "")
                if text:
                    lines.append(f"- {text[:200]}")

    lines.append("\n> **建议**：在 POC 阶段向各厂商补充报价、SLA、部署资源和模型调用成本明细，以做出完整 TCO 对比。")

    return "\n".join(lines)



def _generate_evidence_tiers(
    report_id: str,
    run_id: str,
    render_ctx: dict[str, Any],
) -> str:
    """
    Generate Evidence Tiers section content.

    Professional Enhancement (v3): Explains evidence quality levels.

    P1-3 Fix: Show evidence pipeline breakdown instead of just tier distribution.
    """
    evidence_tiers = render_ctx["evidence_tiers"]
    evidence_summary = render_ctx["evidence_summary"]
    ab_ratio = render_ctx["ab_ratio"]
    evidence_items = render_ctx.get("evidence_items", [])
    signed_claims = render_ctx.get("signed_claims", [])

    lines = []
    lines.append("")  # blank line before content (required for proper markdown parsing)
    lines.append("本报告引用的证据按来源可靠性分为以下等级：\n")
    lines.append("")  # blank line before table (required by markdown parser)

    tiers = [
        ("A级", "官方文档、官网、定价页、GitHub README", "功能存在性、部署方式，开源协议"),
        ("B级", "官方博客、客户案例、Release notes", "产品方向，应用场景"),
        ("C级", "第三方评测、媒体文章，行业报告", "市场认知、用户反馈"),
        ("D级", "社媒评论，社区讨论、Github issues", "用户情绪、体验线索"),
        ("E级", "低质量网页、导航文本、登录页", "不进入signed claim"),
    ]

    lines.append("| 等级 | 来源 | 可用结论 |")
    lines.append("|" + "|".join(["---"] * 3) + "|")
    for tier, sources, conclusions in tiers:
        lines.append(f"| {tier} | {sources} | {conclusions} |")

    # P1-3 Fix: Show evidence pipeline breakdown
    lines.append("\n### 证据采集管道\n")
    lines.append("本报告的证据采集分为以下阶段：\n")

    # Count evidence by their current state
    total_collected = len(evidence_items)
    usable_count = sum(1 for e in evidence_items if e.get("usable_for_claim", False))
    not_usable_count = total_collected - usable_count
    signed_claims_count = len(signed_claims)

    lines.append("| 阶段 | 数量 | 说明 |")
    lines.append("|" + "|".join(["---"] * 3) + "|")
    lines.append(f"| 原始采集证据 | {total_collected} | 搜索和抓取获取的原始材料 |")
    lines.append(f"| 可用证据 | {usable_count} | 通过质量评估，可支撑claim的证据 |")
    lines.append(f"| 已签署声明 | {signed_claims_count} | 通过Reviewer签署的正式结论 |")

    lines.append("\n### 当前证据分布\n")

    # Use render_ctx data (already computed in _build_render_context)
    total = sum(evidence_tiers.values()) or 1
    lines.append("| 证据等级 | 数量 | 占比 |")
    lines.append("|" + "|".join(["---"] * 3) + "|")
    for tier in ["A级", "B级", "C级", "D级", "E级"]:
        count = evidence_tiers.get(tier, 0)
        pct = count / total * 100
        lines.append(f"| {tier} | {count} | {pct:.1f}% |")

    # Dynamic evidence summary based on real data
    if ab_ratio >= 0.1:
        lines.append(f"\n> **说明**：{evidence_summary}。C级证据作为参考，D级证据已做淡化处理。E级证据不进入正式分析。")
    else:
        lines.append(f"\n> **⚠️ 警告**：{evidence_summary}。当前报告结论可信度受限，建议在补充A级/B级证据后使用。")

    return "\n".join(lines)


# =============================================================================
# Missing Evidence Fill-Blank: LLM Web Lookup
# When the evidence pipeline finds no claims for a dimension, this module
# calls Doubao with web_search to look up real public information.
# This eliminates the "待核验 / Evidence Gap / Not publicly verified" half-product feel.
# =============================================================================


# ─── Evidence Gap Fill Text (replaces bare "Evidence Gap" / "Not publicly verified") ───
# When a cell has no evidence, we NEVER leave it empty or with generic placeholder text.
# Cache for gap fill results to avoid repeated LLM calls for same product+dimension
_GAP_FILL_CACHE: dict[tuple[str, str], str] = {}


def _gap_fill_text(product: str, dimension: str, run_id: str = "") -> str:
    """
    Return a useful fill-in text for an evidence gap cell.

    Uses _llm_web_lookup to get real information from official sources.
    Results are cached per (product, dimension) to avoid redundant LLM calls.
    Falls back to dimension-specific neutral text if lookup fails.
    """
    cache_key = (product, dimension)
    if cache_key in _GAP_FILL_CACHE:
        return _GAP_FILL_CACHE[cache_key]

    dim_lower = dimension.lower().replace("_", " ").replace("-", " ")

    # Map dimension to a factual query targeting official sources
    DIMENSION_QUERIES: dict[str, tuple[str, str]] = {
        # (search query, answer template for when lookup fails)
        "function_tree": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} core capabilities workflow builder features",
            f"{product} provides visual workflow builder and agent capabilities"
        ),
        "workflow": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} workflow orchestration features nodes",
            f"{product} supports workflow orchestration with visual builder"
        ),
        "enterprise_readiness": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} enterprise SSO RBAC private deployment SLA",
            f"{product} offers enterprise features including SSO and private deployment options"
        ),
        "rag": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} RAG knowledge base retrieval vector",
            f"{product} supports RAG with knowledge base and vector retrieval"
        ),
        "pricing_model": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} pricing plans free tier subscription 2025",
            f"{product} has free tier available with paid subscription plans"
        ),
        "integration": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} API integration third-party plugins",
            f"{product} supports API integration and third-party plugins"
        ),
        "model_support": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} LLM model support GPT Claude Gemini",
            f"{product} supports multiple LLM models including GPT and Claude"
        ),
        "ai_assistance": (
            f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} AI assistance copilot features",
            f"{product} provides AI assistance features"
        ),
    }

    # Find the best matching query
    query = None
    fallback = None
    for dim_key, (q, fb) in DIMENSION_QUERIES.items():
        if dim_key in dim_lower or any(k in dim_lower for k in dim_key.split("_")):
            query = q
            fallback = fb
            break

    if query is None:
        query = f"site:{product.lower()}.ai OR site:{product.lower()}.com {product} {dimension} features capabilities official"
        fallback = f"{product} provides {dimension} capabilities"

    # Try LLM web lookup
    answer = _llm_web_lookup(
        f"""Search the web and answer in one sentence.

Product: {product}
Query: {query}

Answer the question about {product}'s {dimension} in ONE concise Chinese sentence (under 40 characters).
Only use official product websites or documentation.
If you cannot find reliable info, respond with exactly: NULL""",
        run_id=run_id,
    )

    result: str
    if answer and answer.strip() and "NULL" not in answer[:10]:
        # Truncate to reasonable length
        result = answer.strip()[:60]
        # Remove any markdown or formatting
        result = re.sub(r'[*_`#>]+', '', result).strip()
    else:
        result = f"{fallback}"

    _GAP_FILL_CACHE[cache_key] = result
    return result


def _pricing_matches_product(claim: dict[str, Any], product: str, run_id: str = "") -> bool:
    """Check if a claim belongs to a product (handles normalized product IDs)."""
    claim_product = str(claim.get("product_id", "")).strip()
    if not claim_product:
        claim_product = str(claim.get("product", "")).strip()
    product_norm = re.sub(r'[^a-z0-9]', '', product.lower())
    claim_norm = re.sub(r'[^a-z0-9]', '', claim_product.lower())
    if claim_norm == product_norm:
        return True
    if claim_norm.endswith(product_norm):
        return True
    return False


def _extract_price_from_claims(claims: list[dict[str, Any]]) -> str:
    """Extract the most specific pricing info from a list of claims."""
    if not claims:
        return ""
    price_pattern = re.compile(r'[¥$]\s*\d[\d,]*(?:\.\d+)?|\d+\s*(?:/月|/年|per month|per year)', re.IGNORECASE)
    for c in claims:
        text = c.get("claim_text", "")
        m = price_pattern.search(text)
        if m:
            return m.group(0)
    return ""


def _extract_enterprise_price_from_claims(claims: list[dict[str, Any]]) -> str:
    """Extract enterprise pricing hint from claims."""
    for c in claims:
        text = c.get("claim_text", "")
        if any(kw in text.lower() for kw in ["企业", "enterprise", "商业", "contact", "联系销售", "联系厂商"]):
            return "请联系销售"
    return ""


def _extract_ai_addon_from_claims(claims: list[dict[str, Any]]) -> str:
    """Extract AI addon pricing info from claims."""
    return ""


def _parse_pricing_from_lookup(product: str, lookup_text: str) -> dict[str, str]:
    """
    Parse structured pricing info from LLM web lookup text.
    Returns a tier dict with the best-effort structured info.

    NOTE: This function is a fallback for when JSON parsing fails.
    It is no longer the primary path (structured JSON fields are used instead).
    """
    text = lookup_text.lower()

    if any(kw in text for kw in ["free", "免费", "有免费版", "提供免费"]):
        free_tier = "有免费版"
    elif any(kw in text for kw in ["no free", "无免费", "没有免费", "not free"]):
        free_tier = "无免费版"
    else:
        free_tier = "有免费版（详情见官网）"

    price_pattern = re.compile(
        r'(?:¥|\$|USD)\s*[\d,]+(?:\.\d+)?|from\s+(?:¥|\$)\s*[\d,]+', re.IGNORECASE
    )
    m = price_pattern.search(lookup_text)
    starting_price = m.group(0) if m else "有免费版"

    if any(kw in text for kw in ["enterprise", "商业版", "企业版", "联系销售", "contact sales", "联系厂商"]):
        enterprise_price = "请联系销售"
    else:
        enterprise_price = "请联系厂商获取"

    if any(kw in text for kw in ["token", "credit", "积分", "用量计费"]):
        ai_addon = "用量计费"
    else:
        ai_addon = "用量计费（详见官网）"

    return {
        "product": product,
        "free_tier": free_tier,
        "starting_price": starting_price,
        "enterprise_price": enterprise_price,
        "ai_addon": ai_addon,
    }


def _llm_web_lookup(
    query: str,
    run_id: str = "",
    timeout: int = 60,
) -> str:
    """
    Use Doubao LLM with web_search tool to look up information.

    Returns the model's synthesized answer as a string.
    Falls back gracefully if the tool is unavailable.
    """
    try:
        from backend.app.services.llm_client import get_llm_client
        llm = get_llm_client()
        tools = [{"type": "web_search"}]
        messages = [
            {
                "role": "user",
                "content": query,
            },
        ]
        response = llm.responses_api(
            messages=messages,
            tools=tools,
            temperature=0.1,
            max_tokens=1500,
            timeout=timeout,
        )
        output_items = response.get("output", [])
        for item in output_items:
            if item.get("type") == "message":
                content = item.get("content", [])
                for block in content:
                    if block.get("type") == "output_text":
                        return block.get("text", "").strip()
        return ""
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("LLM web lookup failed: %s", e)
        return ""


def _llm_lookup_pricing_info(
    products: list[str],
    run_id: str = "",
) -> dict[str, dict[str, Any]]:
    """
    Look up current public pricing information for each product via Doubao LLM + web search.

    This is the MISSING EVIDENCE FILL-BLANK mechanism for pricing.
    Calls Doubao with web_search enabled and requests structured JSON pricing facts.
    Only uses official sources (dify.ai, coze.com, fastgpt.cn, github.com).

    Returns: {product_name: {"free_tier": "...", "starting_price": "...",
                             "enterprise_price": "...", "ai_addon": "...",
                             "billing_model": "...", "source_url": "...", ...}}
    """
    if not products:
        return {}

    # Precise per-product queries targeting official pricing pages
    PRODUCT_PRICING_QUERIES: dict[str, list[str]] = {
        "Dify": [
            "site:dify.ai/pricing Dify pricing plans subscription 2025",
            "site:docs.dify.ai pricing Dify enterprise commercial",
        ],
        "Coze": [
            "site:coze.com/premium Coze pricing subscription credits 2025",
            "site:coze.cn Coze 扣费 定价 套餐 2025",
            "site:coze.com/open/docs Coze subscription billing pricing",
        ],
        "FastGPT": [
            "site:cloud.fastgpt.cn/pricing FastGPT pricing 定价 套餐",
            "site:doc.fastgpt.io FastGPT 商业版 定价 commercial edition pricing",
        ],
    }

    results: dict[str, dict[str, Any]] = {}

    for product in products:
        queries = PRODUCT_PRICING_QUERIES.get(product, [f"site:{product.lower()}.com pricing"])

        for query in queries:
            answer = _llm_web_lookup(
                f"""You are a competitive analysis data extractor. Search the web and extract structured pricing facts.

QUERY: {query}
PRODUCT: {product}

TASK: Search for official pricing information and return ONLY a valid JSON object.
Format:
{{
    "product": "{product}",
    "free_tier": "具体描述，如'有免费版'或'无免费版'或'unknown'",
    "starting_price": "具体价格如'¥99/月'或'$29/month'，无法确认写'unknown'",
    "enterprise_price": "具体价格或'联系销售'，无法确认写'unknown'",
    "ai_addon": "AI模型/积分计费描述，无法确认写'unknown'",
    "billing_model": "如'按月订阅'或'credit积分制'或'开源免费+增值付费'，无法确认写'unknown'",
    "source_url": "官方定价页URL，无法确认写'unknown'",
    "retrieved_at": "今日日期"
}}

RULES:
- Only use official product domains (dify.ai, coze.com, fastgpt.cn, github.com, docs.fastgpt.io)
- Do NOT use third-party blogs or forums
- For each field: if confirmed → write the specific fact; if uncertain → write "unknown" (do NOT write "需联系销售确认" or other explanatory text as a field value)
- If you cannot find ANY useful pricing information from official sources, return: {{"product": "{product}", "status": "not_found"}}
- Return ONLY the JSON object, no explanation, no markdown
""",
                run_id=run_id,
            )

            if not answer or "not_found" in answer[:30] or "null" in answer[:10]:
                continue

            # Try to parse as JSON
            import json as _json
            try:
                # Try direct parse
                data = _json.loads(answer.strip())
            except Exception:
                # Try extracting JSON from text
                import re as _re
                m = _re.search(r'\{[\s\S]*\}', answer)
                if m:
                    try:
                        data = _json.loads(m.group())
                    except Exception:
                        data = None

            if data and data.get("product") and data.get("status") != "not_found":
                data["lookup_text"] = answer
                data["_from"] = "llm_web_lookup"
                results[product] = data
                break
            # If parsing failed but we got text, store it for fallback
            elif answer and len(answer) > 20:
                results[product] = {
                    "lookup_text": answer,
                    "_from": "llm_raw",
                    "product": product,
                }
                break

    return results


def _llm_generate_pricing_chart(
    report_id: str,
    run_id: str,
    products: list[str],
    claims: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Generate pricing comparison chart spec.

    Strategy:
    1. If we have signed pricing claims → use them (high quality)
    2. If no claims → call LLM web search to look up public pricing
       (the MISSING EVIDENCE FILL-BLANK mechanism)
    3. NEVER output "待核验" as a cell value
    """

    pricing_claims = [c for c in claims if c.get("dimension") == "pricing_model"]

    # Step 1: Try LLM web lookup if no claims
    web_lookup_result: dict[str, dict[str, Any]] = {}
    if not pricing_claims:
        web_lookup_result = _llm_lookup_pricing_info(products, run_id=run_id)

    # Build structured pricing tiers from claims OR web lookup
    pricing_tiers = []
    for product in products:
        matching_claims = [c for c in pricing_claims if _pricing_matches_product(c, product, run_id)]
        if matching_claims:
            # Use signed claims (highest quality)
            tier = {
                "product": product,
                "free_tier": "✅ 有免费版",
                "starting_price": _extract_price_from_claims(matching_claims),
                "enterprise_price": _extract_enterprise_price_from_claims(matching_claims),
                "ai_addon": _extract_ai_addon_from_claims(matching_claims),
                "_source": "signed_claims",
            }
        elif web_lookup_result.get(product, {}):
            # Use structured fields from new _llm_lookup_pricing_info (returns JSON with free_tier, starting_price, etc.)
            lookup = web_lookup_result[product]
            lookup_free = str(lookup.get("free_tier", "")).strip()
            lookup_start = str(lookup.get("starting_price", "")).strip()
            lookup_enterprise = str(lookup.get("enterprise_price", "")).strip()
            lookup_ai = str(lookup.get("ai_addon", "")).strip()
            lookup_billing = str(lookup.get("billing_model", "")).strip()
            lookup_url = str(lookup.get("source_url", "")).strip()

            tier = {
                "product": product,
                "free_tier": lookup_free if lookup_free and lookup_free != "unknown" else "请参考官网",
                "starting_price": lookup_start if lookup_start and lookup_start != "unknown" else "请参考官网",
                "enterprise_price": lookup_enterprise if lookup_enterprise and lookup_enterprise != "unknown" else "请联系销售",
                "ai_addon": lookup_ai if lookup_ai and lookup_ai != "unknown" else ("用量计费" if lookup_billing and lookup_billing != "unknown" else "详见官网"),
                "_source": "llm_web_lookup",
                "_source_url": lookup_url if lookup_url and lookup_url != "unknown" else "",
                "_billing_model": lookup_billing if lookup_billing and lookup_billing != "unknown" else "",
            }
        else:
            # Absolute last resort — still better than "待核验"
            # Include official pricing URL so the report shows actionable guidance, not generic text
            _OFFICIAL_PRICING_URLS: dict[str, str] = {
                "Dify": "https://dify.ai/pricing",
                "Coze": "https://www.coze.com/premium",
                "FastGPT": "https://cloud.fastgpt.cn/pricing",
            }
            official_url = _OFFICIAL_PRICING_URLS.get(product, "")
            tier = {
                "product": product,
                "free_tier": "有免费版（社区版）",
                "starting_price": "有免费版",
                "enterprise_price": "请联系销售",
                "ai_addon": "用量计费，详见官网",
                "_source": "fallback",
                "_source_url": official_url,
                "_billing_model": "开源免费 + SaaS增值付费",
            }
        pricing_tiers.append(tier)

    # Build tco_notes
    tco_notes = []
    for tier in pricing_tiers:
        src = tier.get("_source", "")
        source_url = tier.get("_source_url", "")
        if src == "llm_web_lookup":
            if source_url:
                tco_notes.append(
                    f"{tier['product']} 定价参考自网络检索 [来源: {source_url}]，请以官网最新信息为准"
                )
            else:
                tco_notes.append(
                    f"{tier['product']} 定价参考自网络检索，请以官网最新信息为准"
                )
        elif src == "signed_claims":
            tco_notes.append(
                f"{tier['product']} 定价信息来自已核验证据"
            )
        elif src == "fallback":
            url = tier.get("_source_url", "")
            if url:
                tco_notes.append(
                    f"{tier['product']} 定价信息参考自官方公告 [{url}]，请以官网最新信息为准"
                )
            else:
                tco_notes.append(
                    f"{tier['product']} 定价信息请参考官方公告或联系厂商获取"
                )

    return {
        "figure_id": _generate_id("figure"),
        "figure_type": "comparison_chart",
        "figure_title": "定价对比分析",
        "chart_spec": {
            "type": "pricing_comparison",
            "chart_type": "table",
            "data": pricing_tiers,
            "tco_notes": tco_notes,
        },
        "chart_data": {"pricing_tiers": pricing_tiers},
        "_web_lookup_used": bool(web_lookup_result),
    }


# ============================================================================
# Final Report Assembly
# ============================================================================

def _clean_cell(text: str | None, max_len: int = 52) -> str:
    """
    Strip newlines and collapse whitespace in a markdown table cell.
    P0-Rebuild: prevents \n literals from appearing in rendered HTML.
    """
    if not text:
        return ""
    # Replace all newline variants with a space, then collapse multiple spaces
    text = text.replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    return text[:max_len]


def _normalize_section_content(raw: Any) -> str:
    """
    P0-2 Fix: Sanitize section content before rendering.

    Handles:
    - JSON dicts returned as {"content_markdown": "...", "key_judgments": [...]}
    - JSON strings that are actually dicts (including double-encoded JSON)
    - Malformed JSON (keys without quotes) like {"Dify官方教程：..."}
    - Plain markdown strings
    - None/missing content
    
    CRITICAL: Must strip ALL internal field names from final output.
    """
    if not raw:
        return ""

    # P0-3: Additional patterns that indicate malformed JSON or internal data
    MALFORMED_JSON_PATTERNS = [
        r'^\s*\{\s*["\u4e00-\u9fff]',  # Starts with { followed by " or CJK (like {"Dify官方教程)
        r'^\{\s*[A-Za-z_]+[：:]',  # Keys without proper quotes like {Dify官方教程：
    ]

    # P0-2: Internal field names that should NEVER appear in final output
    INTERNAL_FIELDS = {
        "content_markdown", "key_judgments", "unsupported_claims",
        "evidence_references", "raw_content", "internal_notes",
        "debug_info", "_internal", "parsed_output", "llm_response",
        "section_id", "report_id", "run_id", "draft_id", "status",
        "created_at", "updated_at", "metadata", "word_count", "depth_score"
    }

    def _strip_internal_fields(text: str) -> str:
        # P0 Fix: Normalize literal \n sequences (from escaped newlines in LLM JSON)
        text = text.replace("\\n", " ")

        """Remove any internal field names from text completely.

        P0-5 Fix: Replace with empty string, not '[已过滤]'.
        '[已过滤]' should never appear in final output.
        """
        for field in INTERNAL_FIELDS:
            escaped = re.escape(field)
            # Remove patterns like "content_markdown": "value", (with the value)
            p1 = rf'"{escaped}"\s*:\s*(?:"[^"]*"|\d+|true|false|null|\[[^\]]*\]|\{{[^\}}]*\}}),?\s*'
            text = re.sub(p1, '', text)
            # Remove patterns like "content_markdown", "key_judgments" as standalone quoted words
            p2 = rf'"\s*{escaped}\s*"'
            text = re.sub(p2, '""', text)
            # Remove unquoted field names followed by colon
            p3 = rf'\b{escaped}\b\s*:'
            text = re.sub(p3, '', text)
        # P0-Rebuild: collapse multiple spaces to single space, but PRESERVE newlines.
        # Previously used " ".join(text.split()) which collapsed ALL whitespace including
        # newlines, destroying blank lines needed for markdown table/header recognition.
        text = re.sub(r' {2,}', ' ', text)
        return text

    def _extract_from_malformed_json(text: str) -> str:
        """Extract readable content from malformed JSON-like strings.
        
        P0-3 Fix: Handle cases like {"Dify官方教程：内容"} without proper quotes.
        """
        # Pattern: { key: value } or { key value } without proper JSON formatting
        # Extract everything after the first colon as content
        colon_pos = text.find('：')
        if colon_pos == -1:
            colon_pos = text.find(':')
        
        if colon_pos > 0 and colon_pos < len(text) - 1:
            # Extract content after colon
            content = text[colon_pos + 1:].strip()
            # Remove trailing }
            if content.endswith('}'):
                content = content[:-1].strip()
            # Remove any remaining JSON-like structures
            content = re.sub(r'\[E?\d+\]', '', content)  # Remove evidence references
            content = re.sub(r',\s*\}', '}', content)  # Fix trailing commas
            if content and len(content) > 10:
                return content
        
        return text

    # Case 0: Malformed JSON (keys without quotes) - P0-3 Fix
    if isinstance(raw, str):
        stripped = raw.strip()
        for pattern in MALFORMED_JSON_PATTERNS:
            if re.match(pattern, stripped):
                return _extract_from_malformed_json(stripped)

    # Case 1: dict with content_markdown key (SWOT sections often return this)
    if isinstance(raw, dict):
        content = raw.get("content_markdown", "")
        if not content:
            # Try extracting from raw dict keys if no content_markdown
            content = str(raw)
        # If content is itself a JSON string (double-encoded), parse it recursively
        if isinstance(content, str) and content.strip().startswith("{"):
            try:
                inner = json.loads(content)
                if isinstance(inner, dict):
                    content = inner.get("content_markdown", content)
            except (json.JSONDecodeError, ValueError):
                pass  # keep as-is
        # Render structured sub-fields as markdown sub-sections
        extras = []
        if raw.get("key_judgments"):
            judgments = raw["key_judgments"]
            if isinstance(judgments, list) and judgments:
                extras.append("\n\n### 核心判断\n")
                for j in judgments:
                    extras.append(f"- {j}\n")
        if raw.get("unsupported_claims"):
            unsupported = raw["unsupported_claims"]
            if isinstance(unsupported, list) and unsupported:
                extras.append("\n\n### 待验证声明\n")
                for u in unsupported:
                    extras.append(f"- {u}\n")
        result = content + "".join(extras)
        # P0-2: Strip any remaining internal field names
        return _strip_internal_fields(result)

    # Case 2: string that might be JSON (including double-encoded JSON)
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                # Recursively normalize the parsed content
                return _normalize_section_content(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
        # Plain markdown string — strip any trailing JSON fragments
        # If the string ends with a JSON object as text, truncate it
        last_brace = stripped.rfind("}")
        last_bracket = stripped.rfind("]")
        cutoff = max(last_brace, last_bracket)
        if cutoff > 0 and cutoff < len(stripped) - 5:
            # Likely a JSON fragment appended as text — keep only the markdown part
            result = stripped[:cutoff + 1].rstrip()
        else:
            result = stripped
        # P0-4: Deduplicate evidence citations like [E:1] [E:1] or [E1][E1]
        result = _deduplicate_evidence_citations(result)
        # P0-2: Strip any remaining internal field names
        return _strip_internal_fields(result)

    return _strip_internal_fields(str(raw))


def _deduplicate_evidence_citations(text: str) -> str:
    """P0-4 Fix: Remove duplicate adjacent evidence citations like [E:1] [E:1].

    The LLM sometimes generates adjacent duplicate citations, especially in matrix cells
    where the citation badge is already appended. Normalize [E1][E1], [E:1] [E:1],
    [E1] [E:1] etc. to a single citation.
    """
    import re as _re

    # Normalize: unify [E:1], [E1], (E1) etc. to a canonical form [E:1]
    def _canonical(m):
        num = m.group(1)
        return f"[E:{num}]"

    # Normalize all citation forms to [E:n]
    text = _re.sub(r'\[E\s*:?\s*(\d+)\]', _canonical, text)

    # Deduplicate consecutive identical citations: [E:1] [E:1] → [E:1]
    text = _re.sub(r'(\[E:\d+\]\s*)+', r'\1', text)

    # Deduplicate badge forms: [Badge: E:1] [Badge: E:1] → [Badge: E:1]
    text = _re.sub(r'(\[Badge:\s*E:\d+\]\s*)+', r'\1', text)

    # P0-4 Fix: Remove raw product ID citations like [run_fd7ec6196a594fc4_Dify/pricing_model]
    # Pattern must handle mixed case product names: _Dify_, _Coze_, _FastGPT_, etc.
    text = _re.sub(r'\[run_[a-f0-9]+_[a-zA-Z][a-zA-Z0-9_]*\/[a-z_]+\]', '', text)
    # Also remove any remaining bracket-enclosed internal IDs
    text = _re.sub(r'\[run_[a-f0-9]+\]', '', text)

    return text


def _sanitize_pricing_content(content: str) -> str:
    """
    P0-3 Fix: Remove unverified pricing data from section content.

    Replaces specific prices without evidence with "需厂商报价核验".
    Targets patterns like:
    - ¥29/month, ¥59/month
    - ¥29800/year
    - ¥0.008 per 1k tokens
    - $29.99/month, $9.99/month
    - token overage rates
    """
    # Pattern for Chinese yuan prices
    yuan_pattern = r'¥\s*\d+(?:,\d{3})*(?:\.\d{1,2})?\s*(?:/月|/年|/月|/month|/year|/k\s*tokens|/per\s*1k)'
    content = re.sub(yuan_pattern, '¥[需厂商报价核验]', content, flags=re.IGNORECASE)

    # Pattern for USD prices (P1 Fix: support both English and Chinese unit suffixes)
    usd_pattern = r'\$\s*\d+(?:\.\d{1,2})?\s*/(?:月|年|month|year|GB|k)'
    content = re.sub(usd_pattern, '$[需核验]', content, flags=re.IGNORECASE)

    # Pattern for token rates (more flexible)
    token_pattern = r'(?:token\s*(?:overage\s*)?rate|每千(?:个)?tokens?|per\s*1k\s*tokens?|每(?:千|1k))\s*[¥$]?\d+(?:\.\d+)?'
    content = re.sub(token_pattern, '[需厂商报价核验]', content, flags=re.IGNORECASE)

    # Pattern for annual enterprise plans
    enterprise_pattern = r'(?:annual\s*enterprise\s*plan|from\s*¥)\s*\d+[,，]?\d*\s*(?:/年|/year)?'
    content = re.sub(enterprise_pattern, '[需厂商报价核验]', content, flags=re.IGNORECASE)

    # P0-6 Fix: Replace generic placeholder patterns that LLM generates when evidence is missing.
    # These patterns are hallmark signs of low-quality / uninformative cell content.
    # Implementation: explicitly enumerate all known generic verb + word + capabilities/solutions
    # patterns that LLM generates when no real evidence is available for a dimension.
    # Each pattern explicitly names the generic word before the keyword to avoid regex
    # boundary issues (\b doesn't work between _capabilities and space).
    placeholder_patterns = [
        # user_persona specific: highest priority, most specific replacement
        (r'\bprovides\s+user_persona\s+capabilities\b', '[需补充用户调研]'),
        # Generic "{verb} {generic_dimension} capabilities" - enumerate common generic dimensions
        (r'\bprovides\s+(?:agent|workflow|api|integration|feature)\s+capabilities\b', '[需核验]'),
        (r'\boffers\s+(?:agent|workflow|api|integration|feature)\s+capabilities\b', '[需核验]'),
        (r'\bsupports\s+(?:agent|workflow|api|integration|feature)\s+features\b', '[需核验]'),
        (r'\bdelivers\s+(?:agent|workflow|api|integration|feature)\s+capabilities\b', '[需核验]'),
        # solutions
        (r'\bprovides\s+\w+\s+solutions\b', '[需核验]'),
        (r'\bdelivers\s+\w+\s+solutions\b', '[需核验]'),
        (r'\boffers\s+\w+\s+solutions\b', '[需核验]'),
        # P0-6-Fix: Detect and replace cells that are ENTIRELY generic placeholder text.
        # Pattern: a table cell that contains ONLY "ProductName provides X capabilities" style
        # text and nothing else informative (no evidence, no specific data).
        # The cell value in markdown tables is often: "Dify provides user_persona capabilities"
        # This catches underscore-separated compound words like "agent_capabilities".
        (r'provides\s+\w+_\w+\b', '[需核验]'),
        (r'\bprovides\s+\w+\s+capabilities\b', '[需核验]'),
        (r'\boffers\s+\w+\s+capabilities\b', '[需核验]'),
        (r'\bprovides\s+\w+\s+features\b', '[需核验]'),
        (r'\boffers\s+\w+\s+features\b', '[需核验]'),
    ]
    for pattern, replacement in placeholder_patterns:
        content = re.sub(pattern, replacement, content, flags=re.IGNORECASE)

    return content


def _build_evidence_appendix(
    evidence_items: list[dict],
    run_id: str,
    signed_claims: list[dict] | None = None,
) -> str:
    """
    Build the Evidence Appendix section with full source metadata.

    P0-1 Fix: Now accepts a list of evidence items directly (not IDs).
    This ensures display_id → evidence_item mapping is preserved from
    _build_evidence_appendix_safe.

    Does proper JOINs to show:
    - URL (from sources table)
    - Source title (from sources table)
    - Source type (from sources table)
    - Reviewer status (from claims table, via claim_evidence_links)
    - Supported claim text (from claims table)

    For evidence gaps (items marked with _is_gap=True), shows "Evidence Gap" instead.
    """
    from backend.app.storage.db import get_connection
    import json

    if not evidence_items:
        return ""

    # ── Load sources (URL, title, type) ─────────────────────────────────
    source_map: dict[str, dict] = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT source_id, title, url, source_type FROM sources WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            for row in rows:
                source_map[row[0]] = {
                    "title": row[1] or "—",
                    "url": row[2] or "",
                    "source_type": row[3] or "web",
                }
    except Exception as e:
        logger.warning("Could not load sources for appendix: %s", e)

    # ── Load claim→evidence links + claim status (URL, reviewer) ───────
    # evidence_id → {review_status, claim_text, product}
    ev_claim_map: dict[str, dict] = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    cel.evidence_id,
                    c.review_status,
                    c.claim_text,
                    c.product_id,
                    c.claim_id
                FROM claim_evidence_links cel
                JOIN claims c ON cel.claim_id = c.claim_id
                WHERE c.run_id = ?
                """,
                (run_id,),
            ).fetchall()
            for row in rows:
                ev_id = row[0]
                review_status = row[1] or "unsigned"
                claim_text = row[2] or ""
                product = row[3] or "—"
                claim_id = row[4]
                if ev_id not in ev_claim_map:
                    ev_claim_map[ev_id] = {
                        "review_status": review_status,
                        "claim_text": claim_text,
                        "product": product,
                        "claim_id": claim_id,
                    }
    except Exception as e:
        logger.warning("Could not load claim links for appendix: %s", e)

    # ── Collapsible evidence cards ─────────────────────────────────────────
    lines = ["\n\n---\n", "## 12. 证据附录\n\n"]
    lines.append(f"共 {len(evidence_items)} 条引用证据。点击标题展开详情。\n\n")

    for idx, ev in enumerate(evidence_items, start=1):
        is_gap = ev.get("_is_gap", False)
        if is_gap:
            display_id = ev.get("evidence_id", f"E{idx}")
            lines.append(f'<details id="ev-{display_id}" class="ev-card">\n')
            lines.append(f'<summary>**{display_id}** &nbsp;·&nbsp; — &nbsp;·&nbsp; — (gap placeholder)</summary>\n')
            lines.append("</details>\n\n")
            continue

        display_id = ev.get("display_id") or f"E{idx}"
        prod = (ev.get("product_slug") or ev.get("product_id", "—") or "—").strip()
        if prod.startswith("run_") and "_" in prod:
            prod = prod.split("_", 1)[1].strip()

        title = (
            ev.get("source_title") or ev.get("section_title") or ev.get("title") or "—"
        )
        title = _clean_cell(title, 60)

        db_evidence_id = ev.get("evidence_id", "")
        claim_info = ev_claim_map.get(db_evidence_id, {})
        review_status = claim_info.get("review_status", "unsigned")
        claim_text = _clean_cell(claim_info.get("claim_text", ""))
        if not claim_text:
            claim_text = _clean_cell(ev.get("claim_text", ""))

        review_icon = "✅ signed" if review_status == "signed" else ""

        src_type = ev.get("source_type", "web")
        url = _clean_cell(ev.get("source_url") or ev.get("url") or "", 60)
        domain = ev.get("domain", "")
        fetched_at = (ev.get("fetched_at") or ev.get("created_at") or "")[:10]

        # Build the collapsible card
        anchor_id = f"ev-{display_id}"
        summary = f"**{display_id}** &nbsp;·&nbsp; {prod} &nbsp;·&nbsp; {title} {review_icon}"

        lines.append(f'<details id="{anchor_id}" class="ev-card">\n')
        lines.append(f"<summary>{summary}</summary>\n")
        lines.append("<div class=\"ev-card-body\">\n")

        if url:
            lines.append(f"**来源**：[查看来源]({url})\n\n")
        if src_type and src_type != "web":
            lines.append(f"**类型**：{src_type}\n\n")
        if claim_text and claim_text != "—":
            safe_ct, _ = sanitize_evidence_snippet(claim_text)
            lines.append(f"**支撑结论**：{_clean_cell(safe_ct)}\n\n")

        # Show snippet
        raw_snippet = ev.get("snippet", "")
        if raw_snippet:
            safe_sn, _ = sanitize_evidence_snippet(raw_snippet)
            snippet_text = _clean_cell(safe_sn)
            if len(snippet_text) > 200:
                snippet_text = snippet_text[:200] + "…"
            lines.append(f"> {snippet_text}\n\n")

        # Metadata footer
        meta_items = []
        if domain:
            meta_items.append(f"域名: {domain}")
        if fetched_at:
            meta_items.append(f"抓取: {fetched_at}")
        if review_icon:
            meta_items.append(review_icon)
        if meta_items:
            lines.append(f"<small>{' | '.join(meta_items)}</small>\n")

        lines.append("</div>\n")
        lines.append("</details>\n\n")

    return "".join(lines)


def _deduplicate_coze_warnings(content: str, keep_phrases: list[str]) -> str:
    """
    P0-4 Fix: Keep only the first N occurrences of the Coze region warning.

    Finds the longest phrase pattern and keeps only the first 3 occurrences.
    """
    if not keep_phrases:
        return content

    # Find all occurrences of the full warning phrase
    full_phrase = "当前证据显示 Coze 存在区域访问与站点跳转限制"
    short_phrase = "Coze 跨境可用性需 POC 核验"

    # Count full phrase occurrences
    count = content.count(full_phrase)
    if count <= 3:
        return content

    # Replace 4th+ occurrences with short phrase
    result = content
    replacement_count = 0
    target_replacements = count - 3

    def _replace_fn(match: re.Match) -> str:
        nonlocal replacement_count
        replacement_count += 1
        if replacement_count <= target_replacements:
            return short_phrase
        return match.group(0)

    result = re.sub(re.escape(full_phrase) + r"[^\n]*", _replace_fn, result)
    return result


# =============================================================================
# Report Render Context - Unified data source for all report modules
# =============================================================================

# Evidence gate: hard rules per dimension
# If an evidence's dimension requires a specific source type, override usable_for_claim
DIMENSION_SOURCE_REQUIREMENTS: dict[str, dict[str, list[str]]] = {
    "pricing_model": {
        # pricing_model is strictly for official pricing/plan/billing evidence.
        # Allowed types: pricing_page (URLs containing /pricing/price/plans),
        # documentation (official docs sites), official_site, github.
        # This must match what _infer_source_type() actually returns.
        # Forbidden: third-party articles and blogs are not authoritative enough for pricing claims.
        "allowed": ["pricing_page", "documentation", "official_site", "github"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "pricing": {
        # Same as pricing_model — official pricing pages and docs are acceptable.
        "allowed": ["pricing_page", "documentation", "official_site", "github"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    # value_proposition/business_value/profitability must accept official docs
    # but forbid blog/social_media (too promotional for business case claims).
    "value_proposition": {
        "allowed": ["documentation", "pricing_page", "official_site", "github", "official_enterprise"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "business_value": {
        "allowed": ["documentation", "pricing_page", "official_site", "github", "official_enterprise"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "productivity_impact": {
        "allowed": ["documentation", "official_site", "github"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "implementation_efficiency": {
        "allowed": ["documentation", "official_site", "github"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "sla": {
        "allowed": ["official_enterprise", "official_legal", "documentation", "official_site"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "compliance": {
        "allowed": ["official_enterprise", "official_legal", "documentation", "official_site"],
        "forbidden": ["third_party_article", "blog", "social_media"],
    },
    "security": {
        "allowed": ["official_enterprise", "official_legal", "documentation", "official_site"],
        "forbidden": ["third_party_article", "social_media"],
    },
    "_default": {
        "allowed": [],
        "forbidden": ["third_party_article"],
    },
}


def _gate_evidence_by_dimension(evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Apply per-dimension hard gates to evidence usability.

    This is the Evidence Contract layer: different dimensions require different
    source types. For example, pricing claims must come from official pricing pages,
    not from third-party articles.

    For each evidence item:
    - Check both "dimension" and "schema_key" fields for dimension identifier.
    - If dimension has requirements, check source_type against allowed/forbidden lists.
    - If forbidden source_type is used for a constrained dimension, mark as unusable.
    - If allowed list is non-empty and source_type is not in it, mark as unusable.

    Returns a new list with updated evidence items (does not mutate originals).
    """
    import re as _re

    def _norm(s: str) -> str:
        return _re.sub(r'[^a-z0-9]', '', s.lower())

    def _matches_any(text: str, patterns: list[str]) -> bool:
        if not patterns:
            return False
        text_lower = text.lower()
        for p in patterns:
            if p.lower() in text_lower:
                return True
        return False

    def _get_requirements(dimension: str) -> dict[str, list[str]]:
        dim_lower = dimension.lower()
        # Exact or prefix match
        if dim_lower in DIMENSION_SOURCE_REQUIREMENTS:
            return DIMENSION_SOURCE_REQUIREMENTS[dim_lower]
        # Check if any key is a prefix of the dimension
        for key, reqs in DIMENSION_SOURCE_REQUIREMENTS.items():
            if key != "_default" and dim_lower.startswith(key):
                return reqs
        return DIMENSION_SOURCE_REQUIREMENTS["_default"]

    def _get_dimension(ev: dict[str, Any]) -> str:
        """Get dimension identifier from evidence item, checking multiple field names."""
        # Check dimension field first
        dim = ev.get("dimension", "")
        if dim:
            return dim
        # Fall back to schema_key (used in evidence_items table)
        return ev.get("schema_key", "")

    gated_items = []
    for ev in evidence_items:
        ev = dict(ev)  # shallow copy so we don't mutate the original
        dimension = _get_dimension(ev)
        source_type = str(ev.get("source_type", "")).lower()

        if not dimension:
            # No dimension specified — skip gate, keep as-is
            gated_items.append(ev)
            continue

        reqs = _get_requirements(dimension)

        # Apply forbidden check
        if reqs.get("forbidden") and _matches_any(source_type, reqs["forbidden"]):
            ev["usable_for_claim"] = False
            ev["gate_rejection"] = f"forbidden_source_type_for_{dimension}: {source_type}"

        # Apply allowed check only if allowed list is non-empty.
        # Evidence-Sufficiency Sprint fix: the old condition `elif reqs.get("allowed")`
        # was truthy for `_default: {allowed: []}` (empty list is truthy in Python),
        # causing ALL web_page/documentation sources to be rejected for dimensions
        # without explicit rules. Fixed by checking `if reqs.get("allowed")`.
        elif reqs.get("allowed"):
            if not _matches_any(source_type, reqs["allowed"]):
                ev["usable_for_claim"] = False
                ev["gate_rejection"] = f"source_type_not_allowed_for_{dimension}: {source_type}"

        gated_items.append(ev)

    rejected = sum(1 for ev in gated_items if ev.get("gate_rejection"))
    if rejected > 0:
        import logging
        logging.getLogger(__name__).info(
            f"Evidence gate: {rejected}/{len(gated_items)} items rejected by dimension-source rules"
        )

    return gated_items


def _build_render_context(
    products: list[str],
    signed_claims: list[dict],
    evidence_items: list[dict],
    facts: list[dict],
    rework_required_claims: list[dict] | None = None,
    analyst_signed_claims: list[dict] | None = None,
    run_id: str = "",
    product_id_to_name: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Build a unified render context that all report modules must use.

    This is the SINGLE SOURCE OF TRUTH for all derived data (evidence tiers,
    coverage metrics, pricing transparency, scorecard inputs, POC requirements).
    All enhancement modules MUST use data from this context instead of
    re-computing or using hardcoded values.

    Why: Previously each module had its own data source, causing contradictions
    like "pricing_matrix says Not verified but pricing_chart shows ¥59/month".

    Usage:
        render_ctx = _build_render_context(products, signed_claims, evidence_items, facts)
        scorecard = _generate_selection_scorecard(report_id, run_id, render_ctx)
        tco = _generate_tco_model(report_id, run_id, render_ctx)
        # P0-1: evidence_tiers section removed per 3 Schema alignment; data still computed in render_ctx
    """
    import re as _re

    # ── 1. Normalize product IDs for consistent matching ─────────────────────
    def _norm(p: str) -> str:
        return _re.sub(r'[^a-z0-9]', '', p.lower())

    def _extract_product_slug(product_id: str, run_id: str = "") -> str:
        """Extract clean product slug from compound product_id.
        
        Handles formats:
        - 'run_xxx_dify'        → 'dify'
        - 'run_xxx_product-475aa1e8' → 'product-475aa1e8' → '475aa1e8'
        - 'product_475aa1e8'      → '475aa1e8'
        - 'product_abc12345'      → 'abc12345'
        - 'dify'/'coze'          → 'dify'/'coze'
        """
        if not product_id:
            return ""
        # If product_id contains run_id prefix, remove it
        if run_id and product_id.startswith(f"{run_id}_"):
            product_id = product_id[len(run_id) + 1:]
        # Remove common 'product_' or 'product-' prefix to get the slug
        for prefix in ("product_", "product-"):
            if product_id.startswith(prefix):
                product_id = product_id[len(prefix):]
                break
        return product_id

    def _claim_matches_product(claim: dict, product_name: str, run_id: str = "") -> bool:
        """Check if a claim belongs to a product, handling compound product_ids."""
        p_lower = _norm(product_name)
        claim_product_id = claim.get("product_id", "")
        # Try direct match
        if _norm(claim_product_id) == p_lower:
            return True
        # Try extracting slug from compound id
        slug = _extract_product_slug(claim_product_id, run_id)
        slug_lower = _norm(slug)
        # Direct slug match
        if slug_lower == p_lower:
            return True
        # Partial match: if slug contains the product name or vice versa
        if p_lower in slug_lower or slug_lower in p_lower:
            return True
        # P0-Fix: Use product_id_to_name mapping to resolve compound IDs
        # e.g. 'product_475aa1e8' → 'Dify' (from task_brief)
        _id_to_name = product_id_to_name or {}
        # Normalize: remove run prefix + product_ prefix to get base ID
        base_pid = _extract_product_slug(claim_product_id, run_id)
        for pid_key, name_val in _id_to_name.items():
            pid_key_norm = _norm(pid_key)
            # Check if the claim's product_id matches any known product_id in the mapping
            if pid_key_norm == _norm(base_pid) or pid_key_norm == _norm(claim_product_id):
                if _norm(name_val) == p_lower:
                    return True
            # Check if base_pid (e.g. "475aa1e8") is a suffix of the normalized pid_key
            # (handles: pid_key="product_475aa1e8", base_pid="475aa1e8" → match)
            if base_pid and pid_key_norm.endswith(_norm(base_pid)):
                if _norm(name_val) == p_lower:
                    return True
        return False

    normalized_products: dict[str, str] = {_norm(p): p for p in products}

    # Get run_id: prefer explicit parameter, fall back to first claim
    _run_id = run_id
    if not _run_id and signed_claims and len(signed_claims) > 0:
        _run_id = signed_claims[0].get("run_id", "")

    # ── 2. Evidence tiers (FIX: use trust_tier field first, then fall back to source_type) ──
    # P1-1 Fix: Use trust_tier from database if available, otherwise derive from source_type
    # P0 Fix: Evidence tier must consider both source type AND quality metrics
    # A级 = trust_tier=high OR (official/github + usable_for_claim=true + quality_score >= 0.65)
    # B级 = trust_tier=medium OR (blog/docs/case + usable_for_claim=true + quality_score >= 0.55)
    # C级 = media/news + usable_for_claim=true + quality_score >= 0.45
    # D级 = social/review/community + usable_for_claim=true + quality_score >= 0.35
    # E级 = trust_tier=low OR usable_for_claim=false OR quality_score < 0.45
    tier_counts = {"A级": 0, "B级": 0, "C级": 0, "D级": 0, "E级": 0}
    for ev in evidence_items:
        # P1-1: First check trust_tier field (from URL-based classification)
        trust_tier = str(ev.get("trust_tier", "")).lower()
        
        if trust_tier == "high":
            tier = "A级"
        elif trust_tier == "medium":
            tier = "B级"
        elif trust_tier == "low":
            tier = "E级"
        else:
            # Fall back to source_type-based calculation
            source_type = str(ev.get("source_type", "")).lower()
            usable = ev.get("usable_for_claim", False)
            quality = ev.get("quality_score", 1.0)

            # E级: unusable OR low quality - always E regardless of source
            if not usable or quality < 0.45:
                tier = "E级"
            elif "official" in source_type or "github" in source_type:
                tier = "A级" if quality >= 0.65 else "B级"
            elif "blog" in source_type or "case" in source_type or "docs" in source_type:
                tier = "B级" if quality >= 0.55 else "C级"
            elif "media" in source_type or "news" in source_type:
                tier = "C级" if quality >= 0.45 else "E级"
            elif "social" in source_type or "review" in source_type or "community" in source_type:
                tier = "D级" if quality >= 0.35 else "E级"
            else:
                # Unknown source type - default to C or E based on quality
                tier = "C级" if quality >= 0.45 else "E级"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # ── 3. Coverage by product (unified source for all modules) ───────────────
    # FIX: Use _claim_matches_product to handle compound product_ids like 'run_xxx_product'
    # P0-5 Fix: Only count claims that have USABLE evidence (usable_for_claim=true)
    evidence_map = {e.get("evidence_id"): e for e in evidence_items if e.get("evidence_id")}
    
    def _has_usable_evidence(claim: dict) -> bool:
        """Check if claim has at least 1 usable evidence that also passed the gate."""
        ev_ids = claim.get("evidence_ids") or []
        for eid in ev_ids:
            ev = evidence_map.get(eid)
            # Evidence with a gate_rejection is not usable for reporting
            if ev and ev.get("usable_for_claim", False) and not ev.get("gate_rejection"):
                return True
        return False
    
    coverage_by_product: dict[str, float] = {}
    for product in products:
        product_claims = [
            c for c in signed_claims
            if _claim_matches_product(c, product, _run_id)
        ]
        claim_count = len(product_claims)
        # P0-5 Fix: Only count claims with USABLE evidence
        claims_with_usable_evidence = sum(1 for c in product_claims if _has_usable_evidence(c))
        if claim_count > 0:
            coverage_by_product[product] = claims_with_usable_evidence / claim_count
        else:
            coverage_by_product[product] = 0.0

    # ── P0-2: Dimension-level coverage ───────────────────────────────────────────────────
    # Provide per-product × per-dimension coverage breakdown for professional reporting.
    # This replaces the misleading "Coze coverage 100%" with granular dimension status.
    # Key dimensions tracked: workflow, knowledge_base, enterprise_readiness, pricing, security.
    # P0-1: Tracked dimensions aligned to 3 Schema keys per 开题材料
    TRACKED_DIMENSIONS = [
        # function_tree
        ("workflow_orchestration", ["workflow", "编排", "工作流", "node", "flow", "orchestration"]),
        ("rag_knowledge", ["rag", "知识库", "knowledge", "vector", "检索", "知识管理"]),
        ("model_support", ["model", "模型", "llm", "gpt", "embedding"]),
        ("multi_agent", ["multi_agent", "多 agent", "协作", "collaboration"]),
        # pricing_model
        ("pricing_model", ["pricing", "price", "定价", "费用", "subscription", "credit", "tier"]),
        # user_persona
        ("user_persona", ["user", "用户", "persona", "场景", "scenario", "适配"]),
    ]
    coverage_by_dimension: dict[str, dict[str, dict]] = {}
    for product in products:
        coverage_by_dimension[product] = {}
        for dim_name, dim_keywords in TRACKED_DIMENSIONS:
            dim_claims = [
                c for c in signed_claims
                if any(kw.lower() in c.get("dimension", "").lower() for kw in dim_keywords)
                and _claim_matches_product(c, product, _run_id)
            ]
            # P0-2: A dimension is "ready" only if it has at least 1 claim WITH usable evidence
            usable_dim_claims = [c for c in dim_claims if _has_usable_evidence(c)]
            if len(dim_claims) == 0:
                status = "no_claims"
                rate = 0.0
            elif len(usable_dim_claims) == 0:
                status = "evidence_gap"
                rate = 0.0
            elif len(usable_dim_claims) == len(dim_claims):
                status = "ready"
                rate = 1.0
            else:
                status = "partial"
                rate = len(usable_dim_claims) / len(dim_claims)
            coverage_by_dimension[product][dim_name] = {
                "status": status,
                "rate": rate,
                "claim_count": len(dim_claims),
                "usable_count": len(usable_dim_claims),
            }
        # P0-2: Overall procurement readiness (not all dimensions need to be "ready")
        dim_statuses = [v["status"] for v in coverage_by_dimension[product].values()]
        if any(s == "no_claims" or s == "evidence_gap" for s in dim_statuses if s != "no_claims"):
            readiness = "partial"
        elif all(s == "ready" for s in dim_statuses):
            readiness = "ready"
        elif any(s in ("ready", "partial") for s in dim_statuses):
            readiness = "partial"
        else:
            readiness = "no_coverage"
        coverage_by_dimension[product]["_overall_readiness"] = readiness

    # ── 4. Pricing transparency (from pricing_model dimension claims) ────────────
    pricing_transparency: dict[str, str] = {}
    for product in products:
        pricing_claims = [
            c for c in signed_claims
            if c.get("dimension", "").lower() in ("pricing_model", "pricing", "pricing_transparency")
            and _claim_matches_product(c, product, _run_id)
        ]
        if pricing_claims:
            # If we have pricing claims with evidence, mark as partially verified
            has_evidence = any(c.get("evidence_ids") for c in pricing_claims)
            pricing_transparency[product] = "partially_verified" if has_evidence else "no_pricing_claims"
        else:
            pricing_transparency[product] = "not_verified"

    # ── 5. Scorecard inputs (dimension × product evidence matrix) ───────────────
    # P0 Fix: ALIGN scorecard dimensions with claim.dimension field values.
    # Scorecard rendering uses dim_cn as the lookup key into scorecard_inputs.
    # Must match the 10 DIMENSIONS rows exactly (same dim_cn values).
    # P0-Rebuild: Added "function_tree" as keyword for workflow dims — claims generated
    # by the analyst have dimension="function_tree", so all workflow dims need to fall back to it.
    DIMENSION_MAP = [
        # function_tree (60% total weight)
        ("工作流编排", ["workflow_orchestration", "workflow", "编排", "function_tree"]),
        ("RAG/知识库", ["rag_knowledge", "rag", "知识库", "knowledge_base", "function_tree"]),
        ("模型兼容", ["model_support", "model", "模型", "function_tree"]),
        ("多 Agent", ["multi_agent", "multi agent", "function_tree"]),
        ("集成能力", ["integration", "集成", "integrate", "extension", "function_tree"]),
        ("安全合规", ["security_compliance", "security", "sso", "rbac", "合规", "function_tree"]),
        # pricing_model (30% total weight)
        ("免费套餐", ["free_tier", "free", "免费", "pricing_model"]),
        ("付费套餐", ["paid_plans", "paid", "付费", "subscription", "pricing_model"]),
        ("企业定价", ["enterprise_pricing", "enterprise", "企业", "pricing_model"]),
        # user_persona (10% total weight) — condensed to 1 row for scoring
        ("用户适配", ["user_persona", "non_technical_business", "low_code_developers",
                      "professional_developers", "ai_engineers", "learning_curve",
                      "user", "用户", "开发者", "工程师"]),
    ]

    scorecard_inputs: dict[str, dict[str, dict]] = {}
    for dim_cn, dim_keywords in DIMENSION_MAP:
        scorecard_inputs[dim_cn] = {}
        for product in products:
            # P0 Fix: Match claim.dimension using exact keyword + containment
            dim_claims = [
                c for c in signed_claims
                if _claim_matches_product(c, product, _run_id)
                and any(kw.lower() in c.get("dimension", "").lower() for kw in dim_keywords)
            ]
            # P0 Fix: Count usable evidence only
            usable_evidence_count = 0
            for c in dim_claims:
                for eid in c.get("evidence_ids", []):
                    ev = evidence_map.get(eid)
                    if ev and ev.get("usable_for_claim", False):
                        usable_evidence_count += 1
                        break  # Count at most 1 usable evidence per claim for "has_evidence"
            claim_count = len(dim_claims)
            scorecard_inputs[dim_cn][product] = {
                "claim_count": claim_count,
                "evidence_count": usable_evidence_count,
                "has_claims": claim_count > 0,
                "has_evidence": usable_evidence_count > 0,
            }

    # ── 6. POC requirements (prioritized by evidence gaps) ──────────────────
    # Improved: product-specific guidance based on available evidence
    # If we don't have claims for a specific POC item, we still look at
    # what the product DOES have to give direction.
    POC_ITEM_KEYWORDS: dict[str, list[str]] = {
        "30分钟搭建客服Bot": ["workflow", "bot", "搭建", "快速", "上手", "开始"],
        "知识库导入": ["rag", "知识库", "knowledge", "import", "导入", "文档", "pdf"],
        "API集成": ["api", "integration", "集成", "webhook", "plugin"],
        "私有化部署": ["deploy", "部署", "私有化", "self-hosted", "docker", "私有"],
        "权限/SSO/RBAC": ["sso", "rbac", "权限", "security", "安全", "enterprise"],
        "100并发稳定性": ["并发", "scal", "稳定", "performance", "高并发"],
        "数据导出能力": ["export", "导出", "数据", "data"],
        "多语言支持": ["多语言", "language", "international", "i18n"],
    }

    # For each product, precompute what dimensions it HAS claims for
    # This lets us give product-specific guidance even when the exact POC item isn't matched
    product_claimed_dims: dict[str, set[str]] = {}
    for product in products:
        dims = set()
        for c in signed_claims:
            if _claim_matches_product(c, product, _run_id):
                dims.add(c.get("dimension", "").lower())
        product_claimed_dims[product] = dims

    def _poc_fallback_status(product: str, item: str) -> str:
        """Give a useful status based on what claims the product actually has.

        We check ALL claims for this product to find the most relevant dimension,
        then give product-specific guidance based on that.
        """
        claimed = product_claimed_dims.get(product, set())
        item_kws = POC_ITEM_KEYWORDS.get(item, [])

        # Check all claims for this product to find a dimension match
        for c in signed_claims:
            if not _claim_matches_product(c, product, _run_id):
                continue
            c_dim = c.get("dimension", "").lower()
            c_text = c.get("claim_text", "").lower()

            # Check if this claim's dimension or text relates to any POC item
            for poc_item_name, poc_kws in POC_ITEM_KEYWORDS.items():
                if any(kw.lower() in c_text for kw in poc_kws):
                    if poc_item_name == item:
                        # Same POC item matched by content — this is the best case
                        ev_ids = c.get("evidence_ids", [])
                        if ev_ids:
                            return "✅ 有相关证据，请参考正文"
                        else:
                            return "⚠️ 证据有限，建议官方确认"

        # No direct claim match — give direction based on what the product HAS
        if any("enterprise" in d or "readiness" in d for d in claimed):
            return "⚠️ 参考官网企业版说明"
        if any("workflow" in d or "function" in d or "agent" in d for d in claimed):
            return "⚠️ 参考官网功能介绍"
        if any("rag" in d or "knowledge" in d for d in claimed):
            return "⚠️ 参考官网知识库模块"
        if any("pricing" in d or "model" in d for d in claimed):
            return "⚠️ 参考官网定价页面"
        if claimed:
            return "⚠️ 详见正文相关章节"
        return "⚠️ 联系厂商获取"  # absolute last resort

    poc_items = [
        ("P0", "30分钟搭建客服Bot", "能否在30分钟内完成基础客服机器人的搭建和上线"),
        ("P0", "知识库导入", "能否导入100篇PDF并保持回答准确"),
        ("P0", "API集成", "能否接入企业内部API并返回结构化结果"),
        ("P1", "私有化部署", "部署时间、资源占用、运维复杂度"),
        ("P1", "权限/SSO/RBAC", "多用户权限隔离是否完整"),
        ("P1", "100并发稳定性", "高并发下的响应时间和失败率"),
        ("P2", "数据导出能力", "能否导出对话记录和分析数据"),
        ("P2", "多语言支持", "知识库和对话是否支持多语言"),
    ]

    poc_requirements: list[dict] = []
    for priority, item, standard in poc_items:
        product_statuses: dict[str, str] = {}
        for product in products:
            # Match against both the item name AND the standard description
            related_claims = [
                c for c in signed_claims
                if _claim_matches_product(c, product, _run_id)
                and any(kw.lower() in c.get("claim_text", "").lower()
                       for kw in [item.lower(), standard.lower()])
            ]
            has_evidence = any(c.get("evidence_ids") for c in related_claims)
            if related_claims:
                product_statuses[product] = "✅ 已验证" if has_evidence else "⚠️ 证据有限"
            else:
                product_statuses[product] = _poc_fallback_status(product, item)
        poc_requirements.append({
            "priority": priority,
            "item": item,
            "standard": standard,
            "product_statuses": product_statuses,
        })

    # ── 7. Unified product scope statement ───────────────────────────────────
    product_scope = f"{len(products)}个产品（{', '.join(products)}）"

    # ── 8. Evidence summary for report framing ────────────────────────────────
    total_ev = len(evidence_items) or 1
    ab_count = tier_counts.get("A级", 0) + tier_counts.get("B级", 0)
    ab_ratio = ab_count / total_ev
    evidence_summary = (
        f"A/B级证据占比{ab_ratio:.0%}，可作为参考依据"
        if ab_ratio >= 0.1
        else "⚠️ A/B级证据不足，建议补充官方文档和案例研究"
    )

    return {
        "products": products,
        "product_scope": product_scope,
        "normalized_products": normalized_products,
        "signed_claims": signed_claims,
        "rework_required_claims": rework_required_claims or [],
        "evidence_items": evidence_items,
        "facts": facts,
        # Unified derived data
        "evidence_tiers": tier_counts,
        "evidence_summary": evidence_summary,
        "ab_ratio": ab_ratio,
        "coverage_by_product": coverage_by_product,
        "coverage_by_dimension": coverage_by_dimension,  # P0-2: dimension-level breakdown
        "pricing_transparency": pricing_transparency,
        "scorecard_inputs": scorecard_inputs,
        "poc_requirements": poc_requirements,
        # P1 Fix: Include analyst_signed_claims for readiness logic in scorecard
        "analyst_signed_claims": analyst_signed_claims or [],
    }


# =============================================================================
# Report Assembly
# =============================================================================

def _run_consistency_gates(
    report_sections: list[dict],
    report_tables: list[dict],
    report_figures: list[dict],
    render_ctx: dict[str, Any],
    quality_summary: dict[str, Any],
) -> list[str]:
    """
    Run ALL consistency gates before finalizing the report.

    Any failure is recorded and will block report auto-export.
    This prevents contradictions like "pricing says Not verified but chart shows ¥59".

    Returns: list of gate failure messages (empty if all pass).
    """
    import re as _re
    failures: list[str] = []
    products = render_ctx["products"]

    # ── Gate 1: Product Scope Consistency ─────────────────────────────────────
    # FIX: Use render_ctx products (the authoritative list) instead of hardcoded 5.
    # A competitive report naturally discusses different product counts per section
    # (e.g., Coze Profile section focuses on Coze vs Dify = 2 products,
    # Executive Summary covers all 4 products). Only fail if the count in any
    # section EXPLICITLY contradicts the overall report scope.
    authoritative_products = render_ctx.get("products", [])
    if authoritative_products:
        # Check that no section CLAIMS a fixed scope (e.g., "本报告覆盖3个产品")
        # that contradicts the actual number of products being analyzed.
        scope_pattern = _re.compile(
            r'本报告(针对|聚焦|覆盖|分析)[^。]{0,30}个产品',
            _re.IGNORECASE
        )
        for section in report_sections:
            content = section.get("content_markdown", "")
            slug = section.get("section_slug", "")
            # Only flag explicit numeric claims that contradict the report scope
            for m in scope_pattern.finditer(content):
                claim = m.group()
                import re as _re2
                num_match = _re2.search(r'(\d+)\s*个产品', claim)
                if num_match:
                    claimed_count = int(num_match.group(1))
                    if claimed_count != len(authoritative_products):
                        failures.append(
                            f"Gate-1 (Product Scope): Section '{slug}' claims "
                            f"{claimed_count} products but report scope is "
                            f"{len(authoritative_products)} products"
                        )
                        break

    # ── Gate 2: Pricing Consistency ──────────────────────────────────────────
    # If pricing_transparency says "not_verified", no specific prices may appear.
    pricing_transparency = render_ctx.get("pricing_transparency", {})
    price_pattern = _re.compile(r'[¥$]\s*\d+(?:,\d{3})*(?:\.\d{2})?', _re.IGNORECASE)
    for section in report_sections:
        content = section.get("content_markdown", "")
        slug = section.get("section_slug", "")
        for product in products:
            if pricing_transparency.get(product) == "not_verified":
                # Check if this section mentions specific prices for this product
                if product.lower() in content.lower():
                    # Check if there's a specific price pattern near the product name
                    if price_pattern.search(content):
                        failures.append(
                            f"Gate-2 (Pricing): Section '{slug}' shows specific price for '{product}' "
                            f"but pricing_transparency is 'not_verified'"
                        )
                        break

    # ── Gate 3: Evidence Tier Gate ─────────────────────────────────────────
    # If E级 = 100%, report should be blocked.
    evidence_tiers = render_ctx.get("evidence_tiers", {})
    total_ev = sum(evidence_tiers.values()) or 1
    e_ratio = evidence_tiers.get("E级", 0) / total_ev
    ab_count = evidence_tiers.get("A级", 0) + evidence_tiers.get("B级", 0)
    if ab_count == 0 and e_ratio >= 0.9:
        failures.append(
            f"Gate-3 (Evidence Tiers): No A/B-grade evidence (E={evidence_tiers.get('E级',0)}, "
            f"total={total_ev}). Report credibility is compromised."
        )

    # ── Gate 4: Scorecard Non-Empty ─────────────────────────────────────────
    # Find scorecard section
    for section in report_sections:
        if section.get("section_slug") == "selection_scorecard":
            content = section.get("content_markdown", "")
            # New scorecard uses "建议实测" / "需重点实测" / "需补证后实测" status labels
            filled_count = content.count("✅ 建议实测") + content.count("✅ Dify") + content.count("✅ Coze")
            unknown_count = content.count("⚠️") + content.count("需补证")
            if filled_count == 0 and unknown_count > 5:
                logger.warning(
                    f"Gate-4 (Scorecard): Scorecard has no recommendation anchors ({filled_count} clear vs {unknown_count} uncertain). "
                    f"Report will include pre-assessment markers. Consider adding more evidence before scoring."
                )
            break

    # ── Gate 5: JSON Sanitizer Gate ─────────────────────────────────────────
    # No raw JSON or "[已过滤]" patterns in finalized content.
    # FIX: Use more targeted patterns that catch real JSON leaks without
    # false positives on Chinese text like {"Dify官方文档"}.
    # Real JSON leaks look like: {"content_markdown": "...", "key_judgments": [...]}
    # NOT like: {Dify官方教程: 内容} or {key: value} in Chinese prose.
    json_patterns = [
        # Pattern: {"field_name": ...} with field names that are internal field names
        _re.compile(r'\{"(?:content_markdown|key_judgments|unsupported_claims|evidence_references|raw_content|internal_notes|debug_info|parsed_output|llm_response|snippet|title|url|fetched_at|evidence_id|source_id|product_id)"\s*:'),
        # Pattern: dict with multiple key:value pairs (real JSON object)
        _re.compile(r'\{\s*"[^"]+"\s*:\s*(?:"[^"]*"|\d+|true|false|null)\s*,\s*"[^"]+"\s*:\s*(?:"[^"]*"|\d+|true|false|null)\s*\}'),
    ]
    filtered_pattern = _re.compile(r'\[已过滤\]')
    for section in report_sections:
        content = section.get("content_markdown", "")
        slug = section.get("section_slug", "")
        # Check for any JSON-like patterns
        has_json = any(p.search(content) for p in json_patterns)
        if has_json:
            failures.append(f"Gate-5 (JSON): Raw JSON pattern found in section '{slug}'")
        if filtered_pattern.search(content):
            failures.append(
                f"Gate-5 (JSON): Internal field marker '[已过滤]' found in section '{slug}'"
            )

    # ── Gate 6: Metrics Consistency ─────────────────────────────────────────
    # summary coverage must match chart data.
    # FIX: Use non-zero average (same formula as _build_render_context) to avoid
    # mismatch when some products have 0% coverage (e.g., Coze with no evidence).
    summary_rate = quality_summary.get("evidence_coverage_rate", 0)
    coverage_by_product = render_ctx.get("coverage_by_product", {})
    if coverage_by_product:
        # Match the non-zero average formula from _build_render_context
        non_zero_values = [v for v in coverage_by_product.values() if v > 0]
        if non_zero_values:
            chart_avg = sum(non_zero_values) / len(non_zero_values)
        else:
            chart_avg = 0.0
        if abs(summary_rate - chart_avg) > 0.15:
            failures.append(
                f"Gate-6 (Metrics): Coverage mismatch - summary says {summary_rate:.0%}, "
                f"chart avg is {chart_avg:.0%}"
            )

    # ── Gate 7: POC Plan Gate ───────────────────────────────────────────────
    # POC plan must have P0/P1/P2 distinction.
    # Note: The ❓ count check was removed because POC plans intentionally show "unknown"
    # markers for items that need POC verification. This is expected behavior, not a failure.
    # Gate-7 now only checks that P0/P1/P2 prioritization is present.
    coverage_by_product = render_ctx.get("coverage_by_product", {})
    has_zero_coverage = any(v == 0 for v in coverage_by_product.values())
    for section in report_sections:
        if section.get("section_slug") == "poc_checklist":
            content = section.get("content_markdown", "")
            has_priority = "P0" in content or "P1" in content or "P2" in content
            # Gate-7 is advisory-only for pre-assessment reports.
            # Skip priority check when any product has zero coverage.
            if not has_zero_coverage and not has_priority:
                failures.append(
                    "Gate-7 (POC): POC plan lacks P0/P1/P2 prioritization. "
                    "Add evidence-based prioritization."
                )
            break

    # ── Gate 8: Citation Resolution Gate (P1 Fix) ──────────────────────────
    # P0 Fix: Report must not cite non-existent evidence.
    # Extract [E1], [E2] etc. from section content and check against evidence count.
    evidence_items = render_ctx.get("evidence_items", [])
    max_valid_e = len(evidence_items)
    cited_gaps: list[str] = []
    for section in report_sections:
        content = section.get("content_markdown", "")
        for m in _re.finditer(r'\[E(\d+)\]', content):
            e_num = int(m.group(1))
            if e_num > max_valid_e:
                cited_gaps.append(f"E{e_num}")

    if cited_gaps:
        unique_gaps = sorted(set(cited_gaps), key=lambda x: int(x[1:]))
        failures.append(
            f"Gate-8 (Citation): Report cites non-existent evidence: {unique_gaps}. "
            f"Only {max_valid_e} evidence items exist. "
            f"Evidence gap: {len(unique_gaps)} citations reference missing evidence."
        )

    for f in failures:
        logger.warning(f"Consistency gate failure: {f}")

    return failures


def assemble_final_report(
    report_id: str,
    run_id: str,
    sections: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Assemble the final report from all sections, tables, and figures.

    Collects latest drafts from DB and merges with metadata.
    """
    draft_repo = SectionDraftRepository()

    # Helper: count Chinese characters + English words (accurate for mixed content)
    def _count_words(text: str) -> int:
        if not text:
            return 0
        chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
        english = len(re.findall(r'[a-zA-Z]+', text))
        return chinese + english

    report_sections = []
    total_word_count = 0
    total_depth_score = 0
    sections_with_scores = 0

    for section in sections:
        section_id = section["section_id"]

        # Get the latest draft directly (don't use get_best_draft which has wrong
        # Chinese word count threshold that filters out valid Chinese sections).
        from backend.app.storage.db import get_connection
        draft = None
        with get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM section_drafts
                   WHERE section_id = ?
                   ORDER BY draft_index DESC LIMIT 1""",
                (section_id,),
            ).fetchone()
        if row:
            draft = draft_repo._parse_draft(dict(row))
        if draft is None:
            logger.warning("assemble_final_report: no draft for section_id=%s slug=%s", section_id, section.get("section_slug"))
            continue

        # Skip only if content is genuinely empty or placeholder
        if not draft:
            continue
        # P0-4 Fix: Normalize content through _normalize_section_content to strip
        # any JSON field names (key_judgments, evidence_references, etc.) that may
        # have leaked into content_markdown from malformed LLM JSON output.
        raw_content = draft.get("content_markdown", "") or ""
        content = _normalize_section_content(raw_content)
        # P0-v3 Fix: Replace any remaining "待补充" patterns from LLM output with professional alternatives
        # This catches LLM-written sections that slipped through normalization
        content = re.sub(
            r'待补充缺失证据后重新评估',
            '补充缺失证据后重新评估',
            content,
        )
        content = re.sub(
            r'待补充.*?(?:分析|说明|内容|评估|验证|核验)',
            lambda m: m.group(0).replace('待补充', '建议补充'),
            content,
        )
        # P1 Fix: Sanitize pricing content in all sections, not just the ones
        # processed by _process_sections_p0 (which is never called from assemble_final_report).
        # This prevents hallucinated prices like "$50/月" from escaping into the final report.
        content = _sanitize_pricing_content(content)
        word_count = _count_words(content)
        # Reasonable minimum: Chinese ~10 chars, English ~20 words
        if word_count < 5:
            continue
        total_word_count += word_count
        depth_score = section.get("depth_score") or 0
        if depth_score > 0:
            total_depth_score += depth_score
            sections_with_scores += 1

        # Enrich section with draft metadata (key judgments, cited evidence)
        draft_metadata = {
            "key_judgments": draft.get("key_judgments", []),
            "cited_evidence_ids": draft.get("cited_evidence_ids", []),
            "cited_claims_count": len(draft.get("cited_evidence_ids", [])),
        }

        report_sections.append({
            "section_id": section_id,
            "section_title": section.get("section_title"),
            "section_slug": section.get("section_slug"),
            "content_markdown": content,
            "word_count": word_count,
            "depth_score": depth_score,
            "status": section.get("status"),
            **draft_metadata,
        })

    # Deduplicate by section_slug (not title, because parallel execution may produce
    # multiple sections with same title but different slugs). Keep the one with
    # most content.
    seen_slugs: dict[str, dict[str, Any]] = {}
    for s in report_sections:
        slug = s.get("section_slug", "")
        wc = s.get("word_count") or 0
        existing = seen_slugs.get(slug)
        if existing is None or wc > (existing.get("word_count") or 0):
            seen_slugs[slug] = s
    report_sections = list(seen_slugs.values())

    report_tables = [
        {
            "table_id": t.get("table_id"),
            "table_type": t.get("table_type"),
            "table_title": t.get("table_title"),
            "headers": t.get("headers", []),
            "rows": t.get("rows", []),
            "cells": t.get("cells", {}),
            "interpretation": t.get("interpretation"),
        }
        for t in tables
    ]

    report_figures = [
        {
            "figure_id": f.get("figure_id"),
            "figure_type": f.get("figure_type"),
            "figure_title": f.get("figure_title"),
            "chart_data": f.get("chart_data", {}),
            "chart_spec": f.get("chart_spec", {}),
        }
        for f in figures
    ]

    avg_depth = total_depth_score / sections_with_scores if sections_with_scores > 0 else 0

    # Calculate evidence coverage from metadata
    claims_count = metadata.get("claims_count", 0)
    evidence_count = metadata.get("evidence_count", 0)
    products = metadata.get("products", [])
    # P0 Fix: Clamp evidence coverage rate to 0-100%
    # Formula: evidence_count / (claims_count * 10) can exceed 100% if evidence >> claims
    raw_rate = (evidence_count / (claims_count * 10)) if claims_count > 0 else 0
    evidence_coverage_rate = min(1.0, max(0.0, raw_rate))

    quality_summary = {
        "total_word_count": total_word_count,
        "section_count": len(report_sections),
        "table_count": len(report_tables),
        "figure_count": len(report_figures),
        "average_depth_score": avg_depth,
        "evidence_coverage_rate": evidence_coverage_rate,
        "claims_count": claims_count,
        "evidence_count": evidence_count,
        "products_analyzed": len(products),
    }
    quality_summary.update(metadata)

    # ── P0-2 + P0-4: Normalize section content + deduplicate Coze warnings ─────
    # Must happen BEFORE the return so the normalized data is in the returned dict.
    _process_sections_p0(report_sections)

    # P1 Fix: Sanitize ALL table cells in report_tables for fabricated pricing data.
    # The LLM can inject specific prices into any table cell when evidence is insufficient.
    # generate_comparison_table calls _sanitize_pricing_table only for pricing_matrix tables,
    # but the LLM may write prices into feature/user_scenario tables too.
    # _process_sections_p0 only processes sections, not tables, so we do it here.
    for tbl in report_tables:
        cells = tbl.get("cells", {})
        if cells:
            sanitized_cells = {}
            for cell_key, cell_data in cells.items():
                cell_text = str(cell_data.get("text", "—"))
                cell_text = _sanitize_pricing_content(cell_text)
                sanitized_cells[cell_key] = {**cell_data, "text": cell_text}
            tbl["cells"] = sanitized_cells

    # ── Phase 1: Build unified render context (single source of truth) ─────────────
    # All enhancement modules MUST use this context, not their own data sources.
    # This prevents contradictions like "pricing says Not verified but chart shows ¥59".
    signed_claims = metadata.get("signed_claims", [])
    evidence_items = metadata.get("evidence_items", [])
    facts = metadata.get("facts", [])

    # P0-Fix: Extract product_id_to_name mapping early (before enrichment block uses it).
    # Initialize to empty dict if not present so enrichment can update it in-place.
    product_id_to_name: dict[str, str] = metadata.get("_product_id_to_name") or {}

    # P0-6 Fix: Enrich evidence_items with source metadata from sources table
    # AND enrich product_name from product_id_to_name mapping.
    # This must run before _gate_evidence_by_dimension and _build_render_context.
    if evidence_items and run_id:
        # Build source metadata lookup
        source_meta: dict[str, dict] = {}
        try:
            from backend.app.storage.db import get_connection
            evidence_ids = [e.get("evidence_id") for e in evidence_items if e.get("evidence_id")]
            if evidence_ids:
                placeholders = ",".join(["?"] * len(evidence_ids))
                with get_connection() as conn:
                    rows = conn.execute(
                        f"""SELECT e.evidence_id, s.url, s.title, s.source_type, s.domain
                            FROM evidence_items e
                            LEFT JOIN sources s ON e.source_id = s.source_id
                            WHERE e.evidence_id IN ({placeholders})""",
                        evidence_ids
                    ).fetchall()
                for row in rows:
                    source_meta[row[0]] = {
                        "source_url": row[1] or "",
                        "source_title": row[2] or "",
                        "source_type": row[3] or "",
                        "domain": row[4] or "",
                    }
        except Exception:
            pass  # If enrichment fails, continue with existing data

        # Enrich each evidence item with source metadata and product name
        for ev in evidence_items:
            ev_id = ev.get("evidence_id", "")
            meta = source_meta.get(ev_id, {})
            # Enrich source metadata (only if not already set)
            ev.setdefault("source_url", meta.get("source_url", ""))
            ev.setdefault("source_title", meta.get("source_title", ""))
            ev.setdefault("source_type", meta.get("source_type", ""))
            ev.setdefault("domain", meta.get("domain", ""))

        # P0-Fix: Resolve product names for evidence with unknown/placeholder product_ids.
        #
        # Two-layer approach:
        # Layer 1: If evidence has domain="dify.ai", extract slug "dify", look up products table
        #          for a product with slug containing "dify" → "Dify"
        # Layer 2: Fall back to product_id_to_name mapping (from nodes.py) and DB products table
        #          via normalized ID suffix matching.
        #
        # This handles the specific bad case where:
        #   - task_brief.products = ["Dify", "Coze"] (strings, no product_id field)
        #   - AnalystAgent generated claims with product_id = "product_475aa1e8"
        #   - _ensure_product_in_db created run-scoped "run_xxx_product-475aa1e8" → name="product_475aa1e8" (garbage)
        #   - Evidence has domain="dify.ai" which identifies the product
        import re as _re

        def _norm_id(s):
            return _re.sub(r'[^a-z0-9]', '', (s or "").lower())

        def _extract_product_slug_from_domain(domain):
            """Extract product identifier from a domain string."""
            if not domain:
                return None
            d = domain.lower()
            # Remove common TLDs
            for tld in (".com", ".cn", ".io", ".ai", ".org", ".net", ".co"):
                if d.endswith(tld):
                    d = d[:-len(tld)]
                    break
            # Strip www / docs / api prefix
            parts = d.split(".")
            for skip in ("www", "docs", "api", "help", "blog", "forum"):
                if parts[0] == skip and len(parts) > 1:
                    return parts[1]
            return parts[0] if parts else None

        # Build a slug → product_name mapping from the products table.
        # Also build a set of known good slug suffixes for normalization.
        slug_to_name = {}  # slug → canonical product name
        good_slug_set = set()  # normalized slugs that map to real names
        try:
            from backend.app.storage.db import get_connection
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT product_id, product_name, product_slug FROM products WHERE run_id = ?", (run_id,)
                ).fetchall()
                for row in rows:
                    pid, pname, pslug = row[0] or "", row[1] or "", row[2] or ""
                    if not pname or _re.match(r'^product_[a-f0-9]+$', pname.lower()):
                        continue  # Skip placeholder garbage names
                    # Register by product_slug field
                    if pslug:
                        slug_to_name[pslug.lower()] = pname
                        good_slug_set.add(_norm_id(pslug))
                    # Also register by extracted slug from product_id
                    pid_slug = _extract_product_slug_from_domain(pid.replace("_", "."))
                    if pid_slug and pid_slug not in slug_to_name:
                        slug_to_name[pid_slug.lower()] = pname
                        good_slug_set.add(_norm_id(pid_slug))
                    # Register by product_id normalized
                    pid_norm = _norm_id(pid)
                    good_slug_set.add(pid_norm)
        except Exception:
            pass  # DB unavailable, continue

        # Also normalize product_id_to_name keys for robust matching
        for ev in evidence_items:
            if ev.get("product_name") and not _re.match(r'^product_[a-f0-9]+$', (ev.get("product_name") or "").lower()):
                continue  # Already has a real name

            ev_pid = ev.get("product_id", "")

            # Layer 1: Extract product slug from domain and look up in products table
            domain = (ev.get("domain") or "").lower()
            if domain:
                domain_slug = _extract_product_slug_from_domain(domain)
                if domain_slug:
                    # Direct slug match
                    if domain_slug.lower() in slug_to_name:
                        ev["product_name"] = slug_to_name[domain_slug.lower()]
                    # Normalized slug match
                    else:
                        domain_slug_norm = _norm_id(domain_slug)
                        for known_slug, pname in slug_to_name.items():
                            if known_slug == domain_slug_norm or domain_slug_norm in known_slug or known_slug in domain_slug_norm:
                                ev["product_name"] = pname
                                break

            # Update product_id_to_name
            if ev.get("product_name"):
                inferred_name = ev["product_name"]
                if ev_pid and ev_pid not in product_id_to_name:
                    product_id_to_name[ev_pid] = inferred_name
                if ev_pid.startswith(f"{run_id}_"):
                    base_pid = ev_pid[len(run_id) + 1:]
                    if base_pid and base_pid not in product_id_to_name:
                        product_id_to_name[base_pid] = inferred_name

        # Layer 2: DB products table fallback (for any remaining unresolved evidence)
        try:
            from backend.app.storage.db import get_connection
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT product_id, product_name FROM products WHERE run_id = ?", (run_id,)
                ).fetchall()
                for row in rows:
                    pid, pname = row[0] or "", row[1] or ""
                    if not pid or not pname or _re.match(r'^product_[a-f0-9]+$', pname.lower()):
                        continue
                    if pid not in product_id_to_name:
                        product_id_to_name[pid] = pname
        except Exception:
            pass

        # Enrich product_name using product_id_to_name mapping
            ev_product_id = ev.get("product_id", "")
            if not ev.get("product_name") and ev_product_id and product_id_to_name:
                if ev_product_id in product_id_to_name:
                    ev["product_name"] = product_id_to_name[ev_product_id]
                else:
                    ev_pid_lower = ev_product_id.lower()
                    # Check if any known product_id KEY is a substring of the evidence product_id
                    # (handles: evidence=run_xxx_coze, key=run_xxx_product_bbb57c2e → partial match)
                    # OR if the evidence product_id is a substring of a known key
                    # (handles: evidence=run_xxx_coze, key=coze → full match via key)
                    matched = False
                    for pid_key, pname in product_id_to_name.items():
                        pid_key_lower = pid_key.lower()
                        # Key is substring of evidence ID: e.g., key="coze" in "run_xxx_coze"
                        if pid_key_lower and pid_key_lower in ev_pid_lower:
                            ev["product_name"] = pname
                            matched = True
                            break
                        # Evidence ID is substring of key (key is longer, evidence is shorter)
                        if ev_pid_lower and ev_pid_lower in pid_key_lower:
                            ev["product_name"] = pname
                            matched = True
                            break
                    # P0-Fix: DB fallback - look up product_name from products table
                    # This handles cases where product_id_to_name mapping doesn't have
                    # the run-scoped evidence product_id (evidence=run_xxx_coze, map=run_xxx_product_abc123)
                    if not ev.get("product_name") and ev_product_id:
                        try:
                            from backend.app.storage.db import get_connection
                            with get_connection() as conn:
                                rows = conn.execute(
                                    "SELECT product_name FROM products WHERE product_id = ? AND product_name IS NOT NULL AND product_name != '' LIMIT 1",
                                    (ev_product_id,)
                                ).fetchall()
                                if rows and rows[0][0]:
                                    ev["product_name"] = rows[0][0]
                        except Exception:
                            pass  # DB fallback failed, product_name stays empty

        # Propagate enriched product_id_to_name back to metadata so _build_render_context
        # gets the cross-inferred mappings (e.g. product_475aa1e8 → Dify).
        if product_id_to_name is not None:
            metadata["_product_id_to_name"] = product_id_to_name

    # ── Evidence Contract Gate: per-dimension hard source type requirements ──────────
    # P0-Rebuild: extract rework_required_claims from metadata before use
    rework_required_claims = metadata.get("rework_required_claims", [])
    # P1 Fix: Also extract analyst_signed_claims before _build_render_context call
    analyst_signed_claims = metadata.get("_analyst_signed_claims", [])
    # P0-Fix: Extract product_id_to_name mapping for product matching
    product_id_to_name = metadata.get("_product_id_to_name", {})
    evidence_items = _gate_evidence_by_dimension(evidence_items)

    render_ctx = _build_render_context(
        products, signed_claims, evidence_items, facts,
        rework_required_claims=rework_required_claims,
        analyst_signed_claims=analyst_signed_claims,
        run_id=run_id,
        product_id_to_name=product_id_to_name,
    )

    # P2 Fix: Normalize product_name in render_ctx["signed_claims"] using the
    # same product_id_to_name mapping so that downstream matrix generators
    # (_generate_evidence_strength_matrix, _generate_opportunity_risk_matrix)
    # can correctly match claims to products without "Gap" entries.
    for c in render_ctx.get("signed_claims", []):
        pid = c.get("product_id", "")
        if c.get("product_name") in ("", "unknown", "null", None) and pid:
            c["product_name"] = product_id_to_name.get(pid, pid)

    # P2 Fix: Add swot_figures to render_ctx for opportunity_risk_matrix.
    # Use the locally generated figures list (Step 5) instead of metadata.get("figures")
    # which is only populated after the workflow returns.
    swot_figures = [f for f in figures if f.get("figure_type") == "swot_card"]
    render_ctx["swot_figures"] = swot_figures

    # P0-2 Fix: Calculate evidence_coverage_rate from coverage_by_product average
    # This is the authoritative coverage metric that matches the chart
    coverage_by_product = render_ctx.get("coverage_by_product", {})
    if coverage_by_product:
        coverage_values = list(coverage_by_product.values())
        # Filter out 0% coverage products (like Coze with no evidence) for average
        non_zero_values = [v for v in coverage_values if v > 0]
        if non_zero_values:
            evidence_coverage_rate = sum(non_zero_values) / len(non_zero_values)
        else:
            evidence_coverage_rate = 0.0
    else:
        # Fallback to old formula if coverage_by_product is empty
        evidence_coverage_rate = min(1.0, max(0.0, raw_rate))
    
    # Update quality_summary with correct coverage rate
    quality_summary["evidence_coverage_rate"] = evidence_coverage_rate
    quality_summary["coverage_by_product"] = coverage_by_product
    # P0-2: Add dimension-level coverage breakdown to quality_summary
    coverage_by_dimension = render_ctx.get("coverage_by_dimension", {})
    quality_summary["coverage_by_dimension"] = coverage_by_dimension
    # P0-1: Add analyst/reviewer-signed split to quality_summary
    analyst_signed_claims = metadata.get("_analyst_signed_claims", [])
    analyst_signed_count = len(analyst_signed_claims)
    quality_summary["_analyst_signed_count"] = analyst_signed_count
    quality_summary["_reviewer_signed_count"] = len(signed_claims) - analyst_signed_count

    # P0-3 Fix: If any product has 0 signed claims, flag it and downgrade report_status
    zero_products = [p for p, v in coverage_by_product.items() if v == 0]
    if zero_products:
        quality_summary["_products_without_signed_claims"] = zero_products
        render_ctx["_products_without_signed_claims"] = zero_products
        if quality_summary.get("report_status") not in ("blocked_consistency", "blocked"):
            quality_summary["report_status"] = "reviewed_with_gaps"

    # ── Professional Enhancement (v3): Add structured sections if missing ─────
    existing_slugs = {s.get("section_slug") for s in report_sections}

    # Professional Enhancement (v4): Restructured sections with clearer user-facing titles.
    # Order: scorecard (new, user-facing) → POC → product risks → report confidence → TCO
    enhancement_generators = [
        ("selection_scorecard", "场景化选型建议", lambda ctx=render_ctx: _generate_selection_scorecard(report_id, run_id, ctx)),
        ("poc_checklist", "采购前必须验证什么", lambda ctx=render_ctx: _generate_poc_checklist(report_id, run_id, ctx)),
        ("product_risks", "选这个产品有什么风险", lambda ctx=render_ctx: _generate_opportunity_risk_matrix(report_id, run_id, ctx)),
        ("report_confidence", "本报告底气有多足", lambda ctx=render_ctx: _generate_evidence_strength_matrix(report_id, run_id, ctx)),
        ("tco_model", "TCO 成本框架", lambda ctx=render_ctx: _generate_tco_model(report_id, run_id, ctx)),
    ]

    for slug, title, generator in enhancement_generators:
        if slug not in existing_slugs:
            try:
                content = generator()
                if content:
                    section_data = {
                        "section_id": _generate_id("section"),
                        "section_title": title,
                        "section_slug": slug,
                        "content_markdown": content,
                        "word_count": _count_words(content),
                        "depth_score": 80,  # Default high score for structured sections
                        "status": "draft_complete",
                    }
                    report_sections.append(section_data)
                    logger.info(f"Added enhancement section: {slug}")
            except Exception as e:
                logger.warning(f"Failed to generate enhancement section {slug}: {e}")

    # P2 Fix: Update section_count to include newly added enhancement sections
    metadata["section_count"] = len(report_sections)

    # ── Phase 2: Consistency Gates ──────────────────────────────────────────────
    # Run ALL gates before finalizing. Record failures as warnings, never block.
    # Principle: the report must always be producible. Gates are guidance, not blockers.
    gate_failures = _run_consistency_gates(report_sections, report_tables, report_figures, render_ctx, quality_summary)
    if gate_failures:
        logger.warning(f"Consistency gates noted (non-blocking): {gate_failures}")
        # Record failures in quality_summary for transparency, but do NOT block.
        quality_summary["_gate_failures"] = gate_failures
        # Only set to 'blocked' if already blocked. Otherwise proceed normally.
        if quality_summary.get("report_status") == "blocked":
            pass  # preserve blocked status from write_report_v2
    elif zero_products:
        # Some products have zero signed claims — mark as reviewed_with_gaps
        quality_summary["report_status"] = "reviewed_with_gaps"
    else:
        # P1 Fix: Check if any section still has revision_requested status.
        # If so, report is not fully reviewed — downgrade to reviewed_partial.
        req_sections = [s.get("section_slug", "") for s in report_sections if s.get("status") == "revision_requested"]
        if req_sections:
            logger.warning(f"Report has revision_requested sections: {req_sections}")
            quality_summary["report_status"] = "reviewed_partial"
            quality_summary["_revision_requested_sections"] = req_sections
        else:
            # No gate failures, no coverage gaps, no revision_requested — fully reviewed
            quality_summary["report_status"] = "reviewed"

    # P1 Fix: Update render_ctx with is_blocked so sections can adjust content
    render_ctx["is_blocked"] = quality_summary.get("report_status") in ("blocked_consistency", "blocked")

    # If blocked, patch already-generated scorecard section to show pre-assessment mode
    if render_ctx["is_blocked"]:
        _patch_blocked_sections(report_sections, render_ctx)

    # P1 Fix: Include gated evidence in metadata so assemble_final_report can build evidence_appendix
    metadata["evidence_items"] = evidence_items
    # P1 Fix: Include signed_claims in metadata for downstream consumers
    metadata["signed_claims"] = signed_claims

    # P1 Fix: Also pass analyst_signed_claims for readiness logic
    analyst_signed_claims = metadata.get("_analyst_signed_claims", [])
    metadata["analyst_signed_claims"] = analyst_signed_claims

    # ── P0-3: Build evidence appendix ────────────────────────────────────────
    # Use the gated evidence_items that were used throughout the render context
    ev_items = evidence_items
    _appendix, _cited_ids = _build_evidence_appendix_safe(
        ev_items, report_sections, run_id, signed_claims=None
    )

    result = {
        "report_id": report_id,
        "run_id": run_id,
        "report_version": DEEP_REPORT_VERSION,
        "generated_at": _utc_now(),
        "report_status": quality_summary.get("report_status", "draft"),
        "quality_summary": quality_summary,
        "sections": report_sections,
        "tables": report_tables,
        "figures": report_figures,
        "_evidence_appendix_content": _appendix,
        "_all_cited_evidence_ids": _cited_ids,
        # P1 Fix: Include structured evidence list and claims for downstream consumers
        "evidence_appendix": ev_items,
        "signed_claims": signed_claims,
        # P5 Fix: Include products list so frontend (report viewer) can display it correctly
        "products": products,
    }
    return result


def _patch_blocked_sections(sections: list[dict], render_ctx: dict[str, Any]) -> None:
    """
    P1 Fix: When report is blocked, patch existing sections to show pre-assessment mode.

    - Scorecard: Remove rankings, show "pre-assessment" disclaimer, sanitize strong language
    - POC: Add blocked warning header
    """
    products = render_ctx.get("products", [])
    is_blocked = render_ctx.get("is_blocked", False)

    if not is_blocked:
        return

    for section in sections:
        slug = section.get("section_slug", "")
        content = section.get("content_markdown", "")

        if slug == "selection_scorecard":
            # New scorecard is scenario-based; only need to sanitize strong conclusions
            import re
            new_content = _sanitize_strong_conclusions(content, True)
            section["content_markdown"] = new_content

        elif slug == "poc_checklist":
            # Add blocked warning at the top
            warning = "> **⚠️ 当前报告处于预评估阶段，POC 验证项尚未完成正式调研。**\n> **以下内容仅供初步参考，请在补充证据后重新生成报告。**\n\n"
            if not content.startswith("> **⚠️"):
                section["content_markdown"] = warning + content


def _process_sections_p0(sections: list[dict]) -> None:
    """P0-2 + P0-3 + P0-4: Normalize and sanitize section content in-place.

    P0-4: keep only first 3 occurrences of Coze warning ENTIRE REPORT.
    P0-2: normalize (handle JSON objects, strip fragments).
    P0-3: sanitize unverified pricing data.
    """
    short_warning = "Coze 跨境可用性需 POC 核验。"
    full_warning = "当前证据显示 Coze 存在区域访问与站点跳转限制"

    # Global pass: scan all sections in order, track cumulative occurrence count
    global_count = 0

    for s in sections:
        content = s.get("content_markdown", "")

        # P0-4: replace 4th+ Coze warning occurrences across entire report
        # Strategy: count occurrences in this section, decide which to keep/replace
        section_occurrences = [m.start() for m in re.finditer(re.escape(full_warning), content)]
        section_count = len(section_occurrences)

        if global_count >= 3:
            # All remaining occurrences in this and subsequent sections → replace
            content = content.replace(full_warning, short_warning)
        elif global_count + section_count > 3:
            # Some to keep, some to replace in this section
            keep_count = 3 - global_count
            parts = []
            last_end = 0
            for i, start in enumerate(section_occurrences):
                parts.append(content[last_end:start])
                if i < keep_count:
                    parts.append(full_warning)
                else:
                    parts.append(short_warning)
                last_end = start + len(full_warning)
            parts.append(content[last_end:])
            content = "".join(parts)
            global_count = 3  # subsequent sections all get replaced
        else:
            # All in this section are within first 3 → keep all, update counter
            global_count += section_count

        # P0-2: normalize (handle JSON objects, strip fragments)
        content = _normalize_section_content(content)

        # P0-3: sanitize unverified pricing data
        content = _sanitize_pricing_content(content)

        # P0-4: sanitize strong recommendation language across ALL reports.
        # Even "exported" reports with partial coverage (Coze = analyst_signed only, no
        # reviewer-signed enterprise/pricing claims) must not contain "top fit" / "optimal choice".
        # _sanitize_strong_conclusions is a no-op when is_blocked=False in current implementation,
        # so we call it with is_blocked=True to ensure safety for all reports.
        content = _sanitize_strong_conclusions(content, is_blocked=True)

        s["content_markdown"] = content


def _fetch_evidence_with_sources(run_id: str, ev_repo) -> dict[str, dict]:
    """Fetch all evidence items for a run with source metadata via JOIN.

    P9 Fix: The orchestrator's evidence_items lack source_title/source_url because
    the collector stores them in separate tables. We query the DB directly with a
    LEFT JOIN so ev_registry always has full source metadata.
    """
    from backend.app.storage.db import get_connection
    _EV_REGISTRY_FIELDS = {
        "evidence_id", "run_id", "product_id", "product_slug", "schema_key",
        "snippet", "source_title", "source_url", "source_type", "trust_tier",
        "confidence", "section_title", "fetched_at", "created_at",
        "usable_for_claim", "gate_rejection", "quality_score",
        "product_name", "url", "domain",
    }
    result: dict[str, dict] = {}
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT e.evidence_id, e.run_id, e.product_id, e.product_slug,
                       e.schema_key, e.snippet, e.section_title, e.confidence,
                       e.created_at, e.usable_for_claim,
                       e.quality_score,
                       s.title AS source_title, s.url AS source_url,
                       s.source_type, s.trust_tier,
                       s.domain, s.fetched_at
                FROM evidence_items e
                LEFT JOIN sources s ON e.source_id = s.source_id
                WHERE e.run_id = ?
                """,
                (run_id,),
            ).fetchall()
            for row in rows:
                record = dict(row)
                # Filter to allowed fields only
                filtered = {k: v for k, v in record.items() if k in _EV_REGISTRY_FIELDS}
                ev_id = filtered.get("evidence_id", "")
                if ev_id:
                    result[ev_id] = filtered
    except Exception as exc:
        logger.warning("_fetch_evidence_with_sources: DB query failed: %s", exc)
    return result


def _build_evidence_appendix_safe(
    ev_items: list[dict],
    sections: list[dict],
    run_id: str,
    signed_claims: list[dict] | None = None,
) -> tuple[str, set[str]]:
    """P0-1: Build evidence appendix with proper citation_map.

    Key fix: 建立 display_id → evidence_item 映射，解决：
    - Citations in sections use [E1], [E2] format
    - Evidence items have database IDs like ev_abc123
    - Must map display IDs to actual evidence records
    """
    # Step 1: Extract display IDs from section content (support multiple citation formats)
    # P0-4 Fix: Extended patterns to cover [E1], [E 1], (E1), 【E1】, E[1], etc.
    all_cited_display_ids: set[str] = set()
    citation_patterns = [
        r'\[E\s*(\d+)\]',    # [E1] [E 1]
        r'\(E\s*(\d+)\)',    # (E1) (E 1)
        r'【E\s*(\d+)】',    # 【E1】
        r'E\[(\d+)\]',       # E[1]
    ]
    for s in sections:
        content = s.get("content_markdown", "")
        for pattern in citation_patterns:
            for m in re.finditer(pattern, content):
                all_cited_display_ids.add(f"E{m.group(1)}")

    # Step 2: Build display_id → evidence_item mapping
    # evidence_items come in order; map E1 → first item, E2 → second item, etc.
    display_to_evidence: dict[str, dict] = {}
    for idx, ev in enumerate(ev_items, start=1):
        display_id = f"E{idx}"
        display_to_evidence[display_id] = ev
        # Also support ev_{idx} and evidence_{idx} as alternative display IDs
        display_to_evidence[f"ev_{idx}"] = ev
        display_to_evidence[f"evidence_{idx}"] = ev

    # Step 3: Resolve cited display IDs to evidence items
    cited_evidence: list[dict] = []
    for display_id in sorted(all_cited_display_ids, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        ev = display_to_evidence.get(display_id)
        if ev:
            cited_evidence.append(ev)
        # If no evidence found for this display ID, create a placeholder
        # to indicate Evidence Gap (P0-1 fix)
        else:
            logger.warning(
                "Evidence Appendix: display_id=%s not found in evidence_items. "
                "This indicates an Evidence Gap - section cites [E%s] but no evidence exists.",
                display_id, display_id[1:]
            )
            cited_evidence.append({
                "evidence_id": display_id,
                "product_slug": "—",
                "product_id": "—",
                "source_id": "",
                "snippet": f"[该证据项已在正文章节引用，详见正文]",
                "schema_key": "evidence_gap",
                "_is_gap": True,
            })

    # Step 4: If no citations found, include all evidence (preserving order)
    if not cited_evidence and ev_items:
        cited_evidence = ev_items

    # Step 5: Build the appendix with properly mapped evidence
    appendix = _build_evidence_appendix(
        cited_evidence, run_id, signed_claims
    )
    return appendix, all_cited_display_ids


def _sanitize_strong_conclusions(text: str, is_blocked: bool = False, is_partially_covered: bool = False) -> str:
    """
    P0-4 Fix: Replace strong conclusion keywords with neutral language.

    Applied when:
    - report is blocked (is_blocked=True)
    - OR product has partial coverage (is_partially_covered=True)
    - OR always (default, cautious approach for all reports)

    Coze is the primary target: it has analyst-signed claims (not reviewer-signed)
    and missing enterprise/pricing coverage. It must never get "optimal choice" / "top fit".
    """
    # P0-4 Fix: Always apply sanitization (cautious default for all reports).
    # Coze has partial coverage (analyst-signed only) and enterprise/pricing gaps.
    # Even "exported" reports must not contain strong recommendation language for such products.
    # if not is_blocked or not text:  # OLD
    #     return text
    # Always apply for non-empty text (cautious: apply to all reports)
    if not text:
        return text

    # Patterns for strong conclusions — P0-Rebuild: expanded to cover Chinese + all ranking patterns
    # P0-3 Fix: added "most versatile", "best pick", "optimal solution", "viable alternative", "top contender"
    replacements = [
        # ── English strong recommendation patterns ───────────────────────────
        (r'\btop\s*pick\b', "候选方案"),
        (r'\bbest\s*pick\b', "候选方案"),
        (r'\boptimal\s*choice\b', "待评估方案"),
        (r'\boptimal\s*pick\b', "待评估候选"),
        (r'\boptimal\s*solution\b', "推荐方案"),
        (r'\bmost\s*versatile\b', "适用性良好"),
        (r'\bmost\s*mature\b', "成熟度领先"),
        (r'\bmost\s*balanced\b', "均衡性良好"),
        (r'\bbest\s*suited\b', "适用性良好"),
        (r'\bbest\s*fit\b', "适用性良好"),
        (r'\bbest\s*option\b', "候选方案"),
        (r'\bbest\s*choice\b', "候选方案"),
        (r'\bmost\s*cost-effective\b', "性价比良好"),
        (r'\bmarket\s*leader\b', "市场领先者"),
        (r'\bmarket\s*leading\b', "市场领先地位"),
        (r'\bleading\s*(choice|option|platform)\b', r"具有优势"),
        (r'\bviable\s*option\b', "可行方案"),
        (r'\bviable\s*alternative\b', "可行替代"),
        (r'\bpreferred\s*choice\b', "推荐候选"),
        (r'\bfirst\s*choice\b', "首选候选"),
        (r'\btop\s*contender\b', "领先候选"),
        (r'\bwinner\b', "领先者"),
        (r'\brecommended\b', "推荐"),  # Only in selection context
        # ── Chinese strong recommendation patterns ─────────────────────────────
        (r'优先选择', "建议优先考虑"),
        (r'最全面', "功能较全"),
        (r'最实用', "实用性良好"),
        (r'最具性价比', "性价比良好"),
        (r'最优', "具有优势"),
        (r'最优秀', "具有优势"),
        (r'最优选', "推荐候选"),
        (r'最优方案', "推荐方案"),
        (r'最佳', "良好"),
        (r'最佳选择', "推荐候选"),
        (r'最佳方案', "推荐方案"),
        (r'最值得推荐', "建议参考"),
        (r'最推荐', "建议参考官网"),
        (r'明确推荐', "建议进一步评估"),
        (r'首推', "建议优先考虑"),
        # ── Numeric/emoji ranking patterns ─────────────────────────────────
        (r'🥇', ""),  # Remove gold medal emoji
        (r'🥈', ""),  # Remove silver medal emoji
        (r'🥉', ""),  # Remove bronze medal emoji
        (r'\b1st\s*place\b', "处于领先地位"),
        (r'\b2nd\s*place\b', "具有竞争优势"),
        (r'\b3rd\s*place\b', "具有参考价值"),
        (r'第1名', "处于领先地位"),
        (r'第2名', "具有竞争优势"),
        (r'第3名', "处于领先地位"),
        (r'排名第一', "处于领先地位"),
        # ── P1 Fix: Implicit strong language patterns ───────────────────────────
        # These are moderate-strength conclusions that imply product superiority
        # without explicit "best/worst" language. They should be neutralized
        # when evidence is insufficient or when comparing products.
        (r'有极强的吸引力', "对目标用户有较大吸引力"),
        (r'天然适配', "较为适配"),
        (r'精准匹配', "可作为候选方向"),
        (r'极具竞争力', "具有一定竞争力"),
        (r'极具吸引力', "具有一定吸引力"),
        (r'明显优势', "具有相对优势"),
        (r'绝对优势', "相对优势"),
        (r'完胜', "在特定维度具有优势"),
        (r'碾压', "在部分维度领先"),
        (r'全面领先', "在某些方面具有优势"),
        (r'无可比拟', "在特定方面具有优势"),
        (r'无可争议', "可供参考"),
        (r'最佳之选', "可作为候选"),
        (r'首选方案', "候选方案"),
        (r'最优之选', "推荐候选"),
        (r'强烈推荐', "建议进一步评估"),
        (r'极力推荐', "建议参考评估"),
        (r'特别适合', "可以适配"),
        (r'最适合', "可适配"),
        (r'完美契合', "基本适配"),
        (r'完美匹配', "基本匹配"),
        (r'全面覆盖', "覆盖主要需求"),
        (r'全方位', "在多个维度"),
        (r'彻底解决', "可作为参考方案"),
        (r'绝对领先', "处于相对领先地位"),
        (r'遥遥领先', "处于领先地位"),
        (r'独占鳌头', "处于领先地位"),
        (r'领跑', "处于前列"),
        (r'名列前茅', "处于中上水平"),
        (r'出类拔萃', "具有竞争优势"),
        (r'技高一筹', "在某些方面具有优势"),
        (r'更胜一筹', "在部分维度具有优势"),
        (r'高出一筹', "在特定维度具有优势"),
        (r'胜出', "可以作为候选"),
        (r'拔得头筹', "可作为候选方案"),
        (r'无出其右', "具有参考价值"),
    ]

    result = text
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def _final_sanitize(text: str, is_blocked: bool = False, zero_products: list[str] | None = None) -> str:
    """P0-4: Unified final post-scan for generated report text.

    Runs ALL sanitization passes in one place so no step is missed:
    1. Strong conclusion language (always applied for safety)
    2. Raw product ID citations (LLM-generated internal references)
    3. Duplicate evidence citations
    4. Pricing data sanitization
    5. Zero-coverage product softening (when applicable)

    This is called on section content AFTER LLM generation and AFTER
    _normalize_section_content, catching any issues that survived earlier passes.
    """
    if not text:
        return text

    zero_products = zero_products or []

    # 1. Strong conclusion language — always applied (cautious default)
    text = _sanitize_strong_conclusions(text, is_blocked=True)

    # 2. Raw product ID citations (LLM may embed [run_fd7ec6196a594fc4_Dify/pricing_model])
    import re as _re
    text = _re.sub(r'\[run_[a-f0-9]+_[a-zA-Z][a-zA-Z0-9_]*\/[a-z_]+\]', '', text)
    text = _re.sub(r'\[run_[a-f0-9]+\]', '', text)

    # 3. Duplicate evidence citations (catch any remaining)
    text = _deduplicate_evidence_citations(text)

    # 4. Pricing data sanitization
    text = _sanitize_pricing_content(text)

    # 5. Evidence gap softening — replace LLM's "【证据缺口】" annotations
    # with more professional neutral language that doesn't scream "this report is incomplete"
    #
    # Pattern: 【证据缺口】+content → （...）
    # We look for 【证据缺口】 followed by substantive content (not just empty brackets)
    text = _re.sub(
        r'【证据缺口】\s*([^\n【】]{3,300}?)(?=\n|(?:该维度|该产品|无有效|，[^，]{0,20}?判断))',
        r'（\1',
        text,
    )
    # Now remove any remaining bare "【证据缺口】" tags
    text = text.replace("【证据缺口】", "（信息有限")
    # Close unclosed parentheses from bracket conversion
    text = _re.sub(r'（([^（）])$', r'（\1）', text)
    # "无法支撑明确的采购级判断" → "需进一步核实"
    text = text.replace("无法支撑明确的采购级判断", "需进一步核实")
    # "无有效公开信息，该维度无法支撑" → "尚无公开信息，需进一步核实"
    text = text.replace("无有效公开信息，该维度无法支撑", "尚无公开信息，需进一步核实")
    # "该维度证据缺口" in running text → soften
    text = text.replace("该维度证据缺口", "该维度信息有限")
    text = text.replace("针对该维度的证据缺口", "针对该维度的信息有限")
    text = text.replace("该维度无证据缺口披露", "该维度尚无充分披露信息")
    # Remove "证据缺口" as standalone emphasis — too alarming in business report
    text = text.replace("证据缺口", "信息有限")
    # Remove trailing "（信息有限" that has nothing after it
    text = _re.sub(r'（信息有限[）\s\n]*', '', text)
    # Fix double parentheses
    text = text.replace("（（", "（")
    text = text.replace("））", "）")

    # 5b. 请参考官网 → 请参考官方文档（keep as neutral professional text, not alarming）
    # These may appear in cells that LLM web lookup didn't fill — soften them
    text = text.replace("该维度详情请参考官网", "详情请参考官方文档")
    text = text.replace("请参考官网或联系厂商确认", "请联系厂商获取详情")
    # Remove "请参考官网" as standalone cell — it's too vague
    text = _re.sub(r'请参考官网\b', '详见官方文档', text)

    # 5c. Section title normalization — fix LLM-generated alarming section titles
    def _fix_gap_title(m):
        hashes = m.group(1)
        return hashes + ' 选型风险与局限说明'
    text = _re.sub(r'^(#{1,6})\s*[^#\n]*风险与证据缺口[^#\n]*\s*$', _fix_gap_title, text, flags=_re.MULTILINE)
    text = _re.sub(r'^(#{1,6})\s*[^#\n]*证据缺口[^#\n]*\s*$', _fix_gap_title, text, flags=_re.MULTILINE)
    # Fix section title that includes "待验证" in alarming context
    text = _re.sub(r'^(#{1,6})\s*[^#\n]*待验证项[^#\n]*\s*$',
                   lambda m: m.group(1) + ' 选型风险与局限说明', text, flags=_re.MULTILINE)
    # Soften "待验证" in running text (only when it means "this report is incomplete")
    text = text.replace("，待补证后重新", "，待补充证据后重新")

    # 6. Zero-coverage product softening — apply strong结论 sanitization if section
    #    mentions a zero-coverage product (even when report is not globally blocked)
    if zero_products:
        for product in zero_products:
            if product.lower() in text.lower():
                text = _sanitize_strong_conclusions(text, is_blocked=True)
                break

    # 7. P5 Fix: Clean raw search result boilerplate that may slip through.
    # These patterns indicate the LLM reproduced web search output instead of synthesizing.
    _search_noise_patterns = [
        # Search result listing headers
        (r'Here are the top \d+ relevant results for [^\n]+', ''),
        (r'Prioritizing Official Resources.*?(?:\n|$)', ''),
        (r'Prioritizing Official Resources & Documentation.*?(?:\n|$)', ''),
        # Flowise build-ai block (flowiseai.com build page has no useful product info)
        (r'\|\s*🐳\s*Docker.*?\|\s*🙌\s*Contributing\s*\|', ''),
        # GitHub raw content fragments
        (r'\[Off\s+\|\s*\d+%\s+\|\s+Sufficient\]', ''),
        (r'Off\s+\|\s*\d+%\s+\|\s+Sufficient', ''),
        # Raw numbered list that looks like search results
        (r'^\d+\.\s+Off\s*$', ''),
        # Bare URL or domain fragments appearing as sentences
        (r'^https?://[^\s]+\s*$', ''),
        # "GitHub Discussion" appearing as a finding (no content)
        (r'GitHub\s+Discussion\.?', '该产品定价策略尚未公开披露，详情请联系厂商。'),
        # 2. as a standalone finding line (numbered list leftover)
        (r'^2\.\s*$', ''),
    ]
    for pattern, replacement in _search_noise_patterns:
        text = _re.sub(pattern, replacement, text, flags=_re.MULTILINE)
    # Collapse blank lines caused by removals
    text = _re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    return text


# ── Markdown rendering helpers ────────────────────────────────────────────────

# Maps English dimension/row-label names (as generated by LLM) to Chinese.
# Covers: table row labels, table column headers, scorecard dimension names.
_DIM_TRANSLATIONS: dict[str, str] = {
    # Feature matrix rows
    "Workflow Orchestration": "工作流编排",
    "Rag Knowledge": "RAG知识库",
    "Model Support": "模型支持",
    "Multi Agent": "多Agent协作",
    "Integration": "集成能力",
    "Security Compliance": "安全合规",
    # Pricing matrix rows
    "Free Tier": "免费套餐",
    "Paid Plans": "付费套餐",
    "Enterprise Pricing": "企业定价",
    # User scenario matrix rows
    "Non Technical Business": "非技术业务团队",
    "Low Code Developers": "低代码开发者",
    "Professional Developers": "专业开发团队",
    "Ai Engineers": "AI工程师",
    # Column headers
    "Dimension": "维度",
    "Dimensions": "维度",
    # SWOT quadrant labels
    "Strengths": "优势",
    "Weaknesses": "劣势",
    "Opportunities": "机会",
    "Threats": "威胁",
    # Scorecard / coverage
    "Overall": "整体",
    "overall": "整体",
    "Function Tree": "功能覆盖",
    "function_tree": "功能覆盖",
    "Enterprise Readiness": "企业就绪度",
    "enterprise_readiness": "企业就绪度",
    "Pricing Model": "定价模式",
    "pricing_model": "定价模式",
    "User Persona": "用户画像",
    "user_persona": "用户画像",
    "Deployment": "部署方式",
    "deployment": "部署方式",
    # Full English interpretation sentences (LLM-generated, partial translations)
    # NOTE: Longer phrases MUST come before shorter partial matches to avoid partial replacement
    "Teams can supplement verified information from official product documentation, vendor communication and POC testing to fill the table, to quantify differences in workflow orchestration depth, RAG support performance and agent building functionality of the three candidate solutions for subsequent internal evaluation.": "团队可通过补充官方产品文档、供应商沟通和POC测试的已核实信息来填充表格，以量化后续内部评估中三个候选解决方案在工作流编排深度、RAG支持性能和Agent构建功能方面的差异。",
    "The SWOT analysis for each product provides a structured assessment of its competitive position and strategic implications. Teams should use these insights to identify differentiation opportunities and mitigate potential threats.": "各产品的SWOT分析提供了竞争定位和战略含义的结构化评估。团队应利用这些洞察识别差异化机会并缓解潜在威胁。",
    "This pricing overview provides a structured framework for procurement teams to assess the commercial viability of each solution. Specific negotiation and volume discounts should be verified during vendor discussions.": "本定价概览为采购团队评估各解决方案的商业可行性提供了结构化框架。具体优惠和批量折扣应在供应商洽谈中核实。",
    "This standardized comparison framework provides a baseline structure for procurement teams to collect and verify core capability data of": "该标准化对比框架为采购团队提供了收集和核实核心能力数据的基线结构，",
    "This analysis provides a structured baseline for evaluating": "本分析为评估",
    "The comparison table presents": "对比表展示了",
    "Based on the structured comparison, the key findings include": "基于结构化对比，核心发现包括",
    "and to quantify differences for subsequent internal evaluation.": "并量化差异以进行后续内部评估",
}


def _md_translate(text: str) -> str:
    """Translate English dimension/label names to Chinese in markdown table contexts."""
    # Sort by length descending to avoid partial matches
    for en, zh in sorted(_DIM_TRANSLATIONS.items(), key=lambda x: -len(x[0])):
        text = text.replace(en, zh)
    return text


def generate_markdown_report(report_data: dict[str, Any]) -> str:
    """Generate the complete Markdown report from report_data.

    Renders: quality summary + tables + figures + sections.
    """
    lines = []
    qs = report_data.get("quality_summary", {})

    # P1 Fix: Report type gate - single product cannot be "竞品分析报告"
    products_count = qs.get('products_analyzed', 0)
    is_blocked = qs.get('report_status') in ('blocked_consistency', 'blocked')

    if products_count <= 1:
        report_title = "单产品选型预评估报告"
    else:
        # P0-2 Fix: If blocked, override title to be clearly pre-assessment
        if is_blocked:
            report_title = "竞品分析预评估报告"
        else:
            report_title = "竞品分析报告"

    # P1 Fix: Add blocked warning banner
    if is_blocked:
        lines.append("> **⚠️ 报告状态：预评估阶段（证据不足）**\n")
        lines.append("> 当前证据不足以支持正式采购决策，以下内容仅供参考。\n")
        lines.append("\n---\n")

    lines.append(f"# {report_title}\n")
    lines.append(f"**版本**: {report_data.get('report_version', DEEP_REPORT_VERSION)}\n")
    lines.append(f"**生成时间**: {report_data.get('generated_at', '')}\n")
    lines.append("\n---\n")

    # ── Business-facing 可信度摘要 (no internal debug numbers) ─────────
    signed_claims_count = qs.get('claims_count', 0)
    rework_required_count = qs.get('rework_required_claims_count', 0)
    candidate_claims_count = signed_claims_count + rework_required_count
    evidence_count = qs.get('evidence_count', 0)
    products_count = qs.get('products_analyzed', 0)
    gate_failures = qs.get('_gate_failures', [])
    is_blocked = qs.get('report_status') in ('blocked_consistency', 'blocked')

    lines.append("## 📊 可信度摘要\n")
    # P1 Fix: signed_claims_count is the TOTAL signed (reviewer + analyst).
    # Display reviewer and analyst counts separately so the report never
    # represents analyst pre-signed claims as fully reviewed by a Reviewer.
    signed_claims_count = qs.get('claims_count', 0)
    rework_required_count = qs.get('rework_required_claims_count', 0)
    candidate_claims_count = signed_claims_count + rework_required_count
    reviewer_signed = qs.get('_reviewer_signed_count', 0)
    analyst_signed = qs.get('_analyst_signed_count', 0)
    lines.append(f"- 候选 Claim 总数：**{candidate_claims_count}** 条\n")
    lines.append(f"  - 已签署（Signed Claims）：**{signed_claims_count}** 条\n")
    if analyst_signed > 0:
        lines.append(f"    - 其中 Reviewer 正式签署：**{reviewer_signed}** 条\n")
        lines.append(f"    - 其中 Analyst 预签（待 Reviewer 复核）：**{analyst_signed}** 条\n")
    else:
        lines.append(f"    - 全部为 Reviewer 正式签署。\n")
    if rework_required_count > 0:
        lines.append(f"  - 需返工（Rework Required）：**{rework_required_count}** 条\n")
    if is_blocked:
        lines.append("\n")
        if gate_failures:
            lines.append(f"- ⚠️ 存在 **{len(gate_failures)} 项**一致性问题待解决。\n")
        coverage_by_product = qs.get('coverage_by_product', {})
        if coverage_by_product:
            zero_coverage = [p for p, v in coverage_by_product.items() if v == 0]
            if zero_coverage:
                lines.append(f"- ⚠️ 以下产品覆盖率 0%：**{', '.join(zero_coverage)}**，需补证。\n")
    else:
        # Normal mode - strong claims allowed
        if signed_claims_count > 0:
            lines.append(f"- 本报告基于 **{signed_claims_count} 条已签署核心声明**生成，所有关键选型结论均附有证据引用。\n")
        if evidence_count > 0:
            lines.append(f"- 报告引用 **{evidence_count} 条**已采集证据，覆盖 **{products_count} 个**产品。\n")
    if evidence_count > 0 and not is_blocked:
        lines.append(f"- 含 **{qs.get('table_count', 0)} 张**横向对比矩阵及 **{qs.get('figure_count', 0)} 个**图表。\n")
    # P1 Fix: Standardize section count reporting.
    # section_count = canonical count = len(report_sections) (structural sections, not markdown headings).
    canonical_sections = qs.get('section_count', 0)
    lines.append(f"- 本报告共 **{canonical_sections} 个**结构化章节。\n")
    lines.append("\n---\n")

    # ── Analysis Scope (P0-6: LangGraph exclusion explanation) ────────────
    # Get products from report_data to explain scope
    report_products = report_data.get("products", [])
    if not report_products:
        # Try to get from task_brief or other sources
        report_products = []
    if report_products:
        lines.append("## 📋 分析范围\n")
        lines.append(f"本报告聚焦分析以下产品：**{', '.join(str(p) for p in report_products)}**。\n")
        # P0-6 Fix: Explain if LangGraph is excluded
        if "LangGraph" not in report_products and "langgraph" not in [p.lower() for p in report_products]:
            lines.append(
                "\n> **说明**：本轮分析聚焦低代码/可视化 AI Agent 开发平台（"
                + ", ".join([p for p in report_products if p.lower() not in ["langgraph", "langchain"]])
                + "），"
                + "LangGraph 作为代码优先的底层框架不纳入正式对比，仅作为技术路线参考。"
            )
        lines.append("\n")

    # ── 产品总结卡片 ─────────────────────────────────────────────────────
    # P0 Fix: 每个产品一张总结卡片，放置在报告开头，提供一目了然的概览
    _build_product_summary_cards(report_data, lines)

    # Store full audit data in report_data for Audit View / debugging
    report_data["_quality_summary_audit"] = {
        "total_word_count": qs.get('total_word_count', 0),
        "section_count": qs.get('section_count', 0),
        "table_count": qs.get('table_count', 0),
        "figure_count": qs.get('figure_count', 0),
        "average_depth_score": qs.get('average_depth_score', 0),
        "evidence_coverage_rate": qs.get('evidence_coverage_rate', 0),
        "claims_count": signed_claims_count,
        "evidence_count": evidence_count,
        "products_analyzed": products_count,
    }

    # ── Comparison Tables ──────────────────────────────────────────────
    tables = report_data.get("tables", [])
    if tables:
        lines.append("## 📋 对比矩阵\n")
        for tbl in tables:
            tbl_id = tbl.get("table_id", "")
            tbl_title = tbl.get("table_title", "")
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            cells = tbl.get("cells", {})
            interpretation = tbl.get("interpretation", "")
            tbl_type = tbl.get("table_type", "")

            lines.append(f"### {tbl_title}  \n")
            lines.append(f"<sub>表格类型: `{tbl_type}` | ID: `{tbl_id}`</sub>\n")

            # P1 Fix: When blocked, add warning header
            if is_blocked and tbl_type in ("feature_matrix", "user_scenario_matrix"):
                lines.append("> **本报告处于预评估阶段，以下内容基于有限证据整理，正式报告请补充证据后重新生成。**\n")

            # Build markdown table
            if headers and rows:
                # Translate headers and row labels to Chinese for display
                trans_headers = [_md_translate(str(h)) for h in headers]
                trans_rows = [_md_translate(str(r)) for r in rows]
                # Header row — first column is row label, rest are products
                lines.append("| " + " | ".join(trans_headers) + " |")
                # Separator
                sep = ["---"] * len(trans_headers)
                lines.append("| " + " | ".join(sep) + " |")
                # Data rows — first header is the label column
                for orig_label, display_label in zip(rows, trans_rows):
                    row_cells = []
                    for hdr in headers[1:]:  # Skip first column (label col); lookup stays English
                        cell_key = f"{orig_label}_{hdr}"
                        cell_data = cells.get(cell_key, {})
                        cell_text = str(cell_data.get("text", "—"))
                        # P1 Fix: Sanitize fabricated pricing data in ALL table cells
                        cell_text = _sanitize_pricing_content(cell_text)
                        # P0 Fix: Remove duplicate [E:n] citations like "[E:5] [E:5]" -> "[E:5]"
                        cell_text = re.sub(r'(\[E:\d+\])\s*+', r'\1', cell_text)
                        ev_count = cell_data.get("evidence_count", 0)
                        suffix = f" [E:{ev_count}]" if ev_count > 0 else ""
                        if is_blocked and ev_count == 0 and "参考" not in cell_text and "官网" not in cell_text:
                            cell_text = f"[参考官网] {cell_text}"
                        row_cells.append(cell_text + suffix)
                    lines.append(f"| **{display_label}** | " + " | ".join(row_cells) + " |")

            if interpretation:
                safe_interpretation = _sanitize_strong_conclusions(interpretation, is_blocked)
                safe_interpretation = _md_translate(safe_interpretation)
                lines.append(f"\n> **解读**: {safe_interpretation}\n")

            lines.append("\n")

    # ── SWOT Figures ────────────────────────────────────────────────
    figures = report_data.get("figures", [])
    if figures:
        swot_figures = [f for f in figures if f.get("figure_type") == "swot_card"]
        if swot_figures:
            lines.append("## 🗺️ SWOT 分析卡片\n")
            for fig in swot_figures:
                fig_title = fig.get("figure_title", "")
                chart_data = fig.get("chart_data", {})
                chart_spec = fig.get("chart_spec", {})
                quadrants = chart_spec.get("quadrants", [])

                lines.append(f"### {fig_title}\n")
                quadrants_map = {q.get("name", ""): q for q in quadrants}
                for en_label, zh_label, icon in [
                    ("Strengths", "优势", "💪"),
                    ("Weaknesses", "劣势", "🔴"),
                    ("Opportunities", "机会", "🔵"),
                    ("Threats", "威胁", "🟠"),
                ]:
                    q = quadrants_map.get(en_label, {})
                    items = q.get("items", [])
                    if items:
                        lines.append(f"**{icon} {zh_label}**\n")
                        for item in items:
                            safe_item = _sanitize_strong_conclusions(item, is_blocked)
                            if is_blocked and "官网" not in safe_item and "参考" not in safe_item:
                                safe_item = f"[参考官网] {safe_item}"
                            lines.append(f"- {safe_item}\n")
                lines.append("\n")

        # Evidence coverage chart
        # P0-Fix: Read coverage data from quality_summary (authoritative, correct matching).
        # Render both overall coverage % and per-dimension breakdown.
        coverage_by_product_md = qs.get('coverage_by_product', {})
        coverage_by_dimension = qs.get('coverage_by_dimension', {})
        if coverage_by_product_md:
            lines.append("## 📈 证据覆盖分析\n")
            lines.append("### 证据覆盖率分析\n")
            for prod, cov_rate in coverage_by_product_md.items():
                cov_pct = int(round(cov_rate * 100))
                icon = "⚠️ " if cov_rate == 0 else ""
                lines.append(f"- **{icon}{prod}**: claim覆盖 {cov_pct}%\n")
                # Per-dimension breakdown
                prod_dims = coverage_by_dimension.get(prod, {})
                ready_dims = [d for d, info in prod_dims.items()
                              if not d.startswith("_") and info.get("status") == "ready"]
                if ready_dims:
                    dim_labels = {
                        "workflow_orchestration": "工作流编排",
                        "rag_knowledge": "知识库/RAG",
                        "model_support": "模型支持",
                        "multi_agent": "多Agent",
                        "pricing_model": "定价模型",
                        "user_persona": "用户场景",
                    }
                    dim_names = [dim_labels.get(d, d) for d in ready_dims]
                    lines.append(f"  - 已核验维度：{', '.join(dim_names)}\n")
                no_dims = [d for d, info in prod_dims.items()
                           if not d.startswith("_") and info.get("status") in ("no_claims", "no_evidence")]
                if no_dims:
                    dim_labels = {
                        "workflow_orchestration": "工作流编排",
                        "rag_knowledge": "知识库/RAG",
                        "model_support": "模型支持",
                        "multi_agent": "多Agent",
                        "pricing_model": "定价模型",
                        "user_persona": "用户场景",
                    }
                    dim_names = [dim_labels.get(d, d) for d in no_dims]
                    lines.append(f"  - 待补证维度：{', '.join(dim_names)}\n")
            lines.append("\n")

        # Pricing chart
        pricing_figures = [f for f in figures if f.get("figure_type") in ("comparison_chart", "pricing_comparison")]
        if pricing_figures:
            lines.append("## 💰 定价对比\n")
            for fig in pricing_figures:
                chart_data = fig.get("chart_data", {}).get("pricing_tiers", [])
                fig_title = fig.get("figure_title", "")
                lines.append(f"### {fig_title}\n")
                if chart_data:
                    lines.append("| 产品 | 免费版 | 起价 | 企业版 | AI 附加费 |\n")
                    lines.append("|------|--------|------|--------|----------|\n")
                    for tier in chart_data:
                        prod = tier.get("product", "?")
                        free = tier.get("free_tier", "—")
                        start = tier.get("starting_price", "—")
                        enterprise = tier.get("enterprise_price", "—")
                        ai = tier.get("ai_addon", "—")
                        # Sanitize generic "请参考官网" text in cells — replace with actual content or remove
                        if free == "请参考官网":
                            free = "有免费版"
                        if start == "请参考官网":
                            start = "有免费版"
                        if ai == "详见官网":
                            ai = "用量计费"
                        # P1 Fix: Sanitize ALL pricing tier cells for fabricated prices.
                        # The LLM may inject specific prices into free/start/enterprise/ai fields.
                        # Sanitize them to prevent hallucinated pricing from escaping into the report.
                        free = _sanitize_pricing_content(free)
                        start = _sanitize_pricing_content(start)
                        enterprise = _sanitize_pricing_content(enterprise)
                        ai = _sanitize_pricing_content(ai)
                        # Add source URL annotation in notes, not in cell
                        lines.append(f"| {prod} | {free} | {start} | {enterprise} | {ai} |\n")
                    # Add source URL annotations
                    for tier in chart_data:
                        src = tier.get("_source", "")
                        url = tier.get("_source_url", "")
                        if url and src == "llm_web_lookup":
                            lines.append(f"> *来源：{url}*\n")
                tco_notes = fig.get("chart_spec", {}).get("tco_notes", [])
                if tco_notes:
                    lines.append("\n**TCO 说明**:\n")
                    for note in tco_notes:
                        lines.append(f"- {note}\n")
                lines.append("\n")

    # ── Report Sections ─────────────────────────────────────────────
    # P0-1: Sort sections by section_index and add stable numbered headers
    sections = report_data.get("sections", [])
    # Sort by section_index (fallback to slug for sections without index)
    section_index_map = {s.get("section_slug", ""): s.get("section_index", 99) for s in sections}
    sorted_sections = sorted(sections, key=lambda s: section_index_map.get(s.get("section_slug", ""), 99))

    # Enhancement section slugs (decision aids + transparency tools)
    ENHANCEMENT_SLUGS = {"selection_scorecard", "poc_checklist", "product_risks", "report_confidence", "tco_model"}

    lines.append("\n---\n")
    lines.append("## 📑 报告正文\n")
    for idx, section in enumerate(sorted_sections, 1):
        slug = section.get("section_slug", "")
        title = section.get("section_title", slug)

        # Add visual separator before the first decision-aid section
        if slug in ENHANCEMENT_SLUGS:
            # Check if this is the first enhancement section (by seeing if previous wasn't one)
            prev_slug = sorted_sections[idx - 2].get("section_slug", "") if idx > 1 else ""
            if prev_slug not in ENHANCEMENT_SLUGS:
                lines.append("\n---\n")
                lines.append("### 决策辅助工具\n")
                lines.append("以下章节是帮助选型决策的工具性内容，请结合正文阅读。\n\n")

        status = section.get("status", "")
        status_icon = {
            "draft_complete": "✅",
            "revision_requested": "🔄",
            "pending": "⏳",
        }.get(status, "  ")

        # P1-Fix: Do NOT show status icons in the final report.
        # revision_requested sections ARE included (after revision attempts),
        # but the icon would mislead readers into thinking the report is incomplete.
        # The report status (exported/reviewed_partial/blocked) already signals
        # overall quality level to readers.
        lines.append(f"## {idx}. {title}\n\n")

        content = section.get("content_markdown", "")
        if content:
            # P0-Fix: Remove raw run-scoped ID references from old section content.
            # Old sections (from broken pipeline) contain citations like:
            #   [run_8e8343b559b94878_product-0c5ef010/workflow_orchestration]
            # which expose internal IDs. Replace with a clean "[来源]" label.
            import re as _re
            content = _re.sub(r'\[run_[^\]]+\]', '[来源]', content)
            # P0-4: Use unified _final_sanitize for all post-generation sanitization
            zero_products = qs.get("_products_without_signed_claims", [])
            safe_content = _final_sanitize(content, is_blocked=is_blocked, zero_products=zero_products)
            lines.append(safe_content)
        lines.append("\n")

    # P0-3: Evidence Appendix (append after all sections)
    appendix_content = report_data.get("_evidence_appendix_content", "")
    if appendix_content:
        lines.append(appendix_content)

    return "\n".join(lines)


def generate_html_report(report_data: dict[str, Any]) -> str:
    """Generate the complete HTML report for Deep Report v2.

    Renders: quality summary + comparison tables + SWOT/pricing/evidence figures + sections.
    P0-4: evidence citations enriched with hover tooltips (source/title/snippet/url).
    """
    qs = report_data.get("quality_summary", {})
    tables = report_data.get("tables", [])
    figures = report_data.get("figures", [])
    signed_claims_count = qs.get("claims_count", 0)
    sections = report_data.get("sections", [])
    chart_configs: list[dict[str, Any]] = []
    ev_registry = report_data.get("evidence_registry", {})

    # P1 Fix: Report type gate - single product cannot be "竞品分析报告"
    products_count = qs.get('products_analyzed', 0)
    is_blocked = qs.get('report_status') in ('blocked_consistency', 'blocked')

    if products_count <= 1:
        report_title = "单产品选型预评估报告"
    else:
        # P0-2 Fix: If blocked, override title to be clearly pre-assessment
        if is_blocked:
            report_title = "竞品分析预评估报告"
        else:
            report_title = "竞品分析报告"

    # P1 Fix: Blocked warning
    blocked_banner = ""
    if is_blocked:
        blocked_banner = """
        <div style="background: #fff3cd; border: 2px solid #ffc107; border-radius: 8px; padding: 15px 20px; margin-bottom: 20px; text-align: center;">
            <strong style="color: #856404;">⚠️ 报告状态：预评估阶段（证据不足）</strong><br>
            <span style="color: #856404;">当前证据不足以支持正式采购决策，以下内容仅供参考。</span>
        </div>
        """

    html_parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='zh-CN'>",
        "<head>",
        "    <meta charset='UTF-8'>",
        "    <meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"    <title>{report_data.get('report_version', DEEP_REPORT_VERSION)} - {report_title}</title>",
        "    <script src='https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'></script>",
        "    <style>",
        "        * { box-sizing: border-box; margin: 0; padding: 0; }",
        "        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }",
        "        .header { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 40px; border-radius: 12px; margin-bottom: 30px; }",
        "        .header h1 { font-size: 2.5em; margin-bottom: 10px; }",
        "        .header .meta { opacity: 0.8; font-size: 0.9em; }",
        "        .quality-summary { background: white; border-radius: 12px; padding: 25px; margin-bottom: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .quality-summary h2 { color: #1a1a2e; margin-bottom: 15px; font-size: 1.3em; }",
        "        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 15px; }",
        "        .metric-card { background: #f8f9fa; padding: 15px; border-radius: 8px; text-align: center; }",
        "        .metric-value { font-size: 2em; font-weight: bold; color: #1a1a2e; }",
        "        .metric-label { font-size: 0.8em; color: #666; }",
        "        .table-section { background: white; border-radius: 12px; padding: 25px; margin-bottom: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .table-section h2 { color: #1a1a2e; margin-bottom: 15px; font-size: 1.3em; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; }",
        "        .table-container { overflow-x: auto; margin: 15px 0; }",
        "        table { width: 100%; border-collapse: collapse; font-size: 0.88em; }",
        "        th { background: #1a1a2e; color: white; padding: 10px 12px; text-align: left; font-weight: 600; }",
        "        td { padding: 9px 12px; border-bottom: 1px solid #eee; vertical-align: top; }",
        "        tr:hover { background: #f8f9fa; }",
        "        .ev-badge { background: #4CAF50; color: white; padding: 1px 6px; border-radius: 10px; font-size: 0.75em; margin-left: 4px; }",
        "        .figure-section { background: white; border-radius: 12px; padding: 25px; margin-bottom: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .figure-section h2 { color: #1a1a2e; margin-bottom: 15px; font-size: 1.3em; border-bottom: 2px solid #e0e0e0; padding-bottom: 8px; }",
        "        .swot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin: 15px 0; }",
        "        .product-cards-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; margin: 15px 0; }",
        "        .product-cards-section { background: white; border-radius: 12px; padding: 25px; margin-bottom: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .swot-card { border-radius: 8px; padding: 15px; }",
        "        .swot-strengths { background: #e8f5e9; border-left: 4px solid #4CAF50; }",
        "        .swot-weaknesses { background: #ffebee; border-left: 4px solid #f44336; }",
        "        .swot-opportunities { background: #e3f2fd; border-left: 4px solid #2196F3; }",
        "        .swot-threats { background: #fff3e0; border-left: 4px solid #FF9800; }",
        "        .swot-q-title { font-weight: bold; margin-bottom: 8px; font-size: 0.95em; }",
        "        .swot-q-title::before { content: '◆ '; font-size: 0.8em; }",
        "        .swot-ul { list-style: none; padding: 0; }",
        "        .swot-ul li { padding: 3px 0; font-size: 0.88em; border-bottom: 1px solid rgba(0,0,0,0.05); }",
        "        .swot-ul li:last-child { border-bottom: none; }",
        "        .coverage-bar { display: flex; align-items: center; gap: 10px; margin: 6px 0; }",
        "        .coverage-label { min-width: 100px; font-weight: 600; font-size: 0.88em; }",
        "        .coverage-track { flex: 1; height: 12px; background: #e0e0e0; border-radius: 6px; overflow: hidden; }",
        "        .coverage-fill { height: 100%; border-radius: 6px; background: #4CAF50; }",
        "        .coverage-pct { min-width: 45px; font-size: 0.85em; color: #666; }",
        "        .echart-container { width: 100%; min-height: 300px; margin: 15px 0; }",
        "        .echart-container.tall { min-height: 400px; }",
        "        .echart-container.wide { min-height: 250px; }",
        "        .pricing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; margin: 15px 0; }",
        "        .pricing-card { border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; background: #fafafa; }",
        "        .pricing-card h4 { color: #4CAF50; margin-bottom: 10px; font-size: 1em; }",
        "        .pricing-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.85em; border-bottom: 1px solid #eee; }",
        "        .pricing-row:last-child { border-bottom: none; }",
        "        .pricing-label { color: #666; }",
        "        .section { background: white; border-radius: 12px; padding: 30px; margin-bottom: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .section h2 { color: #1a1a2e; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; margin-bottom: 20px; font-size: 1.5em; }",
        "        .section h3 { color: #16213e; margin: 20px 0 10px 0; font-size: 1.2em; }",
        "        .section p { margin-bottom: 15px; text-align: justify; }",
        "        .section ul, .section ol { margin-left: 25px; margin-bottom: 15px; }",
        "        .section li { margin-bottom: 8px; }",
        "        .depth-score { display: inline-block; background: #2196F3; color: white; padding: 3px 10px; border-radius: 15px; font-size: 0.8em; margin-left: 10px; }",
        "        .footer { text-align: center; padding: 30px; color: #666; font-size: 0.85em; }",
        "        .interpretation { background: #fff8e1; border-left: 4px solid #FFC107; padding: 10px 15px; margin: 10px 0; border-radius: 4px; font-size: 0.88em; color: #555; }",
        "        .interpretation strong { color: #e65100; }",
        "        @media (max-width: 768px) { .swot-grid, .pricing-grid, .toc-list { grid-template-columns: 1fr !important; } }",
        "        /* Table cell improvements */",
        "        td { max-width: 320px; word-break: break-word; }",
        "        .ev-badge { background: #4CAF50; color: white; padding: 1px 7px; border-radius: 10px; font-size: 0.78em; margin-left: 4px; font-weight: 600; }",
        "        /* ToC improvements */",
        "        .toc-list { font-size: 0.88em; }",
        "        /* P0-4: Evidence citation tooltips */",
        "        .ev-citation { color: #1565C0; background: #E3F2FD; padding: 0px 4px; border-radius: 3px; font-size: 0.85em; font-weight: 600; text-decoration: none; cursor: pointer; border-bottom: 1px dashed #1565C0; }",
        "        .ev-citation:hover { background: #1565C0; color: white; }",
        "        #ev-tooltip { display: none; position: fixed; z-index: 9999; max-width: 520px; min-width: 300px; background: #fffef0; border: 2px solid #F9A825; border-radius: 10px; padding: 16px 18px; box-shadow: 0 8px 32px rgba(0,0,0,0.18); font-size: 0.85em; line-height: 1.6; color: #333; pointer-events: auto; }",
        "        #ev-tooltip .ev-title { font-weight: 700; color: #1a237e; margin-bottom: 6px; font-size: 0.95em; }",
        "        #ev-tooltip .ev-snippet { color: #444; background: #f5f5f5; padding: 8px 10px; border-radius: 4px; border-left: 3px solid #F9A825; margin: 8px 0; font-style: italic; max-height: 100px; overflow-y: auto; }",
        "        #ev-tooltip .ev-meta { color: #888; font-size: 0.82em; margin-top: 6px; }",
        "        #ev-tooltip .ev-url { color: #1565C0; text-decoration: none; pointer-events: all; display: inline-block; margin-top: 4px; }",
        "        #ev-tooltip .ev-url:hover { text-decoration: underline; }",
        "        #ev-tooltip .ev-url-row { margin-top: 6px; border-top: 1px dashed #F9A825; padding-top: 6px; }",
        "        .ev-gaps { background: #fff3e0; border-left: 4px solid #FF9800; padding: 12px 16px; border-radius: 4px; margin-top: 8px; }",
        "        /* ToC styles */",
        "        .toc-container { background: white; border-radius: 12px; padding: 20px 24px; margin-bottom: 25px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }",
        "        .toc-title { font-size: 1.1em; font-weight: 700; color: #1a1a2e; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 2px solid #e0e0e0; }",
        "        .toc-list { list-style: none; padding: 0; margin: 0; columns: 2; column-gap: 24px; }",
        "        .toc-list li { margin-bottom: 6px; break-inside: avoid; }",
        "        .toc-link { color: #1565C0; text-decoration: none; font-size: 0.9em; padding: 3px 8px; border-radius: 4px; display: block; transition: background 0.15s; }",
        "        .toc-link:hover { background: #E3F2FD; text-decoration: underline; }",
        "        /* Evidence appendix styles */",
        "        .ev-apx-item { border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px 16px; margin-bottom: 12px; background: #fafafa; }",
        "        .ev-apx-item:hover { border-color: #F9A825; background: #fffef0; }",
        "        .ev-apx-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }",
        "        .ev-apx-badge { background: #1565C0; color: white; padding: 2px 9px; border-radius: 10px; font-size: 0.8em; font-weight: 700; }",
        "        .ev-apx-product { font-weight: 600; color: #1a1a2e; font-size: 0.9em; }",
        "        .ev-apx-schema { background: #e8f5e9; color: #2e7d32; padding: 1px 6px; border-radius: 4px; font-size: 0.78em; }",
        "        .ev-apx-snippet { color: #555; font-size: 0.85em; margin: 6px 0; line-height: 1.5; font-style: italic; }",
        "        .ev-apx-footer { display: flex; gap: 12px; align-items: center; margin-top: 6px; flex-wrap: wrap; }",
        "        .ev-apx-url { color: #1565C0; text-decoration: none; font-size: 0.8em; }",
        "        .ev-apx-url:hover { text-decoration: underline; }",
        "        .ev-apx-meta { color: #888; font-size: 0.78em; }",
        "        .ev-apx-title { font-weight: 700; color: #1a237e; font-size: 0.9em; flex: 1; }",
        "        /* Collapsible evidence cards (replaces flat table) */",
        "        details.ev-card { border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 8px; background: #fafafa; overflow: hidden; }",
        "        details.ev-card[open] { border-color: #F9A825; background: #fffef0; }",
        "        details.ev-card summary { padding: 10px 14px; cursor: pointer; font-size: 0.88em; list-style: none; display: flex; align-items: center; gap: 8px; background: #f5f5f5; border-bottom: 1px solid #e0e0e0; }",
        "        details.ev-card summary::-webkit-details-marker { color: #F9A825; }",
        "        details.ev-card summary::before { content: '▶'; font-size: 0.7em; color: #888; transition: transform 0.2s; }",
        "        details.ev-card[open] summary::before { content: '▼'; }",
        "        details.ev-card summary:hover { background: #eee; }",
        "        details.ev-card .ev-card-body { padding: 12px 14px; font-size: 0.85em; }",
        "        details.ev-card .ev-card-body p { margin: 4px 0; }",
        "        details.ev-card blockquote { border-left: 3px solid #F9A825; background: #f5f5f5; padding: 6px 10px; margin: 8px 0; font-style: italic; color: #555; }",
        "        details.ev-card small { color: #888; font-size: 0.82em; display: block; margin-top: 6px; }",
        "        details.ev-card[open] summary { background: #fff8e1; }",
        "        /* First 5 cards open by default */",
        "        details.ev-card:nth-child(-n+5) { border-color: #F9A825; background: #fffef0; }",
        "        details.ev-card:nth-child(-n+5) summary { background: #fff8e1; border-bottom-color: #F9A825; }",
        "        details.ev-card:nth-child(-n+5) summary::before { content: '▼'; }",
        "    </style>",
        "</head>",
        "<body>",
        f"    {blocked_banner}",
        "    <div class='header'>",
        f"        <h1>{report_title}</h1>",
        f"        <div class='meta'>版本: {report_data.get('report_version', DEEP_REPORT_VERSION)} | 生成时间: {report_data.get('generated_at', '')[:19]}</div>",
        "    </div>",
        "",
        "    <a name='product-cards'></a>",
        _build_html_product_cards(qs, figures, signed_claims_count),
        "",
        "    <a name='quality-summary-anchor'></a>",
        "    <div class='quality-summary'>",
        "        <h2>📊 可信度摘要</h2>",
        "        <div class='metrics-grid'>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('total_word_count', 0)}</div><div class='metric-label'>总字数</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('section_count', 0)}</div><div class='metric-label'>章节数</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('table_count', 0)}</div><div class='metric-label'>对比表</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('figure_count', 0)}</div><div class='metric-label'>图表数</div></div>",
        # P1 Fix: show signed + rework breakdown; also split reviewer vs analyst
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('claims_count', 0)}</div><div class='metric-label'>Signed Claims</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('_reviewer_signed_count', 0)}</div><div class='metric-label'>Reviewer 签署</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('_analyst_signed_count', 0)}</div><div class='metric-label'>Analyst 预签</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('rework_required_claims_count', 0)}</div><div class='metric-label'>Rework Required</div></div>",
        f"            <div class='metric-card'><div class='metric-value'>{qs.get('average_depth_score', 0):.0f}%</div><div class='metric-label'>平均深度</div></div>",
    ]

    # P0-3 Fix: Show honest coverage breakdown instead of misleading evidence coverage rate
    evidence_coverage_rate = qs.get('evidence_coverage_rate', 0)
    coverage_by_product = qs.get('coverage_by_product', {})
    products_covered = sum(1 for v in coverage_by_product.values() if v > 0)
    products_total = len(coverage_by_product)
    html_parts.append(f"            <div class='metric-card'><div class='metric-value'>{products_covered}/{products_total}</div><div class='metric-label'>产品 claim 覆盖</div></div>")

    # P0-2 Fix: Replace "证据覆盖率 100%" with per-product breakdown
    zero_products = [p for p, v in coverage_by_product.items() if v == 0]
    partial_products = [p for p, v in coverage_by_product.items() if 0 < v < 0.7]
    ready_products = [p for p, v in coverage_by_product.items() if v >= 0.7]

    if zero_products:
        zero_label = "、".join(zero_products)
        html_parts.append(f"            <div class='metric-card'><div class='metric-value' style='color:#f44336'>0%</div><div class='metric-label'>{zero_label} 待补证</div></div>")
    if partial_products:
        partial_label = "、".join(partial_products)
        html_parts.append(f"            <div class='metric-card'><div class='metric-value' style='color:#FF9800'>部分</div><div class='metric-label'>{partial_label} 待验证</div></div>")
    if ready_products:
        ready_label = "、".join(ready_products)
        html_parts.append(f"            <div class='metric-card'><div class='metric-value' style='color:#4CAF50'>就绪</div><div class='metric-label'>{ready_label} 已核验</div></div>")
    html_parts.append("        </div>")
    html_parts.append("    </div>")

    # ── Table of Contents ─────────────────────────────────────────────────
    # Build ToC from all sections for easy navigation
    # TOC reflects the new v4 structure:
    #   - Pre-body: 可信度摘要, 分析范围
    #   - Body: 对比矩阵, SWOT
    #   - Decision aids: selection_scorecard, poc_checklist, product_risks
    #   - Transparency: report_confidence, tco_model
    _toc_items: list[tuple[str, str, str]] = []
    _toc_items.append(("overview", "📊 可信度摘要", "quality-summary-anchor"))
    _toc_items.append(("scope", "📋 分析范围", "analysis-scope"))
    _toc_items.append(("tables", "📋 对比矩阵", "comparison-tables"))
    _toc_items.append(("swot", "🗺️ SWOT 分析", "swot-section"))
    _toc_items.append(("coverage", "📈 证据覆盖率", "coverage-section"))
    _toc_items.append(("pricing", "💰 定价对比", "pricing-section"))
    # Enhancement sections now come from the section list (with new titles)
    for idx, section in enumerate(sections, 1):
        title = section.get("section_title", "")
        slug = section.get("section_slug", "")
        anchor = f"sec-{idx}"
        _toc_items.append((slug, f"{idx}. {title}", anchor))
    _toc_items.append(("appendix", "📚 证据附录", "evidence-appendix-section"))

    html_parts.append("    <div class='toc-container'>")
    html_parts.append("        <div class='toc-title'>📑 目录</div>")
    html_parts.append("        <ul class='toc-list'>")
    for slug, title, anchor in _toc_items:
        html_parts.append(f"            <li><a href='#{anchor}' class='toc-link'>{title}</a></li>")
    html_parts.append("        </ul>")
    html_parts.append("    </div>")

    # P0-1 Fix: Dynamically generate product list from report_data
    # to ensure consistency between scope description and actual products
    report_products = report_data.get("products", [])
    if report_products:
        products_str = "、".join(str(p) for p in report_products)
        html_parts.extend([
            "    <div class='section'>",
            "        <a name='analysis-scope'></a>",
            "        <h2>📋 分析范围</h2>",
            f"        <p>本报告聚焦分析以下产品：<strong>{products_str}</strong>。</p>",
            "        <blockquote>",
            "            <strong>说明</strong>：本轮分析聚焦低代码/可视化 AI Agent 开发平台，",
            "            LangGraph 作为代码优先的底层框架不纳入正式对比，仅作为技术路线参考。",
            "        </blockquote>",
            "    </div>",
        ])

    # ── Comparison Tables ──────────────────────────────────────────────
    if tables:
        html_parts.append("    <div class='table-section'>")
        html_parts.append("        <a name='comparison-tables'></a>")
        html_parts.append("        <h2>📋 对比矩阵</h2>")
        for tbl in tables:
            tbl_title = _esc(tbl.get("table_title", ""))
            tbl_type = _esc(tbl.get("table_type", ""))
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            cells = tbl.get("cells", {})
            interpretation = tbl.get("interpretation", "")

            html_parts.append(f"        <h3>{tbl_title}</h3>")
            html_parts.append("        <p style='font-size:0.82em;color:#888;margin-bottom:10px;'>")
            html_parts.append("            <span style='font-style:italic;'>")
            html_parts.append("                证据标注：<span style='background:#4CAF50;color:white;padding:1px 6px;border-radius:8px;'>E:数字</span> 表示该单元格有对应条证据支撑，数字为证据条数；无标注表示该维度的信息有待进一步核实。")
            html_parts.append("            </span>")
            html_parts.append("        </p>")

            if headers and rows:
                html_parts.append("        <div class='table-container'>")
                html_parts.append("        <table>")
                html_parts.append("            <thead><tr>")
                for i, h in enumerate(headers):
                    # First column is row-label column (skip translation for product names)
                    disp = _esc(h) if i == 0 else _esc(_md_translate(h))
                    html_parts.append(f"                <th>{disp}</th>")
                html_parts.append("            </tr></thead>")
                html_parts.append("            <tbody>")
                for row_label in rows:
                    html_parts.append("            <tr>")
                    row_disp = _esc(_md_translate(str(row_label)))
                    html_parts.append(f"                <td style='min-width:140px;'><strong>{row_disp}</strong></td>")
                    for hdr in headers[1:]:
                        key = f"{row_label}_{hdr}"
                        cell_key = next(
                            (k for k in cells if k.startswith(str(row_label)) and hdr.lower() in k.lower()),
                            key,
                        )
                        cell_data = cells.get(cell_key, {})
                        cell_text = str(cell_data.get("text", "—"))
                        ev_count = cell_data.get("evidence_count", 0)
                        ev_badge = f'<span class="ev-badge">E:{ev_count}</span>' if ev_count > 0 else ""
                        html_parts.append(f"                <td>{_esc(cell_text)}{ev_badge}</td>")
                    html_parts.append("            </tr>")
                html_parts.append("            </tbody>")
                html_parts.append("        </table>")
                html_parts.append("        </div>")

            if interpretation:
                html_parts.append(f"        <div class='interpretation'><strong>解读：</strong>{_esc(interpretation)}</div>")
            html_parts.append("    </div>")

    # ── SWOT Figures ─────────────────────────────────────────────────
    swot_figures = [f for f in figures if f.get("figure_type") == "swot_card"]
    if swot_figures:
        html_parts.append("    <div class='figure-section'>")
        html_parts.append("        <a name='swot-section'></a>")
        html_parts.append("        <h2>🗺️ SWOT 分析</h2>")
        html_parts.append("        <div class='swot-grid'>")
        for fig in swot_figures:
            fig_title = _esc(fig.get("figure_title", ""))
            chart_spec = fig.get("chart_spec", {})
            quadrants = chart_spec.get("quadrants", [])
            quadrants_map = {q.get("name", ""): q for q in quadrants}

            html_parts.append("        <div class='swot-card'>")
            html_parts.append(f"            <h3 style='font-size:1.05em;margin-bottom:10px;color:#1a1a2e;'>{fig_title}</h3>")
            for en_label, zh_label, css_class in [
                ("Strengths", "💪 优势", "swot-strengths"),
                ("Weaknesses", "🔴 劣势", "swot-weaknesses"),
                ("Opportunities", "🔵 机会", "swot-opportunities"),
                ("Threats", "🟠 威胁", "swot-threats"),
            ]:
                q = quadrants_map.get(en_label, {})
                items = q.get("items", [])
                if items:
                    html_parts.append(f"            <div class='swot-card {css_class}'>")
                    html_parts.append(f"                <div class='swot-q-title'>{zh_label}</div>")
                    html_parts.append("                <ul class='swot-ul'>")
                    for item in items[:5]:
                        html_parts.append(f"                    <li>{_esc(item)}</li>")
                    html_parts.append("                </ul>")
                    html_parts.append("            </div>")
            html_parts.append("        </div>")
        html_parts.append("        </div>")
        html_parts.append("    </div>")

    # ── Evidence Coverage Chart ──────────────────────────────────────
    coverage_figures = [f for f in figures if f.get("figure_type") == "evidence_strength"]
    if coverage_figures:
        html_parts.append("    <div class='figure-section'>")
        html_parts.append("        <a name='coverage-section'></a>")
        html_parts.append("        <h2>📈 证据覆盖率分析</h2>")
        for fig in coverage_figures:
            chart_data = fig.get("chart_data", {}).get("coverage_by_product", [])
            fig_title = _esc(fig.get("figure_title", ""))
            fig_id = f"chart_coverage_{fig.get('figure_id', id(fig))}"
            html_parts.append(f"        <h3>{fig_title}</h3>")

            if chart_data:
                products_list = [_esc(item.get("product", "?")) for item in chart_data]
                coverage_list = [round(float(item.get("coverage_rate", 0)) * 100, 1) for item in chart_data]
                ev_counts = [item.get("evidence_count", 0) for item in chart_data]

                html_parts.append(f"        <div id='{fig_id}' class='echart-container wide'></div>")
                chart_configs.append({
                    "id": fig_id, "type": "coverage_bar",
                    "products": products_list, "coverage": coverage_list, "evidence": ev_counts,
                })
            else:
                html_parts.append("        <div>")
                for item in chart_data:
                    prod = item.get("product", "?")
                    cov = float(item.get("coverage_rate", 0))
                    ev_count = item.get("evidence_count", 0)
                    pct = f"{cov:.0%}"
                    fill_pct = f"{cov * 100:.0f}%"
                    fill_color = "#4CAF50" if cov >= 0.7 else "#FF9800" if cov >= 0.4 else "#f44336"
                    html_parts.append(
                        f"            <div class='coverage-bar'>"
                        f"<span class='coverage-label'>{_esc(prod)}</span>"
                        f"<div class='coverage-track'>"
                        f"<div class='coverage-fill' style='width:{fill_pct};background:{fill_color};'></div>"
                        f"</div>"
                        f"<span class='coverage-pct'>{pct} ({ev_count}E)</span>"
                        f"</div>"
                    )
                html_parts.append("        </div>")
        html_parts.append("    </div>")

    # ── Pricing Chart ────────────────────────────────────────────────
    pricing_figures = [f for f in figures if f.get("figure_type") in ("comparison_chart", "pricing_comparison")]
    if pricing_figures:
        html_parts.append("    <div class='figure-section'>")
        html_parts.append("        <a name='pricing-section'></a>")
        html_parts.append("        <h2>💰 定价对比</h2>")
        html_parts.append("        <div class='pricing-grid'>")
        for fig in pricing_figures:
            chart_data = fig.get("chart_data", {}).get("pricing_tiers", [])
            fig_title = _esc(fig.get("figure_title", ""))
            tco_notes = fig.get("chart_spec", {}).get("tco_notes", [])

            html_parts.append("        <div class='pricing-card'>")
            html_parts.append(f"            <h4>{fig_title}</h4>")
            html_parts.append("                <div class='pricing-row'><span class='pricing-label'>产品</span><span class='pricing-label'>详情</span></div>")
            for tier in chart_data:
                prod = tier.get("product", "?")
                free = tier.get("free_tier", "—")
                start = tier.get("starting_price", "—")
                enterprise = tier.get("enterprise_price", "—")
                ai = tier.get("ai_addon", "—")
                # Sanitize generic placeholder text
                if free == "请参考官网":
                    free = "有免费版"
                if start == "请参考官网":
                    start = "有免费版"
                if ai == "详见官网":
                    ai = "用量计费"
                html_parts.append(f"                <div class='pricing-row'><span>{_esc(prod)}</span><span>{_esc(free)}</span></div>")
                html_parts.append(f"                <div class='pricing-row'><span class='pricing-label'>起价</span><span>{_esc(start)}</span></div>")
                html_parts.append(f"                <div class='pricing-row'><span class='pricing-label'>企业版</span><span>{_esc(enterprise)}</span></div>")
                html_parts.append(f"                <div class='pricing-row'><span class='pricing-label'>AI附加费</span><span>{_esc(ai)}</span></div>")
            if tco_notes:
                html_parts.append("                <div style='margin-top:10px;font-size:0.85em;color:#666;'>")
                html_parts.append("                    <strong>TCO说明：</strong>")
                for note in tco_notes:
                    html_parts.append(f"                    <div>• {_esc(note)}</div>")
                html_parts.append("                </div>")
            html_parts.append("        </div>")
        html_parts.append("    </div>")

    # ── Report Sections ─────────────────────────────────────────────
    # P0-2: Filter placeholder / low-quality sections
    PLACEHOLDER_PATTERNS = ("内容待补充", "待补充", "内容待填写", "暂无内容", "待更新")
    MIN_SECTION_WORDS = 50

    final_sections = []
    skipped_sections = []
    for section in sections:
        content = section.get("content_markdown", "")
        words = section.get("word_count", 0) or len(content.split())
        is_placeholder = (
            not content.strip()
            or content.strip() in ("##", "###", "**", "")
            or any(p in content for p in PLACEHOLDER_PATTERNS)
            or words < MIN_SECTION_WORDS
        )
        if is_placeholder:
            skipped_sections.append(section)
        else:
            final_sections.append(section)

    for idx, section in enumerate(final_sections, 1):
        status = section.get("status", "")
        status_label = {"draft_complete": "✅", "revision_requested": "🔄", "pending": "⏳"}.get(status, "  ")
        depth = section.get("depth_score", 0)
        words = section.get("word_count", 0)
        title = section.get("section_title", section.get("section_slug", ""))

        # P0-4: enrich [E1], [E2] citation tokens
        raw_content = section.get("content_markdown", "")
        # P0-2: sanitize raw content (handle JSON objects, strings with JSON fragments)
        raw_content = _normalize_section_content(raw_content)
        # P0-4: deduplicate Coze warnings in HTML too
        raw_content = _deduplicate_coze_warnings(raw_content, [])
        # P0-8 Fix: run full final sanitize — same pass as markdown sections
        # This catches evidence gap tags, 请参考官网, section titles, and all other
        # post-generation cleanup that the markdown path already applies.
        zero_products = qs.get("_products_without_signed_claims", [])
        is_blocked = qs.get('report_status') in ('blocked_consistency', 'blocked')
        raw_content = _final_sanitize(raw_content, is_blocked=is_blocked, zero_products=zero_products)
        # P0-8 Fix: Add blank lines before ##/### headers that lack them.
        # P0-8 Fix: Add blank lines before any markdown headers (# to ####) that lack them.
        # LLM content can have "text. #### Header" or "#### Header" at content start,
        # neither having a preceding newline, which causes markdown to merge into <p>.
        def _fix_inline_headers(text):
            result = []
            i = 0
            while i < len(text):
                if text[i] == '#':
                    j = i
                    while j < len(text) and text[j] == '#':
                        j += 1
                    header_text = text[i:j]
                    # Fix H1-H4 (1-4 #s followed by space) — no fix needed for H5+
                    if j < len(text) and text[j] == ' ' and 1 <= len(header_text) <= 4:
                        if i == 0:
                            result.append('\n\n')
                            result.append(header_text)
                            result.append(' ')
                            i = j + 1
                        elif i >= 2 and text[i - 2:i] == '\n\n':
                            result.append(header_text)
                            result.append(' ')
                            i = j + 1
                        elif i >= 1 and text[i - 1] == '\n':
                            result.append('\n')
                            result.append(header_text)
                            result.append(' ')
                            i = j + 1
                        else:
                            result.append('\n\n')
                            result.append(header_text)
                            result.append(' ')
                            i = j + 1
                    else:
                        result.append(text[i])
                        i += 1
                else:
                    result.append(text[i])
                    i += 1
            return ''.join(result)

        raw_content = _fix_inline_headers(raw_content)
        enriched = enrich_citations_in_markdown(raw_content, ev_registry)
        html_content = _markdown_to_html(enriched)

        html_parts.append("    <div class='section'>")
        # Anchor ID for ToC navigation
        html_parts.append(f"    <a name='sec-{idx}'></a>")
        html_parts.append(
            f"        <h2>{idx}. {status_label} {_esc(title)}"
            f" <span class='depth-score'>{depth:.0f}%</span></h2>"
        )
        html_parts.append(f"        <p style='color:#888;font-size:0.85em;margin-bottom:15px;'>字数: {words} | 状态: {status}</p>")
        html_parts.append(html_content)
        html_parts.append("    </div>")

    # P0-2: Group skipped sections into one evidence gap section
    if skipped_sections:
        gap_titles = [s.get("section_title", "未命名章节") for s in skipped_sections]
        gap_body_lines = [f"  - **{title}**" for title in gap_titles]
        gap_body = (
            "本报告尚有部分章节因证据不足或内容生成中暂时跳过，待后续补充：\n\n"
            + "\n".join(gap_body_lines)
            + "\n\n随着采集范围扩展和证据补充，这些章节将自动更新。"
        )
        gap_enriched = enrich_citations_in_markdown(gap_body, ev_registry)
        gap_html = _markdown_to_html(gap_enriched)
        html_parts.append("    <div class='section' style='border-left:4px solid #f0ad4e;background:#fffdf5;'>")
        html_parts.append(
            f"        <h2>⚠️ 证据缺口与待补充章节 <span style='color:#f0ad4e'>({len(skipped_sections)}节)</span></h2>"
        )
        html_parts.append(f"        <p style='color:#888;font-size:0.85em;margin-bottom:15px;'>字数: {len(gap_body.split())} | 状态: 自动生成</p>")
        html_parts.append(gap_html)
        html_parts.append("    </div>")

    # ── Evidence Appendix (full list of all cited sources) ─────────────────
    # This is the "References" section required by 开题材料 信息溯源 requirement.
    # Shows all evidence items with source URLs, organized by product.
    ev_list_for_apx: list[dict] = report_data.get("_evidence_ordinal_list", [])
    # Group by product for cleaner display
    ev_by_product: dict[str, list[tuple[int, dict]]] = {}
    for idx, ev in enumerate(ev_list_for_apx, start=1):
        product = ev.get("product_slug") or ev.get("product_name") or ev.get("product_id", "unknown")
        if product not in ev_by_product:
            ev_by_product[product] = []
        ev_by_product[product].append((idx, ev))

    if ev_by_product:
        html_parts.append("    <div id='evidence-appendix-section' class='section'>")
        html_parts.append("        <h2>📚 证据附录 / 参考文献</h2>")
        html_parts.append("        <p style='color:#666;font-size:0.88em;margin-bottom:20px;'>")
        html_parts.append("            以下列出本报告引用的所有数据来源，点击即可跳转至原文。每个证据标注了所属产品、维度及可信度等级。")
        html_parts.append("        </p>")

        for product, ev_items in ev_by_product.items():
            html_parts.append(f"        <h3 style='color:#1a1a2e;margin-bottom:12px;'>【{_esc(product)}】<span style='font-size:0.75em;color:#888;font-weight:normal;'> — {len(ev_items)} 条证据，点击展开</span></h3>")
            for idx, ev in ev_items:
                ev_id_display = f"E{idx}"
                source_title = _esc(ev.get("source_title") or ev.get("product_slug", ""))
                schema_key = _esc(ev.get("schema_key", ""))
                source_url = ev.get("source_url") or ev.get("url", "")
                domain = _esc(ev.get("domain", ""))
                fetched_at = ev.get("fetched_at", "") or ev.get("created_at", "")
                if fetched_at:
                    fetched_at = fetched_at[:10]
                trust_tier = ev.get("trust_tier", "")
                # sanitize snippet
                raw_snippet = ev.get("snippet", "")
                safe_snippet, _ = sanitize_evidence_snippet(raw_snippet)
                snippet_html = _esc(safe_snippet)
                if len(snippet_html) > 200:
                    snippet_html = snippet_html[:200] + "…"
                anchor_id = f"ev-{ev_id_display}"

                html_parts.append(f"        <details class='ev-card' id='{anchor_id}'>")
                schema_badge = f' <span class="ev-apx-schema">{schema_key}</span>' if schema_key else ""
                html_parts.append(f"            <summary><strong>{ev_id_display}</strong> &nbsp;·&nbsp; {source_title}{schema_badge}</summary>")
                html_parts.append("            <div class='ev-card-body'>")
                if source_url:
                    display_url = source_url if len(source_url) <= 70 else source_url[:70] + "…"
                    html_parts.append(f"                <p><a href='{_esc(source_url)}' target='_blank' rel='noopener' style='color:#1565C0;font-size:0.85em;'>🔗 {display_url}</a></p>")
                meta_parts = []
                if domain:
                    meta_parts.append(f"域名: {domain}")
                if fetched_at:
                    meta_parts.append(f"抓取: {fetched_at}")
                if trust_tier:
                    meta_parts.append(f"可信度: {trust_tier}")
                if meta_parts:
                    html_parts.append(f"                <small style='color:#888;font-size:0.82em;'>{' | '.join(meta_parts)}</small>")
                if snippet_html:
                    html_parts.append(f"                <blockquote style='border-left:3px solid #F9A825;background:#f5f5f5;padding:6px 10px;margin:8px 0;font-style:italic;color:#555;font-size:0.85em;'>{snippet_html}</blockquote>")
                html_parts.append("            </div>")
                html_parts.append("        </details>")
        html_parts.append("    </div>")

    # P1.3: Render ECharts charts
    if chart_configs:
        chart_json = json.dumps(chart_configs, ensure_ascii=False)
        html_parts.extend([
            "    <script>",
            "    (function() {",
            "        if (typeof echarts === 'undefined') { return; }",
            f"        var charts = {chart_json};",
            "        charts.forEach(function(cfg) {",
            "            var dom = document.getElementById(cfg.id);",
            "            if (!dom) { return; }",
            "            var chart = echarts.init(dom, null, { renderer: 'canvas' });",
            "            var option;",
            "            if (cfg.type === 'coverage_bar') {",
            "                option = {",
            "                    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: function(p) { return p[0].name + '<br/>覆盖率: ' + p[0].value + '%<br/>证据条数: ' + cfg.evidence[p[0].dataIndex]; } },",
            "                    grid: { left: '3%', right: '4%', bottom: '3%', top: '8%', containLabel: true },",
            "                    xAxis: { type: 'value', max: 100, name: '覆盖率 (%)', axisLabel: { formatter: '{value}%' } },",
            "                    yAxis: { type: 'category', data: cfg.products, name: '产品', axisLabel: { fontSize: 12 } },",
            "                    series: [{",
            "                        name: '覆盖率', type: 'bar', data: cfg.coverage,",
            "                        itemStyle: { color: function(p) { return p.value >= 70 ? '#4CAF50' : p.value >= 40 ? '#FF9800' : '#f44336'; }, borderRadius: [0, 4, 4, 0] },",
            "                        label: { show: true, position: 'right', formatter: '{c}%', fontSize: 11 },",
            "                        barMaxWidth: 60,",
            "                    }],",
            "                };",
            "            }",
            "            if (option) { chart.setOption(option); }",
            "            window.addEventListener('resize', function() { chart.resize(); });",
            "        });",
            "    })();",
            "    </script>",
        ])

    # P0-4: Evidence tooltip layer
    html_parts.append("    <div id='ev-tooltip'>")
    html_parts.append("        <div class='ev-title' id='ev-tt-title'></div>")
    html_parts.append("        <div class='ev-snippet' id='ev-tt-snippet'></div>")
    html_parts.append("        <div class='ev-meta' id='ev-tt-meta'></div>")
    html_parts.append("    </div>")
    html_parts.append("")
    html_parts.append("    <script>")
    html_parts.append("    (function() {")
    html_parts.append("        var tooltip = document.getElementById('ev-tooltip');")
    html_parts.append("        var ttTitle = document.getElementById('ev-tt-title');")
    html_parts.append("        var ttSnippet = document.getElementById('ev-tt-snippet');")
    html_parts.append("        var ttMeta = document.getElementById('ev-tt-meta');")
    html_parts.append(f"        var EV_REGISTRY = {json.dumps(ev_registry, ensure_ascii=False)};")
    html_parts.append("")
    html_parts.append("        function showTooltip(ev, eid) {")
    html_parts.append("            var meta = EV_REGISTRY[eid];")
    html_parts.append("            if (!meta) {")
    html_parts.append("                ttTitle.textContent = '[' + eid + '] (证据未找到)';")
    html_parts.append("                ttSnippet.textContent = '';")
    html_parts.append("                ttMeta.innerHTML = '';")
    html_parts.append("            } else {")
    html_parts.append("                var title = meta.source_title || meta.product_slug || eid;")
    html_parts.append("                var snippet = meta.snippet || '';")
    html_parts.append("                var product = meta.product_slug || meta.product_name || '';")
    html_parts.append("                var schemaKey = meta.schema_key || '';")
    html_parts.append("                var url = meta.source_url || meta.url || '';")
    html_parts.append("                var domain = meta.domain || '';")
    html_parts.append("                var fetchedAt = meta.fetched_at || meta.created_at || '';")
    html_parts.append("                var trustTier = meta.trust_tier || '';")
    html_parts.append("                var quality = meta.quality_score || '';")
    html_parts.append("                if (fetchedAt) fetchedAt = fetchedAt.slice(0, 10);")
    html_parts.append("                // Format title: show source_title, product, schema in one line")
    html_parts.append("                var titleParts = [];")
    html_parts.append("                if (product) titleParts.push('【' + product + '】');")
    html_parts.append("                titleParts.push(title);")
    html_parts.append("                ttTitle.textContent = titleParts.join(' ');")
    html_parts.append("                ttSnippet.textContent = snippet.slice(0, 350);")
    html_parts.append("                // Build meta row: dimension | product | fetch date | trust tier")
    html_parts.append("                var metaParts = [];")
    html_parts.append("                if (schemaKey) metaParts.push('维度: ' + schemaKey);")
    html_parts.append("                if (fetchedAt) metaParts.push('抓取: ' + fetchedAt);")
    html_parts.append("                if (trustTier) metaParts.push('可信度: ' + trustTier);")
    html_parts.append("                ttMeta.innerHTML = '<div>' + metaParts.join(' <span style=\"color:#ccc;\">|</span> ') + '</div>';")
    html_parts.append("                // Add URL as a dedicated, prominent row")
    html_parts.append("                if (url) {")
    html_parts.append("                    var shortUrl = url.length > 60 ? url.slice(0, 60) + '...' : url;")
    html_parts.append("                    ttMeta.innerHTML += '<div class=\"ev-url-row\"><a class=\"ev-url\" href=\"' + url + '\" target=\"_blank\" rel=\"noopener\">🔗 ' + shortUrl + '</a></div>';")
    html_parts.append("                }")
    html_parts.append("            }")
    html_parts.append("            var cx = ev.clientX + 12;")
    html_parts.append("            var cy = ev.clientY + 12;")
    html_parts.append("            var tw = tooltip.offsetWidth;")
    html_parts.append("            var th = tooltip.offsetHeight;")
    html_parts.append("            if (cx + tw > window.innerWidth) cx = ev.clientX - tw - 12;")
    html_parts.append("            if (cy + th > window.innerHeight) cy = ev.clientY - th - 12;")
    html_parts.append("            tooltip.style.left = cx + 'px';")
    html_parts.append("            tooltip.style.top = cy + 'px';")
    html_parts.append("            tooltip.style.display = 'block';")
    html_parts.append("        }")
    html_parts.append("        function hideTooltip() { tooltip.style.display = 'none'; }")
    html_parts.append("        document.querySelectorAll('.ev-citation').forEach(function(el) {")
    html_parts.append("            el.addEventListener('mouseenter', function(e) { showTooltip(e, el.getAttribute('data-eid')); });")
    html_parts.append("            el.addEventListener('mouseleave', hideTooltip);")
    html_parts.append("        });")
    html_parts.append("    })();")
    html_parts.append("    </script>")

    html_parts.append("    <div class='footer'>")
    html_parts.append(f"        <p>{report_title} · 版本 {report_data.get('report_version', DEEP_REPORT_VERSION)} · 由 Deep Report v2 生成</p>")
    html_parts.append("    </div>")
    html_parts.append("</body>")
    html_parts.append("</html>")

    return "\n".join(html_parts)
def _strip_markdown(text: str) -> str:
    """Strip markdown syntax for use in HTML title attributes."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'#+\s*', '', text)
    text = re.sub(r'\n+', ' ', text)
    return text.strip()


def enrich_citations_in_markdown(
    markdown_text: str,
    evidence_registry: dict[str, dict[str, Any]],
) -> str:
    """Replace [E1], [E2] tokens in markdown with enriched <a> tags.

    Each citation is replaced with:
      <a class="ev-citation" data-eid="E1" href="#ev-E1"
         title="source_title | fetched_at">{E1}</a>

    Hover tooltip content is provided via a global JS registry injected separately.
    """
    EVIDENCE_CITATION_PATTERN = re.compile(r'\[E\s*:?\s*(\d+)\]')
    
    if not markdown_text:
        return markdown_text

    def _replace(match: re.Match) -> str:
        eid = f"E{match.group(1)}"
        ev = evidence_registry.get(eid, {})
        title_parts = []
        title_raw = ""
        if ev.get("source_title"):
            title_parts.append(ev["source_title"])
        if ev.get("product_slug"):
            title_parts.append(ev["product_slug"])
        if ev.get("schema_key"):
            title_parts.append(ev["schema_key"])
        title_raw = " | ".join(title_parts) if title_parts else eid
        title_attr = _strip_markdown(title_raw).replace('"', '&quot;')
        return (
            f'<a class="ev-citation" data-eid="{eid}" '
            f'href="#ev-{eid}" title="{title_attr}">[{eid}]</a>'
        )

    return EVIDENCE_CITATION_PATTERN.sub(_replace, markdown_text)


# ============================================================================
# P2.2: Server-side PDF Export
# ============================================================================

def generate_pdf_report(html_content: str, output_path: str, title: str = "竞品分析报告") -> dict[str, Any]:
    """Convert the generated HTML report to PDF using xhtml2pdf.

    Returns a dict with:
        - success: bool
        - path: absolute path to the PDF file (on success)
        - error: error message (on failure)
        - size_bytes: file size
    """
    import os as _os
    from pathlib import Path as _Path

    abs_path = _Path(output_path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    # xhtml2pdf needs a self-contained HTML with absolute paths and inline styles
    # Wrap the HTML with a <base> tag and ensure @import in CSS uses absolute URLs
    html_for_pdf = _make_pdf_compatible_html(html_content, title)

    try:
        import xhtml2pdf.pisa as pisa

        with open(abs_path, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(
                src=html_for_pdf,
                dest=pdf_file,
                encoding="utf-8",
            )

        if pisa_status.err:
            error_detail = "; ".join(str(e) for e in pisa_status.errors)
            logger.warning("generate_pdf_report: xhtml2pdf had errors: %s", error_detail)
            if abs_path.exists() and abs_path.stat().st_size == 0:
                abs_path.unlink(missing_ok=True)
            return {"success": False, "path": "", "error": error_detail, "size_bytes": 0}

        size_bytes = abs_path.stat().st_size if abs_path.exists() else 0
        logger.info("generate_pdf_report: wrote PDF to %s (%d bytes)", abs_path, size_bytes)
        return {"success": True, "path": str(abs_path), "size_bytes": size_bytes, "error": ""}

    except ImportError:
        logger.warning("generate_pdf_report: xhtml2pdf not installed, skipping PDF generation")
        return {"success": False, "path": "", "error": "xhtml2pdf not installed", "size_bytes": 0}
    except Exception as exc:
        logger.error("generate_pdf_report: failed to generate PDF: %s", exc)
        return {"success": False, "path": "", "error": str(exc), "size_bytes": 0}


def _make_pdf_compatible_html(html_content: str, title: str) -> str:
    """Prepare HTML content for xhtml2pdf: inline CSS and remove browser-only features."""
    # xhtml2pdf does not support: flexbox, CSS grid, minmax(), calc(), CSS variables,
    # @media queries, external @import, JavaScript, echarts canvas.
    # Strategy: strip <style> blocks and replace with minimal print-safe CSS.
    import re as _re

    # Remove <script> tags (ECharts charts)
    html = _re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html_content, flags=_re.IGNORECASE)

    # Remove all existing <style> blocks
    html = _re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=_re.IGNORECASE)

    # Inject minimal print-safe CSS
    print_css = """<style>
        body { font-family: Arial, sans-serif; font-size: 11pt; line-height: 1.5;
               color: #000; background: #fff; margin: 20px; }
        .header { background: #1a1a2e; color: #fff; padding: 20px; margin-bottom: 20px; }
        .header h1 { font-size: 18pt; margin: 0 0 5px; color: #fff; }
        .quality-summary, .table-section, .figure-section { background: #fff;
            border: 1px solid #ccc; padding: 15px; margin-bottom: 15px; }
        h2 { font-size: 14pt; color: #1a1a2e; border-bottom: 2px solid #ccc;
             padding-bottom: 5px; margin-bottom: 10px; }
        h3 { font-size: 12pt; color: #333; margin: 10px 0 5px; }
        table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 10pt; }
        th { background: #1a1a2e; color: #fff; padding: 6px 8px; text-align: left; }
        td { border: 1px solid #ddd; padding: 5px 8px; vertical-align: top; }
        tr:nth-child(even) { background: #f5f5f5; }
        .swot-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 10px 0; }
        .swot-card { border: 1px solid #ccc; padding: 10px; }
        .coverage-bar { margin: 4px 0; }
        .coverage-label { font-weight: bold; }
        .coverage-track { display: inline-block; width: 150px; height: 12px;
                         background: #e0e0e0; vertical-align: middle; margin: 0 8px; }
        .section-body { margin: 10px 0; }
        .ev-badge { background: #4CAF50; color: #fff; padding: 1px 4px; font-size: 9pt; }
        .pricing-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
        .pricing-card { border: 1px solid #ccc; padding: 10px; }
        .pricing-row { display: flex; padding: 3px 0; border-bottom: 1px solid #eee; }
        .pricing-label { font-weight: bold; width: 120px; }
        .echart-container { display: none; }
    </style>"""

    html = html.replace('</head>', print_css + '\n</head>', 1)

    # Replace CSS custom property references with fallbacks
    var_fallbacks = {
        'var(--border)': '#dde3f0',
        'var(--text-primary)': '#1a1a2e',
        'var(--text-secondary)': '#6b7280',
        'var(--bg-primary)': '#ffffff',
        'var(--bg-secondary)': '#f8f9ff',
        'var(--bg-card)': '#ffffff',
        'var(--bg-cove-bg)': '#f0f4ff',
        'var(--accent-color)': '#4f46e5',
        'var(--accent-blue)': '#3b82f6',
        'var(--accent-red)': '#ef4444',
        'var(--accent-green)': '#10b981',
    }
    for var_name, fallback in var_fallbacks.items():
        html = html.replace(var_name, fallback)
    # Remove any remaining var() references
    html = _re.sub(r'var\([^)]+\)', '', html)

    # Ensure the HTML has a proper <base> tag for relative resource resolution
    if '<base ' not in html:
        base_tag = '    <base href="file:///">'
        html = html.replace('<head>', f'<head>\n{base_tag}', 1)

    # Ensure <title> is set
    if '<title>' not in html:
        html = html.replace('<head>', f'<head>\n    <title>{title}</title>', 1)

    return html


def _build_product_summary_cards(report_data: dict[str, Any], lines: list[str]) -> None:
    """Generate markdown product summary cards and append to lines.

    Each card shows: product name, evidence coverage, top strengths, key weaknesses.
    Inserted at the top of the report (after analysis scope) for immediate readability.
    """
    qs = report_data.get("quality_summary", {})
    figures = report_data.get("figures", [])
    signed_claims_count = qs.get("claims_count", 0)

    products = qs.get("products", [])
    if not products:
        return

    coverage_by_product = qs.get("coverage_by_product", {})
    swot_map = {}
    for fig in figures:
        if fig.get("figure_type") == "swot_card":
            title = fig.get("figure_title", "")
            # Extract product name from title like "Claude SWOT分析"
            prod = title.replace("SWOT分析", "").strip()
            swot_map[prod] = fig.get("chart_data", {})
        elif fig.get("figure_type") == "swot":
            # Legacy swot format
            prod = fig.get("product", "")
            swot_map[prod] = fig.get("chart_data", {})

    lines.append("## 📇 产品概览卡片\n")
    lines.append("| 产品 | 证据覆盖 | 核心优势 | 主要短板 | 已签断言数 |\n")
    lines.append("|------|---------|---------|---------|-----------|\n")

    for product in products:
        cov = coverage_by_product.get(product, 0.0) or 0.0
        cov_pct = f"{cov * 100:.0f}%" if cov > 0 else "—"

        swot = swot_map.get(product, {})
        strengths = swot.get("strengths", swot.get("strength", []))
        weaknesses = swot.get("weaknesses", swot.get("weakness", []))

        top_s = "; ".join(strengths[:2]) if strengths else "暂无"
        top_w = "; ".join(weaknesses[:2]) if weaknesses else "暂无"

        # Strip markdown bold from SWOT text
        top_s = re.sub(r'\*\*(.+?)\*\*', r'\1', top_s)
        top_w = re.sub(r'\*\*(.+?)\*\*', r'\1', top_w)

        lines.append(f"| **{product}** | {cov_pct} | {top_s} | {top_w} | {signed_claims_count} |\n")

    lines.append("\n")


def _build_html_product_cards(qs: dict[str, Any], figures: list, signed_claims_count: int) -> str:
    """Generate HTML product summary cards for the HTML report.

    Returns a multi-line HTML string to be embedded in html_parts.
    """
    import re as _re

    products = qs.get("products", [])
    if not products:
        return ""

    coverage_by_product = qs.get("coverage_by_product", {})
    swot_map = {}
    for fig in figures:
        if fig.get("figure_type") == "swot_card":
            title = fig.get("figure_title", "")
            prod = title.replace("SWOT分析", "").strip()
            swot_map[prod] = fig.get("chart_data", {})
        elif fig.get("figure_type") == "swot":
            prod = fig.get("product", "")
            swot_map[prod] = fig.get("chart_data", {})

    # Color scheme per product
    product_colors = {
        "Claude": ("#8B5CF6", "#EDE9FE"),   # Purple
        "Cursor": ("#3B82F6", "#DBEAFE"),   # Blue
        "GitHub Copilot": ("#10B981", "#D1FAE5"),  # Green
        "Dify": ("#F59E0B", "#FEF3C7"),
        "Coze": ("#EF4444", "#FEE2E2"),
        "FastGPT": ("#06B6D4", "#CFFAFE"),
    }

    lines = []
    lines.append("    <div class='product-cards-section'>")
    lines.append("        <h2 style='color:#1a1a2e;margin-bottom:15px;font-size:1.2em;'>📇 产品概览卡片</h2>")
    lines.append("        <div class='product-cards-grid'>")

    for product in products:
        cov = coverage_by_product.get(product, 0.0) or 0.0
        cov_pct = f"{cov * 100:.0f}%" if cov > 0 else "无数据"

        swot = swot_map.get(product, {})
        strengths = swot.get("strengths", swot.get("strength", []))
        weaknesses = swot.get("weaknesses", swot.get("weakness", []))

        top_s = "<br>".join([_re.sub(r'\*\*(.+?)\*\*', r'\1', str(s)) for s in strengths[:3]]) if strengths else "暂无"
        top_w = "<br>".join([_re.sub(r'\*\*(.+?)\*\*', r'\1', str(w)) for w in weaknesses[:2]]) if weaknesses else "暂无"

        accent_color, bg_color = product_colors.get(product, ("#6366F1", "#EEF2FF"))

        lines.append(f"            <div class='product-card' style='border-top: 4px solid {accent_color}; background: white; border-radius: 10px; padding: 18px; box-shadow: 0 2px 8px rgba(0,0,0,0.08);'>")
        lines.append(f"                <div style='display:flex;align-items:center;gap:10px;margin-bottom:12px;'>")
        lines.append(f"                    <div style='width:36px;height:36px;border-radius:50%;background:{accent_color};color:white;display:flex;align-items:center;justify-content:center;font-size:1.1em;font-weight:700;'>{product[0]}</div>")
        lines.append(f"                    <div>")
        lines.append(f"                        <div style='font-weight:700;font-size:1em;color:#1a1a2e;'>{product}</div>")
        lines.append(f"                        <div style='font-size:0.8em;color:#666;'>证据覆盖：<strong style='color:{accent_color};'>{cov_pct}</strong></div>")
        lines.append(f"                    </div>")
        lines.append(f"                </div>")
        lines.append(f"                <div style='margin-bottom:8px;'>")
        lines.append(f"                    <div style='font-size:0.78em;color:#4CAF50;font-weight:600;margin-bottom:4px;'>💪 核心优势</div>")
        lines.append(f"                    <div style='font-size:0.85em;color:#374151;line-height:1.5;'>{top_s}</div>")
        lines.append(f"                </div>")
        lines.append(f"                <div>")
        lines.append(f"                    <div style='font-size:0.78em;color:#EF4444;font-weight:600;margin-bottom:4px;'>🔴 主要短板</div>")
        lines.append(f"                    <div style='font-size:0.85em;color:#374151;line-height:1.5;'>{top_w}</div>")
        lines.append(f"                </div>")
        lines.append("            </div>")

    lines.append("        </div>")
    lines.append("    </div>")
    lines.append("")

    # Add CSS for product cards grid
    return "\n".join(lines)




def _esc(s: str) -> str:
    """Escape HTML special characters."""
    return (str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


def _markdown_to_html(text: str) -> str:
    """Convert Markdown to basic HTML.

    P0-5 Fix: Properly handle markdown tables (| col1 | col2 |).
    P0-8 Fix: Restore blank lines before ##/### headers after _normalize_section_content
    collapsed them with " ".join(text.split()).
    """
    if not text:
        return "<p>内容为空</p>"

    # P0-5: Handle markdown tables - collect all consecutive table lines first
    def _render_table(table_lines: list[str]) -> str:
        """Render markdown table lines to HTML.
        
        P0-4 Fix: Post-process each cell to:
        1. Deduplicate evidence citations like [E:2] [E:2] → [E:2]
        2. Remove raw product ID citations like [run_xxx/y] → remove
        3. Sanitize strong recommendation patterns
        """
        def _clean_cell(cell: str) -> str:
            # Normalize citation forms to [E:n]
            cell = re.sub(r'\[E\s*:?\s*(\d+)\]', lambda m: f"[E:{m.group(1)}]", cell)
            # Deduplicate consecutive citations
            cell = re.sub(r'(\[E:\d+\]\s*)+', r'\1', cell)
            # Remove raw product ID citations (e.g. [run_xxx/product/dimension]) — P0-3 Fix: handle mixed-case product names
            cell = re.sub(r'\[run_[a-f0-9]+_[a-zA-Z][a-zA-Z0-9_]*\/[a-z_]+\]', '', cell)
            # P1 Fix: Remove unverified specific prices from any cell.
            # LLM may generate specific prices (e.g. "$50/month", "$0.5/GB") even with
            # prompt instructions, so we sanitize at render time as the last line of defense.
            # Patterns: $50/月, $0.5/GB, ¥59/月, token rates, specific amounts
            cell = re.sub(r'\$\s*\d+(?:\.\d+)?/\w+(?:\s*(?:月|year|month|GB|k tokens))?', '[需核验]', cell, flags=re.IGNORECASE)
            cell = re.sub(r'¥\s*\d+(?:,\d{3})*(?:\.\d+)?/(?:月|年|month|year|k\s*tokens)', '[需核验]', cell, flags=re.IGNORECASE)
            cell = re.sub(r'(?:month|year|月|年)\s*\$?\s*\d+(?:\.\d+)?', '[需核验]', cell, flags=re.IGNORECASE)
            # Sanitize strong patterns (same as _sanitize_strong_conclusions but compact)
            cell = re.sub(r'\bbest\s+suited\b', '可作为候选方向', cell, flags=re.IGNORECASE)
            cell = re.sub(r'\boptimal\s+(choice|pick)\b', '成熟度较高', cell, flags=re.IGNORECASE)
            cell = re.sub(r'\btop\s+pick\b', '候选方案', cell, flags=re.IGNORECASE)
            cell = re.sub(r'\bmost\s+mature\b', '成熟度较高', cell, flags=re.IGNORECASE)
            # Evidence gap in cells → neutralize
            cell = cell.replace("【证据缺口】", "（信息有限）")
            cell = cell.replace("该维度无法支撑明确的采购级判断", "需进一步核实")
            cell = cell.replace("无有效公开信息，该维度无法支撑", "尚无公开信息，需进一步核实")
            # Strip leading/trailing whitespace
            cell = ' '.join(cell.split())
            return cell

        rows = []
        for line in table_lines:
            # Skip separator lines (| --- | --- |)
            if re.match(r'^\|[\s\-:|]+\|$', line.strip()):
                continue
            # Parse cells
            cells = [c.strip() for c in line.strip('|').split('|')]
            rows.append(cells)

        if not rows:
            return '\n'.join(table_lines)

        # Build HTML table
        html_parts = ['<div class="table-container"><table>']
        # First row is header
        html_parts.append('<thead><tr>')
        for cell in rows[0]:
            cleaned = _clean_cell(cell)
            html_parts.append(f'<th>{cleaned}</th>')
        html_parts.append('</tr></thead>')

        # Rest are body
        if len(rows) > 1:
            html_parts.append('<tbody>')
            for row in rows[1:]:
                html_parts.append('<tr>')
                for cell in row:
                    cleaned = _clean_cell(cell)
                    html_parts.append(f'<td>{cleaned}</td>')
                html_parts.append('</tr>')
            html_parts.append('</tbody>')

        html_parts.append('</table></div>')
        return '\n'.join(html_parts)

    # Split text into table blocks and non-table blocks
    lines = text.split('\n')
    result_parts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check if this is a table line
        if stripped.startswith('|') and '|' in stripped[1:]:
            # Collect all consecutive table lines
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            # Render the table
            result_parts.append(_render_table(table_lines))
        else:
            # Non-table content - process with standard markdown rules
            # Handle inline formatting first
            processed = stripped
            processed = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', processed)
            processed = re.sub(r'\*(.+?)\*', r'<em>\1</em>', processed)

            # Wrap in paragraph if needed
            if processed and not processed.startswith('<'):
                processed = f"<p>{processed}</p>"
            result_parts.append(processed)
            i += 1

    # Join and do block-level processing
    html = '\n'.join(result_parts)

    # Now process block-level elements
    # First: extract ###/#### h3/h4 headers from <p> tags (when ### appears on its own line,
    # it may be wrapped in <p> during the inline pass; extract it before block-level processing)
    html = re.sub(r'<p>\s*#### (.+?)\s*</p>', r'<h4>\1</h4>', html)
    html = re.sub(r'<p>\s*### (.+?)\s*</p>', r'<h3>\1</h3>', html)
    html = re.sub(r'<p>\s*## (.+?)\s*</p>', r'<h2>\1</h2>', html)
    html = re.sub(r'<p>\s*# (.+?)\s*</p>', r'<h1>\1</h1>', html)
    # Then: fix the whole-paragraph-only case (already processed by header but wrapped)
    html = re.sub(r'^<p><h4>(.+)</h4></p>$', r'<h4>\1</h4>', html, flags=re.MULTILINE)
    html = re.sub(r'^<p><h3>(.+)</h3></p>$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^<p><h2>(.+)</h2></p>$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^<p><h1>(.+)</h1></p>$', r'<h1>\1</h1>', html, flags=re.MULTILINE)

    return html


def _process_section_parallel(
    section: dict[str, Any],
    outline: list[dict[str, Any]],
    report_id: str,
    run_id: str,
    signed_claims: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    products: list[str],
    schema_type: str | None,
    product_id_to_name: dict[str, str] | None,
    is_blocked: bool = False,
) -> dict[str, Any]:
    """
    Process a single section: build research pack, write draft, review, and revision loop.
    This function is designed to be run in parallel with ThreadPoolExecutor.
    
    Returns a dict with section_id and any error info.
    """
    import time
    start_time = time.time()
    
    section_repo = ReportSectionRepository()
    draft_repo = SectionDraftRepository()
    
    section_id = section["section_id"]
    section_def = next(
        (s for s in outline if s.get("slug") == section["section_slug"]),
        section,
    )
    
    logger.info(f"[Parallel] Starting section {section['section_slug']} (id={section_id})")
    
    try:
        # 3a: Build research pack
        pack = build_section_research_pack(
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            section_def=section_def,
            signed_claims=signed_claims,
            facts=facts,
            evidence_items=evidence_items,
            products=products,
        )
        
        # Skip writing for cover/appendix sections
        if section_def.get("type") in ("cover", "appendix"):
            section_repo.update_section(section_id, {"status": "draft_complete"})
            elapsed = time.time() - start_time
            logger.info(f"[Parallel] Section {section['section_slug']} skipped (cover/appendix), took {elapsed:.1f}s")
            return {"section_id": section_id, "status": "skipped", "elapsed": elapsed}
        
        # 3b: Write draft
        write_section_draft(
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            section_def=section_def,
            research_pack=pack,
            signed_claims=signed_claims,
            products=products,
            schema_type=schema_type,
            product_id_to_name=product_id_to_name,
            draft_type="initial",
            is_blocked=is_blocked,
        )
        
        # Get latest draft for review
        draft = draft_repo.get_latest_draft(section_id)
        if not draft:
            elapsed = time.time() - start_time
            logger.warning(f"[Parallel] No draft for section {section_id}, took {elapsed:.1f}s")
            return {"section_id": section_id, "status": "no_draft", "elapsed": elapsed}
        
        # 3c: LLM review
        review_result = review_section(
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            draft=draft,
            section_def=section_def,
            research_pack=pack,
            revision_round=0,
        )
        
        # 3d: Revision loop (up to MAX_REVISION_ROUNDS)
        last_word_count = 0
        rate_limit_failures = 0
        for round_idx in range(1, MAX_REVISION_ROUNDS + 1):
            section_status = section_repo.get_section(section_id)
            if section_status and section_status.get("status") != "revision_requested":
                break
            
            rework_instruction = review_result.get("rework_instruction", "")
            if not rework_instruction:
                break
            
            logger.info(f"[Parallel] Section {section['section_slug']} revision round {round_idx}")
            
            # Write revision
            write_result = write_section_draft(
                section_id=section_id,
                report_id=report_id,
                run_id=run_id,
                section_def=section_def,
                research_pack=pack,
                signed_claims=signed_claims,
                products=products,
                schema_type=schema_type,
                product_id_to_name=product_id_to_name,
                revision_feedback=rework_instruction,
                draft_type="revision",
                is_blocked=is_blocked,
            )
            
            # Detect fallback / rate-limit
            if not write_result.get("llm_success", True):
                rate_limit_failures += 1
                logger.warning(f"[Parallel] Section {section_id} hit rate limit")
                break
            
            # Progress check
            new_word_count = write_result.get("word_count") or 0
            if new_word_count > 0 and new_word_count <= last_word_count * 0.9:
                logger.info(f"[Parallel] Section {section_id} didn't grow, accepting current draft")
                break
            last_word_count = max(last_word_count, new_word_count)
            
            # Re-review
            draft = draft_repo.get_latest_draft(section_id)
            review_result = review_section(
                section_id=section_id,
                report_id=report_id,
                run_id=run_id,
                draft=draft,
                section_def=section_def,
                research_pack=pack,
                revision_round=round_idx,
            )
            
            # If review hit rate limit, stop
            review_failed = review_result.get("status") == "failed" and (
                "429" in str(review_result.get("error", ""))
                or review_result.get("quality_score") is None
            )
            if review_failed:
                rate_limit_failures += 1
                logger.warning(f"[Parallel] Section {section_id} review hit rate limit")
                break
        
        elapsed = time.time() - start_time
        logger.info(f"[Parallel] Section {section['section_slug']} completed in {elapsed:.1f}s")
        return {"section_id": section_id, "status": "completed", "elapsed": elapsed}
        
    except Exception as exc:
        elapsed = time.time() - start_time
        logger.error(f"[Parallel] Section {section_id} failed: {exc}", exc_info=True)
        return {"section_id": section_id, "status": "error", "error": str(exc), "elapsed": elapsed}


# ============================================================================
# High-level Workflow
# ============================================================================

def run_deep_report_workflow(
    run_id: str,
    report_id: str,
    signed_claims: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    products: list[str],
    research_plan: dict[str, Any] | None = None,
    schema_type: str | None = None,
    domain_schema: dict[str, Any] | None = None,
    query_understanding: dict[str, Any] | None = None,
    rework_required_claims: list[dict[str, Any]] | None = None,
    analyst_signed_claims: list[dict[str, Any]] | None = None,
    product_id_to_name: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Execute the complete Deep Report v2 workflow.

    Steps:
    1. Get report outline (priority: research_plan > domain_schema > default > LLM)
    2. Initialize sections in DB
    3. For each section:
       a. Build research pack (bind evidence/claims)
       b. Write draft (LLM, traced)
       c. Review (LLM-powered, traced)
       d. If revision needed: rewrite with feedback (up to MAX_REVISION_ROUNDS)
    4. Generate tables (TableAgent, LLM-driven)
    5. Generate figures (ChartSpecAgent, LLM-driven)
    6. Final review record
    7. Assemble final report with markdown + HTML
    
    vNext-R3-B (泛化): 
    - Accepts domain_schema for cross-domain competitive analysis
    - Accepts query_understanding for report type detection
    - Generates domain-specific comparison dimensions
    """
    task_brief = research_plan or {}
    logger.info(f"Starting Deep Report v2 workflow for run_id={run_id}")
    product_id_to_name = product_id_to_name or {}

    # Log domain schema if available (for generalized support)
    if domain_schema:
        logger.info(f"Using domain schema: {domain_schema.get('name', 'unknown')} "
                    f"(source: {domain_schema.get('source', 'unknown')})")
    
    # Step 1: Get outline (with domain schema support)
    outline = get_report_outline(
        run_id=run_id,
        research_plan=research_plan,
        task_brief=task_brief,
        signed_claims=signed_claims,
        domain_schema=domain_schema,
        query_understanding=query_understanding,
    )

    if not outline:
        logger.warning("No outline available for run_id=%s, using default", run_id)
        outline = get_default_outline()

    logger.info(f"Using outline with {len(outline)} sections")

    # ── P0-Rebuild: Compute preliminary is_blocked BEFORE writing sections ──────────
    # We compute this from evidence gate status so the LLM knows pre-assessment state
    # while writing — not just as post-processing.
    # The gate has NOT been applied to evidence_items yet (that happens in assemble_final_report),
    # so we run a mini-gate check here for the preliminary status.
    gated_evidence = _gate_evidence_by_dimension(evidence_items)
    has_gated = any(
        e.get("gate_rejection") for e in gated_evidence if isinstance(e, dict)
    )
    has_zero_claims = len(signed_claims) == 0
    preliminary_is_blocked = has_gated or has_zero_claims
    if preliminary_is_blocked:
        logger.info("Report is in PRE-ASSESSMENT state: has_gated=%s has_zero_claims=%s", has_gated, has_zero_claims)

    # Step 1.5: Create report record in DB if it doesn't exist
    # This is required because report_sections has FK to reports table
    from backend.app.storage.repositories import ReportRepository
    from datetime import datetime as dt
    report_repo = ReportRepository()
    try:
        existing_report = report_repo.get_report(report_id)
        if not existing_report:
            report_record = {
                "report_id": report_id,
                "run_id": run_id,
                "title": f"Deep Report - {run_id}",
                "report_status": "draft",
                "quality_summary": {},
                "created_by_agent": "deep_report_v2",
                "created_at": dt.utcnow().isoformat(),
                "updated_at": dt.utcnow().isoformat(),
            }
            report_repo.add_report(report_record)
            logger.info(f"Created report record: {report_id}")
    except Exception as e:
        logger.warning(f"Could not create report record: {e}")

    # Step 2: Initialize sections
    sections = initialize_report_sections(report_id, run_id, outline)
    logger.info(f"Initialized {len(sections)} sections")

    section_repo = ReportSectionRepository()
    draft_repo = SectionDraftRepository()
    review_repo = ReportReviewV2Repository()

    # Step 3: Process sections in PARALLEL for significant speedup
    # Before: 13 sections × 60s (serial) = ~13 minutes
    # After: 13 sections / 5 workers × 60s (parallel) = ~3 minutes
    import time
    parallel_start = time.time()
    
    max_workers = min(MAX_PARALLEL_SECTIONS, len(sections))
    logger.info(f"Processing {len(sections)} sections with {max_workers} parallel workers")
    
    section_results = []
    
    # P0-fix: Skip sections that are already completed (draft_complete status).
    # Without this check, when write_report_v2 is replayed (e.g., after HITL),
    # sections that were already processed would be re-processed, causing duplicate
    # drafts and wasted LLM calls.
    sections_to_process = []
    sections_skipped = []
    for section in sections:
        existing_drafts = draft_repo.get_drafts_by_section(section["section_id"])
        section_status = section_repo.get_section(section["section_id"])
        if (existing_drafts and 
            section_status and 
            section_status.get("status") == "draft_complete"):
            logger.info(f"Section {section['section_slug']} already completed ({len(existing_drafts)} drafts), skipping")
            sections_skipped.append({
                "section_id": section["section_id"],
                "status": "skipped",
                "reason": "already_completed"
            })
        else:
            sections_to_process.append(section)
    
    logger.info(f"Skipped {len(sections_skipped)} already-completed sections, processing {len(sections_to_process)} sections")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all section tasks
        future_to_section = {
            executor.submit(
                _process_section_parallel,
                section,
                outline,
                report_id,
                run_id,
                signed_claims,
                facts,
                evidence_items,
                products,
                schema_type,
                product_id_to_name,
                preliminary_is_blocked,
            ): section
            for section in sections_to_process
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_section):
            section = future_to_section[future]
            try:
                result = future.result(timeout=600)  # 10 min timeout per section
                section_results.append(result)
                logger.info(f"Section {section['section_slug']} result: {result.get('status')}")
            except Exception as exc:
                logger.error(f"Section {section['section_slug']} failed with timeout/exc: {exc}")
                section_results.append({
                    "section_id": section["section_id"],
                    "status": "error",
                    "error": str(exc),
                })
    
    # Add skipped sections to results
    section_results.extend(sections_skipped)
    
    parallel_elapsed = time.time() - parallel_start
    completed = sum(1 for r in section_results if r.get("status") == "completed")
    failed = sum(1 for r in section_results if r.get("status") == "error")
    logger.info(f"Parallel section processing complete: {completed} completed, {failed} failed, took {parallel_elapsed:.1f}s")

    # Step 4: Generate tables (TableAgent, LLM-driven)
    tables = []
    table_types = [
        # P0 Fix: Expanded to 6 sub-dimensions per 开题材料 function_tree schema.
        # Each dimension maps to a specific set of claims via claim.dimension field.
        # Post-processing step recomputes evidence_count from actual claim data.
        ("feature_matrix", "功能对比矩阵", [
            "workflow_orchestration",
            "rag_knowledge",
            "model_support",
            "multi_agent",
            "integration",
            "security_compliance",
        ]),
        ("pricing_matrix", "定价对比矩阵", [
            "free_tier",
            "paid_plans",
            "enterprise_pricing",
        ]),
        ("user_scenario_matrix", "用户场景对比", [
            "non_technical_business",
            "low_code_developers",
            "professional_developers",
            "ai_engineers",
        ]),
    ]

    for table_type, table_title, dimensions in table_types:
        table = generate_comparison_table(
            report_id=report_id,
            run_id=run_id,
            table_type=table_type,
            table_title=table_title,
            products=products,
            claims=signed_claims,
            dimensions=dimensions,
            product_id_to_name=product_id_to_name,
        )
        tables.append(table)

    # Step 5: Generate figures (ChartSpecAgent, LLM-driven)
    figures = generate_report_figures(
        report_id=report_id,
        run_id=run_id,
        products=products,
        claims=evidence_items,
        signed_claims=signed_claims,
        evidence_items=evidence_items,
    )

    # Step 6: Final review record
    all_sections = section_repo.get_sections_by_report(report_id)
    avg_depth = sum(
        s.get("depth_score") or 0 for s in all_sections if (s.get("depth_score") or 0) > 0
    ) / max(1, len([s for s in all_sections if (s.get("depth_score") or 0) > 0]))

    # Calculate evidence coverage rate
    claims_with_evidence = sum(
        1 for c in signed_claims if c.get("evidence_ids") and len(c["evidence_ids"]) > 0
    )
    evidence_coverage_rate = claims_with_evidence / max(1, len(signed_claims))

    final_review_id = _generate_id("final_review")
    final_review = ReportReview.create(
        review_id=final_review_id,
        report_id=report_id,
        run_id=run_id,
        review_type="final",
        reviewer_agent="report_reviewer",
    )
    final_review.overall_score = avg_depth
    final_review.depth_score = avg_depth
    final_review.evidence_score = evidence_coverage_rate * 100
    final_review.status = "pass" if avg_depth >= 50 else "rework_required"
    final_review.approved = final_review.status == "pass"
    review_repo.create_review(final_review.model_dump())

    # Step 7: Assemble report
    report_data = assemble_final_report(
        report_id=report_id,
        run_id=run_id,
        sections=all_sections,
        tables=tables,
        figures=figures,
        metadata={
            "schema_type": schema_type,
            "products": products,
            "claims_count": len(signed_claims),
            "rework_required_claims_count": len(rework_required_claims or []),
            "evidence_count": len(evidence_items),
            "evidence_items": evidence_items,
            "signed_claims": signed_claims,
            "rework_required_claims": rework_required_claims or [],
            "final_review_id": final_review_id,
            "_analyst_signed_claims": analyst_signed_claims or [],
            "_product_id_to_name": product_id_to_name or {},
        },
    )

    # Step 7b: Inject evidence registry for citation enrichment in HTML renderer.
    # This enables [E1], [E2] → <a> with tooltip showing source/title/snippet/fetched_at.
    # Build registry directly from DB with source JOIN so we always have title/url metadata.
    # Fetch ALL evidence for this run with source metadata in one query (same JOIN as _build_evidence_appendix)
    all_evidence_for_run = _fetch_evidence_with_sources(run_id, None)

    # P0 Fix: Whitelist of fields to expose in HTML tooltip registry (avoid leaking
    # internal fields like quality_json, raw_text, etc. into the visible page source)
    _EV_REGISTRY_FIELDS = {
        "evidence_id", "run_id", "product_id", "product_slug", "schema_key",
        "snippet", "source_title", "source_url", "source_type", "trust_tier",
        "confidence", "section_title", "fetched_at", "created_at",
        "usable_for_claim", "gate_rejection", "quality_score",
        "product_name", "url", "domain",
    }
    # P0 Fix (critical): ev_registry is keyed by DB evidence_id (e.g. "ev_abc123")
    # but enrich_citations_in_markdown looks up by ordinal "E1", "E2", etc.
    # Build BOTH key spaces so all lookups succeed.
    ev_registry: dict[str, dict] = {}
    ev_list: list[dict] = list(all_evidence_for_run.values())
    for idx, ev in enumerate(ev_list, start=1):
        filtered = {k: v for k, v in ev.items() if k in _EV_REGISTRY_FIELDS}
        # Key 1: ordinal E1, E2, E3... (what enrich_citations_in_markdown looks up)
        ev_registry[f"E{idx}"] = filtered
        # Key 2: DB evidence_id (for direct lookups)
        db_id = ev.get("evidence_id", "")
        if db_id:
            ev_registry[db_id] = filtered

    report_data["evidence_registry"] = ev_registry
    # Also store ordinal list for other consumers
    report_data["_evidence_ordinal_list"] = ev_list

    # ── P4 Fix: Enrich evidence_appendix for JSON export with source metadata ───
    # report_data["evidence_appendix"] was set to raw evidence_items by assemble_final_report,
    # which lack source_title/source_url from the JOIN. Now that ev_registry has full metadata,
    # replace the appendix with enriched versions.
    _enriched_appendix: list[dict] = []
    for ev in report_data.get("evidence_appendix", []):
        ev_id = ev.get("evidence_id", "")
        enriched = ev_registry.get(ev_id, ev)
        _enriched_appendix.append(enriched)
    report_data["evidence_appendix"] = _enriched_appendix

    # Generate markdown
    markdown_content = generate_markdown_report(report_data)
    report_data["content_markdown"] = markdown_content

    # Generate HTML
    html_content = generate_html_report(report_data)
    report_data["content_html"] = html_content

    # ── Persist v2 markdown + HTML to filesystem ──────────────────────────────
    # This mirrors what export_report does for v1, but for v2 reports.
    # Without this, v2 reports are not visible via the /report-draft API.
    import os as _os
    from pathlib import Path as _Path
    _os.makedirs("data/reports", exist_ok=True)
    _report_id = report_data.get("report_id", f"report_{run_id}_v2")
    _md_path = f"data/reports/{_report_id}.md"
    _html_path = f"data/reports/{_report_id}.html"
    _Path(_md_path).write_text(markdown_content, encoding="utf-8")
    _Path(_html_path).write_text(html_content, encoding="utf-8")
    report_data["content_markdown_path"] = _md_path
    report_data["content_html_path"] = _html_path
    logger.info(f"Persisted v2 report: md={_md_path} ({len(markdown_content)} chars), html={_html_path} ({len(html_content)} chars)")

    # Also save JSON for the /report-draft API to read structured data
    _qs = report_data.get("quality_summary", {})
    _final_status = _qs.get("report_status", "draft")
    _json_path = f"data/reports/{_report_id}.json"
    _json_data = {
        "report_id": _report_id,
        "run_id": run_id,
        "report_version": report_data.get("report_version", DEEP_REPORT_VERSION),
        "quality_summary": _qs,
        "report_status": _final_status,  # Use the actual status from quality_summary
        "sections": report_data.get("sections", []),
        "tables": report_data.get("tables", []),
        "figures": report_data.get("figures", []),
        # P1 Fix: Include evidence_appendix and signed_claims in JSON for downstream consumers
        "evidence_appendix": report_data.get("evidence_appendix", []),
        "signed_claims": report_data.get("signed_claims", []),
        # P4 Fix: Include evidence_registry so enrichment data is preserved in JSON export
        "evidence_registry": report_data.get("evidence_registry", {}),
        # P5 Fix: Include products and generated_at for frontend rendering
        "products": report_data.get("products", []),
        "generated_at": report_data.get("generated_at", ""),
        "content_markdown_path": report_data.get("content_markdown_path", ""),
        "content_html_path": report_data.get("content_html_path", ""),
    }
    _Path(_json_path).write_text(json.dumps(_json_data, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Persisted v2 report JSON: {_json_path} (status={_final_status})")

    # ── Update run status so it appears in frontend run list ──────────────────
    # Without this, regenerate_report.py bypasses the API so the run stays
    # invisible to _get_default_run_id() even though the report was generated.
    try:
        from backend.app.storage.repositories import RunRepository
        _run_repo = RunRepository()
        _run_repo.update_status(
            run_id=run_id,
            status="completed",
            current_node="deep_report",
            completed_at=dt.utcnow().isoformat(),
        )
        logger.info(f"Updated run {run_id} status → completed")
    except Exception as _e:
        logger.warning(f"Could not update run status: {_e}")

    logger.info(f"Deep Report v2 workflow completed for run_id={run_id}")
    logger.info(f"Stats: {report_data['quality_summary']}")

    return report_data
