# 答辩 Demo 走位脚本

> 答辩时间紧迫，这份脚本是**答辩时按部就班操作**的"剧本"，每一步都有"说什么"和"点什么"。

---

## 0. 答辩前 5 分钟：环境检查

```bash
# 在终端运行
cd /home/shijialin/paperworking/workflow_new/productinsight-agent
python3 scripts/verify_demo.py
```

**预期结果**：`34/34 checks passed 🎉 ALL GREEN — ready for demo!`

如果失败：
- 看哪一项红，按"问题修复"章节处理
- 严重时直接重跑：`nohup python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8005 > /tmp/backend_8005.log 2>&1 &`

---

## 1. 30 秒开场：登录 + 看到列表

**浏览器打开**：`http://localhost:8502`

**说什么**：
> "这是 ProductInsight-Agent 的运行历史页面。我们已经跑过多次分析，今天重点展示的是 6 月 9 日那次对 4 个主流 AI Agent 平台（Dify、Coze、FastGPT、Flowise）的竞品分析。"

**看什么**：
- 列表中能看到 `run_2b77aa8121f1452a` 这一行
- 状态：**completed**（绿色）
- "报告可用"：✅

---

## 2. 30 秒：打开报告查看

**操作**：点击 `run_2b77aa8121f1452a` → 进入报告页

**说什么**：
> "这份报告是 v2.0 版本，总共 11 个章节，8993 字，引用了 42 条证据，覆盖 4 个产品。"

**看什么**（页面上的关键数据）：
- 可信度摘要：**候选 10 条，已签发 7 条，待返工 3 条**
- 产品覆盖度：Dify、FastGPT 是 **Sufficient**，Coze、Flowise 是 **Partial**
- 证据分布：4 个产品合计 42 条证据
- **报告下方"已签发核心声明"** 是 demo 重点——每条 claim 都有 1-2 条 evidence_ids

---

## 3. 1 分钟：报告内容讲解

**操作**：滚到"Workflow Orchestration" 章节

**说什么**：
> "Dify 提供 Chatbot、Text Generator、Agent、Chatflow、Workflow 五大应用构建模块，配套模型供应商管理、LLM 负载均衡、Dify Marketplace。每条结论都附了 evidence 编号，方便溯源。"

**看什么**：
- 4 个产品横向对比表
- 每行末尾的 `[证据: ev_xxx, ev_xxx]`

---

## 4. 1.5 分钟：最有杀伤力的 16 步 DAG

**操作**：切换到 "Workflow" 页面（侧边栏）

**说什么**：
> "这是我们最核心的创新——16 步可观测的 DAG 工作流。每一步都对应一个职责清晰的 Agent："

**看什么**（按顺序点名）：
1. `build_task_brief` - 任务规划 Agent
2. `plan_schema` - 动态 Schema 规划（6 种 schema）
3. `plan_sources` - 来源规划
4. `collect_sources` - 数据采集（URL Fetch + Doubao Search）
5. `evaluate_evidence` - 6 维证据质量评分
6. `pii_scrub` - PII 脱敏
7. `extract_facts` - 事实抽取
8. `detect_schema_gaps` - Schema 缺口检测
9. `analyze_dimensions` - 维度分析
10. `review_claims` - Reviewer 审核（hard-gate）
11. `execute_rework` - 返工执行（这里显示 pending，因为这次没触发返工）
12. `prepare_human_intervention` - 人工干预准备
13. `write_report_v2` - 报告写作（耗时 20 分钟，是最贵的环节）
14. `final_review` - 报告终审
15. `export_report` - 导出 HTML/MD/JSON
16. `compute_metrics` - 指标计算

**强调**：
> "每一步都是独立可观测的，token 消耗、耗时、输入输出都有 log。**写报告这一步最长（20 分钟）**，但其他步骤合计 4 分钟。"

---

## 5. 1 分钟：反馈闭环（最关键)

**操作**：回到报告，**重点指出"待返工 3 条"**

**说什么**：
> "我们最骄傲的是反馈闭环。这次分析输出了 10 条候选 claim，但 Reviewer Agent 用 4 项硬性检查（evidence_required / source_diversity / claim_specificity / hallucination_containment）筛掉了 3 条。**这是真实的闭环，不是装样子的**。"

**展开**：
> "比如 Flowise 的 function_tree claim，Reviewer 检查发现 evidence 的 usable_for_claim=False，不足以支撑声明，就 hard-gate 拒绝。同时我们系统会自动生成 rework 任务，可以从更精准的 seed URL 重新采集证据。**这就是"无证据不下结论"（No evidence, no claim）的硬约束**。"

---

## 6. 1 分钟：证据溯源（来源可点击）

**操作**：在报告页点击任意 evidence_id

**说什么**：
> "我们做到了信息溯源。点任意一条证据 ID，就能跳回原始来源：例如 ev_9f529502f74c4794 是从 Dify 定价页采集的原始段落，含 URL、时间戳、置信度。"

**可展示**（如果时间允许）：
- evidence_id 的 snippet 原文
- 源 URL
- 信任分层（trust_tier）
- 6 维质量分（relevance / authority / freshness / schema_fit / information_density / final_score）

---

## 7. 30 秒：可观测性

**操作**：演示 `/api/runs/{id}/metrics` 的 JSON（或者工作流节点的耗时数据）

**说什么**：
> "整套系统是端到端可观测的。6 维证据评分、3 个 rework 阶段、review pass rate 0.7、分析时间 24.5 分钟——这些不是事后算的，是实时写的。"

**关键数字**：
- 总耗时 24.5 分钟
- 写报告 20 分钟（其余 4 分钟做采集+分析+审核）
- 证据覆盖率 1.0（7/7 signed claim 都有 evidence）
- review pass rate 0.7（7/10 通过硬门槛）

---

## 8. 应对可能的问题

**Q：报告只有 7 条签发，3 条没签发，是不是质量不高？**
A：恰恰相反。我们**坚持"无证据不下结论"**。3 条没签发是设计行为：宁可少说，不可乱说。如果不设 hard-gate 强约束，LLM 会编。

**Q：Dify 和 FastGPT 是 Sufficient，Coze 和 Flowise 是 Partial，是不是意味着分析不完整？**
A：Partial 表示部分维度覆盖（如 Coze 只覆盖了 user_persona 维度）。这是**诚实的标识**，提示用户哪些地方需要补充证据。我们没有为了"看起来完整"而虚构数据。

**Q：为什么要 LangGraph 而不是 CrewAI？**
A：LangGraph 的 DAG 模式更适合**有明确依赖顺序的 ETL 流程**。我们的 16 步有 4 个硬依赖（必须先有 schema 才能 plan_sources，必须先有 evidence 才能 review_claims）。CrewAI 适合多角色对话协作，不是这种链式数据流。

**Q：API Key 怎么用？会不会泄露？**
A：API Key 在 `.env` 文件里，不进 git。代码里统一通过环境变量读。演示用临时 key。

**Q：能跑多大规模？**
A：单次 4 个产品 / 24 分钟是线性的。N 个产品 N×6 分钟 + 20 分钟写作 = O(N)。可并行到 5 个 run 跑不同产品组。

---

## 9. 紧急情况处置

### 9.1 Streamlit 卡住
```bash
# 找到进程
ps aux | grep "streamlit run frontend/app.py" | grep -v grep
# 重启
kill <PID>
nohup python -m streamlit run frontend/app.py --server.port 8502 --server.address 0.0.0.0 > /tmp/frontend_8502.log 2>&1 &
```

### 9.2 后端 500
```bash
# 重启
pkill -f "uvicorn.*8005"
nohup python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8005 > /tmp/backend_8005.log 2>&1 &
```

### 9.3 报告链接打不开
```bash
# 直接打开磁盘文件
ls -la /home/shijialin/paperworking/workflow_new/productinsight-agent/data/reports/
# 复制路径
python3 -c "
import shutil
shutil.copy(
    'data/reports/report_run_2b77aa8121f1452a_v2.html',
    '/tmp/demo_report.html'
)
print('Copied to /tmp/demo_report.html')
"
```

### 9.4 数据库锁了
```bash
# 找出 sqlite 锁
fuser /home/shijialin/paperworking/workflow_new/productinsight-agent/data/productinsight.db
# 一般 streamlit / uvicorn 同时写。重启两边就好。
```

---

## 10. 答辩结束：演示资源回收

```bash
# 1. 备份本次演示数据
cp -r data/reports/ /tmp/demo_backup_reports/

# 2. 备份数据库
cp data/productinsight.db /tmp/demo_backup_$(date +%Y%m%d_%H%M).db

# 3. （可选）生成最终报告
python3 scripts/verify_demo.py > /tmp/demo_verify.txt
cat /tmp/demo_verify.txt
```

---

**祝答辩顺利！**
