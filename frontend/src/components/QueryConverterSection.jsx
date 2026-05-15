import { useEffect, useMemo, useRef, useState } from 'react';
import { Clipboard, Database, Download, Eye, GitBranch, Loader2, Play, RefreshCw, Settings2, Upload, Wand2, X } from 'lucide-react';
import { migrationAPI, validationAPI } from '../services/api';
import { useConnection } from '../context/ConnectionContext';
import { useAuth } from '../context/AuthContext';
import CollapsibleSection from './CollapsibleSection';
import StatusBadge from './StatusBadge';

const API_KEY_STORE = 'validation_tool_converter_api_keys';
const QUERY_SESSION_STORE = 'validation_tool_query_session_id';
const DEFAULT_MODE = 'Auto (deterministic -> LLM migration -> validation)';

const toPayloadSettings = (settings) => {
  const rawPercent = Number(settings.threshold);
  const safePercent = Number.isFinite(rawPercent) ? Math.max(0, Math.min(100, rawPercent)) : 99;
  return {
    ...settings,
    threshold: safePercent / 100,
  };
};

const validationSummaryStatus = (payload) => {
  const rows = Array.isArray(payload?.results) ? payload.results : [];
  const checks = rows.flatMap((row) => [
    row.row_count,
    row.schema_check,
    row.numeric_check,
    row.hash_validation,
  ]).filter((value) => {
    const text = String(value || '').trim().toUpperCase();
    return text && text !== 'N/A' && text !== 'NONE' && text !== '-' && text !== '—';
  });

  if (!rows.length) return '';
  if (!checks.length) return 'DONE';
  return checks.every((value) => String(value).trim().toUpperCase() === 'PASS') ? 'PASS' : 'FAIL';
};

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
  const { user } = useAuth();
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
  const [isOutputEditable, setIsOutputEditable] = useState(false);

  const [showDataValidation, setShowDataValidation] = useState(false);
  const [validationSettings, setValidationSettings] = useState({
    validationType: 'shallow',
    rowCount: false, schema: false, numeric: false, hash: false,
    useThreshold: false, threshold: 99,
    includeTimestamp: false, caseSensitive: false,
  });
  const [queryValidationRunning, setQueryValidationRunning] = useState(false);
  const [queryValidationResults, setQueryValidationResults] = useState(null);
  const [queryValidationError, setQueryValidationError] = useState('');
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
  const [queryName, setQueryName] = useState('');
  const [queryId, setQueryId] = useState('');

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
    setIsOutputEditable(false);
    setValidation(null);
    setSuggestions([]);
    setFinalError('');
    setExecution(null);
    setExplanation('');
      setCacheHit(false);
      setCopyState('idle');
      setQueryValidationResults(null);
      setQueryValidationError('');
      setQueryId('');
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
    query_name: queryName,
    query_id: queryId,
    run_by: user?.username || '',
    input_mode: inputMode,
  });

  const handleTranslateSql = async () => {
    if (!bqSql.trim()) {
      setTranslatedSql(`Please enter a ${sourceLabel} SQL query.`);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    clearOutput();
    try {
      const res = await migrationAPI.translateSql({ ...buildPayload(), query_id: '', bq_sql: bqSql }, controller.signal);
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
      setQueryId(data.query_id || '');
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
        queryName,
        runBy: user?.username || '',
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
      if (queryId) {
        await migrationAPI.updateQueryHistory(queryId, {
          source_engine: sourceEngine.toLowerCase(),
          pushed_to_git: true,
        });
      }
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

  const handleRunCsvDatabricks = async () => {
    const runnableRows = csvResults
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => String(row.translated_sql || '').trim());

    if (!runnableRows.length || runningDatabricks) return;
    if (isSnowflake && !isConnected) {
      setCsvError('Please establish a Snowflake connection from the sidebar before running source and Databricks outputs.');
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRunningDatabricks(true);
    setCsvError('');
    try {
      for (const { row, index } of runnableRows) {
        const res = await migrationAPI.runStoredDatabricks({
          sql: row.translated_sql,
          source_sql: row.original_sql,
          source_engine: sourceEngine.toLowerCase(),
          provider,
          model,
          api_key: selectedApiKey,
          session_id: sessionId,
        }, controller.signal);

        setCsvResults((prev) => prev.map((item, itemIndex) => {
          if (itemIndex !== index) return item;
          const executionData = res.data.execution || null;
          return {
            ...item,
            translated_sql: executionData?.repaired_sql || item.translated_sql,
            execution: executionData,
          };
        }));
      }
    } catch (err) {
      if (err?.name === 'CanceledError' || err?.code === 'ERR_CANCELED') {
        setCsvError('Databricks run cancelled.');
      } else {
        setCsvError(`Databricks run error: ${err.response?.data?.detail || err.message}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setRunningDatabricks(false);
    }
  };

  const handleRunCsvValidation = async () => {
    setQueryValidationError('');
    setQueryValidationResults(null);

    const validRows = csvResults
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => String(row.original_sql || '').trim() && String(row.translated_sql || '').trim());

    if (!showDataValidation || !validRows.length) return;
    if (!sessionId) {
      setQueryValidationError(`No active session. Load ${sourceLabel} credentials from Run Validation first.`);
      return;
    }
    if (isSnowflake && !hasRequiredSnowflakeConnection) {
      setQueryValidationError('Please establish a Snowflake connection from the sidebar first.');
      return;
    }

    const metricsSelected = validationSettings.validationType === 'shallow'
      || Boolean(validationSettings.rowCount || validationSettings.schema || validationSettings.numeric || validationSettings.hash);
    if (!metricsSelected) {
      setQueryValidationError('Select at least one metric (or choose Shallow).');
      return;
    }

    setQueryValidationRunning(true);
    try {
      const collectedResults = [];
      const validationRecords = [];
      for (const { row, index } of validRows) {
        const res = await validationAPI.runQuery({
          session_id: sessionId,
          validation_type: validationSettings.validationType,
          run_by: user?.username || undefined,
          settings: toPayloadSettings(validationSettings),
          source_sql: row.original_sql,
          target_sql: row.translated_sql,
        });
        const validationData = res.data;
        collectedResults.push({
          row: row.row_index + 1,
          query: row.query_index + 1,
          result: validationData,
        });
        validationRecords.push(...(validationData.results || []));
        setCsvResults((prev) => prev.map((item, itemIndex) => (
          itemIndex === index ? { ...item, query_validation: validationData, query_validation_error: '' } : item
        )));
        if (row.query_id) {
          await migrationAPI.updateQueryHistory(row.query_id, {
            source_engine: sourceEngine.toLowerCase(),
            validation_status: 'VALIDATED',
          });
        }
      }
      setQueryValidationResults({ results: validationRecords, csv_results: collectedResults });
    } catch (err) {
      setQueryValidationError(err.response?.data?.detail || err.message || 'Failed to run validation.');
    } finally {
      setQueryValidationRunning(false);
    }
  };

  const handleRunQueryValidation = async () => {
    setQueryValidationError('');
    setQueryValidationResults(null);

    if (!showDataValidation) return;
    if (!sessionId) {
      setQueryValidationError(`No active session. Load ${sourceLabel} credentials from Run Validation first.`);
      return;
    }
    if (!bqSql.trim() || !translatedSql.trim()) {
      setQueryValidationError('Both source SQL and target SQL are required.');
      return;
    }
    if (isSnowflake && !hasRequiredSnowflakeConnection) {
      setQueryValidationError('Please establish a Snowflake connection from the sidebar first.');
      return;
    }

    const metricsSelected = validationSettings.validationType === 'shallow'
      || Boolean(validationSettings.rowCount || validationSettings.schema || validationSettings.numeric || validationSettings.hash);
    if (!metricsSelected) {
      setQueryValidationError('Select at least one metric (or choose Shallow).');
      return;
    }

    setQueryValidationRunning(true);
    try {
      const res = await validationAPI.runQuery({
        session_id: sessionId,
        validation_type: validationSettings.validationType,
        run_by: user?.username || undefined,
        settings: toPayloadSettings(validationSettings),
        source_sql: bqSql,
        target_sql: translatedSql,
      });
      setQueryValidationResults(res.data);
      if (queryId) {
        await migrationAPI.updateQueryHistory(queryId, {
          source_engine: sourceEngine.toLowerCase(),
          validation_status: 'VALIDATED',
        });
      }
    } catch (err) {
      setQueryValidationError(err.response?.data?.detail || err.message || 'Failed to run validation.');
    } finally {
      setQueryValidationRunning(false);
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

  const dataValidationMetricsSelected = validationSettings.validationType === 'shallow'
    || Boolean(validationSettings.rowCount || validationSettings.schema || validationSettings.numeric || validationSettings.hash);

  const csvRowsReadyForValidation = useMemo(
    () => csvResults.some((row) => String(row.original_sql || '').trim() && String(row.translated_sql || '').trim()),
    [csvResults],
  );

  const queryValidationBlockers = useMemo(() => {
    const blockers = [];
    if (!showDataValidation) return blockers;
    if (!sessionId) blockers.push('No active session. Open Run Validation and connect first.');
    if (inputMode === 'csv') {
      if (!csvRowsReadyForValidation) blockers.push('No CSV rows have both source SQL and converted SQL.');
    } else {
      if (!bqSql.trim()) blockers.push(`${sourceLabel} source SQL is empty.`);
      if (!translatedSql.trim()) blockers.push('Converted SQL for validation is empty.');
    }
    if (!dataValidationMetricsSelected) blockers.push('Select at least one metric (or choose Shallow).');
    if (isSnowflake && !hasRequiredSnowflakeConnection) blockers.push('Snowflake connection is required (use the sidebar to connect).');
    return blockers;
  }, [
    showDataValidation,
    sessionId,
    inputMode,
    csvRowsReadyForValidation,
    bqSql,
    translatedSql,
    dataValidationMetricsSelected,
    isSnowflake,
    hasRequiredSnowflakeConnection,
    sourceLabel,
  ]);
  const canRunQueryValidation = Boolean(
    showDataValidation
    && sessionId
    && (inputMode === 'csv' ? csvRowsReadyForValidation : (bqSql.trim() && translatedSql.trim()))
    && dataValidationMetricsSelected
    && (!isSnowflake || hasRequiredSnowflakeConnection)
  );

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
              <div className="space-y-3">
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
              </div>
              <div className="form-group">
                <label className="form-label">Output SQL (Databricks)</label>
                <textarea
                  className="form-textarea min-h-[360px]"
                  value={translatedSql}
                  onChange={(e) => setTranslatedSql(e.target.value)}
                  readOnly={!isOutputEditable}
                  placeholder="Converted Databricks SQL appears here..."
                />
              </div>
            </div>

            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-[minmax(220px,320px),auto]">
                <div className="form-group">
                  <label className="form-label text-sm">Query Name</label>
                  <input
                    type="text"
                    className="form-input"
                    value={queryName}
                    onChange={(e) => setQueryName(e.target.value)}
                    placeholder="Enter query name..."
                  />
                </div>
                <div className="pb-2 text-sm font-medium text-gray-700">Query ID: {queryId}</div>
              </div>
              <button
                className={`btn btn-xs ${isOutputEditable ? 'btn-primary' : 'btn-outline'}`}
                type="button"
                onClick={() => setIsOutputEditable((prev) => !prev)}
                disabled={!translatedSql.trim()}
              >
                {isOutputEditable ? 'Lock' : 'Edit'}
              </button>
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
              <button
                className={`btn btn-outline ${showDataValidation ? 'btn-primary' : ''}`}
                type="button"
                onClick={() => setShowDataValidation((prev) => !prev)}
                disabled={!bqSql.trim() || !translatedSql.trim()}
              >
                <Play size={16} /> Data Validation
              </button>
            </div>

            {showDataValidation && (
              <div className="space-y-4">
                {!sessionId && (
                  <div className="alert alert-info">
                    Load {sourceLabel} credentials from Run Validation to enable query validation.
                  </div>
                )}
                <QueryConverterValidationSettings settings={validationSettings} setSettings={setValidationSettings} />
                {queryValidationError && <div className="alert alert-error">{queryValidationError}</div>}
                {!queryValidationRunning && showDataValidation && !canRunQueryValidation && queryValidationBlockers.length > 0 && (
                  <div className="alert alert-warning">
                    <div className="font-semibold mb-1">Run Validation is disabled because:</div>
                    <ul className="list-disc ml-5 text-sm">
                      {queryValidationBlockers.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <button
                  className="btn btn-primary btn-full btn-lg"
                  type="button"
                  onClick={handleRunQueryValidation}
                  disabled={!canRunQueryValidation || queryValidationRunning}
                >
                  {queryValidationRunning ? <><Loader2 size={18} className="animate-spin" /> Running Validations...</> : <><Play size={18} /> Run Validation</>}
                </button>
                {queryValidationResults && <ResultsDisplay results={queryValidationResults} />}
              </div>
            )}

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
              <button className="btn btn-success" type="button" onClick={handleRunCsvDatabricks} disabled={!csvResults.length || runningDatabricks}>
                {runningDatabricks ? <Loader2 size={16} className="animate-spin" /> : <Database size={16} />}
                Run in Databricks
              </button>
              <button
                className={`btn btn-outline ${showDataValidation ? 'btn-primary' : ''}`}
                type="button"
                onClick={() => setShowDataValidation((prev) => !prev)}
                disabled={!csvResults.length}
              >
                <Play size={16} /> Data Validation
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
                        <th>Query ID</th>
                        <th>Complexity</th>
                        <th>Original SQL</th>
                        <th>Translated SQL</th>
                        <th>Databricks</th>
                        <th>Validation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {csvResults.map((row, index) => (
                        <tr key={`${row.row_index}-${row.query_index}-${index}`}>
                          <td>{row.row_index + 1}</td>
                          <td className="font-mono text-xs text-primary-600">{row.query_id || '-'}</td>
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
                          <td>
                            {row.execution ? (
                              <StatusBadge status={row.execution?.databricks?.status || row.execution?.status || 'RUN'} />
                            ) : (
                              <span className="text-xs text-gray-400">-</span>
                            )}
                          </td>
                          <td>
                            {row.query_validation ? (
                              <StatusBadge status={validationSummaryStatus(row.query_validation)} />
                            ) : (
                              <span className="text-xs text-gray-400">-</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {showDataValidation && (
                  <div className="space-y-4">
                    {!sessionId && (
                      <div className="alert alert-info">
                        Load {sourceLabel} credentials from Run Validation to enable query validation.
                      </div>
                    )}
                    <QueryConverterValidationSettings settings={validationSettings} setSettings={setValidationSettings} />
                    {queryValidationError && <div className="alert alert-error">{queryValidationError}</div>}
                    {!queryValidationRunning && showDataValidation && !canRunQueryValidation && queryValidationBlockers.length > 0 && (
                      <div className="alert alert-warning">
                        <div className="font-semibold mb-1">Run Validation is disabled because:</div>
                        <ul className="list-disc ml-5 text-sm">
                          {queryValidationBlockers.map((reason) => (
                            <li key={reason}>{reason}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <button
                      className="btn btn-primary btn-full btn-lg"
                      type="button"
                      onClick={handleRunCsvValidation}
                      disabled={!canRunQueryValidation || queryValidationRunning}
                    >
                      {queryValidationRunning ? <><Loader2 size={18} className="animate-spin" /> Running Validations...</> : <><Play size={18} /> Run Validation</>}
                    </button>
                    {queryValidationResults?.csv_results && (
                      <div className="alert alert-info">
                        Completed validation for {queryValidationResults.csv_results.length} CSV quer{queryValidationResults.csv_results.length === 1 ? 'y' : 'ies'}.
                      </div>
                    )}
                    {queryValidationResults && <ResultsDisplay results={queryValidationResults} />}
                  </div>
                )}
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
                <div className="mb-1 flex items-center justify-between gap-2">
                  <label className="form-label mb-0">Output SQL (Databricks)</label>
                  <button
                    className={`btn btn-xs ${isOutputEditable ? 'btn-primary' : 'btn-outline'}`}
                    type="button"
                    onClick={() => setIsOutputEditable((prev) => !prev)}
                    disabled={!translatedSql.trim()}
                  >
                    {isOutputEditable ? 'Lock' : 'Edit'}
                  </button>
                </div>
                <textarea
                  className="form-textarea min-h-[320px]"
                  value={translatedSql}
                  onChange={(e) => setTranslatedSql(e.target.value)}
                  readOnly={!isOutputEditable}
                  placeholder="Converted Databricks SQL appears here..."
                />
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
              <button
                className={`btn btn-outline ${showDataValidation ? 'btn-primary' : ''}`}
                type="button"
                onClick={() => setShowDataValidation((prev) => !prev)}
                disabled={!bqSql.trim() || !translatedSql.trim()}
              >
                <Play size={16} /> Data Validation
              </button>
            </div>

            {showDataValidation && (
              <div className="space-y-4">
                {!sessionId && (
                  <div className="alert alert-info">
                    Load {sourceLabel} credentials from Run Validation to enable query validation.
                  </div>
                )}
                <QueryConverterValidationSettings settings={validationSettings} setSettings={setValidationSettings} />
                {queryValidationError && <div className="alert alert-error">{queryValidationError}</div>}
                {!queryValidationRunning && showDataValidation && !canRunQueryValidation && queryValidationBlockers.length > 0 && (
                  <div className="alert alert-warning">
                    <div className="font-semibold mb-1">Run Validation is disabled because:</div>
                    <ul className="list-disc ml-5 text-sm">
                      {queryValidationBlockers.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <button
                  className="btn btn-primary btn-full btn-lg"
                  type="button"
                  onClick={handleRunQueryValidation}
                  disabled={!canRunQueryValidation || queryValidationRunning}
                >
                  {queryValidationRunning ? <><Loader2 size={18} className="animate-spin" /> Running Validations...</> : <><Play size={18} /> Run Validation</>}
                </button>
                {queryValidationResults && <ResultsDisplay results={queryValidationResults} />}
              </div>
            )}

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

function QueryComplexityMetrics({ complexity, sourceLabel }) {
  if (!complexity) return null;

  const level = complexity.complexity_level ?? complexity.level ?? 'Unknown';
  const score = complexity.complexity_score ?? complexity.score ?? null;
  const extraEntries = Object.entries(complexity)
    .filter(([key, value]) => !['complexity_level', 'complexity_score', 'level', 'score'].includes(key) && (typeof value === 'number' || typeof value === 'string'))
    .sort(([a], [b]) => a.localeCompare(b));

  return (
    <CollapsibleSection title="Query Complexity" icon={<Eye size={16} />} defaultOpen={false}>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <div className="text-xs font-medium text-gray-500">{sourceLabel} Complexity Level</div>
          <div className="text-lg font-bold text-gray-900">{level}</div>
        </div>
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <div className="text-xs font-medium text-gray-500">Complexity Score</div>
          <div className="text-lg font-bold text-gray-900">{score ?? '—'}</div>
        </div>
      </div>

      {extraEntries.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {extraEntries.map(([key, value]) => (
                <tr key={key}>
                  <td className="font-mono text-xs">{key}</td>
                  <td>{String(value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CollapsibleSection>
  );
}

function QueryConverterValidationSettings({ settings, setSettings }) {
  const hashSelected = settings.validationType === 'deep' && settings.hash;

  return (
    <CollapsibleSection title="⚙️ Validation Settings" icon={<Settings2 size={16} />} defaultOpen={true}>
      <div className="flex items-center gap-4 mb-4">
        <label className="form-label mb-0">Validation Type:</label>
        <div className="flex rounded-lg overflow-hidden border border-gray-300">
          {['shallow', 'deep'].map((t) => (
            <button
              key={t}
              className={`px-5 py-2 text-sm font-medium transition-colors ${
                settings.validationType === t
                  ? 'bg-primary-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
              onClick={() => setSettings((p) => ({ ...p, validationType: t }))}
              type="button"
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
                onChange={(e) => setSettings((p) => ({ ...p, [key]: e.target.checked }))}
              />
              <span className="text-sm">{label}</span>
            </label>
          ))}
        </div>
      )}

      <details className="border border-gray-200 rounded-lg">
        <summary className="px-4 py-2.5 text-sm font-medium text-gray-600 cursor-pointer hover:bg-gray-50 rounded-lg">
          Advanced Options
        </summary>
        <div className="px-4 pb-4 pt-2 space-y-3 border-t border-gray-100">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="form-checkbox"
              checked={settings.useThreshold}
              onChange={(e) => setSettings((p) => ({ ...p, useThreshold: e.target.checked }))}
            />
            <span className="text-sm">Use acceptable threshold for passing</span>
          </label>

          {settings.useThreshold && (
            <div className="form-group ml-6">
              <label className="form-label">Threshold (%)</label>
              <input
                type="number"
                className="form-input w-40"
                value={settings.threshold}
                min="0"
                max="100"
                step="1"
                onChange={(e) => setSettings((p) => ({ ...p, threshold: e.target.value }))}
              />
              <span className="form-hint">e.g., 99 means 99% match</span>
            </div>
          )}

          {hashSelected && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="form-checkbox"
                checked={settings.includeTimestamp}
                onChange={(e) => setSettings((p) => ({ ...p, includeTimestamp: e.target.checked }))}
              />
              <span className="text-sm">Include TIMESTAMP columns in row hash</span>
            </label>
          )}

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="form-checkbox"
              checked={settings.caseSensitive}
              onChange={(e) => setSettings((p) => ({ ...p, caseSensitive: e.target.checked }))}
            />
            <span className="text-sm">Case-sensitive schema validation</span>
          </label>
        </div>
      </details>
    </CollapsibleSection>
  );
}

function ResultsDisplay({ results }) {
  const [detailRecord, setDetailRecord] = useState(null);

  if (!results) return null;
  if (results.error) return <div className="alert alert-error mt-4">❌ {results.error}</div>;

  const records = results.results || results.validation_ids || [];
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

  const nullCountRows = numericRows.filter((row) => {
    const srcNull = Number(row?.source_null_count || 0);
    const tgtNull = Number(row?.target_null_count || 0);
    return srcNull > 0 || tgtNull > 0;
  });

  const nullCountSummary = nullCountRows
    .map((row) => {
      const srcNull = Number(row?.source_null_count || 0);
      const tgtNull = Number(row?.target_null_count || 0);
      if (srcNull > 0 && tgtNull > 0) return `${row.column}: Source ${srcNull}, Target ${tgtNull}`;
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
                  <div className="p-3 border rounded-lg bg-gray-50 text-sm mb-4">
                    <span className="font-semibold text-gray-700">Not matched columns: </span>
                    {(detailRecord.details.row_hash.mismatched_columns || []).length > 0
                      ? detailRecord.details.row_hash.mismatched_columns.join(', ')
                      : 'None'}
                  </div>
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
                </>
              ) : <div className="text-sm text-gray-500 mb-3">{detailRecord.details?.row_hash?.error ? `No hash details available: ${detailRecord.details.row_hash.error}` : 'No hash details available.'}</div>}
            </div>
          </div>
        </div>
      )}
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
