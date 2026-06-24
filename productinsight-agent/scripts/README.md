# scripts/ 目录索引

> 废弃脚本移至 `scripts/archive/`，请勿使用。本文档仅记录活跃脚本。

## 启动类

| 脚本 | 用途 |
|------|------|
| `./start.sh` | **一键启动**（在 productinsight-agent 根目录）：同时启动 Backend + Streamlit + DAG App |

## 数据初始化

| 脚本 | 用途 | 使用场景 |
|------|------|---------|
| `scripts/seed_golden_demo.py` | 预置 Golden Demo 数据到数据库 | 答辩/演示前初始化数据 |
| `scripts/seed_demo_data.py` | 旧版演示数据种子 | 废弃，seed_golden_demo.py 已替代 |

## 测试与验证

| 脚本 | 用途 | 使用场景 |
|------|------|---------|
| `scripts/test_golden_demo.py` | 验证 Golden Demo 数据完整性（28 项检查） | 答辩前检查数据是否损坏 |
| `scripts/verify_demo.py` | 赛前验证脚本（检查 API、节点、报告文件） | 答辩前 5 分钟运行 |
| `scripts/test_e2e_direct.py` | 直接调用 workflow（无 HTTP），测 P0-P2 功能 | 开发调试 |
| `scripts/test_e2e_report.py` | FastAPI TestClient E2E 测试（HTTP 路径） | 开发调试 |
| `scripts/test_e2e_string_products.py` | 验证字符串 product 格式的 bug 修复 | 回归测试 |
| `scripts/test_pipeline.py` | 完整 E2E 管道测试（健康检查→创建→执行→报告） | 开发调试 |
| `scripts/test_real_e2e.py` | 真实 HTTP API 调用跑完整报告 | 集成测试 |
| `scripts/test_real_collection.py` | 真实采集 + 持久化验证 | 数据采集调试 |
| `scripts/test_rework_loop.py` | Coverage Gap Rework 完整闭环测试 | Rework 功能验证 |
| `scripts/test_generalization_*.py` | 泛化能力测试（T1-T5 用例矩阵） | 验收测试 |

## 演示与录制

| 脚本 | 用途 | 使用场景 |
|------|------|---------|
| `scripts/record_demo.py` | 自动化录屏（自动开关 OBS + 加载 Golden Demo） | 答辩演示录制 |
| `scripts/clone_golden_run.py` | 克隆 Golden Run 数据用于 replay | replay 功能 |

## 提交与导出

| 脚本 | 用途 |
|------|------|
| `scripts/pack_for_submission.py` | 生成干净 zip 包（排除 .env、数据库、测试数据等） |

---

## archive/ 废弃脚本说明

以下脚本已废弃，**请勿使用**：

- `run.sh.broken` — 旧启动脚本，有 bug（端口 8505、无 DAG App、无 DAG_APP_URL）
- `run_demo.py` — 废弃，用 seed_golden_demo.py 替代
- `run_workflow*.py` — 调试用临时脚本
- `replay_run.py` — stub 占位脚本，无实际功能
- `supplemental_claims*.py` — 废弃，report generation 已内置此功能
- `export_report.py / generate_html_report.py` — 废弃，backend 已内置 HTML 生成
- `regen_html*.py / regenerate_report.py` — 废弃，同上
- `diagnose_collection.py` — 调试脚本，已完成诊断使命
- `test_llm_client.py / test_decision_aid_llm.py` — 调试脚本
- `test_review.py` — 调试脚本
- `reverse_proxy.py` — 公网部署脚本，已废弃，用 start.sh + cloudflared 替代
- `frontend_test_line.py / test_fix.py` — 临时测试文件

---

*最后更新：2026-06-21*
