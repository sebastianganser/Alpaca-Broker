import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { errorMessage, isApiError } from '../api';

/** Minimal structural subset of a TanStack `UseQueryResult`. */
export interface QueryLike<T> {
  data: T | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  isFetching?: boolean;
  refetch?: () => unknown;
}

interface QueryStateProps<T> {
  query: QueryLike<T>;
  loadingText?: string;
  /** Rendered instead of the default loading text (e.g. skeletons). */
  loading?: ReactNode;
  isEmpty?: (data: T) => boolean;
  emptyText?: ReactNode;
  children: (data: T) => ReactNode;
}

/**
 * Uniform loading / error / empty handling for queries.
 *
 * If a refetch fails while cached data exists, the data stays visible and
 * an error banner is shown above it instead of blanking the view.
 */
export function QueryState<T>({
  query,
  loadingText = 'Lade Daten…',
  loading,
  isEmpty,
  emptyText = 'Keine Daten vorhanden',
  children,
}: QueryStateProps<T>) {
  const { data, isPending, isError, error, refetch } = query;

  if (data === undefined) {
    if (isError) return <ErrorCard error={error} onRetry={refetch} />;
    if (isPending) {
      return loading ?? (
        <div className="loading-pulse text-dim" style={{ padding: 'var(--space-xl)' }}>
          {loadingText}
        </div>
      );
    }
    return null;
  }

  return (
    <>
      {isError && <ErrorBanner error={error} onRetry={refetch} stale />}
      {isEmpty?.(data) ? <div className="empty-state text-dim">{emptyText}</div> : children(data)}
    </>
  );
}

function authHint(error: unknown): ReactNode {
  if (isApiError(error) && error.isAuthError) {
    return (
      <>
        {' '}<Link to="/settings">Zu den Einstellungen →</Link>
      </>
    );
  }
  return null;
}

export function ErrorCard({ error, onRetry }: { error: unknown; onRetry?: () => unknown }) {
  return (
    <div className="card error-card" role="alert">
      <div className="flex items-center gap-sm">
        <AlertTriangle size={16} />
        <strong>Fehler beim Laden</strong>
      </div>
      <div className="text-sm mt-xs">
        {errorMessage(error)}
        {authHint(error)}
      </div>
      {onRetry && (
        <button className="btn btn-ghost btn-sm mt-md" onClick={() => onRetry()}>
          <RefreshCw size={12} /> Erneut versuchen
        </button>
      )}
    </div>
  );
}

export function ErrorBanner({
  error,
  onRetry,
  stale = false,
}: {
  error: unknown;
  onRetry?: () => unknown;
  stale?: boolean;
}) {
  return (
    <div className="error-banner" role="alert">
      <AlertTriangle size={14} style={{ flexShrink: 0 }} />
      <span>
        {stale ? 'Aktualisierung fehlgeschlagen (zeige zuletzt geladene Daten): ' : ''}
        {errorMessage(error)}
        {authHint(error)}
      </span>
      {onRetry && (
        <button className="btn btn-ghost btn-sm" onClick={() => onRetry()} style={{ marginLeft: 'auto' }}>
          <RefreshCw size={12} /> Erneut
        </button>
      )}
    </div>
  );
}

/** Inline error for failed mutations (shows backend `detail`). */
export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="text-xs text-error mt-xs" role="alert">
      {errorMessage(error)}
      {authHint(error)}
    </div>
  );
}

/** "zeige N von M" notice for truncated server results. */
export function TruncationNotice({ shown, total }: { shown: number; total: number | null | undefined }) {
  if (total == null || total <= shown) return null;
  return (
    <div className="text-xs text-warning mb-md">
      Zeige {shown.toLocaleString('de-DE')} von {total.toLocaleString('de-DE')} Einträgen –
      Zeitraum oder Filter eingrenzen.
    </div>
  );
}
