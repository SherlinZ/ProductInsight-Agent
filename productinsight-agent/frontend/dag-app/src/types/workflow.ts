export interface WorkflowNode {
  node_id: string;
  node_name: string;
  node_type: string;
  status: NodeStatus;
  started_at: string;
  completed_at: string;
  latency_ms: number;
  input_summary: Record<string, unknown> | null;
  output_summary: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  // collect_sources node enriched fields
  collection_stats: CollectionStats | null;
  sources: WorkflowSource[];
}

export interface CollectionStats {
  total_urls: number;
  collected: number;
  failed: number;
  skipped: number;
  elapsed_s: number;
  total_timeout_s: number;
  total_chars: number;
}

export interface WorkflowSource {
  source_id: string;
  product_id: string;
  url: string;
  fetch_level: number;
  fetch_strategy: string;
  status: string;
  error_message: string | null;
  char_count: number;
}

export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'paused';

export interface WorkflowEdge {
  edge_id: string;
  from_node: string;
  to_node: string;
  edge_type: string;
  condition: unknown | null;
}

export interface WorkflowSummary {
  total_nodes: number;
  completed: number;
  running: number;
  paused: number;
  failed: number;
  pending: number;
}

export interface WorkflowData {
  run_id: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  summary: WorkflowSummary;
}

export interface TraceSummary {
  total_traces: number;
  failed_traces: number;
  total_tokens: number;
  total_latency_ms: number;
  llm_calls: number;
  non_llm_calls: number;
  successful_llm_calls: number;
  failed_llm_calls: number;
  fallback_llm_calls: number;
}

export interface ArtifactCounts {
  sources: number;
  evidence: number;
  facts: number;
  claims: number;
  signed_claims: number;
}

export interface QualityGate {
  blocked: boolean;
  reason: string | null;
  reason_codes: string[];
}

export interface LiveData {
  run_id: string;
  status: string;
  current_node: string;
  error_message: string | null;
  started_at: string;
  completed_at: string;
  workflow_nodes: WorkflowNode[];
  workflow_summary: WorkflowSummary;
  latest_traces: TraceEntry[];
  trace_summary: TraceSummary;
  current_agent: string;
  current_action: string;
  artifact_counts: ArtifactCounts;
  pending_review_count: number;
  report_status: string | null;
  quality_gate: QualityGate;
}

export interface TraceEntry {
  trace_id: string;
  node_name: string;
  agent_name: string;
  model_name: string;
  status: string;
  latency_ms: number;
  token_input: number;
  token_output: number;
  started_at: string;
  completed_at: string;
  error_message: string | null;
}

export interface NodeLabelInfo {
  label: string;
  agent: string;
}
