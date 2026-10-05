import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { PoliticianTrade } from '../../api';
import { politicianQuery } from '../../queries';
import { DataTable, type Column } from '../../components/DataTable';
import { QueryState, TruncationNotice } from '../../components/QueryState';
import { DaysSelector, TickerCell, Toolbar, type TabProps } from './shared';

const DAYS = [30, 60, 90, 180, 365];

function delayColor(days: number): string {
  if (days <= 7) return 'var(--success)';
  if (days <= 30) return 'var(--warning)';
  return 'var(--error)';
}

/** Purchase → green, sale → red, anything else (exchange, unknown) → neutral. */
function transactionBadgeClass(type: string | null): string {
  const t = type?.toLowerCase() ?? '';
  if (t.includes('purchase') || t.includes('buy')) return 'badge-success';
  if (t.includes('sale') || t.includes('sell')) return 'badge-error';
  return 'badge-neutral';
}

const COLUMNS: Column<PoliticianTrade>[] = [
  { key: 'disclosure_date', label: 'Offenlegung', sortable: true, className: 'text-xs text-dim' },
  { key: 'transaction_date', label: 'Trade-Datum', sortable: true, className: 'text-xs text-dim' },
  {
    key: 'delay_days', label: 'Verzög.', align: 'right', sortable: true,
    render: (t) =>
      t.delay_days != null ? (
        <span className="mono" style={{ fontSize: '0.7rem', fontWeight: 600, color: delayColor(t.delay_days) }}>
          {t.delay_days}d
        </span>
      ) : '—',
  },
  {
    key: 'politician_name', label: 'Politiker', filterable: true, sortable: true,
    style: { fontWeight: 500 },
  },
  {
    key: 'party', label: 'Partei', filterable: 'exact', sortable: true,
    render: (t) => <span className="badge badge-neutral" style={{ fontSize: '0.6rem' }}>{t.party ?? '—'}</span>,
  },
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (t) => <TickerCell ticker={t.ticker} />,
  },
  {
    key: 'transaction_type', label: 'Typ', filterable: true, sortable: true,
    render: (t) => (
      <span className={`badge ${transactionBadgeClass(t.transaction_type)}`}>{t.transaction_type ?? '—'}</span>
    ),
  },
  { key: 'amount_range', label: 'Betrag', className: 'text-xs text-variant' },
];

export default function PoliticianTab({ ticker, clearTicker }: TabProps) {
  const [days, setDays] = useState(60);
  const navigate = useNavigate();
  const query = useQuery(politicianQuery({ days, ticker }));

  return (
    <div>
      <Toolbar>
        <DaysSelector options={DAYS} value={days} onChange={setDays} />
      </Toolbar>
      <QueryState query={query} loadingText="Lade Signale…">
        {({ items, total }) => (
          <>
            <TruncationNotice shown={items.length} total={total} />
            <DataTable
              rows={items}
              columns={COLUMNS}
              rowKey={(t, i) => `${t.politician_name}-${t.ticker}-${t.transaction_date}-${i}`}
              onRowClick={(t) => {
                if (t.ticker) navigate(`/ticker/${encodeURIComponent(t.ticker)}`);
              }}
              emptyText="Keine Politiker-Trades im Zeitraum"
              externalFilterActive={!!ticker}
              onResetFilters={clearTicker}
            />
          </>
        )}
      </QueryState>
    </div>
  );
}
