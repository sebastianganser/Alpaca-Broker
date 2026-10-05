import { useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Area,
  ComposedChart,
  Line,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ArrowLeft, TrendingDown, TrendingUp } from 'lucide-react';
import {
  PERIOD_DAYS,
  isApiError,
  type FundamentalsData,
  type IndicatorPoint,
  type Period,
  type TickerDataQuality,
  type TickerSignalCounts,
} from '../api';
import {
  tickerDataQualityQuery,
  tickerDetailQuery,
  tickerFundamentalsQuery,
  tickerIndicatorsQuery,
  tickerPricesQuery,
  tickerSignalsQuery,
} from '../queries';
import {
  formatFixed,
  formatRatioPct,
  formatSignedPct,
  formatUsd,
  formatUsdCompact,
} from '../format';
import { ErrorBanner, ErrorCard, InlineError } from '../components/QueryState';
import {
  EVENT_META,
  collectEvents,
  snapEvents,
  type ChartEvent,
  type EventKind,
} from './ticker/signalEvents';

const PERIODS: { value: Period; label: string }[] = [
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
  { value: '6m', label: '6M' },
  { value: '1y', label: '1J' },
  { value: '5y', label: '5J' },
  { value: 'all', label: 'Max' },
];

/** Max. signal rows per type loaded for chart markers (counts stay uncapped). */
const SIGNAL_LIMIT = 200;
const EVENT_LIST_SIZE = 15;

interface ChartPoint {
  date: string;
  close: number | null;
  sma_50: number | null;
  sma_200: number | null;
  bollinger_upper: number | null;
  bollinger_lower: number | null;
}

const SERIES = {
  close: { label: 'Close', color: '#28EBCF' },
  sma_50: { label: 'SMA 50', color: '#FFD54F' },
  sma_200: { label: 'SMA 200', color: '#FF8A65' },
  bollinger: { label: 'Bollinger', color: '#3B4A46' },
} as const;

const tooltipStyle = {
  background: '#292A2B',
  border: 'none',
  borderRadius: '8px',
  fontSize: '12px',
  color: '#E3E2E3',
} as const;

function tooltipFormatter(value: unknown, name: unknown): [string, string] {
  const label = String(name ?? '');
  return [typeof value === 'number' ? formatUsd(value) : '—', label];
}

export default function TickerPage() {
  const { symbol: rawSymbol } = useParams<{ symbol: string }>();
  const symbol = (rawSymbol ?? '').toUpperCase();
  const navigate = useNavigate();
  const [period, setPeriod] = useState<Period>('3m');
  const [showEvents, setShowEvents] = useState(true);
  const days = PERIOD_DAYS[period];
  const enabled = symbol !== '';

  const detailQ = useQuery({ ...tickerDetailQuery(symbol), enabled });
  const pricesQ = useQuery({ ...tickerPricesQuery(symbol, period), enabled });
  const indicatorsQ = useQuery({ ...tickerIndicatorsQuery(symbol, period), enabled });
  const fundamentalsQ = useQuery({ ...tickerFundamentalsQuery(symbol), enabled });
  const signalsQ = useQuery({ ...tickerSignalsQuery(symbol, days, SIGNAL_LIMIT), enabled });
  const qualityQ = useQuery({ ...tickerDataQualityQuery(symbol), enabled });

  const prices = pricesQ.data;
  const indicators = indicatorsQ.data;
  const signals = signalsQ.data;

  const chartData = useMemo<ChartPoint[]>(() => {
    if (!prices) return [];
    const byDate = new Map<string, IndicatorPoint>();
    indicators?.forEach((i) => byDate.set(i.trade_date, i));
    return prices.map((p) => {
      const ind = byDate.get(p.trade_date);
      return {
        date: p.trade_date,
        close: p.close,
        sma_50: ind?.sma_50 ?? null,
        sma_200: ind?.sma_200 ?? null,
        bollinger_upper: ind?.bollinger_upper ?? null,
        bollinger_lower: ind?.bollinger_lower ?? null,
      };
    });
  }, [prices, indicators]);

  const chartEvents = useMemo<ChartEvent[]>(
    () => snapEvents(collectEvents(signals), chartData.map((p) => p.date)),
    [signals, chartData],
  );

  const markers = useMemo(() => {
    const closeByDate = new Map(chartData.map((p) => [p.date, p.close]));
    const stackIndex = new Map<string, number>();
    return chartEvents.flatMap((e) => {
      const close = closeByDate.get(e.date);
      if (close == null) return [];
      const n = stackIndex.get(e.date) ?? 0;
      stackIndex.set(e.date, n + 1);
      // Stack several event kinds on the same day slightly above each other.
      return [{ ...e, y: close * (1 + 0.012 * n) }];
    });
  }, [chartEvents, chartData]);

  const presentKinds = useMemo(
    () => (Object.keys(EVENT_META) as EventKind[]).filter((k) => chartEvents.some((e) => e.kind === k)),
    [chartEvents],
  );

  if (!enabled) return null;

  if (isApiError(detailQ.error) && detailQ.error.isNotFound) {
    return (
      <div className="fade-in">
        <button className="btn btn-ghost btn-sm mb-lg" onClick={() => navigate(-1)}>
          <ArrowLeft size={14} /> Zurück
        </button>
        <div className="card empty-state">
          <h3>Ticker „{symbol}“ nicht gefunden</h3>
          <p className="text-sm text-dim mt-sm">
            Der Ticker ist nicht im Universum enthalten. <Link to="/universe">Zum Universum →</Link>
          </p>
        </div>
      </div>
    );
  }

  const ticker = detailQ.data;
  const latestIndicator = indicators?.[indicators.length - 1];
  const markersTruncated =
    signals != null &&
    (signals.counts.ark_deltas > signals.ark_deltas.length ||
      signals.counts.insider_clusters > signals.insider_clusters.length ||
      signals.counts.politician_trades > signals.politician_trades.length ||
      signals.counts.analyst_ratings > signals.analyst_ratings.length);

  return (
    <div className="fade-in">
      <button className="btn btn-ghost btn-sm mb-lg" onClick={() => navigate(-1)}>
        <ArrowLeft size={14} /> Zurück
      </button>

      {detailQ.isError && <ErrorBanner error={detailQ.error} onRetry={detailQ.refetch} />}

      {/* Hero Header */}
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h2 style={{ fontSize: '1.75rem' }}>
            <span style={{ color: 'var(--primary)' }}>{symbol}</span>
            {ticker?.company_name && (
              <span className="text-variant" style={{ fontWeight: 400, fontSize: '1.1rem', marginLeft: '12px' }}>
                {ticker.company_name}
              </span>
            )}
          </h2>
          <div className="flex gap-md mt-md">
            {ticker?.sector && <span className="badge badge-neutral">{ticker.sector}</span>}
            {ticker?.exchange && <span className="badge badge-neutral">{ticker.exchange}</span>}
            {ticker?.index_membership.map((idx) => (
              <span key={idx} className="badge badge-neutral">{idx}</span>
            ))}
            {ticker && !ticker.is_active && <span className="badge badge-warning">inaktiv</span>}
          </div>
        </div>
        <div className="text-right">
          {ticker?.last_price != null && (
            <>
              <div className="stat-value">{formatUsd(ticker.last_price)}</div>
              {ticker.price_change_pct != null && (
                <div
                  className={`stat-change ${ticker.price_change_pct >= 0 ? 'positive' : 'negative'} flex items-center gap-xs justify-end`}
                  style={{ marginTop: '4px' }}
                  title="Veränderung zum vorherigen Handelstag"
                >
                  {ticker.price_change_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {formatSignedPct(ticker.price_change_pct)}
                </div>
              )}
              {ticker.last_price_date && (
                <div className="text-xs text-dim" style={{ marginTop: 2 }}>Stand {ticker.last_price_date}</div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Period Selector */}
      <div className="flex items-center gap-md" style={{ marginBottom: 'var(--space-lg)', flexWrap: 'wrap' }}>
        <div className="tabs" role="tablist" style={{ width: 'fit-content', marginBottom: 0 }}>
          {PERIODS.map((p) => (
            <button
              key={p.value}
              role="tab"
              aria-selected={period === p.value}
              className={`tab${period === p.value ? ' active' : ''}`}
              onClick={() => setPeriod(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-xs text-xs text-dim" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={showEvents} onChange={(e) => setShowEvents(e.target.checked)} />
          Signale im Chart
        </label>
      </div>

      {/* Price Chart */}
      <div className="card mb-lg" style={{ background: 'var(--surface-lowest)' }}>
        <div className="card-title">Kursverlauf</div>
        {pricesQ.isError && <InlineError error={pricesQ.error} />}
        {indicatorsQ.isError && <InlineError error={indicatorsQ.error} />}
        {pricesQ.isPending ? (
          <div className="loading-pulse text-dim" style={{ height: 360, padding: 'var(--space-xl)' }}>Lade Kurse…</div>
        ) : chartData.length === 0 ? (
          <div className="empty-state text-dim" style={{ height: 120 }}>Keine Kursdaten im Zeitraum</div>
        ) : (
          <ResponsiveContainer width="100%" height={360}>
            <ComposedChart data={chartData}>
              <defs>
                <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={SERIES.close.color} stopOpacity={0.2} />
                  <stop offset="95%" stopColor={SERIES.close.color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: '#84948F' }}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                domain={['auto', 'auto']}
                tick={{ fontSize: 10, fill: '#84948F' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v}`}
                width={60}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                labelStyle={{ color: '#84948F' }}
                formatter={tooltipFormatter}
              />
              <Line name="Bollinger oben" type="monotone" dataKey="bollinger_upper" stroke={SERIES.bollinger.color} strokeWidth={1} dot={false} strokeDasharray="4 4" />
              <Line name="Bollinger unten" type="monotone" dataKey="bollinger_lower" stroke={SERIES.bollinger.color} strokeWidth={1} dot={false} strokeDasharray="4 4" />
              <Line name={SERIES.sma_50.label} type="monotone" dataKey="sma_50" stroke={SERIES.sma_50.color} strokeWidth={1} dot={false} opacity={0.6} />
              <Line name={SERIES.sma_200.label} type="monotone" dataKey="sma_200" stroke={SERIES.sma_200.color} strokeWidth={1} dot={false} opacity={0.6} />
              <Area type="monotone" dataKey="close" fill="url(#priceGradient)" stroke="none" tooltipType="none" />
              <Line name={SERIES.close.label} type="monotone" dataKey="close" stroke={SERIES.close.color} strokeWidth={2} dot={false} />
              {showEvents &&
                markers.map((m) => (
                  <ReferenceDot
                    key={`${m.date}-${m.kind}`}
                    x={m.date}
                    y={m.y}
                    r={m.count > 1 ? 6 : 4.5}
                    fill={EVENT_META[m.kind].color}
                    stroke="#111"
                    strokeWidth={1}
                    ifOverflow="extendDomain"
                  />
                ))}
            </ComposedChart>
          </ResponsiveContainer>
        )}
        <div className="flex gap-lg mt-md" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
          <LegendLine color={SERIES.close.color} label={SERIES.close.label} />
          <LegendLine color={SERIES.sma_50.color} label={SERIES.sma_50.label} dim />
          <LegendLine color={SERIES.sma_200.color} label={SERIES.sma_200.label} dim />
          <LegendLine color={SERIES.bollinger.color} label={SERIES.bollinger.label} dim dashed />
          {showEvents &&
            presentKinds.map((k) => (
              <span key={k} className="text-xs flex items-center gap-xs text-dim">
                <span
                  style={{ width: 9, height: 9, borderRadius: '50%', background: EVENT_META[k].color, display: 'inline-block' }}
                />
                {EVENT_META[k].label}
              </span>
            ))}
        </div>
        {showEvents && markersTruncated && (
          <div className="text-xs text-warning mt-sm text-center">
            Nicht alle Signale als Marker dargestellt (max. {SIGNAL_LIMIT} je Typ) – Zeitraum verkürzen.
          </div>
        )}
      </div>

      {/* Indicators + Fundamentals */}
      <div className="grid grid-2 mb-lg">
        <div className="card">
          <div className="card-title">Technische Indikatoren</div>
          {latestIndicator ? <IndicatorGrid ind={latestIndicator} /> : (
            <div className="text-dim text-sm">{indicatorsQ.isPending ? 'Lade…' : 'Keine Indikatordaten'}</div>
          )}
        </div>

        <div className="card">
          <div className="card-title">
            Fundamentals
            {fundamentalsQ.data?.snapshot_date && (
              <span className="text-xs text-dim" style={{ marginLeft: 8, fontWeight: 400 }}>
                Stand {fundamentalsQ.data.snapshot_date}
              </span>
            )}
          </div>
          {fundamentalsQ.isError && <InlineError error={fundamentalsQ.error} />}
          {fundamentalsQ.data ? <FundamentalsGrid f={fundamentalsQ.data} /> : (
            <div className="text-dim text-sm">{fundamentalsQ.isPending ? 'Lade…' : 'Keine Fundamentaldaten'}</div>
          )}
        </div>
      </div>

      {qualityQ.isError && <ErrorCard error={qualityQ.error} onRetry={qualityQ.refetch} />}
      {qualityQ.data && <DataQualityCard quality={qualityQ.data} />}

      {/* Signal Summary */}
      <div className="card">
        <div className="card-title">Signale (letzte {days} Tage)</div>
        {signalsQ.isError && <InlineError error={signalsQ.error} />}
        {signals ? (
          <>
            <SignalCounts counts={signals.counts} symbol={symbol} />
            {chartEvents.length > 0 && <EventList events={chartEvents} />}
          </>
        ) : (
          signalsQ.isPending && <div className="text-dim text-sm">Lade Signale…</div>
        )}
      </div>
    </div>
  );
}

function LegendLine({ color, label, dim, dashed }: { color: string; label: string; dim?: boolean; dashed?: boolean }) {
  return (
    <span className={`text-xs flex items-center gap-xs${dim ? ' text-dim' : ''}`}>
      <span
        style={{
          width: 16,
          height: 0,
          borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}`,
          display: 'inline-block',
        }}
      />
      {label}
    </span>
  );
}

type Status = 'success' | 'error' | 'neutral';

function rsiStatus(rsi: number | null): Status {
  if (rsi == null) return 'neutral';
  if (rsi > 70) return 'error';
  if (rsi < 30) return 'success';
  return 'neutral';
}

function signStatus(v: number | null): Status {
  if (v == null || v === 0) return 'neutral';
  return v > 0 ? 'success' : 'error';
}

function IndicatorGrid({ ind }: { ind: IndicatorPoint }) {
  return (
    <div className="grid grid-2 gap-md">
      <IndicatorCard label="RSI (14)" value={formatFixed(ind.rsi_14, 1)} status={rsiStatus(ind.rsi_14)} />
      <IndicatorCard label="MACD" value={formatFixed(ind.macd, 3)} status={signStatus(ind.macd)} />
      <IndicatorCard label="ATR (14)" value={formatFixed(ind.atr_14, 2)} />
      <IndicatorCard label="SMA 200" value={formatUsd(ind.sma_200)} />
      <IndicatorCard label="Rel. Stärke vs. SPY" value={formatFixed(ind.relative_strength_spy, 3)} />
      <div className="text-xs text-dim" style={{ alignSelf: 'end' }}>Stand {ind.trade_date}</div>
    </div>
  );
}

function FundamentalsGrid({ f }: { f: FundamentalsData }) {
  return (
    <div className="grid grid-2 gap-md">
      <MetricCard label="P/E Ratio" value={formatFixed(f.pe_ratio, 1)} />
      <MetricCard label="Forward P/E" value={formatFixed(f.forward_pe, 1)} />
      <MetricCard label="Market Cap" value={formatUsdCompact(f.market_cap)} />
      <MetricCard label="EV/EBITDA" value={formatFixed(f.ev_ebitda, 1)} />
      <MetricCard label="Revenue Growth" value={formatRatioPct(f.revenue_growth_yoy)} />
      <MetricCard label="Profit Margin" value={formatRatioPct(f.profit_margin)} />
      <MetricCard label="Return on Equity" value={formatRatioPct(f.return_on_equity)} />
      <MetricCard label="Debt/Equity" value={formatFixed(f.debt_to_equity, 2)} />
      <MetricCard label="EPS (TTM)" value={formatUsd(f.eps_ttm)} />
      <MetricCard label="Div. Yield" value={formatRatioPct(f.dividend_yield, 2)} />
      <MetricCard label="Beta" value={formatFixed(f.beta, 2)} />
    </div>
  );
}

function IndicatorCard({ label, value, status = 'neutral' }: { label: string; value: string; status?: Status }) {
  const color = status === 'success' ? 'var(--primary)'
    : status === 'error' ? 'var(--error)'
      : 'var(--on-surface)';
  return (
    <div>
      <div className="label-dim">{label}</div>
      <div style={{ fontSize: '1.1rem', fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="label-dim">{label}</div>
      <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const COUNT_CARDS: { key: keyof TickerSignalCounts; label: string; tab: string }[] = [
  { key: 'ark_deltas', label: 'ARK Deltas', tab: 'ark' },
  { key: 'insider_clusters', label: 'Insider Cluster', tab: 'insider' },
  { key: 'politician_trades', label: 'Politiker Trades', tab: 'politicians' },
  { key: 'analyst_ratings', label: 'Analyst Ratings', tab: 'ratings' },
];

function SignalCounts({ counts, symbol }: { counts: TickerSignalCounts; symbol: string }) {
  const navigate = useNavigate();
  return (
    <div className="grid grid-4">
      {COUNT_CARDS.map((c) => (
        <SignalCountCard
          key={c.key}
          label={c.label}
          count={counts[c.key]}
          onClick={() => navigate(`/signals?tab=${c.tab}&ticker=${encodeURIComponent(symbol)}`)}
        />
      ))}
    </div>
  );
}

function SignalCountCard({ label, count, onClick }: { label: string; count: number; onClick: () => void }) {
  const clickable = count > 0;
  return (
    <button
      type="button"
      className="signal-count-card"
      onClick={onClick}
      disabled={!clickable}
      aria-label={`${label}: ${count}${clickable ? ' – Details anzeigen' : ''}`}
    >
      <div className="label-dim">{label}</div>
      <div className="stat-value" style={{ fontSize: '1.5rem', color: clickable ? 'var(--primary)' : undefined }}>
        {count.toLocaleString('de-DE')}
      </div>
      {clickable && (
        <div style={{ fontSize: '0.6rem', color: 'var(--on-surface-dim)', marginTop: '4px' }}>
          Details anzeigen →
        </div>
      )}
    </button>
  );
}

function EventList({ events }: { events: ChartEvent[] }) {
  const latest = [...events].reverse().slice(0, EVENT_LIST_SIZE);
  return (
    <div className="mt-md">
      <div className="label-dim mb-xs">Letzte Ereignisse im Chart</div>
      <table className="data-table">
        <tbody>
          {latest.map((e) => (
            <tr key={`${e.date}-${e.kind}`}>
              <td className="text-xs text-dim" style={{ whiteSpace: 'nowrap', width: 90 }}>{e.date}</td>
              <td style={{ whiteSpace: 'nowrap', width: 150 }}>
                <span className="text-xs flex items-center gap-xs">
                  <span
                    style={{ width: 8, height: 8, borderRadius: '50%', background: EVENT_META[e.kind].color, display: 'inline-block' }}
                  />
                  {EVENT_META[e.kind].label}{e.count > 1 ? ` ×${e.count}` : ''}
                </span>
              </td>
              <td className="text-xs">{e.texts.slice(0, 3).join(' · ')}{e.texts.length > 3 ? ' …' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DataQualityCard({ quality }: { quality: TickerDataQuality }) {
  return (
    <div className="card mb-lg">
      <div className="card-title">
        Datenqualität
        <span className="text-xs text-dim" style={{ marginLeft: 8, fontWeight: 400 }}>
          {formatRatioPct(quality.overall_completeness, 0)} vollständig
        </span>
      </div>
      <div className="grid grid-2 gap-md">
        {quality.dimensions.map((dim) => {
          const color = dim.status === 'complete' ? 'var(--primary)'
            : dim.status === 'partial' ? 'var(--warning)'
              : 'var(--error)';
          return (
            <div key={dim.label} title={dim.detail ?? undefined}>
              <div className="label-dim">{dim.label}</div>
              <div style={{ fontSize: '0.95rem', fontWeight: 600, color, marginTop: '2px' }}>{dim.summary}</div>
              {dim.detail && <div className="text-xs text-dim">{dim.detail}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
