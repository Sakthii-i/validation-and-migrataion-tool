import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

// Add auth token to all requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token');
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
  getStats: (dateFilter = 'Past 30 days', startDate, endDate) =>
    api.get('/dashboard/stats', { params: { date_filter: dateFilter, start_date: startDate, end_date: endDate } }),
};

// ── Results ──
export const resultsAPI = {
  list: (dateFilter = 'Past 30 days', startDate, endDate) =>
    api.get('/results', { params: { date_filter: dateFilter, start_date: startDate, end_date: endDate } }),
  getById: (validationId) =>
    api.get(`/results/${validationId}`),
};

// ── Connections ──
export const connectionAPI = {
  connect: (payload) => api.post('/connections/connect', payload),
  disconnect: () => api.post('/connections/disconnect'),
  status: () => api.get('/connections/status'),
};

// ── Metadata (catalog/schema/table browsing) ──
export const metadataAPI = {
  getCatalogs: (target) =>
    api.post('/metadata/catalogs', { target }),
  getSchemas: (target, catalog) =>
    api.post('/metadata/schemas', { target, catalog }),
  getTables: (target, catalog, schema) =>
    api.post('/metadata/tables', { target, catalog, schema }),
};

// ── Validation ──
export const validationAPI = {
  run: (payload) => api.post('/validate/run', payload),
  runCSV: (formData) =>
    api.post('/validate/csv', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
  runConfig: (config) => api.post('/validate/config', config),
};

// ── Schema Viewer ──
export const schemaAPI = {
  getSchema: (engine, tablePath, filePassword) =>
    api.post('/schema/view', { engine, table_path: tablePath, file_password: filePassword }),
};

export default api;
