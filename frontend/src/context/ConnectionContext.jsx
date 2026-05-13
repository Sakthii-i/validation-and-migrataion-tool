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
  });

  // Target credentials (Databricks)
  const [targetCreds, setTargetCreds] = useState({
    server_hostname: '', http_path: '', access_token: '',
  });

  const [useStoredCreds, setUseStoredCreds] = useState(false);

  const connect = async () => {
    setConnectionStatus('connecting');
    setError(null);
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
