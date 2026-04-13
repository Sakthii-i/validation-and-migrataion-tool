import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import Sidebar from './components/Sidebar';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import NewValidationPage from './pages/NewValidationPage';
import DataValidationsPage from './pages/DataValidationsPage';
import ValidationDashboardPage from './pages/ValidationDashboardPage';
import BQSchemaViewerPage from './pages/BQSchemaViewerPage';
import AdminPage from './pages/AdminPage';

function ProtectedRoute({ children }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? children : <Navigate to="/login" replace />;
}

function AppLayout({ children }) {
  return (
    <div className="flex">
      <Sidebar />
      <div className="page-wrapper flex-1">
        {children}
      </div>
    </div>
  );
}

export default function App() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <AppLayout>
      <Routes>
        <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
        <Route path="/new-validation" element={<ProtectedRoute><NewValidationPage /></ProtectedRoute>} />
        <Route path="/data-validations" element={<ProtectedRoute><DataValidationsPage /></ProtectedRoute>} />
        <Route path="/validation-dashboard" element={<ProtectedRoute><ValidationDashboardPage /></ProtectedRoute>} />
        <Route path="/bq-schema-viewer" element={<ProtectedRoute><BQSchemaViewerPage /></ProtectedRoute>} />
        <Route path="/admin" element={<ProtectedRoute><AdminPage /></ProtectedRoute>} />
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppLayout>
  );
}
