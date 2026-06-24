#!/usr/bin/env python3
"""
Diagnostic script to test the data collection pipeline.
"""

import sys
sys.path.insert(0, '.')

import os
# Don't chdir if already in backend

from app.services.search_provider import DoubaoWebSearchProvider, create_search_provider
from app.services.domain_schema import understand_query, generate_domain_schema

print("=" * 60)
print("DIAGNOSTIC: Data Collection Pipeline")
print("=" * 60)

# 1. Check provider
print("\n[1] Checking Search Provider...")
provider = DoubaoWebSearchProvider()
print(f"  Provider available: {provider.is_available()}")
print(f"  Provider configured: {provider.is_configured}")

# 2. Test a single search
print("\n[2] Testing single search...")
import time
start = time.time()
results = provider.search("Dify 官方文档", top_k=3)
elapsed = time.time() - start
print(f"  Query: 'Dify 官方文档'")
print(f"  Results: {len(results)}")
print(f"  Time: {elapsed:.1f}s")
if results:
    print(f"  First result: {results[0].title[:50]}...")

# 3. Test domain schema
print("\n[3] Testing Domain Schema...")
query = "分析Dify、LangChain、Coze的差异"
products = ["Dify", "LangChain", "Coze"]
u = understand_query(query, products)
print(f"  Domain: {u['domain']}")
print(f"  Report Type: {u['report_type']}")

schema = generate_domain_schema(u['domain'], products, query)
print(f"  Schema dimensions: {len(schema.get('comparison_dimensions', []))}")

# 4. Test batch search
print("\n[4] Testing batch search (parallel)...")
from concurrent.futures import ThreadPoolExecutor, as_completed

queries = [
    "Dify 官方文档",
    "LangChain 定价", 
    "Coze 产品功能"
]

start = time.time()
all_results = []

def search_one(q):
    return q, provider.search(q, top_k=3)

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(search_one, q) for q in queries]
    for future in as_completed(futures):
        q, results = future.result()
        all_results.extend(results)
        print(f"  '{q}': {len(results)} results")

elapsed = time.time() - start
print(f"\n  Total results: {len(all_results)}")
print(f"  Total time: {elapsed:.1f}s")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"✓ Provider working: {provider.is_available()}")
print(f"✓ Single search works: {len(results) > 0}")
print(f"✓ Parallel search works: {len(all_results) > 0}")
print(f"✓ Domain schema works: {u['domain'] == 'ai_agent_platform'}")
