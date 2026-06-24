import { useState, useEffect, useCallback, useRef } from 'react';
import { WorkflowData, LiveData } from '../types/workflow';
import {
  fetchWorkflowData,
  fetchExpandedDAG,
  fetchLiveData,
} from '../api/backend';

const POLL_INTERVAL_MS = 4000;

export interface WorkflowState {
  workflowData: WorkflowData | null;
  liveData: LiveData | null;
  error: string | null;
  loading: boolean;
  lastUpdated: Date | null;
}

// P1-Redesign (2026-06-18): Accept a mode flag so the hook can swap between
// the fixed-backbone endpoint and the product-parallel expanded endpoint.
export function useWorkflowData(runId: string, mode: 'simple' | 'realistic' = 'simple') {
  const [state, setState] = useState<WorkflowState>({
    workflowData: null,
    liveData: null,
    error: null,
    loading: true,
    lastUpdated: null,
  });

  const stoppedRef = useRef(false);

  const load = useCallback(async () => {
    if (!runId || stoppedRef.current) return;
    try {
      // Fetch workflow (required) and live (optional) in parallel.
      // mode === 'realistic' swaps the workflow endpoint for /dag/expanded.
      const [wd, ld] = await Promise.all([
        mode === 'realistic' ? fetchExpandedDAG(runId) : fetchWorkflowData(runId),
        fetchLiveData(runId),
      ]);
      if (stoppedRef.current) return;
      setState({
        workflowData: wd,
        liveData: ld,
        error: null,
        loading: false,
        lastUpdated: new Date(),
      });
    } catch (err) {
      if (stoppedRef.current) return;
      setState(prev => ({
        ...prev,
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      }));
    }
  }, [runId, mode]);

  // Initial load + polling
  useEffect(() => {
    stoppedRef.current = false;
    setState(prev => ({ ...prev, loading: true, error: null }));
    load();

    const interval = setInterval(() => {
      if (!stoppedRef.current) {
        load();
      }
    }, POLL_INTERVAL_MS);

    return () => {
      clearInterval(interval);
      stoppedRef.current = true;
    };
  }, [load]);

  return state;
}
