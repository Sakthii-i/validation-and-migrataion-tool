import { useState, useEffect } from 'react';
import { authAPI } from '../services/api';
import { useAuth } from '../context/AuthContext';
import CollapsibleSection from '../components/CollapsibleSection';
import { Shield, UserPlus, UserMinus, Users, Eye, EyeOff, Loader2 } from 'lucide-react';

export default function AdminPage() {
  const { isAdmin } = useAuth();
  const [users, setUsers] = useState([]);
  const [newUser, setNewUser] = useState('');
  const [newEmail, setNewEmail] = useState('');
  const [newPass, setNewPass] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [showSmtpPass, setShowSmtpPass] = useState(false);
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
    if (!newUser.trim() || !newPass.trim() || !newEmail.trim() || !smtpPassword.trim()) return;
    setLoading(true);
    try {
      await authAPI.grantAccess({
        username: newUser.trim(),
        password: newPass,
        email: newEmail.trim(),
        smtp_password: smtpPassword,
      });
      setMessage({ type: 'success', text: `Access granted to "${newUser.trim()}"` });
      setNewUser('');
      setNewEmail('');
      setNewPass('');
      setSmtpPassword('');
      fetchUsers();
    } catch (e) {
      setMessage({ type: 'error', text: e.response?.data?.detail || 'Failed' });
    } finally {
      setLoading(false);
    }
  };

  const handleRevoke = async (username) => {
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
              <label className="form-label">Name</label>
              <input className="form-input" value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="johndoe" />
            </div>
            <div className="form-group">
              <label className="form-label">User Email</label>
              <input className="form-input" type="email" value={newEmail} onChange={e => setNewEmail(e.target.value)} placeholder="user@company.com" />
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
            <div className="form-group">
              <label className="form-label">SMTP App Password</label>
              <div className="relative">
                <input
                  type={showSmtpPass ? 'text' : 'password'}
                  className="form-input pr-10"
                  value={smtpPassword}
                  onChange={e => setSmtpPassword(e.target.value)}
                  placeholder="SMTP app password"
                />
                <button type="button" onClick={() => setShowSmtpPass(!showSmtpPass)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                  {showSmtpPass ? <EyeOff size={16} /> : <Eye size={16} />}
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
                    <th>Name</th>
                    <th>User Email</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user, i) => {
                    const username = typeof user === 'string' ? user : user.username;
                    return (
                    <tr key={username}>
                      <td className="text-gray-400">{i + 1}</td>
                      <td className="font-medium">{username}</td>
                      <td>{typeof user === 'string' ? '-' : (user.email || '-')}</td>
                      <td>
                        <button className="btn btn-danger btn-sm" onClick={() => handleRevoke(username)}>
                          <UserMinus size={14} /> Revoke
                        </button>
                      </td>
                    </tr>
                  );})}
                </tbody>
              </table>
            </div>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}
