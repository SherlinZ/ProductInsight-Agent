#!/usr/bin/env python3
"""
打包脚本：生成不含敏感信息、内部开发记录、测试数据的干净 zip 包。

排除内容：
  - .env（含真实 API Key，保留 .env.example）
  - productinsight.db（运行时数据库）
  - PROJECT_STATUS_REPORT.md / CURRENT_STATUS.md（内部开发记录）
  - e2e_*.json（测试输出）
  - frontend_test_line.py / test_fix.py（临时测试文件）
  - =4.0.0（包管理器残留文件）
  - scripts/ 中非核心脚本（调试辅助用）
  - docs/ 目录（内部开发记录）
  - __pycache__、*.pyc 等编译缓存
  - data/reports/（运行时生成的报告文件）
"""

import zipfile
import pathlib
import sys

root = pathlib.Path("productinsight-agent")
output_zip = "productinsight-agent.zip"

# 需要排除的顶层文件名
EXCLUDE_NAMES = {
    ".env",
    "productinsight.db",
    "PROJECT_STATUS_REPORT.md",
    "CURRENT_STATUS.md",
    "e2e_full_result_report_run_fd7ec6196a594fc4_e2.json",
    "frontend_test_line.py",
    "test_fix.py",
    "=4.0.0",
}

# 需要排除的子目录/前缀
EXCLUDE_PREFIXES = {
    "data/reports/",
    "scripts/diagnose_collection.py",
    "scripts/export_report.py",
    "scripts/record_demo.py",
    "scripts/regen_html.py",
    "scripts/regenerate_report.py",
    "scripts/replay_run.py",
    "scripts/run_demo.py",
    "scripts/run_workflow.py",
    "scripts/run_workflow_patched.py",
    "scripts/seed_demo_data.py",
    "scripts/supplemental_claims.py",
    "scripts/supplemental_claims_v2.py",
    "scripts/test_e2e_direct.py",
    "scripts/test_e2e_report.py",
    "scripts/test_e2e_string_products.py",
    "scripts/test_llm_client.py",
    "scripts/test_pipeline.py",
    "scripts/test_real_collection.py",
    "scripts/test_review.py",
    "scripts/test_rework_loop.py",
    "scripts/diagnose_collection.py",
    "docs/",  # 内部开发文档整体排除
}

# 保留的 scripts
KEEP_SCRIPTS = {"seed_golden_demo.py", "test_golden_demo.py"}


def should_exclude(rel_path: str) -> tuple[bool, str]:
    """返回 (是否排除, 排除原因)"""
    name = pathlib.Path(rel_path).name

    # 顶层文件名排除
    if name in EXCLUDE_NAMES:
        if name == ".env":
            return True, "敏感文件（真实 API Key）"
        elif ".db" in name:
            return True, "运行时数据库"
        else:
            return True, "内部开发文件"

    # 前缀排除
    for prefix, reason in [
        ("data/reports/", "运行时报告"),
        ("docs/", "内部开发文档"),
    ]:
        if rel_path.startswith(prefix):
            return True, reason

    # scripts/ 选择性排除
    if rel_path.startswith("scripts/"):
        if name in EXCLUDE_PREFIXES:
            return True, "调试辅助脚本"
        if name.endswith(".py") and name not in KEEP_SCRIPTS:
            return True, "测试脚本"

    # 编译缓存
    if "__pycache__" in rel_path or name.endswith(".pyc"):
        return True, "编译缓存"

    return False, ""


def main():
    if not root.exists():
        print(f"错误：目录 {root} 不存在", file=sys.stderr)
        sys.exit(1)

    collected = []
    skipped = []

    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel_path = f.relative_to(root).as_posix()

        exclude, reason = should_exclude(rel_path)
        if exclude:
            skipped.append((rel_path, reason))
        else:
            collected.append((rel_path, f))

    print(f"待打包文件：{len(collected)} 个")
    print(f"排除文件：{len(skipped)} 个")
    print()

    if skipped:
        print("排除详情：")
        for path, reason in skipped:
            print(f"  [{reason}] {path}")

    print()
    print(f"正在创建 {output_zip} ...")

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, abs_path in collected:
            zf.write(abs_path, rel_path)

    size_mb = pathlib.Path(output_zip).stat().st_size / 1024 / 1024
    print(f"\n完成：{output_zip}（{size_mb:.1f} MB，共 {len(collected)} 个文件）")


if __name__ == "__main__":
    main()
