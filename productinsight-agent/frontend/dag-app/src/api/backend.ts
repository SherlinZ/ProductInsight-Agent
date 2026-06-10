const API_BASE = '';

export async function fetchWorkflowData(runId: string) {
  const resp = await fetch(`${API_BASE}/api/runs/${runId}/workflow`);
  if (!resp.ok) throw new Error(`Failed to fetch workflow: ${resp.status}`);
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
