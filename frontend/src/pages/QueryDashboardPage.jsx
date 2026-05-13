import { useEffect, useState } from 'react';
import { BarChart3, CheckCircle2, RefreshCw, RotateCcw } from 'lucide-react';
import { migrationAPI } from '../services/api';
const EMPTY_SESSION_STATS = {
  total_queries_processed: 0,
  successful_migrations: 0,
  validated_queries: 0,
  simple_queries: 0,
  medium_queries: 0,
  complex_queries: 0,
};

export default function QueryDashboardPage() {
  const [stats, setStats] = useState(EMPTY_SESSION_STATS);
  const [loading, setLoading] = useState(false);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const res = await migrationAPI.getSessionStats();
      setStats({ ...EMPTY_SESSION_STATS, ...(res.data?.stats || {}) });
    } catch {
      setStats(EMPTY_SESSION_STATS);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

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
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          Showing global query totals for everyone using this tool. Validated remains a placeholder until validation is implemented.
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <MetricCard
            title="Queries Migrated"
            value={stats.successful_migrations}
            icon={<RotateCcw size={22} />}
            tone="blue"
            subtitle="Converted to Databricks SQL"
            loading={loading}
          />
          <MetricCard
            title="Queries Validated"
            value={stats.validated_queries}
            icon={<CheckCircle2 size={22} />}
            tone="green"
            subtitle="Not activated yet"
            disabled
            loading={loading}
          />
        </div>

        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="text-sm text-gray-500">Queries processed globally</div>
          <div className="mt-1 text-3xl font-bold text-gray-900">{loading ? '...' : stats.total_queries_processed}</div>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ title, value, icon, tone, subtitle, disabled = false, loading = false }) {
  const toneClasses = {
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    green: 'border-green-200 bg-green-50 text-green-700',
  };

  return (
    <div className={`rounded-lg border p-5 ${toneClasses[tone] || toneClasses.blue} ${disabled ? 'opacity-70' : ''}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-medium">{title}</div>
          <div className="mt-2 text-3xl font-bold text-gray-900">{loading ? '...' : value}</div>
          <div className="mt-1 text-xs text-gray-500">{subtitle}</div>
        </div>
        <div className="rounded-full bg-white/70 p-2 text-gray-700">{icon}</div>
      </div>
    </div>
  );
}