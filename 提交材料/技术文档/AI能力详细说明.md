# ProductInsight Agent — AI 能力详细说明

> **目的**：将代码中实现的 AI 能力细节整理成文档，补充技术原理文档和提交结果中未完整覆盖的工程含金量
> **适用场景**：评委技术审查、答辩 Q&A、代码 Code Review

---

## 一、Evidence 质量评估体系

### 1.1 六维度评分（EvidenceEvaluator）

每条 evidence 在进入 Claims 生成前，必须通过 `EvidenceEvaluator.evaluate()` 的 6 维度评分：

| 维度 | 权重 | 说明 | 阈值 |
|------|------|------|------|
| **relevance**（相关性） | 25% | 产品名匹配 + Schema 维度关键词匹配 | ≥ 0.20 才允许进入 Claim |
| **authority**（权威性） | 20% | 来源类型（official_site/documentation 高，social 低）+ 域名白名单 | 无硬阈值 |
| **freshness**（时效性） | 10% | 基于证据抓取时间，30天内=1.0，2年+=0.1 | 无硬阈值 |
| **schema_fit**（维度对齐） | 25% | Schema 关键词命中率（1个=0.3，6个+=1.0） | 信息性，非门控 |
| **information_density**（信息密度） | 20% | 长度 + 数字/统计数据 + 列表结构 + 导航噪声惩罚 | 无硬阈值 |
| **final_score**（综合分） | 加权和 | relevance×0.25 + authority×0.2 + freshness×0.1 + schema_fit×0.25 + density×0.2 | ≥ **0.32** 才 `usable_for_claim=True` |

**`usable_for_claim` 门控条件**（必须同时满足）：
```python
quality.final_score >= 0.32  AND
quality.relevance >= 0.20    AND
quality.schema_fit >= 0.0     (始终满足)
```

**P0-5 阈值调整历史**：
- `FINAL_SCORE_THRESHOLD`：0.45 → 0.38 → 0.32（逐步放宽）
- `RELEVANCE_THRESHOLD`：0.30 → 0.20（中文来源导致产品名变体匹配率低）
- 调整依据：实际运行数据，防止短证据片段（如定价引用，通常 50-150 字符）永远无法通过门控

### 1.2 中文证据质量评分（Evidence-Sufficiency Sprint）

系统针对中文来源（Dify/Coze/FastGPT 官方文档）做了全面适配：

| 机制 | 实现 |
|------|------|
| Schema 关键词 | 包含 40+ 中文关键词（工作流、编排、节点、画布、拖拽等） |
| 维度关键词 | `pricing_model` 包含"价格/定价/费用/套餐/订阅"等 |
| relevance 计算 | 中文占比 >30% 时改用 substring 匹配而非 token 交集 |
| schema_fit 计算 | 中文占比 >30% 时统计 keyword substring 出现次数 |
| information_density | 对中文内容应用 1.15 倍长度得分加成 |
| `is_meaningful_evidence` | 中文用字符数（≥20 字=有意义），英文用停用词过滤 |

### 1.3 导航噪声黑名单（NOISE_BLACKLIST）

30+ 条硬黑名单模式，防止低质量证据进入系统：

```
导航类：skip to main content / change notification settings / cookie consent
404类：page not found / we couldn't find the page / maybe you were looking for
GitHub目录类：pull requests / issues / actions / releases / packages / settings
认证类：sign in to github / create an account / request rate limit
```

### 1.4 官方域名注册表（OFFICIAL_PRODUCT_DOMAINS）

区分官方来源和第三方来源，防止劣质博客作为权威证据：

| 产品 | 官方域名 | 第三方排除 |
|------|---------|-----------|
| Dify | dify.ai, docs.dify.ai, cloud.dify.ai | dify-china.com 等镜像站 |
| Coze | coze.cn, docs.coze.cn, coze.com | cloud.tencent.com 等博客 |
| FastGPT | fastgpt.cn, docs.fastgpt.cn | — |
| Flowise | flowiseai.com | — |

---

## 二、Schema 体系

### 2.1 6 种分析 Schema

系统支持 6 种结构化分析类型，动态适配用户查询：

| Schema Type | 维度数 | 章节数 | 适用场景 |
|------------|--------|--------|---------|
| `ai_agent_platform` | 6 | 7 | AI Agent 平台（Dify/Coze/Flowise） |
| `competitor_landscape` | 6 | 3 | 宽泛竞品概览 |
| `pricing_analysis` | 6 | 13 | 定价深度分析 |
| `knowledge_management` | 9 | 12 | 知识管理工具（Notion/Confluence） |
| `ai_coding_assistant` | 6 | 7 | AI 编程工具（Cursor/Copilot） |
| `sales_battlecard` | 3 | 3 | 销售对抗话术卡 |

每种 Schema 有独立的：
- 分析维度集合（`SCHEMA_TYPE_DIMENSIONS`）
- 来源类型白名单（`SCHEMA_SOURCE_TYPES`）
- 报告章节模板（`REPORT_OUTLINE_TEMPLATES`）

### 2.2 Schema Type 自动推断（`infer_schema_type`）

用户输入自然语言，系统自动推断分析类型：

```python
用户: "对比 Dify、Coze 和 FastGPT"        → ai_agent_platform
用户: "分析 Notion 和 Confluence 的协作"  → knowledge_management
用户: "Cursor 和 Copilot 哪个好"          → ai_coding_assistant
用户: "Slack 和 Teams 定价对比"           → pricing_analysis
用户: "随便聊聊这几个产品"                → competitor_landscape
```

推断逻辑使用 whole-word 正则匹配防止误判（如 "conf" 不会错误匹配 "conferencing"）。

### 2.3 动态 Schema 关键词（Schema Keywords）

`SCHEMA_KEYWORDS` 包含 80+ 关键词覆盖 6 大维度，`DIMENSION_KEYWORDS` 为每个维度定义独立关键词集。Analyst Agent 的 `_normalize_dimension` 支持维度别名映射（如 "ease of use" → "user_persona"）。

---

## 三、Research Plan 生成机制

### 3.1 LLM + Fallback 双轨制

```
用户自然语言需求
      ↓
尝试 LLM（Doubao-Seed-2.0-lite，temperature=0.3）
      ↓
成功 → 合并 LLM 字段 + 模板结构 → generated_by="llm_augmented"
失败 → 降级到确定性模板 → generated_by="fallback"
```

实测 AskNews/Perplexity（不在预定义列表）→ `generated_by="llm_augmented"` ✅

### 3.2 竞品自动发现链（4 级兜底）

当用户未提供竞品 URL 时：

```
① Doubao Web Search（超时常发）→ 自动降级
      ↓
② DuckDuckGo 搜索（免费，无需配置）→ ✅ 主力兜底
      - AskNews: https://asknews.app/ ✅
      - Perplexity: https://www.perplexity.ai/ ✅
      ↓
③ LLM Inference（用模型知识推断 URL）→ 最终兜底
      ↓
④ Seed URLs → 已知产品用预定义 URL
      ↓
⑤ Fixture 静态数据 → 万不得已
```

### 3.3 语言检测与本地化（vNext-R3-B）

`detect_language()` 自动检测用户输入语言，返回 `zh/en/mixed`，对应不同的 Prompt 语言配置：

| 配置项 | 中文（zh） | 英文（en） |
|--------|-----------|-----------|
| `output_language` | 中文 | English |
| `report_language` | Chinese | English |
| `example_competitors` | Dify、钉钉 | Notion, Confluence |
| `default_report_title` | 竞品分析报告 | Competitive Analysis Report |

### 3.4 预定义竞品库（20+ 竞品）

`KNOWN_COMPETITORS` 注册了 20+ 产品的官方 URL 和别名：

AI Agent 平台：Dify、Coze、Flowise、LangGraph、LangChain、AutoGen、CrewAI
AI 编程工具：Cursor、Windsurf、Trae、GitHub Copilot
知识管理工具：Notion、Confluence、Coda、Slite、Airtable、ClickUp Docs
协作工具：Slack、Microsoft Teams、Zoom、Google Meet

每条包含：`official_url`、`seed_urls`（多个入口）、`known_aliases`（产品别名列表）

---

## 四、Reviewer Agent 质检体系

### 4.1 Claim 质检 8 项检查

| 检查项 | 门控逻辑 | 失败时 |
|--------|---------|--------|
| `noise_filter` | 文本太短（<20字符）或为导航噪声 | 标记 `NOISE_CLAIM` |
| `evidence_required` | claim 必须有 evidence_ids | 标记 `MISSING_EVIDENCE` |
| `evidence_exists` | evidence_ids 在 store 中存在 | 标记 `INVALID_EVIDENCE_ID` |
| `schema_compliance` | dimension 在 ALLOWED_DIMENSIONS 中 | 标记 `SCHEMA_MISMATCH` |
| `confidence_threshold` | confidence ≥ 0.5 才允许签发 | <0.5 标记 `LOW_CONFIDENCE` |
| `pii_masked` | 所有引用 evidence 必须 `pii_masked=True` | 标记 `PII_NOT_MASKED` |
| `required_field_coverage` | 必须有 dimension 和 claim_text | 标记 `SCHEMA_FIELD_MISSING` |
| `evidence_quality_gate`（硬门控） | 至少 1 条 `usable_for_claim=True` 的 evidence | 全部 unusable 标记 `UNUSABLE_EVIDENCE` |

### 4.2 Reason Code → Agent/Node 映射

8 种错误类型 → 精确打回目标：

```
MISSING_EVIDENCE      → collector_agent / collect_sources
INVALID_EVIDENCE_ID   → collector_agent / collect_sources
SCHEMA_MISMATCH       → extractor_agent / extract_facts
SCHEMA_FIELD_MISSING  → extractor_agent / extract_facts
LOW_CONFIDENCE        → analyst_agent / analyze_dimensions
PII_NOT_MASKED        → collector_agent / pii_scrub
NOISE_CLAIM           → analyst_agent / analyze_dimensions
UNUSABLE_EVIDENCE     → collector_agent / collect_sources
```

### 4.3 报告级质检（4 项检查）

| 检查项 | 说明 |
|--------|------|
| `report_span_claim_link` | 非摘要章节必须有 claim_ids |
| `evidence_linked` | 非摘要章节必须有 evidence_ids |
| `pii_leakage` | 报告中不能出现未脱敏的 evidence |
| `schema_compliance` | 所有 claims 的 dimension 必须合法 |

---

## 五、幻觉抑制三层防护

### 5.1 第 1 层：引用强制

- 每条 `[E:N]` 引用必须在 Evidence Appendix 中存在
- `CitationVerifier` 在报告导出前验证引用编号
- Gate-8 阻止引用不存在的 evidence

### 5.2 第 2 层：内部一致性 Gate

| Gate | 检查内容 |
|------|---------|
| Gate-1 | 产品数量一致性 |
| Gate-2 | 定价数字一致性 |
| Gate-4 | Scorecard 非空 |
| Gate-7 | POC 优先级合理性 |
| Gate-8 | 引用编号有效性 |

### 5.3 第 3 层：超长上下文分片（P0-5）

```python
def _chunk_evidence_for_llm(evidence_items, max_tokens=6000):
    # 按 product 分组
    # 超出 max_tokens 时分块
    # _call_llm_with_evidence_chunks() 多批次调用并合并
```

---

## 六、blocked 降级机制

当 evidence 质量不满足 Evidence Contract 时，`WriterAgent` 进入 `is_blocked` 模式：

**禁止出现的语言**：
- "top pick"、"optimal choice"、"most mature"、"best suited for"
- "recommended as the primary option"、"strongly recommended"
- 表情符号排名：🥇🥈🥉

**必须使用的谨慎措辞**：
- "待核验"、"需补充证据"、"暂无法判断"
- "建议 POC 验证后决策"
- 无覆盖产品标注：`⚠️ 无签署声明，需补证后重新评估`

---

## 七、关键技术文件索引

| 功能 | 文件 | 行数 |
|------|------|------|
| Research Plan 生成 | `backend/app/services/research_planner.py` | 1899 |
| 报告章节生成（Deep Report v2） | `backend/app/services/deep_report.py` | 7102 |
| Evidence 质量评估 | `backend/app/services/evidence_evaluator.py` | 830 |
| Reviewer Agent 质检 | `backend/app/agents/reviewer/reviewer.py` | 785 |
| Writer Agent 报告撰写 | `backend/app/agents/writer/writer.py` | 848 |
| Analyst Agent 分析 | `backend/app/agents/analyst/analyst.py` | 735 |
| DAG 编排器节点实现 | `backend/app/orchestrator/nodes.py` | 5821 |
| DAG 图定义与超时 | `backend/app/orchestrator/graph.py` | 1001 |
| LLM 调用封装（重试+多策略解析） | `backend/app/services/llm_client.py` | — |
| 全文追踪 | `backend/app/tracing/llm_trace.py` | — |
| Schema 定义（Pydantic） | `backend/app/schemas/research_plan.py` | — |
| Web 采集（3级 fallback） | `backend/app/services/web_fetcher.py` | — |
