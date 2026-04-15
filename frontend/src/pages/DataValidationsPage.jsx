import { useState, useEffect } from 'react';
import { resultsAPI, validationAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { Filter, Download, Search, Eye, Play, Loader2, Copy, Check } from 'lucide-react';
import { useConnection } from '../context/ConnectionContext';
import { useAuth } from '../context/AuthContext';
import { useNavigate } from 'react-router-dom';

const DATE_FILTERS = ['All Time', 'Today', 'Past 3 days', 'Past 15 days', 'Past 30 days', 'Custom'];

export default function DataValidationsPage() {
  const navigate = useNavigate();
  const { isConnected, sessionId } = useConnection();
  const { user } = useAuth();
  const [results, setResults] = useState([]);
  const [dateFilter, setDateFilter] = useState('Past 30 days');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);
  const [pageSize, setPageSize] = useState(50);
  const [currentPage, setCurrentPage] = useState(1);
  const [rowRunningKey, setRowRunningKey] = useState(null);
  const [actionError, setActionError] = useState('');
  const [copiedValidationId, setCopiedValidationId] = useState('');

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

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const startIndex = (safePage - 1) * pageSize;
  const displayed = filtered.slice(startIndex, startIndex + pageSize);

  useEffect(() => {
    setCurrentPage(1);
  }, [pageSize, searchTerm, dateFilter, customStart, customEnd]);

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  const normalizeStatus = (value) => {
    if (value === null || value === undefined) return '';
    return String(value).trim().toUpperCase();
  };

  const isSelectedStatus = (value) => {
    const text = normalizeStatus(value);
    return text && text !== '-' && text !== 'N/A' && text !== 'NONE';
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

  const buildSettingsFromRow = (row) => {
    const validationType = String(row.validation_type || 'deep').toLowerCase();
    const rowSelected = isSelectedStatus(row.count_validation || row.row_count);
    const schemaSelected = isSelectedStatus(row.schema_check);
    const numericSelected = isSelectedStatus(row.numeric_check);
    const hashSelected = isSelectedStatus(row.hash_validation);

    if (validationType === 'shallow') {
      return {
        validationType,
        rowCount: true,
        schema: true,
        numeric: false,
        hash: false,
        useThreshold: false,
        threshold: 0.99,
        includeTimestamp: false,
        caseSensitive: false,
        colDiffEnabled: false,
        primaryKeys: '',
      };
    }

    return {
      validationType,
      rowCount: rowSelected,
      schema: schemaSelected,
      numeric: numericSelected,
      hash: hashSelected,
      useThreshold: false,
      threshold: 0.99,
      includeTimestamp: false,
      caseSensitive: false,
      colDiffEnabled: false,
      primaryKeys: '',
    };
  };

  const normalizeRunRow = (row, fallbackSource, fallbackTarget) => ({
    validation_id: row.validation_id || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    validation_ts: row.validation_ts || new Date().toISOString(),
    validation_type: row.validation_type,
    source_table_name: row.source_table_name || row.src_table || row.source || fallbackSource,
    target_table_name: row.target_table_name || row.tgt_table || row.target || fallbackTarget,
    row_count: row.row_count ?? row.count_validation,
    schema_check: row.schema_check,
    numeric_check: row.numeric_check,
    hash_validation: row.hash_validation,
    details: row.details,
  });

  const handleViewRow = async (row) => {
    setActionError('');
    if (!row.validation_id) {
      setActionError('Validation ID is missing; unable to fetch Supabase details.');
      return;
    }

    navigate(`/data-validations/${encodeURIComponent(row.validation_id)}`);
  };

  const handleRunRow = async (row) => {
    setActionError('');
    if (!isConnected || !sessionId) {
      setActionError('Connection is not established. Please establish connections before running row validation.');
      return;
    }

    const sourceTable = row.source_table_name || row.src_table_name || row.src_table || row.source_table || row.source;
    const targetTable = row.target_table_name || row.tgt_table_name || row.tgt_table || row.target_table || row.target;

    if (!sourceTable || !targetTable) {
      setActionError('Source/target table path is missing for this row.');
      return;
    }

    const key = row.validation_id || `${sourceTable}-${targetTable}`;
    setRowRunningKey(key);
    try {
      const payload = {
        session_id: sessionId,
        validation_type: String(row.validation_type || 'deep').toLowerCase(),
        run_by: user?.username || undefined,
        table_pairs: [{ source: sourceTable, target: targetTable, source_where: '1=1', target_where: '1=1' }],
        settings: buildSettingsFromRow(row),
      };
      const res = await validationAPI.run(payload);
      const newRows = (res?.data?.results || []).map((item) => normalizeRunRow(item, sourceTable, targetTable));
      if (newRows.length) {
        setResults((prev) => [...prev, ...newRows]);
      }
    } catch (e) {
      setActionError(e.response?.data?.detail || e.message || 'Failed to run validation for this row.');
    } finally {
      setRowRunningKey(null);
    }
  };

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

  const handleCopyValidationId = async (validationId) => {
    if (!validationId) return;
    try {
      await navigator.clipboard.writeText(validationId);
      setCopiedValidationId(validationId);
      setTimeout(() => setCopiedValidationId(''), 1500);
    } catch {
      const temp = document.createElement('textarea');
      temp.value = validationId;
      temp.setAttribute('readonly', '');
      temp.style.position = 'absolute';
      temp.style.left = '-9999px';
      document.body.appendChild(temp);
      temp.select();
      document.execCommand('copy');
      document.body.removeChild(temp);
      setCopiedValidationId(validationId);
      setTimeout(() => setCopiedValidationId(''), 1500);
    }
  };

  return (
    <div>
      <div className="page-topbar">
        <div>
          <h1 className="page-title">Validation History</h1>
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
              Showing {filtered.length === 0 ? 0 : startIndex + 1} - {Math.min(startIndex + displayed.length, filtered.length)} of {filtered.length} results
            </span>
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">Page Size:</label>
              <select className="form-select w-24" value={pageSize} onChange={e => setPageSize(Number(e.target.value))}>
                {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>
        </CollapsibleSection>

        {actionError && <div className="alert alert-error mt-4">{actionError}</div>}

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
                    <th>Last Modified (IST)</th>
                    <th>Type</th>
                    <th>Source Table</th>
                    <th>Target Table</th>
                    <th>Row Count</th>
                    <th>Schema</th>
                    <th>Numeric</th>
                    <th>Hash</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((r, i) => (
                    <tr key={i}>
                      <td
                        className="font-mono text-xs text-primary-600"
                        title={r.validation_id || '—'}
                      >
                        <div className="flex items-center gap-2">
                          <span>
                            {(r.validation_id || '').length > 20
                              ? `${(r.validation_id || '').slice(0, 20)}...`
                              : (r.validation_id || '—')}
                          </span>
                          {r.validation_id && (
                            <button
                              type="button"
                              className="text-primary-700 hover:text-primary-900"
                              title={copiedValidationId === r.validation_id ? 'Copied' : 'Copy full Validation ID'}
                              onClick={() => handleCopyValidationId(r.validation_id)}
                            >
                              {copiedValidationId === r.validation_id ? <Check size={14} /> : <Copy size={14} />}
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="text-xs whitespace-nowrap">{formatIstDateTime(r.validation_ts)}</td>
                      <td><StatusBadge status={r.validation_type || '—'} /></td>
                      <td className="font-mono text-xs">{r.source_table_name || r.src_table_name || '—'}</td>
                      <td className="font-mono text-xs">{r.target_table_name || r.tgt_table_name || '—'}</td>
                      <td><StatusBadge status={r.count_validation || r.row_count || '—'} /></td>
                      <td><StatusBadge status={r.schema_check || '—'} /></td>
                      <td><StatusBadge status={r.numeric_check || '—'} /></td>
                      <td><StatusBadge status={r.hash_validation || '—'} /></td>
                      <td>
                        <div className="flex items-center gap-1">
                          <button
                            className="btn btn-outline btn-sm"
                            title="View row details"
                            onClick={() => handleViewRow(r)}
                          >
                            <Eye size={14} />
                          </button>
                          <button
                            className="btn btn-outline btn-sm"
                            title="Run this row validation"
                            onClick={() => handleRunRow(r)}
                            disabled={rowRunningKey === (r.validation_id || `${r.source_table_name || r.src_table_name}-${r.target_table_name || r.tgt_table_name}`)}
                          >
                            {rowRunningKey === (r.validation_id || `${r.source_table_name || r.src_table_name}-${r.target_table_name || r.tgt_table_name}`)
                              ? <Loader2 size={14} className="animate-spin" />
                              : <Play size={14} />}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="flex items-center justify-end gap-2 p-3 border-t border-gray-200">
                <button
                  className="btn btn-outline btn-sm"
                  disabled={safePage <= 1}
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                >
                  Previous
                </button>
                <span className="text-xs text-gray-500">Page {safePage} of {totalPages}</span>
                <button
                  className="btn btn-outline btn-sm"
                  disabled={safePage >= totalPages}
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
