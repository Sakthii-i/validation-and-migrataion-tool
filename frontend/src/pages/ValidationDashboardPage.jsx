import { useState, useEffect } from 'react';
import { dashboardAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { BarChart3, RefreshCw } from 'lucide-react';

export default function ValidationDashboardPage() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchStats(); }, []);

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

  const breakdownRows = stats ? [
    { metric: 'Row Count', pass: stats.row_count_pass, fail: stats.row_count_fail },
    { metric: 'Schema', pass: stats.schema_pass, fail: stats.schema_fail },
    { metric: 'Numeric Stats', pass: stats.numeric_pass, fail: stats.numeric_fail },
    { metric: 'Row Hash', pass: stats.row_hash_pass, fail: stats.row_hash_fail },
  ] : [];

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title flex items-center gap-2">
          <BarChart3 size={22} /> Validation Dashboard
        </h1>
        <button className="btn btn-outline btn-sm" onClick={fetchStats}>
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

              {/* Breakdown Table */}
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Pass</th>
                    <th>Fail</th>
                    <th>Total</th>
                    <th>Pass Rate</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {breakdownRows.map((r, i) => {
                    const total = (r.pass || 0) + (r.fail || 0);
                    const rate = total > 0 ? ((r.pass / total) * 100).toFixed(1) : '—';
                    const status = total === 0 ? 'N/A' : (r.fail === 0 ? 'PASS' : 'FAIL');
                    return (
                      <tr key={i}>
                        <td className="font-medium">{r.metric}</td>
                        <td className="text-green-700 font-semibold">{(r.pass || 0).toLocaleString()}</td>
                        <td className="text-red-700 font-semibold">{(r.fail || 0).toLocaleString()}</td>
                        <td>{total.toLocaleString()}</td>
                        <td>{rate !== '—' ? `${rate}%` : '—'}</td>
                        <td><StatusBadge status={status} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}
