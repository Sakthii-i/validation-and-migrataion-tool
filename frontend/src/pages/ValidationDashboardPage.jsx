import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { dashboardAPI, resultsAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { BarChart3, RefreshCw } from 'lucide-react';

export default function ValidationDashboardPage() {
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [validations, setValidations] = useState([]);
  const [validationsLoading, setValidationsLoading] = useState(true);

  useEffect(() => {
    fetchAll();
  }, []);

  const fetchAll = async () => {
    await Promise.all([fetchStats(), fetchValidations()]);
  };

  const fetchStats = async () => {
    setLoading(true);
    try {
      const res = await dashboardAPI.getStats('All Time');
      setStats(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchValidations = async () => {
    setValidationsLoading(true);
    try {
      const res = await resultsAPI.list('All Time');
      setValidations(res.data?.results || []);
    } catch (e) {
      console.error(e);
    } finally {
      setValidationsLoading(false);
    }
  };

  const normalizeStatus = (value) => {
    if (value === null || value === undefined) return '';
    return String(value).trim().toUpperCase();
  };

  const isSelectedStatus = (value) => {
    const text = normalizeStatus(value);
    return text && text !== '-' && text !== 'N/A' && text !== 'NONE' && text !== '—';
  };

  const overallStatusForRow = (row) => {
    const fields = [
      row?.count_validation ?? row?.row_count,
      row?.schema_check,
      row?.numeric_check,
      row?.hash_validation,
    ];
    const selected = fields.filter(isSelectedStatus).map(normalizeStatus);
    if (!selected.length) return 'N/A';
    return selected.every((s) => s === 'PASS') ? 'PASS' : 'FAIL';
  };

  const formatIstDateTime = (value) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  };

  const totalPass = (stats?.row_count_pass || 0) + (stats?.schema_pass || 0) + (stats?.numeric_pass || 0) + (stats?.row_hash_pass || 0);
  const totalFail = (stats?.row_count_fail || 0) + (stats?.schema_fail || 0) + (stats?.numeric_fail || 0) + (stats?.row_hash_fail || 0);
  const totalRuns = stats?.total_runs || 0;
  const errors = totalRuns - totalPass - totalFail;

  const summaryBadges = [
    { label: 'Total', value: totalRuns, cls: 'bg-primary-600 text-white' },
    { label: 'Pass', value: totalPass, cls: 'bg-green-600 text-white' },
    { label: 'Fail', value: totalFail, cls: 'bg-red-600 text-white' },
    { label: 'Error', value: Math.max(0, errors), cls: 'bg-orange-500 text-white' },
  ];

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title flex items-center gap-2">
          <BarChart3 size={22} /> Validation Dashboard
        </h1>
        <button className="btn btn-outline btn-sm" onClick={fetchAll}>
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="page-content">
        <CollapsibleSection title="Summary" icon={<BarChart3 size={16} />}>
          {loading ? (
            <div className="flex justify-center py-8"><span className="spinner"></span></div>
          ) : (
            <>
              {/* Summary Badges */}
              <div className="flex flex-wrap gap-3 mb-6">
                {summaryBadges.map((b, i) => (
                  <div key={i} className={`${b.cls} px-5 py-2 rounded-full text-sm font-bold shadow-sm`}>
                    {b.label}: {b.value.toLocaleString()}
                  </div>
                ))}
              </div>

              {/* Validation Jobs Table */}
              {validationsLoading ? (
                <div className="flex justify-center py-8"><span className="spinner"></span></div>
              ) : validations.length === 0 ? (
                <div className="text-center py-8 text-gray-500">No validation runs found.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Validation ID</th>
                        <th>Status</th>
                        <th>Timestamp (IST)</th>
                        <th>User</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {validations.map((row) => {
                        const validationId = row?.validation_id;
                        const status = overallStatusForRow(row);
                        const userValue = row?.run_by || row?.username || row?.user || 'N/A';
                        return (
                          <tr key={validationId || `${row?.validation_ts}-${Math.random().toString(16).slice(2)}`}>
                            <td className="font-mono text-xs text-primary-600" title={validationId || '—'}>
                              {(validationId || '').length > 28 ? `${validationId.slice(0, 28)}...` : (validationId || '—')}
                            </td>
                            <td><StatusBadge status={status} /></td>
                            <td className="text-xs whitespace-nowrap">{formatIstDateTime(row?.validation_ts)}</td>
                            <td className="text-xs whitespace-nowrap">{userValue || 'N/A'}</td>
                            <td>
                              <button
                                className="btn btn-outline btn-sm"
                                onClick={() => validationId && navigate(`/data-validations/${encodeURIComponent(validationId)}`)}
                                disabled={!validationId}
                              >
                                View Details
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}
