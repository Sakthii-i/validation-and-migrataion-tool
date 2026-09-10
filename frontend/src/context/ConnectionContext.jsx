import { createContext, useContext, useState } from 'react';
import { connectionAPI } from '../services/api';

const ConnectionContext = createContext(null);

export function ConnectionProvider({ children }) {
  const [sourceEngine, setSourceEngine] = useState('BigQuery');
  const [connectionStatus, setConnectionStatus] = useState('disconnected'); // disconnected | connecting | connected
  const [sessionId, setSessionId] = useState(null);
  const [error, setError] = useState(null);

  // Source credentials
  const [sourceCreds, setSourceCreds] = useState({
    // BigQuery
    project_id: '', dataset_location: 'US', bq_key_path: '',
    // Snowflake
    sf_account: '', sf_user: '', sf_password: '', sf_warehouse: '', sf_role: '',
    // Trino
    trino_host: '', trino_port: '8080', trino_user: 'admin', trino_catalog: '', trino_schema: '', trino_http_scheme: 'http', trino_password: '',
    // Redshift
    redshift_host: '', redshift_port: '5439', redshift_database: '', redshift_user: '', redshift_password: '', redshift_schema: '',
  });

  // Target credentials (Databricks)
  const [targetCreds, setTargetCreds] = useState({
    server_hostname: '', http_path: '', access_token: '',
  });

  const [useStoredCreds, setUseStoredCreds] = useState(false);

  const connect = async () => {
    setConnectionStatus('connecting');
    setError(null);

    if (sourceEngine === 'Redshift') {
      const hasLiveConnectionConfig = Boolean(
        sourceCreds.redshift_host &&
        sourceCreds.redshift_database &&
        sourceCreds.redshift_user &&
        sourceCreds.redshift_password
      );

      if (!hasLiveConnectionConfig) {
        setConnectionStatus('disconnected');
        const msg = 'Redshift conversion does not require a live connection. Add host, database, user, and password only when you want to establish a real Redshift connection.';
        setError(msg);
        return { status: 'skipped', message: msg };
      }
    }

    try {
      const payload = {
        source_engine: sourceEngine,
        use_stored_credentials: true,
        file_password: '',
        source: sourceEngine === 'BigQuery'
          ? {
              project_id: sourceCreds.project_id,
              dataset_location: sourceCreds.dataset_location,
              bq_key_path: sourceCreds.bq_key_path,
            }
          : sourceEngine === 'Trino'
            ? {
                host: sourceCreds.trino_host,
                port: sourceCreds.trino_port,
                user: sourceCreds.trino_user,
                catalog: sourceCreds.trino_catalog,
                schema: sourceCreds.trino_schema,
                http_scheme: sourceCreds.trino_http_scheme,
                password: sourceCreds.trino_password,
              }
            : sourceEngine === 'Redshift'
              ? {
                  host: sourceCreds.redshift_host,
                  port: sourceCreds.redshift_port,
                  database: sourceCreds.redshift_database,
                  user: sourceCreds.redshift_user,
                  password: sourceCreds.redshift_password,
                  schema: sourceCreds.redshift_schema,
                }
              : {},
        target: {},
      };
      const res = await connectionAPI.connect(payload);
      setSessionId(res.data.session_id);
      setConnectionStatus('connected');
      return res.data;
    } catch (err) {
      setConnectionStatus('disconnected');
      const msg = err.response?.data?.detail || 'Connection failed';
      setError(msg);
      throw new Error(msg);
    }
  };

  const disconnect = () => {
    const sid = sessionId;
    if (sid) {
      // Best-effort cleanup; do not block UI.
      connectionAPI.disconnect({ session_id: sid }).catch(() => {});
    }
    setConnectionStatus('disconnected');
    setSessionId(null);
    setError(null);
  };

  const isConnected = connectionStatus === 'connected';

  return (
    <ConnectionContext.Provider value={{
      sourceEngine, setSourceEngine,
      sourceCreds, setSourceCreds,
      targetCreds, setTargetCreds,
      useStoredCreds, setUseStoredCreds,
      connectionStatus, isConnected, sessionId,
      connect, disconnect, error, setError,
    }}>
      {children}
    </ConnectionContext.Provider>
  );
}

export const useConnection = () => {
  const ctx = useContext(ConnectionContext);
  if (!ctx) throw new Error('useConnection must be used inside ConnectionProvider');
  return ctx;
};
