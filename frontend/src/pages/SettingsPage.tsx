import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, Play, RefreshCw, Tags, Trash2, Wrench } from 'lucide-react';
import {
  ensureSuccess,
  resetDatabase,
  runVacuum,
  startIndicatorBackfill,
  startPriceBackfill,
  startSectorEnrichment,
  triggerJob,
  type SchedulerJob,
} from '../api';
import {
  backfillStatusQuery,
  dbStatsQuery,
  queryKeys,
  schedulerJobsQuery,
  universeCountQuery,
} from '../queries';
import { formatShortDateTime } from '../format';
import { InlineError, QueryState } from '../components/QueryState';
import { ApiKeyCard } from './settings/ApiKeyCard';
import { BackfillCard } from './settings/BackfillCard';

const RESET_WORD = 'RESET';

const TASK_BADGE: Record<string, string> = {
  completed: 'badge-success',
  partial: 'badge-warning',
  failed: 'badge-error',
};

export default function SettingsPage() {
  return (
    <div className="fade-in">
      <div className="page-header">
        <h2>Settings &amp; Operations</h2>
      </div>

      <div className="flex flex-col gap-xl">
        <ApiKeyCard />
        <SchedulerSection />
        <BackfillSection />
        <DbSection />
      </div>
    </div>
  );
}

// ── Scheduler ─────────────────────────────────────────────────────────

function SchedulerSection() {
  const queryClient = useQueryClient();
  const jobsQ = useQuery(schedulerJobsQuery());
  const trigger = useMutation({
    mutationFn: (jobId: string) => ensureSuccess(triggerJob(jobId)),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.schedulerJobs }),
  });

  return (
    <div>
      <div className="label mb-md">Scheduler</div>
      <QueryState
        query={jobsQ}
        loadingText="Lade Jobs…"
        isEmpty={(jobs) => jobs.length === 0}
        emptyText="Keine Jobs registriert (Scheduler inaktiv?)"
      >
        {(jobs) => (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Trigger</th>
                  <th>Nächster Lauf</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    pending={trigger.isPending && trigger.variables === job.id}
                    error={trigger.variables === job.id ? trigger.error : null}
                    onTrigger={() => trigger.mutate(job.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </div>
  );
}

function JobRow({
  job,
  pending,
  error,
  onTrigger,
}: {
  job: SchedulerJob;
  pending: boolean;
  error: unknown;
  onTrigger: () => void;
}) {
  const statusClass = job.is_running ? 'badge-warning'
    : job.via_chain || job.pending ? 'badge-neutral'
      : 'badge-success';
  const statusText = job.is_running ? '⟳ Läuft…'
    : job.via_chain ? 'Nightly Chain'
      : job.pending ? 'Ausstehend'
        : 'Bereit';
  return (
    <tr>
      <td style={{ fontWeight: 500 }}>{job.name}</td>
      <td className="mono text-xs text-dim">{job.trigger}</td>
      <td className="text-sm">
        {job.via_chain && !job.next_run ? (
          <span className="text-dim" title="Läuft als Schritt der nächtlichen Kette (nightly_chain)">via Nightly Chain</span>
        ) : (
          formatShortDateTime(job.next_run)
        )}
      </td>
      <td><span className={`badge ${statusClass}`}>{statusText}</span></td>
      <td className="text-right">
        <button className="btn btn-ghost btn-sm" onClick={onTrigger} disabled={pending || job.is_running}>
          <Play size={12} />
          {pending ? 'Starte…' : job.is_running ? 'Aktiv' : 'Jetzt starten'}
        </button>
        <InlineError error={error} />
      </td>
    </tr>
  );
}

// ── Backfill ──────────────────────────────────────────────────────────

function BackfillSection() {
  const statusQ = useQuery(backfillStatusQuery());
  const tasks = statusQ.data ?? [];
  const running = (operation: string) =>
    tasks.find((t) => t.operation === operation && t.status === 'running');
  const finished = tasks.filter((t) => t.status !== 'idle' && t.status !== 'running');

  return (
    <div>
      <div className="label mb-md">Backfill</div>
      {statusQ.isError && <InlineError error={statusQ.error} />}
      <div className="grid grid-3">
        <BackfillCard
          icon={<Download size={18} style={{ color: 'var(--primary)' }} />}
          title="Price Backfill"
          description="Historische Preisdaten von Alpaca laden (Backend-Standardzeitraum)."
          buttonLabel="Backfill starten"
          task={running('price_backfill')}
          start={() => startPriceBackfill()}
        />
        <BackfillCard
          icon={<RefreshCw size={18} style={{ color: 'var(--primary)' }} />}
          title="TA Indicator Backfill"
          description="Technische Indikatoren aus vorhandenen Preisdaten berechnen."
          buttonLabel="Backfill starten"
          task={running('indicator_backfill')}
          start={startIndicatorBackfill}
        />
        <BackfillCard
          icon={<Tags size={18} style={{ color: 'var(--primary)' }} />}
          title="Sektoren nachladen"
          description="Fehlende Sektor- und Branchendaten von Yahoo Finance laden."
          buttonLabel="Sektoren laden"
          task={running('sector_enrichment')}
          start={startSectorEnrichment}
        />
      </div>

      {finished.length > 0 && (
        <div className="mt-lg">
          <div className="label-dim mb-md">Vergangene Tasks</div>
          <div className="flex flex-col gap-sm">
            {finished.map((t) => (
              <div
                key={t.task_id}
                className="card flex items-center justify-between"
                style={{ padding: 'var(--space-sm) var(--space-lg)' }}
              >
                <span className="text-sm">
                  {t.operation}
                  {t.started_at && <span className="text-xs text-dim"> · {formatShortDateTime(t.started_at)}</span>}
                </span>
                <span className="flex items-center gap-sm">
                  {t.error && <span className="text-xs text-error" title={t.error}>{t.error.slice(0, 80)}</span>}
                  <span className={`badge ${TASK_BADGE[t.status] ?? 'badge-neutral'}`}>{t.status}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Database ──────────────────────────────────────────────────────────

const dangerButtonStyle = { background: 'var(--error)', color: '#fff', border: 'none' } as const;

function DbSection() {
  const queryClient = useQueryClient();
  const statsQ = useQuery(dbStatsQuery());
  const universeCount = useQuery(universeCountQuery());
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const vacuum = useMutation({
    mutationFn: () => ensureSuccess(runVacuum()),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.dbStats }),
  });

  const reset = useMutation({
    mutationFn: () => ensureSuccess(resetDatabase()),
    onSuccess: () => {
      setShowResetConfirm(false);
      setConfirmText('');
      // Every cached view is stale after a reset.
      void queryClient.invalidateQueries();
    },
  });

  const closeConfirm = () => {
    setShowResetConfirm(false);
    setConfirmText('');
    reset.reset();
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-md">
        <div className="label">Datenbank</div>
        <div className="flex gap-sm items-center">
          <button className="btn btn-secondary btn-sm" onClick={() => vacuum.mutate()} disabled={vacuum.isPending}>
            <Wrench size={12} />
            {vacuum.isPending ? 'VACUUM läuft…' : 'VACUUM ANALYZE'}
          </button>
          <button
            className="btn btn-sm"
            style={dangerButtonStyle}
            onClick={() => setShowResetConfirm(true)}
            disabled={reset.isPending || showResetConfirm}
          >
            <Trash2 size={12} />
            Werkszustand
          </button>
        </div>
      </div>
      <InlineError error={vacuum.error} />
      {vacuum.isSuccess && <div className="text-xs text-dim mb-md">{vacuum.data.message}</div>}

      {showResetConfirm && (
        <div
          className="card mb-lg"
          role="alertdialog"
          aria-labelledby="reset-title"
          style={{ border: '1px solid var(--error)', background: 'rgba(239, 68, 68, 0.08)' }}
        >
          <div className="flex items-center gap-sm mb-md">
            <AlertTriangle size={20} style={{ color: 'var(--error)' }} />
            <strong id="reset-title" style={{ color: 'var(--error)' }}>Datenbank zurücksetzen?</strong>
          </div>
          <div className="text-sm mb-lg" style={{ lineHeight: 1.6 }}>
            Diese Aktion löscht <strong>alle gesammelten Daten</strong> unwiderruflich:
            <br />
            Preise, Indikatoren, ARK-Holdings, Insider-Trades, Politiker-Trades,
            Fundamentals, Analyst-Ratings, Earnings-Kalender und Collection-Logs.
            <br />
            <br />
            Das <strong>Ticker-Universum</strong>
            {universeCount.data != null && <> ({universeCount.data.toLocaleString('de-DE')} Ticker)</>} bleibt erhalten.
          </div>
          <label className="text-sm flex items-center gap-sm mb-md">
            Zur Bestätigung <code>{RESET_WORD}</code> eingeben:
            <input
              className="input"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              aria-label={`${RESET_WORD} zur Bestätigung eingeben`}
              autoComplete="off"
              style={{ maxWidth: 140 }}
            />
          </label>
          <InlineError error={reset.error} />
          <div className="flex gap-sm justify-end mt-sm">
            <button className="btn btn-secondary btn-sm" onClick={closeConfirm} disabled={reset.isPending}>
              Abbrechen
            </button>
            <button
              className="btn btn-sm"
              style={dangerButtonStyle}
              onClick={() => reset.mutate()}
              disabled={reset.isPending || confirmText !== RESET_WORD}
            >
              <Trash2 size={12} />
              {reset.isPending ? 'Lösche…' : 'Ja, alles löschen'}
            </button>
          </div>
        </div>
      )}
      {reset.isSuccess && !showResetConfirm && (
        <div className="text-xs text-dim mb-md" role="status">{reset.data.message}</div>
      )}

      <QueryState query={statsQ} loadingText="Lade DB-Statistiken…">
        {(stats) => (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tabelle</th>
                  <th className="text-right">Einträge</th>
                  <th className="text-right">Größe</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((t) => (
                  <tr key={t.table_name}>
                    <td className="mono">{t.table_name}</td>
                    <td className="text-right mono">{t.row_count.toLocaleString('de-DE')}</td>
                    <td className="text-right text-dim">{t.size_human ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </div>
  );
}
