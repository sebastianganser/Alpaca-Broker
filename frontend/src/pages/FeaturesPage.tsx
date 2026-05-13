import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import {
  Brain,
  Target,
  Layers,
  TrendingUp,
  Search,
  X,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import {
  fetchFeatureStats,
  fetchFeatureCoverage,
  fetchSignalConvergence,
  fetchReturnStats,
  fetchTickerFeatures,
  type FeatureCoverageItem,
  type SignalConvergenceItem,
  type HorizonStats,
  type FeatureGroupDetail,
} from '../api';

// ── Helpers ────────────────────────────────────────────────────────────

function formatPct(n: number): string {
  return `${n.toFixed(1)}%`;
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function formatReturnVal(v: number | null): string {
  if (v === null) return '—';
  return `${(v * 100).toFixed(2)}%`;
}

/** Coverage cell color based on fill ratio */
function coverageColor(filled: number, total: number): string {
  if (total === 0) return 'var(--on-surface-dim)';
  const ratio = filled / total;
  if (ratio >= 0.8) return 'var(--primary)';
  if (ratio >= 0.4) return 'var(--warning)';
  if (ratio > 0) return 'var(--error)';
  return 'var(--on-surface-dim)';
}

function coverageBg(filled: number, total: number): string {
  if (total === 0) return 'transparent';
  const ratio = filled / total;
  if (ratio >= 0.8) return 'rgba(0, 255, 179, 0.08)';
  if (ratio >= 0.4) return 'rgba(255, 193, 7, 0.08)';
  if (ratio > 0) return 'rgba(239, 68, 68, 0.06)';
  return 'transparent';
}

// ── Feature Group Totals ────────────────────────────────────────────

const GROUP_TOTALS: Record<string, number> = {
  ark: 11, insider: 8, analyst: 7, politician: 4,
  form13f: 2, fundamentals: 8, technical: 6, earnings: 3,
};

const GROUP_LABELS: Record<string, string> = {
  ark: 'ARK', insider: 'Insider', analyst: 'Analyst', politician: 'Politician',
  form13f: '13F', fundamentals: 'Fundamentals', technical: 'Technical', earnings: 'Earnings',
};

// ── Pipeline Stats Section ──────────────────────────────────────────

function PipelineStats() {
  const { data, isLoading } = useQuery({
    queryKey: ['feature-stats'],
    queryFn: fetchFeatureStats,
    refetchInterval: 30_000,
  });

  if (isLoading || !data) {
    return (
      <div className="grid grid-4" style={{ marginBottom: 'var(--space-2xl)' }}>
        {[...Array(4)].map((_, i) => (
          <div key={i} className="card loading-pulse" style={{ height: 88 }} />
        ))}
      </div>
    );
  }

  const stats = [
    {
      label: 'Letzter Snapshot',
      value: data.last_snapshot_date || '—',
      icon: Brain,
      color: 'var(--primary)',
    },
    {
      label: 'Ticker Coverage',
      value: formatNumber(data.ticker_count),
      icon: Layers,
      color: 'var(--primary)',
    },
    {
      label: 'Feature Coverage',
      value: formatPct(data.feature_coverage_pct),
      icon: Target,
      color: data.feature_coverage_pct > 50 ? 'var(--primary)' : 'var(--warning)',
    },
    {
      label: 'Target Backfill',
      value: formatPct(data.target_backfill_pct),
      icon: TrendingUp,
      color: data.target_backfill_pct > 50 ? 'var(--primary)' : 'var(--warning)',
    },
  ];

  return (
    <div className="grid grid-4" style={{ marginBottom: 'var(--space-2xl)' }}>
      {stats.map((s) => (
        <div key={s.label} className="card flex items-center gap-md">
          <s.icon size={20} style={{ color: s.color, flexShrink: 0 }} />
          <div>
            <div className="label-dim">{s.label}</div>
            <div className="text-sm" style={{ fontWeight: 600 }}>{s.value}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Coverage Heatmap ────────────────────────────────────────────────

function CoverageHeatmap() {
  const { data, isLoading } = useQuery({
    queryKey: ['feature-coverage'],
    queryFn: fetchFeatureCoverage,
    staleTime: 60_000,
  });
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<'ticker' | 'total_filled'>('total_filled');
  const [sortAsc, setSortAsc] = useState(false);

  if (isLoading || !data) {
    return <div className="card loading-pulse" style={{ height: 300 }} />;
  }

  const groups = Object.keys(GROUP_TOTALS) as Array<keyof typeof GROUP_TOTALS>;

  // Filter + sort
  const filtered = data.items
    .filter((item) => item.ticker.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      const aVal = sortKey === 'ticker' ? a.ticker : a.total_filled;
      const bVal = sortKey === 'ticker' ? b.ticker : b.total_filled;
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return sortAsc
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });

  const toggleSort = (key: 'ticker' | 'total_filled') => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(key === 'ticker'); }
  };

  const SortIcon = ({ k }: { k: 'ticker' | 'total_filled' }) =>
    sortKey === k ? (sortAsc ? <ChevronUp size={12} /> : <ChevronDown size={12} />) : null;

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div
        style={{
          padding: 'var(--space-lg) var(--space-lg) var(--space-sm)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div className="card-title" style={{ margin: 0 }}>
          Feature Coverage Heatmap
          {data.snapshot_date && (
            <span className="text-xs text-dim" style={{ marginLeft: 'var(--space-md)', fontWeight: 400 }}>
              Snapshot: {data.snapshot_date}
            </span>
          )}
        </div>
        <div style={{ position: 'relative' }}>
          <Search size={14} style={{ position: 'absolute', left: 8, top: 7, color: 'var(--on-surface-dim)' }} />
          <input
            type="text"
            placeholder="Ticker suchen…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              padding: '4px 8px 4px 28px',
              background: 'var(--surface-container)',
              border: '1px solid var(--outline-variant)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--on-surface)',
              fontSize: '0.75rem',
              width: 160,
            }}
          />
          {search && (
            <X
              size={12}
              style={{ position: 'absolute', right: 8, top: 8, cursor: 'pointer', color: 'var(--on-surface-dim)' }}
              onClick={() => setSearch('')}
            />
          )}
        </div>
      </div>
      <div style={{ maxHeight: 480, overflowY: 'auto' }}>
        <table className="data-table" style={{ fontSize: '0.75rem' }}>
          <thead>
            <tr>
              <th
                style={{ cursor: 'pointer', userSelect: 'none' }}
                onClick={() => toggleSort('ticker')}
              >
                Ticker <SortIcon k="ticker" />
              </th>
              {groups.map((g) => (
                <th key={g} className="text-center" style={{ fontSize: '0.65rem' }}>
                  {GROUP_LABELS[g]}
                </th>
              ))}
              <th
                className="text-right"
                style={{ cursor: 'pointer', userSelect: 'none' }}
                onClick={() => toggleSort('total_filled')}
              >
                Gesamt <SortIcon k="total_filled" />
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => (
              <CoverageRow key={item.ticker} item={item} groups={groups} />
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-dim" style={{ padding: 'var(--space-sm) var(--space-lg)' }}>
        {filtered.length} / {data.items.length} Ticker
      </div>
    </div>
  );
}

function CoverageRow({
  item,
  groups,
}: {
  item: FeatureCoverageItem;
  groups: string[];
}) {
  return (
    <tr>
      <td className="mono" style={{ fontWeight: 600 }}>{item.ticker}</td>
      {groups.map((g) => {
        const filled = item[g as keyof FeatureCoverageItem] as number;
        const total = GROUP_TOTALS[g];
        return (
          <td
            key={g}
            className="text-center mono"
            style={{
              color: coverageColor(filled, total),
              backgroundColor: coverageBg(filled, total),
              fontWeight: filled > 0 ? 600 : 400,
            }}
          >
            {filled}/{total}
          </td>
        );
      })}
      <td className="text-right mono" style={{ fontWeight: 600 }}>
        <span style={{ color: coverageColor(item.total_filled, item.total_possible) }}>
          {item.total_filled}
        </span>
        <span className="text-dim">/{item.total_possible}</span>
      </td>
    </tr>
  );
}

// ── Signal Convergence ──────────────────────────────────────────────

function SignalConvergence() {
  const { data, isLoading } = useQuery({
    queryKey: ['signal-convergence'],
    queryFn: () => fetchSignalConvergence(30),
    staleTime: 60_000,
  });

  if (isLoading || !data) {
    return <div className="card loading-pulse" style={{ height: 200 }} />;
  }

  if (data.items.length === 0) {
    return (
      <div className="card">
        <div className="card-title">Signal Convergence</div>
        <div className="text-dim">Keine Daten verfügbar.</div>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: 'var(--space-lg) var(--space-lg) var(--space-sm)' }}>
        <div className="card-title" style={{ margin: 0 }}>
          Signal Convergence – Multi-Source Overlap
          {data.snapshot_date && (
            <span className="text-xs text-dim" style={{ marginLeft: 'var(--space-md)', fontWeight: 400 }}>
              {data.snapshot_date}
            </span>
          )}
        </div>
      </div>
      <div style={{ maxHeight: 360, overflowY: 'auto' }}>
        <table className="data-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr>
              <th>Ticker</th>
              <th className="text-center">Quellen</th>
              <th>Aktive Signale</th>
              <th className="text-right">ARK Score</th>
              <th className="text-right">Insider Score</th>
              <th className="text-right">Analyst Score</th>
              <th className="text-right">RSI</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <ConvergenceRow key={item.ticker} item={item} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ConvergenceRow({ item }: { item: SignalConvergenceItem }) {
  const barWidth = (item.active_sources / 8) * 100;
  return (
    <tr>
      <td className="mono" style={{ fontWeight: 600 }}>{item.ticker}</td>
      <td className="text-center">
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          minWidth: 60,
        }}>
          <div style={{
            height: 6,
            borderRadius: 3,
            background: 'var(--surface-container-highest)',
            flex: 1,
            minWidth: 40,
          }}>
            <div style={{
              height: '100%',
              width: `${barWidth}%`,
              borderRadius: 3,
              background: item.active_sources >= 5
                ? 'var(--primary)'
                : item.active_sources >= 3
                  ? 'var(--warning)'
                  : 'var(--on-surface-dim)',
              transition: 'width 0.3s ease',
            }} />
          </div>
          <span className="mono" style={{ fontWeight: 600, fontSize: '0.75rem' }}>
            {item.active_sources}
          </span>
        </div>
      </td>
      <td>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {item.source_names.map((s) => (
            <span
              key={s}
              style={{
                fontSize: '0.6rem',
                padding: '1px 5px',
                borderRadius: 'var(--radius-sm)',
                background: 'var(--surface-container-high)',
                color: 'var(--on-surface)',
                whiteSpace: 'nowrap',
              }}
            >
              {s}
            </span>
          ))}
        </div>
      </td>
      <td className="text-right mono">{item.ark_conviction_score?.toFixed(2) ?? '—'}</td>
      <td className="text-right mono">{item.insider_cluster_score?.toFixed(2) ?? '—'}</td>
      <td className="text-right mono">{item.analyst_rating_score?.toFixed(2) ?? '—'}</td>
      <td className="text-right mono">{item.rsi_14?.toFixed(1) ?? '—'}</td>
    </tr>
  );
}

// ── Return Distribution ─────────────────────────────────────────────

function ReturnDistribution() {
  const { data, isLoading } = useQuery({
    queryKey: ['return-stats'],
    queryFn: fetchReturnStats,
    staleTime: 60_000,
  });

  if (isLoading || !data) {
    return <div className="card loading-pulse" style={{ height: 160 }} />;
  }

  if (data.horizons.length === 0) {
    return (
      <div className="card">
        <div className="card-title">Return Distribution</div>
        <div className="text-dim">Noch keine Returns berechnet. Target Backfill läuft ab morgen.</div>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: 'var(--space-lg) var(--space-lg) var(--space-sm)' }}>
        <div className="card-title" style={{ margin: 0 }}>
          Forward Returns – Target Variable Übersicht
        </div>
        <div className="text-xs text-dim">
          {formatNumber(data.total_snapshots)} Snapshots insgesamt
        </div>
      </div>
      <table className="data-table" style={{ fontSize: '0.8rem' }}>
        <thead>
          <tr>
            <th>Horizont</th>
            <th className="text-right">Gefüllt</th>
            <th className="text-right">%</th>
            <th className="text-right">Ø Return</th>
            <th className="text-right">Median</th>
            <th className="text-right">Std</th>
            <th className="text-right">Min</th>
            <th className="text-right">Max</th>
          </tr>
        </thead>
        <tbody>
          {data.horizons.map((h) => (
            <ReturnRow key={h.horizon} h={h} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReturnRow({ h }: { h: HorizonStats }) {
  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{h.horizon}</td>
      <td className="text-right mono">{formatNumber(h.filled_count)}</td>
      <td className="text-right mono" style={{
        color: h.filled_pct > 50 ? 'var(--primary)' : h.filled_pct > 0 ? 'var(--warning)' : 'var(--on-surface-dim)',
      }}>
        {formatPct(h.filled_pct)}
      </td>
      <td className="text-right mono">{formatReturnVal(h.mean)}</td>
      <td className="text-right mono">{formatReturnVal(h.median)}</td>
      <td className="text-right mono">{h.std !== null ? (h.std * 100).toFixed(2) + '%' : '—'}</td>
      <td className="text-right mono" style={{ color: h.min_val !== null && h.min_val < 0 ? 'var(--error)' : undefined }}>
        {formatReturnVal(h.min_val)}
      </td>
      <td className="text-right mono" style={{ color: h.max_val !== null && h.max_val > 0 ? 'var(--primary)' : undefined }}>
        {formatReturnVal(h.max_val)}
      </td>
    </tr>
  );
}

// ── Ticker Detail Modal ─────────────────────────────────────────────

function TickerDetailModal({
  symbol,
  onClose,
}: {
  symbol: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['ticker-features', symbol],
    queryFn: () => fetchTickerFeatures(symbol),
    enabled: !!symbol,
  });

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          width: '90%',
          maxWidth: 720,
          maxHeight: '80vh',
          overflowY: 'auto',
          animation: 'fadeIn 0.2s ease-out',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-md">
          <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
              {symbol}
            </div>
            {data?.snapshot_date && (
              <div className="text-xs text-dim">Snapshot: {data.snapshot_date}</div>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'var(--surface-container-high)',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--on-surface)',
              padding: '4px 8px',
              cursor: 'pointer',
            }}
          >
            <X size={16} />
          </button>
        </div>

        {isLoading && <div className="loading-pulse text-dim">Lade Features…</div>}
        {error && <div style={{ color: 'var(--error)' }}>Fehler: {(error as Error).message}</div>}

        {data && (
          <>
            {/* Summary bar */}
            <div className="flex items-center gap-md mb-md" style={{ fontSize: '0.8rem' }}>
              <span>
                <strong>{data.total_filled}</strong>
                <span className="text-dim"> / {data.total_possible} Features</span>
              </span>
              <span className="text-dim">|</span>
              <span>
                1d: <span className="mono">{formatReturnVal(data.return_1d)}</span>
              </span>
              <span>
                5d: <span className="mono">{formatReturnVal(data.return_5d)}</span>
              </span>
              <span>
                20d: <span className="mono">{formatReturnVal(data.return_20d)}</span>
              </span>
              <span>
                60d: <span className="mono">{formatReturnVal(data.return_60d)}</span>
              </span>
            </div>

            {/* Feature groups */}
            {data.groups.map((group) => (
              <FeatureGroup key={group.group} group={group} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function FeatureGroup({ group }: { group: FeatureGroupDetail }) {
  const [expanded, setExpanded] = useState(group.filled > 0);
  const ratio = group.total > 0 ? group.filled / group.total : 0;

  return (
    <div style={{ marginBottom: 'var(--space-sm)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '6px 8px',
          cursor: 'pointer',
          borderRadius: 'var(--radius-sm)',
          background: 'var(--surface-container)',
          userSelect: 'none',
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-sm">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          <span style={{ fontWeight: 600, fontSize: '0.8rem' }}>{group.group}</span>
        </div>
        <div className="flex items-center gap-sm">
          <div style={{
            width: 50,
            height: 4,
            borderRadius: 2,
            background: 'var(--surface-container-highest)',
          }}>
            <div style={{
              width: `${ratio * 100}%`,
              height: '100%',
              borderRadius: 2,
              background: coverageColor(group.filled, group.total),
            }} />
          </div>
          <span className="mono text-xs" style={{ color: coverageColor(group.filled, group.total) }}>
            {group.filled}/{group.total}
          </span>
        </div>
      </div>

      {expanded && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: '2px 16px',
          padding: '8px 12px',
          fontSize: '0.75rem',
        }}>
          {Object.entries(group.features).map(([key, val]) => (
            <div key={key} className="flex items-center justify-between" style={{ padding: '2px 0' }}>
              <span className="text-dim" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.65rem' }}>
                {key}
              </span>
              <span
                className="mono"
                style={{
                  fontWeight: 600,
                  color: val === null ? 'var(--on-surface-dim)' : 'var(--on-surface)',
                  fontSize: '0.7rem',
                }}
              >
                {val === null ? '—' : typeof val === 'boolean' ? (val ? '✓' : '✗') : typeof val === 'number' ? val.toFixed(4) : String(val)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Page ───────────────────────────────────────────────────────

export default function FeaturesPage() {
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);

  // Listen for clicks on ticker cells in the coverage heatmap
  const handleTickerClick = (ticker: string) => {
    setSelectedTicker(ticker);
  };

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Features</h2>
        <span className="badge badge-success">
          <span className="badge-dot" /> Pipeline Aktiv
        </span>
      </div>

      {/* Pipeline Stats */}
      <div className="label" style={{ marginBottom: 'var(--space-md)' }}>
        Pipeline Übersicht
      </div>
      <PipelineStats />

      {/* Coverage Heatmap */}
      <div
        style={{ marginBottom: 'var(--space-2xl)' }}
        onClick={(e) => {
          const target = e.target as HTMLElement;
          const row = target.closest('tr');
          if (row) {
            const tickerCell = row.querySelector('td:first-child');
            if (tickerCell?.textContent) {
              handleTickerClick(tickerCell.textContent);
            }
          }
        }}
      >
        <CoverageHeatmap />
      </div>

      {/* Two columns: Convergence + Returns */}
      <div className="grid grid-2" style={{ marginBottom: 'var(--space-2xl)', alignItems: 'start' }}>
        <SignalConvergence />
        <ReturnDistribution />
      </div>

      {/* Ticker Detail Modal */}
      {selectedTicker && (
        <TickerDetailModal
          symbol={selectedTicker}
          onClose={() => setSelectedTicker(null)}
        />
      )}
    </div>
  );
}
