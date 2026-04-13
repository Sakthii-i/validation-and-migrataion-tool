import { useState, useEffect } from 'react';
import { authAPI } from '../services/api';
import { useAuth } from '../context/AuthContext';
import CollapsibleSection from '../components/CollapsibleSection';
import { Shield, UserPlus, UserMinus, Users, Eye, EyeOff, Loader2 } from 'lucide-react';

export default function AdminPage() {
  const { isAdmin } = useAuth();
  const [users, setUsers] = useState([]);
  const [newUser, setNewUser] = useState('');
  const [newPass, setNewPass] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => { if (isAdmin) fetchUsers(); }, [isAdmin]);

  const fetchUsers = async () => {
    try {
      const res = await authAPI.listUsers();
      setUsers(res.data.users || []);
    } catch (e) {
      console.error(e);
    }
  };

  const handleGrant = async () => {
    if (!newUser.trim() || !newPass.trim()) return;
    setLoading(true);
    try {
      await authAPI.grantAccess(newUser.trim(), newPass.trim());
      setMessage({ type: 'success', text: `Access granted to "${newUser.trim()}"` });
      setNewUser(''); setNewPass('');
      fetchUsers();
    } catch (e) {
      setMessage({ type: 'error', text: e.response?.data?.detail || 'Failed' });
    } finally {
      setLoading(false);
    }
  };

  const handleRevoke = async (username) => {
    if (!confirm(`Revoke access for "${username}"?`)) return;
    try {
      await authAPI.revokeAccess(username);
      setMessage({ type: 'success', text: `Access revoked for "${username}"` });
      fetchUsers();
    } catch (e) {
      setMessage({ type: 'error', text: e.response?.data?.detail || 'Failed' });
    }
  };

  if (!isAdmin) {
    return (
      <div className="page-content">
        <div className="alert alert-error">🔒 Admin access required.</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title flex items-center gap-2">
          <Shield size={22} /> Admin Panel
        </h1>
      </div>

      <div className="page-content space-y-6">
        {message && (
          <div className={`alert ${message.type === 'success' ? 'alert-success' : 'alert-error'}`}>
            {message.text}
          </div>
        )}

        <CollapsibleSection title="Grant User Access" icon={<UserPlus size={16} />}>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="form-group">
              <label className="form-label">Username</label>
              <input className="form-input" value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="johndoe" />
            </div>
            <div className="form-group">
              <label className="form-label">Password</label>
              <div className="relative">
                <input
                  type={showPass ? 'text' : 'password'}
                  className="form-input pr-10"
                  value={newPass}
                  onChange={e => setNewPass(e.target.value)}
                  placeholder="••••••"
                />
                <button type="button" onClick={() => setShowPass(!showPass)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                  {showPass ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <div className="flex items-end">
              <button className="btn btn-success btn-full" onClick={handleGrant} disabled={loading}>
                {loading ? <Loader2 size={16} className="animate-spin" /> : <UserPlus size={16} />}
                Grant Access
              </button>
            </div>
          </div>
        </CollapsibleSection>

        <CollapsibleSection title="Authorized Users" icon={<Users size={16} />}>
          {users.length === 0 ? (
            <div className="text-center py-8 text-gray-500">No authorized users found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Username</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u, i) => (
                    <tr key={u}>
                      <td className="text-gray-400">{i + 1}</td>
                      <td className="font-medium">{u}</td>
                      <td>
                        <button className="btn btn-danger btn-sm" onClick={() => handleRevoke(u)}>
                          <UserMinus size={14} /> Revoke
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}
