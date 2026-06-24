"""Diagnose _render_evidence_strength_section on real report data.

Loads the most recent report with signed_claims and re-runs the dimension
mapping logic to show that 0% coverage is a key-namespace mismatch
(claims use function_tree / pricing_model / user_persona while
DIMENSION_LABELS uses workflow_orchestration / free_tier / etc.).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/home/shijialin/paperworking/workflow_new/productinsight-agent")
sys.path.insert(0, str(REPO / "backend"))

# Replicate the exact mapping the buggy code uses
DIMENSION_LABELS = {
    "workflow_orchestration": "工作流编排能力",
    "rag_knowledge": "知识库 / RAG 能力",
    "model_support": "模型支持与兼容性",
    "multi_agent": "多 Agent 协作",
    "integration": "集成与扩展能力",
    "security_compliance": "安全合规能力",
    "free_tier": "免费套餐",
    "paid_plans": "付费套餐",
    "enterprise_pricing": "企业定价",
    "non_technical_business": "非技术团队适配",
    "low_code_developers": "低代码开发者适配",
    "professional_developers": "专业开发团队适配",
    "ai_engineers": "AI 工程师适配",
}

# Authoritative mapping from DOMAIN_SCHEMAS["ai_agent_platform"]
SCHEMA_DIM_TO_USER_DIMS = {
    "function_tree": [
        "workflow_orchestration",
        "rag_knowledge",
        "model_support",
        "multi_agent",
        "integration",
        "security_compliance",
    ],
    "pricing_model": ["free_tier", "paid_plans", "enterprise_pricing"],
    "user_persona": [
        "non_technical_business",
        "low_code_developers",
        "professional_developers",
        "ai_engineers",
    ],
}


def level_v0(entry):
    """Current buggy _level: returns 🟡 待补充 for None entries."""
    if entry is None:
        return "🟡", "待补充"
    conf = entry.get("confidence", 0)
    status = entry.get("review_status", "")
    ev = entry.get("evidence_count", 0)
    if status == "signed" and conf >= 0.8 and ev >= 2:
        return "🟢", "高置信"
    if status == "signed" and conf >= 0.65:
        return "🟡", "中等置信"
    if status == "signed":
        return "🟡", "一般置信"
    if ev > 0:
        return "🟠", "证据有限"
    return "🟡", "待补充"


def diagnose(name, sc, products, summary):
    print(f"\n== {name} ==")
    actual_dims = sorted({c.get("dimension", "") for c in sc})
    print(f"  products={products}  signed={len(sc)}  dim_keys={actual_dims}")

    # buggy lookup
    claim_map_buggy: dict[tuple[str, str], dict] = {}
    for c in sc:
        pn = c.get("product_name", "")
        if not pn or pn in ("unknown", "null", ""):
            continue
        dim = c.get("dimension", "")
        if not dim:
            continue
        claim_map_buggy.setdefault((pn, dim), {
            "evidence_count": len(c.get("evidence_ids") or []),
            "confidence": c.get("confidence", 0),
            "review_status": c.get("review_status", ""),
        })

    # fixed lookup
    claim_map_fixed: dict[tuple[str, str], dict] = {}
    for c in sc:
        pn = c.get("product_name", "")
        if not pn or pn in ("unknown", "null", ""):
            continue
        dim = c.get("dimension", "")
        if not dim:
            continue
        user_dims = SCHEMA_DIM_TO_USER_DIMS.get(dim, [dim])
        for ud in user_dims:
            key = (pn, ud)
            if key not in claim_map_fixed:
                claim_map_fixed[key] = {
                    "evidence_count": len(c.get("evidence_ids") or []),
                    "confidence": c.get("confidence", 0),
                    "review_status": c.get("review_status", ""),
                }
            else:
                claim_map_fixed[key]["evidence_count"] += len(c.get("evidence_ids") or [])

    total_cells = len(DIMENSION_LABELS) * len(products)
    hits_buggy = sum(1 for dim in DIMENSION_LABELS for p in products if claim_map_buggy.get((p, dim)))
    hits_fixed = sum(1 for dim in DIMENSION_LABELS for p in products if claim_map_fixed.get((p, dim)))
    print(f"  [BUGGY] {hits_buggy}/{total_cells} = {hits_buggy/total_cells:.0%}  |  [FIXED] {hits_fixed}/{total_cells} = {hits_fixed/total_cells:.0%}  delta +{hits_fixed - hits_buggy}")
    summary.append(f"{name:55s} {hits_buggy:>3}/{total_cells:<3} → {hits_fixed:>3}/{total_cells:<3}  (+{hits_fixed - hits_buggy})")


    if hits_buggy > 0 or hits_fixed > 0:
        print(f"\n[BEFORE] {hits_buggy}/{total_cells}  AFTER: {hits_fixed}/{total_cells}  delta: {hits_fixed - hits_buggy}")


def main():
    reports = sorted(
        p for p in (REPO / "data" / "reports").glob("report_run_*_v2.json")
        if "regression" not in p.name and "reg2" not in p.name
    )
    summary = []
    for target in reports[-15:]:  # last 15
        try:
            data = json.load(open(target))
        except Exception:
            continue
        sc = data.get("signed_claims", [])
        products = data.get("products", [])
        if not sc or not products:
            continue
        diagnose(target.name, sc, products, summary)

    print("\n" + "=" * 60)
    print("SUMMARY across reports with signed_claims")
    print("=" * 60)
    for row in summary:
        print(row)


if __name__ == "__main__":
    main()
