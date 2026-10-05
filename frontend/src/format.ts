/** Shared display formatters (German locale). `null`/`undefined` → '—'. */

const DASH = '—';

export function formatNumber(n: number | null | undefined): string {
  if (n == null) return DASH;
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString('de-DE');
}

export function formatPct(n: number | null | undefined, digits = 1): string {
  return n == null ? DASH : `${n.toFixed(digits)}%`;
}

/** Ratio (0.123) → "12.3%". */
export function formatRatioPct(v: number | null | undefined, digits = 1): string {
  return v == null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

export function formatSignedPct(n: number | null | undefined, digits = 2): string {
  if (n == null) return DASH;
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

export function formatUsd(v: number | null | undefined, digits = 2): string {
  return v == null ? DASH : `$${v.toFixed(digits)}`;
}

export function formatUsdCompact(v: number | null | undefined): string {
  if (v == null) return DASH;
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

export function formatFixed(v: number | null | undefined, digits = 2): string {
  return v == null ? DASH : v.toFixed(digits);
}

export function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return DASH;
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'gerade eben';
  if (mins < 60) return `vor ${mins} Min.`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.floor(hours / 24);
  return `vor ${days} Tag${days > 1 ? 'en' : ''}`;
}

export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null) return DASH;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return DASH;
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return '';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return mins > 0 ? `~${mins}m ${secs}s verbleibend` : `~${secs}s verbleibend`;
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatShortDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString('de-DE', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleDateString('de-DE', {
    day: '2-digit', month: '2-digit', year: '2-digit',
  });
}

/** Whole days between an ISO date (YYYY-MM-DD) and today (local). */
export function daysSince(isoDate: string | null | undefined): number | null {
  if (!isoDate) return null;
  const then = new Date(`${isoDate.slice(0, 10)}T00:00:00`);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((now.getTime() - then.getTime()) / 86_400_000);
}
