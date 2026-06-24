"""
Outline Generator Service (vNext-R3-B).

Generates hierarchical report outlines using LLM based on:
- Competitors
- Analysis dimensions
- Target language

The outline generation is a SEPARATE step from plan generation,
triggered after competitors and dimensions are finalized.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.app.services.llm_client import get_llm_client, LLMClient
from backend.app.tracing.llm_trace import traced_llm_call

logger = logging.getLogger(__name__)


# Default outline structure in Chinese
DEFAULT_OUTLINE_ZH = {
    "sections": [
        {
            "section_id": "cover",
            "type": "chapter",
            "title": "封面与执行摘要",
            "min_words": 500,
            "requires_human_review": True,
            "purpose": "提供报告概览和核心发现摘要",
            "slug": "cover",
        },
        {
            "section_id": "market_overview",
            "type": "chapter",
            "title": "市场概览",
            "min_words": 1000,
            "requires_human_review": False,
            "purpose": "介绍市场背景和竞争格局",
            "slug": "market_overview",
        },
        {
            "section_id": "competitor_profiles",
            "type": "chapter",
            "title": "竞品深度分析",
            "min_words": 3000,
            "requires_human_review": True,
            "purpose": "对各竞品进行全面分析",
            "slug": "competitor_profiles",
        },
        {
            "section_id": "feature_comparison",
            "type": "chapter",
            "title": "功能对比分析",
            "min_words": 2000,
            "requires_human_review": True,
            "purpose": "基于各维度进行横向功能对比",
            "slug": "feature_comparison",
        },
        {
            "section_id": "pricing_analysis",
            "type": "chapter",
            "title": "定价策略分析",
            "min_words": 1500,
            "requires_human_review": False,
            "purpose": "分析各竞品的定价模式和成本结构",
            "slug": "pricing_analysis",
        },
        {
            "section_id": "enterprise_capability",
            "type": "chapter",
            "title": "企业级能力对比",
            "min_words": 1500,
            "requires_human_review": False,
            "purpose": "评估企业就绪度、安全性和合规性",
            "slug": "enterprise_capability",
        },
        {
            "section_id": "swot_analysis",
            "type": "chapter",
            "title": "SWOT 综合分析",
            "min_words": 1500,
            "requires_human_review": True,
            "purpose": "对各竞品进行 SWOT 分析",
            "slug": "swot_analysis",
        },
        {
            "section_id": "conclusion",
            "type": "chapter",
            "title": "结论与建议",
            "min_words": 1000,
            "requires_human_review": True,
            "purpose": "总结分析结果并给出建议",
            "slug": "conclusion",
        },
    ]
}

# Default outline structure in English
DEFAULT_OUTLINE_EN = {
    "sections": [
        {
            "section_id": "cover",
            "type": "chapter",
            "title": "Cover & Executive Summary",
            "min_words": 500,
            "requires_human_review": True,
            "purpose": "Provide report overview and key findings summary",
            "slug": "cover",
        },
        {
            "section_id": "market_overview",
            "type": "chapter",
            "title": "Market Overview",
            "min_words": 1000,
            "requires_human_review": False,
            "purpose": "Introduce market background and competitive landscape",
            "slug": "market_overview",
        },
        {
            "section_id": "competitor_profiles",
            "type": "chapter",
            "title": "In-depth Competitor Profiles",
            "min_words": 3000,
            "requires_human_review": True,
            "purpose": "Comprehensive analysis of each competitor",
            "slug": "competitor_profiles",
        },
        {
            "section_id": "feature_comparison",
            "type": "chapter",
            "title": "Feature Comparison Analysis",
            "min_words": 2000,
            "requires_human_review": True,
            "purpose": "Cross-dimensional feature comparison",
            "slug": "feature_comparison",
        },
        {
            "section_id": "pricing_analysis",
            "type": "chapter",
            "title": "Pricing Strategy Analysis",
            "min_words": 1500,
            "requires_human_review": False,
            "purpose": "Analyze pricing models and cost structures",
            "slug": "pricing_analysis",
        },
        {
            "section_id": "enterprise_capability",
            "type": "chapter",
            "title": "Enterprise Capabilities Comparison",
            "min_words": 1500,
            "requires_human_review": False,
            "purpose": "Evaluate enterprise readiness, security and compliance",
            "slug": "enterprise_capability",
        },
        {
            "section_id": "swot_analysis",
            "type": "chapter",
            "title": "SWOT Analysis",
            "min_words": 1500,
            "requires_human_review": True,
            "purpose": "SWOT analysis for each competitor",
            "slug": "swot_analysis",
        },
        {
            "section_id": "conclusion",
            "type": "chapter",
            "title": "Conclusion & Recommendations",
            "min_words": 1000,
            "requires_human_review": True,
            "purpose": "Summarize findings and provide recommendations",
            "slug": "conclusion",
        },
    ]
}


def generate_report_outline(
    competitors: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    language: str = "zh",
) -> dict[str, Any]:
    """
    Generate a hierarchical report outline using LLM.
    
    This is a dedicated outline generation step, separate from plan generation.
    
    Args:
        competitors: List of competitor specs
        dimensions: List of analysis dimensions
        language: Output language ('zh' or 'en')
    
    Returns:
        Dict containing report_title and sections
    """
    is_chinese = language == "zh"
    
    # Extract competitor names
    competitor_names = [c.get("name", "Unknown") for c in competitors if isinstance(c, dict)]
    competitor_str = ", ".join(competitor_names) if competitor_names else "N/A"
    
    # Extract dimension names
    if is_chinese:
        dimension_names = [d.get("name", d.get("name_en", "Unknown")) for d in dimensions if isinstance(d, dict)]
    else:
        dimension_names = [d.get("name_en", d.get("name", "Unknown")) for d in dimensions if isinstance(d, dict)]
    dimension_str = ", ".join(dimension_names) if dimension_names else "Core Capabilities"
    
    # Build full competitor/dimension context for the prompt
    def _build_comp_context(comps: list[dict], is_zh: bool) -> str:
        if not comps:
            return "N/A"
        lines = []
        for c in comps:
            name = c.get("name", "Unknown")
            company = c.get("company_name", "")
            url = c.get("official_url", "")
            priority = c.get("priority", "")
            lines.append(f"  - {name}" + (f" ({company})" if company else "") +
                          (f" | {url}" if url else "") + (f" | priority: {priority}" if priority else ""))
        return "\n".join(lines)

    comp_context = _build_comp_context(competitors, is_chinese)

    # Build full dimension context
    def _build_dim_context(dims: list[dict], is_zh: bool) -> str:
        if not dims:
            return "Core Capabilities"
        lines = []
        for d in dims:
            name = d.get("name", d.get("name_en", "Unknown"))
            desc = d.get("description", d.get("name_en", ""))
            required = d.get("required", False)
            req_str = " (必选)" if (is_zh and required) else (" (required)" if required else "")
            lines.append(f"  - {name}{req_str}: {desc[:60]}..." if len(desc) > 60 else f"  - {name}{req_str}: {desc}")
        return "\n".join(lines)

    # ── Few-shot example: good outline for a 3-competitor AI coding analysis ──
    # Used in both Chinese and English prompts to guide LLM toward comparative structure
    FEWSHOT_ZH = """【优秀大纲示例 — AI 编程助手竞品分析，供参考】

报告标题：{competitor_str} 竞品对比分析报告

大纲结构：
1. 封面与执行摘要 — 覆盖报告目的、核心发现（3-5条）、关键结论
2. 市场与行业背景 — AI 编程助手赛道概况、市场规模、发展趋势
3. 竞品横向对比（概览）— 用对比表格呈现所有竞品的核心差异（定位/技术/定价/用户）
4. 核心能力深度对比 — 逐一对比各竞品在关键维度上的具体能力差异
5. 技术架构与实现路径 — 对比各竞品的技术路线差异（API/IDE/插件/Agent）
6. 定价与商业模式分析 — 对比各竞品的定价策略、免费策略、商业化路径
7. 用户体验与工作流集成 — 对比各竞品的 IDE 集成、交互体验、生态丰富度
8. 企业级能力对比 — 安全合规、私有部署、企业认证等能力对比
9. SWOT 横向对比分析 — 表格形式呈现各竞品的 Strengths/Weaknesses/Opportunities/Threats
10. 差异化竞争优势总结 — 提炼每个竞品的核心差异化卖点
11. 市场格局与趋势研判 — 竞争态势分析、市场演进预测
12. 结论与战略建议 — 综合评估、各方适用场景、进入策略建议"""

    FEWSHOT_EN = """【Good Outline Example — AI Coding Assistant Competitive Analysis, for reference】

Report Title: {competitor_str} Competitive Analysis Report

Outline Structure:
1. Cover & Executive Summary — Report purpose, 3-5 key findings, critical conclusions
2. Market & Industry Background — AI coding market overview, scale, growth trends
3. Competitor Cross-comparison Overview — Comparison table showing all competitors' core differences (positioning/tech/pricing/users)
4. Core Capabilities Deep Comparison — Point-by-point comparison across key dimensions
5. Technical Architecture & Implementation Paths — Compare tech approaches (API/IDE/plugin/Agent)
6. Pricing & Business Model Analysis — Compare pricing strategies, free tiers, monetization
7. User Experience & Workflow Integration — Compare IDE integration, UX, ecosystem richness
8. Enterprise Capabilities Comparison — Security, compliance, private deployment, enterprise certs
9. SWOT Cross-comparison — Table format showing each competitor's Strengths/Weaknesses/Opportunities/Threats
10. Differentiated Competitive Advantages Summary — Core differentiators for each competitor
11. Market Landscape & Trend Analysis — Competitive dynamics, market evolution predictions
12. Conclusion & Strategic Recommendations — Comprehensive assessment, use-case fit, entry strategies"""

    dim_context = _build_dim_context(dimensions, is_chinese)
    comp_context_filled = comp_context  # already built above

    # Build the prompt
    if is_chinese:
        system_prompt = """你是一个专业的竞品分析报告结构规划专家。你的任务是根据给定的竞品和分析维度，生成一个层次分明、重点突出的报告大纲。

【强制要求】所有输出内容（章节标题、purpose说明、report_title）必须使用中文，不得使用英文或混合语言。

【大纲结构核心原则】
高质量竞品对比报告的大纲必须遵循以下结构：

1. **先横后纵**：先给读者一个全局横向对比，再深入每个维度。绝对不能把每个竞品各写一章，那只是产品列表，不是竞品对比分析。
2. **对比优先于罗列**：每个正文章节都必须同时覆盖全部竞品，不允许只分析一个竞品。
3. **必须有量化对比**：功能对比、定价对比必须以表格/矩阵形式呈现，一目了然。
4. **结论要有差异化**：每个竞品都要有明确的差异化定位说明，不能含糊。
5. **章节数量控制在 10-15 个**，合理分配权重（对比类章节篇幅多一些，背景介绍类少一些）。

输出格式为JSON（所有字段值均为中文）：
{
    "report_title": "报告标题（中文）",
    "sections": [
        {
            "section_id": "unique_id",
            "type": "chapter",
            "title": "章节标题（中文）",
            "min_words": 最低字数,
            "requires_human_review": true/false,
            "purpose": "章节目的说明（中文）",
            "slug": "slug-for-url"
        }
    ]
}"""

        user_prompt = f"""请为以下竞品分析任务生成中文报告大纲：

## 竞品列表
{comp_context_filled}

## 分析维度
{dim_context}

## 参考：优秀大纲示例
{FEWSHOT_ZH.format(competitor_str=competitor_str)}

## 强制约束
请严格按照以下约束生成大纲，禁止违反：
- ❌ 禁止：将每个竞品写成独立的一章（这不是竞品分析，这是产品手册）
- ❌ 禁止：章节中只分析单个竞品而不同时对比所有竞品
- ❌ 禁止：章节标题中出现"Codex产品分析""Cursor功能介绍"这类只针对单一竞品的标题
- ✅ 必须：每个正文章节同时覆盖全部竞品
- ✅ 必须：至少有一个专门的"竞品横向对比"章节（对比表格/矩阵）
- ✅ 必须：至少有一个专门的"差异化分析"或"差异化竞争优势总结"章节
- ✅ 必须：用表格/矩阵形式呈现对比数据
- ✅ 必须：结论章节要给出各竞品的差异化定位，不能只说"各有优势"这种废话

请生成10-15个章节，结构合理、逻辑清晰。"""

    else:
        system_prompt = """You are a professional competitive analysis report structure planner. Your task is to generate a well-structured report outline based on the given competitors and analysis dimensions.

【Mandatory】All output content (chapter titles, purpose descriptions, report_title) must be in English only. No Chinese or mixed language.

【Core Structural Principles for High-Quality Competitive Reports】
1. **Horizontal before vertical**: Give readers a global cross-comparison first, then dive into specific dimensions. Never write a separate chapter for each competitor — that is a product catalog, not a competitive analysis.
2. **Comparison over listing**: Every substantive chapter must cover ALL competitors simultaneously.
3. **Quantitative comparison required**: Feature and pricing comparisons must use tables/matrices.
4. **Conclusions must differentiate**: Each competitor must have a clear differentiated positioning — no vague "各有优势" equivalent.
5. **Target 10-15 chapters**, with proper weight distribution (comparison chapters get more space, background chapters less).

Output format as JSON (all field values in English):
{
    "report_title": "Report Title",
    "sections": [
        {
            "section_id": "unique_id",
            "type": "chapter",
            "title": "Chapter Title",
            "min_words": minimum words,
            "requires_human_review": true/false,
            "purpose": "Chapter purpose description",
            "slug": "slug-for-url"
        }
    ]
}"""

        user_prompt = f"""Please generate a report outline for this competitive analysis task:

## Competitors
{comp_context_filled}

## Analysis Dimensions
{dim_context}

## Reference: Good Outline Example
{FEWSHOT_EN.format(competitor_str=competitor_str)}

## Mandatory Constraints
Strictly follow these constraints — violations are NOT allowed:
- ❌ FORBIDDEN: Write a separate chapter for each individual competitor (that is a product catalog, not competitive analysis)
- ❌ FORBIDDEN: A chapter that analyzes only one competitor without comparing to all others
- ❌ FORBIDDEN: Chapter titles like "Codex Product Analysis" or "Cursor Feature Overview" that target a single competitor only
- ✅ REQUIRED: Every substantive chapter must cover ALL competitors simultaneously
- ✅ REQUIRED: At least one dedicated "Cross-Competitor Comparison" chapter (with comparison table/matrix)
- ✅ REQUIRED: At least one dedicated "Differentiated Advantages" or "Differentiation Strategy" chapter
- ✅ REQUIRED: Use tables/matrices to present comparison data
- ✅ REQUIRED: Conclusion chapter must give each competitor a specific differentiated positioning

Generate 10-15 chapters with logical structure and clear progression."""

    # Try to use LLM
    try:
        llm_client = get_llm_client()
        if llm_client:
            def _call_fn():
                return llm_client.chat_text(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=4000,
                )

            def _parse_fn(text: str) -> dict:
                import json
                import re
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
                    if match:
                        return json.loads(match.group(1))
                    return {}

            response = traced_llm_call(
                run_id="outline_gen",
                node_name="outline_generation",
                agent_name="OutlineGenerator",
                agent_role="outline_generator",
                prompt_version="outline_v2",
                prompt_text=f"System: {system_prompt}\n\nUser: {user_prompt}",
                input_payload={
                    "competitors": competitor_names,
                    "dimensions": dimension_str,
                    "language": language,
                },
                call_fn=_call_fn,
                parse_fn=_parse_fn,
                input_length_hint=len(system_prompt) + len(user_prompt),
                decision_summary="Generated report outline via LLM",
            )

            if response:
                import json
                parsed = response.get("parsed_output", {})
                if isinstance(parsed, dict) and "sections" in parsed:
                    return parsed
                # Fallback: try raw output_text
                output_text = response.get("output_text", "")
                if output_text:
                    try:
                        outline = json.loads(output_text)
                        if isinstance(outline, dict) and "sections" in outline:
                            return outline
                    except json.JSONDecodeError:
                        import re
                        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', output_text, re.DOTALL)
                        if match:
                            try:
                                outline = json.loads(match.group(1))
                                if isinstance(outline, dict) and "sections" in outline:
                                    return outline
                            except json.JSONDecodeError:
                                pass

                logger.warning("LLM outline response could not be parsed, using default")
    except Exception as exc:
        logger.warning(f"LLM outline generation failed: {exc}")

    # Fallback to default outline
    if is_chinese:
        return {
            "report_title": f"{competitor_str} 竞品分析报告" if competitor_names else "竞品分析报告",
            "sections": DEFAULT_OUTLINE_ZH["sections"],
        }
    else:
        return {
            "report_title": f"{competitor_str} Competitive Analysis Report" if competitor_names else "Competitive Analysis Report",
            "sections": DEFAULT_OUTLINE_EN["sections"],
        }
