import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, CheckCircle, Clock, Cpu, Database, Wifi, XCircle } from 'lucide-react';
import type { CollectorStatus, TableStats } from '../api';
import { dashboardQuery } from '../queries';
import { formatNumber, formatRelativeTime, formatUptime } from '../format';
import { QueryState } from '../components/QueryState';

type RunState = 'success' | 'partial' | 'failed' | 'running' | 'pending';

function runState(c: CollectorStatus): RunState {
  if (c.is_running || c.last_status === 'running') return 'running';
  switch (c.last_status) {
    case 'success':
      return 'success';
    case 'partial':
      return 'partial';
    case 'failed':
    case 'error':
      return 'failed';
    default:
      return 'pending';
  }
}

const STATE_BADGE: Record<RunState, { cls: string; label: string }> = {
  success: { cls: 'badge-success', label: 'OK' },
  partial: { cls: 'badge-warning', label: 'Teilweise' },
  failed: { cls: 'badge-error', label: 'Fehler' },
  running: { cls: 'badge-warning', label: 'Läuft' },
  pending: { cls: 'badge-neutral', label: 'Ausstehend' },
};

function StateIcon({ state }: { state: RunState }) {
  if (state === 'success') return <CheckCircle size={16} style={{ color: 'var(--primary)' }} />;
  if (state === 'failed') return <XCircle size={16} style={{ color: 'var(--error)' }} />;
  if (state === 'partial') return <AlertTriangle size={16} style={{ color: 'var(--warning)' }} />;
  return <Clock size={16} style={{ color: 'var(--on-surface-dim)' }} />;
}

function shortName(name: string): string {
  return name
    .replace('Daily ', '')
    .replace('Weekly ', '')
    .replace(' (yfinance)', '')
    .replace(' (Alpaca)', '')
    .replace(' (Senate eFD)', '');
}

function CollectorCard({ c }: { c: CollectorStatus }) {
  const state = runState(c);
  const badge = STATE_BADGE[state];

  let resultLabel: ReactNode = null;
  if (c.last_run && state === 'success' && c.records_written != null) {
    resultLabel = (
      <span style={{ color: c.records_written > 0 ? 'var(--primary)' : 'var(--on-surface-dim)' }}>
        ({formatNumber(c.records_written)} neue Einträge)
      </span>
    );
  } else if (c.last_run && (state === 'failed' || state === 'partial')) {
    resultLabel = (
      <Link to="/logs" style={{ color: state === 'failed' ? 'var(--error)' : 'var(--warning)', fontSize: '0.75rem' }}>
        → siehe Logs
      </Link>
    );
  } else if (state === 'running') {
    resultLabel = <span style={{ color: 'var(--warning)' }}>läuft…</span>;
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-md">
        <span className={`badge ${badge.cls}`}><span className="badge-dot" /> {badge.label}</span>
        <StateIcon state={state} />
      </div>
      <div style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '4px' }} title={c.name}>
        {shortName(c.name)}
      </div>
      <div className="text-xs text-dim" style={{ marginBottom: '2px' }}>
        {formatRelativeTime(c.last_run)}
        {resultLabel && <>{' '}{resultLabel}</>}
      </div>
      <div className="label-dim" style={{ marginTop: '8px', fontSize: '0.6rem' }}>
        Nächster Lauf:{' '}
        {c.next_run
          ? new Date(c.next_run).toLocaleString('de-DE', {
            weekday: 'short', hour: '2-digit', minute: '2-digit',
          })
          : c.via_chain ? 'via Nightly Chain' : '—'}
      </div>
    </div>
  );
}

function TableStatsCard({ stats }: { stats: TableStats[] }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div className="card-title" style={{ padding: 'var(--space-lg) var(--space-lg) var(--space-sm)' }}>
        Datenbestand
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Tabelle</th>
            <th className="text-right">Einträge</th>
            <th>Von</th>
            <th>Bis</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((s) => (
            <tr key={s.table}>
              <td className="mono">{s.table}</td>
              <td className="text-right mono" title={s.row_count.toLocaleString('de-DE')}>{formatNumber(s.row_count)}</td>
              <td className="text-xs text-dim">{s.min_date ?? '—'}</td>
              <td className="text-xs text-dim">{s.max_date ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HealthCard({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="card flex items-center gap-md">
      {icon}
      <div>
        <div className="label-dim">{label}</div>
        <div className="text-sm" style={{ fontWeight: 600 }}>{children}</div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const query = useQuery(dashboardQuery());
  const health = query.data?.system_health;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Dashboard</h2>
        {health && (
          <span className={`badge ${health.scheduler_running ? 'badge-success' : 'badge-error'}`}>
            <span className="badge-dot" />
            {health.scheduler_running ? 'Online' : 'Offline'}
          </span>
        )}
      </div>

      {/* QueryState keeps the last data visible and shows a banner if a poll fails. */}
      <QueryState query={query} loadingText="Lade Dashboard-Daten…">
        {(d) => (
          <>
            <div className="label" style={{ marginBottom: 'var(--space-md)' }}>Collector Status</div>
            <div className="grid grid-5" style={{ marginBottom: 'var(--space-2xl)' }}>
              {d.collectors.map((c) => <CollectorCard key={c.id} c={c} />)}
            </div>

            <div style={{ marginBottom: 'var(--space-2xl)' }}>
              <TableStatsCard stats={d.table_stats} />
            </div>

            <div className="label" style={{ marginBottom: 'var(--space-md)' }}>System Health</div>
            <div className="grid grid-4">
              <HealthCard
                label="Datenbank"
                icon={<Wifi size={20} style={{ color: d.system_health.db_connected ? 'var(--primary)' : 'var(--error)' }} />}
              >
                {d.system_health.db_connected ? 'Verbunden' : 'Getrennt'}
              </HealthCard>
              <HealthCard label="Alembic" icon={<Database size={20} style={{ color: 'var(--primary)' }} />}>
                <span className="font-mono" style={{ fontWeight: 400 }}>{d.system_health.alembic_revision ?? '—'}</span>
              </HealthCard>
              <HealthCard label="Uptime" icon={<Cpu size={20} style={{ color: 'var(--primary)' }} />}>
                {formatUptime(d.system_health.uptime_seconds)}
              </HealthCard>
              <HealthCard label="Jobs" icon={<Activity size={20} style={{ color: 'var(--primary)' }} />}>
                {d.system_health.job_count} aktiv
              </HealthCard>
            </div>
          </>
        )}
      </QueryState>
    </div>
  );
}
