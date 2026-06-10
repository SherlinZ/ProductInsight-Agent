import React, { useEffect, useMemo, useRef, useState } from 'react';
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

const PHASE_LANES: Record<string, number> = {
  'build_task_brief':           0,
  'plan_schema':                0,
  'plan_sources':               0,
  'collect_sources':            0,
  'evaluate_evidence':          0,
  'pii_scrub':                  0,
  'extract_facts':               0,
  'detect_schema_gaps':          0,
  'analyze_dimensions':         0,
  'review_claims':              0,
  'execute_rework':             0,
  'prepare_human_intervention': 0,
  'write_report_v2':            0,
  'final_review':               1,
  'export_report':              2,
  'compute_metrics':            2,
};

function computeLayout(
  nodes: WorkflowNode[],
  _edges: WorkflowEdge[],
): Record<string, { x: number; y: number }> {
  const positions: Record<string, { x: number; y: number }> = {};
  const phaseCounter: Record<number, number> = {};
  for (const node of nodes) {
    const lane = PHASE_LANES[node.node_name] ?? 0;
    phaseCounter[lane] = (phaseCounter[lane] ?? -1) + 1;
    positions[node.node_name] = {
      x: ORIGIN_X + lane * LANE_W,
      y: ORIGIN_Y + phaseCounter[lane] * STEP_H,
    };
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
function WorkflowNodeComponent({
  data,
  selected,
}: {
  data: Record<string, unknown>;
  selected: boolean;
}) {
  const node = data.node as WorkflowNode;
  const colors = STATUS_COLORS[node.status] ?? STATUS_COLORS.pending;
  const isCollector = node.node_name === 'collect_sources';
  const stats: CollectionStats | null = node.collection_stats ?? null;

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
        {getNodeAgent(node.node_name)}
      </div>
      <div className="text-sm font-semibold leading-tight">
        {getNodeLabel(node.node_name)}
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
}

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
    const label = EDGE_LABELS[key];
    const isConditional = edge.edge_type === 'conditional';

    const LOOPBACK_NODES = new Set(['write_report_v2']);
    const isLoopback = LOOPBACK_NODES.has(edge.to_node) && isConditional;

    return {
      id: edge.edge_id,
      source: edge.from_node,
      target: edge.to_node,
      label: label ?? undefined,
      type: 'default',
      animated: isConditional,
      style: {
        stroke: isLoopback ? '#e879f9'
           : isConditional ? '#f97316'
           : '#475569',
        strokeDasharray: isConditional ? '6 4' : undefined,
        strokeWidth: isLoopback ? 2 : 1.5,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: isLoopback ? '#e879f9' : isConditional ? '#f97316' : '#475569',
      },
      labelStyle: {
        fill: '#94a3b8',
        fontSize: 11,
        fontFamily: 'Segoe UI, system-ui, sans-serif',
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

  const handleNodeClick = (_: React.MouseEvent, node: Node) => {
    const raw = nodes.find(n => n.node_name === node.id);
    if (raw && onNodeClick) onNodeClick(raw);
  };

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
        nodeTypes={{ workflowNode: WorkflowNodeComponent }}
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
