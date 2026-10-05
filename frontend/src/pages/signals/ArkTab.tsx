import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Layers } from 'lucide-react';
import type { ARKDelta, ARKSummary } from '../../api';
import { arkDeltasQuery, arkSummaryQuery } from '../../queries';
import { DataTable, type Column } from '../../components/DataTable';
import { QueryState, TruncationNotice } from '../../components/QueryState';
import {
  DaysSelector,
  DeltaTypeBadge,
  DirectionBadge,
  SegmentToggle,
  TickerCell,
  Toolbar,
  type TabProps,
} from './shared';

type ArkView = 'summary' | 'daily';

const SUMMARY_DAYS = [5, 10, 20];
const DAILY_DAYS = [7, 14, 30, 90];

const DELTA_TYPE_LABEL: Record<string, string> = {
  new_position: 'neu',
  closed: 'geschlossen',
  increased: 'erhöht',
  decreased: 'reduziert',
  unchanged: 'unverändert',
};

const DIRECTION_LABEL: Record<string, string> = {
  increased: 'aufgestockt',
  decreased: 'reduziert',
  mixed: 'gemischt',
};

const etfChipStyle = {
  fontSize: '0.65rem',
  padding: '1px 5px',
  borderRadius: '4px',
  background: 'rgba(255,255,255,0.06)',
  color: 'var(--on-surface-dim)',
} as const;

const crossEtfStyle = {
  fontSize: '0.6rem',
  padding: '1px 6px',
  borderRadius: '10px',
  background: 'rgba(56,189,248,0.15)',
  color: 'var(--primary)',
  fontWeight: 600,
} as const;

function bpsColor(bps: number): string {
  if (bps > 0) return 'var(--success)';
  if (bps < 0) return 'var(--warning)';
  return 'var(--on-surface-dim)';
}

const SUMMARY_COLUMNS: Column<ARKSummary>[] = [
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (s) => <TickerCell ticker={s.ticker} />,
  },
  {
    key: 'direction', label: 'Richtung', filterable: true,
    // filter on the German label shown in the badge
    value: (s) => DIRECTION_LABEL[s.direction] ?? s.direction,
    render: (s) => <DirectionBadge direction={s.direction} />,
  },
  {
    key: 'etfs', label: 'ETFs', filterable: true,
    render: (s) => (
      <div className="flex gap-xs items-center" style={{ flexWrap: 'wrap' }}>
        {s.etfs.map((etf) => (
          <span key={etf} className="mono" style={etfChipStyle}>{etf}</span>
        ))}
        {s.n_etfs >= 2 && <span style={crossEtfStyle}>Cross-ETF</span>}
      </div>
    ),
  },
  {
    key: 'total_shares_delta', label: 'Shares Δ (gesamt)', align: 'right', sortable: true,
    className: 'mono text-sm',
    render: (s) => s.total_shares_delta.toLocaleString('de-DE', { maximumFractionDigits: 0 }),
  },
  {
    key: 'total_weight_delta_bps', label: 'Weight Δ (bps)', align: 'right', sortable: true,
    className: 'mono text-sm',
    render: (s) => (
      <span style={{ color: bpsColor(s.total_weight_delta_bps), fontWeight: 600 }}>
        {s.total_weight_delta_bps > 0 ? '+' : ''}{s.total_weight_delta_bps.toFixed(1)}
      </span>
    ),
  },
  { key: 'n_days', label: 'Tage', align: 'right', sortable: true, className: 'mono text-sm text-dim' },
  {
    key: 'last_date', label: 'Zeitraum', sortable: true, className: 'text-xs text-dim',
    render: (s) => (s.first_date === s.last_date ? s.first_date : `${s.first_date} → ${s.last_date}`),
  },
];

const DAILY_COLUMNS: Column<ARKDelta>[] = [
  { key: 'delta_date', label: 'Datum', sortable: true, className: 'text-xs text-dim' },
  { key: 'etf_ticker', label: 'ETF', filterable: 'exact', sortable: true, className: 'mono text-xs' },
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (d) => <TickerCell ticker={d.ticker} />,
  },
  {
    key: 'delta_type', label: 'Typ', filterable: true,
    value: (d) => DELTA_TYPE_LABEL[d.delta_type] ?? d.delta_type,
    render: (d) => <DeltaTypeBadge type={d.delta_type} />,
  },
  {
    key: 'shares_delta', label: 'Shares Δ', align: 'right', sortable: true, className: 'mono text-sm',
    render: (d) => d.shares_delta?.toLocaleString('de-DE') ?? '—',
  },
  {
    key: 'weight_delta', label: 'Weight Δ (bps)', align: 'right', sortable: true, className: 'mono text-sm',
    // weight_delta is in percentage points → ×100 = basis points
    value: (d) => (d.weight_delta != null ? d.weight_delta * 100 : null),
    render: (d) => (d.weight_delta != null ? (d.weight_delta * 100).toFixed(1) : '—'),
  },
];

export default function ArkTab({ ticker, clearTicker }: TabProps) {
  const [view, setView] = useState<ArkView>('summary');
  const [summaryDays, setSummaryDays] = useState(5);
  const [dailyDays, setDailyDays] = useState(14);
  const navigate = useNavigate();

  // A single ticker is best inspected day by day.
  const effectiveView: ArkView = ticker ? 'daily' : view;
  const openTicker = (t: string) => navigate(`/ticker/${encodeURIComponent(t)}`);

  return (
    <div>
      <Toolbar>
        <SegmentToggle<ArkView>
          value={effectiveView}
          onChange={setView}
          disabled={!!ticker}
          options={[
            { value: 'summary', label: <><Layers size={12} /> Zusammenfassung</> },
            { value: 'daily', label: 'Täglich' },
          ]}
        />
        {effectiveView === 'summary' ? (
          <DaysSelector options={SUMMARY_DAYS} value={summaryDays} onChange={setSummaryDays} />
        ) : (
          <DaysSelector options={DAILY_DAYS} value={dailyDays} onChange={setDailyDays} />
        )}
        {ticker && (
          <span className="text-xs text-dim">Mit Ticker-Filter wird die Tagesansicht gezeigt.</span>
        )}
      </Toolbar>

      {effectiveView === 'summary' ? (
        <ArkSummaryView days={summaryDays} onOpen={openTicker} />
      ) : (
        <ArkDailyView days={dailyDays} ticker={ticker} clearTicker={clearTicker} onOpen={openTicker} />
      )}
    </div>
  );
}

function ArkSummaryView({ days, onOpen }: { days: number; onOpen: (t: string) => void }) {
  const query = useQuery(arkSummaryQuery({ days }));
  return (
    <QueryState query={query} loadingText="Lade Signale…">
      {(rows) => (
        <DataTable
          rows={rows}
          columns={SUMMARY_COLUMNS}
          rowKey={(s) => s.ticker}
          onRowClick={(s) => onOpen(s.ticker)}
          emptyText="Keine Daten im Zeitraum"
        />
      )}
    </QueryState>
  );
}

function ArkDailyView({
  days,
  ticker,
  clearTicker,
  onOpen,
}: TabProps & { days: number; onOpen: (t: string) => void }) {
  const query = useQuery(arkDeltasQuery({ days, ticker }));
  return (
    <QueryState query={query} loadingText="Lade Signale…">
      {({ items, total }) => (
        <>
          <TruncationNotice shown={items.length} total={total} />
          <DataTable
            rows={items}
            columns={DAILY_COLUMNS}
            rowKey={(d, i) => `${d.delta_date}-${d.etf_ticker}-${d.ticker}-${i}`}
            onRowClick={(d) => onOpen(d.ticker)}
            emptyText="Keine Daten im Zeitraum"
            externalFilterActive={!!ticker}
            onResetFilters={clearTicker}
          />
        </>
      )}
    </QueryState>
  );
}
