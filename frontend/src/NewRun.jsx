import React, { useState } from 'react';

export default function NewRun({ onRunCreated }) {
  const [buyerName, setBuyerName] = useState('Acme Corp Risk Assessment');
  const [file, setFile] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [statusMsg, setStatusMsg] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) {
      alert('Please select a questionnaire file (.xlsx or .csv)');
      return;
    }

    setSubmitting(true);
    setStatusMsg('Provisioning disposable Daytona sandbox...');

    const formData = new FormData();
    formData.append('buyer_name', buyerName);
    formData.append('file', file);

    try {
      const res = await fetch('/api/runs', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.run_id) {
        setStatusMsg('Run ingested successfully! Redirecting to review screen...');
        if (onRunCreated) onRunCreated(data.run_id);
      } else {
        setStatusMsg('Error creating run: ' + (data.detail || 'Unknown error'));
      }
    } catch (err) {
      setStatusMsg('Failed: ' + err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="new-run-page" style={{ maxWidth: '640px', margin: '0 auto' }}>
      <h2 className="page-title">Start Buyer Questionnaire Run</h2>

      <div className="card">
        <p style={{ color: 'var(--ink-2)', fontSize: '14px', marginBottom: '24px', lineHeight: '1.6' }}>
          Every buyer sends a uniquely-shaped questionnaire. Upload the buyer's original <code>.xlsx</code> or <code>.csv</code> file.
          An isolated <strong>Daytona Sandbox</strong> will inspect the structure, write a custom parser on the fly, and extract questions cleanly.
        </p>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '20px' }}>
            <label className="section-heading" style={{ display: 'block', marginBottom: '8px' }}>
              Buyer / Customer Name
            </label>
            <input
              type="text"
              className="btn btn-secondary"
              style={{ width: '100%', textAlign: 'left', cursor: 'text', padding: '10px 14px' }}
              value={buyerName}
              onChange={(e) => setBuyerName(e.target.value)}
              required
            />
          </div>

          <div style={{ marginBottom: '24px' }}>
            <label className="section-heading" style={{ display: 'block', marginBottom: '8px' }}>
              Questionnaire File (.xlsx, .csv)
            </label>
            <input
              type="file"
              accept=".xlsx,.csv"
              onChange={(e) => setFile(e.target.files[0])}
              style={{ color: 'var(--ink-2)' }}
              required
            />
          </div>

          {statusMsg && (
            <div className="card-surface" style={{ marginBottom: '20px', padding: '12px', fontSize: '14px', color: 'var(--accent)' }}>
              {statusMsg}
            </div>
          )}

          <button type="submit" className="btn" style={{ width: '100%', justifyContent: 'center', padding: '10px' }} disabled={submitting}>
            {submitting ? 'Executing in Daytona Sandbox...' : 'Launch Daytona Sandboxed Ingest'}
          </button>
        </form>
      </div>
    </div>
  );
}
