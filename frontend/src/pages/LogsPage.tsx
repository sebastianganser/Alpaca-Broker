import { useQuery } from '@tanstack/react-query';
import { fetchLogs, fetchCollectorNames } from '../api';
import type { CollectionLogItem } from '../api';
import { useState } from 'react';
import { ChevronLeft, ChevronRight, ChevronDown, ChevronUp, X, AlertTriangle, AlertCircle, Info } from 'lucide-react';

const STATUS_BADGE: Record<string, string> = {
  success: 'badge-success',
  partial: 'badge-warning',
  failed: 'badge-error',
};

const LOG_LEVEL_STYLE: Record<string, { color: string; icon: typeof AlertTriangle }> = {
  WARNING: { color: 'var(--warning)', icon: AlertTriangle },
  ERROR: { color: 'var(--error)', icon: AlertCircle },
  CRITICAL: { color: 'var(--error)', icon: AlertCircle },
  INFO: { color: 'var(--text-dim)', icon: Info },
};

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function collectorLabel(name: string | null): string {
  if (!name) return '—';
  return name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function LogsPage() {
  const [page, setPage] = useState(1);
  const [collector, setCollector] = useState('');
  const [status, setStatus] = useState('');
  const [selectedLog, setSelectedLog] = useState<CollectionLogItem | null>(null);
  const [logsExpanded, setLogsExpanded] = useState(false);
  const limit = 30;

  const { data: collectors } = useQuery({
    queryKey: ['collector-names'],
    queryFn: fetchCollectorNames,
  });

  const { data, isLoading } = useQuery({
    queryKey: ['logs', page, collector, status],
    queryFn: () =>
      fetchLogs({
        page,
        limit,
        ...(collector ? { collector } : {}),
        ...(status ? { status } : {}),
      }),
    refetchInterval: 10_000,
  });

  const totalPages = data ? Math.ceil(data.total / limit) : 0;

  // Count warnings/errors in selected log
  const warnErrorCount = selectedLog?.log_lines
    ? selectedLog.log_lines.filter(
        (l) => l.level === 'WARNING' || l.level === 'ERROR' || l.level === 'CRITICAL'
      ).length
    : 0;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Logs</h2>
        <div className="text-sm text-dim">
          {data ? `${data.total} Einträge` : '...'}
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-md mb-lg items-center">
        <select
          className="input"
          style={{ maxWidth: 240 }}
          value={collector}
          onChange={(e) => { setCollector(e.target.value); setPage(1); }}
        >
          <option value="">Alle Collector</option>
          {collectors?.map((c) => (
            <option key={c} value={c}>
              {collectorLabel(c)}
            </option>
          ))}
        </select>

        <select
          className="input"
          style={{ maxWidth: 160 }}
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1); }}
        >
          <option value="">Alle Status</option>
          <option value="success">✅ Success</option>
          <option value="partial">⚠️ Partial</option>
          <option value="failed">❌ Failed</option>
        </select>

        {(collector || status) && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { setCollector(''); setStatus(''); setPage(1); }}
          >
            <X size={14} />
            Filter zurücksetzen
          </button>
        )}
      </div>

      {/* Table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {isLoading ? (
          <div
            className="loading-pulse text-dim"
            style={{ padding: 'var(--space-xl)', textAlign: 'center' }}
          >
            Lade Logs...
          </div>
        ) : !data || data.logs.length === 0 ? (
          <div
            className="text-dim"
            style={{ padding: 'var(--space-xl)', textAlign: 'center' }}
          >
            Keine Log-Einträge
          </div>
        ) : (
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
              {data.logs.map((log) => {
                const hasLogLines = log.log_lines && log.log_lines.length > 0;
                const logWarnCount = hasLogLines
                  ? log.log_lines!.filter(
                      (l) => l.level === 'WARNING' || l.level === 'ERROR' || l.level === 'CRITICAL'
                    ).length
                  : 0;
                return (
                <tr
                  key={log.id}
                  onClick={() => { setSelectedLog(log); setLogsExpanded(false); }}
                  style={{ cursor: 'pointer' }}
                >
                  <td className="text-xs mono">
                    {formatDateTime(log.started_at)}
                  </td>
                  <td style={{ fontWeight: 500 }}>
                    {collectorLabel(log.collector_name)}
                  </td>
                  <td>
                    <span
                      className={`badge ${STATUS_BADGE[log.status ?? ''] ?? 'badge-neutral'}`}
                    >
                      <span className="badge-dot" />
                      {log.status ?? '—'}
                    </span>
                  </td>
                  <td className="text-right mono">
                    {log.records_fetched?.toLocaleString() ?? '—'}
                  </td>
                  <td className="text-right mono">
                    {log.records_written?.toLocaleString() ?? '—'}
                  </td>
                  <td className="text-right mono">
                    {log.gaps_detected > 0 ? (
                      <span className="text-warning">
                        {log.gaps_detected}
                        {log.gaps_repaired > 0 && (
                          <span className="text-dim"> ({log.gaps_repaired} ✓)</span>
                        )}
                      </span>
                    ) : (
                      <span className="text-dim">0</span>
                    )}
                  </td>
                  <td className="text-right mono text-dim">
                    {formatDuration(log.duration_seconds)}
                  </td>
                  <td>
                    {log.errors ? (
                      <span className="text-error text-xs">⚠ Fehler</span>
                    ) : logWarnCount > 0 ? (
                      <span className="text-warning text-xs" title={`${logWarnCount} Warnungen`}>
                        ⚠ {logWarnCount}
                      </span>
                    ) : hasLogLines ? (
                      <span className="text-dim text-xs" title="Log-Zeilen verfügbar">
                        📋 {log.log_lines!.length}
                      </span>
                    ) : log.notes ? (
                      <span className="text-dim text-xs" title={log.notes}>
                        📝
                      </span>
                    ) : (
                      <span className="text-dim">—</span>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-lg">
          <div className="text-xs text-dim">
            Seite {page} von {totalPages}
          </div>
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

      {/* Detail Modal */}
      {selectedLog && (
        <div className="glass-overlay" onClick={() => setSelectedLog(null)}>
          <div
            className="glass-panel"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 700, minWidth: 500 }}
          >
            <div className="flex items-center justify-between mb-lg">
              <h3>{collectorLabel(selectedLog.collector_name)}</h3>
              <button
                className="btn btn-ghost btn-icon"
                onClick={() => setSelectedLog(null)}
              >
                <X size={18} />
              </button>
            </div>

            <div
              className="grid"
              style={{
                gridTemplateColumns: 'repeat(2, 1fr)',
                gap: 'var(--space-md)',
              }}
            >
              <div>
                <div className="label-dim">Start</div>
                <div className="text-sm mono mt-xs">
                  {formatDateTime(selectedLog.started_at)}
                </div>
              </div>
              <div>
                <div className="label-dim">Ende</div>
                <div className="text-sm mono mt-xs">
                  {formatDateTime(selectedLog.finished_at)}
                </div>
              </div>
              <div>
                <div className="label-dim">Status</div>
                <div className="mt-xs">
                  <span
                    className={`badge ${STATUS_BADGE[selectedLog.status ?? ''] ?? 'badge-neutral'}`}
                  >
                    <span className="badge-dot" />
                    {selectedLog.status ?? '—'}
                  </span>
                </div>
              </div>
              <div>
                <div className="label-dim">Dauer</div>
                <div className="text-sm mono mt-xs">
                  {formatDuration(selectedLog.duration_seconds)}
                </div>
              </div>
              <div>
                <div className="label-dim">Records gelesen</div>
                <div className="text-sm mono mt-xs">
                  {selectedLog.records_fetched?.toLocaleString() ?? '—'}
                </div>
              </div>
              <div>
                <div className="label-dim">Records geschrieben</div>
                <div className="text-sm mono mt-xs">
                  {selectedLog.records_written?.toLocaleString() ?? '—'}
                </div>
              </div>
              <div>
                <div className="label-dim">Gaps erkannt / repariert</div>
                <div className="text-sm mono mt-xs">
                  {selectedLog.gaps_detected} / {selectedLog.gaps_repaired}
                  {selectedLog.gaps_extrapolated > 0 && (
                    <span className="text-warning">
                      {' '}
                      ({selectedLog.gaps_extrapolated} extrapoliert)
                    </span>
                  )}
                </div>
              </div>
            </div>

            {selectedLog.notes && (
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
                  {selectedLog.notes}
                </div>
              </div>
            )}

            {selectedLog.errors && (
              <div className="mt-lg">
                <div className="label-dim mb-xs" style={{ color: 'var(--error)' }}>
                  Fehler-Details
                </div>
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
                  {JSON.stringify(selectedLog.errors, null, 2)}
                </pre>
              </div>
            )}

            {/* Expandable Log Lines */}
            {selectedLog.log_lines && selectedLog.log_lines.length > 0 && (
              <div className="mt-lg">
                <button
                  onClick={() => setLogsExpanded(!logsExpanded)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-sm)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-primary)',
                    cursor: 'pointer',
                    padding: 0,
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    width: '100%',
                  }}
                >
                  {logsExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  <span>Log-Zeilen</span>
                  <span
                    style={{
                      fontSize: '0.7rem',
                      padding: '1px 8px',
                      borderRadius: '10px',
                      background: warnErrorCount > 0 ? 'rgba(255,180,50,0.15)' : 'rgba(255,255,255,0.08)',
                      color: warnErrorCount > 0 ? 'var(--warning)' : 'var(--text-dim)',
                    }}
                  >
                    {selectedLog.log_lines.length}
                    {warnErrorCount > 0 && ` (${warnErrorCount} ⚠)`}
                  </span>
                </button>

                {logsExpanded && (
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
                    {selectedLog.log_lines.map((line, i) => {
                      const style = LOG_LEVEL_STYLE[line.level] || LOG_LEVEL_STYLE.INFO;
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
                          <span style={{ opacity: 0.6, flexShrink: 0, minWidth: 58 }}>
                            {line.level}
                          </span>
                          <span style={{ wordBreak: 'break-word' }}>
                            {line.msg}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
