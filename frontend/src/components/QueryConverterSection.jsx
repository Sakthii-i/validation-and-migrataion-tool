import { useEffect, useMemo, useRef, useState } from 'react';
import { Clipboard, Database, Download, Loader2, Settings2, Upload, Wand2, X } from 'lucide-react';
import { migrationAPI } from '../services/api';

const API_KEY_STORE = 'validation_tool_converter_api_keys';
const DATABRICKS_CONFIG_STORE = 'validation_tool_converter_databricks_config';
const DEFAULT_MODE = 'Auto (deterministic -> LLM migration -> validation)';

const defaultDatabricksConfig = {
  host: '',
  token: '',
  warehouse_id: '',
  catalog: '',
  schema: '',
  timeout_seconds: 90,
  max_rows: 200,
};

function loadJson(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export default function QueryConverterSection() {
  const [config, setConfig] = useState({ providers: ['OpenAI'], provider_model_options: {}, modes: [DEFAULT_MODE] });
  const [provider, setProvider] = useState('OpenAI');
  const [model, setModel] = useState('');
  const [mode, setMode] = useState(DEFAULT_MODE);
  const [apiKeys, setApiKeys] = useState(() => loadJson(API_KEY_STORE, { OpenAI: '', Gemini: '', Claude: '' }));
  const [runInDatabricks, setRunInDatabricks] = useState(false);
  const [databricksConfig, setDatabricksConfig] = useState(() => loadJson(DATABRICKS_CONFIG_STORE, defaultDatabricksConfig));
  const [inputMode, setInputMode] = useState('manual');

  const [bqSql, setBqSql] = useState('');
  const [translatedSql, setTranslatedSql] = useState('');
  const [validation, setValidation] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [finalError, setFinalError] = useState('');
  const [execution, setExecution] = useState(null);
  const [explanation, setExplanation] = useState('');
  const [loading, setLoading] = useState(false);
  const [copyState, setCopyState] = useState('idle');

  const [csvFile, setCsvFile] = useState(null);
  const [csvResults, setCsvResults] = useState([]);
  const [csvError, setCsvError] = useState('');
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    migrationAPI.getConfig()
      .then((res) => {
        const data = res.data;
        setConfig(data);
        const firstProvider = data.providers?.[0] || 'OpenAI';
        setProvider(firstProvider);
        setModel(data.provider_model_options?.[firstProvider]?.[0] || '');
        setMode(data.modes?.[0] || DEFAULT_MODE);
      })
      .catch(() => {
        setTranslatedSql('Converter configuration could not be loaded.');
      });
  }, []);

  useEffect(() => {
    window.localStorage.setItem(API_KEY_STORE, JSON.stringify(apiKeys));
  }, [apiKeys]);

  useEffect(() => {
    window.localStorage.setItem(DATABRICKS_CONFIG_STORE, JSON.stringify(databricksConfig));
  }, [databricksConfig]);

  const models = useMemo(() => config.provider_model_options?.[provider] || [], [config, provider]);

  useEffect(() => {
    if (models.length > 0 && !models.includes(model)) {
      setModel(models[0]);
    }
  }, [models, model]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  const selectedApiKey = apiKeys[provider] || '';

  const updateDatabricksConfig = (field, value) => {
    setDatabricksConfig((prev) => ({ ...prev, [field]: value }));
  };

  const clearOutput = () => {
    setTranslatedSql('');
    setValidation(null);
    setSuggestions([]);
    setFinalError('');
    setExecution(null);
    setExplanation('');
    setCopyState('idle');
  };

  const validateDatabricksFields = () => {
    if (!runInDatabricks) return '';
    if (!databricksConfig.host.trim() || !databricksConfig.token.trim() || !databricksConfig.warehouse_id.trim()) {
      return 'Databricks host, token, and warehouse ID are required only when Run in Databricks is enabled.';
    }
    return '';
  };

  const buildPayload = () => ({
    provider,
    model,
    mode,
    api_key: selectedApiKey,
    run_in_databricks: runInDatabricks,
    databricks: runInDatabricks
      ? {
          host: databricksConfig.host.trim(),
          token: databricksConfig.token.trim(),
          warehouse_id: databricksConfig.warehouse_id.trim(),
          catalog: databricksConfig.catalog.trim() || null,
          schema: databricksConfig.schema.trim() || null,
          timeout_seconds: Number(databricksConfig.timeout_seconds) || 90,
          max_rows: Number(databricksConfig.max_rows) || 200,
        }
      : null,
  });

  const handleTranslateSql = async () => {
    if (!bqSql.trim()) {
      setTranslatedSql('Please enter a BigQuery SQL query.');
      return;
    }

    const databricksError = validateDatabricksFields();
    if (databricksError) {
      setTranslatedSql(databricksError);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    clearOutput();
    try {
      const res = await migrationAPI.translateSql({ ...buildPayload(), bq_sql: bqSql }, controller.signal);
      const data = res.data;
      setTranslatedSql(data.translated_sql || '');
      setValidation(data.validation || null);
      setSuggestions(data.suggestions || []);
      setFinalError(data.final_error || '');
      setExecution(data.execution || null);
      setExplanation(data.explanation || '');
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        setTranslatedSql('Translation cancelled.');
      } else {
        setTranslatedSql(`Translation error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  const handleCsvTranslate = async () => {
    if (!csvFile) return;

    const databricksError = validateDatabricksFields();
    if (databricksError) {
      setCsvError(databricksError);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setCsvError('');
    setCsvResults([]);
    try {
      const res = await migrationAPI.translateCsv(csvFile, {
        ...buildPayload(),
        apiKey: selectedApiKey,
        runInDatabricks,
        databricksConfig,
      }, controller.signal);
      setCsvResults(res.data.results || []);
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        setCsvError('CSV translation cancelled.');
      } else {
        setCsvError(`CSV translation error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  const handleCancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
  };

  const handleCopy = async () => {
    if (!translatedSql) return;
    try {
      await navigator.clipboard.writeText(translatedSql);
      setCopyState('copied');
      setTimeout(() => setCopyState('idle'), 1400);
    } catch {
      setCopyState('failed');
      setTimeout(() => setCopyState('idle'), 1400);
    }
  };

  const downloadCsv = () => {
    if (!csvResults.length) return;
    const rows = [['query'], ...csvResults.map((r) => [r.translated_sql || ''])];
    const content = rows.map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `translated_${csvFile?.name || 'queries.csv'}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const validCsvCount = csvResults.filter((row) => row.validation?.is_valid).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900">BigQuery to Databricks Query Converter</h2>
          <p className="text-sm text-gray-500">Convert SQL without establishing app database connections. API keys and Databricks execution settings are optional.</p>
        </div>
        <div className="inline-flex w-fit rounded-lg border border-gray-200 bg-gray-50 p-1">
          <button className={`btn btn-sm ${inputMode === 'manual' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setInputMode('manual')} type="button">
            <Wand2 size={14} /> Manual
          </button>
          <button className={`btn btn-sm ${inputMode === 'csv' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setInputMode('csv')} type="button">
            <Upload size={14} /> CSV Upload
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[320px,1fr]">
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-4 flex items-center gap-2 font-semibold text-gray-900">
            <Settings2 size={16} /> Configuration
          </div>

          <div className="space-y-4">
            <div className="form-group">
              <label className="form-label">LLM Provider</label>
              <select className="form-select" value={provider} onChange={(e) => setProvider(e.target.value)}>
                {(config.providers || []).map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
              <span className="form-hint">Used only when deterministic conversion needs fallback help.</span>
            </div>

            <div className="form-group">
              <label className="form-label">{provider} API Key</label>
              <input
                className="form-input"
                type="password"
                value={selectedApiKey}
                onChange={(e) => setApiKeys((prev) => ({ ...prev, [provider]: e.target.value.trim() }))}
                placeholder="Optional"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Model</label>
              <select className="form-select" value={model} onChange={(e) => setModel(e.target.value)}>
                {models.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </div>

            <fieldset className="space-y-2">
              <legend className="form-label">Translation Mode</legend>
              {(config.modes || []).map((item) => (
                <label key={item} className="flex items-start gap-2 text-sm text-gray-700">
                  <input className="form-checkbox mt-0.5" type="radio" checked={mode === item} onChange={() => setMode(item)} />
                  <span>{item}</span>
                </label>
              ))}
            </fieldset>

            <div className="border-t border-gray-200 pt-4">
              <label className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-800">
                <input className="form-checkbox" type="checkbox" checked={runInDatabricks} onChange={(e) => setRunInDatabricks(e.target.checked)} />
                <Database size={15} /> Run translated SQL in Databricks
              </label>

              <div className="space-y-3">
                <Input label="Workspace Host" value={databricksConfig.host} onChange={(value) => updateDatabricksConfig('host', value)} placeholder="adb-123.azuredatabricks.net" />
                <Input label="Access Token" type="password" value={databricksConfig.token} onChange={(value) => updateDatabricksConfig('token', value)} />
                <Input label="SQL Warehouse ID" value={databricksConfig.warehouse_id} onChange={(value) => updateDatabricksConfig('warehouse_id', value)} />
                <div className="grid grid-cols-2 gap-2">
                  <Input label="Catalog" value={databricksConfig.catalog} onChange={(value) => updateDatabricksConfig('catalog', value)} />
                  <Input label="Schema" value={databricksConfig.schema} onChange={(value) => updateDatabricksConfig('schema', value)} />
                </div>
              </div>
            </div>
          </div>
        </div>

        {inputMode === 'manual' ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="form-group">
                <label className="form-label">Input SQL (BigQuery)</label>
                <textarea
                  className="form-textarea min-h-[360px]"
                  value={bqSql}
                  onChange={(e) => {
                    setBqSql(e.target.value);
                    clearOutput();
                  }}
                  placeholder="Paste your BigQuery SQL here..."
                />
              </div>
              <div className="form-group">
                <label className="form-label">Output SQL (Databricks)</label>
                <textarea className="form-textarea min-h-[360px]" value={translatedSql} readOnly placeholder="Converted Databricks SQL appears here..." />
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" type="button" onClick={handleTranslateSql} disabled={loading}>
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                {runInDatabricks ? 'Convert and Run' : 'Convert SQL'}
              </button>
              {loading && (
                <button className="btn btn-outline" type="button" onClick={handleCancel}>
                  <X size={16} /> Cancel
                </button>
              )}
              <button className="btn btn-outline" type="button" onClick={handleCopy} disabled={!translatedSql}>
                <Clipboard size={16} /> {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy Failed' : 'Copy SQL'}
              </button>
            </div>

            <ResultDetails validation={validation} suggestions={suggestions} finalError={finalError} execution={execution} explanation={explanation} />
          </div>
        ) : (
          <div className="space-y-4">
            <div
              className="flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 p-8 text-center hover:border-primary-500 hover:bg-primary-50/40"
              onClick={() => fileInputRef.current?.click()}
            >
              <input ref={fileInputRef} className="hidden" type="file" accept=".csv" onChange={(e) => setCsvFile(e.target.files?.[0] || null)} />
              <Upload className="mb-3 text-gray-400" size={36} />
              <div className="font-semibold text-gray-900">{csvFile ? csvFile.name : 'Upload a CSV file'}</div>
              <div className="mt-1 text-sm text-gray-500">Use a column named bq_sql, sql, query, bigquery_sql, or bq_query.</div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" type="button" onClick={handleCsvTranslate} disabled={!csvFile || loading}>
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
                Translate All
              </button>
              {loading && <button className="btn btn-outline" type="button" onClick={handleCancel}><X size={16} /> Cancel</button>}
              <button className="btn btn-outline" type="button" onClick={downloadCsv} disabled={!csvResults.length}>
                <Download size={16} /> Download CSV
              </button>
            </div>

            {csvError && <div className="alert alert-error">{csvError}</div>}
            {csvResults.length > 0 && (
              <div className="space-y-3">
                <div className="alert alert-info">
                  Translated {csvResults.length} quer{csvResults.length === 1 ? 'y' : 'ies'}. Valid Databricks SQL: {validCsvCount}.
                </div>
                <div className="overflow-x-auto">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Row</th>
                        <th>Status</th>
                        <th>Original SQL</th>
                        <th>Translated SQL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {csvResults.map((row, index) => (
                        <tr key={`${row.row_index}-${row.query_index}-${index}`}>
                          <td>{row.row_index + 1}</td>
                          <td>{row.validation?.is_valid ? <span className="badge badge-pass">Valid</span> : <span className="badge badge-fail">Issue</span>}</td>
                          <td><pre className="max-h-32 max-w-md overflow-auto whitespace-pre-wrap font-mono text-xs">{row.original_sql}</pre></td>
                          <td><pre className="max-h-32 max-w-md overflow-auto whitespace-pre-wrap font-mono text-xs">{row.translated_sql}</pre></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Input({ label, value, onChange, type = 'text', placeholder = '' }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <input className="form-input" type={type} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function ResultDetails({ validation, suggestions, finalError, execution, explanation }) {
  if (!validation && !finalError && !execution && !explanation) return null;

  return (
    <div className="space-y-3">
      {validation && (
        validation.is_valid
          ? <div className="alert alert-success">SQL validated for Databricks dialect.</div>
          : <div className="alert alert-warning">Validation warning: {validation.error_message || 'Review translated SQL.'}</div>
      )}
      {finalError && <div className="alert alert-warning whitespace-pre-wrap">{finalError}</div>}
      {suggestions?.length > 0 && (
        <div className="alert alert-info">
          <div>
            <div className="font-semibold">Suggestions</div>
            <ul className="mt-1 list-disc pl-5">
              {suggestions.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
            </ul>
          </div>
        </div>
      )}
      {execution && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-3 font-semibold text-gray-900">Databricks Execution</div>
          <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
            <div><span className="text-gray-500">Status:</span> <span className="font-semibold">{execution.status || 'Unknown'}</span></div>
            <div><span className="text-gray-500">Rows:</span> <span className="font-semibold">{execution.row_count ?? 'N/A'}</span></div>
            <div><span className="text-gray-500">Statement:</span> <span className="font-mono text-xs">{execution.statement_id || 'N/A'}</span></div>
          </div>
          {execution.error && <div className="alert alert-error mt-3">{execution.error}</div>}
        </div>
      )}
      {explanation && (
        <details className="rounded-lg border border-gray-200 bg-white p-4">
          <summary className="cursor-pointer font-semibold text-gray-900">Translation Pipeline</summary>
          <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-gray-700">{explanation}</pre>
        </details>
      )}
    </div>
  );
}
