import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
  BackgroundVariant,
  MarkerType,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  ReactFlowInstance,
} from 'reactflow';
import 'reactflow/dist/style.css';

import { WorkflowNode, WorkflowEdge, CollectionStats } from '../types/workflow';
import { getNodeLabel, getNodeAgent, STATUS_COLORS, EDGE_LABELS } from '../utils/labels';

interface WorkflowGraphProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  onNodeClick?: (node: WorkflowNode) => void;
}

// ── Layout constants ──────────────────────────────────────────────
const LANE_W   = 260;
const STEP_H   = 120;
const ORIGIN_X = 40;
const ORIGIN_Y = 30;

// Phase lanes: 0=规划, 1=采集, 2=评估, 3=分析, 4=写作, 5=输出
const PHASE_LANES: Record<string, number> = {
  'build_task_brief':           0,
  'plan_schema':                0,
  'plan_sources':               0,
  'collect_sources':            1,
  'pii_scrub':                 1,
  'extract_facts':              2,
  'evaluate_evidence':          2,
  'detect_schema_gaps':         2,
  'analyze_dimensions':         3,
  'review_claims':              3,
  'execute_rework':             3,
  'prepare_human_intervention': 3,
  'write_report_v2':           4,
  'final_review':               5,
  'export_report':              5,
  'compute_metrics':            5,
};

function computeLayout(
  nodes: WorkflowNode[],
  _edges: WorkflowEdge[],
): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const phaseCounter: Record<number, number> = {};

  // P1-Redesign (2026-06-18): separate lane for parallel worker nodes.
  // Parallel workers are placed BELOW the backbone in their own horizontal row,
  // distributed evenly by product_index.
  const parallelGroup: Record<string, WorkflowNode[]> = {};
  for (const n of nodes) {
    if (n.node_type === 'parallel_worker') {
      const grp = (n as unknown as { parallel_group?: string }).parallel_group ?? 'collect_parallel';
      if (!parallelGroup[grp]) parallelGroup[grp] = [];
      parallelGroup[grp].push(n);
    }
  }

  for (const node of nodes) {
    if (node.node_type === 'parallel_worker') {
      // Handled below; skip the phase lane.
      continue;
    }
    const lane = PHASE_LANES[node.node_name] ?? 0;
    phaseCounter[lane] = (phaseCounter[lane] ?? -1) + 1;
    positions[node.node_name] = {
      x: ORIGIN_X + lane * LANE_W,
      y: ORIGIN_Y + phaseCounter[lane] * STEP_H,
    };
  }

  // Lay out parallel workers in a row below the backbone.
  // Y = (max backbone phase + 1) * STEP_H, X = centered around the group anchor.
  const maxPhase = Math.max(0, ...Object.values(phaseCounter));
  const parallelY = ORIGIN_Y + (maxPhase + 1) * STEP_H + 60; // 60px gap below backbone
  const PARALLEL_W = 180;
  for (const grp of Object.keys(parallelGroup)) {
    const workers = parallelGroup[grp].sort((a, b) => {
      const ai = (a as unknown as { product_index?: number }).product_index ?? 0;
      const bi = (b as unknown as { product_index?: number }).product_index ?? 0;
      return ai - bi;
    });
    const totalWidth = workers.length * PARALLEL_W;
    // Anchor x based on which group: collect near LANE 0, evaluate near LANE 1
    const groupAnchorX = grp === 'collect_parallel' ? ORIGIN_X + 80 : ORIGIN_X + 80;
    workers.forEach((w, idx) => {
      positions[w.node_name] = {
        x: groupAnchorX + idx * PARALLEL_W - totalWidth / 2,
        y: parallelY,
      };
    });
  }

  return positions;
}

// ── Collection badge ──────────────────────────────────────────────
function CollectionBadge({ stats }: { stats: CollectionStats }) {
  if (!stats || !stats.total_urls || stats.total_urls === 0) return null;
  const total = stats.total_urls;
  const collected = stats.collected ?? 0;
  const failed = stats.failed ?? 0;
  const skipped = stats.skipped ?? 0;
  const rate = total > 0 ? (collected / total) * 100 : 0;
  const segments = [
    { pct: total > 0 ? (collected / total) * 100 : 0, color: '#22c55e' },
    { pct: total > 0 ? (failed / total) * 100 : 0, color: '#f97316' },
    { pct: total > 0 ? (skipped / total) * 100 : 0, color: '#64748b' },
  ];
  return (
    <div className="mt-1.5 space-y-1">
      <div className="flex items-center gap-1.5 text-[10px]">
        <div className="flex rounded overflow-hidden h-1.5 w-16 bg-slate-700">
          {segments.map((s, i) =>
            s.pct > 0 ? (
              <div key={i} style={{ width: `${s.pct}%`, backgroundColor: s.color }} />
            ) : null
          )}
        </div>
        <span className="text-slate-400">
          {collected}/{total}
        </span>
        <span
          className="font-semibold"
          style={{ color: rate >= 80 ? '#22c55e' : rate >= 50 ? '#f59e0b' : '#ef4444' }}
        >
          {rate.toFixed(0)}%
        </span>
      </div>
    </div>
  );
}

// ── Custom node component ────────────────────────────────────────
// Must be memoized so nodeTypes stays referentially stable across renders.
const WorkflowNodeComponent = React.memo(function WorkflowNodeComponent({
  data,
  selected,
}: {
  data: Record<string, unknown>;
  selected: boolean;
}) {
  const node = data.node as WorkflowNode;
  const isParallel = (node as unknown as { node_type?: string }).node_type === 'parallel_worker';
  const isParallelGroup = (node as unknown as { node_type?: string }).node_type === 'parallel_group';

  // P1-Redesign: parallel nodes get their own color scheme (amber) so they
  // visually stand out from the main backbone (blue/green).
  let colors;
  if (isParallelGroup) {
    colors = {
      bg: '#3b0764',
      border: '#e879f9',
      text: '#f5d0fe',
    };
  } else if (isParallel) {
    colors = {
      bg: '#3b0764',
      border: '#a78bfa',
      text: '#e9d5ff',
    };
  } else {
    colors = STATUS_COLORS[node.status] ?? STATUS_COLORS.pending;
  }

  const isCollector = node.node_name === 'collect_sources';
  const stats: CollectionStats | null = node.collection_stats ?? null;

  // Display label override for parallel worker / group nodes
  const displayLabel = (node as unknown as { label?: string }).label ?? getNodeLabel(node.node_name);
  const agentLabel = isParallel
    ? 'Per-Product Worker'
    : isParallelGroup
    ? 'Parallel Group'
    : getNodeAgent(node.node_name);

  return (
    <div
      className="relative px-4 py-3 rounded-xl border-2 min-w-[160px] max-w-[200px] cursor-pointer transition-all duration-300"
      style={{
        backgroundColor: colors.bg,
        borderColor: colors.border,
        color: colors.text,
        outline: selected ? `2px solid ${colors.border}` : 'none',
        outlineOffset: '2px',
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!w-2 !h-2 !min-w-[8px] !min-h-[8px]"
      />

      <div className="text-xs font-medium mb-1 opacity-70">
        {agentLabel}
      </div>
      <div className="text-sm font-semibold leading-tight">
        {displayLabel}
      </div>
      <div className="text-xs mt-1 opacity-60">{node.status.toUpperCase()}</div>

      {isCollector && stats && (
        <CollectionBadge stats={stats} />
      )}

      {node.latency_ms > 0 && (
        <div className="text-xs mt-1 opacity-50">
          {node.latency_ms >= 1000
            ? `${(node.latency_ms / 1000).toFixed(1)}s`
            : `${node.latency_ms}ms`}
        </div>
      )}

      {node.status === 'running' && (
        <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-blue-500 animate-ping" />
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        className="!w-2 !h-2 !min-w-[8px] !min-h-[8px]"
      />
    </div>
  );
});

// Stable nodeTypes — defined at module level (after WorkflowNodeComponent) so
// React Flow never sees a new object reference on re-renders.
const nodeTypes = { workflowNode: WorkflowNodeComponent };

// ── Build helpers ────────────────────────────────────────────────
function buildNodes(
  rawNodes: WorkflowNode[],
  positions: Record<string, { x: number; y: number }>,
): Node[] {
  return rawNodes.map(node => ({
    id: node.node_name,
    type: 'workflowNode',
    position: positions[node.node_name] ?? { x: 0, y: 0 },
    data: { node },
  }));
}

function buildEdges(rawEdges: WorkflowEdge[]): Edge[] {
  return rawEdges.map(edge => {
    const key = `${edge.from_node}->${edge.to_node}`;
    const baseLabel = EDGE_LABELS[key];

    // P1-Redesign: rework edge styling
    const reworkCount = (edge as unknown as { rework_count?: number }).rework_count ?? 0;

    const isConditional = edge.edge_type === 'conditional';
    const isRework = reworkCount > 0;
    const isParallelFan = edge.edge_type === 'parallel_fan_out' || edge.edge_type === 'parallel_fan_in';

    const LOOPBACK_NODES = new Set(['write_report_v2']);
    const isLoopback = LOOPBACK_NODES.has(edge.to_node) && isConditional;

    // Label: append rework count when present
    let labelText: string | undefined = baseLabel;
    if (isRework) {
      labelText = `${baseLabel ?? ''} (rework ×${reworkCount})`.trim();
    } else if (isParallelFan) {
      // Show "×N" only on fan-out, fan-in edges stay clean
      labelText = edge.edge_type === 'parallel_fan_out' ? '×N' : undefined;
    }

    return {
      id: edge.edge_id,
      source: edge.from_node,
      target: edge.to_node,
      label: labelText ?? undefined,
      type: 'default',
      animated: isConditional || isRework,
      style: {
        stroke: isRework ? '#e879f9'
             : isLoopback ? '#e879f9'
             : isParallelFan ? '#a78bfa'
             : isConditional ? '#f97316'
             : '#475569',
        strokeDasharray: isConditional ? '6 4'
                      : isRework ? '4 3'
                      : isParallelFan ? '3 3'
                      : undefined,
        strokeWidth: isRework || isLoopback ? 2 : 1.5,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isRework || isLoopback ? '#e879f9'
             : isParallelFan ? '#a78bfa'
             : isConditional ? '#f97316'
             : '#475569',
      },
      labelStyle: {
        fill: isRework ? '#f5d0fe' : '#94a3b8',
        fontSize: 11,
        fontFamily: 'Segoe UI, system-ui, sans-serif',
        fontWeight: isRework ? 600 : 400,
      },
      labelBgStyle: { fill: '#0f172a', fillOpacity: 0.9 },
      labelBgPadding: [4, 8] as [number, number],
      labelBgBorderRadius: 4,
    };
  });
}

// ── Hook: fitView after nodes load ───────────────────────────────
function useFitViewOnLoad(shouldFit: boolean, instanceRef: ReactFlowInstance | null) {
  useEffect(() => {
    if (!shouldFit || !instanceRef) return;
    const id = setTimeout(() => {
      instanceRef.fitView({ padding: 0.2 });
    }, 50);
    return () => clearTimeout(id);
  }, [shouldFit, instanceRef]);
}

// ── Main component ────────────────────────────────────────────────
export default function WorkflowGraph({
  nodes,
  edges,
  onNodeClick,
}: WorkflowGraphProps) {
  const positions = useMemo(() => computeLayout(nodes, edges), [nodes, edges]);

  // Null-initialized so ReactFlow never sees [] before real data arrives.
  // This prevents the "fitView on empty canvas" bug where fitView runs before
  // nodes are available, leaving the viewport at scale=0.
  const [rfNodes, setNodes, onNodesChange] = useNodesState([]);
  const [rfEdges, setEdges, onEdgesChange] = useEdgesState([]);

  const initializedRef = useRef(false);
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null);
  const [shouldFit, setShouldFit] = useState(false);

  // Initialize state when real data first arrives; also trigger fitView.
  useEffect(() => {
    if (!nodes.length) return;
    setNodes(buildNodes(nodes, positions));
    setEdges(buildEdges(edges));
    setShouldFit(true);
    initializedRef.current = true;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges]);

  // Sync status updates from polling (after first init).
  useEffect(() => {
    if (!initializedRef.current || !nodes.length) return;
    setNodes(buildNodes(nodes, positions));
  }, [nodes, positions, setNodes]);

  useEffect(() => {
    if (!initializedRef.current || !edges.length) return;
    setEdges(buildEdges(edges));
  }, [edges, setEdges]);

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const raw = (node.data as { node: WorkflowNode }).node;
    onNodeClick?.(raw);
  }, [onNodeClick]);

  useFitViewOnLoad(shouldFit, rfInstanceRef.current);

  return (
    <div className="w-full h-full bg-slate-950">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        onNodesChange={rfNodes && rfNodes.length ? onNodesChange : undefined}
        onEdgesChange={rfEdges && rfEdges.length ? onEdgesChange : undefined}
        onNodeClick={handleNodeClick}
        onInit={(instance) => { rfInstanceRef.current = instance; }}
        nodeTypes={nodeTypes}
        fitView={false}
        minZoom={0.3}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="#1e293b"
        />
        <Controls className="!bg-slate-800 !border-slate-700 !text-slate-300" />
        <MiniMap
          className="!bg-slate-900 !border-slate-700"
          nodeColor={n => {
            const raw = n.data as Record<string, unknown>;
            const nodeStatus = (raw?.node as WorkflowNode | undefined)?.status ?? 'pending';
            return STATUS_COLORS[nodeStatus]?.border ?? '#475569';
          }}
          maskColor="rgba(0,0,0,0.7)"
        />
      </ReactFlow>
    </div>
  );
}
