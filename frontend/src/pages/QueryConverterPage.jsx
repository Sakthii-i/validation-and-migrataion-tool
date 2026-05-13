import QueryConverterSection from '../components/QueryConverterSection';

export default function QueryConverterPage() {
  return (
    <div>
      <div className="page-topbar">
        <h1 className="page-title">Query Converter</h1>
      </div>

      <div className="page-content">
        <QueryConverterSection />
      </div>
    </div>
  );
}
