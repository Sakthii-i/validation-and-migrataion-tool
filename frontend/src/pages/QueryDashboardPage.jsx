import { useEffect, useState } from 'react';
import { BarChart3, Eye, RefreshCw, X } from 'lucide-react';
import { migrationAPI } from '../services/api';
import { useConnection } from '../context/ConnectionContext';
import StatusBadge from '../components/StatusBadge';
const EMPTY_SESSION_STATS = {
  successful_migrations: 0,
  validated_queries: 0,
  simple_queries: 0,
  medium_queries: 0,
  complex_queries: 0,
};

export default function QueryDashboardPage() {
  const { sourceEngine } = useConnection();
  const [stats, setStats] = useState(EMPTY_SESSION_STATS);
  const [queries, setQueries] = useState([]);
  const [detailRow, setDetailRow] = useState(null);
  const [runMessage, setRunMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const engine = (sourceEngine || '').toLowerCase();
      const [statsRes, historyRes] = await Promise.all([
        migrationAPI.getSessionStats(null, engine),
        migrationAPI.listQueryHistory(engine),
      ]);
      setStats({ ...EMPTY_SESSION_STATS, ...(statsRes.data?.stats || {}) });
      setQueries(historyRes.data?.queries || []);
    } catch {
      setStats(EMPTY_SESSION_STATS);
      setQueries([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, [sourceEngine]);

  const formatIstDateTime = (value) => {
    if (!value) return '-';
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

  const formatLatency = (value) => {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? `${n.toLocaleString()} ms` : '-';
  };

  const renderLatencyComparison = (row) => {
    const source = Number(row.source_latency_ms || 0);
    const target = Number(row.target_latency_ms || 0);
    if (target <= 0) return '-';
    if (source <= 0) return `${target.toLocaleString()} ms`;

    const diff = source - target;
    if (diff > 0) return <span style={{ color: '#16a34a', fontWeight: 600 }}>{target.toLocaleString()} ms (faster)</span>;
    if (diff < 0) return <span style={{ color: '#dc2626', fontWeight: 600 }}>{target.toLocaleString()} ms (slower)</span>;
    return <span style={{ fontWeight: 600 }}>{target.toLocaleString()} ms (same)</span>;
  };

  return (
    <div>
      <div className="page-topbar flex items-center justify-between gap-3">
        <h1 className="page-title flex items-center gap-2">
          <BarChart3 size={22} /> Query Dashboard
        </h1>
        <button className="btn btn-outline btn-sm" onClick={fetchStats} type="button">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <div className="page-content space-y-5">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <MetricCard
            title="Queries Migrated"
            value={stats.successful_migrations}
            tone="blue"
            subtitle="Converted to Databricks SQL"
            loading={loading}
          />
          <MetricCard
            title="Queries Validated"
            value={stats.validated_queries}
            tone="green"
            subtitle="Validated query runs"
            loading={loading}
          />
        </div>

        <div className="card">
          {loading ? (
            <div className="flex justify-center py-12"><span className="spinner"></span></div>
          ) : queries.length === 0 ? (
            <div className="p-8 text-center text-gray-500">No query runs found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Query ID</th>
                    <th>Query Name</th>
                    <th>User</th>
                    <th>Last Ran (IST)</th>
                    <th>Target Latency</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {queries.map((row) => (
                    <tr key={row.query_id}>
                      <td className="font-mono text-xs text-primary-600">{row.query_id}</td>
                      <td>{row.query_name || 'Untitled Query'}</td>
                      <td className="text-xs">{row.run_by || 'N/A'}</td>
                      <td className="text-xs whitespace-nowrap">{formatIstDateTime(row.last_ran_ts)}</td>
                      <td>{renderLatencyComparison(row)}</td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button className="btn btn-outline btn-sm" type="button" title="View query details" onClick={() => setDetailRow(row)}>
                            <Eye size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {detailRow && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="max-h-[90vh] w-full max-w-5xl overflow-auto rounded-lg bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-gray-200 p-4">
              <div>
                <div className="text-lg font-semibold">{detailRow.query_name || 'Untitled Query'}</div>
                <div className="font-mono text-xs text-primary-600">{detailRow.query_id}</div>
              </div>
              <button className="btn btn-outline btn-sm" type="button" onClick={() => setDetailRow(null)}><X size={14} /></button>
            </div>
            <div className="space-y-4 p-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-8">
                <Info label="Migration Mode" value={detailRow.migration_mode || '-'} />
                <Info label="Input Mode" value={detailRow.details?.input_mode || '-'} />
                <Info label="Validation" value={<StatusBadge status={detailRow.validation_status || 'NOT RUN'} />} />
                <Info label="Pushed To Git" value={detailRow.pushed_to_git ? 'Yes' : 'No'} />
                <Info label="Reviewers" value={detailRow.reviewers?.length ? detailRow.reviewers.join(', ') : '-'} />
                <Info label="Source Latency" value={formatLatency(detailRow.source_latency_ms)} />
                <Info label="Target Latency" value={formatLatency(detailRow.target_latency_ms)} />
                <Info label="Comparison" value={renderLatencyComparison(detailRow)} />
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <SqlBlock title={`${sourceEngine} SQL`} sql={detailRow.source_sql} />
                <SqlBlock title="Databricks SQL" sql={detailRow.translated_sql} />
              </div>
              {detailRow.details?.complexity && (
                <div className="rounded-lg border p-3 text-sm">
                  Complexity: <strong>{detailRow.details.complexity.complexity_level}</strong> ({detailRow.details.complexity.complexity_score})
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Info({ label, value }) {
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <div className="text-[11px] font-semibold uppercase text-gray-500">{label}</div>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}

function SqlBlock({ title, sql }) {
  return (
    <div>
      <div className="mb-2 text-sm font-semibold">{title}</div>
      <pre className="max-h-72 overflow-auto rounded-lg border bg-gray-50 p-3 font-mono text-xs whitespace-pre-wrap">{sql || '-'}</pre>
    </div>
  );
}

function MetricCard({ title, value, tone, subtitle, disabled = false, loading = false }) {
  const toneClasses = {
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    green: 'border-green-200 bg-green-50 text-green-700',
  };

  return (
    <div className={`rounded-lg border p-5 ${toneClasses[tone] || toneClasses.blue} ${disabled ? 'opacity-70' : ''}`}>
      <div>
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-2 text-3xl font-bold text-gray-900">{loading ? '...' : value}</div>
        <div className="mt-1 text-xs text-gray-500">{subtitle}</div>
      </div>
    </div>
  );
}
