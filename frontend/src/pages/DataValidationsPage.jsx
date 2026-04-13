import { useState, useEffect } from 'react';
import { resultsAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { Database, Filter, Download, Search } from 'lucide-react';

const DATE_FILTERS = ['All Time', 'Today', 'Past 3 days', 'Past 15 days', 'Past 30 days', 'Custom'];

export default function DataValidationsPage() {
  const [results, setResults] = useState([]);
  const [dateFilter, setDateFilter] = useState('Past 30 days');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);
  const [pageSize, setPageSize] = useState(50);

  useEffect(() => { fetchResults(); }, [dateFilter, customStart, customEnd]);

  const fetchResults = async () => {
    setLoading(true);
    try {
      const res = await resultsAPI.list(dateFilter, customStart || undefined, customEnd || undefined);
      setResults(res.data.results || []);
    } catch (e) {
      console.error('Results fetch failed', e);
    } finally {
      setLoading(false);
    }
  };

  const filtered = results.filter(r => {
    if (!searchTerm) return true;
    const term = searchTerm.toLowerCase();
    return (
      (r.validation_id || '').toLowerCase().includes(term) ||
      (r.src_table_name || '').toLowerCase().includes(term) ||
      (r.tgt_table_name || '').toLowerCase().includes(term)
    );
  });

  const displayed = filtered.slice(0, pageSize);

  const exportCSV = () => {
    const headers = ['validation_id','validation_ts','validation_type','source_table','target_table','count_validation','schema_check','numeric_check','hash_validation'];
    const rows = filtered.map(r => [
      r.validation_id, r.validation_ts, r.validation_type,
      r.source_table_name || r.src_table_name, r.target_table_name || r.tgt_table_name,
      r.count_validation || r.row_count, r.schema_check, r.numeric_check, r.hash_validation
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'validation_results.csv'; a.click();
  };

  return (
    <div>
      <div className="page-topbar">
        <div>
          <h1 className="page-title">Data Validations</h1>
          <p className="text-xs text-gray-500 mt-0.5">All validation executions captured from PostgreSQL</p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn btn-outline btn-sm" onClick={exportCSV}>
            <Download size={14} /> Export CSV
          </button>
          <button className="btn btn-outline btn-sm" onClick={fetchResults}>
            Refresh
          </button>
        </div>
      </div>

      <div className="page-content">
        {/* Filters */}
        <CollapsibleSection title="Filters & Search" icon={<Filter size={16} />}>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="form-group">
              <label className="form-label">Date Range</label>
              <select className="form-select" value={dateFilter} onChange={e => setDateFilter(e.target.value)}>
                {DATE_FILTERS.map(f => <option key={f}>{f}</option>)}
              </select>
            </div>
            {dateFilter === 'Custom' && (
              <>
                <div className="form-group">
                  <label className="form-label">Start Date</label>
                  <input type="date" className="form-input" value={customStart} onChange={e => setCustomStart(e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="form-label">End Date</label>
                  <input type="date" className="form-input" value={customEnd} onChange={e => setCustomEnd(e.target.value)} />
                </div>
              </>
            )}
            <div className="form-group">
              <label className="form-label">Search</label>
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input className="form-input pl-9" placeholder="Search by table name or ID..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)} />
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between mt-4">
            <span className="text-xs text-gray-500">
              Showing {displayed.length} of {filtered.length} results
            </span>
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">Page Size:</label>
              <select className="form-select w-24" value={pageSize} onChange={e => setPageSize(Number(e.target.value))}>
                {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>
        </CollapsibleSection>

        {/* Results Table */}
        <div className="card mt-4">
          {loading ? (
            <div className="flex justify-center py-12"><span className="spinner"></span></div>
          ) : displayed.length === 0 ? (
            <div className="p-8 text-center text-gray-500">No validation results found.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Validation ID</th>
                    <th>Timestamp</th>
                    <th>Type</th>
                    <th>Source Table</th>
                    <th>Target Table</th>
                    <th>Row Count</th>
                    <th>Schema</th>
                    <th>Numeric</th>
                    <th>Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((r, i) => (
                    <tr key={i}>
                      <td className="font-mono text-xs text-primary-600">{(r.validation_id || '').slice(0, 20)}...</td>
                      <td className="text-xs whitespace-nowrap">{r.validation_ts || '—'}</td>
                      <td><StatusBadge status={r.validation_type || '—'} /></td>
                      <td className="font-mono text-xs">{r.source_table_name || r.src_table_name || '—'}</td>
                      <td className="font-mono text-xs">{r.target_table_name || r.tgt_table_name || '—'}</td>
                      <td><StatusBadge status={r.count_validation || r.row_count || '—'} /></td>
                      <td><StatusBadge status={r.schema_check || '—'} /></td>
                      <td><StatusBadge status={r.numeric_check || '—'} /></td>
                      <td><StatusBadge status={r.hash_validation || '—'} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
