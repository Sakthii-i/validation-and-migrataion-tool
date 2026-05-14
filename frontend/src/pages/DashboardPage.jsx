import { useState, useEffect } from 'react';
import { dashboardAPI } from '../services/api';
import { migrationAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import { BarChart3, CheckCircle2, Hash, Table2, Activity, PieChart } from 'lucide-react';
import { PieChart as RechartPie, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { useConnection } from '../context/ConnectionContext';

const DATE_FILTERS = ['All Time', 'Today', 'Past 3 days', 'Past 15 days', 'Past 30 days', 'Custom'];
const PIE_COLORS = ['#2e7d32', '#c62828'];

export default function DashboardPage() {
  const { sourceEngine } = useConnection();
  const [stats, setStats] = useState(null);
  const [queryStats, setQueryStats] = useState(null);
  const [dateFilter, setDateFilter] = useState('All Time');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [showCharts, setShowCharts] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => { fetchStats(); }, [dateFilter, customStart, customEnd, sourceEngine]);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const res = await dashboardAPI.getStats(
        dateFilter,
        customStart || undefined,
        customEnd || undefined,
        (sourceEngine || '').toLowerCase(),
      );
      setStats(res.data);
      const queryRes = await migrationAPI.getSessionStats(null, (sourceEngine || '').toLowerCase());
      setQueryStats(queryRes.data?.stats || null);
    } catch (e) {
      console.error('Dashboard fetch failed', e);
    } finally {
      setLoading(false);
    }
  };

  const metrics = stats ? [
    { label: 'Queries Migrated', value: queryStats?.successful_migrations ?? 0, icon: <Activity size={20} />, color: 'from-sky-600 to-sky-800' },
    { label: 'Queries Validated', value: queryStats?.validated_queries ?? 0, icon: <CheckCircle2 size={20} />, color: 'from-lime-600 to-lime-800' },
    { label: 'Tables Validated', value: stats.tables_validated ?? 0, icon: <Table2 size={20} />, color: 'from-indigo-600 to-indigo-800' },
    { label: 'Total Validation Runs', value: stats.total_runs ?? 0, icon: <Activity size={20} />, color: 'from-blue-600 to-blue-800' },
    { label: 'Row Count Passed', value: stats.row_count_pass ?? 0, icon: <CheckCircle2 size={20} />, color: 'from-green-600 to-green-800' },
    { label: 'Schema Passed', value: stats.schema_pass ?? 0, icon: <CheckCircle2 size={20} />, color: 'from-emerald-600 to-emerald-800' },
    { label: 'Numeric Passed', value: stats.numeric_pass ?? 0, icon: <BarChart3 size={20} />, color: 'from-teal-600 to-teal-800' },
    { label: 'Row Hash Passed', value: stats.row_hash_pass ?? 0, icon: <Hash size={20} />, color: 'from-cyan-600 to-cyan-800' },
  ] : [];

  const pieData = stats ? [
    { title: 'Row Count', pass: stats.row_count_pass ?? 0, fail: stats.row_count_fail ?? 0 },
    { title: 'Schema', pass: stats.schema_pass ?? 0, fail: stats.schema_fail ?? 0 },
    { title: 'Numeric', pass: stats.numeric_pass ?? 0, fail: stats.numeric_fail ?? 0 },
    { title: 'Row Hash', pass: stats.row_hash_pass ?? 0, fail: stats.row_hash_fail ?? 0 },
  ] : [];

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title">Dashboard</h1>
        <div className="flex items-center gap-3">
          <select className="form-select w-44" value={dateFilter} onChange={(e) => setDateFilter(e.target.value)}>
            {DATE_FILTERS.map(f => <option key={f}>{f}</option>)}
          </select>
          {dateFilter === 'Custom' && (
            <>
              <input type="date" className="form-input w-36" value={customStart} onChange={e => setCustomStart(e.target.value)} />
              <input type="date" className="form-input w-36" value={customEnd} onChange={e => setCustomEnd(e.target.value)} />
            </>
          )}
        </div>
      </div>

      <div className="page-content">
        <CollapsibleSection title="Dashboard Statistics" icon={<BarChart3 size={16} />}>
          {loading ? (
            <div className="flex justify-center py-12"><span className="spinner"></span></div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
              {metrics.map((m, i) => (
                <div key={i} className={`metric-card-colored bg-gradient-to-br ${m.color}`}>
                  <div className="flex items-center gap-2 text-white/80 mb-2">
                    <span className="w-7 h-7 rounded-md bg-white/10 flex items-center justify-center">
                      {m.icon}
                    </span>
                    <span className="text-[11px] font-medium uppercase tracking-wide">{m.label}</span>
                  </div>
                  <div className="text-3xl font-bold text-white">{m.value.toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </CollapsibleSection>

        {/* Toggle Charts */}
        <div className="my-4">
          <button
            className="btn btn-primary btn-full"
            onClick={() => setShowCharts(!showCharts)}
          >
            <PieChart size={16} />
            {showCharts ? 'Hide Stats' : 'View Stats'}
          </button>
        </div>

        {showCharts && stats && (
          <CollapsibleSection title="Validation Overview" icon={<PieChart size={16} />}>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              {pieData.map((d, i) => (
                <div key={i} className="card p-4 text-center">
                  <h3 className="text-sm font-semibold text-gray-700 mb-3">{d.title} Validation</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <RechartPie>
                      <Pie
                        data={[
                          { name: 'Pass', value: d.pass },
                          { name: 'Fail', value: d.fail },
                        ]}
                        cx="50%" cy="50%"
                        innerRadius={50} outerRadius={75}
                        paddingAngle={3}
                        dataKey="value"
                      >
                        {PIE_COLORS.map((c, j) => <Cell key={j} fill={c} />)}
                      </Pie>
                      <Tooltip />
                      <Legend />
                    </RechartPie>
                  </ResponsiveContainer>
                </div>
              ))}
            </div>
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}
