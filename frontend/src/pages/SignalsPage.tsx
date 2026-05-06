import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchArkDeltas,
  fetchArkSummary,
  fetchInsiderClusters,
  fetchPoliticianTrades,
  fetchAnalystRatings,
  fetchTickerSignals,
} from '../api';
import type { ARKDelta, InsiderCluster, PoliticianTrade, AnalystRating } from '../api';
import { TrendingUp, TrendingDown, ArrowRight, Layers, X, Filter } from 'lucide-react';

type Tab = 'ark' | 'insider' | 'politicians' | 'ratings';
type ArkView = 'summary' | 'daily';

/* ─── Reusable column filter input ─────────────────────────────────── */

const filterInputStyle: React.CSSProperties = {
  width: '100%',
  padding: '4px 6px',
  fontSize: '0.65rem',
  background: 'var(--surface-high)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: '4px',
  color: 'var(--on-surface)',
  outline: 'none',
  marginTop: '4px',
  fontFamily: 'var(--font-mono)',
};

function useColumnFilters<T extends Record<string, unknown>>(
  data: T[] | undefined,
  columns: string[],
  initialFilters?: Record<string, string>,
) {
  const [filters, setFilters] = useState<Record<string, string>>(initialFilters ?? {});

  const filtered = useMemo(() => {
    if (!data) return data;
    return data.filter((row) =>
      columns.every((col) => {
        const f = filters[col]?.trim().toLowerCase();
        if (!f) return true;
        const val = String(row[col] ?? '').toLowerCase();
        return val.includes(f);
      }),
    );
  }, [data, filters, columns]);

  const setFilter = (col: string, value: string) =>
    setFilters((prev) => ({ ...prev, [col]: value }));

  const clearFilters = () => setFilters({});
  const hasActiveFilters = Object.values(filters).some((v) => v.trim() !== '');

  return { filtered, filters, setFilter, clearFilters, hasActiveFilters };
}

function FilterInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder ?? 'Filter...'}
      style={filterInputStyle}
      onClick={(e) => e.stopPropagation()}
    />
  );
}

function FilterClearButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="btn btn-sm btn-ghost"
      onClick={onClick}
      style={{ fontSize: '0.7rem', gap: '4px' }}
    >
      <X size={12} /> Filter zurücksetzen
    </button>
  );
}

/* ─── Main Page ────────────────────────────────────────────────────── */

export default function SignalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get('tab') as Tab | null;
  const tickerFilter = searchParams.get('ticker');
  const [activeTab, setActiveTab] = useState<Tab>(urlTab && ['ark', 'insider', 'politicians', 'ratings'].includes(urlTab) ? urlTab : 'ark');
  const navigate = useNavigate();

  const { data: tickerSignals } = useQuery({
    queryKey: ['ticker-signals', tickerFilter],
    queryFn: () => fetchTickerSignals(tickerFilter!, 90),
    enabled: !!tickerFilter,
  });

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab);
    const params = new URLSearchParams();
    params.set('tab', tab);
    if (tickerFilter) params.set('ticker', tickerFilter);
    setSearchParams(params, { replace: true });
  };

  const initialTickerFilter = tickerFilter ? { ticker: tickerFilter } : undefined;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Signals</h2>
      </div>

      <div className="tabs">
        {(['ark', 'insider', 'politicians', 'ratings'] as Tab[]).map((tab) => (
          <button
            key={tab}
            className={`tab${activeTab === tab ? ' active' : ''}`}
            onClick={() => handleTabChange(tab)}
          >
            {tab === 'ark' ? 'ARK' :
             tab === 'insider' ? 'Insider' :
             tab === 'politicians' ? 'Politiker' : 'Analyst'}
          </button>
        ))}
      </div>

      {activeTab === 'ark' && <ArkTab navigate={navigate} tickerFilter={tickerFilter} tickerData={tickerSignals?.ark_deltas} initialFilters={initialTickerFilter} />}
      {activeTab === 'insider' && <InsiderTab navigate={navigate} tickerFilter={tickerFilter} tickerData={tickerSignals?.insider_clusters} initialFilters={initialTickerFilter} />}
      {activeTab === 'politicians' && <PoliticianTab navigate={navigate} tickerFilter={tickerFilter} tickerData={tickerSignals?.politician_trades} initialFilters={initialTickerFilter} />}
      {activeTab === 'ratings' && <RatingsTab navigate={navigate} tickerFilter={tickerFilter} tickerData={tickerSignals?.analyst_ratings} initialFilters={initialTickerFilter} />}
    </div>
  );
}

/* ─── ARK Tab ──────────────────────────────────────────────────────── */

function ArkTab({ navigate, tickerFilter, tickerData, initialFilters }: { navigate: (path: string) => void; tickerFilter: string | null; tickerData?: ARKDelta[]; initialFilters?: Record<string, string> }) {
  const [view, setView] = useState<ArkView>('summary');
  const [summaryDays, setSummaryDays] = useState(5);

  return (
    <div>
      <div className="flex items-center gap-md mb-lg" style={{ flexWrap: 'wrap' }}>
        <div className="flex gap-xs" style={{
          background: 'var(--surface-high)',
          borderRadius: 'var(--radius)',
          padding: '2px',
        }}>
          <button
            className={`btn btn-sm ${view === 'summary' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView('summary')}
            style={{ fontSize: '0.72rem' }}
          >
            <Layers size={12} />
            Zusammenfassung
          </button>
          <button
            className={`btn btn-sm ${view === 'daily' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setView('daily')}
            style={{ fontSize: '0.72rem' }}
          >
            Täglich
          </button>
        </div>

        {view === 'summary' && (
          <div className="flex gap-xs items-center">
            {[5, 10, 20].map((d) => (
              <button
                key={d}
                className={`btn btn-sm ${summaryDays === d ? 'btn-secondary' : 'btn-ghost'}`}
                onClick={() => setSummaryDays(d)}
                style={{ fontSize: '0.7rem', minWidth: 42 }}
              >
                {d}T
              </button>
            ))}
          </div>
        )}
      </div>

      {view === 'summary'
        ? <ArkSummaryView days={summaryDays} navigate={navigate} tickerFilter={tickerFilter} initialFilters={initialFilters} />
        : <ArkDailyView navigate={navigate} tickerFilter={tickerFilter} tickerData={tickerData} initialFilters={initialFilters} />}
    </div>
  );
}

function ArkSummaryView({ days, navigate, tickerFilter, initialFilters }: { days: number; navigate: (path: string) => void; tickerFilter: string | null; initialFilters?: Record<string, string> }) {
  const effectiveDays = tickerFilter ? 90 : days;
  const { data: rawData, isLoading } = useQuery({
    queryKey: ['signals-ark-summary', effectiveDays, tickerFilter],
    queryFn: () => fetchArkSummary(effectiveDays),
  });

  const cols = ['ticker', 'direction'];
  const { filtered: data, filters, setFilter, clearFilters, hasActiveFilters } = useColumnFilters(rawData, cols, initialFilters);

  if (isLoading) return <Loading />;

  return (
    <div>
      {hasActiveFilters && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">{data?.length ?? 0} von {rawData?.length ?? 0} Einträgen</span>
          <FilterClearButton onClick={clearFilters} />
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Ticker<FilterInput value={filters.ticker ?? ''} onChange={(v) => setFilter('ticker', v)} /></th>
              <th>Richtung<FilterInput value={filters.direction ?? ''} onChange={(v) => setFilter('direction', v)} /></th>
              <th>ETFs</th>
              <th className="text-right">Shares Δ (gesamt)</th>
              <th className="text-right">Weight Δ (bps)</th>
              <th className="text-right">Tage</th>
              <th className="text-xs text-dim">Zeitraum</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((s, i) => (
              <tr key={i} onClick={() => navigate(`/ticker/${s.ticker}`)} style={{ cursor: 'pointer' }}>
                <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
                  {s.ticker}
                </td>
                <td>
                  {s.direction === 'increased' ? (
                    <span className="badge badge-success"><TrendingUp size={10} /> AUFGESTOCKT</span>
                  ) : s.direction === 'decreased' ? (
                    <span className="badge badge-warning"><TrendingDown size={10} /> REDUZIERT</span>
                  ) : (
                    <span className="badge badge-neutral">GEMISCHT</span>
                  )}
                </td>
                <td>
                  <div className="flex gap-xs items-center" style={{ flexWrap: 'wrap' }}>
                    {s.etfs.map((etf) => (
                      <span key={etf} className="mono" style={{ fontSize: '0.65rem', padding: '1px 5px', borderRadius: '4px', background: 'rgba(255,255,255,0.06)', color: 'var(--text-dim)' }}>{etf}</span>
                    ))}
                    {s.n_etfs >= 2 && (
                      <span style={{ fontSize: '0.6rem', padding: '1px 6px', borderRadius: '10px', background: 'rgba(56,189,248,0.15)', color: 'var(--primary)', fontWeight: 600 }}>Cross-ETF</span>
                    )}
                  </div>
                </td>
                <td className="text-right mono text-sm">{s.total_shares_delta?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '—'}</td>
                <td className="text-right mono text-sm" style={{ color: s.total_weight_delta_bps > 0 ? 'var(--success)' : s.total_weight_delta_bps < 0 ? 'var(--warning)' : 'var(--text-dim)', fontWeight: 600 }}>
                  {s.total_weight_delta_bps > 0 ? '+' : ''}{s.total_weight_delta_bps.toFixed(1)}
                </td>
                <td className="text-right mono text-sm text-dim">{s.n_days}</td>
                <td className="text-xs text-dim">{s.first_date === s.last_date ? s.first_date : `${s.first_date} → ${s.last_date}`}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr><td colSpan={7} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten im Zeitraum</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ArkDailyView({ navigate, tickerFilter, tickerData, initialFilters }: { navigate: (path: string) => void; tickerFilter: string | null; tickerData?: ARKDelta[]; initialFilters?: Record<string, string> }) {
  const { data: globalData, isLoading } = useQuery({
    queryKey: ['signals-ark'],
    queryFn: () => fetchArkDeltas(14),
    enabled: !tickerFilter,
  });

  const rawData = tickerFilter ? tickerData : globalData;
  const cols = ['ticker', 'etf_ticker', 'delta_type'];
  const { filtered: data, filters, setFilter, clearFilters, hasActiveFilters } = useColumnFilters(rawData, cols, initialFilters);

  if (!tickerFilter && isLoading) return <Loading />;

  return (
    <div>
      {hasActiveFilters && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">{data?.length ?? 0} von {rawData?.length ?? 0} Einträgen</span>
          <FilterClearButton onClick={clearFilters} />
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Datum</th>
              <th>ETF<FilterInput value={filters.etf_ticker ?? ''} onChange={(v) => setFilter('etf_ticker', v)} /></th>
              <th>Ticker<FilterInput value={filters.ticker ?? ''} onChange={(v) => setFilter('ticker', v)} /></th>
              <th>Typ<FilterInput value={filters.delta_type ?? ''} onChange={(v) => setFilter('delta_type', v)} /></th>
              <th className="text-right">Shares Δ</th>
              <th className="text-right">Weight Δ (bps)</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((d, i) => (
              <tr key={i} onClick={() => d.ticker && navigate(`/ticker/${d.ticker}`)}>
                <td className="text-xs text-dim">{d.delta_date}</td>
                <td className="mono text-xs">{d.etf_ticker}</td>
                <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>{d.ticker}</td>
                <td>
                  {d.delta_type === 'new_position' ? (
                    <span className="badge badge-success">NEU</span>
                  ) : d.delta_type === 'closed' ? (
                    <span className="badge badge-error">CLOSED</span>
                  ) : d.delta_type === 'increased' ? (
                    <span className="badge badge-success"><TrendingUp size={10} /> ERHÖHT</span>
                  ) : (
                    <span className="badge badge-warning"><TrendingDown size={10} /> REDUZIERT</span>
                  )}
                </td>
                <td className="text-right mono text-sm">{d.shares_delta?.toLocaleString() ?? '—'}</td>
                <td className="text-right mono text-sm">{d.weight_delta != null ? (d.weight_delta * 100).toFixed(1) : '—'}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr><td colSpan={6} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Insider Tab (now table layout) ───────────────────────────────── */

function InsiderTab({ navigate, tickerFilter, tickerData, initialFilters }: { navigate: (path: string) => void; tickerFilter: string | null; tickerData?: InsiderCluster[]; initialFilters?: Record<string, string> }) {
  const { data: globalData, isLoading } = useQuery({
    queryKey: ['signals-insider'],
    queryFn: () => fetchInsiderClusters(60),
    enabled: !tickerFilter,
  });

  const rawData = tickerFilter ? tickerData : globalData;
  const cols = ['ticker'];
  const { filtered: data, filters, setFilter, clearFilters, hasActiveFilters } = useColumnFilters(rawData, cols, initialFilters);

  if (!tickerFilter && isLoading) return <Loading />;

  return (
    <div>
      {hasActiveFilters && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">{data?.length ?? 0} von {rawData?.length ?? 0} Einträgen</span>
          <FilterClearButton onClick={clearFilters} />
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Ticker<FilterInput value={filters.ticker ?? ''} onChange={(v) => setFilter('ticker', v)} /></th>
              <th>Score</th>
              <th className="text-right">Insider</th>
              <th className="text-right">Käufe</th>
              <th className="text-right">Verkäufe</th>
              <th className="text-right">Kaufvolumen</th>
              <th className="text-xs text-dim">Zeitraum</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((c, i) => (
              <tr key={i} onClick={() => navigate(`/ticker/${c.ticker}`)} style={{ cursor: 'pointer' }}>
                <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>{c.ticker}</td>
                <td>
                  <span className="badge badge-success">
                    {c.cluster_score?.toFixed(1) ?? '—'}
                  </span>
                </td>
                <td className="text-right mono text-sm">{c.n_insiders}</td>
                <td className="text-right mono text-sm" style={{ color: 'var(--success)' }}>{c.n_buys}</td>
                <td className="text-right mono text-sm" style={{ color: 'var(--error)' }}>{c.n_sells}</td>
                <td className="text-right mono text-sm">
                  {c.total_buy_value ? `$${(c.total_buy_value / 1_000_000).toFixed(2)}M` : '—'}
                </td>
                <td className="text-xs text-dim">{c.cluster_start} → {c.cluster_end}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr><td colSpan={7} className="text-dim" style={{ textAlign: 'center' }}>Keine aktiven Cluster</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Politician Tab ───────────────────────────────────────────────── */

function PoliticianTab({ navigate, tickerFilter, tickerData, initialFilters }: { navigate: (path: string) => void; tickerFilter: string | null; tickerData?: PoliticianTrade[]; initialFilters?: Record<string, string> }) {
  const { data: globalData, isLoading } = useQuery({
    queryKey: ['signals-politicians'],
    queryFn: () => fetchPoliticianTrades(60),
    enabled: !tickerFilter,
  });

  const rawData = tickerFilter ? tickerData : globalData;
  const cols = ['ticker', 'politician_name', 'party', 'transaction_type'];
  const { filtered: data, filters, setFilter, clearFilters, hasActiveFilters } = useColumnFilters(rawData, cols, initialFilters);

  if (!tickerFilter && isLoading) return <Loading />;

  return (
    <div>
      {hasActiveFilters && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">{data?.length ?? 0} von {rawData?.length ?? 0} Einträgen</span>
          <FilterClearButton onClick={clearFilters} />
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Offenlegung</th>
              <th>Trade-Datum</th>
              <th className="text-right">Verzög.</th>
              <th>Politiker<FilterInput value={filters.politician_name ?? ''} onChange={(v) => setFilter('politician_name', v)} /></th>
              <th>Partei<FilterInput value={filters.party ?? ''} onChange={(v) => setFilter('party', v)} /></th>
              <th>Ticker<FilterInput value={filters.ticker ?? ''} onChange={(v) => setFilter('ticker', v)} /></th>
              <th>Typ<FilterInput value={filters.transaction_type ?? ''} onChange={(v) => setFilter('transaction_type', v)} /></th>
              <th>Betrag</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((t, i) => (
              <tr key={i} onClick={() => t.ticker && navigate(`/ticker/${t.ticker}`)}>
                <td className="text-xs text-dim">{t.disclosure_date ?? '—'}</td>
                <td className="text-xs text-dim">{t.transaction_date ?? '—'}</td>
                <td className="text-right">
                  {t.delay_days != null ? (
                    <span className="mono" style={{
                      fontSize: '0.7rem', fontWeight: 600,
                      color: t.delay_days <= 7 ? 'var(--success)' : t.delay_days <= 30 ? 'var(--warning)' : 'var(--error)',
                    }}>{t.delay_days}d</span>
                  ) : '—'}
                </td>
                <td style={{ fontWeight: 500 }}>{t.politician_name}</td>
                <td>
                  <span className="badge badge-neutral" style={{ fontSize: '0.6rem' }}>{t.party ?? '—'}</span>
                </td>
                <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>{t.ticker ?? '—'}</td>
                <td>
                  <span className={`badge ${t.transaction_type?.toLowerCase().includes('purchase') ? 'badge-success' : 'badge-error'}`}>
                    {t.transaction_type ?? '—'}
                  </span>
                </td>
                <td className="text-xs text-variant">{t.amount_range ?? '—'}</td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr><td colSpan={8} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Ratings Tab ──────────────────────────────────────────────────── */

function RatingsTab({ navigate, tickerFilter, tickerData, initialFilters }: { navigate: (path: string) => void; tickerFilter: string | null; tickerData?: AnalystRating[]; initialFilters?: Record<string, string> }) {
  const { data: globalData, isLoading } = useQuery({
    queryKey: ['signals-ratings'],
    queryFn: () => fetchAnalystRatings(14),
    enabled: !tickerFilter,
  });

  const rawData = tickerFilter ? tickerData : globalData;
  const cols = ['ticker', 'firm', 'action'];
  const { filtered: data, filters, setFilter, clearFilters, hasActiveFilters } = useColumnFilters(rawData, cols, initialFilters);

  if (!tickerFilter && isLoading) return <Loading />;

  return (
    <div>
      {hasActiveFilters && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">{data?.length ?? 0} von {rawData?.length ?? 0} Einträgen</span>
          <FilterClearButton onClick={clearFilters} />
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Datum</th>
              <th>Ticker<FilterInput value={filters.ticker ?? ''} onChange={(v) => setFilter('ticker', v)} /></th>
              <th>Analyst<FilterInput value={filters.firm ?? ''} onChange={(v) => setFilter('firm', v)} /></th>
              <th>Aktion<FilterInput value={filters.action ?? ''} onChange={(v) => setFilter('action', v)} /></th>
              <th>Alt → Neu</th>
              <th className="text-right">Kursziel</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((r, i) => (
              <tr key={i} onClick={() => navigate(`/ticker/${r.ticker}`)}>
                <td className="text-xs text-dim">{r.rating_date ?? '—'}</td>
                <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>{r.ticker}</td>
                <td className="text-sm">{r.firm ?? '—'}</td>
                <td>
                  <span className={`badge ${
                    r.action?.toLowerCase().includes('upgrade') ? 'badge-success' :
                    r.action?.toLowerCase().includes('downgrade') ? 'badge-error' :
                    'badge-neutral'
                  }`}>{r.action ?? '—'}</span>
                </td>
                <td className="text-sm">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-xs)' }}>
                    <span className="text-dim">{r.rating_old ?? '—'}</span>
                    <ArrowRight size={12} style={{ color: 'var(--on-surface-dim)' }} />
                    <span>{r.rating_new ?? '—'}</span>
                  </span>
                </td>
                <td className="text-right mono text-sm">
                  {r.price_target_new ? `$${r.price_target_new.toFixed(0)}` : '—'}
                </td>
              </tr>
            ))}
            {data?.length === 0 && (
              <tr><td colSpan={6} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Loading ──────────────────────────────────────────────────────── */

function Loading() {
  return (
    <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)' }}>
      Lade Signale...
    </div>
  );
}
