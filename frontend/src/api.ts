/**
 * API client for the Trading Signals backend.
 *
 * Wraps fetch() with base URL handling, auth headers, error parsing
 * (status + backend `detail`) and JSON response typing.
 */

const API_BASE = '/api/v1';
const API_KEY_STORAGE = 'apiKey';
const TOTAL_COUNT_HEADER = 'X-Total-Count';

export const AUTH_ERROR_MESSAGE =
  'API-Schlüssel fehlt oder ist falsch – in den Einstellungen hinterlegen';

// ── Auth (API key in localStorage) ─────────────────────────────────────

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(API_KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null): void {
  if (key && key.trim()) localStorage.setItem(API_KEY_STORAGE, key.trim());
  else localStorage.removeItem(API_KEY_STORAGE);
}

// ── Errors ─────────────────────────────────────────────────────────────

/** Error thrown for non-2xx responses; carries HTTP status and backend detail. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(status: number, message: string, detail: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/** Human-readable (German) message for any thrown value. */
export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

function parseDetail(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return detail
      .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
      .join('; ');
  }
  return detail == null ? null : JSON.stringify(detail);
}

// ── Core request ───────────────────────────────────────────────────────

async function rawRequest(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  if (options.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  headers.set('X-Requested-With', 'XMLHttpRequest');
  const key = getApiKey();
  if (key) headers.set('X-API-Key', key);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
  } catch {
    throw new ApiError(0, 'Backend nicht erreichbar – läuft der Server?');
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = parseDetail(body);
    const status = response.status;
    let message: string;
    if (status === 401 || status === 403) {
      message = detail ? `${AUTH_ERROR_MESSAGE} (${detail})` : AUTH_ERROR_MESSAGE;
    } else {
      message = detail ?? `API-Fehler ${status}`;
    }
    throw new ApiError(status, message, detail);
  }
  return response;
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await rawRequest(endpoint, options);
  // Handle empty responses (204, etc.)
  if (response.status === 204) return {} as T;
  return response.json() as Promise<T>;
}

/** List response with the uncapped total from the `X-Total-Count` header. */
export interface ListResult<T> {
  items: T[];
  /** Total matching rows on the server; null if the endpoint doesn't report it. */
  total: number | null;
}

async function requestList<T>(endpoint: string): Promise<ListResult<T>> {
  const response = await rawRequest(endpoint);
  const items = (await response.json()) as T[];
  const header = response.headers.get(TOTAL_COUNT_HEADER);
  const total = header != null && header !== '' ? Number(header) : null;
  return { items, total: Number.isFinite(total) ? total : null };
}

type QueryValue = string | number | boolean | null | undefined;

function qs(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') search.set(k, String(v));
  });
  const s = search.toString();
  return s ? `?${s}` : '';
}

const seg = (s: string) => encodeURIComponent(s);

// ── Shared literal types ───────────────────────────────────────────────

export type Period = '1m' | '3m' | '6m' | '1y' | '5y' | 'all';
export type DeltaType = 'new_position' | 'closed' | 'increased' | 'decreased' | 'unchanged';
export type ArkDirection = 'increased' | 'decreased' | 'mixed';
export type DataQualityStatus = 'complete' | 'partial' | 'missing';
export type SentimentSort = 'most_negative' | 'most_positive' | 'most_articles';

// ── Dashboard ─────────────────────────────────────────────────────────

export interface CollectorStatus {
  id: string;
  name: string;
  last_run: string | null;
  last_status: string | null;
  records_written: number | null;
  next_run: string | null;
  is_running: boolean;
  /** Paused nightly-chain step: runs inside `nightly_chain`, `next_run` is null. */
  via_chain: boolean;
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
  added_date: string | null;
  added_by: string | null;
  index_membership: string[];
  last_price: number | null;
  last_price_date: string | null;
  price_change_pct: number | null;
}

export type TickerDetail = TickerSummary;

export interface UniverseResponse {
  tickers: TickerSummary[];
  total: number;
  page: number;
  limit: number;
}

export interface UniverseParams {
  page?: number;
  limit?: number;
  search?: string;
  sector?: string;
  index?: string;
  active?: 'true' | 'false' | '';
}

export const fetchUniverse = (params: UniverseParams) =>
  request<UniverseResponse>(`/universe${qs({ ...params })}`);

export const fetchSectors = () =>
  request<string[]>('/universe/sectors');

export const fetchTickerDetail = (ticker: string) =>
  request<TickerDetail>(`/universe/${seg(ticker)}`);

// ── Signals ───────────────────────────────────────────────────────────

export interface ARKDelta {
  delta_date: string;
  etf_ticker: string;
  ticker: string;
  delta_type: DeltaType;
  shares_delta: number | null;
  shares_prev: number | null;
  shares_curr: number | null;
  weight_delta: number | null;
  weight_prev: number | null;
  weight_curr: number | null;
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
  delay_days: number | null;
}

export interface AnalystRating {
  ticker: string;
  firm: string | null;
  rating_date: string | null;
  rating_new: string | null;
  rating_old: string | null;
  action: string | null;
  /** Price target published by this firm with the rating change. */
  firm_target_new: number | null;
  firm_target_old: number | null;
  /** Consensus median target (latest fundamentals snapshot) – context only. */
  consensus_target: number | null;
}

export interface ARKSummary {
  ticker: string;
  total_shares_delta: number;
  total_weight_delta_bps: number;
  n_etfs: number;
  n_days: number;
  etfs: string[];
  direction: ArkDirection;
  first_date: string;
  last_date: string;
}

export interface SignalListParams {
  days: number;
  ticker?: string | null;
  limit?: number;
}

export const fetchArkDeltas = ({ days, ticker, limit = 500 }: SignalListParams) =>
  requestList<ARKDelta>(`/signals/ark${qs({ days, ticker, limit })}`);

export const fetchArkSummary = ({ days, ticker }: SignalListParams) =>
  request<ARKSummary[]>(`/signals/ark/summary${qs({ days, ticker })}`);

export const fetchInsiderClusters = ({ days, ticker, limit = 200 }: SignalListParams) =>
  requestList<InsiderCluster>(`/signals/insider${qs({ days, ticker, limit })}`);

export const fetchPoliticianTrades = ({ days, ticker, limit = 500 }: SignalListParams) =>
  requestList<PoliticianTrade>(`/signals/politicians${qs({ days, ticker, limit })}`);

export const fetchAnalystRatings = ({ days, ticker, limit = 500 }: SignalListParams) =>
  requestList<AnalystRating>(`/signals/ratings${qs({ days, ticker, limit })}`);

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
  ema_12: number | null;
  ema_26: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  bollinger_upper: number | null;
  bollinger_lower: number | null;
  atr_14: number | null;
  volume_sma_20: number | null;
  relative_strength_spy: number | null;
}

export interface FundamentalsData {
  snapshot_date: string | null;
  market_cap: number | null;
  pe_ratio: number | null;
  forward_pe: number | null;
  ps_ratio: number | null;
  pb_ratio: number | null;
  ev_ebitda: number | null;
  profit_margin: number | null;
  operating_margin: number | null;
  return_on_equity: number | null;
  revenue_growth_yoy: number | null;
  eps_ttm: number | null;
  debt_to_equity: number | null;
  dividend_yield: number | null;
  beta: number | null;
}

export interface TickerSignalCounts {
  ark_deltas: number;
  insider_clusters: number;
  politician_trades: number;
  analyst_ratings: number;
}

/** Mirrors backend `TickerSignals` (lists capped by `limit`, counts uncapped). */
export interface TickerSignals {
  ticker: string;
  days: number;
  ark_deltas: ARKDelta[];
  insider_clusters: InsiderCluster[];
  politician_trades: PoliticianTrade[];
  analyst_ratings: AnalystRating[];
  counts: TickerSignalCounts;
}

export const PERIOD_DAYS: Record<Period, number> = {
  '1m': 30, '3m': 90, '6m': 180, '1y': 365, '5y': 1825, all: 3650,
};

export const fetchPrices = (symbol: string, period: Period = '3m') =>
  request<PricePoint[]>(`/ticker/${seg(symbol)}/prices${qs({ period })}`);

export const fetchIndicators = (symbol: string, period: Period = '3m') =>
  request<IndicatorPoint[]>(`/ticker/${seg(symbol)}/indicators${qs({ period })}`);

export const fetchFundamentals = (symbol: string) =>
  request<FundamentalsData | null>(`/ticker/${seg(symbol)}/fundamentals`);

export const fetchTickerSignals = (symbol: string, days = 30, limit = 50) =>
  request<TickerSignals>(`/ticker/${seg(symbol)}/signals${qs({ days, limit })}`);

// ── Data Quality ──────────────────────────────────────────────────────

export interface DataQualityDimension {
  label: string;
  status: DataQualityStatus;
  summary: string;
  detail: string | null;
}

export interface TickerDataQuality {
  ticker: string;
  dimensions: DataQualityDimension[];
  overall_completeness: number;
}

export const fetchDataQuality = (symbol: string) =>
  request<TickerDataQuality>(`/ticker/${seg(symbol)}/data-quality`);

// ── Operations ────────────────────────────────────────────────────────

export interface SchedulerJob {
  id: string;
  name: string;
  trigger: string;
  next_run: string | null;
  pending: boolean;
  is_running: boolean;
  /** Paused nightly-chain step: runs inside `nightly_chain`, `next_run` is null. */
  via_chain: boolean;
}

export interface BackfillStatus {
  task_id: string;
  operation: string;
  /** 'idle' | 'running' | 'completed' | 'partial' | 'failed' */
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

/** Rejects if the backend reports `success: false`, so the UI shows its message. */
export async function ensureSuccess(p: Promise<TriggerResponse>): Promise<TriggerResponse> {
  const res = await p;
  if (!res.success) throw new Error(res.message || 'Aktion fehlgeschlagen');
  return res;
}

export const fetchSchedulerJobs = () =>
  request<SchedulerJob[]>('/ops/scheduler');

export const triggerJob = (jobId: string) =>
  request<TriggerResponse>(`/ops/scheduler/${seg(jobId)}/trigger`, { method: 'POST' });

/** Without `startDate` the backend uses its default lookback window. */
export const startPriceBackfill = (startDate?: string) =>
  request<TriggerResponse>(`/ops/backfill/prices${qs({ start_date: startDate })}`, { method: 'POST' });

export const startIndicatorBackfill = () =>
  request<TriggerResponse>('/ops/backfill/indicators', { method: 'POST' });

export const fetchBackfillStatus = () =>
  request<BackfillStatus[]>('/ops/backfill/status');

export const startSectorEnrichment = () =>
  request<TriggerResponse>('/ops/backfill/sectors', { method: 'POST' });

export const fetchDbStats = () =>
  request<DbTableInfo[]>('/ops/db/stats');

export const runVacuum = () =>
  request<TriggerResponse>('/ops/db/vacuum', { method: 'POST' });

/** Destructive – only call after explicit UI confirmation. */
export const resetDatabase = () =>
  request<TriggerResponse>(`/ops/db/reset${qs({ confirm: 'RESET' })}`, { method: 'POST' });

// ── Logs ──────────────────────────────────────────────────────────────

export interface LogLine {
  level: string;
  ts: string;
  msg: string;
}

export interface CollectionLogItem {
  id: number;
  collector_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  status: string | null;
  records_fetched: number | null;
  records_written: number | null;
  gaps_detected: number;
  gaps_repaired: number;
  gaps_extrapolated: number;
  errors: Record<string, unknown> | null;
  notes: string | null;
  log_lines: LogLine[] | null;
  duration_seconds: number | null;
}

export interface LogsResponse {
  logs: CollectionLogItem[];
  total: number;
  page: number;
  limit: number;
}

export interface LogsParams {
  page?: number;
  limit?: number;
  collector?: string;
  status?: string;
}

export const fetchLogs = (params: LogsParams = {}) =>
  request<LogsResponse>(`/logs${qs({ ...params })}`);

export const fetchCollectorNames = () =>
  request<string[]>('/logs/collectors');

// ── Features ─────────────────────────────────────────────────────────

export interface FeatureStats {
  last_snapshot_date: string | null;
  ticker_count: number;
  feature_coverage_pct: number;
  target_backfill_pct: number;
  total_snapshots: number;
}

export interface FeatureGroupMeta {
  key: string;
  label: string;
  total: number;
  market_wide: boolean;
}

export interface FeatureCoverageItem {
  ticker: string;
  /** FeatureGroupMeta.key → filled column count */
  counts: Record<string, number>;
  total_filled: number;
  total_possible: number;
}

export interface FeatureCoverageResponse {
  snapshot_date: string | null;
  groups: FeatureGroupMeta[];
  total_possible: number;
  items: FeatureCoverageItem[];
  ticker_count: number;
}

export interface SignalConvergenceItem {
  ticker: string;
  active_sources: number;
  source_names: string[];
  ark_conviction_score: number | null;
  insider_cluster_score: number | null;
  analyst_rating_score: number | null;
  rsi_14: number | null;
  sentiment_avg_7d: number | null;
}

export interface SignalConvergenceResponse {
  snapshot_date: string | null;
  /** Number of ticker-specific groups that can count as a source. */
  max_sources: number;
  /** Market-wide groups that never count (e.g. Macro, Breadth). */
  excluded_groups: string[];
  /** Tickers with ≥1 active source (before limit). */
  total: number;
  items: SignalConvergenceItem[];
}

export interface HorizonStats {
  horizon: string;
  filled_count: number;
  total_count: number;
  filled_pct: number;
  mean: number | null;
  median: number | null;
  std: number | null;
  min_val: number | null;
  max_val: number | null;
}

export interface ReturnStatsResponse {
  horizons: HorizonStats[];
  total_snapshots: number;
}

export interface FeatureGroupDetail {
  group: string;
  key: string;
  market_wide: boolean;
  features: Record<string, number | boolean | null>;
  filled: number;
  total: number;
}

export interface TickerFeatureDetail {
  ticker: string;
  snapshot_date: string | null;
  groups: FeatureGroupDetail[];
  total_filled: number;
  total_possible: number;
  return_1d: number | null;
  return_5d: number | null;
  return_20d: number | null;
  return_60d: number | null;
}

export const fetchFeatureStats = () =>
  request<FeatureStats>('/dashboard/feature-stats');

export const fetchFeatureCoverage = () =>
  request<FeatureCoverageResponse>('/features/coverage');

export const fetchSignalConvergence = (limit = 50) =>
  request<SignalConvergenceResponse>(`/features/convergence${qs({ limit })}`);

export const fetchReturnStats = () =>
  request<ReturnStatsResponse>('/features/returns');

export const fetchTickerFeatures = (symbol: string) =>
  request<TickerFeatureDetail>(`/features/ticker/${seg(symbol)}`);

// ── Sentiment ────────────────────────────────────────────────────────

export interface SentimentSummary {
  ticker: string;
  avg_sentiment: number | null;
  article_count: number;
  negative_count: number;
  positive_count: number;
  neutral_count: number;
  neg_pct: number;
  latest_headline: string | null;
  latest_sentiment_label: string | null;
  latest_date: string | null;
}

export interface SentimentArticle {
  article_id: string;
  headline: string;
  source: string | null;
  published_at: string | null;
  ticker: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  url: string | null;
}

export const fetchSentimentSummary = (
  { days, ticker, limit = 500 }: SignalListParams,
  sort: SentimentSort = 'most_negative',
) =>
  requestList<SentimentSummary>(`/signals/sentiment/summary${qs({ days, ticker, sort, limit })}`);

export const fetchSentimentArticles = ({ days, ticker, limit = 500 }: SignalListParams) =>
  requestList<SentimentArticle>(`/signals/sentiment/articles${qs({ days, ticker, limit })}`);
