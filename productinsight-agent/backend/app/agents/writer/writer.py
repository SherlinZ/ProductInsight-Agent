"""
Writer Agent - generates structured competitive analysis reports from signed claims.

Rules:
- Writer MUST NOT read raw webpages or generate new factual claims.
- Writer can ONLY write based on signed_claims provided by the analyst/reviewer pipeline.
- Writer can add organizational text (introductions, transitions, summaries) but NOT new evidence-backed facts.
- If signed_claims is empty, return a blocked report.

vNext-R2-C: Supports dynamic report_outline for domain-aware reports.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from backend.app.tracing.llm_trace import traced_llm_call, create_llm_fallback_trace

logger = logging.getLogger(__name__)

# Prompt version for writer LLM calls
WRITER_PROMPT_VERSION = "v1.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_report_id(run_id: str) -> str:
    return f"report_{run_id}"


def _generate_section_id(section_index: int, section_slug: str) -> str:
    clean = "".join(c if c.isalnum() else "_" for c in section_slug.lower())
    return f"section_{section_index:02d}_{clean}"


def _calculate_quality_summary(signed_claims: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(signed_claims)
    if total == 0:
        coverage_rate = 0.0
    else:
        with_evidence = sum(
            1 for c in signed_claims
            if c.get("evidence_ids") and len(c["evidence_ids"]) > 0
        )
        coverage_rate = with_evidence / total
    return {
        "claim_count": total,
        "evidence_coverage_rate": coverage_rate,
        "unsupported_claim_count": 0,
    }


def _empty_section_template(section_index: int, title: str) -> dict[str, Any]:
    return {
        "section_id": _generate_section_id(section_index, title),
        "section_title": title,
        "content_markdown": "",
        "claim_ids": [],
        "evidence_ids": [],
        "unsupported": False,
    }


def _template_sections_from_claims(
    signed_claims: list[dict[str, Any]], run_id: str
) -> list[dict[str, Any]]:
    """Assemble readable report sections from signed_claims without LLM.

    Generates structured paragraphs, comparison tables, and analysis text
    from claim metadata — NOT just bullet lists.
    """
    if not signed_claims:
        return []

    # --- Strip "Evidence for X:" prefix so report paragraphs are readable ---
    def _ctxt(claim: dict) -> str:
        raw = claim.get("claim_text", "")
        prefix_match = re.match(r"^Evidence\s+for\s+\w+:\s*", raw, re.IGNORECASE)
        if prefix_match:
            raw = raw[prefix_match.end():]
        return raw.strip()

    # --- Group claims by dimension and product ---
    by_dim: dict[str, list[dict]] = {}
    by_product: dict[str, dict[str, list[dict]]] = {}
    all_products: list[str] = []

    for c in signed_claims:
        dim = c.get("dimension", "function_tree")
        pid = c.get("product_id", "unknown")

        by_dim.setdefault(dim, []).append(c)
        by_product.setdefault(pid, {}).setdefault(dim, []).append(c)
        if pid not in all_products:
            all_products.append(pid)

    def _product_feature_list(pid: str) -> list[str]:
        return [_ctxt(c) for c in by_product.get(pid, {}).get("function_tree", []) if _ctxt(c)]

    def _render_intro() -> str:
        if len(all_products) == 1:
            return (
                f"This report provides a competitive analysis of {all_products[0]} "
                f"based on {len(signed_claims)} verified claims across "
                f"{len(by_dim)} dimensions."
            )
        products = ", ".join(all_products[:-1]) + f" and {all_products[-1]}"
        return (
            f"This competitive analysis examines {len(all_products)} products: "
            f"{products}. "
            f"The analysis covers {len(by_dim)} dimensions including features, pricing, "
            f"user personas, and enterprise readiness."
        )

    def _render_feature_comparison() -> str:
        lines = [f"## Feature Comparison\n"]
        if len(all_products) > 1:
            lines.append("| Feature | " + " | ".join(all_products) + " |")
            lines.append("|" + "|".join(["---"] * (len(all_products) + 1)) + "|")
            # Collect feature keywords from function_tree claims
            feat_map: dict[str, list[str]] = {}
            for pid in all_products:
                for c in by_product.get(pid, {}).get("function_tree", []):
                    kw = c.get("schema_key", "")
                    text = _ctxt(c)
                    if kw and text:
                        feat_map.setdefault(kw, [])  # type: ignore
            for kw in sorted(feat_map.keys())[:8]:
                row = [kw.replace("_", " ").title()]
                for pid in all_products:
                    matched = next(
                        (_ctxt(c)[:60] for c in
                         by_product.get(pid, {}).get("function_tree", [])
                         if c.get("schema_key") == kw and _ctxt(c)),
                        "—"
                    )
                    row.append(matched)
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
        lines.append("### Key Capabilities by Product\n")
        for pid in all_products:
            feats = _product_feature_list(pid)
            if feats:
                lines.append(f"**{pid}**: " + "; ".join(feats[:5]) + ".")
        return "\n".join(lines)

    def _render_pricing() -> str:
        lines = ["## Pricing Analysis\n"]
        claims = by_dim.get("pricing_model", [])
        if claims:
            for c in claims:
                pid = c.get("product_id", "unknown")
                text = _ctxt(c)
                if text:
                    lines.append(f"- **{pid}**: {text}\n")
        else:
            lines.append("Pricing information is being collected from product websites and documentation.\n")
        return "\n".join(lines)

    def _render_user_persona() -> str:
        lines = ["## User Persona\n"]
        claims = by_dim.get("user_persona", [])
        if claims:
            for c in claims:
                pid = c.get("product_id", "unknown")
                text = _ctxt(c)
                if text:
                    lines.append(f"- **{pid}** targets users who: {text}\n")
        else:
            lines.append("User persona data is being analyzed from product positioning and community feedback.\n")
        return "\n".join(lines)

    def _render_swot() -> str:
        lines = ["## SWOT Analysis\n"]
        swot_claims = by_dim.get("swot", [])
        if swot_claims:
            for c in swot_claims:
                text = _ctxt(c)
                if text:
                    pid = c.get("product_id", "unknown")
                    lines.append(f"- {pid}: {text}\n")
        else:
            for pid in all_products:
                strengths: list[str] = []
                for c in by_product.get(pid, {}).get("function_tree", [])[:3]:
                    t = _ctxt(c)
                    if t:
                        strengths.append(t)
                if strengths:
                    lines.append(f"**{pid} Strengths**: " + "; ".join(strengths) + ".\n")
        return "\n".join(lines)

    def _render_enterprise() -> str:
        lines = ["## Enterprise Readiness\n"]
        claims = by_dim.get("enterprise_readiness", [])
        if claims:
            for c in claims:
                pid = c.get("product_id", "unknown")
                text = _ctxt(c)
                if text:
                    lines.append(f"- **{pid}**: {text}\n")
        else:
            for pid in all_products:
                feats = _product_feature_list(pid)
                if feats:
                    lines.append(
                        f"- **{pid}**: Enterprise features include {feats[0] if feats else 'standard deployment options'}.\n"
                    )
        return "\n".join(lines)

    def _render_customer_voice() -> str:
        lines = ["## Customer Voice\n"]
        claims = by_dim.get("customer_voice", [])
        if claims:
            for c in claims:
                pid = c.get("product_id", "unknown")
                text = _ctxt(c)
                if text:
                    lines.append(f"- **{pid}** users report: {text}\n")
        else:
            lines.append("Customer feedback data is being collected from community discussions and reviews.\n")
        return "\n".join(lines)


    def _render_key_findings() -> str:
        lines = ["## Key Findings\n"]
        # Prioritize high-confidence, evidence-backed claims across dimensions.
        ranked = sorted(
            signed_claims,
            key=lambda c: (
                len(c.get("evidence_ids") or []),
                float(c.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        if not ranked:
            lines.append("No signed findings are available yet.\n")
            return "\n".join(lines)

        for idx, claim in enumerate(ranked[:6], start=1):
            pid = claim.get("product_id", "unknown")
            dim = str(claim.get("dimension", "general")).replace("_", " ")
            text = _ctxt(claim)
            ev_count = len(claim.get("evidence_ids") or [])
            if text:
                lines.append(
                    f"{idx}. **{pid}** ({dim}): {text} "
                    f"[supported by {ev_count} evidence item{'s' if ev_count != 1 else ''}]."
                )
        return "\n".join(lines)

    def _render_product_overview() -> str:
        lines = ["## Product Overview\n"]
        for pid in all_products:
            overview_parts: list[str] = []
            for dim in ["function_tree", "pricing_model"]:
                for c in by_product.get(pid, {}).get(dim, [])[:2]:
                    t = _ctxt(c)
                    if t:
                        overview_parts.append(t)
            if overview_parts:
                lines.append(f"**{pid}** " + " ".join(overview_parts) + "\n")
            else:
                lines.append(f"**{pid}** is one of the products included in this analysis.\n")
        return "\n".join(lines)

    # --- Section renderer map ---
    SECTION_RENDERERS: dict[str, callable] = {
        "Executive Summary": lambda: f"## Executive Summary\n\n{_render_intro()}\n",
        "Product Overview": _render_product_overview,
        "Feature Comparison": _render_feature_comparison,
        "Pricing Analysis": _render_pricing,
        "User Persona": _render_user_persona,
        "Customer Voice": _render_customer_voice,
        "SWOT Analysis": _render_swot,
        "Enterprise Readiness": _render_enterprise,
        "Key Findings": _render_key_findings,
    }

    SECTION_ORDER = [
        ("Executive Summary", "executive_summary"),
        ("Product Overview", "product_overview"),
        ("Feature Comparison", "feature_comparison"),
        ("Pricing Analysis", "pricing_analysis"),
        ("User Persona", "user_persona"),
        ("Customer Voice", "customer_voice"),
        ("SWOT Analysis", "swot_analysis"),
        ("Enterprise Readiness", "enterprise_readiness"),
        ("Key Findings", "key_findings"),
    ]
    DIMENSION_MAP = {
        "function_tree": "Feature Comparison",
        "pricing_model": "Pricing Analysis",
        "user_persona": "User Persona",
        "customer_voice": "Customer Voice",
        "swot": "SWOT Analysis",
        "enterprise_readiness": "Enterprise Readiness",
    }

    # Group claims by section
    by_section: dict[str, list[dict]] = {title: [] for title, _ in SECTION_ORDER}
    for claim in signed_claims:
        dim = claim.get("dimension", "function_tree")
        section_title = DIMENSION_MAP.get(dim, "Feature Comparison")
        by_section[section_title].append(claim)

    sections: list[dict[str, Any]] = []
    for idx, (title, slug) in enumerate(SECTION_ORDER):
        section_claims = by_section.get(title, [])

        # Executive Summary and Key Findings always include all claims
        if not section_claims:
            if title in ("Executive Summary", "Key Findings"):
                section_claims = signed_claims[:]
            else:
                continue

        claim_ids = [c.get("claim_id", "") for c in section_claims]
        evidence_ids: list[str] = []
        for c in section_claims:
            evidence_ids.extend(c.get("evidence_ids") or [])

        renderer = SECTION_RENDERERS.get(title, lambda: f"## {title}\n\nNo data.\n")
        content = renderer()

        sections.append({
            "section_id": _generate_section_id(idx + 1, slug),
            "section_title": title,
            "content_markdown": content,
            "claim_ids": claim_ids,
            "evidence_ids": evidence_ids,
            "unsupported": False,
        })

    return sections


def _build_system_message(schema_type: str | None = None, is_blocked: bool = False) -> str:
    """
    Build system message for WriterAgent.
    
    vNext-R2-C: Dynamic domain context based on schema_type.
    P0-3: If is_blocked=True, enforce pre-assessment language only.
    """
    domain_context = ""
    if schema_type == "pricing_analysis":
        domain_context = "重点关注定价详情、价值主张、TCO、AI功能成本和竞争性定价分析。"
    elif schema_type == "knowledge_management":
        domain_context = "重点关注知识结构、协作、权限治理和企业集成。"
    elif schema_type == "ai_coding_assistant":
        domain_context = "重点关注AI编码能力、IDE集成、代码生成和企业安全。"
    
    msg = (
        f"你是一位专业的竞品分析报告撰写专家。{domain_context}\n\n"
        "你唯一的事实信息来源是分析师和审核智能体为你提供的已签署声明。"
        "你不得生成新的事实声明，也不得引入未包含在已签署声明中的新证据。\n\n"
        "你必须遵守的规则：\n"
        "1. 只根据已提供的已签署声明撰写。不要编造、推断或超出已签署声明明确陈述的事实信息。\n"
        "2. 你可以添加组织性文字（如引言、过渡语、总结、标题和结构框架），但不得引入新的有证据支撑的事实。\n"
        "3. 所有产品事实（功能、定价、能力、工作流等）必须追溯到已签署的声明。\n"
        "4. 明确将声明归属于其所属的产品和维度。\n"
        "5. 如实呈现对比数据：只包含已签署声明支撑的内容。\n"
        "6. 本报告必须全程使用中文撰写。所有输出内容必须为中文。\n"
        "   允许的英文：专有名词（产品名，品牌名，技术栈名，API名）。\n"
        "   禁止的英文：描述性句子（如'Publicly available free tier'）、形容词（如'unified'、'production-grade'）。\n"
    )

    if is_blocked:
        msg += (
            "\n关键约束（阻塞状态报告）：\n"
            "当前报告处于预评估/阻塞状态，因为证据不足，无法支撑正式采购结论。你必须遵守以下附加规则：\n"
            "1. 不要输出任何强烈正面推荐，如：\n"
            "   - 'top pick'、'optimal choice'、'most mature'、'best suited for'\n"
            "   - 'recommended as the primary option'、'strongly recommended'\n"
            "   - 任何基于表情符号的排名：第1名（🥇）、第2名（🥈）、第3名（🥉）\n"
            "   - 'most suitable for [角色/团队]'、'clearly the winner'\n"
            "2. 只使用预评估/谨慎措辞：\n"
            "   - '待核验'、'需补充证据'、'暂无法判断'、'建议 POC 验证后决策'\n"
            "   - '该产品在此维度有初步线索，需进一步核实'\n"
            "   - '证据不足以支撑明确推荐'\n"
            "3. 产品排名/评分：零覆盖率产品标注为'⚠️ 无签署声明，需补证后重新评估'\n"
            "4. 明确描述证据缺口：哪些维度缺失，需要什么类型的来源。\n"
        )

    msg += (
        "\n你的响应必须是符合以下结构的有效JSON对象。"
        "不要在JSON对象之外包含任何文本。不要将JSON包裹在Markdown代码块中。\n\n"
        '{"sections": [{"section_id": "section_01_executive_summary", "section_title": "执行摘要", '
        '"content_markdown": "...", "claim_ids": ["claim_001"], "evidence_ids": ["ev_001"], "unsupported": false}]}'
    )
    return msg


def _build_user_message(
    signed_claims: list[dict[str, Any]],
    run_id: str,
    report_outline: dict[str, Any] | None = None,
    evidence_map: dict[str, dict[str, Any]] | None = None,
) -> str:
    """
    Build user message for WriterAgent.
    
    vNext-R2-C: If report_outline is provided, use its sections instead of fixed 9 sections.
    P1-1: Enhanced with evidence snippets and stricter requirements.
    """
    parts: list[str] = []
    parts.append(f"运行ID：{run_id}")
    parts.append("")
    parts.append("== 已签署声明 ==")
    parts.append(
        "以下声明已由审核智能体验证并签署。只使用这些声明作为报告的事实依据。"
    )
    parts.append("")

    if not signed_claims:
        parts.append("（无可用的已签署声明）")
    else:
        for idx, claim in enumerate(signed_claims, start=1):
            claim_id = claim.get("claim_id", f"unknown_claim_{idx}")
            product_id = claim.get("product_id", "unknown_product")
            dimension = claim.get("dimension", "general")
            statement = re.sub(r"^Evidence\s+for\s+\w+:\s*", "", claim.get("claim_text", ""), flags=re.IGNORECASE).strip()
            confidence = claim.get("confidence", None)
            risk_level = claim.get("risk_level", "")
            claim_type = claim.get("claim_type", "")
            evidence_ids = claim.get("evidence_ids", []) or []
            lines = [
                f"声明 [{idx}] (ID: {claim_id})",
                f"  产品：{product_id}",
                f"  维度：{dimension}",
                f"  声明类型：{claim_type}",
                f"  风险级别：{risk_level}",
                f"  陈述：{statement}",
            ]
            if confidence is not None:
                lines.append(f"  Confidence: {confidence}")
            lines.append(f"  Evidence IDs: {evidence_ids if evidence_ids else 'none'}")
            
            # P1-1: Include evidence snippets for context
            if evidence_ids and evidence_map:
                snippet_lines = []
                for ev_id in evidence_ids[:3]:  # Include up to 3 evidence snippets
                    ev = evidence_map.get(ev_id, {})
                    snippet = ev.get("snippet", "")
                    if snippet:
                        # Truncate long snippets
                        snippet_text = snippet[:200] + "..." if len(snippet) > 200 else snippet
                        snippet_lines.append(f"    [{ev_id}]: {snippet_text}")
                if snippet_lines:
                    lines.append("  支撑证据：")
                    lines.extend(snippet_lines)
            
            parts.append("\n".join(lines))
            parts.append("")

    parts.append("== 报告要求 ==")
    
    # P1-1: Add strict content requirements
    parts.append("关键写作要求：")
    parts.append("1. 每个章节必须有实质性内容（主要章节至少300+中文字符）")
    parts.append("2. 提供分析，不仅仅是描述 — 解释产品为何不同")
    parts.append("3. 在每个维度上包含产品之间的对比")
    parts.append("4. 为每个发现提供商业影响分析")
    parts.append("5. 使用正确的Markdown格式，包括标题和项目符号")
    parts.append("6. 使用[E:id]格式引用证据，例如[E:ev_123]")
    parts.append("")
    
    # vNext-R2-C: Dynamic sections based on report_outline
    if report_outline and report_outline.get("sections"):
        sections_list = []
        for idx, section in enumerate(report_outline["sections"], start=1):
            title = section.get("title", section.get("section_title", f"Section {idx}"))
            purpose = section.get("purpose", "")
            min_words = section.get("min_words", section.get("min_word_count", 300))
            sections_list.append(f"{idx}. {title} - {purpose} (min: {min_words} chars)")
        parts.append(
            f"生成包含以下{len(sections_list)}个章节的竞品分析报告。"
            "仅基于上述已签署声明为每个章节撰写实质性内容。"
        )
    else:
        # Fallback to fixed 9 sections (old behavior)
        sections_list = [
            "1. 执行摘要 - 所有产品的关键发现高层概述。（最少500字符）",
            "2. 产品概览 - 每个产品的名称、公司、网站。（最少400字符）",
            "3. 功能对比 - 对比工作流、工具使用、内存、多模态、定价。（最少600字符）",
            "4. 定价分析 - 定价详情、免费套餐、试用周期、企业计划。（最少500字符）",
            "5. 用户画像 - 目标用户画像。（最少400字符）",
            "6. 用户之声 - 用户反馈和证言。（最少400字符）",
            "7. SWOT分析 - 每个产品的SWOT。（最少600字符）",
            "8. 企业就绪度 - 安全、合规、SLA、支持、部署。（最少500字符）",
            "9. 关键发现 - 最重要的收获和战略洞察。（最少400字符）",
        ]
        parts.append(
            "生成包含以下9个章节的竞品分析报告。"
            "仅基于上述已签署声明为每个章节撰写实质性内容。"
        )
    
    for s in sections_list:
        parts.append(s)
    parts.append("")

    parts.append("每个章节输出一个JSON对象，包含：")
    parts.append("- section_id：kebab-case，以章节编号开头（如 section_01_executive_summary）")
    parts.append("- section_title：完整章节标题")
    parts.append("- content_markdown：Markdown格式的撰写内容，使用标题和项目符号")
    parts.append("- claim_ids：本章节使用的所有已签署声明ID列表")
    parts.append("- evidence_ids：本章节引用的所有证据ID列表")
    parts.append("- unsupported：始终为false")
    parts.append("")
    parts.append(
        "仅返回一个包含sections数组的有效JSON对象。"
        "不要包含解释性文字，不要使用Markdown代码块。"
    )

    return "\n".join(parts)


def _parse_llm_json_response(text: str) -> dict[str, Any]:
    import json
    import re

    stripped = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(stripped)
    except Exception:
        pass

    # Strategy 2: extract from ```json ... ``` blocks
    for pattern in [
        r"```(?:json)?\s*(\{[\s\S]*?\})\s*```",
        r"```(?:json)?\s*(\[[\s\S]*?\])\s*```",
    ]:
        match = re.search(pattern, stripped, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            try:
                result = json.loads(candidate)
                if isinstance(result, dict) and "sections" in result:
                    return result
            except Exception:
                pass

    # Strategy 3: extract first { ... } JSON object
    open_pos = stripped.find("{")
    if open_pos != -1:
        candidate = stripped[open_pos:]
        try:
            result = json.loads(candidate)
            if isinstance(result, dict) and "sections" in result:
                return result
        except Exception:
            pass

    # Strategy 4: extract first [ ... ] and wrap
    open_pos = stripped.find("[")
    if open_pos != -1:
        candidate = stripped[open_pos:]
        try:
            result = json.loads(candidate)
            if isinstance(result, list):
                return {"sections": result}
        except Exception:
            pass

    raise ValueError(f"Could not parse sections JSON from LLM output. Preview: {stripped[:300]}")


def _enrich_sections_with_defaults(
    raw_sections: list[dict[str, Any]], run_id: str,
    report_outline: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    # vNext-R2-C: If report_outline.sections exists, use its titles instead of defaults
    if report_outline and report_outline.get("sections"):
        expected_titles = []
        for section in report_outline["sections"]:
            title = section.get("title") or section.get("section_title") or ""
            expected_titles.append(title)
    else:
        expected_titles = [
            "Executive Summary",
            "Product Overview",
            "Feature Comparison",
            "Pricing Analysis",
            "User Persona",
            "Customer Voice",
            "SWOT Analysis",
            "Enterprise Readiness",
            "Key Findings",
        ]

    enriched: list[dict[str, Any]] = []
    seen_titles: dict[str, int] = {}

    for section in raw_sections:
        title = section.get("section_title", "")
        normalized = title.lower().strip()
        slug_key = normalized.replace(" ", "_")
        seen_titles[slug_key] = seen_titles.get(slug_key, -1) + 1
        index = seen_titles[slug_key]

        enriched_section: dict[str, Any] = {
            "section_id": section.get(
                "section_id",
                _generate_section_id(index, title or f"section_{index}"),
            ),
            "section_title": title or f"Section {index + 1}",
            "content_markdown": section.get("content_markdown", ""),
            "claim_ids": section.get("claim_ids", []),
            "evidence_ids": section.get("evidence_ids", []),
            "unsupported": bool(section.get("unsupported", False)),
        }
        enriched.append(enriched_section)

    # vNext-R2-C: When report_outline is provided, ensure all its sections exist
    has_report_outline = report_outline and report_outline.get("sections")
    if has_report_outline:
        # Add missing sections from report_outline
        outline_titles_in_enriched = {s["section_title"].lower() for s in enriched}
        for idx, section_def in enumerate(report_outline["sections"]):
            title = section_def.get("title") or section_def.get("section_title") or f"Section {idx + 1}"
            if title.lower() not in outline_titles_in_enriched:
                # vNext-R2-D Patch: preserve outline.section_id when filling missing sections
                section_id = section_def.get("section_id") or _generate_section_id(idx, title)
                empty = _empty_section_template(idx + 1, title)
                empty["section_id"] = section_id
                enriched.append(empty)
    else:
        # Ensure all default sections exist
        for idx, expected_title in enumerate(expected_titles):
            slug = expected_title.lower().replace(" ", "_")
            if slug not in seen_titles:
                enriched.append(_empty_section_template(idx, expected_title))

    return enriched


def _assemble_final_report(
    raw_sections: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]],
    run_id: str,
    report_outline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections = _enrich_sections_with_defaults(raw_sections, run_id, report_outline)
    now = _utc_now()

    claims_with_evidence = sum(
        1 for c in signed_claims
        if c.get("evidence_ids") and len(c["evidence_ids"]) > 0
    )
    total_claims = len(signed_claims)
    coverage_rate = claims_with_evidence / total_claims if total_claims > 0 else 0.0

    return {
        "report_id": _generate_report_id(run_id),
        "run_id": run_id,
        "sections": sections,
        "quality_summary": {
            "claim_count": total_claims,
            "evidence_coverage_rate": coverage_rate,
            "unsupported_claim_count": 0,
        },
        "report_status": "draft",
        "created_at": now,
        "updated_at": now,
    }


class WriterAgent:
    """Agent responsible for writing the final competitive analysis report.

    The Writer is purely a rendering agent: it takes signed (verified) claims
    from the analyst/reviewer pipeline and transforms them into a structured
    report. It MUST NOT read raw webpages or generate new factual claims.
    
    vNext-R2-C: Supports dynamic task_brief and report_outline for domain-aware reports.
    """

    def __init__(self) -> None:
        pass

    def write(
        self,
        signed_claims: list[dict[str, Any]],
        run_id: str,
        project_id: str | None = None,
        task_brief: dict[str, Any] | None = None,
        report_outline: dict[str, Any] | None = None,
        evidence_map: dict[str, dict[str, Any]] | None = None,
        is_blocked: bool = False,
    ) -> dict[str, Any]:
        """
        Write the competitive analysis report.
        
        vNext-R2-C: Added task_brief and report_outline parameters.
        P1-1: Added evidence_map parameter for enhanced evidence context.
        P0-3: Added is_blocked parameter to enforce pre-assessment language.
        
        Args:
            signed_claims: List of verified/signed claims to use as factual basis
            run_id: Run identifier
            project_id: Optional project identifier for tracing
            task_brief: Optional task brief dict containing schema_type
            report_outline: Optional report outline with sections list
            evidence_map: Optional dict of evidence_id -> evidence item for context
            is_blocked: If True, enforce pre-assessment / caution language only
        """
        now = _utc_now()
        logger.info(
            "WriterAgent.write started for run_id=%s with %d signed_claims, is_blocked=%s",
            run_id, len(signed_claims), is_blocked
        )

        # Guard: no signed claims means we cannot write a factual report
        if not signed_claims:
            logger.warning(
                "WriterAgent.write: no signed_claims for run_id=%s - returning blocked report",
                run_id
            )
            return {
                "report_id": _generate_report_id(run_id),
                "run_id": run_id,
                "sections": [],
                "quality_summary": {
                    "claim_count": 0,
                    "evidence_coverage_rate": 0.0,
                    "unsupported_claim_count": 0,
                },
                "report_status": "blocked",
                "reason": "no_signed_claims",
                "created_at": now,
                "updated_at": now,
            }

        # vNext-R2-C: Extract schema_type from task_brief
        schema_type = None
        if task_brief:
            schema_type = task_brief.get("task_type") or task_brief.get("schema_type")
        
        # vNext-R2-C: Count sections from report_outline
        section_count = len(report_outline.get("sections", [])) if report_outline else 9

        # Build prompt with dynamic sections and enhanced evidence context
        # P0-3: Pass is_blocked to enforce pre-assessment language
        system_msg = _build_system_message(schema_type, is_blocked=is_blocked)
        user_msg = _build_user_message(signed_claims, run_id, report_outline, evidence_map)
        
        # P0-3: If blocked, append extra downgrade instruction to user message
        if is_blocked:
            user_msg += (
                "\n\nIMPORTANT CONTEXT:\n"
                "This report is in PRE-ASSESSMENT state due to insufficient evidence. "
                "Do NOT generate strong positive recommendations (top pick, optimal, best, 1st/2nd/3rd place). "
                "Use cautious language: 待核验, 需补充证据, 暂无法判断, 建议 POC 验证后决策. "
                "For any product with no signed evidence, mark as: ⚠️ 无签署声明，需补证后重新评估."
            )
        
        input_payload = {
            "signed_claims_count": len(signed_claims),
            "sections_requested": section_count,
            "schema_type": schema_type,
            "report_section_count": section_count,
            "signed_claims_included": [c.get("claim_id", "") for c in signed_claims[:10]],  # first 10 for tracing
        }

        def _do_llm_call():
            from backend.app.services.llm_client import get_llm_client
            client = get_llm_client()
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ]
            return client.chat_text(messages, temperature=0.1, max_tokens=8192, timeout=120)

        def _parse_response(response: Any) -> dict[str, Any]:
            text = str(response)
            parsed = _parse_llm_json_response(text)
            return {"sections": parsed.get("sections", [])}

        try:
            # Use traced_llm_call
            result = traced_llm_call(
                run_id=run_id,
                project_id=project_id,
                node_name="write_report",
                agent_name="WriterAgent",
                agent_role="writer",
                prompt_version=WRITER_PROMPT_VERSION,
                prompt_text=user_msg,
                input_payload=input_payload,
                call_fn=_do_llm_call,
                parse_fn=_parse_response,
                input_length_hint=len(user_msg),
                decision_summary=f"Generated report with {len(signed_claims)} signed claims",
            )
            
            parsed = result.get("parsed_output", {})
            sections_raw: list[dict[str, Any]] = parsed.get("sections", [])
            if not isinstance(sections_raw, list):
                sections_raw = []

            report = _assemble_final_report(sections_raw, signed_claims, run_id, report_outline)
            logger.info(
                "WriterAgent.write: draft generated for run_id=%s with %d sections, status=%s",
                run_id, len(report["sections"]), report["report_status"],
            )
            return report

        except Exception as exc:
            logger.warning(
                "WriterAgent.write: LLM call failed for run_id=%s (%s) - using template fallback",
                run_id, type(exc).__name__,
            )
            
            # Record fallback trace
            create_llm_fallback_trace(
                run_id=run_id,
                project_id=project_id,
                node_name="write_report",
                agent_name="WriterAgent",
                agent_role="writer",
                prompt_version=WRITER_PROMPT_VERSION,
                prompt_text=user_msg,
                input_payload=input_payload,
                reason=f"LLM_UNAVAILABLE_OR_ERROR: {type(exc).__name__}: {exc}",
                decision_summary="Fallback: template-based report generation",
            )
            
            # Template fallback: assemble report directly from signed_claims
            sections = _template_sections_from_claims(signed_claims, run_id)
            quality = _calculate_quality_summary(signed_claims)
            return {
                "report_id": _generate_report_id(run_id),
                "run_id": run_id,
                "sections": sections,
                "quality_summary": quality,
                "report_status": "draft",
                "reason": f"template_fallback: {type(exc).__name__}: {str(exc)[:80]}",
                "created_at": now,
                "updated_at": now,
            }
