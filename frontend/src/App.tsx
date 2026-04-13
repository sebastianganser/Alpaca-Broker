import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './Layout';
import DashboardPage from './pages/DashboardPage';
import UniversePage from './pages/UniversePage';
import SignalsPage from './pages/SignalsPage';
import SettingsPage from './pages/SettingsPage';
import TickerPage from './pages/TickerPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/universe" element={<UniversePage />} />
            <Route path="/signals" element={<SignalsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/ticker/:symbol" element={<TickerPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
