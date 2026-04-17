import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  fetchArkDeltas,
  fetchArkSummary,
  fetchInsiderClusters,
  fetchPoliticianTrades,
  fetchAnalystRatings,
} from '../api';
import { TrendingUp, TrendingDown, ArrowRight, Layers } from 'lucide-react';

type Tab = 'ark' | 'insider' | 'politicians' | 'ratings';
type ArkView = 'summary' | 'daily';

export default function SignalsPage() {
  const [activeTab, setActiveTab] = useState<Tab>('ark');
  const navigate = useNavigate();

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
            onClick={() => setActiveTab(tab)}
          >
            {tab === 'ark' ? 'ARK' :
             tab === 'insider' ? 'Insider' :
             tab === 'politicians' ? 'Politiker' : 'Analyst'}
          </button>
        ))}
      </div>

      {activeTab === 'ark' && <ArkTab navigate={navigate} />}
      {activeTab === 'insider' && <InsiderTab navigate={navigate} />}
      {activeTab === 'politicians' && <PoliticianTab navigate={navigate} />}
      {activeTab === 'ratings' && <RatingsTab navigate={navigate} />}
    </div>
  );
}

function ArkTab({ navigate }: { navigate: (path: string) => void }) {
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
        ? <ArkSummaryView days={summaryDays} navigate={navigate} />
        : <ArkDailyView navigate={navigate} />}
    </div>
  );
}

function ArkSummaryView({ days, navigate }: { days: number; navigate: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['signals-ark-summary', days],
    queryFn: () => fetchArkSummary(days),
  });

  if (isLoading) return <Loading />;

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Richtung</th>
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
                  <span className="badge badge-success">
                    <TrendingUp size={10} /> AUFGESTOCKT
                  </span>
                ) : s.direction === 'decreased' ? (
                  <span className="badge badge-warning">
                    <TrendingDown size={10} /> REDUZIERT
                  </span>
                ) : (
                  <span className="badge badge-neutral">GEMISCHT</span>
                )}
              </td>
              <td>
                <div className="flex gap-xs items-center" style={{ flexWrap: 'wrap' }}>
                  {s.etfs.map((etf) => (
                    <span
                      key={etf}
                      className="mono"
                      style={{
                        fontSize: '0.65rem',
                        padding: '1px 5px',
                        borderRadius: '4px',
                        background: 'rgba(255,255,255,0.06)',
                        color: 'var(--text-dim)',
                      }}
                    >
                      {etf}
                    </span>
                  ))}
                  {s.n_etfs >= 2 && (
                    <span
                      style={{
                        fontSize: '0.6rem',
                        padding: '1px 6px',
                        borderRadius: '10px',
                        background: 'rgba(56,189,248,0.15)',
                        color: 'var(--primary)',
                        fontWeight: 600,
                      }}
                    >
                      Cross-ETF
                    </span>
                  )}
                </div>
              </td>
              <td className="text-right mono text-sm">
                {s.total_shares_delta?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '—'}
              </td>
              <td className="text-right mono text-sm" style={{
                color: s.total_weight_delta_bps > 0 ? 'var(--success)' : s.total_weight_delta_bps < 0 ? 'var(--warning)' : 'var(--text-dim)',
                fontWeight: 600,
              }}>
                {s.total_weight_delta_bps > 0 ? '+' : ''}{s.total_weight_delta_bps.toFixed(1)}
              </td>
              <td className="text-right mono text-sm text-dim">
                {s.n_days}
              </td>
              <td className="text-xs text-dim">
                {s.first_date === s.last_date ? s.first_date : `${s.first_date} → ${s.last_date}`}
              </td>
            </tr>
          ))}
          {data?.length === 0 && (
            <tr><td colSpan={7} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten im Zeitraum</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ArkDailyView({ navigate }: { navigate: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['signals-ark'],
    queryFn: () => fetchArkDeltas(14),
  });

  if (isLoading) return <Loading />;

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Datum</th>
            <th>ETF</th>
            <th>Ticker</th>
            <th>Typ</th>
            <th className="text-right">Shares Δ</th>
            <th className="text-right">Weight Δ (bps)</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((d, i) => (
            <tr key={i} onClick={() => d.ticker && navigate(`/ticker/${d.ticker}`)}>
              <td className="text-xs text-dim">{d.delta_date}</td>
              <td className="mono text-xs">{d.etf_ticker}</td>
              <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
                {d.ticker}
              </td>
              <td>
                {d.delta_type === 'new_position' ? (
                  <span className="badge badge-success">NEU</span>
                ) : d.delta_type === 'closed' ? (
                  <span className="badge badge-error">CLOSED</span>
                ) : d.delta_type === 'increased' ? (
                  <span className="badge badge-success">
                    <TrendingUp size={10} /> ERHÖHT
                  </span>
                ) : (
                  <span className="badge badge-warning">
                    <TrendingDown size={10} /> REDUZIERT
                  </span>
                )}
              </td>
              <td className="text-right mono text-sm">
                {d.shares_delta?.toLocaleString() ?? '—'}
              </td>
              <td className="text-right mono text-sm">
                {d.weight_delta != null ? (d.weight_delta * 100).toFixed(1) : '—'}
              </td>
            </tr>
          ))}
          {data?.length === 0 && (
            <tr><td colSpan={6} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function InsiderTab({ navigate }: { navigate: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['signals-insider'],
    queryFn: () => fetchInsiderClusters(60),
  });

  if (isLoading) return <Loading />;

  return (
    <div className="grid grid-3">
      {data?.map((c, i) => (
        <div
          key={i}
          className="card"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate(`/ticker/${c.ticker}`)}
        >
          <div className="flex items-center justify-between mb-md">
            <span className="mono" style={{ fontWeight: 700, color: 'var(--primary)', fontSize: '1.1rem' }}>
              {c.ticker}
            </span>
            <span className="badge badge-success">
              Score: {c.cluster_score?.toFixed(1) ?? '—'}
            </span>
          </div>
          <div className="flex gap-lg mb-md">
            <div>
              <div className="label-dim">Insider</div>
              <div className="stat-value" style={{ fontSize: '1.25rem' }}>{c.n_insiders}</div>
            </div>
            <div>
              <div className="label-dim">Käufe</div>
              <div className="stat-value primary" style={{ fontSize: '1.25rem' }}>{c.n_buys}</div>
            </div>
            <div>
              <div className="label-dim">Verkäufe</div>
              <div className="stat-value" style={{ fontSize: '1.25rem', color: 'var(--error)' }}>{c.n_sells}</div>
            </div>
          </div>
          <div className="text-xs text-dim">
            {c.cluster_start} → {c.cluster_end}
          </div>
          {c.total_buy_value && (
            <div className="text-xs text-variant mt-md">
              Kaufvolumen: ${(c.total_buy_value / 1_000_000).toFixed(2)}M
            </div>
          )}
        </div>
      )) ?? null}
      {data?.length === 0 && (
        <div className="card text-dim">Keine aktiven Cluster</div>
      )}
    </div>
  );
}

function PoliticianTab({ navigate }: { navigate: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['signals-politicians'],
    queryFn: () => fetchPoliticianTrades(60),
  });

  if (isLoading) return <Loading />;

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Offenlegung</th>
            <th>Politiker</th>
            <th>Partei</th>
            <th>Ticker</th>
            <th>Typ</th>
            <th>Betrag</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((t, i) => (
            <tr key={i} onClick={() => t.ticker && navigate(`/ticker/${t.ticker}`)}>
              <td className="text-xs text-dim">{t.disclosure_date ?? '—'}</td>
              <td style={{ fontWeight: 500 }}>{t.politician_name}</td>
              <td>
                <span className={`badge ${t.party === 'Democrat' ? 'badge-neutral' : 'badge-neutral'}`}
                  style={{ fontSize: '0.6rem' }}>
                  {t.party ?? '—'}
                </span>
              </td>
              <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
                {t.ticker ?? '—'}
              </td>
              <td>
                <span className={`badge ${t.transaction_type?.toLowerCase().includes('purchase') ? 'badge-success' : 'badge-error'}`}>
                  {t.transaction_type ?? '—'}
                </span>
              </td>
              <td className="text-xs text-variant">{t.amount_range ?? '—'}</td>
            </tr>
          ))}
          {data?.length === 0 && (
            <tr><td colSpan={6} className="text-dim" style={{ textAlign: 'center' }}>Keine Daten</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function RatingsTab({ navigate }: { navigate: (path: string) => void }) {
  const { data, isLoading } = useQuery({
    queryKey: ['signals-ratings'],
    queryFn: () => fetchAnalystRatings(14),
  });

  if (isLoading) return <Loading />;

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Datum</th>
            <th>Ticker</th>
            <th>Analyst</th>
            <th>Aktion</th>
            <th>Alt → Neu</th>
            <th className="text-right">Kursziel</th>
          </tr>
        </thead>
        <tbody>
          {data?.map((r, i) => (
            <tr key={i} onClick={() => navigate(`/ticker/${r.ticker}`)}>
              <td className="text-xs text-dim">{r.rating_date ?? '—'}</td>
              <td className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
                {r.ticker}
              </td>
              <td className="text-sm">{r.firm ?? '—'}</td>
              <td>
                <span className={`badge ${
                  r.action?.toLowerCase().includes('upgrade') ? 'badge-success' :
                  r.action?.toLowerCase().includes('downgrade') ? 'badge-error' :
                  'badge-neutral'
                }`}>
                  {r.action ?? '—'}
                </span>
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
  );
}

function Loading() {
  return (
    <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)' }}>
      Lade Signale...
    </div>
  );
}
