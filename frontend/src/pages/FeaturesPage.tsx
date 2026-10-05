import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  Brain,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Layers,
  Search,
  Target,
  TrendingUp,
  X,
} from 'lucide-react';
import type {
  FeatureCoverageItem,
  FeatureGroupDetail,
  FeatureGroupMeta,
  FeatureStats,
  HorizonStats,
  SignalConvergenceItem,
} from '../api';
import {
  featureCoverageQuery,
  featureStatsQuery,
  returnStatsQuery,
  signalConvergenceQuery,
  tickerFeaturesQuery,
} from '../queries';
import { daysSince, formatFixed, formatNumber, formatPct, formatRatioPct } from '../format';
import { ErrorCard, QueryState } from '../components/QueryState';

/** Snapshots older than this many calendar days count as stale (covers weekends). */
const PIPELINE_STALE_DAYS = 4;
const CONVERGENCE_LIMIT = 50;

// ── Helpers ────────────────────────────────────────────────────────────

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

function formatReturn(v: number | null): string {
  return formatRatioPct(v, 2);
}

// ── Pipeline Status Badge ───────────────────────────────────────────

function PipelineBadge({ stats }: { stats: FeatureStats | undefined }) {
  if (!stats) return null;
  const age = daysSince(stats.last_snapshot_date);
  if (age == null) {
    return <span className="badge badge-neutral">Keine Snapshots</span>;
  }
  if (age <= PIPELINE_STALE_DAYS) {
    return (
      <span className="badge badge-success" title={`Letzter Snapshot: ${stats.last_snapshot_date}`}>
        <span className="badge-dot" /> Pipeline aktuell
      </span>
    );
  }
  return (
    <span className="badge badge-warning" title={`Letzter Snapshot: ${stats.last_snapshot_date}`}>
      Pipeline veraltet ({age} Tage)
    </span>
  );
}

// ── Pipeline Stats Section ──────────────────────────────────────────

function PipelineStats() {
  const query = useQuery(featureStatsQuery());
  const skeleton = (
    <div className="grid grid-4" style={{ marginBottom: 'var(--space-2xl)' }}>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="card loading-pulse" style={{ height: 88 }} />
      ))}
    </div>
  );

  return (
    <QueryState query={query} loading={skeleton}>
      {(data) => {
        const stats = [
          { label: 'Letzter Snapshot', value: data.last_snapshot_date ?? '—', icon: Brain, color: 'var(--primary)' },
          { label: 'Ticker Coverage', value: formatNumber(data.ticker_count), icon: Layers, color: 'var(--primary)' },
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
      }}
    </QueryState>
  );
}

// ── Coverage Heatmap ────────────────────────────────────────────────

type CoverageSortKey = 'ticker' | 'total_filled';

function SortIcon({ active, asc }: { active: boolean; asc: boolean }) {
  if (!active) return null;
  return asc ? <ChevronUp size={12} /> : <ChevronDown size={12} />;
}

function CoverageHeatmap({ onSelect }: { onSelect: (ticker: string) => void }) {
  const query = useQuery(featureCoverageQuery());
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<CoverageSortKey>('total_filled');
  const [sortAsc, setSortAsc] = useState(false);

  const items = query.data?.items;
  const filtered = useMemo(() => {
    if (!items) return [];
    const needle = search.trim().toLowerCase();
    const rows = needle ? items.filter((i) => i.ticker.toLowerCase().includes(needle)) : [...items];
    rows.sort((a, b) => {
      const cmp = sortKey === 'ticker' ? a.ticker.localeCompare(b.ticker) : a.total_filled - b.total_filled;
      return sortAsc ? cmp : -cmp;
    });
    return rows;
  }, [items, search, sortKey, sortAsc]);

  const toggleSort = (key: CoverageSortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(key === 'ticker');
    }
  };

  return (
    <QueryState query={query} loading={<div className="card loading-pulse" style={{ height: 300 }} />}>
      {(data) => (
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
                aria-label="Ticker suchen"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  padding: '4px 8px 4px 28px',
                  background: 'var(--surface-high)',
                  border: '1px solid var(--outline-variant)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--on-surface)',
                  fontSize: '0.75rem',
                  width: 160,
                }}
              />
              {search && (
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Suche leeren"
                  onClick={() => setSearch('')}
                  style={{ position: 'absolute', right: 6, top: 5 }}
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </div>
          {data.items.length === 0 ? (
            <div className="empty-state text-dim">Noch keine Feature-Snapshots vorhanden.</div>
          ) : (
            <div style={{ maxHeight: 480, overflowY: 'auto' }}>
              <table className="data-table" style={{ fontSize: '0.75rem' }}>
                <thead>
                  <tr>
                    <th aria-sort={sortKey === 'ticker' ? (sortAsc ? 'ascending' : 'descending') : undefined}>
                      <button type="button" className="th-sort" onClick={() => toggleSort('ticker')}>
                        Ticker <SortIcon active={sortKey === 'ticker'} asc={sortAsc} />
                      </button>
                    </th>
                    {data.groups.map((g) => (
                      <th
                        key={g.key}
                        className="text-center"
                        style={{ fontSize: '0.65rem' }}
                        title={g.market_wide ? 'Marktweite Features (für alle Ticker gleich)' : undefined}
                      >
                        {g.label}{g.market_wide ? '*' : ''}
                      </th>
                    ))}
                    <th
                      className="text-right"
                      aria-sort={sortKey === 'total_filled' ? (sortAsc ? 'ascending' : 'descending') : undefined}
                    >
                      <button type="button" className="th-sort" onClick={() => toggleSort('total_filled')}>
                        Gesamt <SortIcon active={sortKey === 'total_filled'} asc={sortAsc} />
                      </button>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((item) => (
                    <CoverageRow key={item.ticker} item={item} groups={data.groups} onSelect={onSelect} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="text-xs text-dim" style={{ padding: 'var(--space-sm) var(--space-lg)' }}>
            {filtered.length} / {data.items.length} Ticker · Zeile anklicken für Feature-Details
            {data.groups.some((g) => g.market_wide) && ' · * marktweit (für alle Ticker gleich)'}
          </div>
        </div>
      )}
    </QueryState>
  );
}

function CoverageRow({
  item,
  groups,
  onSelect,
}: {
  item: FeatureCoverageItem;
  groups: FeatureGroupMeta[];
  onSelect: (ticker: string) => void;
}) {
  const onKeyDown = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(item.ticker);
    }
  };
  return (
    <tr onClick={() => onSelect(item.ticker)} onKeyDown={onKeyDown} tabIndex={0} style={{ cursor: 'pointer' }}>
      <td className="mono" style={{ fontWeight: 600 }}>{item.ticker}</td>
      {groups.map((g) => {
        const filled = item.counts[g.key] ?? 0;
        return (
          <td
            key={g.key}
            className="text-center mono"
            style={{
              color: coverageColor(filled, g.total),
              backgroundColor: coverageBg(filled, g.total),
              fontWeight: filled > 0 ? 600 : 400,
            }}
          >
            {filled}/{g.total}
          </td>
        );
      })}
      <td className="text-right mono" style={{ fontWeight: 600 }}>
        <span style={{ color: coverageColor(item.total_filled, item.total_possible) }}>{item.total_filled}</span>
        <span className="text-dim">/{item.total_possible}</span>
      </td>
    </tr>
  );
}

// ── Signal Convergence ──────────────────────────────────────────────

function SignalConvergence() {
  const query = useQuery(signalConvergenceQuery(CONVERGENCE_LIMIT));
  const navigate = useNavigate();

  return (
    <QueryState query={query} loading={<div className="card loading-pulse" style={{ height: 200 }} />}>
      {(data) => (
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
            <div className="text-xs text-dim">
              Quellen = ticker-spezifische Feature-Gruppen mit aktivem Signal (max. {data.max_sources}).
              {data.excluded_groups.length > 0 && ` Marktweite Gruppen (${data.excluded_groups.join(', ')}) zählen nicht.`}
              {data.total > data.items.length && ` Zeige Top ${data.items.length} von ${data.total}.`}
            </div>
          </div>
          {data.items.length === 0 ? (
            <div className="empty-state text-dim">Keine Ticker mit aktiven Signalen.</div>
          ) : (
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
                    <th className="text-right">Sentiment</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <ConvergenceRow
                      key={item.ticker}
                      item={item}
                      maxSources={data.max_sources}
                      onOpen={() => navigate(`/ticker/${encodeURIComponent(item.ticker)}`)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </QueryState>
  );
}

function sourcesColor(active: number, max: number): string {
  const ratio = max > 0 ? active / max : 0;
  if (ratio >= 0.5) return 'var(--primary)';
  if (ratio >= 0.3) return 'var(--warning)';
  return 'var(--on-surface-dim)';
}

function sentimentColor(v: number | null): string | undefined {
  if (v == null) return undefined;
  if (v > 0.1) return 'var(--success)';
  if (v < -0.1) return 'var(--error)';
  return 'var(--on-surface-dim)';
}

function ConvergenceRow({
  item,
  maxSources,
  onOpen,
}: {
  item: SignalConvergenceItem;
  maxSources: number;
  onOpen: () => void;
}) {
  const barWidth = maxSources > 0 ? Math.min(100, (item.active_sources / maxSources) * 100) : 0;
  return (
    <tr
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpen();
      }}
      tabIndex={0}
      style={{ cursor: 'pointer' }}
    >
      <td className="mono" style={{ fontWeight: 600 }}>
        <Link
          to={`/ticker/${encodeURIComponent(item.ticker)}`}
          onClick={(e) => e.stopPropagation()}
          style={{ color: 'var(--primary)' }}
        >
          {item.ticker}
        </Link>
      </td>
      <td className="text-center">
        <div
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 60 }}
          title={`${item.active_sources} von ${maxSources} Quellen`}
        >
          <div style={{ height: 6, borderRadius: 3, background: 'var(--surface-highest)', flex: 1, minWidth: 40 }}>
            <div
              style={{
                height: '100%',
                width: `${barWidth}%`,
                borderRadius: 3,
                background: sourcesColor(item.active_sources, maxSources),
                transition: 'width 0.3s ease',
              }}
            />
          </div>
          <span className="mono" style={{ fontWeight: 600, fontSize: '0.75rem' }}>
            {item.active_sources}/{maxSources}
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
                background: 'var(--surface-high)',
                color: 'var(--on-surface)',
                whiteSpace: 'nowrap',
              }}
            >
              {s}
            </span>
          ))}
        </div>
      </td>
      <td className="text-right mono">{formatFixed(item.ark_conviction_score)}</td>
      <td className="text-right mono">{formatFixed(item.insider_cluster_score)}</td>
      <td className="text-right mono">{formatFixed(item.analyst_rating_score)}</td>
      <td className="text-right mono">{formatFixed(item.rsi_14, 1)}</td>
      <td
        className="text-right mono"
        style={{ color: sentimentColor(item.sentiment_avg_7d), fontWeight: item.sentiment_avg_7d != null ? 600 : 400 }}
      >
        {formatFixed(item.sentiment_avg_7d)}
      </td>
    </tr>
  );
}

// ── Return Distribution ─────────────────────────────────────────────

function ReturnDistribution() {
  const query = useQuery(returnStatsQuery());

  return (
    <QueryState query={query} loading={<div className="card loading-pulse" style={{ height: 160 }} />}>
      {(data) =>
        data.horizons.length === 0 ? (
          <div className="card">
            <div className="card-title">Return Distribution</div>
            <div className="text-dim">Noch keine Returns berechnet – der Target-Backfill füllt sie nachträglich.</div>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: 'var(--space-lg) var(--space-lg) var(--space-sm)' }}>
              <div className="card-title" style={{ margin: 0 }}>Forward Returns – Target Variable Übersicht</div>
              <div className="text-xs text-dim">{formatNumber(data.total_snapshots)} Snapshots insgesamt</div>
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
                {data.horizons.map((h) => <ReturnRow key={h.horizon} h={h} />)}
              </tbody>
            </table>
          </div>
        )
      }
    </QueryState>
  );
}

function ReturnRow({ h }: { h: HorizonStats }) {
  const fillColor = h.filled_pct > 50 ? 'var(--primary)' : h.filled_pct > 0 ? 'var(--warning)' : 'var(--on-surface-dim)';
  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{h.horizon}</td>
      <td className="text-right mono">{formatNumber(h.filled_count)}</td>
      <td className="text-right mono" style={{ color: fillColor }}>{formatPct(h.filled_pct)}</td>
      <td className="text-right mono">{formatReturn(h.mean)}</td>
      <td className="text-right mono">{formatReturn(h.median)}</td>
      <td className="text-right mono">{formatReturn(h.std)}</td>
      <td className="text-right mono" style={{ color: h.min_val != null && h.min_val < 0 ? 'var(--error)' : undefined }}>
        {formatReturn(h.min_val)}
      </td>
      <td className="text-right mono" style={{ color: h.max_val != null && h.max_val > 0 ? 'var(--primary)' : undefined }}>
        {formatReturn(h.max_val)}
      </td>
    </tr>
  );
}

// ── Ticker Detail Modal ─────────────────────────────────────────────

function TickerDetailModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const query = useQuery(tickerFeaturesQuery(symbol));
  const { data } = query;

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

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
        role="dialog"
        aria-modal="true"
        aria-label={`Features ${symbol}`}
        style={{ width: '90%', maxWidth: 720, maxHeight: '80vh', overflowY: 'auto', animation: 'fadeIn 0.2s ease-out' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-md">
          <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{symbol}</div>
            {data?.snapshot_date && <div className="text-xs text-dim">Snapshot: {data.snapshot_date}</div>}
          </div>
          <div className="flex items-center gap-sm">
            <Link to={`/ticker/${encodeURIComponent(symbol)}`} className="btn btn-ghost btn-sm">
              <ExternalLink size={12} /> Ticker-Seite
            </Link>
            <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Schließen" autoFocus>
              <X size={16} />
            </button>
          </div>
        </div>

        {query.isError && !data && <ErrorCard error={query.error} onRetry={query.refetch} />}
        {query.isPending && <div className="loading-pulse text-dim">Lade Features…</div>}

        {data && (
          <>
            <div className="flex items-center gap-md mb-md" style={{ fontSize: '0.8rem', flexWrap: 'wrap' }}>
              <span>
                <strong>{data.total_filled}</strong>
                <span className="text-dim"> / {data.total_possible} Features</span>
              </span>
              <span className="text-dim">|</span>
              <span>1d: <span className="mono">{formatReturn(data.return_1d)}</span></span>
              <span>5d: <span className="mono">{formatReturn(data.return_5d)}</span></span>
              <span>20d: <span className="mono">{formatReturn(data.return_20d)}</span></span>
              <span>60d: <span className="mono">{formatReturn(data.return_60d)}</span></span>
            </div>
            {data.groups.map((group) => <FeatureGroup key={group.key} group={group} />)}
          </>
        )}
      </div>
    </div>
  );
}

function formatFeatureValue(val: number | boolean | null): string {
  if (val == null) return '—';
  if (typeof val === 'boolean') return val ? '✓' : '✗';
  return Number.isInteger(val) ? String(val) : val.toFixed(4);
}

function FeatureGroup({ group }: { group: FeatureGroupDetail }) {
  const [expanded, setExpanded] = useState(group.filled > 0);
  const ratio = group.total > 0 ? group.filled / group.total : 0;

  return (
    <div style={{ marginBottom: 'var(--space-sm)' }}>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
        className="feature-group-header"
      >
        <span className="flex items-center gap-sm">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          <span style={{ fontWeight: 600, fontSize: '0.8rem' }}>{group.group}</span>
          {group.market_wide && <span className="badge badge-neutral" style={{ fontSize: '0.55rem' }}>marktweit</span>}
        </span>
        <span className="flex items-center gap-sm">
          <span style={{ width: 50, height: 4, borderRadius: 2, background: 'var(--surface-highest)', display: 'inline-block' }}>
            <span
              style={{
                width: `${ratio * 100}%`,
                height: '100%',
                borderRadius: 2,
                background: coverageColor(group.filled, group.total),
                display: 'block',
              }}
            />
          </span>
          <span className="mono text-xs" style={{ color: coverageColor(group.filled, group.total) }}>
            {group.filled}/{group.total}
          </span>
        </span>
      </button>

      {expanded && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '2px 16px',
            padding: '8px 12px',
            fontSize: '0.75rem',
          }}
        >
          {Object.entries(group.features).map(([key, val]) => (
            <div key={key} className="flex items-center justify-between" style={{ padding: '2px 0' }}>
              <span className="text-dim" style={{ fontFamily: 'var(--font-mono)', fontSize: '0.65rem' }}>{key}</span>
              <span
                className="mono"
                style={{
                  fontWeight: 600,
                  color: val == null ? 'var(--on-surface-dim)' : 'var(--on-surface)',
                  fontSize: '0.7rem',
                }}
              >
                {formatFeatureValue(val)}
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
  const stats = useQuery(featureStatsQuery());

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Features</h2>
        <PipelineBadge stats={stats.data} />
      </div>

      <div className="label" style={{ marginBottom: 'var(--space-md)' }}>Pipeline Übersicht</div>
      <PipelineStats />

      <div style={{ marginBottom: 'var(--space-2xl)' }}>
        <CoverageHeatmap onSelect={setSelectedTicker} />
      </div>

      <div className="grid grid-2" style={{ marginBottom: 'var(--space-2xl)', alignItems: 'start' }}>
        <SignalConvergence />
        <ReturnDistribution />
      </div>

      {selectedTicker && (
        <TickerDetailModal symbol={selectedTicker} onClose={() => setSelectedTicker(null)} />
      )}
    </div>
  );
}
