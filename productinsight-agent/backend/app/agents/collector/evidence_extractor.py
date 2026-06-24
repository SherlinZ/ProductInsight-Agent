"""
Rule-based evidence extractor.
Maps raw text snippets to structured EvidenceItems based on keyword pattern matching.
Does NOT use LLM for classification.

P0-7: Parallelized with ProcessPoolExecutor for per-document evidence extraction.
Each document is processed in a separate process to bypass GIL for CPU-bound work.
Per-document timeout (60s) ensures slow documents don't block the entire batch.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from multiprocessing import get_context
from typing import Any
from urllib.parse import urlparse

from backend.app.services.pii_service import mask_pii, sanitize_evidence_snippet
from backend.app.services.evidence_evaluator import is_noise_evidence

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Create a URL-safe slug from a product name."""
    return name.lower().replace(" ", "-").replace("_", "-")


# Maps keyword groups to schema_key prefixes
# P0-5: Extended to cover ALL section-required dimensions with broader keyword coverage
_SCHEMA_KEYWORDS: list[tuple[list[str], str, float]] = [
    # (keywords, schema_key_prefix, base_confidence)

    # Evidence-Sufficiency Sprint: removed "cost" and "enterprise" from pricing_model.
    # "cost" causes business_value content (e.g. "reducing cost and time to market")
    # to be misclassified as pricing_model, then rejected by Evidence Contract gate.
    (
        ["pricing", "price", "plan", "free", "subscription", "credit", "fee", "billing",
         "token", "quota", "paid", "套餐", "费用", "计费", "价格", "订阅"],
        "pricing_model",
        0.80,
    ),

    # Evidence-Sufficiency Sprint: added Chinese keywords to workflow classification.
    # This ensures Chinese official docs (Dify/Coze/FastGPT) get classified correctly.
    (
        ["workflow", "orchestration", "node", "flow", "builder", "drag", "canvas", "pipeline",
         "工作流", "编排", "节点", "画布", "拖拽", "流程"],
        "function_tree.workflow",
        0.78,
    ),
    (
        ["knowledge", "rag", "retrieval", "document", "embed", "vector", "chunk", "index",
         "knowledge base", "knowledge structure", "知识库", "向量", "检索", "嵌入", "文档"],
        "agent_product_capabilities.knowledge_base",
        0.75,
    ),
    (
        ["deploy", "self-hosted", "private", "docker", "kubernetes", "k8s", "on-premise",
         "open source", "部署", "私有化", "本地", "开源", "私有部署"],
        "agent_product_capabilities.deployment_options",
        0.78,
    ),
    (
        ["permission", "audit", "sso", "security", "encryption", "rbac", "role",
         "access control", "权限", "单点登录", "审计", "加密", "企业"],
        "agent_product_capabilities.enterprise_readiness",
        0.77,
    ),
    (
        ["user", "team", "developer", "business", "customer", "persona", "audience",
         "用户", "团队", "开发者", "企业", "客户"],
        "user_persona",
        0.72,
    ),
    (
        ["api", "integration", "webhook", "plugin", "extension", "connector",
         "接口", "集成", "插件", "扩展", "连接器"],
        "function_tree.integration",
        0.76,
    ),
    (
        ["model", "llm", "gpt", "claude", "gemini", "anthropic", "openai", "mistral",
         "模型", "大模型", "LLM"],
        "agent_product_capabilities.model_support",
        0.78,
    ),
    (
        ["agent", "bot", "assistant", "chatbot", "copilot", "automation",
         "智能体", "机器人", "助手", "智能助手", "自动化"],
        "function_tree.agent_capabilities",
        0.73,
    ),

    # Evidence-Sufficiency Sprint: new schema key for business_value.
    # Content about "reducing cost", "shortening time to market", "improving productivity"
    # was being misclassified as pricing_model. Now it has its own dimension.
    (
        ["reducing cost", "shorten time", "improve efficiency", "boost productivity",
         "time to market", "lower cost", "cost reduction", "efficiency gain",
         "提升效率", "缩短", "降低成本", "提升生产力", "时间成本", "降本增效",
         " productivity", "throughput", "time saving"],
        "business_value",
        0.75,
    ),

    # P0-5: Extended dimensions (with Chinese keyword additions)
    (
        ["ai", "copilot", "writing", "search", "summariz", "translate", "suggest",
         "auto-complete", "AI", "智能", "写作", "搜索", "摘要"],
        "ai_assistance",
        0.76,
    ),
    (
        ["collab", "collaborat", "share", "comment", "edit", "real-time",
         "simultaneous", "teamwork", "协作", "协同", "团队", "共享"],
        "collaboration_experience",
        0.75,
    ),
    (
        ["govern", "compliance", "hipaa", "gdpr", "soc 2", "certificat", "audit log",
         "policy", "合规", "治理", "审计"],
        "permission_governance",
        0.77,
    ),
    (
        ["ecosystem", "community", "marketplace", "template", "gallery",
         "plugin store", "app directory", "生态", "社区", "模板", "插件市场"],
        "ecosystem",
        0.74,
    ),
    (
        ["swot", "strength", "weakness", "opportunit", "threat", "advantage",
         "competitive", "优势", "劣势", "机会", "威胁"],
        "swot_analysis",
        0.70,
    ),
    (
        ["pricing", "tco", "roi", "return on invest", "cost benefit", "budget",
         "value", "ROI", "TCO", "投资回报", "成本效益"],
        "value_proposition",
        0.75,
    ),
    (
        ["review", "g2", "capterra", "user review", "customer feedback",
         "testimonial", "rating", "用户评价", "口碑"],
        "customer_voice",
        0.70,
    ),

    # Evidence-Sufficiency Sprint: removed standalone "function_tree" entry.
    # It was matching "feature"/"capability"/"function" and overriding more specific
    # sub-entries like "function_tree.workflow". Sub-entries now cover this ground
    # with Chinese keywords added above, so the generic fallback is no longer needed.

    (
        ["rag", "retrieval", "vector", "embed", "knowledge base", "semantic search",
         "检索增强生成", "知识库"],
        "rag_support",
        0.76,
    ),
    (
        ["overview", "introduction", "about", "what is", "product describ",
         "summary", "概述", "介绍", "简介"],
        "product_overview",
        0.68,
    ),
    (
        ["mobile", "ios", "android", "app store", "google play", "phone", "tablet",
         "移动端", "手机", "平板"],
        "mobile_support",
        0.72,
    ),
    (
        ["offline", "disconnect", "no internet", "local", "sync",
         "离线", "断网", "本地"],
        "offline_capability",
        0.70,
    ),
    (
        ["version", "history", "revision", "rollback", "restore", "audit trail",
         "change log", "版本", "历史", "回滚", "变更"],
        "version_control",
        0.73,
    ),
]

# Descriptions for each schema key, used in LLM classification prompts
SCHEMA_KEY_DESCRIPTIONS = """
## Schema Key Descriptions:
pricing_model: Pricing plans, subscription tiers, credit systems, free trials, cost structures
function_tree.workflow: Workflow orchestration, node-based builders, drag-and-drop canvas, pipeline management
agent_product_capabilities.knowledge_base: RAG retrieval, document processing, vector embeddings, knowledge structure
agent_product_capabilities.deployment_options: Self-hosted, Docker, Kubernetes, on-premise, open source deployment
agent_product_capabilities.enterprise_readiness: SSO, RBAC, security, encryption, audit, access control
user_persona: Target users, developer audience, team size, business customer profiles
function_tree.integration: API, webhooks, plugins, extensions, connectors
agent_product_capabilities.model_support: LLM support, GPT/Claude/Gemini integration, model providers
function_tree.agent_capabilities: Agent/bot capabilities, chatbot features, automation tools
ai_assistance: AI writing, search, summarization, translation, auto-complete features
collaboration_experience: Team collaboration, real-time editing, sharing, comments
ecosystem: Community, marketplace, templates, plugins, third-party integrations
swot_analysis: Competitive analysis, strengths, weaknesses, market positioning
value_proposition: ROI, TCO, cost-benefit, pricing value
customer_voice: User reviews, testimonials, ratings, customer feedback
function_tree: Product features, capabilities, use cases
rag_support: RAG architecture, vector search, semantic retrieval
product_overview: Product description, introduction, summary
mobile_support: Mobile apps, iOS/Android, phone/tablet access
offline_capability: Offline mode, local deployment, disconnected operation
version_control: Version history, rollback, audit trail, change logs
"""

# Trust tier boosts
TRUST_BOOSTS = {
    "documentation": 0.05,
    "official_site": 0.08,
    "pricing_page": 0.05,
    "community_review": -0.10,
}

def _best_schema_key(text: str) -> tuple[str, float]:
    """Return (schema_key, base_confidence) for the given text."""
    text_lower = text.lower()
    best_key = "function_tree.general"
    best_hits = 0
    base_conf = 0.70

    for keywords, schema_key, base in _SCHEMA_KEYWORDS:
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > best_hits:
            best_hits = hits
            best_key = schema_key
            base_conf = base

    return best_key, base_conf


def _llm_classify_schema(
    chunk: str,
    product_name: str,
    rule_schema_key: str,
    rule_confidence: float,
    run_id: str,
) -> tuple[str, float, str]:
    """
    Use LLM to disambiguate which schema key the evidence chunk best supports.

    Returns: (schema_key, llm_confidence, reason)

    Called only when:
    - Rule-based confidence is below HIGH_CONF_SCHEMA_THRESHOLD, OR
    - Quality score is in the LLM_CLASSIFY range

    P0.2: ManuSearch-style evidence extraction - LLM semantic understanding
    instead of pure keyword matching.
    """
    try:
        from backend.app.services.llm_client import get_llm_client
        from backend.app.tracing.llm_trace import traced_llm_call

        product = product_name or "the product"
        rule_key = rule_schema_key or "unknown"

        prompt = f"""Given the following evidence text from researching {product}, classify which schema key it best supports.

## Evidence Text:
\"\"\"{chunk[:800]}\"\"\"

## Rule-Based Classification:
Schema key: {rule_key}
Confidence: {rule_confidence:.2f}

{SCHEMA_KEY_DESCRIPTIONS}

Return a JSON object with:
- "schema_key": the best matching schema key from the list above (e.g. "pricing_model")
- "confidence": your confidence from 0.0 to 1.0
- "reason": one sentence explaining why this key fits best

Return ONLY valid JSON, no markdown or explanation."""

        def _call_llm():
            client = get_llm_client()
            return client.chat_text(
                [
                    {"role": "system", "content": "You are an evidence classification assistant. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=300,
                timeout=20,
            )

        def _parse(text: str) -> dict:
            text = text.strip()
            # Try ```json block
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            # Try raw { }
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise ValueError(f"Could not parse JSON from: {text[:200]}")

        result = traced_llm_call(
            run_id=run_id,
            node_name="evidence_classify",
            agent_name="EvidenceClassifier",
            agent_role="evidence_classifier",
            prompt_version="p0.2_v1",
            prompt_text=prompt,
            input_payload={
                "product": product,
                "rule_schema_key": rule_key,
                "rule_confidence": rule_confidence,
                "chunk_length": len(chunk),
            },
            call_fn=_call_llm,
            parse_fn=_parse,
            input_length_hint=len(prompt),
            decision_summary=f"LLM classified schema_key for {product}",
        )

        parsed = result.get("parsed_output") or {}
        llm_key = parsed.get("schema_key", rule_schema_key)
        llm_conf = float(parsed.get("confidence", rule_confidence))
        reason = parsed.get("reason", "")

        # Clamp confidence to valid range
        llm_conf = max(0.0, min(1.0, llm_conf))

        logger.debug(
            "LLM classify: rule=%s/%.2f → llm=%s/%.2f for %s",
            rule_schema_key, rule_confidence, llm_key, llm_conf, product[:20],
        )
        return llm_key, llm_conf, reason

    except Exception as exc:
        logger.warning(
            "LLM classify failed, falling back to rule: %s. Error: %s",
            rule_schema_key, exc,
        )
        return rule_schema_key, rule_confidence, "rule_fallback"


# Evidence-Sufficiency Sprint: helper functions for source_type/trust_tier from URL
# (mirrors collector.py logic so evidence_extractor can infer when upstream doesn't provide them)

OFFICIAL_PRODUCT_DOMAINS = {
    # Coze
    "coze.cn", "coze.com", "www.coze.cn", "www.coze.com",
    "docs.coze.cn", "docs.coze.com",
    # Dify
    "dify.ai", "dify.com", "www.dify.ai", "www.dify.com",
    "docs.dify.ai", "docs.dify.com", "github.com",
    # FastGPT
    "fastgpt.cn", "fastgpt.com", "www.fastgpt.cn", "www.fastgpt.com",
    "docs.fastgpt.cn",
}

THIRD_PARTY_DOMAINS = {
    "medium.com", "dev.to", "stackoverflow.com", "zhihu.com",
    "bilibili.com", "weixin.qq.com", "reddit.com",
}


def _infer_source_type(url: str) -> str:
    """Infer source_type from URL path and domain. Called when upstream doesn't provide it."""
    if not url:
        return "web_page"
    url_lower = url.lower()
    parsed_netloc = url_lower.split("?")[0].split("#")[0]  # strip query/fragment
    # Check domain first — docs.COZE.cn, docs.DIFY.ai etc are documentation domains
    # so any path under them is documentation
    if (
        ".docs." in parsed_netloc
        or parsed_netloc.startswith("docs.")
        or parsed_netloc.endswith(".docs")
        or parsed_netloc.endswith(".docs/")
        or "/docs" in parsed_netloc
        or "/guides" in parsed_netloc
        or "/reference" in parsed_netloc
        or "/api" in parsed_netloc
        or "/documentation" in parsed_netloc
        or "documentation" in parsed_netloc
    ):
        return "documentation"
    if "/pricing" in url_lower or "/price" in url_lower or "/plans" in url_lower:
        return "pricing_page"
    if "github.com" in url_lower:
        return "github"
    return "official_site"


def _infer_trust_tier(url: str, product_id: str = "") -> str:
    """Infer trust_tier from domain. Mirrors collector.py _determine_trust_tier."""
    if not url:
        return "medium"
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    # Strip www. prefix
    domain = domain.replace("www.", "")
    # Check official domains
    if any(d in domain for d in OFFICIAL_PRODUCT_DOMAINS):
        return "high"
    # Check third-party domains
    if any(d in domain for d in THIRD_PARTY_DOMAINS):
        return "low"
    return "medium"


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-like chunks at newlines or sentence-ending punctuation.

    Handles both English (period/comma) and Chinese (。，) text properly.
    Chinese docs often have mobile-friendly one-line-per-sentence format,
    so we prefer paragraph boundaries (double newlines) and avoid over-splitting
    single-line sentences that form a coherent unit.
    """
    # Split on double newlines first (paragraphs) — this is the most reliable separator
    chunks = re.split(r"\n{2,}", text)
    result: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        # For long chunks (>600 chars), split by single newlines,
        # but merge very short lines (<20 chars) with the previous line
        if len(chunk) > 600 and "\n" in chunk:
            lines = [c.strip() for c in chunk.split("\n") if c.strip()]
            merged: list[str] = []
            buffer = ""
            for line in lines:
                if buffer:
                    if len(buffer) + len(line) < 200:
                        # Short line — merge with buffer
                        buffer = buffer + "\n" + line
                    else:
                        # Long enough line — flush buffer and start new
                        if buffer:
                            merged.append(buffer)
                        buffer = line
                else:
                    buffer = line
            if buffer:
                merged.append(buffer)
            result.extend(merged)
        else:
            result.append(chunk)
    return result


def _make_snippet(text: str, max_len: int = 500, min_len: int = 80) -> str:
    """Trim snippet to max_len, preserving word boundaries. Return empty string if too short.

    Evidence-Sufficiency Sprint: skip Chinese nav-noise lines at the beginning of the snippet.
    Chinese docs often have sidebar navigation fragments at the top of the page text:
    "文档 备案 控制台 登录 立即注册 首页 文档中心" etc.
    These are not part of the real content and should be skipped.
    """
    text = text.strip()
    # Skip leading Chinese nav-noise lines
    lines = text.split("\n")
    skip_count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        stripped_lower = stripped.lower()
        # Short nav lines: 1-2 chars (Chinese) or common English nav phrases
        is_short_nav = len(stripped) <= 5 or stripped_lower in {
            "quick start", "getting started", "get started",
            "forum", "changelog", "docs", "documentation",
            "contact us", "contact", "login", "sign up", "signup",
            "log in", "footer", "navigation", "header", "menu",
            "table of contents", "sidebar", "breadcrumb",
            "copyright", "cookie", "subscribe", "read more", "learn more",
            "previous page", "next page", "start shipping",
        }
        # Common Chinese nav single-lines / short phrases
        is_chinese_nav = (
            stripped in {
                "文档", "备案", "控制台", "登录", "注册", "首页",
                "文档中心", "开发者中心", "企业版", "个人版",
                "立即注册", "立即登录", "免费使用", "开始使用",
                "产品", "解决方案", "定价", "关于", "帮助",
                "帮助中心", "API文档", "开放平台",
                # Additional Chinese nav terms (Coze sidebar/menu)
                "扣子", "创建", "主页", "项目开发", "资源库", "任务中心",
                "效果评测", "空间配置", "模板商店", "插件商店", "作品社区",
                "API管理", "目录", "取消固定", "大纲",
                "快速上手", "快速开始", "入门", "使用指南",
                "产品介绍", "核心特性", "功能介绍",
                "跳转到主要内容",
            }
        )
        if is_short_nav or is_chinese_nav:
            skip_count += 1
        else:
            break  # First non-nav line found
    if skip_count > 0 and skip_count < len(lines):
        text = "\n".join(lines[skip_count:])
    if len(text) <= max_len:
        return text if len(text) >= min_len else ""
    # Try to cut at a sentence boundary near max_len
    cutoff = text[:max_len]
    last_period = cutoff.rfind(".")
    last_newline = cutoff.rfind("\n")
    cut = max(last_period, last_newline)
    if cut > min_len:
        return text[:cut + 1].strip()
    return text[:max_len].rsplit(" ", 1)[0].strip() + "..."


# P0-Rebuild: Expanded nav noise patterns — catches documentation sidebar nav,
# Dify docs, Coze docs, GitHub readme nav, and 404 pages that were causing
# wrong schema classification and unusable evidence (relevance → near-zero).
NAVIGATION_NOISE = {
    "quick start", "getting started", "get started", "documentation", "docs",
    "forum", "changelog", "blog", "contact us", "contact ", "login", "sign up",
    "signup", "log in", "footer", "navigation", "header", "menu",
    "table of contents", "sidebar", "breadcrumb", "copyright", "cookie",
    "subscribe", "previous page", "next page", "read more", "learn more",
    "start shipping", "shipping powerful", "core dify", "dify building",
    "build powerful", "chat with", "ask anything", "explore",
    # Chinese nav terms
    "扣子", "创建", "主页", "项目开发", "资源库", "任务中心",
    "效果评测", "空间配置", "模板商店", "插件商店", "作品社区",
    "api管理", "文档中心", "目录", "取消固定", "大纲",
    "快速上手", "快速开始", "入门", "使用指南",
    "产品介绍", "核心特性", "功能介绍", "跳转到主要内容",
    # Dify docs specific nav items
    "request rate limit", "workspace", "overview", "model providers",
    "plugins", "tools", "manage apps", "manage members",
    "personal settings", "billing", "api extension",
    "tutorials", "workflow 101", "simple chatbot",
    "orchestration logic", "version control", "collaboration",
    "hotkeys", "use mcp tools", "self-host", "debug", "basic apps",
    "app toolkit", "publish", "web app", "mcp server", "api",
    "monitor", "dashboard", "logs", "annotation system", "integrations",
    "knowledge", "create", "manage", "test retrieval", "integrate in apps",
    # 404 page noise
    "page not found", "we couldn't find the page",
    "maybe you were looking for", "jump to", "return to",
    # GitHub repo nav noise
    "readme", "pull requests", "issues", "actions", "releases",
    "packages", "environments", "wiki", "security", "insights",
    "settings", "profile", "your repositories",
    # P1 (2026-06-22): Ad/branding noise from third-party ecosystem pages
    "try langsmith", "get a demo", "langsmith",
    "langgraph", "langchain",
    # P1 (2026-06-22): Product branding nav fragments
    "dive into dify", "dive into your",
    # P1 (2026-06-22): Pagination nav elements
    "next  i", "next  ⌘", "prev",
}
BUSINESS_KEYWORDS = [
    "pricing", "free tier", "free plan", "subscription", "credit", "cost", "fee",
    "workflow", "orchestration", "node", "pipeline", "automation", "rag",
    "retrieval", "knowledge base", "vector", "embedding", "chunk",
    "deployment", "docker", "kubernetes", "self-hosted", "enterprise",
    "sso", "rbac", "permission", "audit log", "security", "encryption",
    "integration", "api", "webhook", "plugin", "extension", "sdk",
    "model", "llm", "gpt", "claude", "gemini", "openai", "mistral",
    "agent", "bot", "chatbot", "copilot", "assistant", "automation",
    "team", "collaboration", "open source", "github", "license",
    "tutorial", "guide", "support", "database", "import", "export",
]


def _is_nav_noise(text):
    # P0-3: Use the centralized noise detection from evidence_evaluator
    return is_noise_evidence(text)


def _info_density(text):
    if not text:
        return 0.0
    alpha = sum(1 for c in text if c.isalpha())
    n = len(text)
    if n == 0:
        return 0.0
    return min(1.0, (alpha / n))


def _quality_score(text):
    if _is_nav_noise(text):
        return 0.0
    score = 0.3
    n = len(text)
    if 80 <= n <= 600:
        score += 0.2
    elif n > 600:
        score += 0.1
    elif n < 80:
        score -= 0.2
    score += _info_density(text) * 0.2
    t = text.lower()
    hits = sum(1 for kw in BUSINESS_KEYWORDS if kw in t)
    score += min(0.25, hits * 0.05)
    return max(0.0, min(1.0, score))


import re as _re


def _norm_dedup(text):
    t = text.lower()
    t = _re.sub(r"[^a-z0-9 ]", " ", t)
    return _re.sub(r" +", " ", t).strip()


def _clean_nav_lines(text: str) -> str:
    """Remove single-line navigation phrases from a chunk."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip single-line nav items
        stripped_lower = stripped.lower()
        if len(stripped) < 30:
            if any(nav in stripped_lower for nav in [
                "quick start", "getting started", "get started",
                "forum", "changelog", "docs", "documentation",
                "contact us", "contact", "login", "sign up", "signup",
                "footer", "navigation", "header", "menu",
                "table of contents", "sidebar", "breadcrumb",
                "copyright", "cookie", "subscribe",
                "read more", "learn more", "previous page", "next page",
                "start shipping", "core dify", "dify building",
                "build powerful", "ask anything", "explore",
                "self host", "concepts", "core concepts",
                # Chinese nav terms (Coze, FastGPT, Dify sidebar/menu)
                "扣子", "创建", "主页", "项目开发", "资源库", "任务中心",
                "效果评测", "空间配置", "模板商店", "插件商店", "作品社区",
                "api管理", "文档中心", "目录", "取消固定", "大纲",
                "快速上手", "快速开始", "入门", "使用指南",
                "产品介绍", "核心特性", "功能介绍",
                "跳转到主要内容",
            ]):
                continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _nav_keyword_count(text: str) -> int:
    """Count how many nav keywords appear in text."""
    t = text.lower()
    count = 0
    for nav in NAVIGATION_NOISE:
        if nav in t:
            count += 1
    return count


# P0-Rebuild: Revised content quality gate.
# A chunk must have meaningful substantive content to be worth extracting as evidence.
# Pure nav/404/directory-listing chunks are rejected even if they match schema keywords.
_CONTENT_QUALITY_KEYWORDS = [
    # Feature descriptions (substantive)
    "enable", "provides", "supports", "allows", "offers",
    "build", "create", "deploy", "configure", "manage", "monitor",
    "integrate", "connect", "automate", "orchestrate", "orchestrat",
    "manage", "orchestrat", "workflow", "agent",
    "knowledge base", "rag", "vector", "retrieval",
    "deploy", "self-hosted", "docker", "kubernetes",
    "pricing", "subscription", "free tier", "plan",
    "api", "webhook", "plugin", "extension",
    "model", "llm", "gpt", "claude",
    "enterprise", "sso", "rbac", "audit", "security",
    # Chinese substantive
    "支持", "提供", "功能", "特性", "编排", "工作流",
    "自动化", "智能体", "知识库", "检索", "向量",
    "部署", "私有化", "本地", "开源",
    "集成", "接口", "插件", "扩展",
    "安全", "合规", "审计", "权限",
    "模型", "大模型", "定价", "订阅", "免费",
    "企业", "团队", "协作",
]


def _has_substantive_content(text: str) -> bool:
    """Return True if text has meaningful analytical content beyond nav.</>
    
    P0-Rebuild: This is the critical gate that prevents nav/404/directory-list
    chunks from being extracted as evidence even when they match schema keywords.
    """
    t = text.lower()
    substantive_count = sum(1 for kw in _CONTENT_QUALITY_KEYWORDS if kw in t)
    # Need at least 2 substantive keywords to pass
    return substantive_count >= 2


def _short_nav_line_ratio(text: str) -> float:
    """Fraction of lines that are short (<30 chars) and look like nav items."""
    lines = text.split("\n")
    if not lines:
        return 0.0
    nav_lines = 0
    for line in lines:
        stripped = line.strip()
        if len(stripped) < 30:
            stripped_lower = stripped.lower()
            if any(nav in stripped_lower for nav in NAVIGATION_NOISE):
                nav_lines += 1
    return nav_lines / len(lines) if lines else 0.0



# ----------------------------------------------------------------
# P0-7: Module-level helper for ProcessPoolExecutor (must be picklable)
# ----------------------------------------------------------------
EVIDENCE_PER_DOC_TIMEOUT = 60  # seconds — per-document budget


def _process_single_doc(args: tuple) -> dict[str, Any]:
    """Process a single raw_document and return scored chunks + metadata.

    Runs in a subprocess via ProcessPoolExecutor.
    Returns a dict with extracted data (not yet EvidenceItem — final
    dedup happens in the main process to avoid cross-process dedup races).
    """
    doc, run_id = args
    try:
        doc_start = time.perf_counter()

        source_id = doc.get("source_id", "")
        product_id = doc.get("product_id", "")
        product_slug = doc.get("product_slug") or _slugify(product_id)
        snapshot_id = doc.get("snapshot_id", "")
        raw_text = doc.get("raw_text", "")
        url = doc.get("url", "")
        source_type = doc.get("source_type") or _infer_source_type(url) if url else "web_page"
        trust_tier = doc.get("trust_tier") or _infer_trust_tier(url, product_id)
        trust_boost = TRUST_BOOSTS.get(source_type, 0.0)
        now = datetime.now(timezone.utc).isoformat()

        if not raw_text:
            return {"status": "ok", "source_key": source_id, "product_slug": product_slug,
                    "source_type": source_type, "trust_tier": trust_tier, "trust_boost": trust_boost,
                    "url": url, "title": doc.get("title", "")[:200],
                    "scored_chunks": [], "elapsed": time.perf_counter() - doc_start}

        chunks = _split_sentences(raw_text)
        scored = []
        for chunk in chunks:
            if len(chunk) < 40:
                continue
            chunk = _clean_nav_lines(chunk)
            if not chunk or len(chunk) < 40:
                continue
            # P0-Rebuild: Raise threshold — the expanded NAVIGATION_NOISE set is larger
            # so a nav-heavy page will hit 5+ matches faster now.
            if _nav_keyword_count(chunk) > 5:
                continue
            nav_ratio = _short_nav_line_ratio(chunk)
            if nav_ratio > 0.4:
                continue
            # P0-Rebuild: Critical gate — reject nav/404/directory-list chunks
            # even if they match schema keywords. Only extract chunks with real content.
            # This prevents Dify docs sidebar nav (e.g. "Workflow & Chatflow / Overview
            # / Orchestration Logic / Nodes / Hotkeys") from being extracted as evidence
            # just because they contain the keyword "workflow".
            if not _has_substantive_content(chunk):
                continue
            sk, bc = _best_schema_key(chunk)
            q = _quality_score(chunk)
            scored.append({"chunk": chunk, "schema_key": sk, "base_conf": bc, "quality": q})

        scored.sort(key=lambda x: x["quality"], reverse=True)
        # Only keep top 8 chunks per doc (already sorted)
        top_chunks = scored[:8]

        elapsed = time.perf_counter() - doc_start
        return {
            "status": "ok",
            "source_key": source_id,
            "product_slug": product_slug,
            "snapshot_id": snapshot_id,
            "product_id": product_id,
            "source_type": source_type,
            "trust_tier": trust_tier,
            "trust_boost": trust_boost,
            "url": url,
            "title": doc.get("title", "")[:200],
            "scored_chunks": top_chunks,
            "elapsed": elapsed,
        }
    except Exception as exc:
        return {
            "status": "error",
            "source_key": doc.get("source_id", ""),
            "product_slug": doc.get("product_slug", ""),
            "error": str(exc),
            "elapsed": 0.0,
        }


def _process_fallback_doc(args: tuple) -> dict[str, Any]:
    """Process a single doc for fallback evidence (products with zero evidence).

    Runs in subprocess. Returns chunks keyed by product_slug for scoring.
    """
    doc, run_id = args
    try:
        product_id = doc.get("product_id", "")
        product_slug = doc.get("product_slug") or _slugify(product_id)
        raw_text = doc.get("raw_text", "")
        if not raw_text:
            return {"status": "ok", "product_slug": product_slug, "chunks": []}

        chunks = _split_sentences(raw_text)
        result_chunks = []
        for chunk in chunks:
            if len(chunk) < 50:
                continue
            chunk = _clean_nav_lines(chunk)
            if not chunk or len(chunk) < 50:
                continue
            sk, bc = _best_schema_key(chunk)
            result_chunks.append({"chunk": chunk, "schema_key": sk, "base_conf": bc})
        return {"status": "ok", "product_slug": product_slug, "chunks": result_chunks}
    except Exception as exc:
        return {"status": "error", "product_slug": doc.get("product_slug", ""), "error": str(exc), "chunks": []}


class EvidenceExtractor:
    def extract_evidence(
        self, raw_documents: list[dict[str, Any]], run_id: str
    ) -> tuple[list[dict[str, Any]], dict]:
        """
        Extract EvidenceItems with Product Coverage Guard.
        Returns (evidence_items, product_coverage).

        P0-7: Uses ProcessPoolExecutor for parallel per-document processing,
        bypassing GIL for CPU-bound keyword matching. Each document has a 60s
        timeout to prevent slow documents from blocking the batch.
        """
        overall_start = time.perf_counter()
        evidence_items: list[dict[str, Any]] = []
        seen: set[str] = set()  # cross-doc dedup (done in main process — thread-safe)
        now = datetime.now(timezone.utc).isoformat()
        source_evidence_count: dict[str, int] = {}
        product_evidence: dict[str, list[dict]] = {}

        if not raw_documents:
            return [], {}

        # ── Phase 1: Parallel per-document extraction ───────────────────────────
        num_workers = min(len(raw_documents), os.cpu_count() or 4, 8)
        doc_start = time.perf_counter()
        slow_count = 0

        ctx = get_context("fork")  # faster than spawn for this use case
        with ProcessPoolExecutor(max_workers=num_workers, mp_context=ctx) as pool:
            args_list = [(doc, run_id) for doc in raw_documents]
            futures = {pool.submit(_process_single_doc, args): args for args in args_list}

            for future in as_completed(futures, timeout=EVIDENCE_PER_DOC_TIMEOUT * 2):
                try:
                    result = future.result(timeout=EVIDENCE_PER_DOC_TIMEOUT)
                except Exception as exc:
                    args = futures[future]
                    logger.warning("EvidenceExtractor: doc failed subprocess: %s", exc)
                    result = {"status": "error", "source_key": args[0].get("source_id", ""),
                              "product_slug": args[0].get("product_slug", ""), "error": str(exc)}

                if result.get("status") != "ok":
                    continue

                source_key = result["source_key"]
                if source_evidence_count.get(source_key, 0) >= 8:
                    continue

                trust_boost = result.get("trust_boost", 0.0)
                for item in result["scored_chunks"]:
                    chunk = item["chunk"]
                    schema_key = item["schema_key"]
                    base_conf = item["base_conf"]
                    quality = item["quality"]

                    if source_evidence_count.get(source_key, 0) >= 8:
                        break

                    chunk_hash = str(hash(chunk))[:16]
                    dup_key = f"{source_key}_{schema_key}_{chunk_hash}"
                    if dup_key in seen:
                        continue
                    seen.add(dup_key)

                    snippet = _make_snippet(chunk)
                    if not snippet or len(snippet) < 50:
                        continue
                    if is_noise_evidence(snippet):
                        continue

                    evidence_id = f"ev_{uuid.uuid4().hex[:16]}"
                    confidence = min(base_conf + trust_boost + quality * 0.05, 0.95)

                    pii_masked = False
                    try:
                        snippet, was_modified = sanitize_evidence_snippet(snippet)
                        pii_masked = was_modified
                    except Exception as exc:
                        logger.warning("Snippet sanitization failed for %s: %s", evidence_id, exc)

                    ev_item = {
                        "evidence_id": evidence_id,
                        "run_id": run_id,
                        "source_id": source_key,
                        "snapshot_id": result.get("snapshot_id", ""),
                        "product_id": result.get("product_id", ""),
                        "product_slug": result["product_slug"],
                        "schema_key": schema_key,
                        "snippet": snippet,
                        "start_offset": 0,
                        "end_offset": len(snippet),
                        "section_title": result.get("title", "")[:200],
                        "confidence": round(confidence, 3),
                        "quality_score": round(quality, 3),
                        "pii_masked": pii_masked,
                        "detected_pii_types": [],
                        "evidence_type": "text",
                        "created_at": now,
                        "source_type": result.get("source_type", "web_page"),
                        "trust_tier": result.get("trust_tier", "medium"),
                    }
                    evidence_items.append(ev_item)
                    source_evidence_count[source_key] = source_evidence_count.get(source_key, 0) + 1
                    product_slug = result["product_slug"]
                    if product_slug not in product_evidence:
                        product_evidence[product_slug] = []
                    product_evidence[product_slug].append(ev_item)

                if result.get("elapsed", 0) > 5.0:
                    slow_count += 1
                    logger.warning(
                        "EvidenceExtractor: SLOW DOC (%s) took %.1fs for %d chunks",
                        result.get("url") or result.get("product_slug", ""),
                        result.get("elapsed", 0), len(result.get("scored_chunks", [])),
                    )

        doc_elapsed = time.perf_counter() - doc_start
        logger.info(
            "EvidenceExtractor: parallel processed %d docs in %.1fs (%d workers), "
            "produced %d items, %d slow docs",
            len(raw_documents), doc_elapsed, num_workers, len(evidence_items), slow_count,
        )

        # ── Phase 2: Parallel fallback extraction for products with zero evidence ──
        fallback_start = time.perf_counter()
        MIN_EVIDENCE = 6
        products_seen = set(product_evidence.keys())
        all_chunks_by_product: dict[str, list] = {}

        # Only process docs for products that have no evidence yet
        fallback_docs = [
            doc for doc in raw_documents
            if (doc.get("product_slug") or _slugify(doc.get("product_id", ""))) not in products_seen
        ]

        if fallback_docs:
            with ProcessPoolExecutor(max_workers=num_workers, mp_context=ctx) as pool:
                args_list = [(doc, run_id) for doc in fallback_docs]
                for future in as_completed(futures, timeout=EVIDENCE_PER_DOC_TIMEOUT * 2):
                    try:
                        result = future.result(timeout=EVIDENCE_PER_DOC_TIMEOUT)
                    except Exception as exc:
                        logger.warning("EvidenceExtractor fallback: doc failed subprocess: %s", exc)
                        continue
                    if result.get("status") != "ok":
                        continue
                    for c in result.get("chunks", []):
                        all_chunks_by_product.setdefault(result["product_slug"], []).append(c)

        for product_slug, fallback_chunks in all_chunks_by_product.items():
            current = product_evidence.get(product_slug, [])
            if len(current) >= MIN_EVIDENCE:
                continue
            scored = [(c["chunk"], c["schema_key"], c["base_conf"]) for c in fallback_chunks]
            scored.sort(key=lambda x: _quality_score(x[0]), reverse=True)
            for chunk, schema_key, base_conf in scored:
                if len(product_evidence.get(product_slug, [])) >= MIN_EVIDENCE:
                    break
                snippet = _make_snippet(chunk)
                if not snippet or len(snippet) < 50:
                    continue
                if _is_nav_noise(snippet):
                    continue
                evidence_id = f"ev_{uuid.uuid4().hex[:16]}"
                confidence = min(base_conf + 0.55, 0.65)
                pii_masked = False
                detected_types = []
                try:
                    snippet, _ = sanitize_evidence_snippet(snippet)
                    pii_masked = True
                except:
                    pass
                ev_item = {
                    "evidence_id": evidence_id,
                    "run_id": run_id,
                    "source_id": "",
                    "snapshot_id": "",
                    "product_id": "",
                    "product_slug": product_slug,
                    "schema_key": schema_key,
                    "snippet": snippet,
                    "start_offset": 0,
                    "end_offset": len(snippet),
                    "section_title": "[Fallback - review required]",
                    "confidence": round(confidence, 3),
                    "quality_score": 0.55,
                    "pii_masked": pii_masked,
                    "detected_pii_types": detected_types,
                    "evidence_type": "fallback_text",
                    "created_at": now,
                    "source_type": "web_page",
                    "trust_tier": "low",
                }
                evidence_items.append(ev_item)
                if product_slug not in product_evidence:
                    product_evidence[product_slug] = []
                product_evidence[product_slug].append(ev_item)

        fallback_elapsed = time.perf_counter() - fallback_start
        logger.info(
            "EvidenceExtractor: fallback produced %d additional items in %.1fs",
            len(evidence_items), fallback_elapsed,
        )

        # --- Build product_coverage metrics ---
        all_products = set(k for doc in raw_documents for k in [doc.get("product_slug") or _slugify(doc.get("product_id", ""))])
        product_coverage = {}
        for slug in all_products:
            items = product_evidence.get(slug, [])
            ev_count = len(items)
            sources = len(set(e["source_id"] for e in items if e["source_id"]))
            if ev_count == 0:
                status = "missing"
            elif ev_count < 2:
                status = "weak"
            else:
                status = "sufficient"
            product_coverage[slug] = {
                "product_slug": slug,
                "source_count": sources,
                "evidence_count": ev_count,
                "fact_count": 0,
                "coverage_status": status,
            }

        overall_elapsed = time.perf_counter() - overall_start
        logger.info(
            "EvidenceExtractor: TOTAL completed in %.1fs. %d items from %d products. Coverage: %s",
            overall_elapsed, len(evidence_items), len(product_evidence),
            {k: v["coverage_status"] for k, v in product_coverage.items()},
        )
        return evidence_items, product_coverage
