import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { fetchUniverse, fetchSectors } from '../api';
import { Search, ChevronLeft, ChevronRight } from 'lucide-react';

export default function UniversePage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [sector, setSector] = useState('');
  const [activeFilter, setActiveFilter] = useState<string>('');
  const limit = 50;

  const { data: sectors } = useQuery({
    queryKey: ['sectors'],
    queryFn: fetchSectors,
  });

  const { data, isLoading } = useQuery({
    queryKey: ['universe', page, search, sector, activeFilter],
    queryFn: () =>
      fetchUniverse({
        page,
        limit,
        ...(search && { search }),
        ...(sector && { sector }),
        ...(activeFilter && { active: activeFilter }),
      }),
  });

  const totalPages = data ? Math.ceil(data.total / limit) : 0;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Universe</h2>
        <span className="badge badge-neutral">
          {data?.total ?? '...'} Ticker
        </span>
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
            placeholder="Ticker oder Name suchen..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            style={{ paddingLeft: '36px' }}
          />
        </div>
        <select
          className="input"
          value={sector}
          onChange={(e) => { setSector(e.target.value); setPage(1); }}
          style={{ width: 'auto', minWidth: '180px' }}
        >
          <option value="">Alle Sektoren</option>
          {sectors?.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          className="input"
          value={activeFilter}
          onChange={(e) => { setActiveFilter(e.target.value); setPage(1); }}
          style={{ width: 'auto', minWidth: '120px' }}
        >
          <option value="">Alle</option>
          <option value="true">Aktiv</option>
          <option value="false">Inaktiv</option>
        </select>
      </div>

      {/* Table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {isLoading ? (
          <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)', textAlign: 'center' }}>
            Lade Ticker...
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Name</th>
                <th>Sektor</th>
                <th>Exchange</th>
                <th>Index</th>
                <th className="text-right">Letzter Preis</th>
                <th>Datum</th>
              </tr>
            </thead>
            <tbody>
              {data?.tickers.map((t) => (
                <tr key={t.ticker} onClick={() => navigate(`/ticker/${t.ticker}`)}>
                  <td>
                    <span className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
                      {t.ticker}
                    </span>
                  </td>
                  <td>{t.company_name || '—'}</td>
                  <td className="text-xs text-variant">{t.sector || '—'}</td>
                  <td className="text-xs text-dim">{t.exchange || '—'}</td>
                  <td>
                    <div className="flex gap-xs">
                      {t.index_membership.map((idx) => (
                        <span key={idx} className="badge badge-neutral" style={{ fontSize: '0.6rem' }}>
                          {idx}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="text-right mono">
                    {t.last_price !== null ? `$${t.last_price.toFixed(2)}` : '—'}
                  </td>
                  <td className="text-xs text-dim">{t.last_price_date || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-lg">
          <div className="text-xs text-dim">
            Seite {page} von {totalPages}
          </div>
          <div className="flex gap-sm">
            <button
              className="btn btn-ghost btn-sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft size={14} /> Zurück
            </button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Weiter <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
