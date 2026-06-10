import { NodeLabelInfo } from '../types/workflow';

export const NODE_LABELS: Record<string, NodeLabelInfo> = {
  build_task_brief: { label: '构建任务简报', agent: '任务规划Agent' },
  plan_schema: { label: '规划信息结构', agent: 'Schema规划Agent' },
  plan_sources: { label: '规划搜索源', agent: '搜索规划Agent' },
  collect_sources: { label: '采集网络来源', agent: '搜索执行Agent' },
  evaluate_evidence: { label: '评估证据质量', agent: '评估Agent' },
  pii_scrub: { label: '隐私合规处理', agent: '合规Agent' },
  extract_facts: { label: '抽取结构化事实', agent: '抽取Agent' },
  detect_schema_gaps: { label: '检测结构空白', agent: 'Schema差距规划Agent' },
  execute_rework: { label: '补证返工', agent: '修复Agent' },
  analyze_dimensions: { label: '多维分析', agent: '分析Agent' },
  review_claims: { label: '结论质检', agent: '审查Agent' },
  prepare_human_intervention: { label: '人工介入', agent: '人工审查Agent' },
  write_report_v2: { label: '撰写报告', agent: '撰写Agent' },
  final_review: { label: '最终质量门', agent: '审查Agent' },
  export_report: { label: '导出报告', agent: '导出Agent' },
  compute_metrics: { label: '计算质量指标', agent: '评估Agent' },
};

export function getNodeLabel(nodeName: string): string {
  return NODE_LABELS[nodeName]?.label ?? nodeName;
}

export function getNodeAgent(nodeName: string): string {
  return NODE_LABELS[nodeName]?.agent ?? '—';
}

export const STATUS_COLORS: Record<string, { bg: string; border: string; text: string; glow: string }> = {
  pending:   { bg: '#1e293b', border: '#475569', text: '#94a3b8', glow: '' },
  running:   { bg: '#1e3a5f', border: '#3b82f6', text: '#93c5fd', glow: 'box-shadow: 0 0 12px #3b82f6;' },
  completed: { bg: '#14532d', border: '#22c55e', text: '#86efac', glow: '' },
  failed:    { bg: '#450a0a', border: '#ef4444', text: '#fca5a5', glow: '' },
  paused:    { bg: '#451a03', border: '#f97316', text: '#fdba74', glow: '' },
};

export const EDGE_LABELS: Record<string, string> = {
  'final_review->write_report_v2': '需返工',
  'final_review->export_report': '批准',
};
