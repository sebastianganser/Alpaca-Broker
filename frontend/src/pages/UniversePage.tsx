import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Search } from 'lucide-react';
import type { TickerSummary, UniverseParams } from '../api';
import { sectorsQuery, universeQuery } from '../queries';
import { formatSignedPct, formatUsd } from '../format';
import { QueryState } from '../components/QueryState';
import { useDebouncedValue } from '../hooks/useDebouncedValue';

const PAGE_SIZE = 50;

type ActiveFilter = NonNullable<UniverseParams['active']>;

function parseActive(v: string | null): ActiveFilter {
  return v === 'true' || v === 'false' ? v : '';
}

/**
 * Ticker universe. Page, search, sector and active filter live in the URL
 * so the view survives navigation to a ticker and back.
 */
export default function UniversePage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, Number(searchParams.get('page')) || 1);
  const search = searchParams.get('search') ?? '';
  const sector = searchParams.get('sector') ?? '';
  const active = parseActive(searchParams.get('active'));
  const debouncedSearch = useDebouncedValue(search.trim(), 300);

  /** Update URL params; any filter change resets to page 1. */
  const update = (changes: Record<string, string>, resetPage = true) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        Object.entries(changes).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
        if (resetPage) next.delete('page');
        return next;
      },
      { replace: true },
    );
  };
  const goToPage = (p: number) => update({ page: p > 1 ? String(p) : '' }, false);

  const sectorsQ = useQuery(sectorsQuery());
  const universeQ = useQuery(
    universeQuery({
      page,
      limit: PAGE_SIZE,
      search: debouncedSearch || undefined,
      sector: sector || undefined,
      active: active || undefined,
    }),
  );
  const data = universeQ.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 0;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Universe</h2>
        <span className="badge badge-neutral">{data ? data.total.toLocaleString('de-DE') : '…'} Ticker</span>
        {universeQ.isFetching && data && <span className="text-xs text-dim loading-pulse">aktualisiere…</span>}
      </div>

      {/* Filters */}
      <div className="flex gap-md items-center mb-lg" style={{ flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: '1', maxWidth: '320px' }}>
          <Search
            size={16}
            style={{
              position: 'absolute',
              left: '12px',
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--on-surface-dim)',
            }}
          />
          <input
            className="input"
            type="search"
            placeholder="Ticker oder Name suchen…"
            aria-label="Ticker oder Name suchen"
            value={search}
            onChange={(e) => update({ search: e.target.value })}
            style={{ paddingLeft: '36px' }}
          />
        </div>
        <select
          className="input"
          aria-label="Sektor-Filter"
          value={sector}
          onChange={(e) => update({ sector: e.target.value })}
          style={{ width: 'auto', minWidth: '180px' }}
        >
          <option value="">Alle Sektoren</option>
          {sectorsQ.data?.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          className="input"
          aria-label="Aktiv-Filter"
          value={active}
          onChange={(e) => update({ active: e.target.value })}
          style={{ width: 'auto', minWidth: '120px' }}
        >
          <option value="">Alle</option>
          <option value="true">Aktiv</option>
          <option value="false">Inaktiv</option>
        </select>
      </div>

      <QueryState
        query={universeQ}
        loadingText="Lade Ticker…"
        isEmpty={(d) => d.tickers.length === 0}
        emptyText={<div className="card text-dim text-center">Keine Ticker gefunden</div>}
      >
        {(d) => (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Name</th>
                  <th>Sektor</th>
                  <th>Exchange</th>
                  <th>Index</th>
                  <th className="text-right">Letzter Preis</th>
                  <th className="text-right">Δ Vortag</th>
                  <th>Datum</th>
                </tr>
              </thead>
              <tbody>
                {d.tickers.map((t) => (
                  <UniverseRow
                    key={t.ticker}
                    t={t}
                    onOpen={() => navigate(`/ticker/${encodeURIComponent(t.ticker)}`)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-lg">
          <div className="text-xs text-dim">Seite {page} von {totalPages}</div>
          <div className="flex gap-sm">
            <button className="btn btn-ghost btn-sm" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
              <ChevronLeft size={14} /> Zurück
            </button>
            <button className="btn btn-ghost btn-sm" disabled={page >= totalPages} onClick={() => goToPage(page + 1)}>
              Weiter <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function UniverseRow({ t, onOpen }: { t: TickerSummary; onOpen: () => void }) {
  const change = t.price_change_pct;
  return (
    <tr
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpen();
      }}
      tabIndex={0}
      style={{ cursor: 'pointer', opacity: t.is_active ? 1 : 0.6 }}
    >
      <td>
        <span className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>{t.ticker}</span>
      </td>
      <td>{t.company_name ?? '—'}</td>
      <td className="text-xs text-variant">{t.sector ?? '—'}</td>
      <td className="text-xs text-dim">{t.exchange ?? '—'}</td>
      <td>
        <div className="flex gap-xs">
          {t.index_membership.map((idx) => (
            <span key={idx} className="badge badge-neutral" style={{ fontSize: '0.6rem' }}>{idx}</span>
          ))}
        </div>
      </td>
      <td className="text-right mono">{formatUsd(t.last_price)}</td>
      <td
        className="text-right mono text-sm"
        style={{
          color: change == null ? undefined : change > 0 ? 'var(--success)' : change < 0 ? 'var(--error)' : undefined,
        }}
      >
        {formatSignedPct(change)}
      </td>
      <td className="text-xs text-dim">{t.last_price_date ?? '—'}</td>
    </tr>
  );
}
