#!/usr/bin/env python3
"""Quick test: call section generators with mock render_ctx to verify LLM output quality."""
from __future__ import annotations
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{PROJECT_ROOT}/data/productinsight.db")

# Load env
_env = PROJECT_ROOT / ".env"
if _env.exists():
    from dotenv import load_dotenv
    load_dotenv(_env)

# Verify LLM is configured
try:
    from backend.app.services.llm_client import get_llm_client
    client = get_llm_client()
    print(f"LLM client: provider={client.config.provider}, model={client.model_name}")
except Exception as e:
    print(f"ERROR: LLM not configured: {e}")
    print("Set MODEL_API_KEY and MODEL_NAME in .env")
    sys.exit(1)

from backend.app.services.deep_report import (
    _generate_selection_scorecard,
    _generate_poc_checklist,
    _generate_evidence_strength_matrix,
    _generate_opportunity_risk_matrix,
    _generate_tco_model,
)

# ── Mock render_ctx with realistic data ───────────────────────────────────────
mock_render_ctx = {
    "products": ["Dify", "LangGraph", "Coze"],
    "signed_claims": [
        {
            "product_name": "Dify",
            "dimension": "workflow_orchestration",
            "claim_text": "Dify提供可视化工作流编排界面，支持50+预置节点，非技术人员通过拖拽可在30分钟内完成客服Bot搭建[E3]。",
            "evidence_ids": ["e1", "e2"],
            "confidence": 0.82,
            "review_status": "signed",
            "product_id": "dify",
        },
        {
            "product_name": "Dify",
            "dimension": "rag_knowledge",
            "claim_text": "Dify内置RAG管道，支持PDF/Word/TXT多格式文档导入，向量检索和全文检索混合召回，实测问答准确率在70-80%[E4]。",
            "evidence_ids": ["e3"],
            "confidence": 0.75,
            "review_status": "signed",
            "product_id": "dify",
        },
        {
            "product_name": "Dify",
            "dimension": "security_compliance",
            "claim_text": "Dify支持私有化部署，数据不出本地网络，支持RBAC权限控制和操作审计日志[E5]。",
            "evidence_ids": [],
            "confidence": 0.65,
            "review_status": "signed",
            "product_id": "dify",
        },
        {
            "product_name": "LangGraph",
            "dimension": "workflow_orchestration",
            "claim_text": "LangGraph提供低层级原语（StateGraph、ConditionEdge），支持完全自定义工作流控制逻辑，适合复杂多Agent协作系统搭建[E8]。",
            "evidence_ids": ["e10", "e11"],
            "confidence": 0.88,
            "review_status": "signed",
            "product_id": "langgraph",
        },
        {
            "product_name": "LangGraph",
            "dimension": "model_support",
            "claim_text": "LangGraph原生支持OpenAI、Anthropic、Google Vertex AI等20+模型提供方，开发者可自由切换[E9]。",
            "evidence_ids": ["e12"],
            "confidence": 0.80,
            "review_status": "signed",
            "product_id": "langgraph",
        },
        {
            "product_name": "Coze",
            "dimension": "workflow_orchestration",
            "claim_text": "Coze提供零代码Bot搭建平台，用户通过选择预置插件和填写提示词即可创建对话Bot，无需编程基础[E1]。",
            "evidence_ids": ["e20"],
            "confidence": 0.70,
            "review_status": "signed",
            "product_id": "coze",
        },
        {
            "product_name": "Coze",
            "dimension": "security_compliance",
            "claim_text": "Coze国际版存在区域访问限制，仅面向中国境内用户开放，海外用户无法正常使用[官方说明]。",
            "evidence_ids": [],
            "confidence": 0.95,
            "review_status": "signed",
            "product_id": "coze",
        },
        {
            "product_name": "Coze",
            "dimension": "pricing_model",
            "claim_text": "Coze提供免费版，含有限Bot数量和消息额度；企业版按席位订阅定价，具体价格需联系销售[官网]。",
            "evidence_ids": [],
            "confidence": 0.60,
            "review_status": "signed",
            "product_id": "coze",
        },
        {
            "product_name": "Dify",
            "dimension": "pricing_model",
            "claim_text": "Dify开源版免费使用，商用Dify Pro每席位每月$50，企业版支持私有化部署[官网定价]。",
            "evidence_ids": ["e30"],
            "confidence": 0.85,
            "review_status": "signed",
            "product_id": "dify",
        },
        {
            "product_name": "LangGraph",
            "dimension": "pricing_model",
            "claim_text": "LangGraph基于LangChain，基础框架开源免费，商业使用LangSmith调试工具需订阅，月费$99起[官网]。",
            "evidence_ids": ["e31"],
            "confidence": 0.78,
            "review_status": "signed",
            "product_id": "langgraph",
        },
    ],
    "swot_figures": [
        {
            "product": "Dify",
            "figure_title": "Dify SWOT分析",
            "chart_data": {
                "weaknesses": [
                    "相比LangGraph，自定义工作流控制能力有限，无法实现复杂的层级化Agent架构",
                    "闭源Pro版定价不透明，需要联系销售才能获取报价",
                ],
                "threats": [
                    "Coze等闭源平台正在快速迭代低代码能力，可能侵蚀Dify的非技术用户市场",
                    "大模型厂商（如OpenAI）可能推出原生Agent平台",
                ],
            },
        },
        {
            "product": "LangGraph",
            "figure_title": "LangGraph SWOT分析",
            "chart_data": {
                "weaknesses": [
                    "完全没有可视化界面，对非技术用户完全不可用",
                    "需要Python开发能力，团队学习曲线陡峭",
                ],
                "threats": [
                    "LangChain框架本身更新频繁，API兼容性问题可能导致迁移成本",
                ],
            },
        },
        {
            "product": "Coze",
            "figure_title": "Coze SWOT分析",
            "chart_data": {
                "weaknesses": [
                    "国际版区域访问限制，无法支撑出海业务团队",
                    "仅支持Bot类对话场景，不支持复杂工作流编排",
                ],
                "threats": [
                    "国内政策变化可能导致平台服务不稳定",
                ],
            },
        },
    ],
    "scorecard_inputs": {
        "工作流编排": {
            "Dify": {"evidence_count": 2, "claim_count": 1},
            "LangGraph": {"evidence_count": 2, "claim_count": 1},
            "Coze": {"evidence_count": 1, "claim_count": 1},
        },
        "RAG/知识库": {
            "Dify": {"evidence_count": 1, "claim_count": 1},
            "LangGraph": {"evidence_count": 0, "claim_count": 0},
            "Coze": {"evidence_count": 0, "claim_count": 0},
        },
        "安全合规": {
            "Dify": {"evidence_count": 0, "claim_count": 1},
            "LangGraph": {"evidence_count": 0, "claim_count": 0},
            "Coze": {"evidence_count": 0, "claim_count": 1},
        },
        "免费套餐": {
            "Dify": {"evidence_count": 1, "claim_count": 1},
            "LangGraph": {"evidence_count": 1, "claim_count": 1},
            "Coze": {"evidence_count": 1, "claim_count": 1},
        },
        "模型兼容": {
            "Dify": {"evidence_count": 0, "claim_count": 0},
            "LangGraph": {"evidence_count": 1, "claim_count": 1},
            "Coze": {"evidence_count": 0, "claim_count": 0},
        },
    },
    "coverage_by_product": {
        "Dify": 0.6,
        "LangGraph": 0.4,
        "Coze": 0.3,
    },
    "poc_requirements": [
        {
            "priority": "P0",
            "item": "30分钟搭建客服Bot",
            "standard": "能否在30分钟内完成基础客服机器人的搭建和上线",
            "product_statuses": {
                "Dify": "✅ 已验证",
                "LangGraph": "❌ 无证据",
                "Coze": "⚠️ 参考官网",
            },
        },
        {
            "priority": "P0",
            "item": "知识库导入",
            "standard": "能否导入100篇PDF并保持回答准确",
            "product_statuses": {
                "Dify": "✅ 已验证",
                "LangGraph": "❌ 无证据",
                "Coze": "❌ 无证据",
            },
        },
        {
            "priority": "P1",
            "item": "私有化部署",
            "standard": "是否支持私有化部署，满足数据合规要求",
            "product_statuses": {
                "Dify": "✅ 已有证据",
                "LangGraph": "✅ 已验证",
                "Coze": "⚠️ 参考官网",
            },
        },
    ],
    "pricing_transparency": {
        "Dify": "verified",
        "LangGraph": "partially_verified",
        "Coze": "unknown",
    },
    "evidence_items": [
        {"evidence_id": "e1", "usable_for_claim": True},
        {"evidence_id": "e2", "usable_for_claim": True},
        {"evidence_id": "e3", "usable_for_claim": True},
    ],
    "product_id_to_name": {},
    "swot_figures": [
        {
            "product": "Dify",
            "figure_title": "Dify SWOT",
            "chart_data": {
                "weaknesses": ["相比LangGraph自定义能力有限", "闭源版定价不透明"],
                "threats": ["Coze等闭源平台快速迭代"],
            },
        },
        {
            "product": "LangGraph",
            "figure_title": "LangGraph SWOT",
            "chart_data": {
                "weaknesses": ["无可视化界面，学习曲线陡峭"],
                "threats": ["LangChain API兼容性问题"],
            },
        },
    ],
}

RUN_ID = "test_decision_aid_001"
REPORT_ID = "test_report_001"

print("=" * 70)
print("  Testing Decision Aid Sections with LLM")
print("=" * 70)
print()

# ── Test 1: selection_scorecard ──────────────────────────────────────────────
print("### 1. selection_scorecard ###")
print()
scorecard = _generate_selection_scorecard(REPORT_ID, RUN_ID, mock_render_ctx)
# Print just the key parts
lines = scorecard.split("\n")
in_scenario = False
for i, line in enumerate(lines):
    if "选型建议速查" in line:
        # Print the table + first 30 lines of this section
        print("\n".join(lines[i:i+35]))
        print("...")
        break
print()

# ── Test 2: poc_checklist ───────────────────────────────────────────────────
print("### 2. poc_checklist ###")
print()
poc = _generate_poc_checklist(REPORT_ID, RUN_ID, mock_render_ctx)
lines = poc.split("\n")
print("\n".join(lines[:40]))
print("...")
print()

# ── Test 3: evidence_strength_matrix ───────────────────────────────────────
print("### 3. evidence_strength_matrix (report_confidence) ###")
print()
conf = _generate_evidence_strength_matrix(REPORT_ID, RUN_ID, mock_render_ctx)
lines = conf.split("\n")
print("\n".join(lines[:50]))
print("...")
print()

# ── Test 4: opportunity_risk_matrix ─────────────────────────────────────────
print("### 4. opportunity_risk_matrix (product_risks) ###")
print()
risks = _generate_opportunity_risk_matrix(REPORT_ID, RUN_ID, mock_render_ctx)
lines = risks.split("\n")
print("\n".join(lines[:50]))
print("...")
print()

# ── Test 5: tco_model ────────────────────────────────────────────────────────
print("### 5. tco_model ###")
print()
tco = _generate_tco_model(REPORT_ID, RUN_ID, mock_render_ctx)
lines = tco.split("\n")
print("\n".join(lines[:40]))
print("...")
print()

print("=" * 70)
print("  All tests completed")
print("=" * 70)
