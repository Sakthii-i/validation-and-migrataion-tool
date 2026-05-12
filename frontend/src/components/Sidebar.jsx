import { NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useConnection } from '../context/ConnectionContext';
import {
  LayoutDashboard, Plus, Database, BarChart3, Table2, Shield, LogOut, Plug, PlugZap, ChevronDown, RefreshCw
} from 'lucide-react';
import { useState } from 'react';

const navItems = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/new-validation', label: 'Run Validation', icon: Plus },
  { to: '/data-validations', label: 'Validation History', icon: Database },
  { to: '/validation-dashboard', label: 'Validation Dashboard', icon: BarChart3 },
];

const toolItems = [
  { to: '/bq-schema-viewer', label: 'Schema Viewer', icon: Table2 },
  { to: '/query-converter', label: 'Query Converter', icon: RefreshCw },
];

export default function Sidebar() {
  const { user, isAdmin, logout } = useAuth();
  const { connectionStatus, sourceEngine } = useConnection();
  const [toolsOpen, setToolsOpen] = useState(true);

  const statusColor = connectionStatus === 'connected'
    ? 'bg-green-400' : connectionStatus === 'connecting'
    ? 'bg-yellow-400 animate-pulse' : 'bg-red-400';

  const statusText = connectionStatus === 'connected'
    ? 'Connected' : connectionStatus === 'connecting'
    ? 'Connecting...' : 'Disconnected';

  return (
    <div className="sidebar">
      {/* Brand */}
      <div className="sidebar-brand flex items-center gap-3">
        <img src="/eucloid.jpg" alt="Eucloid logo" className="w-8 h-8 object-contain" />
        <span>Eucloid</span>
      </div>

      {/* Main Navigation */}
      <nav className="sidebar-nav">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}

        {/* Tools Section */}
        <div className="sidebar-section mt-6 flex items-center justify-between cursor-pointer" onClick={() => setToolsOpen(!toolsOpen)}>
          <span>Tools</span>
          <ChevronDown size={12} className={`transition-transform ${toolsOpen ? '' : '-rotate-90'}`} />
        </div>

        {toolsOpen && toolItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
          >
            <Icon size={18} />
            <span>{label}</span>
          </NavLink>
        ))}

        {/* Admin Panel */}
        {isAdmin && (
          <>
            <div className="sidebar-section mt-4">Administration</div>
            <NavLink
              to="/admin"
              className={({ isActive }) => `sidebar-item ${isActive ? 'active' : ''}`}
            >
              <Shield size={18} />
              <span>Admin Panel</span>
            </NavLink>
          </>
        )}
      </nav>

      {/* Connection Status */}
      <div className="px-5 py-3 border-t border-white/10">
        <div className="flex items-center gap-2 text-xs text-white/70">
          {connectionStatus === 'connected' ? <PlugZap size={14} /> : <Plug size={14} />}
          <span className={`w-2 h-2 rounded-full ${statusColor}`}></span>
          <span>{statusText}</span>
        </div>
        {connectionStatus === 'connected' && (
          <div className="text-[10px] text-white/40 mt-1">Engine: {sourceEngine}</div>
        )}
      </div>

      {/* User Info */}
      <div className="sidebar-user flex items-center justify-between">
        <div>
          <div className="text-white/90 font-medium text-sm">{user?.username || 'Guest'}</div>
          <div className="text-white/40 text-[10px] uppercase">{user?.role || ''}</div>
        </div>
        <button onClick={logout} className="text-white/50 hover:text-red-300 transition-colors" title="Logout">
          <LogOut size={16} />
        </button>
      </div>
    </div>
  );
}
