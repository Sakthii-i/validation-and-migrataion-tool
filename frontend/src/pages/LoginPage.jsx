import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { Database, Shield, Eye, EyeOff } from 'lucide-react';

export default function LoginPage() {
  const { login, loading, error, setError } = useAuth();
  const [tab, setTab] = useState('user');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await login(username, password, tab);
    } catch (_) { /* error set in context */ }
  };

  return (
    <div className="login-container">
      {/* Floating particles decoration */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        {[...Array(6)].map((_, i) => (
          <div
            key={i}
            className="absolute rounded-full bg-white/5"
            style={{
              width: `${60 + i * 40}px`,
              height: `${60 + i * 40}px`,
              top: `${10 + i * 15}%`,
              left: `${5 + i * 16}%`,
              animation: `float ${3 + i}s ease-in-out infinite alternate`,
            }}
          />
        ))}
      </div>

      <div className="login-card relative">
        {/* Logo */}
        <div className="text-center mb-8">
          <img src="/eucloid-logo.svg" alt="Eucloid logo" className="w-20 h-20 object-contain mx-auto mb-4" />
          <h1 className="text-2xl font-bold text-gray-800">Eucloid</h1>
          <p className="text-sm text-gray-500 mt-1">Data Validation Framework</p>
        </div>

        {/* Tabs */}
        <div className="tab-bar mb-6">
          <button
            className={`tab-item flex-1 text-center ${tab === 'user' ? 'active' : ''}`}
            onClick={() => { setTab('user'); setError(null); }}
          >
            <Database size={14} className="inline mr-1.5 -mt-0.5" />
            User Login
          </button>
          <button
            className={`tab-item flex-1 text-center ${tab === 'admin' ? 'active' : ''}`}
            onClick={() => { setTab('admin'); setError(null); }}
          >
            <Shield size={14} className="inline mr-1.5 -mt-0.5" />
            Admin Login
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className="alert alert-error mb-4">
            <span>{error}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="form-group">
            <label className="form-label">Username</label>
            <input
              type="text"
              className="form-input"
              placeholder={tab === 'admin' ? 'admin' : 'Enter your username'}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="form-group">
            <label className="form-label">Password</label>
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                className="form-input pr-10"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                onClick={() => setShowPw(!showPw)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
          <button
            type="submit"
            className="btn btn-primary btn-full btn-lg mt-2"
            disabled={loading}
          >
            {loading ? (
              <>
                <span className="spinner"></span>
                Signing in...
              </>
            ) : (
              `Sign in as ${tab === 'admin' ? 'Admin' : 'User'}`
            )}
          </button>
        </form>

        <p className="text-center text-xs text-gray-400 mt-6">
          {tab === 'admin'
            ? 'Admin access grants user management capabilities.'
            : 'Contact your admin if you need access.'}
        </p>
      </div>

      <style>{`
        @keyframes float {
          from { transform: translateY(0) rotate(0deg); opacity: 0.3; }
          to { transform: translateY(-20px) rotate(5deg); opacity: 0.6; }
        }
      `}</style>
    </div>
  );
}
