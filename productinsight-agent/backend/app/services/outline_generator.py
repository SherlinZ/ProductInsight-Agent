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
    
    # Build the prompt
    if is_chinese:
        system_prompt = """你是一个专业的竞品分析报告结构规划专家。你的任务是根据给定的竞品和分析维度，生成一个层次分明、重点突出的报告大纲。

要求：
1. 大纲应包含主要章节(大章节)，每个大章节下可以有子章节
2. 每个章节都要有明确的目的说明
3. 根据分析的竞品和维度，适当调整章节重点
4. 章节数量适中（8-12个主要章节为佳）
5. 每个章节设置合理的最低字数要求

输出格式为JSON：
{
    "report_title": "报告标题",
    "sections": [
        {
            "section_id": "unique_id",
            "type": "chapter",  // 或 "subsection"
            "title": "章节标题",
            "min_words": 最低字数,
            "requires_human_review": true/false,
            "purpose": "章节目的说明",
            "slug": "slug-for-url"
        },
        ...
    ]
}"""

        user_prompt = f"""请为以下竞品分析任务生成报告大纲：

竞品列表：{competitor_str}

分析维度：{dimension_str}

请生成一个完整的报告大纲，包含：
1. 封面与执行摘要
2. 市场概览
3. 各竞品深度分析
4. 功能对比
5. 定价分析
6. 企业能力对比
7. SWOT分析
8. 结论与建议

请确保大纲结构合理，章节之间有逻辑衔接。"""
    else:
        system_prompt = """You are a professional competitive analysis report structure planner. Your task is to generate a well-structured report outline based on the given competitors and analysis dimensions.

Requirements:
1. Outline should include main chapters, each main chapter can have subsections
2. Each chapter must have a clear purpose description
3. Adjust chapter emphasis based on competitors and dimensions
4. Appropriate number of chapters (8-12 main chapters is ideal)
5. Set reasonable minimum word count requirements for each chapter

Output format as JSON:
{
    "report_title": "Report Title",
    "sections": [
        {
            "section_id": "unique_id",
            "type": "chapter",  // or "subsection"
            "title": "Chapter Title",
            "min_words": minimum words,
            "requires_human_review": true/false,
            "purpose": "Chapter purpose description",
            "slug": "slug-for-url"
        },
        ...
    ]
}"""

        user_prompt = f"""Please generate a report outline for this competitive analysis task:

Competitors: {competitor_str}

Analysis Dimensions: {dimension_str}

Please generate a complete report outline including:
1. Cover & Executive Summary
2. Market Overview
3. In-depth Competitor Profiles
4. Feature Comparison
5. Pricing Analysis
6. Enterprise Capabilities
7. SWOT Analysis
8. Conclusion & Recommendations

Ensure the outline structure is logical with proper transitions between chapters."""

    # Try to use LLM
    try:
        llm_client = get_llm_client()
        if llm_client:
            response = traced_llm_call(
                llm_client=llm_client,
                prompt_version="outline_v1",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                max_tokens=4000,
            )
            
            if response:
                import json
                # Try to parse the LLM response as JSON
                try:
                    # Try direct JSON parse first
                    outline = json.loads(response)
                    if isinstance(outline, dict) and "sections" in outline:
                        return outline
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown code block
                    import re
                    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
                    if json_match:
                        try:
                            outline = json.loads(json_match.group(1))
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
