"""
Rule-based Insight Synthesizer.

Takes structured facts, product coverage, and comparison matrix
and produces high-quality key findings without raw evidence snippets.
"""
from __future__ import annotations
import json
from typing import Any


def synthesize_findings(
    facts: list[dict[str, Any]],
    product_coverage: dict[str, dict],
    comparison_matrix: list[dict[str, Any]],
    signed_claims: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Synthesize competitive intelligence findings from structured data.

    Returns a list of finding dicts, each containing:
        - text: complete analysis sentence
        - related_products: list of product slugs
        - dimension: schema dimension
        - evidence_count: int
        - confidence: float
        - finding_type: differentiation | coverage_gap | risk | opportunity | general
    """
    findings: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    def add(dimension, ftype, text, related_products, evidence_count, confidence):
        key = (dimension, ftype)
        if key in seen_types and ftype != "general":
            return
        seen_types.add(key)
        findings.append({
            "text": text,
            "related_products": sorted(set(related_products)),
            "dimension": dimension,
            "evidence_count": evidence_count,
            "confidence": round(confidence, 3),
            "finding_type": ftype,
        })

    # Group facts by dimension and product
    from collections import defaultdict
    dim_map: dict[tuple, list[dict]] = defaultdict(list)
    for f in (facts or []):
        dim = f.get("schema_key", "general")
        prod = f.get("product_slug") or f.get("product_id", "")
        dim_map[(dim, prod)].append(f)

    # 1. DIFFERENTIATION: products that outperform others in a dimension
    all_dims = set(d for d, p in dim_map.keys())
    for dim in all_dims:
        dim_facts = {p: flist for (d, p), flist in dim_map.items() if d == dim}
        if not dim_facts:
            continue
        # Find product with highest confidence in this dimension
        best_prod = max(dim_facts.items(), key=lambda x: max(f.get("confidence", 0) for f in x[1]))
        best_name, best_facts = best_prod
        best_conf = max(f.get("confidence", 0) for f in best_facts)
        others = [p for p in dim_facts if p != best_name]
        if others and best_conf >= 0.65:
            others_with_data = [
                p for p in others
                if any(f.get("confidence", 0) >= 0.5 for f in dim_facts.get(p, []))
            ]
            if not others_with_data:
                dim_display = dim.replace("_", " ").title()
                add(
                    dimension=dim,
                    ftype="differentiation",
                    text=f"{best_name.title()} has the strongest evidence for {dim_display.lower()} among the products analyzed, with no comparable data found for competing products.",
                    related_products=[best_name] + others,
                    evidence_count=len(best_facts),
                    confidence=best_conf,
                )

    # 2. COVERAGE GAPS: products with insufficient evidence
    for slug, cov in (product_coverage or {}).items():
        status = cov.get("coverage_status", "unknown")
        ev_count = cov.get("evidence_count", 0)
        if status in ("weak", "missing"):
            dim_display = slug.replace("_", " ").title()
            if status == "missing":
                add(
                    dimension="coverage",
                    ftype="coverage_gap",
                    text=f"{dim_display} currently has no sufficient public evidence in this run. Conclusions about {dim_display} should be treated as low-confidence pending additional research.",
                    related_products=[slug],
                    evidence_count=ev_count,
                    confidence=0.3,
                )
            else:
                add(
                    dimension="coverage",
                    ftype="coverage_gap",
                    text=f"{dim_display} has limited evidence coverage ({ev_count} items). The competitive analysis for {dim_display} may be incomplete.",
                    related_products=[slug],
                    evidence_count=ev_count,
                    confidence=0.45,
                )

    # 3. DEPLOYMENT INSIGHT: self-hosted as differentiator
    deploy_facts = dim_map.get(("deployment_options", ""), [])
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "deployment_options"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        has_self_hosted = any(
            any(m in str(v.get("deployment_methods", [])) for m in ["self-hosted", "docker"])
            for v in vals
        )
        if has_self_hosted:
            conf = max(f.get("confidence", 0) for f in flist)
            add(
                dimension="deployment_options",
                ftype="differentiation",
                text=f"{prod.title()} offers self-hosted deployment capabilities (Docker/Kubernetes), which is a key differentiator for privacy-conscious teams.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 4. PRICING INSIGHT: free tier differentiator
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "pricing_model"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        has_free = any(v.get("has_free_tier") for v in vals if isinstance(v, dict))
        if has_free:
            conf = max(f.get("confidence", 0) for f in flist)
            add(
                dimension="pricing_model",
                ftype="opportunity",
                text=f"{prod.title()} offers a free tier, lowering the barrier to entry for evaluation and small-scale use.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 5. WORKFLOW INSIGHT: visual workflow builder
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "workflow"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        strong = any(v.get("capability_level") == "strong" for v in vals if isinstance(v, dict))
        if strong:
            conf = max(f.get("confidence", 0) for f in flist)
            add(
                dimension="workflow",
                ftype="opportunity",
                text=f"{prod.title()} provides strong workflow orchestration with visual builder capabilities, enabling complex automation without coding.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 6. PRICING DATA GAP: pricing evidence missing
    pricing_products = [p for (d, p) in dim_map.keys() if d == "pricing_model"]
    all_products_with_coverage = set(product_coverage.keys()) if product_coverage else set()
    pricing_gap = all_products_with_coverage - set(pricing_products)
    if pricing_gap:
        add(
            dimension="pricing_model",
            ftype="risk",
            text=f"Pricing evidence is missing for {', '.join(sorted(p.title() for p in pricing_gap))}. Report conclusions should not be used for procurement decisions without supplementing pricing data.",
            related_products=sorted(pricing_gap),
            evidence_count=0,
            confidence=0.5,
        )

    # 7. RAG/KNOWLEDGE BASE: knowledge management capabilities
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "knowledge_base"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        rag_supported = any(v.get("rag_supported") for v in vals if isinstance(v, dict))
        if rag_supported:
            conf = max(f.get("confidence", 0) for f in flist)
            add(
                dimension="knowledge_base",
                ftype="opportunity",
                text=f"{prod.title()} supports RAG-based knowledge management with vector retrieval, enabling sophisticated document-based Q&A.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 8. MODEL SUPPORT: multi-model support
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "model_support"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        models = []
        for v in vals:
            if isinstance(v, dict):
                models.extend(v.get("supported_models", []))
        if len(models) >= 2:
            conf = max(f.get("confidence", 0) for f in flist)
            add(
                dimension="model_support",
                ftype="opportunity",
                text=f"{prod.title()} supports multiple AI models ({', '.join(models[:4])}), providing flexibility in LLM selection.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 9. ENTERPRISE READINESS: security features
    for prod, flist in [(p, f) for (d, p), f in dim_map.items() if d == "enterprise_readiness"]:
        try:
            vals = [json.loads(f.get("value_json", "{}")) for f in flist]
        except Exception:
            vals = []
        enterprise_features = [
            (k, label) for k, label in [("rbac","RBAC"),("sso","SSO"),("audit_log","Audit Logs"),("encryption","Encryption")]
            if any(v.get(k) for v in vals if isinstance(v, dict))
        ]
        if enterprise_features:
            conf = max(f.get("confidence", 0) for f in flist)
            features_str = ", ".join(l for _, l in enterprise_features)
            add(
                dimension="enterprise_readiness",
                ftype="opportunity",
                text=f"{prod.title()} provides enterprise-grade features: {features_str}, making it suitable for regulated industries.",
                related_products=[prod],
                evidence_count=len(flist),
                confidence=conf,
            )

    # 10. GENERAL: if still no findings, synthesize from best fact summaries
    if not findings:
        best_facts = sorted((facts or []), key=lambda f: f.get("confidence", 0), reverse=True)[:3]
        for f in best_facts:
            raw = f.get("value_json", "{}")
            try:
                v = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                v = {}
            summary = v.get("summary", f.get("schema_key", "")) if isinstance(v, dict) else str(raw)[:120]
            add(
                dimension=f.get("schema_key", "general"),
                ftype="general",
                text=f"{summary}",
                related_products=[f.get("product_slug") or f.get("product_id", "")],
                evidence_count=1,
                confidence=f.get("confidence", 0.5),
            )

    # Sort: differentiation > opportunity > coverage_gap > risk > general
    ORDER = {"differentiation": 0, "opportunity": 1, "coverage_gap": 2, "risk": 3, "general": 4}
    findings.sort(key=lambda x: (ORDER.get(x["finding_type"], 5), -x["confidence"]))
    return findings[:8]
