import { useState } from 'react';
import { WorkflowNode, CollectionStats, WorkflowSource } from '../types/workflow';
import { getNodeLabel, getNodeAgent, STATUS_COLORS } from '../utils/labels';

interface NodeDetailsProps {
  node: WorkflowNode | null;
  onClose: () => void;
}

// ── Artifact counter card (cleaner than raw JSON) ─────────────────
function ArtifactCounters({ summary }: { summary: Record<string, unknown> | null }) {
  if (!summary) return <span className="text-slate-500 italic">—</span>;
  const counters = [
    { key: 'sources',         label: 'Sources',         color: '#60a5fa' },
    { key: 'evidence_items',  label: 'Evidence',         color: '#34d399' },
    { key: 'facts',           label: 'Facts',             color: '#fbbf24' },
    { key: 'claim_drafts',    label: 'Claim Drafts',      color: '#f97316' },
    { key: 'signed_claims',   label: 'Signed Claims',     color: '#e879f9' },
    { key: 'rework_requests', label: 'Rework',           color: '#f87171' },
    { key: 'errors',          label: 'Errors',            color: '#ef4444' },
    { key: 'human_interventions', label: 'Human Review',   color: '#a78bfa' },
  ];
  return (
    <div className="grid grid-cols-2 gap-2">
      {counters.map(({ key, label, color }) => {
        const value = (summary as Record<string, unknown>)[key];
        if (value === undefined || value === null) return null;
        const num = typeof value === 'number' ? value : 0;
        return (
          <div key={key} className="bg-slate-800 rounded-lg p-2 flex items-center justify-between">
            <span className="text-xs text-slate-400">{label}</span>
            <span className="text-sm font-semibold" style={{ color }}>
              {num}
            </span>
          </div>
        );
      })}
      {/* Mode badge */}
      <div className="col-span-2 bg-slate-800 rounded-lg p-2 flex items-center justify-between">
        <span className="text-xs text-slate-400">Mode</span>
        <span className="text-xs font-mono text-slate-300">
          {(summary as Record<string, unknown>).mode as string || '—'}
        </span>
      </div>
      <div className="col-span-2 bg-slate-800 rounded-lg p-2 flex items-center justify-between">
        <span className="text-xs text-slate-400">Human Review Required</span>
        <span className={`text-xs font-medium ${(summary as Record<string, unknown>).requires_human_review ? 'text-amber-400' : 'text-slate-500'}`}>
          {(summary as Record<string, unknown>).requires_human_review ? '是' : '否'}
        </span>
      </div>
    </div>
  );
}

function formatTime(iso: string) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

function fmtMs(ms: number) {
  if (ms <= 0) return '—';
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

// ── Strategy badge ───────────────────────────────────────────────
const STRATEGY_CONFIG: Record<string, { label: string; bg: string; text: string }> = {
  requests:      { label: 'L1 · requests',  bg: '#065f46', text: '#34d399' },
  playwright:     { label: 'L2 · playwright', bg: '#1e3a8a', text: '#93c5fd' },
  search_api:     { label: 'L3 · search API', bg: '#3b0764', text: '#e879f9' },
  fallback:       { label: 'L3 · fallback',  bg: '#3b0764', text: '#e879f9' },
};

function StrategyBadge({ strategy }: { strategy: string }) {
  const cfg = STRATEGY_CONFIG[strategy] ?? { label: strategy, bg: '#1e293b', text: '#94a3b8' };
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium"
      style={{ backgroundColor: cfg.bg, color: cfg.text }}
    >
      {cfg.label}
    </span>
  );
}

// ── Stats bar ────────────────────────────────────────────────────
function StatsBar({ stats }: { stats: CollectionStats }) {
  const rate = stats.total_urls > 0 ? (stats.collected / stats.total_urls) * 100 : 0;
  const colors = ['#22c55e', '#f97316', '#94a3b8']; // green / orange / gray
  const segments = [
    { label: `${stats.collected} 抓取成功`, color: colors[0], pct: (stats.collected / stats.total_urls) * 100 },
    { label: `${stats.failed} 失败`, color: colors[1], pct: (stats.failed / stats.total_urls) * 100 },
    { label: `${stats.skipped} 跳过`, color: colors[2], pct: (stats.skipped / stats.total_urls) * 100 },
  ];

  return (
    <div className="space-y-2">
      {/* Header */}
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-3">
          <span className="text-slate-400">成功率</span>
          <span className="font-bold text-slate-100">{rate.toFixed(0)}%</span>
          <span className="text-slate-500">({stats.collected}/{stats.total_urls} URLs)</span>
        </div>
        <div className="text-slate-500">
          {stats.elapsed_s >= 1 ? `${stats.elapsed_s.toFixed(1)}s` : '—'} / {stats.total_timeout_s}s 超时
        </div>
      </div>

      {/* Segmented bar */}
      <div className="flex rounded overflow-hidden h-3 bg-slate-700">
        {segments.map((s, i) =>
          s.pct > 0 ? (
            <div
              key={i}
              title={`${s.label} (${s.pct.toFixed(0)}%)`}
              style={{ width: `${s.pct}%`, backgroundColor: s.color }}
              className="flex items-center justify-center text-[9px] font-medium text-white/80 transition-all"
            >
              {s.pct >= 8 ? s.label.split(' ')[0] : ''}
            </div>
          ) : null
        )}
      </div>

      {/* Legend */}
      <div className="flex gap-3 text-xs">
        {segments.map((s, i) => (
          <div key={i} className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-sm" style={{ backgroundColor: s.color }} />
            <span className="text-slate-400">{s.label}</span>
          </div>
        ))}
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-sm bg-slate-500" />
          <span className="text-slate-400">{stats.total_chars.toLocaleString()} chars</span>
        </div>
      </div>
    </div>
  );
}

// ── Source table ─────────────────────────────────────────────────
function SourceTable({ sources }: { sources: WorkflowSource[] }) {
  const [filter, setFilter] = useState<'all' | 'collected' | 'failed'>('all');

  const filtered = sources.filter(s =>
    filter === 'all' ? true : s.status === filter
  );

  const groupByProduct = (items: WorkflowSource[]) => {
    const groups: Record<string, WorkflowSource[]> = {};
    for (const src of items) {
      (groups[src.product_id] ??= []).push(src);
    }
    return groups;
  };

  const groups = groupByProduct(filtered);

  return (
    <div className="space-y-3">
      {/* Filter tabs */}
      <div className="flex gap-1">
        {(['all', 'collected', 'failed'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
              filter === f
                ? 'bg-blue-600 text-white'
                : 'bg-slate-700 text-slate-400 hover:bg-slate-600 hover:text-slate-200'
            }`}
          >
            {f === 'all' ? `全部 (${sources.length})` :
             f === 'collected' ? `成功 (${sources.filter(s => s.status === 'collected').length})` :
             `失败 (${sources.filter(s => s.status === 'failed').length})`}
          </button>
        ))}
      </div>

      {Object.entries(groups).map(([productId, srcs]) => (
        <div key={productId}>
          <div className="text-xs font-semibold text-slate-400 mb-1 uppercase tracking-wide">
            {productId}
          </div>
          <div className="space-y-1">
            {srcs.map(src => (
              <div
                key={src.source_id}
                className="flex items-start gap-2 bg-slate-800 rounded p-2 text-xs"
              >
                {/* Status dot */}
                <div className={`mt-0.5 w-2 h-2 rounded-full flex-shrink-0 ${
                  src.status === 'collected' ? 'bg-green-400' :
                  src.status === 'failed' ? 'bg-red-400' : 'bg-slate-500'
                }`} />

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <StrategyBadge strategy={src.fetch_strategy} />
                    <span className="text-slate-300 font-mono truncate max-w-[180px]" title={src.url}>
                      {src.url.length > 40 ? `…${src.url.slice(-40)}` : src.url}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 text-slate-500">
                    {src.char_count > 0 && (
                      <span>{src.char_count.toLocaleString()} chars</span>
                    )}
                    {src.fetch_level > 0 && (
                      <span>L{src.fetch_level}</span>
                    )}
                    {src.error_message && (
                      <span className="text-red-400 truncate max-w-[150px]" title={src.error_message}>
                        {src.error_message.slice(0, 60)}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      {filtered.length === 0 && (
        <div className="text-center text-slate-500 text-xs py-4">无匹配数据</div>
      )}
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────
export default function NodeDetails({ node, onClose }: NodeDetailsProps) {
  const [tab, setTab] = useState<'input' | 'output' | 'sources'>('input');

  if (!node) return null;

  const colors = STATUS_COLORS[node.status] ?? STATUS_COLORS.pending;
  const isCollector = node.node_name === 'collect_sources' || node.node_type === 'collect_sources';
  const stats: CollectionStats | null = node.collection_stats ?? null;
  const sources: WorkflowSource[] = node.sources ?? [];

  // Auto-switch to sources tab when opening a completed collect_sources node
  const showSourcesTab = isCollector && sources.length > 0;

  return (
    <div
      className="h-full flex flex-col border-l border-slate-700 bg-slate-900 animate-slide-in"
      style={{ minWidth: 380 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700 flex-shrink-0">
        <div>
          <div className="text-sm font-semibold text-slate-100">{getNodeLabel(node.node_name)}</div>
          <div className="text-xs text-slate-400">{getNodeAgent(node.node_name)}</div>
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-200 text-lg leading-none w-6 h-6 flex items-center justify-center rounded hover:bg-slate-700 transition-colors"
        >
          ×
        </button>
      </div>

      {/* Status badge + error */}
      <div className="px-4 py-2 border-b border-slate-700">
        <span
          className="inline-flex items-center px-2 py-1 rounded text-xs font-medium"
          style={{ backgroundColor: colors.bg, border: `1px solid ${colors.border}`, color: colors.text }}
        >
          {node.status.toUpperCase()}
        </span>
        {node.error_message && (
          <div className="mt-2 text-xs text-red-400 bg-red-900/30 rounded p-2">
            {node.error_message}
          </div>
        )}
      </div>

      {/* Timestamps */}
      <div className="px-4 py-2 grid grid-cols-2 gap-2 text-xs border-b border-slate-700">
        <div>
          <div className="text-slate-500">Started</div>
          <div className="text-slate-300">{formatTime(node.started_at)}</div>
        </div>
        <div>
          <div className="text-slate-500">Completed</div>
          <div className="text-slate-300">{formatTime(node.completed_at)}</div>
        </div>
        <div>
          <div className="text-slate-500">Latency</div>
          <div className="text-slate-300">{fmtMs(node.latency_ms)}</div>
        </div>
        <div>
          <div className="text-slate-500">Type</div>
          <div className="text-slate-300">{node.node_type}</div>
        </div>
      </div>

      {/* Collection stats preview (shown above tabs for collect_sources) */}
      {stats && (
        <div className="px-4 py-3 border-b border-slate-700 space-y-1">
          <div className="text-xs text-slate-400 font-medium mb-2">采集统计</div>
          <StatsBar stats={stats} />
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-700 flex-shrink-0">
        <button
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            tab === 'input'
              ? 'text-blue-400 border-b-2 border-blue-400 bg-slate-800/50'
              : 'text-slate-400 hover:text-slate-200'
          }`}
          onClick={() => setTab('input')}
        >
          INPUT
        </button>
        <button
          className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
            tab === 'output'
              ? 'text-blue-400 border-b-2 border-blue-400 bg-slate-800/50'
              : 'text-slate-400 hover:text-slate-200'
          }`}
          onClick={() => setTab('output')}
        >
          OUTPUT
        </button>
        {showSourcesTab && (
          <button
            className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
              tab === 'sources'
                ? 'text-blue-400 border-b-2 border-blue-400 bg-slate-800/50'
                : 'text-slate-400 hover:text-slate-200'
            }`}
            onClick={() => setTab('sources')}
          >
            SOURCES
          </button>
        )}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto p-4">
        {tab === 'input' && <ArtifactCounters summary={node.input_summary} />}
        {tab === 'output' && <ArtifactCounters summary={node.output_summary} />}
        {tab === 'sources' && (
          <div className="space-y-3">
            <SourceTable sources={sources} />
            {sources.length === 0 && (
              <div className="text-center text-slate-500 text-xs py-8">
                节点运行中，数据即将更新…
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
