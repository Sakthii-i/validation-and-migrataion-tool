import React from 'react';
import { Activity, CheckCircle2, Gauge, RefreshCw } from 'lucide-react';

/**
 * SessionStatistics - Compact converter-level counters.
 */
export default function SessionStatistics({ stats }) {
  const safeStats = stats || {};

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
      <SummaryMetric
        title="Queries Processed"
        value={safeStats.total_queries_processed ?? 0}
        icon={<Activity size={22} />}
        tone="gray"
      />
      <SummaryMetric
        title="Migrated"
        value={safeStats.successful_migrations ?? 0}
        tone="blue"
      />
      <SummaryMetric
        title="Validated"
        value={safeStats.validated_queries ?? 0}
        tone="green"
      />
      <SummaryMetric
        title="Complex Queries"
        value={safeStats.complex_queries ?? 0}
        icon={<Gauge size={22} />}
        tone="red"
      />
    </div>
  );
}

function SummaryMetric({ title, value, icon, tone }) {
  const toneClasses = {
    gray: 'border-gray-200 bg-white text-gray-600',
    blue: 'border-blue-200 bg-blue-50 text-blue-700',
    green: 'border-green-200 bg-green-50 text-green-700',
    red: 'border-red-200 bg-red-50 text-red-700',
  };

  return (
    <div className={`rounded-lg border p-4 ${toneClasses[tone] || toneClasses.gray}`}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium">{title}</div>
          <div className="mt-1 text-2xl font-bold text-gray-900">{value}</div>
        </div>
        <div className="opacity-70">{icon}</div>
      </div>
    </div>
  );
}
