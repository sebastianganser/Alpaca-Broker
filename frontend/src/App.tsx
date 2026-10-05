import { lazy } from 'react';
import { BrowserRouter, Link, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { isApiError } from './api';
import Layout from './Layout';

// Route-level code splitting keeps recharts etc. out of the initial bundle.
const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const UniversePage = lazy(() => import('./pages/UniversePage'));
const SignalsPage = lazy(() => import('./pages/SignalsPage'));
const FeaturesPage = lazy(() => import('./pages/FeaturesPage'));
const LogsPage = lazy(() => import('./pages/LogsPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const TickerPage = lazy(() => import('./pages/TickerPage'));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Client errors (auth, not found, validation) won't succeed on retry.
      retry: (failureCount, error) =>
        !(isApiError(error) && error.status >= 400 && error.status < 500) && failureCount < 1,
      refetchOnWindowFocus: false,
    },
  },
});

function NotFoundPage() {
  return (
    <div className="fade-in">
      <div className="page-header"><h2>Seite nicht gefunden</h2></div>
      <div className="card empty-state">
        Diese Seite existiert nicht. <Link to="/">Zum Dashboard →</Link>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/universe" element={<UniversePage />} />
            <Route path="/signals" element={<SignalsPage />} />
            <Route path="/features" element={<FeaturesPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/ticker/:symbol" element={<TickerPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
