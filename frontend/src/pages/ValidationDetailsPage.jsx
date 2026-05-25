import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { resultsAPI } from '../services/api';
import StatusBadge from '../components/StatusBadge';

function isNotSelected(status) {
  if (status === null || status === undefined) return true;
  const text = String(status).trim().toUpperCase();
  return text === '' || text === 'N/A' || text === 'NONE' || text === '—' || text === '-';
}

function formatNumericValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(4) : value;
}

export default function ValidationDetailsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { validationId } = useParams();
  const [detailRow, setDetailRow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const backTarget = location.pathname.startsWith('/validation-dashboard') ? '/validation-dashboard' : '/data-validations';

  useEffect(() => {
    const fetchDetails = async () => {
      if (!validationId) {
        setError('Validation ID is missing.');
        setLoading(false);
        return;
      }

      setLoading(true);
      setError('');
      try {
        const res = await resultsAPI.getById(validationId);
        setDetailRow(res.data || null);
      } catch (e) {
        setError(e.response?.data?.detail || e.message || 'Failed to load validation details.');
      } finally {
        setLoading(false);
      }
    };

    fetchDetails();
  }, [validationId]);

  return (
    <div>
      <div className="page-topbar">
        <div>
          <h1 className="page-title">Validation Details</h1>
        </div>
        <button className="btn btn-outline btn-sm" onClick={() => navigate(backTarget)}>
          <ArrowLeft size={14} /> Back
        </button>
      </div>

      <div className="page-content">
        {loading ? (
          <div className="card p-8 text-center">
            <span className="spinner inline-block"></span>
          </div>
        ) : error ? (
          <div className="alert alert-error">{error}</div>
        ) : !detailRow ? (
          <div className="card p-8 text-center text-gray-500">No details found.</div>
        ) : (
          <div className="card">
            <div className="card-body space-y-6">
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {[
                  { label: 'Validation Type', value: detailRow.validation_type || '—' },
                  { label: 'Row Count', value: detailRow.count_validation || detailRow.row_count || '—' },
                  { label: 'Schema', value: detailRow.schema_check || '—' },
                  { label: 'Numeric', value: detailRow.numeric_check || '—' },
                  { label: 'Hash', value: detailRow.hash_validation || '—' },
                ].map((item) => (
                  <div key={item.label} className="flex flex-col gap-1">
                    <span className="text-[11px] uppercase tracking-wider text-gray-500 font-semibold">{item.label}</span>
                    <StatusBadge status={item.value} />
                  </div>
                ))}
              </div>

              <div className="text-xs text-gray-500 font-mono break-all">
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
                    const nullRowsArray = detailRow?.details?.numeric?.null_rows || numericRows;
                    const nullCountRows = nullRowsArray.filter((row) => {
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
                  <>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                      <div className="p-3 border rounded-lg">Source Hash Rows: <strong>{detailRow.details.row_hash.source_hash_count ?? 0}</strong></div>
                      <div className="p-3 border rounded-lg">Target Hash Rows: <strong>{detailRow.details.row_hash.target_hash_count ?? 0}</strong></div>
                      <div className="p-3 border rounded-lg">Matched Hash Rows: <strong>{detailRow.details.row_hash.matched_hash_count ?? 0}</strong></div>
                      <div className="p-3 border rounded-lg">Difference Rows: <strong>{(detailRow.details.row_hash.source_not_in_target_count ?? 0) + (detailRow.details.row_hash.target_not_in_source_count ?? 0)}</strong></div>
                    </div>
                    {detailRow.details.row_hash.mode === 'categorical' && (
                      <div className="mb-4">
                        <div className="text-xs font-semibold text-gray-600 mb-2">
                          Categorical Hash Groups: {(detailRow.details.row_hash.categorical_columns || []).join(', ')}
                        </div>
                        <div className="overflow-x-auto">
                          <table className="data-table">
                            <thead>
                              <tr>
                                {(detailRow.details.row_hash.categorical_columns || []).map((c) => <th key={c}>{c}</th>)}
                                <th>Source Rows</th>
                                <th>Target Rows</th>
                                <th>Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(detailRow.details.row_hash.categories || []).map((row, i) => (
                                <tr key={i}>
                                  {(detailRow.details.row_hash.categorical_columns || []).map((c) => (
                                    <td key={c} className="font-mono text-xs">{row.category_values?.[c] ?? '—'}</td>
                                  ))}
                                  <td>{row.source_row_count ?? 0}</td>
                                  <td>{row.target_row_count ?? 0}</td>
                                  <td>{row.status}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                    <div className="p-3 border rounded-lg bg-gray-50 text-sm mb-4">
                      <span className="font-semibold text-gray-700">Not matched columns: </span>
                      {(detailRow.details.row_hash.mismatched_columns || []).length > 0
                        ? detailRow.details.row_hash.mismatched_columns.join(', ')
                        : 'None'}
                    </div>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      <div>
                        <div className="text-xs font-semibold text-red-600 mb-2">
                          Source not in Target ({detailRow.details?.row_hash?.source_not_in_target_count ?? 0})
                        </div>
                        {detailRow.details?.row_hash?.source_not_in_target_rows?.length ? (
                          <div className="overflow-x-auto">
                            <table className="data-table">
                              <thead>
                                <tr>
                                  {(detailRow.details?.row_hash?.columns || []).map((c) => (
                                    <th key={c}>{c}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {detailRow.details.row_hash.source_not_in_target_rows.map((row, i) => (
                                  <tr key={i}>
                                    {(detailRow.details?.row_hash?.columns || []).map((c) => (
                                      <td key={c} className="font-mono text-xs">{row?.[c] ?? '—'}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : <div className="text-sm text-gray-500">No rows.</div>}
                      </div>
                      <div>
                        <div className="text-xs font-semibold text-red-600 mb-2">
                          Target not in Source ({detailRow.details?.row_hash?.target_not_in_source_count ?? 0})
                        </div>
                        {detailRow.details?.row_hash?.target_not_in_source_rows?.length ? (
                          <div className="overflow-x-auto">
                            <table className="data-table">
                              <thead>
                                <tr>
                                  {(detailRow.details?.row_hash?.columns || []).map((c) => (
                                    <th key={c}>{c}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {detailRow.details.row_hash.target_not_in_source_rows.map((row, i) => (
                                  <tr key={i}>
                                    {(detailRow.details?.row_hash?.columns || []).map((c) => (
                                      <td key={c} className="font-mono text-xs">{row?.[c] ?? '—'}</td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : <div className="text-sm text-gray-500">No rows.</div>}
                      </div>
                    </div>
                  </>
                ) : <div className="text-sm text-gray-500">No hash details available.</div>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
