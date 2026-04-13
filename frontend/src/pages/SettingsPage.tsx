import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchSchedulerJobs,
  fetchBackfillStatus,
  fetchDbStats,
  triggerJob,
  startPriceBackfill,
  startIndicatorBackfill,
  runVacuum,
} from '../api';
import { Play, RefreshCw, Download, Wrench } from 'lucide-react';
import { useState } from 'react';

export default function SettingsPage() {
  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Settings & Operations</h2>
      </div>

      <div className="flex flex-col gap-xl">
        <SchedulerSection />
        <BackfillSection />
        <DbSection />
      </div>
    </div>
  );
}

function SchedulerSection() {
  const queryClient = useQueryClient();
  const { data: jobs, isLoading } = useQuery({
    queryKey: ['scheduler-jobs'],
    queryFn: fetchSchedulerJobs,
    refetchInterval: 10_000,
  });
  const [triggering, setTriggering] = useState<string | null>(null);

  const handleTrigger = async (jobId: string) => {
    setTriggering(jobId);
    try {
      await triggerJob(jobId);
      queryClient.invalidateQueries({ queryKey: ['scheduler-jobs'] });
    } catch (e) {
      console.error(e);
    }
    setTriggering(null);
  };

  return (
    <div>
      <div className="label mb-md">Scheduler</div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {isLoading ? (
          <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)', textAlign: 'center' }}>
            Lade Jobs...
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Trigger</th>
                <th>Nächster Lauf</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobs?.map((job) => (
                <tr key={job.id}>
                  <td style={{ fontWeight: 500 }}>{job.name}</td>
                  <td className="mono text-xs text-dim">{job.trigger}</td>
                  <td className="text-sm">
                    {job.next_run
                      ? new Date(job.next_run).toLocaleString('de-DE', {
                          day: '2-digit', month: '2-digit',
                          hour: '2-digit', minute: '2-digit',
                        })
                      : '—'}
                  </td>
                  <td>
                    <span className={`badge ${job.pending ? 'badge-warning' : 'badge-success'}`}>
                      {job.pending ? 'Ausstehend' : 'Bereit'}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={() => handleTrigger(job.id)}
                      disabled={triggering === job.id}
                    >
                      <Play size={12} />
                      {triggering === job.id ? 'Gestartet...' : 'Jetzt starten'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function BackfillSection() {
  const queryClient = useQueryClient();
  const { data: status } = useQuery({
    queryKey: ['backfill-status'],
    queryFn: fetchBackfillStatus,
    refetchInterval: 2_000,
  });

  const [loading, setLoading] = useState<string | null>(null);

  const handlePriceBackfill = async () => {
    setLoading('prices');
    try {
      await startPriceBackfill('2021-01-01');
      queryClient.invalidateQueries({ queryKey: ['backfill-status'] });
    } catch (e) {
      console.error(e);
    }
    setLoading(null);
  };

  const handleTaBackfill = async () => {
    setLoading('indicators');
    try {
      await startIndicatorBackfill();
      queryClient.invalidateQueries({ queryKey: ['backfill-status'] });
    } catch (e) {
      console.error(e);
    }
    setLoading(null);
  };


  const isPriceRunning = status?.some(
    (t) => t.operation === 'price_backfill' && t.status === 'running'
  );
  const isTaRunning = status?.some(
    (t) => t.operation === 'indicator_backfill' && t.status === 'running'
  );

  return (
    <div>
      <div className="label mb-md">Backfill</div>
      <div className="grid grid-2">
        <div className="card">
          <div className="flex items-center gap-sm mb-md">
            <Download size={18} style={{ color: 'var(--primary)' }} />
            <div style={{ fontWeight: 600 }}>Price Backfill</div>
          </div>
          <div className="text-xs text-dim mb-md">
            Historische Preisdaten ab 2021-01-01 von Alpaca laden
          </div>
          <button
            className="btn btn-primary btn-sm w-full"
            onClick={handlePriceBackfill}
            disabled={!!isPriceRunning || loading === 'prices'}
          >
            {isPriceRunning ? 'Läuft...' : loading === 'prices' ? 'Starte...' : 'Backfill starten'}
          </button>
          {isPriceRunning && (
            <div className="mt-md">
              <div className="progress-bar">
                <div
                  className="progress-bar-fill"
                  style={{
                    width: `${status?.find((t) => t.operation === 'price_backfill')?.progress_pct ?? 0}%`,
                  }}
                />
              </div>
              <div className="text-xs text-dim mt-md" style={{ textAlign: 'center' }}>
                {status?.find((t) => t.operation === 'price_backfill')?.progress_pct?.toFixed(0)}%
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <div className="flex items-center gap-sm mb-md">
            <RefreshCw size={18} style={{ color: 'var(--primary)' }} />
            <div style={{ fontWeight: 600 }}>TA Indicator Backfill</div>
          </div>
          <div className="text-xs text-dim mb-md">
            Technische Indikatoren aus vorhandenen Preisdaten berechnen
          </div>
          <button
            className="btn btn-primary btn-sm w-full"
            onClick={handleTaBackfill}
            disabled={!!isTaRunning || loading === 'indicators'}
          >
            {isTaRunning ? 'Läuft...' : loading === 'indicators' ? 'Starte...' : 'Backfill starten'}
          </button>
          {isTaRunning && (
            <div className="mt-md">
              <div className="progress-bar">
                <div
                  className="progress-bar-fill"
                  style={{
                    width: `${status?.find((t) => t.operation === 'indicator_backfill')?.progress_pct ?? 0}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Completed/Failed tasks */}
      {status && status.filter((t) => t.status !== 'idle' && t.status !== 'running').length > 0 && (
        <div className="mt-lg">
          <div className="label-dim mb-md">Vergangene Tasks</div>
          <div className="flex flex-col gap-sm">
            {status
              .filter((t) => t.status !== 'idle' && t.status !== 'running')
              .map((t) => (
                <div key={t.task_id} className="card flex items-center justify-between" style={{ padding: 'var(--space-sm) var(--space-lg)' }}>
                  <span className="text-sm">{t.operation}</span>
                  <span className={`badge ${t.status === 'completed' ? 'badge-success' : 'badge-error'}`}>
                    {t.status}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DbSection() {
  const queryClient = useQueryClient();
  const { data: stats, isLoading } = useQuery({
    queryKey: ['db-stats'],
    queryFn: fetchDbStats,
  });
  const [vacuuming, setVacuuming] = useState(false);

  const handleVacuum = async () => {
    setVacuuming(true);
    try {
      await runVacuum();
      queryClient.invalidateQueries({ queryKey: ['db-stats'] });
    } catch (e) {
      console.error(e);
    }
    setVacuuming(false);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-md">
        <div className="label">Datenbank</div>
        <button
          className="btn btn-secondary btn-sm"
          onClick={handleVacuum}
          disabled={vacuuming}
        >
          <Wrench size={12} />
          {vacuuming ? 'VACUUM läuft...' : 'VACUUM ANALYZE'}
        </button>
      </div>
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {isLoading ? (
          <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)', textAlign: 'center' }}>
            Lade DB-Statistiken...
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Tabelle</th>
                <th className="text-right">Einträge</th>
                <th className="text-right">Größe</th>
              </tr>
            </thead>
            <tbody>
              {stats?.map((t) => (
                <tr key={t.table_name}>
                  <td className="mono">{t.table_name}</td>
                  <td className="text-right mono">{t.row_count.toLocaleString()}</td>
                  <td className="text-right text-dim">{t.size_human ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
