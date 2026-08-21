import { NavLink } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useConnection } from '../context/ConnectionContext';
import {
  LayoutDashboard, Plus, Database, BarChart3, Table2, Shield, LogOut, Plug, PlugZap, ChevronDown, RefreshCw, Loader2
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
  { to: '/query-dashboard', label: 'Query Dashboard', icon: BarChart3 },
  { to: '/query-converter', label: 'Query Converter', icon: RefreshCw },
];

export default function Sidebar() {
  const { user, isAdmin, logout } = useAuth();
  const { connectionStatus, sourceEngine, setSourceEngine, connect, disconnect, isConnected, error } = useConnection();
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

      {/* Shared Connection Control */}
      <div className="border-t border-white/10 px-4 py-3">
        <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-white/45">
          Source Engine
        </label>
        <select
          className="mb-2 w-full rounded-md border border-white/15 bg-white/10 px-2 py-1.5 text-xs font-medium text-white outline-none"
          value={sourceEngine}
          onChange={(e) => {
            setSourceEngine(e.target.value);
            disconnect();
          }}
          disabled={connectionStatus === 'connecting'}
        >
          <option className="text-gray-900">BigQuery</option>
          <option className="text-gray-900">Snowflake</option>
          <option className="text-gray-900">Trino</option>
        </select>
        {['Snowflake', 'Trino'].includes(sourceEngine) ? (
          <button
            type="button"
            className={`flex w-full items-center justify-center gap-2 rounded-md px-2 py-1.5 text-xs font-semibold transition ${
              isConnected ? 'bg-red-500/20 text-red-100 hover:bg-red-500/30' : 'bg-white/15 text-white hover:bg-white/20'
            } disabled:cursor-not-allowed disabled:opacity-60`}
            onClick={isConnected ? disconnect : connect}
            disabled={connectionStatus === 'connecting'}
          >
            {connectionStatus === 'connecting' ? (
              <><Loader2 size={13} className="animate-spin" /> Connecting</>
            ) : isConnected ? (
              <><PlugZap size={13} /> Disconnect</>
            ) : (
              <><Plug size={13} /> Establish Connection</>
            )}
          </button>
        ) : (
          <div className="rounded-md bg-white/10 px-2 py-1.5 text-[10px] leading-snug text-white/70">
            BigQuery credentials are entered in Run Validation.
          </div>
        )}
        {error && <div className="mt-2 text-[10px] leading-snug text-red-200">{error}</div>}
      </div>

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
