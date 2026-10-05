import type { ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ensureSuccess, type BackfillStatus, type TriggerResponse } from '../../api';
import { queryKeys } from '../../queries';
import { formatEta } from '../../format';
import { InlineError } from '../../components/QueryState';

interface BackfillCardProps {
  icon: ReactNode;
  title: string;
  description: ReactNode;
  buttonLabel: string;
  /** Running task of this operation, if any (from /ops/backfill/status). */
  task: BackfillStatus | undefined;
  start: () => Promise<TriggerResponse>;
}

export function BackfillCard({ icon, title, description, buttonLabel, task, start }: BackfillCardProps) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => ensureSuccess(start()),
    onSettled: () => queryClient.invalidateQueries({ queryKey: queryKeys.backfillStatus }),
  });
  const running = task != null;

  return (
    <div className="card">
      <div className="flex items-center gap-sm mb-md">
        {icon}
        <div style={{ fontWeight: 600 }}>{title}</div>
      </div>
      <div className="text-xs text-dim mb-md">{description}</div>
      <button
        className="btn btn-primary btn-sm w-full"
        onClick={() => mutation.mutate()}
        disabled={running || mutation.isPending}
      >
        {running ? 'Läuft…' : mutation.isPending ? 'Starte…' : buttonLabel}
      </button>
      <InlineError error={mutation.error} />
      {running && <TaskProgress task={task} />}
    </div>
  );
}

export function TaskProgress({ task }: { task: BackfillStatus }) {
  const pct = Math.max(0, Math.min(100, task.progress_pct));
  return (
    <div className="mt-md">
      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-bar-fill" style={{ width: `${pct}%`, transition: 'width 0.5s ease' }} />
      </div>
      <div className="flex justify-between mt-sm">
        <span className="text-xs text-dim mono">{task.current_ticker ?? '…'}</span>
        <span className="text-xs text-dim mono">{pct.toFixed(0)}%</span>
      </div>
      {task.eta_seconds != null && task.eta_seconds > 0 && (
        <div className="text-xs text-dim mt-xs text-center">{formatEta(task.eta_seconds)}</div>
      )}
    </div>
  );
}
