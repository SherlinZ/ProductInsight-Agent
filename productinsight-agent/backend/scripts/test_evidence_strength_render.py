"""End-to-end test: actually call _generate_evidence_strength_matrix on real
report data with the patched function and compare against the pre-fix logic.

The pre-fix logic is the original claim_map builder (no schema_key expansion);
the post-fix logic is what _generate_evidence_strength_matrix now runs internally.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

REPO = Path("/home/shijialin/paperworking/workflow_new/productinsight-agent")
sys.path.insert(0, str(REPO))  # so 'backend.app.services.deep_report' resolves

# Load the patched module
from backend.app.services import deep_report as dr  # noqa: E402

# Replicate the OLD buggy claim_map builder for comparison
OLD_DIMENSION_LABELS = {
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


def old_claim_map(signed_claims):
    """Pre-fix builder: stores (product, claim.dimension) directly."""
    out = {}
    for c in signed_claims:
        pn = c.get("product_name", "")
        dim = c.get("dimension", "")
        if not pn or not dim:
            continue
        key = (pn, dim)
        if key not in out:
            out[key] = {
                "evidence_count": len(c.get("evidence_ids") or []),
                "confidence": c.get("confidence", 0),
                "review_status": c.get("review_status", ""),
            }
    return out


def summarize_legend_rows(render_ctx, claim_map, products):
    """Show which cells the (product, user_dim) lookup would fill."""
    fills = []
    for dim_key, dim_label in OLD_DIMENSION_LABELS.items():
        for p in products:
            if (p, dim_key) in claim_map:
                e = claim_map[(p, dim_key)]
                ev = e["evidence_count"]
                fills.append(f"  ({p}, {dim_label}) → ev={ev}, conf={e['confidence']:.2f}, status={e['review_status']}")
    return fills


def render_section(report_id, run_id, render_ctx):
    """Call the patched generator."""
    return dr._generate_evidence_strength_matrix(report_id, run_id, render_ctx)


def main():
    reports_dir = REPO / "data" / "reports"
    candidates = sorted(
        p for p in reports_dir.glob("report_run_*_v2.json")
        if "regression" not in p.name and "reg2" not in p.name
    )

    for target in candidates[-30:]:
        try:
            data = json.load(open(target))
        except Exception:
            continue
        sc = data.get("signed_claims", [])
        products = data.get("products", [])
        if not sc or not products:
            continue

        print(f"\n{'=' * 70}")
        print(f"== {target.name}  ({len(sc)} claims, {len(products)} products)")
        print("=" * 70)

        # Build the old claim_map for comparison
        old_map = old_claim_map(sc)
        # Count user-dimension fills using OLD map
        old_fills = sum(
            1 for dim in OLD_DIMENSION_LABELS for p in products
            if (p, dim) in old_map
        )
        total_cells = len(OLD_DIMENSION_LABELS) * len(products)
        print(f"\n[OLD] matrix coverage: {old_fills}/{total_cells} = {old_fills/total_cells:.0%}")

        # Build render_ctx and call patched function
        # Need minimal render_ctx: products, signed_claims, coverage_by_product
        render_ctx = {
            "products": products,
            "signed_claims": sc,
            "coverage_by_product": {p: 0.5 for p in products},  # neutral value
            "evidence_items": data.get("evidence_registry", []),
        }
        report_id = data.get("report_id", "rep_test")
        run_id = data.get("run_id", "run_test")
        section = render_section(report_id, run_id, render_ctx)

        # Count filled cells in section output
        new_fills = section.count("高置信") + section.count("中等置信") + section.count("一般置信") + section.count("证据有限")
        new_fills = min(new_fills, total_cells)  # upper bound
        print(f"[NEW] matrix coverage in rendered section: at least {new_fills}/{total_cells} cells")
        print(f"\n--- Rendered section preview (first 1200 chars) ---")
        print(section[:1200])
        print(f"\n... (truncated, total {len(section)} chars)")


if __name__ == "__main__":
    main()
