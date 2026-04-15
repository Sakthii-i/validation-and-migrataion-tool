import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { resultsAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { BarChart3, RefreshCw } from 'lucide-react';

export default function ValidationDashboardPage() {
  const navigate = useNavigate();
  const [validations, setValidations] = useState([]);
  const [validationsLoading, setValidationsLoading] = useState(true);

  useEffect(() => {
    fetchAll();
  }, []);

  const fetchAll = async () => {
    await fetchValidations();
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
    const explicitOverall = normalizeStatus(row?.overall_status);
    if (explicitOverall && ['PASS', 'FAIL', 'ERROR'].includes(explicitOverall)) {
      return explicitOverall;
    }

    const fields = [
      row?.count_validation ?? row?.row_count,
      row?.schema_check,
      row?.numeric_check,
      row?.hash_validation,
    ];
    const selected = fields.filter(isSelectedStatus).map(normalizeStatus);
    if (!selected.length) return 'ERROR';
    if (selected.some((s) => s === 'ERROR')) return 'ERROR';
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

  const statusCounts = validations.reduce(
    (acc, row) => {
      const status = overallStatusForRow(row);
      if (status === 'PASS') acc.pass += 1;
      else if (status === 'FAIL') acc.fail += 1;
      else acc.error += 1;
      acc.total += 1;
      return acc;
    },
    { total: 0, pass: 0, fail: 0, error: 0 },
  );

  const summaryBadges = [
    { label: 'Total', value: statusCounts.total, cls: 'bg-primary-600 text-white' },
    { label: 'Pass', value: statusCounts.pass, cls: 'bg-green-600 text-white' },
    { label: 'Fail', value: statusCounts.fail, cls: 'bg-red-600 text-white' },
    { label: 'Error', value: statusCounts.error, cls: 'bg-orange-500 text-white' },
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
          {validationsLoading ? (
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
              {validations.length === 0 ? (
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
