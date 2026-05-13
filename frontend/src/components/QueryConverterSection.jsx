import { useEffect, useMemo, useRef, useState } from 'react';
import { Clipboard, Database, Download, GitBranch, Loader2, RefreshCw, Settings2, Upload, Wand2, X } from 'lucide-react';
import { migrationAPI } from '../services/api';
import { useConnection } from '../context/ConnectionContext';
import QueryComplexityMetrics from './QueryComplexityMetrics';

const API_KEY_STORE = 'validation_tool_converter_api_keys';
const QUERY_SESSION_STORE = 'validation_tool_query_session_id';
const DEFAULT_MODE = 'Auto (deterministic -> LLM migration -> validation)';

function loadJson(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function getOrCreateQuerySessionId() {
  try {
    const existing = window.localStorage.getItem(QUERY_SESSION_STORE);
    if (existing) return existing;
    const generated = window.crypto?.randomUUID?.() || `qs_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    window.localStorage.setItem(QUERY_SESSION_STORE, generated);
    return generated;
  } catch {
    return `qs_${Date.now()}_${Math.random().toString(16).slice(2)}`;
  }
}

export default function QueryConverterSection() {
  const { isConnected, sourceEngine, sessionId } = useConnection();
  const [config, setConfig] = useState({ providers: ['OpenAI'], provider_model_options: {}, modes: [DEFAULT_MODE] });
  const [provider, setProvider] = useState('OpenAI');
  const [model, setModel] = useState('');
  const [mode, setMode] = useState(DEFAULT_MODE);
  const [apiKeys, setApiKeys] = useState(() => loadJson(API_KEY_STORE, { OpenAI: '', Gemini: '', Claude: '' }));
  const [inputMode, setInputMode] = useState('manual');
  const [cacheStats, setCacheStats] = useState({ persistent: {}, expression: {} });
  const [cacheLoading, setCacheLoading] = useState(false);

  const [bqSql, setBqSql] = useState('');
  const [translatedSql, setTranslatedSql] = useState('');
  const [validation, setValidation] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [finalError, setFinalError] = useState('');
  const [execution, setExecution] = useState(null);
  const [explanation, setExplanation] = useState('');
  const [cacheHit, setCacheHit] = useState(false);
  const [loading, setLoading] = useState(false);
  const [runningDatabricks, setRunningDatabricks] = useState(false);
  const [copyState, setCopyState] = useState('idle');

  const [csvFile, setCsvFile] = useState(null);
  const [csvResults, setCsvResults] = useState([]);
  const [csvError, setCsvError] = useState('');
  const [gitRepoUrl, setGitRepoUrl] = useState('');
  const [gitToken, setGitToken] = useState('');
  const [gitBranches, setGitBranches] = useState([]);
  const [gitBranch, setGitBranch] = useState('');
  const [gitResolvedRef, setGitResolvedRef] = useState('');
  const [gitFiles, setGitFiles] = useState([]);
  const [gitSelectedFile, setGitSelectedFile] = useState('');
  const [gitLoading, setGitLoading] = useState(false);
  const [gitError, setGitError] = useState('');
  const [showGitUpload, setShowGitUpload] = useState(false);
  const [gitUploadMode, setGitUploadMode] = useState('existing');
  const [gitCreateFolder, setGitCreateFolder] = useState(false);
  const [gitFolderMode, setGitFolderMode] = useState('existing');
  const [gitUploadBranch, setGitUploadBranch] = useState('');
  const [gitNewBranch, setGitNewBranch] = useState('');
  const [gitNewFolderName, setGitNewFolderName] = useState('');
  const [gitExistingFolder, setGitExistingFolder] = useState('');
  const [gitNewFileName, setGitNewFileName] = useState('');
  const [gitUploadLoading, setGitUploadLoading] = useState(false);
  const [gitUploadMessage, setGitUploadMessage] = useState('');
  const fileInputRef = useRef(null);
  const abortRef = useRef(null);

  const [complexity, setComplexity] = useState(null);
  const [querySessionId] = useState(() => getOrCreateQuerySessionId());

  useEffect(() => {
    Promise.all([migrationAPI.getConfig(), migrationAPI.getCacheStats()])
      .then(([configRes, cacheRes]) => {
        const data = configRes.data;
        setConfig(data);
        setCacheStats(cacheRes.data || { persistent: {}, expression: {} });
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

  const models = useMemo(() => config.provider_model_options?.[provider] || [], [config, provider]);

  useEffect(() => {
    if (models.length > 0 && !models.includes(model)) {
      setModel(models[0]);
    }
  }, [models, model]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    clearOutput();
    setCsvResults([]);
    setCsvError('');
  }, [sourceEngine]);

  const selectedApiKey = apiKeys[provider] || '';

  const refreshCacheStats = async () => {
    const res = await migrationAPI.getCacheStats();
    setCacheStats(res.data || { persistent: {}, expression: {} });
  };

  const handleClearCache = async () => {
    setCacheLoading(true);
    try {
      await migrationAPI.clearCache();
      await refreshCacheStats();
    } finally {
      setCacheLoading(false);
    }
  };

  const clearOutput = () => {
    setTranslatedSql('');
    setValidation(null);
    setSuggestions([]);
    setFinalError('');
    setExecution(null);
    setExplanation('');
    setCacheHit(false);
    setCopyState('idle');
    setComplexity(null);
  };

  const isSnowflake = sourceEngine === 'Snowflake';
  const sourceLabel = isSnowflake ? 'Snowflake' : 'BigQuery';
  const titleText = `${sourceLabel} to Databricks Query Converter`;
  const inputLabel = `Input SQL (${sourceLabel})`;
  const inputPlaceholder = `Paste your ${sourceLabel} SQL here...`;
  const hasRequiredSnowflakeConnection = !isSnowflake || isConnected;

  const buildPayload = () => ({
    source_engine: sourceEngine.toLowerCase(),
    provider,
    model,
    mode,
    api_key: selectedApiKey,
    run_in_databricks: false,
    databricks: null,
    session_id: querySessionId,
  });

  const handleTranslateSql = async () => {
    if (!bqSql.trim()) {
      setTranslatedSql(`Please enter a ${sourceLabel} SQL query.`);
      return;
    }

    if (!hasRequiredSnowflakeConnection) {
      setTranslatedSql('Please establish a Snowflake connection first. Stored Snowflake credentials from the backend will be used.');
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
      setCacheHit(Number(data.stats?.cache_hits || 0) > 0);
      const queryComplexity = data.stats?.complexity || null;
      setComplexity(queryComplexity);
      await refreshCacheStats();
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

    if (!hasRequiredSnowflakeConnection) {
      setCsvError('Please establish a Snowflake connection first. Stored Snowflake credentials from the backend will be used.');
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
        sourceEngine: sourceEngine.toLowerCase(),
        runInDatabricks: false,
        databricksConfig: null,
        sessionId: querySessionId,
      }, controller.signal);
      const results = res.data.results || [];
      setCsvResults(results);
      await refreshCacheStats();
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

  const handleFetchGitBranches = async () => {
    if (!gitRepoUrl.trim()) {
      setGitError('Git repository URL is required.');
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setGitLoading(true);
    setGitError('');
    setGitBranches([]);
    setGitBranch('');
    setGitUploadBranch('');
    try {
      const res = await migrationAPI.getGitBranches({
        repo_url: gitRepoUrl.trim(),
        token: gitToken.trim() || null,
      }, controller.signal);
      const branches = res.data.branches || [];
      const defaultBranch = res.data.default_branch || branches[0] || '';
      setGitBranches(branches);
      setGitBranch(defaultBranch);
      setGitUploadBranch(defaultBranch);
      if (!branches.length) {
        setGitError('No branches found in the repository.');
      }
    } catch (err) {
      if (err?.name !== 'CanceledError' && err?.code !== 'ERR_CANCELED') {
        setGitError(`Git branch fetch error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setGitLoading(false);
    }
  };

  const handleFetchGitFiles = async () => {
    if (!gitRepoUrl.trim()) {
      setGitError('Git repository URL is required.');
      return;
    }
    if (!gitBranch) {
      setGitError('Select a branch first.');
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setGitLoading(true);
    setGitError('');
    setGitFiles([]);
    setGitSelectedFile('');
    setGitResolvedRef('');
    try {
      const res = await migrationAPI.getGitFiles({
        repo_url: gitRepoUrl.trim(),
        ref: gitBranch,
        token: gitToken.trim() || null,
      }, controller.signal);
      const files = res.data.files || [];
      setGitFiles(files);
      setGitSelectedFile(files[0] || '');
      setGitResolvedRef(res.data.ref || '');
      if (!files.length) {
        setGitError('No files found in the repository.');
      }
    } catch (err) {
      if (err?.name !== 'CanceledError' && err?.code !== 'ERR_CANCELED') {
        setGitError(`Git fetch error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setGitLoading(false);
    }
  };

  const handleLoadGitFile = async () => {
    if (!gitRepoUrl.trim() || !gitSelectedFile) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setGitLoading(true);
    setGitError('');
    try {
      const res = await migrationAPI.getGitFile({
        repo_url: gitRepoUrl.trim(),
        ref: gitBranch,
        path: gitSelectedFile,
        token: gitToken.trim() || null,
      }, controller.signal);
      setBqSql(res.data.content || '');
      setGitResolvedRef(res.data.ref || gitResolvedRef);
      clearOutput();
    } catch (err) {
      if (err?.name !== 'CanceledError' && err?.code !== 'ERR_CANCELED') {
        setGitError(`Git file load error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setGitLoading(false);
    }
  };

  const handleUploadTranslatedSql = async () => {
    if (!translatedSql.trim()) return;
    if (!gitRepoUrl.trim()) {
      setGitUploadMessage('Git repository URL is required.');
      return;
    }
    if (!gitToken.trim()) {
      setGitUploadMessage('Git access token is required to upload.');
      return;
    }
    if (gitUploadMode === 'existing' && !gitUploadBranch) {
      setGitUploadMessage('Select an existing target branch.');
      return;
    }
    if (gitUploadMode === 'create' && !gitNewBranch.trim()) {
      setGitUploadMessage('New branch name is required.');
      return;
    }
    if (!gitNewFileName.trim()) {
      setGitUploadMessage('New file name is required.');
      return;
    }

    if (gitUploadMode === 'create' && gitCreateFolder && !gitNewFolderName.trim()) {
      setGitUploadMessage('New folder name is required.');
      return;
    }
    if (gitUploadMode === 'existing' && gitFolderMode === 'new' && !gitNewFolderName.trim()) {
      setGitUploadMessage('New folder name is required.');
      return;
    }
    if (gitUploadMode === 'existing' && gitFolderMode === 'existing' && !gitExistingFolder) {
      setGitUploadMessage('Select an existing folder.');
      return;
    }

    const folder = gitUploadMode === 'create'
      ? (gitCreateFolder ? gitNewFolderName.trim() : '.')
      : gitFolderMode === 'new'
        ? gitNewFolderName.trim()
        : gitExistingFolder;
    const uploadPath = folder === '.' ? gitNewFileName.trim() : `${folder.replace(/\/+$/, '')}/${gitNewFileName.trim()}`;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setGitUploadLoading(true);
    setGitUploadMessage('');
    try {
      const res = await migrationAPI.uploadGitFile({
        repo_url: gitRepoUrl.trim(),
        token: gitToken.trim(),
        content: translatedSql,
        path: uploadPath,
        mode: gitUploadMode,
        branch: gitUploadMode === 'existing' ? gitUploadBranch : gitBranch,
        base_branch: gitBranch,
        new_branch: gitUploadMode === 'create' ? gitNewBranch.trim() : null,
        message: `Upload translated Databricks SQL ${uploadPath}`,
      }, controller.signal);
      setGitUploadMessage(`Uploaded to ${res.data.branch}:${res.data.path}`);
      if (res.data.branch && !gitBranches.includes(res.data.branch)) {
        setGitBranches((prev) => [...prev, res.data.branch]);
      }
    } catch (err) {
      if (err?.name !== 'CanceledError' && err?.code !== 'ERR_CANCELED') {
        setGitUploadMessage(`Git upload error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setGitUploadLoading(false);
    }
  };

  const handleRunDatabricks = async () => {
    if (!translatedSql.trim()) return;
    if (isSnowflake && !isConnected) {
      setExecution({
        databricks: {
          status: 'BLOCKED',
          error: 'Please establish a Snowflake connection from the sidebar before running source and Databricks outputs.',
          rows: [],
          columns: [],
        },
      });
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRunningDatabricks(true);
    setExecution(null);
    try {
      const res = await migrationAPI.runStoredDatabricks({
        sql: translatedSql,
        source_sql: bqSql,
        source_engine: sourceEngine.toLowerCase(),
        provider,
        model,
        api_key: selectedApiKey,
        session_id: sessionId,
      }, controller.signal);
      setExecution(res.data.execution || null);
      if (res.data.execution?.repaired_sql) {
        setTranslatedSql(res.data.execution.repaired_sql);
      }
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        setExecution({ status: 'CANCELED', error: 'Databricks run cancelled.' });
      } else {
        setExecution({ status: 'FAILED', error: err.response?.data?.detail || err.message });
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setRunningDatabricks(false);
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
  const gitFolders = useMemo(() => {
    const folders = new Set(['.']);
    gitFiles.forEach((file) => {
      const parts = String(file || '').split('/').filter(Boolean);
      for (let index = 1; index < parts.length; index += 1) {
        folders.add(parts.slice(0, index).join('/'));
      }
    });
    return Array.from(folders).sort((a, b) => {
      if (a === '.') return -1;
      if (b === '.') return 1;
      return a.localeCompare(b);
    });
  }, [gitFiles]);

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-bold text-gray-900">{titleText}</h2>
          <p className="text-sm text-gray-500">Convert SQL first, then run the converted SQL in Databricks only when needed.</p>
        </div>
        <div className="inline-flex w-fit rounded-lg border border-gray-200 bg-gray-50 p-1">
          <button className={`btn btn-sm ${inputMode === 'manual' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setInputMode('manual')} type="button">
            <Wand2 size={14} /> Manual
          </button>
          <button className={`btn btn-sm ${inputMode === 'csv' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setInputMode('csv')} type="button">
            <Upload size={14} /> CSV Upload
          </button>
          <button className={`btn btn-sm ${inputMode === 'git' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => setInputMode('git')} type="button">
            <GitBranch size={14} /> Git Repo
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[320px,1fr]">
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-4 flex items-center gap-2 font-semibold text-gray-900">
            <Settings2 size={16} /> Configuration
          </div>

          <div className="space-y-4">
            {isSnowflake && !hasRequiredSnowflakeConnection && (
              <div className="alert alert-warning">
                Please establish a Snowflake connection from the sidebar before converting.
              </div>
            )}

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
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="font-semibold text-gray-900">Cache Stats</div>
                <button className="btn btn-sm btn-outline" type="button" onClick={refreshCacheStats} disabled={cacheLoading}>
                  <RefreshCw size={14} className={cacheLoading ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>
              <div className="grid grid-cols-1 gap-2">
                <CacheMetric title="Persistent cache entries" value={cacheStats?.persistent?.total_entries ?? 0} />
                <CacheMetric title="Expression cache size" value={cacheStats?.expression?.size ?? 0} />
                <CacheMetric title="Expression cache hit rate" value={cacheStats?.expression?.hit_rate ?? '0.0%'} />
              </div>
              <button className="btn btn-outline btn-full mt-3" type="button" onClick={handleClearCache} disabled={cacheLoading}>
                {cacheLoading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
                Clear All Cache
              </button>
            </div>
          </div>
        </div>

        {inputMode === 'manual' ? (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="form-group">
                <label className="form-label">{inputLabel}</label>
                <textarea
                  className="form-textarea min-h-[360px]"
                  value={bqSql}
                  onChange={(e) => {
                    setBqSql(e.target.value);
                    clearOutput();
                  }}
                  placeholder={inputPlaceholder}
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
                Convert SQL
              </button>
              {loading && (
                <button className="btn btn-outline" type="button" onClick={handleCancel}>
                  <X size={16} /> Cancel
                </button>
              )}
              <button className="btn btn-outline" type="button" onClick={handleCopy} disabled={!translatedSql}>
                <Clipboard size={16} /> {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy Failed' : 'Copy SQL'}
              </button>
              <button className="btn btn-success" type="button" onClick={handleRunDatabricks} disabled={!translatedSql || runningDatabricks}>
                {runningDatabricks ? <Loader2 size={16} className="animate-spin" /> : <Database size={16} />}
                Run in Databricks
              </button>
            </div>

            <ResultDetails validation={validation} suggestions={suggestions} finalError={finalError} execution={execution} explanation={explanation} cacheHit={cacheHit} />
            <QueryComplexityMetrics complexity={complexity} sourceLabel={sourceLabel} />
          </div>
        ) : inputMode === 'csv' ? (
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
                        <th>Complexity</th>
                        <th>Original SQL</th>
                        <th>Translated SQL</th>
                      </tr>
                    </thead>
                    <tbody>
                      {csvResults.map((row, index) => (
                        <tr key={`${row.row_index}-${row.query_index}-${index}`}>
                          <td>{row.row_index + 1}</td>
                          <td>{row.validation?.is_valid ? <span className="badge badge-pass">Valid</span> : <span className="badge badge-fail">Issue</span>}</td>
                          <td>
                            {row.stats?.complexity ? (
                              <span className="badge badge-info">
                                {row.stats.complexity.complexity_level} ({row.stats.complexity.complexity_score})
                              </span>
                            ) : (
                              <span className="text-xs text-gray-400">-</span>
                            )}
                          </td>
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
        ) : (
          <div className="space-y-4">
            <div className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr,260px]">
                <Input label="Git Repository URL" value={gitRepoUrl} onChange={setGitRepoUrl} placeholder="https://github.com/org/repo.git" />
                <div className="form-group">
                  <label className="form-label">Branch</label>
                  <select className="form-select" value={gitBranch} onChange={(e) => setGitBranch(e.target.value)} disabled={!gitBranches.length}>
                    <option value="">{gitBranches.length ? 'Select branch' : 'Fetch branches first'}</option>
                    {gitBranches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                  </select>
                </div>
              </div>
              <div className="mt-3">
                <Input label="Git Access Token (optional)" type="password" value={gitToken} onChange={setGitToken} placeholder="Required for private repos" />
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button className="btn btn-primary" type="button" onClick={handleFetchGitBranches} disabled={gitLoading || !gitRepoUrl.trim()}>
                  {gitLoading ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
                  Fetch Branches
                </button>
                <button className="btn btn-outline" type="button" onClick={handleFetchGitFiles} disabled={gitLoading || !gitRepoUrl.trim() || !gitBranch}>
                  {gitLoading ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
                  Fetch Files
                </button>
                {gitResolvedRef && <span className="badge badge-info">Ref: {gitResolvedRef}</span>}
              </div>

              {gitFiles.length > 0 && (
                <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-[1fr,auto] lg:items-end">
                  <div className="form-group">
                    <label className="form-label">Select File</label>
                    <select className="form-select" value={gitSelectedFile} onChange={(e) => setGitSelectedFile(e.target.value)}>
                      {gitFiles.map((file) => <option key={file} value={file}>{file}</option>)}
                    </select>
                  </div>
                  <button className="btn btn-outline" type="button" onClick={handleLoadGitFile} disabled={gitLoading || !gitSelectedFile}>
                    {gitLoading ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
                    Load File
                  </button>
                </div>
              )}

              {gitError && <div className="alert alert-error mt-3">{gitError}</div>}
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="form-group">
                <label className="form-label">{inputLabel}</label>
                <textarea
                  className="form-textarea min-h-[320px]"
                  value={bqSql}
                  onChange={(e) => {
                    setBqSql(e.target.value);
                    clearOutput();
                  }}
                  placeholder="Load a file from Git or paste SQL here..."
                />
              </div>
              <div className="form-group">
                <label className="form-label">Output SQL (Databricks)</label>
                <textarea className="form-textarea min-h-[320px]" value={translatedSql} readOnly placeholder="Converted Databricks SQL appears here..." />
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" type="button" onClick={handleTranslateSql} disabled={loading || !bqSql.trim()}>
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
                Convert SQL
              </button>
              {loading && <button className="btn btn-outline" type="button" onClick={handleCancel}><X size={16} /> Cancel</button>}
              <button className="btn btn-outline" type="button" onClick={handleCopy} disabled={!translatedSql}>
                <Clipboard size={16} /> {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy Failed' : 'Copy SQL'}
              </button>
              <button className="btn btn-success" type="button" onClick={handleRunDatabricks} disabled={!translatedSql || runningDatabricks}>
                {runningDatabricks ? <Loader2 size={16} className="animate-spin" /> : <Database size={16} />}
                Run in Databricks
              </button>
              <button className="btn btn-outline" type="button" onClick={() => setShowGitUpload((prev) => !prev)} disabled={!translatedSql}>
                <GitBranch size={16} /> Upload to Git
              </button>
            </div>

            {showGitUpload && translatedSql && (
              <div className="rounded-lg border border-gray-200 bg-white p-4">
                <div className="mb-3 font-semibold text-gray-900">Upload Translated SQL to Git</div>
                <fieldset className="mb-4 flex flex-wrap gap-4">
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input className="form-radio" type="radio" checked={gitUploadMode === 'create'} onChange={() => setGitUploadMode('create')} />
                    Create new branch
                  </label>
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    <input className="form-radio" type="radio" checked={gitUploadMode === 'existing'} onChange={() => setGitUploadMode('existing')} />
                    Existing branch
                  </label>
                </fieldset>

                {gitUploadMode === 'create' ? (
                  <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                    <Input label="New Branch Name" value={gitNewBranch} onChange={setGitNewBranch} placeholder="translated/databricks-output" />
                    <div className="form-group">
                      <label className="form-label">Base Branch</label>
                      <select className="form-select" value={gitBranch} onChange={(e) => setGitBranch(e.target.value)}>
                        {gitBranches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                      </select>
                    </div>
                  </div>
                ) : (
                  <div className="form-group">
                    <label className="form-label">Target Branch</label>
                    <select className="form-select" value={gitUploadBranch} onChange={(e) => setGitUploadBranch(e.target.value)}>
                      <option value="">Select branch</option>
                      {gitBranches.map((branch) => <option key={branch} value={branch}>{branch}</option>)}
                    </select>
                  </div>
                )}

                {gitUploadMode === 'create' ? (
                  <>
                    <label className="mt-4 flex items-center gap-2 text-sm text-gray-700">
                      <input
                        className="form-checkbox"
                        type="checkbox"
                        checked={gitCreateFolder}
                        onChange={(e) => setGitCreateFolder(e.target.checked)}
                      />
                      <span>Create folder</span>
                    </label>
                    {gitCreateFolder ? (
                      <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
                        <Input label="Folder Name" value={gitNewFolderName} onChange={setGitNewFolderName} placeholder="translated" />
                        <Input label="New File Name" value={gitNewFileName} onChange={setGitNewFileName} placeholder="translated.sql" />
                      </div>
                    ) : (
                      <div className="mt-4">
                        <Input label="New File Name" value={gitNewFileName} onChange={setGitNewFileName} placeholder="translated.sql" />
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <fieldset className="my-4 flex flex-wrap gap-4">
                      <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input className="form-radio" type="radio" checked={gitFolderMode === 'new'} onChange={() => setGitFolderMode('new')} />
                        Create new folder
                      </label>
                      <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input className="form-radio" type="radio" checked={gitFolderMode === 'existing'} onChange={() => setGitFolderMode('existing')} />
                        Put in existing folder
                      </label>
                    </fieldset>

                    {gitFolderMode === 'new' ? (
                      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        <Input label="New Folder Name" value={gitNewFolderName} onChange={setGitNewFolderName} placeholder="translated" />
                        <Input label="New File Name" value={gitNewFileName} onChange={setGitNewFileName} placeholder="translated.sql" />
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        <div className="form-group">
                          <label className="form-label">Choose Folder</label>
                          <select
                            className="form-select"
                            value={gitExistingFolder}
                            onChange={(e) => setGitExistingFolder(e.target.value)}
                          >
                            <option value="">Select folder</option>
                            {gitFolders.map((folder) => (
                              <option key={folder} value={folder}>{folder === '.' ? 'Repository root' : folder}</option>
                            ))}
                          </select>
                        </div>
                        <Input label="New File Name" value={gitNewFileName} onChange={setGitNewFileName} placeholder="translated.sql" />
                      </div>
                    )}
                  </>
                )}

                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn btn-primary" type="button" onClick={handleUploadTranslatedSql} disabled={gitUploadLoading}>
                    {gitUploadLoading ? <Loader2 size={16} className="animate-spin" /> : <GitBranch size={16} />}
                    Upload Translated SQL
                  </button>
                </div>
                {gitUploadMessage && (
                  <div className={`alert mt-3 ${gitUploadMessage.startsWith('Git upload error') ? 'alert-error' : 'alert-info'}`}>
                    {gitUploadMessage}
                  </div>
                )}
              </div>
            )}

            <ResultDetails validation={validation} suggestions={suggestions} finalError={finalError} execution={execution} explanation={explanation} cacheHit={cacheHit} />
            <QueryComplexityMetrics complexity={complexity} sourceLabel={sourceLabel} />
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

function CacheMetric({ title, value }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
      <div className="text-xs font-medium text-gray-500">{title}</div>
      <div className="text-lg font-bold text-gray-900">{value}</div>
    </div>
  );
}

function ResultDetails({ validation, suggestions, finalError, execution, explanation, cacheHit }) {
  if (!validation && !finalError && !execution && !explanation && !cacheHit) return null;

  return (
    <div className="space-y-3">
      {cacheHit && <div className="alert alert-info">This translation was taken from cache.</div>}
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
      {execution && <ExecutionResults execution={execution} />}
      {explanation && (
        <details className="rounded-lg border border-gray-200 bg-white p-4">
          <summary className="cursor-pointer font-semibold text-gray-900">Translation Pipeline</summary>
          <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-xs text-gray-700">{explanation}</pre>
        </details>
      )}
    </div>
  );
}

function ExecutionResults({ execution }) {
  const databricks = execution.databricks || execution;
  const source = execution.source;

  return (
    <div className="space-y-3">
      {execution.repair_message && <div className="alert alert-info">{execution.repair_message}</div>}
      {source ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <ResultTable title="Snowflake Output" result={source} />
          <ResultTable title="Databricks Output" result={databricks} />
        </div>
      ) : (
        <ResultTable title="Databricks Output" result={databricks} />
      )}
    </div>
  );
}

function ResultTable({ title, result }) {
  const rows = Array.isArray(result?.rows) ? result.rows.slice(0, 5) : [];
  const columns = Array.isArray(result?.columns) ? result.columns : (rows[0] ? Object.keys(rows[0]) : []);

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="font-semibold text-gray-900">{title}</div>
        <div className="flex flex-col items-start gap-1 text-xs text-gray-500 sm:items-end">
          <div>Status: <span className="font-semibold">{result?.status || 'Unknown'}</span></div>
          {result?.statement_id && (
            <div>Statement ID: <span className="font-mono font-semibold text-gray-700">{result.statement_id}</span></div>
          )}
        </div>
      </div>
      {result?.error && <div className="alert alert-error mb-3">{result.error}</div>}
      {rows.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {columns.map((column) => <td key={column}>{row[column] === null || row[column] === undefined ? '<null>' : String(row[column])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !result?.error ? (
        <div className="text-sm text-gray-500">No rows returned.</div>
      ) : null}
    </div>
  );
}
