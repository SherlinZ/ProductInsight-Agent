import { useState } from 'react';
import { useWorkflowData } from './hooks/useWorkflowData';
import WorkflowGraph from './components/WorkflowGraph';
import NodeDetails from './components/NodeDetails';
import { WorkflowNode, CollectionStats } from './types/workflow';
import { STATUS_COLORS } from './utils/labels';

function getUrlParam(key: string): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get(key);
}

export default function App() {
  const runId = getUrlParam('run_id') ?? '';
  const { workflowData, liveData, error, loading, lastUpdated } = useWorkflowData(runId);

  const [selectedNode, setSelectedNode] = useState<WorkflowNode | null>(null);

  if (!runId) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950 text-slate-400">
        <div className="text-center">
          <div className="text-4xl mb-4">DAG</div>
          <div className="text-sm">Missing run_id parameter</div>
        </div>
      </div>
    );
  }

  if (loading && !workflowData) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950 text-slate-400">
        <div className="text-center">
          <div className="text-2xl mb-2 animate-pulse">Loading DAG...</div>
          <div className="text-sm text-slate-500">run_id: {runId}</div>
        </div>
      </div>
    );
  }

  if (error && !workflowData) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950 text-red-400">
        <div className="text-center">
          <div className="text-xl mb-2">Failed to load workflow</div>
          <div className="text-sm text-slate-500">{error}</div>
        </div>
      </div>
    );
  }

  const nodes = workflowData?.nodes ?? [];
  const edges = workflowData?.edges ?? [];
  const summary = liveData?.workflow_summary;

  // Derive current running node from live data (null-safe)
  const runningNodeName = liveData?.current_node ?? '';
  const liveNodes: WorkflowNode[] = liveData?.workflow_nodes ?? [];
  const currentAgent = liveData?.current_agent ?? '';

  // Derive collection aggregate from the collect_sources node
  const collectNode = liveNodes.find(n => n.node_name === 'collect_sources');
  const collStats: CollectionStats | null = collectNode?.collection_stats ?? null;
  const collSources = collectNode?.sources ?? [];

  // Strategy breakdown for header pills
  const stratCount: Record<string, number> = {};
  for (const src of collSources) {
    stratCount[src.fetch_strategy] = (stratCount[src.fetch_strategy] ?? 0) + 1;
  }
  const STRAT_PILLS: Record<string, { bg: string; text: string }> = {
    requests:  { bg: '#065f46', text: '#34d399' },
    playwright: { bg: '#1e3a8a', text: '#93c5fd' },
    search_api: { bg: '#3b0764', text: '#e879f9' },
    fallback:  { bg: '#3b0764', text: '#e879f9' },
  };

  return (
    <div className="w-full h-full flex flex-col bg-slate-950 text-slate-200 font-sans">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900 flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="text-sm font-semibold text-slate-200">Workflow DAG</div>
          <div className="text-xs text-slate-500 font-mono">{runId}</div>
        </div>

        {/* Status summary pills */}
        <div className="flex items-center gap-2">
          {summary && (
            <>
              <Pill label="Total" value={summary.total_nodes} />
              <Pill label="Done" value={summary.completed} color={STATUS_COLORS.completed.border} />
              <Pill label="Running" value={summary.running} color={STATUS_COLORS.running.border} />
              <Pill label="Failed" value={summary.failed} color={STATUS_COLORS.failed.border} />
            </>
          )}
          {/* Collection aggregate pills */}
          {collStats && collStats.total_urls > 0 && (
            <>
              <div className="w-px h-4 bg-slate-700 mx-1" />
              <div className="flex items-center gap-1">
                <span className="text-xs text-slate-500">URLs</span>
                <span className="text-xs font-semibold text-slate-200">
                  {collStats.collected}/{collStats.total_urls}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span
                  className="text-xs font-bold"
                  style={{ color: (collStats.collected / collStats.total_urls) >= 0.8 ? '#22c55e' : (collStats.collected / collStats.total_urls) >= 0.5 ? '#f59e0b' : '#ef4444' }}
                >
                  {((collStats.collected / collStats.total_urls) * 100).toFixed(0)}%
                </span>
              </div>
              <div className="flex items-center gap-1">
                <span className="text-xs text-slate-500">chars</span>
                <span className="text-xs font-semibold text-slate-300">
                  {(collStats.total_chars / 1000).toFixed(0)}k
                </span>
              </div>
              {Object.entries(stratCount).map(([strategy, count]) => {
                const pill = STRAT_PILLS[strategy] ?? { bg: '#1e293b', text: '#94a3b8' };
                return (
                  <div
                    key={strategy}
                    className="flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium"
                    style={{ backgroundColor: pill.bg, color: pill.text }}
                  >
                    <span>{count}×{strategy}</span>
                  </div>
                );
              })}
            </>
          )}
          {lastUpdated && (
            <div className="text-xs text-slate-600 ml-2">
              Updated {lastUpdated.toLocaleTimeString()}
            </div>
          )}
        </div>

        {loading && (
          <div className="flex items-center gap-1.5 text-xs text-blue-400">
            <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
            Polling...
          </div>
        )}
      </div>

      {/* Running node highlight */}
      {(runningNodeName || collStats) && (
        <div className="flex items-center gap-3 px-4 py-1.5 bg-blue-900/20 border-b border-blue-800/30 flex-shrink-0 overflow-x-auto">
          {runningNodeName && (
            <>
              <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
              <span className="text-xs text-blue-300 flex-shrink-0">
                Running: <strong>{runningNodeName}</strong>
              </span>
            </>
          )}
          {currentAgent && runningNodeName && (
            <span className="text-xs text-slate-400 flex-shrink-0">· Agent: {currentAgent}</span>
          )}
          {/* Live collection progress */}
          {collStats && (collStats.total_urls ?? 0) > 0 && runningNodeName === 'collect_sources' && (
            <>
              <div className="w-px h-3 bg-slate-600 flex-shrink-0" />
              <div className="flex items-center gap-2 flex-shrink-0">
                <div className="flex rounded overflow-hidden h-2 w-20 bg-slate-700">
                  {[
                    { pct: (collStats.total_urls ?? 0) > 0 ? ((collStats.collected ?? 0) / (collStats.total_urls ?? 1)) * 100 : 0, color: '#22c55e' },
                    { pct: (collStats.total_urls ?? 0) > 0 ? ((collStats.failed ?? 0) / (collStats.total_urls ?? 1)) * 100 : 0, color: '#f97316' },
                  ].map((s, i) =>
                    s.pct > 0 ? (
                      <div key={i} style={{ width: `${s.pct}%`, backgroundColor: s.color }} />
                    ) : null
                  )}
                </div>
                <span className="text-xs text-slate-300 font-mono">
                  {collStats.collected ?? 0}/{collStats.total_urls ?? 0} URLs
                </span>
                <span className="text-xs text-slate-400">
                  · {(collStats.total_chars ?? 0).toLocaleString()} chars
                </span>
                <span className="text-xs text-slate-400">
                  · {(collStats.elapsed_s ?? 0) >= 1 ? `${(collStats.elapsed_s ?? 0).toFixed(0)}s` : '…'}
                </span>
              </div>
            </>
          )}
        </div>
      )}

      {/* Main content: graph + details panel */}
      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 relative">
          <WorkflowGraph
            nodes={nodes}
            edges={edges}
            onNodeClick={setSelectedNode}
          />
        </div>

        {selectedNode && (
          <NodeDetails
            node={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        )}
      </div>
    </div>
  );
}

function Pill({ label, value, color }: { label: string; value: number; color?: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-slate-800 border border-slate-700">
      <span className="text-xs text-slate-400">{label}</span>
      <span className="text-xs font-semibold" style={{ color: color ?? '#94a3b8' }}>
        {value}
      </span>
    </div>
  );
}
