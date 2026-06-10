# ProductInsight Agent

> **Evidence-First Multi-Agent Workstation for Enterprise Competitive Analysis**
> 证据优先的多 Agent 竞品分析工作台

---

## 核心原则

```
No evidence, no claim.    没有证据，不能生成断言（Claim）。
No schema, no report.      没有 Schema 结构，不能出报告。
No review, no publish.     没有质检通过，不能发布。
```

## 项目概述

ProductInsight Agent 是一个基于 Python 的多 Agent 竞品分析系统。系统模拟一个"数字调研小组"，由多个专职 Agent 自动完成从公开信息采集到结构化竞品报告的全链路产出，并通过 Agent 间的交叉审查与反馈机制实现自我校验。

典型分析场景：对比多个 AI 应用开发平台（如 Dify、Coze、FastGPT），从工作流编排、定价模式、目标用户等多个维度生成带证据溯源的竞品分析报告。

---

## 快速启动

### 环境准备

```bash
cd /home/shijialin/paperworking/workflow_new/productinsight-agent

# Python 3.10+ 环境
conda activate py10
# 或
python -m venv .venv && source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

在项目根目录创建 `.env` 文件：

```bash
DATABASE_URL=sqlite:///./data/productinsight.db
APP_ENV=local

# LLM 配置（必须有效）
MODEL_PROVIDER=doubao
MODEL_NAME=your-model-name
MODEL_API_KEY=your-api-key
MODEL_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3
LLM_TIMEOUT=120
ENABLE_LANGSMITH=false
```

> 若暂无有效 LLM Key，可使用 Golden Demo 模式跳过 LLM 调用，直接查看完整演示数据。

### 启动后端

```bash
cd /home/shijialin/paperworking/workflow_new/productinsight-agent
uvicorn backend.app.main:app --reload --port 8005

# 验证启动
curl http://localhost:8005/health
# 返回 {"status":"ok"}
```

### 启动前端

```bash
# 新开一个终端
streamlit run frontend/app.py --server.port 8505 --server.address localhost
# 浏览器访问：http://localhost:8505
```

### Golden Demo（跳过 LLM，直接看效果）

```bash
python scripts/seed_golden_demo.py
python scripts/test_golden_demo.py
pytest -q tests/
```

前端侧边栏 Run ID 选择 `run_golden_completed`，即可查看完整演示结果。

---

## 核心概念

### 数据链路

```
URL 来源（sources）
    ↓ Collector Agent 采集（真实抓取，含 Playwright fallback）
页面快照（snapshots）
    ↓ Evidence Extractor 抽取
证据片段（evidence_items）
    ↓ Evidence Evaluator 评分（6 维度 + Evidence Contract 门控）
可信证据（usable_for_claim = true）
    ↓ Analyst Agent 分析
结构化事实（facts）
    ↓ Analyst Agent 生成
断言草稿（claim_drafts）
    ↓ Reviewer Agent 质检
已签声明（signed_claims） + 待返工（rework_required_claims）
    ↓ Writer Agent（Deep Report v2）
竞品分析报告（report）← 输出 Markdown + HTML，证据附录为折叠卡片
```

### 分析 Schema

系统支持 6 种分析类型，根据查询内容自动推断：

| Schema Type | 适用场景 |
|------------|---------|
| `ai_agent_platform` | AI Agent / 低代码平台竞品分析 |
| `competitor_landscape` | 宽泛竞品概览 |
| `pricing_analysis` | 定价深度对比 |
| `knowledge_management` | 知识管理工具分析 |
| `ai_coding_assistant` | AI 编程工具对比 |
| `sales_battlecard` | 销售对抗话术卡 |

每种类型对应独立的分析维度集合、来源类型白名单和报告章节模板。

### Run（分析任务）

一个 **Run = 一次完整的竞品分析任务**，包含从需求输入到报告输出的全部过程，支持断点重跑。

### Rework（返工）机制

当 Reviewer 发现证据不足时，系统生成 Coverage Gap Rework Task，用户补充 URL 后执行真实补证链路，报告自动更新。

---

## 工作流节点（17 步）

```
build_task_brief              解析任务简报，填充工作流状态
        ↓
plan_schema                   生成 Schema 分析计划
        ↓
plan_sources                  生成来源采集计划
        ↓
collect_sources              采集节点（3 级 fallback）
        ↓
evaluate_evidence            证据质量评分 + Evidence Contract 门控
        ↓
pii_scrub                    个人信息脱敏
        ↓
extract_facts                从证据中抽取结构化事实
        ↓
detect_schema_gaps            检测维度缺口
        ↓
coverage_critic              覆盖率评审
        ↓
analyze_dimensions           Analyst Agent 生成 Claims
        ↓
review_claims                Reviewer Agent 审核 Claims，生成 Rework Requests
        ↓
prepare_human_intervention   人工确认节点
        ↓
execute_rework               执行补证任务
        ↓
write_report_v2              Deep Report v2 生成报告章节
        ↓
final_review                 终审（全局一致性检查）
        ↓
export_report                导出报告（Markdown / HTML）
        ↓
compute_metrics              计算质量指标
```

---

## 报告说明

### 报告结构

14 个结构化章节 + 证据附录：

执行摘要、分析目标与范围、竞品选择逻辑、市场定位图、竞品画像、功能对比矩阵、定价分析、生态信号、SWOT 分析、场景建议、风险说明、评分卡、POC 计划、TCO 成本模型。

证据附录以折叠卡片形式展示，点击展开可查看原始 URL 和摘录。

### 报告状态

| 状态 | 含义 |
|------|------|
| `reviewed` | 全部通过，无缺口 |
| `reviewed_with_gaps` | 已完成，存在证据缺口（正常状态） |
| `blocked` | 证据严重不足，降级为预评估报告 |
| `draft` | 正在生成中 |

---

## 常用 API

```bash
# 创建 Project
POST /api/projects

# 创建 Run
POST /api/projects/{id}/runs

# 异步启动 workflow（推荐）
POST /api/runs/{run_id}/start-async

# 轮询直到完成
GET /api/runs/{run_id}/live

# 获取最新报告 HTML
GET /api/runs/{run_id}/report/html

# 获取报告 Markdown
GET /api/runs/{run_id}/report/md

# 获取 DAG 节点状态
GET /api/runs/{run_id}/workflow

# 生成补证任务
POST /api/runs/{run_id}/coverage-gaps

# 执行补证
POST /api/rework-tasks/{task_id}/execute
```

---

## 故障排查

### 后端启动失败

```bash
# 检查端口占用
lsof -i :8005

# 检查 Python 版本
python3 --version  # 需 3.10+
```

### LLM 调用失败

确认 API Key 有效后重试。Key 过期时 Agent 调用会失败，但 Evidence 采集和报告框架仍可正常运行。

### 前端连不上后端

```bash
# 确认后端运行
curl http://localhost:8005/health

# 检查前端配置（frontend/common/config.py）
# 默认 API_BASE: http://localhost:8005
```

### 数据库锁定

```bash
rm -f data/productinsight.db-wal data/productinsight.db-shm
# 重启后端，迁移自动运行
```

---

## 项目结构

```
productinsight-agent/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI 路由（10 个 router）
│   │   ├── agents/        # 4 个专职 Agent
│   │   ├── orchestrator/  # DAG 工作流编排
│   │   ├── services/      # 业务逻辑服务
│   │   ├── storage/       # SQLite 数据库
│   │   └── schemas/       # Pydantic 数据模型
│   └── main.py            # FastAPI 入口
├── frontend/
│   ├── app.py             # Streamlit 主入口
│   └── views/             # 前端页面模块
├── scripts/
│   ├── seed_golden_demo.py
│   └── test_golden_demo.py
├── data/
│   ├── reports/           # 报告输出
│   └── productinsight.db # SQLite 数据库
├── requirements.txt
└── README.md
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| `docs/提交材料/技术文档/项目原理技术文档.md` | 完整技术原理说明 |
| `docs/提交材料/技术文档/技术难点与关键决策.md` | 主要技术难点与方案权衡 |
| `docs/提交材料/技术文档/AI能力详细说明.md` | AI 能力详细实现说明 |
| `docs/提交材料/技术文档/评分维度自评表.md` | 评分标准逐项对照 |
| `docs/提交材料/开发记录/开发里程碑.md` | 开发过程里程碑 |
