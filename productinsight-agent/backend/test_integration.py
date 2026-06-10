"""
Integration Test for Domain Schema + SearchProvider + Evidence Gap

This script tests the complete pipeline without running the full workflow service.
Useful for debugging and verifying the generalization capability.
"""

import sys
sys.path.insert(0, '.')

from backend.app.services.domain_schema import (
    understand_query,
    generate_domain_schema,
    get_generic_report_outline,
)
from backend.app.services.search_provider import (
    generate_search_queries,
    create_search_provider,
    get_search_config,
    FixtureProvider,
    SeedUrlProvider,
)
from backend.app.services.evidence_quality_gate import EvidenceItemQualityGate
from backend.app.services.evidence_gap import EvidenceGapDetector, EvidenceGapReporter


def test_domain(domain_name: str, query: str, products: list[str]):
    """Test a complete domain pipeline."""
    print(f"\n{'='*60}")
    print(f"Testing: {domain_name}")
    print(f"Query: {query}")
    print(f"Products: {products}")
    print("="*60)
    
    # Step 1: Query Understanding
    u = understand_query(query, products)
    print(f"\n[1] Query Understanding:")
    print(f"    Domain: {u['domain']}")
    print(f"    Report Type: {u['report_type']}")
    
    # Step 2: Generate Domain Schema
    schema = generate_domain_schema(u['domain'], products, query)
    print(f"\n[2] Domain Schema:")
    print(f"    Name: {schema['name']}")
    print(f"    Dimensions: {len(schema['comparison_dimensions'])}")
    print(f"    Seed URLs: {len(schema.get('seed_urls', []))}")
    if schema.get('seed_urls'):
        print(f"      Example: {schema['seed_urls'][0]}")
    
    # Step 3: Generate Search Queries
    queries = generate_search_queries(u['domain'], products, schema)
    print(f"\n[3] Search Queries:")
    print(f"    Generated: {len(queries)} queries")
    print(f"    Examples: {queries[:3]}")
    
    # Step 4: Search
    provider = create_search_provider(mode="fixture", domain=u['domain'])
    print(f"\n[4] Search:")
    print(f"    Provider: {type(provider).__name__}")
    
    all_results = []
    for q in queries[:5]:  # Limit queries
        results = provider.search(q, top_k=3)
        all_results.extend(results)
    print(f"    Results: {len(all_results)}")
    
    # Step 5: Quality Gate
    gate = EvidenceItemQualityGate()
    good_evidence = []
    for r in all_results:
        item = {"content": r.snippet, "url": r.url, "title": r.title}
        score = gate.evaluate_evidence_item(item)
        if score.is_acceptable():
            good_evidence.append(item)
    print(f"\n[5] Quality Gate:")
    print(f"    Filtered: {len(all_results)} → {len(good_evidence)}")
    
    # Step 6: Evidence Gap
    evidence_by_dim = {}
    for dim in schema['comparison_dimensions'][:3]:  # First 3 dims
        evidence_by_dim[dim['dimension']] = [{"content": f"Mock evidence for {dim['chinese']}"}]
    
    detector = EvidenceGapDetector(schema=schema, domain=u['domain'])
    gaps = detector.detect_gaps(evidence_by_dim)
    reporter = EvidenceGapReporter(gaps)
    summary = reporter.generate_summary()
    
    print(f"\n[6] Evidence Gap:")
    print(f"    Gaps detected: {len(gaps)}")
    print(f"    Confidence: {summary['confidence_level']}")
    if gaps:
        print(f"    Example gap: {gaps[0].dimension} ({gaps[0].gap_type})")
    
    # Step 7: Report Outline
    outline = get_generic_report_outline(u['report_type'], schema, products)
    print(f"\n[7] Report Outline:")
    print(f"    Sections: {len(outline)}")
    print(f"    Examples: {[s['slug'] for s in outline[:3]]}")
    
    return {
        "domain": u['domain'],
        "schema": schema,
        "queries": queries,
        "results": len(all_results),
        "gaps": len(gaps),
        "outline_sections": len(outline),
    }


def main():
    print("="*70)
    print("Domain Schema + SearchProvider Integration Test")
    print("="*70)
    
    results = []
    
    # Test all domains
    tests = [
        ("AI Agent Platform", "分析Dify、LangChain、Coze的差异", ["Dify", "LangChain", "Coze"]),
        ("Coffee Chain", "分析瑞幸、星巴克、Manner咖啡", ["瑞幸", "星巴克", "Manner"]),
        ("EV Automobile", "分析特斯拉、比亚迪、小米汽车", ["特斯拉", "比亚迪", "小米汽车"]),
        ("HR SaaS", "帮我选HR SaaS：北森 vs 薪人薪事", ["北森", "薪人薪事"]),
        ("Productivity App", "分析Notion、Confluence、Coda的差异", ["Notion", "Confluence", "Coda"]),
    ]
    
    for domain_name, query, products in tests:
        r = test_domain(domain_name, query, products)
        results.append(r)
    
    # Summary
    print("\n" + "="*70)
    print("Summary")
    print("="*70)
    print(f"{'Domain':<25} {'Dimensions':<12} {'Queries':<10} {'Gaps':<8} {'Sections':<10}")
    print("-"*70)
    for r in results:
        print(f"{r['domain']:<25} {len(r['schema']['comparison_dimensions']):<12} "
              f"{len(r['queries']):<10} {r['gaps']:<8} {r['outline_sections']:<10}")
    
    print("\n" + "="*70)
    print("All tests passed!")
    print("="*70)


if __name__ == "__main__":
    main()
