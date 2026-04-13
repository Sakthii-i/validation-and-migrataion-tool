import { useState, useEffect } from 'react';
import { resultsAPI, validationAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import { Filter, Download, Search, Eye, Play, Loader2 } from 'lucide-react';
import { useConnection } from '../context/ConnectionContext';

const DATE_FILTERS = ['All Time', 'Today', 'Past 3 days', 'Past 15 days', 'Past 30 days', 'Custom'];

export default function DataValidationsPage() {
  const { isConnected, sessionId } = useConnection();
  const [results, setResults] = useState([]);
  const [dateFilter, setDateFilter] = useState('Past 30 days');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [searchTerm, setSearchTerm] = useState('');
  const [loading, setLoading] = useState(true);
  const [pageSize, setPageSize] = useState(50);
  const [currentPage, setCurrentPage] = useState(1);
  const [detailRow, setDetailRow] = useState(null);
  const [rowRunningKey, setRowRunningKey] = useState(null);
  const [actionError, setActionError] = useState('');

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
    if (row.details) {
      setDetailRow(row);
      return;
    }
    if (row.validation_id) {
      try {
        const res = await resultsAPI.getById(row.validation_id);
        setDetailRow({ ...row, ...res.data });
        if (res.data?.details) return;
      } catch {
        // Fall back to current row only.
      }
    }

    const sourceTable = row.source_table_name || row.src_table_name || row.src_table || row.source_table || row.source;
    const targetTable = row.target_table_name || row.tgt_table_name || row.tgt_table || row.target_table || row.target;

    if (!isConnected || !sessionId) {
      setActionError('Connection is not established. Please establish connections to load row details.');
      setDetailRow(row);
      return;
    }

    if (!sourceTable || !targetTable) {
      setActionError('Source/target table path is missing for this row.');
      setDetailRow(row);
      return;
    }

    try {
      const payload = {
        session_id: sessionId,
        validation_type: String(row.validation_type || 'deep').toLowerCase(),
        table_pairs: [{ source: sourceTable, target: targetTable, source_where: '1=1', target_where: '1=1' }],
        settings: buildSettingsFromRow(row),
      };
      const res = await validationAPI.run(payload);
      const first = res?.data?.results?.[0];
      if (first) {
        setDetailRow({ ...row, ...normalizeRunRow(first, sourceTable, targetTable) });
        return;
      }
    } catch (e) {
      setActionError(e.response?.data?.detail || e.message || 'Failed to load row details.');
    }

    setDetailRow(row);
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

  const isNotSelected = (status) => {
    if (status === null || status === undefined) return true;
    const text = String(status).trim().toUpperCase();
    return text === '' || text === 'N/A' || text === 'NONE' || text === '—' || text === '-';
  };

  const formatNumericValue = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(4) : value;
  };

  return (
    <div>
      <div className="page-topbar">
        <div>
          <h1 className="page-title">Data Validations</h1>
          <p className="text-xs text-gray-500 mt-0.5">All validation executions fetched from API result store (Supabase when configured)</p>
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
                      <td className="font-mono text-xs text-primary-600">{(r.validation_id || '').slice(0, 20)}...</td>
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

        {detailRow && (
          <div className="card mt-4">
            <div className="card-header flex items-center justify-between">
              <span>Validation Details</span>
              <button className="btn btn-outline btn-sm" onClick={() => setDetailRow(null)}>Close</button>
            </div>
            <div className="card-body space-y-6">
              <div className="text-xs text-gray-500 font-mono">
                {detailRow.source_table_name || detailRow.src_table_name || '—'} → {detailRow.target_table_name || detailRow.tgt_table_name || '—'}
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-2">Row Count</h4>
                {isNotSelected(detailRow.count_validation || detailRow.row_count) ? (
                  <div className="text-sm text-gray-500">Not selected.</div>
                ) : detailRow.details?.row_count ? (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="p-3 border rounded-lg">Source: <strong>{detailRow.details.row_count.source_count}</strong></div>
                    <div className="p-3 border rounded-lg">Target: <strong>{detailRow.details.row_count.target_count}</strong></div>
                    <div className="p-3 border rounded-lg">Difference: <strong>{detailRow.details.row_count.difference}</strong></div>
                  </div>
                ) : <div className="text-sm text-gray-500">No row count details available.</div>}
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-2">Schema Details</h4>
                {isNotSelected(detailRow.schema_check) ? (
                  <div className="text-sm text-gray-500">Not selected.</div>
                ) : detailRow.details?.schema?.rows?.length ? (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>column_name_src</th>
                          <th>column_name_tgt</th>
                          <th>source_type</th>
                          <th>target_type</th>
                          <th>status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detailRow.details.schema.rows.map((row, idx) => (
                          <tr key={idx}>
                            <td>{row.column_name_src || '—'}</td>
                            <td>{row.column_name_tgt || '—'}</td>
                            <td>{row.source_type || '—'}</td>
                            <td>{row.target_type || '—'}</td>
                            <td>{row.status || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <div className="text-sm text-gray-500">No schema details available.</div>}
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-2">Null Counts</h4>
                {isNotSelected(detailRow.numeric_check) ? (
                  <div className="text-sm text-gray-500 mb-6">Not selected.</div>
                ) : (
                  (() => {
                    const numericRows = detailRow?.details?.numeric?.rows || [];
                    const nullCountRows = numericRows.filter((row) => {
                      const srcNull = Number(row?.source_null_count || 0);
                      const tgtNull = Number(row?.target_null_count || 0);
                      return srcNull > 0 || tgtNull > 0;
                    });
                    if (!nullCountRows.length) {
                      return <div className="text-sm text-gray-500 mb-6">{detailRow.details?.numeric?.error ? `No null count details available: ${detailRow.details.numeric.error}` : 'No columns are null.'}</div>;
                    }
                    const summary = nullCountRows
                      .map((row) => {
                        const srcNull = Number(row?.source_null_count || 0);
                        const tgtNull = Number(row?.target_null_count || 0);
                        if (srcNull > 0 && tgtNull > 0) return `${row.column}: Source ${srcNull}, Target ${tgtNull}`;
                        if (srcNull > 0) return `${row.column}: Source ${srcNull}`;
                        return `${row.column}: Target ${tgtNull}`;
                      })
                      .join(' | ');
                    return (
                      <>
                        <div className="text-sm text-gray-700 mb-2">{summary}</div>
                        <div className="overflow-x-auto mb-6">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Column</th>
                                <th>Source Null Count</th>
                                <th>Target Null Count</th>
                              </tr>
                            </thead>
                            <tbody>
                              {nullCountRows.map((row, idx) => (
                                <tr key={idx}>
                                  <td>{row.column}</td>
                                  <td>{row.source_null_count}</td>
                                  <td>{row.target_null_count}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    );
                  })()
                )}

                <h4 className="text-sm font-semibold mb-2">Numeric Column Statistics</h4>
                {isNotSelected(detailRow.numeric_check) ? (
                  <div className="text-sm text-gray-500">Not selected.</div>
                ) : detailRow.details?.numeric?.rows?.length ? (
                  <div className="overflow-x-auto">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Column</th>
                          <th>Src Min</th>
                          <th>Src Max</th>
                          <th>Src Avg</th>
                          <th>Tgt Min</th>
                          <th>Tgt Max</th>
                          <th>Tgt Avg</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detailRow.details.numeric.rows.map((row, idx) => (
                          <tr key={idx}>
                            <td>{row.column}</td>
                            <td>{formatNumericValue(row.source_min)}</td>
                            <td>{formatNumericValue(row.source_max)}</td>
                            <td>{formatNumericValue(row.source_avg)}</td>
                            <td>{formatNumericValue(row.target_min)}</td>
                            <td>{formatNumericValue(row.target_max)}</td>
                            <td>{formatNumericValue(row.target_avg)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <div className="text-sm text-gray-500">{detailRow.details?.numeric?.error ? `No numeric details available: ${detailRow.details.numeric.error}` : 'No numeric details available.'}</div>}
              </div>

              <div>
                <h4 className="text-sm font-semibold mb-2">Row Hash Differences</h4>
                {isNotSelected(detailRow.hash_validation) ? (
                  <div className="text-sm text-gray-500">Not selected.</div>
                ) : detailRow.details?.row_hash ? (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    <div className="p-3 border rounded-lg">Source Hash Rows: <strong>{detailRow.details.row_hash.source_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Target Hash Rows: <strong>{detailRow.details.row_hash.target_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Matched Hash Rows: <strong>{detailRow.details.row_hash.matched_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Difference Rows: <strong>{(detailRow.details.row_hash.source_not_in_target_count ?? 0) + (detailRow.details.row_hash.target_not_in_source_count ?? 0)}</strong></div>
                  </div>
                ) : <div className="text-sm text-gray-500">No hash details available.</div>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
