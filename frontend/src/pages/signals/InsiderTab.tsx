import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { InsiderCluster } from '../../api';
import { insiderQuery } from '../../queries';
import { formatUsdCompact } from '../../format';
import { DataTable, type Column } from '../../components/DataTable';
import { QueryState, TruncationNotice } from '../../components/QueryState';
import { DaysSelector, TickerCell, Toolbar, type TabProps } from './shared';

const DAYS = [30, 60, 90, 180, 365];

const COLUMNS: Column<InsiderCluster>[] = [
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (c) => <TickerCell ticker={c.ticker} />,
  },
  {
    key: 'cluster_score', label: 'Score', sortable: true,
    render: (c) => <span className="badge badge-success">{c.cluster_score?.toFixed(1) ?? '—'}</span>,
  },
  { key: 'n_insiders', label: 'Insider', align: 'right', sortable: true, className: 'mono text-sm' },
  {
    key: 'n_buys', label: 'Käufe', align: 'right', sortable: true, className: 'mono text-sm',
    style: { color: 'var(--success)' },
  },
  {
    key: 'n_sells', label: 'Verkäufe', align: 'right', sortable: true, className: 'mono text-sm',
    style: { color: 'var(--error)' },
  },
  {
    key: 'total_buy_value', label: 'Kaufvolumen', align: 'right', sortable: true, className: 'mono text-sm',
    render: (c) => formatUsdCompact(c.total_buy_value),
  },
  {
    key: 'cluster_end', label: 'Zeitraum', sortable: true, className: 'text-xs text-dim',
    render: (c) => `${c.cluster_start} → ${c.cluster_end}`,
  },
];

export default function InsiderTab({ ticker, clearTicker }: TabProps) {
  const [days, setDays] = useState(60);
  const navigate = useNavigate();
  const query = useQuery(insiderQuery({ days, ticker }));

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
              rowKey={(c, i) => `${c.ticker}-${c.cluster_start}-${c.cluster_end}-${i}`}
              onRowClick={(c) => navigate(`/ticker/${encodeURIComponent(c.ticker)}`)}
              emptyText="Keine Insider-Cluster im Zeitraum"
              externalFilterActive={!!ticker}
              onResetFilters={clearTicker}
            />
          </>
        )}
      </QueryState>
    </div>
  );
}
