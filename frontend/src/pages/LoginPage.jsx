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
      <div className="login-card relative">
        {/* Logo */}
        <div className="text-center mb-8">
          <img src="/eucloid.jpg" alt="Eucloid logo" className="w-28 h-28 object-contain mx-auto mb-3" />
          <h1 className="text-3xl font-bold text-gray-800">Eucloid</h1>
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

    </div>
  );
}
