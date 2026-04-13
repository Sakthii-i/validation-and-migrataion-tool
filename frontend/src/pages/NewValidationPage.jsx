import { useState, useCallback } from 'react';
import { useConnection } from '../context/ConnectionContext';
import { metadataAPI, validationAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import StatusBadge from '../components/StatusBadge';
import {
  Plug, PlugZap, Database, Server, FolderSearch, FileText, Upload, Settings2,
  Play, Loader2, CheckCircle2, XCircle, ChevronDown, Plus, Trash2
} from 'lucide-react';

// ═══════════════════════════════════════
// CREDENTIALS SECTION
// ═══════════════════════════════════════
function CredentialsSection() {
  const {
    sourceEngine, setSourceEngine, sourceCreds, setSourceCreds,
    targetCreds, setTargetCreds, 
    useStoredCreds, setUseStoredCreds, filePassword, setFilePassword,
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

      <div className="flex items-center gap-2 mb-6">
        <label className="flex items-center gap-2 cursor-pointer p-2 rounded-lg hover:bg-gray-50 border border-gray-200 w-full sm:w-auto">
          <input
            type="checkbox"
            className="form-checkbox"
            checked={useStoredCreds}
            onChange={(e) => setUseStoredCreds(e.target.checked)}
          />
          <span className="text-sm font-medium">Use Server Stored Credentials (Snowflake/Databricks)</span>
        </label>
      </div>

      {useStoredCreds ? (
        <div className="card">
          <div className="card-header bg-primary-50">
            <Plug size={16} className="text-primary-600" />
            Unlock Stored Credentials
          </div>
          <div className="card-body">
            <div className="form-group max-w-md">
              <label className="form-label">Master Password</label>
              <input 
                className="form-input" 
                type="password" 
                value={filePassword} 
                onChange={e => setFilePassword(e.target.value)} 
                placeholder="Enter password to unlock credential.txt..." 
              />
              <span className="form-hint">This will automatically connect to Snowflake and Databricks.</span>
            </div>
          </div>
        </div>
      ) : (
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
                <>
                  <div className="form-group">
                    <label className="form-label">Account</label>
                    <input className="form-input" value={sourceCreds.sf_account} onChange={e => updateSource('sf_account', e.target.value)} placeholder="xyz12345.us-east-1" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">User</label>
                    <input className="form-input" value={sourceCreds.sf_user} onChange={e => updateSource('sf_user', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Password</label>
                    <input className="form-input" type="password" value={sourceCreds.sf_password} onChange={e => updateSource('sf_password', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Warehouse</label>
                    <input className="form-input" value={sourceCreds.sf_warehouse} onChange={e => updateSource('sf_warehouse', e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Role (optional)</label>
                    <input className="form-input" value={sourceCreds.sf_role} onChange={e => updateSource('sf_role', e.target.value)} />
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Target */}
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
        </div>
      )}

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
function ValidationSettings({ settings, setSettings }) {
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
              <label className="form-label">Threshold (0-1)</label>
              <input type="number" className="form-input w-40" value={settings.threshold} min="0" max="1" step="0.01"
                onChange={e => setSettings(p => ({ ...p, threshold: parseFloat(e.target.value) }))} />
              <span className="form-hint">e.g., 0.99 = 99% match</span>
            </div>
          )}

          {/* Timestamp Toggle */}
          {(settings.hash || settings.validationType === 'deep') && (
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

          {/* Col Diff */}
          {(settings.hash || settings.validationType === 'deep') && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" className="form-checkbox" checked={settings.colDiffEnabled} onChange={e => setSettings(p => ({ ...p, colDiffEnabled: e.target.checked }))} />
              <span className="text-sm">Perform column-level diff on hash mismatch</span>
            </label>
          )}
        </div>
      </details>
    </CollapsibleSection>
  );
}

// ═══════════════════════════════════════
// TAB: BROWSE & SELECT
// ═══════════════════════════════════════
function BrowseTab({ settings }) {
  const { isConnected, sessionId, sourceEngine } = useConnection();
  const [srcCatalogs, setSrcCatalogs] = useState([]);
  const [srcSchemas, setSrcSchemas] = useState([]);
  const [srcTables, setSrcTables] = useState([]);
  const [tgtCatalogs, setTgtCatalogs] = useState([]);
  const [tgtSchemas, setTgtSchemas] = useState([]);
  const [tgtTables, setTgtTables] = useState([]);
  const [selectedSrcCatalog, setSelectedSrcCatalog] = useState('');
  const [selectedSrcSchemas, setSelectedSrcSchemas] = useState([]);
  const [selectedSrcTables, setSelectedSrcTables] = useState([]);
  const [selectedTgtCatalog, setSelectedTgtCatalog] = useState('');
  const [selectedTgtSchemas, setSelectedTgtSchemas] = useState([]);
  const [selectedTgtTables, setSelectedTgtTables] = useState([]);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);

  const loadCatalogs = async (target) => {
    try {
      const res = await metadataAPI.getCatalogs(target);
      return res.data.catalogs || [];
    } catch { return []; }
  };

  const loadSchemas = async (target, catalog) => {
    try {
      const res = await metadataAPI.getSchemas(target, catalog);
      return res.data.schemas || [];
    } catch { return []; }
  };

  const loadTables = async (target, catalog, schema) => {
    try {
      const res = await metadataAPI.getTables(target, catalog, schema);
      return res.data.tables || [];
    } catch { return []; }
  };

  const handleRun = async () => {
    setRunning(true);
    try {
      const pairs = selectedSrcTables.map((st, i) => ({
        source: st,
        target: selectedTgtTables[i],
      }));
      const res = await validationAPI.run({
        session_id: sessionId,
        validation_type: settings.validationType,
        table_pairs: pairs,
        settings,
      });
      setResults(res.data);
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
              <div className="flex gap-2">
                <select className="form-select flex-1" value={selectedSrcCatalog} onChange={e => setSelectedSrcCatalog(e.target.value)}>
                  <option value="">Select catalog...</option>
                  {srcCatalogs.map(c => <option key={c}>{c}</option>)}
                </select>
                <button className="btn btn-outline btn-sm" onClick={async () => setSrcCatalogs(await loadCatalogs('source'))}>Load</button>
              </div>
            </div>
            {selectedSrcCatalog && (
              <div className="form-group">
                <label className="form-label">Schema(s)</label>
                <div className="flex gap-2">
                  <select className="form-select flex-1" multiple value={selectedSrcSchemas} onChange={e => setSelectedSrcSchemas([...e.target.selectedOptions].map(o => o.value))} size={4}>
                    {srcSchemas.map(s => <option key={s}>{s}</option>)}
                  </select>
                  <button className="btn btn-outline btn-sm" onClick={async () => setSrcSchemas(await loadSchemas('source', selectedSrcCatalog))}>Load</button>
                </div>
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
              <div className="flex gap-2">
                <select className="form-select flex-1" value={selectedTgtCatalog} onChange={e => setSelectedTgtCatalog(e.target.value)}>
                  <option value="">Select catalog...</option>
                  {tgtCatalogs.map(c => <option key={c}>{c}</option>)}
                </select>
                <button className="btn btn-outline btn-sm" onClick={async () => setTgtCatalogs(await loadCatalogs('target'))}>Load</button>
              </div>
            </div>
            {selectedTgtCatalog && (
              <div className="form-group">
                <label className="form-label">Schema(s)</label>
                <div className="flex gap-2">
                  <select className="form-select flex-1" multiple value={selectedTgtSchemas} onChange={e => setSelectedTgtSchemas([...e.target.selectedOptions].map(o => o.value))} size={4}>
                    {tgtSchemas.map(s => <option key={s}>{s}</option>)}
                  </select>
                  <button className="btn btn-outline btn-sm" onClick={async () => setTgtSchemas(await loadSchemas('target', selectedTgtCatalog))}>Load</button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running Validations...</> : <><Play size={18} /> Run Browse Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: MANUAL ENTRY
// ═══════════════════════════════════════
function ManualTab({ settings }) {
  const { isConnected, sessionId } = useConnection();
  const [srcPaths, setSrcPaths] = useState('');
  const [tgtPaths, setTgtPaths] = useState('');
  const [whereClauses, setWhereClauses] = useState({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);

  const parsePaths = (raw) => raw.replace(/,/g, '\n').split('\n').map(p => p.trim()).filter(Boolean);
  const srcList = parsePaths(srcPaths);
  const tgtList = parsePaths(tgtPaths);
  const pairsValid = srcList.length > 0 && srcList.length === tgtList.length;

  const handleRun = async () => {
    setRunning(true);
    try {
      const pairs = srcList.map((s, i) => ({
        source: s,
        target: tgtList[i],
        source_where: whereClauses[`src_${i}`] || '1=1',
        target_where: whereClauses[`tgt_${i}`] || '1=1',
      }));
      const res = await validationAPI.run({
        session_id: sessionId,
        validation_type: settings.validationType,
        table_pairs: pairs,
        settings,
      });
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="form-group">
          <label className="form-label">Source Table Paths</label>
          <textarea className="form-textarea" rows={5} placeholder={"catalog.schema.table1\ncatalog.schema.table2"} value={srcPaths} onChange={e => setSrcPaths(e.target.value)} />
          <span className="form-hint">One per line, format: catalog.schema.table</span>
        </div>
        <div className="form-group">
          <label className="form-label">Target Table Paths</label>
          <textarea className="form-textarea" rows={5} placeholder={"workspace.default.table1\nworkspace.default.table2"} value={tgtPaths} onChange={e => setTgtPaths(e.target.value)} />
          <span className="form-hint">Must match source count ({srcList.length})</span>
        </div>
      </div>

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

      {srcList.length !== tgtList.length && srcList.length > 0 && (
        <div className="alert alert-error">❌ Source ({srcList.length}) and Target ({tgtList.length}) table counts must match.</div>
      )}

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running || !pairsValid}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running...</> : <><Play size={18} /> Run Manual Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: CSV UPLOAD
// ═══════════════════════════════════════
function CSVTab({ settings }) {
  const { isConnected, sessionId } = useConnection();
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);

  const handleFileChange = (e) => {
    const f = e.target.files[0];
    if (!f) return;
    setFile(f);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const lines = ev.target.result.split('\n').filter(l => l.trim() && !l.startsWith('#'));
      if (lines.length > 1) {
        const headers = lines[0].split(',');
        const rows = lines.slice(1, 6).map(l => l.split(','));
        setPreview({ headers, rows });
      }
    };
    reader.readAsText(f);
  };

  const handleRun = async () => {
    setRunning(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('session_id', sessionId);
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
                <tr>{['shallow','DB','PUBLIC','TABLE2','ws','default','tbl2','1=1','no','','','','no','yes','0.99'].map((v,i)=><td key={i} className="text-xs">{v}</td>)}</tr>
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
          {preview && (
            <div className="mt-4 overflow-x-auto">
              <p className="text-sm text-gray-500 mb-2">Preview (first 5 rows):</p>
              <table className="data-table">
                <thead><tr>{preview.headers.map((h,i)=><th key={i}>{h.trim()}</th>)}</tr></thead>
                <tbody>{preview.rows.map((r,i)=><tr key={i}>{r.map((c,j)=><td key={j} className="text-xs">{c.trim()}</td>)}</tr>)}</tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <button className="btn btn-primary btn-full btn-lg" onClick={handleRun} disabled={running || !file}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running CSV Validations...</> : <><Play size={18} /> Run CSV Validations</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// TAB: CONFIG DRIVEN
// ═══════════════════════════════════════
function ConfigTab({ settings }) {
  const { isConnected, sessionId } = useConnection();
  const [configText, setConfigText] = useState(JSON.stringify({
    tables: [
      {
        name: "Example Validation",
        source: "CATALOG.SCHEMA.TABLE",
        target: "catalog.schema.table",
        validation_type: "deep",
        metrics: ["row_count", "schema", "numeric", "hash"],
        where: "1=1"
      }
    ]
  }, null, 2));
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);
  const [parseError, setParseError] = useState(null);

  const handleSubmit = async () => {
    setParseError(null);
    let parsed;
    try {
      parsed = JSON.parse(configText);
    } catch (e) {
      setParseError(`Invalid JSON: ${e.message}`);
      return;
    }
    setRunning(true);
    try {
      const res = await validationAPI.runConfig({ session_id: sessionId, config: parsed, settings });
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
      <div className="form-group">
        <label className="form-label">Validation Config (JSON)</label>
        <textarea
          className="form-textarea"
          rows={14}
          value={configText}
          onChange={e => setConfigText(e.target.value)}
          spellCheck={false}
        />
        <span className="form-hint">Define table pairs, validation types, and metrics in JSON format.</span>
      </div>

      {parseError && <div className="alert alert-error">{parseError}</div>}

      <button className="btn btn-primary btn-full btn-lg" onClick={handleSubmit} disabled={running}>
        {running ? <><Loader2 size={18} className="animate-spin" /> Running Config Validations...</> : <><Play size={18} /> Submit Config</>}
      </button>

      {results && <ResultsDisplay results={results} />}
    </div>
  );
}

// ═══════════════════════════════════════
// RESULTS DISPLAY
// ═══════════════════════════════════════
function ResultsDisplay({ results }) {
  if (!results) return null;

  if (results.error) {
    return <div className="alert alert-error mt-4">❌ {results.error}</div>;
  }

  const records = results.results || results.validation_ids || [];

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
                </tr>
              ))}
            </tbody>
          </table>
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
    rowCount: true, schema: true, numeric: false, hash: false,
    useThreshold: false, threshold: 0.99,
    includeTimestamp: true, caseSensitive: false, colDiffEnabled: false,
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
        <h1 className="page-title">New Validation</h1>
      </div>
      <div className="page-content space-y-6">
        <CredentialsSection />
        <ValidationSettings settings={settings} setSettings={setSettings} />

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
            {activeTab === 'browse' && <BrowseTab settings={settings} />}
            {activeTab === 'manual' && <ManualTab settings={settings} />}
            {activeTab === 'csv' && <CSVTab settings={settings} />}
            {activeTab === 'config' && <ConfigTab settings={settings} />}
          </div>
        </div>
      </div>
    </div>
  );
}
