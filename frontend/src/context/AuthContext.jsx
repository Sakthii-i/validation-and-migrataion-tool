import { createContext, useContext, useState, useEffect } from 'react';
import { authAPI } from '../services/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const stored = sessionStorage.getItem('auth_user');
      return stored ? JSON.parse(stored) : null;
    } catch {
      sessionStorage.removeItem('auth_user');
      sessionStorage.removeItem('auth_token');
      localStorage.removeItem('auth_user');
      localStorage.removeItem('auth_token');
      return null;
    }
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const isAuthenticated = !!user;
  const isAdmin = user?.role === 'admin';

  const login = async (username, password, role = 'user') => {
    setLoading(true);
    setError(null);
    try {
      const res = await authAPI.login(username, password, role);
      const userData = { username, role: res.data.role || role, token: res.data.token };
      setUser(userData);
      sessionStorage.setItem('auth_user', JSON.stringify(userData));
      sessionStorage.setItem('auth_token', userData.token || '');
      // Remove any stale persistent auth values from older builds.
      localStorage.removeItem('auth_user');
      localStorage.removeItem('auth_token');
      return userData;
    } catch (err) {
      const msg = err.response?.data?.detail || 'Login failed';
      setError(msg);
      throw new Error(msg);
    } finally {
      setLoading(false);
    }
  };

  const logout = () => {
    setUser(null);
    sessionStorage.removeItem('auth_user');
    sessionStorage.removeItem('auth_token');
    localStorage.removeItem('auth_user');
    localStorage.removeItem('auth_token');
  };

  return (
    <AuthContext.Provider value={{ user, isAuthenticated, isAdmin, login, logout, loading, error, setError }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
};
