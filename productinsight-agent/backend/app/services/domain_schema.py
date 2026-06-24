"""
Domain Schema Planner - Generates domain-specific analysis schemas for competitive analysis.

This module enables the system to work across ANY domain/product category, not just AI Agent platforms.
"""

from __future__ import annotations

import re
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Domain Schema Definitions
# ============================================================================

# Pre-defined domain schemas for common categories
DOMAIN_SCHEMAS = {
    "ai_agent_platform": {
        "name": "AI Agent Platform",
        "entities": ["Agent Platform", "LLM Application", "Workflow Orchestration Tool"],
        # P0-1: Strictly aligned with 3 Schema keys per 开题材料要求
        "comparison_dimensions": [
            # Schema 1: function_tree
            {
                "dimension": "workflow_orchestration",
                "chinese": "Workflow 编排",
                "schema_key": "function_tree",
                "business_question": "工作流编排能力如何？",
                "metrics": ["可视化程度", "自定义灵活度", "复杂度上限"],
            },
            {
                "dimension": "rag_knowledge",
                "chinese": "RAG / 知识库",
                "schema_key": "function_tree",
                "business_question": "知识管理与 RAG 能力如何？",
                "metrics": ["知识库易用性", "权限管控", "检索质量"],
            },
            {
                "dimension": "model_support",
                "chinese": "模型兼容",
                "schema_key": "function_tree",
                "business_question": "支持哪些大模型？",
                "metrics": ["模型数量", "API兼容性", "本地部署"],
            },
            {
                "dimension": "multi_agent",
                "chinese": "多 Agent 协作",
                "schema_key": "function_tree",
                "business_question": "多 Agent 协作能力如何？",
                "metrics": ["Agent 数量", "协作模式", "状态管理"],
            },
            # Schema 2: pricing_model
            {
                "dimension": "free_tier",
                "chinese": "免费套餐",
                "schema_key": "pricing_model",
                "business_question": "是否有免费套餐？限制是什么？",
                "metrics": ["使用限制", "API 配额", "功能限制"],
            },
            {
                "dimension": "paid_plans",
                "chinese": "付费套餐",
                "schema_key": "pricing_model",
                "business_question": "付费套餐如何定价？",
                "metrics": ["价格区间", "计费模式", "增值服务"],
            },
            {
                "dimension": "enterprise_pricing",
                "chinese": "企业定价",
                "schema_key": "pricing_model",
                "business_question": "企业版如何定价？",
                "metrics": ["最低消费", "定制化报价", "SLA 支持"],
            },
            {
                "dimension": "trial_policy",
                "chinese": "试用政策",
                "schema_key": "pricing_model",
                "business_question": "试用期和退款政策如何？",
                "metrics": ["试用期时长", "退款条件", "升级路径"],
            },
            # Schema 3: user_persona
            {
                "dimension": "non_technical_business",
                "chinese": "非技术业务团队",
                "schema_key": "user_persona",
                "business_question": "非技术业务人员能否快速上手？",
                "metrics": ["上手难度", "学习曲线", "模板丰富度"],
            },
            {
                "dimension": "low_code_developers",
                "chinese": "低代码开发者",
                "schema_key": "user_persona",
                "business_question": "低代码开发者能否高效使用？",
                "metrics": ["扩展能力", "API 丰富度", "调试体验"],
            },
            {
                "dimension": "professional_developers",
                "chinese": "专业开发团队",
                "schema_key": "user_persona",
                "business_question": "专业开发团队能否深度定制？",
                "metrics": ["代码控制", "版本管理", "集成能力"],
            },
            {
                "dimension": "ai_engineers",
                "chinese": "AI 工程师",
                "schema_key": "user_persona",
                "business_question": "AI 工程师能否实现高级编排？",
                "metrics": ["LLM 自由度", "Agent 框架", "生产级部署"],
            },
        ],
        "evidence_sources": [
            "官网产品页",
            "官方文档",
            "GitHub 仓库",
            "定价页面",
        ],
        "seed_urls": [
            "https://docs.dify.ai",
            "https://dify.ai/pricing",
            "https://python.langchain.com",
            "https://www.coze.com/docs",
            "https://github.com/langchain-ai/langgraph",
        ],
        "product_categories": {
            "primary": ["Dify", "LangChain/LangGraph", "Coze", "AutoGen", "CrewAI"],
            "benchmark": ["Flowise", "n8n", "Zapier"],
            "emerging": ["AgentFlow", "Relevance AI"],
        },
    },
    "coffee_chain": {
        "name": "Coffee Chain",
        "entities": ["Coffee Shop", "Cafe Brand", "Beverage Retail"],
        "comparison_dimensions": [
            {
                "dimension": "store_coverage",
                "chinese": "门店覆盖",
                "business_question": "目标区域的门店密度和扩张能力如何？",
                "metrics": ["门店数量", "地理覆盖", "扩张速度"],
            },
            {
                "dimension": "pricing",
                "chinese": "定价策略",
                "business_question": "客单价区间是否匹配目标用户？",
                "metrics": ["人均消费", "产品线价格带", "促销活动"],
            },
            {
                "dimension": "product_menu",
                "chinese": "产品菜单",
                "business_question": "产品差异化程度和创新能力如何？",
                "metrics": ["SKU数量", "新品频率", "爆款产品"],
            },
            {
                "dimension": "membership",
                "chinese": "会员体系",
                "business_question": "会员运营能力和用户粘性如何？",
                "metrics": ["会员数量", "积分体系", "复购率"],
            },
            {
                "dimension": "delivery",
                "chinese": "外卖能力",
                "business_question": "线上获客和配送能力如何？",
                "metrics": ["外卖平台接入", "配送范围", "配送效率"],
            },
            {
                "dimension": "brand_positioning",
                "chinese": "品牌定位",
                "business_question": "品牌调性是否清晰差异化？",
                "metrics": ["目标客群", "品牌调性", "社交声量"],
            },
            {
                "dimension": "supply_chain",
                "chinese": "供应链",
                "business_question": "成本控制和品质稳定性如何？",
                "metrics": ["原料来源", "品控体系", "成本结构"],
            },
            {
                "dimension": "坪效",
                "chinese": "坪效",
                "business_question": "门店盈利能力如何？",
                "metrics": ["日均客流", "客单价", "租金占比"],
            },
        ],
        "evidence_sources": [
            "官网/小程序",
            "大众点评",
            "美团/饿了么",
            "财报",
            "社交媒体",
            "实地调研",
        ],
        "seed_urls": [
            "https://www.luckincoffeecn.com",
            "https://www.starbucks.com.cn",
            "https://www.manorshop.com",
            "https://investor.luckincoffee.com",
        ],
        "product_categories": {
            "primary": ["瑞幸", "星巴克", "库迪", "Manner"],
            "benchmark": ["Tim Hortons", "M Stand"],
            "emerging": ["霸王茶姬", "茶颜悦色"],
        },
    },
    "ev_automobile": {
        "name": "Electric Vehicle",
        "entities": ["EV Manufacturer", "Auto Brand", "Smart Car"],
        "comparison_dimensions": [
            {
                "dimension": "pricing",
                "chinese": "价格区间",
                "business_question": "价格定位是否匹配目标市场？",
                "metrics": ["售价区间", "性价比", "金融方案"],
            },
            {
                "dimension": "range",
                "chinese": "续航",
                "business_question": "日常使用是否够用？",
                "metrics": ["CLTC续航", "实际续航折扣", "电池衰减"],
            },
            {
                "dimension": "autonomous_driving",
                "chinese": "智驾能力",
                "business_question": "自动驾驶能力处于什么水平？",
                "metrics": ["传感器配置", "NOA覆盖", "实际体验"],
            },
            {
                "dimension": "charging",
                "chinese": "补能体系",
                "business_question": "充电便利性如何？",
                "metrics": ["自建充电桩", "充电网络覆盖", "换电能力"],
            },
            {
                "dimension": "space_comfort",
                "chinese": "空间舒适性",
                "business_question": "乘坐体验是否舒适？",
                "metrics": ["车身尺寸", "座椅舒适度", "噪音控制"],
            },
            {
                "dimension": "safety",
                "chinese": "安全性",
                "business_question": "安全评级和主动安全如何？",
                "metrics": ["碰撞测试", "主动安全配置", "电池安全"],
            },
            {
                "dimension": "brand",
                "chinese": "品牌认知",
                "business_question": "品牌口碑和溢价能力如何？",
                "metrics": ["品牌调性", "用户口碑", "保值率"],
            },
            {
                "dimension": "after_sales",
                "chinese": "售后服务",
                "business_question": "售后网络和服务质量如何？",
                "metrics": ["门店覆盖", "维保费用", "用户满意度"],
            },
        ],
        "evidence_sources": [
            "官网配置表",
            "懂车帝/汽车之家",
            "车主口碑",
            "碰撞测试结果",
            "媒体报道",
            "财报",
        ],
        "seed_urls": [
            "https://www.tesla.cn",
            "https://www.byd.com",
            "https://www.xiaomiev.com",
            "https://www.nio.cn",
            "https://www.xpeng.com",
        ],
        "product_categories": {
            "primary": ["特斯拉", "比亚迪", "蔚来", "小鹏", "理想"],
            "benchmark": ["小米汽车", "问界", "极氪"],
            "emerging": ["小米", "零跑", "哪吒"],
        },
    },
    "hr_saas": {
        "name": "HR SaaS",
        "entities": ["HR Software", "HCM System", "People Management Tool"],
        "comparison_dimensions": [
            {
                "dimension": "recruitment",
                "chinese": "招聘管理",
                "business_question": "招聘流程数字化能力如何？",
                "metrics": ["渠道整合", "流程自动化", "候选人管理"],
            },
            {
                "dimension": "employee_profile",
                "chinese": "员工档案",
                "business_question": "员工信息管理是否高效？",
                "metrics": ["信息完整度", "合同管理", "在职管理"],
            },
            {
                "dimension": "compensation",
                "chinese": "薪酬绩效",
                "business_question": "薪酬核算和绩效考核是否智能？",
                "metrics": ["算薪自动化", "绩效考核", "个税处理"],
            },
            {
                "dimension": "org_structure",
                "chinese": "组织架构",
                "business_question": "组织管理是否灵活？",
                "metrics": ["架构调整", "汇报关系", "部门管理"],
            },
            {
                "dimension": "approval_flow",
                "chinese": "审批流",
                "business_question": "审批效率如何提升？",
                "metrics": ["自定义能力", "移动端体验", "集成能力"],
            },
            {
                "dimension": "permission_compliance",
                "chinese": "权限合规",
                "business_question": "数据安全和合规性如何？",
                "metrics": ["权限体系", "审计日志", "合规认证"],
            },
            {
                "dimension": "integration",
                "chinese": "系统集成",
                "business_question": "与现有系统的集成能力如何？",
                "metrics": ["API开放度", "生态应用", "ERP集成"],
            },
            {
                "dimension": "pricing",
                "chinese": "定价",
                "business_question": "性价比如何？",
                "metrics": ["按人头定价", "实施费用", "年费结构"],
            },
        ],
        "evidence_sources": [
            "官网产品页",
            "产品文档",
            "客户案例",
            "G2/Capterra评价",
            "定价页面",
            "行业报告",
        ],
        "seed_urls": [
            "https://www.beisen.com",
            "https://www.xinrenxinshi.com",
            "https://www.workday.com",
            "https://www.mokahr.com",
        ],
        "product_categories": {
            "primary": ["Workday", "SAP SuccessFactors", "北森", "薪人薪事"],
            "benchmark": ["Moka", "i人事", "2号人事部"],
            "emerging": ["飞书人事", "钉钉人事", "企业微信HR"],
        },
    },
    "productivity_app": {
        "name": "Productivity App / Knowledge Management",
        "entities": ["Knowledge Base", "Collaboration Tool", "Workspace"],
        "comparison_dimensions": [
            {
                "dimension": "knowledge_structure",
                "chinese": "知识结构",
                "business_question": "知识组织能力如何？",
                "metrics": ["页面类型", "双向链接", "标签体系"],
            },
            {
                "dimension": "collaboration",
                "chinese": "协作体验",
                "business_question": "团队协作效率如何？",
                "metrics": ["实时协同", "评论讨论", "版本管理"],
            },
            {
                "dimension": "permission",
                "chinese": "权限治理",
                "business_question": "访问控制是否精细？",
                "metrics": ["页面权限", "团队空间", "公开范围"],
            },
            {
                "dimension": "template_ecosystem",
                "chinese": "模板生态",
                "business_question": "模板是否丰富易用？",
                "metrics": ["模板数量", "社区模板", "自定义模板"],
            },
            {
                "dimension": "integration",
                "chinese": "企业集成",
                "business_question": "与现有工具的集成如何？",
                "metrics": ["官方集成", "API能力", "WebClipper"],
            },
            {
                "dimension": "ai_search",
                "chinese": "AI搜索",
                "business_question": "知识检索能力如何？",
                "metrics": ["语义搜索", "AI助手", "答案生成"],
            },
            {
                "dimension": "pricing",
                "chinese": "定价",
                "business_question": "成本是否合理？",
                "metrics": ["免费额度", "团队版价格", "企业版价格"],
            },
            {
                "dimension": "migration",
                "chinese": "迁移成本",
                "business_question": "切换成本有多高？",
                "metrics": ["导入能力", "数据导出", "API兼容"],
            },
        ],
        "evidence_sources": [
            "官网产品页",
            "官方文档",
            "YouTube教程",
            "G2评价",
            "Twitter/Reddit讨论",
            "客户案例",
        ],
        "seed_urls": [
            "https://www.notion.so",
            "https://www.notion.so/pricing",
            "https://www.atlassian.com/software/confluence",
            "https://coda.io",
            "https://slite.com",
        ],
        "product_categories": {
            "primary": ["Notion", "Confluence", "Coda", "飞书知识库"],
            "benchmark": ["Obsidian", "Roam Research", "Logseq"],
            "emerging": ["Slite", "Craft", "Anytype"],
        },
    },
}


# ============================================================================
# Report Type Definitions
# ============================================================================

REPORT_TYPES = {
    "product_selection": {
        "name": "产品选型",
        "description": "企业采购选型评估",
        "focus": "适合谁、买哪个、风险是什么",
        "key_sections": ["Executive Summary", "Competitor Profiles", "Comparison Matrix", "Pricing Analysis", "Scenario Recommendations"],
    },
    "market_landscape": {
        "name": "市场格局",
        "description": "进入新市场前的格局分析",
        "focus": "市场格局、玩家分层、机会空间",
        "key_sections": ["Market Overview", "Player Segmentation", "Trend Analysis", "Opportunity Assessment"],
    },
    "product_strategy": {
        "name": "产品策略",
        "description": "自研产品的竞品参照",
        "focus": "学谁、避开什么、差异化机会",
        "key_sections": ["Competitor Analysis", "Feature Comparison", "Gap Analysis", "Strategic Recommendations"],
    },
    "sales_battlecard": {
        "name": "销售对抗卡",
        "description": "销售团队对抗竞品",
        "focus": "我方优势、竞品弱点、话术",
        "key_sections": ["Competitive Advantages", "Competitor Weaknesses", "Talk Tracks", "Objection Handling"],
    },
    "procurement_due_diligence": {
        "name": "采购尽调",
        "description": "大企业采购前的尽职调查",
        "focus": "SLA、合规、安全、TCO、实施风险",
        "key_sections": ["Vendor Assessment", "Security & Compliance", "SLA Analysis", "TCO Calculation", "Risk Assessment"],
    },
}


# ============================================================================
# Evidence Source Mapping
# ============================================================================

DOMAIN_EVIDENCE_SOURCES = {
    "ai_agent_platform": {
        "primary": ["官网产品页", "官方文档", "GitHub", "定价页面"],
        "secondary": ["社区论坛", "YouTube教程", "客户案例"],
        "verification": ["技术博客", "行业报告", "第三方测评"],
    },
    "coffee_chain": {
        "primary": ["大众点评", "美团/饿了么", "官网"],
        "secondary": ["小红书", "微博", "财报"],
        "verification": ["实地调研", "用户访谈", "行业报告"],
    },
    "ev_automobile": {
        "primary": ["官网配置表", "懂车帝", "汽车之家"],
        "secondary": ["车主口碑", "媒体报道", "投诉平台"],
        "verification": ["碰撞测试", "实测视频", "财报"],
    },
    "hr_saas": {
        "primary": ["官网产品页", "定价页面", "产品文档"],
        "secondary": ["G2/Capterra", "客户案例", "演示视频"],
        "verification": ["行业报告", "竞品对比文章", "用户评价"],
    },
    "productivity_app": {
        "primary": ["官网", "官方文档", "YouTube"],
        "secondary": ["G2评价", "Reddit/Product Hunt", "Twitter"],
        "verification": ["深度测评", "对比文章", "客户案例"],
    },
    "general": {
        "primary": ["官网", "官方文档", "新闻稿"],
        "secondary": ["媒体报道", "行业分析", "社交媒体"],
        "verification": ["第三方测评", "用户评价", "财报/招股书"],
    },
}


# ============================================================================
# Main Functions
# ============================================================================

def detect_domain(query: str, products: list[str] | None = None) -> str:
    """Detect the domain category from user query and products.
    
    Args:
        query: User's original query text
        products: List of products mentioned
        
    Returns:
        Domain identifier (e.g., "ai_agent_platform", "coffee_chain", etc.)
    """
    query_lower = query.lower()
    # Handle both string list and dict list formats
    products_list = products or []
    products_lower = []
    for p in products_list:
        if isinstance(p, str):
            products_lower.append(p.lower())
        elif isinstance(p, dict):
            name = p.get("product_name", "")
            if name:
                products_lower.append(name.lower())
    all_text = " ".join([query_lower] + products_lower)
    
    # Domain keywords mapping
    domain_keywords = {
        "ai_agent_platform": [
            "ai agent", "agent platform", "llm应用", "工作流编排", 
            "dify", "langchain", "langgraph", "coze", "autogen", "crewai",
            "flowise", "n8n", "rag", "知识库", "agent开发", "智能体"
        ],
        "coffee_chain": [
            "咖啡", "coffee", "瑞幸", "星巴克", "库迪", "manner",
            "奶茶", "茶饮", "饮品店", "cafe", "饮品"
        ],
        "ev_automobile": [
            "新能源", "电动车", "ev", "电动汽车", "智能汽车", 
            "特斯拉", "比亚迪", "蔚来", "小鹏", "理想", "小米汽车",
            "autopilot", "智驾", "续航", "充电"
        ],
        "hr_saas": [
            "hr", "人力资源", "招聘", "薪酬", "绩效", 
            "人事", "员工管理", "组织架构", "北森", "workday",
            "人事系统", "人力资源系统", "HCM"
        ],
        "productivity_app": [
            "知识库", "notion", "confluence", "coda", "飞书",
            "协作", "文档", "workspace", "笔记", "知识管理",
            "文档管理", "协同办公", "第二大脑"
        ],
    }
    
    # Score each domain
    scores = {}
    for domain, keywords in domain_keywords.items():
        score = sum(1 for kw in keywords if kw in all_text)
        scores[domain] = score
    
    # Return best match if score > 0
    best_domain = max(scores, key=scores.get)
    if scores[best_domain] > 0:
        return best_domain
    
    # Default to general competitive analysis
    return "general"


def detect_report_type(query: str) -> str:
    """Detect the report type from user query.
    
    Args:
        query: User's original query text
        
    Returns:
        Report type identifier
    """
    query_lower = query.lower()
    
    # Report type keywords (ordered by specificity - more specific first)
    type_keywords = {
        "procurement_due_diligence": ["尽调", "due diligence", "尽职", "供应商评估", "风险评估"],
        "market_landscape": ["市场格局", "进入市场", "市场规模", " landscape", "行业分析"],
        "product_strategy": ["产品策略", "产品规划", "自研", "竞品分析", "strategy"],
        "product_selection": ["选型", "采购", "买哪个", "哪个好", "comparison", "evaluate", "选", "竞争优势", "分析"],
        "sales_battlecard": ["销售对抗", "battlecard", "销售话术", "销售用"],
    }
    
    # Score with weight (higher weight for more specific types)
    scores = {}
    for rtype, keywords in type_keywords.items():
        weight = {"procurement_due_diligence": 3, "market_landscape": 2, "product_strategy": 2, 
                  "product_selection": 1, "sales_battlecard": 2}.get(rtype, 1)
        score = sum(weight for kw in keywords if kw in query_lower)
        scores[rtype] = score
    
    best_type = max(scores, key=scores.get)
    if scores[best_type] > 0:
        return best_type
    
    # Default to product selection
    return "product_selection"


def generate_domain_schema(
    domain: str,
    products: list[str],
    query: str,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Generate or retrieve domain-specific analysis schema.
    
    Args:
        domain: Detected domain identifier
        products: List of products to analyze
        query: User's original query
        llm_client: Optional LLM client for custom schema generation
        
    Returns:
        Domain schema dict with entities, dimensions, evidence_sources
    """
    # If domain is known, use predefined schema
    if domain in DOMAIN_SCHEMAS:
        schema = DOMAIN_SCHEMAS[domain].copy()
        schema["source"] = "predefined"
        return schema
    
    # For unknown domains, try to generate with LLM or return general
    if llm_client:
        try:
            return _generate_custom_schema_with_llm(llm_client, products, query)
        except Exception as e:
            logger.warning(f"LLM schema generation failed: {e}, using general schema")
    
    # Fallback to general schema
    return {
        "name": "General Competitive Analysis",
        "entities": ["Product", "Service", "Brand"],
        "comparison_dimensions": [
            {"dimension": "pricing", "chinese": "定价", "business_question": "价格竞争力如何？"},
            {"dimension": "features", "chinese": "功能", "business_question": "核心功能有哪些差异？"},
            {"dimension": "user_experience", "chinese": "用户体验", "business_question": "使用体验如何？"},
            {"dimension": "market_position", "chinese": "市场定位", "business_question": "目标用户群体是什么？"},
            {"dimension": "brand", "chinese": "品牌", "business_question": "品牌认知度如何？"},
        ],
        "evidence_sources": ["官网", "用户评价", "媒体报道", "行业报告"],
        "seed_urls": [],
        "product_categories": {"primary": products, "benchmark": [], "emerging": []},
        "source": "general",
    }


def _generate_custom_schema_with_llm(
    llm_client: Any,
    products: list[str],
    query: str,
) -> dict[str, Any]:
    """Generate custom schema for unknown domain using LLM."""
    prompt = f"""Given the following competitive analysis request, generate a domain-specific analysis schema.

Products: {', '.join(products)}
Query: {query}

Generate a JSON schema with:
1. "name": Domain name
2. "entities": What kind of products/entities are being compared
3. "comparison_dimensions": Array of dimensions, each with:
   - dimension: English identifier
   - chinese: Chinese name
   - business_question: What business question does this dimension answer?
   - metrics: Sub-metrics to consider
4. "evidence_sources": Where to find evidence for this domain
5. "product_categories": Suggested primary/benchmark/emerging products

Return ONLY valid JSON."""

    response = llm_client.chat_text(
        messages=[
            {"role": "system", "content": "You are a competitive analysis expert. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    
    # Extract JSON from response
    json_match = re.search(r'\{[\s\S]*\}', response)
    if json_match:
        schema = json.loads(json_match.group())
        schema["source"] = "llm_generated"
        return schema
    
    raise ValueError("Failed to generate schema with LLM")


def get_evidence_plan(domain: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Get evidence collection plan based on domain and schema.
    
    Args:
        domain: Domain identifier
        schema: Domain schema dict
        
    Returns:
        Evidence plan with source categories and priorities
    """
    # Get domain-specific sources
    if domain in DOMAIN_EVIDENCE_SOURCES:
        sources = DOMAIN_EVIDENCE_SOURCES[domain]
    else:
        sources = DOMAIN_EVIDENCE_SOURCES["general"]
    
    # Build evidence plan
    plan = {
        "domain": domain,
        "source_plan": {
            "primary_sources": sources["primary"],
            "secondary_sources": sources["secondary"],
            "verification_sources": sources["verification"],
        },
        "collection_strategy": _get_collection_strategy(domain),
        "quality_checklist": _get_quality_checklist(domain),
    }
    
    return plan


def _get_collection_strategy(domain: str) -> str:
    """Get domain-specific collection strategy description."""
    strategies = {
        "ai_agent_platform": "优先抓取官方文档和GitHub，然后社区论坛和客户案例。定价信息需要多源核实。",
        "coffee_chain": "优先抓取大众点评和美团，然后官网和社交媒体。财报用于财务分析。",
        "ev_automobile": "优先抓取懂车帝配置表，然后车主口碑和实测视频。碰撞测试结果作为安全验证。",
        "hr_saas": "优先抓取官网产品页和定价页，然后G2评价和客户案例。",
        "productivity_app": "优先抓取官网和YouTube教程，然后G2和Product Hunt评价。",
        "general": "优先抓取官网和官方文档，然后媒体报道和行业分析。",
    }
    return strategies.get(domain, strategies["general"])


def _get_quality_checklist(domain: str) -> list[str]:
    """Get domain-specific quality checklist for collected evidence."""
    base_checklist = [
        "信息是否为最新版本",
        "来源是否权威可靠",
        "是否存在利益冲突",
        "是否有其他来源交叉验证",
    ]
    
    domain_specific = {
        "ai_agent_platform": base_checklist + [
            "功能描述是否与实际产品一致",
            "定价是否为最新",
        ],
        "coffee_chain": base_checklist + [
            "门店信息是否最新",
            "价格是否为门店实际价格",
        ],
        "ev_automobile": base_checklist + [
            "配置表是否为最新版本",
            "续航数据是否为官方还是实测",
        ],
        "hr_saas": base_checklist + [
            "功能描述是否为当前版本",
            "定价是否为最新报价",
        ],
    }
    
    return domain_specific.get(domain, base_checklist)


def build_competitor_discovery_prompt(
    domain: str,
    schema: dict[str, Any],
    user_products: list[str] | None = None,
) -> str:
    """Build prompt for competitor discovery.
    
    Args:
        domain: Domain identifier
        schema: Domain schema
        user_products: Optional user-specified products
        
    Returns:
        Prompt string for competitor discovery
    """
    schema_name = schema.get("name", "该领域")
    
    prompt = f"""你是一个竞品发现专家。请为{schema_name}领域的竞品分析提供竞品候选列表。

"""
    
    if user_products:
        prompt += f"""用户已指定产品：{', '.join(user_products)}

请验证这些产品是否适合作为该领域的主要竞品，并建议是否需要补充其他竞品。
"""

    prompt += f"""
根据{schema_name}的分析需求，请按以下分类提供竞品候选：

1. **直接竞品（Primary）**：目标用户高度重合、功能定位相似的直接竞争产品
2. **间接竞品（Benchmark）**：细分市场或用户群有重叠的产品
3. **新兴竞品（Emerging）**：近期快速增长的创新产品

4. **替代方案（Alternative）**：用户可能用来满足相同需求的其他解决方案

请用JSON格式返回：
{{
    "direct_competitors": ["产品1", "产品2", ...],
    "indirect_competitors": ["产品1", ...],
    "emerging_players": ["产品1", ...],
    "alternatives": ["方案1", ...],
    "discovery_notes": "发现说明"
}}

只返回JSON，不要有其他内容。"""
    
    return prompt


def get_generic_report_outline(
    report_type: str,
    schema: dict[str, Any],
    products: list[str],
) -> list[dict[str, Any]]:
    """Generate report outline based on report type and domain schema.
    
    This is the GENERIC competitive analysis skeleton that adapts to domain.
    
    Args:
        report_type: Type of report (product_selection, market_landscape, etc.)
        schema: Domain-specific schema
        products: Products to analyze
        
    Returns:
        List of section definitions
    """
    report_info = REPORT_TYPES.get(report_type, REPORT_TYPES["product_selection"])
    
    # Build outline based on report type
    outline = [
        {
            "slug": "cover-page",
            "title": "报告封面",
            "type": "cover",
            "purpose": "报告标题、日期、分析产品列表",
        },
        {
            "slug": "executive-summary",
            "title": "执行摘要",
            "type": "executive",
            "min_words": 400,
            "target_words": 600,
            "purpose": f"{report_info['name']}的核心结论和建议",
            "section_type": "decision",
        },
        {
            "slug": "analysis-objective",
            "title": "分析目标与范围",
            "type": "chapter",
            "min_words": 300,
            "target_words": 500,
            "purpose": "明确分析目的、服务对象、产品边界",
        },
    ]
    
    # Add sections based on report type
    if report_type in ["product_selection", "procurement_due_diligence"]:
        outline.extend([
            {
                "slug": "competitor-selection",
                "title": "竞品选择逻辑",
                "type": "chapter",
                "min_words": 300,
                "target_words": 500,
                "purpose": "说明竞品筛选标准和分类（主竞品/Benchmark）",
            },
            {
                "slug": "market-positioning",
                "title": "市场定位图",
                "type": "chapter",
                "min_words": 400,
                "target_words": 600,
                "purpose": "2D定位图展示产品差异化位置",
            },
            {
                "slug": "competitor-profiles",
                "title": "竞品画像",
                "type": "chapter",
                "min_words": 600,
                "target_words": 1000,
                "purpose": "每产品一张结构化卡片",
            },
            {
                "slug": "comparison-matrix",
                "title": "能力对比矩阵",
                "type": "chapter",
                "min_words": 600,
                "target_words": 1000,
                "purpose": "核心维度横向结构化对比",
            },
        ])
    
    elif report_type == "market_landscape":
        outline.extend([
            {
                "slug": "market-overview",
                "title": "市场概览",
                "type": "chapter",
                "min_words": 400,
                "target_words": 600,
                "purpose": "市场规模、增长趋势、市场驱动因素",
            },
            {
                "slug": "player-segmentation",
                "title": "玩家分层",
                "type": "chapter",
                "min_words": 500,
                "target_words": 800,
                "purpose": "按定位/规模/阶段对玩家分层",
            },
            {
                "slug": "trend-analysis",
                "title": "趋势分析",
                "type": "chapter",
                "min_words": 400,
                "target_words": 600,
                "purpose": "行业趋势和机会空间",
            },
        ])
    
    # Common sections for all types
    outline.extend([
        {
            "slug": "pricing-analysis",
            "title": "定价与商业模式",
            "type": "chapter",
            "min_words": 400,
            "target_words": 600,
            "purpose": "定价策略、成本结构、商业模式",
        },
        {
            "slug": "ecosystem-signals",
            "title": "生态与市场信号",
            "type": "chapter",
            "min_words": 400,
            "target_words": 600,
            "purpose": "用户口碑、社区活跃度、第三方集成",
        },
        {
            "slug": "swot-analysis",
            "title": "SWOT分析",
            "type": "chapter",
            "min_words": 500,
            "target_words": 800,
            "purpose": "优劣势→机会威胁推导",
        },
    ])
    
    if report_type in ["product_selection", "procurement_due_diligence"]:
        outline.append({
            "slug": "scenario-recommendations",
            "title": "场景化建议",
            "type": "chapter",
            "min_words": 400,
            "target_words": 600,
            "purpose": "WHO+WHAT+WHEN+WHY选型建议",
        })
    
    outline.extend([
        {
            "slug": "risks-gaps",
            "title": "风险与证据缺口",
            "type": "chapter",
            "min_words": 300,
            "target_words": 500,
            "purpose": "已识别风险、待验证缺口",
        },
        {
            "slug": "evidence-appendix",
            "title": "证据附录",
            "type": "appendix",
            "purpose": "完整证据列表和来源",
        },
    ])
    
    return outline


# ============================================================================
# Query Understanding
# ============================================================================

def understand_query(query: str, products: list[str] | None = None) -> dict[str, Any]:
    """Understand and parse user query.
    
    Args:
        query: User's original query
        products: Optional products mentioned
        
    Returns:
        Parsed query understanding with domain, report_type, audience, etc.
    """
    # Detect domain
    domain = detect_domain(query, products)
    
    # Detect report type
    report_type = detect_report_type(query)
    
    # Detect decision audience (default based on report type)
    audience_map = {
        "product_selection": "buyer",
        "market_landscape": "strategist",
        "product_strategy": "product_manager",
        "sales_battlecard": "sales",
        "procurement_due_diligence": "procurement",
    }
    audience = audience_map.get(report_type, "general")
    
    # Detect target region
    region = "unspecified"
    query_lower = query.lower()
    if any(kw in query_lower for kw in ["国内", "中国", "国内版"]):
        region = "china"
    elif any(kw in query_lower for kw in ["海外", "国际", "global", "overseas"]):
        region = "global"
    elif any(kw in query_lower for kw in ["美国", "us", "usa"]):
        region = "us"
    elif any(kw in query_lower for kw in ["日本", "jp", "japan"]):
        region = "japan"
    
    return {
        "original_query": query,
        "domain": domain,
        "domain_name": DOMAIN_SCHEMAS.get(domain, {}).get("name", domain),
        "report_type": report_type,
        "report_type_name": REPORT_TYPES.get(report_type, {}).get("name", report_type),
        "decision_audience": audience,
        "target_region": region,
        "products_mentioned": products or [],
        "need_discovery": len(products or []) == 0,
    }


# ============================================================================
# Utility: Dimension i18n
# ============================================================================

def get_dimension_chinese(dimension: str) -> str:
    """
    Return the Chinese label for a comparison_dimension, falling back to a
    human-readable slug if no explicit chinese field is defined.

    This is the single source of truth for translating dimension names
    (used as table row labels, axis labels, etc.) to Chinese.
    """
    # Scan all domain schemas for a matching dimension
    for schema in DOMAIN_SCHEMAS.values():
        for dim_def in schema.get("comparison_dimensions", []):
            if dim_def.get("dimension") == dimension:
                chinese = dim_def.get("chinese")
                if chinese:
                    return chinese
    # Fallback: replace underscores with spaces, strip common suffixes
    label = dimension.replace("_", " ").replace("-", " ")
    # Strip trailing "_" that might come from slugified names
    label = label.strip()
    return label


def get_all_dimensions_for_schema(schema_type: str) -> list[dict[str, Any]]:
    """Return the comparison_dimensions list for a schema type, with chinese field present."""
    schema = DOMAIN_SCHEMAS.get(schema_type, {})
    dims = schema.get("comparison_dimensions", [])
    # Ensure every dimension has a chinese label
    for dim in dims:
        if "chinese" not in dim:
            dim["chinese"] = get_dimension_chinese(dim.get("dimension", ""))
    return dims
