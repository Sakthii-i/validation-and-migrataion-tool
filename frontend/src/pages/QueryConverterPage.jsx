import { RefreshCw } from 'lucide-react';
import QueryConverterSection from '../components/QueryConverterSection';

export default function QueryConverterPage() {
  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title">Query Converter</h1>
        <span className="text-sm text-gray-500">BigQuery to Databricks</span>
      </div>

      <div className="page-content">
        <div className="section-header">
          <div className="flex items-center gap-2">
            <RefreshCw size={16} />
            <span>Query Converter</span>
          </div>
        </div>
        <div className="section-body">
          <QueryConverterSection />
        </div>
      </div>
    </div>
  );
}
