export default function StatusBadge({ status }) {
  const s = (status || '').toUpperCase();
  const cls =
    s === 'PASS' || s === 'COMPLETED' || s === 'SUCCESS' || s === 'SUCCEEDED' || s === 'DONE' ? 'badge-pass' :
    s === 'FAIL' || s === 'FAILED' || s === 'ERROR' ? 'badge-fail' :
    s === 'PROCESSING' || s === 'RUNNING' ? 'badge-processing' :
    s === 'PENDING' || s === 'RECEIVED' || s === 'QUEUED' ? 'badge-info' :
    s === 'N/A' || s === 'NOT RUN' || s === '-' ? 'badge-pending' :
    'badge-pending';
  return <span className={`badge ${cls}`}>{s || '—'}</span>;
}
