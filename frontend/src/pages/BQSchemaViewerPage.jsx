import { useEffect, useState } from 'react';
import { metadataAPI, schemaAPI } from '../services/api';
import { useConnection } from '../context/ConnectionContext';
import CollapsibleSection from '../components/CollapsibleSection';
import { Table2, Search, Loader2 } from 'lucide-react';

export default function BQSchemaViewerPage() {
  const { isConnected, sessionId } = useConnection();
  const [engine, setEngine] = useState('bigquery');
  const [catalogs, setCatalogs] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [tables, setTables] = useState([]);
  const [selectedCatalog, setSelectedCatalog] = useState('');
  const [selectedSchema, setSelectedSchema] = useState('');
  const [selectedTable, setSelectedTable] = useState('');
  const [schema, setSchema] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const sourceTarget = 'source';

  useEffect(() => {
    setSelectedCatalog('');
    setSelectedSchema('');
    setSelectedTable('');
    setSchemas([]);
    setTables([]);
    setSchema(null);
  }, [engine]);

  useEffect(() => {
    if (!selectedCatalog || !isConnected) {
      setSchemas([]);
      setSelectedSchema('');
      setTables([]);
      setSelectedTable('');
      return;
    }
    (async () => {
      try {
        const res = await metadataAPI.getSchemas(sourceTarget, selectedCatalog, sessionId);
        setSchemas(res.data.schemas || []);
      } catch {
        setSchemas([]);
      }
    })();
  }, [selectedCatalog, isConnected, sessionId]);

  useEffect(() => {
    if (!selectedCatalog || !selectedSchema || !isConnected) {
      setTables([]);
      setSelectedTable('');
      return;
    }
    (async () => {
      try {
        const res = await metadataAPI.getTables(sourceTarget, selectedCatalog, selectedSchema, sessionId);
        setTables(res.data.tables || []);
      } catch {
        setTables([]);
      }
    })();
  }, [selectedCatalog, selectedSchema, isConnected, sessionId]);

  const ensureCatalogs = async () => {
    if (!isConnected || catalogs.length) return;
    try {
      const res = await metadataAPI.getCatalogs(sourceTarget, sessionId);
      setCatalogs(res.data.catalogs || []);
    } catch {
      setCatalogs([]);
    }
  };

  const handleGetSchema = async () => {
    if (!selectedCatalog || !selectedSchema || !selectedTable) return;
    setLoading(true);
    setError(null);
    setSchema(null);
    try {
      const tablePath = `${selectedCatalog}.${selectedSchema}.${selectedTable}`;
      const res = await schemaAPI.getSchema(engine, tablePath);
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
          </div>

          {!isConnected && <div className="alert alert-info mb-4">Please establish connection first.</div>}

          {isConnected && (
            <>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                <div className="form-group">
                  <label className="form-label">Catalog</label>
                  <select className="form-select" value={selectedCatalog} onFocus={ensureCatalogs} onChange={e => setSelectedCatalog(e.target.value)}>
                    <option value="">Select catalog...</option>
                    {catalogs.map(c => <option key={c}>{c}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Schema</label>
                  <select className="form-select" value={selectedSchema} onChange={e => setSelectedSchema(e.target.value)} disabled={!selectedCatalog}>
                    <option value="">Select schema...</option>
                    {schemas.map(s => <option key={s}>{s}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Table</label>
                  <select className="form-select" value={selectedTable} onChange={e => setSelectedTable(e.target.value)} disabled={!selectedSchema}>
                    <option value="">Select table...</option>
                    {tables.map(t => <option key={t}>{t}</option>)}
                  </select>
                </div>
              </div>

              <div className="form-group mb-4">
                <button className="btn btn-primary" onClick={handleGetSchema} disabled={loading || !selectedCatalog || !selectedSchema || !selectedTable}>
                  {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
                  Get Schema
                </button>
                <span className="form-hint">Select catalog, schema, and table from dropdowns.</span>
              </div>
            </>
          )}
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
