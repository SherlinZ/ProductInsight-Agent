"""
ResearchPlanner Service (vNext-R1).

Generates structured ResearchPlan from natural language user queries.
Uses LLM when available, falls back to deterministic templates.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from backend.app.schemas.research_plan import (
    ResearchPlan,
    TaskBrief,
    CompetitorSpec,
    AnalysisDimension,
    SourcePlan,
    SourceDiscovery,
    ReportOutline,
    ReportSection,
    ExecutionDAG,
    DAGNode,
    DAGEdge,
    HumanCheckpoint,
    SuccessMetrics,
    validate_research_plan,
    generate_id,
    utc_now,
)
from backend.app.services.llm_client import LLMClient, LLMError, get_llm_client
from backend.app.tracing.llm_trace import traced_llm_call, create_llm_fallback_trace

logger = logging.getLogger(__name__)

# Prompt version for research plan LLM calls
PLANNER_PROMPT_VERSION = "v2.0"


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Detect the primary language of input text.
    
    Returns: 'zh' for Chinese, 'en' for English, or 'mixed' if unclear.
    """
    if not text:
        return "zh"  # Default to Chinese
    
    # Count Chinese characters vs English words
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    
    if chinese_chars > english_words * 0.5:
        return "zh"
    elif english_words > chinese_chars * 2:
        return "en"
    else:
        return "mixed"


def get_language_config(language: str) -> dict[str, Any]:
    """Get language-specific configuration for prompts and output.
    
    Args:
        language: 'zh', 'en', or 'mixed'
    
    Returns:
        Dict with language-specific strings for prompts
    """
    configs = {
        "zh": {
            "prompt_language": "中文",
            "output_language": "中文",
            "report_language": "Chinese",
            "schema_name": "中文名称",
            "description_label": "描述",
            "example_competitors": "Dify（中文AI应用开发平台）、钉钉（企业协作平台）",
            "default_report_title": "竞品分析报告",
            "default_project_name": "竞品调研项目",
        },
        "en": {
            "prompt_language": "English",
            "output_language": "English",
            "report_language": "English",
            "schema_name": "English name",
            "description_label": "Description",
            "example_competitors": "Notion (all-in-one workspace), Confluence (enterprise wiki)",
            "default_report_title": "Competitive Analysis Report",
            "default_project_name": "Competitive Research Project",
        },
        "mixed": {
            "prompt_language": "English with Chinese context",
            "output_language": "English",
            "report_language": "English",
            "schema_name": "Name (English preferred)",
            "description_label": "Description",
            "example_competitors": "Notion, Confluence, Dify",
            "default_report_title": "Competitive Analysis Report",
            "default_project_name": "Competitive Research Project",
        },
    }
    return configs.get(language, configs["zh"])


# ---------------------------------------------------------------------------
# Default Analysis Dimensions
# ---------------------------------------------------------------------------

DEFAULT_DIMENSIONS: list[dict[str, Any]] = [
    {
        "dimension_id": "function_tree",
        "name": "Function Tree / Core Capabilities",
        "description": "Compare core platform capabilities: workflow builder, RAG, tool calling, multi-agent orchestration, observability, etc.",
        "required": True,
        "sub_dimensions": ["workflow_builder", "rag", "tool_calling", "multi_agent", "observability"],
        "expected_outputs": ["facts", "claims", "comparison_matrix"],
    },
    {
        "dimension_id": "pricing_model",
        "name": "Pricing Model",
        "description": "Analyze pricing structure, tiers, free tier availability, enterprise pricing, and cost efficiency.",
        "required": True,
        "sub_dimensions": ["free_tier", "paid_tiers", "enterprise_pricing", "cost_efficiency"],
        "expected_outputs": ["facts", "claims", "comparison_matrix"],
    },
    {
        "dimension_id": "enterprise_readiness",
        "name": "Enterprise Readiness",
        "description": "Evaluate private deployment, permission control, auditability, security, integration, and support.",
        "required": True,
        "sub_dimensions": ["private_deployment", "permission_control", "auditability", "security", "integration", "support"],
        "expected_outputs": ["facts", "claims", "comparison_matrix"],
    },
    {
        "dimension_id": "user_persona",
        "name": "User Persona",
        "description": "Identify target users, use cases, learning curve, and community size.",
        "required": False,
        "sub_dimensions": ["target_users", "use_cases", "learning_curve", "community_size"],
        "expected_outputs": ["facts", "claims"],
    },
    {
        "dimension_id": "customer_voice",
        "name": "Customer Voice",
        "description": "Collect and analyze user reviews, testimonials, case studies, and G2/Capterra ratings.",
        "required": False,
        "sub_dimensions": ["reviews", "testimonials", "case_studies", "ratings"],
        "expected_outputs": ["facts", "claims"],
    },
    {
        "dimension_id": "market_positioning",
        "name": "Market Positioning",
        "description": "Understand brand positioning, differentiation, and competitive moat.",
        "required": False,
        "sub_dimensions": ["brand_positioning", "differentiation", "competitive_moat"],
        "expected_outputs": ["claims"],
    },
]

# ---------------------------------------------------------------------------
# Schema Type Definitions
# ---------------------------------------------------------------------------

SCHEMA_TYPE_DIMENSIONS: dict[str, list[dict[str, Any]]] = {
    "ai_agent_platform": DEFAULT_DIMENSIONS,
    "competitor_landscape": DEFAULT_DIMENSIONS,
    "product_comparison": [
        d for d in DEFAULT_DIMENSIONS if d["required"]
    ],
    "pricing_analysis": [
        {
            "dimension_id": "pricing_model",
            "name": "Pricing Model & Cost Structure",
            "description": "Analyze pricing structure, tiers, free tier, per-user cost, annual vs monthly billing, and volume discounts.",
            "required": True,
            "sub_dimensions": ["free_tier", "paid_tiers", "enterprise_pricing", "per_user_cost", "volume_discount"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "value_proposition",
            "name": "Value Proposition & ROI",
            "description": "Analyze value for money, ROI, TCO, and total cost of ownership across team sizes.",
            "required": True,
            "sub_dimensions": ["roi", "total_cost", "tco_by_size", "value_comparison"],
            "expected_outputs": ["facts", "claims"],
        },
        {
            "dimension_id": "ai_feature_pricing",
            "name": "AI Feature Add-on Pricing",
            "description": "Compare AI assistant, Copilot, and premium feature pricing beyond base plans.",
            "required": True,
            "sub_dimensions": ["ai_addon_cost", "copilot_pricing", "premium_features"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "admin_security_cost",
            "name": "Admin, Security & Compliance Cost",
            "description": "Analyze admin console, SSO/SCIM, audit logs, compliance features, and premium support costs.",
            "required": True,
            "sub_dimensions": ["sso_scim_cost", "audit_compliance", "premium_support", "security_addon"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "migration_adoption",
            "name": "Migration, Training & Adoption Cost",
            "description": "Assess data migration complexity, staff training, and team adoption curve costs.",
            "required": False,
            "sub_dimensions": ["migration_effort", "training_cost", "adoption_curve"],
            "expected_outputs": ["facts", "claims"],
        },
        {
            "dimension_id": "competitive_positioning",
            "name": "Competitive Pricing Positioning",
            "description": "Compare pricing relative to competitors and market positioning.",
            "required": False,
            "sub_dimensions": ["price_competitiveness", "market_positioning", "pricing_strategy"],
            "expected_outputs": ["claims"],
        },
    ],
    "sales_battlecard": [
        {
            "dimension_id": "strengths",
            "name": "Strengths",
            "description": "Key strengths and advantages.",
            "required": True,
            "sub_dimensions": [],
            "expected_outputs": ["claims"],
        },
        {
            "dimension_id": "weaknesses",
            "name": "Weaknesses",
            "description": "Key weaknesses and vulnerabilities.",
            "required": True,
            "sub_dimensions": [],
            "expected_outputs": ["claims"],
        },
        {
            "dimension_id": "competitive_differentiators",
            "name": "Competitive Differentiators",
            "description": "Unique selling points vs competitors.",
            "required": True,
            "sub_dimensions": [],
            "expected_outputs": ["claims"],
        },
    ],
    # vNext-R1.6: Domain-aware schema for knowledge management / collaborative docs
    "knowledge_management": [
        {
            "dimension_id": "knowledge_structure",
            "name": "Knowledge Structure & Information Architecture",
            "description": "Compare knowledge base organization: spaces/pages hierarchy, search/retrieval, content accumulation patterns, taxonomy, and tagging.",
            "required": True,
            "sub_dimensions": ["space_hierarchy", "page_structure", "search_retrieval", "taxonomy_tagging", "content_accumulation"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "collaboration_experience",
            "name": "Collaboration Experience & Workflow",
            "description": "Evaluate multi-user editing, comments, task coordination, async collaboration, real-time co-editing, and content production workflows.",
            "required": True,
            "sub_dimensions": ["multi_user_editing", "comments_annotations", "task_coordination", "async_collaboration", "content_production"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "permission_governance",
            "name": "Permission Governance & Enterprise Control",
            "description": "Assess permission granularity, admin controls, audit trails, space management, compliance, and data governance capabilities.",
            "required": True,
            "sub_dimensions": ["permission_granularity", "admin_controls", "audit_trails", "space_management", "compliance"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "ai_assistance",
            "name": "AI Assistance & Knowledge Discovery",
            "description": "Compare AI writing, search, summarization, Q&A, and knowledge discovery features.",
            "required": True,
            "sub_dimensions": ["ai_writing", "ai_search", "ai_summarization", "ai_qa", "knowledge_discovery"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "enterprise_integration",
            "name": "Enterprise Integration",
            "description": "Evaluate SSO, SCIM, Slack, Google Drive, Jira, Confluence import, API, webhooks, and third-party ecosystem.",
            "required": True,
            "sub_dimensions": ["sso_scim", "slack", "google_drive", "jira", "confluence_import", "api_webhooks"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "template_ecosystem",
            "name": "Template Ecosystem & Adoption",
            "description": "Compare template libraries, best practice templates, team onboarding cost, and adoption acceleration.",
            "required": False,
            "sub_dimensions": ["template_library", "best_practices", "team_onboarding", "adoption_cost"],
            "expected_outputs": ["facts", "claims"],
        },
        {
            "dimension_id": "migration_cost",
            "name": "Migration Cost & Operational Burden",
            "description": "Assess data import/export, migration from other doc systems, learning curve, and operational overhead.",
            "required": False,
            "sub_dimensions": ["import_export", "migration_guide", "learning_curve", "operational_overhead"],
            "expected_outputs": ["facts", "claims"],
        },
        {
            "dimension_id": "pricing_model",
            "name": "Pricing Model & TCO",
            "description": "Analyze free tier, team plan, enterprise pricing, TCO, and cost efficiency.",
            "required": True,
            "sub_dimensions": ["free_tier", "team_plan", "enterprise_pricing", "tco", "cost_efficiency"],
            "expected_outputs": ["facts", "claims", "comparison_matrix"],
        },
        {
            "dimension_id": "team_fit",
            "name": "Team Fit & Use Case Suitability",
            "description": "Identify which products best fit engineering teams, IT, HR, cross-functional teams, and different company sizes.",
            "required": False,
            "sub_dimensions": ["engineering_fit", "it_fit", "hr_fit", "cross_functional", "company_size"],
            "expected_outputs": ["claims"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Source Types by Schema Type
# ---------------------------------------------------------------------------

SCHEMA_SOURCE_TYPES: dict[str, list[str]] = {
    "ai_agent_platform": [
        "official_website",
        "documentation",
        "github",
        "pricing_page",
        "community_feedback",
    ],
    "competitor_landscape": [
        "official_website",
        "documentation",
        "pricing_page",
        "community_feedback",
        "social_media",
    ],
    "product_comparison": [
        "official_website",
        "documentation",
        "pricing_page",
        "comparison_articles",
    ],
    "pricing_analysis": [
        "pricing_page",
        "documentation",
        "pricing_calculator",
        "comparison_articles",
        "customer_reviews",
        # vNext-R1.6: Enhanced source types for collaboration pricing
        "security_compliance_docs",
        "ai_feature_docs",
        "migration_training_docs",
        "tco_analysis_articles",
    ],
    "sales_battlecard": [
        "official_website",
        "documentation",
        "case_studies",
        "reviews",
        "competitive_analysis",
    ],
    # vNext-R1.6: Source types for knowledge management
    "knowledge_management": [
        "official_website",
        "documentation",
        "pricing_page",
        "g2_reviews",
        "capterra_reviews",
        "case_studies",
        "integration_docs",
        "migration_guides",
        "community_forums",
    ],
}


# ---------------------------------------------------------------------------
# Report Outline Templates
# ---------------------------------------------------------------------------

REPORT_OUTLINE_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "ai_agent_platform": [
        {
            "section_id": "executive_summary",
            "title": "Executive Summary",
            "purpose": "Summarize key findings, competitive positioning, and strategic implications.",
            "required_dimensions": ["function_tree", "pricing_model", "enterprise_readiness"],
            "min_words": 600,
            "requires_human_review": True,
        },
        {
            "section_id": "market_overview",
            "title": "Market Overview",
            "purpose": "Provide context on the AI agent platform market.",
            "required_dimensions": ["market_positioning"],
            "min_words": 400,
            "requires_human_review": False,
        },
        {
            "section_id": "feature_comparison",
            "title": "Feature Comparison",
            "purpose": "Compare core platform capabilities across competitors.",
            "required_dimensions": ["function_tree"],
            "min_words": 900,
            "requires_human_review": True,
        },
        {
            "section_id": "pricing_analysis",
            "title": "Pricing Analysis",
            "purpose": "Compare pricing models and cost efficiency.",
            "required_dimensions": ["pricing_model"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "enterprise_readiness",
            "title": "Enterprise Readiness Assessment",
            "purpose": "Evaluate enterprise deployment and governance capabilities.",
            "required_dimensions": ["enterprise_readiness"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "customer_voice_summary",
            "title": "Customer Voice Summary",
            "purpose": "Synthesize user feedback and market sentiment.",
            "required_dimensions": ["customer_voice", "user_persona"],
            "min_words": 500,
            "requires_human_review": False,
        },
        {
            "section_id": "risks_and_gaps",
            "title": "Risks & Evidence Gaps",
            "purpose": "Identify potential risks and areas lacking sufficient evidence.",
            "required_dimensions": [],
            "min_words": 300,
            "requires_human_review": True,
        },
    ],
    "competitor_landscape": [
        {
            "section_id": "executive_summary",
            "title": "Executive Summary",
            "purpose": "High-level overview of competitive landscape.",
            "required_dimensions": ["function_tree", "pricing_model"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "competitor_profiles",
            "title": "Competitor Profiles",
            "purpose": "Detailed profiles of each competitor.",
            "required_dimensions": ["function_tree", "pricing_model", "enterprise_readiness"],
            "min_words": 800,
            "requires_human_review": True,
        },
        {
            "section_id": "market_positioning",
            "title": "Market Positioning Analysis",
            "purpose": "Compare market positions and differentiation strategies.",
            "required_dimensions": ["market_positioning"],
            "min_words": 600,
            "requires_human_review": False,
        },
    ],
    "product_comparison": [
        {
            "section_id": "executive_summary",
            "title": "Executive Summary",
            "purpose": "Summarize comparison findings.",
            "required_dimensions": ["function_tree", "pricing_model", "enterprise_readiness"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "side_by_side",
            "title": "Side-by-Side Comparison",
            "purpose": "Direct comparison of key attributes.",
            "required_dimensions": ["function_tree", "pricing_model"],
            "min_words": 800,
            "requires_human_review": True,
        },
    ],
    "pricing_analysis": [
        {
            "section_id": "executive_summary",
            "title": "Executive Summary",
            "purpose": "Summarize pricing landscape, key insights, and procurement recommendations for different team sizes.",
            "required_dimensions": ["pricing_model", "value_proposition"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "pricing_scope",
            "title": "Pricing Scope and Procurement Questions",
            "purpose": "Define research scope, key procurement questions, and evaluation criteria for this pricing analysis.",
            "required_dimensions": [],
            "min_words": 350,
            "requires_human_review": False,
        },
        {
            "section_id": "product_overview",
            "title": "Product and Package Overview",
            "purpose": "Overview of each competitor's product positioning and available pricing packages.",
            "required_dimensions": ["pricing_model"],
            "min_words": 450,
            "requires_human_review": False,
        },
        {
            "section_id": "free_tier",
            "title": "Free Tier and Trial Limitations",
            "purpose": "Compare free tier capabilities, time limits, feature restrictions, and trial offers.",
            "required_dimensions": ["pricing_model"],
            "min_words": 400,
            "requires_human_review": True,
        },
        {
            "section_id": "plan_comparison",
            "title": "Team / Business / Enterprise Plan Comparison",
            "purpose": "Side-by-side comparison of Team, Business, and Enterprise plan features and pricing.",
            "required_dimensions": ["pricing_model"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "billing_model",
            "title": "Billing Unit and Seat-based Cost Model",
            "purpose": "Analyze per-user pricing, annual vs monthly billing, minimum seats, and volume discounts.",
            "required_dimensions": ["pricing_model", "value_proposition"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "ai_pricing",
            "title": "AI Feature Add-on and Premium Capability Pricing",
            "purpose": "Compare AI assistant, Copilot, and premium feature pricing across platforms.",
            "required_dimensions": ["pricing_model"],
            "min_words": 450,
            "requires_human_review": True,
        },
        {
            "section_id": "admin_security",
            "title": "Admin, Security, Compliance and Support Cost",
            "purpose": "Analyze admin console, SSO/SCIM, audit logs, compliance features, and premium support costs.",
            "required_dimensions": ["pricing_model"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "migration_cost",
            "title": "Migration, Training and Adoption Cost",
            "purpose": "Assess data migration complexity, staff training, and team adoption curve costs.",
            "required_dimensions": ["pricing_model"],
            "min_words": 400,
            "requires_human_review": False,
        },
        {
            "section_id": "tco_analysis",
            "title": "TCO by Team Size",
            "purpose": "Calculate Total Cost of Ownership for small, medium, and large organizations over 1-3 years.",
            "required_dimensions": ["pricing_model", "value_proposition"],
            "min_words": 600,
            "requires_human_review": True,
        },
        {
            "section_id": "procurement_recommendations",
            "title": "Procurement Recommendations",
            "purpose": "Provide scenario-based recommendations: best value, best features, best for large enterprises.",
            "required_dimensions": ["pricing_model", "value_proposition"],
            "min_words": 450,
            "requires_human_review": True,
        },
        {
            "section_id": "risks_assumptions",
            "title": "Risks, Assumptions and Evidence Gaps",
            "purpose": "Identify pricing risks, assumptions made, and areas lacking sufficient evidence.",
            "required_dimensions": [],
            "min_words": 300,
            "requires_human_review": True,
        },
    ],
    "sales_battlecard": [
        {
            "section_id": "overview",
            "title": "Competitive Overview",
            "purpose": "Quick reference competitive positioning.",
            "required_dimensions": ["strengths", "weaknesses"],
            "min_words": 300,
            "requires_human_review": True,
        },
        {
            "section_id": "strengths",
            "title": "Our Strengths vs Their Weaknesses",
            "purpose": "Leverage our advantages.",
            "required_dimensions": ["strengths", "competitive_differentiators"],
            "min_words": 400,
            "requires_human_review": True,
        },
        {
            "section_id": "handling_objections",
            "title": "Handling Objections",
            "purpose": "Address competitive FUD.",
            "required_dimensions": ["weaknesses"],
            "min_words": 400,
            "requires_human_review": True,
        },
    ],
    # vNext-R1.6: Domain-aware outline for knowledge management / collaborative docs
    "knowledge_management": [
        {
            "section_id": "executive_summary",
            "title": "Executive Summary",
            "purpose": "Summarize key findings, competitive positioning, and strategic recommendations for enterprise knowledge management.",
            "required_dimensions": ["knowledge_structure", "pricing_model", "team_fit"],
            "min_words": 600,
            "requires_human_review": True,
        },
        {
            "section_id": "analysis_scope",
            "title": "Analysis Scope and Selection Questions",
            "purpose": "Define research scope, selection criteria, and key selection questions this report answers.",
            "required_dimensions": [],
            "min_words": 400,
            "requires_human_review": False,
        },
        {
            "section_id": "competitor_positioning",
            "title": "Competitor Positioning Overview",
            "purpose": "High-level positioning of each competitor: target users, use cases, brand positioning, and differentiation.",
            "required_dimensions": ["team_fit"],
            "min_words": 600,
            "requires_human_review": True,
        },
        {
            "section_id": "knowledge_structure",
            "title": "Knowledge Structure and Information Architecture",
            "purpose": "Compare knowledge base organization, space/page hierarchy, search, taxonomy, and content accumulation patterns.",
            "required_dimensions": ["knowledge_structure"],
            "min_words": 800,
            "requires_human_review": True,
        },
        {
            "section_id": "collaboration_workflow",
            "title": "Collaboration Workflow and Content Production",
            "purpose": "Compare multi-user editing, comments, task coordination, and content production workflows.",
            "required_dimensions": ["collaboration_experience"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "permission_governance",
            "title": "Permission Governance and Enterprise Control",
            "purpose": "Compare permission granularity, admin controls, audit trails, and compliance capabilities.",
            "required_dimensions": ["permission_governance"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "ai_assistance",
            "title": "AI Assistance and Knowledge Discovery",
            "purpose": "Compare AI writing, search, summarization, Q&A, and knowledge discovery features.",
            "required_dimensions": ["ai_assistance"],
            "min_words": 600,
            "requires_human_review": True,
        },
        {
            "section_id": "integrations_migration",
            "title": "Integrations, Migration Cost, and Operational Burden",
            "purpose": "Assess third-party integrations, migration complexity, learning curve, and operational overhead.",
            "required_dimensions": ["enterprise_integration", "migration_cost"],
            "min_words": 600,
            "requires_human_review": False,
        },
        {
            "section_id": "template_adoption",
            "title": "Template Ecosystem and Adoption Cost",
            "purpose": "Compare template libraries, best practices, team onboarding, and adoption acceleration.",
            "required_dimensions": ["template_ecosystem"],
            "min_words": 500,
            "requires_human_review": False,
        },
        {
            "section_id": "pricing_tco",
            "title": "Pricing and TCO Analysis",
            "purpose": "Compare pricing tiers, free offerings, enterprise pricing, and total cost of ownership.",
            "required_dimensions": ["pricing_model"],
            "min_words": 700,
            "requires_human_review": True,
        },
        {
            "section_id": "team_recommendations",
            "title": "Scenario-based Recommendations by Team Size",
            "purpose": "Provide use-case-based recommendations for engineering teams, IT, HR, and cross-functional teams.",
            "required_dimensions": ["team_fit"],
            "min_words": 500,
            "requires_human_review": True,
        },
        {
            "section_id": "risks_gaps",
            "title": "Risks, Limitations, and Evidence Gaps",
            "purpose": "Identify potential risks, tool limitations, and areas lacking sufficient evidence.",
            "required_dimensions": [],
            "min_words": 300,
            "requires_human_review": True,
        },
    ],
}


# ---------------------------------------------------------------------------
# Generic Terms (not valid competitors)
# ---------------------------------------------------------------------------

GENERIC_TERMS: set[str] = {
    # Capability terms
    "ai", "agent", "agents", "ide", "coding", "code", "programming", "platform",
    "tool", "tools", "workflow", "enterprise", "security", "context", "team",
    "assistant", "integration", "pricing", "deployment", "cloud", "saas", "paas",
    "iaas", "serverless", "api", "sdk", "llm", "llms", "model", "models",
    "rag", "rag-based", "rag+", "multi-agent", "multiagent", "copilot", "copilots",
    # Business terms
    "business", "company", "startup", "market", "marketplace", "industry",
    "product", "products", "solution", "solutions", "service", "services",
    "software", "app", "apps", "application", "applications", "web", "web-based",
    # Feature terms
    "feature", "features", "capability", "capabilities", "function", "functions",
    "functionality", "ui", "ux", "interface", "dashboard", "analytics",
    # User terms
    "user", "users", "customer", "customers", "developer", "developers",
    "client", "clients", "persona", "personas", "audience",
    # Tech terms
    "backend", "frontend", "database", "db", "storage", "compute", "network",
    "infrastructure", "devops", "sre", "microservice", "microservices",
    # Misc
    "free", "paid", "premium", "basic", "pro", "enterprise", "starter",
    "growth", "scale", "business", "consumer", "b2b", "b2c", "saas",
    # vNext-R1.6: Acronyms (NOT competitors)
    "tco", "cfo", "cto", "cio", "ceo", "coo", "cmo", "cpo",
    "it", "hr", "bi", "crm", "erp", "ml", "nlp", "lLM",
    "sso", "scim", "sla", "sdk", "api",
    "roi", "nps", "kpi", "okr",
    "saas", "paas", "iaas", "saas", "sec", "dpo",
    "gdpr", "hipaa", "soc2", "iso27001",
    "vpn", "dns", "cdn", "ssl", "tls", "ssh", "http", "https",
    "dev", "ops", "qa", "ui", "ux", "api", "cli", "gui",
}

# ---------------------------------------------------------------------------
# Competitor Extraction Patterns
# ---------------------------------------------------------------------------

KNOWN_COMPETITORS: dict[str, dict[str, Any]] = {
    "Dify": {
        "company_name": "Dify",
        "official_url": "https://dify.ai",
        "seed_urls": ["https://dify.ai", "https://docs.dify.ai"],
        "known_aliases": ["Dify AI", "Dify.AI"],
    },
    "Coze": {
        "company_name": "ByteDance",
        "official_url": "https://www.coze.cn",
        "seed_urls": [
            "https://www.coze.cn",
            "https://www.coze.cn/docs",
            "https://www.coze.cn/docs/guides/workflow",
            "https://www.coze.cn/docs/guides/bot",
        ],
        "known_aliases": ["Coze AI", "ByteDance Coze"],
    },
    "Flowise": {
        "company_name": "Flowise",
        "official_url": "https://flowiseai.com",
        "seed_urls": ["https://flowiseai.com", "https://github.com/FlowiseAI/Flowise"],
        "known_aliases": ["Flowise AI"],
    },
    "LangGraph": {
        "company_name": "LangChain",
        "official_url": "https://langchain.com",
        "seed_urls": ["https://langchain.com", "https://github.com/langchain-ai/langgraph"],
        "known_aliases": ["LangGraph AI", "LangChain LangGraph"],
    },
    "LangChain": {
        "company_name": "LangChain",
        "official_url": "https://langchain.com",
        "seed_urls": ["https://langchain.com", "https://python.langchain.com"],
        "known_aliases": ["LangChain AI"],
    },
    "AutoGen": {
        "company_name": "Microsoft",
        "official_url": "https://microsoft.github.io/autogen",
        "seed_urls": ["https://github.com/microsoft/autogen"],
        "known_aliases": ["Microsoft AutoGen", "AutoGen Studio"],
    },
    "CrewAI": {
        "company_name": "CrewAI",
        "official_url": "https://crewai.com",
        "seed_urls": ["https://crewai.com", "https://github.com/crewAI/crewAI"],
        "known_aliases": ["Crew AI"],
    },
    "OpenAI Agents": {
        "company_name": "OpenAI",
        "official_url": "https://openai.com",
        "seed_urls": ["https://openai.com/index/agents"],
        "known_aliases": ["OpenAI Agent SDK"],
    },
    "Cursor": {
        "company_name": "Cursor",
        "official_url": "https://cursor.com",
        "seed_urls": ["https://cursor.com", "https://github.com/getcursor/cursor"],
        "known_aliases": ["Cursor AI"],
    },
    "ClaudeCode": {
        "company_name": "Anthropic",
        "official_url": "https://docs.anthropic.com/en/docs/claude-code",
        "seed_urls": ["https://docs.anthropic.com/en/docs/claude-code"],
        "known_aliases": ["Claude Code"],
    },
    "Codex": {
        "company_name": "OpenAI",
        "official_url": "https://platform.openai.com/docs/models#codex",
        "seed_urls": ["https://platform.openai.com/docs/models#codex"],
        "known_aliases": ["OpenAI Codex"],
    },
    "Windsurf": {
        "company_name": "Codeium",
        "official_url": "https://codeium.com/windsurf",
        "seed_urls": ["https://codeium.com/windsurf"],
        "known_aliases": ["Windsurf AI", "Codeium Windsurf"],
    },
    "Trae": {
        "company_name": "ByteDance",
        "official_url": "https://trae.ai",
        "seed_urls": ["https://trae.ai"],
        "known_aliases": ["Trae AI", "ByteDance Trae"],
    },
    "CloudCode": {
        "company_name": "Google Cloud",
        "official_url": "https://cloud.google.com/code?hl=en",
        "seed_urls": ["https://cloud.google.com/code?hl=en"],
        "known_aliases": ["Google Cloud Code", "Google CloudCode"],
    },
    "GitHub Copilot": {
        "company_name": "Microsoft/GitHub",
        "official_url": "https://github.com/features/copilot",
        "seed_urls": ["https://github.com/features/copilot"],
        "known_aliases": ["Copilot", "GitHub Copilot"],
    },
    # vNext-R1.6: Knowledge Management competitors
    "Notion": {
        "company_name": "Notion Labs",
        "official_url": "https://notion.so",
        "seed_urls": ["https://notion.so", "https://www.notion.so/help"],
        "known_aliases": ["Notion AI"],
    },
    "Confluence": {
        "company_name": "Atlassian",
        "official_url": "https://www.atlassian.com/software/confluence",
        "seed_urls": ["https://www.atlassian.com/software/confluence", "https://confluence.atlassian.com"],
        "known_aliases": ["Atlassian Confluence", "Confluence Cloud", "Confluence Server"],
    },
    "Coda": {
        "company_name": "Coda",
        "official_url": "https://coda.io",
        "seed_urls": ["https://coda.io", "https://coda.io/tour"],
        "known_aliases": ["Coda.io"],
    },
    "Slite": {
        "company_name": "Slite",
        "official_url": "https://slite.com",
        "seed_urls": ["https://slite.com", "https://help.slite.com"],
        "known_aliases": ["Slite app"],
    },
    "Airtable": {
        "company_name": "Airtable",
        "official_url": "https://airtable.com",
        "seed_urls": ["https://airtable.com", "https://airtable.com/workspace"],
        "known_aliases": ["Airtable base"],
    },
    "ClickUp Docs": {
        "company_name": "ClickUp",
        "official_url": "https://clickup.com/docs",
        "seed_urls": ["https://clickup.com/docs"],
        "known_aliases": ["ClickUp", "ClickUp Docs"],
    },
    # vNext-R1.6: Collaboration & Communication tools
    "Slack": {
        "company_name": "Salesforce",
        "official_url": "https://slack.com",
        "seed_urls": ["https://slack.com/pricing", "https://slack.com/enterprise"],
        "known_aliases": ["Slack Technologies", "Salesforce Slack"],
    },
    "Microsoft Teams": {
        "company_name": "Microsoft",
        "official_url": "https://www.microsoft.com/en-us/microsoft-teams/group-chat-software",
        "seed_urls": [
            "https://www.microsoft.com/en-us/microsoft-teams/pricing",
            "https://learn.microsoft.com/en-us/microsoftteams/e1-ex-compare-plans",
        ],
        "known_aliases": ["Teams", "MS Teams", "MS Teams"],
    },
    "Zoom": {
        "company_name": "Zoom Video Communications",
        "official_url": "https://www.zoom.com",
        "seed_urls": [
            "https://zoom.us/pricing",
            "https://zoom.us/enterprise",
            "https://explore.zoom.us/docs/english/home.html",
        ],
        "known_aliases": ["Zoom Video Communications", "Zoom Video"],
    },
    "Google Meet": {
        "company_name": "Google",
        "official_url": "https://meet.google.com",
        "seed_urls": [
            "https://workspace.google.com/products/meet/",
            "https://workspace.google.com/pricing",
        ],
        "known_aliases": ["Google Workspace Meet", "Meet", "G Meet"],
    },
}


def _is_generic_term(name: str) -> bool:
    """Check if a name is a generic/capability term, not a valid product name."""
    name_lower = name.lower()
    # Direct match
    if name_lower in GENERIC_TERMS:
        return True
    # Skip names starting with common verbs/patterns
    skip_prefixes = ["compare ", "vs ", "v ", "and ", "or ", "analyze ", "analysis "]
    for prefix in skip_prefixes:
        if name_lower.startswith(prefix):
            return True
    # Check if name ends with generic suffix (but NOT if it's a known compound like CloudCode)
    generic_suffixes = [
        " ai", " platform", " tool", " tools", " agent", " agents",
        " studio", " hub", " suite", " system", " solution",
    ]
    for suffix in generic_suffixes:
        if name_lower.endswith(suffix):
            return True
    # "cloud" as suffix: only generic when it's the word "cloud" + suffix
    # "cloudcode" (CloudCode) is NOT generic — only flag standalone " cloud"
    if name_lower.endswith(" cloud") or name_lower == "cloud":
        return True
    return False


# ---------------------------------------------------------------------------
# Schema-specific Discovery Query Generator (vNext-R1.6)
# ---------------------------------------------------------------------------

def _generate_discovery_queries(name: str, schema_type: str) -> list[str]:
    """
    Generate schema-specific search queries for source discovery.
    
    vNext-R1.6.1: This is now a thin wrapper. The authoritative implementation
    lives in SourceDiscovery._generate_queries() in research_plan.py.
    """
    from backend.app.schemas.research_plan import SourceDiscovery
    return SourceDiscovery._generate_queries(name, schema_type)


# ---------------------------------------------------------------------------
# Research Questions Generator (vNext-R1.6)
# ---------------------------------------------------------------------------

def _generate_research_questions(schema_type: str, competitors: list[dict]) -> list[str]:
    """Generate research questions based on schema_type and competitors."""
    comp_names = [c.get("name", "") for c in competitors if c.get("name")]
    comp_str = ", ".join(comp_names[:4]) if comp_names else "the selected products"

    if schema_type == "knowledge_management":
        return [
            f"Which product best supports structured knowledge accumulation for mid-to-large teams?",
            f"How do {comp_str} differ in permission governance and content organization?",
            f"Which tools provide the strongest AI-assisted knowledge discovery and search?",
            f"What are the migration and adoption costs for enterprise teams moving from Confluence or Notion?",
            f"Which product fits engineering teams, IT departments, and cross-functional teams best?",
            f"How do these products compare on enterprise integrations (SSO, SCIM, Slack, Google Drive)?",
        ]
    elif schema_type == "ai_coding_assistant":
        return [
            f"How does {comp_str} compare in AI code generation accuracy and context understanding?",
            f"Which IDEs and editors are supported by each coding assistant?",
            f"What are the pricing models and enterprise licensing options?",
            f"How do these tools handle privacy, security, and code leakage concerns?",
            f"Which tool best supports team collaboration and shared coding standards?",
        ]
    elif schema_type == "ai_agent_platform":
        return [
            f"How does {comp_str} compare in building and deploying AI agents?",
            f"Which platforms offer the best RAG (Retrieval-Augmented Generation) capabilities?",
            f"What are the integration options for enterprise systems and data sources?",
            f"How do these platforms compare on observability and monitoring?",
            f"Which platform offers the best developer experience and learning curve?",
        ]
    elif schema_type == "pricing_analysis":
        return [
            f"What are the pricing tiers and cost structures for {comp_str}?",
            f"How do free tiers compare across these products?",
            f"What hidden costs should enterprises consider (support, training, migration)?",
            f"Which product offers the best TCO for different team sizes?",
            f"Are there volume discounts or enterprise agreements available?",
        ]
    else:
        return [
            f"What are the key differentiating features of {comp_str}?",
            f"How do these products compare on pricing and value for money?",
            f"Which product best fits enterprise security and compliance requirements?",
            f"What are the main strengths and weaknesses of each competitor?",
            f"How do customers rate these products on review platforms?",
        ]


def normalize_competitors(competitors: list[dict | CompetitorSpec]) -> list[dict]:
    """
    Normalize and filter competitor list.

    Removes:
    - Generic terms (AI, Agent, IDE, platform, etc.)
    - Capability descriptors
    - Duplicates (by name)

    Returns list of valid competitor dicts.
    """
    seen = {}
    result = []

    for comp in competitors:
        # Convert to dict if CompetitorSpec
        if hasattr(comp, 'to_dict'):
            comp = comp.to_dict()
        if not isinstance(comp, dict):
            continue

        name = comp.get("name", "").strip()
        if not name:
            continue

        # Filter out generic terms
        if _is_generic_term(name):
            logger.debug("Filtered out generic term: %s", name)
            continue

        # Filter out single-letter or very short names
        if len(name) < 2:
            continue

        # Deduplicate by normalized name
        name_key = name.lower()
        if name_key not in seen:
            seen[name_key] = comp
            result.append(comp)
        else:
            # Merge: prefer entries with URLs
            existing = seen[name_key]
            if not existing.get("official_url") and comp.get("official_url"):
                seen[name_key] = comp
                result = [comp if c.get("name", "").lower() == name_key else c for c in result]

    return result


# ---------------------------------------------------------------------------
# Schema Type Inference (vNext-R1.6)
# ---------------------------------------------------------------------------

def infer_schema_type(user_query: str, explicit_schema_type: str | None = None) -> str:
    """
    Infer the appropriate schema_type based on user query content.

    Only infers when explicit_schema_type is None or is the default 'ai_agent_platform'.
    Returns the inferred schema_type.
    """
    # Don't override if user explicitly selected a non-default type
    if explicit_schema_type and explicit_schema_type not in ("ai_agent_platform", "auto", "", None):
        return explicit_schema_type

    query_lower = user_query.lower()

    # vNext-R2-D Frontend Patch: Use whole-word matching to avoid false positives
    # from short substrings (e.g. "conf" in "conferencing" should not match "confluence")
    import re
    def contains_word(text: str, keyword: str) -> bool:
        """Return True if keyword (as whole word) is found in text."""
        return bool(re.search(r'\b' + re.escape(keyword) + r'\b', text))

    # Knowledge Management / Collaborative Docs
    km_keywords = [
        "知识管理", "协作文档", "知识库", "知识沉淀", "wiki", "documentation",
        "docs", "workspace", "notion", "confluence", "coda", "slite",
        "airtable", "clickup docs", "文档", "团队文档", "企业文档",
        "knowledge management", "knowledge base", "collaborative docs",
        "team wiki", "shared workspace",
    ]
    if any(contains_word(query_lower, kw) for kw in km_keywords):
        return "knowledge_management"

    # AI Coding Assistant
    coding_keywords = [
        "ai 编程", "代码助手", "cursor", "github copilot", "trae",
        "windsurf", "coding assistant", "ai coding", "code generation",
        "programming assistant",
        # Be careful with short keywords that might appear in other words
        # "ide" is too short and matches "conferencing", "aside", etc.
        # "ide" removed to avoid false positives
    ]
    if any(contains_word(query_lower, kw) for kw in coding_keywords):
        return "ai_coding_assistant"

    # AI Agent Platform
    agent_keywords = [
        "ai agent", "rag", "workflow", "agent platform", "dify", "coze",
        "flowise", "langgraph", "langchain", "autogen", "crewai",
        "agentic", "multi-agent", "agent orchestration",
    ]
    if any(contains_word(query_lower, kw) for kw in agent_keywords):
        return "ai_agent_platform"

    # Pricing Analysis (check before agent platform to avoid "pricing" → "workflow" confusion)
    pricing_keywords = [
        "定价", "pricing", "商业化", "cost", "tco", "费用", "收费",
        "subscription", "billing", "roi",
        # vNext-R2-D Frontend Patch: Collaboration/pricing tools
        "slack", "zoom", "google meet",
        # Be careful with "teams" - too short, matches many unrelated words
        # Use "microsoft teams" as full phrase instead
        "视频会议", "协作工具", "视频会议平台", "远程协作",
        "collaboration tool", "video conferencing", "messaging platform",
    ]
    if any(contains_word(query_lower, kw) for kw in pricing_keywords):
        return "pricing_analysis"

    # Microsoft Teams (specific enough to be safe)
    if "microsoft teams" in query_lower or "teams pricing" in query_lower:
        return "pricing_analysis"

    # Default fallback
    return "competitor_landscape"


def _extract_competitors_from_query(user_query: str) -> list[CompetitorSpec]:
    """Extract competitor names from user query using pattern matching."""
    competitors: list[CompetitorSpec] = []
    found_names: set[str] = set()

    query_lower = user_query.lower()

    for name, info in KNOWN_COMPETITORS.items():
        name_lower = name.lower()
        aliases_lower = [a.lower() for a in info.get("known_aliases", [])]

        # Word-boundary matching that works with Chinese/English mixed text.
        # Use (?<![a-z0-9]) and (?![a-z0-9]) to ensure word is not adjacent
        # to alphanumeric on either side. This handles "codex" in "针对codex"
        # correctly while preventing "cloudcode" from matching "cloudecode".
        import re as _re
        def _word_match(text: str, word: str) -> bool:
            p = rf'(?<![a-z0-9]){_re.escape(word)}(?![a-z0-9])'
            return bool(_re.search(p, text, _re.IGNORECASE))

        if _word_match(query_lower, name_lower) or any(_word_match(query_lower, a) for a in aliases_lower):
            if name_lower not in found_names:
                competitors.append(CompetitorSpec(
                    competitor_id=f"comp_{name.lower().replace(' ', '_')}",
                    name=name,
                    company_name=info.get("company_name", ""),
                    official_url=info.get("official_url", ""),
                    seed_urls=info.get("seed_urls", []),
                    known_aliases=info.get("known_aliases", []),
                    priority="high",
                ))
                found_names.add(name_lower)

    # Also try to extract any capitalized names that might be product names
    # but filter out generic terms
    capitalized_pattern = re.findall(r'\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b', user_query)
    for cap in capitalized_pattern:
        cap_lower = cap.lower()
        if cap_lower not in found_names and len(cap) > 2:
            # Skip known abbreviations
            if cap in {"AI", "API", "SDK", "LLM", "RAG", "UI", "SaaS", "PaaS", "IaaS"}:
                continue
            # Skip generic terms
            if _is_generic_term(cap_lower):
                continue
            competitors.append(CompetitorSpec(
                competitor_id=f"comp_{cap.lower().replace(' ', '_')}",
                name=cap,
                priority="medium",
            ))
            found_names.add(cap_lower)

    # Also match all-lowercase product names (e.g. "codex, trae, cursor, cloudecode")
    # This catches queries typed entirely in lowercase
    lowercase_pattern = re.findall(r'\b[a-z][a-z0-9_]+(?:\s+[a-z][a-z0-9_]+)*\b', user_query)
    GENERIC_SKIPS = {
        "code", "compare", "competitive", "analysis", "product", "tool",
        "platform", "feature", "vs", "against", "and", "for", "with",
        "openai", "anthropic", "google", "microsoft",
    }
    for lc in lowercase_pattern:
        lc_lower = lc.lower()
        if lc_lower not in found_names and len(lc_lower) > 2:
            if lc_lower in GENERIC_SKIPS:
                continue
            if _is_generic_term(lc_lower):
                continue
            # Title-case for display
            display_name = lc_lower.title()
            competitors.append(CompetitorSpec(
                competitor_id=f"comp_{lc_lower.replace(' ', '_')}",
                name=display_name,
                priority="medium",
            ))
            found_names.add(lc_lower)

    return competitors


def _generate_project_name(
    user_query: str,
    competitors: list[dict] | None = None,
    schema_type: str = "competitor_landscape",
) -> str:
    """
    Generate a project name from user query and competitors.

    vNext-R1.6: Include competitors in name when available.
    """
    # If we have competitors, use them for a more meaningful name
    if competitors:
        comp_names = []
        for comp in competitors:
            if isinstance(comp, dict):
                name = comp.get("name", "")
            elif hasattr(comp, 'name'):
                name = comp.name
            else:
                name = str(comp)
            if name and name not in comp_names:
                comp_names.append(name)

        if comp_names:
            # Use up to 4 competitors in the name
            top_comps = comp_names[:4]
            comp_str = " / ".join(top_comps)

            # Add domain suffix based on schema_type
            domain_suffixes = {
                "knowledge_management": "Knowledge Management Competitive Analysis",
                "ai_coding_assistant": "AI Coding Assistant Comparison",
                "ai_agent_platform": "AI Agent Platform Analysis",
                "pricing_analysis": "Pricing Analysis",
                "sales_battlecard": "Competitive Battlecard",
            }
            suffix = domain_suffixes.get(schema_type, "Competitive Analysis")
            return f"{comp_str} {suffix}"

    # Fallback: extract key words from query
    words = re.findall(r'\b\w+\b', user_query)
    # Filter common words
    stop_words = {
        "the", "a", "an", "and", "or", "for", "with", "to", "of", "in", "on",
        "vs", "compare", "analysis", "analyze", "产品", "分析", "比较",
        "enterprise", "公司", "企业", "平台", "工具",
    }
    key_words = [w for w in words if w.lower() not in stop_words and len(w) > 2]
    if len(key_words) >= 3:
        return " ".join(key_words[:4])
    elif key_words:
        return " ".join(key_words)
    return "Competitive Analysis"


# ---------------------------------------------------------------------------
# LLM-based Planner
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT = """You are a research planning agent for enterprise competitive analysis.

Your task is to generate a structured research plan from a natural language query.
DO NOT fabricate collected evidence or final report content.
DO NOT make up facts about competitors.
Only plan what should be collected, analyzed, reviewed, and reported.

Return valid JSON only with the following structure:
{
  "objective": "Clear research objective",
  "competitors": [
    {
      "name": "Product or company name",
      "company_name": "Parent company name if applicable",
      "official_url": "https://...",
      "priority": "high|medium|low"
    }
  ],
  "competitor_selection_rationale": "Why these competitors were selected",
  "source_plan": [...],
  "schema_plan": [...],
  "workflow_plan": [...]
}

IMPORTANT - Competitor Identification Rules:
- Extract ALL product/company names mentioned in the query (do NOT rely on any hardcoded dictionary)
- Handle ANY casing: Codex, CODEX, codex, claudeCode, ClaudeCode, claude code, Claude Code all refer to the same entity
- Include the company's official URL if you can infer or recall it
- Set priority to "high" for explicitly named competitors, "medium" for implied ones
- Do NOT skip competitors just because you don't have full URL info
- Always include at least the competitors explicitly named in the query

Be specific about dimensions and source types based on the query."""


def _call_llm_for_plan(
    user_query: str,
    schema_type: str,
    target_region: str,
    mode: str,
    run_id: str = "pre_run",
    project_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Call LLM to generate a research plan using traced_llm_call.
    
    Returns plan dict or None on failure.
    Records all LLM calls with full trace logging.
    """
    # Build the prompt text for tracing
    prompt_text = f"""Generate a research plan for the following query:

Query: {user_query}

Schema Type: {schema_type}
Target Region: {target_region}
Mode: {mode}

IMPORTANT: Your primary task is to identify ALL competitors mentioned in the query.
Do NOT use any hardcoded dictionary. Extract competitor names directly from the query text.
Handle all casing variations (e.g., "codex", "Codex", "CODEX" all refer to the same product).

Return the research plan as JSON with these keys:
- objective: What to achieve
- competitors: Array of competitor objects with name (required), company_name, official_url, priority
- competitor_selection_rationale: Why these competitors were selected
- source_plan: Array of objects with step, phase, action, targets, purpose
- schema_plan: Array of objects with dimension, label, schema_keys
- workflow_plan: Array of objects with phase, nodes, description"""

    input_payload = {
        "schema_type": schema_type,
        "target_region": target_region,
        "mode": mode,
        "user_query_length": len(user_query),
    }

    def _do_llm_call():
        """Actual LLM call function."""
        client = get_llm_client()
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]
        return client.chat_text(messages, temperature=0.3, max_tokens=4096)

    def _parse_response(response: Any) -> dict[str, Any]:
        """Parse JSON from LLM response."""
        import re
        import json as _json
        
        text = str(response).strip()
        # Try to extract JSON block
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return _json.loads(match.group(0))
        return _json.loads(text)

    try:
        # Use traced_llm_call
        result = traced_llm_call(
            run_id=run_id,
            project_id=project_id,
            node_name="research_plan",
            agent_name="ResearchPlanner",
            agent_role="research_planner",
            prompt_version=PLANNER_PROMPT_VERSION,
            prompt_text=prompt_text,
            input_payload=input_payload,
            call_fn=_do_llm_call,
            parse_fn=_parse_response,
            input_length_hint=len(prompt_text),
            decision_summary="Generated research plan outline",
        )
        
        # Return the parsed output
        return result.get("parsed_output") or result.get("output_text")
        
    except Exception as exc:
        logger.warning("LLM plan generation failed: %s", exc)
        
        # Record fallback trace with reason
        create_llm_fallback_trace(
            run_id=run_id,
            project_id=project_id,
            node_name="research_plan",
            agent_name="ResearchPlanner",
            agent_role="research_planner",
            prompt_version=PLANNER_PROMPT_VERSION,
            prompt_text=prompt_text,
            input_payload=input_payload,
            reason=f"LLM_UNAVAILABLE_OR_INVALID_JSON: {type(exc).__name__}: {exc}",
            decision_summary="Fallback to template plan",
        )
        return None


# ---------------------------------------------------------------------------
# Fallback Planner (Deterministic)
# ---------------------------------------------------------------------------

def _generate_fallback_plan(
    user_query: str,
    schema_type: str,
    target_region: str,
    mode: str,
    language_config: dict[str, Any] | None = None,
    skip_outline_generation: bool = False,
) -> dict[str, Any]:
    """Generate a deterministic fallback research plan.

    Args:
        language_config: Optional language-specific configuration for localized output
        skip_outline_generation: If True, do NOT call LLM to generate outline.
                                Outline is always a separate user-triggered step.
    """
    if language_config is None:
        language_config = get_language_config("zh")
    
    # Infer schema_type if default
    inferred_schema_type = infer_schema_type(user_query, schema_type)
    schema_type = inferred_schema_type

    competitors = _extract_competitors_from_query(user_query)
    competitors_dicts = [c.to_dict() for c in competitors]
    project_name = _generate_project_name(user_query, competitors_dicts, schema_type)
    
    # Localize project name and report title based on language
    default_report_title = language_config.get("default_report_title", "竞品分析报告")
    default_project_name = language_config.get("default_project_name", "竞品调研项目")
    
    if "竞品" in project_name or "竞品" in user_query or language_config.get("output_language") == "中文":
        # Use localized names
        pass  # Keep current project_name
    else:
        project_name = default_project_name

    # Get dimensions for schema type
    dimensions_list = SCHEMA_TYPE_DIMENSIONS.get(
        schema_type,
        SCHEMA_TYPE_DIMENSIONS["competitor_landscape"]
    )
    analysis_dimensions = [
        AnalysisDimension(**{**d, "dimension_id": d["dimension_id"]})
        for d in dimensions_list
    ]

    # Get source types for schema type
    source_types = SCHEMA_SOURCE_TYPES.get(
        schema_type,
        SCHEMA_SOURCE_TYPES["competitor_landscape"]
    )

    # Get report outline for schema type
    outline_sections = REPORT_OUTLINE_TEMPLATES.get(
        schema_type,
        REPORT_OUTLINE_TEMPLATES["competitor_landscape"]
    )

    # ── Outline: only generate if not skipped ────────────────────────────────
    # Outline is ALWAYS a separate user-triggered step.
    # If skip_outline_generation is True, use the template outline only (no LLM).
    if not skip_outline_generation:
        try:
            from backend.app.services.outline_generator import generate_report_outline as _llm_gen_outline
            language_code = "zh" if language_config.get("output_language") == "中文" else "en"
            llm_outline = _llm_gen_outline(
                competitors=competitors_dicts,
                dimensions=analysis_dimensions,
                language=language_code,
            )
            llm_sections = llm_outline.get("sections", [])
            if llm_sections and len(llm_sections) >= len(outline_sections):
                logger.info(
                    "LLM outline (%d sections) >= template (%d sections) — using LLM outline",
                    len(llm_sections), len(outline_sections),
                )
                outline_sections = llm_sections
                if llm_outline.get("report_title"):
                    report_title = llm_outline["report_title"]
            else:
                logger.info(
                    "LLM outline (%d sections) < template (%d sections) — using template outline",
                    len(llm_sections), len(outline_sections),
                )
        except Exception as exc:
            logger.warning("LLM outline generation in plan phase failed: %s — using template outline", exc)
    else:
        logger.info("Outline generation skipped (user will generate separately)")

    report_sections = [ReportSection(**s) for s in outline_sections]

    # Build TaskBrief
    business_goal_label = "了解竞争定位并做出明智决策" if language_config.get("output_language") == "中文" else "Understand competitive positioning and make informed decisions."
    task_brief = TaskBrief(
        task_id=generate_id("task"),
        project_name=project_name,
        user_query=user_query,
        task_type=schema_type,
        target_region=target_region,
        target_audience="enterprise_product_team",
        business_goal=business_goal_label,
    )

    # Build SourcePlan
    collection_strategy = "先收集官方和公开来源，再补充缺失维度的证据。" if language_config.get("output_language") == "中文" else "Collect official and public sources first, then supplement missing dimensions."
    source_plan = SourcePlan(
        source_plan_id=generate_id("source_plan"),
        source_types=source_types,
        collection_strategy=collection_strategy,
        minimum_sources_per_competitor=2,
        minimum_evidence_per_dimension=3,
        compliance_notes=[
            "遵守 robots 和服务条款。",
            "如涉及访谈或问卷，请遮蔽个人信息。",
            "不暴露敏感商业信息。",
        ] if language_config.get("output_language") == "中文" else [
            "Respect robots and terms where applicable.",
            "Mask personal information from interviews or questionnaires.",
            "Do not expose sensitive business information.",
        ],
    )

    # Build ReportOutline
    report_title = language_config.get("default_report_title", "竞品分析报告")
    if project_name:
        report_title = f"{project_name} - {report_title}"
    report_outline = ReportOutline(
        outline_id=generate_id("outline"),
        report_title=report_title,
        sections=report_sections,
    )

    # Build SourceDiscovery first (vNext-R1.5) - for competitors without URLs
    competitors_for_discovery = [c.to_dict() if hasattr(c, 'to_dict') else c for c in competitors]
    source_discovery = SourceDiscovery.from_competitors(competitors_for_discovery, schema_type=schema_type)

    # Build Research Questions (vNext-R1.6)
    research_questions = _generate_research_questions(schema_type, competitors_for_discovery)

    # Build HumanCheckpoints
    human_checkpoints = [
        HumanCheckpoint(
            checkpoint_id="cp_plan_review",
            stage="research_plan_review",
            title="Review research plan before execution.",
            required=True,
            description="Verify competitors, dimensions, and sources are correct.",
        ),
        HumanCheckpoint(
            checkpoint_id="cp_claim_review",
            stage="claim_review",
            title="Review claims before report generation.",
            required=mode == "review",
            description="Verify extracted claims are accurate and well-supported.",
        ),
        HumanCheckpoint(
            checkpoint_id="cp_outline_review",
            stage="outline_review",
            title="Review report outline before section writing.",
            required=mode == "review",
            description="Approve final report structure and key messages.",
        ),
    ]

    # Build SuccessMetrics
    success_metrics = SuccessMetrics(
        minimum_signed_claims=max(10, len(competitors) * 5),
        minimum_sources_per_competitor=2,
        minimum_evidence_items=max(20, len(competitors) * 10),
        minimum_report_words=5000,
    )

    # Update SourcePlan to note that URLs are optional
    source_plan.collection_strategy = (
        "Official URLs and Seed URLs are optional. "
        "If not provided, auto-source discovery will be triggered. "
        "Collect official and public sources first, then supplement missing dimensions."
    )

    # Build full ResearchPlan
    research_plan = ResearchPlan(
        research_plan_id=generate_id("plan"),
        status="draft",
        task_brief=task_brief,
        competitors=competitors,
        analysis_dimensions=analysis_dimensions,
        source_plan=source_plan,
        source_discovery=source_discovery,
        report_outline=report_outline,
        human_checkpoints=human_checkpoints,
        success_metrics=success_metrics,
        research_questions=research_questions,
        generated_by="fallback",
        user_query=user_query,
        schema_type=schema_type,
        target_region=target_region,
        mode=mode,
    )

    return research_plan.to_dict()


# ---------------------------------------------------------------------------
# Query Analysis (LLM-powered, replaces rule-based extraction)
# ---------------------------------------------------------------------------

ANALYZE_PROMPT_VERSION = "v1.0"


ANALYZE_SYSTEM_PROMPT_ZH = """你是一个专业的竞品分析规划助手。你的任务是从用户的自然语言输入中提取所有关键信息。

【强制要求】所有输出内容必须使用中文，不得使用英文或混合语言。

请仔细分析用户输入，识别：
1. **竞品列表**：用户提到的所有竞品名称，包括任何大小写变体（如 codex、Codex、CODEX 都指同一产品）
2. **分析意图**：用户想做什么类型的分析（如竞品全景、产品对比、定价分析、销售战卡等）
3. **推荐维度**：根据竞品类型推荐的分析维度
4. **目标区域**：目标市场区域
5. **输出语言**：用户期望的报告语言

重要规则：
- 如果输入是纯中文，输出也必须是中文
- 竞品识别不依赖任何硬编码字典，直接从用户输入中提取
- 大小写不敏感：codex、Codex、CODEX、ClaudeCode、claude code 都是有效的竞品名称
- 排除通用术语（AI、Agent、Platform 等）
- 如果用户只说了"竞品分析"而没有明确竞品，从输入中推断最可能的产品

返回格式为JSON：
{
    "inferred_intent": "用户想做什么（中文描述）",
    "competitors": [
        {
            "name": "竞品名称",
            "company_name": "母公司名称（如有）",
            "official_url": "官方网址（如能推断）",
            "priority": "high|medium|low",
            "confidence": 0.0-1.0,
            "note": "识别依据"
        }
    ],
    "schema_type": "ai_agent_platform|competitor_landscape|product_comparison|pricing_analysis|sales_battlecard|knowledge_management",
    "analysis_dimensions": [
        {
            "dimension_id": "dimension_id",
            "name": "维度名称（中文）",
            "reason": "为什么推荐这个维度"
        }
    ],
    "target_region": "global|china|us|europe|southeast_asia",
    "output_language": "zh|en",
    "confidence_score": 0.0-1.0,
    "warnings": ["警告信息（如有）"]
}"""


ANALYZE_SYSTEM_PROMPT_EN = """You are a professional competitive analysis planning assistant. Your task is to extract all key information from the user's natural language input.

Please carefully analyze the user input to identify:
1. **Competitors**: All product/company names mentioned by the user
2. **Intent**: What type of analysis the user wants
3. **Recommended dimensions**: Analysis dimensions based on competitor type
4. **Target region**: Target market region
5. **Output language**: Expected report language

Important rules:
- If input is in Chinese, output should be in Chinese
- Competitor identification is NOT based on any hardcoded dictionary — extract directly from input
- Case-insensitive: codex, Codex, CODEX all refer to the same product
- Exclude generic terms (AI, Agent, Platform, etc.)
- If user only says "competitive analysis" without naming competitors, infer the most likely ones from context

Return format as JSON:
{
    "inferred_intent": "What the user wants to do",
    "competitors": [
        {
            "name": "Product name",
            "company_name": "Parent company name (if applicable)",
            "official_url": "Official URL (if inferrable)",
            "priority": "high|medium|low",
            "confidence": 0.0-1.0,
            "note": "Identification basis"
        }
    ],
    "schema_type": "ai_agent_platform|competitor_landscape|product_comparison|pricing_analysis|sales_battlecard|knowledge_management",
    "analysis_dimensions": [
        {
            "dimension_id": "dimension_id",
            "name": "Dimension name",
            "reason": "Why this dimension is recommended"
        }
    ],
    "target_region": "global|china|us|europe|southeast_asia",
    "output_language": "zh|en",
    "confidence_score": 0.0-1.0,
    "warnings": ["Warning messages (if any)"]
}"""


def analyze_query(
    user_query: str,
    target_region: str = "global",
    run_id: str | None = None,
    project_id: str | None = None,
    detected_language: str = "zh",
) -> dict[str, Any]:
    """
    Analyze user query using LLM to extract competitors, intent, dimensions, and schema.

    This replaces the old rule-based _extract_competitors_from_query and infer_schema_type
    functions with a single intelligent LLM call.

    Args:
        user_query: Natural language research query
        target_region: Target market region hint
        run_id: Optional run ID for trace logging
        project_id: Optional project ID for trace logging
        detected_language: Detected language ('zh', 'en', or 'mixed')

    Returns:
        Dict with competitors, schema_type, dimensions, target_region, etc.
    """
    if not run_id:
        run_id = f"analyze_{generate_id('run')}"

    system_prompt = ANALYZE_SYSTEM_PROMPT_ZH if detected_language in ("zh", "mixed") else ANALYZE_SYSTEM_PROMPT_EN
    user_prompt = f"""请分析以下用户输入：

{user_query}

目标区域提示: {target_region}

请返回JSON格式的分析结果。"""

    input_payload = {
        "user_query_length": len(user_query),
        "target_region": target_region,
        "detected_language": detected_language,
    }

    def _do_llm_call():
        client = get_llm_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return client.chat_text(messages, temperature=0.2, max_tokens=2048)

    def _parse_response(response: Any) -> dict[str, Any]:
        import json as _json
        import re as _re
        text = str(response).strip()
        match = _re.search(r'\{.*\}', text, _re.DOTALL)
        if match:
            return _json.loads(match.group(0))
        return _json.loads(text)

    try:
        result = traced_llm_call(
            run_id=run_id,
            project_id=project_id,
            node_name="query_analysis",
            agent_name="QueryAnalyzer",
            agent_role="query_analyzer",
            prompt_version=ANALYZE_PROMPT_VERSION,
            prompt_text=f"System: {system_prompt}\n\nUser: {user_prompt}",
            input_payload=input_payload,
            call_fn=_do_llm_call,
            parse_fn=_parse_response,
            input_length_hint=len(system_prompt) + len(user_prompt),
            decision_summary="Analyzed user query for competitors, intent, and dimensions",
        )
        parsed = result.get("parsed_output") or result.get("output_text", {})
        if isinstance(parsed, str):
            import json
            parsed = json.loads(parsed)
        return parsed

    except Exception as exc:
        logger.warning("LLM query analysis failed: %s — falling back to rules", exc)

        create_llm_fallback_trace(
            run_id=run_id,
            project_id=project_id,
            node_name="query_analysis",
            agent_name="QueryAnalyzer",
            agent_role="query_analyzer",
            prompt_version=ANALYZE_PROMPT_VERSION,
            prompt_text=f"System: {system_prompt}\n\nUser: {user_prompt}",
            input_payload=input_payload,
            reason=f"LLM_UNAVAILABLE_OR_INVALID_JSON: {type(exc).__name__}: {exc}",
            decision_summary="Fallback to rule-based extraction",
        )

        # Fallback: use existing rule-based extraction
        schema_type = infer_schema_type(user_query, "ai_agent_platform")
        competitors_raw = _extract_competitors_from_query(user_query)
        competitors_dicts = [c.to_dict() for c in competitors_raw]

        return {
            "inferred_intent": f"竞品分析（规则推断: {schema_type}）",
            "competitors": competitors_dicts,
            "schema_type": schema_type,
            "analysis_dimensions": [],
            "target_region": target_region,
            "output_language": detected_language,
            "confidence_score": 0.3,
            "warnings": ["LLM unavailable, used rule-based fallback"],
        }


# ---------------------------------------------------------------------------
# Main Planner Function
# ---------------------------------------------------------------------------

def generate_research_plan(
    user_query: str,
    schema_type: str = "ai_agent_platform",
    target_region: str = "global",
    mode: str = "review",
    run_id: str | None = None,
    project_id: str | None = None,
    detected_language: str = "zh",
    language_config: dict[str, Any] | None = None,
    explicit_competitors: list[dict[str, Any]] | None = None,
    skip_outline_generation: bool = False,
) -> dict[str, Any]:
    """
    Generate a ResearchPlan from user query.

    Three paths:
    1. explicit_competitors + skip_outline: fast path from /analyze review
       → skip LLM analysis, skip LLM outline, use template structure
    2. explicit_competitors, no skip_outline: use preview data but generate outline
       → not used currently, outline always separate
    3. no explicit_competitors: legacy full analysis path
       → tries LLM analysis + LLM outline (now both skipped for outline)

    Outline is ALWAYS generated separately by the user via /generate-outline.
    This function never generates outline anymore.

    Args:
        user_query: Natural language research query
        schema_type: Analysis schema type
        target_region: Target market region
        mode: Execution mode (auto, review, expert)
        run_id: Optional run ID for trace logging
        project_id: Optional project ID for trace logging
        detected_language: Detected user language ('zh', 'en', 'mixed')
        language_config: Language-specific configuration for prompts/output
        explicit_competitors: Pre-analyzed competitor list from /analyze endpoint
        skip_outline_generation: If True, skip LLM outline generation (always True now)
    """
    # Use default run_id if not provided
    if not run_id:
        run_id = f"pre_run_{generate_id('run')}"

    # Use default language config if not provided
    if language_config is None:
        language_config = get_language_config(detected_language)

    # Infer schema_type based on query content (if not already set)
    inferred_schema_type = infer_schema_type(user_query, schema_type)

    # ── Path A: explicit data from /analyze preview ────────────────────────
    if explicit_competitors is not None:
        # Skip LLM analysis — use the preview data the user already reviewed
        competitors = []
        for comp in explicit_competitors:
            if isinstance(comp, dict):
                competitors.append(CompetitorSpec.from_dict(comp))
            elif hasattr(comp, 'to_dict'):
                competitors.append(comp)

        # Deduplicate by name
        seen = {}
        for comp in competitors:
            if comp.name not in seen:
                seen[comp.name] = comp
        competitors = list(seen.values())

        # Normalize and filter competitors (remove generic terms)
        normalized_comps = normalize_competitors(
            [c.to_dict() if hasattr(c, 'to_dict') else c for c in competitors]
        )

        fallback_plan = _generate_fallback_plan(
            user_query, inferred_schema_type, target_region, mode, language_config,
            skip_outline_generation=True,
        )
        fallback_plan["competitors"] = normalized_comps
        fallback_plan["generated_by"] = "analyze_preview"
        fallback_plan["generation_metadata"] = {
            "llm_used": True,
            "llm_call_succeeded": True,
            "data_source": "analyze_preview",
            "fallback_structure_used": True,
        }
        fallback_plan["schema_type"] = inferred_schema_type
        fallback_plan["task_brief"]["project_name"] = _generate_project_name(
            user_query, normalized_comps, inferred_schema_type
        )

        # Build SourceDiscovery
        source_discovery = SourceDiscovery.from_competitors(
            normalized_comps, schema_type=inferred_schema_type
        )
        fallback_plan["source_discovery"] = source_discovery.to_dict()

        # Add research questions
        research_questions = _generate_research_questions(inferred_schema_type, normalized_comps)
        fallback_plan["research_questions"] = research_questions

        fallback_plan["language_metadata"] = {
            "detected_language": detected_language,
            "output_language": language_config.get("output_language", "中文"),
        }

        # NOTE: report_outline is intentionally left empty here.
        # Outline generation is a SEPARATE user-triggered step.

        return fallback_plan

    # ── Path B: no explicit data — run full analysis ─────────────────────────
    # NOTE: outline generation is always skipped here.
    # Users generate outline via the separate /generate-outline endpoint after confirming the plan.
    llm_result = _call_llm_for_plan(
        user_query, inferred_schema_type, target_region, mode,
        run_id=run_id, project_id=project_id
    )

    if llm_result:
        # Enhance with structured data from fallback
        fallback_plan = _generate_fallback_plan(
            user_query, inferred_schema_type, target_region, mode, language_config,
            skip_outline_generation=True,
        )

        # Merge LLM result with fallback structure
        # LLM-identified competitors take priority; fallback fills in missing fields only
        competitors = []

        # First: LLM-identified competitors (primary source)
        if llm_result.get("competitors"):
            for comp in llm_result["competitors"]:
                if isinstance(comp, dict):
                    competitors.append(CompetitorSpec.from_dict(comp))

        # Second: fallback competitors (fill gaps only if LLM found fewer)
        llm_names_lower = {c.name.lower() for c in competitors}
        for comp in fallback_plan.get("competitors", []):
            if isinstance(comp, dict):
                spec = CompetitorSpec.from_dict(comp)
                if spec.name.lower() not in llm_names_lower:
                    competitors.append(spec)

        # Deduplicate by name
        seen = {}
        for comp in competitors:
            if comp.name not in seen:
                seen[comp.name] = comp
        competitors = list(seen.values())

        # Normalize and filter competitors (remove generic terms)
        normalized_comps = normalize_competitors([c.to_dict() if hasattr(c, 'to_dict') else c for c in competitors])
        fallback_plan["competitors"] = normalized_comps
        
        # vNext-R2-C: Use more accurate generated_by value
        fallback_plan["generated_by"] = "llm_augmented"
        
        # vNext-R2-C: Add generation_metadata for detailed tracking
        fallback_plan["generation_metadata"] = {
            "llm_used": True,
            "llm_call_succeeded": True,
            "fallback_structure_used": True,
            "llm_fields_used": [
                "objective",
                "competitors",
                "competitor_selection_rationale",
                "source_plan",
                "schema_plan",
                "workflow_plan",
            ],
            "fallback_fields_merged": [
                "schema_type",
                "task_brief",
                "source_discovery",
                "report_outline",
                "analysis_dimensions",
                "human_checkpoints",
                "success_metrics",
            ],
        }

        # Override schema_type with inferred type
        fallback_plan["schema_type"] = inferred_schema_type

        # Regenerate project name with competitors
        fallback_plan["task_brief"]["project_name"] = _generate_project_name(
            user_query, normalized_comps, inferred_schema_type
        )

        # Build SourceDiscovery for competitors without URLs
        source_discovery = SourceDiscovery.from_competitors(normalized_comps, schema_type=inferred_schema_type)
        fallback_plan["source_discovery"] = source_discovery.to_dict()

        # Add research questions
        research_questions = _generate_research_questions(inferred_schema_type, normalized_comps)
        fallback_plan["research_questions"] = research_questions

        # Add language metadata for output adaptation
        fallback_plan["language_metadata"] = {
            "detected_language": detected_language,
            "output_language": language_config.get("output_language", "中文"),
        }

        # Update SourcePlan strategy note
        if fallback_plan.get("source_plan"):
            sp = fallback_plan["source_plan"]
            if isinstance(sp, dict):
                sp["collection_strategy"] = (
                    "Official URLs and Seed URLs are optional. "
                    "If not provided, auto-source discovery will be triggered. "
                    "Collect official and public sources first, then supplement missing dimensions."
                )

        # Validate
        is_valid, errors = validate_research_plan(fallback_plan)
        if is_valid:
            return fallback_plan
        else:
            logger.warning("LLM plan validation failed: %s. Using fallback.", errors)

    # Fallback to deterministic plan (LLM not available or failed)
    # Always skip outline generation — outline is always a separate step.
    fallback_plan = _generate_fallback_plan(
        user_query, schema_type, target_region, mode, language_config,
        skip_outline_generation=True,
    )
    
    # vNext-R2-C: Update generation_metadata for fallback case
    fallback_plan["generation_metadata"] = {
        "llm_used": llm_result is not None,
        "llm_call_succeeded": False,
        "fallback_structure_used": True,
        "llm_fields_used": [],
        "fallback_fields_merged": [
            "competitors",
            "schema_type",
            "task_brief",
            "source_discovery",
            "report_outline",
            "analysis_dimensions",
            "human_checkpoints",
            "success_metrics",
        ],
    }
    
    # Add language metadata for output adaptation
    fallback_plan["language_metadata"] = {
        "detected_language": detected_language,
        "output_language": language_config.get("output_language", "中文"),
    }
    
    return fallback_plan


def revise_research_plan(
    plan: dict[str, Any],
    human_instruction: str,
) -> dict[str, Any]:
    """
    Revise a ResearchPlan based on human instruction.

    This is a simple deterministic revision - extracts keywords and
    adjusts competitors/dimensions accordingly.
    """
    instruction_lower = human_instruction.lower()

    # Extract competitor mentions
    for name, info in KNOWN_COMPETITORS.items():
        name_lower = name.lower()
        if name_lower in instruction_lower and name not in [c.get("name") for c in plan.get("competitors", [])]:
            plan["competitors"].append({
                "competitor_id": f"comp_{name.lower().replace(' ', '_')}",
                "name": name,
                "company_name": info.get("company_name", ""),
                "official_url": info.get("official_url", ""),
                "seed_urls": info.get("seed_urls", []),
                "priority": "medium",
            })

    # Adjust dimensions based on instruction
    dimension_keywords = {
        "pricing": "pricing_model",
        "enterprise": "enterprise_readiness",
        "deployment": "enterprise_readiness",
        "security": "enterprise_readiness",
        "feature": "function_tree",
        "function": "function_tree",
        "user": "user_persona",
        "customer": "customer_voice",
        "review": "customer_voice",
    }

    current_dims = {d.get("dimension_id") for d in plan.get("analysis_dimensions", [])}
    for keyword, dim_id in dimension_keywords.items():
        if keyword in instruction_lower and dim_id not in current_dims:
            # Add missing dimension from defaults
            for dim in DEFAULT_DIMENSIONS:
                if dim["dimension_id"] == dim_id:
                    plan["analysis_dimensions"].append(dim)
                    current_dims.add(dim_id)
                    break

    # Update task brief with instruction
    if plan.get("task_brief"):
        brief = plan["task_brief"]
        if isinstance(brief, str):
            brief = json.loads(brief)
        existing_goal = brief.get("business_goal", "")
        brief["business_goal"] = f"{existing_goal} {human_instruction}".strip()
        plan["task_brief"] = brief

    plan["generated_by"] = "human_edited"
    return plan


# ---------------------------------------------------------------------------
# DAG Compiler
# ---------------------------------------------------------------------------

def compile_execution_dag(research_plan: dict[str, Any]) -> dict[str, Any]:
    """
    Compile a ResearchPlan into an ExecutionDAG.

    Returns a dict with dag_id, nodes, and edges.
    """
    plan_id = research_plan.get("research_plan_id", generate_id("plan"))
    dag_id = generate_id("dag")

    competitors = research_plan.get("competitors", [])
    num_competitors = len(competitors) if isinstance(competitors, list) else 1
    mode = research_plan.get("mode", "review")

    # Standard pipeline nodes
    nodes = [
        DAGNode(
            node_id="node_confirm_plan",
            node_type="confirm_plan",
            agent_name="system",
            depends_on=[],
            input_refs=["research_plan"],
            output_refs=["confirmed_plan"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_collect_sources",
            node_type="collect_sources",
            agent_name="collector",
            depends_on=["node_confirm_plan"],
            input_refs=["source_plan", "competitor_specs"],
            output_refs=["source_records", "snapshots"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_extract_evidence",
            node_type="extract_evidence",
            agent_name="collector",
            depends_on=["node_collect_sources"],
            input_refs=["source_records"],
            output_refs=["evidence_items"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_extract_facts",
            node_type="extract_facts",
            agent_name="analyst",
            depends_on=["node_extract_evidence"],
            input_refs=["evidence_items", "analysis_dimensions"],
            output_refs=["facts"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_generate_claims",
            node_type="generate_claims",
            agent_name="analyst",
            depends_on=["node_extract_facts"],
            input_refs=["facts"],
            output_refs=["claim_drafts"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_review_claims",
            node_type="review_claims",
            agent_name="reviewer",
            depends_on=["node_generate_claims"],
            input_refs=["claim_drafts", "evidence_items"],
            output_refs=["review_issues", "signed_claims"],
            human_checkpoint=(mode == "review"),
            status="pending",
        ),
        DAGNode(
            node_id="node_plan_report_outline",
            node_type="plan_report_outline",
            agent_name="writer",
            depends_on=["node_review_claims"],
            input_refs=["signed_claims", "report_outline_template"],
            output_refs=["final_outline"],
            human_checkpoint=(mode == "review"),
            status="pending",
        ),
        DAGNode(
            node_id="node_write_sections",
            node_type="write_sections",
            agent_name="writer",
            depends_on=["node_plan_report_outline"],
            input_refs=["final_outline", "signed_claims"],
            output_refs=["section_drafts"],
            human_checkpoint=False,
            status="pending",
        ),
        DAGNode(
            node_id="node_review_report",
            node_type="review_report",
            agent_name="reviewer",
            depends_on=["node_write_sections"],
            input_refs=["section_drafts"],
            output_refs=["review_comments", "approved_sections"],
            human_checkpoint=(mode == "review"),
            status="pending",
        ),
        DAGNode(
            node_id="node_compose_final_report",
            node_type="compose_final_report",
            agent_name="writer",
            depends_on=["node_review_report"],
            input_refs=["approved_sections"],
            output_refs=["final_report"],
            human_checkpoint=False,
            status="pending",
        ),
    ]

    # Define edges
    edges = [
        DAGEdge(from_node="node_confirm_plan", to_node="node_collect_sources"),
        DAGEdge(from_node="node_collect_sources", to_node="node_extract_evidence"),
        DAGEdge(from_node="node_extract_evidence", to_node="node_extract_facts"),
        DAGEdge(from_node="node_extract_facts", to_node="node_generate_claims"),
        DAGEdge(from_node="node_generate_claims", to_node="node_review_claims"),
        DAGEdge(from_node="node_review_claims", to_node="node_plan_report_outline"),
        DAGEdge(from_node="node_plan_report_outline", to_node="node_write_sections"),
        DAGEdge(from_node="node_write_sections", to_node="node_review_report"),
        DAGEdge(from_node="node_review_report", to_node="node_compose_final_report"),
    ]

    dag = ExecutionDAG(
        dag_id=dag_id,
        research_plan_id=plan_id,
        nodes=nodes,
        edges=edges,
    )

    return dag.to_dict()
