import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  Globe,
  Activity,
  Settings,
} from 'lucide-react';
import { useEffect, useState } from 'react';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/universe', icon: Globe, label: 'Universe' },
  { to: '/signals', icon: Activity, label: 'Signals' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

const worldClockZones = [
  { label: 'Berlin', tz: 'Europe/Berlin', flag: '🇩🇪' },
  { label: 'New York', tz: 'America/New_York', flag: '🇺🇸' },
  { label: 'London', tz: 'Europe/London', flag: '🇬🇧' },
  { label: 'Tokyo', tz: 'Asia/Tokyo', flag: '🇯🇵' },
  { label: 'Hong Kong', tz: 'Asia/Hong_Kong', flag: '🇭🇰' },
];

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
      {worldClockZones.map(({ label, tz, flag }) => (
        <div key={tz} className="world-clock-row">
          <span className="world-clock-label">
            <span className="world-clock-flag">{flag}</span>
            {label}
          </span>
          <span className="world-clock-time">
            <span className="world-clock-day">{formatDay(tz)}</span>
            {formatTime(tz)}
          </span>
        </div>
      ))}
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
