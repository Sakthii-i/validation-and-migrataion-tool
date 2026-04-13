import { useState } from 'react';
import { schemaAPI } from '../services/api';
import CollapsibleSection from '../components/CollapsibleSection';
import { Table2, Search, Loader2 } from 'lucide-react';

export default function BQSchemaViewerPage() {
  const [engine, setEngine] = useState('bigquery');
  const [tablePath, setTablePath] = useState('');
  const [filePassword, setFilePassword] = useState('');
  const [schema, setSchema] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleGetSchema = async () => {
    if (!tablePath.trim()) return;
    setLoading(true);
    setError(null);
    setSchema(null);
    try {
      const res = await schemaAPI.getSchema(engine, tablePath.trim(), filePassword);
      setSchema(res.data.columns || []);
    } catch (e) {
      setError(e.response?.data?.detail || 'Failed to fetch schema');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title">Schema Viewer</h1>
      </div>

      <div className="page-content">
        <CollapsibleSection title="Table Schema Information" icon={<Table2 size={16} />}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div className="form-group">
              <label className="form-label">Engine</label>
              <select className="form-select" value={engine} onChange={e => {setEngine(e.target.value); setSchema(null);}}>
                <option value="bigquery">BigQuery</option>
                <option value="snowflake">Snowflake</option>
              </select>
            </div>
            {engine === 'snowflake' && (
              <div className="form-group">
                <label className="form-label">Master Password</label>
                <input 
                  type="password" 
                  className="form-input" 
                  value={filePassword} 
                  onChange={e => setFilePassword(e.target.value)} 
                  placeholder="Password for credential.txt..." 
                />
              </div>
            )}
          </div>
          
          <div className="form-group mb-4">
            <label className="form-label">
              Table Name (format: {engine === 'bigquery' ? 'project.dataset.table' : 'catalog.schema.table'})
            </label>
            <div className="flex gap-2">
              <input
                className="form-input flex-1"
                placeholder={engine === 'bigquery' ? "my-project.my_dataset.my_table" : "my_database.my_schema.my_table"}
                value={tablePath}
                onChange={e => setTablePath(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleGetSchema()}
              />
              <button className="btn btn-primary" onClick={handleGetSchema} disabled={loading || !tablePath.trim() || (engine==='snowflake' && !filePassword)}>
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                Get Schema
              </button>
            </div>
            <span className="form-hint">Fully qualified path is required.</span>
          </div>
        </CollapsibleSection>

        <CollapsibleSection title="Schema Details" icon={<Table2 size={16} />}>
          {error && <div className="alert alert-error">{error}</div>}
          {!schema && !error && !loading && (
            <div className="alert alert-info">Enter a table name and submit to view schema information.</div>
          )}
          {loading && (
            <div className="flex justify-center py-8"><span className="spinner"></span></div>
          )}
          {schema && schema.length > 0 && (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Column Name</th>
                    <th>Data Type</th>
                    <th>Nullable</th>
                  </tr>
                </thead>
                <tbody>
                  {schema.map((col, i) => (
                    <tr key={i}>
                      <td className="text-gray-400">{i + 1}</td>
                      <td className="font-mono text-sm font-medium">{col.column_name}</td>
                      <td><span className="badge badge-purple">{col.data_type}</span></td>
                      <td>{col.is_nullable === 'YES' ? <span className="text-green-600">Yes</span> : <span className="text-red-600">No</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {schema && schema.length === 0 && (
            <div className="alert alert-warning">No columns found for this table.</div>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}
