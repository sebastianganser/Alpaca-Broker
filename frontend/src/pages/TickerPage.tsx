import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  Area, ComposedChart, Line,
} from 'recharts';
import {
  fetchTickerDetail, fetchPrices, fetchIndicators,
  fetchFundamentals, fetchTickerSignals,
} from '../api';
import { ArrowLeft, TrendingUp, TrendingDown } from 'lucide-react';

const periods = ['1m', '3m', '6m', '1y', 'all'] as const;

export default function TickerPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();
  const [period, setPeriod] = useState<string>('3m');

  const { data: ticker } = useQuery({
    queryKey: ['ticker-detail', symbol],
    queryFn: () => fetchTickerDetail(symbol!),
    enabled: !!symbol,
  });

  const { data: prices } = useQuery({
    queryKey: ['ticker-prices', symbol, period],
    queryFn: () => fetchPrices(symbol!, period),
    enabled: !!symbol,
  });

  const { data: indicators } = useQuery({
    queryKey: ['ticker-indicators', symbol, period],
    queryFn: () => fetchIndicators(symbol!, period),
    enabled: !!symbol,
  });

  const { data: fundamentals } = useQuery({
    queryKey: ['ticker-fundamentals', symbol],
    queryFn: () => fetchFundamentals(symbol!),
    enabled: !!symbol,
  });

  const { data: signals } = useQuery({
    queryKey: ['ticker-signals', symbol],
    queryFn: () => fetchTickerSignals(symbol!, 90),
    enabled: !!symbol,
  });

  if (!symbol) return null;

  // Merge prices with indicators for chart
  const chartData = prices?.map((p) => {
    const ind = indicators?.find((i) => i.trade_date === p.trade_date);
    return {
      date: p.trade_date,
      close: p.close,
      sma_50: ind?.sma_50,
      sma_200: ind?.sma_200,
      bollinger_upper: ind?.bollinger_upper,
      bollinger_lower: ind?.bollinger_lower,
    };
  }) ?? [];

  const latestIndicator = indicators?.[indicators.length - 1];

  return (
    <div className="fade-in">
      {/* Back button */}
      <button
        className="btn btn-ghost btn-sm mb-lg"
        onClick={() => navigate(-1)}
      >
        <ArrowLeft size={14} /> Zurück
      </button>

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
            {ticker?.sector && (
              <span className="badge badge-neutral">{ticker.sector}</span>
            )}
            {ticker?.exchange && (
              <span className="badge badge-neutral">{ticker.exchange}</span>
            )}
            {ticker?.index_membership?.map((idx) => (
              <span key={idx} className="badge badge-neutral">{idx}</span>
            ))}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          {ticker?.last_price !== null && ticker?.last_price !== undefined && (
            <>
              <div className="stat-value">
                ${ticker.last_price.toFixed(2)}
              </div>
              {ticker.price_change_pct !== null && ticker.price_change_pct !== undefined && (
                <div className={`stat-change ${ticker.price_change_pct >= 0 ? 'positive' : 'negative'} flex items-center gap-xs`}
                  style={{ justifyContent: 'flex-end', marginTop: '4px' }}>
                  {ticker.price_change_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {ticker.price_change_pct >= 0 ? '+' : ''}{ticker.price_change_pct.toFixed(2)}%
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Period Selector */}
      <div className="tabs" style={{ width: 'fit-content', marginBottom: 'var(--space-lg)' }}>
        {periods.map((p) => (
          <button
            key={p}
            className={`tab${period === p ? ' active' : ''}`}
            onClick={() => setPeriod(p)}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Price Chart */}
      <div className="card mb-lg" style={{ background: 'var(--surface-lowest)' }}>
        <div className="card-title">Kursverlauf</div>
        <ResponsiveContainer width="100%" height={360}>
          <ComposedChart data={chartData}>
            <defs>
              <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#28EBCF" stopOpacity={0.2} />
                <stop offset="95%" stopColor="#28EBCF" stopOpacity={0} />
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
              tickFormatter={(v) => `$${v}`}
              width={60}
            />
            <Tooltip
              contentStyle={{
                background: '#292A2B',
                border: 'none',
                borderRadius: '8px',
                fontSize: '12px',
                color: '#E3E2E3',
              }}
              labelStyle={{ color: '#84948F' }}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
              formatter={(value: any) => [`$${Number(value)?.toFixed(2)}`, '']}
            />
            {/* Bollinger Bands */}
            <Line type="monotone" dataKey="bollinger_upper" stroke="#3B4A46" strokeWidth={1} dot={false} strokeDasharray="4 4" />
            <Line type="monotone" dataKey="bollinger_lower" stroke="#3B4A46" strokeWidth={1} dot={false} strokeDasharray="4 4" />
            {/* SMA Lines */}
            <Line type="monotone" dataKey="sma_50" stroke="#FFD54F" strokeWidth={1} dot={false} opacity={0.6} />
            <Line type="monotone" dataKey="sma_200" stroke="#FF8A65" strokeWidth={1} dot={false} opacity={0.6} />
            {/* Price Area + Line */}
            <Area type="monotone" dataKey="close" fill="url(#priceGradient)" stroke="none" />
            <Line type="monotone" dataKey="close" stroke="#28EBCF" strokeWidth={2} dot={false} />
          </ComposedChart>
        </ResponsiveContainer>
        <div className="flex gap-lg mt-md" style={{ justifyContent: 'center' }}>
          <span className="text-xs flex items-center gap-xs">
            <span style={{ width: 16, height: 2, background: '#28EBCF', display: 'inline-block' }} />
            Close
          </span>
          <span className="text-xs flex items-center gap-xs text-dim">
            <span style={{ width: 16, height: 2, background: '#FFD54F', display: 'inline-block' }} />
            SMA 50
          </span>
          <span className="text-xs flex items-center gap-xs text-dim">
            <span style={{ width: 16, height: 2, background: '#FF8A65', display: 'inline-block' }} />
            SMA 200
          </span>
          <span className="text-xs flex items-center gap-xs text-dim">
            <span style={{ width: 16, height: 2, background: '#3B4A46', display: 'inline-block', borderTop: '1px dashed #3B4A46' }} />
            Bollinger
          </span>
        </div>
      </div>

      {/* Indicators + Fundamentals */}
      <div className="grid grid-2 mb-lg">
        {/* Technical Indicators */}
        <div className="card">
          <div className="card-title">Technische Indikatoren</div>
          {latestIndicator ? (
            <div className="grid grid-2 gap-md">
              <IndicatorCard
                label="RSI (14)"
                value={latestIndicator.rsi_14?.toFixed(1) ?? '—'}
                status={
                  latestIndicator.rsi_14
                    ? latestIndicator.rsi_14 > 70 ? 'error' : latestIndicator.rsi_14 < 30 ? 'success' : 'neutral'
                    : 'neutral'
                }
              />
              <IndicatorCard
                label="MACD"
                value={latestIndicator.macd?.toFixed(3) ?? '—'}
                status={
                  latestIndicator.macd
                    ? latestIndicator.macd > 0 ? 'success' : 'error'
                    : 'neutral'
                }
              />
              <IndicatorCard label="ATR (14)" value={latestIndicator.atr_14?.toFixed(2) ?? '—'} />
              <IndicatorCard label="SMA 200" value={latestIndicator.sma_200 ? `$${latestIndicator.sma_200.toFixed(2)}` : '—'} />
            </div>
          ) : (
            <div className="text-dim text-sm">Keine Indikatordaten</div>
          )}
        </div>

        {/* Fundamentals */}
        <div className="card">
          <div className="card-title">Fundamentals</div>
          {fundamentals ? (
            <div className="grid grid-2 gap-md">
              <MetricCard label="P/E Ratio" value={fundamentals.pe_ratio?.toFixed(1) ?? '—'} />
              <MetricCard label="Forward P/E" value={fundamentals.forward_pe?.toFixed(1) ?? '—'} />
              <MetricCard label="Market Cap" value={fundamentals.market_cap ? `$${(fundamentals.market_cap / 1e9).toFixed(1)}B` : '—'} />
              <MetricCard label="Revenue Growth" value={fundamentals.revenue_growth_yoy ? `${(fundamentals.revenue_growth_yoy * 100).toFixed(1)}%` : '—'} />
              <MetricCard label="Profit Margin" value={fundamentals.profit_margin ? `${(fundamentals.profit_margin * 100).toFixed(1)}%` : '—'} />
              <MetricCard label="EPS (TTM)" value={fundamentals.eps_ttm ? `$${fundamentals.eps_ttm.toFixed(2)}` : '—'} />
              <MetricCard label="Div. Yield" value={fundamentals.dividend_yield ? `${(fundamentals.dividend_yield * 100).toFixed(2)}%` : '—'} />
              <MetricCard label="Beta" value={fundamentals.beta?.toFixed(2) ?? '—'} />
            </div>
          ) : (
            <div className="text-dim text-sm">Keine Fundamentaldaten</div>
          )}
        </div>
      </div>

      {/* Signal Summary */}
      {signals && (
        <div className="card">
          <div className="card-title">Signale (letzte 90 Tage)</div>
          <div className="grid grid-4">
            <div>
              <div className="label-dim">ARK Deltas</div>
              <div className="stat-value" style={{ fontSize: '1.5rem' }}>
                {signals.ark_deltas.length}
              </div>
            </div>
            <div>
              <div className="label-dim">Insider Cluster</div>
              <div className="stat-value" style={{ fontSize: '1.5rem' }}>
                {signals.insider_clusters.length}
              </div>
            </div>
            <div>
              <div className="label-dim">Politiker Trades</div>
              <div className="stat-value" style={{ fontSize: '1.5rem' }}>
                {signals.politician_trades.length}
              </div>
            </div>
            <div>
              <div className="label-dim">Analyst Ratings</div>
              <div className="stat-value" style={{ fontSize: '1.5rem' }}>
                {signals.analyst_ratings.length}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function IndicatorCard({ label, value, status = 'neutral' }: {
  label: string; value: string; status?: string;
}) {
  const color = status === 'success' ? 'var(--primary)' :
    status === 'error' ? 'var(--error)' : 'var(--on-surface)';
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
