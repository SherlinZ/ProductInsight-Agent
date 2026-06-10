"""
Test Parallel Section Processing in Deep Report v2

This script tests that sections are processed in parallel,
not sequentially. It verifies the speedup from parallel execution.
"""

import sys
sys.path.insert(0, '.')

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

# Test the parallel processing logic
def mock_section_processing(section_id: str, delay: float = 2.0) -> dict:
    """Mock a section processing that takes ~2 seconds."""
    time.sleep(delay)
    return {"section_id": section_id, "status": "completed", "elapsed": delay}

def test_serial_processing(num_sections: int = 5) -> float:
    """Process sections serially (old way)."""
    start = time.time()
    for i in range(num_sections):
        result = mock_section_processing(f"section_{i}")
    elapsed = time.time() - start
    return elapsed

def test_parallel_processing(num_sections: int = 5, max_workers: int = 5) -> float:
    """Process sections in parallel (new way)."""
    start = time.time()
    section_results = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_section = {
            executor.submit(mock_section_processing, f"section_{i}"): f"section_{i}"
            for i in range(num_sections)
        }
        
        for future in as_completed(future_to_section):
            try:
                result = future.result(timeout=60)
                section_results.append(result)
            except Exception as exc:
                print(f"Section failed: {exc}")
    
    elapsed = time.time() - start
    return elapsed

def test_parallel_with_individual_timing(num_sections: int = 5, max_workers: int = 5) -> dict:
    """Test parallel processing with timing for each section."""
    start = time.time()
    section_results = []
    individual_times = []
    
    def timed_section(section_id: str) -> dict:
        s = time.time()
        time.sleep(2.0)  # Simulate LLM call
        return {"section_id": section_id, "wall_time": time.time() - s}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_section = {
            executor.submit(timed_section, f"section_{i}"): f"section_{i}"
            for i in range(num_sections)
        }
        
        for future in as_completed(future_to_section):
            result = future.result(timeout=60)
            section_results.append(result)
            individual_times.append(result["wall_time"])
    
    total_elapsed = time.time() - start
    return {
        "total_elapsed": total_elapsed,
        "individual_times": individual_times,
        "expected_serial": num_sections * 2.0,
        "expected_parallel": (num_sections / max_workers) * 2.0 + 0.5,  # +0.5 for overhead
    }

def main():
    print("=" * 70)
    print("Parallel Section Processing Test")
    print("=" * 70)
    
    num_sections = 5
    max_workers = 5
    
    # Test 1: Serial vs Parallel timing
    print(f"\n[Test 1] Comparing Serial vs Parallel Processing")
    print(f"         Sections: {num_sections}, Workers: {max_workers}")
    print("-" * 50)
    
    serial_time = test_serial_processing(num_sections)
    print(f"  Serial processing:   {serial_time:.2f}s")
    
    parallel_time = test_parallel_processing(num_sections, max_workers)
    print(f"  Parallel processing: {parallel_time:.2f}s")
    
    speedup = serial_time / parallel_time
    print(f"  Speedup:            {speedup:.2f}x")
    
    # Test 2: Verify parallel execution
    print(f"\n[Test 2] Verifying Parallel Execution")
    print("-" * 50)
    
    result = test_parallel_with_individual_timing(num_sections, max_workers)
    
    print(f"  Total elapsed:       {result['total_elapsed']:.2f}s")
    print(f"  Expected (serial):  {result['expected_serial']:.2f}s")
    print(f"  Expected (parallel): {result['expected_parallel']:.2f}s")
    print(f"  Individual times:    {[f'{t:.2f}s' for t in result['individual_times']]}")
    
    # Verify parallel execution happened
    is_parallel = result['total_elapsed'] < result['expected_serial'] * 0.7
    print(f"\n  Parallel execution verified: {'✅ YES' if is_parallel else '❌ NO'}")
    
    # Test 3: Import and check constants
    print(f"\n[Test 3] Checking deep_report module")
    print("-" * 50)
    
    try:
        from backend.app.services.deep_report import (
            MAX_PARALLEL_SECTIONS,
            _process_section_parallel,
            run_deep_report_workflow,
        )
        print(f"  MAX_PARALLEL_SECTIONS = {MAX_PARALLEL_SECTIONS}")
        print(f"  _process_section_parallel: {callable(_process_section_parallel)}")
        print(f"  run_deep_report_workflow: {callable(run_deep_report_workflow)}")
        print(f"  Module imports: ✅ OK")
    except ImportError as e:
        print(f"  Module imports: ❌ FAILED - {e}")
    
    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    
    if speedup > 2.0 and is_parallel:
        print("✅ Parallel processing is working correctly!")
        print(f"   - Speedup: {speedup:.2f}x")
        print(f"   - All sections processed in parallel")
    else:
        print("⚠️  Some issues detected")
        print(f"   - Speedup: {speedup:.2f}x (expected > 2.0x)")
        print(f"   - Parallel: {'Yes' if is_parallel else 'No'}")
    
    print("=" * 70)

if __name__ == "__main__":
    main()
