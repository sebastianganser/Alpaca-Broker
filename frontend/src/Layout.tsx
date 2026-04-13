import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  Globe,
  Activity,
  Settings,
} from 'lucide-react';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/universe', icon: Globe, label: 'Universe' },
  { to: '/signals', icon: Activity, label: 'Signals' },
  { to: '/settings', icon: Settings, label: 'Settings' },
];

export default function Layout() {
  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>Signal Warehouse</h1>
          <div className="subtitle">Alpaca Paper Trading</div>
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
