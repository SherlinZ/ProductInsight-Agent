const API_BASE = '';

export async function fetchWorkflowData(runId: string) {
  const resp = await fetch(`${API_BASE}/api/runs/${runId}/workflow`);
  if (!resp.ok) throw new Error(`Failed to fetch workflow: ${resp.status}`);
  return resp.json();
}

// P1-Redesign (2026-06-18): Realistic mode fetches the product-parallel
// expanded DAG with rework iteration counts.
export async function fetchExpandedDAG(runId: string) {
  const resp = await fetch(`${API_BASE}/api/runs/${runId}/dag/expanded`);
  if (!resp.ok) throw new Error(`Failed to fetch expanded DAG: ${resp.status}`);
  return resp.json();
}

export async function fetchLiveData(runId: string) {
  try {
    const resp = await fetch(`${API_BASE}/api/runs/${runId}/live`);
    if (!resp.ok) return null;  // 404 / 500 — not all runs have live data
    return resp.json();
  } catch {
    return null;  // network error — gracefully degrade
  }
}
