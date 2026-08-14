import React, { useState, useEffect } from 'react';
import EvidenceLibrary from './EvidenceLibrary';
import NewRun from './NewRun';
import RunReview from './RunReview';
import Consistency from './Consistency';

export default function App() {
  const [currentRoute, setCurrentRoute] = useState(window.location.pathname);
  const [activeRunId, setActiveRunId] = useState(null);

  useEffect(() => {
    const handlePopState = () => {
      setCurrentRoute(window.location.pathname);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (path, runId = null) => {
    window.history.pushState({}, '', path);
    setCurrentRoute(path);
    if (runId) setActiveRunId(runId);
  };

  const renderContent = () => {
    if (currentRoute === '/consistency') {
      return <Consistency />;
    }
    if (currentRoute === '/runs/new') {
      return <NewRun onRunCreated={(id) => navigate(`/runs/${id}`, id)} />;
    }
    if (currentRoute.startsWith('/runs/') && currentRoute !== '/runs/new') {
      const parts = currentRoute.split('/');
      const id = activeRunId || parts[2];
      return <RunReview runId={id} onBack={() => navigate('/library')} />;
    }
    return <EvidenceLibrary />;
  };

  return (
    <div className="app-container">
      <header className="navbar">
        <div className="brand" onClick={() => navigate('/library')} style={{ cursor: 'pointer' }}>
          <div>
            <h1>Compliance Passport</h1>
            <div className="brand-tagline">
              Compliance Passport — answer it once, answer it everywhere.
            </div>
          </div>
          <span className="badge-sandbox" style={{ marginLeft: '12px' }}>Daytona Engine</span>
        </div>
        <nav className="nav-links">
          <button
            className={currentRoute === '/library' || currentRoute === '/' ? 'active' : ''}
            onClick={() => navigate('/library')}
          >
            Evidence Library
          </button>
          <button
            className={currentRoute === '/runs/new' ? 'active' : ''}
            onClick={() => navigate('/runs/new')}
          >
            New Run
          </button>
          <button
            className={currentRoute === '/consistency' ? 'active' : ''}
            onClick={() => navigate('/consistency')}
          >
            Consistency
          </button>
        </nav>
      </header>

      <main className="content">
        {renderContent()}
      </main>

      <footer className="footer">
        Built on SoftwareForge · Executed in Daytona sandboxes
      </footer>
    </div>
  );
}
