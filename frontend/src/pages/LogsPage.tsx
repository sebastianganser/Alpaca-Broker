import { useEffect, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertCircle,
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Info,
  X,
} from 'lucide-react';
import type { CollectionLogItem, LogLine } from '../api';
import { collectorNamesQuery, logsQuery } from '../queries';
import { formatDateTime, formatDuration } from '../format';
import { QueryState } from '../components/QueryState';

const PAGE_SIZE = 30;

const STATUS_BADGE: Record<string, string> = {
  success: 'badge-success',
  partial: 'badge-warning',
  failed: 'badge-error',
};

const LOG_LEVEL_STYLE: Record<string, { color: string; icon: typeof AlertTriangle }> = {
  WARNING: { color: 'var(--warning)', icon: AlertTriangle },
  ERROR: { color: 'var(--error)', icon: AlertCircle },
  CRITICAL: { color: 'var(--error)', icon: AlertCircle },
  INFO: { color: 'var(--on-surface-dim)', icon: Info },
};

const WARN_LEVELS = new Set(['WARNING', 'ERROR', 'CRITICAL']);

function warnCount(lines: LogLine[] | null): number {
  return lines ? lines.filter((l) => WARN_LEVELS.has(l.level)).length : 0;
}

function collectorLabel(name: string | null): string {
  if (!name) return '—';
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function StatusBadge({ status }: { status: string | null }) {
  return (
    <span className={`badge ${STATUS_BADGE[status ?? ''] ?? 'badge-neutral'}`}>
      <span className="badge-dot" />
      {status ?? '—'}
    </span>
  );
}

export default function LogsPage() {
  const [page, setPage] = useState(1);
  const [collector, setCollector] = useState('');
  const [status, setStatus] = useState('');
  // Keep the clicked row as fallback, but prefer the fresh copy from polling.
  const [selected, setSelected] = useState<CollectionLogItem | null>(null);

  const collectorsQ = useQuery(collectorNamesQuery());
  const logsQ = useQuery(
    logsQuery({ page, limit: PAGE_SIZE, collector: collector || undefined, status: status || undefined }),
  );
  const data = logsQ.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 0;
  const selectedLog = selected ? (data?.logs.find((l) => l.id === selected.id) ?? selected) : null;

  const changeFilter = (setter: (v: string) => void) => (v: string) => {
    setter(v);
    setPage(1);
  };

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Logs</h2>
        <div className="text-sm text-dim">
          {data ? `${data.total.toLocaleString('de-DE')} Einträge` : '…'}
          {logsQ.isFetching && data && <span className="loading-pulse"> · aktualisiere</span>}
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-md mb-lg items-center">
        <select
          className="input"
          aria-label="Collector-Filter"
          style={{ maxWidth: 240 }}
          value={collector}
          onChange={(e) => changeFilter(setCollector)(e.target.value)}
        >
          <option value="">Alle Collector</option>
          {collectorsQ.data?.map((c) => (
            <option key={c} value={c}>{collectorLabel(c)}</option>
          ))}
        </select>

        <select
          className="input"
          aria-label="Status-Filter"
          style={{ maxWidth: 160 }}
          value={status}
          onChange={(e) => changeFilter(setStatus)(e.target.value)}
        >
          <option value="">Alle Status</option>
          <option value="success">✅ Success</option>
          <option value="partial">⚠️ Partial</option>
          <option value="failed">❌ Failed</option>
        </select>

        {(collector || status) && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setCollector('');
              setStatus('');
              setPage(1);
            }}
          >
            <X size={14} />
            Filter zurücksetzen
          </button>
        )}
      </div>

      <QueryState
        query={logsQ}
        loadingText="Lade Logs…"
        isEmpty={(d) => d.logs.length === 0}
        emptyText={<div className="card text-dim text-center">Keine Log-Einträge</div>}
      >
        {(d) => (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Zeitpunkt</th>
                  <th>Collector</th>
                  <th>Status</th>
                  <th className="text-right">Gelesen</th>
                  <th className="text-right">Geschrieben</th>
                  <th className="text-right">Gaps</th>
                  <th className="text-right">Dauer</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {d.logs.map((log) => (
                  <LogRow key={log.id} log={log} onOpen={() => setSelected(log)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-lg">
          <div className="text-xs text-dim">Seite {page} von {totalPages}</div>
          <div className="flex gap-sm">
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
            >
              <ChevronLeft size={14} />
              Zurück
            </button>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
            >
              Weiter
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}

      {selectedLog && (
        <LogDetailModal key={selectedLog.id} log={selectedLog} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function LogRow({ log, onOpen }: { log: CollectionLogItem; onOpen: () => void }) {
  const lines = log.log_lines ?? [];
  const warnings = warnCount(log.log_lines);
  return (
    <tr
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onOpen();
      }}
      tabIndex={0}
      style={{ cursor: 'pointer' }}
    >
      <td className="text-xs mono">{formatDateTime(log.started_at)}</td>
      <td style={{ fontWeight: 500 }}>{collectorLabel(log.collector_name)}</td>
      <td><StatusBadge status={log.status} /></td>
      <td className="text-right mono">{log.records_fetched?.toLocaleString('de-DE') ?? '—'}</td>
      <td className="text-right mono">{log.records_written?.toLocaleString('de-DE') ?? '—'}</td>
      <td className="text-right mono">
        {log.gaps_detected > 0 ? (
          <span className="text-warning">
            {log.gaps_detected}
            {log.gaps_repaired > 0 && <span className="text-dim"> ({log.gaps_repaired} ✓)</span>}
          </span>
        ) : (
          <span className="text-dim">0</span>
        )}
      </td>
      <td className="text-right mono text-dim">{formatDuration(log.duration_seconds)}</td>
      <td>
        {log.errors ? (
          <span className="text-error text-xs">⚠ Fehler</span>
        ) : warnings > 0 ? (
          <span className="text-warning text-xs" title={`${warnings} Warnungen`}>⚠ {warnings}</span>
        ) : lines.length > 0 ? (
          <span className="text-dim text-xs" title="Log-Zeilen verfügbar">📋 {lines.length}</span>
        ) : log.notes ? (
          <span className="text-dim text-xs" title={log.notes}>📝</span>
        ) : (
          <span className="text-dim">—</span>
        )}
      </td>
    </tr>
  );
}

function LogDetailModal({ log, onClose }: { log: CollectionLogItem; onClose: () => void }) {
  const [linesExpanded, setLinesExpanded] = useState(false);
  const lines = log.log_lines ?? [];
  const warnings = warnCount(log.log_lines);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="glass-overlay" onClick={onClose}>
      <div
        className="glass-panel"
        role="dialog"
        aria-modal="true"
        aria-label={`Log ${collectorLabel(log.collector_name)}`}
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 700, minWidth: 500 }}
      >
        <div className="flex items-center justify-between mb-lg">
          <h3>{collectorLabel(log.collector_name)}</h3>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Schließen" autoFocus>
            <X size={18} />
          </button>
        </div>

        <div className="grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)', gap: 'var(--space-md)' }}>
          <Field label="Start">{formatDateTime(log.started_at)}</Field>
          <Field label="Ende">{formatDateTime(log.finished_at)}</Field>
          <div>
            <div className="label-dim">Status</div>
            <div className="mt-xs"><StatusBadge status={log.status} /></div>
          </div>
          <Field label="Dauer">{formatDuration(log.duration_seconds)}</Field>
          <Field label="Records gelesen">{log.records_fetched?.toLocaleString('de-DE') ?? '—'}</Field>
          <Field label="Records geschrieben">{log.records_written?.toLocaleString('de-DE') ?? '—'}</Field>
          <Field label="Gaps erkannt / repariert">
            {log.gaps_detected} / {log.gaps_repaired}
            {log.gaps_extrapolated > 0 && (
              <span className="text-warning"> ({log.gaps_extrapolated} extrapoliert)</span>
            )}
          </Field>
        </div>

        {log.notes && (
          <div className="mt-lg">
            <div className="label-dim mb-xs">Notizen</div>
            <div
              className="text-sm"
              style={{
                background: 'var(--surface-lowest)',
                borderRadius: 'var(--radius)',
                padding: 'var(--space-md)',
                whiteSpace: 'pre-wrap',
              }}
            >
              {log.notes}
            </div>
          </div>
        )}

        {log.errors && (
          <div className="mt-lg">
            <div className="label-dim mb-xs" style={{ color: 'var(--error)' }}>Fehler-Details</div>
            <pre
              className="font-mono text-xs"
              style={{
                background: 'var(--surface-lowest)',
                borderRadius: 'var(--radius)',
                padding: 'var(--space-md)',
                overflow: 'auto',
                maxHeight: 300,
                color: 'var(--error)',
                border: '1px solid rgba(255,180,171,0.2)',
              }}
            >
              {JSON.stringify(log.errors, null, 2)}
            </pre>
          </div>
        )}

        {lines.length > 0 && (
          <div className="mt-lg">
            <button
              type="button"
              aria-expanded={linesExpanded}
              onClick={() => setLinesExpanded(!linesExpanded)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-sm)',
                background: 'none',
                border: 'none',
                color: 'var(--on-surface)',
                cursor: 'pointer',
                padding: 0,
                fontSize: '0.8rem',
                fontWeight: 500,
                width: '100%',
              }}
            >
              {linesExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              <span>Log-Zeilen</span>
              <span
                style={{
                  fontSize: '0.7rem',
                  padding: '1px 8px',
                  borderRadius: '10px',
                  background: warnings > 0 ? 'rgba(255,180,50,0.15)' : 'rgba(255,255,255,0.08)',
                  color: warnings > 0 ? 'var(--warning)' : 'var(--on-surface-dim)',
                }}
              >
                {lines.length}
                {warnings > 0 && ` (${warnings} ⚠)`}
              </span>
            </button>

            {linesExpanded && (
              <div
                style={{
                  marginTop: 'var(--space-sm)',
                  background: 'var(--surface-lowest)',
                  borderRadius: 'var(--radius)',
                  padding: 'var(--space-sm)',
                  maxHeight: 400,
                  overflow: 'auto',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                {lines.map((line, i) => {
                  const style = LOG_LEVEL_STYLE[line.level] ?? LOG_LEVEL_STYLE.INFO;
                  const Icon = style.icon;
                  return (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: 'var(--space-xs)',
                        padding: '3px var(--space-xs)',
                        fontSize: '0.72rem',
                        fontFamily: 'var(--font-mono)',
                        color: style.color,
                        borderBottom: '1px solid rgba(255,255,255,0.03)',
                      }}
                    >
                      <Icon size={12} style={{ marginTop: 2, flexShrink: 0 }} />
                      <span style={{ opacity: 0.6, flexShrink: 0, minWidth: 58 }}>{line.level}</span>
                      {line.ts && <span style={{ opacity: 0.5, flexShrink: 0 }}>{line.ts}</span>}
                      <span style={{ wordBreak: 'break-word' }}>{line.msg}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="label-dim">{label}</div>
      <div className="text-sm mono mt-xs">{children}</div>
    </div>
  );
}
