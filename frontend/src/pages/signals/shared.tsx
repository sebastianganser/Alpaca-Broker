import type { ReactNode } from 'react';
import { TrendingDown, TrendingUp } from 'lucide-react';
import type { ArkDirection, DeltaType } from '../../api';

/** Props shared by all signal tabs. */
export interface TabProps {
  /** Exact ticker filter from the URL (?ticker=), applied server-side. */
  ticker: string | null;
  /** Removes the URL ticker filter. */
  clearTicker: () => void;
}

export function DaysSelector({
  options,
  value,
  onChange,
  label = 'Zeitraum',
}: {
  options: number[];
  value: number;
  onChange: (days: number) => void;
  label?: string;
}) {
  return (
    <div className="flex gap-xs items-center" role="group" aria-label={label}>
      <span className="text-xs text-dim" style={{ marginRight: 4 }}>{label}:</span>
      {options.map((d) => (
        <button
          key={d}
          className={`btn btn-sm ${value === d ? 'btn-secondary' : 'btn-ghost'}`}
          onClick={() => onChange(d)}
          aria-pressed={value === d}
          style={{ fontSize: '0.7rem', minWidth: 42 }}
        >
          {d}T
        </button>
      ))}
    </div>
  );
}

export function SegmentToggle<V extends string>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: { value: V; label: ReactNode }[];
  value: V;
  onChange: (v: V) => void;
  disabled?: boolean;
}) {
  return (
    <div className="segment-toggle" role="group">
      {options.map((o) => (
        <button
          key={o.value}
          className={`btn btn-sm ${value === o.value ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => onChange(o.value)}
          aria-pressed={value === o.value}
          disabled={disabled}
          style={{ fontSize: '0.72rem' }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-md mb-lg" style={{ flexWrap: 'wrap' }}>
      {children}
    </div>
  );
}

export function TickerCell({ ticker }: { ticker: string | null }) {
  return (
    <span className="mono" style={{ fontWeight: 600, color: 'var(--primary)' }}>
      {ticker ?? '—'}
    </span>
  );
}

export function DeltaTypeBadge({ type }: { type: DeltaType | string }) {
  switch (type) {
    case 'new_position':
      return <span className="badge badge-success">NEU</span>;
    case 'closed':
      return <span className="badge badge-error">GESCHLOSSEN</span>;
    case 'increased':
      return <span className="badge badge-success"><TrendingUp size={10} /> ERHÖHT</span>;
    case 'decreased':
      return <span className="badge badge-warning"><TrendingDown size={10} /> REDUZIERT</span>;
    default:
      // Unknown / future delta types: neutral, never mislabelled
      return <span className="badge badge-neutral">{String(type).toUpperCase()}</span>;
  }
}

export function DirectionBadge({ direction }: { direction: ArkDirection }) {
  if (direction === 'increased') {
    return <span className="badge badge-success"><TrendingUp size={10} /> AUFGESTOCKT</span>;
  }
  if (direction === 'decreased') {
    return <span className="badge badge-warning"><TrendingDown size={10} /> REDUZIERT</span>;
  }
  return <span className="badge badge-neutral">GEMISCHT</span>;
}

export function SentimentBadge({ label }: { label: string | null }) {
  if (!label) return <span className="badge badge-neutral">—</span>;
  const cls = label === 'positive' ? 'badge-success'
    : label === 'negative' ? 'badge-error'
    : 'badge-neutral';
  const text = label === 'positive' ? 'POSITIV'
    : label === 'negative' ? 'NEGATIV'
    : 'NEUTRAL';
  return <span className={`badge ${cls}`}>{text}</span>;
}
