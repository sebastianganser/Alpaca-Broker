import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { Layers } from 'lucide-react';
import type { SentimentArticle, SentimentSort, SentimentSummary } from '../../api';
import { sentimentArticlesQuery, sentimentSummaryQuery } from '../../queries';
import { formatShortDate } from '../../format';
import { DataTable, type Column } from '../../components/DataTable';
import { QueryState, TruncationNotice } from '../../components/QueryState';
import {
  DaysSelector,
  SegmentToggle,
  SentimentBadge,
  TickerCell,
  Toolbar,
  type TabProps,
} from './shared';
import { sentimentColor } from './utils';

type SentimentView = 'summary' | 'articles';

const DAYS = [7, 14, 30, 90];

const SORT_OPTIONS: { value: SentimentSort; label: string }[] = [
  { value: 'most_negative', label: 'Negativste zuerst' },
  { value: 'most_positive', label: 'Positivste zuerst' },
  { value: 'most_articles', label: 'Meiste Artikel' },
];

const ellipsis = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
} as const;

function negPctColor(pct: number): string {
  if (pct > 50) return 'var(--error)';
  if (pct > 30) return 'var(--warning)';
  return 'var(--on-surface-dim)';
}

const SUMMARY_COLUMNS: Column<SentimentSummary>[] = [
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    render: (s) => <TickerCell ticker={s.ticker} />,
  },
  {
    key: 'avg_sentiment', label: 'Ø Sentiment', align: 'right', sortable: true, className: 'mono',
    render: (s) => (
      <span style={{ fontWeight: 700, color: sentimentColor(s.avg_sentiment) }}>
        {s.avg_sentiment != null ? `${s.avg_sentiment > 0 ? '+' : ''}${s.avg_sentiment.toFixed(3)}` : '—'}
      </span>
    ),
  },
  { key: 'article_count', label: 'Artikel', align: 'right', sortable: true, className: 'mono text-sm' },
  {
    key: 'positive_count', label: 'Positiv', align: 'center', sortable: true, className: 'mono text-sm',
    style: { color: 'var(--success)' },
  },
  {
    key: 'negative_count', label: 'Negativ', align: 'center', sortable: true, className: 'mono text-sm',
    style: { color: 'var(--error)' },
  },
  { key: 'neutral_count', label: 'Neutral', align: 'center', sortable: true, className: 'mono text-sm text-dim' },
  {
    key: 'neg_pct', label: 'Neg. %', align: 'right', sortable: true, className: 'mono text-sm',
    render: (s) => (
      <span style={{ color: negPctColor(s.neg_pct), fontWeight: s.neg_pct > 50 ? 600 : 400 }}>
        {s.neg_pct.toFixed(0)}%
      </span>
    ),
  },
  {
    key: 'latest_headline', label: 'Letzte Headline', style: { maxWidth: 300 },
    render: (s) => (
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <SentimentBadge label={s.latest_sentiment_label} />
        <span className="text-xs text-dim" style={ellipsis} title={s.latest_headline ?? undefined}>
          {s.latest_headline ?? '—'}
        </span>
      </div>
    ),
  },
];

const ARTICLE_COLUMNS: Column<SentimentArticle>[] = [
  {
    key: 'published_at', label: 'Datum', sortable: true, className: 'text-xs text-dim',
    style: { whiteSpace: 'nowrap' },
    render: (a) => formatShortDate(a.published_at),
  },
  {
    key: 'ticker', label: 'Ticker', filterable: true, sortable: true,
    value: (a) => a.ticker ?? 'GLOBAL',
    render: (a) =>
      a.ticker ? (
        <Link
          to={`/ticker/${encodeURIComponent(a.ticker)}`}
          className="mono"
          style={{ fontWeight: 600, color: 'var(--primary)' }}
        >
          {a.ticker}
        </Link>
      ) : (
        <span className="mono text-dim">GLOBAL</span>
      ),
  },
  { key: 'source', label: 'Quelle', filterable: true, sortable: true, className: 'text-xs text-dim' },
  {
    key: 'sentiment_label', label: 'Sentiment', filterable: 'exact', sortable: true,
    render: (a) => <SentimentBadge label={a.sentiment_label} />,
  },
  {
    key: 'sentiment_score', label: 'Score', align: 'right', sortable: true, className: 'mono text-sm',
    render: (a) => (
      <span style={{ fontWeight: 600, color: sentimentColor(a.sentiment_score) }}>
        {a.sentiment_score != null ? a.sentiment_score.toFixed(3) : '—'}
      </span>
    ),
  },
  {
    key: 'headline', label: 'Headline', filterable: true, style: { maxWidth: 400 },
    render: (a) =>
      a.url ? (
        <a
          href={a.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs"
          title={a.headline}
          style={{ ...ellipsis, color: 'var(--on-surface)', textDecoration: 'none', display: 'block' }}
        >
          {a.headline}
        </a>
      ) : (
        <span className="text-xs" title={a.headline} style={{ ...ellipsis, display: 'block' }}>
          {a.headline}
        </span>
      ),
  },
];

export default function SentimentTab({ ticker, clearTicker }: TabProps) {
  const [view, setView] = useState<SentimentView>('summary');
  const [days, setDays] = useState(7);
  const [sort, setSort] = useState<SentimentSort>('most_negative');

  return (
    <div>
      <Toolbar>
        <SegmentToggle<SentimentView>
          value={view}
          onChange={setView}
          options={[
            { value: 'summary', label: <><Layers size={12} /> Zusammenfassung</> },
            { value: 'articles', label: 'Artikel' },
          ]}
        />
        <DaysSelector options={DAYS} value={days} onChange={setDays} />
        {view === 'summary' && (
          <label className="flex items-center gap-xs text-xs text-dim">
            Sortierung:
            <select
              className="input"
              value={sort}
              onChange={(e) => setSort(e.target.value as SentimentSort)}
              style={{ fontSize: '0.72rem', padding: '4px 8px' }}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
        )}
      </Toolbar>

      {view === 'summary' ? (
        <SentimentSummaryView days={days} sort={sort} ticker={ticker} clearTicker={clearTicker} />
      ) : (
        <SentimentArticlesView days={days} ticker={ticker} clearTicker={clearTicker} />
      )}
    </div>
  );
}

function SentimentSummaryView({
  days,
  sort,
  ticker,
  clearTicker,
}: TabProps & { days: number; sort: SentimentSort }) {
  const navigate = useNavigate();
  const query = useQuery(sentimentSummaryQuery({ days, ticker }, sort));
  return (
    <QueryState query={query} loadingText="Lade Sentiment…">
      {({ items, total }) => (
        <>
          <TruncationNotice shown={items.length} total={total} />
          <DataTable
            rows={items}
            columns={SUMMARY_COLUMNS}
            rowKey={(s) => s.ticker}
            onRowClick={(s) => navigate(`/ticker/${encodeURIComponent(s.ticker)}`)}
            emptyText="Keine Sentiment-Daten im Zeitraum – der News Collector läuft täglich."
            externalFilterActive={!!ticker}
            onResetFilters={clearTicker}
          />
        </>
      )}
    </QueryState>
  );
}

function SentimentArticlesView({ days, ticker, clearTicker }: TabProps & { days: number }) {
  const query = useQuery(sentimentArticlesQuery({ days, ticker }));
  return (
    <QueryState query={query} loadingText="Lade Artikel…">
      {({ items, total }) => (
        <>
          <TruncationNotice shown={items.length} total={total} />
          <DataTable
            rows={items}
            columns={ARTICLE_COLUMNS}
            rowKey={(a, i) => `${a.article_id}-${a.ticker ?? 'global'}-${i}`}
            emptyText="Keine Artikel im Zeitraum"
            externalFilterActive={!!ticker}
            onResetFilters={clearTicker}
          />
        </>
      )}
    </QueryState>
  );
}
