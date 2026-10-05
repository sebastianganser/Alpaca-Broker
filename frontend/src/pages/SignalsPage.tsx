import { useSearchParams } from 'react-router-dom';
import { X } from 'lucide-react';
import ArkTab from './signals/ArkTab';
import InsiderTab from './signals/InsiderTab';
import PoliticianTab from './signals/PoliticianTab';
import RatingsTab from './signals/RatingsTab';
import SentimentTab from './signals/SentimentTab';

const TABS = [
  { id: 'ark', label: 'ARK' },
  { id: 'insider', label: 'Insider' },
  { id: 'politicians', label: 'Politiker' },
  { id: 'ratings', label: 'Analyst' },
  { id: 'sentiment', label: 'Sentiment' },
] as const;

type Tab = (typeof TABS)[number]['id'];

function isTab(v: string | null): v is Tab {
  return TABS.some((t) => t.id === v);
}

/**
 * Signals overview. Tab and ticker filter live in the URL
 * (`?tab=insider&ticker=AAPL`) so views are linkable from the ticker page.
 * The ticker filter is applied server-side (exact match).
 */
export default function SignalsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get('tab');
  const activeTab: Tab = isTab(rawTab) ? rawTab : 'ark';
  const ticker = searchParams.get('ticker')?.trim().toUpperCase() || null;

  const update = (changes: Record<string, string | null>) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        Object.entries(changes).forEach(([k, v]) => {
          if (v) next.set(k, v);
          else next.delete(k);
        });
        return next;
      },
      { replace: true },
    );
  };

  const clearTicker = () => update({ ticker: null });
  const tabProps = { ticker, clearTicker };

  return (
    <div className="fade-in">
      <div className="page-header flex items-center gap-md">
        <h2>Signals</h2>
        {ticker && (
          <span className="chip">
            Ticker: <span className="mono">{ticker}</span>
            <button
              type="button"
              className="chip-remove"
              onClick={clearTicker}
              aria-label="Ticker-Filter entfernen"
              title="Ticker-Filter entfernen"
            >
              <X size={12} />
            </button>
          </span>
        )}
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => update({ tab: tab.id })}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'ark' && <ArkTab {...tabProps} />}
      {activeTab === 'insider' && <InsiderTab {...tabProps} />}
      {activeTab === 'politicians' && <PoliticianTab {...tabProps} />}
      {activeTab === 'ratings' && <RatingsTab {...tabProps} />}
      {activeTab === 'sentiment' && <SentimentTab {...tabProps} />}
    </div>
  );
}
