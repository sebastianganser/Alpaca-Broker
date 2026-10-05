import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, KeyRound } from 'lucide-react';
import { getApiKey, setApiKey } from '../../api';

/**
 * Stores the API key in localStorage; api.ts sends it as `X-API-Key`.
 * All queries are refetched after saving so auth errors clear immediately.
 */
export function ApiKeyCard() {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(() => getApiKey() ?? '');
  const [visible, setVisible] = useState(false);
  const [saved, setSaved] = useState<'saved' | 'cleared' | null>(null);
  const hasKey = !!getApiKey();

  const save = (key: string | null) => {
    setApiKey(key);
    setSaved(key && key.trim() ? 'saved' : 'cleared');
    if (!key) setValue('');
    void queryClient.invalidateQueries();
  };

  return (
    <div>
      <div className="label mb-md">Zugang</div>
      <div className="card">
        <div className="flex items-center gap-sm mb-md">
          <KeyRound size={18} style={{ color: 'var(--primary)' }} />
          <div style={{ fontWeight: 600 }}>API-Schlüssel</div>
          <span className={`badge ${hasKey ? 'badge-success' : 'badge-neutral'}`} style={{ marginLeft: 'auto' }}>
            {hasKey ? 'hinterlegt' : 'nicht gesetzt'}
          </span>
        </div>
        <div className="text-xs text-dim mb-md">
          Wird nur lokal in diesem Browser gespeichert und bei jeder Anfrage als <code>X-API-Key</code> gesendet.
        </div>
        <form
          className="flex gap-sm items-center"
          onSubmit={(e) => {
            e.preventDefault();
            save(value);
          }}
        >
          <input
            className="input"
            type={visible ? 'text' : 'password'}
            autoComplete="off"
            spellCheck={false}
            placeholder="API-Schlüssel eingeben"
            aria-label="API-Schlüssel"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setSaved(null);
            }}
            style={{ maxWidth: 360 }}
          />
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setVisible(!visible)}
            aria-label={visible ? 'Schlüssel verbergen' : 'Schlüssel anzeigen'}
          >
            {visible ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={!value.trim()}>
            Speichern
          </button>
          {hasKey && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => save(null)}>
              Entfernen
            </button>
          )}
        </form>
        {saved && (
          <div className="text-xs text-dim mt-sm" role="status">
            {saved === 'saved' ? 'Gespeichert – Daten werden neu geladen.' : 'Schlüssel entfernt.'}
          </div>
        )}
      </div>
    </div>
  );
}
