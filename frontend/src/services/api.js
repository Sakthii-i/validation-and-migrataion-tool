import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

// Add auth token to all requests
api.interceptors.request.use((config) => {
  const token = sessionStorage.getItem('auth_token') || localStorage.getItem('auth_token');
  if (token) config.headers['Authorization'] = `Bearer ${token}`;
  return config;
});

// ── Auth ──
export const authAPI = {
  login: (username, password, role = 'user') =>
    api.post('/auth/login', { username, password, role }),
  listUsers: () => api.get('/auth/users'),
  grantAccess: (username, password) =>
    api.post('/auth/grant', { username, password }),
  revokeAccess: (username) =>
    api.post('/auth/revoke', { username }),
};

// ── Dashboard ──
export const dashboardAPI = {
  getStats: (dateFilter = 'Past 30 days', startDate, endDate, sourceEngine) =>
    api.get('/dashboard/stats', {
      params: {
        date_filter: dateFilter,
        start_date: startDate,
        end_date: endDate,
        source_engine: sourceEngine,
      },
    }),
};

// ── Results ──
export const resultsAPI = {
  list: (dateFilter = 'Past 30 days', startDate, endDate, sourceEngine) =>
    api.get('/results', {
      params: {
        date_filter: dateFilter,
        start_date: startDate,
        end_date: endDate,
        source_engine: sourceEngine,
      },
    }),
  getById: (validationId) =>
    api.get(`/results/${validationId}`),
};

// ── Connections ──
export const connectionAPI = {
  connect: (payload) => api.post('/connections/connect', payload),
  disconnect: (payload = {}) => api.post('/connections/disconnect', payload),
  status: () => api.get('/connections/status'),
};

// ── Metadata (catalog/schema/table browsing) ──
export const metadataAPI = {
  getCatalogs: (target, sessionId = null) =>
    api.post('/metadata/catalogs', { target, session_id: sessionId }),
  getSchemas: (target, catalog, sessionId = null) =>
    api.post('/metadata/schemas', { target, catalog, session_id: sessionId }),
  getTables: (target, catalog, schema, sessionId = null) =>
    api.post('/metadata/tables', { target, catalog, schema_name: schema, session_id: sessionId }),
};

// ── Validation ──
export const validationAPI = {
  run: (payload) => api.post('/validate/run', payload),
  runQuery: (payload) => api.post('/validate/query', payload),
  runCSV: (formData) =>
    api.post('/validate/csv', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  runConfig: (config) => api.post('/validate/config', config),
};

// ── Schema Viewer ──
export const schemaAPI = {
  getSchema: (engine, tablePath) =>
    api.post('/schema/view', { engine, table_path: tablePath }),
};

// BigQuery -> Databricks migration converter
export const migrationAPI = {
  getConfig: () => api.get('/migration/config'),
  getCacheStats: () => api.get('/migration/cache/stats'),
  getSessionStats: (sessionId = null, sourceEngine = null) => {
    const params = {};
    if (sessionId) params.session_id = sessionId;
    if (sourceEngine) params.source_engine = sourceEngine;
    if (Object.keys(params).length) return api.get('/migration/session-stats', { params });
    return api.get('/migration/session-stats');
  },
  listQueryHistory: (sourceEngine = 'bigquery') => api.get('/migration/query-history', { params: { source_engine: sourceEngine } }),
  updateQueryHistory: (queryId, payload) => api.patch(`/migration/query-history/${queryId}`, payload),
  clearCache: () => api.post('/migration/cache/clear'),
  getNormalizedPreview: (sql) => api.post('/migration/preview/normalized', { sql }),
  getGitBranches: (payload, signal) => api.post('/migration/git/branches', payload, { signal }),
  getGitFiles: (payload, signal) => api.post('/migration/git/files', payload, { signal }),
  getGitFile: (payload, signal) => api.post('/migration/git/file', payload, { signal }),
  uploadGitFile: (payload, signal) => api.post('/migration/git/upload', payload, { signal }),
  translateSql: (payload, signal) => api.post('/migration/translate', payload, { signal }),
  runStoredDatabricks: (payload, signal) => api.post('/migration/databricks/execute-stored', payload, { signal }),
  translateCsv: (file, options = {}, signal) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('source_engine', options.sourceEngine || options.source_engine || 'bigquery');
    formData.append('provider', options.provider || 'OpenAI');
    formData.append('model', options.model || '');
    formData.append('mode', options.mode || 'Auto (deterministic -> LLM migration -> validation)');
    formData.append('api_key', options.apiKey || '');
    formData.append('run_in_databricks', options.runInDatabricks ? 'true' : 'false');
    formData.append('databricks_host', options.databricksConfig?.host || '');
    formData.append('databricks_token', options.databricksConfig?.token || '');
    formData.append('databricks_warehouse_id', options.databricksConfig?.warehouse_id || '');
    formData.append('databricks_catalog', options.databricksConfig?.catalog || '');
    formData.append('databricks_schema', options.databricksConfig?.schema || '');
    formData.append('databricks_timeout_seconds', String(options.databricksConfig?.timeout_seconds ?? 90));
    formData.append('databricks_max_rows', String(options.databricksConfig?.max_rows ?? 200));
    formData.append('session_id', options.sessionId || options.session_id || '');

    return api.post('/migration/translate/csv', formData, {
      signal,
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },
};

export default api;
