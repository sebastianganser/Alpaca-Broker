/**
 * Central query keys and query options (TanStack Query v5).
 *
 * Keeping keys in one place guarantees that invalidation after mutations
 * hits every consumer.
 */

import { keepPreviousData, queryOptions } from '@tanstack/react-query';
import {
  fetchAnalystRatings,
  fetchArkDeltas,
  fetchArkSummary,
  fetchBackfillStatus,
  fetchCollectorNames,
  fetchDashboard,
  fetchDataQuality,
  fetchDbStats,
  fetchFeatureCoverage,
  fetchFeatureStats,
  fetchFundamentals,
  fetchIndicators,
  fetchInsiderClusters,
  fetchLogs,
  fetchPoliticianTrades,
  fetchPrices,
  fetchReturnStats,
  fetchSchedulerJobs,
  fetchSectors,
  fetchSentimentArticles,
  fetchSentimentSummary,
  fetchSignalConvergence,
  fetchTickerDetail,
  fetchTickerFeatures,
  fetchTickerSignals,
  fetchUniverse,
  type LogsParams,
  type Period,
  type SentimentSort,
  type SignalListParams,
  type UniverseParams,
} from './api';

export const queryKeys = {
  dashboard: ['dashboard'] as const,
  featureStats: ['feature-stats'] as const,
  featureCoverage: ['feature-coverage'] as const,
  signalConvergence: (limit: number) => ['signal-convergence', limit] as const,
  returnStats: ['return-stats'] as const,
  tickerFeatures: (symbol: string) => ['ticker-features', symbol] as const,
  universe: (params: UniverseParams) => ['universe', params] as const,
  universeCount: ['universe', 'count'] as const,
  sectors: ['sectors'] as const,
  tickerDetail: (symbol: string) => ['ticker-detail', symbol] as const,
  tickerPrices: (symbol: string, period: Period) => ['ticker-prices', symbol, period] as const,
  tickerIndicators: (symbol: string, period: Period) => ['ticker-indicators', symbol, period] as const,
  tickerFundamentals: (symbol: string) => ['ticker-fundamentals', symbol] as const,
  tickerSignals: (symbol: string, days: number, limit: number) =>
    ['ticker-signals', symbol, days, limit] as const,
  tickerDataQuality: (symbol: string) => ['ticker-data-quality', symbol] as const,
  signals: (kind: string, params: SignalListParams, extra?: string) =>
    ['signals', kind, params.days, params.ticker ?? null, extra ?? null] as const,
  schedulerJobs: ['scheduler-jobs'] as const,
  backfillStatus: ['backfill-status'] as const,
  dbStats: ['db-stats'] as const,
  logs: (params: LogsParams) => ['logs', params] as const,
  collectorNames: ['collector-names'] as const,
};

// ── Dashboard / Features ──────────────────────────────────────────────

export const dashboardQuery = () =>
  queryOptions({
    queryKey: queryKeys.dashboard,
    queryFn: fetchDashboard,
    refetchInterval: 30_000,
  });

export const featureStatsQuery = () =>
  queryOptions({
    queryKey: queryKeys.featureStats,
    queryFn: fetchFeatureStats,
    refetchInterval: 60_000,
  });

export const featureCoverageQuery = () =>
  queryOptions({
    queryKey: queryKeys.featureCoverage,
    queryFn: fetchFeatureCoverage,
    staleTime: 60_000,
  });

export const signalConvergenceQuery = (limit: number) =>
  queryOptions({
    queryKey: queryKeys.signalConvergence(limit),
    queryFn: () => fetchSignalConvergence(limit),
    staleTime: 60_000,
  });

export const returnStatsQuery = () =>
  queryOptions({
    queryKey: queryKeys.returnStats,
    queryFn: fetchReturnStats,
    staleTime: 60_000,
  });

export const tickerFeaturesQuery = (symbol: string) =>
  queryOptions({
    queryKey: queryKeys.tickerFeatures(symbol),
    queryFn: () => fetchTickerFeatures(symbol),
  });

// ── Universe / Ticker ─────────────────────────────────────────────────

export const universeQuery = (params: UniverseParams) =>
  queryOptions({
    queryKey: queryKeys.universe(params),
    queryFn: () => fetchUniverse(params),
    placeholderData: keepPreviousData,
  });

/** Number of active tickers (cheap: limit=1, only `total` is used). */
export const universeCountQuery = () =>
  queryOptions({
    queryKey: queryKeys.universeCount,
    queryFn: () => fetchUniverse({ limit: 1 }),
    select: (data) => data.total,
  });

export const sectorsQuery = () =>
  queryOptions({ queryKey: queryKeys.sectors, queryFn: fetchSectors, staleTime: 300_000 });

export const tickerDetailQuery = (symbol: string) =>
  queryOptions({
    queryKey: queryKeys.tickerDetail(symbol),
    queryFn: () => fetchTickerDetail(symbol),
  });

export const tickerPricesQuery = (symbol: string, period: Period) =>
  queryOptions({
    queryKey: queryKeys.tickerPrices(symbol, period),
    queryFn: () => fetchPrices(symbol, period),
    placeholderData: keepPreviousData,
  });

export const tickerIndicatorsQuery = (symbol: string, period: Period) =>
  queryOptions({
    queryKey: queryKeys.tickerIndicators(symbol, period),
    queryFn: () => fetchIndicators(symbol, period),
    placeholderData: keepPreviousData,
  });

export const tickerFundamentalsQuery = (symbol: string) =>
  queryOptions({
    queryKey: queryKeys.tickerFundamentals(symbol),
    queryFn: () => fetchFundamentals(symbol),
  });

export const tickerSignalsQuery = (symbol: string, days: number, limit = 200) =>
  queryOptions({
    queryKey: queryKeys.tickerSignals(symbol, days, limit),
    queryFn: () => fetchTickerSignals(symbol, days, limit),
    placeholderData: keepPreviousData,
  });

export const tickerDataQualityQuery = (symbol: string) =>
  queryOptions({
    queryKey: queryKeys.tickerDataQuality(symbol),
    queryFn: () => fetchDataQuality(symbol),
  });

// ── Signals ───────────────────────────────────────────────────────────

export const arkSummaryQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('ark-summary', params),
    queryFn: () => fetchArkSummary(params),
  });

export const arkDeltasQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('ark', params),
    queryFn: () => fetchArkDeltas(params),
  });

export const insiderQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('insider', params),
    queryFn: () => fetchInsiderClusters(params),
  });

export const politicianQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('politicians', params),
    queryFn: () => fetchPoliticianTrades(params),
  });

export const ratingsQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('ratings', params),
    queryFn: () => fetchAnalystRatings(params),
  });

export const sentimentSummaryQuery = (params: SignalListParams, sort: SentimentSort) =>
  queryOptions({
    queryKey: queryKeys.signals('sentiment-summary', params, sort),
    queryFn: () => fetchSentimentSummary(params, sort),
  });

export const sentimentArticlesQuery = (params: SignalListParams) =>
  queryOptions({
    queryKey: queryKeys.signals('sentiment-articles', params),
    queryFn: () => fetchSentimentArticles(params),
  });

// ── Operations / Logs ─────────────────────────────────────────────────

export const schedulerJobsQuery = () =>
  queryOptions({
    queryKey: queryKeys.schedulerJobs,
    queryFn: fetchSchedulerJobs,
    refetchInterval: 5_000,
  });

export const backfillStatusQuery = () =>
  queryOptions({
    queryKey: queryKeys.backfillStatus,
    queryFn: fetchBackfillStatus,
    refetchInterval: 3_000,
  });

export const dbStatsQuery = () =>
  queryOptions({ queryKey: queryKeys.dbStats, queryFn: fetchDbStats });

export const logsQuery = (params: LogsParams) =>
  queryOptions({
    queryKey: queryKeys.logs(params),
    queryFn: () => fetchLogs(params),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });

export const collectorNamesQuery = () =>
  queryOptions({ queryKey: queryKeys.collectorNames, queryFn: fetchCollectorNames });
