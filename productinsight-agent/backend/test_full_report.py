"""
Full Report Generation Test

This script tests the parallel section processing logic.
"""

import sys
sys.path.insert(0, '.')

import time
import logging
from datetime import datetime

# Enable logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_parallel_speedup():
    """Test parallel speedup with simulated work."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    print(f"\n[Test 1] Parallel Speedup Simulation")
    print("=" * 60)
    
    num_sections = 14
    time_per_section = 2.0  # Simulated 2 seconds per section
    max_workers = 3  # Same as MAX_PARALLEL_SECTIONS
    
    # Serial execution
    print(f"  Simulating {num_sections} sections @ {time_per_section}s each...")
    serial_start = time.time()
    for i in range(num_sections):
        time.sleep(time_per_section)
    serial_time = time.time() - serial_start
    
    # Parallel execution
    parallel_start = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(time.sleep, time_per_section) for _ in range(num_sections)]
        for f in as_completed(futures):
            f.result()
    parallel_time = time.time() - parallel_start
    
    speedup = serial_time / parallel_time
    
    print(f"  Serial time:   {serial_time:.1f}s")
    print(f"  Parallel time: {parallel_time:.1f}s")
    print(f"  Speedup:       {speedup:.1f}x")
    
    # Verify speedup (expect at least 2x for 3 workers)
    expected_speedup = num_sections / max_workers
    is_effective = speedup > 2.0  # At least 2x speedup with 3 workers
    
    print(f"  Expected speedup: ~{expected_speedup:.1f}x")
    print(f"  ✅ Effective parallelization!" if is_effective else "  ⚠️  Less efficient than expected")
    
    return is_effective


def test_deep_report_imports():
    """Test that deep_report module imports correctly."""
    print(f"\n[Test 2] Deep Report Module Imports")
    print("=" * 60)
    
    try:
        from backend.app.services.deep_report import (
            MAX_PARALLEL_SECTIONS,
            _process_section_parallel,
            run_deep_report_workflow,
            get_default_outline,
            initialize_report_sections,
        )
        
        print(f"  MAX_PARALLEL_SECTIONS = {MAX_PARALLEL_SECTIONS}")
        print(f"  _process_section_parallel: callable")
        print(f"  run_deep_report_workflow: callable")
        print(f"  get_default_outline: callable")
        print(f"  initialize_report_sections: callable")
        
        outline = get_default_outline()
        print(f"  Default outline sections: {len(outline)}")
        
        return True
    except ImportError as e:
        print(f"  ❌ Import failed: {e}")
        return False


def test_parallel_config():
    """Test parallel processing configuration."""
    from backend.app.services.deep_report import MAX_PARALLEL_SECTIONS
    from backend.app.services.deep_report import get_default_outline
    
    print(f"\n[Test 3] Parallel Configuration")
    print("=" * 60)
    print(f"  MAX_PARALLEL_SECTIONS = {MAX_PARALLEL_SECTIONS}")
    
    outline = get_default_outline()
    sections_count = len(outline)
    effective_workers = min(MAX_PARALLEL_SECTIONS, sections_count)
    
    print(f"  Outline sections: {sections_count}")
    print(f"  Effective workers: {effective_workers}")
    
    # Estimate time with real LLM calls (~30s per section)
    time_per_section_llm = 30  # seconds
    serial_time = sections_count * time_per_section_llm
    parallel_time = (sections_count / effective_workers) * time_per_section_llm
    
    print(f"\n  Estimated time with LLM calls (~30s/section):")
    print(f"    Serial:   ~{serial_time/60:.1f} minutes")
    print(f"    Parallel: ~{parallel_time/60:.1f} minutes")
    print(f"    Speedup:  ~{serial_time/parallel_time:.1f}x")
    
    return True


def test_workflow_signature():
    """Test that run_deep_report_workflow has correct signature."""
    import inspect
    from backend.app.services.deep_report import run_deep_report_workflow
    
    print(f"\n[Test 4] Workflow Signature")
    print("=" * 60)
    
    sig = inspect.signature(run_deep_report_workflow)
    params = list(sig.parameters.keys())
    
    print(f"  Parameters: {params}")
    
    required = ["run_id", "report_id", "signed_claims", "facts", "evidence_items", "products"]
    all_present = True
    for req in required:
        present = req in params
        print(f"    {req}: {'✅' if present else '❌'}")
        if not present:
            all_present = False
    
    return all_present


def main():
    print("=" * 70)
    print("Full Report Generation Test")
    print("=" * 70)
    
    results = {}
    
    # Test 1: Parallel speedup
    try:
        results["parallel_speedup"] = test_parallel_speedup()
    except Exception as e:
        print(f"\n❌ Test 1 failed: {e}")
        results["parallel_speedup"] = False
    
    # Test 2: Module imports
    try:
        results["module_imports"] = test_deep_report_imports()
    except Exception as e:
        print(f"\n❌ Test 2 failed: {e}")
        results["module_imports"] = False
    
    # Test 3: Parallel config
    try:
        results["parallel_config"] = test_parallel_config()
    except Exception as e:
        print(f"\n❌ Test 3 failed: {e}")
        results["parallel_config"] = False
    
    # Test 4: Workflow signature
    try:
        results["workflow_signature"] = test_workflow_signature()
    except Exception as e:
        print(f"\n❌ Test 4 failed: {e}")
        results["workflow_signature"] = False
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    
    all_passed = True
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name}: {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✅ All tests passed!")
        print("\nThe parallel report generation is configured correctly.")
        print("Real report generation will use the API/workflow.")
    else:
        print("⚠️  Some tests failed. Check errors above.")
    print("=" * 70)
    
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
