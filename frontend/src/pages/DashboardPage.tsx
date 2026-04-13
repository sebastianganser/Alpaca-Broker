import { useQuery } from '@tanstack/react-query';
import { fetchDashboard, type CollectorStatus, type TableStats } from '../api';
import { CheckCircle, XCircle, Clock, Database, Cpu, Wifi, Activity } from 'lucide-react';

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return '—';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'gerade eben';
  if (mins < 60) return `vor ${mins} Min.`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  const days = Math.floor(hours / 24);
  return `vor ${days} Tag${days > 1 ? 'en' : ''}`;
}

function formatUptime(seconds: number | null): string {
  if (!seconds) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function StatusBadge({ status }: { status: string | null }) {
  if (status === 'success')
    return <span className="badge badge-success"><span className="badge-dot" /> OK</span>;
  if (status === 'error' || status === 'failed')
    return <span className="badge badge-error"><span className="badge-dot" /> Fehler</span>;
  if (status === 'running')
    return <span className="badge badge-warning"><span className="badge-dot" /> Läuft</span>;
  return <span className="badge badge-neutral"><span className="badge-dot" /> Ausstehend</span>;
}

function CollectorCard({ c }: { c: CollectorStatus }) {
  const shortName = c.name
    .replace('Daily ', '').replace('Weekly ', '')
    .replace(' (yfinance)', '').replace(' (Alpaca)', '')
    .replace(' (Senate eFD)', '');

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-md">
        <StatusBadge status={c.last_status} />
        {c.last_status === 'success' ? (
          <CheckCircle size={16} style={{ color: 'var(--primary)' }} />
        ) : c.last_status === 'error' ? (
          <XCircle size={16} style={{ color: 'var(--error)' }} />
        ) : (
          <Clock size={16} style={{ color: 'var(--on-surface-dim)' }} />
        )}
      </div>
      <div style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '4px' }}>
        {shortName}
      </div>
      <div className="text-xs text-dim" style={{ marginBottom: '8px' }}>
        {formatRelativeTime(c.last_run)}
      </div>
      {c.records_written !== null && (
        <div className="text-xs text-variant">
          {formatNumber(c.records_written)} records
        </div>
      )}
      <div className="label-dim" style={{ marginTop: '8px', fontSize: '0.6rem' }}>
        Nächster Lauf: {c.next_run ? new Date(c.next_run).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '—'}
      </div>
    </div>
  );
}

function DataTable({ stats }: { stats: TableStats[] }) {
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
              <td className="text-right mono">{formatNumber(s.row_count)}</td>
              <td className="text-xs text-dim">{s.min_date || '—'}</td>
              <td className="text-xs text-dim">{s.max_date || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DashboardPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    refetchInterval: 30_000,
  });

  if (isLoading) {
    return (
      <div className="fade-in">
        <div className="page-header">
          <h2>Dashboard</h2>
        </div>
        <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)' }}>
          Lade Dashboard-Daten...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fade-in">
        <div className="page-header"><h2>Dashboard</h2></div>
        <div className="card" style={{ color: 'var(--error)' }}>
          Fehler beim Laden: {(error as Error).message}
        </div>
      </div>
    );
  }

  const d = data!;

  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Dashboard</h2>
        <span className={`badge ${d.system_health.scheduler_running ? 'badge-success' : 'badge-error'}`}>
          <span className="badge-dot" />
          {d.system_health.scheduler_running ? 'Online' : 'Offline'}
        </span>
      </div>

      {/* Collector Status Grid */}
      <div className="label" style={{ marginBottom: 'var(--space-md)' }}>
        Collector Status
      </div>
      <div className="grid grid-5" style={{ marginBottom: 'var(--space-2xl)' }}>
        {d.collectors.map((c) => (
          <CollectorCard key={c.id} c={c} />
        ))}
      </div>

      {/* Data Table */}
      <div style={{ marginBottom: 'var(--space-2xl)' }}>
        <DataTable stats={d.table_stats} />
      </div>

      {/* System Health */}
      <div className="label" style={{ marginBottom: 'var(--space-md)' }}>
        System Health
      </div>
      <div className="grid grid-4">
        <div className="card flex items-center gap-md">
          <Wifi size={20} style={{ color: d.system_health.db_connected ? 'var(--primary)' : 'var(--error)' }} />
          <div>
            <div className="label-dim">Datenbank</div>
            <div className="text-sm" style={{ fontWeight: 600 }}>
              {d.system_health.db_connected ? 'Verbunden' : 'Getrennt'}
            </div>
          </div>
        </div>
        <div className="card flex items-center gap-md">
          <Database size={20} style={{ color: 'var(--primary)' }} />
          <div>
            <div className="label-dim">Alembic</div>
            <div className="text-sm font-mono">
              {d.system_health.alembic_revision || '—'}
            </div>
          </div>
        </div>
        <div className="card flex items-center gap-md">
          <Cpu size={20} style={{ color: 'var(--primary)' }} />
          <div>
            <div className="label-dim">Uptime</div>
            <div className="text-sm" style={{ fontWeight: 600 }}>
              {formatUptime(d.system_health.uptime_seconds)}
            </div>
          </div>
        </div>
        <div className="card flex items-center gap-md">
          <Activity size={20} style={{ color: 'var(--primary)' }} />
          <div>
            <div className="label-dim">Jobs</div>
            <div className="text-sm" style={{ fontWeight: 600 }}>
              {d.system_health.job_count} aktiv
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
