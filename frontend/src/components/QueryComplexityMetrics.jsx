import React from 'react';
import { AlertCircle, CheckCircle2, Circle, Gauge, TrendingUp, Zap } from 'lucide-react';

/**
 * QueryComplexityMetrics - Displays DQX Analyzer-style query complexity analysis.
 */
export default function QueryComplexityMetrics({ complexity, sourceLabel = 'Snowflake' }) {
  if (!complexity) return null;

  const getComplexityColor = (level) => {
    switch (level) {
      case 'SIMPLE':
        return 'text-green-600 bg-green-50 border-green-200';
      case 'MEDIUM':
        return 'text-amber-600 bg-amber-50 border-amber-200';
      case 'COMPLEX':
        return 'text-red-600 bg-red-50 border-red-200';
      default:
        return 'text-gray-600 bg-gray-50 border-gray-200';
    }
  };

  const getRiskColor = (level) => {
    switch (level) {
      case 'low':
        return 'text-green-600 bg-green-50 border-green-200';
      case 'medium':
        return 'text-amber-600 bg-amber-50 border-amber-200';
      case 'high':
        return 'text-red-600 bg-red-50 border-red-200';
      default:
        return 'text-gray-600 bg-gray-50 border-gray-200';
    }
  };

  const getComplexityIconColor = (level) => {
    switch (level) {
      case 'SIMPLE':
        return 'text-green-600';
      case 'MEDIUM':
        return 'text-amber-600';
      case 'COMPLEX':
        return 'text-red-600';
      default:
        return 'text-gray-500';
    }
  };

  return (
    <div className="space-y-4">
      <div className={`rounded-lg border p-4 ${getComplexityColor(complexity.complexity_level)}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium opacity-75">Query Complexity (Databricks Labs DQX Analyzer)</div>
            <div className="mt-1 flex items-center gap-2">
              <Gauge size={24} className={getComplexityIconColor(complexity.complexity_level)} />
              <span className="text-xl font-bold">{complexity.complexity_level}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="text-3xl font-bold">{complexity.complexity_score}</div>
            <div className="text-xs opacity-75">/ 100</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric title="Tables" value={complexity.tables_referenced} />
        <Metric title="Joins" value={complexity.joins_count} />
        <Metric title="Subqueries" value={complexity.subqueries_count} />
        <Metric title="CTEs" value={complexity.cte_count} />
      </div>

      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="mb-3 font-semibold text-gray-900">Features Detected</div>
        <div className="space-y-2">
          <Feature active={complexity.aggregations} label="Aggregations (GROUP BY / HAVING)" />
          <Feature active={complexity.window_functions} label="Window Functions (OVER)" />
          <Feature active={complexity.set_operations} label="Set Operations (UNION / INTERSECT / EXCEPT)" />
          {complexity.vendor_specific_functions?.length > 0 && (
            <div className="flex items-start gap-2 text-sm">
              <AlertCircle size={16} className="mt-0.5 flex-shrink-0 text-amber-600" />
              <span className="text-gray-900">Vendor-specific: {complexity.vendor_specific_functions.join(', ')}</span>
            </div>
          )}
        </div>
      </div>

      <div className={`rounded-lg border p-4 ${getRiskColor(complexity.estimated_conversion_risk)}`}>
        <div className="mb-3 flex items-center gap-2 font-semibold">
          <TrendingUp size={18} />
          <span>{sourceLabel} to Databricks Conversion Risk</span>
        </div>
        <div className="mb-2 text-sm font-medium capitalize">Risk Level: {complexity.estimated_conversion_risk.toUpperCase()}</div>
        {complexity.conversion_risk_factors?.length > 0 ? (
          <ul className="space-y-1">
            {complexity.conversion_risk_factors.map((factor, idx) => (
              <li key={idx} className="flex items-start gap-2 text-sm">
                <Circle size={6} className="mt-1.5 flex-shrink-0 fill-current" />
                <span>{factor}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm">No conversion risks identified.</p>
        )}
      </div>

      {complexity.indicators?.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="mb-3 font-semibold text-gray-900">Complexity Patterns Detected</div>
          <div className="flex flex-wrap gap-2">
            {complexity.indicators.map((indicator, idx) => (
              <span key={idx} className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800">
                <Zap size={12} />
                {indicator.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Metric({ title, value }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 text-center">
      <div className="text-2xl font-bold text-primary-600">{value ?? 0}</div>
      <div className="text-xs text-gray-600">{title}</div>
    </div>
  );
}

function Feature({ active, label }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {active ? <CheckCircle2 size={16} className="text-green-600" /> : <div className="h-4 w-4" />}
      <span className={active ? 'text-gray-900' : 'text-gray-400'}>{label}</span>
    </div>
  );
}
