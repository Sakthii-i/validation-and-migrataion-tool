import { useEffect, useState } from 'react';
import { useConnection } from '../context/ConnectionContext';
import { useAuth } from '../context/AuthContext';
import { metadataAPI, validationAPI, schemaAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import {
  Plug, PlugZap, Database, Server, FolderSearch, FileText, Upload, Settings2,
  Play, Loader2, CheckCircle2, XCircle, ChevronDown, Plus, Trash2, Eye
} from 'lucide-react';

const toPayloadSettings = (settings) => {
  const rawPercent = Number(settings.threshold);
  const safePercent = Number.isFinite(rawPercent) ? Math.max(0, Math.min(100, rawPercent)) : 99;
  return {
    ...settings,
    threshold: safePercent / 100,
    categoricalColumns: Array.isArray(settings.categoricalColumns) ? settings.categoricalColumns.join(',') : settings.categoricalColumns,
  };
};

const MAX_ROW_HASH_ROWS = 1000000;

const parseMetricList = (value) => String(value || '')
  .split(',')
  .map((item) => item.trim().toLowerCase())
  .filter(Boolean);

const isRowHashEnabled = (row, fallbackSettings = {}) => {
  const validationType = String(row?.validation_type || 'shallow').trim().toLowerCase();
  if (validationType === 'shallow') return false;

  const metrics = parseMetricList(row?.metrics);
  if (metrics.length > 0) {
    return metrics.includes('hash');
  }

  return Boolean(fallbackSettings.hash);
};

const getCategoricalColumnsList = (value) => {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }

  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
};

const requiresCategoricalColumnsForHash = (settings) => (
  settings.validationType === 'deep'
  && settings.hash
  && typeof settings.sourceRowCount === 'number'
  && settings.sourceRowCount > MAX_ROW_HASH_ROWS
  && getCategoricalColumnsList(settings.categoricalColumns).length === 0
);

const parseCsvText = (text) => {
  const rows = [];
  const lines = String(text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .filter((line) => line.trim().length > 0);

  if (!lines.length) {
    return { headers: [], rows: [] };
  }

  const parseLine = (line) => {
    const values = [];
    let current = '';
    let inQuotes = false;

    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      const next = line[index + 1];

      if (char === '"' && inQuotes && next === '"') {
        current += '"';
        index += 1;
      } else if (char === '"') {
        inQuotes = !inQuotes;
      } else if (char === ',' && !inQuotes) {
        values.push(current);
        current = '';
      } else {
        current += char;
      }
    }

    values.push(current);
    return values.map((value) => value.trim());
  };

  const headers = parseLine(lines[0]);
  for (let lineIndex = 1; lineIndex < lines.length; lineIndex += 1) {
    const values = parseLine(lines[lineIndex]);
    const row = {};
    headers.forEach((header, headerIndex) => {
      row[header] = values[headerIndex] ?? '';
    });
    rows.push(row);
  }

  return { headers, rows };
};

const serializeCsvRows = (headers, rows) => {
  const csvHeaders = Array.isArray(headers) ? headers : [];
  const escapeCell = (value) => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const lines = [csvHeaders.join(',')];

  rows.forEach((row) => {
    lines.push(csvHeaders.map((header) => escapeCell(row?.[header] ?? '')).join(','));
  });

  return lines.join('\n');
};

// ═══════════════════════════════════════
// CREDENTIALS SECTION
// ═══════════════════════════════════════
function CredentialsSection() {
  return null;
  const {
    sourceEngine, setSourceEngine, sourceCreds, setSourceCreds,
    targetCreds, setTargetCreds, 
    connect, disconnect,
    connectionStatus, isConnected, error
  } = useConnection();

  const updateSource = (field, value) => setSourceCreds(prev => ({ ...prev, [field]: value }));
  const updateTarget = (field, value) => setTargetCreds(prev => ({ ...prev, [field]: value }));

  return (
    <CollapsibleSection title="🔐 Connection & Credentials" icon={<Plug size={16} />}>
      {/* Engine Selector */}
      <div className="form-group mb-6">
        <label className="form-label">Source Compute Engine</label>
        <select
          className="form-select"
          value={sourceEngine}
          onChange={(e) => { setSourceEngine(e.target.value); disconnect(); }}
        >
          <option>BigQuery</option>
          <option>Snowflake</option>
        </select>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Source */}
        <div className="card">
          <div className="card-header">
            <Database size={16} className="text-primary-600" />
            Source — {sourceEngine}
          </div>
          <div className="card-body space-y-3">
            {sourceEngine === 'BigQuery' ? (
              <>
                <div className="form-group">
                  <label className="form-label">GCP Project ID</label>
                  <input className="form-input" value={sourceCreds.project_id} onChange={e => updateSource('project_id', e.target.value)} placeholder="my-gcp-project" />
                </div>
                <div className="form-group">
                  <label className="form-label">Dataset Location</label>
                  <input className="form-input" value={sourceCreds.dataset_location} onChange={e => updateSource('dataset_location', e.target.value)} placeholder="US" />
                </div>
                <div className="form-group">
                  <label className="form-label">Service Account Key Path</label>
                  <input className="form-input" value={sourceCreds.bq_key_path} onChange={e => updateSource('bq_key_path', e.target.value)} placeholder="/path/to/key.json" />
                </div>
              </>
            ) : (
              null
            )}
          </div>
        </div>

        {/* Target */}
        {sourceEngine === 'BigQuery' ? (
          <div className="card">
            <div className="card-header">
              <Server size={16} className="text-primary-600" />
              Target — Databricks
            </div>
            <div className="card-body space-y-3">
              <div className="form-group">
                <label className="form-label">Server Hostname</label>
                <input className="form-input" value={targetCreds.server_hostname} onChange={e => updateTarget('server_hostname', e.target.value)} placeholder="adb-xxxx.azuredatabricks.net" />
              </div>
              <div className="form-group">
                <label className="form-label">HTTP Path</label>
                <input className="form-input" value={targetCreds.http_path} onChange={e => updateTarget('http_path', e.target.value)} placeholder="/sql/1.0/warehouses/xxxx" />
              </div>
              <div className="form-group">
                <label className="form-label">Access Token</label>
                <input className="form-input" type="password" value={targetCreds.access_token} onChange={e => updateTarget('access_token', e.target.value)} placeholder="dapi..." />
              </div>
            </div>
          </div>
        ) : (
          <div className="card">
            <div className="card-header">
              <Server size={16} className="text-primary-600" />
              Target — Databricks
            </div>
            <div className="card-body" />
          </div>
        )}
      </div>

      {error && <div className="alert alert-error mt-4">{error}</div>}

      <button
        className={`btn ${isConnected ? 'btn-danger' : 'btn-primary'} btn-full btn-lg mt-5`}
        onClick={isConnected ? disconnect : connect}
        disabled={connectionStatus === 'connecting'}
      >
        {connectionStatus === 'connecting' ? (
          <><Loader2 size={18} className="animate-spin" /> Connecting...</>
        ) : isConnected ? (
          <><PlugZap size={18} /> Disconnect</>
        ) : (
          <><Plug size={18} /> Establish Connections</>
        )}
      </button>
    </CollapsibleSection>
  );
}

// ═══════════════════════════════════════
// VALIDATION SETTINGS
// ═══════════════════════════════════════
function BigQueryCredentialsSection() {
  const {
    sourceEngine, sourceCreds, setSourceCreds,
    connect, connectionStatus, isConnected, error,
  } = useConnection();

  if (sourceEngine !== 'BigQuery') return null;

  const updateSource = (field, value) => setSourceCreds(prev => ({ ...prev, [field]: value }));

  return (
    <CollapsibleSection title="BigQuery Credentials" icon={<Database size={16} />}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="form-group">
          <label className="form-label">GCP Project ID</label>
          <input className="form-input" value={sourceCreds.project_id} onChange={e => updateSource('project_id', e.target.value)} placeholder="my-gcp-project" />
        </div>
        <div className="form-group">
          <label className="form-label">Dataset Location</label>
          <input className="form-input" value={sourceCreds.dataset_location} onChange={e => updateSource('dataset_location', e.target.value)} placeholder="US" />
        </div>
        <div className="form-group">
          <label className="form-label">Service Account Key Path</label>
          <input className="form-input" value={sourceCreds.bq_key_path} onChange={e => updateSource('bq_key_path', e.target.value)} placeholder="/path/to/key.json" />
        </div>
      </div>

      {error && <div className="alert alert-error mt-4">{error}</div>}

      <button
        className="btn btn-primary btn-full btn-lg mt-5"
        onClick={connect}
        disabled={connectionStatus === 'connecting'}
      >
        {connectionStatus === 'connecting' ? (
          <><Loader2 size={18} className="animate-spin" /> Loading BigQuery Credentials...</>
        ) : isConnected ? (
          <><CheckCircle2 size={18} /> BigQuery Credentials Loaded</>
        ) : (
          <><Database size={18} /> Use BigQuery Credentials</>
        )}
      </button>
    </CollapsibleSection>
  );
}

function ValidationSettings({ settings, setSettings }) {
  const hashSelected = settings.validationType === 'deep' && settings.hash;
  const needsCategoricalColumns = requiresCategoricalColumnsForHash(settings);

  return (
    <CollapsibleSection title="⚙️ Validation Settings" icon={<Settings2 size={16} />} defaultOpen={true}>
      {/* Type */}
      <div className="flex items-center gap-4 mb-4">
        <label className="form-label mb-0">Validation Type:</label>
        <div className="flex rounded-lg overflow-hidden border border-gray-300">
          {['shallow', 'deep'].map(t => (
            <button
              key={t}
              className={`px-5 py-2 text-sm font-medium transition-colors ${
                settings.validationType === t
                  ? 'bg-primary-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setSettings(p => ({ ...p, validationType: t }))}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {settings.validationType === 'shallow' && (
        <div className="alert alert-info mb-4">
          Shallow mode automatically runs <strong>Row Count</strong> and <strong>Schema</strong> validation.
        </div>
      )}

      {/* Deep Checkboxes */}
      {settings.validationType === 'deep' && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          {[
            { key: 'rowCount', label: 'Row Count' },
            { key: 'schema', label: 'Schema' },
            { key: 'numeric', label: 'Numeric Stats' },
            { key: 'hash', label: 'Row Hash' },
          ].map(({ key, label }) => (
            <label key={key} className="flex items-center gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50">
              <input
                type="checkbox"
                className="form-checkbox"
                checked={settings[key]}
                onChange={e => setSettings(p => ({ ...p, [key]: e.target.checked }))}
              />
              <span className="text-sm">{label}</span>
            </label>
          ))}
        </div>
      )}

      {/* Large Table Warning for Deep Hash */}
      {hashSelected && settings.validationType === 'deep' && typeof settings.sourceRowCount === 'number' && settings.sourceRowCount > 1000000 && (
        <div className="mb-4 p-4 border border-warning-200 bg-warning-50 rounded-lg">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 text-warning-600">⚠️</div>
            <div className="flex-1">
              <h4 className="text-sm font-semibold text-warning-900 mb-1">Large Table Detected ({settings.sourceRowCount.toLocaleString()} rows)</h4>
              <p className="text-xs text-warning-700 mb-3">
                Tables over 1,000,000 rows require 1-2 Categorical Columns to optimize hash validation performance via grouped aggregation.
              </p>
              <div className="form-group mb-0">
                <label className="form-label text-warning-900">Categorical Columns (Required)</label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 p-3 border border-warning-200 rounded-lg max-h-48 overflow-y-auto bg-white">
                  {(settings.availablePrimaryKeyColumns || []).map((col) => {
                    const selected = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean).includes(col);
                    return (
                      <label key={col} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-warning-50 p-1.5 rounded transition-colors">
                        <input
                          type="checkbox"
                          className="form-checkbox rounded text-warning-600 focus:ring-warning-500"
                          checked={selected}
                          onChange={(e) => {
                            const current = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean);
                            const next = e.target.checked ? [...current, col] : current.filter(c => c !== col);
                            setSettings((p) => ({ ...p, categoricalColumns: next.join(', ') }));
                          }}
                        />
                        <span className="truncate" title={col}>{col}</span>
                      </label>
                    );
                  })}
                  {(!settings.availablePrimaryKeyColumns || settings.availablePrimaryKeyColumns.length === 0) && (
                    <div className="col-span-full py-2 text-center text-warning-600 text-xs italic">
                      Select a source table to load columns.
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {needsCategoricalColumns && (
        <div className="alert alert-warning mb-4">
          Categorical columns are required before running hash validation on tables over 1,000,000 rows.
        </div>
      )}

      {/* Advanced Options */}
      <details className="border border-gray-200 rounded-lg">
        <summary className="px-4 py-2.5 text-sm font-medium text-gray-600 cursor-pointer hover:bg-gray-50 rounded-lg">
          Advanced Options
        </summary>
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-gray-100">
          {/* Threshold */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="form-checkbox" checked={settings.useThreshold} onChange={e => setSettings(p => ({ ...p, useThreshold: e.target.checked }))} />
            <span className="text-sm">Use acceptable threshold for passing</span>
          </label>
          {settings.useThreshold && (
            <div className="form-group ml-6">
              <label className="form-label">Threshold (%)</label>
              <input type="number" className="form-input w-40" value={settings.threshold} min="0" max="100" step="1"
                onChange={e => setSettings(p => ({ ...p, threshold: e.target.value }))} />
              <span className="form-hint">e.g., 99 means 99% match</span>
            </div>
          )}

          {/* Timestamp Toggle */}
          {hashSelected && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="form-checkbox" checked={settings.includeTimestamp} onChange={e => setSettings(p => ({ ...p, includeTimestamp: e.target.checked }))} />
              <span className="text-sm">Include TIMESTAMP columns in row hash</span>
            </label>
          )}

          {/* Case Sensitive */}
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" className="form-checkbox" checked={settings.caseSensitive} onChange={e => setSettings(p => ({ ...p, caseSensitive: e.target.checked }))} />
            <span className="text-sm">Case-sensitive schema validation</span>
          </label>

          {/* Categorical Columns (Optional when small) */}
          {hashSelected && (!settings.sourceRowCount || settings.sourceRowCount <= 1000000) && (
            <div className="form-group pt-2 border-t border-gray-100 mt-2">
              <label className="form-label">Categorical Columns (Optimization)</label>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 p-3 border border-gray-200 rounded-lg max-h-48 overflow-y-auto bg-gray-50/30">
                {(settings.availablePrimaryKeyColumns || []).map(col => {
                  const selected = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean).includes(col);
                  return (
                    <label key={`cat-${col}`} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-white p-1.5 rounded transition-colors">
                      <input
                        type="checkbox"
                        className="form-checkbox rounded text-primary-600 focus:ring-primary-500"
                        checked={selected}
                        onChange={(e) => {
                          const currentKeys = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean);
                          const nextKeys = e.target.checked
                            ? [...currentKeys, col]
                            : currentKeys.filter(k => k !== col);
                          setSettings(p => ({ ...p, categoricalColumns: nextKeys.join(', ') }));
                        }}
                      />
                      <span className="truncate" title={col}>{col}</span>
                    </label>
                  );
                })}
                {(!settings.availablePrimaryKeyColumns || settings.availablePrimaryKeyColumns.length === 0) && (
                  <div className="col-span-full py-4 text-center text-gray-400 text-xs italic">
                    Select a source table to see available columns.
                  </div>
                )}
              </div>
              <span className="form-hint">Select 1 or 2 low-cardinality columns to optimize hash comparison.</span>
            </div>
          )}

        </div>
      </details>
    </CollapsibleSection>
  );
}

// ═══════════════════════════════════════
// TAB: BROWSE & SELECT
// ═══════════════════════════════════════
function BrowseTab({ settings, setSettings }) {
  const { isConnected, sessionId, sourceEngine } = useConnection();
  const { user } = useAuth();
  const [srcCatalogs, setSrcCatalogs] = useState([]);
  const [srcSchemas, setSrcSchemas] = useState([]);
  const [srcTables, setSrcTables] = useState([]);
  const [tgtCatalogs, setTgtCatalogs] = useState([]);
  const [tgtSchemas, setTgtSchemas] = useState([]);
  const [tgtTables, setTgtTables] = useState([]);
  const [selectedSrcCatalog, setSelectedSrcCatalog] = useState('');
  const [selectedSrcSchema, setSelectedSrcSchema] = useState('');
  const [selectedSrcTable, setSelectedSrcTable] = useState('');
  const [selectedTgtCatalog, setSelectedTgtCatalog] = useState('');
  const [selectedTgtSchema, setSelectedTgtSchema] = useState('');
  const [selectedTgtTable, setSelectedTgtTable] = useState('');
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);
  const [useSeparateWhere, setUseSeparateWhere] = useState(false);
  const [whereClause, setWhereClause] = useState('1=1');
  const [sourceWhereClause, setSourceWhereClause] = useState('1=1');
  const [targetWhereClause, setTargetWhereClause] = useState('1=1');
  const [tablePairs, setTablePairs] = useState([]);
  const [overridePerTable, setOverridePerTable] = useState(false);
  const [tableValidationOverrides, setTableValidationOverrides] = useState({});

  const loadCatalogs = async (target) => {
    try {
      const res = await metadataAPI.getCatalogs(target, sessionId);
      return res.data.catalogs || [];
    } catch { return []; }
  };

  const loadSchemas = async (target, catalog) => {
    try {
      const res = await metadataAPI.getSchemas(target, catalog, sessionId);
      return res.data.schemas || [];
    } catch { return []; }
  };

  const loadTables = async (target, catalog, schema) => {
    try {
      const res = await metadataAPI.getTables(target, catalog, schema, sessionId);
      return res.data.tables || [];
    } catch { return []; }
  };

  const ensureCatalogs = async (target) => {
    if (target === 'source' && !srcCatalogs.length) {
      setSrcCatalogs(await loadCatalogs('source'));
    }
    if (target === 'target' && !tgtCatalogs.length) {
      setTgtCatalogs(await loadCatalogs('target'));
    }
  };

  useEffect(() => {
    if (!selectedSrcCatalog) {
      setSrcSchemas([]);
      setSelectedSrcSchema('');
      setSrcTables([]);
      setSelectedSrcTable('');
      return;
    }
    (async () => {
      const schemas = await loadSchemas('source', selectedSrcCatalog);
      setSrcSchemas(schemas);
      setSelectedSrcSchema('');
      setSrcTables([]);
      setSelectedSrcTable('');
    })();
  }, [selectedSrcCatalog]);

  useEffect(() => {
    if (!selectedSrcCatalog || !selectedSrcSchema) {
      setSrcTables([]);
      setSelectedSrcTable('');
      return;
    }
    (async () => {
      const tables = await loadTables('source', selectedSrcCatalog, selectedSrcSchema);
      setSrcTables(tables);
      setSelectedSrcTable('');
    })();
  }, [selectedSrcCatalog, selectedSrcSchema]);

  useEffect(() => {
    if (!selectedSrcCatalog || !selectedSrcSchema || !selectedSrcTable) {
      setSettings(prev => ({ ...prev, availablePrimaryKeyColumns: [], sourceRowCount: null }));
      return;
    }

    (async () => {
      try {
        const tablePath = `${selectedSrcCatalog}.${selectedSrcSchema}.${selectedSrcTable}`;
        const [res, countRes] = await Promise.all([
          schemaAPI.getSchema(sourceEngine, tablePath),
          metadataAPI.getRowCount(sourceEngine, selectedSrcCatalog, selectedSrcSchema, selectedSrcTable, sessionId).catch(() => ({ data: { row_count: 0 } }))
        ]);
        const cols = (res.data.columns || [])
          .map(col => col.column_name || col.COLUMN_NAME || col.name)
          .filter(Boolean);
        const count = countRes.data.row_count || countRes.data.ROW_COUNT || 0;
        setSettings(prev => ({
          ...prev,
          availablePrimaryKeyColumns: cols,
          sourceRowCount: count,
          primaryKeys: prev.primaryKeys
            ? prev.primaryKeys.split(',').map(value => value.trim()).filter(Boolean).every(value => cols.includes(value))
              ? prev.primaryKeys
              : ''
            : '',
          categoricalColumns: prev.categoricalColumns
            ? prev.categoricalColumns.split(',').map(v => v.trim()).filter(Boolean).filter(v => cols.includes(v)).join(', ')
            : '',
        }));
      } catch {
        setSettings(prev => ({ ...prev, availablePrimaryKeyColumns: [], sourceRowCount: null }));
      }
    })();
  }, [selectedSrcCatalog, selectedSrcSchema, selectedSrcTable, sourceEngine, setSettings, sessionId]);

  useEffect(() => {
    if (!selectedTgtCatalog) {
      setTgtSchemas([]);
      setSelectedTgtSchema('');
      setTgtTables([]);
      setSelectedTgtTable('');
      return;
    }
    (async () => {
      const schemas = await loadSchemas('target', selectedTgtCatalog);
      setTgtSchemas(schemas);
      setSelectedTgtSchema('');
      setTgtTables([]);
      setSelectedTgtTable('');
    })();
  }, [selectedTgtCatalog]);

  useEffect(() => {
    if (!selectedTgtCatalog || !selectedTgtSchema) {
      setTgtTables([]);
      setSelectedTgtTable('');
      return;
    }
    (async () => {
      const tables = await loadTables('target', selectedTgtCatalog, selectedTgtSchema);
      setTgtTables(tables);
      setSelectedTgtTable('');
    })();
  }, [selectedTgtCatalog, selectedTgtSchema]);

  const currentPairReady = !!(selectedSrcCatalog && selectedSrcSchema && selectedSrcTable && selectedTgtCatalog && selectedTgtSchema && selectedTgtTable);

  const currentPair = currentPairReady ? {
    source: `${selectedSrcCatalog}.${selectedSrcSchema}.${selectedSrcTable}`,
    target: `${selectedTgtCatalog}.${selectedTgtSchema}.${selectedTgtTable}`,
    source_where: useSeparateWhere ? (sourceWhereClause || '1=1') : (whereClause || '1=1'),
    target_where: useSeparateWhere ? (targetWhereClause || '1=1') : (whereClause || '1=1'),
  } : null;

  const addCurrentPair = () => {
    if (!currentPair) return;
    setTablePairs(prev => {
      const exists = prev.some(p => p.source === currentPair.source && p.target === currentPair.target);
      return exists ? prev : [...prev, currentPair];
    });
  };

  const handleRun = async () => {
    const pairs = tablePairs.length ? tablePairs : (currentPair ? [currentPair] : []);

    if (!pairs.length) {
      setResults({ error: 'Select source and target catalog, schema, and table.' });
      return;
    }

    const baseSettings = toPayloadSettings(settings);

    if (settings.validationType === 'deep' && settings.hash && settings.colDiffEnabled && !(settings.primaryKeys || '').trim()) {
      setResults({ error: 'Primary key is required when column-level diff is enabled for hash validation.' });
      return;
    }

    if (requiresCategoricalColumnsForHash(settings)) {
      setResults({ error: 'Categorical columns are required for hash validation when the source table has more than 1,000,000 rows.' });
      return;
    }

    setRunning(true);
    try {
      if (overridePerTable && pairs.length > 1 && settings.validationType === 'deep') {
        const aggregated = [];
        for (let i = 0; i < pairs.length; i += 1) {
          const o = tableValidationOverrides[i] || {};
          const overrideThresholdPercent = Number(o.threshold);
          const overrideThresholdDecimal = Number.isFinite(overrideThresholdPercent)
            ? Math.max(0, Math.min(100, overrideThresholdPercent)) / 100
            : baseSettings.threshold;
          const pairSettings = {
            ...baseSettings,
            rowCount: o.rowCount ?? baseSettings.rowCount,
            schema: o.schema ?? baseSettings.schema,
            numeric: o.numeric ?? baseSettings.numeric,
            hash: o.hash ?? baseSettings.hash,
            caseSensitive: o.caseSensitive ?? baseSettings.caseSensitive,
            useThreshold: o.useThreshold ?? baseSettings.useThreshold,
            threshold: (o.useThreshold ?? baseSettings.useThreshold) ? overrideThresholdDecimal : baseSettings.threshold,
            includeTimestamp: o.includeTimestamp ?? baseSettings.includeTimestamp,
            colDiffEnabled: o.colDiffEnabled ?? baseSettings.colDiffEnabled,
            primaryKeys: o.primaryKeys ?? baseSettings.primaryKeys,
          };

          if (pairSettings.hash && pairSettings.colDiffEnabled && !(pairSettings.primaryKeys || '').trim()) {
            setResults({ error: `Primary key is required for table ${i + 1} when hash + column-level mismatch is enabled.` });
            setRunning(false);
            return;
          }

          const res = await validationAPI.run({
            session_id: sessionId,
            validation_type: settings.validationType,
            run_by: user?.username || undefined,
            table_pairs: [pairs[i]],
            settings: pairSettings,
          });
          aggregated.push(...(res.data?.results || []));
        }
        setResults({ results: aggregated });
      } else {
        const res = await validationAPI.run({
          session_id: sessionId,
          validation_type: settings.validationType,
          run_by: user?.username || undefined,
          table_pairs: pairs,
          settings: baseSettings,
        });
        setResults(res.data);
      }
    } catch (e) {
      setResults({ error: e.response?.data?.detail || e.message });
    } finally {
      setRunning(false);
    }
  };

  if (!isConnected) {
    return <div className="alert alert-info">🔌 Please establish connections first to browse catalogs.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Source */}
        <div className="card">
          <div className="card-header"><Database size={16} className="text-primary-600" /> Source — {sourceEngine}</div>
          <div className="card-body space-y-3">
            <div className="form-group">
              <label className="form-label">Catalog</label>
              <select className="form-select" value={selectedSrcCatalog} onFocus={() => ensureCatalogs('source')} onChange={e => setSelectedSrcCatalog(e.target.value)}>
                <option value="">Select catalog...</option>
                {srcCatalogs.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            {selectedSrcCatalog && (
              <div className="form-group">
                <label className="form-label">Schema</label>
                <select className="form-select" value={selectedSrcSchema} onChange={e => setSelectedSrcSchema(e.target.value)}>
                  <option value="">Select schema...</option>
                  {srcSchemas.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>
            )}
            {selectedSrcSchema && (
              <div className="form-group">
                <label className="form-label">Table</label>
                <select className="form-select" value={selectedSrcTable} onChange={e => setSelectedSrcTable(e.target.value)}>
                  <option value="">Select table...</option>
                  {srcTables.map(t => <option key={t}>{t}</option>)}
                </select>
              </div>
            )}
          </div>
        </div>

        {/* Target */}
        <div className="card">
          <div className="card-header"><Server size={16} className="text-primary-600" /> Target — Databricks</div>
          <div className="card-body space-y-3">
            <div className="form-group">
              <label className="form-label">Catalog</label>
              <select className="form-select" value={selectedTgtCatalog} onFocus={() => ensureCatalogs('target')} onChange={e => setSelectedTgtCatalog(e.target.value)}>
                <option value="">Select catalog...</option>
                {tgtCatalogs.map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            {selectedTgtCatalog && (
              <div className="form-group">
                <label className="form-label">Schema</label>
                <select className="form-select" value={selectedTgtSchema} onChange={e => setSelectedTgtSchema(e.target.value)}>
                  <option value="">Select schema...</option>
                  {tgtSchemas.map(s => <option key={s}>{s}</option>)}
                </select>
              </div>
            )}
            {selectedTgtSchema && (
              <div className="form-group">
                <label className="form-label">Table</label>
                <select className="form-select" value={selectedTgtTable} onChange={e => setSelectedTgtTable(e.target.value)}>
                  <option value="">Select table...</option>
                  {tgtTables.map(t => <option key={t}>{t}</option>)}
                </select>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-outline btn-sm"
          onClick={addCurrentPair}
          disabled={!currentPairReady}
        >
          Add Selected Pair
        </button>
        {tablePairs.length > 0 && (
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => { setTablePairs([]); setTableValidationOverrides({}); }}
          >
            Clear Pairs
          </button>
        )}
      </div>

      <div className="card">
        <div className="card-header">Per-Table WHERE Conditions</div>
        <div className="card-body space-y-3">
          {tablePairs.length === 0 ? (
            <>
              <div className="text-sm font-semibold text-gray-700">
                {selectedSrcCatalog && selectedSrcSchema && selectedSrcTable ? `${selectedSrcCatalog}.${selectedSrcSchema}.${selectedSrcTable}` : 'source.table'}
                {' -> '}
                {selectedTgtCatalog && selectedTgtSchema && selectedTgtTable ? `${selectedTgtCatalog}.${selectedTgtSchema}.${selectedTgtTable}` : 'target.table'}
              </div>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="form-checkbox"
                  checked={useSeparateWhere}
                  onChange={e => setUseSeparateWhere(e.target.checked)}
                />
                <span className="text-sm">Use separate Source/Target WHERE</span>
              </label>

              {!useSeparateWhere && (
                <div className="form-group">
                  <label className="form-label">WHERE clause (applies to both source and target)</label>
                  <input
                    className="form-input"
                    value={whereClause}
                    onChange={e => setWhereClause(e.target.value)}
                    placeholder="1=1"
                  />
                </div>
              )}

              {useSeparateWhere && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="form-group">
                    <label className="form-label">Source WHERE</label>
                    <input
                      className="form-input"
                      value={sourceWhereClause}
                      onChange={e => setSourceWhereClause(e.target.value)}
                      placeholder="1=1"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Target WHERE</label>
                    <input
                      className="form-input"
                      value={targetWhereClause}
                      onChange={e => setTargetWhereClause(e.target.value)}
                      placeholder="1=1"
                    />
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="space-y-2">
              {tablePairs.map((pair, i) => (
                <div key={`${pair.source}-${pair.target}-${i}`} className="card p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-xs font-medium text-gray-600">{pair.source} → {pair.target}</div>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      onClick={() => {
                        setTablePairs(prev => prev.filter((_, idx) => idx !== i));
                        setTableValidationOverrides(prev => {
                          const next = { ...prev };
                          delete next[i];
                          return next;
                        });
                      }}
                    >
                      Remove
                    </button>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <input
                      className="form-input"
                      placeholder="Source WHERE (1=1)"
                      value={pair.source_where || ''}
                      onChange={e => setTablePairs(prev => prev.map((p, idx) => idx === i ? { ...p, source_where: e.target.value } : p))}
                    />
                    <input
                      className="form-input"
                      placeholder="Target WHERE (1=1)"
                      value={pair.target_where || ''}
                      onChange={e => setTablePairs(prev => prev.map((p, idx) => idx === i ? { ...p, target_where: e.target.value } : p))}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}

          {settings.validationType === 'deep' && settings.hash && (
            <label className="flex items-center gap-2 cursor-pointer pt-2 border-t border-gray-100">
              <input
                type="checkbox"
                className="form-checkbox"
                checked={settings.colDiffEnabled}
                onChange={e => setSettings(p => ({ ...p, colDiffEnabled: e.target.checked }))}
              />
              <span className="text-sm">Perform column-level mismatch</span>
            </label>
          )}

          {settings.validationType === 'deep' && settings.hash && settings.colDiffEnabled && (
            <div className="form-group">
              <label className="form-label">Primary Key Column(s)</label>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 p-3 border border-gray-200 rounded-lg max-h-48 overflow-y-auto bg-gray-50/30">
                {(settings.availablePrimaryKeyColumns || []).map(col => {
                  const selected = (settings.primaryKeys || '').split(',').map(v => v.trim()).filter(Boolean).includes(col);
                  return (
                    <label key={col} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-white p-1.5 rounded transition-colors">
                      <input
                        type="checkbox"
                        className="form-checkbox rounded text-primary-600 focus:ring-primary-500"
                        checked={selected}
                        onChange={(e) => {
                          const currentKeys = (settings.primaryKeys || '').split(',').map(v => v.trim()).filter(Boolean);
                          const nextKeys = e.target.checked
                            ? [...currentKeys, col]
                            : currentKeys.filter(k => k !== col);
                          setSettings(p => ({ ...p, primaryKeys: nextKeys.join(', ') }));
                        }}
                      />
                      <span className="truncate" title={col}>{col}</span>
                    </label>
                  );
                })}
                {(!settings.availablePrimaryKeyColumns || settings.availablePrimaryKeyColumns.length === 0) && (
                  <div className="col-span-full py-4 text-center text-gray-400 text-xs italic">
                    Select a source table to see available columns.
                  </div>
                )}
              </div>
              <span className="form-hint">Select one or more columns to form the primary key for join-based comparison.</span>
            </div>
          )}

          {settings.validationType === 'deep' && settings.hash && settings.sourceRowCount > 1000000 && (
            <div className="form-group pt-3 border-t border-gray-100">
              <label className="form-label text-orange-600 font-bold flex items-center gap-1">
                ⚠️ Categorical Columns (Required: Table &gt; 1M rows)
              </label>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 p-3 border border-orange-200 rounded-lg max-h-48 overflow-y-auto bg-orange-50/30">
                {(settings.availablePrimaryKeyColumns || []).map(col => {
                  const selected = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean).includes(col);
                  return (
                    <label key={`cat-req-${col}`} className="flex items-center gap-2 text-sm cursor-pointer hover:bg-white p-1.5 rounded transition-colors">
                      <input
                        type="checkbox"
                        className="form-checkbox rounded text-orange-600 focus:ring-orange-500"
                        checked={selected}
                        onChange={(e) => {
                          const currentKeys = (settings.categoricalColumns || '').split(',').map(v => v.trim()).filter(Boolean);
                          const nextKeys = e.target.checked
                            ? [...currentKeys, col]
                            : currentKeys.filter(k => k !== col);
                          setSettings(p => ({ ...p, categoricalColumns: nextKeys.join(', ') }));
                        }}
                      />
                      <span className="truncate" title={col}>{col}</span>
                    </label>
                  );
                })}
              </div>
              <span className="form-hint text-orange-700">The table has <strong>{settings.sourceRowCount.toLocaleString()}</strong> rows. To avoid extremely long validation times, please select 1 or 2 low-cardinality columns (e.g., status, region, year) to perform a grouped hash comparison.</span>
            </div>
          )}
        </div>
      </div>

      {tablePairs.length > 1 && settings.validationType === 'deep' && (
        <div className="card p-3 space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="form-checkbox"
              checked={overridePerTable}
              onChange={e => setOverridePerTable(e.target.checked)}
            />
            <span className="text-sm">Override validations per table</span>
          </label>

          {overridePerTable && (
            <div className="space-y-2 pt-2 border-t border-gray-100">
              {tablePairs.map((pair, i) => {
                const ov = tableValidationOverrides[i] || {};
                const checks = [
                  { key: 'rowCount', label: 'Row Count' },
                  { key: 'schema', label: 'Schema' },
                  { key: 'numeric', label: 'Numeric' },
                  { key: 'hash', label: 'Hash' },
                ];
                const hashSelected = ov.hash ?? settings.hash;
                const colDiffSelected = ov.colDiffEnabled ?? settings.colDiffEnabled;
                const thresholdEnabled = ov.useThreshold ?? settings.useThreshold;
                return (
                  <div key={`${pair.source}-${pair.target}-${i}`} className="card p-2">
                    <div className="text-xs font-medium text-gray-600 mb-2">{pair.source} → {pair.target}</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {checks.map((c) => (
                        <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={ov[c.key] ?? settings[c.key]}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), [c.key]: e.target.checked },
                            }))}
                          />
                          <span>{c.label}</span>
                        </label>
                      ))}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 pt-3 border-t border-gray-100">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input
                          type="checkbox"
                          className="form-checkbox"
                          checked={ov.caseSensitive ?? settings.caseSensitive}
                          onChange={(e) => setTableValidationOverrides(prev => ({
                            ...prev,
                            [i]: { ...(prev[i] || {}), caseSensitive: e.target.checked },
                          }))}
                        />
                        <span>Case Sensitive</span>
                      </label>

                      <div className="flex items-center gap-2">
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={thresholdEnabled}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), useThreshold: e.target.checked },
                            }))}
                          />
                          <span>Threshold (%)</span>
                        </label>
                        {thresholdEnabled && (
                          <input
                            type="number"
                            className="form-input w-28"
                            min="0"
                            max="100"
                            step="1"
                            value={ov.threshold ?? settings.threshold}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), threshold: e.target.value },
                            }))}
                          />
                        )}
                      </div>
                    </div>

                    {hashSelected && (
                      <div className="space-y-2 mt-3 pt-3 border-t border-gray-100">
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={ov.includeTimestamp ?? settings.includeTimestamp}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), includeTimestamp: e.target.checked },
                            }))}
                          />
                          <span>Include TIMESTAMP columns</span>
                        </label>

                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={colDiffSelected}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), colDiffEnabled: e.target.checked },
                            }))}
                          />
                          <span>Perform column-level mismatch</span>
                        </label>

                        {colDiffSelected && (
                          <div className="form-group">
                            <label className="form-label">Primary Key Column</label>
                            <select
                              className="form-select"
                              value={(((ov.primaryKeys ?? settings.primaryKeys) || '').split(',').map(v => v.trim()).filter(Boolean)[0] || '')}
                              onChange={(e) => setTableValidationOverrides(prev => ({
                                ...prev,
                                [i]: { ...(prev[i] || {}), primaryKeys: e.target.value },
                              }))}
                              disabled={!settings.availablePrimaryKeyColumns?.length}
                            >
                              <option value="">Select primary key column...</option>
                              {(settings.availablePrimaryKeyColumns || []).map(col => (
                                <option key={col} value={col}>{col}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running || requiresCategoricalColumnsForHash(settings)}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running Validations...</> : <><Play size={18} /> Run Browse Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: MANUAL ENTRY
// ═══════════════════════════════════════
function ManualTab({ settings, setSettings }) {
  const { isConnected, sessionId, sourceEngine } = useConnection();
  const { user } = useAuth();
  const [srcPaths, setSrcPaths] = useState('');
  const [tgtPaths, setTgtPaths] = useState('');
  const [whereClauses, setWhereClauses] = useState({});
  const [overridePerTable, setOverridePerTable] = useState(false);
  const [tableValidationOverrides, setTableValidationOverrides] = useState({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);
  const needsCategoricalColumns = requiresCategoricalColumnsForHash(settings);

  const parsePaths = (raw) => raw.replace(/,/g, '\n').split('\n').map(p => p.trim()).filter(Boolean);
  const normalizeSourcePath = (path) => (sourceEngine === 'Snowflake' ? String(path || '').toUpperCase() : path);
  const normalizeSourceInput = (text) => (sourceEngine === 'Snowflake' ? String(text || '').toUpperCase() : text);

  const srcList = parsePaths(srcPaths).map(normalizeSourcePath);
  const tgtList = parsePaths(tgtPaths);
  const pairsValid = srcList.length > 0 && srcList.length === tgtList.length;

  useEffect(() => {
    if (!(settings.validationType === 'deep' && settings.hash && settings.colDiffEnabled)) {
      return;
    }

    const firstSourceTable = srcList[0];
    if (!firstSourceTable || firstSourceTable.split('.').length < 3) {
      setSettings(prev => ({ ...prev, availablePrimaryKeyColumns: [], primaryKeys: '' }));
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const res = await schemaAPI.getSchema(sourceEngine, firstSourceTable);
        const cols = (res.data.columns || [])
          .map(col => col.column_name || col.COLUMN_NAME || col.name)
          .filter(Boolean);
        if (cancelled) return;
        setSettings(prev => ({
          ...prev,
          availablePrimaryKeyColumns: cols,
          primaryKeys: prev.primaryKeys && cols.includes(prev.primaryKeys) ? prev.primaryKeys : '',
        }));
      } catch {
        if (cancelled) return;
        setSettings(prev => ({ ...prev, availablePrimaryKeyColumns: [], primaryKeys: '' }));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [settings.validationType, settings.hash, settings.colDiffEnabled, srcPaths, sourceEngine, setSettings]);

  const handleRun = async () => {
    const pairs = srcList.map((s, i) => ({
      source: s,
      target: tgtList[i],
      source_where: whereClauses[`src_${i}`] || '1=1',
      target_where: whereClauses[`tgt_${i}`] || '1=1',
    }));

    const baseSettings = toPayloadSettings(settings);

    if (settings.validationType === 'deep' && settings.hash && settings.colDiffEnabled && !(settings.primaryKeys || '').trim()) {
      setResults({ error: 'Primary key is required when column-level diff is enabled for hash validation.' });
      return;
    }

    setRunning(true);
    try {
      if (overridePerTable && pairs.length > 1 && settings.validationType === 'deep') {
        const aggregated = [];
        for (let i = 0; i < pairs.length; i += 1) {
          const o = tableValidationOverrides[i] || {};
          const overrideThresholdPercent = Number(o.threshold);
          const overrideThresholdDecimal = Number.isFinite(overrideThresholdPercent)
            ? Math.max(0, Math.min(100, overrideThresholdPercent)) / 100
            : baseSettings.threshold;
          const pairSettings = {
            ...baseSettings,
            rowCount: o.rowCount ?? baseSettings.rowCount,
            schema: o.schema ?? baseSettings.schema,
            numeric: o.numeric ?? baseSettings.numeric,
            hash: o.hash ?? baseSettings.hash,
            caseSensitive: o.caseSensitive ?? baseSettings.caseSensitive,
            useThreshold: o.useThreshold ?? baseSettings.useThreshold,
            threshold: (o.useThreshold ?? baseSettings.useThreshold) ? overrideThresholdDecimal : baseSettings.threshold,
            includeTimestamp: o.includeTimestamp ?? baseSettings.includeTimestamp,
            colDiffEnabled: o.colDiffEnabled ?? baseSettings.colDiffEnabled,
            primaryKeys: o.primaryKeys ?? baseSettings.primaryKeys,
          };

          if (pairSettings.hash && pairSettings.colDiffEnabled && !(pairSettings.primaryKeys || '').trim()) {
            setResults({ error: `Primary key is required for table ${i + 1} when hash + column-level mismatch is enabled.` });
            setRunning(false);
            return;
          }

          const res = await validationAPI.run({
            session_id: sessionId,
            validation_type: settings.validationType,
            run_by: user?.username || undefined,
            table_pairs: [pairs[i]],
            settings: pairSettings,
          });
          aggregated.push(...(res.data?.results || []));
        }
        setResults({ results: aggregated });
      } else {
        const res = await validationAPI.run({
          session_id: sessionId,
          validation_type: settings.validationType,
          run_by: user?.username || undefined,
          table_pairs: pairs,
          settings: baseSettings,
        });
        setResults(res.data);
      }
    } catch (e) {
      setResults({ error: e.response?.data?.detail || e.message });
    } finally {
      setRunning(false);
    }
  };

  if (!isConnected) {
    return <div className="alert alert-info">🔌 Please establish connections first.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="form-group">
          <label className="form-label">Source Table Paths</label>
          <textarea className="form-textarea" rows={5} placeholder={"catalog.schema.table1\ncatalog.schema.table2"} value={srcPaths} onChange={e => setSrcPaths(normalizeSourceInput(e.target.value))} />
          <span className="form-hint">One per line, format: catalog.schema.table</span>
        </div>
        <div className="form-group">
          <label className="form-label">Target Table Paths</label>
          <textarea className="form-textarea" rows={5} placeholder={"workspace.default.table1\nworkspace.default.table2"} value={tgtPaths} onChange={e => setTgtPaths(e.target.value)} />
          <span className="form-hint">Must match source count ({srcList.length})</span>
        </div>
      </div>

      {settings.validationType === 'deep' && settings.hash && (
        <div className="card p-3 space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="form-checkbox"
              checked={settings.colDiffEnabled}
              onChange={e => setSettings(p => ({ ...p, colDiffEnabled: e.target.checked }))}
            />
            <span className="text-sm">Perform column-level mismatch</span>
          </label>

          {settings.colDiffEnabled && (
            <div className="pt-2 border-t border-gray-100">
              <div className="form-group">
                <label className="form-label">Primary Key Column</label>
                <select
                  className="form-select"
                  value={(settings.primaryKeys || '').split(',').map(v => v.trim()).filter(Boolean)[0] || ''}
                  onChange={e => setSettings(p => ({ ...p, primaryKeys: e.target.value }))}
                  disabled={!settings.availablePrimaryKeyColumns?.length}
                >
                  <option value="">Select primary key column...</option>
                  {(settings.availablePrimaryKeyColumns || []).map(col => (
                    <option key={col} value={col}>{col}</option>
                  ))}
                </select>
                <span className="form-hint">Dropdown is loaded from the first source table path.</span>
              </div>
            </div>
          )}
        </div>
      )}

      {pairsValid && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-gray-700">Per-Table WHERE Conditions</h3>
          {srcList.map((s, i) => (
            <div key={i} className="card p-3">
              <div className="text-xs font-medium text-gray-500 mb-2">🔹 {s} → {tgtList[i]}</div>
              <div className="grid grid-cols-2 gap-3">
                <input className="form-input" placeholder="Source WHERE (1=1)" value={whereClauses[`src_${i}`] || ''} onChange={e => setWhereClauses(p => ({ ...p, [`src_${i}`]: e.target.value }))} />
                <input className="form-input" placeholder="Target WHERE (1=1)" value={whereClauses[`tgt_${i}`] || ''} onChange={e => setWhereClauses(p => ({ ...p, [`tgt_${i}`]: e.target.value }))} />
              </div>
            </div>
          ))}
        </div>
      )}

      {pairsValid && srcList.length > 1 && settings.validationType === 'deep' && (
        <div className="card p-3 space-y-3">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="form-checkbox"
              checked={overridePerTable}
              onChange={e => setOverridePerTable(e.target.checked)}
            />
            <span className="text-sm">Override validations per table</span>
          </label>

          {overridePerTable && (
            <div className="space-y-2 pt-2 border-t border-gray-100">
              {srcList.map((s, i) => {
                const ov = tableValidationOverrides[i] || {};
                const checks = [
                  { key: 'rowCount', label: 'Row Count' },
                  { key: 'schema', label: 'Schema' },
                  { key: 'numeric', label: 'Numeric' },
                  { key: 'hash', label: 'Hash' },
                ];
                const hashSelected = ov.hash ?? settings.hash;
                const colDiffSelected = ov.colDiffEnabled ?? settings.colDiffEnabled;
                const thresholdEnabled = ov.useThreshold ?? settings.useThreshold;
                return (
                  <div key={i} className="card p-2">
                    <div className="text-xs font-medium text-gray-600 mb-2">{s} → {tgtList[i]}</div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {checks.map((c) => (
                        <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={ov[c.key] ?? settings[c.key]}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), [c.key]: e.target.checked },
                            }))}
                          />
                          <span>{c.label}</span>
                        </label>
                      ))}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 pt-3 border-t border-gray-100">
                      <label className="flex items-center gap-2 text-sm cursor-pointer">
                        <input
                          type="checkbox"
                          className="form-checkbox"
                          checked={ov.caseSensitive ?? settings.caseSensitive}
                          onChange={(e) => setTableValidationOverrides(prev => ({
                            ...prev,
                            [i]: { ...(prev[i] || {}), caseSensitive: e.target.checked },
                          }))}
                        />
                        <span>Case Sensitive</span>
                      </label>

                      <div className="flex items-center gap-2">
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={thresholdEnabled}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), useThreshold: e.target.checked },
                            }))}
                          />
                          <span>Threshold (%)</span>
                        </label>
                        {thresholdEnabled && (
                          <input
                            type="number"
                            className="form-input w-28"
                            min="0"
                            max="100"
                            step="1"
                            value={ov.threshold ?? settings.threshold}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), threshold: e.target.value },
                            }))}
                          />
                        )}
                      </div>
                    </div>

                    {hashSelected && (
                      <div className="space-y-2 mt-3 pt-3 border-t border-gray-100">
                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={ov.includeTimestamp ?? settings.includeTimestamp}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), includeTimestamp: e.target.checked },
                            }))}
                          />
                          <span>Include TIMESTAMP columns</span>
                        </label>

                        <label className="flex items-center gap-2 text-sm cursor-pointer">
                          <input
                            type="checkbox"
                            className="form-checkbox"
                            checked={colDiffSelected}
                            onChange={(e) => setTableValidationOverrides(prev => ({
                              ...prev,
                              [i]: { ...(prev[i] || {}), colDiffEnabled: e.target.checked },
                            }))}
                          />
                          <span>Perform column-level mismatch</span>
                        </label>

                        {colDiffSelected && (
                          <div className="form-group">
                            <label className="form-label">Primary Key Column</label>
                            <select
                              className="form-select"
                              value={(((ov.primaryKeys ?? settings.primaryKeys) || '').split(',').map(v => v.trim()).filter(Boolean)[0] || '')}
                              onChange={(e) => setTableValidationOverrides(prev => ({
                                ...prev,
                                [i]: { ...(prev[i] || {}), primaryKeys: e.target.value },
                              }))}
                              disabled={!settings.availablePrimaryKeyColumns?.length}
                            >
                              <option value="">Select primary key column...</option>
                              {(settings.availablePrimaryKeyColumns || []).map(col => (
                                <option key={col} value={col}>{col}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {srcList.length !== tgtList.length && srcList.length > 0 && (
        <div className="alert alert-error">❌ Source ({srcList.length}) and Target ({tgtList.length}) table counts must match.</div>
      )}

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running || !pairsValid || needsCategoricalColumns}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running...</> : <><Play size={18} /> Run Manual Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: CSV UPLOAD
// ═══════════════════════════════════════
function CSVTab({ settings, setSettings }) {
  const { isConnected, sessionId, sourceEngine } = useConnection();
  const { user } = useAuth();
  const [file, setFile] = useState(null);
  const [csvHeaders, setCsvHeaders] = useState([]);
  const [csvRows, setCsvRows] = useState([]);
  const [csvPreviewMeta, setCsvPreviewMeta] = useState({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [csvError, setCsvError] = useState(null);

  const csvNeedsCategoricalColumns = csvRows.some((row, index) => {
    const meta = csvPreviewMeta[index] || {};
    const rowCount = Number(meta.rowCount || 0);
    const selected = getCategoricalColumnsList(row.categorical_columns);
    return rowCount > MAX_ROW_HASH_ROWS && selected.length === 0 && isRowHashEnabled(row, settings);
  });

  const csvNeedsPrimaryKeys = csvRows.some((row) => (
    settings.colDiffEnabled
    && isRowHashEnabled(row, settings)
    && getCategoricalColumnsList(row.primary_keys).length === 0
  ));

  const handleFileChange = (e) => {
    const f = e.target.files[0];
    setCsvError(null);
    setResults(null);
    if (!f) {
      setFile(null);
      setCsvHeaders([]);
      setCsvRows([]);
      setCsvPreviewMeta({});
      return;
    }
    setFile(f);

    const reader = new FileReader();
    reader.onload = async (ev) => {
      try {
        const parsed = parseCsvText(ev.target.result);
        const rows = parsed.rows.map((row) => ({
          ...row,
          categorical_columns: row.categorical_columns || '',
          primary_keys: row.primary_keys || '',
        }));
        setCsvHeaders(parsed.headers);
        setCsvRows(rows);

        const entries = await Promise.all(rows.map(async (row, index) => {
          const tablePath = [row.source_catalog, row.source_schema, row.source_table]
            .map((value) => String(value || '').trim())
            .filter(Boolean)
            .join('.');

          if (!sourceEngine || !tablePath || tablePath.split('.').length !== 3) {
            return [index, { rowCount: 0, columns: [] }];
          }

          try {
            const [schemaRes, countRes] = await Promise.all([
              schemaAPI.getSchema(sourceEngine, tablePath),
              metadataAPI.getRowCount(sourceEngine, row.source_catalog, row.source_schema, row.source_table, sessionId).catch(() => ({ data: { row_count: 0 } })),
            ]);
            const columns = (schemaRes.data.columns || [])
              .map((col) => col.column_name || col.COLUMN_NAME || col.name)
              .filter(Boolean);
            const rowCount = Number(countRes.data.row_count || countRes.data.ROW_COUNT || 0);
            return [index, { rowCount, columns }];
          } catch {
            return [index, { rowCount: 0, columns: [] }];
          }
        }));

        setCsvPreviewMeta(Object.fromEntries(entries));
      } catch (err) {
        setFile(null);
        setCsvHeaders([]);
        setCsvRows([]);
        setCsvPreviewMeta({});
        setCsvError(`Invalid CSV: ${err.message}`);
      }
    };
    reader.readAsText(f);
  };

  const handleRun = async () => {
    if (csvNeedsCategoricalColumns) {
      setResults({ error: 'Source table has more than 1,000,000 rows. Select categorical columns for the affected row(s) before running hash validation.' });
      return;
    }

    if (csvNeedsPrimaryKeys) {
      setResults({ error: 'Primary key is required for rows using hash validation with column-level mismatch enabled.' });
      return;
    }

    setRunning(true);
    try {
      const form = new FormData();
      const shouldIncludeCategorical = csvRows.some((row) => getCategoricalColumnsList(row.categorical_columns).length > 0);
      const shouldIncludePrimaryKeys = csvRows.some((row) => getCategoricalColumnsList(row.primary_keys).length > 0);
      const uploadHeaders = [
        ...csvHeaders.filter((header) => !['categorical_columns', 'primary_keys'].includes(header)),
        ...(shouldIncludeCategorical ? ['categorical_columns'] : []),
        ...(shouldIncludePrimaryKeys ? ['primary_keys'] : []),
      ];
      const fileToUpload = file && csvRows.length
        ? new File([serializeCsvRows(uploadHeaders, csvRows)], file.name, { type: 'text/csv' })
        : file;
      form.append('file', fileToUpload);
      form.append('session_id', sessionId);
      form.append('settings', JSON.stringify(toPayloadSettings(settings)));
      form.append('run_by', user?.username || '');
      const res = await validationAPI.runCSV(form);
      setResults(res.data);
    } catch (e) {
      setResults({ error: e.response?.data?.detail || e.message });
    } finally {
      setRunning(false);
    }
  };

  if (!isConnected) {
    return <div className="alert alert-info">🔌 Please establish connections first.</div>;
  }

  const templateCols = ['validation_type','source_catalog','source_schema','source_table','target_catalog','target_schema','target_table','where_clause','use_separate_where','source_where','target_where','metrics','case_sensitive','include_timestamp','row_threshold'];

  return (
    <div className="space-y-6">
      {/* Template */}
      <div className="card">
        <div className="card-header"><FileText size={16} /> CSV Template Format</div>
        <div className="card-body">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead><tr>{templateCols.map(c => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                <tr>{['deep','DB','PUBLIC','TABLE1','ws','default','tbl1','1=1','no','','','row_count,schema,numeric,hash','no','yes',''].map((v,i)=><td key={i} className="text-xs">{v}</td>)}</tr>
                <tr>{['shallow','DB','PUBLIC','TABLE2','ws','default','tbl2','1=1','no','','','','no','yes','99'].map((v,i)=><td key={i} className="text-xs">{v}</td>)}</tr>
              </tbody>
            </table>
          </div>
          <button className="btn btn-outline btn-sm mt-3" onClick={() => {
            const csv = templateCols.join(',') + '\n';
            const blob = new Blob([csv], { type: 'text/csv' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = 'reconciliation_template.csv'; a.click();
          }}>
            ⬇ Download CSV Template
          </button>
        </div>
      </div>

      {/* Upload */}
      <div className="card">
        <div className="card-header"><Upload size={16} /> Upload CSV</div>
        <div className="card-body">
          <input type="file" accept=".csv" onChange={handleFileChange} className="form-input" />
          {csvRows.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <p className="text-sm text-gray-500 mb-2">Preview rows</p>
              <table className="data-table">
                <thead>
                  <tr>
                    {csvHeaders.map((header) => <th key={header}>{header}</th>)}
                    <th>row_count</th>
                    <th>categorical_columns</th>
                    {settings.colDiffEnabled && <th>primary_keys</th>}
                  </tr>
                </thead>
                <tbody>
                  {csvRows.map((row, idx) => {
                    const meta = csvPreviewMeta[idx] || {};
                    const rowCount = Number(meta.rowCount || 0);
                    const rowColumns = meta.columns || [];
                    const selectedCategorical = getCategoricalColumnsList(row.categorical_columns);
                    const selectedPrimaryKeys = getCategoricalColumnsList(row.primary_keys);
                    const rowHashEnabled = isRowHashEnabled(row, settings);
                    return (
                      <tr key={idx}>
                        {csvHeaders.map((header) => (
                          <td key={header} className="text-xs">{String(row?.[header] ?? '')}</td>
                        ))}
                        <td className="text-xs">{rowCount ? rowCount.toLocaleString() : ''}</td>
                        <td className="text-xs min-w-64">
                          {rowCount > MAX_ROW_HASH_ROWS && rowHashEnabled ? (
                            <div className="space-y-1">
                              <select
                                multiple
                                className="form-select min-h-24"
                                value={selectedCategorical}
                                onChange={(e) => {
                                  const selected = Array.from(e.target.selectedOptions).map((option) => option.value);
                                  setCsvRows((prev) => prev.map((item, itemIndex) => (
                                    itemIndex === idx ? { ...item, categorical_columns: selected.join(', ') } : item
                                  )));
                                }}
                              >
                                {rowColumns.map((col) => <option key={col} value={col}>{col}</option>)}
                              </select>
                              <div className="text-[11px] text-gray-500 italic">
                                Source table {String(row.source_catalog || '').trim()}.{String(row.source_schema || '').trim()}.{String(row.source_table || '').trim()} has {rowCount.toLocaleString()} rows.
                              </div>
                            </div>
                          ) : (
                            <span className="text-gray-400">Not required</span>
                          )}
                        </td>
                        {settings.colDiffEnabled && (
                          <td className="text-xs min-w-64">
                            {rowHashEnabled ? (
                              <select
                                multiple
                                className="form-select min-h-24"
                                value={selectedPrimaryKeys}
                                onChange={(e) => {
                                  const selected = Array.from(e.target.selectedOptions).map((option) => option.value);
                                  setCsvRows((prev) => prev.map((item, itemIndex) => (
                                    itemIndex === idx ? { ...item, primary_keys: selected.join(', ') } : item
                                  )));
                                }}
                              >
                                {rowColumns.map((col) => <option key={col} value={col}>{col}</option>)}
                              </select>
                            ) : (
                              <span className="text-gray-400">Not required</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {csvError && <div className="alert alert-error mt-4">{csvError}</div>}
        </div>
      </div>

      <div className="card p-3 space-y-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="form-checkbox"
            checked={settings.colDiffEnabled}
            onChange={e => setSettings(p => ({ ...p, colDiffEnabled: e.target.checked }))}
          />
          <span className="text-sm">Perform column-level mismatch</span>
        </label>
        {settings.colDiffEnabled && (
          <div className="form-hint text-sm text-gray-500">
            Choose primary keys per row in the preview table.
          </div>
        )}
      </div>

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running || !file || csvNeedsCategoricalColumns || csvNeedsPrimaryKeys}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running CSV Validations...</> : <><Play size={18} /> Run CSV Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: CONFIG DRIVEN
// ═══════════════════════════════════════
function ConfigTab({ settings, setSettings }) {
  const { isConnected, sessionId, sourceEngine } = useConnection();
  const { user } = useAuth();
  const [file, setFile] = useState(null);
  const [config, setConfig] = useState(null);
  const [preview, setPreview] = useState(null);
  const [previewColumns, setPreviewColumns] = useState({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [parseError, setParseError] = useState(null);
  const needsCategoricalColumns = requiresCategoricalColumnsForHash(settings);

  const jsonTemplate = {
    tables: [
      {
        validation_type: "deep",
        source_catalog: "DB",
        source_schema: "PUBLIC",
        source_table: "TABLE1",
        target_catalog: "ws",
        target_schema: "default",
        target_table: "tbl1",
        where_clause: "1=1",
        use_separate_where: "no",
        source_where: "",
        target_where: "",
        metrics: "row_count,schema,numeric,hash",
        case_sensitive: "no",
        include_timestamp: "yes",
        row_threshold: ""
      },
      {
        validation_type: "shallow",
        source_catalog: "DB",
        source_schema: "PUBLIC",
        source_table: "TABLE2",
        target_catalog: "ws",
        target_schema: "default",
        target_table: "tbl2",
        where_clause: "1=1",
        use_separate_where: "no",
        source_where: "",
        target_where: "",
        metrics: "row_count,schema",
        case_sensitive: "no",
        include_timestamp: "yes",
        row_threshold: "99"
      }
    ]
  };

  const templateFields = [
    'validation_type',
    'source_catalog',
    'source_schema',
    'source_table',
    'target_catalog',
    'target_schema',
    'target_table',
    'where_clause',
    'use_separate_where',
    'source_where',
    'target_where',
    'metrics',
    'case_sensitive',
    'include_timestamp',
    'row_threshold',
  ];

  const getCategoricalColumns = (value) => {
    if (Array.isArray(value)) {
      return value.map((item) => String(item).trim()).filter(Boolean);
    }

    return String(value || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  };

  const updatePreviewCategoricalColumns = (rowIndex, nextColumns) => {
    setConfig((prev) => {
      if (!prev || !Array.isArray(prev.tables)) return prev;
      return {
        ...prev,
        tables: prev.tables.map((row, index) => (
          index === rowIndex ? { ...row, categorical_columns: nextColumns.join(', ') } : row
        )),
      };
    });

    setPreview((prev) => (Array.isArray(prev)
      ? prev.map((row, index) => (
        index === rowIndex ? { ...row, categorical_columns: nextColumns.join(', ') } : row
      ))
      : prev));
  };

  const configNeedsCategoricalColumns = (preview || []).some((row, index) => {
    const meta = previewColumns[index] || {};
    const rowCount = Number(meta.rowCount || 0);
    return rowCount > MAX_ROW_HASH_ROWS
      && getCategoricalColumns(row?.categorical_columns).length === 0
      && isRowHashEnabled(row, settings);
  });

  const configNeedsPrimaryKeys = (preview || []).some((row) => (
    settings.colDiffEnabled
    && isRowHashEnabled(row, settings)
    && getCategoricalColumns(row?.primary_keys).length === 0
  ));

  const loadPreviewColumns = async (rows) => {
    if (!sourceEngine || !Array.isArray(rows) || !rows.length) {
      setPreviewColumns({});
      return;
    }

    const entries = await Promise.all(rows.map(async (row, index) => {
      const tablePath = [row?.source_catalog, row?.source_schema, row?.source_table]
        .map((value) => String(value || '').trim())
        .filter(Boolean)
        .join('.');

      if (!tablePath || tablePath.split('.').length !== 3) {
        return [index, []];
      }

      try {
        const [schemaRes, countRes] = await Promise.all([
          schemaAPI.getSchema(sourceEngine, tablePath),
          metadataAPI.getRowCount(sourceEngine, row?.source_catalog, row?.source_schema, row?.source_table, sessionId).catch(() => ({ data: { row_count: 0 } })),
        ]);
        const cols = (schemaRes.data.columns || [])
          .map((col) => col.column_name || col.COLUMN_NAME || col.name)
          .filter(Boolean);
        return [index, { columns: cols, rowCount: Number(countRes.data.row_count || countRes.data.ROW_COUNT || 0) }];
      } catch {
        return [index, { columns: [], rowCount: 0 }];
      }
    }));

    setPreviewColumns(Object.fromEntries(entries));
  };

  const handleFileChange = (e) => {
    const f = e.target.files[0];
    setParseError(null);
    setResults(null);
    if (!f) {
      setFile(null);
      setConfig(null);
      setPreview(null);
      return;
    }

    setFile(f);
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const parsed = JSON.parse(ev.target.result);
        const rows = Array.isArray(parsed?.tables) ? parsed.tables : [];
        setConfig(parsed);
        setPreview(rows);
        setPreviewColumns({});
        loadPreviewColumns(rows);
      } catch (err) {
        setConfig(null);
        setPreview(null);
        setPreviewColumns({});
        setParseError(`Invalid JSON: ${err.message}`);
      }
    };
    reader.readAsText(f);
  };

  const handleSubmit = async () => {
    setParseError(null);
    if (configNeedsPrimaryKeys) {
      setResults({ error: 'Primary key is required for rows using hash validation with column-level mismatch enabled.' });
      return;
    }
    if (configNeedsCategoricalColumns) {
      setResults({ error: 'One or more source tables have more than 1,000,000 rows. Select categorical columns in the preview for those rows before running hash validation.' });
      return;
    }
    if (!config) {
      setParseError('Please upload a valid JSON config file.');
      return;
    }

    setRunning(true);
    try {
      const configWithCategoricals = {
        ...config,
        tables: Array.isArray(config.tables)
          ? config.tables.map((row, index) => ({
            ...row,
            categorical_columns: preview?.[index]?.categorical_columns || row.categorical_columns || '',
            primary_keys: preview?.[index]?.primary_keys || row.primary_keys || '',
          }))
          : [],
      };
      const res = await validationAPI.runConfig({ session_id: sessionId, run_by: user?.username || undefined, config: configWithCategoricals, settings: toPayloadSettings(settings) });
      setResults(res.data);
    } catch (e) {
      setResults({ error: e.response?.data?.detail || e.message });
    } finally {
      setRunning(false);
    }
  };

  if (!isConnected) {
    return <div className="alert alert-info">🔌 Please establish connections first.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="card">
        <div className="card-header"><FileText size={16} /> JSON Template Format</div>
        <div className="card-body space-y-3">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>{templateFields.map(field => <th key={field}>{field}</th>)}</tr>
              </thead>
              <tbody>
                {(jsonTemplate.tables || []).slice(0, 2).map((row, i) => (
                  <tr key={i}>
                    {templateFields.map((field) => (
                      <td key={field} className="text-xs">{String(row?.[field] ?? '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn btn-outline btn-sm" onClick={() => {
            const blob = new Blob([JSON.stringify(jsonTemplate, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'reconciliation_template.json';
            a.click();
          }}>
            ⬇ Download JSON Template
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header"><Upload size={16} /> Upload JSON</div>
        <div className="card-body">
          <input type="file" accept=".json,application/json" onChange={handleFileChange} className="form-input" />
          {preview && (
            <div className="mt-4 overflow-x-auto">
              <p className="text-sm text-gray-500 mb-2">Preview:</p>
              <table className="data-table">
                <thead>
                  <tr>
                    {templateFields.map(field => <th key={field}>{field}</th>)}
                    <th>row_count</th>
                    <th>categorical_columns</th>
                    {settings.colDiffEnabled && <th>primary_keys</th>}
                  </tr>
                </thead>
                <tbody>
                  {preview.map((row, idx) => {
                    const meta = previewColumns[idx] || {};
                    const rowCount = Number(meta.rowCount || 0);
                    const selected = getCategoricalColumns(row?.categorical_columns);
                    const selectedPrimaryKeys = getCategoricalColumns(row?.primary_keys);
                    const rowHashEnabled = isRowHashEnabled(row, settings);

                    return (
                      <tr key={idx}>
                        {templateFields.map((field) => (
                          <td key={field} className="text-xs">
                            {Array.isArray(row?.[field]) ? row[field].join(',') : String(row?.[field] ?? '')}
                          </td>
                        ))}
                        <td className="text-xs">{rowCount ? rowCount.toLocaleString() : ''}</td>
                        <td className="text-xs min-w-64">
                          {rowCount > MAX_ROW_HASH_ROWS && rowHashEnabled ? (
                            <div className="space-y-1">
                              <select
                                multiple
                                className="form-select min-h-24"
                                value={selected}
                                onChange={(e) => {
                                  const next = Array.from(e.target.selectedOptions).map((option) => option.value);
                                  updatePreviewCategoricalColumns(idx, next);
                                }}
                              >
                                {(meta.columns || []).map((col) => (
                                  <option key={col} value={col}>{col}</option>
                                ))}
                              </select>
                              <div className="text-[11px] text-gray-500 italic">
                                Source table {String(row.source_catalog || '').trim()}.{String(row.source_schema || '').trim()}.{String(row.source_table || '').trim()} has {rowCount.toLocaleString()} rows.
                              </div>
                            </div>
                          ) : (
                            <span className="text-gray-400">Not required</span>
                          )}
                        </td>
                        {settings.colDiffEnabled && (
                          <td className="text-xs min-w-64">
                            {rowHashEnabled ? (
                              <select
                                multiple
                                className="form-select min-h-24"
                                value={selectedPrimaryKeys}
                                onChange={(e) => {
                                  const next = Array.from(e.target.selectedOptions).map((option) => option.value);
                                  setConfig((prev) => {
                                    if (!prev || !Array.isArray(prev.tables)) return prev;
                                    return {
                                      ...prev,
                                      tables: prev.tables.map((item, itemIndex) => (
                                        itemIndex === idx ? { ...item, primary_keys: next.join(', ') } : item
                                      )),
                                    };
                                  });
                                  setPreview((prev) => (Array.isArray(prev)
                                    ? prev.map((item, itemIndex) => (
                                      itemIndex === idx ? { ...item, primary_keys: next.join(', ') } : item
                                    ))
                                    : prev));
                                }}
                              >
                                {(meta.columns || []).map((col) => (
                                  <option key={col} value={col}>{col}</option>
                                ))}
                              </select>
                            ) : (
                              <span className="text-gray-400">Not required</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card p-3 space-y-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="form-checkbox"
            checked={settings.colDiffEnabled}
            onChange={e => setSettings(p => ({ ...p, colDiffEnabled: e.target.checked }))}
          />
          <span className="text-sm">Perform column-level mismatch</span>
        </label>
        {settings.colDiffEnabled && (
          <div className="form-hint text-sm text-gray-500">
            Choose primary keys per row in the preview table.
          </div>
        )}
      </div>

      {parseError && <div className="alert alert-error">{parseError}</div>}
      {configNeedsCategoricalColumns && (
        <div className="alert alert-warning">
          One or more preview rows need categorical columns because their source table has more than 1,000,000 rows.
        </div>
      )}
      {configNeedsPrimaryKeys && (
        <div className="alert alert-warning">
          One or more preview rows need primary keys because column-level mismatch is enabled for hash validation.
        </div>
      )}

      <button className="btn btn-primary btn-full btn-lg" onClick={handleSubmit} disabled={running || !file || !config || configNeedsCategoricalColumns || configNeedsPrimaryKeys}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running Config Validations...</> : <><Play size={18} /> Run Config Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// RESULTS DISPLAY
// ═══════════════════════════════════════
function ResultsDisplay({ results }) {
  const [detailRecord, setDetailRecord] = useState(null);

  if (!results) return null;

  if (results.error) {
    return <div className="alert alert-error mt-4">❌ {results.error}</div>;
  }

  const records = results.results || results.validation_ids || [];
  const isFailStatus = (status) => String(status || '').trim().toUpperCase() === 'FAIL';
  const failedRecords = Array.isArray(records)
    ? records.filter((r) => (
        isFailStatus(r.row_count || r.count_validation)
        || isFailStatus(r.schema_check)
        || isFailStatus(r.numeric_check)
        || isFailStatus(r.hash_validation)
      ))
    : [];
  const numericRows = detailRecord?.details?.numeric?.rows || [];
  const isNotSelected = (status) => {
    if (status === null || status === undefined) return true;
    const text = String(status).trim().toUpperCase();
    return text === '' || text === 'N/A' || text === 'NONE' || text === '—' || text === '-';
  };

  const rowCountStatus = detailRecord?.row_count || detailRecord?.count_validation;
  const schemaStatus = detailRecord?.schema_check;
  const numericStatus = detailRecord?.numeric_check;
  const hashStatus = detailRecord?.hash_validation;

  const rowCountNotSelected = isNotSelected(rowCountStatus);
  const schemaNotSelected = isNotSelected(schemaStatus);
  const numericNotSelected = isNotSelected(numericStatus);
  const hashNotSelected = isNotSelected(hashStatus);

  const nullRowsArray = detailRecord?.details?.numeric?.null_rows || numericRows;
  const nullCountRows = nullRowsArray.filter((row) => {
    const srcNull = Number(row?.source_null_count || 0);
    const tgtNull = Number(row?.target_null_count || 0);
    return srcNull > 0 || tgtNull > 0;
  });

  const nullCountSummary = nullCountRows
    .map((row) => {
      const srcNull = Number(row?.source_null_count || 0);
      const tgtNull = Number(row?.target_null_count || 0);
      if (srcNull > 0 && tgtNull > 0) {
        return `${row.column}: Source ${srcNull}, Target ${tgtNull}`;
      }
      if (srcNull > 0) return `${row.column}: Source ${srcNull}`;
      return `${row.column}: Target ${tgtNull}`;
    })
    .join(' | ');

  const formatNumericValue = (value) => {
    if (value === null || value === undefined || value === '') return '—';
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(4) : value;
  };

  if (records.length === 0 && !results.error) {
    return <div className="alert alert-success mt-4">🎉 Validations completed successfully!</div>;
  }

  return (
    <div className="mt-6">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">Validation Results</h3>
      {Array.isArray(records) && records.length > 0 && (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Target</th>
                <th>Type</th>
                <th>Row Count</th>
                <th>Schema</th>
                <th>Numeric</th>
                <th>Hash</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={i}>
                  <td className="font-mono text-xs">{r.src_table || r.source || '—'}</td>
                  <td className="font-mono text-xs">{r.tgt_table || r.target || '—'}</td>
                  <td><StatusBadge status={r.validation_type || '—'} /></td>
                  <td><StatusBadge status={r.row_count || r.count_validation || '—'} /></td>
                  <td><StatusBadge status={r.schema_check || '—'} /></td>
                  <td><StatusBadge status={r.numeric_check || '—'} /></td>
                  <td><StatusBadge status={r.hash_validation || '—'} /></td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-outline btn-sm"
                      title="View validation details"
                      onClick={() => setDetailRecord(r)}
                    >
                      <Eye size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {failedRecords.map((record, i) => {
        const notice = record.email_notification || {};
        const recipient = notice.recipient || record.email || 'the respective mail';
        const message = notice.sent
          ? `Validation failed. Validation report has been sent to ${recipient}.`
          : (notice.message || `Validation failed. Validation report has been sent to ${recipient}.`);

        return (
          <div
            key={`${record.validation_id || i}-email-notification`}
            className={`alert mt-3 ${notice.sent === false ? 'alert-error' : 'alert-success'}`}
          >
            {message}
          </div>
        );
      })}

      {detailRecord && (
        <div className="card mt-4">
          <div className="card-header flex items-center justify-between">
            <span>Validation Details</span>
            <button type="button" className="btn btn-outline btn-sm" onClick={() => setDetailRecord(null)}>Close</button>
          </div>
          <div className="card-body space-y-6">
            <div className="text-xs text-gray-500 font-mono">
              {detailRecord.src_table || detailRecord.source || '—'} → {detailRecord.tgt_table || detailRecord.target || '—'}
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Row Count</h4>
              {rowCountNotSelected ? (
                <div className="text-sm text-gray-500">Not selected.</div>
              ) : detailRecord.details?.row_count?.error ? (
                <div className="text-sm text-gray-500">{`No row count details available: ${detailRecord.details.row_count.error}`}</div>
              ) : detailRecord.details?.row_count ? (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="p-3 border rounded-lg">Source: <strong>{detailRecord.details.row_count.source_count}</strong></div>
                  <div className="p-3 border rounded-lg">Target: <strong>{detailRecord.details.row_count.target_count}</strong></div>
                  <div className="p-3 border rounded-lg">Difference: <strong>{detailRecord.details.row_count.difference}</strong></div>
                </div>
              ) : <div className="text-sm text-gray-500">No row count details available.</div>}
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Schema Details</h4>
              {schemaNotSelected ? (
                <div className="text-sm text-gray-500">Not selected.</div>
              ) : detailRecord.details?.schema?.rows?.length ? (
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
                      {detailRecord.details.schema.rows.map((row, idx) => (
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
              ) : <div className="text-sm text-gray-500">{detailRecord.details?.schema?.error ? `No schema details available: ${detailRecord.details.schema.error}` : 'No schema details available.'}</div>}
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Null Counts</h4>
              {numericNotSelected ? (
                <div className="text-sm text-gray-500 mb-6">Not selected.</div>
              ) : nullCountRows.length ? (
                <>
                <div className="text-sm text-gray-700 mb-2">{nullCountSummary}</div>
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
              ) : <div className="text-sm text-gray-500 mb-6">{detailRecord.details?.numeric?.error ? `No null count details available: ${detailRecord.details.numeric.error}` : 'No columns are null.'}</div>}

              <h4 className="text-sm font-semibold mb-2">Numeric Column Statistics</h4>
              {numericNotSelected ? (
                <div className="text-sm text-gray-500">Not selected.</div>
              ) : detailRecord.details?.numeric?.rows?.length ? (
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
                      {detailRecord.details.numeric.rows.map((row, idx) => (
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
              ) : <div className="text-sm text-gray-500">{detailRecord.details?.numeric?.error ? `No numeric details available: ${detailRecord.details.numeric.error}` : 'No numeric details available.'}</div>}
            </div>

            <div>
              <h4 className="text-sm font-semibold mb-2">Row Hash Differences</h4>
              {hashNotSelected ? (
                <div className="text-sm text-gray-500">Not selected.</div>
              ) : detailRecord.details?.row_hash ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    <div className="p-3 border rounded-lg">Source Hash Rows: <strong>{detailRecord.details.row_hash.source_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Target Hash Rows: <strong>{detailRecord.details.row_hash.target_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Matched Hash Rows: <strong>{detailRecord.details.row_hash.matched_hash_count ?? 0}</strong></div>
                    <div className="p-3 border rounded-lg">Difference Rows: <strong>{(detailRecord.details.row_hash.source_not_in_target_count ?? 0) + (detailRecord.details.row_hash.target_not_in_source_count ?? 0)}</strong></div>
                  </div>
                  {detailRecord.details.row_hash.mode === 'categorical' && (
                    <div className="mb-4">
                      <div className="text-xs font-semibold text-gray-600 mb-2">
                        Categorical Hash Groups: {(detailRecord.details.row_hash.categorical_columns || []).join(', ')}
                      </div>
                      <div className="overflow-x-auto">
                        <table className="data-table">
                          <thead>
                            <tr>
                              {(detailRecord.details.row_hash.categorical_columns || []).map((c) => <th key={c}>{c}</th>)}
                              <th>Source Rows</th>
                              <th>Target Rows</th>
                              <th>Source Hash Sum</th>
                              <th>Target Hash Sum</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(detailRecord.details.row_hash.categories || []).map((row, i) => (
                              <tr key={i}>
                                {(detailRecord.details.row_hash.categorical_columns || []).map((c) => (
                                  <td key={c} className="font-mono text-xs">{row.category_values?.[c] ?? '—'}</td>
                                ))}
                                <td>{row.source_row_count ?? 0}</td>
                                <td>{row.target_row_count ?? 0}</td>
                                <td className="font-mono text-xs">{row.source_hash_sum || '—'}</td>
                                <td className="font-mono text-xs">{row.target_hash_sum || '—'}</td>
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
                    {(detailRecord.details.row_hash.mismatched_columns || []).length > 0
                      ? detailRecord.details.row_hash.mismatched_columns.join(', ')
                      : 'None'}
                  </div>
                </>
              ) : <div className="text-sm text-gray-500 mb-3">{detailRecord.details?.row_hash?.error ? `No hash details available: ${detailRecord.details.row_hash.error}` : 'No hash details available.'}</div>}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-red-600 mb-2">
                    Source not in Target ({detailRecord.details?.row_hash?.source_not_in_target_count ?? 0})
                  </div>
                  {detailRecord.details?.row_hash?.source_not_in_target_rows?.length ? (
                    <div className="overflow-x-auto">
                      <table className="data-table">
                        <thead>
                          <tr>
                            {(detailRecord.details?.row_hash?.columns || []).map((c) => (
                              <th key={c}>{c}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {detailRecord.details.row_hash.source_not_in_target_rows.map((row, i) => (
                            <tr key={i}>
                              {(detailRecord.details?.row_hash?.columns || []).map((c) => (
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
                    Target not in Source ({detailRecord.details?.row_hash?.target_not_in_source_count ?? 0})
                  </div>
                  {detailRecord.details?.row_hash?.target_not_in_source_rows?.length ? (
                    <div className="overflow-x-auto">
                      <table className="data-table">
                        <thead>
                          <tr>
                            {(detailRecord.details?.row_hash?.columns || []).map((c) => (
                              <th key={c}>{c}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {detailRecord.details.row_hash.target_not_in_source_rows.map((row, i) => (
                            <tr key={i}>
                              {(detailRecord.details?.row_hash?.columns || []).map((c) => (
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
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════
export default function NewValidationPage() {
  const [activeTab, setActiveTab] = useState('browse');
  const [settings, setSettings] = useState({
    validationType: 'shallow',
    rowCount: false, schema: false, numeric: false, hash: false,
    useThreshold: false, threshold: 99,
    includeTimestamp: false, caseSensitive: false, colDiffEnabled: false,
    primaryKeys: '',
    availablePrimaryKeyColumns: [],
  });

  const tabs = [
    { key: 'browse', label: 'Browse & Select', icon: <FolderSearch size={14} /> },
    { key: 'manual', label: 'Manual Entry', icon: <FileText size={14} /> },
    { key: 'csv', label: 'Upload CSV', icon: <Upload size={14} /> },
    { key: 'config', label: 'Config Driven', icon: <Settings2 size={14} /> },
  ];

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title">Run Validation</h1>
      </div>
      <div className="page-content space-y-6">
        <BigQueryCredentialsSection />
        {activeTab !== 'csv' && activeTab !== 'config' && (
          <ValidationSettings settings={settings} setSettings={setSettings} />
        )}

        {/* Table Selection Tabs */}
        <div className="card">
          <div className="tab-bar px-2 pt-2">
            {tabs.map(({ key, label, icon }) => (
              <button
                key={key}
                className={`tab-item flex items-center gap-1.5 ${activeTab === key ? 'active' : ''}`}
                onClick={() => setActiveTab(key)}
              >
                {icon} {label}
              </button>
            ))}
          </div>
          <div className="p-5">
            {activeTab === 'browse' && <BrowseTab settings={settings} setSettings={setSettings} />}
            {activeTab === 'manual' && <ManualTab settings={settings} setSettings={setSettings} />}
            {activeTab === 'csv' && <CSVTab settings={settings} setSettings={setSettings} />}
            {activeTab === 'config' && <ConfigTab settings={settings} setSettings={setSettings} />}
          </div>
        </div>
      </div>
    </div>
  );
}
