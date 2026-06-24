from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from backend.app.services.llm_client import get_llm_client
from backend.app.tracing.llm_trace import traced_llm_call, create_llm_fallback_trace

logger = logging.getLogger(__name__)

# Prompt version for analyst LLM calls
ANALYST_PROMPT_VERSION = "v1.2"

# 18-dimension trigger list (v1.2 - report-quality upgrade)
# Used by _build_dimension_trigger_block to instruct the LLM to attempt
# at least one claim per (product, dimension) pair, provided evidence exists.
DIMENSION_TRIGGER_LIST: list[dict[str, str]] = [
    # Core function_tree (6)
    {"dim": "workflow_orchestration", "zh": "工作流编排", "category": "function"},
    {"dim": "rag_knowledge",          "zh": "RAG 与知识库", "category": "function"},
    {"dim": "model_support",          "zh": "模型支持",     "category": "function"},
    {"dim": "multi_agent",            "zh": "多 Agent 能力", "category": "function"},
    {"dim": "integration",            "zh": "集成与扩展",   "category": "function"},
    {"dim": "ease_of_use",            "zh": "易用性",       "category": "function"},
    # Pricing (4)
    {"dim": "pricing_model",          "zh": "定价模式",     "category": "pricing"},
    {"dim": "free_tier",              "zh": "免费层",       "category": "pricing"},
    {"dim": "paid_plans",             "zh": "付费方案",     "category": "pricing"},
    {"dim": "value_proposition",      "zh": "价值主张",     "category": "pricing"},
    # User / market (4)
    {"dim": "user_persona",           "zh": "目标用户",     "category": "user_market"},
    {"dim": "market_positioning",     "zh": "市场定位",     "category": "user_market"},
    {"dim": "customer_voice",         "zh": "客户声音",     "category": "user_market"},
    {"dim": "competitive_positioning","zh": "竞争定位",     "category": "user_market"},
    # Enterprise / deployment (4)
    {"dim": "deployment_options",     "zh": "部署方式",     "category": "enterprise"},
    {"dim": "security",               "zh": "安全",         "category": "enterprise"},
    {"dim": "compliance",             "zh": "合规",         "category": "enterprise"},
    {"dim": "enterprise_readiness",   "zh": "企业就绪度",   "category": "enterprise"},
]

# v1.2: Map trigger-list dim names to whatever the user task_brief might use
# (lets the trigger block stay stable while still respecting normalization)
_TRIGGER_DIM_ALIASES: dict[str, list[str]] = {
    "rag_knowledge": ["rag", "rag_support", "knowledge_base"],
    "ease_of_use": ["usability", "user_friendly"],
    "value_proposition": ["ai_feature_pricing"],
    "market_positioning": ["market_position", "brand_positioning"],
    "competitive_positioning": ["competitive_position"],
    "deployment_options": ["deployment"],
}

# Fallback defaults (used when task_brief doesn't specify)
PRODUCTS = ["dify", "coze", "fastgpt", "flowise"]

ALL_DIMENSIONS = [
    # Core function_tree dimensions
    "function_tree",
    "workflow_orchestration",
    "rag",
    "rag_knowledge",
    "knowledge_base",
    "multi_agent",
    "model_support",
    "integration",
    "deployment",
    "ease_of_use",
    "usability",
    # Core pricing dimensions
    "pricing_model",
    "pricing",
    "free_tier",
    "paid_plans",
    "enterprise_pricing",
    "trial_policy",
    "cost",
    # Core user_persona dimensions
    "user_persona",
    "user_friendly",
    "usability",
    # Core enterprise dimensions
    "enterprise_readiness",
    "security",
    "compliance",
    "sla",
    "admin_security_cost",
    # Analysis dimensions
    "customer_voice",
    "swot",
    "swot_strength",
    "swot_weakness",
    "swot_opportunity",
    "swot_threat",
    # Pricing analysis
    "value_proposition",
    "ai_feature_pricing",
    "competitive_positioning",
]

# Pricing analysis dimensions (vNext-R2-C)
PRICING_ANALYSIS_DIMENSIONS = [
    "pricing_model",
    "value_proposition",
    "ai_feature_pricing",
    "admin_security_cost",
    "migration_adoption",
    "competitive_positioning",
]

CLAIM_TYPES = [
    "factual_summary",
    "comparative_insight",
    "swot_strength",
    "swot_weakness",
    "swot_opportunity",
    "swot_threat",
    "recommendation",
]

RISK_LEVELS = ["low", "medium", "high"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalystAgent:
    def analyze(
        self,
        evidence_items: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        task_brief: dict[str, Any],
        run_id: str,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        logger.info(
            "AnalystAgent.analyze started | run_id=%s | evidence_count=%d | facts_count=%d",
            run_id,
            len(evidence_items),
            len(facts),
        )

        if not evidence_items:
            logger.warning(
                "AnalystAgent.analyze | run_id=%s | No evidence items provided, returning empty list.",
                run_id,
            )
            return []

        # vNext-R2-C: Extract valid products and dimensions from task_brief
        valid_products, valid_dimensions = self._extract_valid_products_and_dimensions(task_brief)
        
        # P0-Fix: Also add run-scoped product_ids from evidence items to valid_products.
        # Evidence items have product_id like "run_xxx_coze" (from source collection),
        # while task_brief may have base IDs like "product_fb68d0a4". Without this,
        # the analyst's _normalize_product_id returns None for ALL evidence, causing
        # 0 claims to be generated even though evidence exists.
        for ev in evidence_items:
            ev_pid = str(ev.get("product_id", "")).strip()
            if ev_pid and ev_pid not in valid_products:
                valid_products.add(ev_pid)
                # Also add lower-case variant for case-insensitive matching
                valid_products.add(ev_pid.lower())
        
        system_msg = self._build_system_prompt(task_brief)
        user_msg = self._build_user_prompt(evidence_items, facts, task_brief, run_id, valid_products, valid_dimensions)

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        input_payload = {
            "evidence_count": len(evidence_items),
            "facts_count": len(facts),
            "task_brief_title": task_brief.get("title", ""),
            "task_type": task_brief.get("task_type", task_brief.get("schema_type", "")),
            "valid_products": list(valid_products),
            "valid_dimensions": list(valid_dimensions),
        }

        def _do_llm_call():
            client = get_llm_client()
            return client.chat_json(messages, temperature=0.1, max_tokens=8192, timeout=120)

        def _parse_response(response: Any) -> dict[str, Any]:
            if isinstance(response, dict):
                return response
            return {"raw_response": str(response)}

        try:
            result = traced_llm_call(
                run_id=run_id,
                project_id=project_id,
                node_name="analyze_dimensions",
                agent_name="AnalystAgent",
                agent_role="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                prompt_text=user_msg,
                input_payload=input_payload,
                call_fn=_do_llm_call,
                parse_fn=_parse_response,
                input_length_hint=len(user_msg),
                decision_summary="Generated claim drafts from evidence",
            )
            
            response = result.get("parsed_output") or {}
            
        except Exception as exc:
            logger.error(
                "AnalystAgent.analyze | run_id=%s | LLM call failed: %s",
                run_id,
                exc,
            )
            
            # Record fallback trace
            create_llm_fallback_trace(
                run_id=run_id,
                project_id=project_id,
                node_name="analyze_dimensions",
                agent_name="AnalystAgent",
                agent_role="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                prompt_text=user_msg,
                input_payload=input_payload,
                reason=f"LLM_UNAVAILABLE_OR_ERROR: {type(exc).__name__}: {exc}",
                decision_summary="Fallback: no claims generated",
            )
            return []

        # vNext-R2-C: Pass valid_products and valid_dimensions for filtering
        claims = self._parse_and_enrich_claims(
            response, evidence_items, run_id,
            valid_products=valid_products,
            valid_dimensions=valid_dimensions,
        )

        # P0-Fix: Guarantee each product has at least one claim.
        # LLM non-determinism means same evidence can produce different claim sets.
        # _ensure_product_coverage issues supplemental calls for any product
        # that has usable evidence but received zero claims in the main pass.
        coverage_result = self._ensure_product_coverage(
            claims=claims,
            evidence_items=evidence_items,
            facts=facts,
            task_brief=task_brief,
            run_id=run_id,
            project_id=project_id,
            valid_products=valid_products,
        )
        if coverage_result.get("supplemental_claims"):
            logger.info(
                "AnalystAgent.analyze: added %d supplemental claims for missing products | "
                "run_id=%s | gaps=%s",
                len(coverage_result["supplemental_claims"]),
                run_id,
                coverage_result.get("gaps", []),
            )
            claims = claims + coverage_result["supplemental_claims"]

        logger.info(
            "AnalystAgent.analyze completed | run_id=%s | claims_generated=%d",
            run_id,
            len(claims),
        )
        return claims

    # ------------------------------------------------------------------ #
    # Valid products and dimensions extraction (vNext-R2-C)               #
    # ------------------------------------------------------------------ #

    def _extract_valid_products_and_dimensions(self, task_brief: dict[str, Any]) -> tuple[set[str], set[str]]:
        """
        Extract valid products and dimensions from task_brief.
        
        vNext-R2-C: Supports dynamic products (Slack, Teams, Zoom, etc.) 
        and pricing_analysis dimensions (value_proposition, ai_feature_pricing, etc.).
        Also supports multiple product field names: product_id, product_name, name, product_slug.
        
        Returns:
            tuple of (valid_products set, valid_dimensions set)
        """
        # --- Extract products ---
        raw_products = task_brief.get("products", [])
        if not raw_products:
            # Fallback to competitors if products not specified
            raw_products = task_brief.get("competitors", [])
        
        valid_products: set[str] = set()
        
        def _add_product_variants(pid: str) -> None:
            """Add all variants of product ID to valid_products set."""
            if not pid:
                return
            # Raw value
            valid_products.add(pid)
            # Lower case
            valid_products.add(pid.lower())
            # Space/dash to underscore
            valid_products.add(pid.lower().replace(" ", "_").replace("-", "_"))
            # Space/underscore to dash
            valid_products.add(pid.lower().replace("_", "-"))
        
        if raw_products:
            for p in raw_products:
                if isinstance(p, dict):
                    # vNext-R2-C: Support multiple field names with priority order
                    # product_id > product_name > name > product_slug
                    pid = (
                        p.get("product_id")
                        or p.get("product_name")
                        or p.get("name")
                        or p.get("product_slug")
                        or ""
                    )
                    _add_product_variants(pid)
                else:
                    # Product is a string
                    pid = str(p).strip()
                    _add_product_variants(pid)
        
        # vNext-R2-C: If no valid products found, fallback to default PRODUCTS
        if not valid_products:
            valid_products = set(PRODUCTS)
        
        # --- Extract dimensions ---
        raw_dimensions = task_brief.get(
            "analysis_dimensions", 
            task_brief.get("dimensions", [])
        )
        
        # Also check schema_type for pricing_analysis
        schema_type = task_brief.get("task_type", task_brief.get("schema_type", ""))
        
        valid_dimensions: set[str] = set()
        
        if raw_dimensions:
            for d in raw_dimensions:
                if isinstance(d, dict):
                    dim_id = d.get("dimension_id", "")
                    if dim_id:
                        valid_dimensions.add(dim_id)
                        valid_dimensions.add(dim_id.lower())
                else:
                    dim = str(d).strip()
                    if dim:
                        valid_dimensions.add(dim)
                        valid_dimensions.add(dim.lower())
        else:
            # Fallback based on schema_type
            if schema_type == "pricing_analysis":
                valid_dimensions = set(PRICING_ANALYSIS_DIMENSIONS)
            else:
                valid_dimensions = set(ALL_DIMENSIONS)
        
        return valid_products, valid_dimensions

    def _normalize_product_id(self, raw_pid: str, valid_products: set[str], run_scoped_fallback: str | None = None) -> str | None:
        """
        Normalize product_id for matching.
        
        vNext-R2-C: Supports:
        - "Slack" -> "slack"
        - "slack" -> "slack"
        - "Microsoft Teams" -> "microsoft_teams" / "microsoft teams"
        - "microsoft_teams" -> "microsoft_teams"
        - run-scoped "run_xxx_slack" -> extracts "slack" for matching
        
        P0-Fix: When no valid_product match is found, falls back to the run-scoped
        product_id (e.g. "run_xxx_coze") if provided. This ensures evidence and
        claims share the same ID space even when the analyst's valid_products set
        contains base IDs (product_abc123) that don't match run-scoped evidence IDs.
        """
        if not raw_pid:
            return None
        
        pid = raw_pid.strip()
        pid_lower = pid.lower()
        
        # Direct match
        if pid in valid_products or pid_lower in valid_products:
            return pid_lower
        
        # Check slugified versions
        slugified = pid_lower.replace(" ", "_").replace("-", "_")
        if slugified in valid_products:
            return slugified
        
        # Check if valid_products contains a normalized version of this product
        # e.g., if valid_products has "slack", and pid is "Slack" or "run_xxx_slack"
        for vp in valid_products:
            vp_lower = vp.lower()
            if pid_lower == vp_lower:
                return vp_lower
            # Check if product ID ends with the valid product name
            # e.g., "run_xxx_slack" contains "slack"
            if pid_lower.endswith("_" + vp_lower) or pid_lower.endswith("-" + vp_lower):
                return vp_lower
            if pid_lower.startswith(vp_lower + "_") or pid_lower.startswith(vp_lower + "-"):
                return vp_lower
        
        # P0-Fix: No valid_products match found. Fall back to the run-scoped
        # product_id so evidence and claims share the same ID space.
        if run_scoped_fallback:
            return run_scoped_fallback
        
        return None

    # Alias map: common LLM-generated dimension names → canonical ALL_DIMENSIONS entries.
    # Handles cases where LLM generates "ease_of_use" → "user_persona", "functionality" → "function_tree", etc.
    DIMENSION_ALIASES: dict[str, str] = {
        "ease_of_use": "user_persona",
        "ease of use": "user_persona",
        "usability": "user_persona",
        "user_friendly": "user_persona",
        "functionality": "function_tree",
        "features": "function_tree",
        "core_features": "function_tree",
        "product_capabilities": "function_tree",
        "agent_capabilities": "function_tree",
        "deployment": "function_tree",
        "deployment_options": "function_tree",
        "multi_agent": "function_tree",
        "multi_agent_capabilities": "function_tree",
        "rag": "function_tree",
        "knowledge_base": "function_tree",
        "model_support": "function_tree",
        "integration": "function_tree",
        "pricing": "pricing_model",
        "price": "pricing_model",
        "cost": "pricing_model",
        "enterprise": "enterprise_readiness",
        "enterprise_ready": "enterprise_readiness",
        "security": "enterprise_readiness",
        "compliance": "enterprise_readiness",
        "swot": "swot",
        "customer_voice": "customer_voice",
        "customer_reviews": "customer_voice",
        "market_position": "customer_voice",
    }

    def _normalize_dimension(self, raw_dim: str, valid_dimensions: set[str]) -> str | None:
        """
        Normalize dimension for matching.

        vNext-R2-C: Supports pricing_analysis dimensions like
        value_proposition, ai_feature_pricing, admin_security_cost, etc.
        Also handles aliases via DIMENSION_ALIASES for LLM-generated dimension names.
        """
        if not raw_dim:
            return None

        dim = raw_dim.strip()
        dim_lower = dim.lower()

        # Alias lookup with multi-variant keys (handles "ease_of_use" ↔ "ease of use")
        # Try original, underscore→space, space→underscore variants
        for variant in {dim_lower, dim_lower.replace("_", " "), dim_lower.replace(" ", "_")}:
            canonical = self.DIMENSION_ALIASES.get(variant)
            if canonical and canonical in valid_dimensions:
                return canonical

        # Keyword-based fallback: if raw dimension shares significant words with a
        # valid_dimension, map to it (supports LLM variants like "ease of use" → "user_persona")
        raw_words = set(dim_lower.replace("_", " ").replace("-", " ").split())
        meaningful_raw = raw_words - {"and", "or", "of", "the", "a", "an", "to", "for", "in", "on", "andor"}
        for vd in valid_dimensions:
            vd_words = set(vd.replace("_", " ").replace("-", " ").split())
            meaningful_vd = vd_words - {"and", "or", "of", "the", "a", "an", "to", "for", "in", "on"}
            # Match if any meaningful word from raw overlaps with vd's meaningful words
            if meaningful_raw and meaningful_vd and meaningful_raw & meaningful_vd:
                return vd

        return None

    # ------------------------------------------------------------------ #
    # Prompt construction                                                  #
    # ------------------------------------------------------------------ #

    def _extract_valid_dimensions_for_trigger(self, task_brief: dict[str, Any] | None) -> set[str]:
        """
        v1.2: Extract valid_dimensions from task_brief for the trigger block.

        Mirrors the same logic used in _extract_valid_products_and_dimensions
        (called by analyze()), so the system-prompt trigger block matches
        what the user prompt actually presents to the LLM.
        """
        if not task_brief:
            return set(ALL_DIMENSIONS)
        raw_dimensions = task_brief.get(
            "analysis_dimensions",
            task_brief.get("dimensions", []),
        )
        schema_type = task_brief.get("task_type", task_brief.get("schema_type", ""))
        if raw_dimensions:
            valid_dims: set[str] = set()
            for d in raw_dimensions:
                if isinstance(d, dict):
                    dim_id = d.get("dimension_id", "")
                    if dim_id:
                        valid_dims.add(dim_id)
                        valid_dims.add(dim_id.lower())
                else:
                    dim = str(d).strip()
                    if dim:
                        valid_dims.add(dim)
                        valid_dims.add(dim.lower())
            return valid_dims or set(ALL_DIMENSIONS)
        if schema_type == "pricing_analysis":
            return set(PRICING_ANALYSIS_DIMENSIONS)
        return set(ALL_DIMENSIONS)

    def _build_dimension_trigger_block(self, valid_dimensions: set[str]) -> str:
        """
        Build a 18-dimension coverage target block (v1.2).

        The block tells the LLM to ATTEMPT at least one claim per
        (product, dimension) pair, provided evidence exists. Pairs without
        evidence are explicitly skipped (no fabrication).

        Only dimensions present in ``valid_dimensions`` are listed, so this
        block respects the task_brief's declared analysis dimensions.
        """
        # Filter trigger list to dimensions that are actually allowed for this run
        matched: list[dict[str, str]] = []
        for entry in DIMENSION_TRIGGER_LIST:
            aliases = _TRIGGER_DIM_ALIASES.get(entry["dim"], [])
            candidates = [entry["dim"]] + aliases
            if any(c in valid_dimensions for c in candidates):
                matched.append(entry)

        if not matched:
            return "No dimension coverage target available for this task."

        # Group by category for readability
        grouped: dict[str, list[dict[str, str]]] = {}
        for entry in matched:
            grouped.setdefault(entry["category"], []).append(entry)

        category_zh = {
            "function":     "功能维度",
            "pricing":      "定价维度",
            "user_market":  "用户与市场维度",
            "enterprise":   "企业与部署维度",
        }

        lines: list[str] = [
            f"You should ATTEMPT to cover each of the following {len(matched)} dimensions, "
            "producing at least one claim per (product, dimension) pair IF the evidence supports it. "
            "If no evidence exists for a (product, dimension) pair, SKIP that pair entirely — "
            "do NOT fabricate or hallucinate.\n",
        ]
        for cat in ("function", "pricing", "user_market", "enterprise"):
            entries = grouped.get(cat, [])
            if not entries:
                continue
            lines.append(f"- {category_zh[cat]}:")
            for e in entries:
                lines.append(f"  - `{e['dim']}` ({e['zh']})")
        lines.append("")
        lines.append(
            "Target output: aim for 15+ signed-eligible claims across the four products, "
            "weighted by evidence availability. Cover all four products, not just one."
        )
        return "\n".join(lines)

    def _build_coverage_matrix_block(
        self,
        evidence_items: list[dict[str, Any]],
        valid_products: set[str],
        valid_dimensions: set[str],
    ) -> str:
        """
        v1.2: Build a (product, dimension) coverage matrix from available evidence.

        The matrix tells the LLM exactly which pairs are evidence-backed and
        therefore eligible for claim generation. This drives the increase
        from 6-7 claims to 15+.

        Output format (markdown-ish):
          product=coze:  workflow_orchestration(3 ev), rag_knowledge(2 ev), ...
          product=dify:  ...

        Only pairs with >= 1 evidence item are listed, to keep the matrix honest
        and to discourage fabrication of unsupported pairs.
        """
        from collections import defaultdict
        # Count evidence per (normalized_product, normalized_dimension)
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for ev in evidence_items:
            pid_raw = str(ev.get("product_id", "")).strip()
            dim_raw = str(ev.get("schema_key", "")).strip()
            if not pid_raw or not dim_raw:
                continue
            # Reuse the same normalization used elsewhere
            pid = self._normalize_product_id(pid_raw, valid_products) or pid_raw.lower()
            dim = self._normalize_dimension(dim_raw, valid_dimensions) or dim_raw.lower()
            if pid in valid_products or pid in {p.lower() for p in valid_products}:
                if dim in valid_dimensions or dim in {d.lower() for d in valid_dimensions}:
                    counts[(pid, dim)] += 1

        if not counts:
            return "(No evidence-backed pairs detected. Generate claims only where you see evidence in the list above.)"

        # Group by product
        per_product: dict[str, dict[str, int]] = defaultdict(dict)
        for (pid, dim), n in counts.items():
            per_product[pid][dim] = per_product[pid].get(dim, 0) + n

        lines: list[str] = [
            f"The following {sum(len(v) for v in per_product.values())} (product, dimension) "
            "pairs have evidence. Generate at least one claim for each pair.",
            "Skip pairs not listed here.",
            "",
        ]
        for pid in sorted(per_product.keys()):
            dims_sorted = sorted(per_product[pid].items(), key=lambda x: -x[1])
            dims_str = ", ".join(f"{d} ({n} ev)" for d, n in dims_sorted)
            lines.append(f"- product=`{pid}`: {dims_str}")

        lines.append("")
        lines.append(
            "Target: aim for 15+ claims total, distributed across all products. "
            "Use higher confidence for pairs with more evidence, lower for pairs with thin evidence."
        )
        return "\n".join(lines)

    def _build_system_prompt(self, task_brief: dict[str, Any] | None = None) -> str:
        """
        Build system prompt dynamically based on task_brief.
        
        vNext-R2-C: No longer hardcoded to specific products like Dify/Coze.
        """
        task_type = task_brief.get("task_type", task_brief.get("schema_type", "")) if task_brief else ""
        
        # Dynamic domain context based on schema_type
        domain_context = ""
        if task_type == "pricing_analysis":
            domain_context = "Focus on pricing models, value proposition, AI feature costs, admin/security costs, and competitive positioning."
        elif task_type == "knowledge_management":
            domain_context = "Focus on knowledge structure, collaboration, permission governance, AI assistance, and enterprise integration."
        elif task_type == "ai_coding_assistant":
            domain_context = "Focus on AI coding capabilities, IDE integration, code generation quality, and enterprise security."
        elif task_type:
            domain_context = f"Analysis domain: {task_type}."
        
        # Build trigger block based on valid_dimensions (extracted from task_brief)
        valid_dims_for_trigger = self._extract_valid_dimensions_for_trigger(task_brief)
        trigger_block = self._build_dimension_trigger_block(valid_dims_for_trigger)

        return f"""\
You are a senior competitive intelligence analyst. Analyze only the products and dimensions \
specified in the task brief. {domain_context}

CORE PRINCIPLE — NO EVIDENCE, NO CLAIM. Use only the provided evidence items. \
Do NOT fabricate, extrapolate, or hallucinate any information that is not directly \
supported by the evidence. If evidence is sparse for a product/dimension pair, \
generate fewer claims with lower confidence rather than inventing details.

# LANGUAGE REQUIREMENT — MANDATORY
claim_text must be written in **Simplified Chinese (简体中文)**.
Do NOT write claim_text in English. The final report is in Chinese.
Exception: product names, brand names, and technical terms (API, SDK, LLM, etc.) \
may remain in English where natural.

Each claim must cite at least one evidence_id from the provided evidence list. \
Claims without evidence_ids will be rejected in the review stage.

You will receive:
- A task brief describing the analysis scope and objectives.
- Evidence items (snippets) extracted from web sources, each with an evidence_id, \
  product_id, and schema_key (dimension tag).

# DIMENSION COVERAGE TARGET (v1.2)
{trigger_block}

Your output must be a valid JSON object with a top-level "claims" array. \
Each element in the array must conform exactly to the schema below."""

    def _build_user_prompt(
        self,
        evidence_items: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        task_brief: dict[str, Any],
        run_id: str,
        valid_products: set[str] | None = None,
        valid_dimensions: set[str] | None = None,
    ) -> str:
        # vNext-R2-C: Use provided valid_products/valid_dimensions or fall back
        if valid_products is None:
            valid_products = set(PRODUCTS)
        if valid_dimensions is None:
            valid_dimensions = set(ALL_DIMENSIONS)
        
        task_title = task_brief.get("title", "Competitive Analysis")
        task_description = task_brief.get("description", "")

        # Build target_products list for prompt display
        target_products = list(valid_products)
        target_dimensions = list(valid_dimensions)

        evidence_lines: list[str] = []
        for ev in evidence_items:
            snippet = (ev.get("snippet") or "").strip()
            if not snippet:
                continue
            evidence_lines.append(
                f"- evidence_id: {ev.get('evidence_id', '')}  "
                f"product_id: {ev.get('product_id', '')}  "
                f"schema_key: {ev.get('schema_key', '')}  "
                f"snippet: {snippet[:300]}"
            )

        facts_lines: list[str] = []
        for fact in facts:
            fact_text = (fact.get("fact_text") or "").strip()
            if not fact_text:
                continue
            facts_lines.append(
                f"- fact_id: {fact.get('fact_id', '')}  "
                f"product_id: {fact.get('product_id', '')}  "
                f"fact_text: {fact_text[:300]}"
            )

        lines: list[str] = [
            f"# Task Brief",
            f"Title: {task_title}",
            f"Description: {task_description}",
            f"Target Products: {', '.join(target_products)}",
            f"Analysis Dimensions: {', '.join(target_dimensions)}",
            "",
            "# Evidence Items (Primary Source Material)",
            "",
        ]

        if evidence_lines:
            lines.extend(evidence_lines)
        else:
            lines.append("(No evidence items provided)")

        # P5 Fix: Add synthesis instruction BEFORE the requirements section.
        # This prevents the LLM from reproducing raw search snippets as claims.
        lines.extend([
            "",
            "# Important: Evidence Quality Guidance",
            "",
            "The evidence items above are RAW SOURCE MATERIAL. Your task is to SYNTHESIZE these into",
            "structured competitive analysis claims — NOT to reproduce or list them verbatim.",
            "",
            "For each product+dimension pair with evidence:",
            "  1. Read ALL evidence snippets for that pair first",
            "  2. Form a synthesized understanding",
            "  3. Write a concise claim that reflects the consensus across evidence",
            "",
            "NEVER produce output that looks like a search result listing (e.g. 'Here are the top 5 results...',",
            "'Prioritizing Official Resources', or bare URL snippets).",
            "NEVER write claims that are just paraphrased evidence snippets.",
            "NEVER prefix a claim with 'According to [source]' or reproduce the source title in the claim.",
            "",
        ])

        lines.extend(["", "# Extracted Facts (if any)", ""])
        if facts_lines:
            lines.extend(facts_lines)
        else:
            lines.append("(No facts extracted)")

        # v1.2: Build a (product, dimension) coverage matrix from available evidence
        # so the LLM has an explicit checklist to satisfy, increasing signed-claim count.
        coverage_block = self._build_coverage_matrix_block(
            evidence_items=evidence_items,
            valid_products=set(target_products) if target_products else set(PRODUCTS),
            valid_dimensions=set(target_dimensions) if target_dimensions else set(ALL_DIMENSIONS),
        )

        lines.extend([
            "",
            "# Coverage Matrix (v1.2 — evidence-backed pairs to cover)",
            "",
            coverage_block,
            "",
        ])

        lines.extend([
            "",
            "# Requirements",
            "",
            "1. For each product and each dimension, generate 2–3 claim drafts",
            "   based ONLY on the evidence above.",
            "",
            "2. Each claim must be a dict with these exact fields:",
            "   - claim_id: string like 'claim_{product_id}_{dimension}_{idx}'",
            "   - run_id: pass the run_id through unchanged",
            "   - product_id: one of " + ", ".join(target_products),
            "   - dimension: one of " + ", ".join(target_dimensions),
            "   - claim_text: concise, factual, 1–3 sentences",
            "   - fact_ids: list of supporting fact_ids (may be empty [])",
            "   - evidence_ids: list of evidence_ids that support this claim (MUST have at least one)",
            "   - confidence: float 0.0–1.0  (lower when evidence is thin)",
            "   - risk_level: 'low' | 'medium' | 'high'  ('high' = inference-heavy)",
            "   - claim_type: one of: " + ", ".join(CLAIM_TYPES),
            "   - review_status: always 'pending'",
            "",
            "3. claim_type guidance:",
            "   - 'factual_summary': direct extraction from evidence",
            "   - 'comparative_insight': draws comparison across products",
            "   - 'swot_strength': SW of the SWOT dimension",
            "   - 'swot_weakness': SW of the SWOT dimension",
            "   - 'swot_opportunity': OT of the SWOT dimension",
            "   - 'swot_threat': OT of the SWOT dimension",
            "   - 'recommendation': actionable recommendation",
            "",
            "4. If there is no evidence for a specific product+dimension pair,",
            "   skip that pair entirely. Do NOT generate placeholder claims.",
            "",
            "5. Keep claim_text factual and neutral. Do not editorialize.",
            "   Avoid words like 'best', 'worst', 'only', 'all', 'always', 'never'",
            "   unless directly supported by the evidence.",
            "",
            "5b. claim_text must be in Simplified Chinese (简体中文).",
            "   Only product names, brand names, and technical terms may remain in English.",
            "   Example GOOD: Dify提供可视化工作流编排功能，支持拖拽节点和版本控制。",
            "   Example BAD: Dify provides visual workflow orchestration capabilities.",
            "",
            "Return JSON only. Format: {\"claims\": [...]}",
            "Do NOT wrap the JSON in markdown fences or any other formatting.",
            "Do NOT include any explanation outside the JSON object.",
        ])

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Response parsing (vNext-R2-C: dynamic product/dimension filtering)   #
    # ------------------------------------------------------------------ #

    def _parse_and_enrich_claims(
        self,
        response: dict[str, Any],
        evidence_items: list[dict[str, Any]],
        run_id: str,
        valid_products: set[str] | None = None,
        valid_dimensions: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Parse and enrich claims from LLM response.
        
        vNext-R2-C: Uses dynamic valid_products and valid_dimensions for filtering,
        supporting non-standard products (Slack, Teams, Zoom, etc.) and 
        pricing_analysis dimensions (value_proposition, ai_feature_pricing, etc.).
        
        If valid_products/valid_dimensions are not provided, falls back to
        the default PRODUCTS and ALL_DIMENSIONS sets.
        """
        claims: list[dict[str, Any]] = []

        raw_claims: list[dict[str, Any]] = []
        if isinstance(response, dict):
            raw_claims = response.get("claims", [])

        if not isinstance(raw_claims, list):
            logger.warning(
                "AnalystAgent._parse_and_enrich_claims | run_id=%s | "
                "Response 'claims' field is not a list (type=%s). Returning empty list.",
                run_id,
                type(raw_claims).__name__,
            )
            return []

        # vNext-R2-C: Use dynamic valid_products/valid_dimensions, fall back to defaults
        if valid_products is None:
            valid_products = set(PRODUCTS)
        if valid_dimensions is None:
            valid_dimensions = set(ALL_DIMENSIONS)
        
        valid_claim_types = set(CLAIM_TYPES)
        valid_risk_levels = set(RISK_LEVELS)

        idx_by_product_dim: dict[str, int] = {}

        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue

            product_id_raw = str(raw.get("product_id") or "").strip()
            dimension_raw = str(raw.get("dimension") or "").strip()

            # vNext-R2-C: Use normalized matching for products and dimensions
            product_id = self._normalize_product_id(product_id_raw, valid_products)
            dimension = self._normalize_dimension(dimension_raw, valid_dimensions)

            if not product_id:
                logger.debug(
                    "AnalystAgent skipping claim: product_id=%r not in valid_products=%s",
                    product_id_raw, valid_products
                )
                continue
            if not dimension:
                logger.debug(
                    "AnalystAgent skipping claim: dimension=%r not in valid_dimensions=%s",
                    dimension_raw, valid_dimensions
                )
                continue

            claim_text = str(raw.get("claim_text") or "").strip()
            if not claim_text:
                logger.debug("AnalystAgent skipping claim: empty claim_text")
                continue

            raw_evidence_ids = raw.get("evidence_ids", [])
            # Filter evidence_ids: only keep those that exist in the input evidence_items
            valid_evidence_ids = {ev.get("evidence_id") for ev in evidence_items if ev.get("evidence_id")}
            evidence_ids = [str(e).strip() for e in raw_evidence_ids if e and str(e).strip() in valid_evidence_ids]
            if not evidence_ids and raw_evidence_ids:
                logger.debug(
                    "AnalystAgent: claim %s/%s has no valid evidence_ids matching input items - lower confidence",
                    product_id,
                    dimension,
                )

            fact_ids = raw.get("fact_ids", [])
            if isinstance(fact_ids, list):
                fact_ids = [str(f).strip() for f in fact_ids if f]
            else:
                fact_ids = []

            raw_confidence = raw.get("confidence")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else 0.5
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = 0.5

            risk_level = str(raw.get("risk_level") or "medium").strip().lower()
            if risk_level not in valid_risk_levels:
                risk_level = "medium"

            if not evidence_ids and confidence > 0.4:
                confidence = min(confidence, 0.4)

            claim_type = str(raw.get("claim_type") or "factual_summary").strip().lower()
            if claim_type not in valid_claim_types:
                claim_type = "factual_summary"

            key = f"{product_id}_{dimension}"
            idx = idx_by_product_dim.get(key, 0)
            claim_id = f"claim_{product_id}_{dimension}_{idx}"
            idx_by_product_dim[key] = idx + 1

            claim: dict[str, Any] = {
                "claim_id": claim_id,
                "run_id": run_id,
                "product_id": product_id,
                "dimension": dimension,
                "claim_text": claim_text,
                "fact_ids": fact_ids,
                "evidence_ids": evidence_ids,
                "confidence": round(confidence, 3),
                "risk_level": risk_level,
                "claim_type": claim_type,
                "review_status": "pending",
            }

            claims.append(claim)

        return claims

    def _ensure_product_coverage(
        self,
        claims: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        task_brief: dict[str, Any],
        run_id: str,
        project_id: str | None,
        valid_products: set[str],
    ) -> dict[str, Any]:
        """
        Check that every product with usable evidence received at least one claim.

        LLM non-determinism means the main analyze() pass can silently skip
        a product even when high-quality evidence exists. This method detects
        those gaps and issues a targeted supplemental LLM call to fill them.

        Returns:
            {
                "gaps": list of product_ids with no claims despite usable evidence,
                "supplemental_claims": list of newly-generated claims,
            }
        """
        result: dict[str, Any] = {"gaps": [], "supplemental_claims": []}

        # Index usable evidence by product
        usable_by_product: dict[str, list[dict[str, Any]]] = {}
        for ev in evidence_items:
            if not ev.get("usable_for_claim"):
                continue
            pid = str(ev.get("product_id") or "").strip()
            if pid:
                usable_by_product.setdefault(pid, []).append(ev)

        # Determine which products already have at least one claim
        products_with_claims: set[str] = set()
        for claim in claims:
            pid = str(claim.get("product_id") or "").strip()
            if pid:
                products_with_claims.add(pid)

        # Find gaps: products that have usable evidence but zero claims
        gaps: list[str] = []
        for pid in usable_by_product:
            pid_lower = pid.lower()
            has_claim = (
                pid in products_with_claims
                or pid_lower in products_with_claims
                or any(pid in p or p in pid for p in products_with_claims)
            )
            if not has_claim:
                gaps.append(pid)

        if not gaps:
            return result

        result["gaps"] = gaps
        logger.warning(
            "AnalystAgent._ensure_product_coverage | run_id=%s | "
            "products with no claims despite usable evidence: %s",
            run_id, gaps,
        )

        supplemental_claims: list[dict[str, Any]] = []

        # Gather evidence for gap products
        gap_evidence: list[dict[str, Any]] = []
        for pid in gaps:
            gap_evidence.extend(usable_by_product.get(pid, []))

        if not gap_evidence:
            logger.info(
                "AnalystAgent._ensure_product_coverage | run_id=%s | "
                "gap products %s have no usable evidence — skipping supplemental call",
                run_id, gaps,
            )
            return result

        supplemental_system = (
            "You are an analyst specializing in AI agent product competitive analysis. "
            "Generate factual, evidence-backed claims for the specified products only. "
            "Each claim must cite specific evidence from the provided data. "
            "If evidence is insufficient for a claim, note that clearly. "
            "Output a JSON object with a 'claims' array."
        )

        evidence_summary_lines: list[str] = []
        for ev in gap_evidence:
            snippet = str(ev.get("snippet") or "")[:500]
            schema = ev.get("schema_key", "general")
            source = ev.get("source_type", "unknown")
            evidence_summary_lines.append(
                f"[{ev.get('product_id')}] [{schema}] [{source}] {snippet}"
            )
        evidence_text = "\n".join(evidence_summary_lines)

        supplemental_user = (
            f"Task: Generate factual claims for the following products that currently have no coverage.\n"
            f"Products requiring coverage: {', '.join(gaps)}\n\n"
            f"Available evidence (product | schema | source | snippet):\n"
            f"{evidence_text}\n\n"
            f"Requirements:\n"
            f"1. Generate at least ONE claim per product listed above\n"
            f"2. Each claim must reference specific evidence from above\n"
            f"3. Claims must be factual and precise\n"
            f"4. Output JSON: {{'claims': [{{'product_id': '...', 'dimension': '...', "
            f"'claim_text': '...', 'evidence_ids': ['...'], 'confidence': 0.7, 'risk_level': 'low'}}]}}\n"
            f"5. If evidence does not support a claim, state that explicitly\n"
            f"6. Do NOT invent information not present in the evidence"
        )

        messages = [
            {"role": "system", "content": supplemental_system},
            {"role": "user", "content": supplemental_user},
        ]

        def _supplemental_call():
            client = get_llm_client()
            return client.chat_json(messages, temperature=0.0, max_tokens=4096, timeout=60)

        try:
            sup_result = traced_llm_call(
                run_id=run_id,
                project_id=project_id,
                node_name="analyze_dimensions",
                agent_name="AnalystAgent",
                agent_role="analyst",
                prompt_version=ANALYST_PROMPT_VERSION,
                prompt_text=supplemental_user,
                input_payload={
                    "gap_products": gaps,
                    "evidence_count": len(gap_evidence),
                    "supplemental": True,
                },
                call_fn=_supplemental_call,
                parse_fn=lambda r: r if isinstance(r, dict) else {"raw": str(r)},
                input_length_hint=len(supplemental_user),
                decision_summary=f"Supplemental coverage for: {', '.join(gaps)}",
            )
            sup_response = sup_result.get("parsed_output") or {}
        except Exception as exc:
            logger.error(
                "AnalystAgent._ensure_product_coverage | run_id=%s | "
                "supplemental LLM call failed: %s",
                run_id, exc,
            )
            return result

        sup_claims = self._parse_and_enrich_claims(
            sup_response, evidence_items, run_id,
            valid_products=valid_products,
            valid_dimensions=None,
        )

        for claim in sup_claims:
            claim_pid = str(claim.get("product_id") or "").strip().lower()
            if any(
                g.lower() in claim_pid or claim_pid in g.lower() or g.lower() == claim_pid
                for g in gaps
            ):
                supplemental_claims.append(claim)

        result["supplemental_claims"] = supplemental_claims
        logger.info(
            "AnalystAgent._ensure_product_coverage | run_id=%s | "
            "supplemental claims generated: %d for gaps: %s",
            run_id, len(supplemental_claims), gaps,
        )
        return result
