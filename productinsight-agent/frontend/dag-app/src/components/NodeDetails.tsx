import React, { useState } from 'react';
import { WorkflowNode, CollectionStats, WorkflowSource } from '../types/workflow';
import { getNodeLabel, getNodeAgent, STATUS_COLORS } from '../utils/labels';

interface NodeDetailsProps {
  node: WorkflowNode | null;
  onClose: () => void;
}

// ── Execution summary card (concrete data, above tabs) ───────────────
function SummaryCard({ nodeName, summary }: { nodeName: string; summary: Record<string, unknown> | null }) {
  if (!summary) return null;
  const s = summary as Record<string, unknown>;

  const rows: { label: string; value: React.ReactNode }[] = [];

  if (nodeName === 'collect_sources') {
    const keys = (s.top_schema_keys as string[] | undefined) ?? [];
    const domains = (s.sample_domains as string[] | undefined) ?? [];
    const products = (s.top_products as string[] | undefined) ?? [];
    const srcTypes = (s.source_types as Record<string, number> | undefined);
    const evCount = s.evidence_items as number | undefined;
    const srcCount = s.sources as number | undefined;
    const errCount = s.errors as number | undefined;
    if (keys.length) rows.push({ label: '覆盖维度', value: keys.map(k => <span key={k} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-slate-700 text-slate-200 mr-1 mb-0.5">{k}</span>) });
    if (domains.length) rows.push({ label: '来源', value: domains.map(d => <span key={d} className="inline-block px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-300 mr-1 mb-0.5">{d}</span>) });
    if (products.length) rows.push({ label: '产品', value: products.join('、') });
    if (srcTypes) {
      const parts = Object.entries(srcTypes).slice(0, 4).map(([k, v]) => `${k}×${v}`);
      rows.push({ label: '来源类型', value: <span className="text-xs text-slate-300">{parts.join('  ')}</span> });
    }
    // Fallback when no rich data yet
    if (rows.length === 0 && (evCount !== undefined || srcCount !== undefined)) {
      if (evCount !== undefined) rows.push({ label: '提取证据', value: String(evCount) + ' 条' });
      if (srcCount !== undefined) rows.push({ label: '处理 URL', value: String(srcCount) + ' 个' });
      if (errCount !== undefined && errCount > 0) rows.push({ label: '错误', value: String(errCount) + ' 个' });
    }

  } else if (nodeName === 'evaluate_evidence') {
    const keys = (s.top_schema_keys as string[] | undefined) ?? [];
    const srcTypes = (s.source_types as Record<string, number> | undefined);
    const total = s.evidence_eval_total as number | undefined;
    const avg = s.evidence_eval_avg_score as number | undefined;
    const usable = s.evidence_eval_usable as number | undefined;
    if (keys.length) rows.push({ label: '高频维度', value: keys.map(k => <span key={k} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-slate-700 text-slate-200 mr-1 mb-0.5">{k}</span>) });
    if (srcTypes) {
      const parts = Object.entries(srcTypes).slice(0, 4).map(([k, v]) => `${k}×${v}`);
      rows.push({ label: '来源类型', value: <span className="text-xs text-slate-300">{parts.join('  ')}</span> });
    }
    if (total !== undefined && avg !== undefined) rows.push({ label: '评估结果', value: `${total} 条，平均分 ${Number(avg).toFixed(2)}` });
    else if (total !== undefined && usable !== undefined) rows.push({ label: '评估结果', value: `${total} 条，${usable} 条可用` });

  } else if (nodeName === 'pii_scrub') {
    const ev = s.evidence_items as number | undefined;
    if (ev !== undefined) rows.push({ label: '处理量', value: `${ev} 条证据已脱敏` });

  } else if (nodeName === 'extract_facts') {
    const keys = (s.top_schema_keys as string[] | undefined) ?? [];
    const facts = s.facts as number | undefined;
    const evCount = s.evidence_items as number | undefined;
    if (keys.length) rows.push({ label: '抽取维度', value: keys.map(k => <span key={k} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-slate-700 text-slate-200 mr-1 mb-0.5">{k}</span>) });
    if (facts !== undefined) rows.push({ label: '总事实数', value: String(facts) });
    else if (evCount !== undefined) rows.push({ label: '处理证据', value: String(evCount) + ' 条' });

  } else if (nodeName === 'detect_schema_gaps' || nodeName === 'coverage_critic') {
    const gaps = (s.top_gap_dims as string[] | undefined) ?? [];
    const rate = s.schema_coverage_rate as number | undefined;
    const highGaps = s.high_priority_schema_gaps as number | undefined;
    const gapCount = s.schema_gaps as number | undefined;
    if (gaps.length) rows.push({ label: '主要缺口', value: gaps.map(g => <span key={g} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-red-900/50 text-red-300 mr-1 mb-0.5">{g}</span>) });
    if (rate !== undefined) rows.push({ label: '覆盖率', value: `${(Number(rate) * 100).toFixed(0)}%` });
    if (highGaps !== undefined) rows.push({ label: '高优先级缺口', value: String(highGaps) });
    else if (gapCount !== undefined) rows.push({ label: '缺口数', value: String(gapCount) });

  } else if (nodeName === 'execute_rework') {
    const reasons = (s.top_rework_reasons as string[] | undefined) ?? [];
    const ok = s.rework_succeeded as number | undefined;
    const fail = s.rework_failed as number | undefined;
    const tasks = s.rework_tasks as number | undefined;
    if (reasons.length) rows.push({ label: '返工原因', value: <ol className="text-xs text-slate-300 list-decimal list-inside space-y-0.5">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ol> });
    if (ok !== undefined && fail !== undefined) rows.push({ label: '处理结果', value: `${ok} 成功，${fail} 失败` });
    else if (tasks !== undefined) rows.push({ label: '处理任务', value: `${tasks} 个` });

  } else if (nodeName === 'review_claims') {
    const titles = (s.top_claim_titles as string[] | undefined) ?? [];
    const signed = s.signed_claims as number | undefined;
    const rework = s.rework_requests as number | undefined;
    const drafts = s.claim_drafts as number | undefined;
    if (titles.length) rows.push({ label: 'Claim 标题', value: <ol className="text-xs text-slate-300 list-decimal list-inside space-y-0.5">{titles.map((t, i) => <li key={i}>{t}</li>)}</ol> });
    if (signed !== undefined) rows.push({ label: 'Signed', value: String(signed) });
    if (rework !== undefined && rework > 0) rows.push({ label: '返工', value: String(rework) });
    else if (drafts !== undefined && rows.length === 0) rows.push({ label: '草稿', value: `${drafts} 个` });

  } else if (nodeName === 'write_report_v2') {
    const titles = (s.top_claim_titles as string[] | undefined) ?? [];
    const gaps = (s.top_gap_dims as string[] | undefined) ?? [];
    const signed = s.signed_claims as number | undefined;
    const gapCount = s.schema_gaps as number | undefined;
    if (titles.length) rows.push({ label: '核心 Claims', value: <ol className="text-xs text-slate-300 list-decimal list-inside space-y-0.5">{titles.map((t, i) => <li key={i}>{t}</li>)}</ol> });
    if (gaps.length) rows.push({ label: '待补缺口', value: gaps.map(g => <span key={g} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-red-900/50 text-red-300 mr-1 mb-0.5">{g}</span>) });
    if (signed !== undefined) rows.push({ label: 'Signed', value: String(signed) });
    else if (gapCount !== undefined && rows.length === 0) rows.push({ label: 'Schema 缺口', value: `${gapCount} 个` });

  } else if (nodeName === 'plan_sources') {
    const domains = (s.sample_domains as string[] | undefined) ?? [];
    const src = s.sources as number | undefined;
    const mode = s.mode as string | undefined;
    if (domains.length) rows.push({ label: '候选域名', value: domains.map(d => <span key={d} className="inline-block px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-300 mr-1 mb-0.5">{d}</span>) });
    if (src !== undefined) rows.push({ label: '计划采集', value: `${src} 个 URL` });
    if (mode) rows.push({ label: '模式', value: mode });

  } else if (nodeName === 'plan_schema') {
    const products = (s.top_products as string[] | undefined) ?? [];
    if (products.length) rows.push({ label: '调研产品', value: products.join('、') });

  } else if (nodeName === 'build_task_brief') {
    const products = (s.top_products as string[] | undefined) ?? [];
    const mode = s.mode as string | undefined;
    if (products.length) rows.push({ label: '调研产品', value: products.join('、') });
    if (mode) rows.push({ label: '模式', value: mode });

  } else if (nodeName === 'analyze_dimensions') {
    const gaps = (s.top_gap_dims as string[] | undefined) ?? [];
    const gapCount = s.schema_gaps as number | undefined;
    if (gaps.length) rows.push({ label: '主要缺口', value: gaps.map(g => <span key={g} className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-red-900/50 text-red-300 mr-1 mb-0.5">{g}</span>) });
    else if (gapCount !== undefined) rows.push({ label: '缺口数', value: `${gapCount} 个` });

  } else if (nodeName === 'final_review') {
    const signed = s.signed_claims as number | undefined;
    if (signed !== undefined) rows.push({ label: '终审结果', value: `${signed} 个 claims 可用` });

  } else if (nodeName === 'export_report') {
    rows.push({ label: '状态', value: '报告已导出' });

  } else if (nodeName === 'compute_metrics') {
    rows.push({ label: '状态', value: '指标计算完成' });
  }

  if (rows.length === 0) return null;

  return (
    <div className="border-b border-slate-700">
      <div className="px-4 py-3 bg-slate-800/50">
        <div className="text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wide">执行摘要</div>
        <div className="space-y-2">
          {rows.map(({ label, value }, i) => (
            <div key={i} className="flex items-start gap-3">
              <span className="text-xs text-slate-500 flex-shrink-0 w-16 pt-0.5">{label}</span>
              <span className="text-xs text-slate-200 flex-1 leading-relaxed">{value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
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

// ── Live progress banner (running nodes only) ─────────────────────────
function LiveProgressBanner({ nodeName, summary }: { nodeName: string; summary: Record<string, unknown> | null }) {
  if (!summary) {
    return (
      <div className="px-4 py-2.5 bg-blue-900/20 border-b border-blue-800/40">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
          <span className="text-xs text-blue-300 animate-pulse">运行中…</span>
        </div>
      </div>
    );
  }
  const s = summary as Record<string, unknown>;

  let text = '';
  if (nodeName === 'collect_sources') {
    const products = (s.top_products as string[] | undefined) ?? [];
    const product = products[0] ?? (s.top_schema_keys ? undefined : '数据');
    const count = s.sources as number | undefined;
    const ev = s.evidence_items as number | undefined;
    if (count !== undefined && ev !== undefined) {
      text = `正在采集 ${product ?? '数据'}（${count} 个 URL，${ev} 条证据…）`;
    } else if (count !== undefined) {
      text = `正在采集 ${product ?? '数据'}（已抓取 ${count} 个 URL…）`;
    } else {
      text = `正在采集 ${product ?? '数据'}…`;
    }
  } else if (nodeName === 'evaluate_evidence') {
    const total = s.evidence_eval_total as number | undefined;
    const usable = s.evidence_eval_usable as number | undefined;
    if (total !== undefined && usable !== undefined) {
      text = `正在评估证据质量（${total} 条，${usable} 条可用…）`;
    } else if (total !== undefined) {
      text = `正在评估证据质量（已评估 ${total} 条…）`;
    } else {
      text = '正在评估证据质量…';
    }
  } else if (nodeName === 'extract_facts') {
    const ev = s.evidence_items as number | undefined;
    const facts = s.facts as number | undefined;
    if (ev !== undefined && facts !== undefined) {
      text = `正在抽取事实（${ev} 条证据，${facts} 个事实…）`;
    } else if (ev !== undefined) {
      text = `正在抽取事实（已处理 ${ev} 条证据…）`;
    } else {
      text = '正在抽取事实…';
    }
  } else if (nodeName === 'review_claims') {
    const drafts = s.claim_drafts as number | undefined;
    const signed = s.signed_claims as number | undefined;
    if (drafts !== undefined && signed !== undefined) {
      text = `正在审查 claim（${drafts} 个草稿，${signed} 个已签收…）`;
    } else if (drafts !== undefined) {
      text = `正在审查 claim 草稿（已生成 ${drafts} 个…）`;
    } else {
      text = '正在审查 claim…';
    }
  } else if (nodeName === 'write_report_v2') {
    const signed = s.signed_claims as number | undefined;
    const gaps = s.schema_gaps as number | undefined;
    text = `正在撰写报告${signed !== undefined ? `（${signed} 个 signed claims${gaps ? `，${gaps} 个缺口` : ''}）` : '…'}…`;
  } else if (nodeName === 'plan_sources') {
    const domains = (s.sample_domains as string[] | undefined) ?? [];
    const count = s.sources as number | undefined;
    if (domains.length) {
      text = `正在制定采集计划（${domains[0]}${domains[1] ? `、${domains[1]}` : ''}…）…`;
    } else if (count !== undefined) {
      text = `正在制定采集计划（${count} 个 URL）…`;
    } else {
      text = '正在制定采集计划…';
    }
  } else if (nodeName === 'plan_schema') {
    const products = (s.top_products as string[] | undefined) ?? [];
    if (products.length) {
      text = `正在规划 Schema（${products[0]}）…`;
    } else {
      text = '正在规划 Schema…';
    }
  } else if (nodeName === 'build_task_brief') {
    const mode = s.mode as string | undefined;
    text = mode ? `正在初始化任务概要（${mode} 模式）…` : '正在初始化任务概要…';
  } else if (nodeName === 'detect_schema_gaps') {
    const rate = s.schema_coverage_rate as number | undefined;
    text = rate !== undefined
      ? `正在检测 schema 缺口（覆盖率 ${(Number(rate) * 100).toFixed(0)}%…）`
      : '正在检测 schema 缺口…';
  } else if (nodeName === 'execute_rework') {
    const tasks = s.rework_tasks as number | undefined;
    text = tasks !== undefined
      ? `正在执行返工（${tasks} 个任务）…`
      : '正在执行返工…';
  } else if (nodeName === 'analyze_dimensions') {
    text = '正在分析维度缺口…';
  } else if (nodeName === 'pii_scrub') {
    const ev = s.evidence_items as number | undefined;
    text = ev !== undefined
      ? `正在脱敏处理（${ev} 条证据）…`
      : '正在脱敏处理…';
  } else {
    text = '运行中…';
  }

  return (
    <div className="px-4 py-2.5 bg-blue-900/20 border-b border-blue-800/40">
      <div className="flex items-center gap-2">
        <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse flex-shrink-0" />
        <span className="text-xs text-blue-300 leading-relaxed">{text}</span>
      </div>
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

      {/* Live progress banner (running nodes only) */}
      {node.status === 'running' && (
        <LiveProgressBanner nodeName={node.node_name} summary={node.input_summary} />
      )}

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

      {/* Execution summary — concrete data, independent of tabs */}
      <SummaryCard nodeName={node.node_name} summary={node.output_summary ?? node.input_summary} />

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
