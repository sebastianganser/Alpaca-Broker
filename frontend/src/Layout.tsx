import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  Globe,
  Activity,
  Settings,
  LockOpen,
  Lock,
} from 'lucide-react';
import { useEffect, useState } from 'react';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/universe', icon: Globe, label: 'Universe' },
  { to: '/signals', icon: Activity, label: 'Signals' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

// Trading hours in local time for each exchange
const worldClockZones = [
  { label: 'Frankfurt', tz: 'Europe/Berlin', flag: '🇩🇪', openH: 9, openM: 0, closeH: 17, closeM: 30 },
  { label: 'New York', tz: 'America/New_York', flag: '🇺🇸', openH: 9, openM: 30, closeH: 16, closeM: 0 },
  { label: 'London', tz: 'Europe/London', flag: '🇬🇧', openH: 8, openM: 0, closeH: 16, closeM: 30 },
  { label: 'Tokyo', tz: 'Asia/Tokyo', flag: '🇯🇵', openH: 9, openM: 0, closeH: 15, closeM: 0 },
];

function isExchangeOpen(
  now: Date,
  tz: string,
  openH: number, openM: number,
  closeH: number, closeM: number,
): boolean {
  // Parse weekday
  const dayMatch = now.toLocaleDateString('en-US', { timeZone: tz, weekday: 'short' });
  if (['Sat', 'Sun'].includes(dayMatch)) return false;

  // Parse hour and minute
  const timeStr = now.toLocaleTimeString('en-US', {
    timeZone: tz,
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
  });
  const [h, m] = timeStr.split(':').map(Number);

  const nowMinutes = h * 60 + m;
  const openMinutes = openH * 60 + openM;
  const closeMinutes = closeH * 60 + closeM;

  return nowMinutes >= openMinutes && nowMinutes < closeMinutes;
}

function WorldClock() {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (tz: string) =>
    now.toLocaleTimeString('de-DE', {
      timeZone: tz,
      hour: '2-digit',
      minute: '2-digit',
    });

  const formatDay = (tz: string) => {
    const day = now.toLocaleDateString('en-US', { timeZone: tz, weekday: 'short' });
    return day.toUpperCase();
  };

  return (
    <div className="world-clock">
      {worldClockZones.map(({ label, tz, flag, openH, openM, closeH, closeM }) => {
        const open = isExchangeOpen(now, tz, openH, openM, closeH, closeM);
        return (
          <div key={tz} className="world-clock-row">
            <span className="world-clock-label">
              <span className="world-clock-flag">{flag}</span>
              {label}
            </span>
            <span className="world-clock-time">
              <span className="world-clock-day">{formatDay(tz)}</span>
              {formatTime(tz)}
              {open ? (
                <LockOpen size={10} className="world-clock-status open" />
              ) : (
                <Lock size={10} className="world-clock-status closed" />
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default function Layout() {
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>Alpaca Broker</h1>
          <div className="subtitle">Signal Warehouse</div>
        </div>
        <nav className="sidebar-nav">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `nav-item${isActive ? ' active' : ''}`
              }
            >
              <Icon />
              {label}
            </NavLink>
          ))}
        </nav>
        <WorldClock />
        <div style={{ padding: '0 var(--space-lg)' }}>
          <div className="label-dim" style={{ fontSize: '0.6rem' }}>
            v0.1.0 · Sprint 7
          </div>
        </div>
      </aside>
      <main className="main-content fade-in">
        <Outlet />
      </main>
    </div>
  );
}
