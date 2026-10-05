import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import type { AnalystRating } from '../../api';
import { ratingsQuery } from '../../queries';
import { formatUsd } from '../../format';
import { DataTable, type Column } from '../../components/DataTable';
import { QueryState, TruncationNotice } from '../../components/QueryState';
import { DaysSelector, TickerCell, Toolbar, type TabProps } from './shared';

const DAYS = [7, 14, 30, 90, 180];

function actionBadgeClass(action: string | null): string {
  const a = action?.toLowerCase() ?? '';
  if (a.includes('upgrade')) return 'badge-success';
  if (a.includes('downgrade')) return 'badge-error';
  return 'badge-neutral';
}

const COLUMNS: Column<AnalystRating>[] = [
  { key: 'rating_date', label: 'Datum', sortable: true, className: 'text-xs text-dim' },
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (r) => <TickerCell ticker={r.ticker} />,
  },
  { key: 'firm', label: 'Analyst', filterable: true, sortable: true, className: 'text-sm' },
  {
    key: 'action', label: 'Aktion', filterable: true, sortable: true,
    render: (r) => <span className={`badge ${actionBadgeClass(r.action)}`}>{r.action ?? '—'}</span>,
  },
  {
    key: 'rating_new', label: 'Alt → Neu', className: 'text-sm',
    render: (r) => (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-xs)' }}>
        <span className="text-dim">{r.rating_old ?? '—'}</span>
        <ArrowRight size={12} style={{ color: 'var(--on-surface-dim)' }} />
        <span>{r.rating_new ?? '—'}</span>
      </span>
    ),
  },
  {
    key: 'firm_target_new', label: 'Kursziel Firma', align: 'right', sortable: true, className: 'mono text-sm',
    render: (r) => {
      if (r.firm_target_new == null) return '—';
      if (r.firm_target_old != null && r.firm_target_old !== r.firm_target_new) {
        return (
          <span title="Vorheriges → neues Kursziel dieser Firma">
            <span className="text-dim">{formatUsd(r.firm_target_old, 0)}</span> → {formatUsd(r.firm_target_new, 0)}
          </span>
        );
      }
      return formatUsd(r.firm_target_new, 0);
    },
  },
  {
    key: 'consensus_target', label: 'Konsens-Ziel', align: 'right', sortable: true,
    className: 'mono text-sm text-dim',
    render: (r) => (
      <span title="Median-Kursziel aller Analysten (letzter Fundamentals-Snapshot)">
        {formatUsd(r.consensus_target, 0)}
      </span>
    ),
  },
];

export default function RatingsTab({ ticker, clearTicker }: TabProps) {
  const [days, setDays] = useState(14);
  const navigate = useNavigate();
  const query = useQuery(ratingsQuery({ days, ticker }));

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
              rowKey={(r, i) => `${r.ticker}-${r.firm}-${r.rating_date}-${i}`}
              onRowClick={(r) => navigate(`/ticker/${encodeURIComponent(r.ticker)}`)}
              emptyText="Keine Analysten-Ratings im Zeitraum"
              externalFilterActive={!!ticker}
              onResetFilters={clearTicker}
            />
          </>
        )}
      </QueryState>
    </div>
  );
}
