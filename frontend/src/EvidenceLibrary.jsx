import React, { useState, useEffect } from 'react';

export default function EvidenceLibrary() {
  const [documents, setDocuments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('recruitment fees');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [msg, setMsg] = useState('');

  const fetchDocuments = async () => {
    try {
      const res = await fetch('/api/documents');
      const data = await res.json();
      if (data.documents) setDocuments(data.documents);
    } catch (e) {
      console.error('Failed to load documents:', e);
    }
  };

  const handleSearch = async (queryStr) => {
    const q = queryStr !== undefined ? queryStr : searchQuery;
    if (!q.trim()) return;
    setSearching(true);
    try {
      const res = await fetch(`/api/evidence/search?q=${encodeURIComponent(q)}&k=6`);
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch (e) {
      console.error('Search error:', e);
    } finally {
      setSearching(false);
    }
  };

  useEffect(() => {
    fetchDocuments();
    handleSearch('recruitment fees');
  }, []);

  const handleFileUpload = async (e) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    setUploading(true);
    setMsg('Uploading & chunking policy documents...');

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i]);
    }

    try {
      const res = await fetch('/api/documents', {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.status === 'success') {
        setMsg(`Successfully ingested ${data.count} document(s)!`);
        fetchDocuments();
        handleSearch(searchQuery);
      } else {
        setMsg('Error uploading documents');
      }
    } catch (err) {
      setMsg('Upload failed: ' + err.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="library-page">
      <h2 className="page-title">Evidence Library</h2>

      <div className="card">
        <div className="card-title">
          <span className="section-heading" style={{ margin: 0 }}>Active Compliance Policies</span>
          <label className="btn">
            {uploading ? 'Ingesting...' : 'Upload Policy (.md / .txt)'}
            <input type="file" multiple accept=".md,.txt" onChange={handleFileUpload} style={{ display: 'none' }} />
          </label>
        </div>
        {msg && <p style={{ color: 'var(--accent)', marginBottom: '16px', fontSize: '14px' }}>{msg}</p>}

        {documents.length === 0 ? (
          <div className="card-surface" style={{ textAlign: 'center', padding: '40px', cursor: 'pointer' }} onClick={() => document.querySelector('input[type=file]').click()}>
            <p style={{ fontSize: '16px', fontWeight: '600', marginBottom: '8px' }}>No policy documents ingested yet</p>
            <p style={{ color: 'var(--ink-2)', fontSize: '14px' }}>
              Click to upload files from <code>seed/policies/</code> (e.g. ALS-POL-001 through ALS-POL-005)
            </p>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Doc ID</th>
                  <th>Document Title</th>
                  <th>Owner</th>
                  <th>Version</th>
                  <th>Effective Date</th>
                  <th>Next Review</th>
                  <th>Chunks</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((doc) => (
                  <tr key={doc.id || doc.doc_id}>
                    <td><span className="doc-chip">{doc.doc_id}</span></td>
                    <td style={{ fontWeight: '500' }}>{doc.title}</td>
                    <td style={{ color: 'var(--ink-2)' }}>{doc.owner || 'Compliance Office'}</td>
                    <td style={{ color: 'var(--ink-2)' }}>{doc.version || '1.0'}</td>
                    <td style={{ color: 'var(--ink-2)' }}>{doc.effective_date || '2026-08-05'}</td>
                    <td style={{ color: 'var(--ink-2)' }}>{doc.next_review || '2027-08-05'}</td>
                    <td className="tabular-nums"><strong>{doc.chunks_count}</strong></td>
                    <td><span className="status-pill answered">Active</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-title">
          <span className="section-heading" style={{ margin: 0 }}>Semantic & Keyword Search</span>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); handleSearch(); }} style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
          <input
            type="text"
            className="btn btn-secondary"
            style={{ flex: 1, textAlign: 'left', cursor: 'text', padding: '10px 14px' }}
            placeholder="Search clause text or headings (e.g. 'recruitment fees')..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <button type="submit" className="btn" disabled={searching}>
            {searching ? 'Searching...' : 'Search Evidence'}
          </button>
        </form>

        <div className="search-results">
          {searchResults.length === 0 ? (
            <p style={{ color: 'var(--ink-3)', fontSize: '14px' }}>No matching evidence chunks found.</p>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {searchResults.map((res, idx) => (
                <div key={idx} className="card-surface" style={{ marginBottom: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <span className="doc-chip">{res.doc_id} {res.clause_ref ? `§${res.clause_ref}` : ''}</span>
                      <span style={{ fontWeight: '600', fontSize: '14px', color: 'var(--ink)' }}>{res.heading || res.doc_title}</span>
                    </div>
                    <span className="mono-text" style={{ color: 'var(--ink-3)', fontVariantNumeric: 'tabular-nums' }}>
                      BM25 Score: {res.score}
                    </span>
                  </div>
                  <p style={{ fontSize: '14px', color: 'var(--ink-2)', whiteSpace: 'pre-wrap', lineHeight: '1.6' }}>
                    {res.content}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
