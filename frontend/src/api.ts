/**
 * API client for the Trading Signals backend.
 *
 * Wraps fetch() with base URL handling, error parsing,
 * and JSON response typing.
 */

const API_BASE = '/api/v1';

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `API error: ${response.status}`);
  }

  // Handle empty responses (204, etc.)
  if (response.status === 204) return {} as T;

  return response.json();
}

// ── Dashboard ─────────────────────────────────────────────────────────

export interface CollectorStatus {
  id: string;
  name: string;
  last_run: string | null;
  last_status: string | null;
  records_written: number | null;
  next_run: string | null;
  is_running: boolean;
}

export interface TableStats {
  table: string;
  row_count: number;
  min_date: string | null;
  max_date: string | null;
}

export interface SystemHealth {
  db_connected: boolean;
  alembic_revision: string | null;
  scheduler_running: boolean;
  job_count: number;
  uptime_seconds: number | null;
}

export interface DashboardSummary {
  collectors: CollectorStatus[];
  table_stats: TableStats[];
  system_health: SystemHealth;
}

export const fetchDashboard = () =>
  request<DashboardSummary>('/dashboard/summary');

// ── Universe ──────────────────────────────────────────────────────────

export interface TickerSummary {
  ticker: string;
  company_name: string | null;
  exchange: string | null;
  sector: string | null;
  industry: string | null;
  is_active: boolean;
  added_by: string | null;
  index_membership: string[];
  last_price: number | null;
  last_price_date: string | null;
  price_change_pct?: number | null;
}

export interface UniverseResponse {
  tickers: TickerSummary[];
  total: number;
  page: number;
  limit: number;
}

export const fetchUniverse = (params: Record<string, string | number>) => {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  });
  return request<UniverseResponse>(`/universe?${qs.toString()}`);
};

export const fetchSectors = () =>
  request<string[]>('/universe/sectors');

export const fetchTickerDetail = (ticker: string) =>
  request<TickerSummary>(`/universe/${ticker}`);

// ── Signals ───────────────────────────────────────────────────────────

export interface ARKDelta {
  delta_date: string;
  etf_ticker: string;
  ticker: string;
  shares_delta: number | null;
  weight_delta_bps: number | null;
  pct_change: number | null;
  is_new_position: boolean;
  is_closed_position: boolean;
}

export interface InsiderCluster {
  ticker: string;
  cluster_start: string;
  cluster_end: string;
  n_insiders: number;
  n_buys: number;
  n_sells: number;
  total_buy_value: number | null;
  cluster_score: number | null;
}

export interface PoliticianTrade {
  politician_name: string;
  party: string | null;
  ticker: string | null;
  transaction_date: string | null;
  disclosure_date: string | null;
  transaction_type: string | null;
  amount_range: string | null;
}

export interface AnalystRating {
  ticker: string;
  firm: string | null;
  rating_date: string | null;
  rating_new: string | null;
  rating_old: string | null;
  action: string | null;
  price_target_new: number | null;
  price_target_old: number | null;
}

export const fetchArkDeltas = (days = 7) =>
  request<ARKDelta[]>(`/signals/ark?days=${days}`);

export const fetchInsiderClusters = (days = 30) =>
  request<InsiderCluster[]>(`/signals/insider?days=${days}`);

export const fetchPoliticianTrades = (days = 30) =>
  request<PoliticianTrade[]>(`/signals/politicians?days=${days}`);

export const fetchAnalystRatings = (days = 7) =>
  request<AnalystRating[]>(`/signals/ratings?days=${days}`);

// ── Ticker Detail ─────────────────────────────────────────────────────

export interface PricePoint {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

export interface IndicatorPoint {
  trade_date: string;
  sma_20: number | null;
  sma_50: number | null;
  sma_200: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  bollinger_upper: number | null;
  bollinger_lower: number | null;
  atr_14: number | null;
}

export interface FundamentalsData {
  snapshot_date: string | null;
  market_cap: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  ps_ratio: number | null;
  pb_ratio: number | null;
  profit_margin: number | null;
  operating_margin: number | null;
  revenue_growth_yoy: number | null;
  eps_ttm: number | null;
  dividend_yield: number | null;
  beta: number | null;
}

export const fetchPrices = (symbol: string, period = '3m') =>
  request<PricePoint[]>(`/ticker/${symbol}/prices?period=${period}`);

export const fetchIndicators = (symbol: string, period = '3m') =>
  request<IndicatorPoint[]>(`/ticker/${symbol}/indicators?period=${period}`);

export const fetchFundamentals = (symbol: string) =>
  request<FundamentalsData | null>(`/ticker/${symbol}/fundamentals`);

export const fetchTickerSignals = (symbol: string, days = 30) =>
  request<{
    ark_deltas: ARKDelta[];
    insider_clusters: InsiderCluster[];
    politician_trades: PoliticianTrade[];
    analyst_ratings: AnalystRating[];
  }>(`/ticker/${symbol}/signals?days=${days}`);

// ── Data Quality ──────────────────────────────────────────────────────

export interface DataQualityDimension {
  label: string;
  status: 'complete' | 'partial' | 'missing';
  summary: string;
  detail: string | null;
}

export interface TickerDataQuality {
  ticker: string;
  dimensions: DataQualityDimension[];
  overall_completeness: number;
}

export const fetchDataQuality = (symbol: string) =>
  request<TickerDataQuality>(`/ticker/${symbol}/data-quality`);

// ── Operations ────────────────────────────────────────────────────────

export interface SchedulerJob {
  id: string;
  name: string;
  trigger: string;
  next_run: string | null;
  pending: boolean;
  is_running: boolean;
}

export interface BackfillStatus {
  task_id: string;
  operation: string;
  status: string;
  progress_pct: number;
  current_ticker: string | null;
  started_at: string | null;
  eta_seconds: number | null;
  error: string | null;
}

export interface DbTableInfo {
  table_name: string;
  row_count: number;
  size_bytes: number | null;
  size_human: string | null;
}

export interface TriggerResponse {
  success: boolean;
  message: string;
  task_id: string | null;
}

export const fetchSchedulerJobs = () =>
  request<SchedulerJob[]>('/ops/scheduler');

export const triggerJob = (jobId: string) =>
  request<TriggerResponse>(`/ops/scheduler/${jobId}/trigger`, { method: 'POST' });

export const startPriceBackfill = (startDate = '2021-01-01') =>
  request<TriggerResponse>(`/ops/backfill/prices?start_date=${startDate}`, { method: 'POST' });

export const startIndicatorBackfill = () =>
  request<TriggerResponse>('/ops/backfill/indicators', { method: 'POST' });

export const fetchBackfillStatus = () =>
  request<BackfillStatus[]>('/ops/backfill/status');

export const fetchDbStats = () =>
  request<DbTableInfo[]>('/ops/db/stats');

export const runVacuum = () =>
  request<TriggerResponse>('/ops/db/vacuum', { method: 'POST' });

export const resetDatabase = () =>
  request<TriggerResponse>('/ops/db/reset', { method: 'POST' });
