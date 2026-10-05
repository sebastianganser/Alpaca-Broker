/**
 * Turns ticker signals into chart overlay events.
 *
 * Signal dates (e.g. insider cluster end, rating date on a weekend) don't
 * always coincide with a trading day, so each event is snapped to the next
 * available trading date of the chart. Events outside the chart range are
 * dropped.
 */

import type { TickerSignals } from '../../api';

export type EventKind =
  | 'ark_buy'
  | 'ark_sell'
  | 'insider'
  | 'upgrade'
  | 'downgrade'
  | 'pol_buy'
  | 'pol_sell';

export const EVENT_META: Record<EventKind, { label: string; color: string }> = {
  ark_buy: { label: 'ARK Kauf', color: '#4ADE80' },
  ark_sell: { label: 'ARK Verkauf', color: '#FB923C' },
  insider: { label: 'Insider-Cluster', color: '#A78BFA' },
  upgrade: { label: 'Upgrade', color: '#38BDF8' },
  downgrade: { label: 'Downgrade', color: '#F87171' },
  pol_buy: { label: 'Politiker Kauf', color: '#FACC15' },
  pol_sell: { label: 'Politiker Verkauf', color: '#E879F9' },
};

export interface RawEvent {
  date: string;
  kind: EventKind;
  text: string;
}

export interface ChartEvent {
  /** Snapped trading date (x-axis category). */
  date: string;
  kind: EventKind;
  count: number;
  texts: string[];
}

const ARK_DELTA_LABEL: Record<string, string> = {
  new_position: 'neue Position',
  increased: 'aufgestockt',
  decreased: 'reduziert',
  closed: 'geschlossen',
};

function iso(d: string | null | undefined): string | null {
  return d ? d.slice(0, 10) : null;
}

export function collectEvents(signals: TickerSignals | undefined): RawEvent[] {
  if (!signals) return [];
  const events: RawEvent[] = [];

  for (const d of signals.ark_deltas) {
    const kind: EventKind | null =
      d.delta_type === 'new_position' || d.delta_type === 'increased' ? 'ark_buy'
        : d.delta_type === 'closed' || d.delta_type === 'decreased' ? 'ark_sell'
          : null;
    if (kind) {
      events.push({ date: d.delta_date, kind, text: `${d.etf_ticker}: ${ARK_DELTA_LABEL[d.delta_type] ?? d.delta_type}` });
    }
  }

  for (const c of signals.insider_clusters) {
    events.push({
      date: c.cluster_end,
      kind: 'insider',
      text: `${c.n_insiders} Insider (${c.n_buys} Käufe / ${c.n_sells} Verkäufe)`,
    });
  }

  for (const r of signals.analyst_ratings) {
    const date = iso(r.rating_date);
    const action = r.action?.toLowerCase() ?? '';
    const kind: EventKind | null = action.includes('downgrade') ? 'downgrade'
      : action.includes('upgrade') ? 'upgrade'
        : null;
    if (date && kind) {
      events.push({ date, kind, text: `${r.firm ?? 'Analyst'}: ${r.rating_old ?? '—'} → ${r.rating_new ?? '—'}` });
    }
  }

  for (const t of signals.politician_trades) {
    const date = iso(t.transaction_date) ?? iso(t.disclosure_date);
    const type = t.transaction_type?.toLowerCase() ?? '';
    const kind: EventKind | null = type.includes('purchase') || type.includes('buy') ? 'pol_buy'
      : type.includes('sale') || type.includes('sell') ? 'pol_sell'
        : null;
    if (date && kind) {
      events.push({ date, kind, text: `${t.politician_name}${t.amount_range ? ` (${t.amount_range})` : ''}` });
    }
  }

  return events;
}

/** Index of the first trading date ≥ `date` (dates sorted ascending), or -1. */
export function snapIndex(tradingDates: string[], date: string): number {
  let lo = 0;
  let hi = tradingDates.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (tradingDates[mid] < date) lo = mid + 1;
    else hi = mid;
  }
  return lo < tradingDates.length ? lo : -1;
}

/** Snap events onto trading dates and merge same-day events of the same kind. */
export function snapEvents(events: RawEvent[], tradingDates: string[]): ChartEvent[] {
  if (tradingDates.length === 0) return [];
  const first = tradingDates[0];
  const merged = new Map<string, ChartEvent>();
  for (const e of events) {
    if (e.date < first) continue;
    const idx = snapIndex(tradingDates, e.date);
    if (idx < 0) continue;
    const date = tradingDates[idx];
    const key = `${date}|${e.kind}`;
    const existing = merged.get(key);
    if (existing) {
      existing.count += 1;
      existing.texts.push(e.text);
    } else {
      merged.set(key, { date, kind: e.kind, count: 1, texts: [e.text] });
    }
  }
  return [...merged.values()].sort((a, b) => a.date.localeCompare(b.date));
}
